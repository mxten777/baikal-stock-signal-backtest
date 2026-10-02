"""Read-only analysis of stored Expanded CANDIDATE performance."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.expanded_evidence import build_expanded_signal_records
from scripts.korean_market_calendar import is_trading_day, load_holidays
from src.expanded_candidate_performance import IDENTITY_FIELDS, PERFORMANCE_FIELDS, STATUS_RANK
from src.expanded_shadow_ledger import DEDUPE_FIELDS, LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.shadow_tracking import FORWARD_HORIZONS, normalize_market


class AnalysisIntegrityError(ValueError):
    """Invalid source evidence must not silently contribute to statistics."""


def _read_ledger(path: Path, fields: list[str], keys: tuple[str, ...]) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=fields)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if set(fields) - set(frame.columns):
        raise AnalysisIntegrityError(f"Missing columns in {path.name}")
    if frame[list(keys)].eq("").any().any():
        raise AnalysisIntegrityError(f"Missing identity in {path.name}")
    if frame.duplicated(list(keys)).any():
        raise AnalysisIntegrityError(f"Duplicate identity in {path.name}")
    for field in keys:
        if field in {"basDd", "source_basDd", "signal_date"}:
            for value in frame[field]:
                try:
                    canonical = date.fromisoformat(value).isoformat()
                except ValueError as exc:
                    raise AnalysisIntegrityError(f"Invalid {field} in {path.name}") from exc
                if canonical != value:
                    raise AnalysisIntegrityError(f"Noncanonical {field} in {path.name}")
    return frame


def _number(value: object) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisIntegrityError(f"Invalid numeric evidence: {value!r}") from exc
    if not math.isfinite(result):
        raise AnalysisIntegrityError(f"Nonfinite numeric evidence: {value!r}")
    return result


def _horizon_date(signal_date: str, horizon: int, holidays: frozenset[date]) -> str:
    endpoint = date.fromisoformat(signal_date)
    if not is_trading_day(endpoint, holidays):
        raise AnalysisIntegrityError(f"Nontrading signal date: {signal_date}")
    remaining = horizon
    while remaining:
        endpoint += timedelta(days=1)
        if endpoint > date(2027, 2, 28) or endpoint < date(2024, 1, 1):
            raise AnalysisIntegrityError("Horizon exceeds the supported KRX calendar; review calendar first")
        if is_trading_day(endpoint, holidays):
            remaining -= 1
    return endpoint.isoformat()


def sample_warning(count: int) -> str:
    if count == 0:
        return "NO_DATA"
    if count < 10:
        return "SMALL_SAMPLE"
    if count < 20:
        return "CAUTION"
    return "DESCRIPTIVE_ONLY"


def _concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tickers = Counter(row["ticker"] for row in rows)
    dates = Counter(row["signal_date"] for row in rows)
    count = len(rows)
    ticker_share = max(tickers.values(), default=0) / count if count else None
    date_share = max(dates.values(), default=0) / count if count else None
    warnings = []
    if ticker_share is not None and ticker_share > 0.10:
        warnings.append("TICKER_CONCENTRATION")
    if date_share is not None and date_share > 0.25:
        warnings.append("DATE_CONCENTRATION")
    return {
        "n": count,
        "unique_tickers": len(tickers),
        "unique_signal_dates": len(dates),
        "repeated_tickers": sum(value > 1 for value in tickers.values()),
        "repeat_record_fraction": (count - len(tickers)) / count if count else None,
        "max_ticker_share": ticker_share,
        "max_date_share": date_share,
        "top_three_date_share": sum(sorted(dates.values(), reverse=True)[:3]) / count if count else None,
        "ticker_counts": dict(tickers),
        "date_counts": dict(dates),
        "warnings": warnings,
    }


def _metric(values: list[float]) -> dict[str, float | None]:
    series = pd.Series(values, dtype=float)
    return {
        "avg": float(series.mean()) if values else None,
        "median": float(series.median()) if values else None,
        "win_rate": float(series.gt(0).mean()) if values else None,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [row["return"] for row in rows if row["return"] is not None]
    excess = [row["excess_return"] for row in rows if row["excess_return"] is not None]
    return_metrics = _metric(returns)
    excess_metrics = _metric(excess)
    return {
        "N_total": len(rows), "N_return": len(returns), "N_excess": len(excess),
        "N_open": sum(row["maturity_status"] == "OPEN" for row in rows),
        "N_missing": sum(row["maturity_status"] == "DATA_MISSING" for row in rows),
        "avg_return": return_metrics["avg"], "median_return": return_metrics["median"],
        "return_win_rate": return_metrics["win_rate"],
        "avg_excess": excess_metrics["avg"], "median_excess": excess_metrics["median"],
        "excess_win_rate": excess_metrics["win_rate"],
        "return_sample_warning": sample_warning(len(returns)),
        "excess_sample_warning": sample_warning(len(excess)),
        "return_concentration": _concentration([row for row in rows if row["return"] is not None]),
        "excess_concentration": _concentration([row for row in rows if row["excess_return"] is not None]),
    }


def diagnose_benchmark_coverage(
    rows: list[dict[str, Any]], benchmark_map: dict[str, pd.DataFrame], cutoff: str,
) -> dict[str, Any]:
    """Inspect required endpoints without deriving or populating any returns."""
    coverage = {}
    for symbol in ("KS11", "KQ11"):
        source = benchmark_map.get(symbol)
        valid_dates: set[str] = set()
        latest = None
        invalid_count = duplicate_count = 0
        if source is not None:
            if not {"date", "close"}.issubset(source.columns):
                raise AnalysisIntegrityError(f"Malformed diagnostic benchmark: {symbol}")
            dates = pd.to_datetime(source["date"], errors="coerce")
            prices = pd.to_numeric(source["close"], errors="coerce")
            valid = dates.notna() & prices.gt(0) & prices.map(lambda value: math.isfinite(value))
            invalid_count = int((~valid).sum())
            duplicate_count = int(dates.dt.normalize().duplicated().sum())
            valid_dates = set(dates[valid].dt.strftime("%Y-%m-%d"))
            latest = max(valid_dates, default=None)
            if duplicate_count:
                valid_dates = set()
        due = [row for row in rows if row["benchmark_symbol"] == symbol and row["horizon_date"] <= cutoff]
        required = sorted({endpoint for row in due for endpoint in (row["signal_date"], row["horizon_date"])})
        missing = sorted(set(required) - valid_dates)
        coverage[symbol] = {
            "status": "NOT_PROVIDED" if source is None else "INSUFFICIENT" if missing or invalid_count or duplicate_count else "AVAILABLE",
            "latest_index_date": latest, "required_dates": required, "missing_dates": missing,
            "N_required": len(due),
            "N_covered": sum(row["signal_date"] in valid_dates and row["horizon_date"] in valid_dates for row in due),
            "invalid_rows": invalid_count, "duplicate_dates": duplicate_count,
            "N_stored_benchmark": sum(row["benchmark_return"] is not None for row in rows if row["benchmark_symbol"] == symbol),
        }
    return coverage


def build_performance_analysis(
    repo_root: Path, *, cutoff_date: str, benchmark_map: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Return JSON-ready rows, summaries and diagnostics; never fetch or write data.

    Diagnostic benchmark frames never populate metrics. Return percentages and
    excess percentage points come exclusively from the official ledger; win
    rates and concentration shares are fractions. OPEN/missing are determined
    per horizon against the explicit cutoff, not the ledger-wide status.
    """
    cutoff = date.fromisoformat(cutoff_date).isoformat()
    paths = ExpandedShadowPaths(Path(repo_root))
    signals = _read_ledger(paths.signal_ledger_path, LEDGER_FIELDS, DEDUPE_FIELDS)
    performance = _read_ledger(paths.output_root / "expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, IDENTITY_FIELDS)
    candidates = signals.loc[signals["decision"].eq("CANDIDATE")]
    signal_keys = set(candidates[list(DEDUPE_FIELDS)].itertuples(index=False, name=None))
    performance_by_key = {tuple(row[field] for field in IDENTITY_FIELDS): row for row in performance.to_dict("records")}
    orphan_keys = set(performance_by_key) - signal_keys
    if orphan_keys:
        raise AnalysisIntegrityError(f"Orphan performance identities: {len(orphan_keys)}")
    holidays, calendar_warning = load_holidays(paths.repo_root)
    records, warnings = build_expanded_signal_records(paths.repo_root)
    if calendar_warning:
        warnings.append(calendar_warning)
    rows = []
    missing_joins = 0
    missing_profiles = 0
    profile_market_mismatches = 0
    ledger_statuses: Counter[str] = Counter()
    for record in records:
        if record.evidence.decision != "CANDIDATE" or record.signal_date > cutoff or record.basDd > cutoff:
            continue
        source = candidates.loc[
            candidates["basDd"].eq(record.basDd) & candidates["stock_code"].eq(record.ticker)
            & candidates["signal_date"].eq(record.signal_date)
        ]
        if len(source) != 1:
            raise AnalysisIntegrityError("Ambiguous evidence join across engine versions")
        signal = source.iloc[0]
        key = tuple(signal[field] for field in DEDUPE_FIELDS)
        stored = performance_by_key.get(key)
        if stored is None:
            missing_joins += 1
        else:
            ledger_statuses[stored["tracking_status"]] += 1
            if stored["tracking_status"] not in STATUS_RANK:
                raise AnalysisIntegrityError("Invalid performance tracking status")
            for left, right in (("stock_name", "stock_name"), ("market", "market"), ("foreign_status", "foreign_status"), ("run_id", "source_run_id"), ("source_commit", "source_commit"), ("created_at", "source_created_at")):
                if signal[left] != stored[right]:
                    raise AnalysisIntegrityError(f"Performance join conflict: {left}")
            for left, right in (("signal_price", "entry_price"), ("signal_score", "signal_score")):
                if _number(signal[left]) != _number(stored[right]):
                    raise AnalysisIntegrityError(f"Performance join conflict: {left}")
        evidence = record.evidence
        missing_profiles += record.profile is None
        profile_market_mismatches += record.profile is not None and record.profile.market != record.market
        for horizon in FORWARD_HORIZONS:
            endpoint = _horizon_date(record.signal_date, horizon, holidays)
            eligible = endpoint <= cutoff
            metrics = {name: _number(stored[f"{field}_{horizon}d"]) if stored else None for name, field in (("return", "return"), ("benchmark_return", "benchmark"), ("excess_return", "excess"))}
            if metrics["excess_return"] is not None:
                if metrics["return"] is None or metrics["benchmark_return"] is None:
                    raise AnalysisIntegrityError("Stored excess lacks return or benchmark")
                if abs(metrics["return"] - metrics["benchmark_return"] - metrics["excess_return"]) > 1e-8:
                    raise AnalysisIntegrityError("Stored excess arithmetic mismatch")
            if not eligible:
                metrics = dict.fromkeys(metrics)
            maturity = "DATA_MISSING" if stored is None else "OPEN" if not eligible else "MATURED" if metrics["return"] is not None else "DATA_MISSING"
            rows.append({
                "signal_key": list(key), "ticker": record.ticker, "name": record.stock_name,
                "signal_date": record.signal_date, "market": record.market,
                "sector": record.profile.sector if record.profile else None,
                "profile_as_of": record.profile.profile_as_of if record.profile else None,
                "entry_price": record.signal_price, "prev_score": evidence.prev_score,
                "current_score": evidence.current_score,
                "score_delta": evidence.current_score - evidence.prev_score if evidence.current_score is not None and evidence.prev_score is not None and evidence.evidence_status == "AVAILABLE" else None,
                "trend_score": evidence.trend_score, "volume_score": evidence.volume_score,
                "momentum_score": evidence.momentum_score, "foreign_status": evidence.foreign_status,
                "foreign_5d_ratio": evidence.foreign_5d_ratio, "horizon": horizon,
                "horizon_date": endpoint, "maturity_status": maturity, **metrics,
                "evidence_status": evidence.evidence_status,
                "performance_source": "OFFICIAL_LEDGER" if stored else "MISSING_OFFICIAL_LEDGER",
                "benchmark_symbol": normalize_market(record.market),
                "benchmark_status": "AVAILABLE" if metrics["benchmark_return"] is not None else "MISSING" if eligible else "NOT_DUE",
                "cutoff_date": cutoff,
            })
    base_rows = [row for row in rows if row["horizon"] == 5]
    missing_fields = {field: sum(row[field] is None for row in base_rows) for field in ("entry_price", "prev_score", "current_score", "score_delta", "trend_score", "volume_score", "momentum_score", "foreign_5d_ratio", "sector")}
    return {
        "analysis_version": "1", "cutoff_date": cutoff, "rows": rows,
        "summaries": {str(horizon): _summary([row for row in rows if row["horizon"] == horizon]) for horizon in FORWARD_HORIZONS},
        "quality": {
            "duplicate_signal_keys": 0, "duplicate_performance_keys": 0,
            "missing_signal_ledger": not paths.signal_ledger_path.is_file(),
            "missing_performance_joins": missing_joins, "orphan_performance_joins": 0,
            "missing_profile_joins": missing_profiles,
            "profile_market_label_mismatches": profile_market_mismatches,
            "unknown_market_count": sum(row["benchmark_symbol"] is None for row in base_rows),
            "missing_fields": missing_fields, "evidence_status": dict(Counter(row["evidence_status"] for row in base_rows)),
            "ledger_tracking_status": dict(ledger_statuses), "concentration": _concentration(base_rows),
            "profile_time_policy": "CURRENT_PROFILE_NOT_POINT_IN_TIME",
            "calendar_policy": "EXISTING_KRX_CALENDAR_RULE_BASED_AFTER_2026_09_03",
            "warnings": warnings,
        },
        "benchmark_coverage": diagnose_benchmark_coverage(rows, benchmark_map or {}, cutoff),
    }
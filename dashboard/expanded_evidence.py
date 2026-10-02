"""Read-only Company Profile and Decision Evidence projection for Expanded."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_company_profiles import CompanyProfile, load_company_profiles, profile_store_path
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.indicators import add_all_indicators
from src import config
from src.signal_engine import raw_to_score, score_momentum, score_trend, score_volume


EVIDENCE_AVAILABLE = "AVAILABLE"
EVIDENCE_PARTIAL = "PARTIAL"
EVIDENCE_UNAVAILABLE = "UNAVAILABLE"
_REQUIRED_INDICATORS = ("ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal")
_PERFORMANCE_KEY_FIELDS = ("source_basDd", "ticker", "signal_date", "engine_version")


@dataclass(frozen=True)
class DecisionEvidence:
    signal_reason: str | None
    prev_score: float | None
    current_score: float | None
    trend_score: int | None
    volume_score: int | None
    momentum_score: int | None
    foreign_status: str | None
    foreign_5d_ratio: float | None
    decision: str | None
    decision_reason: str | None
    evidence_status: str


@dataclass(frozen=True)
class ExpandedPerformanceEvidence:
    tracking_status: str
    return_5d: float | int | None
    excess_5d: float | int | None
    return_10d: float | int | None
    excess_10d: float | int | None
    return_20d: float | int | None
    excess_20d: float | int | None


@dataclass(frozen=True)
class ExpandedSignalRecord:
    basDd: str
    ticker: str
    stock_name: str
    market: str
    signal_date: str
    signal_price: float | None
    raw_score: int | None
    signal_score: float | None
    signal_type: str | None
    profile: CompanyProfile | None
    evidence: DecisionEvidence
    performance: ExpandedPerformanceEvidence | None


def build_expanded_signal_records(
    repo_root: Path,
    *,
    basdd: str | None = None,
) -> tuple[list[ExpandedSignalRecord], list[str]]:
    """Join existing Expanded artifacts without writing or re-evaluating signals."""
    paths = ExpandedShadowPaths(Path(repo_root))
    warnings: list[str] = []
    profiles, profile_warnings = load_company_profiles(profile_store_path(paths.repo_root))
    warnings.extend(profile_warnings)

    signal_rows = _read_csv(paths.signal_ledger_path, set(LEDGER_FIELDS), "signal ledger", warnings)
    if basdd is not None:
        signal_rows = [row for row in signal_rows if row.get("basDd") == basdd]
    performance_path = paths.validate_output_path(paths.output_root / "expanded_candidate_performance_ledger.csv")
    performance_rows = _read_csv(performance_path, set(PERFORMANCE_FIELDS), "performance ledger", warnings)
    performance_by_key = {_key(row, _PERFORMANCE_KEY_FIELDS): row for row in performance_rows}

    records: list[ExpandedSignalRecord] = []
    for row in signal_rows:
        ticker = str(row.get("stock_code", ""))
        signal_date = str(row.get("signal_date", ""))
        basdd = str(row.get("basDd", ""))
        engine_version = str(row.get("engine_version", ""))
        raw_score = _optional_int(row.get("raw_score"))
        ledger_score = _optional_float(row.get("signal_score"))
        foreign_ratio = _optional_float(row.get("foreign_5d_ratio"))
        decision = _optional_text(row.get("decision"))
        foreign_status = _optional_text(row.get("foreign_status"))
        exclusion_reason = _optional_text(row.get("exclusion_reason"))
        evidence = _build_decision_evidence(
            paths=paths,
            row=row,
            ledger_score=ledger_score,
            raw_score=raw_score,
            foreign_status=foreign_status,
            foreign_ratio=foreign_ratio,
            decision=decision,
            exclusion_reason=exclusion_reason,
            warnings=warnings,
        )
        performance_row = (
            performance_by_key.get((basdd, ticker, signal_date, engine_version))
            if decision == "CANDIDATE"
            else None
        )
        records.append(
            ExpandedSignalRecord(
                basDd=basdd,
                ticker=ticker,
                stock_name=str(row.get("stock_name", "")),
                market=str(row.get("market", "")),
                signal_date=signal_date,
                signal_price=_optional_float(row.get("signal_price")),
                raw_score=raw_score,
                signal_score=ledger_score,
                signal_type=_optional_text(row.get("signal_type")),
                profile=profiles.get(ticker),
                evidence=evidence,
                performance=_performance_record(performance_row),
            )
        )
    return records, warnings


def _build_decision_evidence(
    *,
    paths: ExpandedShadowPaths,
    row: dict[str, str],
    ledger_score: float | None,
    raw_score: int | None,
    foreign_status: str | None,
    foreign_ratio: float | None,
    decision: str | None,
    exclusion_reason: str | None,
    warnings: list[str],
) -> DecisionEvidence:
    ticker = str(row.get("stock_code", ""))
    signal_date = str(row.get("signal_date", ""))
    basdd = str(row.get("basDd", ""))
    decision_reason = _decision_reason(decision, foreign_status, exclusion_reason)
    current_score = ledger_score

    try:
        market_path = paths.validate_data_path(paths.market_dir(basdd) / f"{ticker}.csv")
        if not market_path.is_file():
            raise FileNotFoundError(market_path)
        market_frame = pd.read_csv(market_path, parse_dates=["date"])
        replay = _reconstruct_score_evidence(market_frame, signal_date)
        prev_score = replay["prev_score"]
        matched = replay["raw_score"] == raw_score and _scores_equal(replay["current_score"], ledger_score)
        signal_reason = _signal_reason(prev_score, current_score) if prev_score is not None else None
        threshold_matches = (
            prev_score is not None
            and current_score is not None
            and prev_score < config.SIGNAL_PREV_THRESHOLD
            and current_score >= config.SIGNAL_THRESHOLD
        )
        if matched:
            status = EVIDENCE_AVAILABLE if threshold_matches else EVIDENCE_PARTIAL
            if not threshold_matches:
                warnings.append(f"Expanded evidence threshold mismatch for {ticker} on {signal_date}")
            return DecisionEvidence(
                signal_reason=signal_reason if threshold_matches else None,
                prev_score=prev_score,
                current_score=current_score,
                trend_score=replay["trend_score"],
                volume_score=replay["volume_score"],
                momentum_score=replay["momentum_score"],
                foreign_status=foreign_status,
                foreign_5d_ratio=foreign_ratio,
                decision=decision,
                decision_reason=decision_reason,
                evidence_status=status,
            )
        warnings.append(f"Expanded evidence score mismatch for {ticker} on {signal_date}")
        return DecisionEvidence(
            signal_reason=signal_reason,
            prev_score=prev_score,
            current_score=current_score,
            trend_score=None,
            volume_score=None,
            momentum_score=None,
            foreign_status=foreign_status,
            foreign_5d_ratio=foreign_ratio,
            decision=decision,
            decision_reason=decision_reason,
            evidence_status=EVIDENCE_PARTIAL,
        )
    except Exception as exc:
        warnings.append(f"Expanded score evidence unavailable for {ticker} on {signal_date}: {type(exc).__name__}")
        return DecisionEvidence(
            signal_reason=None,
            prev_score=None,
            current_score=current_score,
            trend_score=None,
            volume_score=None,
            momentum_score=None,
            foreign_status=foreign_status,
            foreign_5d_ratio=foreign_ratio,
            decision=decision,
            decision_reason=decision_reason,
            evidence_status=EVIDENCE_UNAVAILABLE,
        )


def _reconstruct_score_evidence(market_frame: pd.DataFrame, signal_date: str) -> dict[str, Any]:
    required_market = {"date", "open", "high", "low", "close", "volume"}
    if not required_market.issubset(market_frame.columns):
        raise ValueError("market snapshot is missing required columns")
    frame = market_frame[["date", "open", "high", "low", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["date"].isna().any():
        raise ValueError("market snapshot has invalid dates")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = add_all_indicators(frame.sort_values("date").reset_index(drop=True))

    prev_score = 0.0
    prev_ma20 = float("nan")
    prev_macd_diff = float("nan")
    prev_close = float("nan")
    target_date = pd.Timestamp(signal_date).normalize()

    for _, source_row in frame.iterrows():
        row = source_row.copy()
        row["prev_close"] = prev_close
        if any(pd.isna(row[column]) for column in _REQUIRED_INDICATORS):
            prev_close = row["close"]
            prev_ma20 = row["ma20"] if not pd.isna(row["ma20"]) else prev_ma20
            prev_macd_diff = (
                row["macd"] - row["macd_signal"]
                if not pd.isna(row["macd"]) and not pd.isna(row["macd_signal"])
                else prev_macd_diff
            )
            prev_score = 0.0
            continue

        trend = score_trend(row, prev_ma20)
        volume = score_volume(row)
        momentum = score_momentum(row, prev_macd_diff)
        raw = trend + volume + momentum
        current = raw_to_score(raw)
        if pd.Timestamp(row["date"]).normalize() == target_date:
            return {
                "prev_score": prev_score,
                "current_score": current,
                "raw_score": raw,
                "trend_score": trend,
                "volume_score": volume,
                "momentum_score": momentum,
            }

        prev_close = row["close"]
        prev_ma20 = row["ma20"]
        prev_macd_diff = row["macd"] - row["macd_signal"]
        prev_score = current

    raise ValueError("signal date not found in market snapshot")


def _read_csv(path: Path, required: set[str], label: str, warnings: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        warnings.append(f"Expanded {label} not found: {path}")
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = sorted(required - set(reader.fieldnames or []))
            if missing:
                raise ValueError(f"missing fields: {missing}")
            return list(reader)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        warnings.append(f"Expanded {label} malformed: {exc}")
        return []


def _performance_record(row: dict[str, str] | None) -> ExpandedPerformanceEvidence | None:
    if row is None or row.get("decision", "CANDIDATE") == "EXCLUDED":
        return None
    return ExpandedPerformanceEvidence(
        tracking_status=row.get("tracking_status", "OPEN"),
        return_5d=_optional_number(row.get("return_5d")),
        excess_5d=_optional_number(row.get("excess_5d")),
        return_10d=_optional_number(row.get("return_10d")),
        excess_10d=_optional_number(row.get("excess_10d")),
        return_20d=_optional_number(row.get("return_20d")),
        excess_20d=_optional_number(row.get("excess_20d")),
    )


def _key(row: dict[str, str], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "")) for field in fields)


def _decision_reason(decision: str | None, foreign_status: str | None, exclusion_reason: str | None) -> str | None:
    if decision == "EXCLUDED":
        return exclusion_reason or "EXCLUDED"
    if decision == "CANDIDATE":
        if foreign_status == "NEGATIVE":
            return "Stored decision is CANDIDATE; Foreign status is NEGATIVE."
        return f"Foreign status {foreign_status or 'UNKNOWN'} is not NEGATIVE; existing rule classifies as CANDIDATE."
    return None


def _signal_reason(prev_score: float | None, current_score: float | None) -> str | None:
    if prev_score is None or current_score is None:
        return None
    return (
        f"Score crossed threshold: {prev_score:.1f} -> {current_score:.1f} "
        f"(threshold {config.SIGNAL_THRESHOLD:g})"
    )


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(number)


def _optional_number(value: object) -> float | int | None:
    number = _optional_float(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _scores_equal(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= 1e-9
"""Read-only, provenance-checked summaries of stored Expanded returns."""

from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from scripts.korean_market_calendar import load_holidays
from src.expanded_candidate_performance import IDENTITY_FIELDS, PERFORMANCE_FIELDS, STATUS_RANK
from src.expanded_performance_analysis import AnalysisIntegrityError, _horizon_date, _number, _read_ledger
from src.expanded_shadow_ledger import DEDUPE_FIELDS, LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedShadowPaths


STORED_PERFORMANCE_ENDPOINT = "/api/dashboard/expanded-shadow/stored-performance"

# STEP 21-N audited source-vintage disagreements; no official correction approved.
SOURCE_VINTAGE_HOLDS = {
    ("115180", "2026-10-01"): 5,
    ("086520", "2026-10-06"): 5,
    ("003280", "2026-10-12"): 10,
    ("251970", "2026-10-16"): 5,
}


def build_stored_performance(repo_root: Path) -> dict[str, Any]:
    paths = ExpandedShadowPaths(repo_root)
    performance_path = paths.output_root / "expanded_candidate_performance_ledger.csv"
    if not performance_path.is_file() or not paths.signal_ledger_path.is_file():
        raise AnalysisIntegrityError("Expanded signal/performance ledger missing")
    signals = _read_ledger(paths.signal_ledger_path, LEDGER_FIELDS, DEDUPE_FIELDS)
    performance = _read_ledger(performance_path, PERFORMANCE_FIELDS, IDENTITY_FIELDS)
    candidates = {
        tuple(row[field] for field in DEDUPE_FIELDS): row
        for row in signals.to_dict("records") if row["decision"] == "CANDIDATE"
    }
    rows = performance.to_dict("records")
    stored_keys = {tuple(row[field] for field in IDENTITY_FIELDS) for row in rows}
    if set(candidates) - stored_keys:
        raise AnalysisIntegrityError("Candidate signals missing stored performance records")
    for row in rows:
        key = tuple(row[field] for field in IDENTITY_FIELDS)
        signal = candidates.get(key)
        if signal is None:
            raise AnalysisIntegrityError(f"Unmatched performance identity: {key}")
        if row["tracking_status"] not in STATUS_RANK:
            raise AnalysisIntegrityError(f"Invalid tracking status: {key}")
        for left, right in (
            ("stock_name", "stock_name"), ("market", "market"),
            ("foreign_status", "foreign_status"), ("source_run_id", "run_id"),
            ("source_commit", "source_commit"), ("source_created_at", "created_at"),
        ):
            if row[left] != signal[right]:
                raise AnalysisIntegrityError(f"Performance identity conflict: {key}: {left}")
        for left, right in (("entry_price", "signal_price"), ("signal_score", "signal_score")):
            value = _number(row[left])
            if value is None or value != _number(signal[right]) or (left == "entry_price" and value <= 0):
                raise AnalysisIntegrityError(f"Performance identity conflict: {key}: {left}")
        for horizon in (5, 10, 20):
            stock, benchmark, excess = [
                _number(row[f"{field}_{horizon}d"]) for field in ("return", "benchmark", "excess")
            ]
            if excess is not None and (
                stock is None or benchmark is None or abs(stock - benchmark - excess) > 1e-8
            ):
                raise AnalysisIntegrityError(f"Stored excess mismatch: {key}: {horizon}D")
    holidays, warning = load_holidays(repo_root)
    if warning:
        raise AnalysisIntegrityError(warning)
    cutoff = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    summaries = []
    holds = [
        {"ticker": row["ticker"], "name": row["stock_name"], "signal_date": row["signal_date"],
         "conflict_horizon": SOURCE_VINTAGE_HOLDS[(row["ticker"], row["signal_date"])],
         "reason": "SOURCE_VINTAGE_CONFLICT"}
        for row in rows if (row["ticker"], row["signal_date"]) in SOURCE_VINTAGE_HOLDS
    ]
    for horizon in (5, 10, 20):
        values = []
        not_due = missing = blocked = benchmark_verified = 0
        for row in rows:
            if _horizon_date(row["signal_date"], horizon, holidays) > cutoff:
                not_due += 1
            elif (row["ticker"], row["signal_date"]) in SOURCE_VINTAGE_HOLDS:
                blocked += 1
            else:
                value = _number(row[f"return_{horizon}d"])
                if value is None:
                    missing += 1
                else:
                    values.append(value)
                    benchmark_verified += _number(row[f"excess_{horizon}d"]) is not None
        summaries.append({
            "horizon": f"{horizon}D", "evaluated": len(values),
            "average_return": mean(values) if values else None,
            "win_rate": 100 * sum(value > 0 for value in values) / len(values) if values else None,
            "not_due": not_due, "unconfirmed": missing, "conflict_held": blocked,
            "benchmark_verified": benchmark_verified,
        })
    return {
        "read_only": True, "status": "READY", "cutoff_date": cutoff,
        "source": str(performance_path.relative_to(repo_root)),
        "total": len(rows), "horizons": summaries, "conflicts": holds,
        "policy": "Stored ledger values only; identity/finite/excess checks. All horizons of audited conflict signals held. No prices fetched or returns recalculated.",
        "calendar_policy": "Existing KRX calendar; post-2026-09-03 dates rule-based.",
    }

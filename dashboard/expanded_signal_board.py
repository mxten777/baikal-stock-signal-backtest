"""Read-only dashboard projection for Expanded Shadow artifacts."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.expanded_candidate_performance import PERFORMANCE_FIELDS, STATUS_RANK
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import COMPLETED_RUN_STATUSES, ExpandedShadowPaths


EXPANDED_BOARD_ENDPOINT = "/api/dashboard/expanded-shadow"
SURFACE_READY = "READY"
SURFACE_MISSING = "MISSING"
SURFACE_EMPTY = "EMPTY"
SURFACE_STALE = "STALE"
SURFACE_MALFORMED = "MALFORMED"


def build_expanded_signal_board(repo_root: Path) -> dict[str, Any]:
    paths = ExpandedShadowPaths(Path(repo_root))
    warnings: list[str] = []
    run_summary = _read_run_summary(paths, warnings)
    source_date = run_summary.get("source_date")
    new_candidates = _read_new_candidates(paths, source_date, run_summary, warnings)
    performance = _read_performance(paths, warnings)

    surface_statuses = [run_summary["status"], new_candidates["status"], performance["status"]]
    if SURFACE_MALFORMED in surface_statuses:
        status = SURFACE_MALFORMED
    elif run_summary["status"] == SURFACE_MISSING:
        status = SURFACE_MISSING
    elif SURFACE_STALE in surface_statuses:
        status = SURFACE_STALE
    elif run_summary["status"] == SURFACE_EMPTY:
        status = SURFACE_EMPTY
    else:
        status = SURFACE_READY

    return {
        "mode": "EXPANDED_SHADOW",
        "read_only": True,
        "status": status,
        "run_summary": run_summary,
        "new_candidates": new_candidates,
        "status_summary": performance["status_summary"],
        "performance": performance,
        "warnings": warnings,
    }


def _read_run_summary(paths: ExpandedShadowPaths, warnings: list[str]) -> dict[str, Any]:
    source = paths.current_run_path
    empty = _empty_run_summary(str(source))
    if not source.is_file():
        return empty
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        required = {
            "run_id",
            "basDd",
            "status",
            "canonical_universe_count",
            "attempted_ticker_count",
            "ready_count",
            "signal_count",
            "new_candidate_count",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"missing fields: {missing}")
        values = {name: _nonnegative_int(payload[name], name) for name in required if name.endswith("count")}
        attempted = values["attempted_ticker_count"]
        ready = values["ready_count"]
        signals = values["signal_count"]
        candidates = values["new_candidate_count"]
        if not (ready <= attempted and candidates <= signals <= ready):
            raise ValueError("manifest counts are inconsistent")
        run_status = str(payload["status"])
        if run_status not in COMPLETED_RUN_STATUSES:
            raise ValueError(f"current run is not completed: {run_status}")
        return {
            "status": SURFACE_EMPTY if signals == 0 else SURFACE_READY,
            "source": str(source),
            "run_id": str(payload["run_id"]),
            "source_date": str(payload["basDd"]),
            "run_status": run_status,
            "universe": values["canonical_universe_count"],
            "attempted": attempted,
            "ready": ready,
            "failure": attempted - ready,
            "signals": signals,
            "candidate": candidates,
            "excluded": signals - candidates,
            "no_signal": ready - signals,
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "runtime_seconds": payload.get("runtime_seconds"),
        }
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        warnings.append(f"Expanded run manifest malformed: {exc}")
        return {**empty, "status": SURFACE_MALFORMED}


def _read_new_candidates(
    paths: ExpandedShadowPaths,
    source_date: str | None,
    run_summary: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    source = paths.signal_ledger_path
    empty = {"status": SURFACE_MISSING, "source": str(source), "source_date": source_date, "count": 0, "records": []}
    if source_date is None or run_summary["status"] in {SURFACE_MISSING, SURFACE_MALFORMED}:
        return empty
    if not source.is_file():
        if run_summary.get("signals", 0) == 0:
            return {**empty, "status": SURFACE_EMPTY}
        warnings.append("Expanded signal ledger is missing for a run with signals")
        return {**empty, "status": SURFACE_STALE}
    try:
        rows = _read_csv(source, set(LEDGER_FIELDS))
        cohort = [row for row in rows if row["basDd"] == source_date]
        candidates = [row for row in cohort if row["decision"] == "CANDIDATE"]
        records = [
            {
                "stock_name": row["stock_name"],
                "ticker": row["stock_code"],
                "market": row["market"],
                "signal_date": row["signal_date"],
                "entry_price": _number_or_none(row["signal_price"]),
                "signal_score": _number_or_none(row["signal_score"]),
                "foreign_status": row["foreign_status"],
            }
            for row in candidates
        ]
        expected = run_summary.get("candidate")
        status = SURFACE_READY
        if not cohort and run_summary.get("signals", 0):
            status = SURFACE_STALE
            warnings.append(f"Expanded signal ledger has no rows for latest source date {source_date}")
        elif expected != len(records):
            status = SURFACE_STALE
            warnings.append(f"Expanded candidate count mismatch: manifest={expected}, ledger={len(records)}")
        elif not records:
            status = SURFACE_EMPTY
        return {**empty, "status": status, "count": len(records), "records": records}
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as exc:
        warnings.append(f"Expanded signal ledger malformed: {exc}")
        return {**empty, "status": SURFACE_MALFORMED}


def _read_performance(paths: ExpandedShadowPaths, warnings: list[str]) -> dict[str, Any]:
    source = paths.output_root / "expanded_candidate_performance_ledger.csv"
    empty = {
        "status": SURFACE_MISSING,
        "source": str(source),
        "count": 0,
        "records": [],
        "empty_message": "성과 추적 데이터가 아직 생성되지 않았습니다.",
        "status_summary": None,
    }
    if not source.is_file():
        return empty
    try:
        rows = _read_csv(source, set(PERFORMANCE_FIELDS))
        if not rows:
            return {**empty, "status": SURFACE_EMPTY, "empty_message": "성과 추적 데이터가 비어 있습니다."}
        counts = Counter(row["tracking_status"] for row in rows)
        invalid = sorted(set(counts) - set(STATUS_RANK))
        if invalid:
            raise ValueError(f"invalid tracking statuses: {invalid}")
        records = [
            {
                "stock_name": row["stock_name"],
                "ticker": row["ticker"],
                "signal_date": row["signal_date"],
                "tracking_status": row["tracking_status"],
                "return_5d": _number_or_none(row["return_5d"]),
                "excess_5d": _number_or_none(row["excess_5d"]),
                "return_10d": _number_or_none(row["return_10d"]),
                "excess_10d": _number_or_none(row["excess_10d"]),
                "return_20d": _number_or_none(row["return_20d"]),
                "excess_20d": _number_or_none(row["excess_20d"]),
            }
            for row in rows
        ]
        summary = {status: counts.get(status, 0) for status in ("OPEN", "5D", "10D", "20D", "COMPLETE")}
        return {**empty, "status": SURFACE_READY, "count": len(records), "records": records, "empty_message": None, "status_summary": summary}
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as exc:
        warnings.append(f"Expanded performance ledger malformed: {exc}")
        return {**empty, "status": SURFACE_MALFORMED, "empty_message": "성과 추적 데이터를 읽을 수 없습니다."}


def _empty_run_summary(source: str) -> dict[str, Any]:
    return {
        "status": SURFACE_MISSING,
        "source": source,
        "run_id": None,
        "source_date": None,
        "run_status": None,
        "universe": None,
        "attempted": None,
        "ready": None,
        "failure": None,
        "signals": None,
        "candidate": None,
        "excluded": None,
        "no_signal": None,
        "started_at": None,
        "finished_at": None,
        "runtime_seconds": None,
    }


def _read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        missing = sorted(required - fields)
        if missing:
            raise ValueError(f"missing fields: {missing}")
        return list(reader)


def _number_or_none(value: object) -> float | int | None:
    if value in (None, ""):
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def _nonnegative_int(value: object, field: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError(f"{field} must be non-negative")
    return number
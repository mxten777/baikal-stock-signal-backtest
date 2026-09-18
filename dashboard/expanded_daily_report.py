"""Read-only Expanded Shadow Daily Report builder.

Reads exactly three source-of-truth artifacts:
1. expanded_shadow_run.json
2. expanded_shadow_signal_ledger.csv
3. expanded_candidate_performance_ledger.csv

Every value is copied as-is into the Common Report Model (dashboard.daily_report_model).
No signal, score, or performance value is recalculated here. This module never writes
to Signal Engine / Production / Shadow / DUAL / Scheduler / Expanded Operational /
Snapshot / ledger / manifest artifacts.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from dashboard.daily_report_model import (
    NEW_CANDIDATES_EMPTY,
    NEW_CANDIDATES_NOT_FOUND,
    NEW_CANDIDATES_READY,
    NEW_CANDIDATES_UNAVAILABLE,
    STATUS_MALFORMED,
    STATUS_MISSING,
    STATUS_READY,
    DailyReportModel,
    NewCandidateRecord,
    PerformanceRecord,
    RunSummary,
)
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedShadowPaths

_RUN_SUMMARY_REQUIRED_FIELDS = (
    "run_id",
    "basDd",
    "canonical_universe_count",
    "attempted_ticker_count",
    "ready_count",
    "signal_count",
    "new_candidate_count",
)

_EMPTY_RUN_SUMMARY = RunSummary(
    source_date=None,
    run_id=None,
    universe=None,
    attempted=None,
    ready=None,
    signals=None,
    candidate=None,
    excluded=None,
    no_signal=None,
    failure=None,
)


def build_daily_report_model(repo_root: Path, source_date: str | None = None) -> DailyReportModel:
    paths = ExpandedShadowPaths(Path(repo_root))
    warnings: list[str] = []
    run_summary, manifest_status, manifest_basdd = _read_run_summary(paths, warnings)

    if manifest_status != STATUS_READY:
        return DailyReportModel(
            status=manifest_status,
            run_summary=run_summary,
            new_candidates_status=NEW_CANDIDATES_UNAVAILABLE,
            new_candidates=[],
            performance=[],
            warnings=warnings,
        )

    effective_date = source_date if source_date is not None else manifest_basdd
    new_candidates_status, new_candidates = _read_new_candidates(paths, effective_date, warnings)
    performance = _read_performance(paths, warnings)

    return DailyReportModel(
        status=STATUS_READY,
        run_summary=run_summary,
        new_candidates_status=new_candidates_status,
        new_candidates=new_candidates,
        performance=performance,
        warnings=warnings,
    )


def _read_run_summary(paths: ExpandedShadowPaths, warnings: list[str]) -> tuple[RunSummary, str, str | None]:
    source = paths.current_run_path
    if not source.is_file():
        warnings.append(f"Run manifest not found: {source}")
        return _EMPTY_RUN_SUMMARY, STATUS_MISSING, None
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        missing = sorted(set(_RUN_SUMMARY_REQUIRED_FIELDS) - set(payload))
        if missing:
            raise ValueError(f"missing fields: {missing}")
        universe = int(payload["canonical_universe_count"])
        attempted = int(payload["attempted_ticker_count"])
        ready = int(payload["ready_count"])
        signals = int(payload["signal_count"])
        candidate = int(payload["new_candidate_count"])
        basdd = str(payload["basDd"])
        run_summary = RunSummary(
            source_date=basdd,
            run_id=str(payload["run_id"]),
            universe=universe,
            attempted=attempted,
            ready=ready,
            signals=signals,
            candidate=candidate,
            excluded=signals - candidate,
            no_signal=ready - signals,
            failure=attempted - ready,
        )
        return run_summary, STATUS_READY, basdd
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        warnings.append(f"Run manifest malformed: {exc}")
        return _EMPTY_RUN_SUMMARY, STATUS_MALFORMED, None


def _read_new_candidates(
    paths: ExpandedShadowPaths, effective_date: str | None, warnings: list[str]
) -> tuple[str, list[NewCandidateRecord]]:
    source = paths.signal_ledger_path
    if effective_date is None:
        warnings.append("No source date available for candidate lookup")
        return NEW_CANDIDATES_NOT_FOUND, []
    if not source.is_file():
        warnings.append(f"Signal ledger not found: {source}")
        return NEW_CANDIDATES_NOT_FOUND, []
    try:
        rows = _read_csv(source, set(LEDGER_FIELDS))
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        warnings.append(f"Signal ledger malformed: {exc}")
        return NEW_CANDIDATES_NOT_FOUND, []
    cohort = [row for row in rows if row["basDd"] == effective_date]
    if not cohort:
        warnings.append(f"No signal ledger rows found for date {effective_date}")
        return NEW_CANDIDATES_NOT_FOUND, []
    candidates = [row for row in cohort if row["decision"] == "CANDIDATE"]
    records = [
        NewCandidateRecord(
            stock_name=row["stock_name"],
            ticker=row["stock_code"],
            market=row["market"],
            signal_date=row["signal_date"],
            entry_price=_number_or_none(row["signal_price"]),
            signal_score=_number_or_none(row["signal_score"]),
            foreign_status=row["foreign_status"],
        )
        for row in candidates
    ]
    if not records:
        return NEW_CANDIDATES_EMPTY, []
    return NEW_CANDIDATES_READY, records


def _read_performance(paths: ExpandedShadowPaths, warnings: list[str]) -> list[PerformanceRecord]:
    source = paths.output_root / "expanded_candidate_performance_ledger.csv"
    if not source.is_file():
        return []
    try:
        rows = _read_csv(source, set(PERFORMANCE_FIELDS))
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        warnings.append(f"Performance ledger malformed: {exc}")
        return []
    return [
        PerformanceRecord(
            ticker=row["ticker"],
            stock_name=row["stock_name"],
            signal_date=row["signal_date"],
            entry_price=_number_or_none(row["entry_price"]),
            tracking_status=row["tracking_status"],
            return_5d=_number_or_none(row["return_5d"]),
            benchmark_5d=_number_or_none(row["benchmark_5d"]),
            excess_5d=_number_or_none(row["excess_5d"]),
            return_10d=_number_or_none(row["return_10d"]),
            benchmark_10d=_number_or_none(row["benchmark_10d"]),
            excess_10d=_number_or_none(row["excess_10d"]),
            return_20d=_number_or_none(row["return_20d"]),
            benchmark_20d=_number_or_none(row["benchmark_20d"]),
            excess_20d=_number_or_none(row["excess_20d"]),
        )
        for row in rows
    ]


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

"""Read-only Expanded Shadow Daily Report model builder.

Legacy summary, candidate, and performance fields remain copied from their existing
artifacts. Signal-level profile and decision evidence come from the shared Expanded
evidence builder. This module never writes operational or ledger artifacts.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date
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
    DailyComparison,
    SignalChange,
    NewCandidateRecord,
    PerformanceRecord,
    RunSummary,
)
from dashboard.expanded_evidence import ExpandedSignalRecord, build_expanded_signal_records
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import (
    COMPLETED_RUN_STATUSES,
    ExpandedManifestError,
    ExpandedPathError,
    ExpandedShadowPaths,
)

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
    run_summary, manifest_status, manifest_basdd = _read_run_summary(paths, warnings, source_date)

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
    new_candidates_status, new_candidates = _read_new_candidates(
        paths, effective_date, warnings,
        empty_run=effective_date == run_summary.source_date and run_summary.signals == 0,
    )
    performance = _read_performance(paths, warnings)
    expanded_signals, expanded_signal_warnings = build_expanded_signal_records(
        repo_root,
        basdd=effective_date,
    )

    return DailyReportModel(
        status=STATUS_READY,
        run_summary=run_summary,
        new_candidates_status=new_candidates_status,
        new_candidates=new_candidates,
        performance=performance,
        warnings=warnings,
        expanded_signals=expanded_signals,
        expanded_signal_warnings=expanded_signal_warnings,
        report_date=effective_date,
        comparison=_read_comparison(paths, effective_date),
        performance_as_of=_performance_as_of(paths, warnings),
    )


def _read_run_summary(paths: ExpandedShadowPaths, warnings: list[str], source_date: str | None = None) -> tuple[RunSummary, str, str | None]:
    source = paths.current_run_path
    if source_date is not None:
        try:
            dated = paths.resolve_manifest_path(source_date)
        except (ExpandedManifestError, ExpandedPathError, OSError, UnicodeError) as exc:
            warnings.append(f"Run manifest malformed: {exc}")
            return _EMPTY_RUN_SUMMARY, STATUS_MALFORMED, None
        if dated.is_file():
            source = dated
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
    paths: ExpandedShadowPaths, effective_date: str | None, warnings: list[str], *, empty_run: bool = False
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
        if empty_run:
            return NEW_CANDIDATES_EMPTY, []
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


def _performance_as_of(paths: ExpandedShadowPaths, warnings: list[str]) -> str | None:
    source = paths.output_root / "expanded_candidate_performance_ledger.csv"
    if not source.is_file():
        return None
    try:
        rows = _read_csv(source, {"updated_at", "tracking_status"})
        if any(not row["updated_at"] and row["tracking_status"] != "OPEN" for row in rows):
            raise ValueError("성과 확정 기록의 updated_at 없음")
        dates = [date.fromisoformat(row["updated_at"][:10]).isoformat() for row in rows if row["updated_at"]]
        return max(dates) if dates else None
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        warnings.append(f"Performance update date unavailable: {exc}")
        return None


def _read_comparison(paths: ExpandedShadowPaths, source_date: str | None) -> DailyComparison:
    previous_date = None
    try:
        if source_date is None:
            raise ValueError("보고서 기준일 없음")
        date.fromisoformat(source_date)
        calendar_path = paths.market_dir(source_date) / "005930.csv"
        calendar = _read_csv(calendar_path, {"date"})
        trading_dates = sorted({date.fromisoformat(row["date"]).isoformat() for row in calendar})
        if source_date not in trading_dates:
            raise ValueError("저장 시세에 당일 거래일 없음")
        prior = [day for day in trading_dates if day < source_date]
        if not prior:
            raise ValueError("저장 시세에 전 거래일 없음")
        previous_date = prior[-1]
        ledger = _read_csv(paths.signal_ledger_path, set(LEDGER_FIELDS))
        cohorts: list[dict[str, dict[str, str]]] = []
        for day in (previous_date, source_date):
            manifest = json.loads(paths.resolve_manifest_path(day).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError(f"{day} manifest 객체 형식 아님")
            if manifest.get("basDd") != day or manifest.get("status") not in COMPLETED_RUN_STATUSES:
                raise ValueError(f"{day} 완료 manifest 확인 불가")
            if manifest["ready_count"] != manifest["canonical_universe_count"]:
                raise ValueError(f"{day} 전체 분석 완료 확인 불가")
            rows = [row for row in ledger if row["basDd"] == day]
            if len(rows) != manifest["signal_count"]:
                raise ValueError(f"{day} 원장과 manifest 신호 수 불일치")
            by_ticker = {row["stock_code"]: row for row in rows}
            if len(by_ticker) != len(rows):
                raise ValueError(f"{day} 중복 종목")
            cohorts.append(by_ticker)
        yesterday, today = cohorts
        fields = ("signal_score", "signal_type", "decision", "foreign_status", "foreign_5d_ratio")

        def values(row: dict[str, str]) -> tuple[str | float | None, ...]:
            score = float(row["signal_score"])
            ratio = float(row["foreign_5d_ratio"]) if row["foreign_5d_ratio"] else None
            if not math.isfinite(score) or (ratio is not None and not math.isfinite(ratio)):
                raise ValueError("비교 수치 유효성 확인 불가")
            if not all(row[field] for field in ("signal_type", "decision", "foreign_status")):
                raise ValueError("비교 항목 누락")
            return score, row["signal_type"], row["decision"], row["foreign_status"], ratio

        def signature(row: dict[str, str]) -> str:
            return " / ".join(row[field] or "확인 불가" for field in fields)

        changes = tuple(
            SignalChange(
                ticker=ticker,
                stock_name=row["stock_name"],
                change="신규" if ticker not in yesterday else (
                    "유지" if values(row) == values(yesterday[ticker]) else "변경"
                ),
                previous=signature(yesterday[ticker]) if ticker in yesterday else None,
                current=signature(row),
            )
            for ticker, row in today.items()
        )
        return DailyComparison(
            previous_date=previous_date,
            status="AVAILABLE",
            reason="저장 삼성전자 시세 거래일 기준. 양일 발생 목록의 Score·Signal·판정·외국인 분류/비율만 비교하며 신호 상태 지속은 추정하지 않습니다.",
            changes=changes,
            previous_only=tuple(f'{row["stock_name"]}({ticker})' for ticker, row in yesterday.items() if ticker not in today),
        )
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError, ExpandedManifestError, ExpandedPathError, KeyError, TypeError, ValueError) as exc:
        return DailyComparison(previous_date=previous_date, reason=f"비교 불가: {exc}")

"""Single operational entry point for the isolated Expanded Shadow workflow."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.expanded_candidate_performance import _load_benchmark_map, _load_price_map, _load_signal_ledger
from scripts.expanded_shadow_daily_run import _source_commit
from src.expanded_candidate_performance import run_expanded_candidate_performance
from src.expanded_shadow_daily import STATUS_ALREADY_COMPLETED, STATUS_DATA_NOT_READY, ExpandedDailyResult, run_expanded_shadow_daily
from src.expanded_shadow_ops import ExpandedShadowPaths, utc_now_iso
from src.expanded_shadow_pipeline import STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES


FINAL_SUCCESS = "SUCCESS"
FINAL_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
FINAL_DAILY_RUN_FAILED = "DAILY_RUN_FAILED"
FINAL_PERFORMANCE_UPDATE_FAILED = "PERFORMANCE_UPDATE_FAILED"
PERFORMANCE_NOT_RUN = "NOT_RUN"
PERFORMANCE_SUCCESS = "SUCCESS"
PERFORMANCE_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
PERFORMANCE_FAILED = "FAILED"
SUCCESS_EXIT_STATUSES = {
    FINAL_SUCCESS,
    FINAL_SUCCESS_WITH_WARNING,
    STATUS_DATA_NOT_READY,
    STATUS_ALREADY_COMPLETED,
}


@dataclass(frozen=True)
class ExpandedOrchestrationResult:
    source_date: str
    daily_run_status: str
    performance_status: str
    candidates_registered: int
    candidates_updated: int
    final_status: str
    started_at: str
    completed_at: str
    runtime_seconds: float
    daily_run: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    benchmark_errors: dict[str, str] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_performance_stage(
    *,
    repo_root: Path,
    source_date: str,
    now_func: Callable[[], str] = utc_now_iso,
) -> dict[str, Any]:
    paths = ExpandedShadowPaths(repo_root)
    signal_ledger = _load_signal_ledger(paths)
    candidates = _candidate_rows(signal_ledger)
    price_map = _load_price_map(paths, source_date, candidates)
    benchmark_map, benchmark_errors = _load_benchmark_map(candidates, source_date)
    stats = run_expanded_candidate_performance(
        repo_root=repo_root,
        price_map=price_map,
        benchmark_map=benchmark_map,
        now_func=now_func,
    )
    return {**stats, "benchmark_errors": benchmark_errors}


def run_expanded_daily_orchestration(
    *,
    repo_root: Path,
    source_date: str,
    source_commit: str = "UNKNOWN",
    daily_runner: Callable[..., ExpandedDailyResult] | None = None,
    performance_runner: Callable[..., dict[str, Any]] | None = None,
    now_func: Callable[[], str] = utc_now_iso,
) -> ExpandedOrchestrationResult:
    started_at = now_func()
    resolved_daily_runner = daily_runner or run_expanded_shadow_daily
    resolved_performance_runner = performance_runner or run_performance_stage

    try:
        daily = resolved_daily_runner(
            repo_root=repo_root,
            source_date=source_date,
            source_commit=source_commit,
        )
    except Exception as exc:  # noqa: BLE001 - orchestration must isolate phase failures
        return _failed_result(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=FINAL_DAILY_RUN_FAILED,
            daily_run_status=FINAL_DAILY_RUN_FAILED,
            performance_status=PERFORMANCE_NOT_RUN,
            exc=exc,
        )

    daily_payload = daily.to_dict()
    if daily.status == STATUS_DATA_NOT_READY:
        return _result(
            source_date=source_date,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_NOT_RUN,
            final_status=STATUS_DATA_NOT_READY,
            started_at=started_at,
            completed_at=now_func(),
            daily_run=daily_payload,
        )

    allowed_daily_statuses = {STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES, STATUS_ALREADY_COMPLETED}
    if daily.status not in allowed_daily_statuses:
        completed_at = now_func()
        return ExpandedOrchestrationResult(
            source_date=source_date,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_NOT_RUN,
            candidates_registered=0,
            candidates_updated=0,
            final_status=FINAL_DAILY_RUN_FAILED,
            started_at=started_at,
            completed_at=completed_at,
            runtime_seconds=_runtime_seconds(started_at, completed_at),
            daily_run=daily_payload,
            error_code=daily.status,
            error_message=daily.message or "Expanded daily run did not complete successfully.",
        )

    try:
        performance = resolved_performance_runner(
            repo_root=repo_root,
            source_date=source_date,
            now_func=now_func,
        )
    except Exception as exc:  # noqa: BLE001 - completed daily artifacts are never rolled back
        return _failed_result(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=FINAL_PERFORMANCE_UPDATE_FAILED,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_FAILED,
            exc=exc,
            daily_run=daily_payload,
        )

    benchmark_errors = dict(performance.get("benchmark_errors") or {})
    has_warning = (
        daily.status == STATUS_SUCCESS_WITH_TICKER_FAILURES
        or bool(benchmark_errors)
        or int(performance.get("mismatch") or 0) > 0
    )
    performance_status = PERFORMANCE_SUCCESS_WITH_WARNING if benchmark_errors or int(performance.get("mismatch") or 0) else PERFORMANCE_SUCCESS
    if has_warning:
        final_status = FINAL_SUCCESS_WITH_WARNING
    elif daily.status == STATUS_ALREADY_COMPLETED:
        final_status = STATUS_ALREADY_COMPLETED
    else:
        final_status = FINAL_SUCCESS
    return _result(
        source_date=source_date,
        daily_run_status=daily.status,
        performance_status=performance_status,
        final_status=final_status,
        started_at=started_at,
        completed_at=now_func(),
        daily_run=daily_payload,
        performance=performance,
        benchmark_errors=benchmark_errors,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow daily orchestration")
    parser.add_argument("--source-date", help="Trading date in YYYY-MM-DD; defaults to the current Asia/Seoul date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    source_date = args.source_date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    result = run_expanded_daily_orchestration(
        repo_root=Path.cwd(),
        source_date=source_date,
        source_commit=_source_commit(Path.cwd()),
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{result.final_status}: {result.source_date}")
        print(f"Daily={result.daily_run_status} Performance={result.performance_status}")
    return 0 if result.final_status in SUCCESS_EXIT_STATUSES else 1


def _candidate_rows(signal_ledger: pd.DataFrame) -> pd.DataFrame:
    if signal_ledger.empty or "decision" not in signal_ledger:
        return signal_ledger.iloc[0:0]
    return signal_ledger.loc[signal_ledger["decision"].astype(str) == "CANDIDATE"]


def _result(
    *,
    source_date: str,
    daily_run_status: str,
    performance_status: str,
    final_status: str,
    started_at: str,
    completed_at: str,
    daily_run: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    benchmark_errors: dict[str, str] | None = None,
) -> ExpandedOrchestrationResult:
    performance = performance or {}
    return ExpandedOrchestrationResult(
        source_date=source_date,
        daily_run_status=daily_run_status,
        performance_status=performance_status,
        candidates_registered=int(performance.get("registered") or 0),
        candidates_updated=int(performance.get("updated") or 0),
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        daily_run=daily_run,
        performance=performance or None,
        benchmark_errors=benchmark_errors or {},
    )


def _failed_result(
    *,
    source_date: str,
    started_at: str,
    completed_at: str,
    final_status: str,
    daily_run_status: str,
    performance_status: str,
    exc: Exception,
    daily_run: dict[str, Any] | None = None,
) -> ExpandedOrchestrationResult:
    return ExpandedOrchestrationResult(
        source_date=source_date,
        daily_run_status=daily_run_status,
        performance_status=performance_status,
        candidates_registered=0,
        candidates_updated=0,
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        daily_run=daily_run,
        error_code=type(exc).__name__,
        error_message=str(exc),
    )


def _runtime_seconds(started_at: str, completed_at: str) -> float:
    try:
        return max((datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds(), 0.0)
    except ValueError:
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
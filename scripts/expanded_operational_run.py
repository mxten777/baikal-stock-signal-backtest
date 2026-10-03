"""Single scheduler-ready entry point for Expanded Shadow operations."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from scripts.expanded_daily_orchestrator import (
    FINAL_DAILY_RUN_FAILED,
    FINAL_PERFORMANCE_UPDATE_FAILED,
    FINAL_SUCCESS,
    FINAL_SUCCESS_WITH_WARNING,
    ExpandedOrchestrationResult,
    run_expanded_daily_orchestration,
)
from scripts.expanded_candidate_performance import PROVIDER_LEGACY, PROVIDER_NAVER
from scripts.expanded_shadow_daily_run import _source_commit
from src.expanded_shadow_data import FinanceDataReaderMarketSource, NaverInvestorFlowSource, RetryPolicy
from src.expanded_shadow_ops import utc_now_iso
from src.expanded_snapshot_preparation import (
    PREPARATION_DATA_NOT_READY,
    PREPARATION_FAILED,
    PREPARATION_READY,
    SnapshotPreparationResult,
    SourceDateResolution,
    prepare_expanded_snapshots,
    resolve_expanded_source_date,
)
from src.expanded_shadow_daily import STATUS_ALREADY_COMPLETED, STATUS_DATA_NOT_READY


SUCCESS_EXIT_STATUSES = {
    FINAL_SUCCESS,
    FINAL_SUCCESS_WITH_WARNING,
    STATUS_DATA_NOT_READY,
    STATUS_ALREADY_COMPLETED,
}


@dataclass(frozen=True)
class ExpandedOperationalResult:
    source_date: str | None
    final_status: str
    started_at: str
    completed_at: str
    runtime_seconds: float
    source_date_resolution: dict[str, Any] | None = None
    snapshot_preparation: dict[str, Any] | None = None
    orchestration: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_expanded_operational_run(
    *,
    repo_root: Path,
    explicit_source_date: str | None = None,
    market_source: Any | None = None,
    investor_source: Any | None = None,
    retry_policy: RetryPolicy | None = None,
    resolver_now: datetime | None = None,
    source_commit: str = "UNKNOWN",
    benchmark_provider: str = PROVIDER_LEGACY,
    tickers: Iterable[str] | None = None,
    resolver: Callable[..., SourceDateResolution] | None = None,
    preparer: Callable[..., SnapshotPreparationResult] | None = None,
    orchestrator: Callable[..., ExpandedOrchestrationResult] | None = None,
    now_func: Callable[[], str] = utc_now_iso,
) -> ExpandedOperationalResult:
    if benchmark_provider not in {PROVIDER_LEGACY, PROVIDER_NAVER}:
        raise ValueError(f"unsupported benchmark provider: {benchmark_provider!r}")
    started_at = now_func()
    resolved_resolver = resolver or resolve_expanded_source_date
    resolved_preparer = preparer or prepare_expanded_snapshots
    resolved_orchestrator = orchestrator or run_expanded_daily_orchestration

    try:
        resolution = resolved_resolver(
            repo_root=repo_root,
            explicit_source_date=explicit_source_date,
            now=resolver_now,
        )
    except Exception as exc:  # noqa: BLE001 - return machine-readable operational failure
        return _failure(
            source_date=explicit_source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=PREPARATION_FAILED,
            exc=exc,
        )

    source_date = resolution.source_date
    try:
        preparation = resolved_preparer(
            repo_root=repo_root,
            source_date=source_date,
            market_source=market_source or FinanceDataReaderMarketSource(),
            investor_source=investor_source or NaverInvestorFlowSource(),
            retry_policy=retry_policy or RetryPolicy(max_attempts=2),
            tickers=tickers,
        )
    except Exception as exc:  # noqa: BLE001 - preparation failures never reach signal execution
        return _failure(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=PREPARATION_FAILED,
            exc=exc,
            resolution=resolution,
        )

    if preparation.preparation_status != PREPARATION_READY:
        final_status = (
            STATUS_DATA_NOT_READY
            if preparation.preparation_status == PREPARATION_DATA_NOT_READY
            else PREPARATION_FAILED
        )
        return _result(
            source_date=source_date,
            final_status=final_status,
            started_at=started_at,
            completed_at=now_func(),
            resolution=resolution,
            preparation=preparation,
        )

    try:
        orchestration_result = resolved_orchestrator(
            repo_root=repo_root,
            source_date=source_date,
            source_commit=source_commit,
            benchmark_provider=benchmark_provider,
        )
    except Exception as exc:  # noqa: BLE001 - defensive boundary around the E1 entry point
        return _failure(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=FINAL_DAILY_RUN_FAILED,
            exc=exc,
            resolution=resolution,
            preparation=preparation,
        )

    return _result(
        source_date=source_date,
        final_status=orchestration_result.final_status,
        started_at=started_at,
        completed_at=now_func(),
        resolution=resolution,
        preparation=preparation,
        orchestration=orchestration_result,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow operational master")
    parser.add_argument("--source-date", help="Explicit recovery date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--benchmark-provider",
        choices=(PROVIDER_LEGACY, PROVIDER_NAVER),
        default=None,
        help="Expanded benchmark source (default: legacy; scheduled context: naver)",
    )
    parser.add_argument(
        "--run-context",
        choices=("scheduled",),
        help="CLI-local context for default benchmark selection",
    )
    args = parser.parse_args(argv)
    benchmark_provider = (
        args.benchmark_provider
        if args.benchmark_provider is not None
        else PROVIDER_NAVER if args.run_context == "scheduled" else PROVIDER_LEGACY
    )
    root = Path.cwd()
    result = run_expanded_operational_run(
        repo_root=root,
        explicit_source_date=args.source_date,
        source_commit=_source_commit(root),
        benchmark_provider=benchmark_provider,
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{result.final_status}: {result.source_date or 'UNRESOLVED'}")
    return 0 if result.final_status in SUCCESS_EXIT_STATUSES else 1


def _result(
    *,
    source_date: str,
    final_status: str,
    started_at: str,
    completed_at: str,
    resolution: SourceDateResolution,
    preparation: SnapshotPreparationResult,
    orchestration: ExpandedOrchestrationResult | None = None,
) -> ExpandedOperationalResult:
    return ExpandedOperationalResult(
        source_date=source_date,
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        source_date_resolution=resolution.to_dict(),
        snapshot_preparation=preparation.to_dict(),
        orchestration=None if orchestration is None else orchestration.to_dict(),
    )


def _failure(
    *,
    source_date: str | None,
    started_at: str,
    completed_at: str,
    final_status: str,
    exc: Exception,
    resolution: SourceDateResolution | None = None,
    preparation: SnapshotPreparationResult | None = None,
) -> ExpandedOperationalResult:
    return ExpandedOperationalResult(
        source_date=source_date,
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        source_date_resolution=None if resolution is None else resolution.to_dict(),
        snapshot_preparation=None if preparation is None else preparation.to_dict(),
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

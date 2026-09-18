"""Expanded Shadow integrated pipeline.

STEP 13-D7 only: compose the already isolated D1-D6 layers into one
fake-source-capable daily flow. This module does not schedule jobs, update
dashboards, or run real 574-provider collection by itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.expanded_shadow_data import (
    CollectionResult,
    InvestorSource,
    MarketSource,
    RetryPolicy,
    collect_investor_snapshot,
    collect_market_snapshot,
)
from src.expanded_shadow_eligibility import (
    ALL_ELIGIBILITY_STATUSES,
    STATUS_READY,
    EligibilityResult,
    classify_ticker_eligibility,
)
from src.expanded_shadow_ledger import ExpandedShadowLedgerStore
from src.expanded_shadow_ops import (
    ExpandedQuarantineRecord,
    ExpandedRegistryEvent,
    ExpandedRunLock,
    ExpandedRunManifest,
    ExpandedShadowPaths,
    append_quarantine,
    append_registry,
    compute_event_id,
    publish_completed_run,
    utc_now_iso,
    write_manifest,
)
from src.expanded_shadow_signal import ExpandedSignalEvaluation, evaluate_ready_ticker_from_snapshots
from src.expanded_shadow_universe import EXPECTED_BAS_DD, EXPECTED_UNIVERSE_COUNT, load_expanded_universe


STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_WITH_TICKER_FAILURES = "SUCCESS_WITH_TICKER_FAILURES"
EVENT_RUN_COMPLETED = "RUN_COMPLETED"
EVENT_RUN_FAILED = "RUN_FAILED"


class ExpandedPipelineError(RuntimeError):
    """Raised when the integrated Expanded Shadow pipeline cannot run."""


@dataclass(frozen=True)
class ExpandedTickerRunResult:
    ticker: str
    name: str
    market: str
    eligibility: EligibilityResult
    signal: ExpandedSignalEvaluation | None = None
    ledger_saved: bool = False
    quarantine_saved: bool = False


@dataclass(frozen=True)
class ExpandedPipelineResult:
    run_id: str
    basDd: str
    status: str
    manifest: ExpandedRunManifest
    registry_appended: bool
    ticker_results: tuple[ExpandedTickerRunResult, ...] = field(default_factory=tuple)

    @property
    def status_counts(self) -> dict[str, int]:
        return dict(self.manifest.status_counts)


@dataclass
class _RunProgress:
    attempted_ticker_count: int = 0
    market_success_count: int = 0
    investor_success_count: int = 0
    ready_count: int = 0
    quarantine_count: int = 0
    signal_count: int = 0
    new_candidate_count: int = 0
    status_counts: dict[str, int] = field(default_factory=lambda: {status: 0 for status in sorted(ALL_ELIGIBILITY_STATUSES)})
    market_dates: dict[str, int] = field(default_factory=dict)
    investor_dates: dict[str, int] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    last_stage: str = "INITIALIZATION"


def run_expanded_shadow_pipeline(
    *,
    repo_root: Path,
    basDd: str,
    market_source: MarketSource,
    investor_source: InvestorSource,
    retry_policy: RetryPolicy | None = None,
    run_id: str | None = None,
    source_commit: str = "UNKNOWN",
    signal_evaluator: Callable[..., ExpandedSignalEvaluation] = evaluate_ready_ticker_from_snapshots,
    now_func: Callable[[], str] = utc_now_iso,
    use_lock: bool = True,
) -> ExpandedPipelineResult:
    """Run one Expanded Shadow daily flow using explicit sources.

    Sources must be supplied by the caller. Tests use fake sources; the CLI is
    fail-safe and does not wire real providers unless a future step approves it.
    """
    paths = ExpandedShadowPaths(repo_root)
    universe = load_expanded_universe(paths.controlled_universe_path, basDd=EXPECTED_BAS_DD)
    if universe.row_count != EXPECTED_UNIVERSE_COUNT:
        raise ExpandedPipelineError(f"expected 574 tickers, got {universe.row_count}")

    resolved_run_id = run_id or uuid.uuid4().hex
    started_at = now_func()
    lock = ExpandedRunLock(paths, now_func=now_func)
    progress = _RunProgress()

    if use_lock:
        lock.acquire(resolved_run_id, basDd)
    try:
        try:
            result = _run_with_lock(
                paths=paths,
                basDd=basDd,
                run_id=resolved_run_id,
                started_at=started_at,
                source_commit=source_commit,
                universe_sha256=universe.sha256,
                tickers=tuple(universe.tickers),
                market_source=market_source,
                investor_source=investor_source,
                retry_policy=retry_policy or RetryPolicy(),
                signal_evaluator=signal_evaluator,
                now_func=now_func,
                progress=progress,
            )
        except Exception as exc:
            _record_system_failure(
                paths=paths,
                basDd=basDd,
                run_id=resolved_run_id,
                started_at=started_at,
                source_commit=source_commit,
                universe_sha256=universe.sha256,
                progress=progress,
                exc=exc,
                now_func=now_func,
            )
            raise
    finally:
        if use_lock:
            lock.release()
    return result


def _run_with_lock(
    *,
    paths: ExpandedShadowPaths,
    basDd: str,
    run_id: str,
    started_at: str,
    source_commit: str,
    universe_sha256: str,
    tickers: tuple,
    market_source: MarketSource,
    investor_source: InvestorSource,
    retry_policy: RetryPolicy,
    signal_evaluator: Callable[..., ExpandedSignalEvaluation],
    now_func: Callable[[], str],
    progress: _RunProgress,
) -> ExpandedPipelineResult:
    ledger = ExpandedShadowLedgerStore(paths)
    ticker_results: list[ExpandedTickerRunResult] = []

    for ticker_info in tickers:
        progress.attempted_ticker_count += 1
        progress.last_stage = f"MARKET:{ticker_info.ticker}"
        market_result = collect_market_snapshot(
            ticker=ticker_info.ticker,
            basDd=basDd,
            paths=paths,
            source=market_source,
            retry_policy=retry_policy,
        )
        progress.last_stage = f"INVESTOR:{ticker_info.ticker}"
        investor_result = collect_investor_snapshot(
            ticker=ticker_info.ticker,
            basDd=basDd,
            paths=paths,
            source=investor_source,
            retry_policy=retry_policy,
        )
        progress.last_stage = f"ELIGIBILITY:{ticker_info.ticker}"
        eligibility = classify_ticker_eligibility(
            ticker=ticker_info.ticker,
            basDd=basDd,
            market=market_result,
            investor=investor_result,
        )

        progress.status_counts[eligibility.status] += 1
        _bump(progress.market_dates, market_result.source_date)
        _bump(progress.investor_dates, investor_result.source_date)
        progress.retry_counts[ticker_info.ticker] = max(market_result.attempt_count, investor_result.attempt_count)
        progress.market_success_count += int(market_result.success)
        progress.investor_success_count += int(investor_result.success)

        signal: ExpandedSignalEvaluation | None = None
        ledger_saved = False
        quarantine_saved = False
        if eligibility.status == STATUS_READY:
            progress.ready_count += 1
            progress.last_stage = f"SIGNAL:{ticker_info.ticker}"
            signal = signal_evaluator(eligibility=eligibility, name=ticker_info.name, market=ticker_info.market, paths=paths)
            if signal.signal_present:
                progress.signal_count += 1
                if signal.decision == "CANDIDATE":
                    progress.new_candidate_count += 1
                progress.last_stage = f"LEDGER:{ticker_info.ticker}"
                ledger_saved = ledger.add_evaluation(
                    signal,
                    run_id=run_id,
                    source_commit=source_commit,
                    created_at=started_at,
                )
        else:
            progress.last_stage = f"QUARANTINE:{ticker_info.ticker}"
            quarantine_saved = append_quarantine(paths, _quarantine_record(run_id, basDd, eligibility, market_result, investor_result, started_at))
            progress.quarantine_count += 1

        ticker_results.append(
            ExpandedTickerRunResult(
                ticker=ticker_info.ticker,
                name=ticker_info.name,
                market=ticker_info.market,
                eligibility=eligibility,
                signal=signal,
                ledger_saved=ledger_saved,
                quarantine_saved=quarantine_saved,
            )
        )

    finished_at = now_func()
    progress.last_stage = "PUBLICATION"
    status = STATUS_SUCCESS if progress.ready_count == EXPECTED_UNIVERSE_COUNT else STATUS_SUCCESS_WITH_TICKER_FAILURES
    manifest = ExpandedRunManifest(
        run_id=run_id,
        basDd=basDd,
        started_at=started_at,
        finished_at=finished_at,
        runtime_seconds=_runtime_seconds(started_at, finished_at),
        status=status,
        canonical_universe_count=EXPECTED_UNIVERSE_COUNT,
        attempted_ticker_count=progress.attempted_ticker_count,
        market_success_count=progress.market_success_count,
        investor_success_count=progress.investor_success_count,
        ready_count=progress.ready_count,
        quarantine_count=progress.quarantine_count,
        signal_count=progress.signal_count,
        new_candidate_count=progress.new_candidate_count,
        status_counts=progress.status_counts,
        market_source_date_distribution=progress.market_dates,
        investor_source_date_distribution=progress.investor_dates,
        retry_counts=progress.retry_counts,
        universe_sha256=universe_sha256,
        source_commit=source_commit,
        ledger_path=str(paths.signal_ledger_path),
        quarantine_path=str(paths.quarantine_path(basDd)),
        system_failures=[],
        ticker_failure_sample=[_failure_sample(result) for result in ticker_results if result.eligibility.status != STATUS_READY][:20],
    )
    event = ExpandedRegistryEvent(
        event_id=compute_event_id(run_id, basDd, EVENT_RUN_COMPLETED, status),
        run_id=run_id,
        basDd=basDd,
        event_type=EVENT_RUN_COMPLETED,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        canonical_universe_count=EXPECTED_UNIVERSE_COUNT,
        attempted_ticker_count=progress.attempted_ticker_count,
        ready_count=progress.ready_count,
        signal_count=progress.signal_count,
        quarantine_count=progress.quarantine_count,
        manifest_path=str(paths.run_manifest_path(basDd, run_id)),
        created_at=finished_at,
    )
    registry_appended = publish_completed_run(paths, manifest, event)
    return ExpandedPipelineResult(run_id, basDd, status, manifest, registry_appended, tuple(ticker_results))


def _record_system_failure(
    *,
    paths: ExpandedShadowPaths,
    basDd: str,
    run_id: str,
    started_at: str,
    source_commit: str,
    universe_sha256: str,
    progress: _RunProgress,
    exc: Exception,
    now_func: Callable[[], str],
) -> None:
    run_path = paths.run_manifest_path(basDd, run_id)
    if run_path.exists():
        return
    finished_at = now_func()
    failure = {
        "attempted": progress.attempted_ticker_count,
        "error_class": type(exc).__name__,
        "error_message": str(exc),
        "last_stage": progress.last_stage,
    }
    manifest = ExpandedRunManifest(
        run_id=run_id,
        basDd=basDd,
        started_at=started_at,
        finished_at=finished_at,
        runtime_seconds=_runtime_seconds(started_at, finished_at),
        status="SYSTEM_FAILURE",
        canonical_universe_count=EXPECTED_UNIVERSE_COUNT,
        attempted_ticker_count=progress.attempted_ticker_count,
        market_success_count=progress.market_success_count,
        investor_success_count=progress.investor_success_count,
        ready_count=progress.ready_count,
        quarantine_count=progress.quarantine_count,
        signal_count=progress.signal_count,
        new_candidate_count=progress.new_candidate_count,
        status_counts=progress.status_counts,
        market_source_date_distribution=progress.market_dates,
        investor_source_date_distribution=progress.investor_dates,
        retry_counts=progress.retry_counts,
        universe_sha256=universe_sha256,
        source_commit=source_commit,
        ledger_path=str(paths.signal_ledger_path),
        quarantine_path=str(paths.quarantine_path(basDd)),
        system_failures=[failure],
        ticker_failure_sample=[],
    )
    try:
        write_manifest(paths, manifest)
        append_registry(
            paths,
            ExpandedRegistryEvent(
                event_id=compute_event_id(run_id, basDd, EVENT_RUN_FAILED, manifest.status),
                run_id=run_id,
                basDd=basDd,
                event_type=EVENT_RUN_FAILED,
                status=manifest.status,
                started_at=started_at,
                finished_at=finished_at,
                canonical_universe_count=EXPECTED_UNIVERSE_COUNT,
                attempted_ticker_count=progress.attempted_ticker_count,
                ready_count=progress.ready_count,
                signal_count=progress.signal_count,
                quarantine_count=progress.quarantine_count,
                error_code=type(exc).__name__,
                error_message=str(exc),
                last_stage=progress.last_stage,
                manifest_path=str(run_path),
                created_at=finished_at,
            ),
        )
    except Exception:
        return


def _quarantine_record(
    run_id: str,
    basDd: str,
    eligibility: EligibilityResult,
    market: CollectionResult,
    investor: CollectionResult,
    created_at: str,
) -> ExpandedQuarantineRecord:
    evidence = market if eligibility.status in {"MARKET_FAILED", "DATA_INVALID"} and market.error_code else investor
    return ExpandedQuarantineRecord(
        run_id=run_id,
        basDd=basDd,
        ticker=eligibility.ticker,
        ticker_status=eligibility.status,
        stage=evidence.dataset_type,
        attempt_count=evidence.attempt_count,
        error_code=eligibility.error_code or evidence.error_code or eligibility.status,
        error_class=eligibility.error_class or evidence.error_class or eligibility.status,
        error_message=eligibility.error_message or evidence.error_message or eligibility.status,
        source_date=evidence.source_date,
        created_at=created_at,
    )


def _bump(distribution: dict[str, int], value: str | None) -> None:
    key = value or "missing"
    distribution[key] = distribution.get(key, 0) + 1


def _runtime_seconds(started_at: str, finished_at: str) -> float:
    try:
        return max((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds(), 0.0)
    except ValueError:
        return 0.0


def _failure_sample(result: ExpandedTickerRunResult) -> dict[str, str | None]:
    return {
        "ticker": result.ticker,
        "name": result.name,
        "market": result.market,
        "status": result.eligibility.status,
        "error_code": result.eligibility.error_code,
        "error_message": result.eligibility.error_message,
    }
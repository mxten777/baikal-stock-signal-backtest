"""Expanded Shadow integrated pipeline.

STEP 13-D7 only: compose the already isolated D1-D6 layers into one
fake-source-capable daily flow. This module does not schedule jobs, update
dashboards, or run real 574-provider collection by itself.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    STATUS_DATA_INVALID,
    EligibilityResult,
    classify_ticker_eligibility,
)
from src.expanded_shadow_ledger import DEDUPE_FIELDS, ExpandedShadowLedgerStore, ExpandedLedgerRecord, build_ledger_record
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
    publish_partial_run,
    preserve_outputs_on_error,
    utc_now_iso,
    write_manifest,
    validate_manifest,
    unresolved_partial_dates,
    read_operational_start_date,
)
from src.expanded_shadow_signal import ExpandedSignalEvaluation, evaluate_ready_ticker_from_snapshots
from src.expanded_shadow_universe import create_universe_snapshot, load_expanded_universe
from src.expanded_trading_halts import (
    TradingHaltEvidenceProvider, TradingStatusAssessment, assess_trading_status, validate_assessment,
)


STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_WITH_TICKER_FAILURES = "SUCCESS_WITH_TICKER_FAILURES"
STATUS_PARTIAL = "PARTIAL"
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
    universe_count: int = 0
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
    trading_halt_exclusions: list[dict[str, str]] = field(default_factory=list)


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
    trading_halt_provider: TradingHaltEvidenceProvider | None = None,
    trading_status: TradingStatusAssessment | None = None,
) -> ExpandedPipelineResult:
    """Run one Expanded Shadow daily flow using explicit sources.

    Sources must be supplied by the caller. Tests use fake sources; the CLI is
    fail-safe and does not wire real providers unless a future step approves it.
    """
    from src.expanded_continuation import assert_execution_date

    assert_execution_date(repo_root, basDd)
    paths = ExpandedShadowPaths(repo_root)
    universe = load_expanded_universe(paths.controlled_universe_path, basDd=None, source_date=basDd)

    resolved_run_id = run_id or uuid.uuid4().hex
    started_at = now_func()
    lock = ExpandedRunLock(paths, now_func=now_func)
    progress = _RunProgress(universe_count=universe.row_count)

    if use_lock:
        lock.acquire(resolved_run_id, basDd)
    try:
        try:
            start_date = read_operational_start_date(paths)
            if start_date is not None and basDd < start_date:
                raise ExpandedPipelineError("Source date is before approved operational boundary")
            create_universe_snapshot(
                universe.path, paths.universe_snapshots_dir / basDd, basDd=None,
            )
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
                trading_halt_provider=trading_halt_provider,
                trading_status=trading_status,
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
    trading_halt_provider: TradingHaltEvidenceProvider | None,
    trading_status: TradingStatusAssessment | None,
) -> ExpandedPipelineResult:
    records: list[ExpandedLedgerRecord] = []
    unresolved: list[dict[str, Any]] = []
    ticker_results: list[ExpandedTickerRunResult] = []
    assessment = trading_status or assess_trading_status(
        basDd, (item.ticker for item in tickers), trading_halt_provider)
    validate_assessment(assessment, basDd, (item.ticker for item in tickers))
    progress.trading_halt_exclusions = list(assessment.exclusions)
    unknown_tickers = {item["ticker"] for item in assessment.unknown}
    unresolved.extend(assessment.unknown)
    progress.quarantine_count = len(unknown_tickers)
    if unknown_tickers:
        progress.status_counts["UNKNOWN"] = len(unknown_tickers)
    unresolved_dates = unresolved_partial_dates(paths)
    from src.expanded_continuation import continuation_policy

    policy = continuation_policy(paths.repo_root)
    if policy:
        unresolved_dates -= {policy["exception_date"]}
    if unresolved_dates and basDd > min(unresolved_dates):
        raise ExpandedPipelineError("Unresolved PARTIAL date blocks later execution")
    excluded_tickers = {item["ticker"] for item in progress.trading_halt_exclusions}
    if excluded_tickers:
        progress.status_counts["TRADING_HALT_EXCLUDED"] = len(excluded_tickers)

    for ticker_info in tickers:
        if ticker_info.ticker in excluded_tickers or ticker_info.ticker in unknown_tickers:
            continue
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
            progress.last_stage = f"SIGNAL:{ticker_info.ticker}"
            try:
                signal = signal_evaluator(eligibility=eligibility, name=ticker_info.name, market=ticker_info.market, paths=paths)
            except Exception as exc:  # noqa: BLE001 - retain healthy ticker results in PARTIAL evidence
                eligibility = replace(
                    eligibility, status=STATUS_DATA_INVALID,
                    error_code="SIGNAL_EVALUATION_FAILED", error_class=type(exc).__name__,
                    error_message=str(exc),
                )
                progress.status_counts[STATUS_READY] -= 1
                progress.status_counts[STATUS_DATA_INVALID] += 1
                unresolved.append(ExpandedQuarantineRecord(
                    run_id=run_id, basDd=basDd, ticker=ticker_info.ticker,
                    ticker_status=STATUS_DATA_INVALID, stage="SIGNAL",
                    attempt_count=max(market_result.attempt_count, investor_result.attempt_count),
                    error_code=eligibility.error_code, error_class=eligibility.error_class,
                    error_message=eligibility.error_message, created_at=started_at,
                ).to_dict())
                quarantine_saved = True
                progress.quarantine_count += 1
            else:
                progress.ready_count += 1
            if signal is not None and signal.signal_present:
                progress.signal_count += 1
                if signal.decision == "CANDIDATE":
                    progress.new_candidate_count += 1
                progress.last_stage = f"LEDGER:{ticker_info.ticker}"
                record = build_ledger_record(
                    signal,
                    run_id=run_id,
                    source_commit=source_commit,
                    created_at=started_at,
                )
                if record is not None:
                    records.append(record)
        else:
            progress.last_stage = f"QUARANTINE:{ticker_info.ticker}"
            unresolved.append(_quarantine_record(
                run_id, basDd, eligibility, market_result, investor_result, started_at).to_dict())
            quarantine_saved = True
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
    status = (
        STATUS_SUCCESS
        if not unresolved and progress.ready_count == len(tickers) - len(excluded_tickers)
        else STATUS_PARTIAL
    )
    ledger_path = (
        paths.partial_dir(basDd, run_id) / "signals.csv"
        if status == STATUS_PARTIAL else paths.signal_ledger_path
    )
    ledger = ExpandedShadowLedgerStore(paths, ledger_path)
    progress.last_stage = "LEDGER_MERGE"
    manifest = ExpandedRunManifest(
        run_id=run_id,
        basDd=basDd,
        started_at=started_at,
        finished_at=finished_at,
        runtime_seconds=_runtime_seconds(started_at, finished_at),
        status=status,
        canonical_universe_count=len(tickers),
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
        ledger_path=str(ledger_path),
        quarantine_path=str(paths.partial_dir(basDd, run_id) / "manifest.json") if status == STATUS_PARTIAL else str(paths.quarantine_path(basDd)),
        system_failures=[],
        ticker_failure_sample=[_failure_sample(result) for result in ticker_results if result.eligibility.status != STATUS_READY][:20],
        trading_halt_exclusions=progress.trading_halt_exclusions,
        unresolved_tickers=unresolved,
    )
    validate_manifest(manifest)
    if status == STATUS_PARTIAL:
        partial_manifest = paths.partial_dir(basDd, run_id) / "manifest.json"
        if partial_manifest.exists() and json.loads(partial_manifest.read_text(encoding="utf-8")) != manifest.to_dict():
            raise ExpandedPipelineError("Conflicting immutable PARTIAL run")
    existing = ledger.load()
    existing_keys = {
        tuple(str(getattr(row, field)) for field in DEDUPE_FIELDS)
        for row in existing.itertuples(index=False)
    }
    new_tickers = {
        record.stock_code for record in records
        if tuple(str(getattr(record, field)) for field in DEDUPE_FIELDS) not in existing_keys
    }
    ticker_results = [
        replace(item, ledger_saved=item.ticker in new_tickers) for item in ticker_results
    ]
    event = ExpandedRegistryEvent(
        event_id=compute_event_id(run_id, basDd, EVENT_RUN_COMPLETED, status),
        run_id=run_id,
        basDd=basDd,
        event_type="RUN_PARTIAL" if status == STATUS_PARTIAL else EVENT_RUN_COMPLETED,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        canonical_universe_count=len(tickers),
        attempted_ticker_count=progress.attempted_ticker_count,
        ready_count=progress.ready_count,
        signal_count=progress.signal_count,
        quarantine_count=progress.quarantine_count,
        manifest_path=str(paths.partial_dir(basDd, run_id) / "manifest.json") if status == STATUS_PARTIAL else str(paths.run_manifest_path(basDd, run_id)),
        created_at=finished_at,
        trading_halt_exclusions=progress.trading_halt_exclusions,
    )
    with (
        preserve_outputs_on_error(paths, (ledger_path,))
        if status != STATUS_PARTIAL else nullcontext()
    ):
        if records or status == STATUS_PARTIAL:
            ledger.merge(records)
        progress.last_stage = "PUBLICATION"
        registry_appended = (
            publish_partial_run(paths, manifest, event) if status == STATUS_PARTIAL
            else publish_completed_run(paths, manifest, event)
        )
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
        canonical_universe_count=progress.universe_count,
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
        trading_halt_exclusions=progress.trading_halt_exclusions,
    )
    try:
        if not run_path.exists():
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
                canonical_universe_count=progress.universe_count,
                attempted_ticker_count=progress.attempted_ticker_count,
                ready_count=progress.ready_count,
                signal_count=progress.signal_count,
                quarantine_count=progress.quarantine_count,
                error_code=type(exc).__name__,
                error_message=str(exc),
                last_stage=progress.last_stage,
                manifest_path=str(run_path),
                created_at=finished_at,
                trading_halt_exclusions=progress.trading_halt_exclusions,
            ),
        )
    except Exception as audit_error:
        raise ExpandedPipelineError(
            f"Run failed ({type(exc).__name__}: {exc}); failure audit could not be saved: "
            f"{type(audit_error).__name__}: {audit_error}"
        ) from exc


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
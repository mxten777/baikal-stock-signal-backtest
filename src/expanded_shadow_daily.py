"""Incremental daily orchestration for the isolated Expanded Shadow system."""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

from src.expanded_shadow_data import RetryPolicy
from src.expanded_shadow_ops import (
    COMPLETED_RUN_STATUSES,
    ExpandedRegistryEvent,
    ExpandedRunLock,
    ExpandedShadowPaths,
    append_registry,
    compute_event_id,
    utc_now_iso,
    unresolved_partial_dates,
    read_operational_start_date,
)
from src.expanded_shadow_pipeline import ExpandedPipelineResult, run_expanded_shadow_pipeline
from src.expanded_shadow_universe import load_expanded_universe
from src.expanded_trading_halts import (
    TradingHaltEvidenceProvider,
    TradingStatusAssessment,
    assess_trading_status,
    validate_assessment,
)


STATUS_ALREADY_COMPLETED = "ALREADY_COMPLETED"
STATUS_DATA_NOT_READY = "DATA_NOT_READY"
STATUS_RUN = "RUN"
ACTION_RUN = "RUN"
ACTION_SKIP = "SKIP"


class ExpandedDailyError(RuntimeError):
    """Raised when daily orchestration evidence is malformed or conflicting."""


@dataclass(frozen=True)
class ExpandedDataReadiness:
    ready: bool
    source_date: str
    universe_count: int
    market_ready_count: int
    investor_ready_count: int
    market_source_dates: dict[str, int] = field(default_factory=dict)
    investor_source_dates: dict[str, int] = field(default_factory=dict)
    failed_tickers: tuple[dict[str, str], ...] = field(default_factory=tuple)
    trading_halt_exclusions: list[dict[str, str]] = field(default_factory=list)
    trading_status: TradingStatusAssessment | None = None


@dataclass(frozen=True)
class ExpandedDailyResult:
    status: str
    action: str
    source_date: str
    readiness: ExpandedDataReadiness | None = None
    pipeline_result: ExpandedPipelineResult | None = None
    completed_run_id: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "action": self.action,
            "source_date": self.source_date,
            "run_id": self.pipeline_result.run_id if self.pipeline_result else self.completed_run_id,
            "message": self.message,
        }
        if self.readiness is not None:
            payload["data_gate"] = {
                "ready": self.readiness.ready,
                "universe_count": self.readiness.universe_count,
                "market_ready_count": self.readiness.market_ready_count,
                "investor_ready_count": self.readiness.investor_ready_count,
                "market_source_dates": self.readiness.market_source_dates,
                "investor_source_dates": self.readiness.investor_source_dates,
                "failed_tickers": list(self.readiness.failed_tickers),
                "trading_halt_exclusions": self.readiness.trading_halt_exclusions,
            }
        if self.pipeline_result is not None:
            manifest = self.pipeline_result.manifest
            payload.update(
                {
                    "universe_count": manifest.canonical_universe_count,
                    "attempted": manifest.attempted_ticker_count,
                    "ready": manifest.ready_count,
                    "failure_count": manifest.attempted_ticker_count - manifest.ready_count,
                    "candidate_count": manifest.new_candidate_count,
                    "excluded_count": manifest.signal_count - manifest.new_candidate_count,
                    "no_signal_count": manifest.ready_count - manifest.signal_count,
                    "signal_count": manifest.signal_count,
                    "started_at": manifest.started_at,
                    "finished_at": manifest.finished_at,
                    "runtime_seconds": manifest.runtime_seconds,
                    "manifest_path": str(
                        Path(manifest.ledger_path).parent / "manifest.json"
                    ) if manifest.status == "PARTIAL" else str(
                        ExpandedShadowPaths(Path(self.pipeline_result.manifest.ledger_path).parents[2])
                        .run_manifest_path(self.source_date, self.pipeline_result.run_id)
                    ),
                    "failed_tickers": manifest.ticker_failure_sample,
                    "trading_halt_exclusions": manifest.trading_halt_exclusions,
                }
            )
        return payload


class PreparedMarketSource:
    def __init__(self, paths: ExpandedShadowPaths, source_date: str) -> None:
        self.paths = paths
        self.source_date = source_date

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        del start, end
        return pd.read_csv(self.paths.market_dir(self.source_date) / f"{ticker}.csv", dtype={"ticker": str})


class PreparedInvestorSource:
    def __init__(self, paths: ExpandedShadowPaths, source_date: str) -> None:
        self.paths = paths
        self.source_date = source_date

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        del start, end
        return pd.read_csv(
            self.paths.investor_dir(self.source_date) / f"{ticker}_investor.csv",
            dtype={"ticker": str},
        )


def probe_expanded_data_readiness(
    paths: ExpandedShadowPaths,
    source_date: str,
    tickers: Iterable[str],
    trading_halt_provider: TradingHaltEvidenceProvider | None = None,
    trading_status: TradingStatusAssessment | None = None,
) -> ExpandedDataReadiness:
    ticker_list = tuple(tickers)
    assessment = trading_status or assess_trading_status(source_date, ticker_list, trading_halt_provider)
    validate_assessment(assessment, source_date, ticker_list)
    exclusions = list(assessment.exclusions)
    unknown_tickers = {item["ticker"] for item in assessment.unknown}
    excluded_tickers = {item["ticker"] for item in exclusions}
    market_dates: dict[str, int] = {}
    investor_dates: dict[str, int] = {}
    failures: list[dict[str, str]] = list(assessment.unknown)
    market_ready = 0
    investor_ready = 0

    for ticker in ticker_list:
        if ticker in excluded_tickers or ticker in unknown_tickers:
            continue
        market_date, market_error = _snapshot_latest_date(paths.market_dir(source_date) / f"{ticker}.csv")
        investor_date, investor_error = _snapshot_latest_date(
            paths.investor_dir(source_date) / f"{ticker}_investor.csv"
        )
        _bump(market_dates, market_date)
        _bump(investor_dates, investor_date)
        market_ready += int(market_date == source_date and market_error is None)
        investor_ready += int(investor_date == source_date and investor_error is None)
        if market_date != source_date or market_error is not None:
            failures.append(_gate_failure(ticker, "MARKET", market_date, source_date, market_error))
        if investor_date != source_date or investor_error is not None:
            failures.append(_gate_failure(ticker, "INVESTOR", investor_date, source_date, investor_error))

    return ExpandedDataReadiness(
        ready=(
            not assessment.unknown
            and market_ready == len(ticker_list) - len(exclusions)
            and investor_ready == len(ticker_list) - len(exclusions)
        ),
        source_date=source_date,
        universe_count=len(ticker_list),
        market_ready_count=market_ready,
        investor_ready_count=investor_ready,
        market_source_dates=market_dates,
        investor_source_dates=investor_dates,
        failed_tickers=tuple(failures),
        trading_halt_exclusions=exclusions,
        trading_status=assessment,
    )


def run_expanded_shadow_daily(
    *,
    repo_root: Path,
    source_date: str,
    source_commit: str = "UNKNOWN",
    run_id: str | None = None,
    pipeline_runner: Callable[..., ExpandedPipelineResult] = run_expanded_shadow_pipeline,
    trading_halt_provider: TradingHaltEvidenceProvider | None = None,
    trading_status: TradingStatusAssessment | None = None,
    use_lock: bool = True,
) -> ExpandedDailyResult:
    from src.expanded_continuation import assert_execution_date, continuation_policy

    _validate_source_date(source_date)
    assert_execution_date(repo_root, source_date)
    paths = ExpandedShadowPaths(repo_root)
    universe = load_expanded_universe(paths.controlled_universe_path, basDd=None, source_date=source_date)
    resolved_run_id = run_id or uuid.uuid4().hex
    lock = ExpandedRunLock(paths)
    if use_lock:
        lock.acquire(resolved_run_id, source_date)
    try:
        start_date = read_operational_start_date(paths)
        if start_date is not None and source_date < start_date:
            raise ExpandedDailyError("Source date is before approved operational boundary")
        unresolved = unresolved_partial_dates(paths)
        policy = continuation_policy(repo_root)
        if policy:
            unresolved -= {policy["exception_date"]}
        if unresolved and source_date > min(unresolved):
            raise ExpandedDailyError("Unresolved PARTIAL date blocks later execution")
        completed = _completed_manifest(paths, source_date)
        if completed is not None:
            return ExpandedDailyResult(
                status=STATUS_ALREADY_COMPLETED,
                action=ACTION_SKIP,
                source_date=source_date,
                completed_run_id=str(completed["run_id"]),
                message="Expanded source date already has a completed immutable run.",
            )

        tickers = tuple(item.ticker for item in universe.tickers)
        readiness = probe_expanded_data_readiness(
            paths, source_date, tickers, trading_halt_provider, trading_status,
        )
        if not readiness.ready and not (
            readiness.market_ready_count and readiness.investor_ready_count
        ):
            blocked_at = utc_now_iso()
            status_failure = next(
                (
                    failure
                    for failure in readiness.failed_tickers
                    if failure.get("dataset") == "TRADING_STATUS"
                ),
                None,
            )
            block_message = "Market and Investor snapshots are not aligned and complete for the source date."
            if status_failure is not None:
                block_message += f" Trading status verification failed: {status_failure['error_message']}"
            append_registry(
                paths,
                ExpandedRegistryEvent(
                    event_id=compute_event_id(
                        resolved_run_id, source_date, "READINESS_GATE_BLOCKED", STATUS_DATA_NOT_READY,
                    ),
                    run_id=resolved_run_id,
                    basDd=source_date,
                    event_type="READINESS_GATE_BLOCKED",
                    status=STATUS_DATA_NOT_READY,
                    started_at=blocked_at,
                    finished_at=blocked_at,
                    canonical_universe_count=readiness.universe_count,
                    attempted_ticker_count=0,
                    ready_count=min(readiness.market_ready_count, readiness.investor_ready_count),
                    signal_count=0,
                    quarantine_count=0,
                    error_code=STATUS_DATA_NOT_READY,
                    error_message=block_message,
                    last_stage="READINESS_GATE",
                    trading_halt_exclusions=readiness.trading_halt_exclusions,
                ),
            )
            return ExpandedDailyResult(
                status=STATUS_DATA_NOT_READY,
                action=ACTION_SKIP,
                source_date=source_date,
                readiness=readiness,
                message=block_message,
            )

        pipeline_result = pipeline_runner(
            repo_root=repo_root,
            basDd=source_date,
            market_source=PreparedMarketSource(paths, source_date),
            investor_source=PreparedInvestorSource(paths, source_date),
            retry_policy=RetryPolicy(max_attempts=1),
            run_id=resolved_run_id,
            source_commit=source_commit,
            use_lock=False,
            trading_halt_provider=trading_halt_provider,
            trading_status=readiness.trading_status,
        )
        return ExpandedDailyResult(
            status=pipeline_result.status,
            action=ACTION_RUN,
            source_date=source_date,
            readiness=readiness,
            pipeline_result=pipeline_result,
        )
    finally:
        if use_lock:
            lock.release()


def _completed_manifest(paths: ExpandedShadowPaths, source_date: str) -> dict[str, Any] | None:
    latest = paths.latest_manifest_path(source_date)
    legacy = paths.manifest_path(source_date)
    if not latest.exists() and not legacy.exists():
        return None
    manifest_path = paths.resolve_manifest_path(source_date)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpandedDailyError(f"completed manifest is unreadable: {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ExpandedDailyError(f"completed manifest is not an object: {manifest_path}")
    if payload.get("basDd", source_date) != source_date:
        raise ExpandedDailyError(f"completed manifest source date mismatch: {manifest_path}")
    if payload.get("status") not in COMPLETED_RUN_STATUSES:
        return None
    if not isinstance(payload.get("run_id"), str):
        raise ExpandedDailyError(f"completed manifest run_id is invalid: {manifest_path}")
    return payload


def _snapshot_latest_date(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, "MISSING_SNAPSHOT"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "date" not in reader.fieldnames:
                return None, "MISSING_DATE_COLUMN"
            dates: list[date] = []
            for row in reader:
                value = (row.get("date") or "").strip()
                if value:
                    dates.append(date.fromisoformat(value))
    except ValueError:
        return None, "INVALID_DATE"
    except (OSError, csv.Error, UnicodeError):
        return None, "UNREADABLE_SNAPSHOT"
    if not dates:
        return None, "EMPTY_SNAPSHOT"
    return max(dates).isoformat(), None


def _validate_source_date(source_date: str) -> None:
    try:
        parsed = date.fromisoformat(source_date)
    except (TypeError, ValueError) as exc:
        raise ExpandedDailyError(f"invalid source_date: {source_date!r}") from exc
    if parsed.isoformat() != source_date:
        raise ExpandedDailyError(f"source_date must use YYYY-MM-DD: {source_date!r}")


def _bump(distribution: dict[str, int], source_date: str | None) -> None:
    key = source_date or "missing"
    distribution[key] = distribution.get(key, 0) + 1


def _gate_failure(
    ticker: str,
    dataset: str,
    actual: str | None,
    expected: str,
    error: str | None,
) -> dict[str, str]:
    return {
        "ticker": ticker,
        "dataset": dataset,
        "source_date": actual or "missing",
        "expected_source_date": expected,
        "error_code": error or "SOURCE_DATE_MISMATCH",
    }
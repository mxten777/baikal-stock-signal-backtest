"""Snapshot preparation and source-date resolution for Expanded Shadow."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.korean_market_calendar import is_trading_day, load_holidays
from src.expanded_shadow_data import (
    DATASET_INVESTOR,
    DATASET_MARKET,
    ERROR_EMPTY_SOURCE,
    ERROR_SNAPSHOT_CONFLICT,
    ERROR_VALIDATION_FAILED,
    CollectionResult,
    InvestorSource,
    MarketSource,
    RetryPolicy,
    collect_investor_snapshot,
    collect_market_snapshot,
    validate_investor_frame,
    validate_market_frame,
)
from src.expanded_shadow_ops import COMPLETED_RUN_STATUSES, ExpandedShadowPaths
from src.expanded_shadow_universe import EXPECTED_BAS_DD, load_expanded_universe


PREPARATION_READY = "READY"
PREPARATION_DATA_NOT_READY = "DATA_NOT_READY"
PREPARATION_FAILED = "SNAPSHOT_PREPARATION_FAILED"
ERROR_SOURCE_DATE_NOT_READY = "SOURCE_DATE_NOT_READY"
ERROR_EXISTING_SNAPSHOT_INVALID = "EXISTING_SNAPSHOT_INVALID"
MARKET_START_DATE = "2024-01-01"
OPERATIONAL_CUTOFF = time(18, 30)


class ExpandedSourceDateError(ValueError):
    """Raised when no safe Expanded source date can be selected."""


class SourceDateNotReadyError(RuntimeError):
    retryable = True
    error_code = ERROR_SOURCE_DATE_NOT_READY


@dataclass(frozen=True)
class SourceDateResolution:
    source_date: str
    reason: str
    latest_completed_trading_date: str
    completed_dates: tuple[str, ...] = field(default_factory=tuple)
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SnapshotPreparationResult:
    source_date: str
    universe_count: int
    market_ready: int
    investor_ready: int
    ready_count: int
    market_failures: tuple[dict[str, Any], ...]
    investor_failures: tuple[dict[str, Any], ...]
    reused_count: int
    collected_count: int
    preparation_status: str
    market_reused: int = 0
    investor_reused: int = 0
    market_collected: int = 0
    investor_collected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExactDateSource:
    """Prevent lagging provider responses from being published under a target date."""

    def __init__(self, source: MarketSource | InvestorSource | Callable[..., pd.DataFrame], source_date: str) -> None:
        self.source = source
        self.source_date = source_date

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        fetch = getattr(self.source, "fetch", self.source)
        frame = fetch(ticker, start, end)
        if frame is None or frame.empty or "date" not in frame.columns:
            return frame
        dates = pd.to_datetime(frame["date"], errors="coerce")
        if dates.notna().any():
            latest = dates.max().strftime("%Y-%m-%d")
            if latest < self.source_date:
                raise SourceDateNotReadyError(
                    f"{ticker} source latest date {latest} is before target {self.source_date}"
                )
        return frame


def resolve_expanded_source_date(
    *,
    repo_root: Path,
    explicit_source_date: str | None = None,
    now: datetime | None = None,
    holidays: frozenset[date] | None = None,
    completed_dates: Iterable[str] | None = None,
    cutoff: time = OPERATIONAL_CUTOFF,
) -> SourceDateResolution:
    local_now = _to_seoul(now)
    holiday_set, warning = load_holidays(repo_root) if holidays is None else (holidays, None)
    latest = _latest_completed_trading_date(local_now, holiday_set, cutoff)

    if explicit_source_date is not None:
        target = _parse_date(explicit_source_date)
        if target > latest:
            raise ExpandedSourceDateError(
                f"source date {target.isoformat()} is after latest completed trading date {latest.isoformat()}"
            )
        if not is_trading_day(target, holiday_set):
            raise ExpandedSourceDateError(f"source date is not a KRX trading day: {target.isoformat()}")
        return SourceDateResolution(
            source_date=target.isoformat(),
            reason="EXPLICIT",
            latest_completed_trading_date=latest.isoformat(),
            completed_dates=tuple(sorted(_normalize_completed_dates(completed_dates or ()))),
            warning=warning,
        )

    completed = _normalize_completed_dates(
        _read_completed_dates(ExpandedShadowPaths(repo_root)) if completed_dates is None else completed_dates
    )
    if completed:
        cursor = _parse_date(EXPECTED_BAS_DD)
        while cursor <= latest:
            if is_trading_day(cursor, holiday_set) and cursor.isoformat() not in completed:
                return SourceDateResolution(
                    source_date=cursor.isoformat(),
                    reason="OLDEST_GAP",
                    latest_completed_trading_date=latest.isoformat(),
                    completed_dates=tuple(sorted(completed)),
                    warning=warning,
                )
            cursor += timedelta(days=1)

    return SourceDateResolution(
        source_date=latest.isoformat(),
        reason="LATEST_COMPLETED",
        latest_completed_trading_date=latest.isoformat(),
        completed_dates=tuple(sorted(completed)),
        warning=warning,
    )


def prepare_expanded_snapshots(
    *,
    repo_root: Path,
    source_date: str,
    market_source: MarketSource | Callable[..., pd.DataFrame],
    investor_source: InvestorSource | Callable[..., pd.DataFrame],
    retry_policy: RetryPolicy | None = None,
    tickers: Iterable[str] | None = None,
) -> SnapshotPreparationResult:
    target = _parse_date(source_date).isoformat()
    paths = ExpandedShadowPaths(repo_root)
    ticker_list = tuple(tickers) if tickers is not None else tuple(
        item.ticker
        for item in load_expanded_universe(paths.controlled_universe_path, basDd=EXPECTED_BAS_DD).tickers
    )
    policy = retry_policy or RetryPolicy(max_attempts=2)
    exact_market_source = ExactDateSource(market_source, target)
    exact_investor_source = ExactDateSource(investor_source, target)

    market_ready: set[str] = set()
    investor_ready: set[str] = set()
    market_failures: list[dict[str, Any]] = []
    investor_failures: list[dict[str, Any]] = []
    market_reused = investor_reused = market_collected = investor_collected = 0

    for ticker in ticker_list:
        market_path = paths.market_dir(target) / f"{ticker}.csv"
        market_existing = _validate_existing_snapshot(
            market_path, ticker, target, DATASET_MARKET, validate_market_frame
        )
        if market_existing is True:
            market_ready.add(ticker)
            market_reused += 1
        elif isinstance(market_existing, dict):
            market_failures.append(market_existing)
        else:
            market_result = collect_market_snapshot(
                ticker=ticker,
                basDd=target,
                paths=paths,
                source=exact_market_source,
                retry_policy=policy,
                start_date=MARKET_START_DATE,
            )
            if _is_exact_success(market_result, target):
                market_ready.add(ticker)
                market_collected += 1
            else:
                market_failures.append(_failure_payload(market_result))

        investor_path = paths.investor_dir(target) / f"{ticker}_investor.csv"
        investor_existing = _validate_existing_snapshot(
            investor_path, ticker, target, DATASET_INVESTOR, validate_investor_frame
        )
        if investor_existing is True:
            investor_ready.add(ticker)
            investor_reused += 1
        elif isinstance(investor_existing, dict):
            investor_failures.append(investor_existing)
        else:
            investor_result = collect_investor_snapshot(
                ticker=ticker,
                basDd=target,
                paths=paths,
                source=exact_investor_source,
                retry_policy=policy,
            )
            if _is_exact_success(investor_result, target):
                investor_ready.add(ticker)
                investor_collected += 1
            else:
                investor_failures.append(_failure_payload(investor_result))

    ready_count = len(market_ready & investor_ready)
    failures = market_failures + investor_failures
    if ready_count == len(ticker_list):
        status = PREPARATION_READY
    elif any(_is_blocking_failure(failure) for failure in failures):
        status = PREPARATION_FAILED
    else:
        status = PREPARATION_DATA_NOT_READY
    return SnapshotPreparationResult(
        source_date=target,
        universe_count=len(ticker_list),
        market_ready=len(market_ready),
        investor_ready=len(investor_ready),
        ready_count=ready_count,
        market_failures=tuple(market_failures),
        investor_failures=tuple(investor_failures),
        reused_count=market_reused + investor_reused,
        collected_count=market_collected + investor_collected,
        preparation_status=status,
        market_reused=market_reused,
        investor_reused=investor_reused,
        market_collected=market_collected,
        investor_collected=investor_collected,
    )


def _validate_existing_snapshot(
    path: Path,
    ticker: str,
    source_date: str,
    dataset: str,
    validator: Callable[[pd.DataFrame | None, str, str], tuple[pd.DataFrame, str]],
) -> bool | dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, dtype={"ticker": str})
        _, latest = validator(frame, ticker, source_date)
    except Exception as exc:  # noqa: BLE001 - existing immutable evidence fails closed
        return {
            "ticker": ticker,
            "dataset": dataset,
            "source_date": None,
            "attempt_count": 0,
            "error_code": ERROR_EXISTING_SNAPSHOT_INVALID,
            "error_class": type(exc).__name__,
            "error_message": str(exc),
        }
    if latest != source_date:
        return {
            "ticker": ticker,
            "dataset": dataset,
            "source_date": latest,
            "attempt_count": 0,
            "error_code": ERROR_EXISTING_SNAPSHOT_INVALID,
            "error_class": "SourceDateMismatch",
            "error_message": f"existing snapshot source date {latest} does not match target {source_date}",
        }
    return True


def _is_exact_success(result: CollectionResult, source_date: str) -> bool:
    return result.success and result.source_date == source_date


def _failure_payload(result: CollectionResult) -> dict[str, Any]:
    error_code = result.error_code
    if result.success and result.source_date is not None:
        error_code = ERROR_SOURCE_DATE_NOT_READY
    return {
        "ticker": result.ticker,
        "dataset": result.dataset_type,
        "source_date": result.source_date,
        "attempt_count": result.attempt_count,
        "error_code": error_code,
        "error_class": result.error_class,
        "error_message": result.error_message,
    }


def _is_blocking_failure(failure: dict[str, Any]) -> bool:
    return failure.get("error_code") not in {ERROR_EMPTY_SOURCE, ERROR_SOURCE_DATE_NOT_READY}


def _read_completed_dates(paths: ExpandedShadowPaths) -> set[str]:
    if not paths.registry_path.exists():
        return set()
    completed: set[str] = set()
    try:
        with paths.registry_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError(f"line {line_number} is not an object")
                if payload.get("event_type") == "RUN_COMPLETED" and payload.get("status") in COMPLETED_RUN_STATUSES:
                    completed.add(_parse_date(str(payload.get("basDd"))).isoformat())
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ExpandedSourceDateError(f"Expanded registry is malformed: {exc}") from exc
    return completed


def _normalize_completed_dates(values: Iterable[str]) -> set[str]:
    return {_parse_date(str(value)).isoformat() for value in values}


def _latest_completed_trading_date(
    local_now: datetime,
    holidays: frozenset[date],
    cutoff: time,
) -> date:
    candidate = local_now.date()
    if not is_trading_day(candidate, holidays) or local_now.timetz().replace(tzinfo=None) < cutoff:
        candidate -= timedelta(days=1)
    while not is_trading_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def _to_seoul(value: datetime | None) -> datetime:
    try:
        zone = ZoneInfo("Asia/Seoul")
    except Exception:
        zone = timezone(timedelta(hours=9), name="Asia/Seoul")
    reference = value or datetime.now(zone)
    if reference.tzinfo is None:
        return reference.replace(tzinfo=zone)
    return reference.astimezone(zone)


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ExpandedSourceDateError(f"invalid source date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ExpandedSourceDateError(f"source date must use YYYY-MM-DD: {value!r}")
    return parsed

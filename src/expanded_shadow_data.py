"""Expanded Shadow per-ticker data collection.

STEP 13-D3 only: one-ticker market/investor fetch, validation, conservative
retry, and Expanded-only snapshot writes. This module does not run the 574
batch, decide eligibility, evaluate signals, write ledgers, schedule runs, or
touch Production data paths.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import pandas as pd

from src.expanded_shadow_ops import ExpandedPathError, ExpandedShadowPaths
from src.expanded_shadow_universe import TICKER_PATTERN


MARKET_REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
MARKET_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]
INVESTOR_REQUIRED_COLUMNS = ["date", "ticker", "foreign_net_buy", "institution_net_buy"]
INVESTOR_NUMERIC_COLUMNS = ["foreign_net_buy", "institution_net_buy"]

DATASET_MARKET = "MARKET"
DATASET_INVESTOR = "INVESTOR"

ERROR_INVALID_TICKER = "INVALID_TICKER"
ERROR_EMPTY_SOURCE = "EMPTY_SOURCE"
ERROR_VALIDATION_FAILED = "VALIDATION_FAILED"
ERROR_FETCH_FAILED = "FETCH_FAILED"
ERROR_SNAPSHOT_CONFLICT = "SNAPSHOT_CONFLICT"

RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


class ExpandedDataError(RuntimeError):
    """Base class for Expanded Shadow per-ticker collection failures."""

    retryable = False
    error_code = ERROR_FETCH_FAILED


class ExpandedDataValidationError(ExpandedDataError):
    error_code = ERROR_VALIDATION_FAILED


class ExpandedTemporaryEmptyError(ExpandedDataError):
    retryable = True
    error_code = ERROR_EMPTY_SOURCE


class ExpandedSnapshotConflictError(ExpandedDataError):
    error_code = ERROR_SNAPSHOT_CONFLICT



class MarketSource(Protocol):
    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        ...


class InvestorSource(Protocol):
    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        ...


class FinanceDataReaderMarketSource:
    """Per-ticker FinanceDataReader wrapper; no Production updater reuse."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        import FinanceDataReader as fdr

        frame = fdr.DataReader(ticker, start, end)
        if frame is None:
            raise ExpandedTemporaryEmptyError("market source returned None")
        frame = frame.reset_index()
        return frame.rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )


class NaverInvestorFlowSource:
    """Per-ticker Naver investor wrapper; never coerces ticker to int."""

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        from src.data_provider.naver_investor_provider import fetch_investor_flow

        return fetch_investor_flow(ticker, start, end)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    retry_sleep_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.retry_sleep_seconds < 0:
            raise ValueError("retry_sleep_seconds must be non-negative")


@dataclass(frozen=True)
class CollectionResult:
    ticker: str
    dataset_type: str
    success: bool
    attempt_count: int
    source_date: str | None
    row_count: int
    snapshot_path: str | None
    error_code: str | None
    error_class: str | None
    error_message: str | None
    runtime_seconds: float


def validate_ticker(ticker: str) -> str:
    if not isinstance(ticker, str) or not re.fullmatch(TICKER_PATTERN, ticker):
        raise ExpandedDataValidationError(f"invalid ticker identifier: {ticker!r}")
    return ticker


def validate_market_frame(frame: pd.DataFrame | None, ticker: str, basDd: str) -> tuple[pd.DataFrame, str]:
    validate_ticker(ticker)
    normalized = _normalize_frame(frame, MARKET_REQUIRED_COLUMNS, "market")
    if "ticker" in normalized.columns:
        _validate_ticker_identity(normalized["ticker"], ticker, "market")
    _validate_dates(normalized, basDd, "market")
    _validate_market_numeric(normalized)
    return normalized[MARKET_REQUIRED_COLUMNS].copy(), _latest_source_date(normalized)


def validate_investor_frame(frame: pd.DataFrame | None, ticker: str, basDd: str) -> tuple[pd.DataFrame, str]:
    validate_ticker(ticker)
    normalized = _normalize_frame(frame, INVESTOR_REQUIRED_COLUMNS, "investor")
    _validate_ticker_identity(normalized["ticker"], ticker, "investor")
    _validate_dates(normalized, basDd, "investor")
    for column in INVESTOR_NUMERIC_COLUMNS:
        values = pd.to_numeric(normalized[column], errors="coerce")
        if values.isna().any():
            raise ExpandedDataValidationError(f"investor numeric invalid: {column}")
        normalized[column] = values.astype("Int64")
    return normalized[INVESTOR_REQUIRED_COLUMNS].copy(), _latest_source_date(normalized)


def write_market_snapshot(paths: ExpandedShadowPaths, basDd: str, ticker: str, frame: pd.DataFrame) -> Path:
    validate_ticker(ticker)
    path = paths.market_dir(basDd) / f"{ticker}.csv"
    return _write_snapshot(paths, path, frame)


def write_investor_snapshot(paths: ExpandedShadowPaths, basDd: str, ticker: str, frame: pd.DataFrame) -> Path:
    validate_ticker(ticker)
    path = paths.investor_dir(basDd) / f"{ticker}_investor.csv"
    return _write_snapshot(paths, path, frame)


def collect_market_snapshot(
    *,
    ticker: str,
    basDd: str,
    paths: ExpandedShadowPaths,
    source: MarketSource | Callable[[str, str, str], pd.DataFrame],
    retry_policy: RetryPolicy | None = None,
    start_date: str = "1900-01-01",
) -> CollectionResult:
    return _collect_snapshot(
        ticker=ticker,
        basDd=basDd,
        paths=paths,
        source=source,
        retry_policy=retry_policy or RetryPolicy(),
        start_date=start_date,
        dataset_type=DATASET_MARKET,
        validator=validate_market_frame,
        writer=write_market_snapshot,
    )


def collect_investor_snapshot(
    *,
    ticker: str,
    basDd: str,
    paths: ExpandedShadowPaths,
    source: InvestorSource | Callable[[str, str, str], pd.DataFrame],
    retry_policy: RetryPolicy | None = None,
    start_date: str = "1900-01-01",
) -> CollectionResult:
    return _collect_snapshot(
        ticker=ticker,
        basDd=basDd,
        paths=paths,
        source=source,
        retry_policy=retry_policy or RetryPolicy(),
        start_date=start_date,
        dataset_type=DATASET_INVESTOR,
        validator=validate_investor_frame,
        writer=write_investor_snapshot,
    )


def _collect_snapshot(
    *,
    ticker: str,
    basDd: str,
    paths: ExpandedShadowPaths,
    source: Any,
    retry_policy: RetryPolicy,
    start_date: str,
    dataset_type: str,
    validator: Callable[[pd.DataFrame | None, str, str], tuple[pd.DataFrame, str]],
    writer: Callable[[ExpandedShadowPaths, str, str, pd.DataFrame], Path],
) -> CollectionResult:
    started = time.monotonic()
    attempt = 0
    last_error: BaseException | None = None

    try:
        validate_ticker(ticker)
    except BaseException as exc:
        return _failure_result(ticker, dataset_type, 0, started, exc, ERROR_INVALID_TICKER)

    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            frame = _fetch(source, ticker, start_date, basDd)
            if frame is None or frame.empty:
                raise ExpandedTemporaryEmptyError(f"{dataset_type.lower()} source returned empty data")
            validated, source_date = validator(frame, ticker, basDd)
            snapshot_path = writer(paths, basDd, ticker, validated)
            return CollectionResult(
                ticker=ticker,
                dataset_type=dataset_type,
                success=True,
                attempt_count=attempt,
                source_date=source_date,
                row_count=len(validated),
                snapshot_path=str(snapshot_path),
                error_code=None,
                error_class=None,
                error_message=None,
                runtime_seconds=_elapsed(started),
            )
        except BaseException as exc:  # noqa: BLE001 - failures are returned as ticker evidence
            last_error = exc
            if attempt >= retry_policy.max_attempts or not _is_retryable(exc):
                break
            if retry_policy.retry_sleep_seconds:
                time.sleep(retry_policy.retry_sleep_seconds)

    return _failure_result(
        ticker,
        dataset_type,
        attempt,
        started,
        last_error or ExpandedDataError("unknown collection failure"),
        getattr(last_error, "error_code", ERROR_FETCH_FAILED),
    )


def _normalize_frame(frame: pd.DataFrame | None, required: list[str], label: str) -> pd.DataFrame:
    if frame is None:
        raise ExpandedDataValidationError(f"{label} frame is None")
    if not isinstance(frame, pd.DataFrame):
        raise ExpandedDataValidationError(f"{label} source did not return a DataFrame")
    if frame.empty:
        raise ExpandedTemporaryEmptyError(f"{label} frame is empty")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ExpandedDataValidationError(f"{label} schema missing columns: {missing}")
    return frame.copy()


def _validate_ticker_identity(series: pd.Series, ticker: str, label: str) -> None:
    values = series.astype(str)
    if values.isna().any() or values.ne(ticker).any():
        bad = values[values.ne(ticker)].head(5).tolist()
        raise ExpandedDataValidationError(f"{label} ticker identity mismatch: {bad}")


def _validate_dates(frame: pd.DataFrame, basDd: str, label: str) -> None:
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise ExpandedDataValidationError(f"{label} invalid date value")
    if dates.duplicated().any():
        raise ExpandedDataValidationError(f"{label} duplicate date")
    if not dates.is_monotonic_increasing:
        raise ExpandedDataValidationError(f"{label} dates not ascending")
    target = pd.Timestamp(basDd)
    if (dates > target).any():
        first = dates[dates > target].iloc[0].strftime("%Y-%m-%d")
        raise ExpandedDataValidationError(f"{label} future date {first}")
    frame["date"] = dates.dt.strftime("%Y-%m-%d")


def _validate_market_numeric(frame: pd.DataFrame) -> None:
    for column in MARKET_NUMERIC_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any():
            raise ExpandedDataValidationError(f"market numeric invalid: {column}")
        frame[column] = values
    price_invalid = (frame[["open", "high", "low", "close"]] <= 0).any().any()
    if bool(price_invalid):
        raise ExpandedDataValidationError("market price must be positive")
    if bool((frame["volume"] < 0).any()):
        raise ExpandedDataValidationError("market volume must be non-negative")
    if bool((frame["high"] < frame["low"]).any()):
        raise ExpandedDataValidationError("market OHLC invalid: high below low")
    if bool((frame["high"] < frame["open"]).any() or (frame["high"] < frame["close"]).any()):
        raise ExpandedDataValidationError("market OHLC invalid: high below open/close")
    if bool((frame["low"] > frame["open"]).any() or (frame["low"] > frame["close"]).any()):
        raise ExpandedDataValidationError("market OHLC invalid: low above open/close")


def _latest_source_date(frame: pd.DataFrame) -> str:
    return str(frame["date"].iloc[-1])


def _fetch(source: Any, ticker: str, start: str, end: str) -> pd.DataFrame:
    fetch = getattr(source, "fetch", source)
    return fetch(ticker, start, end)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ExpandedDataValidationError) and not isinstance(exc, ExpandedTemporaryEmptyError):
        return False
    if bool(getattr(exc, "retryable", False)):
        return True
    status = _extract_http_status(exc)
    if status in RETRYABLE_HTTP_STATUSES:
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in ("timeout", "timed out", "connection reset", "connection aborted", "temporar"))


def _extract_http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _failure_result(
    ticker: str,
    dataset_type: str,
    attempt_count: int,
    started: float,
    exc: BaseException,
    error_code: str | None,
) -> CollectionResult:
    return CollectionResult(
        ticker=ticker,
        dataset_type=dataset_type,
        success=False,
        attempt_count=attempt_count,
        source_date=None,
        row_count=0,
        snapshot_path=None,
        error_code=error_code or ERROR_FETCH_FAILED,
        error_class=type(exc).__name__,
        error_message=str(exc),
        runtime_seconds=_elapsed(started),
    )


def _write_snapshot(paths: ExpandedShadowPaths, path: Path, frame: pd.DataFrame) -> Path:
    target = paths.validate_data_path(path)
    payload = _csv_bytes(frame)
    digest = hashlib.sha256(payload).hexdigest()
    if target.exists():
        existing_digest = _file_sha256(target)
        if existing_digest != digest:
            raise ExpandedSnapshotConflictError(f"conflicting snapshot exists: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return target


def _csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _elapsed(started: float) -> float:
    return max(time.monotonic() - started, 0.0)
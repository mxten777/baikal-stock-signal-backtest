"""Expanded Shadow per-ticker eligibility classification.

STEP 13-D4 only: classify already-collected D3 market/investor evidence into
one per-ticker status. This module does not fetch data, evaluate signals,
write ledgers, or create runtime artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.expanded_shadow_data import (
    DATASET_INVESTOR,
    DATASET_MARKET,
    ERROR_SNAPSHOT_CONFLICT,
    ERROR_VALIDATION_FAILED,
    CollectionResult,
)
from src.expanded_shadow_universe import TICKER_PATTERN


STATUS_READY = "READY"
STATUS_MARKET_FAILED = "MARKET_FAILED"
STATUS_INVESTOR_FAILED = "INVESTOR_FAILED"
STATUS_SOURCE_LAG = "SOURCE_LAG"
STATUS_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STATUS_DATA_INVALID = "DATA_INVALID"

ALL_ELIGIBILITY_STATUSES = frozenset(
    {
        STATUS_READY,
        STATUS_MARKET_FAILED,
        STATUS_INVESTOR_FAILED,
        STATUS_SOURCE_LAG,
        STATUS_INSUFFICIENT_HISTORY,
        STATUS_DATA_INVALID,
    }
)

HISTORY_READY_ROW_COUNT = 60
DATA_INVALID_ERROR_CODES = frozenset({ERROR_VALIDATION_FAILED, ERROR_SNAPSHOT_CONFLICT})
DATA_INVALID_MARKERS = (
    "schema",
    "numeric",
    "duplicate",
    "future",
    "identity",
    "mismatch",
    "conflict",
    "invalid",
)


@dataclass(frozen=True)
class EligibilityResult:
    ticker: str
    basDd: str
    status: str
    market_success: bool
    investor_success: bool
    market_source_date: str | None
    investor_source_date: str | None
    market_row_count: int
    investor_row_count: int
    market_attempt_count: int
    investor_attempt_count: int
    lagging_datasets: tuple[str, ...]
    error_code: str | None
    error_class: str | None
    error_message: str | None


def classify_ticker_eligibility(
    *,
    ticker: str,
    basDd: str,
    market: CollectionResult,
    investor: CollectionResult,
) -> EligibilityResult:
    """Classify one canonical ticker from D3 collection evidence only.

    Precedence is deterministic and fail-closed:
    MARKET_FAILED -> INVESTOR_FAILED -> DATA_INVALID -> SOURCE_LAG ->
    INSUFFICIENT_HISTORY -> READY.
    """
    _validate_inputs(ticker, market, investor)
    base = _base_result(ticker, basDd, market, investor)

    if not market.success and not _is_data_invalid(market):
        return _with_failure(base, STATUS_MARKET_FAILED, market)
    if not investor.success and not _is_data_invalid(investor):
        return _with_failure(base, STATUS_INVESTOR_FAILED, investor)

    invalid = _first_data_invalid(market, investor)
    if invalid is not None:
        return _with_failure(base, STATUS_DATA_INVALID, invalid)

    future_source = _future_source_result(basDd, market, investor)
    if future_source is not None:
        return _with_failure(base, STATUS_DATA_INVALID, future_source)

    lagging = _lagging_datasets(basDd, market, investor)
    if lagging:
        return EligibilityResult(**{**base.__dict__, "status": STATUS_SOURCE_LAG, "lagging_datasets": lagging})

    if market.row_count < HISTORY_READY_ROW_COUNT:
        return EligibilityResult(**{**base.__dict__, "status": STATUS_INSUFFICIENT_HISTORY})

    return EligibilityResult(**{**base.__dict__, "status": STATUS_READY})


def _validate_inputs(ticker: str, market: CollectionResult, investor: CollectionResult) -> None:
    if not isinstance(ticker, str) or not re.fullmatch(TICKER_PATTERN, ticker):
        raise ValueError(f"invalid canonical ticker: {ticker!r}")
    if market.ticker != ticker:
        raise ValueError(f"market result ticker mismatch: {market.ticker!r} != {ticker!r}")
    if investor.ticker != ticker:
        raise ValueError(f"investor result ticker mismatch: {investor.ticker!r} != {ticker!r}")
    if market.dataset_type != DATASET_MARKET:
        raise ValueError(f"market result has wrong dataset_type: {market.dataset_type!r}")
    if investor.dataset_type != DATASET_INVESTOR:
        raise ValueError(f"investor result has wrong dataset_type: {investor.dataset_type!r}")


def _base_result(
    ticker: str,
    basDd: str,
    market: CollectionResult,
    investor: CollectionResult,
) -> EligibilityResult:
    return EligibilityResult(
        ticker=ticker,
        basDd=basDd,
        status=STATUS_DATA_INVALID,
        market_success=market.success,
        investor_success=investor.success,
        market_source_date=market.source_date,
        investor_source_date=investor.source_date,
        market_row_count=market.row_count,
        investor_row_count=investor.row_count,
        market_attempt_count=market.attempt_count,
        investor_attempt_count=investor.attempt_count,
        lagging_datasets=(),
        error_code=None,
        error_class=None,
        error_message=None,
    )


def _with_failure(
    base: EligibilityResult,
    status: str,
    evidence: CollectionResult,
) -> EligibilityResult:
    return EligibilityResult(
        **{
            **base.__dict__,
            "status": status,
            "error_code": evidence.error_code,
            "error_class": evidence.error_class,
            "error_message": evidence.error_message,
        }
    )


def _is_data_invalid(result: CollectionResult) -> bool:
    if result.success:
        return False
    if result.error_code in DATA_INVALID_ERROR_CODES:
        return True
    text = " ".join(
        str(value or "")
        for value in (result.error_code, result.error_class, result.error_message)
    ).lower()
    return any(marker in text for marker in DATA_INVALID_MARKERS)


def _first_data_invalid(*results: CollectionResult) -> CollectionResult | None:
    return next((result for result in results if _is_data_invalid(result)), None)


def _future_source_result(
    basDd: str,
    market: CollectionResult,
    investor: CollectionResult,
) -> CollectionResult | None:
    for result in (market, investor):
        if result.success and result.source_date is not None and result.source_date > basDd:
            return CollectionResult(
                ticker=result.ticker,
                dataset_type=result.dataset_type,
                success=False,
                attempt_count=result.attempt_count,
                source_date=result.source_date,
                row_count=result.row_count,
                snapshot_path=result.snapshot_path,
                error_code=ERROR_VALIDATION_FAILED,
                error_class="FutureSourceDate",
                error_message=f"{result.dataset_type} source_date {result.source_date} is after basDd {basDd}",
                runtime_seconds=result.runtime_seconds,
            )
    return None


def _lagging_datasets(
    basDd: str,
    market: CollectionResult,
    investor: CollectionResult,
) -> tuple[str, ...]:
    lagging: list[str] = []
    if market.source_date is not None and market.source_date < basDd:
        lagging.append(DATASET_MARKET)
    if investor.source_date is not None and investor.source_date < basDd:
        lagging.append(DATASET_INVESTOR)
    return tuple(lagging)
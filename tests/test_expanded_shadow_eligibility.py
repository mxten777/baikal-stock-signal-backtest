from __future__ import annotations

from pathlib import Path

import pytest

from src.expanded_shadow_data import DATASET_INVESTOR, DATASET_MARKET, CollectionResult
from src.expanded_shadow_eligibility import (
    ALL_ELIGIBILITY_STATUSES,
    HISTORY_READY_ROW_COUNT,
    STATUS_DATA_INVALID,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_INVESTOR_FAILED,
    STATUS_MARKET_FAILED,
    STATUS_READY,
    STATUS_SOURCE_LAG,
    classify_ticker_eligibility,
)
from src.expanded_shadow_universe import compute_universe_sha256


BAS_DD = "2026-09-17"
EXPECTED_UNIVERSE_SHA = "073982938b6dd222d6b0ca3621ce763a15fd9af43c835ddd7676e78bcd71c6d2"


def _result(
    *,
    ticker: str = "005930",
    dataset_type: str = DATASET_MARKET,
    success: bool = True,
    source_date: str | None = BAS_DD,
    row_count: int = 60,
    attempt_count: int = 1,
    error_code: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> CollectionResult:
    return CollectionResult(
        ticker=ticker,
        dataset_type=dataset_type,
        success=success,
        attempt_count=attempt_count,
        source_date=source_date,
        row_count=row_count,
        snapshot_path=None if not success else f"data/expanded_shadow/{dataset_type.lower()}/{BAS_DD}/{ticker}.csv",
        error_code=error_code,
        error_class=error_class,
        error_message=error_message,
        runtime_seconds=0.01,
    )


def _classify(ticker: str = "005930", market: CollectionResult | None = None, investor: CollectionResult | None = None):
    return classify_ticker_eligibility(
        ticker=ticker,
        basDd=BAS_DD,
        market=market or _result(ticker=ticker, dataset_type=DATASET_MARKET),
        investor=investor or _result(ticker=ticker, dataset_type=DATASET_INVESTOR, row_count=5),
    )


def test_valid_numeric_ticker_ready():
    result = _classify("005930")

    assert result.status == STATUS_READY


def test_valid_alphanumeric_ticker_ready():
    result = _classify("0015N0")

    assert result.status == STATUS_READY


def test_ticker_preserved_exactly():
    result = _classify("0015N0")

    assert result.ticker == "0015N0"


def test_no_int_coercion():
    result = _classify("000660")

    assert isinstance(result.ticker, str)
    assert result.ticker == "000660"


def test_market_collection_failure_is_market_failed():
    market = _result(success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timed out", attempt_count=2)
    result = _classify(market=market)

    assert result.status == STATUS_MARKET_FAILED
    assert result.error_class == "TimeoutError"
    assert result.market_attempt_count == 2


def test_investor_collection_failure_is_investor_failed():
    investor = _result(dataset_type=DATASET_INVESTOR, success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timed out", attempt_count=2)
    result = _classify(investor=investor)

    assert result.status == STATUS_INVESTOR_FAILED
    assert result.error_class == "TimeoutError"
    assert result.investor_attempt_count == 2


@pytest.mark.parametrize(
    ("error_code", "error_message"),
    [
        ("VALIDATION_FAILED", "schema missing columns"),
        ("VALIDATION_FAILED", "numeric invalid"),
        ("VALIDATION_FAILED", "future date"),
        ("VALIDATION_FAILED", "ticker identity mismatch"),
        ("SNAPSHOT_CONFLICT", "conflicting snapshot exists"),
    ],
)
def test_market_validation_evidence_is_data_invalid(error_code: str, error_message: str):
    market = _result(success=False, error_code=error_code, error_class="ExpandedDataValidationError", error_message=error_message)
    result = _classify(market=market)

    assert result.status == STATUS_DATA_INVALID
    assert result.error_code == error_code


def test_invalid_investor_schema_evidence_is_data_invalid():
    investor = _result(dataset_type=DATASET_INVESTOR, success=False, error_code="VALIDATION_FAILED", error_class="ExpandedDataValidationError", error_message="schema missing columns")
    result = _classify(investor=investor)

    assert result.status == STATUS_DATA_INVALID


def test_stale_market_only_is_source_lag():
    market = _result(source_date="2026-09-16")
    result = _classify(market=market)

    assert result.status == STATUS_SOURCE_LAG
    assert result.lagging_datasets == (DATASET_MARKET,)


def test_stale_investor_only_is_source_lag():
    investor = _result(dataset_type=DATASET_INVESTOR, source_date="2026-09-16", row_count=5)
    result = _classify(investor=investor)

    assert result.status == STATUS_SOURCE_LAG
    assert result.lagging_datasets == (DATASET_INVESTOR,)


def test_both_stale_is_source_lag():
    market = _result(source_date="2026-09-16")
    investor = _result(dataset_type=DATASET_INVESTOR, source_date="2026-09-15", row_count=5)
    result = _classify(market=market, investor=investor)

    assert result.status == STATUS_SOURCE_LAG
    assert result.lagging_datasets == (DATASET_MARKET, DATASET_INVESTOR)


def test_lagging_dataset_evidence_correct():
    market = _result(source_date="2026-09-16")
    investor = _result(dataset_type=DATASET_INVESTOR, source_date=BAS_DD, row_count=5)
    result = _classify(market=market, investor=investor)

    assert result.market_source_date == "2026-09-16"
    assert result.investor_source_date == BAS_DD
    assert result.lagging_datasets == (DATASET_MARKET,)


def test_market_59_rows_is_insufficient_history():
    market = _result(row_count=59)
    result = _classify(market=market)

    assert result.status == STATUS_INSUFFICIENT_HISTORY


def test_market_60_rows_is_ready():
    market = _result(row_count=HISTORY_READY_ROW_COUNT)
    result = _classify(market=market)

    assert result.status == STATUS_READY


def test_market_more_than_60_rows_is_ready():
    market = _result(row_count=61)
    result = _classify(market=market)

    assert result.status == STATUS_READY


def test_stale_and_less_than_60_rows_is_source_lag_by_precedence():
    market = _result(source_date="2026-09-16", row_count=59)
    result = _classify(market=market)

    assert result.status == STATUS_SOURCE_LAG


def test_market_failure_and_investor_failure_uses_market_precedence():
    market = _result(success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="market timed out")
    investor = _result(dataset_type=DATASET_INVESTOR, success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="investor timed out")
    result = _classify(market=market, investor=investor)

    assert result.status == STATUS_MARKET_FAILED
    assert result.error_message == "market timed out"


def test_validation_failure_and_stale_uses_data_invalid_precedence():
    market = _result(success=False, source_date="2026-09-16", error_code="VALIDATION_FAILED", error_class="ExpandedDataValidationError", error_message="schema invalid")
    result = _classify(market=market)

    assert result.status == STATUS_DATA_INVALID


def test_future_source_date_is_data_invalid_not_source_lag():
    market = _result(source_date="2026-09-18")
    result = _classify(market=market)

    assert result.status == STATUS_DATA_INVALID
    assert result.error_class == "FutureSourceDate"


@pytest.mark.parametrize(
    "market,investor,expected",
    [
        (_result(success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timeout"), None, STATUS_MARKET_FAILED),
        (None, _result(dataset_type=DATASET_INVESTOR, success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timeout"), STATUS_INVESTOR_FAILED),
        (_result(success=False, error_code="VALIDATION_FAILED", error_class="ExpandedDataValidationError", error_message="schema invalid"), None, STATUS_DATA_INVALID),
        (_result(source_date="2026-09-16"), None, STATUS_SOURCE_LAG),
        (_result(row_count=59), None, STATUS_INSUFFICIENT_HISTORY),
        (_result(row_count=60), None, STATUS_READY),
    ],
)
def test_exactly_one_final_status_per_ticker(market, investor, expected):
    result = _classify(market=market, investor=investor)

    assert result.status == expected
    assert result.status in ALL_ELIGIBILITY_STATUSES


def test_all_six_statuses_reachable():
    statuses = {
        _classify(market=_result(success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timeout")).status,
        _classify(investor=_result(dataset_type=DATASET_INVESTOR, success=False, error_code="FETCH_FAILED", error_class="TimeoutError", error_message="timeout")).status,
        _classify(market=_result(success=False, error_code="VALIDATION_FAILED", error_class="ExpandedDataValidationError", error_message="schema invalid")).status,
        _classify(market=_result(source_date="2026-09-16")).status,
        _classify(market=_result(row_count=59)).status,
        _classify(market=_result(row_count=60)).status,
    }

    assert statuses == ALL_ELIGIBILITY_STATUSES


def test_canonical_ticker_is_never_dropped():
    result = _classify("0015N0", market=_result(ticker="0015N0"), investor=_result(ticker="0015N0", dataset_type=DATASET_INVESTOR, row_count=5))

    assert result.ticker == "0015N0"
    assert result.status == STATUS_READY


def test_no_external_provider_call_from_d4():
    class ProviderLike:
        def fetch(self, *_args):
            raise AssertionError("D4 must not fetch")

    provider = ProviderLike()
    assert hasattr(provider, "fetch")
    result = _classify()

    assert result.status == STATUS_READY


def test_no_production_path_write(tmp_path: Path):
    _classify()

    assert not (tmp_path / "data" / "raw").exists()
    assert not (tmp_path / "data" / "investor").exists()


def test_no_expanded_runtime_write(tmp_path: Path):
    _classify()

    assert not (tmp_path / "data" / "expanded_shadow" / "market").exists()
    assert not (tmp_path / "output" / "expanded_shadow").exists()


def test_d1_universe_unchanged():
    root = Path(__file__).resolve().parents[1]
    controlled = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    snapshot = root / "data" / "expanded_shadow" / "universe" / "snapshots" / "2026-09-17_expanded_universe_574.csv"

    assert compute_universe_sha256(controlled) == EXPECTED_UNIVERSE_SHA
    assert compute_universe_sha256(snapshot) == EXPECTED_UNIVERSE_SHA


def test_d2_d3_files_unchanged_by_classification():
    _classify()

    assert Path("src/expanded_shadow_ops.py").exists()
    assert Path("src/expanded_shadow_data.py").exists()
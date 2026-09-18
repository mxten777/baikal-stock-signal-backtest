from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.expanded_shadow_data import (
    DATASET_INVESTOR,
    DATASET_MARKET,
    ERROR_EMPTY_SOURCE,
    ERROR_INVALID_TICKER,
    ERROR_VALIDATION_FAILED,
    CollectionResult,
    ExpandedSnapshotConflictError,
    ExpandedTemporaryEmptyError,
    RetryPolicy,
    collect_investor_snapshot,
    collect_market_snapshot,
    validate_investor_frame,
    validate_market_frame,
)
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_universe import compute_universe_sha256


BAS_DD = "2026-09-17"
EXPECTED_UNIVERSE_SHA = "073982938b6dd222d6b0ca3621ce763a15fd9af43c835ddd7676e78bcd71c6d2"


class TimeoutSource:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if len(self.calls) == 1:
            raise TimeoutError("timed out")
        return self.frame.copy()


class SequenceSource:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, ticker: str, start: str, end: str):
        self.calls.append((ticker, start, end))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value.copy() if isinstance(value, pd.DataFrame) else value


class StatusCodeError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class TypeCheckingSource:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.seen_type: type | None = None
        self.seen_value = None

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.seen_type = type(ticker)
        self.seen_value = ticker
        return self.frame.copy()


def _paths(tmp_path: Path) -> ExpandedShadowPaths:
    return ExpandedShadowPaths(tmp_path)


def _market_frame(ticker: str = "005930") -> pd.DataFrame:
    dates = pd.date_range("2026-09-15", periods=3, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [100, 101, 102],
            "high": [110, 111, 112],
            "low": [90, 91, 92],
            "close": [105, 106, 107],
            "volume": [1000, 1001, 1002],
            "ticker": [ticker, ticker, ticker],
        }
    )


def _investor_frame(ticker: str = "005930") -> pd.DataFrame:
    dates = pd.date_range("2026-09-15", periods=3, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "ticker": [ticker, ticker, ticker],
            "foreign_net_buy": [1, -2, 3],
            "institution_net_buy": [4, -5, 6],
        }
    )


def test_numeric_market_ticker_succeeds(tmp_path: Path):
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _market_frame("005930"))

    assert result.success is True
    assert result.dataset_type == DATASET_MARKET
    assert result.source_date == BAS_DD


def test_alphanumeric_market_ticker_preserved(tmp_path: Path):
    result = collect_market_snapshot(ticker="0015N0", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _market_frame("0015N0"))

    assert result.success is True
    assert result.ticker == "0015N0"
    assert result.snapshot_path is not None and result.snapshot_path.endswith("0015N0.csv")


def test_numeric_investor_ticker_succeeds(tmp_path: Path):
    result = collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _investor_frame("005930"))

    assert result.success is True
    assert result.dataset_type == DATASET_INVESTOR


def test_alphanumeric_investor_ticker_preserved(tmp_path: Path):
    paths = _paths(tmp_path)
    result = collect_investor_snapshot(ticker="0015N0", basDd=BAS_DD, paths=paths, source=lambda *_: _investor_frame("0015N0"))

    saved = pd.read_csv(paths.investor_dir(BAS_DD) / "0015N0_investor.csv", dtype={"ticker": str})
    assert result.success is True
    assert saved["ticker"].tolist() == ["0015N0", "0015N0", "0015N0"]


def test_no_int_coercion(tmp_path: Path):
    source = TypeCheckingSource(_investor_frame("0015N0"))
    result = collect_investor_snapshot(ticker="0015N0", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is True
    assert source.seen_type is str
    assert source.seen_value == "0015N0"


def test_market_timeout_retries_once_then_succeeds(tmp_path: Path):
    source = TimeoutSource(_market_frame("005930"))
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is True
    assert result.attempt_count == 2
    assert len(source.calls) == 2


def test_investor_timeout_retries_once_then_succeeds(tmp_path: Path):
    source = TimeoutSource(_investor_frame("005930"))
    result = collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is True
    assert result.attempt_count == 2
    assert len(source.calls) == 2


def test_max_two_attempts(tmp_path: Path):
    source = SequenceSource([TimeoutError("timed out"), TimeoutError("timed out"), _market_frame()])
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source, retry_policy=RetryPolicy(max_attempts=2))

    assert result.success is False
    assert result.attempt_count == 2
    assert len(source.calls) == 2


def test_non_retryable_validation_failure_not_retried(tmp_path: Path):
    frame = _market_frame().drop(columns=["volume"])
    source = SequenceSource([frame, _market_frame()])
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is False
    assert result.error_code == ERROR_VALIDATION_FAILED
    assert len(source.calls) == 1


def test_http_429_retry_behavior(tmp_path: Path):
    source = SequenceSource([StatusCodeError(429), _market_frame()])
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is True
    assert result.attempt_count == 2


def test_temporary_empty_response_retry(tmp_path: Path):
    source = SequenceSource([pd.DataFrame(), _investor_frame()])
    result = collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert result.success is True
    assert result.attempt_count == 2


def test_invalid_market_schema_fails(tmp_path: Path):
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _market_frame().drop(columns=["close"]))

    assert result.success is False
    assert result.error_code == ERROR_VALIDATION_FAILED


def test_invalid_investor_schema_fails(tmp_path: Path):
    result = collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _investor_frame().drop(columns=["ticker"]))

    assert result.success is False
    assert result.error_code == ERROR_VALIDATION_FAILED


def test_duplicate_market_date_fails():
    frame = _market_frame()
    frame.loc[1, "date"] = frame.loc[0, "date"]

    with pytest.raises(Exception, match="duplicate date"):
        validate_market_frame(frame, "005930", BAS_DD)


def test_duplicate_investor_date_fails():
    frame = _investor_frame()
    frame.loc[1, "date"] = frame.loc[0, "date"]

    with pytest.raises(Exception, match="duplicate date"):
        validate_investor_frame(frame, "005930", BAS_DD)


def test_future_market_date_fails():
    frame = _market_frame()
    frame.loc[2, "date"] = "2026-09-18"

    with pytest.raises(Exception, match="future date"):
        validate_market_frame(frame, "005930", BAS_DD)


def test_future_investor_date_fails():
    frame = _investor_frame()
    frame.loc[2, "date"] = "2026-09-18"

    with pytest.raises(Exception, match="future date"):
        validate_investor_frame(frame, "005930", BAS_DD)


def test_invalid_market_numeric_value_fails():
    frame = _market_frame()
    frame["close"] = frame["close"].astype(object)
    frame.loc[0, "close"] = "bad"

    with pytest.raises(Exception, match="numeric invalid"):
        validate_market_frame(frame, "005930", BAS_DD)


def test_market_non_trading_row_removed_before_snapshot(tmp_path: Path):
    frame = _market_frame()
    frame.loc[1, ["open", "high", "low", "volume"]] = 0
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: frame)

    saved = pd.read_csv(result.snapshot_path)
    assert result.success is True
    assert result.row_count == 2
    assert saved["date"].tolist() == ["2026-09-15", "2026-09-17"]


def test_market_zero_price_with_volume_fails_with_row_evidence(tmp_path: Path):
    frame = _market_frame("033790")
    frame.loc[1, ["open", "high", "low"]] = 0
    result = collect_market_snapshot(ticker="033790", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: frame)

    assert result.success is False
    assert result.error_code == ERROR_VALIDATION_FAILED
    assert result.error_message is not None
    message = result.error_message
    assert "market price must be positive" in message
    assert "ticker=033790" in message
    assert "date=2026-09-16" in message
    assert "open=0" in message
    assert "high=0" in message
    assert "low=0" in message
    assert "close=106" in message
    assert "volume=1001" in message


def test_market_high_rounding_difference_up_to_two_won_passes():
    frame = _market_frame()
    frame.loc[0, "high"] = frame.loc[0, "close"] - 1
    frame.loc[1, "high"] = frame.loc[1, "close"] - 2

    validated, _ = validate_market_frame(frame, "005930", BAS_DD)

    assert validated["high"].tolist()[:2] == [104, 104]


def test_market_high_inversion_over_two_won_fails_with_row_evidence():
    frame = _market_frame()
    frame.loc[0, "high"] = frame.loc[0, "close"] - 3

    with pytest.raises(Exception, match="market OHLC invalid: high below open/close") as error:
        validate_market_frame(frame, "005930", BAS_DD)

    message = str(error.value)
    assert "ticker=005930" in message
    assert "date=2026-09-15" in message
    assert "high=102" in message
    assert "close=105" in message


def test_market_all_non_trading_rows_preserve_empty_source_contract():
    frame = _market_frame()
    frame[["open", "high", "low", "volume"]] = 0

    with pytest.raises(ExpandedTemporaryEmptyError, match="market frame is empty"):
        validate_market_frame(frame, "005930", BAS_DD)


def test_invalid_investor_numeric_value_fails():
    frame = _investor_frame()
    frame["foreign_net_buy"] = frame["foreign_net_buy"].astype(object)
    frame.loc[0, "foreign_net_buy"] = "bad"

    with pytest.raises(Exception, match="numeric invalid"):
        validate_investor_frame(frame, "005930", BAS_DD)


def test_market_snapshot_correct_expanded_path(tmp_path: Path):
    paths = _paths(tmp_path)
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: _market_frame())

    assert result.snapshot_path == str(paths.market_dir(BAS_DD) / "005930.csv")


def test_investor_snapshot_correct_expanded_path(tmp_path: Path):
    paths = _paths(tmp_path)
    result = collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: _investor_frame())

    assert result.snapshot_path == str(paths.investor_dir(BAS_DD) / "005930_investor.csv")


def test_identical_snapshot_rerun_idempotent(tmp_path: Path):
    paths = _paths(tmp_path)
    first = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: _market_frame())
    second = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: _market_frame())

    assert first.success is True
    assert second.success is True
    assert first.snapshot_path == second.snapshot_path


def test_conflicting_snapshot_fails_closed(tmp_path: Path):
    paths = _paths(tmp_path)
    collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: _market_frame())
    changed = _market_frame()
    changed.loc[0, "close"] = 106
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: changed)

    assert result.success is False
    assert result.error_code == "SNAPSHOT_CONFLICT"
    assert result.error_class == ExpandedSnapshotConflictError.__name__


def test_no_previous_day_substitution(tmp_path: Path):
    paths = _paths(tmp_path)
    previous = paths.market_dir("2026-09-16") / "005930.csv"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous-day\n", encoding="utf-8")
    result = collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=paths, source=lambda *_: pd.DataFrame())

    assert result.success is False
    assert result.error_code == ERROR_EMPTY_SOURCE
    assert not (paths.market_dir(BAS_DD) / "005930.csv").exists()


def test_production_raw_never_written(tmp_path: Path):
    collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _market_frame())

    assert not (tmp_path / "data" / "raw").exists()


def test_production_investor_never_written(tmp_path: Path):
    collect_investor_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _investor_frame())

    assert not (tmp_path / "data" / "investor").exists()


def test_dual_output_never_written(tmp_path: Path):
    collect_market_snapshot(ticker="005930", basDd=BAS_DD, paths=_paths(tmp_path), source=lambda *_: _market_frame())

    assert not (tmp_path / "output" / "dual_shadow_signal_ledger.csv").exists()
    assert not (tmp_path / "output" / "dual_shadow_run_registry.jsonl").exists()


def test_d1_universe_unchanged():
    root = Path(__file__).resolve().parents[1]
    controlled = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    snapshot = root / "data" / "expanded_shadow" / "universe" / "snapshots" / "2026-09-17_expanded_universe_574.csv"

    assert compute_universe_sha256(controlled) == EXPECTED_UNIVERSE_SHA
    assert compute_universe_sha256(snapshot) == EXPECTED_UNIVERSE_SHA


def test_invalid_ticker_fails_before_fetch(tmp_path: Path):
    source = TypeCheckingSource(_market_frame())
    result = collect_market_snapshot(ticker="0015n0", basDd=BAS_DD, paths=_paths(tmp_path), source=source)

    assert isinstance(result, CollectionResult)
    assert result.success is False
    assert result.error_code == ERROR_INVALID_TICKER
    assert source.seen_type is None
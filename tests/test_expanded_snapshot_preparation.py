from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path

import pandas as pd
import pytest

import src.expanded_snapshot_preparation as preparation
from src.expanded_shadow_data import ERROR_SNAPSHOT_CONFLICT, RetryPolicy
from src.expanded_shadow_ops import ExpandedShadowPaths


SOURCE_DATE = "2026-09-18"
TICKERS = ("000001", "000002", "000003")
NO_HOLIDAYS: frozenset = frozenset()


def _market(ticker: str, source_date: str = SOURCE_DATE) -> pd.DataFrame:
    dates = pd.bdate_range(end=source_date, periods=60)
    closes = [100.0 + index for index in range(60)]
    return pd.DataFrame({"date": dates, "open": closes, "high": [v + 1 for v in closes], "low": [v - 1 for v in closes], "close": closes, "volume": [1000] * 60, "ticker": [ticker] * 60})


def _investor(ticker: str, source_date: str = SOURCE_DATE) -> pd.DataFrame:
    dates = pd.bdate_range(end=source_date, periods=5)
    return pd.DataFrame({"date": dates, "ticker": [ticker] * 5, "foreign_net_buy": [1] * 5, "institution_net_buy": [2] * 5})


class FakeSource:
    def __init__(self, builder, plans=None):
        self.builder = builder
        self.plans = plans or {}
        self.calls = []

    def fetch(self, ticker: str, _start: str, _end: str):
        self.calls.append(ticker)
        plan = self.plans.get(ticker, SOURCE_DATE)
        if plan == "TIMEOUT":
            raise TimeoutError(f"{ticker} timeout")
        return self.builder(ticker, plan)


def _prepare(root: Path, market: FakeSource, investor: FakeSource):
    return preparation.prepare_expanded_snapshots(repo_root=root, source_date=SOURCE_DATE, market_source=market, investor_source=investor, retry_policy=RetryPolicy(max_attempts=2), tickers=TICKERS)


def test_explicit_source_date_has_priority(tmp_path: Path):
    result = preparation.resolve_expanded_source_date(repo_root=tmp_path, explicit_source_date="2026-09-17", now=datetime(2026, 9, 18, 23, tzinfo=timezone.utc), holidays=NO_HOLIDAYS, completed_dates={"2026-09-17"})
    assert result.source_date == "2026-09-17"
    assert result.reason == "EXPLICIT"


def test_weekend_and_holiday_resolve_to_previous_trading_day(tmp_path: Path):
    weekend = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 19, 10), holidays=NO_HOLIDAYS, completed_dates=())
    holiday = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 18, 23), holidays=frozenset({pd.Timestamp("2026-09-18").date()}), completed_dates=())
    assert weekend.source_date == "2026-09-18"
    assert holiday.source_date == "2026-09-17"


def test_before_cutoff_uses_previous_completed_trading_day(tmp_path: Path):
    result = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 18, 9), holidays=NO_HOLIDAYS, completed_dates=())
    assert result.source_date == "2026-09-17"


def test_after_cutoff_uses_latest_completed_trading_day(tmp_path: Path):
    result = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 18, 23), holidays=NO_HOLIDAYS, completed_dates=())
    assert result.source_date == "2026-09-18"
    assert result.reason == "LATEST_COMPLETED"


def test_one_day_gap_and_multi_day_gap_choose_oldest_only(tmp_path: Path):
    one_gap = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 18, 23), holidays=NO_HOLIDAYS, completed_dates={"2026-09-17"})
    multi_gap = preparation.resolve_expanded_source_date(repo_root=tmp_path, now=datetime(2026, 9, 22, 23), holidays=NO_HOLIDAYS, completed_dates={"2026-09-17"})
    assert one_gap.source_date == "2026-09-18"
    assert one_gap.reason == "OLDEST_GAP"
    assert multi_gap.source_date == "2026-09-18"
    assert multi_gap.reason == "OLDEST_GAP"


def test_future_or_non_trading_explicit_date_is_rejected(tmp_path: Path):
    with pytest.raises(preparation.ExpandedSourceDateError):
        preparation.resolve_expanded_source_date(repo_root=tmp_path, explicit_source_date="2026-09-19", now=datetime(2026, 9, 18, 23), holidays=NO_HOLIDAYS)


def test_existing_valid_snapshots_are_reused_without_provider_calls(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    market = FakeSource(_market)
    investor = FakeSource(_investor)
    first = _prepare(tmp_path, market, investor)
    market.calls.clear(); investor.calls.clear()

    second = _prepare(tmp_path, market, investor)

    assert first.preparation_status == "READY"
    assert second.preparation_status == "READY"
    assert second.reused_count == 6
    assert second.collected_count == 0
    assert market.calls == []
    assert investor.calls == []
    assert len(list(paths.market_dir(SOURCE_DATE).glob("*.csv"))) == 3


def test_missing_market_ticker_only_is_collected_on_safety_run(tmp_path: Path):
    market = FakeSource(_market, {"000003": "TIMEOUT"})
    investor = FakeSource(_investor)
    first = _prepare(tmp_path, market, investor)
    market.plans["000003"] = SOURCE_DATE
    market.calls.clear(); investor.calls.clear()

    second = _prepare(tmp_path, market, investor)

    assert first.market_ready == 2
    assert first.investor_ready == 3
    assert first.preparation_status == "SNAPSHOT_PREPARATION_FAILED"
    assert second.preparation_status == "READY"
    assert market.calls == ["000003"]
    assert investor.calls == []
    assert second.reused_count == 5
    assert second.collected_count == 1


def test_investor_source_lag_is_data_not_ready_and_writes_no_lagging_file(tmp_path: Path):
    market = FakeSource(_market)
    investor = FakeSource(_investor, {"000003": "2026-09-17"})

    result = _prepare(tmp_path, market, investor)

    assert result.preparation_status == "DATA_NOT_READY"
    assert result.ready_count == 2
    assert result.investor_failures[0]["ticker"] == "000003"
    assert result.investor_failures[0]["error_code"] == "SOURCE_DATE_NOT_READY"
    assert result.investor_failures[0]["attempt_count"] == 2
    assert not (ExpandedShadowPaths(tmp_path).investor_dir(SOURCE_DATE) / "000003_investor.csv").exists()


def test_investor_partial_retry_exhaustion_blocks_exact_gate(tmp_path: Path):
    investor = FakeSource(_investor, {"000003": "TIMEOUT"})

    result = _prepare(tmp_path, FakeSource(_market), investor)

    assert result.preparation_status == "SNAPSHOT_PREPARATION_FAILED"
    assert result.market_ready == 3
    assert result.investor_ready == 2
    assert result.ready_count == 2
    assert result.investor_failures[0]["ticker"] == "000003"
    assert result.investor_failures[0]["attempt_count"] == 2


def test_existing_exact_date_mismatch_fails_closed_without_provider_call(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    bad_path = paths.market_dir(SOURCE_DATE) / "000001.csv"
    bad_path.parent.mkdir(parents=True)
    _market("000001", "2026-09-17").drop(columns=["ticker"]).to_csv(bad_path, index=False)
    market = FakeSource(_market)

    result = _prepare(tmp_path, market, FakeSource(_investor))

    assert result.preparation_status == "SNAPSHOT_PREPARATION_FAILED"
    assert result.market_failures[0]["error_code"] == "EXISTING_SNAPSHOT_INVALID"
    assert "000001" not in market.calls


def test_snapshot_conflict_and_retry_exhaustion_are_reported(tmp_path: Path, monkeypatch):
    original = preparation.collect_market_snapshot

    def conflict(**kwargs):
        if kwargs["ticker"] == "000001":
            return preparation.CollectionResult("000001", "MARKET", False, 1, None, 0, None, ERROR_SNAPSHOT_CONFLICT, "ExpandedSnapshotConflictError", "conflict", 0.0)
        return original(**kwargs)

    monkeypatch.setattr(preparation, "collect_market_snapshot", conflict)
    market = FakeSource(_market, {"000002": "TIMEOUT"})
    result = _prepare(tmp_path, market, FakeSource(_investor))

    failures = {row["ticker"]: row for row in result.market_failures}
    assert result.preparation_status == "SNAPSHOT_PREPARATION_FAILED"
    assert failures["000001"]["error_code"] == ERROR_SNAPSHOT_CONFLICT
    assert failures["000002"]["attempt_count"] == 2


def test_fixture_equivalent_exact_gate_requires_all_datasets(tmp_path: Path):
    result = _prepare(tmp_path, FakeSource(_market), FakeSource(_investor))
    assert result.universe_count == 3
    assert result.market_ready == 3
    assert result.investor_ready == 3
    assert result.ready_count == 3
    assert result.preparation_status == "READY"

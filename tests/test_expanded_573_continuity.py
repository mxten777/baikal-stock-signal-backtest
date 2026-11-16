from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from scripts.expanded_daily_orchestrator import run_expanded_daily_orchestration
from src.expanded_shadow_data import RetryPolicy
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_snapshot_preparation import (
    ExpandedSourceDateError,
    prepare_expanded_snapshots,
    resolve_expanded_source_date,
)
from src.expanded_trading_halts import TradingStatusAssessment
import src.expanded_snapshot_preparation as preparation


class Market:
    def __init__(self, rows: int = 70):
        self.rows = rows

    def fetch(self, ticker, start, end):
        dates = pd.bdate_range(end=end, periods=self.rows)
        prices = [100.0 + n for n in range(self.rows)]
        return pd.DataFrame({
            "date": dates, "ticker": ticker, "open": prices,
            "high": [x + 1 for x in prices], "low": [x - 1 for x in prices],
            "close": prices, "volume": [10000] * self.rows,
        })


class Investor:
    def fetch(self, ticker, start, end):
        return pd.DataFrame({
            "date": pd.bdate_range(end=end, periods=5), "ticker": ticker,
            "foreign_net_buy": [10] * 5, "institution_net_buy": [5] * 5,
        })


@pytest.fixture
def root(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    paths.controlled_universe_path.parent.mkdir(parents=True)
    paths.controlled_universe_path.write_text(
        "ticker,name,market,source_basDd\n000001,Test,KOSPI,2026-11-12\n",
        encoding="utf-8",
    )
    paths.operational_resume_path.parent.mkdir(parents=True)
    paths.operational_resume_path.write_text(json.dumps({
        "operation_start_date": "2026-11-12", "unrecovered_dates": [],
    }), encoding="utf-8")
    paths.registry_path.write_text(
        json.dumps({"basDd": "2026-11-13", "status": "PARTIAL"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(preparation, "load_holidays", lambda _: (frozenset(), None))
    return tmp_path


def prepare(root: Path, day: str, rows: int = 70):
    return prepare_expanded_snapshots(
        repo_root=root, source_date=day, market_source=Market(rows),
        investor_source=Investor(), retry_policy=RetryPolicy(max_attempts=1),
        trading_status=TradingStatusAssessment(day),
    )


def test_prior_gap_ready_run_and_manual_backfill_preserve_latest(root: Path):
    paths = ExpandedShadowPaths(root)
    old_event = paths.registry_path.read_bytes()
    now = datetime.fromisoformat("2026-11-16T19:30:00+09:00")
    with pytest.raises(ExpandedSourceDateError, match="PARTIAL"):
        resolve_expanded_source_date(repo_root=root, explicit_source_date="2026-11-16", now=now)
    assert resolve_expanded_source_date(
        repo_root=root, explicit_source_date="2026-11-16", now=now,
        allow_prior_incomplete=True,
    ).source_date == "2026-11-16"
    assert resolve_expanded_source_date(
        repo_root=root, now=now, allow_prior_incomplete=True,
    ).source_date == "2026-11-16"

    performance_dates = []

    def performance(**kwargs):
        performance_dates.append(kwargs["source_date"])
        return {}

    assert prepare(root, "2026-11-16").preparation_status == "READY"
    result = run_expanded_daily_orchestration(
        repo_root=root, source_date="2026-11-16", allow_prior_incomplete=True,
        trading_status=TradingStatusAssessment("2026-11-16"), performance_runner=performance,
    )
    assert result.final_status == "SUCCESS"
    assert paths.registry_path.read_bytes().startswith(old_event)
    latest = paths.current_run_path.read_bytes()

    assert prepare(root, "2026-11-13").preparation_status == "READY"
    backfill = run_expanded_daily_orchestration(
        repo_root=root, source_date="2026-11-13", allow_prior_incomplete=True,
        trading_status=TradingStatusAssessment("2026-11-13"), performance_runner=performance,
    )
    assert backfill.final_status == "SUCCESS"
    assert paths.current_run_path.read_bytes() == latest
    assert paths.latest_manifest_path("2026-11-13").is_file()
    assert performance_dates == ["2026-11-16", "2026-11-16"]
    assert paths.registry_path.read_bytes().startswith(old_event)


def test_insufficient_history_still_prevents_ready_publication(root: Path):
    paths = ExpandedShadowPaths(root)
    assert prepare(root, "2026-11-16", rows=59).preparation_status == "READY"
    result = run_expanded_daily_orchestration(
        repo_root=root, source_date="2026-11-16", allow_prior_incomplete=True,
        trading_status=TradingStatusAssessment("2026-11-16"),
        performance_runner=lambda **_: pytest.fail("performance must not run"),
    )
    assert result.final_status == "PARTIAL"
    assert not paths.current_run_path.exists()
    assert not paths.latest_manifest_path("2026-11-16").exists()


def test_unpublished_gap_is_ignored_only_when_enabled(root: Path):
    paths = ExpandedShadowPaths(root)
    paths.registry_path.write_text(
        json.dumps({"basDd": "2026-11-13", "status": "DATA_NOT_READY"}) + "\n",
        encoding="utf-8",
    )
    kwargs = {
        "repo_root": root, "explicit_source_date": "2026-11-16",
        "now": datetime.fromisoformat("2026-11-16T19:30:00+09:00"),
        "completed_dates": ["2026-11-12"],
    }
    with pytest.raises(ExpandedSourceDateError, match="Uncompleted"):
        resolve_expanded_source_date(**kwargs)
    assert resolve_expanded_source_date(
        **kwargs, allow_prior_incomplete=True,
    ).source_date == "2026-11-16"


def test_stale_input_and_unknown_trading_status_remain_blocked(root: Path):
    class StaleInvestor(Investor):
        def fetch(self, ticker, start, end):
            return super().fetch(ticker, start, "2026-11-13")

    stale = prepare_expanded_snapshots(
        repo_root=root, source_date="2026-11-16", market_source=Market(),
        investor_source=StaleInvestor(), retry_policy=RetryPolicy(max_attempts=1),
        trading_status=TradingStatusAssessment("2026-11-16"),
    )
    assert stale.preparation_status != "READY"
    assert stale.investor_failures[0]["error_code"] == "SOURCE_DATE_NOT_READY"
    unknown = TradingStatusAssessment("2026-11-16", unknown=({
        "ticker": "000001", "source_date": "2026-11-16", "status": "UNKNOWN",
        "error_code": "TRADING_STATUS_UNVERIFIED",
    },))
    result = prepare_expanded_snapshots(
        repo_root=root, source_date="2026-11-16", market_source=Market(),
        investor_source=Investor(), trading_status=unknown,
    )
    assert result.preparation_status != "READY"
    assert result.trading_status.unknown == unknown.unknown
    assert not ExpandedShadowPaths(root).current_run_path.exists()

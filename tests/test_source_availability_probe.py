"""Tests for STEP 17-H Source Availability Probe (scripts/source_availability_probe.py).

All tests use mock/fake sources — no real external API calls are made.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.source_availability_probe import (
    PROBE_TICKERS,
    ProbeResult,
    append_probe_log,
    compute_target_basDd,
    probe_one_ticker,
    run_probe,
)

SEOUL_TZ = ZoneInfo("Asia/Seoul")


class FakeMarketSource:
    def __init__(self, frame: pd.DataFrame | None = None, exc: Exception | None = None):
        self._frame = frame
        self._exc = exc
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if self._exc is not None:
            raise self._exc
        return self._frame


class FakeInvestorSource:
    def __init__(self, frame: pd.DataFrame | None = None, exc: Exception | None = None):
        self._frame = frame
        self._exc = exc
        self.calls: list[tuple[str, str, str]] = []

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if self._exc is not None:
            raise self._exc
        return self._frame


def _frame(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates)})


def _now() -> datetime:
    return datetime(2026, 9, 22, 19, 0, 0, tzinfo=SEOUL_TZ)


def test_fetch_success_matches_target_basDd():
    now = _now()
    target = compute_target_basDd(now)
    market_source = FakeMarketSource(frame=_frame(["2026-09-20", "2026-09-21", target]))
    investor_source = FakeInvestorSource(frame=_frame(["2026-09-20", "2026-09-21", target]))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    assert result.market_latest_basDd == target
    assert result.investor_latest_basDd == target
    assert result.target_basDd_match is True
    assert result.error is None


def test_market_not_ready():
    now = _now()
    market_source = FakeMarketSource(frame=_frame(["2026-09-20", "2026-09-21"]))  # behind target
    investor_source = FakeInvestorSource(frame=_frame([compute_target_basDd(now)]))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    assert result.market_latest_basDd == "2026-09-21"
    assert result.target_basDd_match is False
    assert result.error is None


def test_investor_not_ready():
    now = _now()
    target = compute_target_basDd(now)
    market_source = FakeMarketSource(frame=_frame([target]))
    investor_source = FakeInvestorSource(frame=_frame(["2026-09-19", "2026-09-20"]))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    assert result.investor_latest_basDd == "2026-09-20"
    assert result.target_basDd_match is False
    assert result.error is None


def test_fetch_exception_captured_without_crash():
    now = _now()
    market_source = FakeMarketSource(exc=RuntimeError("boom"))
    investor_source = FakeInvestorSource(frame=_frame([compute_target_basDd(now)]))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    assert result.market_latest_basDd is None
    assert result.target_basDd_match is False
    assert result.error is not None
    assert "MARKET_FETCH_ERROR" in result.error
    assert "boom" in result.error


def test_both_sources_raise_exceptions():
    now = _now()
    market_source = FakeMarketSource(exc=ValueError("market down"))
    investor_source = FakeInvestorSource(exc=ValueError("investor down"))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    assert result.market_latest_basDd is None
    assert result.investor_latest_basDd is None
    assert result.target_basDd_match is False
    assert "MARKET_FETCH_ERROR" in result.error
    assert "INVESTOR_FETCH_ERROR" in result.error


def test_four_tickers_are_unique():
    tickers = [entry["ticker"] for entry in PROBE_TICKERS]
    assert len(tickers) == 4
    assert len(set(tickers)) == 4


def test_probe_time_includes_seoul_timezone():
    now = _now()
    market_source = FakeMarketSource(frame=_frame([compute_target_basDd(now)]))
    investor_source = FakeInvestorSource(frame=_frame([compute_target_basDd(now)]))

    result = probe_one_ticker(
        "005930", market_source=market_source, investor_source=investor_source, now=now
    )

    parsed = datetime.fromisoformat(result.probe_time)
    assert parsed.utcoffset().total_seconds() == 9 * 3600


def test_run_probe_calls_all_four_tickers_with_injected_sources():
    now = _now()
    target = compute_target_basDd(now)
    market_source = FakeMarketSource(frame=_frame([target]))
    investor_source = FakeInvestorSource(frame=_frame([target]))

    results = run_probe(market_source=market_source, investor_source=investor_source, now=now)

    assert len(results) == 4
    assert [r.ticker for r in results] == [entry["ticker"] for entry in PROBE_TICKERS]
    assert len(market_source.calls) == 4
    assert len(investor_source.calls) == 4
    assert all(r.target_basDd_match for r in results)


def test_jsonl_append_only(tmp_path: Path):
    log_path = tmp_path / "source_availability_probe_log.jsonl"
    now = _now()
    result1 = ProbeResult(
        ticker="005930",
        probe_time=now.isoformat(),
        target_basDd="2026-09-22",
        market_latest_basDd="2026-09-22",
        investor_latest_basDd="2026-09-22",
        target_basDd_match=True,
        error=None,
    )
    result2 = ProbeResult(
        ticker="080220",
        probe_time=now.isoformat(),
        target_basDd="2026-09-22",
        market_latest_basDd=None,
        investor_latest_basDd=None,
        target_basDd_match=False,
        error="MARKET_FETCH_ERROR: boom",
    )

    append_probe_log([result1], log_path=log_path)
    append_probe_log([result2], log_path=log_path)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row1 = json.loads(lines[0])
    row2 = json.loads(lines[1])
    assert row1["ticker"] == "005930"
    assert row2["ticker"] == "080220"


def test_jsonl_append_creates_parent_dir(tmp_path: Path):
    log_path = tmp_path / "nested" / "output" / "source_availability_probe_log.jsonl"
    now = _now()
    result = ProbeResult(
        ticker="005930",
        probe_time=now.isoformat(),
        target_basDd="2026-09-22",
        market_latest_basDd="2026-09-22",
        investor_latest_basDd="2026-09-22",
        target_basDd_match=True,
        error=None,
    )

    append_probe_log([result], log_path=log_path)

    assert log_path.exists()
    assert len(log_path.read_text(encoding="utf-8").strip().splitlines()) == 1


def test_existing_production_and_expanded_artifacts_untouched():
    """Probe module import/definition must not touch any production/expanded artifact paths."""
    protected_paths = [
        Path("data/raw"),
        Path("data/investor"),
        Path("data/expanded_shadow"),
        Path("output/shadow_signal_records.csv"),
        Path("output/dual_shadow_signal_ledger.csv"),
        Path("output/daily_run_registry.jsonl"),
    ]
    snapshots = {}
    for path in protected_paths:
        if path.exists():
            if path.is_dir():
                snapshots[path] = sorted(p.name for p in path.rglob("*"))
            else:
                snapshots[path] = path.stat().st_mtime

    # Importing the module and referencing its constants must have zero side effects.
    _ = PROBE_TICKERS

    for path, snapshot in snapshots.items():
        if path.is_dir():
            assert sorted(p.name for p in path.rglob("*")) == snapshot
        else:
            assert path.stat().st_mtime == snapshot

"""
Safe Investor Updater 테스트.

실제 Naver 네트워크에 의존하지 않는다. 모든 source는 테스트 전용 fake이며
production operational data 생성/갱신에 사용하지 않는다.
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from scripts.safe_investor_update import (
    REQUIRED_COLUMNS,
    STATUS_FAILED,
    STATUS_NO_NEW_DATA,
    STATUS_SOURCE_LAG,
    STATUS_UPDATED,
    SafeInvestorUpdater,
    compute_target_market_date,
)

TODAY = date(2026, 9, 4)
MARKET_TARGET = date(2026, 9, 3)  # data/raw 기준 operational latest date

TEST_TICKERS = {f"{i:06d}": f"테스트{i}" for i in range(20)}


def _make_market_df(start: date, end: date) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    return pd.DataFrame({
        "date": days,
        "open": range(1000, 1000 + len(days)),
        "high": range(1010, 1010 + len(days)),
        "low": range(990, 990 + len(days)),
        "close": range(1005, 1005 + len(days)),
        "volume": range(100, 100 + len(days)),
    })


def _make_investor_df(start: date, end: date, ticker: str, base: int = 100) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    return pd.DataFrame({
        "date": days,
        "ticker": int(ticker),
        "foreign_net_buy": range(base, base + len(days)),
        "institution_net_buy": range(base + 5, base + 5 + len(days)),
    })


class FakeSource:
    """테스트 전용 investor source. ticker별 DataFrame 또는 exception을 반환한다."""

    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls = []

    def fetch(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        if ticker in self.errors:
            raise self.errors[ticker]
        if ticker not in self.responses:
            raise AssertionError(f"unexpected fetch for {ticker}")
        return self.responses[ticker].copy()


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    """market target date = 2026-09-03 를 만드는 raw market data."""
    d = tmp_path / "raw"
    d.mkdir()
    for ticker in TEST_TICKERS:
        _make_market_df(date(2026, 8, 20), MARKET_TARGET).to_csv(d / f"{ticker}.csv", index=False)
    return d


@pytest.fixture()
def investor_dir(tmp_path: Path) -> Path:
    """기존 investor 데이터 latest = 2026-09-01 (신규 row 여유 있음)."""
    d = tmp_path / "investor"
    d.mkdir()
    for ticker in TEST_TICKERS:
        _make_investor_df(date(2026, 8, 20), date(2026, 9, 1), ticker).to_csv(
            d / f"{ticker}_investor.csv", index=False
        )
    return d


def _read_all(investor_dir: Path):
    return {
        p.stem.replace("_investor", ""): pd.read_csv(p)
        for p in sorted(investor_dir.glob("*_investor.csv"))
    }


def _run(investor_dir, raw_dir, source, tmp_path, today=TODAY):
    updater = SafeInvestorUpdater(
        tickers=TEST_TICKERS,
        investor_dir=investor_dir,
        raw_dir=raw_dir,
        source=source,
        staging_dir=tmp_path / "staging",
        today=today,
    )
    return updater.run()


def _uniform_source(end: date, start: date = date(2026, 8, 24)) -> FakeSource:
    responses = {t: _make_investor_df(start, end, t) for t in TEST_TICKERS}
    return FakeSource(responses=responses)


# ----------------------------------------------------------------------
# 1. append-only investor update
# ----------------------------------------------------------------------
def test_append_only_investor_update(investor_dir, raw_dir, tmp_path):
    result = _run(investor_dir, raw_dir, _uniform_source(MARKET_TARGET), tmp_path)
    assert result.status == STATUS_UPDATED
    assert result.publish_status == "PUBLISHED"
    assert result.rows_added == len(TEST_TICKERS) * 2  # 09-02, 09-03
    assert result.published_latest_date == MARKET_TARGET.strftime("%Y-%m-%d")
    for df in _read_all(investor_dir).values():
        assert df["date"].max() == MARKET_TARGET.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# 2. historical immutability
# ----------------------------------------------------------------------
def test_historical_immutability(investor_dir, raw_dir, tmp_path):
    before = _read_all(investor_dir)
    result = _run(investor_dir, raw_dir, _uniform_source(MARKET_TARGET), tmp_path)
    assert result.status == STATUS_UPDATED
    after = _read_all(investor_dir)
    for ticker, prev in before.items():
        cur = after[ticker]
        assert len(cur) == len(prev) + 2
        pd.testing.assert_frame_equal(
            prev, cur.iloc[: len(prev)].reset_index(drop=True)
        )


# ----------------------------------------------------------------------
# 3. overlap mismatch → existing wins
# ----------------------------------------------------------------------
def test_overlap_mismatch_existing_wins(investor_dir, raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_investor_df(date(2026, 8, 24), MARKET_TARGET, t)
        df.loc[df["date"] == pd.Timestamp("2026-08-28"), "foreign_net_buy"] = 999999
        responses[t] = df
    before = _read_all(investor_dir)
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_UPDATED
    assert result.overlap_mismatches
    after = _read_all(investor_dir)
    for ticker, prev in before.items():
        cur = after[ticker]
        pd.testing.assert_frame_equal(
            prev, cur.iloc[: len(prev)].reset_index(drop=True)
        )


# ----------------------------------------------------------------------
# 4. duplicate rejection
# ----------------------------------------------------------------------
def test_duplicate_rejection(investor_dir, raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_investor_df(date(2026, 8, 24), MARKET_TARGET, t)
        responses[t] = pd.concat([df, df.tail(1)], ignore_index=True)
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 5. invalid schema rejection
# ----------------------------------------------------------------------
def test_invalid_schema_rejection(investor_dir, raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_investor_df(date(2026, 8, 24), MARKET_TARGET, t)
        responses[t] = df.drop(columns=["institution_net_buy"])
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 6. one ticker fetch failure → publish 0
# ----------------------------------------------------------------------
def test_fetch_failure_blocks_publish(investor_dir, raw_dir, tmp_path):
    responses = {t: _make_investor_df(date(2026, 8, 24), MARKET_TARGET, t) for t in TEST_TICKERS}
    failing = sorted(TEST_TICKERS)[0]
    del responses[failing]
    errors = {failing: RuntimeError("network down")}
    before = _read_all(investor_dir)
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses, errors=errors), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.fetch_failed_count == 1
    assert result.publish_status == "NOT_PUBLISHED"
    after = _read_all(investor_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 7. partial ticker latest-date mismatch → publish 0
# ----------------------------------------------------------------------
def test_partial_source_lag_blocks_publish(investor_dir, raw_dir, tmp_path):
    responses = {}
    lagging = sorted(TEST_TICKERS)[0]
    for t in TEST_TICKERS:
        end = date(2026, 9, 2) if t == lagging else MARKET_TARGET
        responses[t] = _make_investor_df(date(2026, 8, 24), end, t)
    before = _read_all(investor_dir)
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.source_lag_type == "PARTIAL"
    assert result.publish_status == "NOT_PUBLISHED"
    after = _read_all(investor_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 8. uniform source lag → safe result
# ----------------------------------------------------------------------
def test_uniform_source_lag_safe_publish(investor_dir, raw_dir, tmp_path):
    lag_date = date(2026, 9, 2)  # market target(09-03)보다 하루 뒤처짐
    result = _run(investor_dir, raw_dir, _uniform_source(lag_date), tmp_path)
    assert result.status == STATUS_SOURCE_LAG
    assert result.source_lag_type == "UNIFORM"
    assert result.publish_status == "PUBLISHED"
    assert result.published_latest_date == lag_date.strftime("%Y-%m-%d")
    assert result.gap_days == 1


# ----------------------------------------------------------------------
# 9. market target alignment
# ----------------------------------------------------------------------
def test_market_target_alignment(raw_dir):
    target = compute_target_market_date(raw_dir, TEST_TICKERS)
    assert target == MARKET_TARGET.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# 10. no-new-data
# ----------------------------------------------------------------------
def test_no_new_data(investor_dir, raw_dir, tmp_path):
    # source가 기존 latest(09-01)까지만 반환 → 신규 row 없음
    result = _run(investor_dir, raw_dir, _uniform_source(date(2026, 9, 1)), tmp_path)
    assert result.status == STATUS_NO_NEW_DATA
    assert result.publish_status == "SKIPPED_NO_NEW_DATA"
    assert result.rows_added == 0


# ----------------------------------------------------------------------
# 11. rerun idempotency
# ----------------------------------------------------------------------
def test_rerun_idempotency(investor_dir, raw_dir, tmp_path):
    source = _uniform_source(MARKET_TARGET)
    first = _run(investor_dir, raw_dir, source, tmp_path)
    assert first.status == STATUS_UPDATED

    after_first = _read_all(investor_dir)
    second = _run(investor_dir, raw_dir, _uniform_source(MARKET_TARGET), tmp_path)
    assert second.status == STATUS_NO_NEW_DATA
    assert second.rows_added == 0
    after_second = _read_all(investor_dir)
    for ticker in after_first:
        pd.testing.assert_frame_equal(after_first[ticker], after_second[ticker])
        assert not after_second[ticker]["date"].duplicated().any()


# ----------------------------------------------------------------------
# 12. publish failure rollback
# ----------------------------------------------------------------------
def test_publish_failure_rollback(investor_dir, raw_dir, tmp_path, monkeypatch):
    before = _read_all(investor_dir)
    real_replace = __import__("os").replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise OSError("simulated disk failure")
        return real_replace(src, dst)

    monkeypatch.setattr("scripts.safe_investor_update.os.replace", flaky_replace)
    with pytest.raises(OSError):
        _run(investor_dir, raw_dir, _uniform_source(MARKET_TARGET), tmp_path)
    after = _read_all(investor_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 13. future-date rejection
# ----------------------------------------------------------------------
def test_future_date_rejection(investor_dir, raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        responses[t] = _make_investor_df(date(2026, 8, 24), date(2026, 9, 5), t)  # target 이후
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 14. all ticker coverage
# ----------------------------------------------------------------------
def test_all_ticker_coverage(investor_dir, raw_dir, tmp_path):
    result = _run(investor_dir, raw_dir, _uniform_source(MARKET_TARGET), tmp_path)
    assert result.ticker_count == len(TEST_TICKERS)
    assert result.fetch_success_count == len(TEST_TICKERS)
    assert len(result.tickers) == len(TEST_TICKERS)


# ----------------------------------------------------------------------
# 15. empty-source handling
# ----------------------------------------------------------------------
def test_empty_source_handling(investor_dir, raw_dir, tmp_path):
    # 기존 데이터가 이미 market target까지 도달한 상태에서 source가 빈 응답
    for ticker in TEST_TICKERS:
        _make_investor_df(date(2026, 8, 20), MARKET_TARGET, ticker).to_csv(
            investor_dir / f"{ticker}_investor.csv", index=False
        )
    empty_cols = ["date", "foreign_net_buy", "institution_net_buy"]
    responses = {t: pd.DataFrame(columns=empty_cols) for t in TEST_TICKERS}
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_NO_NEW_DATA
    assert result.publish_status == "SKIPPED_NO_NEW_DATA"


def test_empty_source_unexpected_when_behind_target(investor_dir, raw_dir, tmp_path):
    # 기존 데이터가 market target에 못 미치는데 source가 완전히 빈 응답 → 실패
    empty_cols = ["date", "foreign_net_buy", "institution_net_buy"]
    responses = {t: pd.DataFrame(columns=empty_cols) for t in TEST_TICKERS}
    result = _run(investor_dir, raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 16. staging isolation
# ----------------------------------------------------------------------
def test_staging_isolation(investor_dir, raw_dir, tmp_path):
    staging_dir = tmp_path / "staging"
    responses = {}
    for t in TEST_TICKERS:
        df = _make_investor_df(date(2026, 8, 24), MARKET_TARGET, t)
        responses[t] = pd.concat([df, df.tail(1)], ignore_index=True)  # duplicate → FAIL
    before = _read_all(investor_dir)
    updater = SafeInvestorUpdater(
        tickers=TEST_TICKERS,
        investor_dir=investor_dir,
        raw_dir=raw_dir,
        source=FakeSource(responses=responses),
        staging_dir=staging_dir,
        today=TODAY,
    )
    result = updater.run()
    assert result.status == STATUS_FAILED
    # staging에는 후보 파일이 남아있지만 production은 손대지 않는다.
    assert any(staging_dir.glob("*.staged.csv"))
    after = _read_all(investor_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])

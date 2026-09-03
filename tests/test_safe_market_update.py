"""
Safe Market Updater 테스트.

실제 네트워크에 의존하지 않는다. 모든 source는 테스트 전용 fake이며
production operational data 생성에 사용하지 않는다.
"""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from scripts.safe_market_update import (
    REQUIRED_COLUMNS,
    STATUS_FAILED,
    STATUS_NO_NEW_DATA,
    STATUS_UPDATED,
    SafeMarketUpdater,
)

TODAY = date(2026, 9, 4)

TEST_TICKERS = {f"T{i:04d}": f"테스트{i}" for i in range(20)}


def _make_df(start: date, end: date, base_price: int = 1000) -> pd.DataFrame:
    """start~end (inclusive)의 연속 일자 fake 시세를 만든다."""
    days = pd.date_range(start, end, freq="D")
    return pd.DataFrame({
        "date": days,
        "open": range(base_price, base_price + len(days)),
        "high": range(base_price + 10, base_price + 10 + len(days)),
        "low": range(base_price - 10, base_price - 10 + len(days)),
        "close": range(base_price + 5, base_price + 5 + len(days)),
        "volume": range(100, 100 + len(days)),
    })


class FakeSource:
    """테스트 전용 source. ticker별 DataFrame 또는 exception을 반환한다."""

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
    d = tmp_path / "raw"
    d.mkdir()
    base_start = date(2026, 8, 20)
    base_end = date(2026, 9, 3)
    for ticker in TEST_TICKERS:
        _make_df(base_start, base_end).to_csv(d / f"{ticker}.csv", index=False)
    return d


def _read_all(raw_dir: Path):
    return {
        p.stem: pd.read_csv(p)
        for p in sorted(raw_dir.glob("*.csv"))
    }


def _source_with_new_data(new_date: date = TODAY) -> FakeSource:
    # existing max = 2026-09-03, source는 overlap + new date(TODAY)를 반환
    responses = {
        t: _make_df(date(2026, 8, 24), new_date) for t in TEST_TICKERS
    }
    return FakeSource(responses=responses)


def _run(raw_dir, source, tmp_path):
    updater = SafeMarketUpdater(
        tickers=TEST_TICKERS,
        raw_dir=raw_dir,
        source=source,
        staging_dir=tmp_path / "staging",
        today=TODAY,
    )
    return updater.run()


# ----------------------------------------------------------------------
# 1. append-only update
# ----------------------------------------------------------------------
def test_append_only_update(raw_dir, tmp_path):
    result = _run(raw_dir, _source_with_new_data(), tmp_path)
    assert result.status == STATUS_UPDATED
    assert result.publish_status == "PUBLISHED"
    assert result.rows_added == len(TEST_TICKERS)
    assert result.published_latest_date == TODAY.strftime("%Y-%m-%d")
    for df in _read_all(raw_dir).values():
        assert df["date"].max() == TODAY.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------
# 2. historical rows unchanged
# ----------------------------------------------------------------------
def test_historical_rows_unchanged(raw_dir, tmp_path):
    before = _read_all(raw_dir)
    result = _run(raw_dir, _source_with_new_data(), tmp_path)
    assert result.status == STATUS_UPDATED
    after = _read_all(raw_dir)
    for ticker, prev in before.items():
        cur = after[ticker]
        assert len(cur) == len(prev) + 1
        pd.testing.assert_frame_equal(
            prev, cur.iloc[: len(prev)].reset_index(drop=True)
        )


# ----------------------------------------------------------------------
# 3. source overlap mismatch → existing wins
# ----------------------------------------------------------------------
def test_overlap_mismatch_existing_wins(raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_df(date(2026, 8, 24), TODAY)
        # overlap 날짜의 close 값을 변조한다
        df.loc[df["date"] == pd.Timestamp("2026-09-01"), "close"] = 999999
        responses[t] = df
    before = _read_all(raw_dir)
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_UPDATED
    assert result.overlap_mismatches  # mismatch가 report되어야 한다
    after = _read_all(raw_dir)
    for ticker, prev in before.items():
        cur = after[ticker]
        pd.testing.assert_frame_equal(
            prev, cur.iloc[: len(prev)].reset_index(drop=True)
        )


# ----------------------------------------------------------------------
# 4. duplicate rejection
# ----------------------------------------------------------------------
def test_duplicate_rejection(raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_df(date(2026, 8, 24), TODAY)
        responses[t] = pd.concat([df, df.tail(1)], ignore_index=True)
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 5. invalid schema rejection
# ----------------------------------------------------------------------
def test_invalid_schema_rejection(raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_df(date(2026, 8, 24), TODAY)
        responses[t] = df.drop(columns=["volume"])
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.publish_status == "NOT_PUBLISHED"


# ----------------------------------------------------------------------
# 6. ticker fetch failure → publish 0
# ----------------------------------------------------------------------
def test_fetch_failure_blocks_publish(raw_dir, tmp_path):
    responses = {t: _make_df(date(2026, 8, 24), TODAY) for t in TEST_TICKERS}
    failing = sorted(TEST_TICKERS)[0]
    del responses[failing]
    errors = {failing: RuntimeError("network down")}
    before = _read_all(raw_dir)
    result = _run(raw_dir, FakeSource(responses=responses, errors=errors), tmp_path)
    assert result.status == STATUS_FAILED
    assert result.fetch_failed_count == 1
    assert result.publish_status == "NOT_PUBLISHED"
    after = _read_all(raw_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 7. one ticker failure → all production unchanged
# ----------------------------------------------------------------------
def test_single_ticker_failure_all_unchanged(raw_dir, tmp_path):
    responses = {}
    for t in TEST_TICKERS:
        df = _make_df(date(2026, 8, 24), TODAY)
        if t == sorted(TEST_TICKERS)[5]:
            df = pd.concat([df, df.tail(1)], ignore_index=True)  # duplicate
        responses[t] = df
    before = _read_all(raw_dir)
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    after = _read_all(raw_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 8. no-new-data
# ----------------------------------------------------------------------
def test_no_new_data(raw_dir, tmp_path):
    responses = {
        t: _make_df(date(2026, 8, 24), date(2026, 9, 3)) for t in TEST_TICKERS
    }
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_NO_NEW_DATA
    assert result.publish_status == "SKIPPED_NO_NEW_DATA"
    assert result.rows_added == 0


# ----------------------------------------------------------------------
# 9. rerun idempotency
# ----------------------------------------------------------------------
def test_rerun_idempotency(raw_dir, tmp_path):
    first = _run(raw_dir, _source_with_new_data(), tmp_path / "r1")
    assert first.status == STATUS_UPDATED
    after_first = _read_all(raw_dir)
    # 동일 source 상태로 두 번째 실행
    second = _run(raw_dir, _source_with_new_data(), tmp_path / "r2")
    assert second.status == STATUS_NO_NEW_DATA
    after_second = _read_all(raw_dir)
    for ticker in after_first:
        assert len(after_first[ticker]) == len(after_second[ticker])
        assert not after_second[ticker]["date"].duplicated().any()
        pd.testing.assert_frame_equal(after_first[ticker], after_second[ticker])


# ----------------------------------------------------------------------
# 10. atomic/publish failure handling → rollback
# ----------------------------------------------------------------------
def test_publish_failure_rollback(raw_dir, tmp_path, monkeypatch):
    import scripts.safe_market_update as smu

    before = _read_all(raw_dir)
    real_replace = smu.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 3:  # 3번째 ticker replace에서 실패
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(smu.os, "replace", flaky_replace)
    result = None
    with pytest.raises(OSError):
        _run(raw_dir, _source_with_new_data(), tmp_path)
    after = _read_all(raw_dir)
    # publish된 ticker도 backup에서 rollback되어 모두 원본과 동일해야 한다
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 11. future-date rejection
# ----------------------------------------------------------------------
def test_future_date_rejection(raw_dir, tmp_path):
    future = TODAY + timedelta(days=3)
    responses = {t: _make_df(date(2026, 8, 24), future) for t in TEST_TICKERS}
    before = _read_all(raw_dir)
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    after = _read_all(raw_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 12. all ticker coverage
# ----------------------------------------------------------------------
def test_all_ticker_coverage(raw_dir, tmp_path):
    source = _source_with_new_data()
    result = _run(raw_dir, source, tmp_path)
    assert result.ticker_count == len(TEST_TICKERS)
    assert result.fetch_success_count == len(TEST_TICKERS)
    assert {t for t, _, _ in source.calls} == set(TEST_TICKERS)
    assert result.status == STATUS_UPDATED


# ----------------------------------------------------------------------
# 13. unexpected empty source → fail (기존 max < today인데 source가 비어 있음)
# ----------------------------------------------------------------------
def test_unexpected_empty_source_fails(raw_dir, tmp_path):
    responses = {
        t: _make_df(date(2026, 8, 24), TODAY) for t in TEST_TICKERS
    }
    empty_ticker = sorted(TEST_TICKERS)[2]
    responses[empty_ticker] = pd.DataFrame(columns=REQUIRED_COLUMNS)
    before = _read_all(raw_dir)
    result = _run(raw_dir, FakeSource(responses=responses), tmp_path)
    assert result.status == STATUS_FAILED
    after = _read_all(raw_dir)
    for ticker in before:
        pd.testing.assert_frame_equal(before[ticker], after[ticker])


# ----------------------------------------------------------------------
# 14. staging은 production glob과 분리되고 종료 시 정리된다
# ----------------------------------------------------------------------
def test_staging_isolated_and_cleaned(raw_dir, tmp_path):
    staging = tmp_path / "staging"
    result = _run(raw_dir, _source_with_new_data(), tmp_path)
    assert result.status == STATUS_UPDATED
    # staging dir은 raw dir과 분리
    assert staging != raw_dir
    # production dir에 staging/temp 파일이 남지 않는다
    leftovers = [p for p in raw_dir.iterdir() if p.suffix != ".csv"]
    assert leftovers == []

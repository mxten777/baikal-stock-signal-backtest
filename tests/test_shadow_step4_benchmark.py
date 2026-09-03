"""
Shadow STEP 4 — Benchmark / Excess Return 계산 단위 테스트.

검증 대상:
  1. KOSPI(KS11) / KOSDAQ(KQ11) 매핑 및 5D 계산
  2. 10D / 20D 계산
  3. Excess 계산 정확성 및 stock/benchmark 동기화(한쪽이라도 None이면 None)
  4. Benchmark 거래일 gap 처리
  5. signal_date가 Benchmark에 없음 / 알 수 없는 market → record 불변
  6. CANDIDATE / EXCLUDED 동일 계산
  7. Immutable 필드 불변, 기존값 mismatch 미덮어쓰기, dry-run 파일 무변경
  8. STEP 3 status 불변

네트워크/외부 데이터 호출 없이 fixture DataFrame만 사용한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.shadow_update_benchmark import run_update_benchmark
from src.shadow_tracking import (
    FOREIGN_STATUS_NEGATIVE,
    FOREIGN_STATUS_POSITIVE,
    IMMUTABLE_FIELDS,
    STATUS_5D_DONE,
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    ShadowStore,
    compute_benchmark_returns,
    compute_excess,
    normalize_market,
)

TICKER = "005930"
KOSDAQ_TICKER = "080220"
SIGNAL_DATE = "2024-01-01"


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


def _benchmark_df(n_after_signal: int, freq: str = "D", start: str = SIGNAL_DATE) -> pd.DataFrame:
    """signal_date를 첫 행으로 두고 이후 n거래일을 갖는 Benchmark 데이터.

    close = 1000 * (1 + 0.01 * index) → +N거래일 benchmark_return = N %.
    """
    dates = pd.date_range(start, periods=n_after_signal + 1, freq=freq)
    return pd.DataFrame({
        "date": dates,
        "close": [1000.0 * (1 + 0.01 * i) for i in range(len(dates))],
    })


def _add_record(
    store: ShadowStore,
    ticker: str = TICKER,
    market: str = "KS11",
    foreign_status: str = FOREIGN_STATUS_POSITIVE,
) -> None:
    store.record_signal(
        stock_code=ticker,
        stock_name="테스트종목",
        market=market,
        signal_date=SIGNAL_DATE,
        signal_price=100.0,
        signal_score=80.0,
        foreign_status=foreign_status,
    )


def _set_returns(store: ShadowStore, ticker: str = TICKER, **fields) -> None:
    store.update_performance({(ticker, SIGNAL_DATE): dict(fields)})


def _row(store: ShadowStore) -> pd.Series:
    return store.load().iloc[0]


# ──────────────────────────────────────────────
# 1~2. 시장 매핑 및 horizon별 계산
# ──────────────────────────────────────────────
def test_kospi_maps_to_ks11_and_computes_5d(store):
    _add_record(store, market="KS11")
    _set_returns(store, return_5d=5.0)

    stats = run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(5)})

    row = _row(store)
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(0.0)
    assert stats["kospi"] == 1
    assert stats["kosdaq"] == 0
    assert stats["new_benchmark_5d"] == 1


def test_kosdaq_maps_to_kq11_and_computes_5d(store):
    _add_record(store, ticker=KOSDAQ_TICKER, market="KOSDAQ")
    _set_returns(store, ticker=KOSDAQ_TICKER, return_5d=8.0)

    stats = run_update_benchmark(
        store=store,
        benchmark_map={"KS11": _benchmark_df(20), "KQ11": _benchmark_df(5)},
    )

    row = _row(store)
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(3.0)
    assert stats["kosdaq"] == 1
    assert stats["kospi"] == 0


def test_10d_and_20d_benchmark_and_excess(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0, return_10d=12.0, return_20d=30.0)

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(20)})

    row = _row(store)
    assert row["benchmark_return_10d"] == pytest.approx(10.0)
    assert row["benchmark_return_20d"] == pytest.approx(20.0)
    assert row["excess_10d"] == pytest.approx(2.0)
    assert row["excess_20d"] == pytest.approx(10.0)


def test_benchmark_data_short_of_10d_keeps_none(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0, return_10d=12.0)

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(5)})

    row = _row(store)
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert pd.isna(row["benchmark_return_10d"])
    assert pd.isna(row["excess_10d"])


# ──────────────────────────────────────────────
# 3. stock / benchmark 동기화
# ──────────────────────────────────────────────
def test_stock_return_none_keeps_excess_none(store):
    _add_record(store)

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(20)})

    row = _row(store)
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert pd.isna(row["excess_5d"])
    assert pd.isna(row["excess_10d"])
    assert pd.isna(row["excess_20d"])


def test_compute_excess_requires_both_values():
    assert compute_excess(5.0, 2.0) == pytest.approx(3.0)
    assert compute_excess(None, 2.0) is None
    assert compute_excess(5.0, None) is None
    assert compute_excess(float("nan"), 2.0) is None


# ──────────────────────────────────────────────
# 4. 거래일 gap
# ──────────────────────────────────────────────
def test_trading_day_gap_uses_row_order():
    # 영업일(B) 기준 → 주말이 자연스럽게 제외된다.
    df = _benchmark_df(5, freq="B")
    computed = compute_benchmark_returns(df, SIGNAL_DATE)

    assert computed["benchmark_return_5d"] == pytest.approx(5.0)
    assert df["date"].iloc[5] == pd.Timestamp("2024-01-08")


# ──────────────────────────────────────────────
# 5. signal_date 미존재 / unknown market
# ──────────────────────────────────────────────
def test_signal_date_missing_in_benchmark_leaves_record_untouched(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0)
    before = store.load()

    stats = run_update_benchmark(
        store=store, benchmark_map={"KS11": _benchmark_df(20, start="2024-02-01")}
    )

    assert stats["missing_benchmark"] == 1
    assert stats["updated"] == 0
    pd.testing.assert_frame_equal(store.load(), before)


def test_unknown_market_leaves_record_untouched(store):
    _add_record(store, market="NYSE")
    _set_returns(store, return_5d=5.0)
    before = store.load()

    stats = run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(20)})

    assert stats["unknown_market"] == 1
    assert stats["updated"] == 0
    pd.testing.assert_frame_equal(store.load(), before)


def test_normalize_market_variants():
    assert normalize_market("KOSPI") == "KS11"
    assert normalize_market("ks11") == "KS11"
    assert normalize_market("KOSDAQ") == "KQ11"
    assert normalize_market("kq11") == "KQ11"
    assert normalize_market("UNKNOWN") is None
    assert normalize_market(None) is None


# ──────────────────────────────────────────────
# 6. CANDIDATE / EXCLUDED
# ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "foreign_status,decision",
    [(FOREIGN_STATUS_POSITIVE, DECISION_CANDIDATE), (FOREIGN_STATUS_NEGATIVE, DECISION_EXCLUDED)],
)
def test_candidate_and_excluded_are_computed_identically(store, foreign_status, decision):
    _add_record(store, foreign_status=foreign_status)
    _set_returns(store, return_5d=7.0)

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(5)})

    row = _row(store)
    assert row["decision"] == decision
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(2.0)


# ──────────────────────────────────────────────
# 7. 불변성 / mismatch / dry-run
# ──────────────────────────────────────────────
def test_immutable_fields_unchanged(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0)
    before = store.load().iloc[0]

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(20)})

    after = _row(store)
    for field in IMMUTABLE_FIELDS:
        assert str(after[field]) == str(before[field])


def test_existing_mismatch_is_not_overwritten(store, capsys):
    _add_record(store)
    _set_returns(store, return_5d=5.0, benchmark_return_5d=99.0)

    stats = run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(5)})

    row = _row(store)
    assert row["benchmark_return_5d"] == pytest.approx(99.0)
    assert row["excess_5d"] == pytest.approx(5.0 - 99.0)
    assert stats["mismatch"] == 1
    assert "덮어쓰지 않음" in capsys.readouterr().out


def test_dry_run_does_not_modify_file(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0)
    before = store.path.read_text(encoding="utf-8")

    stats = run_update_benchmark(
        store=store, benchmark_map={"KS11": _benchmark_df(20)}, dry_run=True
    )

    assert stats["new_benchmark_5d"] == 1
    assert stats["updated"] == 0
    assert store.path.read_text(encoding="utf-8") == before


# ──────────────────────────────────────────────
# 8. STEP 3 status 불변
# ──────────────────────────────────────────────
def test_status_is_not_changed_by_benchmark_step(store):
    _add_record(store)
    _set_returns(store, return_5d=5.0, status=STATUS_5D_DONE)

    run_update_benchmark(store=store, benchmark_map={"KS11": _benchmark_df(20)})

    assert _row(store)["status"] == STATUS_5D_DONE

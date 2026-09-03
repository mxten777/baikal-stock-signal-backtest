"""
Shadow STEP 3 — Forward Return(5D/10D/20D) 추적 단위 테스트.

검증 대상:
  1. 5거래일 미도래 → return_5d None / OPEN
  2. 정확히 5거래일 도래 → return_5d 계산 / 5D_DONE
  3. 10거래일 도래 → 5D+10D / 10D_DONE
  4. 20거래일 도래 → 5D+10D+20D / COMPLETE
  5. 주말/공휴일 gap에서도 거래일 index 기준 계산
  6. signal_date가 가격 데이터에 없음 → record 불변
  7. CANDIDATE 성과 계산
  8. EXCLUDED 성과 계산
  9. Immutable 필드 변경 없음
  10. dry-run에서 파일 변경 없음
  11. 기존 값과 새 계산값 불일치 시 조용히 overwrite하지 않음

네트워크/외부 데이터 호출 없이 fixture DataFrame만 사용한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.shadow_update_returns import run_update_returns
from src.shadow_tracking import (
    FOREIGN_STATUS_NEGATIVE,
    FOREIGN_STATUS_POSITIVE,
    IMMUTABLE_FIELDS,
    STATUS_10D_DONE,
    STATUS_5D_DONE,
    STATUS_COMPLETE,
    STATUS_OPEN,
    ShadowStore,
    compute_forward_returns,
    resolve_status,
)

TICKER = "005930"
SIGNAL_DATE = "2024-01-01"
SIGNAL_PRICE = 100.0


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


def _price_df(n_after_signal: int, freq: str = "D") -> pd.DataFrame:
    """signal_date를 첫 행으로 두고 이후 n거래일을 갖는 가격 데이터.

    close = 100 + index → +N거래일 종가는 100 + N (return_Nd = N %).
    """
    dates = pd.date_range(SIGNAL_DATE, periods=n_after_signal + 1, freq=freq)
    return pd.DataFrame({
        "date": dates,
        "close": [100.0 + i for i in range(len(dates))],
    })


def _add_record(store: ShadowStore, foreign_status: str = FOREIGN_STATUS_POSITIVE) -> None:
    store.record_signal(
        stock_code=TICKER,
        stock_name="삼성전자",
        market="KS11",
        signal_date=SIGNAL_DATE,
        signal_price=SIGNAL_PRICE,
        signal_score=80.0,
        foreign_status=foreign_status,
    )


def _row(store: ShadowStore) -> pd.Series:
    return store.load().iloc[0]


# ──────────────────────────────────────────────
# 1~4. 거래일 도래 수준별 계산 / status
# ──────────────────────────────────────────────
def test_less_than_5_trading_days_keeps_none_and_open(store):
    _add_record(store)
    run_update_returns(store=store, price_map={TICKER: _price_df(4)})

    row = _row(store)
    assert pd.isna(row["return_5d"])
    assert pd.isna(row["return_10d"])
    assert pd.isna(row["return_20d"])
    assert row["status"] == STATUS_OPEN


def test_exactly_5_trading_days_sets_return_5d(store):
    _add_record(store)
    stats = run_update_returns(store=store, price_map={TICKER: _price_df(5)})

    row = _row(store)
    assert row["return_5d"] == pytest.approx(5.0)
    assert pd.isna(row["return_10d"])
    assert row["status"] == STATUS_5D_DONE
    assert stats["new_5d"] == 1
    assert stats["new_10d"] == 0


def test_10_trading_days_sets_5d_and_10d(store):
    _add_record(store)
    run_update_returns(store=store, price_map={TICKER: _price_df(10)})

    row = _row(store)
    assert row["return_5d"] == pytest.approx(5.0)
    assert row["return_10d"] == pytest.approx(10.0)
    assert pd.isna(row["return_20d"])
    assert row["status"] == STATUS_10D_DONE


def test_20_trading_days_sets_all_and_complete(store):
    _add_record(store)
    stats = run_update_returns(store=store, price_map={TICKER: _price_df(20)})

    row = _row(store)
    assert row["return_5d"] == pytest.approx(5.0)
    assert row["return_10d"] == pytest.approx(10.0)
    assert row["return_20d"] == pytest.approx(20.0)
    assert row["status"] == STATUS_COMPLETE
    assert stats["complete"] == 1
    assert stats["pending"] == 0


# ──────────────────────────────────────────────
# 5. 주말/공휴일 gap
# ──────────────────────────────────────────────
def test_uses_trading_day_index_not_calendar_days(store):
    _add_record(store)
    # 영업일(주말 제외) + 중간 공휴일 형태로 1거래일 삭제
    price = _price_df(21, freq="B").drop(index=7).reset_index(drop=True)
    run_update_returns(store=store, price_map={TICKER: price})

    row = _row(store)
    # 행 순서 기준 +5/+10/+20번째 종가를 사용한다 (삭제된 거래일 이후는 한 칸씩 당겨짐)
    assert row["return_5d"] == pytest.approx(5.0)
    assert row["return_10d"] == pytest.approx(11.0)
    assert row["return_20d"] == pytest.approx(21.0)
    assert row["status"] == STATUS_COMPLETE


def test_compute_forward_returns_skips_calendar_gaps():
    price = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10",
            "2024-01-11", "2024-01-12",
        ]),
        "close": [100.0, 101.0, 102.0, 103.0, 104.0, 110.0],
    })
    result = compute_forward_returns(price, "2024-01-05", 100.0)
    assert result["return_5d"] == pytest.approx(10.0)
    assert result["return_10d"] is None
    assert result["return_20d"] is None


# ──────────────────────────────────────────────
# 6. signal_date 미존재
# ──────────────────────────────────────────────
def test_missing_signal_date_leaves_record_unchanged(store):
    _add_record(store)
    before = store.load()

    price = pd.DataFrame({
        "date": pd.date_range("2025-06-02", periods=30, freq="B"),
        "close": [100.0 + i for i in range(30)],
    })
    stats = run_update_returns(store=store, price_map={TICKER: price})

    pd.testing.assert_frame_equal(before, store.load())
    assert stats["missing_price"] == 1
    assert stats["checked"] == 0


def test_missing_price_data_leaves_record_unchanged(store):
    _add_record(store)
    before = store.load()

    stats = run_update_returns(store=store, price_map={})

    pd.testing.assert_frame_equal(before, store.load())
    assert stats["missing_price"] == 1


# ──────────────────────────────────────────────
# 7~8. CANDIDATE / EXCLUDED 모두 추적
# ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "foreign_status, expected_decision",
    [(FOREIGN_STATUS_POSITIVE, "CANDIDATE"), (FOREIGN_STATUS_NEGATIVE, "EXCLUDED")],
)
def test_tracks_both_candidate_and_excluded(store, foreign_status, expected_decision):
    _add_record(store, foreign_status=foreign_status)
    run_update_returns(store=store, price_map={TICKER: _price_df(20)})

    row = _row(store)
    assert row["decision"] == expected_decision
    assert row["return_20d"] == pytest.approx(20.0)
    assert row["status"] == STATUS_COMPLETE


# ──────────────────────────────────────────────
# 9. Immutable 필드 보호
# ──────────────────────────────────────────────
def test_immutable_fields_are_not_modified(store):
    _add_record(store)
    before = store.load().iloc[0]

    run_update_returns(store=store, price_map={TICKER: _price_df(20)})
    after = store.load().iloc[0]

    for field in IMMUTABLE_FIELDS:
        assert str(before[field]) == str(after[field])


def test_update_performance_rejects_immutable_field(store):
    _add_record(store)
    with pytest.raises(ValueError):
        store.update_performance({(TICKER, SIGNAL_DATE): {"signal_price": 1.0}})


# ──────────────────────────────────────────────
# 10. dry-run
# ──────────────────────────────────────────────
def test_dry_run_does_not_modify_file(store):
    _add_record(store)
    before_text = store.path.read_text(encoding="utf-8")

    stats = run_update_returns(store=store, price_map={TICKER: _price_df(20)}, dry_run=True)

    assert store.path.read_text(encoding="utf-8") == before_text
    assert stats["new_5d"] == 1
    assert stats["complete"] == 1
    assert stats["updated"] == 0


# ──────────────────────────────────────────────
# 11. 기존 값 불일치
# ──────────────────────────────────────────────
def test_existing_value_mismatch_is_reported_and_not_overwritten(store, capsys):
    _add_record(store)
    store.update_performance({(TICKER, SIGNAL_DATE): {"return_5d": 99.0, "status": STATUS_5D_DONE}})

    stats = run_update_returns(store=store, price_map={TICKER: _price_df(5)})
    captured = capsys.readouterr()

    assert stats["mismatch"] == 1
    assert "덮어쓰지 않음" in captured.out
    assert _row(store)["return_5d"] == pytest.approx(99.0)


def test_matching_existing_value_is_not_counted_as_new(store):
    _add_record(store)
    run_update_returns(store=store, price_map={TICKER: _price_df(5)})

    stats = run_update_returns(store=store, price_map={TICKER: _price_df(5)})
    assert stats["new_5d"] == 0
    assert stats["mismatch"] == 0
    assert stats["updated"] == 0


# ──────────────────────────────────────────────
# status 규칙 단위 검증
# ──────────────────────────────────────────────
def test_resolve_status_does_not_advance_on_gap():
    assert resolve_status(None, None, None) == STATUS_OPEN
    assert resolve_status(1.0, None, None) == STATUS_5D_DONE
    assert resolve_status(1.0, 2.0, None) == STATUS_10D_DONE
    assert resolve_status(1.0, 2.0, 3.0) == STATUS_COMPLETE
    # 중간 구간 결측 시 앞당기지 않는다
    assert resolve_status(None, 2.0, 3.0) == STATUS_OPEN
    assert resolve_status(1.0, None, 3.0) == STATUS_5D_DONE


def test_empty_store_returns_zero_stats(store):
    stats = run_update_returns(store=store)
    assert stats["total"] == 0
    assert stats["updated"] == 0

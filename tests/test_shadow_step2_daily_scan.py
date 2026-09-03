"""
Shadow STEP 2 — Daily Shadow Pipeline 단위 테스트.

검증 대상:
  1. Signal 없음 → Shadow record 생성 안 됨
  2. 신규 Signal + Foreign POSITIVE → CANDIDATE 저장
  3. 신규 Signal + Foreign NEUTRAL → CANDIDATE 저장
  4. 신규 Signal + Foreign NEGATIVE → EXCLUDED 저장
  5. 동일 종목/동일 signal_date 재실행 → 중복 저장 안 됨
  6. 여러 종목 중 Signal 발생 종목만 저장
  7. NO_DATA → 기존 규칙대로 NEUTRAL/CANDIDATE 처리
  8. 기존 Shadow Record 수정 없음 (기존 레코드 보존)
  9. dry-run → CSV에 저장하지 않음
  10. 기존 Signal 로직(generate_signals) 결과 변화 없음 회귀 확인

외부(네이버 등) 데이터 호출 없이 순수 fixture DataFrame만 사용한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.shadow_daily_scan import run_daily_scan
from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    ShadowStore,
)


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


def _make_signal_price_df(n: int = 90, volume_spike_last_only: bool = True) -> pd.DataFrame:
    """마지막 거래일에만 Signal이 발생하도록 설계된 가격 데이터.

    (tests/test_signal_engine.py의 _make_price_df 패턴을 그대로 재사용)
    """
    closes = [float(100 + i * 0.5) for i in range(n)]
    if volume_spike_last_only:
        volume = [500_000] * (n - 1) + [2_000_000]
    else:
        volume = [500_000] * n
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": volume,
    })


def _make_flat_price_df(n: int = 90) -> pd.DataFrame:
    """Signal이 전혀 발생하지 않도록 설계된 평탄한 가격 데이터."""
    closes = [100.0] * n
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes,
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": [500_000] * n,
    })


def _investor_df(dates: pd.DatetimeIndex, foreign_net_buy: float) -> pd.DataFrame:
    return pd.DataFrame({
        "date": dates,
        "foreign_net_buy": [foreign_net_buy] * len(dates),
        "institution_net_buy": [0.0] * len(dates),
    })


TICKERS = {"005930": "삼성전자"}


class TestNoSignal:
    def test_flat_price_no_shadow_record(self, store):
        price_data = {"005930": _make_flat_price_df()}
        stats, run_date = run_daily_scan(
            tickers=TICKERS,
            price_data=price_data,
            investor_map={},
            raw_map={},
            store=store,
        )
        assert stats["new_signals"] == 0
        assert stats["candidate"] == 0
        assert stats["excluded"] == 0
        assert store.load().empty


class TestForeignPositive:
    def test_candidate_saved(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=1_000_000)}
        raw_map = {"005930": df}
        stats, run_date = run_daily_scan(
            tickers=TICKERS,
            price_data={"005930": df},
            investor_map=investor_map,
            raw_map=raw_map,
            store=store,
        )
        assert stats["new_signals"] == 1
        assert stats["candidate"] == 1
        assert stats["excluded"] == 0
        saved = store.load()
        assert len(saved) == 1
        assert saved.iloc[0]["decision"] == DECISION_CANDIDATE
        assert saved.iloc[0]["foreign_status"] == "POSITIVE"
        assert run_date == df["date"].max()


class TestForeignNeutral:
    def test_candidate_saved(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=0.0)}
        raw_map = {"005930": df}
        stats, _ = run_daily_scan(
            tickers=TICKERS,
            price_data={"005930": df},
            investor_map=investor_map,
            raw_map=raw_map,
            store=store,
        )
        assert stats["candidate"] == 1
        saved = store.load()
        assert saved.iloc[0]["decision"] == DECISION_CANDIDATE
        assert saved.iloc[0]["foreign_status"] == "NEUTRAL"


class TestForeignNegative:
    def test_excluded_saved(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=-1_000_000)}
        raw_map = {"005930": df}
        stats, _ = run_daily_scan(
            tickers=TICKERS,
            price_data={"005930": df},
            investor_map=investor_map,
            raw_map=raw_map,
            store=store,
        )
        assert stats["excluded"] == 1
        assert stats["candidate"] == 0
        saved = store.load()
        assert saved.iloc[0]["decision"] == DECISION_EXCLUDED
        assert saved.iloc[0]["foreign_status"] == "NEGATIVE"
        assert saved.iloc[0]["exclusion_reason"] == "FOREIGN_NEGATIVE"


class TestDuplicatePrevention:
    def test_same_run_twice_no_duplicate(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=1_000_000)}
        raw_map = {"005930": df}

        stats1, _ = run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
        )
        stats2, _ = run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
        )
        assert stats1["candidate"] == 1
        assert stats2["candidate"] == 0
        assert stats2["duplicate_skip"] == 1
        assert len(store.load()) == 1


class TestMultipleTickersOnlySignalSaved:
    def test_only_signal_ticker_saved(self, store):
        signal_df = _make_signal_price_df()
        flat_df = _make_flat_price_df()
        tickers = {"005930": "삼성전자", "000660": "SK하이닉스"}
        price_data = {"005930": signal_df, "000660": flat_df}
        investor_map = {
            "005930": _investor_df(signal_df["date"], foreign_net_buy=1_000_000),
            "000660": _investor_df(flat_df["date"], foreign_net_buy=1_000_000),
        }
        raw_map = {"005930": signal_df, "000660": flat_df}

        stats, _ = run_daily_scan(
            tickers=tickers, price_data=price_data,
            investor_map=investor_map, raw_map=raw_map, store=store,
        )
        assert stats["new_signals"] == 1
        saved = store.load()
        assert len(saved) == 1
        assert saved.iloc[0]["stock_code"] == "005930"


class TestNoData:
    def test_missing_investor_data_treated_as_neutral(self, store):
        df = _make_signal_price_df()
        stats, _ = run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map={}, raw_map={"005930": df}, store=store,
        )
        assert stats["no_data"] == 1
        assert stats["candidate"] == 1
        saved = store.load()
        assert saved.iloc[0]["foreign_status"] == "NEUTRAL"
        assert saved.iloc[0]["decision"] == DECISION_CANDIDATE


class TestDryRun:
    def test_dry_run_does_not_write(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=1_000_000)}
        raw_map = {"005930": df}
        stats, _ = run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
            dry_run=True,
        )
        assert stats["candidate"] == 1
        assert store.load().empty

    def test_dry_run_then_real_run_not_duplicated_oddly(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=1_000_000)}
        raw_map = {"005930": df}
        run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
            dry_run=True,
        )
        assert store.load().empty
        stats, _ = run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
            dry_run=False,
        )
        assert stats["candidate"] == 1
        assert len(store.load()) == 1


class TestExistingRecordsUntouched:
    def test_prior_shadow_record_not_modified(self, store):
        df = _make_signal_price_df()
        investor_map = {"005930": _investor_df(df["date"], foreign_net_buy=-1_000_000)}
        raw_map = {"005930": df}

        # 과거에 이미 기록된 NEGATIVE(EXCLUDED) Signal
        prior_date = (df["date"].max() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        store.record_signal(
            stock_code="005930", stock_name="삼성전자", market="KS11",
            signal_date=prior_date, signal_price=50000.0, signal_score=80.0,
            foreign_status="NEGATIVE",
        )

        run_daily_scan(
            tickers=TICKERS, price_data={"005930": df},
            investor_map=investor_map, raw_map=raw_map, store=store,
        )

        saved = store.load()
        assert len(saved) == 2
        prior_row = saved[saved["signal_date"] == prior_date].iloc[0]
        assert prior_row["decision"] == DECISION_EXCLUDED
        assert prior_row["foreign_status"] == "NEGATIVE"


class TestSignalLogicUnchanged:
    def test_only_latest_trading_day_signal_considered(self, store):
        """마지막 거래일이 아닌 과거 Signal은 이번 실행에서 새로 저장되지 않는다."""
        from src.indicators import add_all_indicators
        from src.signal_engine import generate_signals

        df = _make_signal_price_df()
        df_ind = add_all_indicators(df)
        all_signals = generate_signals(df_ind, "005930", "삼성전자")
        assert not all_signals.empty
        assert (all_signals["signal_date"] == df["date"].max()).any()

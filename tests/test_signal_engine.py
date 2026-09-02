"""
Signal Engine 단위 테스트
"""

import pytest
import pandas as pd
import numpy as np
from src.signal_engine import (
    score_trend,
    score_volume,
    score_momentum,
    raw_to_score,
    is_overheated,
    generate_signals,
    compute_v2_penalties,
    compute_raw_score_v2,
    generate_signals_v2,
    V2_VOLUME_PENALTY,
    V2_PRE_RETURN_PENALTY,
    V2_RSI_PENALTY,
)
from src.indicators import add_all_indicators


def _base_row(**kwargs) -> pd.Series:
    defaults = {
        "close": 100.0,
        "prev_close": 98.0,
        "ma5": 99.0,
        "ma20": 95.0,
        "ma60": 90.0,
        "volume": 2_000_000,
        "volume_ma20": 1_000_000,
        "rsi": 55.0,
        "macd": 0.5,
        "macd_signal": 0.3,
        "return_5d_pct": 2.0,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


class TestScoreTrend:
    def test_all_conditions_met(self):
        row = _base_row()
        prev_ma20 = 94.0  # ma20(95) > prev_ma20(94)
        assert score_trend(row, prev_ma20) == 25

    def test_no_conditions(self):
        row = _base_row(close=80.0, ma5=85.0, ma20=90.0, ma60=95.0)
        assert score_trend(row, 91.0) == 0

    def test_partial_conditions(self):
        # MA5 > MA20 (+7), Close > MA20 (+5)
        row = _base_row(ma5=96.0, ma20=95.0, ma60=96.0, close=97.0)
        prev_ma20 = 95.0  # ma20 안 올라서 +6 없음
        score = score_trend(row, prev_ma20)
        assert score == 12  # 7 + 5


class TestScoreVolume:
    def test_volume_2x(self):
        row = _base_row(volume=2_000_000, volume_ma20=1_000_000, close=101.0, prev_close=99.0)
        # 2x = +15, close>prev_close AND volume>vma = +5 → 20
        assert score_volume(row) == 20

    def test_volume_1_5x(self):
        row = _base_row(volume=1_500_000, volume_ma20=1_000_000, close=99.0, prev_close=100.0)
        # 1.5x = +10, close < prev_close so no bonus → 10
        assert score_volume(row) == 10

    def test_volume_below_1_2x(self):
        row = _base_row(volume=1_100_000, volume_ma20=1_000_000, close=99.0, prev_close=98.0)
        # < 1.2x = 0, close>prev+volume>vma = +5
        assert score_volume(row) == 5

    def test_max_capped_at_20(self):
        row = _base_row(volume=5_000_000, volume_ma20=1_000_000, close=101.0, prev_close=99.0)
        assert score_volume(row) <= 20


class TestScoreMomentum:
    def test_rsi_45_60_range(self):
        row = _base_row(rsi=52.0, macd=0.5, macd_signal=0.3, return_5d_pct=1.0)
        # RSI 45~60: +5, macd>signal 유지: +3, return>0: +3 = 11
        prev_macd_diff = 0.1  # already above, no crossover
        assert score_momentum(row, prev_macd_diff) == 11

    def test_rsi_60_70_range(self):
        row = _base_row(rsi=65.0, macd=0.5, macd_signal=0.3, return_5d_pct=1.0)
        prev_macd_diff = 0.1
        # RSI 60~70: +7, macd>signal: +3, return>0: +3 = 13
        assert score_momentum(row, prev_macd_diff) == 13

    def test_macd_crossover(self):
        row = _base_row(rsi=55.0, macd=0.5, macd_signal=0.3, return_5d_pct=1.0)
        prev_macd_diff = -0.1  # 음수에서 양수로 → 상향 돌파
        # RSI 45~60: +5, crossover: +7, return: +3 = 15
        assert score_momentum(row, prev_macd_diff) == 15

    def test_rsi_over_70_zero(self):
        row = _base_row(rsi=75.0, macd=0.5, macd_signal=0.3, return_5d_pct=1.0)
        prev_macd_diff = 0.1
        # RSI>70: 0, macd>signal: +3, return: +3 = 6
        assert score_momentum(row, prev_macd_diff) == 6


class TestRawToScore:
    def test_max_raw(self):
        assert raw_to_score(65) == 100.0

    def test_zero_raw(self):
        assert raw_to_score(0) == 0.0

    def test_mid_raw(self):
        # 52/65*100 = 80.0
        assert raw_to_score(52) == pytest.approx(80.0, abs=0.1)


class TestOverheated:
    def test_high_rsi_overheated(self):
        row = _base_row(rsi=80.0, return_5d_pct=5.0, volume=1_500_000, volume_ma20=1_000_000)
        assert is_overheated(row) is True

    def test_high_return_overheated(self):
        row = _base_row(rsi=60.0, return_5d_pct=25.0, volume=1_500_000, volume_ma20=1_000_000)
        assert is_overheated(row) is True

    def test_extreme_volume_overheated(self):
        row = _base_row(rsi=60.0, return_5d_pct=5.0, volume=5_000_000, volume_ma20=1_000_000)
        assert is_overheated(row) is True

    def test_normal_not_overheated(self):
        row = _base_row(rsi=60.0, return_5d_pct=5.0, volume=1_500_000, volume_ma20=1_000_000)
        assert is_overheated(row) is False


class TestGenerateSignals:
    def _make_price_df(self, n: int = 100) -> pd.DataFrame:
        """신호가 발생하도록 설계된 가격 데이터"""
        closes = [float(100 + i * 0.5) for i in range(n)]
        return pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [2_000_000 if i > 60 else 500_000 for i in range(n)],
        })

    def test_no_lookahead_bias(self):
        """Signal 발생일이 미래 날짜를 포함하지 않아야 한다."""
        df = self._make_price_df(150)
        df_ind = add_all_indicators(df)
        signals = generate_signals(df_ind, "TEST", "테스트")
        if not signals.empty:
            for _, sig in signals.iterrows():
                sig_date = pd.to_datetime(sig["signal_date"])
                assert sig_date in df["date"].values

    def test_75_crossover_condition(self):
        """전일 score < 75, 오늘 score >= 75 조건만 Signal 생성."""
        df = self._make_price_df(150)
        df_ind = add_all_indicators(df)
        signals = generate_signals(df_ind, "TEST", "테스트")
        # 모든 signal의 score는 >= 75여야 한다
        if not signals.empty:
            assert (signals["score"] >= 75).all()

    def test_returns_dataframe(self):
        df = self._make_price_df(150)
        df_ind = add_all_indicators(df)
        result = generate_signals(df_ind, "TEST", "테스트")
        assert isinstance(result, pd.DataFrame)

    def test_uses_previous_signal_threshold(self, monkeypatch):
        import src.config as config

        original_signal = config.SIGNAL_THRESHOLD
        original_prev = config.SIGNAL_PREV_THRESHOLD
        config.SIGNAL_THRESHOLD = 75
        config.SIGNAL_PREV_THRESHOLD = 90

        try:
            df = pd.DataFrame([
                {
                    "date": pd.Timestamp("2023-01-02"), "open": 100.0, "high": 101.0, "low": 99.0,
                    "close": 100.5, "volume": 1_000_000,
                    "ma5": 99.0, "ma20": 98.0, "ma60": 97.0,
                    "volume_ma20": 1_000_000, "rsi": 60.0, "macd": 0.4, "macd_signal": 0.2,
                    "return_5d_pct": 1.0,
                },
                {
                    "date": pd.Timestamp("2023-01-03"), "open": 101.0, "high": 102.0, "low": 100.0,
                    "close": 101.5, "volume": 1_000_000,
                    "ma5": 100.0, "ma20": 99.0, "ma60": 98.0,
                    "volume_ma20": 1_000_000, "rsi": 62.0, "macd": 0.5, "macd_signal": 0.2,
                    "return_5d_pct": 1.5,
                },
            ])

            monkeypatch.setattr("src.signal_engine.compute_raw_score", lambda row, prev_ma20, prev_macd_diff: 80 if row["date"] == df.iloc[0]["date"] else 90)
            monkeypatch.setattr("src.signal_engine.raw_to_score", lambda raw: float(raw))

            result = generate_signals(df, "TEST", "테스트")
            assert len(result) == 2
        finally:
            config.SIGNAL_THRESHOLD = original_signal
            config.SIGNAL_PREV_THRESHOLD = original_prev


class TestV2Penalties:
    def _base_row(self, **kwargs) -> pd.Series:
        defaults = {
            "close": 100.0,
            "prev_close": 98.0,
            "ma5": 99.0,
            "ma20": 95.0,
            "ma60": 90.0,
            "volume": 2_000_000,
            "volume_ma20": 1_000_000,
            "rsi": 55.0,
            "macd": 0.5,
            "macd_signal": 0.3,
            "return_5d_pct": 2.0,
        }
        defaults.update(kwargs)
        return pd.Series(defaults)

    def test_no_penalty_normal(self):
        row = self._base_row(
            volume=1_500_000, volume_ma20=1_000_000,
            return_5d_pct=5.0, rsi=55.0,
        )
        vol, pre, rsi = compute_v2_penalties(row)
        assert vol == 0
        assert pre == 0
        assert rsi == 0

    def test_volume_penalty_at_3x(self):
        # volume_ratio = 3.0 → penalty
        row = self._base_row(volume=3_000_000, volume_ma20=1_000_000)
        vol, pre, rsi = compute_v2_penalties(row)
        assert vol == V2_VOLUME_PENALTY  # 10

    def test_volume_penalty_below_3x(self):
        # volume_ratio = 2.99 → no penalty
        row = self._base_row(volume=2_990_000, volume_ma20=1_000_000)
        vol, pre, rsi = compute_v2_penalties(row)
        assert vol == 0

    def test_pre_return_penalty_at_12pct(self):
        row = self._base_row(return_5d_pct=12.0)
        vol, pre, rsi = compute_v2_penalties(row)
        assert pre == V2_PRE_RETURN_PENALTY  # 10

    def test_pre_return_penalty_below_12pct(self):
        row = self._base_row(return_5d_pct=11.99)
        vol, pre, rsi = compute_v2_penalties(row)
        assert pre == 0

    def test_rsi_penalty_at_70(self):
        row = self._base_row(rsi=70.0)
        vol, pre, rsi_pen = compute_v2_penalties(row)
        assert rsi_pen == V2_RSI_PENALTY  # 5

    def test_rsi_penalty_below_70(self):
        row = self._base_row(rsi=69.99)
        vol, pre, rsi_pen = compute_v2_penalties(row)
        assert rsi_pen == 0

    def test_all_penalties_combined(self):
        row = self._base_row(
            volume=3_000_000, volume_ma20=1_000_000,
            return_5d_pct=15.0, rsi=72.0,
        )
        vol, pre, rsi_pen = compute_v2_penalties(row)
        assert vol == V2_VOLUME_PENALTY
        assert pre == V2_PRE_RETURN_PENALTY
        assert rsi_pen == V2_RSI_PENALTY

    def test_adjusted_score_reduces_raw(self):
        # Build a row that would trigger all penalties
        row = self._base_row(
            volume=3_000_000, volume_ma20=1_000_000,
            return_5d_pct=15.0, rsi=72.0,
        )
        prev_ma20 = 94.0
        prev_macd_diff = 0.1
        from src.signal_engine import compute_raw_score
        raw_v1 = compute_raw_score(row, prev_ma20, prev_macd_diff)
        raw_v2 = compute_raw_score_v2(row, prev_ma20, prev_macd_diff)
        total_penalty = V2_VOLUME_PENALTY + V2_PRE_RETURN_PENALTY + V2_RSI_PENALTY
        assert raw_v2 == max(raw_v1 - total_penalty, 0)

    def test_adjusted_score_floor_zero(self):
        # Ensure raw score never goes negative
        row = self._base_row(
            volume=3_000_000, volume_ma20=1_000_000,
            return_5d_pct=15.0, rsi=72.0,
            ma5=85.0, ma20=90.0, ma60=95.0, close=80.0,
        )
        raw_v2 = compute_raw_score_v2(row, 91.0, -0.1)
        assert raw_v2 >= 0

    def test_generate_signals_v2_returns_dataframe(self):
        n = 150
        closes = [float(100 + i * 0.5) for i in range(n)]
        df = pd.DataFrame({
            "date": pd.date_range("2022-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low":  [c - 1 for c in closes],
            "close": closes,
            "volume": [2_000_000 if i > 60 else 500_000 for i in range(n)],
        })
        df_ind = add_all_indicators(df)
        result = generate_signals_v2(df_ind, "TEST", "테스트")
        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert (result["score"] >= 75).all()
            assert "v2_volume_penalty" in result.columns
            assert "v2_pre_return_penalty" in result.columns
            assert "v2_rsi_penalty" in result.columns

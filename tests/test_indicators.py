"""
기술지표 계산 단위 테스트
"""

import pytest
import pandas as pd
import numpy as np
from src.indicators import add_moving_averages, add_rsi, add_macd, add_volume_ma


def _make_df(closes: list[float], volumes: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": volumes if volumes is not None else [1_000_000] * n,
    })


class TestMovingAverages:
    def test_ma5_value(self):
        df = _make_df(list(range(1, 11)))  # 1~10
        result = add_moving_averages(df)
        # MA5 at index 4 = mean(1,2,3,4,5) = 3.0
        assert result.loc[4, "ma5"] == pytest.approx(3.0)

    def test_ma5_nan_before_period(self):
        df = _make_df(list(range(1, 11)))
        result = add_moving_averages(df)
        assert pd.isna(result.loc[3, "ma5"])

    def test_ma20_nan_before_period(self):
        df = _make_df([100.0] * 25)
        result = add_moving_averages(df)
        assert pd.isna(result.loc[18, "ma20"])
        assert not pd.isna(result.loc[19, "ma20"])

    def test_ma60_nan_before_period(self):
        df = _make_df([100.0] * 70)
        result = add_moving_averages(df)
        assert pd.isna(result.loc[58, "ma60"])
        assert not pd.isna(result.loc[59, "ma60"])

    def test_constant_price_all_ma_equal(self):
        df = _make_df([50.0] * 70)
        result = add_moving_averages(df)
        assert result.loc[69, "ma5"] == pytest.approx(50.0)
        assert result.loc[69, "ma20"] == pytest.approx(50.0)
        assert result.loc[69, "ma60"] == pytest.approx(50.0)


class TestRSI:
    def test_rsi_range(self):
        """RSI는 항상 0~100 사이여야 한다."""
        closes = [float(i % 20 + 80) for i in range(60)]
        df = _make_df(closes)
        result = add_rsi(df)
        valid = result["rsi"].dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rsi_nan_before_period(self):
        df = _make_df([100.0] * 30)
        result = add_rsi(df, period=14)
        # EWM with min_periods — first valid at index 13
        assert pd.isna(result.loc[0, "rsi"])

    def test_all_up_rsi_high(self):
        """연속 상승 시 RSI는 높아야 한다."""
        closes = [float(100 + i) for i in range(30)]
        df = _make_df(closes)
        result = add_rsi(df)
        valid = result["rsi"].dropna()
        assert valid.iloc[-1] > 80

    def test_all_down_rsi_low(self):
        """연속 하락 시 RSI는 낮아야 한다."""
        closes = [float(200 - i) for i in range(30)]
        df = _make_df(closes)
        result = add_rsi(df)
        valid = result["rsi"].dropna()
        assert valid.iloc[-1] < 20


class TestMACD:
    def test_macd_columns_exist(self):
        df = _make_df([float(i) for i in range(50)])
        result = add_macd(df)
        assert "macd" in result.columns
        assert "macd_signal" in result.columns

    def test_macd_uptrend_positive(self):
        """강한 상승 추세에서 MACD는 양수여야 한다."""
        closes = [float(100 + i * 2) for i in range(60)]
        df = _make_df(closes)
        result = add_macd(df)
        assert result["macd"].iloc[-1] > 0

    def test_macd_downtrend_negative(self):
        """강한 하락 추세에서 MACD는 음수여야 한다."""
        closes = [float(300 - i * 2) for i in range(60)]
        df = _make_df(closes)
        result = add_macd(df)
        assert result["macd"].iloc[-1] < 0

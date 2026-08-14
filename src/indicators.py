"""
기술지표 계산 모듈
입력: pandas DataFrame (date, open, high, low, close, volume)
출력: 지표 컬럼이 추가된 DataFrame
"""

import pandas as pd
import numpy as np


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """MA5, MA20, MA60 계산"""
    df = df.copy()
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    return df


def add_volume_ma(df: pd.DataFrame) -> pd.DataFrame:
    """Volume MA20 계산"""
    df = df.copy()
    df["volume_ma20"] = df["volume"].rolling(20).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """RSI 계산 (Wilder's smoothing)"""
    df = df.copy()
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    # avg_loss=0 이면 RSI=100 (완전 상승 구간)
    rsi_vals = np.where(
        avg_loss == 0,
        100.0,
        100.0 - (100.0 / (1.0 + avg_gain / avg_loss)),
    )
    df["rsi"] = np.where(avg_gain.isna() | avg_loss.isna(), np.nan, rsi_vals)
    return df


def add_macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD, MACD Signal 계산"""
    df = df.copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd"] = ema_fast - ema_slow
    df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
    return df


def add_return_5d(df: pd.DataFrame) -> pd.DataFrame:
    """최근 5거래일 수익률 (%) 계산"""
    df = df.copy()
    df["return_5d_pct"] = df["close"].pct_change(5) * 100
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """모든 기술지표를 한번에 추가"""
    df = add_moving_averages(df)
    df = add_volume_ma(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_return_5d(df)
    return df

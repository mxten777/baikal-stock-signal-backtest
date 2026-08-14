"""
Signal 발생일 이후 수익률 및 최대낙폭 계산
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def compute_forward_returns(
    signals_df: pd.DataFrame,
    price_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    signals_df: generate_signals() 결과
    price_df: 해당 종목의 전체 가격 DataFrame (date, close, low 포함)

    각 Signal 행에 return_5d, return_10d, return_20d, max_drawdown_20d를 채운다.
    미래 데이터가 부족한 경우 NaN으로 남긴다.
    """
    if signals_df.empty:
        return signals_df

    price_df = price_df.copy().reset_index(drop=True)
    price_df["date"] = pd.to_datetime(price_df["date"])

    signals = signals_df.copy()
    signals["signal_date"] = pd.to_datetime(signals["signal_date"])

    date_to_idx = {d: i for i, d in enumerate(price_df["date"])}

    for idx, row in signals.iterrows():
        sig_date = row["signal_date"]
        if sig_date not in date_to_idx:
            continue
        i = date_to_idx[sig_date]
        sig_close = row["signal_close"]

        for period, col in [(5, "return_5d"), (10, "return_10d"), (20, "return_20d")]:
            future_idx = i + period
            if future_idx < len(price_df):
                future_close = price_df.loc[future_idx, "close"]
                signals.at[idx, col] = round(
                    (future_close - sig_close) / sig_close * 100, 2
                )

        # 최대낙폭: 발생일 이후 20거래일 구간의 최저가 기준
        end_idx = min(i + 20, len(price_df) - 1)
        if end_idx > i:
            min_low = price_df.loc[i + 1 : end_idx, "low"].min()
            signals.at[idx, "max_drawdown_20d"] = round(
                (min_low - sig_close) / sig_close * 100, 2
            )

    return signals

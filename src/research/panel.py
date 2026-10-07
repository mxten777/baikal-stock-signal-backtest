"""Causal features and calendar-session targets for Fixed-574 research."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators import add_all_indicators
from src.signal_engine import (
    compute_v2_penalties,
    is_overheated,
    raw_to_score,
    score_momentum,
    score_trend,
    score_volume,
)

HORIZONS = (5, 10, 20)
STRATEGIES = ("A", "B", "C")
OHLCV = ("open", "high", "low", "close", "volume")
THRESHOLD = 75
WARMUP = 60


def normalize_history(frame: pd.DataFrame, cutoff: str) -> pd.DataFrame:
    required = {"date", *OHLCV}
    if not required.issubset(frame.columns):
        raise ValueError(f"Missing OHLCV columns: {sorted(required - set(frame.columns))}")
    result = frame[["date", *OHLCV]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    dates = result["date"]
    if dates.isna().any() or dates.duplicated().any():
        raise ValueError("Null or duplicate history date")
    if dates.dt.tz is not None or dates.ne(dates.dt.normalize()).any():
        raise ValueError("History must use timezone-free session dates")
    if dates.gt(pd.Timestamp(cutoff)).any() or dates.lt(pd.Timestamp("2024-01-01")).any():
        raise ValueError("History outside frozen 2024-01-01..cutoff interval")
    for column in OHLCV:
        result[column] = pd.to_numeric(result[column], errors="raise")
    return result.sort_values("date").reset_index(drop=True)


def split_year(dates: pd.Series) -> pd.Series:
    return dates.dt.strftime("%Y")


def crossing_flags(score: pd.Series, ready: pd.Series) -> pd.Series:
    return ready & ready.shift(1, fill_value=False) & score.ge(THRESHOLD) & score.shift(1).lt(THRESHOLD)


def cooldown_flags(signals: pd.Series, sessions: int = 20) -> pd.Series:
    accepted = np.zeros(len(signals), dtype=bool)
    last = -sessions - 1
    for position in np.flatnonzero(signals.to_numpy(dtype=bool)):
        if position - last > sessions:
            accepted[position] = True
            last = position
    return pd.Series(accepted, index=signals.index)


def build_ticker_panel(
    history: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    ticker: str,
    market: str,
) -> pd.DataFrame:
    if calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Calendar must be unique and ascending")
    if not history["date"].isin(calendar).all():
        raise ValueError("History dates absent from calendar")
    frame = history.set_index("date").reindex(calendar).rename_axis("trading_date").reset_index()
    frame["ticker"] = ticker
    frame["market"] = market
    frame["session_index"] = np.arange(len(calendar))
    frame["split"] = split_year(frame["trading_date"])
    frame["evaluation_month"] = frame["trading_date"].dt.strftime("%Y-%m")
    frame["bar_present"] = frame["trading_date"].isin(history["date"])
    numeric = frame[list(OHLCV)]
    finite = pd.Series(np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1), index=frame.index)
    prices = frame[["open", "high", "low", "close"]]
    frame["bar_valid"] = (
        frame["bar_present"] & finite & prices.gt(0).all(axis=1)
        & frame["volume"].ge(0) & frame["high"].ge(frame["low"])
        & (frame["high"] + 2).ge(frame[["open", "close"]].max(axis=1))
        & frame["low"].le(frame[["open", "close"]].min(axis=1))
    )
    frame["zero_volume"] = frame["bar_present"] & frame["volume"].eq(0)
    frame["tradable_bar"] = frame["bar_valid"] & ~frame["zero_volume"]
    frame["gap_reason"] = np.select(
        [~frame["bar_present"], ~frame["bar_valid"], frame["zero_volume"]],
        ["MISSING_OR_SUSPENDED_UNKNOWN", "INVALID_BAR", "ZERO_VOLUME"],
        default="OK",
    )
    valid = frame["tradable_bar"]
    segment = (~valid).cumsum()
    frame["valid_history_count"] = valid.astype(int).groupby(segment).cumsum()
    indicator_columns = [
        "ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal",
        "return_1d_pct", "return_5d_pct", "return_10d_pct", "return_20d_pct",
        "volume_ma5", "volume_ratio", "volume_ratio_5d_20d", "volatility_20d_pct",
        "ma20_slope_1d_pct", "ma20_slope_5d_pct", "ma5_ma20_distance_pct",
        "ma20_ma60_distance_pct", "macd_histogram", "macd_pct", "prev_close",
        "prev_ma20", "prev_macd_histogram",
    ]
    frame[indicator_columns] = np.nan
    for _, run in frame.loc[valid].groupby(segment[valid], sort=False):
        indicators = add_all_indicators(run.rename(columns={"trading_date": "date"}))
        close = indicators["close"]
        for horizon in (1, 5, 10, 20):
            indicators[f"return_{horizon}d_pct"] = (close / close.shift(horizon) - 1) * 100
        indicators["volume_ma5"] = indicators["volume"].rolling(5).mean()
        indicators["volume_ratio"] = indicators["volume"] / indicators["volume_ma20"]
        indicators["volume_ratio_5d_20d"] = indicators["volume_ma5"] / indicators["volume_ma20"]
        indicators["volatility_20d_pct"] = indicators["return_1d_pct"].rolling(20).std(ddof=1)
        ma20 = indicators["ma20"]
        indicators["ma20_slope_1d_pct"] = (ma20 / ma20.shift(1) - 1) * 100
        indicators["ma20_slope_5d_pct"] = (ma20 / ma20.shift(5) - 1) * 100
        indicators["ma5_ma20_distance_pct"] = (indicators["ma5"] / ma20 - 1) * 100
        indicators["ma20_ma60_distance_pct"] = (ma20 / indicators["ma60"] - 1) * 100
        indicators["macd_histogram"] = indicators["macd"] - indicators["macd_signal"]
        indicators["macd_pct"] = indicators["macd"] / close * 100
        indicators["prev_close"] = close.shift(1)
        indicators["prev_ma20"] = ma20.shift(1)
        indicators["prev_macd_histogram"] = indicators["macd_histogram"].shift(1)
        frame.loc[run.index, indicator_columns] = indicators[indicator_columns]
    frame["ma5_above_ma20"] = frame["ma5"].gt(frame["ma20"]) & valid
    frame["ma20_above_ma60"] = frame["ma20"].gt(frame["ma60"]) & valid
    frame["close_above_ma20"] = frame["close"].gt(frame["ma20"]) & valid
    frame["macd_cross_up"] = frame["prev_macd_histogram"].le(0) & frame["macd_histogram"].gt(0)
    frame["feature_ready"] = (
        frame["valid_history_count"].ge(WARMUP) & frame[indicator_columns].notna().all(axis=1)
    )
    frame["feature_ready_120"] = frame["feature_ready"] & frame["valid_history_count"].ge(120)
    frame["feature_exclusion_reason"] = np.where(
        ~valid, frame["gap_reason"], np.where(frame["feature_ready"], "OK", "WARMUP")
    )
    score_columns = [
        "trend_raw", "volume_raw", "momentum_raw", "volume_penalty",
        "pre_return_penalty", "rsi_penalty", "v02_raw", "v02_score", "score_A", "score_B", "score_C",
    ]
    frame[score_columns] = np.nan
    frame["overheated"] = False
    score_rows: list[list[float]] = []
    heated_rows: list[bool] = []
    for _, row in frame.loc[frame["feature_ready"]].iterrows():
        trend = score_trend(row, row["prev_ma20"])
        volume = score_volume(row)
        momentum = score_momentum(row, row["prev_macd_histogram"])
        penalties = compute_v2_penalties(row)
        raw = max(trend + volume + momentum - sum(penalties), 0)
        score_a = raw_to_score(raw)
        score_rows.append([
            trend, volume, momentum, *penalties, raw, score_a, score_a,
            round(momentum / 20 * 100, 1), round((volume + momentum) / 40 * 100, 1),
        ])
        heated_rows.append(is_overheated(row))
    if score_rows:
        frame.loc[frame["feature_ready"], score_columns] = score_rows
        frame.loc[frame["feature_ready"], "overheated"] = heated_rows
    for strategy in STRATEGIES:
        score = frame[f"score_{strategy}"]
        frame[f"condition_{strategy}"] = frame["feature_ready"] & score.ge(THRESHOLD)
        signal = crossing_flags(score, frame["feature_ready"])
        frame[f"signal_{strategy}"] = signal
        nonheated = signal & ~frame["overheated"]
        frame[f"cooldown_{strategy}"] = cooldown_flags(nonheated)
        frame[f"signal_120_{strategy}"] = crossing_flags(score, frame["feature_ready_120"])
    return add_targets(frame)


def add_targets(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    dates = frame["trading_date"]
    valid = frame["tradable_bar"]
    for horizon in HORIZONS:
        exit_dates = dates.shift(-horizon)
        path_ok = valid.copy()
        invalid_price = frame["bar_present"] & ~frame["bar_valid"]
        for offset in range(1, horizon + 1):
            path_ok &= valid.shift(-offset, fill_value=False)
            invalid_price |= (frame["bar_present"] & ~frame["bar_valid"]).shift(-offset, fill_value=False)
        status = np.select(
            [exit_dates.isna(), invalid_price, ~path_ok],
            ["IMMATURE", "INVALID_PRICE", "MISSING_PATH"],
            default="AVAILABLE",
        )
        frame[f"target_date_{horizon}d"] = exit_dates
        frame[f"target_status_{horizon}d"] = status
        frame[f"future_return_{horizon}d"] = (
            (frame["close"].shift(-horizon) / frame["close"] - 1) * 100
        ).where(pd.Series(status, index=frame.index).eq("AVAILABLE"))
        frame[f"split_purged_{horizon}d"] = exit_dates.notna() & split_year(exit_dates).ne(frame["split"])
        frame[f"evaluation_ready_{horizon}d"] = (
            frame["feature_ready"] & pd.Series(status, index=frame.index).eq("AVAILABLE")
            & ~frame[f"split_purged_{horizon}d"]
        )
    frame["evaluation_ready_common_20d"] = frame[
        [f"evaluation_ready_{h}d" for h in HORIZONS]
    ].all(axis=1)
    return frame

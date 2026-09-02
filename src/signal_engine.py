"""
BAIKAL Signal Score v0.1 계산 및 Signal 판정
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from src import config


# ──────────────────────────────────────────────
# Score 구성요소
# ──────────────────────────────────────────────

def score_trend(row: pd.Series, prev_ma20: float) -> int:
    """A. Trend — 최대 25점"""
    score = 0
    if row["ma5"] > row["ma20"]:
        score += 7
    if row["ma20"] > row["ma60"]:
        score += 7
    if row["close"] > row["ma20"]:
        score += 5
    if not pd.isna(prev_ma20) and row["ma20"] > prev_ma20:
        score += 6
    return score


def score_volume(row: pd.Series) -> int:
    """B. Volume — 최대 20점"""
    score = 0
    vma = row["volume_ma20"]
    if pd.isna(vma) or vma == 0:
        return 0
    ratio = row["volume"] / vma

    # 구간은 중복 가산 없이 가장 높은 하나만 적용
    if ratio >= 2.0:
        score += 15
    elif ratio >= 1.5:
        score += 10
    elif ratio >= 1.2:
        score += 5

    # 추가 조건
    if row["close"] > row["prev_close"] and row["volume"] > vma:
        score += 5

    return min(score, 20)


def score_momentum(row: pd.Series, prev_macd_signal_diff: float) -> int:
    """C. Momentum — 최대 20점"""
    score = 0

    # RSI
    rsi = row["rsi"]
    if not pd.isna(rsi):
        if 45 <= rsi < 60:
            score += 5
        elif 60 <= rsi <= 70:
            score += 7
        # RSI > 70 → 0점

    # MACD (상향 돌파 vs 유지, 중복 불가)
    macd_diff = row["macd"] - row["macd_signal"]
    if not pd.isna(macd_diff) and not pd.isna(prev_macd_signal_diff):
        crossed_up = (prev_macd_signal_diff <= 0) and (macd_diff > 0)
        if crossed_up:
            score += 7
        elif macd_diff > 0:
            score += 3

    # 5일 수익률
    if not pd.isna(row["return_5d_pct"]) and row["return_5d_pct"] > 0:
        score += 3

    return min(score, 20)


# ──────────────────────────────────────────────
# 통합 Score 계산
# ──────────────────────────────────────────────

def compute_raw_score(row: pd.Series, prev_ma20: float, prev_macd_diff: float) -> int:
    """원점수 계산 (최대 65점)"""
    return (
        score_trend(row, prev_ma20)
        + score_volume(row)
        + score_momentum(row, prev_macd_diff)
    )


def raw_to_score(raw: int) -> float:
    """65점 만점 → 100점 환산, 소수점 1자리"""
    return round(raw / config.RAW_SCORE_MAX * 100, 1)


# ──────────────────────────────────────────────
# v0.2 Penalty 상수 (수정 금지)
# ──────────────────────────────────────────────

V2_VOLUME_THRESHOLD = 3.0
V2_VOLUME_PENALTY = 10

V2_PRE_RETURN_THRESHOLD = 12.0
V2_PRE_RETURN_PENALTY = 10

V2_RSI_THRESHOLD = 70.0
V2_RSI_PENALTY = 5


def compute_v2_penalties(row: pd.Series) -> tuple[int, int, int]:
    """v0.2 penalty 계산: (volume_penalty, pre_return_penalty, rsi_penalty)"""
    volume_penalty = 0
    vma = row.get("volume_ma20")
    if not pd.isna(vma) and vma > 0 and row["volume"] / vma >= V2_VOLUME_THRESHOLD:
        volume_penalty = V2_VOLUME_PENALTY

    pre_return_penalty = 0
    r5 = row.get("return_5d_pct")
    if not pd.isna(r5) and r5 >= V2_PRE_RETURN_THRESHOLD:
        pre_return_penalty = V2_PRE_RETURN_PENALTY

    rsi_penalty = 0
    rsi = row.get("rsi")
    if not pd.isna(rsi) and rsi >= V2_RSI_THRESHOLD:
        rsi_penalty = V2_RSI_PENALTY

    return volume_penalty, pre_return_penalty, rsi_penalty


def compute_raw_score_v2(row: pd.Series, prev_ma20: float, prev_macd_diff: float) -> int:
    """v0.2 adjusted raw score — penalty 적용 후 최저 0"""
    raw = compute_raw_score(row, prev_ma20, prev_macd_diff)
    vol_pen, pre_pen, rsi_pen = compute_v2_penalties(row)
    return max(raw - vol_pen - pre_pen - rsi_pen, 0)


def classify_signal(score: float) -> str:
    if score >= 85:
        return "STRONG_WATCH"
    elif score >= 75:
        return "BUY_WATCH"
    elif score >= 65:
        return "WAIT"
    elif score >= 50:
        return "WATCH"
    else:
        return "RISK"


def is_overheated(row: pd.Series) -> bool:
    """과열 필터: True이면 OVERHEATED로 기록"""
    if not pd.isna(row["rsi"]) and row["rsi"] > config.OVERHEATED_RSI:
        return True
    if not pd.isna(row["return_5d_pct"]) and row["return_5d_pct"] > config.OVERHEATED_RETURN_5D:
        return True
    vma = row["volume_ma20"]
    if not pd.isna(vma) and vma > 0:
        if row["volume"] > config.OVERHEATED_VOLUME_RATIO * vma:
            return True
    return False


# ──────────────────────────────────────────────
# Signal 생성 (종목 단위)
# ──────────────────────────────────────────────

def generate_signals(df: pd.DataFrame, ticker: str, name: str) -> pd.DataFrame:
    """
    지표가 계산된 DataFrame을 받아 Signal 발생 행만 반환한다.
    Look-ahead bias 없음: 당일 데이터만 사용.
    """
    df = df.copy().reset_index(drop=True)

    signals = []

    prev_score = 0.0
    prev_ma20 = float("nan")
    prev_macd_diff = float("nan")
    prev_close = float("nan")

    for i, row in df.iterrows():
        # 전일 종가를 row에 추가 (volume 조건에서 사용)
        row = row.copy()
        row["prev_close"] = prev_close

        # NaN 체크: 지표 계산에 필요한 최소 데이터 없으면 skip
        required = ["ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal"]
        if any(pd.isna(row[c]) for c in required):
            prev_close = row["close"]
            prev_ma20 = row["ma20"] if not pd.isna(row["ma20"]) else prev_ma20
            prev_macd_diff = (
                row["macd"] - row["macd_signal"]
                if not pd.isna(row["macd"]) and not pd.isna(row["macd_signal"])
                else prev_macd_diff
            )
            prev_score = 0.0
            continue

        raw = compute_raw_score(row, prev_ma20, prev_macd_diff)
        score = raw_to_score(raw)

        # 신규 Signal 발생 조건
        if prev_score < config.SIGNAL_PREV_THRESHOLD and score >= config.SIGNAL_THRESHOLD:
            signal_type = classify_signal(score)
            if is_overheated(row):
                signal_type = "OVERHEATED"

            vma = row["volume_ma20"]
            volume_ratio = row["volume"] / vma if (not pd.isna(vma) and vma > 0) else None

            signals.append({
                "ticker": ticker,
                "name": name,
                "signal_date": row["date"],
                "signal_close": row["close"],
                "raw_score": raw,
                "score": score,
                "signal_type": signal_type,
                "rsi": round(row["rsi"], 2),
                "macd": round(row["macd"], 4),
                "macd_signal": round(row["macd_signal"], 4),
                "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
                # 성과 지표는 backtest.py에서 채운다
                "return_5d": None,
                "return_10d": None,
                "return_20d": None,
                "max_drawdown_20d": None,
            })

        # 다음 루프를 위한 이전값 업데이트
        prev_close = row["close"]
        prev_ma20 = row["ma20"]
        prev_macd_diff = row["macd"] - row["macd_signal"]
        prev_score = score

    return pd.DataFrame(signals)


def generate_signals_v2(df: pd.DataFrame, ticker: str, name: str) -> pd.DataFrame:
    """
    Signal Score v0.2 — v0.1과 동일 구조, penalty 적용.
    v0.1 generate_signals 수정 없음.
    """
    df = df.copy().reset_index(drop=True)
    signals = []

    prev_score = 0.0
    prev_ma20 = float("nan")
    prev_macd_diff = float("nan")
    prev_close = float("nan")

    for i, row in df.iterrows():
        row = row.copy()
        row["prev_close"] = prev_close

        required = ["ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal"]
        if any(pd.isna(row[c]) for c in required):
            prev_close = row["close"]
            prev_ma20 = row["ma20"] if not pd.isna(row["ma20"]) else prev_ma20
            prev_macd_diff = (
                row["macd"] - row["macd_signal"]
                if not pd.isna(row["macd"]) and not pd.isna(row["macd_signal"])
                else prev_macd_diff
            )
            prev_score = 0.0
            continue

        raw = compute_raw_score_v2(row, prev_ma20, prev_macd_diff)
        score = raw_to_score(raw)

        if prev_score < config.SIGNAL_PREV_THRESHOLD and score >= config.SIGNAL_THRESHOLD:
            signal_type = classify_signal(score)
            if is_overheated(row):
                signal_type = "OVERHEATED"

            vma = row["volume_ma20"]
            volume_ratio = row["volume"] / vma if (not pd.isna(vma) and vma > 0) else None
            vol_pen, pre_pen, rsi_pen = compute_v2_penalties(row)

            signals.append({
                "ticker": ticker,
                "name": name,
                "signal_date": row["date"],
                "signal_close": row["close"],
                "raw_score": raw,
                "score": score,
                "signal_type": signal_type,
                "rsi": round(row["rsi"], 2),
                "macd": round(row["macd"], 4),
                "macd_signal": round(row["macd_signal"], 4),
                "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
                "v2_volume_penalty": vol_pen,
                "v2_pre_return_penalty": pre_pen,
                "v2_rsi_penalty": rsi_pen,
                "return_5d": None,
                "return_10d": None,
                "return_20d": None,
                "max_drawdown_20d": None,
            })

        prev_close = row["close"]
        prev_ma20 = row["ma20"]
        prev_macd_diff = row["macd"] - row["macd_signal"]
        prev_score = score

    return pd.DataFrame(signals)

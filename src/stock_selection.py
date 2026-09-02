"""
STEP 13 — Stock Selection Score

기존 Signal 후보들 중 더 좋은 종목을 선별하기 위한 보조 점수.

점수 구성 (100점 만점):
  1. Signal 강도   (기존 score 컬럼, 0~100)          weight 60%
  2. 외국인 수급 상태 (foreign 5일 누적 / 20일 평균거래량) weight 25%
  3. 실적 성장 정보 (STEP 12 성장 그룹, 보조 점수만)     weight 15%

주의:
- 실적 성장은 Hard Filter로 사용하지 않는다 (탈락 없음, 가중 점수만 반영).
- 데이터가 없는 항목은 중립값(50점)을 부여해 있는/없는 종목 간 불이익을 주지 않는다.
- Look-ahead bias 방지: 수급은 signal_date 이전 데이터만, 실적은 disclosure_date < signal_date
  조건으로 이미 필터링된 STEP 12 join 결과를 그대로 사용한다.
- 기존 Signal 생성/backtest/benchmark 로직은 수정하지 않는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ──────────────────────────────────────────────
# 가중치 (합계 100, 수정 시 과도한 튜닝 금지)
# ──────────────────────────────────────────────
SIGNAL_WEIGHT = 0.60
INVESTOR_WEIGHT = 0.25
GROWTH_WEIGHT = 0.15

NEUTRAL_SCORE = 50.0


# ──────────────────────────────────────────────
# 1. 외국인 수급 Feature / Score
# ──────────────────────────────────────────────
def compute_foreign_5d_ratio(
    signals: pd.DataFrame,
    investor_map: dict[str, pd.DataFrame],
    raw_map: dict[str, pd.DataFrame],
) -> pd.Series:
    """Signal 발생일 기준 외국인 5일 누적 순매수 / 20일 평균거래량 비율.

    투자자 수급 데이터가 없는 종목은 NaN을 반환한다 (Hard Filter 아님).
    """
    ratios = []
    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        signal_date = sig["signal_date"]

        inv_df = investor_map.get(ticker)
        raw_df = raw_map.get(ticker)
        if inv_df is None or raw_df is None:
            ratios.append(np.nan)
            continue

        past_inv = inv_df[inv_df["date"] <= signal_date].tail(5)
        past_raw = raw_df[raw_df["date"] <= signal_date].tail(20)
        if past_inv.empty or past_raw.empty:
            ratios.append(np.nan)
            continue

        avg_vol_20d = past_raw["volume"].mean()
        if pd.isna(avg_vol_20d) or avg_vol_20d == 0:
            ratios.append(np.nan)
            continue

        foreign_5d = past_inv["foreign_net_buy"].sum()
        ratios.append(foreign_5d / avg_vol_20d)

    return pd.Series(ratios, index=signals.index)


def foreign_ratio_to_score(ratio: float) -> float:
    """외국인 수급 비율 → 0~100점 (STEP 9 섹션 7 구간과 동일). 데이터 없으면 중립 50점."""
    if pd.isna(ratio):
        return NEUTRAL_SCORE
    if ratio >= 0.20:
        return 100.0
    if ratio > 0.0:
        return 75.0
    if ratio > -0.20:
        return 40.0
    return 10.0


# ──────────────────────────────────────────────
# 2. 실적 성장 Score (보조 점수 — Hard Filter 아님)
# ──────────────────────────────────────────────
def growth_row_to_score(row: pd.Series) -> float:
    """STEP 12 join 결과 한 행을 실적 성장 보조 점수(0~100)로 변환한다.

    fundamental 매칭이 없으면 중립 50점 (탈락/불이익 없음).
    """
    if pd.isna(row.get("fundamental_report_period")):
        return NEUTRAL_SCORE

    rev_yoy = row.get("revenue_yoy")
    oi_flag = row.get("oi_yoy_flag")
    oi_yoy = row.get("operating_income_yoy")
    ni_flag = row.get("ni_yoy_flag")
    ni_yoy = row.get("net_income_yoy")

    rev_pos = pd.notna(rev_yoy) and rev_yoy > 0
    oi_pos = (oi_flag == "normal" and pd.notna(oi_yoy) and oi_yoy > 0) or (oi_flag == "turnaround")
    ni_pos = (ni_flag == "normal" and pd.notna(ni_yoy) and ni_yoy > 0) or (ni_flag == "turnaround")
    rev_strong = pd.notna(rev_yoy) and rev_yoy > 10.0
    oi_strong = (oi_flag == "normal" and pd.notna(oi_yoy) and oi_yoy > 10.0) or (oi_flag == "turnaround")

    if rev_strong and oi_strong and ni_pos:
        return 100.0
    if rev_pos and oi_pos:
        return 80.0
    if oi_pos:
        return 65.0
    if rev_pos:
        return 60.0
    return 35.0


# ──────────────────────────────────────────────
# 3. 통합 Stock Selection Score
# ──────────────────────────────────────────────
def compute_stock_selection_score(
    signal_score: float, investor_score: float, growth_score: float
) -> float:
    """가중합으로 Stock Selection Score(0~100)를 계산한다."""
    total = (
        signal_score * SIGNAL_WEIGHT
        + investor_score * INVESTOR_WEIGHT
        + growth_score * GROWTH_WEIGHT
    )
    return round(total, 1)


def add_stock_selection_score(joined: pd.DataFrame) -> pd.DataFrame:
    """foreign_5d_ratio, growth join 컬럼이 포함된 DataFrame에 점수 컬럼들을 추가한다."""
    result = joined.copy()
    result["investor_score"] = result["foreign_5d_ratio"].apply(foreign_ratio_to_score)
    result["growth_score"] = result.apply(growth_row_to_score, axis=1)
    result["stock_selection_score"] = [
        compute_stock_selection_score(s, i, g)
        for s, i, g in zip(result["score"], result["investor_score"], result["growth_score"])
    ]
    return result


# ──────────────────────────────────────────────
# 4. 점수 그룹 분류 (상/중/하)
# ──────────────────────────────────────────────
def classify_score_group(df: pd.DataFrame, score_col: str = "stock_selection_score") -> pd.Series:
    """점수 3분위(tercile) 기준으로 상/중/하 그룹 라벨을 반환한다."""
    ranks = df[score_col].rank(pct=True, method="first")
    labels = pd.Series(index=df.index, dtype=object)
    labels[ranks <= 1 / 3] = "LOW"
    labels[(ranks > 1 / 3) & (ranks <= 2 / 3)] = "MID"
    labels[ranks > 2 / 3] = "HIGH"
    return labels

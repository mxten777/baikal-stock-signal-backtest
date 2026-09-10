"""
src/dual_shadow_evaluator.py
============================
DUAL Shadow STEP 2 — Latest-Day Evaluation Adapter.

v0.1 Baseline과 v0.2 Challenger의 최신 거래일 평가 결과를
비교 가능한 순수 객체 형태로 반환하는 Evaluation Adapter.

기존 generate_signals() / generate_signals_v2()의 핵심 신호 규칙
(prev_score, SIGNAL_PREV_THRESHOLD, SIGNAL_THRESHOLD, overheating)을
100% 동일하게 보존하며, Challenger가 Signal을 발생시키지 않는 경우에도
score, signal_type, penalty 정보를 온전히 유지한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src import config
from src.signal_engine import (
    classify_signal,
    compute_raw_score,
    compute_raw_score_v2,
    compute_v2_penalties,
    is_overheated,
    raw_to_score,
)

REQUIRED_COLUMNS = ["ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal"]


@dataclass(frozen=True)
class BaselineEvaluation:
    raw_score: int | None
    score: float | None
    signal_type: str | None
    signal_present: bool


@dataclass(frozen=True)
class ChallengerEvaluation:
    raw_score: int | None
    score: float | None
    signal_type: str | None
    signal_present: bool
    volume_penalty: int
    pre_return_penalty: int
    rsi_penalty: int
    total_penalty: int


@dataclass(frozen=True)
class DualShadowEvaluation:
    trade_date: str
    ticker: str
    name: str
    close: float | None
    baseline: BaselineEvaluation
    challenger: ChallengerEvaluation
    comparison_group: str
    evaluation_status: str

    def to_dict(self) -> dict[str, Any]:
        """반환 schema 규격에 맞춘 dict 변환."""
        return {
            "trade_date": self.trade_date,
            "ticker": self.ticker,
            "name": self.name,
            "close": self.close,
            "baseline": {
                "raw_score": self.baseline.raw_score,
                "score": self.baseline.score,
                "signal_type": self.baseline.signal_type,
                "signal_present": self.baseline.signal_present,
            },
            "challenger": {
                "raw_score": self.challenger.raw_score,
                "score": self.challenger.score,
                "signal_type": self.challenger.signal_type,
                "signal_present": self.challenger.signal_present,
                "volume_penalty": self.challenger.volume_penalty,
                "pre_return_penalty": self.challenger.pre_return_penalty,
                "rsi_penalty": self.challenger.rsi_penalty,
                "total_penalty": self.challenger.total_penalty,
            },
            "comparison_group": self.comparison_group,
            "evaluation_status": self.evaluation_status,
        }


def _make_not_evaluable(
    ticker: str,
    name: str,
    trade_date: str = "",
    close: float | None = None,
) -> DualShadowEvaluation:
    return DualShadowEvaluation(
        trade_date=trade_date,
        ticker=ticker,
        name=name,
        close=close,
        baseline=BaselineEvaluation(
            raw_score=None,
            score=None,
            signal_type=None,
            signal_present=False,
        ),
        challenger=ChallengerEvaluation(
            raw_score=None,
            score=None,
            signal_type=None,
            signal_present=False,
            volume_penalty=0,
            pre_return_penalty=0,
            rsi_penalty=0,
            total_penalty=0,
        ),
        comparison_group="BOTH_NO",
        evaluation_status="NOT_EVALUABLE",
    )


def evaluate_latest_day(
    df_ind: pd.DataFrame | None,
    ticker: str,
    name: str,
) -> DualShadowEvaluation:
    """최신 거래일에 대한 v0.1 Baseline 및 v0.2 Challenger 평가 수행.

    Look-ahead bias 없이 과거 시계열을 따라가며 prev_score 상태를 추적하고,
    최신 거래일 시점의 점수/신호 발생 여부/페널티/비교군을 계산한다.
    """
    if df_ind is None or not isinstance(df_ind, pd.DataFrame) or df_ind.empty:
        return _make_not_evaluable(ticker, name)

    df = df_ind.copy().reset_index(drop=True)

    # 필수 컬럼 존재 여부 확인
    for col in REQUIRED_COLUMNS + ["close"]:
        if col not in df.columns:
            latest_date_str = ""
            if "date" in df.columns and not df["date"].empty:
                last_d = df["date"].iloc[-1]
                latest_date_str = last_d.strftime("%Y-%m-%d") if hasattr(last_d, "strftime") else str(last_d)[:10]
            close_val = float(df["close"].iloc[-1]) if ("close" in df.columns and not pd.isna(df["close"].iloc[-1])) else None
            return _make_not_evaluable(ticker, name, trade_date=latest_date_str, close=close_val)

    # 과거 시계열 상태 추적 (Baseline과 Challenger 각각 독립적인 prev_score 추적)
    prev_score_b = 0.0
    prev_score_c = 0.0
    prev_ma20 = float("nan")
    prev_macd_diff = float("nan")
    prev_close = float("nan")

    n_rows = len(df)

    # 0부터 N-2번째 행까지 상태 업데이트
    for i in range(n_rows - 1):
        row = df.iloc[i].copy()
        row["prev_close"] = prev_close

        if any(pd.isna(row.get(c)) for c in REQUIRED_COLUMNS):
            prev_close = row.get("close", float("nan"))
            if not pd.isna(row.get("ma20")):
                prev_ma20 = row["ma20"]
            if not pd.isna(row.get("macd")) and not pd.isna(row.get("macd_signal")):
                prev_macd_diff = row["macd"] - row["macd_signal"]
            prev_score_b = 0.0
            prev_score_c = 0.0
            continue

        raw_b = compute_raw_score(row, prev_ma20, prev_macd_diff)
        score_b = raw_to_score(raw_b)

        raw_c = compute_raw_score_v2(row, prev_ma20, prev_macd_diff)
        score_c = raw_to_score(raw_c)

        prev_close = row["close"]
        prev_ma20 = row["ma20"]
        prev_macd_diff = row["macd"] - row["macd_signal"]
        prev_score_b = score_b
        prev_score_c = score_c

    # N-1번째 행 (최신 거래일) 평가
    latest_row = df.iloc[-1].copy()
    latest_row["prev_close"] = prev_close

    trade_date = ""
    if "date" in df.columns and not pd.isna(latest_row["date"]):
        d_val = latest_row["date"]
        trade_date = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)[:10]

    close_val = float(latest_row["close"]) if not pd.isna(latest_row["close"]) else None

    # 최신 거래일 필수 지표 NaN 체크
    if any(pd.isna(latest_row.get(c)) for c in REQUIRED_COLUMNS) or close_val is None:
        return _make_not_evaluable(ticker, name, trade_date=trade_date, close=close_val)

    # 1. Baseline 평가
    raw_b = compute_raw_score(latest_row, prev_ma20, prev_macd_diff)
    score_b = raw_to_score(raw_b)
    sig_type_b = classify_signal(score_b)
    if is_overheated(latest_row):
        sig_type_b = "OVERHEATED"
    sig_present_b = (
        prev_score_b < config.SIGNAL_PREV_THRESHOLD
        and score_b >= config.SIGNAL_THRESHOLD
    )

    # 2. Challenger 평가
    vol_pen, pre_pen, rsi_pen = compute_v2_penalties(latest_row)
    tot_pen = vol_pen + pre_pen + rsi_pen
    raw_c = max(raw_b - tot_pen, 0)
    score_c = raw_to_score(raw_c)
    sig_type_c = classify_signal(score_c)
    if is_overheated(latest_row):
        sig_type_c = "OVERHEATED"
    sig_present_c = (
        prev_score_c < config.SIGNAL_PREV_THRESHOLD
        and score_c >= config.SIGNAL_THRESHOLD
    )

    # 3. comparison_group 분류
    if sig_present_b and sig_present_c:
        comparison_group = "BOTH_YES"
    elif sig_present_b and not sig_present_c:
        comparison_group = "BASELINE_ONLY"
    elif not sig_present_b and sig_present_c:
        comparison_group = "CHALLENGER_ONLY"
    else:
        comparison_group = "BOTH_NO"

    return DualShadowEvaluation(
        trade_date=trade_date,
        ticker=ticker,
        name=name,
        close=close_val,
        baseline=BaselineEvaluation(
            raw_score=raw_b,
            score=score_b,
            signal_type=sig_type_b,
            signal_present=sig_present_b,
        ),
        challenger=ChallengerEvaluation(
            raw_score=raw_c,
            score=score_c,
            signal_type=sig_type_c,
            signal_present=sig_present_c,
            volume_penalty=vol_pen,
            pre_return_penalty=pre_pen,
            rsi_penalty=rsi_pen,
            total_penalty=tot_pen,
        ),
        comparison_group=comparison_group,
        evaluation_status="OK",
    )

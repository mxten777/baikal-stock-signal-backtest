"""
STEP 14 — 통합 백테스트 실행 스크립트

기존 Signal(STEP 1~9)과 Stock Selection v0.1(STEP 13) 점수를 결합하여
전략 A/B/C/D의 성과를 비교한다.

전략:
  A. ALL_SIGNAL         : 기존 Valid Signal 전체
  B. SELECTION_MID      : Stock Selection Score 그룹 MID
  C. SELECTION_HIGH     : Stock Selection Score 그룹 HIGH
  D. SELECTION_MID_HIGH : MID + HIGH

주의:
- 입력은 output/step13_stock_selection_score.csv (STEP 13 산출물)를 그대로 사용한다.
- STEP 13 점수/가중치/그룹 기준을 그대로 사용하며, 새 필터/튜닝은 하지 않는다.
- 기존 Signal/수급/실적/backtest/benchmark 로직은 변경하지 않는다.
- STEP 15 채택 여부 판단은 이 스크립트의 범위가 아니다.

실행: python -m scripts.step14_integrated_backtest
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.integrated_backtest import (
    STRATEGIES,
    build_strategies,
    build_strategy_table,
    build_ticker_table,
    build_yearly_table,
    ticker_concentration,
    year_concentration,
)

SCORED_PATH = ROOT / "output" / "step13_stock_selection_score.csv"
OUT_STRATEGY = ROOT / "output" / "step14_strategy_performance.csv"
OUT_TICKER = ROOT / "output" / "step14_ticker_performance.csv"
OUT_YEARLY = ROOT / "output" / "step14_yearly_performance.csv"
OUT_CONCENTRATION = ROOT / "output" / "step14_concentration_check.csv"


def load_scored() -> pd.DataFrame:
    df = pd.read_csv(SCORED_PATH, parse_dates=["signal_date"], dtype={"ticker": str})
    df["ticker"] = df["ticker"].str.zfill(6)
    return df


def _fmt(val, width: int, decimals: int = 2) -> str:
    if isinstance(val, float) and np.isnan(val):
        return f"{'N/A':>{width}}"
    return f"{val:>{width}.{decimals}f}"


def print_strategy_table(df: pd.DataFrame) -> None:
    hdr = (
        f"\n{'Strategy':<20} {'N':>5} "
        f"{'R5D':>6} {'R10D':>6} {'R20D':>6} "
        f"{'W5D%':>6} {'W10D%':>6} {'W20D%':>6} "
        f"{'XS20D':>7} {'MDD20D':>7} {'WorstDD':>8}"
    )
    sep = "=" * 100
    print(sep)
    print("STEP 14 — 통합 백테스트: 전략 A/B/C/D 성과")
    print(sep)
    print(hdr)
    print("-" * 100)
    for _, row in df.iterrows():
        print(
            f"{row['strategy']:<20} {int(row['signal_count']):>5} "
            f"{_fmt(row['avg_return_5d'], 6)} "
            f"{_fmt(row['avg_return_10d'], 6)} "
            f"{_fmt(row['avg_return_20d'], 6)} "
            f"{_fmt(row['win_rate_5d'], 6, 1)} "
            f"{_fmt(row['win_rate_10d'], 6, 1)} "
            f"{_fmt(row['win_rate_20d'], 6, 1)} "
            f"{_fmt(row['avg_excess_return_20d'], 7)} "
            f"{_fmt(row['avg_max_drawdown_20d'], 7)} "
            f"{_fmt(row['worst_max_drawdown_20d'], 8)}"
        )
    print(sep)
    print("R=Avg Return(%), W=Win Rate(%), XS20D=Avg Excess Return 20D(%), MDD20D=Avg Max Drawdown 20D(%)")
    print()


def run() -> pd.DataFrame:
    print("[STEP 14] 통합 백테스트 (Signal + Stock Selection v0.1)")
    print()

    print("[1] step13_stock_selection_score.csv 로드 ...")
    if not SCORED_PATH.exists():
        print(f"[ERROR] {SCORED_PATH} 없음. 먼저 python -m scripts.step13_stock_selection 을 실행하세요.")
        sys.exit(1)
    scored = load_scored()
    print(f"    → {len(scored)}건, {scored['ticker'].nunique()}종목, "
          f"기간 {scored['signal_date'].min().date()} ~ {scored['signal_date'].max().date()}")

    print("[2] 전략 A/B/C/D 성과 계산 ...")
    strategy_table = build_strategy_table(scored)
    print_strategy_table(strategy_table)

    print("[3] 종목별 성과 계산 ...")
    ticker_rows = []
    for strat in STRATEGIES:
        t = build_ticker_table(scored, strat)
        if not t.empty:
            t.insert(0, "strategy", strat)
            ticker_rows.append(t)
    ticker_table = pd.concat(ticker_rows, ignore_index=True) if ticker_rows else pd.DataFrame()

    print("[4] 연도별 성과 계산 ...")
    yearly_rows = []
    for strat in STRATEGIES:
        y = build_yearly_table(scored, strat)
        if not y.empty:
            y.insert(0, "strategy", strat)
            yearly_rows.append(y)
    yearly_table = pd.concat(yearly_rows, ignore_index=True) if yearly_rows else pd.DataFrame()

    print("[5] 종목/연도 의존도 확인 ...")
    concentration_rows = []
    for strat in STRATEGIES:
        tc = ticker_concentration(scored, strat)
        yc = year_concentration(scored, strat)
        concentration_rows.append({"strategy": strat, **tc, **yc})
    concentration_table = pd.DataFrame(concentration_rows)
    print(concentration_table.to_string(index=False))
    print()

    OUT_STRATEGY.parent.mkdir(parents=True, exist_ok=True)
    strategy_table.to_csv(OUT_STRATEGY, index=False, encoding="utf-8-sig")
    ticker_table.to_csv(OUT_TICKER, index=False, encoding="utf-8-sig")
    yearly_table.to_csv(OUT_YEARLY, index=False, encoding="utf-8-sig")
    concentration_table.to_csv(OUT_CONCENTRATION, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUT_STRATEGY.name}, {OUT_TICKER.name}, {OUT_YEARLY.name}, {OUT_CONCENTRATION.name}")

    return strategy_table


if __name__ == "__main__":
    run()

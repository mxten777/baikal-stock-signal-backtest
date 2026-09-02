"""
STEP 13-A — Stock Selection 점수 검증 (읽기 전용 분석)

STEP 13에서 산출한 output/step13_stock_selection_score.csv 를 그대로 사용해
LOW/MID/HIGH 그룹의 점수 구성과 HIGH 그룹 저성과 원인을 진단한다.

점수/가중치/기준 변경 없음. 새 필터 추가 없음. 기존 코드 수정 없음 (읽기 전용 분석 스크립트).

실행: python -m scripts.step13a_stock_selection_validation
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

SCORED_PATH = ROOT / "output" / "step13_stock_selection_score.csv"


def _avg(series: pd.Series) -> float:
    s = series.dropna()
    return round(float(s.mean()), 2) if len(s) else float("nan")


def _win_rate(series: pd.Series) -> float:
    s = series.dropna()
    return round(float((s > 0).sum() / len(s) * 100), 1) if len(s) else float("nan")


def load_scored() -> pd.DataFrame:
    if not SCORED_PATH.exists():
        print(f"[ERROR] {SCORED_PATH} 없음. 먼저 python -m scripts.step13_stock_selection 을 실행하세요.")
        sys.exit(1)
    return pd.read_csv(SCORED_PATH, dtype={"ticker": str})


# ─────────────────────────────────────────────────────────────────────────────
# 1. 그룹별 점수 구성 평균
# ─────────────────────────────────────────────────────────────────────────────
def print_section1_score_components(df: pd.DataFrame) -> None:
    print("=" * 90)
    print("[1] LOW/MID/HIGH 그룹별 점수 구성 평균")
    print("=" * 90)
    rows = []
    for group in ["LOW", "MID", "HIGH"]:
        g = df[df["score_group"] == group]
        rows.append({
            "group": group,
            "N": len(g),
            "avg_signal_score": _avg(g["score"]),
            "avg_investor_score": _avg(g["investor_score"]),
            "avg_growth_score": _avg(g["growth_score"]),
            "avg_selection_score": _avg(g["stock_selection_score"]),
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 2. HIGH 그룹 저성과 원인: 종목별 / Signal score 구간별 분포
# ─────────────────────────────────────────────────────────────────────────────
def print_section2_high_group_breakdown(df: pd.DataFrame) -> None:
    print("=" * 90)
    print("[2] HIGH 그룹 — 종목별 분포 (20D 수익률/초과수익 기여도)")
    print("=" * 90)
    high = df[df["score_group"] == "HIGH"]
    rows = []
    for ticker, g in high.groupby("ticker"):
        rows.append({
            "ticker": ticker,
            "name": g["name"].iloc[0],
            "N": len(g),
            "avg_return_20d": _avg(g["return_20d"]),
            "avg_excess_20d": _avg(g["excess_return_20d"]),
            "win_rate_20d": _win_rate(g["return_20d"]),
        })
    tbl = pd.DataFrame(rows).sort_values("avg_return_20d")
    print(tbl.to_string(index=False))
    print()

    print("-" * 90)
    print("[2b] HIGH 그룹 — 기존 Signal score 구간별 분포")
    print("-" * 90)
    bins = [0, 75, 80, 85, 90, 100]
    labels = ["<75", "75-80", "80-85", "85-90", "90+"]
    high = high.copy()
    high["signal_score_bucket"] = pd.cut(high["score"], bins=bins, labels=labels, right=False)
    rows = []
    for bucket, g in high.groupby("signal_score_bucket", observed=True):
        rows.append({
            "signal_score_bucket": bucket,
            "N": len(g),
            "avg_return_20d": _avg(g["return_20d"]),
            "avg_excess_20d": _avg(g["excess_return_20d"]),
            "win_rate_20d": _win_rate(g["return_20d"]),
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Selection score decile 분포
# ─────────────────────────────────────────────────────────────────────────────
def print_section3_deciles(df: pd.DataFrame) -> None:
    print("=" * 90)
    print("[3] Stock Selection Score Decile 분포")
    print("=" * 90)
    ranks = df["stock_selection_score"].rank(pct=True, method="first")
    decile = np.minimum((ranks * 10).apply(np.ceil), 10).astype(int)
    df = df.copy()
    df["decile"] = decile
    rows = []
    for d in range(1, 11):
        g = df[df["decile"] == d]
        rows.append({
            "decile": d,
            "score_range": f"{g['stock_selection_score'].min():.1f}-{g['stock_selection_score'].max():.1f}" if len(g) else "N/A",
            "N": len(g),
            "avg_return_20d": _avg(g["return_20d"]),
            "win_rate_20d": _win_rate(g["return_20d"]),
            "avg_excess_20d": _avg(g["excess_return_20d"]),
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 4. 수급 실제 매칭 vs 중립값 그룹 비교
# ─────────────────────────────────────────────────────────────────────────────
def print_section4_investor_matched_vs_neutral(df: pd.DataFrame) -> None:
    print("=" * 90)
    print("[4] 외국인 수급 실제 매칭 vs 중립값(50점) 그룹 비교")
    print("=" * 90)
    matched = df[df["foreign_5d_ratio"].notna()]
    neutral = df[df["foreign_5d_ratio"].isna()]
    rows = []
    for label, g in [("MATCHED (실제 수급 데이터)", matched), ("NEUTRAL (수급 데이터 없음, 50점)", neutral)]:
        rows.append({
            "group": label,
            "N": len(g),
            "avg_signal_score": _avg(g["score"]),
            "avg_selection_score": _avg(g["stock_selection_score"]),
            "avg_return_20d": _avg(g["return_20d"]),
            "win_rate_20d": _win_rate(g["return_20d"]),
            "avg_excess_20d": _avg(g["excess_return_20d"]),
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 5. HIGH 그룹 성과를 왜곡하는 개별 종목 확인
# ─────────────────────────────────────────────────────────────────────────────
def print_section5_distortion_check(df: pd.DataFrame) -> None:
    print("=" * 90)
    print("[5] HIGH 그룹 성과 왜곡 종목 확인 (해당 종목 제외 시 HIGH 평균 변화)")
    print("=" * 90)
    high = df[df["score_group"] == "HIGH"]
    baseline_ret = _avg(high["return_20d"])
    baseline_excess = _avg(high["excess_return_20d"])
    print(f"HIGH 전체: N={len(high)}, avg_return_20d={baseline_ret}, avg_excess_20d={baseline_excess}")
    print()

    rows = []
    for ticker in high["ticker"].unique():
        excl = high[high["ticker"] != ticker]
        rows.append({
            "excluded_ticker": ticker,
            "name": high[high["ticker"] == ticker]["name"].iloc[0],
            "N_removed": len(high) - len(excl),
            "avg_return_20d_without": _avg(excl["return_20d"]),
            "delta_return_20d": round(_avg(excl["return_20d"]) - baseline_ret, 2) if len(excl) else float("nan"),
            "avg_excess_20d_without": _avg(excl["excess_return_20d"]),
            "delta_excess_20d": round(_avg(excl["excess_return_20d"]) - baseline_excess, 2) if len(excl) else float("nan"),
        })
    tbl = pd.DataFrame(rows).sort_values("delta_return_20d", ascending=False)
    print(tbl.to_string(index=False))
    print()


def run() -> None:
    print("[STEP 13-A] Stock Selection 점수 검증 (읽기 전용)")
    print()
    df = load_scored()

    print_section1_score_components(df)
    print_section2_high_group_breakdown(df)
    print_section3_deciles(df)
    print_section4_investor_matched_vs_neutral(df)
    print_section5_distortion_check(df)


if __name__ == "__main__":
    run()

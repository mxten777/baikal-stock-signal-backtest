"""
STEP 12 — 실적 성장 × Signal 성과 분석

분기 YoY 성장 조건별 Signal 성과를 비교한다.
Look-ahead Bias 방지: disclosure_date < signal_date 인 최신 분기 실적만 사용.

실행: python -m scripts.step12_fundamental_signal_performance

분석 대상: data/fundamentals/ 에 있는 모든 종목의 분기 실적
          (현재 005930·000660·035720, 나머지 종목 실적 없어 제외)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.dart_fundamental_provider import (
    compute_net_income_yoy,
    join_signals_step12,
)

FUNDAMENTAL_DIR = ROOT / "data" / "fundamentals"
SIGNALS_PATH = ROOT / "output" / "signals.csv"
OUTPUT_PATH = ROOT / "output" / "step12_growth_signal_performance.csv"


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_all_fundamentals() -> pd.DataFrame:
    """모든 종목 fundamentals CSV를 로드하고 net_income_yoy를 추가한다."""
    dfs = []
    for f in sorted(FUNDAMENTAL_DIR.glob("*_fundamentals.csv")):
        df = pd.read_csv(f, parse_dates=["disclosure_date"], dtype={"ticker": str})
        df["disclosure_date"] = pd.to_datetime(df["disclosure_date"])
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    return compute_net_income_yoy(combined)


# ─────────────────────────────────────────────────────────────────────────────
# 그룹 조건 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def oi_positive_growth(df: pd.DataFrame) -> pd.Series:
    """OI YoY > 0 조건 마스크.

    - flag='normal' 이고 oi_yoy > 0: 일반 정 성장
    - flag='turnaround': 전년 음수 → 당기 양수 (흑자전환)
    """
    normal_pos = (df["oi_yoy_flag"] == "normal") & (df["operating_income_yoy"] > 0)
    turnaround = df["oi_yoy_flag"] == "turnaround"
    return normal_pos | turnaround


def ni_positive_growth(df: pd.DataFrame) -> pd.Series:
    """Net Income YoY > 0 조건 마스크."""
    normal_pos = (df["ni_yoy_flag"] == "normal") & (df["net_income_yoy"] > 0)
    turnaround = df["ni_yoy_flag"] == "turnaround"
    return normal_pos | turnaround


def strong_growth(df: pd.DataFrame) -> pd.Series:
    """강한 성장 조건: Revenue YoY > 10% AND OI YoY > 10% (또는 흑자전환) AND NI YoY > 0."""
    rev_strong = df["revenue_yoy"].notna() & (df["revenue_yoy"] > 10.0)
    oi_strong = (
        ((df["oi_yoy_flag"] == "normal") & (df["operating_income_yoy"] > 10.0))
        | (df["oi_yoy_flag"] == "turnaround")
    )
    return rev_strong & oi_strong & ni_positive_growth(df)


# ─────────────────────────────────────────────────────────────────────────────
# 성과 지표 계산
# ─────────────────────────────────────────────────────────────────────────────

def _avg(series: pd.Series) -> float:
    s = series.dropna()
    return round(float(s.mean()), 2) if len(s) else float("nan")


def _win_rate(series: pd.Series) -> float:
    s = series.dropna()
    return round(float((s > 0).sum() / len(s) * 100), 1) if len(s) else float("nan")


def group_metrics(df: pd.DataFrame) -> dict:
    """그룹 성과 지표를 계산한다."""
    n = len(df)
    if n == 0:
        nan = float("nan")
        return {
            "valid_signal_count": 0,
            "avg_return_5d": nan, "avg_return_10d": nan, "avg_return_20d": nan,
            "win_rate_5d": nan, "win_rate_10d": nan, "win_rate_20d": nan,
            "avg_excess_20d": nan, "avg_max_dd_20d": nan, "worst_max_dd_20d": nan,
        }

    has_excess = "excess_return_20d" in df.columns
    dd = df["max_drawdown_20d"].dropna()
    return {
        "valid_signal_count": n,
        "avg_return_5d": _avg(df["return_5d"]),
        "avg_return_10d": _avg(df["return_10d"]),
        "avg_return_20d": _avg(df["return_20d"]),
        "win_rate_5d": _win_rate(df["return_5d"]),
        "win_rate_10d": _win_rate(df["return_10d"]),
        "win_rate_20d": _win_rate(df["return_20d"]),
        "avg_excess_20d": _avg(df["excess_return_20d"]) if has_excess else float("nan"),
        "avg_max_dd_20d": _avg(df["max_drawdown_20d"]),
        "worst_max_dd_20d": round(float(dd.min()), 2) if len(dd) else float("nan"),
    }


def build_group_table(joined: pd.DataFrame) -> pd.DataFrame:
    """5개 그룹별 성과 테이블을 반환한다."""
    has_fund = joined["fundamental_report_period"].notna()
    valid_mask = joined["signal_type"] != "OVERHEATED" if "signal_type" in joined.columns else pd.Series(True, index=joined.index)
    base = joined[has_fund & valid_mask].copy()

    rev_pos = base["revenue_yoy"].notna() & (base["revenue_yoy"] > 0)

    groups = [
        ("G0: All Signals",                     base),
        ("G1: Revenue YoY > 0%",                base[rev_pos]),
        ("G2: OI YoY > 0%",                     base[oi_positive_growth(base)]),
        ("G3: Revenue YoY > 0% & OI YoY > 0%",  base[rev_pos & oi_positive_growth(base)]),
        ("G4: Strong Growth (Rev>10%, OI>10%, NI+)", base[strong_growth(base)]),
    ]

    rows = []
    for name, gdf in groups:
        rows.append({"group": name, **group_metrics(gdf)})

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
# 콘솔 출력
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val, width: int, decimals: int = 2) -> str:
    if isinstance(val, float) and np.isnan(val):
        return f"{'N/A':>{width}}"
    return f"{val:>{width}.{decimals}f}"


def print_table(df: pd.DataFrame) -> None:
    hdr = (
        f"\n{'Group':<48} {'ValidN':>6} "
        f"{'R5D':>6} {'R10D':>6} {'R20D':>6} "
        f"{'W5D%':>6} {'W10D%':>6} {'W20D%':>6} "
        f"{'XS20D':>7} {'MDD20D':>7} {'WorstDD':>8}"
    )
    sep = "=" * 116
    print(sep)
    print("STEP 12 — 실적 성장 × Signal 성과 분석")
    print(sep)
    print(hdr)
    print("-" * 116)
    for _, row in df.iterrows():
        print(
            f"{row['group']:<48} {int(row['valid_signal_count']):>6} "
            f"{_fmt(row['avg_return_5d'], 6)} "
            f"{_fmt(row['avg_return_10d'], 6)} "
            f"{_fmt(row['avg_return_20d'], 6)} "
            f"{_fmt(row['win_rate_5d'], 6, 1)} "
            f"{_fmt(row['win_rate_10d'], 6, 1)} "
            f"{_fmt(row['win_rate_20d'], 6, 1)} "
            f"{_fmt(row['avg_excess_20d'], 7)} "
            f"{_fmt(row['avg_max_dd_20d'], 7)} "
            f"{_fmt(row['worst_max_dd_20d'], 8)}"
        )
    print(sep)
    print("R=Avg Return(%), W=Win Rate(%), XS20D=Avg Excess Return 20D(%), MDD20D=Avg Max Drawdown 20D(%)")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────

def run() -> pd.DataFrame:
    print("[STEP 12] 실적 성장 × Signal 성과 분석")
    print()

    print("[1] signals.csv 로드 ...")
    if not SIGNALS_PATH.exists():
        print(f"[ERROR] {SIGNALS_PATH} 없음. 먼저 python -m src.main 을 실행하세요.")
        sys.exit(1)
    signals = pd.read_csv(SIGNALS_PATH, parse_dates=["signal_date"], dtype={"ticker": str})
    print(f"    → {len(signals)}건 signal, {signals['ticker'].nunique()}종목")

    print("[2] fundamentals 로드 + net_income_yoy 계산 ...")
    fundamentals = load_all_fundamentals()
    if fundamentals.empty:
        print(f"[ERROR] {FUNDAMENTAL_DIR} 에 *_fundamentals.csv 파일 없음.")
        sys.exit(1)
    tickers_fund = sorted(fundamentals["ticker"].unique())
    print(f"    → {len(tickers_fund)}종목: {', '.join(tickers_fund)}")
    print(f"    → {len(fundamentals)}분기 레코드")

    print("[3] Signal × Fundamental join (look-ahead bias 방지) ...")
    joined = join_signals_step12(signals, fundamentals)
    has_fund_mask = joined["fundamental_report_period"].notna()
    n_matched = int(has_fund_mask.sum())
    print(f"    → fundamental 매칭 signal: {n_matched}건 / {len(joined)}건")

    print("[4] 그룹별 성과 계산 ...")
    result = build_group_table(joined)

    print_table(result)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_PATH}")

    return result


if __name__ == "__main__":
    run()

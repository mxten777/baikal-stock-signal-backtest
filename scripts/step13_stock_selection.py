"""
STEP 13 — Stock Selection Score 분석

기존 Signal(signals.csv) 후보들에 Stock Selection Score를 부여하고,
점수 상/중/하 그룹별 성과를 비교하여 기존 전체 Signal 대비 개선 여부를 확인한다.

점수 구성:
  1. Signal 강도 (기존 score, 0~100)               weight 60%
  2. 외국인 수급 상태 (foreign 5D / avg volume 20D)  weight 25%
  3. 실적 성장 정보 (STEP 12 성장 그룹, 보조 점수)     weight 15%

주의:
- 기존 Signal 생성/backtest/benchmark 로직 변경 없음.
- 실적 성장은 Hard Filter로 사용하지 않음 (탈락 없음, 가중 점수만 반영).
- Look-ahead bias 방지: 수급은 signal_date 이전 데이터만, 실적은 STEP 12와 동일하게
  disclosure_date < signal_date 조건으로 join.
- STEP 14 통합 백테스트는 아직 수행하지 않음 (이 STEP은 분석/점수 산출까지).

실행: python -m scripts.step13_stock_selection
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.dart_fundamental_provider import (
    compute_net_income_yoy,
    join_signals_step12,
)
from src.stock_selection import add_stock_selection_score, classify_score_group

SIGNALS_PATH = ROOT / "output" / "signals.csv"
INVESTOR_DIR = ROOT / "data" / "investor"
RAW_DIR = ROOT / "data" / "raw"
FUNDAMENTAL_DIR = ROOT / "data" / "fundamentals"
OUTPUT_PATH = ROOT / "output" / "step13_stock_selection_score.csv"


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────────────────────
def load_signals() -> pd.DataFrame:
    df = pd.read_csv(SIGNALS_PATH, parse_dates=["signal_date"], dtype={"ticker": str})
    df["ticker"] = df["ticker"].str.zfill(6)
    return df


def load_investor_map() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for path in sorted(INVESTOR_DIR.glob("*_investor.csv")):
        ticker = path.stem.replace("_investor", "")
        df = pd.read_csv(path, parse_dates=["date"])
        df["ticker"] = df["ticker"].astype(str).str.zfill(6)
        result[ticker] = df.sort_values("date").reset_index(drop=True)
    return result


def load_raw_map() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for path in sorted(RAW_DIR.glob("*.csv")):
        ticker = path.stem
        df = pd.read_csv(path, parse_dates=["date"])
        result[ticker] = df.sort_values("date").reset_index(drop=True)
    return result


def load_all_fundamentals() -> pd.DataFrame:
    dfs = []
    for f in sorted(FUNDAMENTAL_DIR.glob("*_fundamentals.csv")):
        df = pd.read_csv(f, parse_dates=["disclosure_date"], dtype={"ticker": str})
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    return compute_net_income_yoy(pd.concat(dfs, ignore_index=True))


# ─────────────────────────────────────────────────────────────────────────────
# 그룹 성과 지표 (STEP 12 스타일)
# ─────────────────────────────────────────────────────────────────────────────
def _avg(series: pd.Series) -> float:
    s = series.dropna()
    return round(float(s.mean()), 2) if len(s) else float("nan")


def _win_rate(series: pd.Series) -> float:
    s = series.dropna()
    return round(float((s > 0).sum() / len(s) * 100), 1) if len(s) else float("nan")


def group_metrics(df: pd.DataFrame) -> dict:
    n = len(df)
    if n == 0:
        nan = float("nan")
        return {
            "signal_count": 0,
            "avg_return_5d": nan, "avg_return_10d": nan, "avg_return_20d": nan,
            "win_rate_5d": nan, "win_rate_10d": nan, "win_rate_20d": nan,
            "avg_excess_return_20d": nan, "avg_max_drawdown_20d": nan, "worst_max_drawdown_20d": nan,
        }
    dd = df["max_drawdown_20d"].dropna()
    return {
        "signal_count": n,
        "avg_return_5d": _avg(df["return_5d"]),
        "avg_return_10d": _avg(df["return_10d"]),
        "avg_return_20d": _avg(df["return_20d"]),
        "win_rate_5d": _win_rate(df["return_5d"]),
        "win_rate_10d": _win_rate(df["return_10d"]),
        "win_rate_20d": _win_rate(df["return_20d"]),
        "avg_excess_return_20d": _avg(df["excess_return_20d"]) if "excess_return_20d" in df.columns else float("nan"),
        "avg_max_drawdown_20d": _avg(df["max_drawdown_20d"]),
        "worst_max_drawdown_20d": round(float(dd.min()), 2) if len(dd) else float("nan"),
    }


def build_group_table(scored: pd.DataFrame) -> pd.DataFrame:
    """ALL / LOW / MID / HIGH 그룹별 성과 테이블."""
    groups = [
        ("ALL (전체 Signal)", scored),
        ("LOW",  scored[scored["score_group"] == "LOW"]),
        ("MID",  scored[scored["score_group"] == "MID"]),
        ("HIGH", scored[scored["score_group"] == "HIGH"]),
    ]
    rows = [{"group": name, **group_metrics(gdf)} for name, gdf in groups]
    return pd.DataFrame(rows)


def _fmt(val, width: int, decimals: int = 2) -> str:
    if isinstance(val, float) and np.isnan(val):
        return f"{'N/A':>{width}}"
    return f"{val:>{width}.{decimals}f}"


def print_table(df: pd.DataFrame) -> None:
    hdr = (
        f"\n{'Group':<22} {'N':>5} "
        f"{'R5D':>6} {'R10D':>6} {'R20D':>6} "
        f"{'W5D%':>6} {'W10D%':>6} {'W20D%':>6} "
        f"{'XS20D':>7} {'MDD20D':>7} {'WorstDD':>8}"
    )
    sep = "=" * 100
    print(sep)
    print("STEP 13 — Stock Selection Score 그룹별 성과")
    print(sep)
    print(hdr)
    print("-" * 100)
    for _, row in df.iterrows():
        print(
            f"{row['group']:<22} {int(row['signal_count']):>5} "
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


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────
def run() -> pd.DataFrame:
    print("[STEP 13] Stock Selection Score 분석")
    print()

    print("[1] signals.csv 로드 ...")
    if not SIGNALS_PATH.exists():
        print(f"[ERROR] {SIGNALS_PATH} 없음. 먼저 python -m src.main 을 실행하세요.")
        sys.exit(1)
    signals = load_signals()
    # OVERHEATED는 매매 대상이 아니므로 STEP 12와 동일하게 제외
    signals = signals[signals["signal_type"] != "OVERHEATED"].reset_index(drop=True)
    print(f"    → {len(signals)}건 signal (OVERHEATED 제외), {signals['ticker'].nunique()}종목")

    print("[2] 외국인 수급 데이터 로드 ...")
    investor_map = load_investor_map()
    raw_map = load_raw_map()
    print(f"    → 수급 데이터 보유 종목: {sorted(investor_map.keys())}")

    print("[3] 실적 데이터 로드 + net_income_yoy 계산 ...")
    fundamentals = load_all_fundamentals()
    print(f"    → 실적 데이터 보유 종목: {sorted(fundamentals['ticker'].unique()) if not fundamentals.empty else []}")

    print("[4] Signal × 수급 × 실적 join (look-ahead bias 방지) ...")
    from src.stock_selection import compute_foreign_5d_ratio
    signals["foreign_5d_ratio"] = compute_foreign_5d_ratio(signals, investor_map, raw_map)
    joined = join_signals_step12(signals, fundamentals)

    print("[5] Stock Selection Score 계산 ...")
    scored = add_stock_selection_score(joined)
    scored["score_group"] = classify_score_group(scored)

    n_investor_matched = scored["foreign_5d_ratio"].notna().sum()
    n_fund_matched = scored["fundamental_report_period"].notna().sum()
    print(f"    → 수급 매칭: {n_investor_matched}건 / 실적 매칭: {n_fund_matched}건 (전체 {len(scored)}건)")

    print("[6] 그룹별 성과 계산 ...")
    result = build_group_table(scored)
    print_table(result)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {OUTPUT_PATH}")

    return result


if __name__ == "__main__":
    run()

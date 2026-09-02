"""
STEP 1-B — 289 Signal x 20종목 전체 수급 효과 재검증

STEP 1-A에서 확보한 20종목 전체 수급 데이터를,
output/step13_stock_selection_score.csv 에 확정된 289 Valid Signals(20종목)와 결합하여
v0.1(3종목)에서 관찰된 수급 효과가 20종목 확대 시에도 재현되는지 검증한다.

이번 STEP에서는 다음을 절대 변경하지 않는다:
  - Signal 조건 / SIGNAL_THRESHOLD / SIGNAL_PREV_THRESHOLD / MID·HIGH 기준
  - 수급 POSITIVE/NEUTRAL/NEGATIVE 분류 기준
  - Selection Score 및 60/25/15 가중치

분류 기준(POSITIVE/NEUTRAL/NEGATIVE)은 기존 코드(scripts/step9_investor_effect.py
섹션 7, src/stock_selection.py foreign_ratio_to_score)에 이미 정의된
ratio 임계값(+-0.20, 5거래일 누적 순매수 / 20일 평균거래량)을 그대로 재사용한다.
새 기준을 만들지 않는다.

실행: python -m scripts.step1b_flow_verification
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step9_investor_effect import (
    LOW_SAMPLE_THRESHOLD,
    compute_investor_features,
)
from src.config import OUTPUT_DIR, TICKERS

STOCK_SELECTION_PATH = OUTPUT_DIR / "step13_stock_selection_score.csv"
INVESTOR_DIR = ROOT / "data" / "investor"
RAW_DIR = ROOT / "data" / "raw"

EXPECTED_SIGNAL_COUNT = 289
EXPECTED_TICKER_COUNT = 20

# 기존 STEP 9 / stock_selection.py 에서 이미 사용 중인 ratio 임계값 (신규 기준 아님)
FLOW_RATIO_POSITIVE = 0.20
FLOW_RATIO_NEGATIVE = -0.20

# 기존 STEP 9 결과 (3종목, 비교 기준값)
BASELINE_3TICKER = {
    "foreign": {
        "POSITIVE": {"count": 37, "avg_excess_20d": 0.82, "win_rate_20d": 45.9},
        "NEGATIVE": {"count": 5, "avg_excess_20d": -4.42, "win_rate_20d": 20.0},
    },
    "institution": {
        "POSITIVE": {"count": 36, "avg_excess_20d": -0.08, "win_rate_20d": 44.4},
        "NEGATIVE": {"count": 6, "avg_excess_20d": 1.86, "win_rate_20d": 33.3},
    },
}

OUTPUT_PERFORMANCE = OUTPUT_DIR / "v02_step1b_flow_performance.csv"
OUTPUT_BY_STOCK = OUTPUT_DIR / "v02_step1b_flow_by_stock.csv"
OUTPUT_BY_SIGNAL_LEVEL = OUTPUT_DIR / "v02_step1b_flow_by_signal_level.csv"
OUTPUT_MERGE_QUALITY = OUTPUT_DIR / "v02_step1b_flow_merge_quality.csv"


# ─────────────────────────────────────────────
# 1. 데이터 로드
# ─────────────────────────────────────────────
def load_base_signals() -> pd.DataFrame:
    """289 Valid Signals (STEP 13 확정본)를 로드한다."""
    df = pd.read_csv(STOCK_SELECTION_PATH, dtype={"ticker": str}, parse_dates=["signal_date"])
    df["ticker"] = df["ticker"].str.zfill(6)
    keep_cols = [
        "ticker", "name", "signal_date", "score", "signal_type",
        "return_5d", "return_10d", "return_20d",
        "excess_return_5d", "excess_return_10d", "excess_return_20d",
        "score_group", "stock_selection_score",
    ]
    return df[keep_cols].reset_index(drop=True)


def load_investor_map() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for ticker in TICKERS:
        path = INVESTOR_DIR / f"{ticker}_investor.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            df["ticker"] = df["ticker"].astype(str).str.zfill(6)
            result[ticker] = df.sort_values("date").reset_index(drop=True)
    return result


def load_raw_map() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for ticker in TICKERS:
        path = RAW_DIR / f"{ticker}.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            result[ticker] = df.sort_values("date").reset_index(drop=True)
    return result


def verify_baseline(signals: pd.DataFrame) -> None:
    count = len(signals)
    tickers = signals["ticker"].nunique()
    if count != EXPECTED_SIGNAL_COUNT or tickers != EXPECTED_TICKER_COUNT:
        raise RuntimeError(
            f"기준 불일치: Signal Count={count} (기대 {EXPECTED_SIGNAL_COUNT}), "
            f"대상 종목={tickers} (기대 {EXPECTED_TICKER_COUNT}) — 분석 중단"
        )


# ─────────────────────────────────────────────
# 2. 수급 결합 + 결합 품질
# ─────────────────────────────────────────────
def merge_investor_features(
    signals: pd.DataFrame,
    investor_map: dict[str, pd.DataFrame],
    raw_map: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """기존 STEP 9 compute_investor_features 로 1d/3d/5d Feature를 결합한다 (로직 변경 없음)."""
    return compute_investor_features(signals, investor_map, raw_map)


def build_merge_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """결합 품질 요약 테이블과 결합 실패 목록을 반환한다."""
    total = len(df)
    foreign_ok = df["foreign_net_5d"].notna()
    institution_ok = df["institution_net_5d"].notna()
    both_ok = foreign_ok & institution_ok

    summary = pd.DataFrame([{
        "total_signals": total,
        "merged_success": int(both_ok.sum()),
        "merged_failed": int((~both_ok).sum()),
        "merge_rate_pct": round(both_ok.sum() / total * 100, 2) if total else 0.0,
        "foreign_present_pct": round(foreign_ok.sum() / total * 100, 2) if total else 0.0,
        "institution_present_pct": round(institution_ok.sum() / total * 100, 2) if total else 0.0,
    }])

    failed = df.loc[~both_ok, ["ticker", "name", "signal_date"]].copy()
    failed["reason"] = np.where(
        ~foreign_ok.loc[failed.index] & ~institution_ok.loc[failed.index],
        "foreign/institution 데이터 없음 (investor 파일 미존재 또는 signal_date 이전 데이터 없음)",
        np.where(~foreign_ok.loc[failed.index], "foreign 데이터 없음", "institution 데이터 없음"),
    )
    return summary, failed


# ─────────────────────────────────────────────
# 3. POSITIVE / NEUTRAL / NEGATIVE 분류 (기존 ratio 임계값 재사용)
# ─────────────────────────────────────────────
def classify_flow(ratio: float) -> str:
    """기존 STEP 9 섹션 7 / stock_selection.foreign_ratio_to_score 의 +-0.20 임계값을 그대로 사용."""
    if pd.isna(ratio):
        return "NO_DATA"
    if ratio >= FLOW_RATIO_POSITIVE:
        return "POSITIVE"
    if ratio <= FLOW_RATIO_NEGATIVE:
        return "NEGATIVE"
    return "NEUTRAL"


def add_flow_classification(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["foreign_flow_class"] = result["foreign_5d_ratio"].apply(classify_flow)
    result["institution_flow_class"] = result["institution_5d_ratio"].apply(classify_flow)
    return result


# ─────────────────────────────────────────────
# 4. 그룹 성과 통계
# ─────────────────────────────────────────────
def _low_sample_flag(n: int) -> str:
    return " [LOW SAMPLE]" if n < LOW_SAMPLE_THRESHOLD else ""


def group_stats(label: str, group_df: pd.DataFrame) -> dict:
    n = len(group_df)
    flag = _low_sample_flag(n)

    def _avg(col: str) -> float:
        s = group_df[col].dropna()
        return round(float(s.mean()), 2) if len(s) else float("nan")

    def _win(col: str) -> float:
        s = group_df[col].dropna()
        return round(float((s > 0).mean() * 100), 1) if len(s) else float("nan")

    return {
        "Group": f"{label}{flag}",
        "Signal Count": n,
        "Avg Return 5D": _avg("return_5d"),
        "Avg Return 10D": _avg("return_10d"),
        "Avg Return 20D": _avg("return_20d"),
        "Win Rate 5D (%)": _win("return_5d"),
        "Win Rate 10D (%)": _win("return_10d"),
        "Win Rate 20D (%)": _win("return_20d"),
        "Avg Excess 5D": _avg("excess_return_5d"),
        "Avg Excess 10D": _avg("excess_return_10d"),
        "Avg Excess 20D": _avg("excess_return_20d"),
    }


def build_flow_performance_table(df: pd.DataFrame) -> pd.DataFrame:
    """Foreign / Institution POSITIVE/NEUTRAL/NEGATIVE 그룹별 성과 + POS-NEG 차이."""
    rows: list[dict] = []
    for factor, class_col in [("Foreign", "foreign_flow_class"), ("Institution", "institution_flow_class")]:
        for label in ("POSITIVE", "NEUTRAL", "NEGATIVE"):
            sub = df[df[class_col] == label]
            row = group_stats(f"{factor} {label}", sub)
            row["Factor"] = factor
            rows.append(row)

        pos = df[df[class_col] == "POSITIVE"]
        neg = df[df[class_col] == "NEGATIVE"]
        diff_row = {
            "Group": f"{factor} POSITIVE - NEGATIVE",
            "Factor": factor,
            "Signal Count": np.nan,
            "Avg Return 5D": np.nan,
            "Avg Return 10D": np.nan,
            "Avg Return 20D": round(_safe_mean(pos, "return_20d") - _safe_mean(neg, "return_20d"), 2),
            "Win Rate 5D (%)": np.nan,
            "Win Rate 10D (%)": np.nan,
            "Win Rate 20D (%)": round(_safe_winrate(pos, "return_20d") - _safe_winrate(neg, "return_20d"), 1),
            "Avg Excess 5D": np.nan,
            "Avg Excess 10D": np.nan,
            "Avg Excess 20D": round(_safe_mean(pos, "excess_return_20d") - _safe_mean(neg, "excess_return_20d"), 2),
        }
        rows.append(diff_row)

    cols = [
        "Factor", "Group", "Signal Count",
        "Avg Return 5D", "Avg Return 10D", "Avg Return 20D",
        "Win Rate 5D (%)", "Win Rate 10D (%)", "Win Rate 20D (%)",
        "Avg Excess 5D", "Avg Excess 10D", "Avg Excess 20D",
    ]
    return pd.DataFrame(rows)[cols]


def _safe_mean(sub: pd.DataFrame, col: str) -> float:
    s = sub[col].dropna()
    return float(s.mean()) if len(s) else float("nan")


def _safe_winrate(sub: pd.DataFrame, col: str) -> float:
    s = sub[col].dropna()
    return float((s > 0).mean() * 100) if len(s) else float("nan")


# ─────────────────────────────────────────────
# 5. 종목별 편향 확인
# ─────────────────────────────────────────────
def build_by_stock_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    overall_pos = df[df["foreign_flow_class"] == "POSITIVE"]
    overall_neg = df[df["foreign_flow_class"] == "NEGATIVE"]
    overall_diff = _safe_mean(overall_pos, "excess_return_20d") - _safe_mean(overall_neg, "excess_return_20d")

    for ticker, sub in df.groupby("ticker"):
        name = sub["name"].iloc[0]
        f_counts = sub["foreign_flow_class"].value_counts().to_dict()
        i_counts = sub["institution_flow_class"].value_counts().to_dict()

        excl = df[df["ticker"] != ticker]
        excl_pos = excl[excl["foreign_flow_class"] == "POSITIVE"]
        excl_neg = excl[excl["foreign_flow_class"] == "NEGATIVE"]
        excl_diff = _safe_mean(excl_pos, "excess_return_20d") - _safe_mean(excl_neg, "excess_return_20d")
        sign_flip = (
            not pd.isna(overall_diff) and not pd.isna(excl_diff)
            and (overall_diff > 0) != (excl_diff > 0)
        )

        rows.append({
            "ticker": ticker,
            "name": name,
            "signal_count": len(sub),
            "foreign_positive": int(f_counts.get("POSITIVE", 0)),
            "foreign_neutral": int(f_counts.get("NEUTRAL", 0)),
            "foreign_negative": int(f_counts.get("NEGATIVE", 0)),
            "institution_positive": int(i_counts.get("POSITIVE", 0)),
            "institution_neutral": int(i_counts.get("NEUTRAL", 0)),
            "institution_negative": int(i_counts.get("NEGATIVE", 0)),
            "avg_excess_20d": round(_safe_mean(sub, "excess_return_20d"), 2),
            "overall_foreign_pos_neg_diff_20d_excl_this_ticker": round(excl_diff, 2) if not pd.isna(excl_diff) else np.nan,
            "sign_flips_when_excluded": sign_flip,
        })
    return pd.DataFrame(rows).sort_values("signal_count", ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────
# 6. MID / HIGH x Foreign / Institution 교차
# ─────────────────────────────────────────────
def build_signal_level_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for level in ("MID", "HIGH"):
        level_df = df[df["score_group"] == level]
        for factor, class_col in [("Foreign", "foreign_flow_class"), ("Institution", "institution_flow_class")]:
            for label in ("POSITIVE", "NEUTRAL", "NEGATIVE"):
                sub = level_df[level_df[class_col] == label]
                row = group_stats(f"{level} x {factor} {label}", sub)
                row["Signal Level"] = level
                row["Factor"] = factor
                rows.append(row)
    cols = [
        "Signal Level", "Factor", "Group", "Signal Count",
        "Avg Return 5D", "Avg Return 10D", "Avg Return 20D",
        "Win Rate 5D (%)", "Win Rate 10D (%)", "Win Rate 20D (%)",
        "Avg Excess 5D", "Avg Excess 10D", "Avg Excess 20D",
    ]
    return pd.DataFrame(rows)[cols]


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main() -> None:
    print("=" * 70)
    print("STEP 1-B — 289 Signal x 20종목 전체 수급 효과 재검증")
    print("=" * 70)

    signals = load_base_signals()
    verify_baseline(signals)
    print(f"Signal Count = {len(signals)} (기대 {EXPECTED_SIGNAL_COUNT}) — OK")
    print(f"대상 종목 = {signals['ticker'].nunique()} (기대 {EXPECTED_TICKER_COUNT}) — OK")
    print()

    investor_map = load_investor_map()
    raw_map = load_raw_map()
    merged = merge_investor_features(signals, investor_map, raw_map)

    quality_summary, failed = build_merge_quality(merged)
    print("결합 품질:")
    print(quality_summary.to_string(index=False))
    if len(failed):
        print("\n결합 실패 Signal:")
        print(failed.to_string(index=False))
    print()

    classified = add_flow_classification(merged)

    performance = build_flow_performance_table(classified)
    by_stock = build_by_stock_table(classified)
    by_signal_level = build_signal_level_table(classified)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    quality_summary.to_csv(OUTPUT_MERGE_QUALITY, index=False, encoding="utf-8-sig")
    if len(failed):
        fail_path = OUTPUT_DIR / "v02_step1b_flow_merge_failures.csv"
        failed.to_csv(fail_path, index=False, encoding="utf-8-sig")
        print(f"Wrote {fail_path.relative_to(ROOT)}")
    performance.to_csv(OUTPUT_PERFORMANCE, index=False, encoding="utf-8-sig")
    by_stock.to_csv(OUTPUT_BY_STOCK, index=False, encoding="utf-8-sig")
    by_signal_level.to_csv(OUTPUT_BY_SIGNAL_LEVEL, index=False, encoding="utf-8-sig")

    print("Foreign / Institution 그룹별 성과:")
    print(performance.to_string(index=False))
    print()
    print("종목별 편향 확인:")
    print(by_stock.to_string(index=False))
    print()
    print("MID / HIGH x Foreign / Institution 교차:")
    print(by_signal_level.to_string(index=False))
    print()

    print(f"Wrote {OUTPUT_MERGE_QUALITY.relative_to(ROOT)}")
    print(f"Wrote {OUTPUT_PERFORMANCE.relative_to(ROOT)}")
    print(f"Wrote {OUTPUT_BY_STOCK.relative_to(ROOT)}")
    print(f"Wrote {OUTPUT_BY_SIGNAL_LEVEL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

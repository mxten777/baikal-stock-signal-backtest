"""
STEP 6 — Signal 조합 성과 분석
Signal Score v0.1 변경 없음. output/signals.csv, summary.csv 를 읽어 분석만 수행.
raw CSV 데이터로부터 구성 점수 및 사전 수익률을 재계산한다.
"""

from __future__ import annotations

import sys
import pandas as pd
import numpy as np
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

SIGNALS_CSV = ROOT_DIR / "output" / "signals.csv"
SUMMARY_CSV = ROOT_DIR / "output" / "summary.csv"

from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.indicators import add_all_indicators
from src.signal_engine import score_trend, score_volume, score_momentum


# ── 데이터 로드 ────────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(SIGNALS_CSV, parse_dates=["signal_date"], dtype={"ticker": str})
    summary = pd.read_csv(SUMMARY_CSV, dtype={"ticker": str})
    # Restore zero-padded 6-digit ticker codes
    signals["ticker"] = signals["ticker"].str.zfill(6)
    summary["ticker"] = summary["ticker"].str.zfill(6)
    return signals, summary


def valid_signals(signals: pd.DataFrame) -> pd.DataFrame:
    return signals[signals["signal_type"] != "OVERHEATED"].copy()


# ── Signal 보강: 구성 점수 + 사전 수익률 ──────────────────────────────────────

def _prev_ma20(df_ind: pd.DataFrame, i: int) -> float:
    """i 이전 마지막으로 유효한 MA20 값 반환"""
    for j in range(i - 1, -1, -1):
        v = df_ind.iloc[j]["ma20"]
        if not pd.isna(v):
            return float(v)
    return float("nan")


def _prev_macd_diff(df_ind: pd.DataFrame, i: int) -> float:
    """i 이전 마지막으로 유효한 (macd - macd_signal) 값 반환"""
    for j in range(i - 1, -1, -1):
        m = df_ind.iloc[j]["macd"]
        s = df_ind.iloc[j]["macd_signal"]
        if not pd.isna(m) and not pd.isna(s):
            return float(m - s)
    return float("nan")


def _backward_return(df_ind: pd.DataFrame, idx: int, n: int) -> float | None:
    back_idx = idx - n
    if back_idx < 0:
        return None
    prev_c = float(df_ind.iloc[back_idx]["close"])
    sig_c = float(df_ind.iloc[idx]["close"])
    if prev_c == 0:
        return None
    return round((sig_c / prev_c - 1) * 100, 2)


def _enrich_ticker(
    sig_dates: set[pd.Timestamp],
    df_ind: pd.DataFrame,
) -> dict[pd.Timestamp, dict]:
    df_ind = df_ind.reset_index(drop=True)
    df_ind["date"] = pd.to_datetime(df_ind["date"])
    date_to_idx: dict[pd.Timestamp, int] = {
        row["date"]: idx for idx, row in df_ind.iterrows()
    }

    results: dict[pd.Timestamp, dict] = {}
    for sig_date in sig_dates:
        if sig_date not in date_to_idx:
            continue
        i = date_to_idx[sig_date]
        row = df_ind.iloc[i].copy()

        prev_c = float(df_ind.iloc[i - 1]["close"]) if i > 0 else float("nan")
        row["prev_close"] = prev_c

        pm20 = _prev_ma20(df_ind, i)
        pmd = _prev_macd_diff(df_ind, i)

        results[sig_date] = {
            "trend_score": score_trend(row, pm20),
            "volume_score": score_volume(row),
            "momentum_score": score_momentum(row, pmd),
            "return_3d_before": _backward_return(df_ind, i, 3),
            "return_5d_before": _backward_return(df_ind, i, 5),
            "return_10d_before": _backward_return(df_ind, i, 10),
        }

    return results


def enrich_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """signals에 구성점수 및 사전수익률 컬럼 추가 (원본 수정 없음)"""
    provider = CsvDataProvider(config.DATA_RAW_DIR)
    signals = signals.copy()

    new_cols = [
        "trend_score", "volume_score", "momentum_score",
        "return_3d_before", "return_5d_before", "return_10d_before",
    ]
    for col in new_cols:
        signals[col] = float("nan")

    for ticker in signals["ticker"].unique():
        try:
            df = provider.load(ticker)
        except (FileNotFoundError, ValueError):
            continue

        df_ind = add_all_indicators(df)
        ticker_mask = signals["ticker"] == ticker
        sig_dates = set(pd.to_datetime(signals.loc[ticker_mask, "signal_date"]))

        enrichments = _enrich_ticker(sig_dates, df_ind)

        for sig_date, vals in enrichments.items():
            mask = ticker_mask & (signals["signal_date"] == sig_date)
            for col, val in vals.items():
                signals.loc[mask, col] = val

    return signals


# ── 공통 성과 집계 헬퍼 ────────────────────────────────────────────────────

def _perf_row(label: str, grp: pd.DataFrame) -> dict:
    e5 = grp["excess_return_5d"].dropna()
    e10 = grp["excess_return_10d"].dropna()
    e20 = grp["excess_return_20d"].dropna()
    return {
        "Label": label,
        "Signal Count": len(grp),
        "Avg Excess 5D": round(e5.mean(), 2) if len(e5) > 0 else float("nan"),
        "Avg Excess 10D": round(e10.mean(), 2) if len(e10) > 0 else float("nan"),
        "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
        "Median Excess 20D": round(e20.median(), 2) if len(e20) > 0 else float("nan"),
        "Exc Win Rate 5D": round((e5 > 0).sum() / len(e5) * 100, 1) if len(e5) > 0 else float("nan"),
        "Exc Win Rate 10D": round((e10 > 0).sum() / len(e10) * 100, 1) if len(e10) > 0 else float("nan"),
        "Exc Win Rate 20D": round((e20 > 0).sum() / len(e20) * 100, 1) if len(e20) > 0 else float("nan"),
    }


# ── Section 2: A / B / C 조합 분석 ────────────────────────────────────────

def print_combination_analysis(v: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("2. Signal 조합 성과 분석  (Valid Signal 기준)")
    print("=" * 72)
    print("  A : 80 <= Score < 85")
    print("  B : 55 <= RSI   < 60")
    print("  C : 1.5 <= Volume Ratio < 3.0")
    print()

    cA = (v["score"] >= 80) & (v["score"] < 85)
    cB = (v["rsi"] >= 55) & (v["rsi"] < 60)
    cC = (v["volume_ratio"] >= 1.5) & (v["volume_ratio"] < 3.0)

    combos = [
        ("A", cA),
        ("B", cB),
        ("C", cC),
        ("A + B", cA & cB),
        ("A + C", cA & cC),
        ("B + C", cB & cC),
        ("A + B + C", cA & cB & cC),
    ]

    rows = [_perf_row(label, v[mask]) for label, mask in combos]
    df = pd.DataFrame(rows).set_index("Label")
    print(df.to_string())


# ── Section 3: 반대 조건 비교 ─────────────────────────────────────────────

def print_opposite_analysis(v: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("3. 반대 조건 비교  (Valid Signal 기준, 20D 초과수익 기준)")
    print("=" * 72)

    pairs = [
        ("Score  80~85   (A)", (v["score"] >= 80) & (v["score"] < 85)),
        ("Score  >= 85", v["score"] >= 85),
        ("RSI    55~60   (B)", (v["rsi"] >= 55) & (v["rsi"] < 60)),
        ("RSI    >= 65", v["rsi"] >= 65),
        ("VolRatio 1.5~3.0 (C)", (v["volume_ratio"] >= 1.5) & (v["volume_ratio"] < 3.0)),
        ("VolRatio >= 3.0", v["volume_ratio"] >= 3.0),
    ]

    rows = []
    for label, mask in pairs:
        e20 = v.loc[mask, "excess_return_20d"].dropna()
        rows.append({
            "Group": label,
            "Signal Count": int(mask.sum()),
            "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
            "Median Excess 20D": round(e20.median(), 2) if len(e20) > 0 else float("nan"),
            "Exc Win Rate 20D": round((e20 > 0).sum() / len(e20) * 100, 1) if len(e20) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Group")
    print(df.to_string())


# ── Section 4: Signal 발생 전 상승률별 성과 ──────────────────────────────────

def print_pre_signal_return_analysis(v: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("4. Signal 발생 전 5D 상승률 구간별 성과  (Valid Signal 기준)")
    print("=" * 72)

    v2 = v.dropna(subset=["return_5d_before"]).copy()
    bins = [float("-inf"), 0.0, 3.0, 7.0, 12.0, float("inf")]
    labels = ["< 0%", "0 ~ 3%", "3 ~ 7%", "7 ~ 12%", ">= 12%"]
    v2["pre_band"] = pd.cut(v2["return_5d_before"], bins=bins, labels=labels, right=False)

    rows = []
    for band in labels:
        grp = v2[v2["pre_band"] == band]
        e20 = grp["excess_return_20d"].dropna()
        rows.append({
            "Pre-Signal 5D Return": band,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
            "Median Excess 20D": round(e20.median(), 2) if len(e20) > 0 else float("nan"),
            "Exc Win Rate 20D": round((e20 > 0).sum() / len(e20) * 100, 1) if len(e20) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Pre-Signal 5D Return")
    print(df.to_string())

    # 3D / 10D 요약
    print()
    for n, col in ((3, "return_3d_before"), (10, "return_10d_before")):
        vp = v.dropna(subset=[col])
        if len(vp) > 0:
            print(
                f"  {n}D-before  mean={vp[col].mean():.2f}%  "
                f"median={vp[col].median():.2f}%  N={len(vp)}"
            )


# ── Section 5: Score 구간별 구성점수 비교 ──────────────────────────────────

def print_score_decomposition(v: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("5. Score 구간별 구성점수 비교  (80~85 vs 85+)")
    print("=" * 72)

    bands = [
        ("80 <= Score < 85", (v["score"] >= 80) & (v["score"] < 85)),
        ("Score >= 85",      v["score"] >= 85),
    ]

    comp_cols = ["trend_score", "volume_score", "momentum_score"]
    rows = []
    for label, mask in bands:
        grp = v[mask]
        comp = grp.dropna(subset=comp_cols)
        e20 = grp["excess_return_20d"].dropna()
        rows.append({
            "Score Band": label,
            "Signal Count": int(mask.sum()),
            "Avg raw_score": round(grp["raw_score"].mean(), 2),
            "Avg trend_score": round(comp["trend_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg volume_score": round(comp["volume_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg momentum_score": round(comp["momentum_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Score Band")
    print(df.to_string())


# ── Section 6: GOOD / BAD 그룹 구성 비교 ──────────────────────────────────

def _classify_group(avg_excess_20d: float) -> str:
    if avg_excess_20d > 1.0:
        return "GOOD"
    if avg_excess_20d < -1.0:
        return "BAD"
    return "NEUTRAL"


def print_good_bad_comparison(v: pd.DataFrame, summary: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("6. GOOD / BAD 종목 그룹별 Signal 구성 비교  (STEP 5 분류 기준)")
    print("=" * 72)
    print("  분류 기준: GOOD = avg_excess_20d > +1%,  BAD = avg_excess_20d < -1%")
    print()

    ticker_group = {
        row["ticker"]: _classify_group(row["avg_excess_20d"])
        for _, row in summary.iterrows()
    }

    v2 = v.copy()
    v2["group"] = v2["ticker"].map(ticker_group)

    comp_cols = ["trend_score", "volume_score", "momentum_score"]
    rows = []
    for g in ("GOOD", "NEUTRAL", "BAD"):
        grp = v2[v2["group"] == g]
        comp = grp.dropna(subset=comp_cols)
        pre = grp.dropna(subset=["return_5d_before"])
        rows.append({
            "Group": g,
            "N Tickers": sum(1 for gv in ticker_group.values() if gv == g),
            "N Signals": len(grp),
            "Avg trend_score": round(comp["trend_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg volume_score": round(comp["volume_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg momentum_score": round(comp["momentum_score"].mean(), 2) if len(comp) > 0 else float("nan"),
            "Avg RSI": round(grp["rsi"].mean(), 2) if len(grp) > 0 else float("nan"),
            "Avg VolRatio": round(grp["volume_ratio"].mean(), 2) if len(grp) > 0 else float("nan"),
            "Avg return_5d_before": round(pre["return_5d_before"].mean(), 2) if len(pre) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Group")
    print(df.to_string())


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("BAIKAL Signal Score v0.1 — STEP 6 Signal 조합 성과 분석")
    print("=" * 72)

    signals, summary = load_data()

    print("\n[Signal 데이터 보강 중 (구성점수 + 사전수익률) ...]")
    signals = enrich_signals(signals)

    v = valid_signals(signals)
    enriched_ok = v["trend_score"].notna().sum()
    print(f"Valid Signal: {len(v)}개  |  구성점수 보강 완료: {enriched_ok}개")

    print_combination_analysis(v)
    print_opposite_analysis(v)
    print_pre_signal_return_analysis(v)
    print_score_decomposition(v)
    print_good_bad_comparison(v, summary)

    print("\n" + "=" * 72)
    print("STEP 6 완료 — 알고리즘 변경 없음")
    print("=" * 72)


if __name__ == "__main__":
    main()

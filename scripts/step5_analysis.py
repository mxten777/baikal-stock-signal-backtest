"""
STEP 5 — Signal 성과 특성 분석
Signal Score v0.1 변경 없음. 기존 output/signals.csv, summary.csv 를 읽어 분석만 수행.
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


# ── 데이터 로드 ───────────────────────────────────────────────────────────

def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(SIGNALS_CSV, parse_dates=["signal_date"])
    summary = pd.read_csv(SUMMARY_CSV)
    return signals, summary


def valid_signals(signals: pd.DataFrame) -> pd.DataFrame:
    return signals[signals["signal_type"] != "OVERHEATED"].copy()


# ── Section 2: 종목 그룹 분류 ─────────────────────────────────────────────

def _classify_group(avg_excess_20d: float) -> str:
    if avg_excess_20d > 1.0:
        return "GOOD"
    if avg_excess_20d < -1.0:
        return "BAD"
    return "NEUTRAL"


def print_ticker_groups(summary: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("2. 종목 성과 그룹 분류 (Avg Excess Return 20D 기준)")
    print("=" * 60)

    groups: dict[str, list[str]] = {"GOOD": [], "NEUTRAL": [], "BAD": []}
    for _, row in summary.iterrows():
        g = _classify_group(row["avg_excess_20d"])
        groups[g].append(f"  {row['ticker']}  {row['name']}  (avg_excess_20d={row['avg_excess_20d']:+.2f}%)")

    for g in ("GOOD", "NEUTRAL", "BAD"):
        print(f"\n[{g}]  ({len(groups[g])}종목)")
        for item in groups[g]:
            print(item)


# ── Section 3: 그룹별 특성 비교 ──────────────────────────────────────────

def print_group_comparison(signals: pd.DataFrame, summary: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("3. 그룹별 특성 비교")
    print("=" * 60)

    ticker_group = {
        row["ticker"]: _classify_group(row["avg_excess_20d"])
        for _, row in summary.iterrows()
    }

    v = valid_signals(signals)
    v["group"] = v["ticker"].map(ticker_group)

    rows = []
    for g in ("GOOD", "NEUTRAL", "BAD"):
        tickers_in_g = [t for t, grp in ticker_group.items() if grp == g]
        grp_v = v[v["group"] == g]
        exc20 = grp_v["excess_return_20d"].dropna()

        rows.append({
            "Group": g,
            "종목 수": len(tickers_in_g),
            "Valid Signal 수": len(grp_v),
            "Avg Score": round(grp_v["score"].mean(), 2),
            "Median Score": round(grp_v["score"].median(), 2),
            "Avg RSI": round(grp_v["rsi"].mean(), 2),
            "Median RSI": round(grp_v["rsi"].median(), 2),
            "Avg Vol Ratio": round(grp_v["volume_ratio"].mean(), 2),
            "Median Vol Ratio": round(grp_v["volume_ratio"].median(), 2),
            "Avg 5D Ret": round(grp_v["return_5d"].mean(), 2),
            "Avg Exc 5D": round(grp_v["excess_return_5d"].mean(), 2),
            "Avg Exc 10D": round(grp_v["excess_return_10d"].mean(), 2),
            "Avg Exc 20D": round(grp_v["excess_return_20d"].mean(), 2),
            "Exc Win Rate 20D": round((exc20 > 0).sum() / len(exc20) * 100, 1) if len(exc20) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Group")
    print(df.to_string())


# ── Section 4: Score 구간별 분석 ─────────────────────────────────────────

def print_score_band_analysis(signals: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("4. Score 구간별 성과")
    print("=" * 60)

    v = valid_signals(signals).copy()
    bins = [75, 80, 85, float("inf")]
    labels = ["75≤Score<80", "80≤Score<85", "Score≥85"]
    v["score_band"] = pd.cut(v["score"], bins=bins, labels=labels, right=False)

    rows = []
    for band in labels:
        grp = v[v["score_band"] == band]
        exc = grp["excess_return_20d"].dropna()
        rows.append({
            "Score Band": band,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(exc.mean(), 2) if len(exc) > 0 else float("nan"),
            "Median Excess 20D": round(exc.median(), 2) if len(exc) > 0 else float("nan"),
            "Excess Win Rate 20D": round((exc > 0).sum() / len(exc) * 100, 1) if len(exc) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Score Band")
    print(df.to_string())


# ── Section 5: RSI 구간별 분석 ────────────────────────────────────────────

def print_rsi_band_analysis(signals: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("5. RSI 구간별 성과")
    print("=" * 60)

    v = valid_signals(signals).copy()
    bins = [0, 55, 60, 65, 76]
    labels = ["RSI<55", "55≤RSI<60", "60≤RSI<65", "65≤RSI≤75"]
    v["rsi_band"] = pd.cut(v["rsi"], bins=bins, labels=labels, right=False)

    rows = []
    for band in labels:
        grp = v[v["rsi_band"] == band]
        exc = grp["excess_return_20d"].dropna()
        rows.append({
            "RSI Band": band,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(exc.mean(), 2) if len(exc) > 0 else float("nan"),
            "Median Excess 20D": round(exc.median(), 2) if len(exc) > 0 else float("nan"),
            "Excess Win Rate 20D": round((exc > 0).sum() / len(exc) * 100, 1) if len(exc) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("RSI Band")
    print(df.to_string())


# ── Section 6: Volume Ratio 구간별 분석 ──────────────────────────────────

def print_volume_band_analysis(signals: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("6. Volume Ratio 구간별 성과")
    print("=" * 60)

    v = valid_signals(signals).copy()
    bins = [0, 1.5, 2.0, 3.0, float("inf")]
    labels = ["<1.5", "1.5~2.0", "2.0~3.0", "≥3.0"]
    v["vol_band"] = pd.cut(v["volume_ratio"], bins=bins, labels=labels, right=False)

    rows = []
    for band in labels:
        grp = v[v["vol_band"] == band]
        exc = grp["excess_return_20d"].dropna()
        rows.append({
            "Volume Band": band,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(exc.mean(), 2) if len(exc) > 0 else float("nan"),
            "Median Excess 20D": round(exc.median(), 2) if len(exc) > 0 else float("nan"),
            "Excess Win Rate 20D": round((exc > 0).sum() / len(exc) * 100, 1) if len(exc) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Volume Band")
    print(df.to_string())


# ── Section 7: 시장 추세 분석 ─────────────────────────────────────────────

def _load_benchmark_with_ma(symbol: str, start_date: str) -> pd.DataFrame:
    from src.benchmark import load_benchmark
    bdf = load_benchmark(symbol, start_date)
    bdf = bdf.sort_values("date").reset_index(drop=True)
    bdf["ma20"] = bdf["close"].rolling(20).mean()
    bdf["ma60"] = bdf["close"].rolling(60).mean()
    return bdf.set_index("date")


def _get_trend(sig_date: pd.Timestamp, bdf_ma: pd.DataFrame) -> str | None:
    available = bdf_ma.index[bdf_ma.index <= sig_date]
    if len(available) == 0:
        return None
    row = bdf_ma.loc[available[-1]]
    if pd.isna(row["ma20"]) or pd.isna(row["ma60"]):
        return None
    return "BULL" if row["ma20"] > row["ma60"] else "BEAR"


def print_market_trend_analysis(signals: pd.DataFrame) -> None:
    print("\n" + "=" * 60)
    print("7. 시장 추세 분석 (BULL / BEAR)")
    print("=" * 60)

    v = valid_signals(signals).copy()

    # 60거래일 ≈ 90 캘린더일 앞에서 시작
    start_date = (v["signal_date"].min() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")

    benchmark_ma: dict[str, pd.DataFrame] = {}
    for symbol in v["benchmark"].unique():
        print(f"  Benchmark {symbol} MA 계산 중 ...")
        try:
            benchmark_ma[symbol] = _load_benchmark_with_ma(symbol, start_date)
        except Exception as e:
            print(f"  !! {symbol} 로드 실패: {e}")
            return

    v["market_trend"] = v.apply(
        lambda r: _get_trend(r["signal_date"], benchmark_ma[r["benchmark"]]),
        axis=1,
    )
    v = v.dropna(subset=["market_trend"])

    rows = []
    for trend in ("BULL", "BEAR"):
        grp = v[v["market_trend"] == trend]
        exc = grp["excess_return_20d"].dropna()
        rows.append({
            "Market": trend,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(exc.mean(), 2) if len(exc) > 0 else float("nan"),
            "Median Excess 20D": round(exc.median(), 2) if len(exc) > 0 else float("nan"),
            "Excess Win Rate 20D": round((exc > 0).sum() / len(exc) * 100, 1) if len(exc) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Market")
    print(df.to_string())


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("BAIKAL Signal Score v0.1 — STEP 5 성과 특성 분석")
    print("=" * 60)

    signals, summary = load_data()
    v = valid_signals(signals)
    print(f"\n총 Valid Signal: {len(v)}개 (excess_return_20d 유효: {v['excess_return_20d'].notna().sum()}개)")
    print(f"총 Signal (OVERHEATED 포함): {len(signals)}개 / 종목: {summary['ticker'].nunique()}개")

    print_ticker_groups(summary)
    print_group_comparison(signals, summary)
    print_score_band_analysis(signals)
    print_rsi_band_analysis(signals)
    print_volume_band_analysis(signals)
    print_market_trend_analysis(signals)


if __name__ == "__main__":
    main()

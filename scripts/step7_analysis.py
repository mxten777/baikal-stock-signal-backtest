"""
STEP 7 — Signal Score v0.2 후보 백테스트 및 v0.1 비교
v0.1 코드 수정 없음.
"""

from __future__ import annotations

import sys
import pandas as pd
import numpy as np
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.indicators import add_all_indicators
from src.signal_engine import generate_signals, generate_signals_v2
from src.backtest import compute_forward_returns
from src.benchmark import load_benchmark, add_benchmark_returns


# ── 파이프라인 ─────────────────────────────────────────────────────────────

def _load_benchmarks() -> dict[str, pd.DataFrame]:
    benchmarks: dict[str, pd.DataFrame] = {}
    for symbol in config.BENCHMARK_SYMBOLS:
        print(f"  Benchmark: {symbol} ...")
        benchmarks[symbol] = load_benchmark(symbol, "2023-01-01")
    return benchmarks


def _run_pipeline(generator, benchmarks: dict[str, pd.DataFrame]) -> pd.DataFrame:
    provider = CsvDataProvider(config.DATA_RAW_DIR)
    all_signals: list[pd.DataFrame] = []

    for ticker, name in config.TICKERS.items():
        try:
            df = provider.load(ticker)
        except (FileNotFoundError, ValueError):
            continue
        df_ind = add_all_indicators(df)
        signals = generator(df_ind, ticker, name)
        if signals.empty:
            continue
        signals = compute_forward_returns(signals, df_ind)
        all_signals.append(signals)

    if not all_signals:
        return pd.DataFrame()

    df_all = pd.concat(all_signals, ignore_index=True)
    df_all = add_benchmark_returns(
        df_all,
        benchmarks=benchmarks,
        market_map=config.MARKET_MAP,
        periods=config.RETURN_PERIODS,
    )
    return df_all


def valid(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["signal_type"] != "OVERHEATED"].copy()


# ── 성과 집계 헬퍼 ─────────────────────────────────────────────────────────

def _perf(label: str, grp: pd.DataFrame) -> dict:
    e5  = grp["excess_return_5d"].dropna()
    e10 = grp["excess_return_10d"].dropna()
    e20 = grp["excess_return_20d"].dropna()

    def _avg(s): return round(s.mean(), 2) if len(s) > 0 else float("nan")
    def _med(s): return round(s.median(), 2) if len(s) > 0 else float("nan")
    def _win(s): return round((s > 0).sum() / len(s) * 100, 1) if len(s) > 0 else float("nan")

    return {
        "Model": label,
        "Valid Signals": len(grp),
        "Avg Excess 5D":  _avg(e5),
        "Avg Excess 10D": _avg(e10),
        "Avg Excess 20D": _avg(e20),
        "Median Excess 5D":  _med(e5),
        "Median Excess 10D": _med(e10),
        "Median Excess 20D": _med(e20),
        "Exc Win Rate 5D":  _win(e5),
        "Exc Win Rate 10D": _win(e10),
        "Exc Win Rate 20D": _win(e20),
    }


# ── Section 1: 전체 비교 ───────────────────────────────────────────────────

def print_overall_comparison(v1: pd.DataFrame, v2: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("1. v0.1 vs v0.2 전체 비교  (Valid Signal 기준)")
    print("=" * 80)
    rows = [_perf("v0.1", v1), _perf("v0.2", v2)]
    df = pd.DataFrame(rows).set_index("Model")
    print(df.to_string())


# ── Section 2: Outlier Top 5 제거 비교 ────────────────────────────────────

def print_outlier_removed_comparison(v1: pd.DataFrame, v2: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("2. Outlier Top 5 제거 비교  (20D Return 상위 5개 제거)")
    print("=" * 80)

    rows = []
    for label, grp in [("v0.1", v1), ("v0.2", v2)]:
        g = grp.copy()
        g["return_20d"] = pd.to_numeric(g["return_20d"], errors="coerce")
        g = g.dropna(subset=["return_20d"])
        top5_idx = g["return_20d"].nlargest(5).index
        g_trimmed = g.drop(index=top5_idx)
        e20 = g_trimmed["excess_return_20d"].dropna()
        rows.append({
            "Model": label,
            "Signals after trim": len(g_trimmed),
            "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
            "Median Excess 20D": round(e20.median(), 2) if len(e20) > 0 else float("nan"),
            "Exc Win Rate 20D": round((e20 > 0).sum() / len(e20) * 100, 1) if len(e20) > 0 else float("nan"),
        })

    df = pd.DataFrame(rows).set_index("Model")
    print(df.to_string())


# ── Section 3: v0.2 Score 구간별 성과 ────────────────────────────────────

def print_v2_score_bands(v2: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("3. v0.2 Score 구간별 성과  (Valid Signal 기준)")
    print("=" * 80)

    bands = [
        ("75~80", (v2["score"] >= 75) & (v2["score"] < 80)),
        ("80~85", (v2["score"] >= 80) & (v2["score"] < 85)),
        ("85+",   v2["score"] >= 85),
    ]
    rows = []
    for label, mask in bands:
        grp = v2[mask]
        e20 = grp["excess_return_20d"].dropna()
        rows.append({
            "Score Band": label,
            "Signal Count": len(grp),
            "Avg Excess 20D": round(e20.mean(), 2) if len(e20) > 0 else float("nan"),
            "Median Excess 20D": round(e20.median(), 2) if len(e20) > 0 else float("nan"),
            "Exc Win Rate 20D": round((e20 > 0).sum() / len(e20) * 100, 1) if len(e20) > 0 else float("nan"),
        })
    df = pd.DataFrame(rows).set_index("Score Band")
    print(df.to_string())


# ── Section 4 & 5: Signal 변화 및 제거 Signal 성과 ────────────────────────

def print_signal_change(v1: pd.DataFrame, v2: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("4. Signal 변화  (Valid Signal 기준, ticker + signal_date 기준)")
    print("=" * 80)

    key = lambda df: set(zip(df["ticker"], pd.to_datetime(df["signal_date"]).dt.date))

    k1 = key(v1)
    k2 = key(v2)

    common  = k1 & k2
    removed = k1 - k2
    new     = k2 - k1

    print(f"  v0.1 valid signal count : {len(k1)}")
    print(f"  v0.2 valid signal count : {len(k2)}")
    print(f"  removed signals         : {len(removed)}")
    print(f"  new signals             : {len(new)}")
    print(f"  common signals          : {len(common)}")

    # 제거된 Signal의 기존(v0.1) 성과
    print("\n" + "=" * 80)
    print("5. 제거된 Signal의 v0.1 기준 성과")
    print("=" * 80)

    if removed:
        v1_copy = v1.copy()
        v1_copy["_key"] = list(zip(v1_copy["ticker"], pd.to_datetime(v1_copy["signal_date"]).dt.date))
        removed_df = v1_copy[v1_copy["_key"].isin(removed)]

        e20_rem = removed_df["excess_return_20d"].dropna()
        print(f"  Removed signal count    : {len(removed_df)}")
        print(f"  Avg Excess 20D          : {round(e20_rem.mean(), 2) if len(e20_rem) > 0 else 'N/A'}")
        print(f"  Median Excess 20D       : {round(e20_rem.median(), 2) if len(e20_rem) > 0 else 'N/A'}")
    else:
        print("  제거된 Signal 없음")


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("BAIKAL Signal Score — STEP 7: v0.1 vs v0.2 비교")
    print("=" * 80)

    print("\n[Benchmark 데이터 로드]")
    try:
        benchmarks = _load_benchmarks()
    except Exception as e:
        print(f"  !! Benchmark 로드 실패: {e}")
        sys.exit(1)

    print("\n[v0.1 파이프라인 실행]")
    df_v1 = _run_pipeline(generate_signals, benchmarks)
    if df_v1.empty:
        print("  !! v0.1 신호 없음")
        sys.exit(1)
    v1 = valid(df_v1)
    print(f"  v0.1 전체 Signal: {len(df_v1)}  Valid: {len(v1)}")

    print("\n[v0.2 파이프라인 실행]")
    df_v2 = _run_pipeline(generate_signals_v2, benchmarks)
    if df_v2.empty:
        print("  !! v0.2 신호 없음")
        sys.exit(1)
    v2 = valid(df_v2)
    print(f"  v0.2 전체 Signal: {len(df_v2)}  Valid: {len(v2)}")

    print_overall_comparison(v1, v2)
    print_outlier_removed_comparison(v1, v2)
    print_v2_score_bands(v2)
    print_signal_change(v1, v2)

    print("\n" + "=" * 80)
    print("STEP 7 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()

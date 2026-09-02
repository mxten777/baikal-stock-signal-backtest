"""STEP 2 — Foreign Flow Factor stability verification.

Reaggregates the fixed 289 signals and fixed STEP 1-B Foreign classifications.
Run: python -m scripts.step2_foreign_flow_stability
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step1b_flow_verification import (
    LOW_SAMPLE_THRESHOLD,
    add_flow_classification,
    load_base_signals,
    load_investor_map,
    load_raw_map,
    merge_investor_features,
    verify_baseline,
)
from src.benchmark import load_benchmark
from src.config import MARKET_MAP, OUTPUT_DIR

OUTPUT_BY_YEAR = OUTPUT_DIR / "v02_step2_foreign_by_year.csv"
OUTPUT_BY_MARKET = OUTPUT_DIR / "v02_step2_foreign_by_market.csv"
OUTPUT_BY_REGIME = OUTPUT_DIR / "v02_step2_foreign_by_regime.csv"
OUTPUT_BY_SIGNAL_LEVEL = OUTPUT_DIR / "v02_step2_foreign_by_signal_level.csv"
OUTPUT_BY_HORIZON = OUTPUT_DIR / "v02_step2_foreign_by_horizon.csv"
OUTPUT_BY_STOCK = OUTPUT_DIR / "v02_step2_foreign_by_stock.csv"
OUTPUT_SENSITIVITY = OUTPUT_DIR / "v02_step2_foreign_sensitivity.csv"

FLOW_CLASSES = ("POSITIVE", "NEUTRAL", "NEGATIVE")
REGIME_MA_SHORT = 20
REGIME_MA_MEDIUM = 60


def _mean(df: pd.DataFrame, column: str) -> float:
    values = df[column].dropna()
    return round(float(values.mean()), 2) if len(values) else float("nan")


def _win_rate(df: pd.DataFrame, column: str) -> float:
    values = df[column].dropna()
    return round(float((values > 0).mean() * 100), 1) if len(values) else float("nan")


def _flag(n: int) -> str:
    return " [LOW SAMPLE]" if n < LOW_SAMPLE_THRESHOLD else ""


def build_year_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year in (2023, 2024, 2025, 2026):
        year_df = df[df["signal_date"].dt.year == year]
        partial = "PARTIAL YEAR; " if year in (2023, 2026) else ""
        for flow_class in FLOW_CLASSES:
            subset = year_df[year_df["foreign_flow_class"] == flow_class]
            rows.append({"Year": year, "Foreign Flow": flow_class, "N": len(subset),
                         "Low Sample": f"{partial}{_flag(len(subset)).strip()}".strip("; "),
                         "Avg Return 20D": _mean(subset, "return_20d"),
                         "Avg Excess 20D": _mean(subset, "excess_return_20d"),
                         "Win Rate 20D (%)": _win_rate(subset, "return_20d")})
        positive = year_df[year_df["foreign_flow_class"] == "POSITIVE"]
        negative = year_df[year_df["foreign_flow_class"] == "NEGATIVE"]
        rows.append({"Year": year, "Foreign Flow": "POSITIVE - NEGATIVE", "N": np.nan,
                     "Low Sample": f"{partial}{_flag(min(len(positive), len(negative))).strip()}".strip("; "),
                     "Avg Return 20D": round(_mean(positive, "return_20d") - _mean(negative, "return_20d"), 2),
                     "Avg Excess 20D": round(_mean(positive, "excess_return_20d") - _mean(negative, "excess_return_20d"), 2),
                     "Win Rate 20D (%)": round(_win_rate(positive, "return_20d") - _win_rate(negative, "return_20d"), 1)})
    return pd.DataFrame(rows)


def _build_group_table(df: pd.DataFrame, dimension: str, values: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    for value in values:
        group = df[df[dimension] == value]
        for flow_class in FLOW_CLASSES:
            subset = group[group["foreign_flow_class"] == flow_class]
            rows.append({dimension: value, "Foreign Flow": flow_class, "N": len(subset),
                         "Low Sample": _flag(len(subset)).strip(),
                         "Avg Excess 5D": _mean(subset, "excess_return_5d"),
                         "Avg Excess 10D": _mean(subset, "excess_return_10d"),
                         "Avg Excess 20D": _mean(subset, "excess_return_20d"),
                         "Win Rate 20D (%)": _win_rate(subset, "return_20d")})
        positive = group[group["foreign_flow_class"] == "POSITIVE"]
        negative = group[group["foreign_flow_class"] == "NEGATIVE"]
        rows.append({dimension: value, "Foreign Flow": "POSITIVE - NEGATIVE", "N": np.nan,
                     "Low Sample": _flag(min(len(positive), len(negative))).strip(),
                     "Avg Excess 5D": round(_mean(positive, "excess_return_5d") - _mean(negative, "excess_return_5d"), 2),
                     "Avg Excess 10D": round(_mean(positive, "excess_return_10d") - _mean(negative, "excess_return_10d"), 2),
                     "Avg Excess 20D": round(_mean(positive, "excess_return_20d") - _mean(negative, "excess_return_20d"), 2),
                     "Win Rate 20D (%)": round(_win_rate(positive, "return_20d") - _win_rate(negative, "return_20d"), 1)})
    return pd.DataFrame(rows)


def add_market_and_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Reuse STEP 5's pre-existing MA20-versus-MA60 trend logic as UP/DOWN."""
    result = df.copy()
    result["Market"] = result["ticker"].map(MARKET_MAP).map({"KS11": "KOSPI", "KQ11": "KOSDAQ"})
    result["Regime"] = pd.NA
    start = (result["signal_date"].min() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    for symbol, market in (("KS11", "KOSPI"), ("KQ11", "KOSDAQ")):
        benchmark = load_benchmark(symbol, start).sort_values("date").reset_index(drop=True)
        benchmark["ma20"] = benchmark["close"].rolling(REGIME_MA_SHORT).mean()
        benchmark["ma60"] = benchmark["close"].rolling(REGIME_MA_MEDIUM).mean()
        indexed = benchmark.set_index("date")
        for index, signal_date in result.loc[result["Market"] == market, "signal_date"].items():
            available = indexed.index[indexed.index <= signal_date]
            if len(available):
                row = indexed.loc[available[-1]]
                if pd.notna(row["ma20"]) and pd.notna(row["ma60"]):
                    result.at[index, "Regime"] = "UP" if row["ma20"] > row["ma60"] else "DOWN"
    return result


def build_horizon_table(df: pd.DataFrame) -> pd.DataFrame:
    positive = df[df["foreign_flow_class"] == "POSITIVE"]
    negative = df[df["foreign_flow_class"] == "NEGATIVE"]
    rows = []
    for horizon in (5, 10, 20):
        return_col, excess_col = f"return_{horizon}d", f"excess_return_{horizon}d"
        rows.append({"Horizon": f"{horizon}D", "Positive N": len(positive), "Negative N": len(negative),
                     "Avg Return Difference": round(_mean(positive, return_col) - _mean(negative, return_col), 2),
                     "Avg Excess Difference": round(_mean(positive, excess_col) - _mean(negative, excess_col), 2),
                     "Win Rate Difference (%)": round(_win_rate(positive, return_col) - _win_rate(negative, return_col), 1),
                     "Low Sample": _flag(min(len(positive), len(negative))).strip()})
    return pd.DataFrame(rows)


def build_stock_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (ticker, name), group in df.groupby(["ticker", "name"], sort=True):
        positive = group[group["foreign_flow_class"] == "POSITIVE"]
        negative = group[group["foreign_flow_class"] == "NEGATIVE"]
        rows.append({"ticker": ticker, "name": name, "Foreign Positive N": len(positive), "Foreign Negative N": len(negative),
                     "Positive Avg Excess 20D": _mean(positive, "excess_return_20d"),
                     "Negative Avg Excess 20D": _mean(negative, "excess_return_20d"),
                     "Positive - Negative Excess 20D": round(_mean(positive, "excess_return_20d") - _mean(negative, "excess_return_20d"), 2),
                     "Low Sample": _flag(min(len(positive), len(negative))).strip()})
    return pd.DataFrame(rows)


def build_institution_reference(df: pd.DataFrame) -> pd.DataFrame:
    """Minimal STEP 1-B comparison reference; Institution is not re-stratified."""
    rows = []
    for flow_class in ("POSITIVE", "NEGATIVE"):
        subset = df[df["institution_flow_class"] == flow_class]
        rows.append({"Institution Flow": flow_class, "N": len(subset),
                     "Avg Excess 20D": _mean(subset, "excess_return_20d"),
                     "Win Rate 20D (%)": _win_rate(subset, "return_20d")})
    positive = df[df["institution_flow_class"] == "POSITIVE"]
    negative = df[df["institution_flow_class"] == "NEGATIVE"]
    rows.append({"Institution Flow": "POSITIVE - NEGATIVE", "N": np.nan,
                 "Avg Excess 20D": round(_mean(positive, "excess_return_20d") - _mean(negative, "excess_return_20d"), 2),
                 "Win Rate 20D (%)": round(_win_rate(positive, "return_20d") - _win_rate(negative, "return_20d"), 1)})
    return pd.DataFrame(rows)


def build_sensitivity_table(df: pd.DataFrame, stock_table: pd.DataFrame) -> pd.DataFrame:
    overall_avg = df.groupby("ticker")["excess_return_20d"].mean()
    exclusions = [("ALL", None), ("EXCLUDE HIGHEST AVG EXCESS", overall_avg.idxmax()), ("EXCLUDE LOWEST AVG EXCESS", overall_avg.idxmin())]
    rows = []
    for scenario, ticker in exclusions:
        subset = df if ticker is None else df[df["ticker"] != ticker]
        positive = subset[subset["foreign_flow_class"] == "POSITIVE"]
        negative = subset[subset["foreign_flow_class"] == "NEGATIVE"]
        difference = _mean(positive, "excess_return_20d") - _mean(negative, "excess_return_20d")
        rows.append({"Scenario": scenario, "Excluded Ticker": ticker or "",
                     "Excluded Name": "" if ticker is None else stock_table.loc[stock_table["ticker"] == ticker, "name"].iloc[0],
                     "Positive N": len(positive), "Negative N": len(negative),
                     "Positive - Negative Excess 20D": round(difference, 2), "Direction Positive": difference > 0,
                     "Low Sample": _flag(min(len(positive), len(negative))).strip()})
    return pd.DataFrame(rows)


def main() -> None:
    signals = load_base_signals()
    verify_baseline(signals)
    print(f"Signal Count = {len(signals)} (expected 289) - OK")
    classified = add_flow_classification(merge_investor_features(signals, load_investor_map(), load_raw_map()))
    if len(classified) != 289 or classified["foreign_flow_class"].isin(FLOW_CLASSES).sum() != 289:
        raise RuntimeError("Foreign flow merge/classification coverage mismatch - analysis stopped")
    enriched = add_market_and_regime(classified)
    outputs = {
        OUTPUT_BY_YEAR: build_year_table(enriched),
        OUTPUT_BY_MARKET: _build_group_table(enriched, "Market", ("KOSPI", "KOSDAQ")),
        OUTPUT_BY_REGIME: _build_group_table(enriched.dropna(subset=["Regime"]), "Regime", ("UP", "DOWN")),
        OUTPUT_BY_SIGNAL_LEVEL: _build_group_table(enriched, "score_group", ("MID", "HIGH")),
        OUTPUT_BY_HORIZON: build_horizon_table(enriched),
        OUTPUT_BY_STOCK: build_stock_table(enriched),
    }
    outputs[OUTPUT_SENSITIVITY] = build_sensitivity_table(enriched, outputs[OUTPUT_BY_STOCK])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, table in outputs.items():
        table.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"Wrote {path.relative_to(ROOT)}")
    print("Institution reference (20D only):")
    print(build_institution_reference(enriched).to_string(index=False))


if __name__ == "__main__":
    main()
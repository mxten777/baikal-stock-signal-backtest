"""STEP 7 - Foreign NEGATIVE Filter time stability / walk-forward validation.

Analysis-only. Does not change production signal generation, thresholds,
foreign class definition, weights, or the frozen Step 13 source. Reuses the
existing Foreign NEGATIVE definition (ratio <= -0.20) and STEP 6 false
negative definition (foreign_class == NEGATIVE and return_20d > 0 and
excess_20d > 0) unchanged.

Run: python -m scripts.step7_foreign_filter_robustness
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step5_selection_recalculation import build_recalculated_selection
from src.config import MARKET_MAP, OUTPUT_DIR

EXPECTED_SIGNAL_COUNT = 289
EXPECTED_TICKER_COUNT = 20
EXPECTED_NEGATIVE_COUNT = 37
LOW_SAMPLE_THRESHOLD = 10
PARTIAL_YEARS = (2023, 2026)
WALK_FORWARD_FOLDS = 4
HORIZONS = ("5d", "10d", "20d")

OUTPUT_YEAR = OUTPUT_DIR / "v02_step7_filter_by_year.csv"
OUTPUT_EARLY_LATE = OUTPUT_DIR / "v02_step7_filter_early_late.csv"
OUTPUT_WALKFORWARD = OUTPUT_DIR / "v02_step7_filter_walkforward.csv"
OUTPUT_SUCCESS_RATE = OUTPUT_DIR / "v02_step7_filter_success_rate.csv"
OUTPUT_NEGATIVE_BY_PERIOD = OUTPUT_DIR / "v02_step7_negative_by_period.csv"
OUTPUT_FALSE_NEGATIVE_BY_PERIOD = OUTPUT_DIR / "v02_step7_false_negative_by_period.csv"
OUTPUT_HORIZON = OUTPUT_DIR / "v02_step7_filter_by_horizon.csv"
OUTPUT_MARKET = OUTPUT_DIR / "v02_step7_filter_by_market.csv"
OUTPUT_BY_STOCK = OUTPUT_DIR / "v02_step7_filter_by_stock.csv"
OUTPUT_LEAVE_ONE_OUT = OUTPUT_DIR / "v02_step7_filter_leave_one_out.csv"
OUTPUT_REPORT = OUTPUT_DIR / "v02_step7_filter_robustness_report.md"


def _avg(series: pd.Series) -> float:
    values = series.dropna()
    return round(float(values.mean()), 2) if len(values) else np.nan


def _win_rate(series: pd.Series) -> float:
    values = series.dropna()
    return round(float((values > 0).mean() * 100), 1) if len(values) else np.nan


def load_baseline() -> pd.DataFrame:
    frame = build_recalculated_selection()
    if len(frame) != EXPECTED_SIGNAL_COUNT or frame["ticker"].nunique() != EXPECTED_TICKER_COUNT:
        raise RuntimeError(
            f"STEP 7 baseline mismatch: signals={len(frame)} (expected {EXPECTED_SIGNAL_COUNT}), "
            f"tickers={frame['ticker'].nunique()} (expected {EXPECTED_TICKER_COUNT}) - analysis halted"
        )
    negative_count = int((frame["current_foreign_class"] == "NEGATIVE").sum())
    if negative_count != EXPECTED_NEGATIVE_COUNT:
        raise RuntimeError(
            f"STEP 7 baseline mismatch: NEGATIVE={negative_count} (expected {EXPECTED_NEGATIVE_COUNT}) - analysis halted"
        )
    frame = frame.sort_values(["signal_date", "ticker"], kind="stable").reset_index(drop=True)
    frame["year"] = frame["signal_date"].dt.year
    frame["market"] = frame["ticker"].map(MARKET_MAP)
    frame["is_negative"] = frame["current_foreign_class"] == "NEGATIVE"
    frame["is_eligible"] = ~frame["is_negative"]
    frame["is_false_negative"] = (
        frame["is_negative"] & (frame["return_20d"] > 0) & (frame["excess_20d"] > 0)
    )
    return frame


def _horizon_metrics(df: pd.DataFrame, horizon: str) -> dict[str, float | int]:
    return {
        "N": len(df),
        "Avg Return": _avg(df[f"return_{horizon}"]),
        "Avg Excess": _avg(df[f"excess_return_{horizon}"]),
        "Win Rate (%)": _win_rate(df[f"return_{horizon}"]),
    }


def _before_after_row(df: pd.DataFrame, horizon: str = "20d") -> dict[str, float | int]:
    eligible = df[df["is_eligible"]]
    before = _horizon_metrics(df, horizon)
    after = _horizon_metrics(eligible, horizon)
    excess_improvement = (
        round(after["Avg Excess"] - before["Avg Excess"], 2)
        if pd.notna(after["Avg Excess"]) and pd.notna(before["Avg Excess"]) else np.nan
    )
    win_rate_improvement = (
        round(after["Win Rate (%)"] - before["Win Rate (%)"], 1)
        if pd.notna(after["Win Rate (%)"]) and pd.notna(before["Win Rate (%)"]) else np.nan
    )
    return {
        "All N": len(df),
        "Negative N": int(df["is_negative"].sum()),
        "Eligible N": len(eligible),
        "Before Avg Return 20D": before["Avg Return"],
        "Before Avg Excess 20D": before["Avg Excess"],
        "Before Win Rate 20D (%)": before["Win Rate (%)"],
        "After Avg Return 20D": after["Avg Return"],
        "After Avg Excess 20D": after["Avg Excess"],
        "After Win Rate 20D (%)": after["Win Rate (%)"],
        "Excess Improvement": excess_improvement,
        "Win Rate Improvement": win_rate_improvement,
    }


def build_year_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year in sorted(frame["year"].dropna().unique()):
        subset = frame[frame["year"] == year]
        row = {"Year": int(year)}
        row.update(_before_after_row(subset))
        low_sample = year in PARTIAL_YEARS or row["Eligible N"] < LOW_SAMPLE_THRESHOLD or row["Negative N"] < LOW_SAMPLE_THRESHOLD
        row["Low Sample"] = bool(low_sample)
        rows.append(row)
    return pd.DataFrame(rows)


def build_early_late_table(frame: pd.DataFrame) -> pd.DataFrame:
    n = len(frame)
    split = n // 2
    periods = {
        "EARLY": frame.iloc[:split],
        "LATE": frame.iloc[split:],
    }
    rows = []
    for label, subset in periods.items():
        row = {"Period": label}
        row.update(_before_after_row(subset))
        row["Low Sample"] = row["Eligible N"] < LOW_SAMPLE_THRESHOLD or row["Negative N"] < LOW_SAMPLE_THRESHOLD
        row["Start Date"] = subset["signal_date"].min()
        row["End Date"] = subset["signal_date"].max()
        rows.append(row)
    return pd.DataFrame(rows)


def build_walkforward_table(frame: pd.DataFrame, n_folds: int = WALK_FORWARD_FOLDS) -> pd.DataFrame:
    n = len(frame)
    boundaries = [round(i * n / n_folds) for i in range(n_folds + 1)]
    rows = []
    for fold_idx in range(n_folds):
        subset = frame.iloc[boundaries[fold_idx]:boundaries[fold_idx + 1]]
        row = {"Fold": f"Fold {fold_idx + 1}"}
        row.update(_before_after_row(subset))
        row["Low Sample"] = row["Eligible N"] < LOW_SAMPLE_THRESHOLD or row["Negative N"] < LOW_SAMPLE_THRESHOLD
        row["Start Date"] = subset["signal_date"].min()
        row["End Date"] = subset["signal_date"].max()
        rows.append(row)
    return pd.DataFrame(rows)


def build_success_rate_table(year: pd.DataFrame, early_late: pd.DataFrame, walkforward: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period_type, table, label_col in (
        ("YEAR", year, "Year"),
        ("EARLY_LATE", early_late, "Period"),
        ("WALK_FORWARD", walkforward, "Fold"),
    ):
        for _, r in table.iterrows():
            rows.append({
                "Period Type": period_type,
                "Period": r[label_col],
                "N": r["All N"],
                "Low Sample": bool(r["Low Sample"]),
                "Excess Improved": bool(r["Excess Improvement"] > 0) if pd.notna(r["Excess Improvement"]) else np.nan,
                "Win Rate Improved": bool(r["Win Rate Improvement"] > 0) if pd.notna(r["Win Rate Improvement"]) else np.nan,
            })
    detail = pd.DataFrame(rows)
    valid = detail[~detail["Low Sample"]]
    summary = pd.DataFrame([{
        "Period Type": "SUMMARY",
        "Period": "VALID_PERIODS_ONLY",
        "N": len(valid),
        "Low Sample": False,
        "Excess Improved": int((valid["Excess Improved"] == True).sum()),  # noqa: E712
        "Win Rate Improved": int((valid["Win Rate Improved"] == True).sum()),  # noqa: E712
    }, {
        "Period Type": "SUMMARY",
        "Period": "EXCESS_WORSENED",
        "N": len(valid),
        "Low Sample": False,
        "Excess Improved": int((valid["Excess Improved"] == False).sum()),  # noqa: E712
        "Win Rate Improved": int((valid["Win Rate Improved"] == False).sum()),  # noqa: E712
    }])
    return pd.concat([detail, summary], ignore_index=True)


def build_negative_by_period(frame: pd.DataFrame, year: pd.DataFrame, walkforward: pd.DataFrame,
                             walkforward_boundaries: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for _, r in year.iterrows():
        subset = frame[frame["year"] == r["Year"]]
        negative = subset[subset["is_negative"]]
        rows.append({
            "Period Type": "YEAR", "Period": int(r["Year"]), "N": len(negative),
            "Avg Return 20D": _avg(negative["return_20d"]), "Avg Excess 20D": _avg(negative["excess_20d"]),
            "Win Rate 20D (%)": _win_rate(negative["return_20d"]), "Low Sample": bool(r["Low Sample"]),
        })
    for fold_idx, subset in enumerate(walkforward_boundaries, start=1):
        negative = subset[subset["is_negative"]]
        rows.append({
            "Period Type": "FOLD", "Period": f"Fold {fold_idx}", "N": len(negative),
            "Avg Return 20D": _avg(negative["return_20d"]), "Avg Excess 20D": _avg(negative["excess_20d"]),
            "Win Rate 20D (%)": _win_rate(negative["return_20d"]),
            "Low Sample": len(negative) < LOW_SAMPLE_THRESHOLD,
        })
    return pd.DataFrame(rows)


def build_false_negative_by_period(frame: pd.DataFrame, year: pd.DataFrame,
                                   walkforward_boundaries: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for _, r in year.iterrows():
        subset = frame[frame["year"] == r["Year"]]
        negative = subset[subset["is_negative"]]
        fn = negative[negative["is_false_negative"]]
        rate = round(len(fn) / len(negative) * 100, 1) if len(negative) else np.nan
        rows.append({
            "Period Type": "YEAR", "Period": int(r["Year"]), "Negative N": len(negative),
            "False Negative N": len(fn), "False Negative Rate (%)": rate, "Low Sample": bool(r["Low Sample"]),
        })
    for fold_idx, subset in enumerate(walkforward_boundaries, start=1):
        negative = subset[subset["is_negative"]]
        fn = negative[negative["is_false_negative"]]
        rate = round(len(fn) / len(negative) * 100, 1) if len(negative) else np.nan
        rows.append({
            "Period Type": "FOLD", "Period": f"Fold {fold_idx}", "Negative N": len(negative),
            "False Negative N": len(fn), "False Negative Rate (%)": rate,
            "Low Sample": len(negative) < LOW_SAMPLE_THRESHOLD,
        })
    return pd.DataFrame(rows)


def build_horizon_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    eligible = frame[frame["is_eligible"]]
    for horizon in HORIZONS:
        before = _horizon_metrics(frame, horizon)
        after = _horizon_metrics(eligible, horizon)
        rows.append({
            "Horizon": horizon.upper(),
            "Before Avg Return": before["Avg Return"], "Before Avg Excess": before["Avg Excess"],
            "Before Win Rate (%)": before["Win Rate (%)"],
            "After Avg Return": after["Avg Return"], "After Avg Excess": after["Avg Excess"],
            "After Win Rate (%)": after["Win Rate (%)"],
            "Excess Improvement": round(after["Avg Excess"] - before["Avg Excess"], 2),
            "Win Rate Improvement": round(after["Win Rate (%)"] - before["Win Rate (%)"], 1),
        })
    return pd.DataFrame(rows)


def build_market_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for market in ("KS11", "KQ11"):
        subset = frame[frame["market"] == market]
        row = {"Market": "KOSPI" if market == "KS11" else "KOSDAQ"}
        row.update(_before_after_row(subset))
        row["Low Sample"] = row["Eligible N"] < LOW_SAMPLE_THRESHOLD or row["Negative N"] < LOW_SAMPLE_THRESHOLD
        rows.append(row)
    return pd.DataFrame(rows)


def build_by_stock_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, subset in frame.groupby("ticker"):
        name = subset["name"].iloc[0]
        negative_n = int(subset["is_negative"].sum())
        before_excess = _avg(subset["excess_20d"])
        if negative_n == 0:
            after_excess = before_excess
            improvement = "N/A"
        else:
            eligible = subset[subset["is_eligible"]]
            after_excess = _avg(eligible["excess_20d"])
            improvement = round(after_excess - before_excess, 2) if pd.notna(after_excess) and pd.notna(before_excess) else "N/A"
        rows.append({
            "ticker": ticker, "name": name, "Signal N": len(subset), "Negative N": negative_n,
            "Before Avg Excess 20D": before_excess,
            "After Avg Excess 20D": after_excess if negative_n else "N/A",
            "Improvement": improvement,
        })
    table = pd.DataFrame(rows).sort_values("Signal N", ascending=False).reset_index(drop=True)
    return table


def build_leave_one_out_table(frame: pd.DataFrame) -> pd.DataFrame:
    overall = _before_after_row(frame)
    overall_improvement = overall["Excess Improvement"]
    overall_sign = overall_improvement > 0
    rows = []
    for ticker in sorted(frame["ticker"].unique()):
        remaining = frame[frame["ticker"] != ticker]
        row = {"Excluded Ticker": ticker, "Remaining N": len(remaining)}
        row.update(_before_after_row(remaining))
        sign_flipped = (
            pd.notna(row["Excess Improvement"]) and (row["Excess Improvement"] > 0) != overall_sign
        )
        row["Sign Flipped"] = bool(sign_flipped)
        rows.append(row)
    table = pd.DataFrame(rows)
    table.attrs["overall_improvement"] = overall_improvement
    return table


def determine_verdict(success_rate: pd.DataFrame, leave_one_out: pd.DataFrame) -> str:
    summary = success_rate[success_rate["Period Type"] == "SUMMARY"]
    improved_row = summary[summary["Period"] == "VALID_PERIODS_ONLY"].iloc[0]
    total_valid = improved_row["N"]
    excess_improved_count = improved_row["Excess Improved"]
    rate = excess_improved_count / total_valid if total_valid else np.nan
    any_sign_flip = bool(leave_one_out["Sign Flipped"].any())
    if pd.isna(rate):
        return "CONDITIONAL_FILTER"
    if rate >= 0.8 and not any_sign_flip:
        return "ROBUST_FILTER"
    if rate >= 0.5:
        return "CONDITIONAL_FILTER"
    return "FRAGILE_FILTER"


def write_report(frame: pd.DataFrame, year: pd.DataFrame, early_late: pd.DataFrame, walkforward: pd.DataFrame,
                 success_rate: pd.DataFrame, negative_period: pd.DataFrame, false_negative_period: pd.DataFrame,
                 horizon: pd.DataFrame, market: pd.DataFrame, by_stock: pd.DataFrame, leave_one_out: pd.DataFrame,
                 verdict: str) -> None:
    OUTPUT_REPORT.write_text(
        "# STEP 7 Foreign NEGATIVE Filter Robustness\n\n"
        "## A. Baseline\n\n"
        f"- Signals: {len(frame)}/{EXPECTED_SIGNAL_COUNT}; tickers: {frame['ticker'].nunique()}/{EXPECTED_TICKER_COUNT}; "
        f"NEGATIVE: {int(frame['is_negative'].sum())}/{EXPECTED_NEGATIVE_COUNT}.\n"
        "- Foreign NEGATIVE definition, thresholds, weights, and signal conditions unchanged.\n\n"
        "## B. Year-by-Year Filter Effect\n\n" + year.to_markdown(index=False) + "\n\n"
        "## C. Early / Late Comparison\n\n" + early_late.to_markdown(index=False) + "\n\n"
        "## D. Walk-Forward Results\n\n" + walkforward.to_markdown(index=False) + "\n\n"
        "## E. Filter Success Rate\n\n" + success_rate.to_markdown(index=False) + "\n\n"
        "## F. NEGATIVE Group Time Stability\n\n" + negative_period.to_markdown(index=False) + "\n\n"
        "## G. False Negative Time Distribution\n\n" + false_negative_period.to_markdown(index=False) + "\n\n"
        "## H. 5D / 10D / 20D Horizon Effect\n\n" + horizon.to_markdown(index=False) + "\n\n"
        "## I. KOSPI / KOSDAQ\n\n" + market.to_markdown(index=False) + "\n\n"
        "## J. Stock Dependency / Leave-One-Out\n\n"
        + by_stock.to_markdown(index=False) + "\n\n"
        + leave_one_out.to_markdown(index=False) + "\n\n"
        "## K. Generated Files\n\n"
        "- " + ", ".join([
            OUTPUT_YEAR.name, OUTPUT_EARLY_LATE.name, OUTPUT_WALKFORWARD.name, OUTPUT_SUCCESS_RATE.name,
            OUTPUT_NEGATIVE_BY_PERIOD.name, OUTPUT_FALSE_NEGATIVE_BY_PERIOD.name, OUTPUT_HORIZON.name,
            OUTPUT_MARKET.name, OUTPUT_BY_STOCK.name, OUTPUT_LEAVE_ONE_OUT.name,
        ]) + "\n\n"
        "## L. Tests\n\n- `python -m pytest -q` must remain 0 failed.\n\n"
        "## M. Final Judgment\n\n" + verdict + "\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame | str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = load_baseline()

    year = build_year_table(frame)
    early_late = build_early_late_table(frame)
    walkforward = build_walkforward_table(frame)
    success_rate = build_success_rate_table(year, early_late, walkforward)

    n = len(frame)
    boundaries = [round(i * n / WALK_FORWARD_FOLDS) for i in range(WALK_FORWARD_FOLDS + 1)]
    walkforward_boundaries = [frame.iloc[boundaries[i]:boundaries[i + 1]] for i in range(WALK_FORWARD_FOLDS)]

    negative_period = build_negative_by_period(frame, year, walkforward, walkforward_boundaries)
    false_negative_period = build_false_negative_by_period(frame, year, walkforward_boundaries)
    horizon = build_horizon_table(frame)
    market = build_market_table(frame)
    by_stock = build_by_stock_table(frame)
    leave_one_out = build_leave_one_out_table(frame)
    verdict = determine_verdict(success_rate, leave_one_out)

    year.to_csv(OUTPUT_YEAR, index=False, encoding="utf-8-sig")
    early_late.to_csv(OUTPUT_EARLY_LATE, index=False, encoding="utf-8-sig")
    walkforward.to_csv(OUTPUT_WALKFORWARD, index=False, encoding="utf-8-sig")
    success_rate.to_csv(OUTPUT_SUCCESS_RATE, index=False, encoding="utf-8-sig")
    negative_period.to_csv(OUTPUT_NEGATIVE_BY_PERIOD, index=False, encoding="utf-8-sig")
    false_negative_period.to_csv(OUTPUT_FALSE_NEGATIVE_BY_PERIOD, index=False, encoding="utf-8-sig")
    horizon.to_csv(OUTPUT_HORIZON, index=False, encoding="utf-8-sig")
    market.to_csv(OUTPUT_MARKET, index=False, encoding="utf-8-sig")
    by_stock.to_csv(OUTPUT_BY_STOCK, index=False, encoding="utf-8-sig")
    leave_one_out.to_csv(OUTPUT_LEAVE_ONE_OUT, index=False, encoding="utf-8-sig")
    write_report(frame, year, early_late, walkforward, success_rate, negative_period, false_negative_period,
                horizon, market, by_stock, leave_one_out, verdict)

    return {
        "frame": frame, "year": year, "early_late": early_late, "walkforward": walkforward,
        "success_rate": success_rate, "negative_period": negative_period,
        "false_negative_period": false_negative_period, "horizon": horizon, "market": market,
        "by_stock": by_stock, "leave_one_out": leave_one_out, "verdict": verdict,
    }


if __name__ == "__main__":
    reports = run()
    print("STEP 7 complete")
    print(reports["year"].to_string(index=False))
    print(f"Final Judgment: {reports['verdict']}")

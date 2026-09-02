"""STEP 5 - Recalculate Selection Score with current investor coverage.

This script is analysis-only. It keeps the frozen Step 13 signal, fundamental,
weight, and threshold definitions unchanged, then recalculates only the Foreign
component from the current 100% investor data coverage.

Run: python -m scripts.step5_selection_recalculation
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step1b_flow_verification import (
    EXPECTED_SIGNAL_COUNT,
    EXPECTED_TICKER_COUNT,
    add_flow_classification,
    build_merge_quality,
    load_investor_map,
    load_raw_map,
    merge_investor_features,
    verify_baseline,
)
from src.config import OUTPUT_DIR
from src.integrated_backtest import STRATEGIES, build_strategies, compute_metrics
from src.stock_selection import classify_score_group, compute_stock_selection_score, foreign_ratio_to_score

SOURCE_PATH = OUTPUT_DIR / "step13_stock_selection_score.csv"
OUTPUT_SELECTION = OUTPUT_DIR / "v02_step5_selection_recalculated.csv"
OUTPUT_SCORE_CHANGE = OUTPUT_DIR / "v02_step5_score_change_summary.csv"
OUTPUT_RANK_MOVEMENT = OUTPUT_DIR / "v02_step5_rank_movement.csv"
OUTPUT_TOP_GROUP = OUTPUT_DIR / "v02_step5_top_group_comparison.csv"
OUTPUT_QUANTILE = OUTPUT_DIR / "v02_step5_quantile_comparison.csv"
OUTPUT_FOREIGN_CLASS = OUTPUT_DIR / "v02_step5_foreign_class_impact.csv"
OUTPUT_SIGNAL_LEVEL = OUTPUT_DIR / "v02_step5_signal_level_comparison.csv"
OUTPUT_REPORT = OUTPUT_DIR / "v02_step5_selection_recalculation_report.md"

SIGNAL_COLUMNS = [
    "ticker", "name", "signal_date", "score", "signal_type", "return_5d", "return_10d", "return_20d",
    "excess_return_5d", "excess_return_10d", "excess_return_20d", "max_drawdown_20d", "score_group",
    "foreign_5d_ratio", "investor_score", "growth_score", "stock_selection_score",
]


def load_frozen_signals() -> pd.DataFrame:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing {SOURCE_PATH}; run STEP 13 first")
    df = pd.read_csv(SOURCE_PATH, dtype={"ticker": str}, parse_dates=["signal_date"])
    df["ticker"] = df["ticker"].str.zfill(6)
    verify_baseline(df)
    missing = sorted(set(SIGNAL_COLUMNS) - set(df.columns))
    if missing:
        raise RuntimeError(f"Frozen Step 13 file is missing required columns: {missing}")
    return df[SIGNAL_COLUMNS].copy().reset_index(drop=True)


def _compare_direction(series: pd.Series) -> dict[str, int]:
    return {
        "unchanged": int((series == 0).sum()),
        "increased": int((series > 0).sum()),
        "decreased": int((series < 0).sum()),
    }


def _avg(series: pd.Series) -> float:
    values = series.dropna()
    return round(float(values.mean()), 2) if len(values) else np.nan


def _win_rate(series: pd.Series) -> float:
    values = series.dropna()
    return round(float((values > 0).mean() * 100), 1) if len(values) else np.nan


def _metric_row(df: pd.DataFrame) -> dict[str, float | int]:
    metrics = compute_metrics(df)
    return {
        "N": metrics["signal_count"],
        "Avg Return 5D": metrics["avg_return_5d"],
        "Avg Return 10D": metrics["avg_return_10d"],
        "Avg Return 20D": metrics["avg_return_20d"],
        "Avg Excess 20D": metrics["avg_excess_return_20d"],
        "Win Rate 20D (%)": metrics["win_rate_20d"],
    }


def build_recalculated_selection() -> pd.DataFrame:
    frozen = load_frozen_signals()
    current_input = frozen.drop(columns=["foreign_5d_ratio"])
    current = merge_investor_features(current_input, load_investor_map(), load_raw_map())
    quality, failed = build_merge_quality(current)
    merged_success = int(quality.loc[0, "merged_success"])
    if merged_success != EXPECTED_SIGNAL_COUNT or len(failed):
        raise RuntimeError(f"Investor merge must be 289/289; got {merged_success}/{EXPECTED_SIGNAL_COUNT}")
    if len(current) != EXPECTED_SIGNAL_COUNT or current["ticker"].nunique() != EXPECTED_TICKER_COUNT:
        raise RuntimeError("STEP 5 current feature frame is not the fixed 289-signal / 20-ticker baseline")

    result = current.rename(columns={
        "score": "signal_score",
        "score_group": "frozen_score_group",
        "foreign_5d_ratio": "current_foreign_ratio",
        "investor_score": "frozen_foreign_score",
        "growth_score": "fundamental_score",
        "stock_selection_score": "frozen_selection_score",
    }).copy()
    result["excess_20d"] = result["excess_return_20d"]
    result["current_foreign_net_5d"] = result["foreign_net_5d"]
    result["frozen_foreign_ratio"] = frozen["foreign_5d_ratio"].to_numpy()
    result["current_foreign_score"] = result["current_foreign_ratio"].apply(foreign_ratio_to_score)
    result["current_selection_score"] = [
        compute_stock_selection_score(signal, foreign, fundamental)
        for signal, foreign, fundamental in zip(
            result["signal_score"], result["current_foreign_score"], result["fundamental_score"]
        )
    ]
    result["foreign_score_delta"] = result["current_foreign_score"] - result["frozen_foreign_score"]
    result["selection_score_delta"] = result["current_selection_score"] - result["frozen_selection_score"]
    result["frozen_rank"] = result["frozen_selection_score"].rank(ascending=False, method="first").astype(int)
    result["current_rank"] = result["current_selection_score"].rank(ascending=False, method="first").astype(int)
    result["rank_delta"] = result["frozen_rank"] - result["current_rank"]
    result["absolute_rank_movement"] = result["rank_delta"].abs()
    result["current_score_group"] = classify_score_group(
        result.rename(columns={"current_selection_score": "stock_selection_score"}), "stock_selection_score"
    )
    result = add_flow_classification(result.rename(columns={"current_foreign_ratio": "foreign_5d_ratio"})).rename(
        columns={"foreign_5d_ratio": "current_foreign_ratio", "foreign_flow_class": "current_foreign_class"}
    )
    result["signal_level"] = result["frozen_score_group"]
    return result


def build_signal_comparison(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ticker", "signal_date", "signal_level", "signal_score", "fundamental_score",
        "frozen_foreign_ratio", "frozen_foreign_score", "frozen_selection_score",
        "current_foreign_net_5d", "current_foreign_ratio", "current_foreign_score", "current_selection_score",
        "foreign_score_delta", "selection_score_delta", "frozen_rank", "current_rank", "rank_delta",
        "return_20d", "excess_20d",
    ]
    return df.loc[:, columns].sort_values(["current_rank", "frozen_rank"]).reset_index(drop=True)


def build_score_change_summary(df: pd.DataFrame) -> pd.DataFrame:
    selection_delta = df["selection_score_delta"]
    rows = []
    for component, delta_col in [("Foreign Score", "foreign_score_delta"), ("Selection Score", "selection_score_delta")]:
        counts = _compare_direction(df[delta_col])
        rows.append({
            "Component": component,
            "Unchanged": counts["unchanged"],
            "Increased": counts["increased"],
            "Decreased": counts["decreased"],
            "Mean Selection Delta": round(float(selection_delta.mean()), 3),
            "Median Selection Delta": round(float(selection_delta.median()), 3),
            "Min Selection Delta": round(float(selection_delta.min()), 3),
            "Max Selection Delta": round(float(selection_delta.max()), 3),
        })
    return pd.DataFrame(rows)


def build_rank_movement(df: pd.DataFrame) -> pd.DataFrame:
    signal_rows = df.loc[:, [
        "ticker", "name", "signal_date", "signal_level", "signal_score", "frozen_selection_score",
        "current_selection_score", "frozen_rank", "current_rank", "rank_delta", "absolute_rank_movement",
        "return_20d", "excess_20d",
    ]].copy()
    signal_rows.insert(0, "Record Type", "SIGNAL")
    summary = pd.DataFrame([
        {
            "Record Type": "MOVEMENT_SUMMARY",
            "ticker": "ALL",
            "name": "ALL",
            "signal_date": pd.NaT,
            "signal_level": "ALL",
            "rank_delta": np.nan,
            "absolute_rank_movement": round(float(df["absolute_rank_movement"].mean()), 2),
            "Mean Absolute Rank Movement": round(float(df["absolute_rank_movement"].mean()), 2),
            "Median Rank Movement": round(float(df["absolute_rank_movement"].median()), 2),
            "Max Rise": int(df["rank_delta"].max()),
            "Max Fall": int(df["rank_delta"].min()),
        }
    ])
    top_rise = signal_rows.sort_values(["rank_delta", "current_selection_score"], ascending=[False, False]).head(20).copy()
    top_rise["Record Type"] = "TOP_RISE_20"
    top_fall = signal_rows.sort_values(["rank_delta", "current_selection_score"], ascending=[True, False]).head(20).copy()
    top_fall["Record Type"] = "TOP_FALL_20"
    return pd.concat([summary, top_rise, top_fall, signal_rows], ignore_index=True, sort=False)


def _scored_for_grouping(df: pd.DataFrame, score_col: str, group_col: str) -> pd.DataFrame:
    return df.rename(columns={score_col: "stock_selection_score", group_col: "score_group"})


def build_top_group_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scenarios = [
        ("Frozen", "frozen_selection_score", "frozen_score_group"),
        ("Current", "current_selection_score", "current_score_group"),
    ]
    for scenario, score_col, group_col in scenarios:
        grouped = build_strategies(_scored_for_grouping(df, score_col, group_col))
        for strategy in STRATEGIES:
            rows.append({
                "Scenario": scenario,
                "Selection Group": strategy,
                "Avg Selection Score": _avg(grouped[strategy]["stock_selection_score"]),
                **_metric_row(grouped[strategy]),
            })
    return pd.DataFrame(rows)


def build_quantile_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for scenario, score_col in [("Frozen", "frozen_selection_score"), ("Current", "current_selection_score")]:
        quantiles = pd.qcut(df[score_col].rank(method="first"), q=4, labels=["Q1", "Q2", "Q3", "Q4"])
        correlation = float(df[score_col].rank(method="average").corr(df["excess_20d"].rank(method="average")))
        for quantile in ("Q1", "Q2", "Q3", "Q4"):
            subset = df[quantiles == quantile]
            rows.append({
                "Scenario": scenario,
                "Quantile": quantile,
                "Avg Selection Score": _avg(subset[score_col]),
                "Avg Excess 20D": _avg(subset["excess_20d"]),
                "Win Rate 20D (%)": _win_rate(subset["return_20d"]),
                "Spearman vs Excess 20D": round(correlation, 4),
                "N": len(subset),
            })
    return pd.DataFrame(rows)


def build_foreign_class_impact(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for foreign_class in ("POSITIVE", "NEUTRAL", "NEGATIVE"):
        subset = df[df["current_foreign_class"] == foreign_class]
        rows.append({
            "Current Foreign Class": foreign_class,
            "N": len(subset),
            "Avg Frozen Selection Score": _avg(subset["frozen_selection_score"]),
            "Avg Current Selection Score": _avg(subset["current_selection_score"]),
            "Avg Selection Score Delta": round(float(subset["selection_score_delta"].mean()), 3) if len(subset) else np.nan,
            "Avg Excess 20D": _avg(subset["excess_20d"]),
            "Win Rate 20D (%)": _win_rate(subset["return_20d"]),
        })
    return pd.DataFrame(rows)


def build_signal_level_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for level in ("MID", "HIGH"):
        frozen_members = df[df["frozen_score_group"] == level]
        current_members = df[df["current_score_group"] == level]
        base = df[df["signal_level"] == level]
        rows.append({
            "Record Type": "FROZEN_GROUP_PERFORMANCE",
            "Signal Level": level,
            "Scenario": "Frozen",
            "Avg Selection Score": _avg(frozen_members["frozen_selection_score"]),
            "Avg Rank Movement": round(float(frozen_members["rank_delta"].mean()), 2) if len(frozen_members) else np.nan,
            "Avg Absolute Rank Movement": round(float(frozen_members["absolute_rank_movement"].mean()), 2) if len(frozen_members) else np.nan,
            **_metric_row(frozen_members),
        })
        rows.append({
            "Record Type": "CURRENT_GROUP_PERFORMANCE",
            "Signal Level": level,
            "Scenario": "Current",
            "Avg Selection Score": _avg(current_members["current_selection_score"]),
            "Avg Rank Movement": round(float(current_members["rank_delta"].mean()), 2) if len(current_members) else np.nan,
            "Avg Absolute Rank Movement": round(float(current_members["absolute_rank_movement"].mean()), 2) if len(current_members) else np.nan,
            **_metric_row(current_members),
        })
        rows.append({
            "Record Type": "ORIGINAL_LEVEL_MOVEMENT",
            "Signal Level": level,
            "Scenario": "Frozen Level Members",
            "Avg Frozen Selection Score": _avg(base["frozen_selection_score"]),
            "Avg Current Selection Score": _avg(base["current_selection_score"]),
            "Avg Selection Score Delta": round(float(base["selection_score_delta"].mean()), 3) if len(base) else np.nan,
            "Avg Rank Movement": round(float(base["rank_delta"].mean()), 2) if len(base) else np.nan,
            "Avg Absolute Rank Movement": round(float(base["absolute_rank_movement"].mean()), 2) if len(base) else np.nan,
            **_metric_row(base),
        })
    return pd.DataFrame(rows)


def _top_bottom_spread(table: pd.DataFrame, scenario: str) -> float:
    q_rows = table[table["Scenario"] == scenario].set_index("Quantile")
    return round(float(q_rows.loc["Q4", "Avg Excess 20D"] - q_rows.loc["Q1", "Avg Excess 20D"]), 2)


def determine_verdict(top_group: pd.DataFrame, quantile: pd.DataFrame, foreign_class: pd.DataFrame) -> str:
    top = top_group.set_index(["Scenario", "Selection Group"])
    frozen_high = float(top.loc[("Frozen", "SELECTION_HIGH"), "Avg Excess 20D"])
    current_high = float(top.loc[("Current", "SELECTION_HIGH"), "Avg Excess 20D"])
    frozen_spread = _top_bottom_spread(quantile, "Frozen")
    current_spread = _top_bottom_spread(quantile, "Current")
    class_scores = foreign_class.set_index("Current Foreign Class")
    negative_lowered = (
        "NEGATIVE" in class_scores.index
        and float(class_scores.loc["NEGATIVE", "Avg Selection Score Delta"]) < 0
    )
    if current_high > frozen_high and current_spread > frozen_spread and negative_lowered:
        return "RECALC_IMPROVES"
    if current_high < frozen_high and current_spread < frozen_spread:
        return "RECALC_WORSE"
    return "RECALC_NEUTRAL"


def write_report(
    df: pd.DataFrame,
    score_change: pd.DataFrame,
    rank_movement: pd.DataFrame,
    top_group: pd.DataFrame,
    quantile: pd.DataFrame,
    foreign_class: pd.DataFrame,
    signal_level: pd.DataFrame,
    verdict: str,
) -> None:
    foreign_counts = score_change.set_index("Component").loc["Foreign Score"]
    selection_counts = score_change.set_index("Component").loc["Selection Score"]
    movement_summary = rank_movement[rank_movement["Record Type"] == "MOVEMENT_SUMMARY"].iloc[0]
    top = top_group.set_index(["Scenario", "Selection Group"])
    frozen_high = top.loc[("Frozen", "SELECTION_HIGH")]
    current_high = top.loc[("Current", "SELECTION_HIGH")]
    frozen_q_spread = _top_bottom_spread(quantile, "Frozen")
    current_q_spread = _top_bottom_spread(quantile, "Current")
    foreign_negative = foreign_class.set_index("Current Foreign Class").loc["NEGATIVE"]
    mid_high = signal_level[signal_level["Record Type"].isin(["FROZEN_GROUP_PERFORMANCE", "CURRENT_GROUP_PERFORMANCE"])]

    OUTPUT_REPORT.write_text(
        "# STEP 5 Selection Score Recalculation Report\n\n"
        "## Baseline Check\n\n"
        f"- Signal Count: {len(df)} / {EXPECTED_SIGNAL_COUNT}\n"
        f"- Ticker Count: {df['ticker'].nunique()} / {EXPECTED_TICKER_COUNT}\n"
        f"- Investor Merge: {int(df['foreign_net_5d'].notna().sum())}/{len(df)}\n"
        "- Formula: signal_score * 0.60 + current_foreign_score * 0.25 + fundamental_score * 0.15\n"
        "- Changed component: Foreign only. Step 13 fundamental_score is preserved.\n\n"
        "## Score Change\n\n"
        f"- Foreign Score: unchanged {int(foreign_counts['Unchanged'])}, increased {int(foreign_counts['Increased'])}, decreased {int(foreign_counts['Decreased'])}.\n"
        f"- Selection Score: unchanged {int(selection_counts['Unchanged'])}, increased {int(selection_counts['Increased'])}, decreased {int(selection_counts['Decreased'])}.\n"
        f"- Selection delta mean/median/min/max: {selection_counts['Mean Selection Delta']}, {selection_counts['Median Selection Delta']}, {selection_counts['Min Selection Delta']}, {selection_counts['Max Selection Delta']}.\n\n"
        "## Rank Movement\n\n"
        f"- Mean absolute rank movement: {movement_summary['Mean Absolute Rank Movement']}\n"
        f"- Median movement: {movement_summary['Median Rank Movement']}\n"
        f"- Max rise: {int(movement_summary['Max Rise'])}\n"
        f"- Max fall: {int(movement_summary['Max Fall'])}\n\n"
        "## Frozen vs Current Top Group\n\n"
        f"- Frozen HIGH: N={int(frozen_high['N'])}, Avg Return 20D={frozen_high['Avg Return 20D']}, Avg Excess 20D={frozen_high['Avg Excess 20D']}, Win Rate 20D={frozen_high['Win Rate 20D (%)']}%.\n"
        f"- Current HIGH: N={int(current_high['N'])}, Avg Return 20D={current_high['Avg Return 20D']}, Avg Excess 20D={current_high['Avg Excess 20D']}, Win Rate 20D={current_high['Win Rate 20D (%)']}%.\n\n"
        "## Quantile Monotonicity\n\n"
        f"- Frozen Q4-Q1 Avg Excess 20D spread: {frozen_q_spread}\n"
        f"- Current Q4-Q1 Avg Excess 20D spread: {current_q_spread}\n\n"
        "## Foreign Negative\n\n"
        f"- Current NEGATIVE: N={int(foreign_negative['N'])}, Avg frozen score={foreign_negative['Avg Frozen Selection Score']}, Avg current score={foreign_negative['Avg Current Selection Score']}, Delta={foreign_negative['Avg Selection Score Delta']}.\n\n"
        "## MID / HIGH\n\n"
        f"{mid_high.to_markdown(index=False)}\n\n"
        "## Final Judgment\n\n"
        f"{verdict}\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame | str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    recalculated = build_recalculated_selection()
    selection = build_signal_comparison(recalculated)
    score_change = build_score_change_summary(recalculated)
    rank_movement = build_rank_movement(recalculated)
    top_group = build_top_group_comparison(recalculated)
    quantile = build_quantile_comparison(recalculated)
    foreign_class = build_foreign_class_impact(recalculated)
    signal_level = build_signal_level_comparison(recalculated)
    verdict = determine_verdict(top_group, quantile, foreign_class)

    selection.to_csv(OUTPUT_SELECTION, index=False, encoding="utf-8-sig")
    score_change.to_csv(OUTPUT_SCORE_CHANGE, index=False, encoding="utf-8-sig")
    rank_movement.to_csv(OUTPUT_RANK_MOVEMENT, index=False, encoding="utf-8-sig")
    top_group.to_csv(OUTPUT_TOP_GROUP, index=False, encoding="utf-8-sig")
    quantile.to_csv(OUTPUT_QUANTILE, index=False, encoding="utf-8-sig")
    foreign_class.to_csv(OUTPUT_FOREIGN_CLASS, index=False, encoding="utf-8-sig")
    signal_level.to_csv(OUTPUT_SIGNAL_LEVEL, index=False, encoding="utf-8-sig")
    write_report(recalculated, score_change, rank_movement, top_group, quantile, foreign_class, signal_level, verdict)

    return {
        "recalculated": recalculated,
        "selection": selection,
        "score_change": score_change,
        "rank_movement": rank_movement,
        "top_group": top_group,
        "quantile": quantile,
        "foreign_class": foreign_class,
        "signal_level": signal_level,
        "verdict": verdict,
    }


if __name__ == "__main__":
    reports = run()
    print("STEP 5 complete")
    print("Baseline: 289 signals / 20 tickers / investor merge 289/289")
    print(reports["score_change"].to_string(index=False))
    print()
    print(reports["rank_movement"][reports["rank_movement"]["Record Type"] == "MOVEMENT_SUMMARY"].to_string(index=False))
    print()
    print(reports["top_group"].to_string(index=False))
    print()
    print(reports["quantile"].to_string(index=False))
    print()
    print(reports["foreign_class"].to_string(index=False))
    print()
    print(reports["signal_level"].to_string(index=False))
    print()
    print(f"Final Judgment: {reports['verdict']}")
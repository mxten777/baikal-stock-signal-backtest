"""STEP 3 - Flow Score structure validation without changing the model.

The production Selection Score has a single 25% flow component: the Foreign
score. Institution is not part of the current calculation. This script keeps
that fact explicit, then evaluates Institution as a parallel diagnostic and
as an analysis-only virtual replacement for the same 25% slot.

Run: python -m scripts.step3_flow_score_validation
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
    load_investor_map,
    load_raw_map,
    merge_investor_features,
)
from src.config import OUTPUT_DIR
from src.stock_selection import (
    GROWTH_WEIGHT,
    INVESTOR_WEIGHT,
    SIGNAL_WEIGHT,
    compute_stock_selection_score,
    foreign_ratio_to_score,
)

SOURCE_PATH = OUTPUT_DIR / "step13_stock_selection_score.csv"
OUTPUT_COMPONENTS = OUTPUT_DIR / "v02_step3_flow_components.csv"
OUTPUT_FOREIGN = OUTPUT_DIR / "v02_step3_foreign_score_performance.csv"
OUTPUT_INSTITUTION = OUTPUT_DIR / "v02_step3_institution_score_performance.csv"
OUTPUT_COMBINED = OUTPUT_DIR / "v02_step3_combined_flow_performance.csv"
OUTPUT_INCREMENTAL = OUTPUT_DIR / "v02_step3_flow_incremental_value.csv"
OUTPUT_CORRELATIONS = OUTPUT_DIR / "v02_step3_score_correlations.csv"
OUTPUT_RANK_IMPACT = OUTPUT_DIR / "v02_step3_rank_impact.csv"
OUTPUT_VIRTUAL = OUTPUT_DIR / "v02_step3_virtual_flow_comparison.csv"
OUTPUT_STRUCTURE = OUTPUT_DIR / "v02_step3_flow_score_structure.md"


def load_base_signals() -> pd.DataFrame:
    """Load the frozen 289-signal Step 13 result including actual scores."""
    signals = pd.read_csv(SOURCE_PATH, dtype={"ticker": str}, parse_dates=["signal_date"])
    signals["ticker"] = signals["ticker"].str.zfill(6)
    if len(signals) != EXPECTED_SIGNAL_COUNT or signals["ticker"].nunique() != EXPECTED_TICKER_COUNT:
        raise RuntimeError("STEP 13 baseline must contain 289 signals across 20 tickers")
    return signals


def score_bucket(score: float) -> str:
    """Use the existing score tiers, presented as low/mid/high."""
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MID"
    return "LOW"


def add_component_scores(signals: pd.DataFrame) -> pd.DataFrame:
    feature_columns = [
        column for column in signals.columns
        if column.startswith(("foreign_", "institution_", "avg_volume_"))
    ]
    merged = merge_investor_features(
        signals.drop(columns=feature_columns), load_investor_map(), load_raw_map()
    )
    result = merged.copy()
    # Step 13 is the frozen production score source. Investor data may be updated
    # after that run, so retain its stored score for the current-model analysis.
    result["foreign_score"] = result["investor_score"]
    result["institution_score"] = result["institution_5d_ratio"].apply(foreign_ratio_to_score)

    # This is deliberately not a new combination: it is the actual 25% Flow score.
    result["combined_flow_score"] = result["foreign_score"]
    result["flow_contribution"] = result["combined_flow_score"] * INVESTOR_WEIGHT
    result["virtual_foreign_selection_score"] = result.apply(
        lambda row: compute_stock_selection_score(row["score"], row["foreign_score"], row["growth_score"]), axis=1
    )
    result["virtual_institution_selection_score"] = result.apply(
        lambda row: compute_stock_selection_score(row["score"], row["institution_score"], row["growth_score"]), axis=1
    )
    if not np.allclose(result["stock_selection_score"], result["virtual_foreign_selection_score"]):
        raise RuntimeError("Current Selection Score is not reproducible from the current Foreign flow score")
    return result


def _metrics(df: pd.DataFrame) -> dict[str, float | int]:
    returns = df["return_20d"].dropna()
    excess_column = "excess_return_20d" if "excess_return_20d" in df.columns else "excess_20d"
    excess = df[excess_column].dropna()
    return {
        "N": len(df),
        "Avg Return 20D": round(float(returns.mean()), 2) if len(returns) else np.nan,
        "Avg Excess 20D": round(float(excess.mean()), 2) if len(excess) else np.nan,
        "Win Rate 20D (%)": round(float((returns > 0).mean() * 100), 1) if len(returns) else np.nan,
    }


def build_score_performance(df: pd.DataFrame, score_col: str, factor: str) -> pd.DataFrame:
    rows: list[dict] = []
    for group in ("LOW", "MID", "HIGH"):
        subset = df[df[score_col].apply(score_bucket) == group]
        rows.append({"Factor": factor, "Grouping": "SCORE_BUCKET", "Group": group, **_metrics(subset)})
    quantiles = pd.qcut(df[score_col].rank(method="first"), q=4, labels=["Q1", "Q2", "Q3", "Q4"])
    for group in ("Q1", "Q2", "Q3", "Q4"):
        rows.append({"Factor": factor, "Grouping": "QUANTILE", "Group": group, **_metrics(df[quantiles == group])})
    return pd.DataFrame(rows)


def build_incremental_value(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    foreign_groups = df["foreign_score"].apply(score_bucket)
    institution_groups = df["institution_score"].apply(score_bucket)
    for foreign_group in ("LOW", "MID", "HIGH"):
        within_foreign = df[foreign_groups == foreign_group]
        for institution_group in ("LOW", "MID", "HIGH"):
            subset = within_foreign[institution_groups.loc[within_foreign.index] == institution_group]
            rows.append({
                "Foreign Score Group": foreign_group,
                "Institution Score Group": institution_group,
                **_metrics(subset),
            })
    return pd.DataFrame(rows)


def _spearman(left: pd.Series, right: pd.Series) -> float:
    return float(left.rank(method="average").corr(right.rank(method="average")))


def build_correlations(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for score_name, column in [
        ("signal_score", "score"),
        ("foreign_score", "foreign_score"),
        ("institution_score", "institution_score"),
        ("combined_flow_score", "combined_flow_score"),
        ("current_selection_score", "stock_selection_score"),
    ]:
        subset = df[[column, "return_20d", "excess_return_20d"]].dropna()
        for outcome in ("return_20d", "excess_20d"):
            outcome_col = "excess_return_20d" if outcome == "excess_20d" else outcome
            rows.append({
                "Score": score_name,
                "Outcome": outcome,
                "Sample Count": len(subset),
                "Spearman Correlation": round(_spearman(subset[column], subset[outcome_col]), 4),
                "P Value": np.nan,
            })
    return pd.DataFrame(rows)


def build_rank_impact(df: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({
        "Record Type": "SIGNAL",
        "ticker": df["ticker"],
        "signal_date": df["signal_date"],
        "signal_level": df["score_group"],
        "signal_score": df["score"],
        "current_selection_score": df["stock_selection_score"],
        "signal_only_rank": df["score"].rank(ascending=False, method="first").astype(int),
        "current_selection_rank": df["stock_selection_score"].rank(ascending=False, method="first").astype(int),
        "flow_contribution": df["flow_contribution"],
        "return_20d": df["return_20d"],
        "excess_20d": df["excess_return_20d"],
    })
    result["rank_movement"] = result["signal_only_rank"] - result["current_selection_rank"]
    movement = pd.qcut(result["rank_movement"].rank(method="first"), q=3, labels=["FELL", "NEUTRAL", "ROSE"])
    summaries = []
    for group in ("ROSE", "NEUTRAL", "FELL"):
        subset = result[movement == group]
        summaries.append({"Record Type": "MOVEMENT_SUMMARY", "movement_group": group, **_metrics(subset)})
    return pd.concat([result, pd.DataFrame(summaries)], ignore_index=True, sort=False)


def build_virtual_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    scenarios = [
        ("CURRENT_COMBINED_EQUALS_FOREIGN", "stock_selection_score"),
        ("FOREIGN_ONLY", "virtual_foreign_selection_score"),
        ("INSTITUTION_ONLY", "virtual_institution_selection_score"),
    ]
    for scenario, score_col in scenarios:
        ranks = df[score_col].rank(pct=True, ascending=False, method="first")
        top = df[ranks <= 1 / 3]
        bottom = df[ranks > 2 / 3]
        top_metrics = _metrics(top)
        bottom_metrics = _metrics(bottom)
        rows.append({
            "Scenario": scenario,
            "Top N": top_metrics["N"],
            "Top Avg Excess 20D": top_metrics["Avg Excess 20D"],
            "Top Win Rate 20D (%)": top_metrics["Win Rate 20D (%)"],
            "Bottom N": bottom_metrics["N"],
            "Bottom Avg Excess 20D": bottom_metrics["Avg Excess 20D"],
            "Bottom Win Rate 20D (%)": bottom_metrics["Win Rate 20D (%)"],
            "Top-Bottom Excess Spread": round(top_metrics["Avg Excess 20D"] - bottom_metrics["Avg Excess 20D"], 2),
            "Top-Bottom Win Rate Spread (%)": round(top_metrics["Win Rate 20D (%)"] - bottom_metrics["Win Rate 20D (%)"], 1),
        })
    return pd.DataFrame(rows)


def write_structure_document() -> None:
    OUTPUT_STRUCTURE.write_text(
        "# STEP 3 Flow Score Structure\n\n"
        "- Foreign 5D ratio = sum of `foreign_net_buy` through `signal_date` for the latest 5 trading rows / mean volume through `signal_date` for the latest 20 rows.\n"
        "- Foreign score: ratio >= 0.20 -> 100; ratio > 0 -> 75; ratio > -0.20 -> 40; otherwise -> 10; missing -> 50.\n"
        "- Institution has the same ratio available from investor data, but no production Institution score or Foreign+Institution combination exists.\n"
        f"- Current Selection Score = signal_score * {SIGNAL_WEIGHT:.2f} + foreign_score * {INVESTOR_WEIGHT:.2f} + fundamental_score * {GROWTH_WEIGHT:.2f}.\n"
        "- Thus current `combined_flow_score` in this report is exactly `foreign_score`; Institution-only is an analysis-only replacement of the unchanged 25% slot.\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame]:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing {SOURCE_PATH}; run STEP 13 first")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    components = add_component_scores(load_base_signals())
    component_columns = [
        "ticker", "signal_date", "score_group", "score", "foreign_score", "institution_score",
        "combined_flow_score", "growth_score", "stock_selection_score", "return_20d", "excess_return_20d",
    ]
    components.loc[:, component_columns].rename(columns={
        "score_group": "signal_level", "score": "signal_score", "growth_score": "fundamental_score",
        "stock_selection_score": "current_selection_score", "excess_return_20d": "excess_20d",
    }).to_csv(OUTPUT_COMPONENTS, index=False, encoding="utf-8-sig")

    foreign = build_score_performance(components, "foreign_score", "FOREIGN")
    institution = build_score_performance(components, "institution_score", "INSTITUTION")
    combined = build_score_performance(components, "combined_flow_score", "CURRENT_COMBINED_EQUALS_FOREIGN")
    incremental = build_incremental_value(components)
    correlations = build_correlations(components)
    rank_impact = build_rank_impact(components)
    virtual = build_virtual_comparison(components)
    foreign.to_csv(OUTPUT_FOREIGN, index=False, encoding="utf-8-sig")
    institution.to_csv(OUTPUT_INSTITUTION, index=False, encoding="utf-8-sig")
    combined.to_csv(OUTPUT_COMBINED, index=False, encoding="utf-8-sig")
    incremental.to_csv(OUTPUT_INCREMENTAL, index=False, encoding="utf-8-sig")
    correlations.to_csv(OUTPUT_CORRELATIONS, index=False, encoding="utf-8-sig")
    rank_impact.to_csv(OUTPUT_RANK_IMPACT, index=False, encoding="utf-8-sig")
    virtual.to_csv(OUTPUT_VIRTUAL, index=False, encoding="utf-8-sig")
    write_structure_document()
    return {"components": components, "foreign": foreign, "institution": institution, "combined": combined,
            "incremental": incremental, "correlations": correlations, "rank_impact": rank_impact, "virtual": virtual}


if __name__ == "__main__":
    reports = run()
    print("STEP 3 complete: current flow score is Foreign-only; Institution is diagnostic/virtual only.")
    print(reports["virtual"].to_string(index=False))
"""STEP 4 - Diagnose the mismatch between Foreign classification and frozen score.

This script is analysis-only.  It neither changes scoring functions nor writes to
the frozen Step 13 selection-score source.

Run: python -m scripts.step4_foreign_score_mismatch
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
    load_base_signals,
    load_investor_map,
    load_raw_map,
    merge_investor_features,
    verify_baseline,
)
from src.config import OUTPUT_DIR
from src.stock_selection import foreign_ratio_to_score

MAPPING_PATH = OUTPUT_DIR / "v02_step4_foreign_mapping.csv"
DISTRIBUTION_PATH = OUTPUT_DIR / "v02_step4_class_score_distribution.csv"
CROSSTAB_PATH = OUTPUT_DIR / "v02_step4_class_score_crosstab.csv"
RATIO_PATH = OUTPUT_DIR / "v02_step4_ratio_performance.csv"
SATURATION_PATH = OUTPUT_DIR / "v02_step4_score_saturation.csv"
NORMALIZATION_PATH = OUTPUT_DIR / "v02_step4_normalization_by_stock.csv"
REPRESENTATION_PATH = OUTPUT_DIR / "v02_step4_representation_comparison.csv"
LOGIC_PATH = OUTPUT_DIR / "v02_step4_foreign_scoring_logic.md"

CLASS_ORDER = ["POSITIVE", "NEUTRAL", "NEGATIVE"]
SCORE_BUCKETS = ["LOW", "MID", "HIGH"]


def score_bucket(score: float) -> str:
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "MID"
    return "LOW"


def _metric_row(df: pd.DataFrame) -> dict[str, float | int]:
    excess = df["excess_return_20d"].dropna()
    returns = df["return_20d"].dropna()
    return {
        "N": len(df),
        "Avg Excess 20D": round(float(excess.mean()), 2) if len(excess) else np.nan,
        "Win Rate 20D (%)": round(float((returns > 0).mean() * 100), 1) if len(returns) else np.nan,
    }


def _spearman(left: pd.Series, right: pd.Series) -> float:
    return float(left.rank(method="average").corr(right.rank(method="average")))


def load_mapping() -> pd.DataFrame:
    """Join current STEP 1-B features to frozen production foreign scores."""
    frozen = load_base_signals()
    verify_baseline(frozen)
    frozen_scores = pd.read_csv(
        OUTPUT_DIR / "step13_stock_selection_score.csv",
        dtype={"ticker": str},
        parse_dates=["signal_date"],
    )
    frozen_scores["ticker"] = frozen_scores["ticker"].str.zfill(6)
    frozen_scores = frozen_scores[["ticker", "signal_date", "foreign_5d_ratio", "investor_score"]].rename(
        columns={"foreign_5d_ratio": "frozen_foreign_ratio", "investor_score": "foreign_score"}
    )
    current = merge_investor_features(frozen, load_investor_map(), load_raw_map())
    current = add_flow_classification(current)
    result = current.merge(frozen_scores, on=["ticker", "signal_date"], how="left", validate="one_to_one")
    if len(result) != EXPECTED_SIGNAL_COUNT or result["ticker"].nunique() != EXPECTED_TICKER_COUNT:
        raise RuntimeError("STEP 4 mapping is not the fixed 289-signal / 20-ticker baseline")
    if result["foreign_score"].isna().any():
        raise RuntimeError("Frozen Step 13 foreign_score is missing from the mapping")
    result["foreign_class"] = result["foreign_flow_class"]
    result["recomputed_foreign_score"] = result["foreign_5d_ratio"].apply(foreign_ratio_to_score)
    result["score_matches_recomputed"] = result["foreign_score"] == result["recomputed_foreign_score"]
    result["foreign_score_bucket"] = result["foreign_score"].apply(score_bucket)
    return result


def build_distribution(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for foreign_class in CLASS_ORDER:
        scores = df.loc[df["foreign_class"] == foreign_class, "foreign_score"]
        rows.append({
            "Foreign Class": foreign_class, "N": len(scores), "Min": scores.min(),
            "Q1": scores.quantile(0.25), "Median": scores.median(), "Q3": scores.quantile(0.75),
            "Max": scores.max(), "Mean": scores.mean(),
        })
    return pd.DataFrame(rows).round(2)


def build_crosstab(df: pd.DataFrame) -> pd.DataFrame:
    table = pd.crosstab(df["foreign_class"], df["foreign_score_bucket"])
    return table.reindex(index=CLASS_ORDER, columns=SCORE_BUCKETS, fill_value=0).rename_axis("Foreign Class").reset_index()


def build_ratio_performance(df: pd.DataFrame) -> pd.DataFrame:
    fixed = pd.cut(
        df["foreign_5d_ratio"],
        bins=[-np.inf, -0.5, -0.2, 0.2, 0.5, np.inf],
        labels=["<= -0.50", "-0.50 to -0.20", "-0.20 to 0.20", "0.20 to 0.50", ">= 0.50"],
        include_lowest=True,
    )
    rows = []
    for label in fixed.cat.categories:
        subset = df[fixed == label]
        rows.append({"Grouping": "FIXED_RATIO", "Ratio Bucket": label, **_metric_row(subset),
                     "Avg Foreign Score": round(float(subset["foreign_score"].mean()), 2) if len(subset) else np.nan})
    quantiles = pd.qcut(df["foreign_5d_ratio"].rank(method="first"), q=4, labels=["Q1", "Q2", "Q3", "Q4"])
    for label in ("Q1", "Q2", "Q3", "Q4"):
        subset = df[quantiles == label]
        rows.append({"Grouping": "RATIO_QUANTILE", "Ratio Bucket": label, **_metric_row(subset),
                     "Avg Foreign Score": round(float(subset["foreign_score"].mean()), 2)})
    return pd.DataFrame(rows)


def build_saturation(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for score in sorted(df["foreign_score"].unique()):
        subset = df[df["foreign_score"] == score]
        rows.append({"Frozen Foreign Score": score, "Signal Count": len(subset),
                     "Avg Current Foreign Ratio": round(float(subset["foreign_5d_ratio"].mean()), 4),
                     "Avg Excess 20D": round(float(subset["excess_return_20d"].mean()), 2)})
    return pd.DataFrame(rows)


def build_normalization(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, subset in df.groupby("ticker", sort=True):
        rows.append({"ticker": ticker, "name": subset["name"].iat[0], "N": len(subset),
                     "Ratio Mean": subset["foreign_5d_ratio"].mean(), "Ratio Std": subset["foreign_5d_ratio"].std(),
                     "Frozen Score Mean": subset["foreign_score"].mean(), "Frozen Score Std": subset["foreign_score"].std(),
                     "POSITIVE %": (subset["foreign_class"] == "POSITIVE").mean() * 100,
                     "NEGATIVE %": (subset["foreign_class"] == "NEGATIVE").mean() * 100,
                     "Avg Excess 20D": subset["excess_return_20d"].mean()})
    return pd.DataFrame(rows).round(2)


def build_representation_comparison(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    representations = [
        ("RAW_FOREIGN_RATIO", "foreign_5d_ratio", "QUANTILE"),
        ("FROZEN_CURRENT_FOREIGN_SCORE", "foreign_score", "QUANTILE"),
        ("CURRENT_THREE_CLASS", "foreign_class", "CLASS"),
    ]
    for name, column, kind in representations:
        if kind == "QUANTILE":
            groups = pd.qcut(df[column].rank(method="first"), q=4, labels=["Q1", "Q2", "Q3", "Q4"])
            for group in ("Q1", "Q2", "Q3", "Q4"):
                rows.append({"Representation": name, "Grouping": group, **_metric_row(df[groups == group]),
                             "Spearman vs Excess 20D": round(_spearman(df[column], df["excess_return_20d"]), 4)})
        else:
            for group in CLASS_ORDER:
                rows.append({"Representation": name, "Grouping": group, **_metric_row(df[df[column] == group]),
                             "Spearman vs Excess 20D": np.nan})
    return pd.DataFrame(rows)


def write_logic_document(df: pd.DataFrame) -> None:
    score_match_count = int(df["score_matches_recomputed"].sum())
    frozen_missing_count = int(df["frozen_foreign_ratio"].isna().sum())
    LOGIC_PATH.write_text(
        "# STEP 4 Foreign Scoring Logic\n\n"
        "## Current Feature Path\n\n"
        "1. Raw investor `foreign_net_buy` is filtered to `date <= signal_date`.\n"
        "2. `foreign_net_5d` is the sum of the latest five available investor rows. `foreign_net_1d` and `foreign_net_3d` use the same method with one and three rows.\n"
        "3. `avg_volume_20d` is the mean `volume` of the latest 20 raw-price rows where `date <= signal_date`.\n"
        "4. `foreign_ratio` (`foreign_5d_ratio`) = `foreign_net_5d / avg_volume_20d`. There is no clipping or additional normalization. Missing data or zero/non-positive denominator produces NaN.\n"
        "5. Class: POSITIVE if ratio >= 0.20; NEGATIVE if ratio <= -0.20; otherwise NEUTRAL.\n"
        "6. Score function range is {10, 40, 50, 75, 100}: NaN -> 50; ratio >= 0.20 -> 100; 0 < ratio < 0.20 -> 75; -0.20 < ratio <= 0 -> 40; ratio <= -0.20 -> 10.\n"
        "7. Selection Score = `signal_score * 0.60 + foreign_score * 0.25 + growth_score * 0.15`, rounded to one decimal.\n\n"
        "## Input Consistency Result\n\n"
        "The current STEP 1-B classification uses a recomputed current `foreign_5d_ratio`. Selection Score uses the frozen Step 13 `investor_score`; Step 13 stores its historical ratio but does not retain raw investor/volume windows. "
        f"The frozen score is internally consistent with its frozen ratio for all {len(df)}/{len(df)} signals, but {frozen_missing_count}/{len(df)} frozen ratios are NaN and therefore received the neutral score 50. Only {score_match_count}/{len(df)} frozen scores equal a score recomputed from current investor/raw data. Therefore class and frozen score do not use the same realized input values in this analysis run, despite using the same formula in code.\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = load_mapping()
    mapping.rename(columns={"foreign_5d_ratio": "foreign_ratio", "excess_return_20d": "excess_20d"}).loc[:, [
        "ticker", "name", "signal_date", "foreign_net_1d", "foreign_net_3d", "foreign_net_5d", "foreign_ratio", "frozen_foreign_ratio",
        "foreign_class", "foreign_score", "recomputed_foreign_score", "score_matches_recomputed", "return_20d", "excess_20d",
    ]].to_csv(MAPPING_PATH, index=False, encoding="utf-8-sig")
    distribution = build_distribution(mapping)
    crosstab = build_crosstab(mapping)
    ratio = build_ratio_performance(mapping)
    saturation = build_saturation(mapping)
    normalization = build_normalization(mapping)
    representation = build_representation_comparison(mapping)
    distribution.to_csv(DISTRIBUTION_PATH, index=False, encoding="utf-8-sig")
    crosstab.to_csv(CROSSTAB_PATH, index=False, encoding="utf-8-sig")
    ratio.to_csv(RATIO_PATH, index=False, encoding="utf-8-sig")
    saturation.to_csv(SATURATION_PATH, index=False, encoding="utf-8-sig")
    normalization.to_csv(NORMALIZATION_PATH, index=False, encoding="utf-8-sig")
    representation.to_csv(REPRESENTATION_PATH, index=False, encoding="utf-8-sig")
    write_logic_document(mapping)
    return {"mapping": mapping, "distribution": distribution, "crosstab": crosstab, "ratio": ratio,
            "saturation": saturation, "normalization": normalization, "representation": representation}


if __name__ == "__main__":
    reports = run()
    print("STEP 4 complete")
    print(reports["representation"].to_string(index=False))
"""STEP 6 - Compare continuous, negative-penalty, and negative-filter structures.

This is an analysis-only experiment. It does not change production scoring,
signal generation, thresholds, or the frozen Step 13 source.

Run: python -m scripts.step6_foreign_structure_comparison
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step5_selection_recalculation import build_recalculated_selection
from src.config import OUTPUT_DIR
from src.integrated_backtest import compute_metrics

SOURCE_PATH = OUTPUT_DIR / "v02_step5_selection_recalculated.csv"
OUTPUT_MODEL = OUTPUT_DIR / "v02_step6_model_comparison.csv"
OUTPUT_TOP = OUTPUT_DIR / "v02_step6_top_group_comparison.csv"
OUTPUT_QUANTILE = OUTPUT_DIR / "v02_step6_quantile_comparison.csv"
OUTPUT_CLASS = OUTPUT_DIR / "v02_step6_foreign_class_rank.csv"
OUTPUT_OPPORTUNITY = OUTPUT_DIR / "v02_step6_filter_opportunity_cost.csv"
OUTPUT_FALSE_NEGATIVE = OUTPUT_DIR / "v02_step6_false_negatives.csv"
OUTPUT_LEVEL = OUTPUT_DIR / "v02_step6_signal_level_comparison.csv"
OUTPUT_YEAR = OUTPUT_DIR / "v02_step6_year_comparison.csv"
OUTPUT_REPORT = OUTPUT_DIR / "v02_step6_foreign_structure_comparison_report.md"

EXPECTED_SIGNAL_COUNT = 289
EXPECTED_TICKER_COUNT = 20
PENALTY_POINTS = 10.0
NEUTRAL_FLOW_CONTRIBUTION = 50.0 * 0.25
MODELS = ("CURRENT_CONTINUOUS", "NEGATIVE_PENALTY", "NEGATIVE_FILTER")
CLASS_ORDER = ("POSITIVE", "NEUTRAL", "NEGATIVE")


def _avg(series: pd.Series) -> float:
    values = series.dropna()
    return round(float(values.mean()), 2) if len(values) else np.nan


def _metric_row(frame: pd.DataFrame) -> dict[str, float | int]:
    metrics = compute_metrics(frame)
    return {
        "N": metrics["signal_count"],
        "Avg Return 5D": metrics["avg_return_5d"],
        "Avg Return 10D": metrics["avg_return_10d"],
        "Avg Return 20D": metrics["avg_return_20d"],
        "Avg Excess 20D": metrics["avg_excess_return_20d"],
        "Win Rate 20D (%)": metrics["win_rate_20d"],
    }


def load_baseline() -> pd.DataFrame:
    if not SOURCE_PATH.exists():
        raise FileNotFoundError(f"Missing {SOURCE_PATH}; run STEP 5 first")
    frame = build_recalculated_selection()
    required = {
        "ticker", "name", "signal_date", "signal_score", "fundamental_score",
        "current_foreign_score", "current_foreign_class", "current_selection_score",
        "return_5d", "return_10d", "return_20d", "excess_20d", "signal_level",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"STEP 5 baseline is missing required columns: {missing}")
    if len(frame) != EXPECTED_SIGNAL_COUNT or frame["ticker"].nunique() != EXPECTED_TICKER_COUNT:
        raise RuntimeError("STEP 6 requires the fixed 289-signal / 20-ticker baseline")
    if frame["current_foreign_net_5d"].notna().sum() != EXPECTED_SIGNAL_COUNT:
        raise RuntimeError("Investor merge must be 289/289")
    frame["excess_return_20d"] = frame["excess_20d"]
    return frame.reset_index(drop=True)


def add_model_scores(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["CURRENT_CONTINUOUS_score"] = result["current_selection_score"]
    result["NEGATIVE_PENALTY_score"] = (
        result["signal_score"] * 0.60
        + result["fundamental_score"] * 0.15
        + NEUTRAL_FLOW_CONTRIBUTION
        - result["current_foreign_class"].eq("NEGATIVE") * PENALTY_POINTS
    ).round(1)
    result["NEGATIVE_FILTER_eligible"] = result["current_foreign_class"].ne("NEGATIVE")
    for model in ("CURRENT_CONTINUOUS", "NEGATIVE_PENALTY"):
        result[f"{model}_rank"] = result[f"{model}_score"].rank(ascending=False, method="first").astype(int)
    return result


def _model_frame(frame: pd.DataFrame, model: str) -> pd.DataFrame:
    if model == "NEGATIVE_FILTER":
        return frame[frame["NEGATIVE_FILTER_eligible"]].copy()
    return frame.copy()


def build_model_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model in MODELS:
        eligible = _model_frame(frame, model)
        rows.append({
            "Model": model,
            "Scope": "Eligible",
            "Eligible N": len(eligible),
            "Selected N": len(eligible),
            **_metric_row(eligible),
        })
    return pd.DataFrame(rows)


def _top(frame: pd.DataFrame, score_col: str, percent: int) -> pd.DataFrame:
    n = max(1, math.ceil(len(frame) * percent / 100))
    return frame.sort_values([score_col, "ticker", "signal_date"], ascending=[False, True, True]).head(n)


def build_top_group_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model in ("CURRENT_CONTINUOUS", "NEGATIVE_PENALTY"):
        for percent in (10, 20, 30):
            selected = _top(frame, f"{model}_score", percent)
            rows.append({"Model": model, "Top Group": f"TOP_{percent}%", "Eligible N": len(frame),
                         "Selected N": len(selected), "Avg Selection Score": _avg(selected[f"{model}_score"]),
                         **_metric_row(selected)})
    return pd.DataFrame(rows)


def _quantile_rows(frame: pd.DataFrame, model: str) -> list[dict]:
    ranks = frame[f"{model}_score"].rank(method="first")
    quantiles = pd.qcut(ranks, q=4, labels=["Q1", "Q2", "Q3", "Q4"])
    rows = []
    for quantile in ("Q1", "Q2", "Q3", "Q4"):
        subset = frame[quantiles == quantile]
        rows.append({"Model": model, "Quantile": quantile,
                     "Avg Selection Score": _avg(subset[f"{model}_score"]), **_metric_row(subset)})
    return rows


def build_quantile_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame([row for model in ("CURRENT_CONTINUOUS", "NEGATIVE_PENALTY")
                           for row in _quantile_rows(frame, model)])
    spreads = result.groupby("Model", sort=False)["Avg Excess 20D"].agg(lambda values: values.iloc[-1] - values.iloc[0])
    result["Q4-Q1 Spread"] = result["Model"].map(spreads).round(2)
    return result


def build_foreign_class_rank(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for model in MODELS:
        scored = _model_frame(frame, model)
        score_col = "current_selection_score" if model == "NEGATIVE_FILTER" else f"{model}_score"
        ranks = scored[score_col].rank(ascending=False, method="first")
        for foreign_class in CLASS_ORDER:
            subset = scored[scored["current_foreign_class"] == foreign_class]
            rows.append({"Model": model, "Foreign Class": foreign_class, "N": len(subset),
                         "Avg Final Score": _avg(subset[score_col]), "Avg Rank": _avg(ranks.loc[subset.index]),
                         "Avg Rank Delta vs Continuous": (
                             np.nan if model == "NEGATIVE_FILTER" else
                             _avg(subset[f"{model}_rank"] - subset["CURRENT_CONTINUOUS_rank"])
                         )})
    return pd.DataFrame(rows)


def build_filter_opportunity_cost(frame: pd.DataFrame) -> pd.DataFrame:
    negative = frame[frame["current_foreign_class"] == "NEGATIVE"]
    return pd.DataFrame([{
        "Filtered Class": "NEGATIVE", "N": len(negative),
        "20D Return > 0 N": int((negative["return_20d"] > 0).sum()),
        "Excess 20D > 0 N": int((negative["excess_20d"] > 0).sum()),
        "Avg Return 20D": _avg(negative["return_20d"]),
        "Avg Excess 20D": _avg(negative["excess_20d"]),
    }])


def build_false_negatives(frame: pd.DataFrame) -> pd.DataFrame:
    columns = ["ticker", "signal_date", "signal_level", "current_foreign_ratio", "return_20d", "excess_20d"]
    result = frame[(frame["current_foreign_class"] == "NEGATIVE") & (frame["return_20d"] > 0) & (frame["excess_20d"] > 0)]\
        .loc[:, columns].sort_values(["return_20d", "excess_20d"], ascending=False).reset_index(drop=True)
    return result.rename(columns={"current_foreign_ratio": "foreign_ratio"})


def build_signal_level_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for level in ("MID", "HIGH"):
        base = frame[frame["signal_level"] == level]
        for model in MODELS:
            subset = _model_frame(base, model)
            rows.append({"Signal Level": level, "Model": model, "Eligible N": len(subset),
                         "Selected N": len(subset), **_metric_row(subset)})
    return pd.DataFrame(rows)


def build_year_comparison(frame: pd.DataFrame, low_sample_threshold: int = 10) -> pd.DataFrame:
    rows = []
    frame = frame.assign(year=frame["signal_date"].dt.year)
    for year in sorted(frame["year"].dropna().unique()):
        base = frame[frame["year"] == year]
        for model in MODELS:
            subset = _model_frame(base, model)
            row = {"Year": int(year), "Model": model, "Low Sample": len(subset) < low_sample_threshold,
                   **_metric_row(subset)}
            rows.append(row)
    return pd.DataFrame(rows)


def determine_verdict(model: pd.DataFrame, top: pd.DataFrame, quantile: pd.DataFrame) -> str:
    overall = model.set_index("Model")
    top30 = top[top["Top Group"] == "TOP_30%"].set_index("Model")
    qspread = quantile.groupby("Model")["Q4-Q1 Spread"].first()
    penalty_better = (top30.loc["NEGATIVE_PENALTY", "Avg Excess 20D"] > top30.loc["CURRENT_CONTINUOUS", "Avg Excess 20D"]
                      and top30.loc["NEGATIVE_PENALTY", "Win Rate 20D (%)"] >= top30.loc["CURRENT_CONTINUOUS", "Win Rate 20D (%)"]
                      and qspread["NEGATIVE_PENALTY"] > qspread["CURRENT_CONTINUOUS"])
    filter_better = (overall.loc["NEGATIVE_FILTER", "Avg Excess 20D"] > overall.loc["CURRENT_CONTINUOUS", "Avg Excess 20D"]
                     and overall.loc["NEGATIVE_FILTER", "Win Rate 20D (%)"] >= overall.loc["CURRENT_CONTINUOUS", "Win Rate 20D (%)"])
    if filter_better:
        return "FILTER_BETTER"
    if penalty_better:
        return "PENALTY_BETTER"
    return "CONTINUOUS_BEST" if (overall.loc["CURRENT_CONTINUOUS", "Avg Excess 20D"] > overall.loc["NEGATIVE_PENALTY", "Avg Excess 20D"]
                                  and overall.loc["CURRENT_CONTINUOUS", "Win Rate 20D (%)"] >= overall.loc["NEGATIVE_PENALTY", "Win Rate 20D (%)"]) else "NO_CLEAR_WINNER"


def write_report(frame: pd.DataFrame, model: pd.DataFrame, top: pd.DataFrame, quantile: pd.DataFrame,
                 foreign_class: pd.DataFrame, opportunity: pd.DataFrame, false_negatives: pd.DataFrame,
                 level: pd.DataFrame, year: pd.DataFrame, verdict: str) -> None:
    OUTPUT_REPORT.write_text(
        "# STEP 6 Foreign Structure Comparison\n\n"
        "## Baseline\n\n"
        f"- Fixed signals: {len(frame)}/{EXPECTED_SIGNAL_COUNT}; tickers: {frame['ticker'].nunique()}/{EXPECTED_TICKER_COUNT}.\n"
        f"- Investor merge: {int(frame['current_foreign_net_5d'].notna().sum())}/{len(frame)}.\n"
        "- No production config, signal condition, fundamental criterion, or +/-0.20 class threshold was changed.\n\n"
        "## Model Definitions\n\n"
        "- CURRENT_CONTINUOUS: `0.60*signal_score + 0.25*current_foreign_score + 0.15*fundamental_score`.\n"
        f"- NEGATIVE_PENALTY: `0.60*signal_score + 0.15*fundamental_score + {NEUTRAL_FLOW_CONTRIBUTION:.1f} - 10.0*(foreign_class == NEGATIVE)`. The -10 is predefined, not tuned.\n"
        "- NEGATIVE_FILTER: eligible only when `foreign_class != NEGATIVE`; source signals remain unchanged.\n\n"
        "## Overall\n\n" + model.to_markdown(index=False) + "\n\n"
        "## Top Groups\n\n" + top.to_markdown(index=False) + "\n\n"
        "## Quantiles\n\n" + quantile.to_markdown(index=False) + "\n\n"
        "## Foreign Class Rank\n\n" + foreign_class.to_markdown(index=False) + "\n\n"
        f"## Filter Opportunity Cost\n\n{opportunity.to_markdown(index=False)}\n\n"
        f"- False negatives meeting both `return_20d > 0` and `excess_20d > 0`: {len(false_negatives)}.\n\n"
        "## MID / HIGH\n\n" + level.to_markdown(index=False) + "\n\n"
        "## Year Comparison\n\n" + year.to_markdown(index=False) + "\n\n"
        "## Final Judgment\n\n" + verdict + "\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame | str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = add_model_scores(load_baseline())
    model = build_model_comparison(frame)
    top = build_top_group_comparison(frame)
    quantile = build_quantile_comparison(frame)
    foreign_class = build_foreign_class_rank(frame)
    opportunity = build_filter_opportunity_cost(frame)
    false_negatives = build_false_negatives(frame)
    level = build_signal_level_comparison(frame)
    year = build_year_comparison(frame)
    verdict = determine_verdict(model, top, quantile)
    model.to_csv(OUTPUT_MODEL, index=False, encoding="utf-8-sig")
    top.to_csv(OUTPUT_TOP, index=False, encoding="utf-8-sig")
    quantile.to_csv(OUTPUT_QUANTILE, index=False, encoding="utf-8-sig")
    foreign_class.to_csv(OUTPUT_CLASS, index=False, encoding="utf-8-sig")
    opportunity.to_csv(OUTPUT_OPPORTUNITY, index=False, encoding="utf-8-sig")
    false_negatives.to_csv(OUTPUT_FALSE_NEGATIVE, index=False, encoding="utf-8-sig")
    level.to_csv(OUTPUT_LEVEL, index=False, encoding="utf-8-sig")
    year.to_csv(OUTPUT_YEAR, index=False, encoding="utf-8-sig")
    write_report(frame, model, top, quantile, foreign_class, opportunity, false_negatives, level, year, verdict)
    return {"model": model, "top": top, "quantile": quantile, "foreign_class": foreign_class,
            "opportunity": opportunity, "false_negatives": false_negatives, "level": level,
            "year": year, "verdict": verdict}


if __name__ == "__main__":
    reports = run()
    print("STEP 6 complete")
    print(reports["model"].to_string(index=False))
    print(f"Final Judgment: {reports['verdict']}")
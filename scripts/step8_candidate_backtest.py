"""STEP 8 - Integrated v0.1 baseline vs v0.2 candidate backtest.

Analysis-only. The frozen 289-signal population and existing selection score
are unchanged; the candidate only excludes Foreign NEGATIVE signals.

Run: python -m scripts.step8_candidate_backtest
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.step5_selection_recalculation import build_recalculated_selection
from src.config import OUTPUT_DIR

EXPECTED_SIGNAL_COUNT = 289
EXPECTED_NEGATIVE_COUNT = 37
EXPECTED_ELIGIBLE_COUNT = 252
LOW_SAMPLE_THRESHOLD = 10
PARTIAL_YEARS = {2023, 2026}

OUTPUT_OVERALL = OUTPUT_DIR / "v02_step8_candidate_overall.csv"
OUTPUT_LEVEL = OUTPUT_DIR / "v02_step8_candidate_by_signal_level.csv"
OUTPUT_STOCK = OUTPUT_DIR / "v02_step8_candidate_by_stock.csv"
OUTPUT_YEAR = OUTPUT_DIR / "v02_step8_candidate_by_year.csv"
OUTPUT_RISK = OUTPUT_DIR / "v02_step8_candidate_risk.csv"
OUTPUT_TAIL = OUTPUT_DIR / "v02_step8_candidate_tail_risk.csv"
OUTPUT_OPPORTUNITY = OUTPUT_DIR / "v02_step8_filtered_opportunity_cost.csv"
OUTPUT_SUMMARY = OUTPUT_DIR / "v02_step8_baseline_candidate_summary.csv"
OUTPUT_REPORT = OUTPUT_DIR / "v02_step8_candidate_backtest_report.md"


def _avg(series: pd.Series) -> float:
    values = series.dropna()
    return round(float(values.mean()), 2) if len(values) else np.nan


def _win_rate(series: pd.Series) -> float:
    values = series.dropna()
    return round(float((values > 0).mean() * 100), 1) if len(values) else np.nan


def _metrics(frame: pd.DataFrame) -> dict[str, float | int]:
    return {
        "Signal N": len(frame),
        "Avg Return 5D": _avg(frame["return_5d"]),
        "Avg Return 10D": _avg(frame["return_10d"]),
        "Avg Return 20D": _avg(frame["return_20d"]),
        "Win Rate 5D (%)": _win_rate(frame["return_5d"]),
        "Win Rate 10D (%)": _win_rate(frame["return_10d"]),
        "Win Rate 20D (%)": _win_rate(frame["return_20d"]),
        "Avg Excess 5D": _avg(frame["excess_return_5d"]),
        "Avg Excess 10D": _avg(frame["excess_return_10d"]),
        "Avg Excess 20D": _avg(frame["excess_return_20d"]),
    }


def _difference(candidate: dict, baseline: dict) -> dict:
    result = {}
    for key, value in candidate.items():
        base = baseline[key]
        if isinstance(value, (int, float, np.integer, np.floating)) and isinstance(base, (int, float, np.integer, np.floating)):
            result[key] = round(value - base, 2) if pd.notna(value) and pd.notna(base) else np.nan
        else:
            result[key] = np.nan
    return result


def load_baseline() -> pd.DataFrame:
    frame = build_recalculated_selection().copy()
    negative = frame["current_foreign_class"].eq("NEGATIVE")
    if len(frame) != EXPECTED_SIGNAL_COUNT or int(negative.sum()) != EXPECTED_NEGATIVE_COUNT:
        raise RuntimeError(
            f"STEP 8 signal count mismatch: baseline={len(frame)} (expected {EXPECTED_SIGNAL_COUNT}), "
            f"filtered={int(negative.sum())} (expected {EXPECTED_NEGATIVE_COUNT})"
        )
    frame["year"] = pd.to_datetime(frame["signal_date"]).dt.year
    frame["is_negative"] = negative
    frame["is_eligible"] = ~negative
    return frame.sort_values(["signal_date", "ticker"], kind="stable").reset_index(drop=True)


def build_overall_table(frame: pd.DataFrame) -> pd.DataFrame:
    baseline = _metrics(frame)
    candidate = _metrics(frame[frame["is_eligible"]])
    return pd.DataFrame([
        {"Strategy": "BASELINE", **baseline},
        {"Strategy": "CANDIDATE", **candidate},
        {"Strategy": "CANDIDATE - BASELINE", **_difference(candidate, baseline)},
    ])


def _comparison_rows(frame: pd.DataFrame, groups: list[tuple[str, pd.DataFrame]]) -> pd.DataFrame:
    rows = []
    for label, group in groups:
        base = _metrics(group)
        candidate = _metrics(group[group["is_eligible"]])
        rows.extend([
            {"Group": label, "Strategy": "BASELINE", **base},
            {"Group": label, "Strategy": "CANDIDATE", **candidate},
            {"Group": label, "Strategy": "CANDIDATE - BASELINE", **_difference(candidate, base)},
        ])
    return pd.DataFrame(rows)


def build_signal_level_table(frame: pd.DataFrame) -> pd.DataFrame:
    groups = [(level, frame[frame["signal_level"] == level]) for level in ("MID", "HIGH")]
    return _comparison_rows(frame, groups)


def build_stock_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, group in frame.groupby("ticker", sort=True):
        base = _metrics(group)
        candidate = _metrics(group[group["is_eligible"]])
        rows.append({
            "ticker": ticker, "name": group["name"].iloc[0],
            "Baseline N": base["Signal N"], "Candidate N": candidate["Signal N"],
            "Filtered N": int(group["is_negative"].sum()),
            "Baseline Excess 20D": base["Avg Excess 20D"],
            "Candidate Excess 20D": candidate["Avg Excess 20D"],
            "Improvement": _difference(candidate, base)["Avg Excess 20D"],
            "Baseline Win Rate 20D (%)": base["Win Rate 20D (%)"],
            "Candidate Win Rate 20D (%)": candidate["Win Rate 20D (%)"],
            "Low Sample": len(group) < LOW_SAMPLE_THRESHOLD or candidate["Signal N"] < LOW_SAMPLE_THRESHOLD,
        })
    result = pd.DataFrame(rows)
    result["Improvement Direction"] = np.where(result["Improvement"] > 0, "IMPROVED", np.where(result["Improvement"] < 0, "WORSENED", "UNCHANGED"))
    return result


def build_year_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, group in frame.groupby("year", sort=True):
        base = _metrics(group)
        candidate = _metrics(group[group["is_eligible"]])
        rows.append({
            "Year": int(year), "Baseline N": base["Signal N"], "Candidate N": candidate["Signal N"],
            "Baseline Avg Excess 20D": base["Avg Excess 20D"], "Candidate Avg Excess 20D": candidate["Avg Excess 20D"],
            "Improvement": _difference(candidate, base)["Avg Excess 20D"],
            "Baseline Win Rate 20D (%)": base["Win Rate 20D (%)"], "Candidate Win Rate 20D (%)": candidate["Win Rate 20D (%)"],
            "Low Sample": year in PARTIAL_YEARS or len(group) < LOW_SAMPLE_THRESHOLD or candidate["Signal N"] < LOW_SAMPLE_THRESHOLD,
        })
    return pd.DataFrame(rows)


def build_risk_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy, group in (("BASELINE", frame), ("CANDIDATE", frame[frame["is_eligible"]])):
        dd = group["max_drawdown_20d"].dropna()
        returns = group["return_20d"].dropna()
        rows.append({
            "Strategy": strategy, "Signal N": len(group),
            "Avg Max Drawdown 20D": _avg(dd), "Max Drawdown 20D": round(float(dd.min()), 2) if len(dd) else np.nan,
            "Negative Return Frequency (%)": round(float((returns <= 0).mean() * 100), 1) if len(returns) else np.nan,
            "Downside Return Average 20D": _avg(returns[returns < 0]),
        })
    baseline, candidate = rows
    rows.append({"Strategy": "CANDIDATE - BASELINE", **_difference({k: v for k, v in candidate.items() if k != "Strategy"}, {k: v for k, v in baseline.items() if k != "Strategy"})})
    return pd.DataFrame(rows)


def build_tail_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy, group in (("BASELINE", frame), ("CANDIDATE", frame[frame["is_eligible"]])):
        values = group["return_20d"].dropna()
        rows.append({
            "Strategy": strategy, "Signal N": len(values), "Worst Return 20D": values.min() if len(values) else np.nan,
            "5th Percentile": values.quantile(.05) if len(values) else np.nan, "10th Percentile": values.quantile(.10) if len(values) else np.nan,
            "Median": values.median() if len(values) else np.nan, "90th Percentile": values.quantile(.90) if len(values) else np.nan,
            "Best Return 20D": values.max() if len(values) else np.nan,
            "Return <= -5% (%)": (values <= -5).mean() * 100 if len(values) else np.nan,
            "Return <= -10% (%)": (values <= -10).mean() * 100 if len(values) else np.nan,
        })
    baseline, candidate = rows
    rows.append({"Strategy": "CANDIDATE - BASELINE", **_difference(candidate, baseline)})
    return pd.DataFrame(rows).round(2)


def build_opportunity_cost_table(frame: pd.DataFrame) -> pd.DataFrame:
    excluded = frame[frame["is_negative"]]
    values = excluded["return_20d"].dropna()
    excess = excluded["excess_return_20d"].dropna()
    return pd.DataFrame([{
        "Filtered N": len(excluded), "Avg Return 20D": _avg(values), "Avg Excess 20D": _avg(excess),
        "Win Rate 20D (%)": _win_rate(values), "Positive Return N": int((values > 0).sum()),
        "Positive Excess N": int((excess > 0).sum()), "Low Sample": len(excluded) < LOW_SAMPLE_THRESHOLD,
    }])


def build_summary_table(frame: pd.DataFrame) -> pd.DataFrame:
    baseline = _metrics(frame)
    candidate = _metrics(frame[frame["is_eligible"]])
    mid = _comparison_rows(frame, [("MID", frame[frame["signal_level"] == "MID"])])
    high = _comparison_rows(frame, [("HIGH", frame[frame["signal_level"] == "HIGH"])])
    def value(table: pd.DataFrame, strategy: str, column: str) -> float:
        return table.loc[table["Strategy"].eq(strategy), column].iloc[0]
    rows = []
    for label, base, cand in (
        ("Signal Count", baseline["Signal N"], candidate["Signal N"]),
        ("Avg Return 20D", baseline["Avg Return 20D"], candidate["Avg Return 20D"]),
        ("Avg Excess 20D", baseline["Avg Excess 20D"], candidate["Avg Excess 20D"]),
        ("Win Rate 20D", baseline["Win Rate 20D (%)"], candidate["Win Rate 20D (%)"]),
        ("MID Excess 20D", value(mid, "BASELINE", "Avg Excess 20D"), value(mid, "CANDIDATE", "Avg Excess 20D")),
        ("HIGH Excess 20D", value(high, "BASELINE", "Avg Excess 20D"), value(high, "CANDIDATE", "Avg Excess 20D")),
    ):
        rows.append({"Metric": label, "Baseline": base, "Candidate": cand, "Difference": round(cand - base, 2)})
    tail = build_tail_table(frame)
    for label, column in (("worst 20D return", "Worst Return 20D"), ("5th percentile", "5th Percentile"), ("<= -5% loss rate", "Return <= -5% (%)"), ("<= -10% loss rate", "Return <= -10% (%)")):
        base = tail.loc[tail["Strategy"].eq("BASELINE"), column].iloc[0]
        cand = tail.loc[tail["Strategy"].eq("CANDIDATE"), column].iloc[0]
        rows.append({"Metric": label, "Baseline": base, "Candidate": cand, "Difference": round(cand - base, 2)})
    return pd.DataFrame(rows)


def _write_report(frame: pd.DataFrame, tables: dict[str, pd.DataFrame], verdict: str) -> None:
    stock = tables["stock"]
    improved = int((stock["Improvement"] > 0).sum())
    worsened = int((stock["Improvement"] < 0).sum())
    risk_available = "Max Drawdown 20D" in tables["risk"].columns
    OUTPUT_REPORT.write_text(
        "# STEP 8 Foreign NEGATIVE Filter Candidate Backtest\n\n"
        "## A. Baseline / Candidate\n\n- Baseline: frozen v0.1 289 Valid Signals.\n- Candidate: same signals excluding Foreign NEGATIVE only; existing score structure unchanged.\n\n"
        f"## B. Signal Count Validation\n\n- Baseline: {len(frame)}; filtered NEGATIVE: {int(frame['is_negative'].sum())}; candidate eligible: {int(frame['is_eligible'].sum())}.\n\n"
        "## C. Overall Performance\n\n" + tables["overall"].to_markdown(index=False) + "\n\n"
        "## D. MID / HIGH Performance\n\n" + tables["level"].to_markdown(index=False) + "\n\n"
        f"## E. Stock Performance\n\n- Improved stocks: {improved}; worsened stocks: {worsened}. LOW SAMPLE is marked in the table.\n\n" + stock.to_markdown(index=False) + "\n\n"
        "## F. Year Performance\n\n" + tables["year"].to_markdown(index=False) + "\n\n"
        "## G. Risk / Drawdown\n\n" + tables["risk"].to_markdown(index=False) + "\n\n"
        + ("Existing signal-level max drawdown and downside metrics were used; no portfolio assumption was added.\n\n" if risk_available else "Existing risk metrics were unavailable.\n\n")
        + "## H. Tail Risk\n\n" + tables["tail"].to_markdown(index=False) + "\n\n"
        "## I. Opportunity Cost\n\n" + tables["opportunity"].to_markdown(index=False) + "\n\n"
        "## J. Baseline vs Candidate Summary\n\n" + tables["summary"].to_markdown(index=False) + "\n\n"
        "## K. Selection Score Handling\n\n- No new weights or ranking formula were introduced.\n\n"
        "## L. Generated Files\n\n- " + ", ".join(path.name for path in (OUTPUT_OVERALL, OUTPUT_LEVEL, OUTPUT_STOCK, OUTPUT_YEAR, OUTPUT_RISK, OUTPUT_TAIL, OUTPUT_OPPORTUNITY, OUTPUT_SUMMARY)) + "\n\n"
        "## M. Final Judgment\n\n" + verdict + "\n",
        encoding="utf-8",
    )


def run() -> dict[str, pd.DataFrame]:
    frame = load_baseline()
    tables = {
        "overall": build_overall_table(frame), "level": build_signal_level_table(frame), "stock": build_stock_table(frame),
        "year": build_year_table(frame), "risk": build_risk_table(frame), "tail": build_tail_table(frame),
        "opportunity": build_opportunity_cost_table(frame), "summary": build_summary_table(frame),
    }
    outputs = {"overall": OUTPUT_OVERALL, "level": OUTPUT_LEVEL, "stock": OUTPUT_STOCK, "year": OUTPUT_YEAR,
               "risk": OUTPUT_RISK, "tail": OUTPUT_TAIL, "opportunity": OUTPUT_OPPORTUNITY, "summary": OUTPUT_SUMMARY}
    for key, path in outputs.items():
        tables[key].to_csv(path, index=False, encoding="utf-8-sig")
    overall = tables["overall"]
    avg_excess_diff = overall.loc[overall["Strategy"].eq("CANDIDATE - BASELINE"), "Avg Excess 20D"].iloc[0]
    risk = tables["risk"].loc[tables["risk"]["Strategy"].eq("CANDIDATE - BASELINE")].iloc[0]
    verdict = "CANDIDATE_PASS" if avg_excess_diff > 0 and risk["Negative Return Frequency (%)"] <= 0 else "CANDIDATE_PARTIAL"
    _write_report(frame, tables, verdict)
    print(tables["summary"].to_string(index=False))
    print(f"Signal count: {len(frame)} baseline / {int(frame['is_negative'].sum())} filtered / {int(frame['is_eligible'].sum())} candidate")
    print(f"Improved stocks: {int((tables['stock']['Improvement'] > 0).sum())}; worsened stocks: {int((tables['stock']['Improvement'] < 0).sum())}")
    print(f"Final judgment: {verdict}")
    return tables


if __name__ == "__main__":
    run()
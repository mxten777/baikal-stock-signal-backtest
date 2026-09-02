from __future__ import annotations

import pandas as pd

from scripts.step5_selection_recalculation import (
    build_quantile_comparison,
    build_score_change_summary,
    build_top_group_comparison,
    determine_verdict,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["000001", "000002", "000003", "000004"],
        "name": ["A", "B", "C", "D"],
        "signal_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "signal_score": [70.0, 80.0, 60.0, 90.0],
        "frozen_foreign_score": [50.0, 50.0, 50.0, 50.0],
        "current_foreign_score": [100.0, 10.0, 50.0, 75.0],
        "fundamental_score": [50.0, 50.0, 50.0, 50.0],
        "frozen_selection_score": [62.0, 68.0, 56.0, 74.0],
        "current_selection_score": [74.5, 58.0, 56.0, 80.2],
        "selection_score_delta": [12.5, -10.0, 0.0, 6.2],
        "foreign_score_delta": [50.0, -40.0, 0.0, 25.0],
        "frozen_score_group": ["MID", "HIGH", "LOW", "HIGH"],
        "current_score_group": ["HIGH", "MID", "LOW", "HIGH"],
        "return_5d": [1.0, -1.0, 0.5, 2.0],
        "return_10d": [2.0, -2.0, 1.0, 3.0],
        "return_20d": [3.0, -3.0, 1.0, 4.0],
        "excess_return_20d": [2.0, -4.0, 0.0, 5.0],
        "excess_20d": [2.0, -4.0, 0.0, 5.0],
        "max_drawdown_20d": [-1.0, -5.0, -2.0, -1.5],
    })


def test_score_change_summary_counts_directions() -> None:
    report = build_score_change_summary(_frame()).set_index("Component")
    assert report.loc["Foreign Score", "Unchanged"] == 1
    assert report.loc["Foreign Score", "Increased"] == 2
    assert report.loc["Foreign Score", "Decreased"] == 1
    assert report.loc["Selection Score", "Median Selection Delta"] == 3.1


def test_top_group_comparison_uses_existing_selection_groups() -> None:
    report = build_top_group_comparison(_frame())
    assert set(report["Selection Group"]) == {"ALL_SIGNAL", "SELECTION_MID", "SELECTION_HIGH", "SELECTION_MID_HIGH"}
    assert set(report["Scenario"]) == {"Frozen", "Current"}


def test_quantile_comparison_builds_frozen_and_current_q1_to_q4() -> None:
    report = build_quantile_comparison(_frame())
    assert set(report["Scenario"]) == {"Frozen", "Current"}
    assert set(report["Quantile"]) == {"Q1", "Q2", "Q3", "Q4"}


def test_determine_verdict_marks_improvement_only_when_top_and_spread_improve() -> None:
    top_group = pd.DataFrame({
        "Scenario": ["Frozen", "Current"],
        "Selection Group": ["SELECTION_HIGH", "SELECTION_HIGH"],
        "Avg Excess 20D": [1.0, 2.0],
    })
    quantile = pd.DataFrame({
        "Scenario": ["Frozen", "Frozen", "Current", "Current"],
        "Quantile": ["Q1", "Q4", "Q1", "Q4"],
        "Avg Excess 20D": [0.0, 1.0, -1.0, 2.0],
    })
    foreign_class = pd.DataFrame({
        "Current Foreign Class": ["NEGATIVE"],
        "Avg Selection Score Delta": [-10.0],
    })
    assert determine_verdict(top_group, quantile, foreign_class) == "RECALC_IMPROVES"
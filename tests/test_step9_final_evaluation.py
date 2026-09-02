from __future__ import annotations

from scripts.step9_final_evaluation import (
    build_comparison,
    build_limitations,
    build_robustness_review,
)


def test_final_comparison_preserves_step8_overall_metrics() -> None:
    result = build_comparison()
    row = result[(result["Scope"] == "OVERALL") & (result["Strategy"] == "CANDIDATE") & (result["Metric"] == "Avg Excess 20D")].iloc[0]
    assert row["Value"] == 1.74


def test_robustness_has_no_leave_one_out_sign_flips() -> None:
    result = build_robustness_review()
    row = result[result["Dimension"] == "LEAVE_ONE_OUT"].iloc[0]
    assert row["Sign Flips"] == 0
    assert result.loc[result["Dimension"] == "WALK_FORWARD", "Periods"].iloc[0] == 4


def test_limitations_include_high_negative_excess_and_kosdaq() -> None:
    result = build_limitations()
    assert set(result["ID"]) >= {"A", "B", "C", "D"}
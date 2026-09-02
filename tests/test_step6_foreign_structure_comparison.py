from __future__ import annotations

import pandas as pd

from scripts.step6_foreign_structure_comparison import (
    add_model_scores,
    build_false_negatives,
    build_filter_opportunity_cost,
    build_top_group_comparison,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["000001", "000002", "000003", "000004"],
        "name": ["A", "B", "C", "D"],
        "signal_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "signal_score": [80.0, 70.0, 60.0, 90.0],
        "fundamental_score": [50.0] * 4,
        "current_foreign_score": [100.0, 10.0, 50.0, 75.0],
        "current_foreign_ratio": [0.5, -0.5, 0.0, -0.6],
        "current_foreign_class": ["POSITIVE", "NEGATIVE", "NEUTRAL", "NEGATIVE"],
        "current_selection_score": [74.5, 55.5, 61.0, 77.5],
        "current_foreign_net_5d": [1, 1, 1, 1],
        "return_5d": [1.0, -1.0, 0.5, 2.0],
        "return_10d": [2.0, -2.0, 1.0, 3.0],
        "return_20d": [3.0, -3.0, 1.0, 4.0],
        "excess_20d": [2.0, -4.0, 0.0, 5.0],
        "max_drawdown_20d": [-1.0, -5.0, -2.0, -1.5],
        "signal_level": ["MID", "HIGH", "MID", "HIGH"],
    })


def test_negative_penalty_is_fixed_ten_points_from_neutral_flow_baseline() -> None:
    result = add_model_scores(_frame())
    assert result.loc[1, "NEGATIVE_PENALTY_score"] == 52.0
    assert result.loc[0, "NEGATIVE_PENALTY_score"] == 68.0


def test_top_groups_use_ceiling_of_fixed_baseline_count() -> None:
    result = build_top_group_comparison(add_model_scores(_frame()))
    assert set(result["Selected N"]) == {1, 2}
    assert set(result["Top Group"]) == {"TOP_10%", "TOP_20%", "TOP_30%"}


def test_filter_opportunity_and_false_negative_outputs() -> None:
    frame = add_model_scores(_frame())
    opportunity = build_filter_opportunity_cost(frame).iloc[0]
    assert opportunity["N"] == 2
    assert opportunity["20D Return > 0 N"] == 1
    assert opportunity["Excess 20D > 0 N"] == 1
    false_negatives = build_false_negatives(frame)
    assert false_negatives["ticker"].tolist() == ["000004"]
    assert "foreign_ratio" in false_negatives.columns
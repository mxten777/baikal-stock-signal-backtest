from __future__ import annotations

import pandas as pd

from scripts.step8_candidate_backtest import (
    build_opportunity_cost_table,
    build_overall_table,
    build_risk_table,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["000001", "000001", "000002"],
        "name": ["A", "A", "B"],
        "signal_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2025-01-01"]),
        "signal_level": ["MID", "MID", "HIGH"],
        "current_foreign_class": ["POSITIVE", "NEGATIVE", "NEUTRAL"],
        "return_5d": [1, -2, 3], "return_10d": [2, -3, 4], "return_20d": [5, -6, 7],
        "excess_return_5d": [1, -2, 3], "excess_return_10d": [2, -3, 4], "excess_return_20d": [4, -5, 6],
        "max_drawdown_20d": [-1, -7, -2],
        "is_negative": [False, True, False], "is_eligible": [True, False, True],
        "year": [2024, 2024, 2025],
    })


def test_overall_has_exact_candidate_delta() -> None:
    result = build_overall_table(_frame())
    candidate = result[result["Strategy"] == "CANDIDATE"].iloc[0]
    difference = result[result["Strategy"] == "CANDIDATE - BASELINE"].iloc[0]
    assert candidate["Signal N"] == 2
    assert candidate["Avg Excess 20D"] == 5.0
    assert difference["Avg Excess 20D"] == 3.33


def test_opportunity_cost_counts_excluded_positive_cases() -> None:
    result = build_opportunity_cost_table(_frame()).iloc[0]
    assert result["Filtered N"] == 1
    assert result["Positive Return N"] == 0
    assert result["Positive Excess N"] == 0


def test_risk_uses_existing_signal_drawdown_and_negative_frequency() -> None:
    result = build_risk_table(_frame())
    candidate = result[result["Strategy"] == "CANDIDATE"].iloc[0]
    assert candidate["Max Drawdown 20D"] == -2
    assert candidate["Negative Return Frequency (%)"] == 0
from __future__ import annotations

import pandas as pd

from scripts.step7_foreign_filter_robustness import (
    build_by_stock_table,
    build_early_late_table,
    build_leave_one_out_table,
    build_walkforward_table,
    build_year_table,
)


def _frame() -> pd.DataFrame:
    dates = pd.to_datetime([
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
        "2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04",
    ])
    frame = pd.DataFrame({
        "ticker": ["000001", "000002", "000003", "000004", "000001", "000002", "000003", "000004"],
        "name": ["A", "B", "C", "D", "A", "B", "C", "D"],
        "signal_date": dates,
        "return_5d": [1.0, -1.0, 0.5, 2.0, 1.0, -1.0, 0.5, 2.0],
        "return_10d": [2.0, -2.0, 1.0, 3.0, 2.0, -2.0, 1.0, 3.0],
        "return_20d": [3.0, -3.0, 1.0, 4.0, 3.0, -3.0, 1.0, 4.0],
        "excess_return_5d": [1.0, -2.0, 0.0, 2.5, 1.0, -2.0, 0.0, 2.5],
        "excess_return_10d": [1.5, -3.0, 0.0, 3.5, 1.5, -3.0, 0.0, 3.5],
        "excess_return_20d": [2.0, -4.0, 0.0, 5.0, 2.0, -4.0, 0.0, 5.0],
        "excess_20d": [2.0, -4.0, 0.0, 5.0, 2.0, -4.0, 0.0, 5.0],
        "current_foreign_class": ["POSITIVE", "NEGATIVE", "NEUTRAL", "NEGATIVE"] * 2,
    })
    frame["year"] = frame["signal_date"].dt.year
    frame["is_negative"] = frame["current_foreign_class"] == "NEGATIVE"
    frame["is_eligible"] = ~frame["is_negative"]
    frame["is_false_negative"] = frame["is_negative"] & (frame["return_20d"] > 0) & (frame["excess_20d"] > 0)
    return frame


def test_year_table_filters_negative_and_improves_excess() -> None:
    result = build_year_table(_frame())
    assert set(result["Year"]) == {2024, 2025}
    row = result[result["Year"] == 2024].iloc[0]
    assert row["Negative N"] == 2
    assert row["Eligible N"] == 2
    assert row["Excess Improvement"] > 0


def test_early_late_split_is_time_ordered_and_roughly_equal() -> None:
    result = build_early_late_table(_frame())
    assert set(result["Period"]) == {"EARLY", "LATE"}
    early = result[result["Period"] == "EARLY"].iloc[0]
    late = result[result["Period"] == "LATE"].iloc[0]
    assert early["All N"] == 4
    assert late["All N"] == 4
    assert early["Start Date"] < late["Start Date"]


def test_walkforward_produces_requested_fold_count() -> None:
    result = build_walkforward_table(_frame(), n_folds=4)
    assert len(result) == 4
    assert result["All N"].sum() == 8


def test_by_stock_marks_no_negative_as_not_applicable() -> None:
    result = build_by_stock_table(_frame())
    row = result[result["ticker"] == "000001"].iloc[0]
    assert row["Negative N"] == 0
    assert row["After Avg Excess 20D"] == "N/A"
    assert row["Improvement"] == "N/A"


def test_leave_one_out_flags_sign_flip_when_removing_key_ticker() -> None:
    result = build_leave_one_out_table(_frame())
    assert set(result["Excluded Ticker"]) == {"000001", "000002", "000003", "000004"}
    assert "Sign Flipped" in result.columns

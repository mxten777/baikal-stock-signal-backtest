from __future__ import annotations

import pandas as pd

from scripts.step2_foreign_flow_stability import (
    _build_group_table,
    build_horizon_table,
    build_institution_reference,
    build_sensitivity_table,
    build_stock_table,
    build_year_table,
)


def _signals() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["000001", "000001", "000002", "000002", "000003", "000003"],
        "name": ["A", "A", "B", "B", "C", "C"],
        "signal_date": pd.to_datetime(["2023-12-01", "2023-12-05", "2024-01-01", "2024-01-02", "2025-01-01", "2025-01-02"]),
        "foreign_flow_class": ["POSITIVE", "NEGATIVE", "POSITIVE", "NEGATIVE", "NEUTRAL", "POSITIVE"],
        "Market": ["KOSPI", "KOSPI", "KOSDAQ", "KOSDAQ", "KOSPI", "KOSPI"],
        "Regime": ["UP", "UP", "DOWN", "DOWN", "UP", "UP"],
        "score_group": ["MID", "MID", "HIGH", "HIGH", "HIGH", "HIGH"],
        "return_5d": [2, -1, 3, 1, 0, 2], "return_10d": [3, -2, 4, 2, 0, 3], "return_20d": [4, -3, 5, 2, 0, 4],
        "excess_return_5d": [1, -2, 2, 0, 0, 1], "excess_return_10d": [2, -3, 3, 1, 0, 2], "excess_return_20d": [3, -4, 4, 1, 0, 3],
    })


def test_year_table_has_partial_year_and_difference():
    table = build_year_table(_signals())
    assert "PARTIAL YEAR" in table.loc[table["Year"] == 2023, "Low Sample"].iloc[0]
    assert table[(table["Year"] == 2023) & (table["Foreign Flow"] == "POSITIVE - NEGATIVE")].iloc[0]["Avg Excess 20D"] == 7.0


def test_group_table_includes_classes_and_low_sample_difference():
    table = _build_group_table(_signals(), "Market", ("KOSPI", "KOSDAQ"))
    assert set(table["Foreign Flow"]) == {"POSITIVE", "NEUTRAL", "NEGATIVE", "POSITIVE - NEGATIVE"}
    assert table.loc[table["Foreign Flow"] == "POSITIVE - NEGATIVE", "Low Sample"].notna().all()


def test_horizon_table_calculates_positive_negative_difference():
    table = build_horizon_table(_signals())
    assert list(table["Horizon"]) == ["5D", "10D", "20D"]
    assert table.loc[table["Horizon"] == "20D", "Avg Excess Difference"].iloc[0] == 4.83


def test_stock_and_sensitivity_tables_cover_exclusions():
    signals = _signals()
    stock_table = build_stock_table(signals)
    assert "LOW SAMPLE" in stock_table.loc[stock_table["ticker"] == "000001", "Low Sample"].iloc[0]
    sensitivity = build_sensitivity_table(signals, stock_table)
    assert len(sensitivity) == 3
    assert sensitivity.loc[0, "Scenario"] == "ALL"


def test_institution_reference_remains_limited_to_20_day_benchmark():
    data = _signals().assign(institution_flow_class=["POSITIVE", "NEGATIVE", "POSITIVE", "NEGATIVE", "NEUTRAL", "POSITIVE"])
    table = build_institution_reference(data)
    assert list(table["Institution Flow"]) == ["POSITIVE", "NEGATIVE", "POSITIVE - NEGATIVE"]
    assert set(table.columns) == {"Institution Flow", "N", "Avg Excess 20D", "Win Rate 20D (%)"}
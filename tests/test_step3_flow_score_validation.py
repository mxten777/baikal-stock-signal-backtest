from __future__ import annotations

import pandas as pd
import pytest

from scripts.step3_flow_score_validation import (
    _spearman,
    build_score_performance,
    build_virtual_comparison,
    score_bucket,
)


def _components() -> pd.DataFrame:
    return pd.DataFrame({
        "foreign_score": [10.0, 40.0, 50.0, 75.0, 100.0, 100.0],
        "institution_score": [100.0, 75.0, 50.0, 40.0, 10.0, 10.0],
        "combined_flow_score": [10.0, 40.0, 50.0, 75.0, 100.0, 100.0],
        "score": [50.0, 55.0, 60.0, 65.0, 70.0, 75.0],
        "growth_score": [50.0] * 6,
        "stock_selection_score": [45.0, 50.5, 55.5, 60.5, 65.5, 68.5],
        "virtual_foreign_selection_score": [45.0, 50.5, 55.5, 60.5, 65.5, 68.5],
        "virtual_institution_selection_score": [67.5, 59.25, 55.5, 51.75, 43.0, 43.0],
        "return_20d": [-1.0, 0.5, 1.0, 2.0, 3.0, 4.0],
        "excess_return_20d": [-2.0, -0.5, 0.0, 1.0, 2.0, 3.0],
    })


@pytest.mark.parametrize(("score", "expected"), [(10, "LOW"), (40, "LOW"), (50, "MID"), (75, "HIGH")])
def test_score_bucket_uses_existing_score_tiers(score: float, expected: str) -> None:
    assert score_bucket(score) == expected


def test_spearman_is_one_for_monotonic_values() -> None:
    assert _spearman(pd.Series([1, 2, 3]), pd.Series([10, 20, 30])) == pytest.approx(1.0)


def test_score_performance_contains_buckets_and_quantiles() -> None:
    report = build_score_performance(_components(), "foreign_score", "FOREIGN")
    assert set(report["Group"]) == {"LOW", "MID", "HIGH", "Q1", "Q2", "Q3", "Q4"}


def test_current_combined_and_foreign_only_are_identical() -> None:
    report = build_virtual_comparison(_components()).set_index("Scenario")
    assert report.loc["CURRENT_COMBINED_EQUALS_FOREIGN", "Top-Bottom Excess Spread"] == report.loc[
        "FOREIGN_ONLY", "Top-Bottom Excess Spread"
    ]
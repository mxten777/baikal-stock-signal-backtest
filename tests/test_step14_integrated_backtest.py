from __future__ import annotations

import pandas as pd
import pytest

from src.integrated_backtest import (
    add_year_column,
    build_strategies,
    build_strategy_table,
    build_ticker_table,
    build_yearly_table,
    compute_metrics,
    ticker_concentration,
    year_concentration,
)


def _sample_scored() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ["005930", "005930", "000660", "000660", "005930"],
        "name": ["삼성전자", "삼성전자", "SK하이닉스", "SK하이닉스", "삼성전자"],
        "signal_date": pd.to_datetime([
            "2023-01-10", "2023-06-01", "2024-02-15", "2024-03-01", "2024-05-01",
        ]),
        "return_5d": [1.0, -2.0, 3.0, None, 2.0],
        "return_10d": [2.0, -1.0, 4.0, 1.0, 3.0],
        "return_20d": [5.0, -3.0, 6.0, 2.0, 4.0],
        "excess_return_20d": [1.0, -1.0, 2.0, 0.5, 1.5],
        "max_drawdown_20d": [-2.0, -8.0, -1.0, -3.0, -4.0],
        "score_group": ["LOW", "MID", "HIGH", "MID", "HIGH"],
    })


def test_build_strategies_partitions_by_score_group() -> None:
    scored = _sample_scored()
    strategies = build_strategies(scored)
    assert len(strategies["ALL_SIGNAL"]) == 5
    assert len(strategies["SELECTION_MID"]) == 2
    assert len(strategies["SELECTION_HIGH"]) == 2
    assert len(strategies["SELECTION_MID_HIGH"]) == 4


def test_compute_metrics_basic() -> None:
    df = pd.DataFrame({
        "return_5d": [1.0, -1.0],
        "return_10d": [2.0, -2.0],
        "return_20d": [3.0, -3.0],
        "excess_return_20d": [1.0, -1.0],
        "max_drawdown_20d": [-1.0, -5.0],
    })
    metrics = compute_metrics(df)
    assert metrics["signal_count"] == 2
    assert metrics["avg_return_20d"] == 0.0
    assert metrics["win_rate_20d"] == 50.0
    assert metrics["worst_max_drawdown_20d"] == -5.0


def test_compute_metrics_empty_returns_nan() -> None:
    metrics = compute_metrics(pd.DataFrame({
        "return_5d": [], "return_10d": [], "return_20d": [],
        "excess_return_20d": [], "max_drawdown_20d": [],
    }))
    assert metrics["signal_count"] == 0
    assert pd.isna(metrics["avg_return_20d"])


def test_build_strategy_table_has_all_four_strategies() -> None:
    table = build_strategy_table(_sample_scored())
    assert list(table["strategy"]) == [
        "ALL_SIGNAL", "SELECTION_MID", "SELECTION_HIGH", "SELECTION_MID_HIGH",
    ]


def test_build_ticker_table_sorted_by_signal_count() -> None:
    table = build_ticker_table(_sample_scored(), "ALL_SIGNAL")
    assert table.iloc[0]["ticker"] == "005930"
    assert table.iloc[0]["signal_count"] == 3


def test_add_year_column() -> None:
    result = add_year_column(_sample_scored())
    assert list(result["year"]) == [2023, 2023, 2024, 2024, 2024]


def test_build_yearly_table_groups_by_year() -> None:
    table = build_yearly_table(_sample_scored(), "ALL_SIGNAL")
    assert list(table["year"]) == [2023, 2024]
    assert table.iloc[0]["signal_count"] == 2
    assert table.iloc[1]["signal_count"] == 3


def test_ticker_concentration_identifies_top_ticker() -> None:
    result = ticker_concentration(_sample_scored(), "ALL_SIGNAL")
    assert result["top_ticker"] == "005930"
    assert result["top_ticker_share_pct"] == pytest.approx(60.0)


def test_ticker_concentration_empty_strategy_returns_nan() -> None:
    scored = _sample_scored()
    scored["score_group"] = "LOW"
    result = ticker_concentration(scored, "SELECTION_HIGH")
    assert result["top_ticker"] is None
    assert pd.isna(result["top_ticker_share_pct"])


def test_year_concentration_identifies_top_year() -> None:
    result = year_concentration(_sample_scored(), "ALL_SIGNAL")
    assert result["top_year"] == 2024
    assert result["top_year_share_pct"] == pytest.approx(60.0)

from __future__ import annotations

import pandas as pd
import pytest

from src.stock_selection import (
    add_stock_selection_score,
    classify_score_group,
    compute_foreign_5d_ratio,
    compute_stock_selection_score,
    foreign_ratio_to_score,
    growth_row_to_score,
)


def test_foreign_ratio_to_score_buckets() -> None:
    assert foreign_ratio_to_score(float("nan")) == 50.0
    assert foreign_ratio_to_score(0.25) == 100.0
    assert foreign_ratio_to_score(0.10) == 75.0
    assert foreign_ratio_to_score(-0.10) == 40.0
    assert foreign_ratio_to_score(-0.30) == 10.0


def test_compute_foreign_5d_ratio_uses_only_past_data() -> None:
    signals = pd.DataFrame({
        "ticker": ["005930"],
        "signal_date": pd.to_datetime(["2024-01-10"]),
    })
    investor_map = {
        "005930": pd.DataFrame({
            "date": pd.to_datetime(
                ["2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
            ),
            "foreign_net_buy": [100, 100, 100, 100, 999999],  # 01-11은 미래, 제외되어야 함
        }),
    }
    raw_map = {
        "005930": pd.DataFrame({
            "date": pd.to_datetime(["2024-01-08", "2024-01-09", "2024-01-10"]),
            "volume": [1000, 1000, 1000],
        }),
    }

    ratio = compute_foreign_5d_ratio(signals, investor_map, raw_map)
    # 미래(01-11) 데이터가 섞이지 않아야 함: foreign 5d sum = 100+100+100+100=400 (01-05,08,09,10)
    assert ratio.iloc[0] == pytest.approx(400 / 1000)


def test_compute_foreign_5d_ratio_missing_data_returns_nan() -> None:
    signals = pd.DataFrame({
        "ticker": ["999999"],
        "signal_date": pd.to_datetime(["2024-01-10"]),
    })
    ratio = compute_foreign_5d_ratio(signals, {}, {})
    assert pd.isna(ratio.iloc[0])


def test_growth_row_to_score_neutral_when_no_fundamental_match() -> None:
    row = pd.Series({"fundamental_report_period": None})
    assert growth_row_to_score(row) == 50.0


def test_growth_row_to_score_strong_growth() -> None:
    row = pd.Series({
        "fundamental_report_period": "2024-Q1",
        "revenue_yoy": 15.0,
        "oi_yoy_flag": "normal",
        "operating_income_yoy": 20.0,
        "ni_yoy_flag": "normal",
        "net_income_yoy": 5.0,
    })
    assert growth_row_to_score(row) == 100.0


def test_growth_row_to_score_no_growth() -> None:
    row = pd.Series({
        "fundamental_report_period": "2024-Q1",
        "revenue_yoy": -5.0,
        "oi_yoy_flag": "normal",
        "operating_income_yoy": -3.0,
        "ni_yoy_flag": "normal",
        "net_income_yoy": -1.0,
    })
    assert growth_row_to_score(row) == 35.0


def test_compute_stock_selection_score_weighted_sum() -> None:
    score = compute_stock_selection_score(80.0, 100.0, 60.0)
    expected = round(80.0 * 0.60 + 100.0 * 0.25 + 60.0 * 0.15, 1)
    assert score == expected


def test_add_stock_selection_score_neutral_when_missing_data() -> None:
    joined = pd.DataFrame({
        "score": [80.0],
        "foreign_5d_ratio": [float("nan")],
        "fundamental_report_period": [None],
    })
    result = add_stock_selection_score(joined)
    assert result.loc[0, "investor_score"] == 50.0
    assert result.loc[0, "growth_score"] == 50.0
    assert result.loc[0, "stock_selection_score"] == pytest.approx(80.0 * 0.60 + 50.0 * 0.25 + 50.0 * 0.15)


def test_classify_score_group_terciles() -> None:
    df = pd.DataFrame({"stock_selection_score": [10, 20, 30, 40, 50, 60]})
    labels = classify_score_group(df)
    assert list(labels) == ["LOW", "LOW", "MID", "MID", "HIGH", "HIGH"]


def test_growth_score_is_auxiliary_not_hard_filter() -> None:
    """실적 성장이 나쁘더라도 Signal 자체는 그대로 유지되고 점수만 낮아져야 한다 (탈락 없음)."""
    joined = pd.DataFrame({
        "score": [90.0],
        "foreign_5d_ratio": [float("nan")],
        "fundamental_report_period": ["2024-Q1"],
        "revenue_yoy": [-20.0],
        "oi_yoy_flag": ["normal"],
        "operating_income_yoy": [-10.0],
        "ni_yoy_flag": ["normal"],
        "net_income_yoy": [-5.0],
    })
    result = add_stock_selection_score(joined)
    assert len(result) == 1  # 행이 제거되지 않음
    assert result.loc[0, "growth_score"] == 35.0
    assert result.loc[0, "stock_selection_score"] > 0

from __future__ import annotations

import pandas as pd
import pytest

from scripts.step12_fundamental_signal_performance import build_group_table
from src.data_provider.dart_fundamental_provider import (
    compute_net_income_yoy,
    join_signals_step12,
)


def test_compute_net_income_yoy_flags_and_values() -> None:
    df = pd.DataFrame(
        {
            "ticker": ["005930"] * 8,
            "report_period": [
                "2023-Q1",
                "2023-Q2",
                "2023-Q3",
                "2023-Q4",
                "2024-Q1",
                "2024-Q2",
                "2024-Q3",
                "2024-Q4",
            ],
            "disclosure_date": pd.to_datetime(
                [
                    "2023-05-15",
                    "2023-08-14",
                    "2023-11-14",
                    "2024-03-15",
                    "2024-05-15",
                    "2024-08-14",
                    "2024-11-14",
                    "2025-03-15",
                ]
            ),
            "net_income": [100.0, -50.0, -40.0, 0.0, 120.0, 30.0, -20.0, 10.0],
            "revenue": [100.0] * 8,
            "operating_income": [10.0] * 8,
        }
    )

    result = compute_net_income_yoy(df)
    by_period = result.set_index("report_period")

    assert by_period.loc["2024-Q1", "ni_yoy_flag"] == "normal"
    assert by_period.loc["2024-Q1", "net_income_yoy"] == pytest.approx(20.0)

    assert by_period.loc["2024-Q2", "ni_yoy_flag"] == "turnaround"
    assert pd.isna(by_period.loc["2024-Q2", "net_income_yoy"])

    assert by_period.loc["2024-Q3", "ni_yoy_flag"] == "both_negative"
    assert by_period.loc["2024-Q3", "net_income_yoy"] == pytest.approx(-50.0)

    assert by_period.loc["2024-Q4", "ni_yoy_flag"] == "base_zero"
    assert pd.isna(by_period.loc["2024-Q4", "net_income_yoy"])


def test_join_signals_step12_uses_strict_pre_disclosure() -> None:
    signals = pd.DataFrame(
        {
            "ticker": ["005930", "005930"],
            "signal_date": pd.to_datetime(["2024-05-15", "2024-08-15"]),
            "signal_type": ["BUY_WATCH", "BUY_WATCH"],
        }
    )
    fundamentals = pd.DataFrame(
        {
            "ticker": ["005930", "005930", "005930"],
            "report_period": ["2023-Q4", "2024-Q1", "2024-Q2"],
            "disclosure_date": pd.to_datetime(["2024-03-15", "2024-05-15", "2024-08-14"]),
            "revenue_yoy": [1.0, 2.0, 3.0],
            "operating_income_yoy": [4.0, 5.0, 6.0],
            "operating_margin": [0.1, 0.2, 0.3],
            "oi_yoy_flag": ["normal", "normal", "normal"],
            "net_income_yoy": [7.0, 8.0, 9.0],
            "ni_yoy_flag": ["normal", "normal", "normal"],
        }
    )

    joined = join_signals_step12(signals, fundamentals)

    # disclosure_date == signal_date 는 제외되어 2023-Q4가 매칭되어야 한다.
    assert joined.loc[0, "fundamental_report_period"] == "2023-Q4"
    assert joined.loc[0, "revenue_yoy"] == pytest.approx(1.0)

    # 8/15 signal은 8/14 공시(2024-Q2)까지 사용 가능
    assert joined.loc[1, "fundamental_report_period"] == "2024-Q2"
    assert joined.loc[1, "revenue_yoy"] == pytest.approx(3.0)


def test_build_group_table_filters_overheated_and_counts_groups() -> None:
    joined = pd.DataFrame(
        {
            "ticker": ["A", "A", "A", "A"],
            "signal_type": ["BUY_WATCH", "OVERHEATED", "BUY_WATCH", "BUY_WATCH"],
            "fundamental_report_period": ["2024-Q1", "2024-Q1", "2024-Q1", "2024-Q1"],
            "revenue_yoy": [5.0, 50.0, -3.0, 15.0],
            "operating_income_yoy": [20.0, 50.0, 0.0, 5.0],
            "oi_yoy_flag": ["normal", "normal", "turnaround", "normal"],
            "net_income_yoy": [10.0, 50.0, None, -10.0],
            "ni_yoy_flag": ["normal", "normal", "turnaround", "normal"],
            "return_5d": [1.0, 99.0, -1.0, 2.0],
            "return_10d": [2.0, 99.0, -2.0, 1.0],
            "return_20d": [10.0, 99.0, 0.0, -5.0],
            "excess_return_20d": [3.0, 80.0, -1.0, 0.5],
            "max_drawdown_20d": [-2.0, -0.1, -8.0, -4.0],
        }
    )

    table = build_group_table(joined).set_index("group")

    assert table.loc["G0: All Signals", "valid_signal_count"] == 3
    assert table.loc["G1: Revenue YoY > 0%", "valid_signal_count"] == 2
    assert table.loc["G2: OI YoY > 0%", "valid_signal_count"] == 3
    assert table.loc["G3: Revenue YoY > 0% & OI YoY > 0%", "valid_signal_count"] == 2
    assert table.loc["G4: Strong Growth (Rev>10%, OI>10%, NI+)", "valid_signal_count"] == 0

    # OVERHEATED(99%) 제외 확인: (10 + 0 - 5) / 3 = 1.666...
    assert table.loc["G0: All Signals", "avg_return_20d"] == pytest.approx(1.67, abs=1e-2)

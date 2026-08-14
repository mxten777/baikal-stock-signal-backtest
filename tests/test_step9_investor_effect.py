"""
STEP 9 — 수급 효과 검증 단위 테스트

compute_investor_features 와 _group_stats 만 검증.
실제 파일 I/O 없음.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.step9_investor_effect import (
    LOW_SAMPLE_THRESHOLD,
    _group_stats,
    compute_investor_features,
)


# ─────────────────────────────────────────────
# Fixture 헬퍼
# ─────────────────────────────────────────────

def _make_investor_df(ticker: str) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    return pd.DataFrame({
        "date": dates,
        "ticker": ticker,
        "foreign_net_buy": [100, -200, 300, 400, 500, -100, 200, -300, 100, 50],
        "institution_net_buy": [-50, 100, -100, 200, -300, 400, -200, 100, 50, -50],
    })


def _make_raw_df() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=25, freq="B")
    return pd.DataFrame({
        "date": dates,
        "volume": [1_000_000] * 25,
    })


def _make_signals(ticker: str, signal_date: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": [ticker],
        "signal_date": pd.to_datetime([signal_date]),
        "excess_return_5d": [1.0],
        "excess_return_10d": [2.0],
        "excess_return_20d": [3.0],
    })


# ─────────────────────────────────────────────
# compute_investor_features
# ─────────────────────────────────────────────

class TestComputeInvestorFeatures:
    def test_adds_expected_columns(self):
        signals = _make_signals("005930", "2024-01-10")
        result = compute_investor_features(
            signals,
            {"005930": _make_investor_df("005930")},
            {"005930": _make_raw_df()},
        )
        for col in [
            "foreign_net_1d", "foreign_net_3d", "foreign_net_5d",
            "institution_net_1d", "institution_net_3d", "institution_net_5d",
            "avg_volume_20d", "foreign_5d_ratio", "institution_5d_ratio",
        ]:
            assert col in result.columns, f"컬럼 누락: {col}"

    def test_1d_equals_single_day_value(self):
        """1d 누적은 signal_date 당일 단일 값이어야 한다."""
        inv_df = _make_investor_df("005930")
        result = compute_investor_features(
            _make_signals("005930", "2024-01-10"),
            {"005930": inv_df},
            {},
        )
        expected = inv_df[inv_df["date"] == pd.Timestamp("2024-01-10")][
            "foreign_net_buy"
        ].values[0]
        assert result["foreign_net_1d"].iloc[0] == expected

    def test_5d_is_sum_of_last_5_rows(self):
        inv_df = _make_investor_df("005930")
        result = compute_investor_features(
            _make_signals("005930", "2024-01-10"),
            {"005930": inv_df},
            {},
        )
        expected = (
            inv_df[inv_df["date"] <= pd.Timestamp("2024-01-10")]
            .tail(5)["foreign_net_buy"]
            .sum()
        )
        assert result["foreign_net_5d"].iloc[0] == expected

    def test_missing_investor_data_returns_nan(self):
        result = compute_investor_features(_make_signals("035720", "2024-01-10"), {}, {})
        assert pd.isna(result["foreign_net_5d"].iloc[0])
        assert pd.isna(result["institution_net_5d"].iloc[0])

    def test_ratio_equals_net5d_over_avg_vol(self):
        result = compute_investor_features(
            _make_signals("005930", "2024-01-10"),
            {"005930": _make_investor_df("005930")},
            {"005930": _make_raw_df()},
        )
        f5d = result["foreign_net_5d"].iloc[0]
        avg_vol = result["avg_volume_20d"].iloc[0]
        assert abs(result["foreign_5d_ratio"].iloc[0] - f5d / avg_vol) < 1e-9

    def test_ratio_nan_when_no_raw_data(self):
        result = compute_investor_features(
            _make_signals("005930", "2024-01-10"),
            {"005930": _make_investor_df("005930")},
            {},
        )
        assert pd.isna(result["foreign_5d_ratio"].iloc[0])
        assert pd.isna(result["institution_5d_ratio"].iloc[0])

    def test_avg_volume_20d_uses_at_most_20_rows(self):
        """avg_volume_20d는 최근 20거래일 평균이어야 한다."""
        raw_df = _make_raw_df()  # 25행, 볼륨 전부 1_000_000
        result = compute_investor_features(
            _make_signals("005930", "2024-02-08"),  # 25번째 거래일
            {"005930": _make_investor_df("005930")},
            {"005930": raw_df},
        )
        assert result["avg_volume_20d"].iloc[0] == pytest.approx(1_000_000.0)

    def test_institution_net_3d_matches_expected_sum(self):
        inv_df = _make_investor_df("005930")
        result = compute_investor_features(
            _make_signals("005930", "2024-01-10"),
            {"005930": inv_df},
            {},
        )
        expected = (
            inv_df[inv_df["date"] <= pd.Timestamp("2024-01-10")]
            .tail(3)["institution_net_buy"]
            .sum()
        )
        assert result["institution_net_3d"].iloc[0] == expected


# ─────────────────────────────────────────────
# _group_stats
# ─────────────────────────────────────────────

class TestGroupStats:
    def _df(self, n: int, excess_20d=None) -> pd.DataFrame:
        vals = excess_20d if excess_20d is not None else [1.0] * n
        return pd.DataFrame({
            "excess_return_5d": [0.5] * n,
            "excess_return_10d": [1.0] * n,
            "excess_return_20d": vals,
        })

    def test_count_matches(self):
        assert _group_stats("T", self._df(7))["Count"] == 7

    def test_low_sample_flag_below_threshold(self):
        stats = _group_stats("T", self._df(LOW_SAMPLE_THRESHOLD - 1))
        assert "[LOW SAMPLE]" in stats["Group"]

    def test_no_low_sample_flag_at_threshold(self):
        stats = _group_stats("T", self._df(LOW_SAMPLE_THRESHOLD))
        assert "[LOW SAMPLE]" not in stats["Group"]

    def test_win_rate_all_positive(self):
        assert _group_stats("T", self._df(6, [1.0] * 6))["Win Rate 20D (%)"] == 100.0

    def test_win_rate_all_negative(self):
        assert _group_stats("T", self._df(6, [-1.0] * 6))["Win Rate 20D (%)"] == 0.0

    def test_empty_returns_nan(self):
        stats = _group_stats("E", self._df(0))
        assert pd.isna(stats["Avg Excess 20D"])
        assert pd.isna(stats["Win Rate 20D (%)"])

    def test_median_excess_20d(self):
        stats = _group_stats("T", self._df(5, [1.0, 2.0, 3.0, 4.0, 5.0]))
        assert stats["Median Excess 20D"] == pytest.approx(3.0)

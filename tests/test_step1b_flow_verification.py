"""
STEP 1-B — 수급 효과 재검증 단위 테스트

classify_flow / build_merge_quality / group_stats / verify_baseline 만 검증.
실제 파일 I/O 없음.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.step1b_flow_verification import (
    EXPECTED_SIGNAL_COUNT,
    EXPECTED_TICKER_COUNT,
    add_flow_classification,
    build_by_stock_table,
    build_flow_performance_table,
    build_merge_quality,
    build_signal_level_table,
    classify_flow,
    group_stats,
    verify_baseline,
)


# ─────────────────────────────────────────────
# classify_flow
# ─────────────────────────────────────────────
class TestClassifyFlow:
    def test_positive_at_threshold(self):
        assert classify_flow(0.20) == "POSITIVE"

    def test_positive_above_threshold(self):
        assert classify_flow(0.35) == "POSITIVE"

    def test_negative_at_threshold(self):
        assert classify_flow(-0.20) == "NEGATIVE"

    def test_negative_below_threshold(self):
        assert classify_flow(-0.50) == "NEGATIVE"

    def test_neutral_between_thresholds(self):
        assert classify_flow(0.0) == "NEUTRAL"
        assert classify_flow(0.19) == "NEUTRAL"
        assert classify_flow(-0.19) == "NEUTRAL"

    def test_nan_returns_no_data(self):
        assert classify_flow(np.nan) == "NO_DATA"


# ─────────────────────────────────────────────
# verify_baseline
# ─────────────────────────────────────────────
class TestVerifyBaseline:
    def test_passes_when_counts_match(self):
        df = pd.DataFrame({
            "ticker": [f"{i:06d}" for i in range(EXPECTED_TICKER_COUNT)] * (EXPECTED_SIGNAL_COUNT // EXPECTED_TICKER_COUNT)
            + [f"{i:06d}" for i in range(EXPECTED_SIGNAL_COUNT % EXPECTED_TICKER_COUNT)],
        })
        assert len(df) == EXPECTED_SIGNAL_COUNT
        verify_baseline(df)  # 예외 없이 통과해야 함

    def test_raises_on_signal_count_mismatch(self):
        df = pd.DataFrame({"ticker": ["005930"] * 10})
        with pytest.raises(RuntimeError):
            verify_baseline(df)

    def test_raises_on_ticker_count_mismatch(self):
        df = pd.DataFrame({"ticker": ["005930"] * EXPECTED_SIGNAL_COUNT})
        with pytest.raises(RuntimeError):
            verify_baseline(df)


# ─────────────────────────────────────────────
# build_merge_quality
# ─────────────────────────────────────────────
class TestBuildMergeQuality:
    def _df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker": ["005930", "005930", "000660"],
            "name": ["삼성전자", "삼성전자", "SK하이닉스"],
            "signal_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "foreign_net_5d": [100.0, np.nan, 200.0],
            "institution_net_5d": [50.0, 30.0, np.nan],
        })

    def test_merge_rate_calculation(self):
        summary, failed = build_merge_quality(self._df())
        assert summary.loc[0, "total_signals"] == 3
        assert summary.loc[0, "merged_success"] == 1
        assert summary.loc[0, "merged_failed"] == 2
        assert summary.loc[0, "merge_rate_pct"] == pytest.approx(33.33, abs=0.01)

    def test_failed_rows_have_reason(self):
        _, failed = build_merge_quality(self._df())
        assert len(failed) == 2
        assert "reason" in failed.columns


# ─────────────────────────────────────────────
# group_stats
# ─────────────────────────────────────────────
class TestGroupStats:
    def _df(self, n: int) -> pd.DataFrame:
        return pd.DataFrame({
            "return_5d": [1.0] * n,
            "return_10d": [2.0] * n,
            "return_20d": [3.0] * n,
            "excess_return_5d": [0.5] * n,
            "excess_return_10d": [1.0] * n,
            "excess_return_20d": [1.5] * n,
        })

    def test_signal_count(self):
        assert group_stats("T", self._df(10))["Signal Count"] == 10

    def test_win_rate_all_positive(self):
        assert group_stats("T", self._df(6))["Win Rate 20D (%)"] == 100.0

    def test_empty_group_returns_nan(self):
        stats = group_stats("E", self._df(0))
        assert pd.isna(stats["Avg Excess 20D"])


# ─────────────────────────────────────────────
# 통합 흐름 (add_flow_classification -> 성과/종목/레벨 테이블)
# ─────────────────────────────────────────────
class TestIntegratedTables:
    def _merged(self) -> pd.DataFrame:
        n = 10
        return pd.DataFrame({
            "ticker": ["005930"] * n,
            "name": ["삼성전자"] * n,
            "signal_date": pd.date_range("2024-01-01", periods=n, freq="B"),
            "score_group": ["MID"] * 5 + ["HIGH"] * 5,
            "foreign_5d_ratio": [0.25, 0.30, -0.25, 0.0, 0.10, 0.25, -0.30, 0.05, 0.0, -0.10],
            "institution_5d_ratio": [0.25, -0.25, 0.30, 0.0, 0.10, 0.25, -0.30, 0.05, 0.0, -0.10],
            "return_5d": [1.0] * n,
            "return_10d": [2.0] * n,
            "return_20d": [3.0] * n,
            "excess_return_5d": [0.5] * n,
            "excess_return_10d": [1.0] * n,
            "excess_return_20d": [1.5] * n,
        })

    def test_flow_performance_has_pos_neg_diff_row(self):
        classified = add_flow_classification(self._merged())
        perf = build_flow_performance_table(classified)
        assert "Foreign POSITIVE - NEGATIVE" in perf["Group"].values
        assert "Institution POSITIVE - NEGATIVE" in perf["Group"].values

    def test_by_stock_table_counts_classes(self):
        classified = add_flow_classification(self._merged())
        by_stock = build_by_stock_table(classified)
        row = by_stock.iloc[0]
        assert row["signal_count"] == 10
        assert row["foreign_positive"] + row["foreign_neutral"] + row["foreign_negative"] == 10

    def test_signal_level_table_has_mid_high(self):
        classified = add_flow_classification(self._merged())
        table = build_signal_level_table(classified)
        assert set(table["Signal Level"].unique()) == {"MID", "HIGH"}

"""
STEP 10 — 기업 실적 Provider 단위 테스트

실제 DART API 호출 없음. 모든 외부 호출은 mock 처리.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_provider.dart_fundamental_provider import (
    _build_disclosure_map,
    _extract_amount,
    _normalize_finstate,
    _parse_amount,
    compute_yoy_growth,
    fetch_quarterly_fundamentals,
    join_signals_to_fundamentals,
)


# ─────────────────────────────────────────────
# Mock 데이터 헬퍼
# ─────────────────────────────────────────────

def _make_dart_mock() -> MagicMock:
    dart = MagicMock()
    dart.find_corp_code.return_value = "00126380"

    # 공시 목록 mock
    dart.list.return_value = pd.DataFrame([
        {"report_nm": "2023년 1분기보고서", "rcept_dt": "20230515"},
        {"report_nm": "2023년 반기보고서",  "rcept_dt": "20230814"},
        {"report_nm": "2023년 3분기보고서", "rcept_dt": "20231114"},
        {"report_nm": "2023년 사업보고서",  "rcept_dt": "20240315"},
        {"report_nm": "2024년 1분기보고서", "rcept_dt": "20240515"},
        {"report_nm": "2024년 반기보고서",  "rcept_dt": "20240814"},
    ])

    # finstate_all mock — 손익계산서 행만 포함
    def _fake_finstate(ticker, year, reprt_code="11011", fs_div="CFS"):
        return pd.DataFrame([
            {"account_nm": "매출액",        "thstrm_amount": "10,000,000"},
            {"account_nm": "영업이익",       "thstrm_amount": "1,500,000"},
            {"account_nm": "당기순이익",     "thstrm_amount": "1,200,000"},
            {"account_nm": "자산총계",       "thstrm_amount": "50,000,000"},
            {"account_nm": "자본총계",       "thstrm_amount": "20,000,000"},
        ])

    dart.finstate_all.side_effect = _fake_finstate
    return dart


# ─────────────────────────────────────────────
# _parse_amount
# ─────────────────────────────────────────────

class TestParseAmount:
    def test_parses_comma_number(self):
        assert _parse_amount("1,234,567") == pytest.approx(1_234_567.0)

    def test_parses_plain_number(self):
        assert _parse_amount("9876543") == pytest.approx(9_876_543.0)

    def test_returns_none_for_nan(self):
        assert _parse_amount(float("nan")) is None

    def test_returns_none_for_non_numeric(self):
        assert _parse_amount("N/A") is None

    def test_negative_number(self):
        assert _parse_amount("-500,000") == pytest.approx(-500_000.0)


# ─────────────────────────────────────────────
# _extract_amount
# ─────────────────────────────────────────────

class TestExtractAmount:
    def _make_df(self):
        return pd.DataFrame([
            {"account_nm": "매출액",    "thstrm_amount": "5,000,000"},
            {"account_nm": "영업이익",  "thstrm_amount": "800,000"},
            {"account_nm": "자산총계",  "thstrm_amount": "30,000,000"},
        ])

    def test_finds_first_alias(self):
        df = self._make_df()
        result = _extract_amount(df, ["매출액", "수익(매출액)"])
        assert result == pytest.approx(5_000_000.0)

    def test_falls_back_to_second_alias(self):
        df = self._make_df()
        # "수익(매출액)" 없어서 "매출액"으로 폴백 — 반대 순서
        result = _extract_amount(df, ["수익(매출액)", "매출액"])
        assert result == pytest.approx(5_000_000.0)

    def test_returns_none_if_not_found(self):
        df = self._make_df()
        assert _extract_amount(df, ["존재하지않는계정"]) is None


# ─────────────────────────────────────────────
# _build_disclosure_map
# ─────────────────────────────────────────────

class TestBuildDisclosureMap:
    def test_maps_quarters_to_dates(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame([
            {"report_nm": "2024년 1분기보고서", "rcept_dt": "20240515"},
            {"report_nm": "2024년 반기보고서",  "rcept_dt": "20240814"},
            {"report_nm": "2024년 3분기보고서", "rcept_dt": "20241114"},
            {"report_nm": "2024년 사업보고서",  "rcept_dt": "20250315"},
        ])
        result = _build_disclosure_map(dart, "005930", 2024, 2024)
        assert result["2024-Q1"] == "2024-05-15"
        assert result["2024-Q2"] == "2024-08-14"
        assert result["2024-Q3"] == "2024-11-14"
        assert result["2024-Q4"] == "2025-03-15"

    def test_returns_empty_on_dart_error(self):
        dart = MagicMock()
        dart.list.side_effect = Exception("network error")
        result = _build_disclosure_map(dart, "005930", 2024, 2024)
        assert result == {}

    def test_returns_empty_when_list_is_empty(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame()
        result = _build_disclosure_map(dart, "005930", 2024, 2024)
        assert result == {}

    def test_earliest_date_wins_for_duplicate(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame([
            {"report_nm": "2024년 1분기보고서", "rcept_dt": "20240520"},
            {"report_nm": "2024년 1분기보고서 (정정)", "rcept_dt": "20240601"},
        ])
        result = _build_disclosure_map(dart, "005930", 2024, 2024)
        # 더 이른 날짜(최초 공시)가 사용되어야 한다
        assert result.get("2024-Q1") == "2024-05-20"


# ─────────────────────────────────────────────
# fetch_quarterly_fundamentals
# ─────────────────────────────────────────────

class TestFetchQuarterlyFundamentals:
    def test_returns_expected_columns(self):
        dart = _make_dart_mock()
        df = fetch_quarterly_fundamentals(dart, "005930", 2023, 2024)
        for col in [
            "ticker", "report_period", "disclosure_date",
            "revenue", "operating_income", "net_income",
            "total_assets", "total_equity",
        ]:
            assert col in df.columns, f"컬럼 누락: {col}"

    def test_disclosure_date_is_datetime(self):
        dart = _make_dart_mock()
        df = fetch_quarterly_fundamentals(dart, "005930", 2023, 2024)
        assert pd.api.types.is_datetime64_any_dtype(df["disclosure_date"])

    def test_revenue_is_numeric(self):
        dart = _make_dart_mock()
        df = fetch_quarterly_fundamentals(dart, "005930", 2023, 2024)
        assert df["revenue"].iloc[0] == pytest.approx(10_000_000.0)

    def test_returns_empty_when_no_disclosures(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame()
        df = fetch_quarterly_fundamentals(dart, "005930", 2024, 2024)
        assert df.empty

    def test_skips_period_when_finstate_fails(self):
        dart = _make_dart_mock()
        dart.finstate_all.side_effect = Exception("API error")
        df = fetch_quarterly_fundamentals(dart, "005930", 2023, 2024)
        assert df.empty


# ─────────────────────────────────────────────
# join_signals_to_fundamentals  — Look-ahead Bias 방지
# ─────────────────────────────────────────────

class TestJoinSignalsToFundamentals:
    def _make_signals(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker": ["005930", "005930", "005930"],
            "signal_date": pd.to_datetime(["2024-04-01", "2024-06-01", "2024-10-01"]),
            "excess_return_20d": [1.0, 2.0, 3.0],
        })

    def _make_fundamentals(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker": ["005930", "005930", "005930"],
            "report_period": ["2023-Q4", "2024-Q1", "2024-Q2"],
            "disclosure_date": pd.to_datetime(["2024-03-15", "2024-05-15", "2024-08-14"]),
            "revenue": [100.0, 110.0, 115.0],
            "operating_income": [10.0, 12.0, 13.0],
            "net_income": [8.0, 9.0, 10.0],
            "total_assets": [500.0, 510.0, 520.0],
            "total_equity": [200.0, 210.0, 215.0],
        })

    def test_signal_before_any_disclosure_returns_none(self):
        signals = self._make_signals().iloc[:1].copy()  # 2024-04-01
        fund = self._make_fundamentals().iloc[1:]  # disclosure from 2024-05-15 onward
        result = join_signals_to_fundamentals(signals, fund)
        assert result["fund_report_period"].iloc[0] is None

    def test_uses_most_recent_disclosed_quarter(self):
        # 2024-06-01 signal → 2024-05-15 disclosure (2024-Q1) should match
        signals = self._make_signals().iloc[1:2].copy()
        fund = self._make_fundamentals()
        result = join_signals_to_fundamentals(signals, fund)
        assert result["fund_report_period"].iloc[0] == "2024-Q1"

    def test_does_not_use_same_day_disclosure(self):
        # signal_date == disclosure_date → should NOT match (bias prevention)
        signals = pd.DataFrame({
            "ticker": ["005930"],
            "signal_date": pd.to_datetime(["2024-05-15"]),  # same as Q1 disclosure
            "excess_return_20d": [1.0],
        })
        fund = self._make_fundamentals()
        result = join_signals_to_fundamentals(signals, fund)
        # Only 2023-Q4 (disclosed 2024-03-15) qualifies
        assert result["fund_report_period"].iloc[0] == "2023-Q4"

    def test_empty_fundamentals_returns_signals_unchanged(self):
        signals = self._make_signals()
        result = join_signals_to_fundamentals(signals, pd.DataFrame())
        assert len(result) == len(signals)
        assert "excess_return_20d" in result.columns


# ─────────────────────────────────────────────
# compute_yoy_growth
# ─────────────────────────────────────────────

class TestComputeYoyGrowth:
    def _make_fund(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker": ["005930"] * 4,
            "report_period": ["2023-Q1", "2023-Q2", "2024-Q1", "2024-Q2"],
            "disclosure_date": pd.to_datetime(
                ["2023-05-15", "2023-08-14", "2024-05-15", "2024-08-14"]
            ),
            "revenue": [90.0, 95.0, 100.0, 110.0],
            "operating_income": [9.0, 10.0, 11.0, 13.0],
            "net_income": [7.0, 8.0, 8.5, 10.0],
            "total_assets": [400.0, 410.0, 450.0, 460.0],
            "total_equity": [150.0, 155.0, 170.0, 175.0],
        })

    def test_yoy_revenue_growth_is_correct(self):
        df = compute_yoy_growth(self._make_fund())
        # 2024-Q1 vs 2023-Q1: (100/90) - 1 ≈ 0.1111
        q1_2024 = df[df["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["yoy_revenue_growth"] == pytest.approx(100 / 90 - 1, rel=1e-6)

    def test_operating_margin_is_correct(self):
        df = compute_yoy_growth(self._make_fund())
        q1_2024 = df[df["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["operating_margin"] == pytest.approx(11 / 100, rel=1e-6)

    def test_first_year_has_no_yoy(self):
        df = compute_yoy_growth(self._make_fund())
        q1_2023 = df[df["report_period"] == "2023-Q1"].iloc[0]
        assert pd.isna(q1_2023["yoy_revenue_growth"])

    def test_adds_three_new_columns(self):
        df = compute_yoy_growth(self._make_fund())
        for col in ["yoy_revenue_growth", "yoy_operating_income_growth", "operating_margin"]:
            assert col in df.columns

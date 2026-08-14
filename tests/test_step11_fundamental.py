"""
STEP 11 — 재무데이터 단위 테스트

실제 DART API 호출 없음. 모든 외부 호출은 mock 처리.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pandas as pd
import pytest

from src.data_provider.dart_fundamental_provider import (
    compute_growth_metrics,
    deaccumulate_quarters,
    join_signals_step11,
)


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _make_cumulative_df(ticker: str = "005930") -> pd.DataFrame:
    """
    DART API 실제 구조:
    - Q1/Q2/Q3: thstrm_amount = 단일분기 값
    - Q4(사업보고서): thstrm_amount = 연간 누적 합계 (Q1+Q2+Q3+Q4)
    """
    return pd.DataFrame({
        "ticker": [ticker] * 4,
        "report_period": ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"],
        "disclosure_date": pd.to_datetime([
            "2024-05-15", "2024-08-14", "2024-11-14", "2025-03-15",
        ]),
        # Q1=100, Q2=120, Q3=140 (single quarter); Q4 row = annual total 520
        "revenue":          [100.0, 120.0, 140.0, 520.0],
        "operating_income": [10.0,  12.0,  14.0,  52.0],
        "net_income":       [8.0,   9.0,   10.0,  38.0],
        # BS: 변환 불필요
        "total_assets":     [500.0, 510.0, 520.0, 530.0],
        "total_equity":     [200.0, 205.0, 210.0, 215.0],
    })


def _make_deaccumulated_df(ticker: str = "005930") -> pd.DataFrame:
    """deaccumulate_quarters 적용 후 예상 단일분기 DataFrame."""
    return pd.DataFrame({
        "ticker": [ticker] * 4,
        "report_period": ["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4"],
        "disclosure_date": pd.to_datetime([
            "2024-05-15", "2024-08-14", "2024-11-14", "2025-03-15",
        ]),
        # Q1/Q2/Q3: 그대로; Q4 = 520-100-120-140=160
        "revenue":          [100.0, 120.0, 140.0, 160.0],
        "operating_income": [10.0,  12.0,  14.0,  16.0],
        "net_income":       [8.0,   9.0,   10.0,  11.0],
        "total_assets":     [500.0, 510.0, 520.0, 530.0],
        "total_equity":     [200.0, 205.0, 210.0, 215.0],
    })


def _make_two_year_fund() -> pd.DataFrame:
    """
    YoY 계산을 위한 2개년 단일분기 DataFrame.
    (deaccumulate_quarters 적용 후 상태로 가정)
    """
    return pd.DataFrame({
        "ticker": ["005930"] * 8,
        "report_period": [
            "2023-Q1", "2023-Q2", "2023-Q3", "2023-Q4",
            "2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4",
        ],
        "disclosure_date": pd.to_datetime([
            "2023-05-15", "2023-08-14", "2023-11-14", "2024-03-15",
            "2024-05-15", "2024-08-14", "2024-11-14", "2025-03-15",
        ]),
        "revenue":          [90.0, 95.0, 92.0, 98.0, 100.0, 110.0, 115.0, 120.0],
        "operating_income": [9.0, 10.0, 8.0, 11.0, 10.0, 12.0, 14.0, 16.0],
        "net_income":       [7.0, 8.0, 6.0, 9.0, 8.0, 9.0, 11.0, 13.0],
        "total_assets":     [400.0] * 8,
        "total_equity":     [150.0] * 8,
    })


# ─────────────────────────────────────────────────────────────────────────────
# API Key 미설정 처리
# ─────────────────────────────────────────────────────────────────────────────

class TestApiKeyHandling:
    def test_missing_api_key_exits(self, tmp_path, monkeypatch):
        """DART_API_KEY 미설정 시 sys.exit(1) 호출 확인."""
        monkeypatch.delenv("DART_API_KEY", raising=False)

        # step11 스크립트의 _get_api_key 함수 직접 테스트
        import importlib
        import sys

        spec = importlib.util.spec_from_file_location(
            "step11_mod",
            str(pytest.importorskip("pathlib").Path(__file__).parent.parent
                / "scripts" / "step11_fundamental_actual.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # _get_api_key only; don't execute main
        spec.loader.exec_module(mod)

        with pytest.raises(SystemExit) as exc_info:
            mod._get_api_key()
        assert exc_info.value.code == 1

    def test_present_api_key_returns_key(self, monkeypatch):
        """DART_API_KEY 설정 시 값을 반환한다."""
        monkeypatch.setenv("DART_API_KEY", "testkey12345")
        import importlib
        spec = importlib.util.spec_from_file_location(
            "step11_mod2",
            str(pytest.importorskip("pathlib").Path(__file__).parent.parent
                / "scripts" / "step11_fundamental_actual.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._get_api_key() == "testkey12345"


# ─────────────────────────────────────────────────────────────────────────────
# 분기 누적값 → 단일분기 변환
# ─────────────────────────────────────────────────────────────────────────────

class TestDeaccumulateQuarters:
    def test_q1_unchanged(self):
        """Q1은 단일분기 → 변환 없음."""
        df = _make_cumulative_df()
        result = deaccumulate_quarters(df)
        q1 = result[result["report_period"] == "2024-Q1"].iloc[0]
        assert q1["revenue"] == pytest.approx(100.0)
        assert q1["operating_income"] == pytest.approx(10.0)
        assert q1["net_income"] == pytest.approx(8.0)

    def test_q2_unchanged(self):
        """Q2(반기보고서)는 이미 단일분기 → 변환 없음."""
        df = _make_cumulative_df()
        result = deaccumulate_quarters(df)
        q2 = result[result["report_period"] == "2024-Q2"].iloc[0]
        assert q2["revenue"] == pytest.approx(120.0)
        assert q2["operating_income"] == pytest.approx(12.0)
        assert q2["net_income"] == pytest.approx(9.0)

    def test_q3_unchanged(self):
        """Q3(3분기보고서)는 이미 단일분기 → 변환 없음."""
        df = _make_cumulative_df()
        result = deaccumulate_quarters(df)
        q3 = result[result["report_period"] == "2024-Q3"].iloc[0]
        assert q3["revenue"] == pytest.approx(140.0)
        assert q3["operating_income"] == pytest.approx(14.0)

    def test_q4_is_fy_minus_q1_q2_q3(self):
        """Q4(사업보고서) = 연간누적 - Q1 - Q2 - Q3."""
        df = _make_cumulative_df()
        result = deaccumulate_quarters(df)
        q4 = result[result["report_period"] == "2024-Q4"].iloc[0]
        assert q4["revenue"] == pytest.approx(160.0)       # 520 - 100 - 120 - 140
        assert q4["operating_income"] == pytest.approx(16.0)  # 52 - 10 - 12 - 14
        assert q4["net_income"] == pytest.approx(11.0)     # 38 - 8 - 9 - 10

    def test_bs_items_not_modified(self):
        """total_assets, total_equity는 변환하지 않는다."""
        df = _make_cumulative_df()
        result = deaccumulate_quarters(df)
        q4 = result[result["report_period"] == "2024-Q4"].iloc[0]
        assert q4["total_assets"] == pytest.approx(530.0)
        assert q4["total_equity"] == pytest.approx(215.0)

    def test_missing_q1_makes_q4_none(self):
        """Q1 데이터 없으면 Q4 P&L을 None으로 설정한다 (Q1+Q2+Q3 합산 불가)."""
        df = _make_cumulative_df()
        df = df[df["report_period"] != "2024-Q1"].reset_index(drop=True)
        result = deaccumulate_quarters(df)
        q4 = result[result["report_period"] == "2024-Q4"].iloc[0]
        assert q4["revenue"] is None or pd.isna(q4["revenue"])

    def test_empty_df_returns_empty(self):
        result = deaccumulate_quarters(pd.DataFrame())
        assert result.empty

    def test_multiple_tickers_independent(self):
        """종목별로 독립적으로 Q4를 변환한다."""
        df1 = _make_cumulative_df("005930")
        df2 = _make_cumulative_df("000660")
        combined = pd.concat([df1, df2], ignore_index=True)
        result = deaccumulate_quarters(combined)
        for ticker in ["005930", "000660"]:
            q4 = result[(result["ticker"] == ticker) & (result["report_period"] == "2024-Q4")].iloc[0]
            assert q4["revenue"] == pytest.approx(160.0)  # 520-100-120-140


# ─────────────────────────────────────────────────────────────────────────────
# YoY 계산
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeGrowthMetrics:
    def test_revenue_yoy_correct(self):
        df = compute_growth_metrics(_make_two_year_fund())
        q1_2024 = df[df["report_period"] == "2024-Q1"].iloc[0]
        expected = (100.0 / 90.0 - 1) * 100
        assert q1_2024["revenue_yoy"] == pytest.approx(expected, rel=1e-4)

    def test_operating_income_yoy_correct(self):
        df = compute_growth_metrics(_make_two_year_fund())
        q1_2024 = df[df["report_period"] == "2024-Q1"].iloc[0]
        expected = (10.0 / 9.0 - 1) * 100
        assert q1_2024["operating_income_yoy"] == pytest.approx(expected, rel=1e-4)

    def test_operating_margin_correct(self):
        df = compute_growth_metrics(_make_two_year_fund())
        q1_2024 = df[df["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["operating_margin"] == pytest.approx(10.0 / 100.0, rel=1e-4)

    def test_first_year_has_no_yoy(self):
        df = compute_growth_metrics(_make_two_year_fund())
        q1_2023 = df[df["report_period"] == "2023-Q1"].iloc[0]
        assert pd.isna(q1_2023["revenue_yoy"])
        assert pd.isna(q1_2023["operating_income_yoy"])

    def test_turnaround_flag(self):
        """전년 영업이익 음수 → 당기 양수 : turnaround 플래그, YoY=None."""
        df = pd.DataFrame({
            "ticker": ["005930"] * 2,
            "report_period": ["2023-Q1", "2024-Q1"],
            "disclosure_date": pd.to_datetime(["2023-05-15", "2024-05-15"]),
            "revenue": [100.0, 110.0],
            "operating_income": [-5.0, 10.0],   # 적자 → 흑자
            "net_income": [1.0, 2.0],
            "total_assets": [500.0, 510.0],
            "total_equity": [200.0, 210.0],
        })
        result = compute_growth_metrics(df)
        q1_2024 = result[result["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["oi_yoy_flag"] == "turnaround"
        assert pd.isna(q1_2024["operating_income_yoy"])

    def test_base_zero_flag(self):
        """전년 영업이익 0 : base_zero 플래그, YoY=None."""
        df = pd.DataFrame({
            "ticker": ["005930"] * 2,
            "report_period": ["2023-Q1", "2024-Q1"],
            "disclosure_date": pd.to_datetime(["2023-05-15", "2024-05-15"]),
            "revenue": [100.0, 110.0],
            "operating_income": [0.0, 10.0],
            "net_income": [1.0, 2.0],
            "total_assets": [500.0, 510.0],
            "total_equity": [200.0, 210.0],
        })
        result = compute_growth_metrics(df)
        q1_2024 = result[result["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["oi_yoy_flag"] == "base_zero"
        assert pd.isna(q1_2024["operating_income_yoy"])

    def test_both_negative_flag_computes_yoy(self):
        """전년 음수, 당기도 음수 : both_negative 플래그, YoY 계산됨."""
        df = pd.DataFrame({
            "ticker": ["005930"] * 2,
            "report_period": ["2023-Q1", "2024-Q1"],
            "disclosure_date": pd.to_datetime(["2023-05-15", "2024-05-15"]),
            "revenue": [100.0, 110.0],
            "operating_income": [-10.0, -8.0],
            "net_income": [1.0, 2.0],
            "total_assets": [500.0, 510.0],
            "total_equity": [200.0, 210.0],
        })
        result = compute_growth_metrics(df)
        q1_2024 = result[result["report_period"] == "2024-Q1"].iloc[0]
        assert q1_2024["oi_yoy_flag"] == "both_negative"
        assert not pd.isna(q1_2024["operating_income_yoy"])
        expected = (-8.0 / -10.0 - 1) * 100
        assert q1_2024["operating_income_yoy"] == pytest.approx(expected, rel=1e-4)

    def test_adds_required_columns(self):
        df = compute_growth_metrics(_make_two_year_fund())
        for col in ["revenue_yoy", "operating_income_yoy", "operating_margin", "oi_yoy_flag"]:
            assert col in df.columns


# ─────────────────────────────────────────────────────────────────────────────
# Signal Join — Look-ahead Bias 방지
# ─────────────────────────────────────────────────────────────────────────────

class TestJoinSignalsStep11:
    def _signals(self) -> pd.DataFrame:
        return pd.DataFrame({
            "ticker": ["005930"] * 3,
            "signal_date": pd.to_datetime(["2024-04-01", "2024-06-01", "2024-10-01"]),
            "excess_return_20d": [1.0, 2.0, 3.0],
        })

    def _fundamentals(self) -> pd.DataFrame:
        fund = _make_two_year_fund()
        return compute_growth_metrics(fund)

    def test_disclosure_date_strictly_less_than_signal_date(self):
        """disclosure_date < signal_date 인 것만 사용 (동일일 제외)."""
        signals = pd.DataFrame({
            "ticker": ["005930"],
            "signal_date": pd.to_datetime(["2024-05-15"]),  # Q1 2024 공시일과 동일
            "excess_return_20d": [1.0],
        })
        fund = self._fundamentals()
        result = join_signals_step11(signals, fund)
        # 2024-05-15 공시는 제외 → 2023-Q4 (공시 2024-03-15)가 매칭
        assert result["fundamental_report_period"].iloc[0] == "2023-Q4"

    def test_most_recent_prior_disclosure_selected(self):
        """signal_date 이전 가장 최근 공시가 선택된다."""
        signals = pd.DataFrame({
            "ticker": ["005930"],
            "signal_date": pd.to_datetime(["2024-09-01"]),
            "excess_return_20d": [1.0],
        })
        fund = self._fundamentals()
        result = join_signals_step11(signals, fund)
        # 2024-08-14 공시(2024-Q2)가 가장 최근 prior disclosure
        assert result["fundamental_report_period"].iloc[0] == "2024-Q2"

    def test_no_prior_disclosure_returns_none(self):
        """공시 이전 Signal: fundamental_report_period = None."""
        signals = pd.DataFrame({
            "ticker": ["005930"],
            "signal_date": pd.to_datetime(["2022-01-01"]),
            "excess_return_20d": [1.0],
        })
        fund = self._fundamentals()
        result = join_signals_step11(signals, fund)
        assert result["fundamental_report_period"].iloc[0] is None

    def test_yoy_fields_propagated_to_joined(self):
        """growth metrics 컬럼이 joined에 포함된다."""
        fund = self._fundamentals()
        result = join_signals_step11(self._signals(), fund)
        for col in ["revenue_yoy", "operating_income_yoy", "operating_margin"]:
            assert col in result.columns

    def test_empty_fundamentals_returns_signals_with_none_cols(self):
        result = join_signals_step11(self._signals(), pd.DataFrame())
        assert len(result) == 3
        assert "fundamental_report_period" in result.columns
        assert result["fundamental_report_period"].iloc[0] is None

    def test_future_disclosure_not_used(self):
        """signal_date 이후 공시는 절대 연결되지 않는다."""
        signals = pd.DataFrame({
            "ticker": ["005930"],
            "signal_date": pd.to_datetime(["2023-12-01"]),
            "excess_return_20d": [1.0],
        })
        fund = self._fundamentals()
        result = join_signals_step11(signals, fund)
        if result["fundamental_disclosure_date"].iloc[0] is not None:
            assert result["fundamental_disclosure_date"].iloc[0] < pd.Timestamp("2023-12-01")

    def test_ticker_isolation(self):
        """다른 종목의 실적이 연결되지 않는다."""
        signals = pd.DataFrame({
            "ticker": ["000660"],
            "signal_date": pd.to_datetime(["2024-06-01"]),
            "excess_return_20d": [1.0],
        })
        fund = self._fundamentals()  # 005930 데이터만
        result = join_signals_step11(signals, fund)
        assert result["fundamental_report_period"].iloc[0] is None

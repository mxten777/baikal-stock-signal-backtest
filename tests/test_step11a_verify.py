"""
STEP 11-A — 분기값 원본 검증 단위 테스트

공식 OpenDART fnlttSinglAcntAll 필드 정의:
  thstrm_amount     = 당기금액    = 분/반기 P&L이면 3개월(단일분기) 금액
  thstrm_add_amount = 당기누적금액 = Q2→H1 누적, Q3→9M 누적

따라서:
  Q1: thstrm_amount = Q1 단독 (= 누적과 동일)
  Q2: thstrm_amount = Q2 단독,  thstrm_add_amount = H1(Q1+Q2) 누적
  Q3: thstrm_amount = Q3 단독,  thstrm_add_amount = 9M(Q1+Q2+Q3) 누적
  Q4: thstrm_amount = FY 누적   → deaccumulate_quarters()에서 Q4 단독으로 변환

실제 DART API 호출 없음. 모든 외부 호출은 mock 처리.
"""
from __future__ import annotations
import pandas as pd
import pytest
from unittest.mock import MagicMock
from src.data_provider.dart_fundamental_provider import (
    _extract_add_amount,
    _extract_amount,
    _normalize_finstate,
    deaccumulate_quarters,
    deaccumulate_quarters_full,
    fetch_quarterly_fundamentals,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock DART 응답 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _dart_row(account_nm, thstrm_amount, thstrm_add_amount=None, sj_div="IS"):
    row = {"account_nm": account_nm, "thstrm_amount": thstrm_amount, "sj_div": sj_div}
    if thstrm_add_amount is not None:
        row["thstrm_add_amount"] = thstrm_add_amount
    return row


def _make_dart_q1_raw():
    """Q1: thstrm_amount = Q1 단독 (= thstrm_add_amount). Samsung 2024-Q1 예시."""
    return pd.DataFrame([
        _dart_row("매출액",     "71,915,601,000,000", "71,915,601,000,000"),
        _dart_row("영업이익",   "6,606,009,000,000",  "6,606,009,000,000"),
        _dart_row("당기순이익", "6,754,708,000,000",  "6,754,708,000,000"),
        _dart_row("자산총계",   "470,899,812,000,000", sj_div="BS"),
        _dart_row("자본총계",   "371,916,124,000,000", sj_div="BS"),
    ])


def _make_dart_q2_raw():
    """
    Q2 반기보고서.
    thstrm_amount     = Q2 단독(3개월) — 공식 DART 정의
    thstrm_add_amount = H1 누적(Q1+Q2)
    Samsung 2024-Q2: Q2_single=74.07조, H1=145.98조
    """
    return pd.DataFrame([
        _dart_row("매출액",     "74,068,302,000,000",  "145,983,903,000,000"),
        _dart_row("영업이익",   "10,443,878,000,000",  "17,049,887,000,000"),
        _dart_row("당기순이익", "9,841,345,000,000",   "16,596,053,000,000"),
        _dart_row("자산총계",   "485,757,698,000,000", sj_div="BS"),
        _dart_row("자본총계",   "383,526,671,000,000", sj_div="BS"),
    ])


def _make_dart_q2_raw_no_add():
    """thstrm_add_amount 컬럼 없는 Q2 응답."""
    return pd.DataFrame([
        {"account_nm": "매출액",     "thstrm_amount": "74,068,302,000,000",  "sj_div": "IS"},
        {"account_nm": "영업이익",   "thstrm_amount": "10,443,878,000,000",  "sj_div": "IS"},
        {"account_nm": "당기순이익", "thstrm_amount": "9,841,345,000,000",   "sj_div": "IS"},
        {"account_nm": "자산총계",   "thstrm_amount": "485,757,698,000,000", "sj_div": "BS"},
        {"account_nm": "자본총계",   "thstrm_amount": "383,526,671,000,000", "sj_div": "BS"},
    ])


def _make_dart_q3_raw():
    """
    Q3 3분기보고서.
    thstrm_amount     = Q3 단독(3개월)
    thstrm_add_amount = 9M 누적(Q1+Q2+Q3)
    Samsung 2024-Q3: Q3_single=79.10조, 9M=225.08조
    """
    return pd.DataFrame([
        _dart_row("매출액",     "79,098,731,000,000",  "225,082,634,000,000"),
        _dart_row("영업이익",   "9,183,371,000,000",   "26,233,258,000,000"),
        _dart_row("당기순이익", "10,100,904,000,000",  "26,696,957,000,000"),
        _dart_row("자산총계",   "491,307,317,000,000", sj_div="BS"),
        _dart_row("자본총계",   "386,281,363,000,000", sj_div="BS"),
    ])


def _make_dart_q4_raw():
    """Q4 사업보고서: thstrm_amount = FY 누적. Samsung 2024."""
    return pd.DataFrame([
        _dart_row("매출액",     "300,870,903,000,000"),
        _dart_row("영업이익",   "32,725,961,000,000"),
        _dart_row("당기순이익", "34,451,351,000,000"),
        _dart_row("자산총계",   "514,531,948,000,000", sj_div="BS"),
        _dart_row("자본총계",   "402,192,070,000,000", sj_div="BS"),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# _extract_add_amount — 당기누적금액 추출
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractAddAmount:
    def test_returns_h1_cumulative_for_q2(self):
        """Q2 반기보고서: thstrm_add_amount = H1 누적금액."""
        df = _make_dart_q2_raw()
        result = _extract_add_amount(df, ["매출액", "수익(매출액)"])
        assert result == pytest.approx(145_983_903_000_000.0)

    def test_returns_9m_cumulative_for_q3(self):
        """Q3 3분기보고서: thstrm_add_amount = 9M 누적금액."""
        df = _make_dart_q3_raw()
        assert _extract_add_amount(df, ["매출액"]) == pytest.approx(225_082_634_000_000.0)

    def test_falls_back_to_second_alias(self):
        df = _make_dart_q2_raw()
        assert _extract_add_amount(df, ["수익(매출액)", "매출액"]) == pytest.approx(145_983_903_000_000.0)

    def test_returns_none_when_column_missing(self):
        df = _make_dart_q2_raw_no_add()
        assert _extract_add_amount(df, ["매출액"]) is None

    def test_returns_none_when_account_not_found(self):
        assert _extract_add_amount(_make_dart_q2_raw(), ["없는계정"]) is None

    def test_returns_none_when_add_amount_is_nan(self):
        df = pd.DataFrame([{"account_nm": "매출액", "thstrm_amount": "100,000",
                             "thstrm_add_amount": float("nan"), "sj_div": "IS"}])
        assert _extract_add_amount(df, ["매출액"]) is None

    def test_q2_cumulative_equals_q1_plus_q2_single(self):
        """H1_누적 = Q1_thstrm + Q2_thstrm 교차검증."""
        q1 = _extract_amount(_make_dart_q1_raw(), ["매출액"])
        q2 = _extract_amount(_make_dart_q2_raw(), ["매출액"])
        h1 = _extract_add_amount(_make_dart_q2_raw(), ["매출액"])
        assert h1 == pytest.approx(q1 + q2, rel=1e-6)

    def test_q3_cumulative_equals_q1_q2_q3_sum(self):
        """9M_누적 = Q1 + Q2 + Q3 합계 교차검증."""
        q1 = _extract_amount(_make_dart_q1_raw(), ["매출액"])
        q2 = _extract_amount(_make_dart_q2_raw(), ["매출액"])
        q3 = _extract_amount(_make_dart_q3_raw(), ["매출액"])
        nm9 = _extract_add_amount(_make_dart_q3_raw(), ["매출액"])
        assert nm9 == pytest.approx(q1 + q2 + q3, rel=1e-6)

    def test_negative_cumulative_value(self):
        """음수 누적금액도 올바르게 파싱한다."""
        df = pd.DataFrame([{"account_nm": "당기순이익", "thstrm_amount": "-2,000,000",
                             "thstrm_add_amount": "-5,000,000", "sj_div": "IS"}])
        assert _extract_add_amount(df, ["당기순이익"]) == pytest.approx(-5_000_000.0)


# ─────────────────────────────────────────────────────────────────────────────
# _extract_amount — sj_div 필터링
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractAmountWithSjDiv:
    def test_pnl_filter_ignores_bs_rows(self):
        """IS/CIS 필터 적용 시 BS 행을 무시한다."""
        from src.data_provider.dart_fundamental_provider import _PNL_SJ_DIVS
        df = pd.DataFrame([
            {"account_nm": "매출액", "thstrm_amount": "999,999", "sj_div": "BS"},
            {"account_nm": "매출액", "thstrm_amount": "5,000,000", "sj_div": "IS"},
        ])
        assert _extract_amount(df, ["매출액"], sj_div_filter=_PNL_SJ_DIVS) == pytest.approx(5_000_000.0)

    def test_no_sj_div_column_falls_back_to_full_df(self):
        """sj_div 컬럼 없으면 필터 무시하고 전체 검색."""
        from src.data_provider.dart_fundamental_provider import _PNL_SJ_DIVS
        df = pd.DataFrame([{"account_nm": "매출액", "thstrm_amount": "5,000,000"}])
        assert _extract_amount(df, ["매출액"], sj_div_filter=_PNL_SJ_DIVS) == pytest.approx(5_000_000.0)

    def test_no_filter_returns_first_row(self):
        """필터 없으면 .iloc[0] 행을 반환한다."""
        df = pd.DataFrame([
            {"account_nm": "매출액", "thstrm_amount": "3,000,000", "sj_div": "CIS"},
            {"account_nm": "매출액", "thstrm_amount": "5,000,000", "sj_div": "IS"},
        ])
        assert _extract_amount(df, ["매출액"]) == pytest.approx(3_000_000.0)

    def test_filter_selects_is_over_cis_when_is_first(self):
        """IS 행이 먼저 있으면 해당 값을 반환한다."""
        from src.data_provider.dart_fundamental_provider import _PNL_SJ_DIVS
        df = pd.DataFrame([
            {"account_nm": "당기순이익", "thstrm_amount": "3,000,000", "sj_div": "IS"},
            {"account_nm": "당기순이익", "thstrm_amount": "3,100,000", "sj_div": "CIS"},
        ])
        assert _extract_amount(df, ["당기순이익"], sj_div_filter=_PNL_SJ_DIVS) == pytest.approx(3_000_000.0)


# ─────────────────────────────────────────────────────────────────────────────
# _normalize_finstate — thstrm_amount(3개월값) 기반
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizeFinstate:
    def test_q1_uses_thstrm_amount(self):
        """Q1: thstrm_amount = Q1 3개월값."""
        row = _normalize_finstate(_make_dart_q1_raw(), "005930", "2024-Q1", "2024-05-16")
        assert row["revenue"] == pytest.approx(71_915_601_000_000.0)
        assert row["operating_income"] == pytest.approx(6_606_009_000_000.0)
        assert row["net_income"] == pytest.approx(6_754_708_000_000.0)

    def test_q2_uses_thstrm_amount_single_quarter(self):
        """Q2: thstrm_amount = Q2 3개월 단독값 (thstrm_add_amount=H1 누적은 무시)."""
        row = _normalize_finstate(_make_dart_q2_raw(), "005930", "2024-Q2", "2024-08-14")
        assert row["revenue"] == pytest.approx(74_068_302_000_000.0)
        assert row["operating_income"] == pytest.approx(10_443_878_000_000.0)
        assert row["net_income"] == pytest.approx(9_841_345_000_000.0)

    def test_q2_does_not_store_h1_cumulative(self):
        """Q2 저장값은 H1 누적(145.98조)이 아니다."""
        row = _normalize_finstate(_make_dart_q2_raw(), "005930", "2024-Q2", "2024-08-14")
        assert row["revenue"] != pytest.approx(145_983_903_000_000.0)

    def test_q3_uses_thstrm_amount_single_quarter(self):
        """Q3: thstrm_amount = Q3 3개월 단독값."""
        row = _normalize_finstate(_make_dart_q3_raw(), "005930", "2024-Q3", "2024-11-14")
        assert row["revenue"] == pytest.approx(79_098_731_000_000.0)
        assert row["net_income"] == pytest.approx(10_100_904_000_000.0)

    def test_q3_does_not_store_9m_cumulative(self):
        """Q3 저장값은 9M 누적(225.08조)이 아니다."""
        row = _normalize_finstate(_make_dart_q3_raw(), "005930", "2024-Q3", "2024-11-14")
        assert row["revenue"] != pytest.approx(225_082_634_000_000.0)

    def test_q4_uses_thstrm_amount_fy_cumulative(self):
        """Q4: thstrm_amount = FY 누적 그대로 저장 (deaccumulate_quarters에서 변환)."""
        row = _normalize_finstate(_make_dart_q4_raw(), "005930", "2024-Q4", "2025-03-11")
        assert row["revenue"] == pytest.approx(300_870_903_000_000.0)

    def test_bs_items_extracted_correctly(self):
        """BS 항목(자산총계, 자본총계) 올바르게 추출."""
        row = _normalize_finstate(_make_dart_q2_raw(), "005930", "2024-Q2", "2024-08-14")
        assert row["total_assets"] == pytest.approx(485_757_698_000_000.0)
        assert row["total_equity"] == pytest.approx(383_526_671_000_000.0)

    def test_metadata_fields_set_correctly(self):
        row = _normalize_finstate(_make_dart_q1_raw(), "005930", "2024-Q1", "2024-05-16")
        assert row["ticker"] == "005930"
        assert row["report_period"] == "2024-Q1"
        assert row["disclosure_date"] == pd.Timestamp("2024-05-16")

    def test_negative_operating_income(self):
        """음수 영업이익을 올바르게 추출한다."""
        df = pd.DataFrame([
            {"account_nm": "매출액",     "thstrm_amount": "5,000,000",  "sj_div": "IS"},
            {"account_nm": "영업이익",   "thstrm_amount": "-1,200,000", "sj_div": "IS"},
            {"account_nm": "당기순이익", "thstrm_amount": "-800,000",   "sj_div": "IS"},
            {"account_nm": "자산총계",   "thstrm_amount": "50,000,000", "sj_div": "BS"},
            {"account_nm": "자본총계",   "thstrm_amount": "20,000,000", "sj_div": "BS"},
        ])
        row = _normalize_finstate(df, "000660", "2023-Q3", "2023-11-14")
        assert row["operating_income"] == pytest.approx(-1_200_000.0)
        assert row["net_income"] == pytest.approx(-800_000.0)

    def test_missing_account_returns_none(self):
        df = pd.DataFrame([{"account_nm": "매출액", "thstrm_amount": "5,000,000", "sj_div": "IS"}])
        row = _normalize_finstate(df, "005930", "2024-Q2", "2024-08-14")
        assert row["operating_income"] is None
        assert row["net_income"] is None

    def test_alternative_revenue_alias(self):
        """'수익(매출액)' 별칭도 매출액으로 인식한다."""
        df = pd.DataFrame([
            {"account_nm": "수익(매출액)", "thstrm_amount": "10,000,000", "sj_div": "IS"},
            {"account_nm": "영업이익",     "thstrm_amount": "1,000,000",  "sj_div": "IS"},
            {"account_nm": "당기순이익",   "thstrm_amount": "700,000",    "sj_div": "IS"},
            {"account_nm": "자산총계",     "thstrm_amount": "100,000,000","sj_div": "BS"},
            {"account_nm": "자본총계",     "thstrm_amount": "40,000,000", "sj_div": "BS"},
        ])
        row = _normalize_finstate(df, "035720", "2024-Q2", "2024-09-26")
        assert row["revenue"] == pytest.approx(10_000_000.0)

    def test_quarterly_net_income_alias_variants(self):
        """분기순이익, 반기순이익, 당기순이익(손실) alias 인식."""
        for alias in ("분기순이익", "반기순이익", "당기순이익(손실)"):
            df = pd.DataFrame([
                {"account_nm": "매출액",  "thstrm_amount": "5,000,000",  "sj_div": "IS"},
                {"account_nm": alias,     "thstrm_amount": "200,000",    "sj_div": "IS"},
                {"account_nm": "영업이익","thstrm_amount": "300,000",    "sj_div": "IS"},
                {"account_nm": "자산총계","thstrm_amount": "10,000,000", "sj_div": "BS"},
                {"account_nm": "자본총계","thstrm_amount": "4,000,000",  "sj_div": "BS"},
            ])
            row = _normalize_finstate(df, "005930", "2022-Q2", "2022-08-16")
            assert row["net_income"] == pytest.approx(200_000.0), f"alias={alias}"

    def test_duplicate_account_prefers_is_over_bs(self):
        """동일 계정이 IS/BS 양쪽에 있으면 IS 행을 선택한다."""
        df = pd.DataFrame([
            {"account_nm": "당기순이익", "thstrm_amount": "999",      "sj_div": "BS"},
            {"account_nm": "당기순이익", "thstrm_amount": "200,000",  "sj_div": "IS"},
            {"account_nm": "매출액",     "thstrm_amount": "1,000,000","sj_div": "IS"},
            {"account_nm": "영업이익",   "thstrm_amount": "100,000",  "sj_div": "IS"},
            {"account_nm": "자산총계",   "thstrm_amount": "5,000,000","sj_div": "BS"},
            {"account_nm": "자본총계",   "thstrm_amount": "2,000,000","sj_div": "BS"},
        ])
        row = _normalize_finstate(df, "005930", "2024-Q2", "2024-08-14")
        assert row["net_income"] == pytest.approx(200_000.0)


# ─────────────────────────────────────────────────────────────────────────────
# deaccumulate_quarters_full — 누적값 입력 → 단일분기 변환 (검증/폴백용)
# ─────────────────────────────────────────────────────────────────────────────

def _make_full_cumulative_df(ticker="005930"):
    """누적값 구조: Q1=단독, Q2=H1, Q3=9M, Q4=FY."""
    return pd.DataFrame({
        "ticker": [ticker] * 4,
        "report_period": ["2024-Q1","2024-Q2","2024-Q3","2024-Q4"],
        "disclosure_date": pd.to_datetime(["2024-05-16","2024-08-14","2024-11-14","2025-03-11"]),
        "revenue":          [100.0, 210.0, 300.0, 420.0],
        "operating_income": [10.0,  21.0,  30.0,  42.0],
        "net_income":       [8.0,   17.0,  24.0,  34.0],
        "total_assets":     [500.0, 510.0, 520.0, 530.0],
        "total_equity":     [200.0, 205.0, 210.0, 215.0],
    })


class TestDeaccumulateQuartersFull:
    def test_q1_unchanged(self):
        r = deaccumulate_quarters_full(_make_full_cumulative_df())
        q1 = r[r["report_period"] == "2024-Q1"].iloc[0]
        assert q1["revenue"] == pytest.approx(100.0)

    def test_q2_single_h1_minus_q1(self):
        r = deaccumulate_quarters_full(_make_full_cumulative_df())
        q2 = r[r["report_period"] == "2024-Q2"].iloc[0]
        assert q2["revenue"] == pytest.approx(110.0)   # 210-100
        assert q2["net_income"] == pytest.approx(9.0)  # 17-8

    def test_q3_single_9m_minus_h1_original(self):
        """원본 H1 스냅샷 사용 — 변환된 Q2가 아님."""
        r = deaccumulate_quarters_full(_make_full_cumulative_df())
        q3 = r[r["report_period"] == "2024-Q3"].iloc[0]
        assert q3["revenue"] == pytest.approx(90.0)   # 300-210

    def test_q4_single_fy_minus_9m_original(self):
        r = deaccumulate_quarters_full(_make_full_cumulative_df())
        q4 = r[r["report_period"] == "2024-Q4"].iloc[0]
        assert q4["revenue"] == pytest.approx(120.0)  # 420-300

    def test_bs_unchanged(self):
        r = deaccumulate_quarters_full(_make_full_cumulative_df())
        assert r[r["report_period"] == "2024-Q4"].iloc[0]["total_assets"] == pytest.approx(530.0)

    def test_negative_values(self):
        """누적 음수 영업이익도 올바르게 변환한다."""
        df = pd.DataFrame({
            "ticker": ["000660"]*4,
            "report_period": ["2023-Q1","2023-Q2","2023-Q3","2023-Q4"],
            "disclosure_date": pd.to_datetime(["2023-05-15","2023-08-14","2023-11-14","2024-03-19"]),
            "revenue":          [5.09, 12.39, 21.45, 32.75],
            "operating_income": [-3.40, -6.28, -8.07, -7.73],
            "net_income":       [-2.58, -5.57, -7.75, -9.13],
            "total_assets":     [104.0]*4, "total_equity": [61.0]*4,
        })
        r = deaccumulate_quarters_full(df)
        assert r[r["report_period"]=="2023-Q2"].iloc[0]["operating_income"] == pytest.approx(-2.88, rel=1e-3)
        assert r[r["report_period"]=="2023-Q3"].iloc[0]["operating_income"] == pytest.approx(-1.79, rel=1e-3)
        assert r[r["report_period"]=="2023-Q4"].iloc[0]["operating_income"] == pytest.approx(0.34, rel=1e-3)

    def test_missing_q1_makes_q2_none(self):
        df = _make_full_cumulative_df()
        df = df[df["report_period"] != "2024-Q1"].reset_index(drop=True)
        r = deaccumulate_quarters_full(df)
        assert pd.isna(r[r["report_period"]=="2024-Q2"].iloc[0]["revenue"])

    def test_missing_q2_makes_q3_none(self):
        df = _make_full_cumulative_df()
        df = df[df["report_period"] != "2024-Q2"].reset_index(drop=True)
        r = deaccumulate_quarters_full(df)
        q3 = r[r["report_period"]=="2024-Q3"].iloc[0]["revenue"]
        assert q3 is None or pd.isna(q3)

    def test_missing_q3_makes_q4_none(self):
        df = _make_full_cumulative_df()
        df = df[df["report_period"] != "2024-Q3"].reset_index(drop=True)
        r = deaccumulate_quarters_full(df)
        q4 = r[r["report_period"]=="2024-Q4"].iloc[0]["revenue"]
        assert q4 is None or pd.isna(q4)

    def test_empty_df(self):
        assert deaccumulate_quarters_full(pd.DataFrame()).empty

    def test_multiple_tickers_independent(self):
        combined = pd.concat([_make_full_cumulative_df("005930"), _make_full_cumulative_df("000660")], ignore_index=True)
        r = deaccumulate_quarters_full(combined)
        for t in ["005930","000660"]:
            assert r[(r["ticker"]==t)&(r["report_period"]=="2024-Q2")].iloc[0]["revenue"] == pytest.approx(110.0)

    def test_does_not_cross_years(self):
        df = pd.DataFrame({
            "ticker": ["005930"]*8,
            "report_period": ["2023-Q1","2023-Q2","2023-Q3","2023-Q4","2024-Q1","2024-Q2","2024-Q3","2024-Q4"],
            "disclosure_date": pd.to_datetime(["2023-05-16","2023-08-14","2023-11-14","2024-03-12",
                                                "2024-05-16","2024-08-14","2024-11-14","2025-03-11"]),
            "revenue":          [100.,200.,290.,390.,90.,195.,285.,380.],
            "operating_income": [10.,20.,29.,39.,9.,19.,28.,37.],
            "net_income":       [8.,16.,23.,31.,7.,15.,22.,29.],
            "total_assets": [500.]*8, "total_equity": [200.]*8,
        })
        r = deaccumulate_quarters_full(df)
        assert r[r["report_period"]=="2023-Q2"].iloc[0]["revenue"] == pytest.approx(100.)
        assert r[r["report_period"]=="2024-Q2"].iloc[0]["revenue"] == pytest.approx(105.)


# ─────────────────────────────────────────────────────────────────────────────
# 통합: normalize(thstrm_amount=3개월값) → deaccumulate_quarters(Q4 전용)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipelineThstrmAmount:
    def _make_mock_dart(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame([
            {"report_nm": "2024년 1분기보고서", "rcept_dt": "20240516"},
            {"report_nm": "2024년 반기보고서",  "rcept_dt": "20240814"},
            {"report_nm": "2024년 3분기보고서", "rcept_dt": "20241114"},
            {"report_nm": "2024년 사업보고서",  "rcept_dt": "20250311"},
        ])
        def _fake(ticker, year, reprt_code, fs_div="CFS"):
            return {
                "11013": _make_dart_q1_raw,
                "11012": _make_dart_q2_raw,
                "11014": _make_dart_q3_raw,
                "11011": _make_dart_q4_raw,
            }.get(reprt_code, pd.DataFrame)()
        dart.finstate_all.side_effect = _fake
        return dart

    def test_q1_stored_as_single(self):
        df = fetch_quarterly_fundamentals(self._make_mock_dart(), "005930", 2024, 2024)
        assert df[df["report_period"]=="2024-Q1"].iloc[0]["revenue"] == pytest.approx(71_915_601_000_000.0)

    def test_q2_stored_as_single_not_h1(self):
        df = fetch_quarterly_fundamentals(self._make_mock_dart(), "005930", 2024, 2024)
        q2 = df[df["report_period"]=="2024-Q2"].iloc[0]
        assert q2["revenue"] == pytest.approx(74_068_302_000_000.0)
        assert q2["revenue"] != pytest.approx(145_983_903_000_000.0)

    def test_q3_stored_as_single_not_9m(self):
        df = fetch_quarterly_fundamentals(self._make_mock_dart(), "005930", 2024, 2024)
        q3 = df[df["report_period"]=="2024-Q3"].iloc[0]
        assert q3["revenue"] == pytest.approx(79_098_731_000_000.0)
        assert q3["revenue"] != pytest.approx(225_082_634_000_000.0)

    def test_q4_fy_minus_q1_q2_q3(self):
        df = fetch_quarterly_fundamentals(self._make_mock_dart(), "005930", 2024, 2024)
        q4 = df[df["report_period"]=="2024-Q4"].iloc[0]
        expected = 300_870_903_000_000.0 - 71_915_601_000_000.0 - 74_068_302_000_000.0 - 79_098_731_000_000.0
        assert q4["revenue"] == pytest.approx(expected, rel=1e-6)

    def test_annual_sum_equals_fy(self):
        df = fetch_quarterly_fundamentals(self._make_mock_dart(), "005930", 2024, 2024)
        assert df["revenue"].dropna().sum() == pytest.approx(300_870_903_000_000.0, rel=1e-6)

    def test_cross_check_q2_thstrm_vs_add_amount(self):
        """Q1_thstrm + Q2_thstrm == Q2_thstrm_add_amount(H1 누적) 교차검증."""
        q1 = _extract_amount(_make_dart_q1_raw(), ["매출액"])
        q2 = _extract_amount(_make_dart_q2_raw(), ["매출액"])
        h1 = _extract_add_amount(_make_dart_q2_raw(), ["매출액"])
        assert h1 == pytest.approx(q1 + q2, rel=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# CFS/OFS fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestCfsOfsFallback:
    def test_ofs_used_when_cfs_fails(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame([{"report_nm": "2024년 1분기보고서", "rcept_dt": "20240515"}])
        count = {"cfs": 0, "ofs": 0}
        def _fake(ticker, year, reprt_code, fs_div="CFS"):
            if fs_div == "CFS":
                count["cfs"] += 1
                raise Exception("CFS 없음")
            count["ofs"] += 1
            return pd.DataFrame([
                {"account_nm": "매출액",    "thstrm_amount": "5,000,000", "sj_div": "IS"},
                {"account_nm": "영업이익",  "thstrm_amount": "500,000",   "sj_div": "IS"},
                {"account_nm": "당기순이익","thstrm_amount": "400,000",   "sj_div": "IS"},
                {"account_nm": "자산총계",  "thstrm_amount": "20,000,000","sj_div": "BS"},
                {"account_nm": "자본총계",  "thstrm_amount": "8,000,000", "sj_div": "BS"},
            ])
        dart.finstate_all.side_effect = _fake
        df = fetch_quarterly_fundamentals(dart, "005930", 2024, 2024)
        assert not df.empty
        assert count["cfs"] > 0 and count["ofs"] > 0
        assert df.iloc[0]["revenue"] == pytest.approx(5_000_000.0)

    def test_both_cfs_ofs_fail_skips_quarter(self):
        dart = MagicMock()
        dart.list.return_value = pd.DataFrame([
            {"report_nm": "2024년 1분기보고서", "rcept_dt": "20240515"},
            {"report_nm": "2024년 반기보고서",  "rcept_dt": "20240814"},
        ])
        dart.finstate_all.side_effect = Exception("API 오류")
        assert fetch_quarterly_fundamentals(dart, "005930", 2024, 2024).empty

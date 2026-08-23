"""
DART Open API 기반 분기별 기업 실적 Provider

데이터 소스: https://opendart.fss.or.kr
필요 조건: DART API Key (dart.fss.or.kr 에서 무료 신청)

제공 항목: 분기별 매출액, 영업이익, 당기순이익, 자산총계, 자본총계
제공 단위: 연결재무제표(CFS) 우선, 없으면 별도재무제표(OFS)
Look-ahead Bias 방지: 공시일(disclosure_date) 기준으로 Signal과 연결

공식 OpenDART fnlttSinglAcntAll 필드 정의:
  thstrm_amount     = 당기금액   = 분/반기 P&L이면 [3개월] 단일분기 값
  thstrm_add_amount = 당기누적금액 = Q2=H1누적, Q3=9M누적 (Q4=FY와 동일)

  Q1  thstrm_amount = Q1 단독 (= 누적이기도 함)
  Q2  thstrm_amount = Q2 단독(3개월)  /  thstrm_add_amount = H1 누적
  Q3  thstrm_amount = Q3 단독(3개월)  /  thstrm_add_amount = 9M 누적
  Q4  thstrm_amount = FY 누적  →  Q4_single = FY − Q1 − Q2 − Q3

따라서 Q1/Q2/Q3 thstrm_amount는 변환 없이 단일분기로 사용한다.
Q4만 deaccumulate_quarters()에서 단일분기로 변환한다.

Signal Engine과 연결하지 않는 독립 Provider.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# reprt_code → (분기 구분, 보고서명 키워드)
_REPRT_MAP: dict[str, tuple[str, str]] = {
    "11013": ("Q1", "1분기"),
    "11012": ("Q2", "반기"),
    "11014": ("Q3", "3분기"),
    "11011": ("Q4", "사업"),
}

# 재무항목 계정명 후보 (연결재무제표 기준)
_ACCOUNT_ALIASES: dict[str, list[str]] = {
    "revenue": ["매출액", "수익(매출액)", "영업수익"],
    "operating_income": ["영업이익", "영업이익(손실)"],
    "net_income": ["당기순이익", "반기순이익", "분기순이익",
                   "당기순이익(손실)", "반기순이익(손실)", "분기순이익(손실)"],
    "total_assets": ["자산총계"],
    "total_equity": ["자본총계"],
}


def _parse_amount(val) -> float | None:
    """DART 금액 문자열(쉼표 포함)을 float으로 변환한다."""
    if pd.isna(val):
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except ValueError:
        return None


# DART sj_div 코드 (재무제표 구분)
_PNL_SJ_DIVS: frozenset[str] = frozenset({"IS", "CIS"})


def _extract_amount(
    df: pd.DataFrame,
    aliases: list[str],
    sj_div_filter: frozenset[str] | None = None,
) -> float | None:
    """재무제표 DataFrame에서 계정명으로 thstrm_amount (당기 3개월 금액)를 추출한다.

    sj_div_filter를 지정하면 해당 재무제표 구분(IS/CIS 등) 행만 검색한다.
    sj_div 컬럼이 없는 경우(테스트 mock 등)에는 필터를 무시한다.
    """
    subset = df
    if sj_div_filter is not None and "sj_div" in df.columns:
        filtered = df[df["sj_div"].isin(sj_div_filter)]
        if not filtered.empty:
            subset = filtered  # 필터 결과가 있을 때만 적용
    for alias in aliases:
        rows = subset[subset["account_nm"] == alias]
        if not rows.empty:
            return _parse_amount(rows.iloc[0]["thstrm_amount"])
    return None


def _extract_add_amount(df: pd.DataFrame, aliases: list[str]) -> float | None:
    """thstrm_add_amount (당기누적금액) 컬럼을 추출한다.

    공식 DART 정의: thstrm_add_amount = 당기누적금액
      Q2 반기보고서 → H1 누적 (Q1 + Q2)
      Q3 3분기보고서 → 9M 누적 (Q1 + Q2 + Q3)

    검증·cross-check 용도. 단일분기 기본 소스는 thstrm_amount를 사용한다.
    컬럼이 없거나 값이 비어 있으면 None을 반환한다.
    """
    if "thstrm_add_amount" not in df.columns:
        return None
    for alias in aliases:
        rows = df[df["account_nm"] == alias]
        if not rows.empty:
            parsed = _parse_amount(rows.iloc[0]["thstrm_add_amount"])
            if parsed is not None:
                return parsed
    return None


def _normalize_finstate(
    raw_df: pd.DataFrame, ticker: str, report_period: str, disclosure_date: str
) -> dict:
    """finstate_all 응답 DataFrame에서 핵심 재무항목을 추출한다.

    공식 DART 정의에 따라 모든 분기에서 thstrm_amount(당기 3개월 금액)를 사용한다.
    Q2/Q3에서도 thstrm_amount = 3개월 단일분기값이므로 변환 없이 그대로 저장한다.
    Q4(사업보고서)만 thstrm_amount = FY 누적이므로 deaccumulate_quarters()에서 변환한다.

    중복 계정 행 처리: sj_div = IS/CIS 행을 우선 검색하여 .iloc[0] 순서 의존성을 최소화한다.
    """
    row: dict = {
        "ticker": ticker,
        "report_period": report_period,
        "disclosure_date": pd.to_datetime(disclosure_date),
    }
    for field, aliases in _ACCOUNT_ALIASES.items():
        is_pnl = field in set(_PNL_FIELDS)
        # P&L 항목은 손익계산서(IS/CIS) 행 우선 — BS 행에 동일 계정이 있어도 제외
        sj_filter = _PNL_SJ_DIVS if is_pnl else None
        row[field] = _extract_amount(raw_df, aliases, sj_div_filter=sj_filter)
    return row


def fetch_quarterly_fundamentals(
    dart,  # OpenDartReader instance
    ticker: str,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    분기별 기업 실적 데이터를 DART API에서 수집한다.

    Parameters
    ----------
    dart : OpenDartReader
        API key로 초기화된 OpenDartReader 인스턴스
    ticker : str
        종목코드 (6자리 문자열, 예: '005930')
    start_year : int
        수집 시작 연도
    end_year : int
        수집 종료 연도

    Returns
    -------
    DataFrame
        columns: ticker, report_period, disclosure_date,
                 revenue, operating_income, net_income,
                 total_assets, total_equity
    """
    # 공시 목록으로 disclosure_date 확보
    disclosure_map = _build_disclosure_map(dart, ticker, start_year, end_year)

    rows: list[dict] = []
    for year in range(start_year, end_year + 1):
        for reprt_code, (quarter_label, _) in _REPRT_MAP.items():
            period_key = f"{year}-{quarter_label}"
            disclosure_date = disclosure_map.get(period_key)
            if disclosure_date is None:
                continue  # 해당 기간 공시 없음

            try:
                raw = dart.finstate_all(ticker, year, reprt_code=reprt_code, fs_div="CFS")
            except Exception:
                # CFS 없으면 OFS로 재시도
                try:
                    raw = dart.finstate_all(ticker, year, reprt_code=reprt_code, fs_div="OFS")
                except Exception:
                    continue

            if raw is None or raw.empty:
                continue

            row = _normalize_finstate(raw, ticker, period_key, disclosure_date)
            rows.append(row)

    if not rows:
        return pd.DataFrame(
            columns=[
                "ticker", "report_period", "disclosure_date",
                "revenue", "operating_income", "net_income",
                "total_assets", "total_equity",
            ]
        )

    df = pd.DataFrame(rows)
    df["disclosure_date"] = pd.to_datetime(df["disclosure_date"])
    df = deaccumulate_quarters(df)
    return df.sort_values("disclosure_date").reset_index(drop=True)


def _build_disclosure_map(
    dart, ticker: str, start_year: int, end_year: int
) -> dict[str, str]:
    """
    ticker의 정기공시 목록에서 각 분기별 공시일을 찾는다.

    반환: {"2024-Q1": "2024-05-15", ...}

    DART 보고서명 형식 예:
      "분기보고서 (2024.03)"  → 2024-Q1
      "반기보고서 (2024.06)"  → 2024-Q2
      "분기보고서 (2024.09)"  → 2024-Q3
      "사업보고서 (2023.12)"  → 2023-Q4
    """
    try:
        lst = dart.list(
            ticker,
            start=f"{start_year - 1}-01-01",
            end=f"{end_year}-12-31",
            kind="A",  # 정기공시
        )
    except Exception:
        return {}

    if lst is None or lst.empty:
        return {}

    result: dict[str, str] = {}
    for _, row in lst.iterrows():
        report_nm = str(row.get("report_nm", ""))
        rcept_dt = str(row.get("rcept_dt", ""))
        if len(rcept_dt) != 8:
            continue
        disclosure = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"

        key = _classify_report(report_nm, rcept_dt)
        if key is None:
            continue

        # 더 이른 공시일(최초 공시)을 우선
        if key not in result or disclosure < result[key]:
            result[key] = disclosure

    return result


def _classify_report(report_nm: str, rcept_dt: str) -> str | None:
    """
    보고서명·수신일로부터 분기 키 "YYYY-QN" 을 반환한다.

    우선순위:
      1. "(YYYY.MM)" 패턴 — DART 실제 API 응답에서 가장 신뢰성 높음
      2. 텍스트 기반("1분기"/"3분기"/"반기"/"사업") — 테스트 목(mock) 데이터 호환
    """
    _MONTH_TO_Q = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}

    # Pattern 1: "(YYYY.MM)" in report name
    period_match = re.search(r'\((\d{4})\.(\d{2})\)', report_nm)
    if period_match:
        year = int(period_match.group(1))
        month = int(period_match.group(2))
        q = _MONTH_TO_Q.get(month)
        return f"{year}-{q}" if q else None

    # Pattern 2: text keywords + year in report name
    year_candidates = [int(y) for y in re.findall(r"(20\d{2})", report_nm)]
    filing_month = int(rcept_dt[4:6]) if len(rcept_dt) == 8 else 0
    filing_year = int(rcept_dt[:4]) if len(rcept_dt) == 8 else 0

    if year_candidates:
        rep_year = year_candidates[0]
        if "1분기" in report_nm:
            return f"{rep_year}-Q1"
        if "반기" in report_nm:
            return f"{rep_year}-Q2"
        if "3분기" in report_nm:
            return f"{rep_year}-Q3"
        if "사업" in report_nm:
            return f"{rep_year}-Q4"
        if "분기" in report_nm:
            if 3 <= filing_month <= 7:
                return f"{rep_year}-Q1"
            if 9 <= filing_month <= 12:
                return f"{rep_year}-Q3"
    else:
        # No year in name: derive from filing date
        if "사업" in report_nm:
            rep_year = filing_year - (1 if filing_month <= 4 else 0)
            return f"{rep_year}-Q4"
        if "반기" in report_nm:
            return f"{filing_year}-Q2"
        if "1분기" in report_nm or ("분기" in report_nm and 3 <= filing_month <= 7):
            return f"{filing_year}-Q1"
        if "3분기" in report_nm or ("분기" in report_nm and 9 <= filing_month <= 12):
            return f"{filing_year}-Q3"

    return None


def join_signals_to_fundamentals(
    signals: pd.DataFrame,
    fundamentals: pd.DataFrame,
) -> pd.DataFrame:
    """
    Signal 발생일 기준으로 그 날짜 이전에 공시된 가장 최근 분기 실적을 연결한다.
    Look-ahead Bias를 방지하기 위해 signal_date > disclosure_date 인 것만 사용한다.
    """
    if fundamentals.empty:
        return signals.copy()

    fund_sorted = fundamentals.sort_values("disclosure_date").reset_index(drop=True)

    joined_rows: list[dict] = []
    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        sig_date = sig["signal_date"]

        ticker_fund = fund_sorted[
            (fund_sorted["ticker"] == ticker)
            & (fund_sorted["disclosure_date"] < sig_date)
        ]

        if ticker_fund.empty:
            matched = {
                "fund_report_period": None,
                "fund_disclosure_date": None,
                "fund_revenue": None,
                "fund_operating_income": None,
                "fund_net_income": None,
                "fund_total_assets": None,
                "fund_total_equity": None,
            }
        else:
            latest = ticker_fund.iloc[-1]
            matched = {
                "fund_report_period": latest["report_period"],
                "fund_disclosure_date": latest["disclosure_date"],
                "fund_revenue": latest["revenue"],
                "fund_operating_income": latest["operating_income"],
                "fund_net_income": latest["net_income"],
                "fund_total_assets": latest.get("total_assets"),
                "fund_total_equity": latest.get("total_equity"),
            }
        joined_rows.append(matched)

    fund_df = pd.DataFrame(joined_rows, index=signals.index)
    return pd.concat([signals.reset_index(drop=True), fund_df.reset_index(drop=True)], axis=1)


def compute_yoy_growth(
    fundamentals: pd.DataFrame,
) -> pd.DataFrame:
    """
    각 분기에 대해 YoY Revenue Growth, YoY Operating Income Growth,
    Operating Margin을 계산한다.
    """
    df = fundamentals.copy()
    df = df.sort_values(["ticker", "report_period"]).reset_index(drop=True)

    # 전년 동기 매칭 (예: 2024-Q1 → 2023-Q1)
    # 각 분기 데이터를 다음 해 동일 분기의 "전년" 데이터로 등록한다.
    prev_map: dict[tuple, dict] = {}
    for _, row in df.iterrows():
        period = row["report_period"]  # "2023-Q1"
        try:
            year_str, q = period.split("-")
            next_year_period = f"{int(year_str) + 1}-{q}"  # "2024-Q1"
        except ValueError:
            continue
        key = (row["ticker"], next_year_period)
        prev_map[key] = {
            "prev_revenue": row["revenue"],
            "prev_operating_income": row["operating_income"],
        }

    yoy_rev: list = []
    yoy_oi: list = []
    op_margin: list = []

    for _, row in df.iterrows():
        key = (row["ticker"], row["report_period"])
        prev = prev_map.get(key, {})

        prev_rev = prev.get("prev_revenue")
        prev_oi = prev.get("prev_operating_income")
        rev = row["revenue"]
        oi = row["operating_income"]

        yoy_rev.append(
            (rev / prev_rev - 1) if rev is not None and prev_rev and prev_rev != 0 else None
        )
        yoy_oi.append(
            (oi / prev_oi - 1) if oi is not None and prev_oi and prev_oi != 0 else None
        )
        op_margin.append(
            (oi / rev) if oi is not None and rev and rev != 0 else None
        )

    df["yoy_revenue_growth"] = yoy_rev
    df["yoy_operating_income_growth"] = yoy_oi
    df["operating_margin"] = op_margin
    return df


def save_fundamentals(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "fundamentals_quarterly.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — 분기 누적값 → 단일분기 변환
# ─────────────────────────────────────────────────────────────────────────────

# P&L 항목만 단일분기 변환 대상 (BS 항목은 기말 잔액이므로 변환 불필요)
_PNL_FIELDS = ("revenue", "operating_income", "net_income")

# 분기 순서 맵 (quarter label → preceding quarter label)
_PREV_QUARTER: dict[str, str] = {"Q2": "Q1", "Q3": "Q2", "Q4": "Q3"}


def deaccumulate_quarters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Q4(사업보고서) 연간 누적값을 단일분기값으로 변환한다.

    전제: Q1/Q2/Q3 P&L은 _normalize_finstate()에서 thstrm_add_amount(당분기값)를
    우선 사용했으므로 이미 단일분기 상태다. Q4만 FY 누적으로 남아 있다.

    - Q1: 그대로 (단일분기)
    - Q2: 그대로 (thstrm_add_amount로 수집된 단일분기)
    - Q3: 그대로 (thstrm_add_amount로 수집된 단일분기)
    - Q4: FY 누적 − (Q1 + Q2 + Q3) → 단일분기

    thstrm_add_amount 폴백으로 Q2/Q3가 누적값으로 저장된 경우에는
    deaccumulate_quarters_full()을 사용한다.

    total_assets, total_equity 등 BS 항목은 변환하지 않는다.
    """
    if df.empty:
        return df.copy()

    df = df.copy()

    q4_rows = df[df["report_period"].str.endswith("-Q4", na=False)]
    for idx in q4_rows.index:
        row = df.loc[idx]
        year = str(row["report_period"])[:4]
        ticker = row["ticker"]

        q123_periods = [f"{year}-Q1", f"{year}-Q2", f"{year}-Q3"]
        q123_df = df[(df["ticker"] == ticker) & (df["report_period"].isin(q123_periods))]

        for field in _PNL_FIELDS:
            fy_val = row[field]
            if fy_val is None or (isinstance(fy_val, float) and pd.isna(fy_val)):
                df.at[idx, field] = None
                continue
            q123_vals = q123_df[field].dropna()
            if len(q123_vals) != 3:
                # Q1/Q2/Q3 중 하나라도 없으면 Q4 단일분기 계산 불가
                df.at[idx, field] = None
            else:
                df.at[idx, field] = fy_val - float(q123_vals.sum())

    return df.sort_values(["ticker", "report_period"]).reset_index(drop=True)


def deaccumulate_quarters_full(df: pd.DataFrame) -> pd.DataFrame:
    """
    누적값 기준 DataFrame을 단일분기 DataFrame으로 변환한다.

    입력 형태 (누적값이 저장된 경우):
      Q1 = Q1 단독  /  Q2 = H1 누적  /  Q3 = 9M 누적  /  Q4 = FY 누적

    변환 규칙:
      Q1: 그대로 (이미 단일분기)
      Q2_single = H1_누적 − Q1
      Q3_single = 9M_누적 − H1_누적  (원본 H1 스냅샷 사용)
      Q4_single = FY_누적 − 9M_누적  (원본 9M 스냅샷 사용)

    사용 목적:
    - thstrm_add_amount(당기누적금액) 기반 cross-check / 검증
    - 일부 구형 공시에서 thstrm_amount가 누적값으로 잘못 저장된 경우 복구

    정상 DART 처리(thstrm_amount = 3개월값) 경로에서는 호출하지 않는다.
    BS 항목(total_assets, total_equity)은 변환하지 않는다.
    """
    if df.empty:
        return df.copy()

    df = df.copy()

    def _val(row_or_none, field: str) -> float | None:
        if row_or_none is None:
            return None
        v = row_or_none[field]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)

    for ticker_val in df["ticker"].unique():
        t_mask = df["ticker"] == ticker_val
        years = df.loc[t_mask, "report_period"].str[:4].unique()

        for year in years:
            # Snapshot original cumulative values before any write-back
            snap: dict[str, dict] = {}
            for q in ("Q1", "Q2", "Q3", "Q4"):
                mask = t_mask & (df["report_period"] == f"{year}-{q}")
                if mask.any():
                    idx = df.index[mask][0]
                    snap[q] = {"idx": idx, "vals": {f: df.at[idx, f] for f in _PNL_FIELDS}}

            # Q2 = H1 - Q1
            if "Q2" in snap:
                if "Q1" in snap:
                    for field in _PNL_FIELDS:
                        h1 = _val(snap["Q2"]["vals"], field)
                        q1 = _val(snap["Q1"]["vals"], field)
                        if h1 is not None and q1 is not None:
                            df.at[snap["Q2"]["idx"], field] = h1 - q1
                        else:
                            df.at[snap["Q2"]["idx"], field] = None
                else:
                    for field in _PNL_FIELDS:
                        df.at[snap["Q2"]["idx"], field] = None

            # Q3 = 9M - H1  (using original H1 snapshot, not converted Q2)
            if "Q3" in snap:
                if "Q2" in snap:
                    for field in _PNL_FIELDS:
                        nm9 = _val(snap["Q3"]["vals"], field)
                        h1 = _val(snap["Q2"]["vals"], field)   # original H1
                        if nm9 is not None and h1 is not None:
                            df.at[snap["Q3"]["idx"], field] = nm9 - h1
                        else:
                            df.at[snap["Q3"]["idx"], field] = None
                else:
                    for field in _PNL_FIELDS:
                        df.at[snap["Q3"]["idx"], field] = None

            # Q4 = FY - 9M  (using original 9M snapshot, not converted Q3)
            if "Q4" in snap:
                if "Q3" in snap:
                    for field in _PNL_FIELDS:
                        fy = _val(snap["Q4"]["vals"], field)
                        nm9 = _val(snap["Q3"]["vals"], field)  # original 9M
                        if fy is not None and nm9 is not None:
                            df.at[snap["Q4"]["idx"], field] = fy - nm9
                        else:
                            df.at[snap["Q4"]["idx"], field] = None
                else:
                    for field in _PNL_FIELDS:
                        df.at[snap["Q4"]["idx"], field] = None

    return df.sort_values(["ticker", "report_period"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — 성장지표 계산 (revenue_yoy, operating_income_yoy, operating_margin)
# ─────────────────────────────────────────────────────────────────────────────

def compute_growth_metrics(fundamentals: pd.DataFrame) -> pd.DataFrame:
    """
    단일분기 실적으로부터 성장지표를 계산한다.

    Columns added
    -------------
    revenue_yoy            : 전년 동기 대비 매출 증가율 (%)
    operating_income_yoy   : 전년 동기 대비 영업이익 증가율 (%)
    operating_margin       : 영업이익률 (단순비율, 0.15 = 15%)
    oi_yoy_flag            : 'normal' | 'base_zero' | 'turnaround' | 'both_negative'
                             전년 영업이익이 0 또는 음수인 경우 operating_income_yoy는 None
    """
    df = fundamentals.copy()
    df = df.sort_values(["ticker", "report_period"]).reset_index(drop=True)

    # 전년 동기 매핑: (ticker, YYYY-QN) → prev year values
    prev_map: dict[tuple, dict] = {}
    for _, row in df.iterrows():
        period = row["report_period"]
        try:
            year_str, q = period.split("-")
            next_period = f"{int(year_str) + 1}-{q}"
        except ValueError:
            continue
        prev_map[(row["ticker"], next_period)] = {
            "prev_revenue": row["revenue"],
            "prev_oi": row["operating_income"],
        }

    rev_yoy_list: list = []
    oi_yoy_list: list = []
    margin_list: list = []
    flag_list: list = []

    for _, row in df.iterrows():
        key = (row["ticker"], row["report_period"])
        prev = prev_map.get(key, {})

        rev = row["revenue"]
        oi = row["operating_income"]
        prev_rev = prev.get("prev_revenue")
        prev_oi = prev.get("prev_oi")

        # Revenue YoY (%)
        if rev is not None and prev_rev is not None and prev_rev != 0:
            rev_yoy_list.append(round((rev / prev_rev - 1) * 100, 4))
        else:
            rev_yoy_list.append(None)

        # Operating Income YoY (%) — 전년 0 또는 음수 시 별도 표시
        if oi is None or prev_oi is None:
            oi_yoy_list.append(None)
            flag_list.append(None)
        elif prev_oi == 0:
            oi_yoy_list.append(None)
            flag_list.append("base_zero")
        elif prev_oi < 0 and oi > 0:
            oi_yoy_list.append(None)
            flag_list.append("turnaround")
        elif prev_oi < 0 and oi <= 0:
            oi_yoy_list.append(round((oi / prev_oi - 1) * 100, 4))
            flag_list.append("both_negative")
        else:
            oi_yoy_list.append(round((oi / prev_oi - 1) * 100, 4))
            flag_list.append("normal")

        # Operating Margin
        if oi is not None and rev is not None and rev != 0:
            margin_list.append(round(oi / rev, 6))
        else:
            margin_list.append(None)

    df["revenue_yoy"] = rev_yoy_list
    df["operating_income_yoy"] = oi_yoy_list
    df["operating_margin"] = margin_list
    df["oi_yoy_flag"] = flag_list
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — Signal Join (fundamental_ prefix, Look-ahead Bias 방지)
# ─────────────────────────────────────────────────────────────────────────────

def join_signals_step11(
    signals: pd.DataFrame,
    fundamentals: pd.DataFrame,
) -> pd.DataFrame:
    """
    Signal 발생일 기준으로 signal_date > disclosure_date 인 가장 최근 분기 실적을 연결.

    동일일 공시(disclosure_date == signal_date)는 제외한다.
    추가 컬럼: fundamental_report_period, fundamental_disclosure_date,
               revenue_yoy, operating_income_yoy, operating_margin
    """
    if fundamentals.empty:
        for col in (
            "fundamental_report_period", "fundamental_disclosure_date",
            "revenue_yoy", "operating_income_yoy", "operating_margin",
        ):
            signals = signals.copy()
            signals[col] = None
        return signals

    fund_sorted = fundamentals.sort_values("disclosure_date").reset_index(drop=True)

    rows: list[dict] = []
    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        sig_date = pd.Timestamp(sig["signal_date"])

        eligible = fund_sorted[
            (fund_sorted["ticker"] == ticker)
            & (fund_sorted["disclosure_date"] < sig_date)  # strict less-than
        ]

        if eligible.empty:
            rows.append({
                "fundamental_report_period": None,
                "fundamental_disclosure_date": None,
                "revenue_yoy": None,
                "operating_income_yoy": None,
                "operating_margin": None,
            })
        else:
            latest = eligible.iloc[-1]
            rows.append({
                "fundamental_report_period": latest["report_period"],
                "fundamental_disclosure_date": latest["disclosure_date"],
                "revenue_yoy": latest.get("revenue_yoy"),
                "operating_income_yoy": latest.get("operating_income_yoy"),
                "operating_margin": latest.get("operating_margin"),
            })

    joined = pd.DataFrame(rows, index=signals.index)
    return pd.concat([signals.reset_index(drop=True), joined.reset_index(drop=True)], axis=1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 11 — 종목별 개별 CSV 저장
# ─────────────────────────────────────────────────────────────────────────────

def save_fundamentals_per_ticker(df: pd.DataFrame, output_dir: Path) -> list[Path]:
    """종목별 CSV를 data/fundamentals/<ticker>_fundamentals.csv 에 저장한다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for ticker, ticker_df in df.groupby("ticker"):
        path = output_dir / f"{ticker}_fundamentals.csv"
        ticker_df.to_csv(path, index=False, encoding="utf-8-sig")
        paths.append(path)
    return paths


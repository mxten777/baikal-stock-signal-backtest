"""
STEP 11-A — OpenDART 분기 재무값 원본 검증

실행: python -m scripts.step11a_verify_raw
     python -m scripts.step11a_verify_raw --year 2024
     python -m scripts.step11a_verify_raw --year 2023 --verbose

목적:
  현재 data/fundamentals/<ticker>_fundamentals.csv 에 저장된 분기 재무값이
  OpenDART 원본과 정확히 일치하는지 검증한다.

공식 OpenDART fnlttSinglAcntAll 필드 정의:
  thstrm_amount     = 당기금액    = 분/반기 P&L이면 [3개월] 단일분기 금액
  thstrm_add_amount = 당기누적금액 = Q2→H1 누적, Q3→9M 누적

단일분기 산출 규칙:
  Q1 = thstrm_amount  (= Q1 자체, 누적도 동일)
  Q2 = thstrm_amount  (Q2 3개월 단독)
  Q3 = thstrm_amount  (Q3 3개월 단독)
  Q4 = FY_누적(thstrm_amount) − 9M_누적(Q3_thstrm_add_amount)
     = FY − Q1_thstrm − Q2_thstrm − Q3_thstrm

교차검증:
  Q2: thstrm_add_amount(H1 누적) ≈ Q1_thstrm + Q2_thstrm
  Q3: thstrm_add_amount(9M 누적) ≈ Q1_thstrm + Q2_thstrm + Q3_thstrm
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.dart_fundamental_provider import (
    _ACCOUNT_ALIASES,
    _PNL_SJ_DIVS,
    _REPRT_MAP,
    _build_disclosure_map,
    _extract_add_amount,
    _extract_amount,
    _parse_amount,
    deaccumulate_quarters_full,
)

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────

TARGET_TICKERS: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035720": "카카오",
}
FUNDAMENTAL_DIR = ROOT / "data" / "fundamentals"

_FIELDS = ("revenue", "operating_income", "net_income")
_FIELD_KO = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
}

# 허용 오차: 저장값과 산출값의 상대 오차 5% 이내면 PASS
_REL_TOLERANCE = 0.05


# ─────────────────────────────────────────────────────────────────────────────
# API Key
# ─────────────────────────────────────────────────────────────────────────────

def _get_api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("[ERROR] DART_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  Windows : set DART_API_KEY=<발급받은_키>")
        print("  Linux   : export DART_API_KEY=<발급받은_키>")
        sys.exit(1)
    return key


# ─────────────────────────────────────────────────────────────────────────────
# DART 원본 수집
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_raw_quarter(dart, ticker: str, year: int, reprt_code: str) -> pd.DataFrame | None:
    """CFS 우선으로 finstate_all 을 호출하고 원본 DataFrame 을 반환한다."""
    for fs in ("CFS", "OFS"):
        try:
            raw = dart.finstate_all(ticker, year, reprt_code=reprt_code, fs_div=fs)
            if raw is not None and not raw.empty:
                raw["_fs_used"] = fs
                return raw
        except Exception:
            continue
    return None


def _raw_summary(raw_df: pd.DataFrame, quarter_label: str) -> dict:
    """raw finstate DataFrame 에서 검증에 필요한 정보를 추출한다."""
    result: dict = {}
    for field, aliases in _ACCOUNT_ALIASES.items():
        if field not in _FIELDS:
            continue
        thstrm = _extract_amount(raw_df, aliases, sj_div_filter=_PNL_SJ_DIVS)
        add = _extract_add_amount(raw_df, aliases)
        result[field] = {
            "thstrm_amount": thstrm,       # Q1=Q1단독, Q2=Q2단독, Q3=Q3단독, Q4=FY누적
            "thstrm_add_amount": add,       # Q2=H1누적, Q3=9M누적, Q4=FY(=thstrm과 동일)
        }
    result["_fs_used"] = raw_df["_fs_used"].iloc[0] if "_fs_used" in raw_df.columns else "?"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 저장 CSV 로드
# ─────────────────────────────────────────────────────────────────────────────

def _load_stored(ticker: str) -> pd.DataFrame:
    path = FUNDAMENTAL_DIR / f"{ticker}_fundamentals.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"ticker": str})


# ─────────────────────────────────────────────────────────────────────────────
# 단일분기 기대값 산출
# ─────────────────────────────────────────────────────────────────────────────

def _expected_single(
    raw_by_quarter: dict[str, dict],
    quarter_label: str,
    field: str,
) -> float | None:
    """
    DART 원본을 기반으로 해당 분기의 올바른 단일분기값을 산출한다.

    Q1: thstrm_amount (= Q1 단독)
    Q2: thstrm_amount (= Q2 3개월 단독)
    Q3: thstrm_amount (= Q3 3개월 단독)
    Q4: FY_누적 − (Q1 + Q2 + Q3) all thstrm_amounts
        또는 FY_누적 − Q3_add_amount(9M 누적)
    """
    current = raw_by_quarter.get(quarter_label, {}).get(field, {})
    thstrm = current.get("thstrm_amount")

    if quarter_label in ("Q1", "Q2", "Q3"):
        return thstrm  # 이미 3개월 단독값

    if quarter_label == "Q4":
        if thstrm is None:
            return None
        # Q3_add_amount = 9M 누적을 이용한 빠른 계산
        nm9 = (raw_by_quarter.get("Q3", {}).get(field, {}) or {}).get("thstrm_add_amount")
        if nm9 is not None:
            return thstrm - nm9
        # fallback: FY - Q1 - Q2 - Q3 (thstrm_amounts)
        vals = [
            (raw_by_quarter.get(q, {}).get(field, {}) or {}).get("thstrm_amount")
            for q in ("Q1", "Q2", "Q3")
        ]
        if all(v is not None for v in vals):
            return thstrm - sum(vals)  # type: ignore[arg-type]
        return None

    return None


def _is_pass(stored: float | None, expected: float | None, tol: float = _REL_TOLERANCE) -> str:
    """
    저장값과 기대값 비교. 절대차 < max(abs(expected)*tol, 1_000_000) 이면 PASS.
    기대값이 None 이거나 0이면 저장값도 None/0인지만 확인.
    """
    if expected is None:
        return "SKIP"
    if stored is None:
        return "FAIL(stored=None)"
    abs_diff = abs(stored - expected)
    threshold = max(abs(expected) * tol, 1_000_000)   # 최소 100만원 오차 허용
    return "PASS" if abs_diff <= threshold else f"FAIL(diff={abs_diff:,.0f})"


# ─────────────────────────────────────────────────────────────────────────────
# 검증 실행
# ─────────────────────────────────────────────────────────────────────────────

def verify_ticker(dart, ticker: str, name: str, year: int, verbose: bool) -> list[dict]:
    """단일 종목의 지정 연도를 검증하고 결과 행 목록을 반환한다."""
    stored_df = _load_stored(ticker)
    disclosure_map = _build_disclosure_map(dart, ticker, year, year)

    # DART 원본 수집 (Q1/Q2/Q3/Q4)
    raw_by_quarter: dict[str, dict] = {}
    for reprt_code, (q_label, _) in _REPRT_MAP.items():
        period_key = f"{year}-{q_label}"
        if disclosure_map.get(period_key) is None:
            continue
        raw = _fetch_raw_quarter(dart, ticker, year, reprt_code)
        if raw is None:
            continue
        raw_by_quarter[q_label] = _raw_summary(raw, q_label)

    rows: list[dict] = []
    for q_label in ("Q1", "Q2", "Q3", "Q4"):
        period_key = f"{year}-{q_label}"
        q_raw = raw_by_quarter.get(q_label)

        for field in _FIELDS:
            if q_raw is not None:
                thstrm_val = q_raw[field]["thstrm_amount"]
                add_val = q_raw[field]["thstrm_add_amount"]
                expected = _expected_single(raw_by_quarter, q_label, field)
                fs_used = q_raw.get("_fs_used", "?")
            else:
                thstrm_val = add_val = expected = None
                fs_used = "N/A"

            # 저장값 조회
            stored_val: float | None = None
            if not stored_df.empty:
                mask = (stored_df["ticker"] == ticker) & (stored_df["report_period"] == period_key)
                if mask.any():
                    raw_stored = stored_df.loc[mask, field].iloc[0]
                    stored_val = float(raw_stored) if pd.notna(raw_stored) else None

            status = _is_pass(stored_val, expected)

            row = {
                "Company": name,
                "Ticker": ticker,
                "Quarter": period_key,
                "Field": _FIELD_KO[field],
                "reprt_code": _next_reprt_code(q_label),
                "fs_div": fs_used,
                "thstrm_amount (raw)": _fmt(thstrm_val),
                "thstrm_add_amount": _fmt(add_val),
                "Expected_single": _fmt(expected),
                "Stored_value": _fmt(stored_val),
                "Status": status,
            }
            rows.append(row)

            if verbose and q_raw is not None:
                _print_verbose_row(row, thstrm_val, add_val, expected, stored_val, q_label)

    return rows


def _next_reprt_code(q_label: str) -> str:
    mapping = {"Q1": "11013", "Q2": "11012", "Q3": "11014", "Q4": "11011"}
    return mapping.get(q_label, "?")


def _fmt(val: float | None) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1e11:
        return f"{val/1e12:.3f}조"
    if abs(val) >= 1e8:
        return f"{val/1e8:.1f}억"
    return f"{val:,.0f}"


def _print_verbose_row(row: dict, thstrm: float | None, add: float | None,
                       expected: float | None, stored: float | None, q_label: str) -> None:
    print(f"    [{row['Quarter']}] {row['Field']}")
    print(f"      reprt_code      : {row['reprt_code']}  fs_div: {row['fs_div']}")
    print(f"      thstrm_amount   : {_fmt(thstrm):>15}  (DART 당기{'누적=FY' if q_label == 'Q4' else '=3개월단독'})")  
    if q_label in ("Q2", "Q3"):
        cum_label = "H1 누적" if q_label == "Q2" else "9M 누적"
        src = f"{cum_label}" if add is not None else "N/A"
        print(f"      thstrm_add_amt  : {_fmt(add):>15}  ({src} — 교차검증용)")
    print(f"      Expected_single : {_fmt(expected):>15}")
    print(f"      Stored_value    : {_fmt(stored):>15}")
    diff = None if (expected is None or stored is None) else stored - expected
    diff_str = _fmt(diff) if diff is not None else "N/A"
    print(f"      Diff            : {diff_str:>15}   {row['Status']}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# 요약 테이블 출력
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary_table(all_rows: list[dict]) -> None:
    """분기/회사별 요약 PASS/FAIL 표를 출력한다."""
    # Pivot: Company × Quarter, 각 셀에 3개 field 상태
    companies = list(dict.fromkeys(r["Company"] for r in all_rows))
    quarters = list(dict.fromkeys(r["Quarter"] for r in all_rows))

    header = f"{'Company':<12} {'Quarter':<10} {'매출액':>12} {'영업이익':>12} {'당기순이익':>12}  Overall"
    print(header)
    print("-" * len(header))

    field_ko_order = ["매출액", "영업이익", "당기순이익"]

    for company in companies:
        for quarter in quarters:
            rows_cq = [r for r in all_rows if r["Company"] == company and r["Quarter"] == quarter]
            if not rows_cq:
                continue
            statuses: dict[str, str] = {r["Field"]: r["Status"] for r in rows_cq}
            cells = [statuses.get(fk, "N/A") for fk in field_ko_order]
            overall = "PASS" if all(c.startswith("PASS") or c == "SKIP" for c in cells) else "FAIL"
            cells_str = [f"{'✓' if c.startswith('PASS') else ('?' if c == 'SKIP' else '✗'):>6}" for c in cells]
            print(f"{company:<12} {quarter:<10} {cells_str[0]:>12} {cells_str[1]:>12} {cells_str[2]:>12}  {overall}")
        print()


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDART 분기 재무값 원본 검증")
    parser.add_argument("--year", type=int, default=2024, help="검증 대상 연도 (기본: 2024)")
    parser.add_argument("--verbose", "-v", action="store_true", help="각 분기 상세 출력")
    args = parser.parse_args()

    api_key = _get_api_key()

    try:
        import OpenDartReader
        dart = OpenDartReader(api_key)
    except Exception as e:
        print(f"[ERROR] OpenDartReader 초기화 실패: {e}")
        sys.exit(1)

    print("=" * 72)
    print(f"STEP 11-A — OpenDART 분기 재무값 원본 검증 (대상 연도: {args.year})")
    print("=" * 72)
    print()
    print("검증 규칙 (공식 OpenDART 필드 정의 기준):")
    print("  Q1       : thstrm_amount = Q1 3개월 단독값")
    print("  Q2       : thstrm_amount = Q2 3개월 단독값  (thstrm_add_amount = H1 누적, 교차검증용)")
    print("  Q3       : thstrm_amount = Q3 3개월 단독값  (thstrm_add_amount = 9M 누적, 교차검증용)")
    print("  Q4       : FY 누적 − (Q1 + Q2 + Q3)  또는  FY − 9M_누적")
    print()

    all_rows: list[dict] = []
    for ticker, name in TARGET_TICKERS.items():
        print(f"  [{ticker}] {name} 검증 중 (연도: {args.year}) ...")
        rows = verify_ticker(dart, ticker, name, args.year, args.verbose)
        all_rows.extend(rows)
        pass_cnt = sum(1 for r in rows if r["Status"].startswith("PASS"))
        skip_cnt = sum(1 for r in rows if r["Status"] == "SKIP")
        fail_cnt = len(rows) - pass_cnt - skip_cnt
        print(f"    결과: {pass_cnt} PASS / {fail_cnt} FAIL / {skip_cnt} SKIP  "
              f"(총 {len(rows)}건)")
        print()

    if not all_rows:
        print("[WARNING] 검증 결과 없음.")
        return

    print("=" * 72)
    print("종합 검증 결과")
    print("=" * 72)
    _print_summary_table(all_rows)

    total = len(all_rows)
    pass_total = sum(1 for r in all_rows if r["Status"].startswith("PASS"))
    skip_total = sum(1 for r in all_rows if r["Status"] == "SKIP")
    fail_total = total - pass_total - skip_total
    print(f"전체: {pass_total} PASS / {fail_total} FAIL / {skip_total} SKIP  (총 {total}건)")
    print()

    if fail_total == 0:
        print("✓ 저장된 분기 재무값이 OpenDART 원본과 일치합니다.")
    else:
        print("✗ FAIL 항목이 있습니다. 위 상세 내용을 확인하세요.")
        print("  주요 원인 후보:")
        print("    1) thstrm_add_amount 미수집으로 H1/9M 누적값이 저장됨")
        print("    2) account_nm 불일치로 다른 계정값이 수집됨")
        print("    3) CFS/OFS 선택 불일치")
        print("    4) DART 원본 데이터 수정/정정 공시 미반영")


if __name__ == "__main__":
    main()

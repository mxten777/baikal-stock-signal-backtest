"""
STEP 8 — 외국인·기관 수급 데이터 수집 및 검증 스크립트

목적:
  - 수급 데이터 소스 조사 결과 보고
  - 3종목 시험 수집 (005930, 000660, 035720)
  - 데이터 품질 검증
  - v0.2 Signal과 Join 가능 여부 확인

실행: python -m scripts.step8_investor_flow
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.naver_investor_provider import fetch_investor_flow, save_investor_flow

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
TEST_TICKERS = ["005930", "000660", "035720"]
START_DATE = "2023-08-15"
END_DATE = "2026-08-15"
INVESTOR_DIR = ROOT / "data" / "investor"
SIGNALS_PATH = ROOT / "output" / "signals.csv"


# ─────────────────────────────────────────────
# 섹션 1 — 데이터 소스 후보 보고
# ─────────────────────────────────────────────
def print_source_report() -> None:
    print("=" * 70)
    print("STEP 8 — 외국인·기관 수급 데이터 소스 조사 결과")
    print("=" * 70)

    report = [
        {
            "소스": "KRX 공식 API (pykrx)",
            "3년 과거": "가능 (원칙적)",
            "종목별 일별": "가능",
            "무료": "무료",
            "자동수집": "가능 (pykrx 1.0.45)",
            "상업적 이용": "KRX 이용약관 적용",
            "현재 사용 가능": "❌ 로그인 필요 (KRX_ID/KRX_PW)",
        },
        {
            "소스": "Naver Finance (frgn.naver)",
            "3년 과거": "가능 (45+ 페이지)",
            "종목별 일별": "가능",
            "무료": "무료",
            "자동수집": "HTML 스크래핑",
            "상업적 이용": "비공식 API, 상업적 이용 제한 가능",
            "현재 사용 가능": "✅ 동작 확인",
        },
        {
            "소스": "KIS Developer API",
            "3년 과거": "가능",
            "종목별 일별": "가능",
            "무료": "무료 (개인 투자자)",
            "자동수집": "REST API",
            "상업적 이용": "한국투자증권 계좌 필요",
            "현재 사용 가능": "❌ API Key 등록 필요",
        },
        {
            "소스": "FinanceDataReader",
            "3년 과거": "가능 (OHLCV)",
            "종목별 일별": "가능",
            "무료": "무료",
            "자동수집": "가능",
            "상업적 이용": "MIT 라이선스",
            "현재 사용 가능": "⚠️ 수급 데이터 미제공 (OHLCV만)",
        },
    ]

    df = pd.DataFrame(report).set_index("소스")
    print(df.to_string())
    print()
    print("최종 선택: Naver Finance (frgn.naver)")
    print("  선택 이유: 현재 환경에서 인증 없이 동작하는 유일한 소스")
    print("  데이터 단위: 거래량 기준 (주, shares) — 거래대금 기준 미제공")
    print("  제공 항목: 외국인 순매매량, 기관 순매매량")
    print("  미제공 항목: 개인 순매매량 (KRX 로그인 없이 직접 수집 불가)")
    print("  상업적 이용 주의: Naver 서비스 이용약관 확인 필요")
    print()


# ─────────────────────────────────────────────
# 섹션 2 — 3종목 데이터 수집
# ─────────────────────────────────────────────
def collect_investor_data() -> dict[str, pd.DataFrame]:
    print("=" * 70)
    print(f"섹션 2 — 3종목 수급 데이터 수집 ({START_DATE} ~ {END_DATE})")
    print("=" * 70)

    results: dict[str, pd.DataFrame] = {}
    for ticker in TEST_TICKERS:
        print(f"  수집 중: {ticker} ...", end="", flush=True)
        try:
            df = fetch_investor_flow(ticker, START_DATE, END_DATE)
            if df.empty:
                print(" ❌ 빈 데이터")
            else:
                path = save_investor_flow(df, INVESTOR_DIR)
                results[ticker] = df
                print(f" ✅ {len(df)} 행 → {path.name}")
        except Exception as exc:
            print(f" ❌ 오류: {exc}")
    print()
    return results


# ─────────────────────────────────────────────
# 섹션 3 — 데이터 품질 검증
# ─────────────────────────────────────────────
def validate_data(results: dict[str, pd.DataFrame]) -> None:
    print("=" * 70)
    print("섹션 3 — 데이터 품질 검증")
    print("=" * 70)

    for ticker, df in results.items():
        print(f"\n[{ticker}]")
        print(f"  첫 날짜    : {df['date'].min().date()}")
        print(f"  마지막 날짜: {df['date'].max().date()}")
        print(f"  행 수      : {len(df)}")

        null_foreign = df["foreign_net_buy"].isna().sum()
        null_inst = df["institution_net_buy"].isna().sum()
        print(f"  결측치     : foreign_net_buy={null_foreign}, institution_net_buy={null_inst}")

        dup_count = df.duplicated(subset="date").sum()
        print(f"  중복 날짜  : {dup_count}")

        print(f"\n  최근 5거래일 샘플:")
        sample = df.tail(5)[["date", "foreign_net_buy", "institution_net_buy"]].copy()
        sample["date"] = sample["date"].dt.strftime("%Y-%m-%d")
        print(sample.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# 섹션 4 — Signal Join 검증
# ─────────────────────────────────────────────
def verify_signal_join(results: dict[str, pd.DataFrame]) -> None:
    print("=" * 70)
    print("섹션 4 — v0.2 Signal Join 검증 (date + ticker 기준)")
    print("=" * 70)

    if not SIGNALS_PATH.exists():
        print("  ❌ output/signals.csv 없음 — 먼저 python -m src.main 실행 필요")
        return

    signals = pd.read_csv(SIGNALS_PATH)
    signals["signal_date"] = pd.to_datetime(signals["signal_date"])
    # ticker가 정수로 저장된 경우 6자리 zero-pad 문자열로 정규화
    signals["ticker"] = signals["ticker"].astype(str).str.zfill(6)

    investor_combined = pd.concat(results.values(), ignore_index=True) if results else pd.DataFrame()

    if investor_combined.empty:
        print("  ❌ 수급 데이터 없음")
        return

    # 대상 ticker만 필터
    collected_tickers = list(results.keys())
    sig_subset = signals[signals["ticker"].isin(collected_tickers)].copy()

    merged = sig_subset.merge(
        investor_combined,
        left_on=["signal_date", "ticker"],
        right_on=["date", "ticker"],
        how="left",
    )

    total_signals = len(sig_subset)
    matched = merged["foreign_net_buy"].notna().sum()
    unmatched = total_signals - matched
    match_rate = matched / total_signals * 100 if total_signals > 0 else 0.0

    print(f"  대상 ticker  : {collected_tickers}")
    print(f"  Signal Count : {total_signals}")
    print(f"  Matched      : {matched}")
    print(f"  Unmatched    : {unmatched}")
    print(f"  Match Rate   : {match_rate:.1f}%")
    print()

    if unmatched > 0:
        unmatched_rows = merged[merged["foreign_net_buy"].isna()][["ticker", "signal_date"]]
        print("  미매칭 Signal:")
        print(unmatched_rows.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main() -> None:
    print_source_report()
    results = collect_investor_data()
    if results:
        validate_data(results)
        verify_signal_join(results)
    else:
        print("수집된 데이터 없음 — 검증 생략")

    print("=" * 70)
    print("STEP 8 완료")
    print("  수급 Score 설계 및 알고리즘 수정은 이번 STEP에서 수행하지 않음")
    print("=" * 70)


if __name__ == "__main__":
    main()

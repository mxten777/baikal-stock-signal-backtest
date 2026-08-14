"""
STEP 10 — 기업 실적 데이터 적용 가능성 검증

기존 Signal 알고리즘 수정 없음.
Stock Selection Score 구현 없음.
실적 Threshold 설정 없음.

실행: python -m scripts.step10_fundamental_feasibility
API Key 설정: set DART_API_KEY=<your_key>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.dart_fundamental_provider import (
    compute_yoy_growth,
    fetch_quarterly_fundamentals,
    join_signals_to_fundamentals,
    save_fundamentals,
)

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
TARGET_TICKERS = {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"}
SIGNALS_PATH = ROOT / "output" / "signals.csv"
FUNDAMENTAL_DIR = ROOT / "data" / "fundamental"
START_YEAR = 2022  # YoY 계산을 위해 1년 전부터 수집
END_YEAR = 2025


# ─────────────────────────────────────────────
# 섹션 1 — 데이터 소스 후보 보고
# ─────────────────────────────────────────────
def print_source_report() -> None:
    print("=" * 80)
    print("STEP 10 — 기업 실적 데이터 소스 후보 조사")
    print("=" * 80)

    sources = [
        {
            "소스": "OpenDART (dart.fss.or.kr)",
            "분기 실적": "✅",
            "3년 과거": "✅",
            "공시일 확인": "✅ (rcept_dt)",
            "자동수집": "✅ REST API",
            "무료": "✅",
            "API Key": "필요 (무료 신청)",
            "상업적 이용": "FSS 이용약관 적용",
            "현재 사용 가능": "⚠️ Key 필요",
        },
        {
            "소스": "FinanceDataReader",
            "분기 실적": "❌ OHLCV만",
            "3년 과거": "✅ (OHLCV)",
            "공시일 확인": "❌",
            "자동수집": "✅",
            "무료": "✅ MIT",
            "API Key": "불필요",
            "상업적 이용": "MIT 라이선스",
            "현재 사용 가능": "❌ 재무제표 미제공",
        },
        {
            "소스": "pykrx",
            "분기 실적": "❌ PER/PBR/EPS만",
            "3년 과거": "✅",
            "공시일 확인": "❌",
            "자동수집": "✅",
            "무료": "✅",
            "API Key": "불필요",
            "상업적 이용": "비공식 KRX 스크래핑",
            "현재 사용 가능": "⚠️ 분기 재무제표 미제공",
        },
        {
            "소스": "Naver Finance (스크래핑)",
            "분기 실적": "⚠️ 요약 제공",
            "3년 과거": "⚠️ 제한적",
            "공시일 확인": "❌ 미제공",
            "자동수집": "⚠️ JS 동적 로딩",
            "무료": "✅",
            "API Key": "불필요",
            "상업적 이용": "Naver 이용약관 제한",
            "현재 사용 가능": "❌ 공시일 미제공 → Look-ahead Bias 방지 불가",
        },
        {
            "소스": "KIS Developer API",
            "분기 실적": "✅",
            "3년 과거": "✅",
            "공시일 확인": "⚠️",
            "자동수집": "✅ REST API",
            "무료": "✅ (개인)",
            "API Key": "필요 (증권계좌 필요)",
            "상업적 이용": "한국투자증권 이용약관",
            "현재 사용 가능": "❌ 증권계좌 필요",
        },
    ]

    df = pd.DataFrame(sources).set_index("소스")
    print(df.to_string())
    print()

    print("── 최종 선택: OpenDART (dart.fss.or.kr) ──────────────────────────────────")
    print("  선택 이유:")
    print("    1. 분기별 재무제표 완전 제공 (매출액, 영업이익, 당기순이익, 자산총계, 자본총계)")
    print("    2. rcept_dt(공시일) 제공 → Look-ahead Bias 방지 가능")
    print("    3. 공식 금융감독원 데이터 소스 — 신뢰성 최高")
    print("    4. 무료 API, OpenDartReader 라이브러리 사용 가능")
    print()


# ─────────────────────────────────────────────
# 섹션 2 — API Key 상태 확인 및 발급 안내
# ─────────────────────────────────────────────
def check_api_key() -> str | None:
    key = os.environ.get("DART_API_KEY", "").strip()
    print("=" * 80)
    print("섹션 2 — DART API Key 상태")
    print("=" * 80)

    if key:
        masked = key[:4] + "*" * (len(key) - 4)
        print(f"  상태    : ✅ 설정됨 ({masked})")
        print()
        return key

    print("  상태    : ❌ 미설정 (DART_API_KEY 환경변수 없음)")
    print()
    print("  발급 절차:")
    print("    1. https://opendart.fss.or.kr 접속")
    print("    2. 우측 상단 '회원가입' → 이메일 인증")
    print("    3. 로그인 후 '인증키 신청/관리' 클릭")
    print("    4. API 서비스 이용 약관 동의 후 신청")
    print("    5. 즉시 발급 (40자리 문자열)")
    print()
    print("  사용 방법:")
    print("    Windows: set DART_API_KEY=<발급받은_키>")
    print("    Linux/Mac: export DART_API_KEY=<발급받은_키>")
    print("    Python:  os.environ['DART_API_KEY'] = '<발급받은_키>'")
    print()
    print("  일일 한도: 10,000건 (종목당 분기별 1건 = 4건/연도)")
    print("  3종목 × 4분기 × 4연도 = 약 48건 → 한도 이내")
    print()
    return None


# ─────────────────────────────────────────────
# 섹션 3 — 3종목 시험 수집
# ─────────────────────────────────────────────
def collect_and_report(dart_api_key: str) -> pd.DataFrame:
    print("=" * 80)
    print(f"섹션 3 — 3종목 시험 수집 ({START_YEAR} ~ {END_YEAR})")
    print("=" * 80)

    try:
        import OpenDartReader
        dart = OpenDartReader(dart_api_key)
    except Exception as e:
        print(f"  ❌ OpenDartReader 초기화 실패: {e}")
        return pd.DataFrame()

    all_frames: list[pd.DataFrame] = []
    for ticker, name in TARGET_TICKERS.items():
        print(f"  수집 중: [{ticker}] {name} ...", end="", flush=True)
        try:
            df = fetch_quarterly_fundamentals(dart, ticker, START_YEAR, END_YEAR)
            if df.empty:
                print(" ❌ 데이터 없음")
            else:
                path = save_fundamentals(df, FUNDAMENTAL_DIR)
                all_frames.append(df)
                print(f" ✅ {len(df)}건 → {path.name}")
        except Exception as exc:
            print(f" ❌ 오류: {exc}")

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    print()
    print(f"  총 수집: {len(combined)}건")
    print()
    return combined


# ─────────────────────────────────────────────
# 섹션 4 — 확보 필드 및 샘플 확인
# ─────────────────────────────────────────────
def print_data_quality(fundamentals: pd.DataFrame) -> None:
    print("=" * 80)
    print("섹션 4 — 확보 필드 및 데이터 품질")
    print("=" * 80)

    print("  필드 목록:")
    for col in fundamentals.columns:
        null_cnt = fundamentals[col].isna().sum()
        total = len(fundamentals)
        print(f"    {col:30s}  null={null_cnt}/{total}")
    print()

    print("  최근 4건 샘플 (삼성전자):")
    sample = fundamentals[fundamentals["ticker"] == "005930"].tail(4)
    if not sample.empty:
        print(sample.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# 섹션 5 — 성장률 계산 가능 여부
# ─────────────────────────────────────────────
def print_growth_metrics(fundamentals: pd.DataFrame) -> None:
    print("=" * 80)
    print("섹션 5 — 성장률 계산 가능 여부")
    print("=" * 80)

    df = compute_yoy_growth(fundamentals)

    metrics = {
        "YoY Revenue Growth": df["yoy_revenue_growth"].notna().sum(),
        "YoY Operating Income Growth": df["yoy_operating_income_growth"].notna().sum(),
        "Operating Margin": df["operating_margin"].notna().sum(),
    }
    total = len(df)
    for metric, cnt in metrics.items():
        print(f"  {metric:35s}: {cnt}/{total}건 계산 가능")
    print()

    print("  YoY Revenue Growth 샘플 (삼성전자):")
    sample = df[df["ticker"] == "005930"][
        ["report_period", "disclosure_date", "revenue", "yoy_revenue_growth", "operating_margin"]
    ].tail(6)
    if not sample.empty:
        print(sample.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# 섹션 6 — Look-ahead Bias 방지 및 Signal Join
# ─────────────────────────────────────────────
def print_signal_join(fundamentals: pd.DataFrame) -> None:
    print("=" * 80)
    print("섹션 6 — Signal Join 결과 (Look-ahead Bias 방지 확인)")
    print("=" * 80)

    if not SIGNALS_PATH.exists():
        print("  ❌ output/signals.csv 없음")
        return

    signals = pd.read_csv(SIGNALS_PATH)
    signals["signal_date"] = pd.to_datetime(signals["signal_date"])
    signals["ticker"] = signals["ticker"].astype(str).str.zfill(6)

    target_signals = signals[signals["ticker"].isin(TARGET_TICKERS)].copy().reset_index(drop=True)

    joined = join_signals_to_fundamentals(target_signals, fundamentals)

    total = len(joined)
    matched = joined["fund_report_period"].notna().sum()
    unmatched = total - matched
    rate = matched / total * 100 if total > 0 else 0.0

    print(f"  대상 Signal 수   : {total}")
    print(f"  Matched          : {matched}")
    print(f"  Unmatched        : {unmatched}")
    print(f"  Match Rate       : {rate:.1f}%")
    print()

    if unmatched > 0:
        missing = joined[joined["fund_report_period"].isna()][["ticker", "signal_date"]]
        print("  미매칭 Signal:")
        print(missing.to_string(index=False))
        print()

    print("  Look-ahead Bias 방지 원칙:")
    print("    signal_date 당일에 가장 최근 disclosure_date < signal_date 인 분기만 사용")
    print("    → 투자자가 Signal 당시 알 수 있었던 실적만 연결됨")
    print()

    print("  매칭 샘플 (최근 5건):")
    sample = joined[joined["fund_report_period"].notna()][
        ["ticker", "signal_date", "fund_report_period", "fund_disclosure_date",
         "fund_revenue", "fund_operating_income"]
    ].tail(5)
    if not sample.empty:
        print(sample.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# No API Key — 구조 및 기대 결과 미리보기
# ─────────────────────────────────────────────
def print_no_key_preview() -> None:
    print("=" * 80)
    print("섹션 3 ~ 6 미리보기 (API Key 발급 후 실행 예정)")
    print("=" * 80)
    print()
    print("  기대 수집 결과:")
    preview = pd.DataFrame([
        {"ticker": "005930", "report_period": "2024-Q1", "disclosure_date": "2024-05-??",
         "revenue": "약 71.9조원", "operating_income": "약 6.6조원", "공시일기반": "✅"},
        {"ticker": "005930", "report_period": "2024-Q2", "disclosure_date": "2024-08-??",
         "revenue": "약 74.1조원", "operating_income": "약 10.4조원", "공시일기반": "✅"},
        {"ticker": "000660", "report_period": "2024-Q1", "disclosure_date": "2024-05-??",
         "revenue": "약 12.4조원", "operating_income": "약 2.9조원", "공시일기반": "✅"},
        {"ticker": "035720", "report_period": "2024-Q1", "disclosure_date": "2024-05-??",
         "revenue": "약 2.0조원", "operating_income": "약 0.04조원", "공시일기반": "✅"},
    ])
    print(preview.to_string(index=False))
    print()
    print("  성장률 계산 가능 항목:")
    print("    YoY Revenue Growth           : ✅ (동일 분기 전년 대비)")
    print("    YoY Operating Income Growth  : ✅ (동일 분기 전년 대비)")
    print("    Operating Margin             : ✅ (영업이익 / 매출액)")
    print()
    print("  Look-ahead Bias 방지:")
    print("    ✅ disclosure_date 기준 연결 → Signal 당시 공개된 실적만 사용")
    print()
    print("  Signal Join 예상 Match Rate:")
    print("    3종목 Signal 수: 42건 (005930=13, 000660=11, 035720=18)")
    print("    예상 Match Rate: ~85% 이상 (2023 초 Signal 제외)")
    print()


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main() -> None:
    print("=" * 80)
    print("STEP 10 — 기업 실적 데이터 적용 가능성 검증")
    print("=" * 80)
    print()

    print_source_report()

    dart_api_key = check_api_key()

    if dart_api_key:
        fundamentals = collect_and_report(dart_api_key)
        if not fundamentals.empty:
            print_data_quality(fundamentals)
            print_growth_metrics(fundamentals)
            print_signal_join(fundamentals)
    else:
        print_no_key_preview()


if __name__ == "__main__":
    main()

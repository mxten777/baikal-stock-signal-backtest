"""
STEP 11 - 실제 재무데이터 수집 및 Signal 결합 검증

실행: python -m scripts.step11_fundamental_actual
API Key: set DART_API_KEY=<your_key>

수집 대상: 005930(삼성전자), 000660(SK하이닉스), 035720(카카오)
기간: 2022 ~ 2026 (YoY 계산 기준연도 포함)

이번 STEP에서는 Stock Selection Score, Signal 알고리즘 수정 없음.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.data_provider.dart_fundamental_provider import (
    compute_growth_metrics,
    fetch_quarterly_fundamentals,
    join_signals_step11,
    save_fundamentals_per_ticker,
)

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
TARGET_TICKERS = {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"}
SIGNALS_PATH = ROOT / "output" / "signals.csv"
FUNDAMENTAL_DIR = ROOT / "data" / "fundamentals"
START_YEAR = 2022   # YoY 기준연도 포함
END_YEAR = 2026     # 현재 연도


# ─────────────────────────────────────────────
# 섹션 1 - API Key 확인
# ─────────────────────────────────────────────
def _get_api_key() -> str:
    """환경변수에서 DART_API_KEY를 읽는다. 없으면 즉시 종료."""
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("[ERROR] DART_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  Windows: set DART_API_KEY=<발급받은_키>")
        print("  Linux/Mac: export DART_API_KEY=<발급받은_키>")
        sys.exit(1)
    return key


# ─────────────────────────────────────────────
# 섹션 2 - 데이터 수집
# ─────────────────────────────────────────────
def collect_fundamentals(dart_api_key: str) -> dict[str, pd.DataFrame]:
    """3종목 분기 실적 수집 → 단일분기 변환 → 성장지표 계산."""
    try:
        import OpenDartReader
        dart = OpenDartReader(dart_api_key)
    except Exception as e:
        print(f"[ERROR] OpenDartReader 초기화 실패: {e}")
        sys.exit(1)

    result: dict[str, pd.DataFrame] = {}

    print("=" * 72)
    print("섹션 2 - 데이터 수집")
    print("=" * 72)

    for ticker, name in TARGET_TICKERS.items():
        print(f"  [{ticker}] {name} 수집 중 ({START_YEAR}~{END_YEAR}) ...", end="", flush=True)
        try:
            df = fetch_quarterly_fundamentals(dart, ticker, START_YEAR, END_YEAR)
        except Exception as exc:
            print(f" [FAIL] {exc}")
            continue

        if df.empty:
            print(" [FAIL] 데이터 없음")
            continue

        df_with_metrics = compute_growth_metrics(df)
        result[ticker] = df_with_metrics

        quarters = len(df)
        periods = f"{df['report_period'].min()} ~ {df['report_period'].max()}"
        print(f" OK  {quarters}분기 / {periods}")

    print()
    return result


# ─────────────────────────────────────────────
# 섹션 3 - 최근 4분기 데이터 출력
# ─────────────────────────────────────────────
def print_recent_quarters(per_ticker: dict[str, pd.DataFrame]) -> None:
    print("=" * 72)
    print("섹션 3 - 최근 4분기 실적 (단일분기 변환 후)")
    print("=" * 72)

    cols = [
        "report_period", "disclosure_date",
        "revenue", "operating_income", "net_income",
    ]

    for ticker, name in TARGET_TICKERS.items():
        df = per_ticker.get(ticker)
        print(f"  [{ticker}] {name}")
        if df is None or df.empty:
            print("    데이터 없음")
            continue
        sample = df[cols].tail(4).copy()
        sample["revenue"] = sample["revenue"].apply(
            lambda v: f"{v/1e12:.2f}조" if v is not None and not pd.isna(v) else "N/A"
        )
        sample["operating_income"] = sample["operating_income"].apply(
            lambda v: f"{v/1e12:.3f}조" if v is not None and not pd.isna(v) else "N/A"
        )
        sample["net_income"] = sample["net_income"].apply(
            lambda v: f"{v/1e12:.3f}조" if v is not None and not pd.isna(v) else "N/A"
        )
        print(sample.to_string(index=False))
        print()


# ─────────────────────────────────────────────
# 섹션 4 - 성장지표 계산 결과
# ─────────────────────────────────────────────
def print_growth_metrics(per_ticker: dict[str, pd.DataFrame]) -> None:
    print("=" * 72)
    print("섹션 4 - 성장지표 계산 결과")
    print("=" * 72)

    metrics_cols = [
        "report_period", "revenue_yoy", "operating_income_yoy",
        "operating_margin", "oi_yoy_flag",
    ]

    for ticker, name in TARGET_TICKERS.items():
        df = per_ticker.get(ticker)
        print(f"  [{ticker}] {name}")
        if df is None or df.empty:
            print("    데이터 없음")
            continue

        avail = [c for c in metrics_cols if c in df.columns]
        sample = df[avail].dropna(subset=["revenue_yoy"]).tail(6).copy()
        if "operating_margin" in sample.columns:
            sample["operating_margin"] = sample["operating_margin"].apply(
                lambda v: f"{v*100:.1f}%" if v is not None and not pd.isna(v) else "N/A"
            )
        if "revenue_yoy" in sample.columns:
            sample["revenue_yoy"] = sample["revenue_yoy"].apply(
                lambda v: f"{v:.1f}%" if v is not None and not pd.isna(v) else "N/A"
            )
        if "operating_income_yoy" in sample.columns:
            sample["operating_income_yoy"] = sample["operating_income_yoy"].apply(
                lambda v: f"{v:.1f}%" if v is not None and not pd.isna(v) else "N/A"
            )
        print(sample.to_string(index=False))
        print()


# ─────────────────────────────────────────────
# 섹션 5 - CSV 저장
# ─────────────────────────────────────────────
def save_data(per_ticker: dict[str, pd.DataFrame]) -> None:
    print("=" * 72)
    print("섹션 5 - CSV 저장")
    print("=" * 72)

    all_frames = list(per_ticker.values())
    if not all_frames:
        print("  저장할 데이터 없음")
        return

    combined = pd.concat(all_frames, ignore_index=True)
    paths = save_fundamentals_per_ticker(combined, FUNDAMENTAL_DIR)
    for p in paths:
        print(f"  저장: {p.relative_to(ROOT)}")
    print()


# ─────────────────────────────────────────────
# 섹션 6 - Signal Join (Look-ahead Bias 방지)
# ─────────────────────────────────────────────
def perform_signal_join(
    per_ticker: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    각 Signal에 발생일 이전 공시된 가장 최근 분기 실적을 연결한다.
    반환: (joined_df, per_ticker_summary)
    """
    print("=" * 72)
    print("섹션 6 - Signal Join (Look-ahead Bias 방지)")
    print("=" * 72)

    if not SIGNALS_PATH.exists():
        print("  [ERROR] output/signals.csv 없음")
        return pd.DataFrame(), pd.DataFrame()

    signals = pd.read_csv(SIGNALS_PATH)
    signals["signal_date"] = pd.to_datetime(signals["signal_date"])
    signals["ticker"] = signals["ticker"].astype(str).str.zfill(6)

    target_signals = signals[signals["ticker"].isin(TARGET_TICKERS)].copy().reset_index(drop=True)

    if per_ticker:
        combined_fund = pd.concat(list(per_ticker.values()), ignore_index=True)
    else:
        combined_fund = pd.DataFrame()

    joined = join_signals_step11(target_signals, combined_fund)

    # 종목별 집계
    summary_rows = []
    for ticker, name in TARGET_TICKERS.items():
        subset = joined[joined["ticker"] == ticker]
        total = len(subset)
        matched = subset["fundamental_report_period"].notna().sum()
        unmatched = total - matched
        rate = matched / total * 100 if total > 0 else 0.0
        summary_rows.append({
            "ticker": ticker,
            "name": name,
            "signal_count": total,
            "matched": matched,
            "unmatched": unmatched,
            "match_rate": f"{rate:.1f}%",
        })
        print(f"  [{ticker}] {name}")
        print(f"    Signal Count : {total}")
        print(f"    Matched      : {matched}")
        print(f"    Unmatched    : {unmatched}")
        print(f"    Match Rate   : {rate:.1f}%")
        print()

    summary_df = pd.DataFrame(summary_rows)
    return joined, summary_df


# ─────────────────────────────────────────────
# 섹션 7 - Look-ahead Bias 검증
# ─────────────────────────────────────────────
def verify_lookahead_bias(joined: pd.DataFrame) -> None:
    print("=" * 72)
    print("섹션 7 - Look-ahead Bias 검증")
    print("=" * 72)

    if joined.empty:
        print("  검증 대상 없음")
        return

    matched = joined[joined["fundamental_report_period"].notna()].copy()

    # 미래 공시 연결 검사: disclosure_date >= signal_date
    future_links = matched[
        matched["fundamental_disclosure_date"] >= matched["signal_date"]
    ]
    future_count = len(future_links)

    # 동일일 공시 연결 검사: disclosure_date == signal_date
    same_day = matched[
        matched["fundamental_disclosure_date"] == matched["signal_date"]
    ]
    same_day_count = len(same_day)

    print(f"  미래 공시 연결 건수  : {future_count}  (0이어야 함)")
    print(f"  동일일 공시 연결 건수: {same_day_count}  (0이어야 함)")

    if future_count > 0:
        print("\n  [WARN] 미래 공시 연결 발견:")
        print(future_links[["ticker", "signal_date", "fundamental_disclosure_date"]].to_string(index=False))

    if same_day_count > 0:
        print("\n  [WARN] 동일일 공시 연결 발견:")
        print(same_day[["ticker", "signal_date", "fundamental_disclosure_date"]].to_string(index=False))

    if future_count == 0 and same_day_count == 0:
        print("  [OK] Look-ahead Bias 없음")
    print()


# ─────────────────────────────────────────────
# 섹션 8 - 샘플 출력
# ─────────────────────────────────────────────
def print_samples(joined: pd.DataFrame) -> None:
    print("=" * 72)
    print("섹션 8 - Signal Join 샘플 (종목별 3건)")
    print("=" * 72)

    sample_cols = [
        "ticker", "signal_date",
        "fundamental_report_period", "fundamental_disclosure_date",
        "revenue_yoy", "operating_income_yoy", "operating_margin",
        "excess_return_20d",
    ]
    avail = [c for c in sample_cols if c in joined.columns]

    for ticker, name in TARGET_TICKERS.items():
        subset = joined[joined["ticker"] == ticker][avail]
        matched = subset[subset["fundamental_report_period"].notna()]
        sample = matched.tail(3)
        print(f"  [{ticker}] {name}")
        if sample.empty:
            print("    매칭된 Signal 없음")
        else:
            print(sample.to_string(index=False))
        print()


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main() -> None:
    print("=" * 72)
    print("STEP 11 - 실제 재무데이터 수집 및 Signal 결합 검증")
    print("=" * 72)
    print()

    dart_api_key = _get_api_key()
    masked = dart_api_key[:4] + "*" * (len(dart_api_key) - 4)
    print(f"  DART_API_KEY : {masked}")
    print()

    per_ticker = collect_fundamentals(dart_api_key)

    print_recent_quarters(per_ticker)
    print_growth_metrics(per_ticker)
    save_data(per_ticker)

    joined, _summary = perform_signal_join(per_ticker)
    verify_lookahead_bias(joined)
    print_samples(joined)

    print("=" * 72)
    print("STEP 11 완료")
    print("=" * 72)


if __name__ == "__main__":
    main()

"""
STEP 12-A — 나머지 종목 실적 데이터 보강 수집

STEP 11(005930/000660/035720)에서 수집하지 않은 나머지 종목의 분기 실적을
동일한 수집 구조(fetch_quarterly_fundamentals → compute_growth_metrics)로 보강한다.

기존 Signal/Score 로직은 변경하지 않는다. STEP 11 스크립트도 수정하지 않는다.

실행: python -m scripts.step12a_collect_missing_fundamentals
API Key: $env:DART_API_KEY=<key> (터미널에 직접 설정)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import config
from src.data_provider.dart_fundamental_provider import (
    compute_growth_metrics,
    fetch_quarterly_fundamentals,
    save_fundamentals_per_ticker,
)

FUNDAMENTAL_DIR = ROOT / "data" / "fundamentals"
START_YEAR = 2022   # YoY 기준연도 포함 (STEP 11과 동일)
END_YEAR = 2026


def _get_api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        print("[ERROR] DART_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)
    return key


def _missing_tickers() -> dict[str, str]:
    existing = {
        p.stem.replace("_fundamentals", "")
        for p in FUNDAMENTAL_DIR.glob("*_fundamentals.csv")
    }
    return {t: name for t, name in config.TICKERS.items() if t not in existing}


def run() -> None:
    api_key = _get_api_key()
    missing = _missing_tickers()

    if not missing:
        print("보강할 종목 없음 (모든 종목 실적 데이터 보유).")
        return

    print(f"보강 대상: {len(missing)}종목 → {', '.join(missing)}")

    try:
        import OpenDartReader
        dart = OpenDartReader(api_key)
    except Exception as e:
        print(f"[ERROR] OpenDartReader 초기화 실패: {e}")
        sys.exit(1)

    for ticker, name in missing.items():
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
        save_fundamentals_per_ticker(df_with_metrics, FUNDAMENTAL_DIR)
        print(f" OK  {len(df)}분기")

    print("\n수집 완료.")


if __name__ == "__main__":
    run()

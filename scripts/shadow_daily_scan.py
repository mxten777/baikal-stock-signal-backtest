"""
Shadow STEP 2 — Daily Shadow Pipeline (수동 실행 entry point).

흐름:
  현재 대상 종목(config.TICKERS)
    → 최신 가격 데이터 로드 (기존 CsvDataProvider)
    → 최신 거래일 기준 신규 Technical Signal 탐지 (기존 src.signal_engine.generate_signals)
    → 신규 Signal 발생 종목만 기존 Foreign 판정 로직(scripts.step9_investor_effect.compute_investor_features
      + scripts.step1b_flow_verification.classify_flow)으로 foreign_status 계산
    → 기존 STEP 1 규칙(decide_candidate)으로 CANDIDATE/EXCLUDED 결정
    → ShadowStore로 저장 (append-only, 동일 stock_code+signal_date 중복 방지)

이번 STEP에서 변경하지 않는 것:
  - Technical Signal 생성 로직 / threshold / weight / ROBUST_FILTER
  - Foreign 판정 로직(+-0.20 임계값)
  - Shadow STEP 1에서 확정한 CANDIDATE/EXCLUDED 규칙

이번 STEP에서 하지 않는 것:
  - 5D/10D/20D 성과 계산, Benchmark, 리포트, 대시보드, 알림, 실매수

실행: python scripts/shadow_daily_scan.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.shadow_step1_track_signals import foreign_status_from_ratio
from scripts.step1b_flow_verification import load_investor_map, load_raw_map
from scripts.step9_investor_effect import compute_investor_features
from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.indicators import add_all_indicators
from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DEFAULT_SHADOW_STORE_PATH,
    ShadowStore,
    decide_candidate,
)
from src.signal_engine import generate_signals


def detect_latest_signals(
    tickers: dict[str, str],
    price_data: dict[str, pd.DataFrame] | None,
    provider: CsvDataProvider,
) -> tuple[list[dict], pd.Timestamp | None]:
    """각 종목의 최신 거래일 기준 신규 Signal만 탐지한다 (기존 generate_signals 재사용).

    Signal이 없는 종목은 아무것도 반환하지 않는다.
    """
    detected: list[dict] = []
    run_date: pd.Timestamp | None = None

    for ticker, name in tickers.items():
        if price_data is not None:
            df = price_data.get(ticker)
            if df is None:
                print(f"  !! [{ticker}] {name}: 가격 데이터 없음 — skip")
                continue
        else:
            try:
                df = provider.load(ticker)
            except (FileNotFoundError, ValueError) as e:
                print(f"  !! [{ticker}] {name}: 가격 데이터 로드 실패 ({e}) — skip")
                continue

        latest_date = df["date"].max()
        if run_date is None or latest_date > run_date:
            run_date = latest_date

        df_ind = add_all_indicators(df)
        signals = generate_signals(df_ind, ticker, name)
        if signals.empty:
            continue

        # 최신 거래일에 발생한 신규 Signal만 채택 (과거 Signal 재저장 금지)
        latest_signals = signals[signals["signal_date"] == latest_date]
        for _, sig in latest_signals.iterrows():
            detected.append(sig.to_dict())

    return detected, run_date


def run_daily_scan(
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    investor_map: dict[str, pd.DataFrame] | None = None,
    raw_map: dict[str, pd.DataFrame] | None = None,
    store: ShadowStore | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, int], pd.Timestamp | None]:
    """Daily Shadow Pipeline 실행. (stats, 실행 기준일)을 반환한다."""
    tickers = tickers or config.TICKERS
    store = store or ShadowStore()
    provider = CsvDataProvider(config.DATA_RAW_DIR)

    stats = {
        "checked": len(tickers),
        "new_signals": 0,
        "candidate": 0,
        "excluded": 0,
        "duplicate_skip": 0,
        "no_data": 0,
    }

    detected, run_date = detect_latest_signals(tickers, price_data, provider)
    if not detected:
        return stats, run_date

    detected_df = pd.DataFrame(detected)
    detected_df["signal_date"] = pd.to_datetime(detected_df["signal_date"])
    stats["new_signals"] = len(detected_df)

    if investor_map is None:
        investor_map = load_investor_map()
    if raw_map is None:
        raw_map = load_raw_map()

    features = compute_investor_features(detected_df, investor_map, raw_map)

    for _, row in features.iterrows():
        ticker = row["ticker"]
        name = row["name"]
        signal_date_str = str(pd.Timestamp(row["signal_date"]).date())
        ratio = row["foreign_5d_ratio"]

        if pd.isna(ratio):
            stats["no_data"] += 1
            print(f"  [NO_DATA] {ticker} {name} {signal_date_str}: foreign 수급 데이터 없음 → NEUTRAL 처리")

        foreign_status = foreign_status_from_ratio(ratio)

        if store.exists(ticker, signal_date_str):
            stats["duplicate_skip"] += 1
            continue

        if dry_run:
            decision, _ = decide_candidate(foreign_status)
        else:
            record = store.record_signal(
                stock_code=ticker,
                stock_name=name,
                market=config.MARKET_MAP.get(ticker, "KS11"),
                signal_date=signal_date_str,
                signal_price=float(row["signal_close"]),
                signal_score=float(row["score"]),
                foreign_status=foreign_status,
            )
            if record is None:
                stats["duplicate_skip"] += 1
                continue
            decision = record.decision

        if decision == DECISION_CANDIDATE:
            stats["candidate"] += 1
        else:
            stats["excluded"] += 1

    return stats, run_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow STEP 2 — Daily Shadow Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Signal 탐지/Foreign 판정/Candidate 판정만 수행하고 CSV에는 저장하지 않는다.",
    )
    args = parser.parse_args()

    stats, run_date = run_daily_scan(dry_run=args.dry_run)

    print("=" * 50)
    print(f"실행 기준일: {run_date.date() if run_date is not None else 'N/A'}")
    print(f"검사 종목 수: {stats['checked']}")
    print(f"신규 Signal 수: {stats['new_signals']}")
    print(f"CANDIDATE 수: {stats['candidate']}")
    print(f"EXCLUDED 수: {stats['excluded']}")
    print(f"DUPLICATE SKIP 수: {stats['duplicate_skip']}")
    print(f"NO_DATA 수: {stats['no_data']}")
    save_note = " (dry-run: 실제 저장 안 함)" if args.dry_run else ""
    print(f"저장 파일: {DEFAULT_SHADOW_STORE_PATH}{save_note}")
    print("=" * 50)


if __name__ == "__main__":
    main()

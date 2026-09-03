"""
Shadow STEP 3 — Forward Return(5D/10D/20D) 추적 (수동 실행 entry point).

흐름:
  저장된 Shadow Record 로드
    → 종목별 가격 데이터 로드 (기존 CsvDataProvider, data/raw/{ticker}.csv)
    → signal_date의 거래일 위치 기준 +5/+10/+20 거래일 종가로 Forward Return(%) 계산
    → 성과 필드(return_5d/10d/20d, status)만 atomic write로 갱신

이번 STEP에서 변경하지 않는 것:
  - Technical Signal / Foreign 판정 / CANDIDATE-EXCLUDED 결정 로직
  - Shadow Record의 Signal 판정 관련 불변 필드
  - threshold / weight / filter

이번 STEP에서 하지 않는 것:
  - Benchmark, Excess Return, 리포트, 대시보드, 알림, 실매수

실행: python scripts/shadow_update_returns.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.shadow_tracking import (
    DEFAULT_SHADOW_STORE_PATH,
    RETURN_FIELD_BY_HORIZON,
    RETURN_MISMATCH_TOLERANCE,
    STATUS_COMPLETE,
    ShadowStore,
    compute_forward_returns,
    resolve_status,
)


def _load_price_df(
    ticker: str,
    price_map: dict[str, pd.DataFrame] | None,
    provider: CsvDataProvider,
) -> pd.DataFrame | None:
    if price_map is not None:
        return price_map.get(ticker)
    try:
        return provider.load(ticker)
    except (FileNotFoundError, ValueError):
        return None


def compute_updates(
    records: pd.DataFrame,
    price_map: dict[str, pd.DataFrame] | None = None,
    provider: CsvDataProvider | None = None,
) -> tuple[dict[tuple[str, str], dict[str, object]], dict[str, int]]:
    """Shadow Record별 성과 필드 갱신안과 실행 통계를 만든다 (파일은 건드리지 않는다)."""
    provider = provider or CsvDataProvider(config.DATA_RAW_DIR)
    stats = {
        "total": len(records),
        "checked": 0,
        "new_5d": 0,
        "new_10d": 0,
        "new_20d": 0,
        "complete": 0,
        "pending": 0,
        "missing_price": 0,
        "mismatch": 0,
    }
    updates: dict[tuple[str, str], dict[str, object]] = {}
    price_cache: dict[str, pd.DataFrame | None] = {}

    for _, row in records.iterrows():
        ticker = str(row["stock_code"])
        signal_date = str(row["signal_date"])

        if ticker not in price_cache:
            price_cache[ticker] = _load_price_df(ticker, price_map, provider)
        price_df = price_cache[ticker]

        computed = (
            None
            if price_df is None
            else compute_forward_returns(price_df, signal_date, row["signal_price"])
        )
        if computed is None:
            stats["missing_price"] += 1
            print(f"  !! [{ticker}] {signal_date}: 가격 데이터 또는 signal_date 없음 — 미갱신")
            continue

        stats["checked"] += 1

        values: dict[str, float | None] = {}
        for field in RETURN_FIELD_BY_HORIZON.values():
            existing = row.get(field)
            existing_na = existing is None or pd.isna(existing)
            new_value = computed[field]

            if not existing_na:
                if new_value is not None and abs(float(existing) - float(new_value)) > RETURN_MISMATCH_TOLERANCE:
                    stats["mismatch"] += 1
                    print(
                        f"  !! [{ticker}] {signal_date} {field}: 기존값 {float(existing):.4f} != "
                        f"재계산값 {float(new_value):.4f} — 덮어쓰지 않음 (데이터 정합성 확인 필요)"
                    )
                values[field] = float(existing)  # 기존 값 보존
                continue

            if new_value is not None:
                stats[f"new_{field.split('_')[1]}"] += 1
            values[field] = None if new_value is None else float(new_value)

        new_status = resolve_status(values["return_5d"], values["return_10d"], values["return_20d"])
        if new_status == STATUS_COMPLETE:
            if str(row.get("status")) != STATUS_COMPLETE:
                stats["complete"] += 1
        else:
            stats["pending"] += 1

        patch: dict[str, object] = dict(values)
        patch["status"] = new_status
        updates[(ticker, signal_date)] = patch

    return updates, stats


def run_update_returns(
    store: ShadowStore | None = None,
    price_map: dict[str, pd.DataFrame] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Shadow STEP 3 실행. dry-run이면 CSV를 수정하지 않는다."""
    store = store or ShadowStore()
    records = store.load()
    if records.empty:
        return {
            "total": 0,
            "checked": 0,
            "new_5d": 0,
            "new_10d": 0,
            "new_20d": 0,
            "complete": 0,
            "pending": 0,
            "missing_price": 0,
            "mismatch": 0,
            "updated": 0,
        }

    updates, stats = compute_updates(records, price_map=price_map)
    stats["updated"] = 0 if dry_run else store.update_performance(updates)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow STEP 3 — Forward Return(5D/10D/20D) 추적")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산 결과와 status 변화만 확인하고 CSV에는 저장하지 않는다.",
    )
    args = parser.parse_args()

    stats = run_update_returns(dry_run=args.dry_run)

    print("=" * 50)
    print(f"전체 Shadow Record 수: {stats['total']}")
    print(f"확인한 Record 수: {stats['checked']}")
    print(f"5D 신규 계산 수: {stats['new_5d']}")
    print(f"10D 신규 계산 수: {stats['new_10d']}")
    print(f"20D 신규 계산 수: {stats['new_20d']}")
    print(f"COMPLETE 전환 수: {stats['complete']}")
    print(f"평가 시점 미도래 수: {stats['pending']}")
    print(f"가격 데이터 누락 수: {stats['missing_price']}")
    print(f"기존값 불일치 수: {stats['mismatch']}")
    print(f"실제 갱신 Record 수: {stats['updated']}")
    save_note = " (dry-run: 실제 저장 안 함)" if args.dry_run else ""
    print(f"저장 파일: {DEFAULT_SHADOW_STORE_PATH}{save_note}")
    print("=" * 50)


if __name__ == "__main__":
    main()

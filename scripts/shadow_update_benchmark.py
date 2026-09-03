"""
Shadow STEP 4 — Benchmark(KS11/KQ11) Return 및 Excess Return 계산 (수동 실행 entry point).

흐름:
  저장된 Shadow Record 로드
    → market 값을 KS11/KQ11로 정규화 (KOSPI→KS11, KOSDAQ→KQ11)
    → 기존 백테스트와 동일한 Benchmark 데이터 소스(src.benchmark.load_benchmark) 재사용
    → signal_date의 Benchmark 거래일 위치 기준 +5/+10/+20 거래일 종가로 benchmark_return(%) 계산
    → 종목 return_Nd와 benchmark_return_Nd가 모두 있을 때만 excess_Nd 계산
    → benchmark/excess 필드만 atomic write로 갱신

이번 STEP에서 변경하지 않는 것:
  - 종목 Forward Return 계산 로직 (STEP 3)
  - Technical Signal / Foreign 판정 / CANDIDATE-EXCLUDED 결정
  - status(OPEN/5D_DONE/10D_DONE/COMPLETE)의 의미와 규칙
  - threshold / weight / filter

이번 STEP에서 하지 않는 것:
  - 성과 요약 리포트, 승률/평균 Excess 집계, 대시보드, 알림, 실매수

실행: python scripts/shadow_update_benchmark.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.shadow_tracking import (
    BENCHMARK_FIELD_BY_HORIZON,
    DEFAULT_SHADOW_STORE_PATH,
    EXCESS_FIELD_BY_HORIZON,
    FORWARD_HORIZONS,
    RETURN_FIELD_BY_HORIZON,
    RETURN_MISMATCH_TOLERANCE,
    ShadowStore,
    compute_benchmark_returns,
    compute_excess,
    normalize_market,
)

EMPTY_STATS = {
    "total": 0,
    "checked": 0,
    "kospi": 0,
    "kosdaq": 0,
    "new_benchmark_5d": 0,
    "new_benchmark_10d": 0,
    "new_benchmark_20d": 0,
    "new_excess_5d": 0,
    "new_excess_10d": 0,
    "new_excess_20d": 0,
    "missing_benchmark": 0,
    "unknown_market": 0,
    "mismatch": 0,
    "updated": 0,
}


def load_benchmark_map(records: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Shadow Record에 실제로 등장하는 시장의 Benchmark만 기존 소스에서 로드한다."""
    from src.benchmark import load_benchmark

    symbols = {
        symbol
        for symbol in (normalize_market(m) for m in records["market"])
        if symbol is not None
    }
    if not symbols:
        return {}

    start = (pd.to_datetime(records["signal_date"]).min() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    return {symbol: load_benchmark(symbol, start) for symbol in sorted(symbols)}


def _resolve_field(
    row: pd.Series,
    field: str,
    new_value: float | None,
    stats: dict[str, int],
    label: str,
    stat_key: str | None,
) -> tuple[float | None, bool]:
    """기존 값이 있으면 보존하고, 불일치 시 mismatch로 보고한다.

    Returns (저장에 사용할 값, patch에 포함할지 여부).
    """
    existing = row.get(field)
    existing_na = existing is None or pd.isna(existing)

    if not existing_na:
        if new_value is not None and abs(float(existing) - float(new_value)) > RETURN_MISMATCH_TOLERANCE:
            stats["mismatch"] += 1
            print(
                f"  !! {label} {field}: 기존값 {float(existing):.4f} != "
                f"재계산값 {float(new_value):.4f} — 덮어쓰지 않음 (데이터 정합성 확인 필요)"
            )
        return float(existing), False

    if new_value is None:
        return None, False

    if stat_key:
        stats[stat_key] += 1
    return float(new_value), True


def compute_benchmark_updates(
    records: pd.DataFrame,
    benchmark_map: dict[str, pd.DataFrame],
) -> tuple[dict[tuple[str, str], dict[str, object]], dict[str, int]]:
    """Shadow Record별 benchmark/excess 갱신안과 실행 통계를 만든다 (파일은 건드리지 않는다)."""
    stats = dict(EMPTY_STATS)
    stats["total"] = len(records)
    updates: dict[tuple[str, str], dict[str, object]] = {}

    for _, row in records.iterrows():
        ticker = str(row["stock_code"])
        signal_date = str(row["signal_date"])
        label = f"[{ticker}] {signal_date}"

        symbol = normalize_market(row.get("market"))
        if symbol is None:
            stats["unknown_market"] += 1
            print(f"  !! {label}: 알 수 없는 market {row.get('market')!r} — 미갱신")
            continue

        stats["kospi" if symbol == "KS11" else "kosdaq"] += 1

        benchmark_df = benchmark_map.get(symbol)
        computed = (
            None
            if benchmark_df is None
            else compute_benchmark_returns(benchmark_df, signal_date)
        )
        if computed is None:
            stats["missing_benchmark"] += 1
            print(f"  !! {label}: Benchmark({symbol}) 데이터 또는 signal_date 없음 — 미갱신")
            continue

        stats["checked"] += 1

        patch: dict[str, object] = {}
        for horizon in FORWARD_HORIZONS:
            bm_field = BENCHMARK_FIELD_BY_HORIZON[horizon]
            excess_field = EXCESS_FIELD_BY_HORIZON[horizon]

            bm_value, bm_write = _resolve_field(
                row, bm_field, computed[bm_field], stats, label, f"new_benchmark_{horizon}d"
            )
            if bm_write:
                patch[bm_field] = bm_value

            excess_value, excess_write = _resolve_field(
                row,
                excess_field,
                compute_excess(row.get(RETURN_FIELD_BY_HORIZON[horizon]), bm_value),
                stats,
                label,
                f"new_excess_{horizon}d",
            )
            if excess_write:
                patch[excess_field] = excess_value

        if patch:
            updates[(ticker, signal_date)] = patch

    return updates, stats


def run_update_benchmark(
    store: ShadowStore | None = None,
    benchmark_map: dict[str, pd.DataFrame] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Shadow STEP 4 실행. dry-run이면 CSV를 수정하지 않는다."""
    store = store or ShadowStore()
    records = store.load()
    if records.empty:
        return dict(EMPTY_STATS)

    if benchmark_map is None:
        benchmark_map = load_benchmark_map(records)

    updates, stats = compute_benchmark_updates(records, benchmark_map)
    stats["updated"] = 0 if dry_run else store.update_performance(updates)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shadow STEP 4 — Benchmark(KS11/KQ11) Return 및 Excess Return 계산"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산과 통계만 수행하고 CSV에는 저장하지 않는다.",
    )
    args = parser.parse_args()

    stats = run_update_benchmark(dry_run=args.dry_run)

    print("=" * 50)
    print(f"전체 Shadow Record 수: {stats['total']}")
    print(f"확인한 Record 수: {stats['checked']}")
    print(f"KOSPI(KS11) Record 수: {stats['kospi']}")
    print(f"KOSDAQ(KQ11) Record 수: {stats['kosdaq']}")
    print(f"benchmark 5D 신규 계산 수: {stats['new_benchmark_5d']}")
    print(f"benchmark 10D 신규 계산 수: {stats['new_benchmark_10d']}")
    print(f"benchmark 20D 신규 계산 수: {stats['new_benchmark_20d']}")
    print(f"excess 5D 신규 계산 수: {stats['new_excess_5d']}")
    print(f"excess 10D 신규 계산 수: {stats['new_excess_10d']}")
    print(f"excess 20D 신규 계산 수: {stats['new_excess_20d']}")
    print(f"Benchmark 데이터 누락 수: {stats['missing_benchmark']}")
    print(f"알 수 없는 market 수: {stats['unknown_market']}")
    print(f"기존값 불일치 수: {stats['mismatch']}")
    print(f"실제 갱신 Record 수: {stats['updated']}")
    print(f"dry-run: {'예' if args.dry_run else '아니오'}")
    print(f"저장 파일: {DEFAULT_SHADOW_STORE_PATH}")
    print("=" * 50)


if __name__ == "__main__":
    main()

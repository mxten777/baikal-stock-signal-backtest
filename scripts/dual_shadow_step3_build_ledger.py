"""Dual Shadow STEP 3 — DUAL Comparison Ledger 저장 (수동 실행 entry point).

흐름:
  config.TICKERS 20종목 각각의 최신 가격 데이터를 로드하고
  src.dual_shadow_evaluator.evaluate_latest_day()로 Baseline(v0.1)/Challenger(v0.2)를
  평가한 뒤, src.dual_shadow_ledger.DualShadowLedgerStore로 append-only 저장한다.

이번 STEP에서 변경하지 않는 것:
  - 기존 Production Shadow 코드/산출물/schema (src.shadow_tracking, output/shadow_signal_records.csv)
  - Scheduler, Dashboard, API

이번 STEP에서 하지 않는 것:
  - Forward Return 계산

실행: python -m scripts.dual_shadow_step3_build_ledger [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dual_shadow_ledger import DualShadowLedgerStore, run_dual_shadow_ledger_build


def main() -> int:
    parser = argparse.ArgumentParser(description="DUAL Shadow STEP 3 — Comparison Ledger 저장")
    parser.add_argument("--dry-run", action="store_true", help="실제 CSV 저장 없이 통계만 출력")
    args = parser.parse_args()

    if args.dry_run:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = DualShadowLedgerStore(path=Path(tmp_dir) / "dual_shadow_signal_ledger.csv")
            saved, stats = run_dual_shadow_ledger_build(store=store)
    else:
        saved, stats = run_dual_shadow_ledger_build()

    print(f"[DUAL Shadow STEP 3] checked={stats['checked']} saved={stats['saved']} "
          f"duplicate_skip={stats['duplicate_skip']}")
    print(f"  BOTH_YES={stats['both_yes']} BASELINE_ONLY={stats['baseline_only']} "
          f"CHALLENGER_ONLY={stats['challenger_only']} BOTH_NO={stats['both_no']} "
          f"NOT_EVALUABLE={stats['not_evaluable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

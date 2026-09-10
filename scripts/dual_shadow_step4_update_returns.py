"""DUAL Shadow STEP 4 — Forward Return Tracking 저장 (수동 실행 entry point).

흐름:
  output/dual_shadow_signal_ledger.csv (STEP 3, 읽기 전용) 로드
    → 종목별 가격 데이터 로드 (기존 CsvDataProvider, data/raw/{ticker}.csv)
    → evaluation_close 기준 +5/+10/+20 거래일 종가로 Forward Return(%) 계산
    → 완성된(AVAILABLE) 조합만 output/dual_shadow_forward_returns.csv에 append-only 저장
      (아직 도래하지 않은 horizon은 NOT_AVAILABLE 통계로만 보고하고 CSV row로 남기지 않음)

이번 STEP에서 변경하지 않는 것:
  - STEP 3 Ledger의 당시 판단값
  - 기존 Production Shadow 코드/산출물/schema (src.shadow_tracking)
  - Scheduler, Dashboard, API

이번 STEP에서 하지 않는 것:
  - Baseline/Challenger 성과 우열 판단

실행: python -m scripts.dual_shadow_step4_update_returns [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dual_shadow_forward_returns import (
    DEFAULT_FORWARD_RETURN_PATH,
    DualForwardReturnStore,
    run_dual_shadow_forward_returns_update,
)
from src.dual_shadow_ledger import DEFAULT_LEDGER_PATH, DualShadowLedgerStore


def main() -> int:
    parser = argparse.ArgumentParser(description="DUAL Shadow STEP 4 — Forward Return Tracking")
    parser.add_argument("--dry-run", action="store_true", help="실제 CSV 저장 없이 통계만 출력")
    args = parser.parse_args()

    if args.dry_run:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            forward_store = DualForwardReturnStore(path=Path(tmp_dir) / "dual_shadow_forward_returns.csv")
            stats = run_dual_shadow_forward_returns_update(
                ledger_store=DualShadowLedgerStore(path=DEFAULT_LEDGER_PATH),
                forward_store=forward_store,
                dry_run=True,
            )
    else:
        stats = run_dual_shadow_forward_returns_update()

    print(
        f"[DUAL Shadow STEP 4] ledger_rows={stats['ledger_rows']} "
        f"skipped_not_evaluable={stats['skipped_not_evaluable']} missing_price={stats['missing_price']}"
    )
    print(
        f"  already_recorded={stats['already_recorded']} available={stats['available']} "
        f"not_available={stats['not_available']} saved={stats['saved']}"
    )
    print(f"  저장 파일: {DEFAULT_FORWARD_RETURN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

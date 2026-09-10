"""DUAL Shadow STEP 5 — Baseline vs Challenger 성과 비교 집계 (READ-ONLY 실행 entry point).

흐름:
  output/dual_shadow_signal_ledger.csv (STEP 3, 읽기 전용)
  + output/dual_shadow_forward_returns.csv (STEP 4, 읽기 전용)
  → Read → Aggregate → Compare
  → output/dual_shadow_performance_summary.json (derived read-model, 재생성 가능, git 추적 안 함)

이번 STEP에서 하지 않는 것:
  - Signal 재계산 / Ledger 수정 / Forward Return 재계산
  - Scheduler 연결, Dashboard/API 변경
  - 자동 승자 판정

실행: python -m scripts.dual_shadow_step5_compare
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dual_shadow_performance import (
    DEFAULT_PERFORMANCE_SUMMARY_PATH,
    build_performance_summary,
)


def main() -> int:
    summary = build_performance_summary()
    payload = summary.to_dict()

    DEFAULT_PERFORMANCE_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PERFORMANCE_SUMMARY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[DUAL Shadow STEP 5] status={summary.status} total_evidence_rows={summary.total_evidence_rows}")
    for horizon, data in summary.horizons.items():
        b = data["baseline"]
        c = data["challenger"]
        print(
            f"  horizon={horizon}D baseline_count={b['signal_count']} challenger_count={c['signal_count']} "
            f"baseline_avg={b['avg_return']} challenger_avg={c['avg_return']}"
        )
    print(f"  저장 파일: {DEFAULT_PERFORMANCE_SUMMARY_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

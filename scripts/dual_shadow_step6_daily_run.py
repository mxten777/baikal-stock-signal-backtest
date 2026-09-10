"""DUAL Shadow STEP 6 - manual daily pipeline entry point.

Run with:
  python -m scripts.dual_shadow_step6_daily_run --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.dual_shadow_daily_pipeline import STATUS_FAILED, run_dual_shadow_daily_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="DUAL Shadow STEP 6 manual daily pipeline")
    parser.add_argument("--trade-date", help="Expected trade date YYYY-MM-DD. Defaults to latest uniform market date.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON result")
    args = parser.parse_args()

    result = run_dual_shadow_daily_pipeline(target_trade_date=args.trade_date)
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"[DUAL Shadow STEP 6] trade_date={result.trade_date} status={result.status} "
            f"ledger_saved={result.ledger.get('saved', 0)} "
            f"forward_saved={result.forward_returns.get('saved', 0)} "
            f"performance={result.performance.get('status')}"
        )
        if result.errors:
            for error in result.errors:
                print(f"  ERROR {error['code']}: {error['message']}")
    return 1 if result.status == STATUS_FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())

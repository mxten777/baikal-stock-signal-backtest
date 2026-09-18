"""Fail-safe CLI for the Expanded Shadow pipeline.

STEP 13-D7 does not approve real 574-provider execution. This entry point is
present so future steps have a stable command surface, but it refuses to run
unless a later implementation wires approved real sources explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow daily pipeline")
    parser.add_argument("--bas-dd", default="2026-09-17")
    parser.add_argument(
        "--allow-real-providers",
        action="store_true",
        help="Reserved for a future approved step; D7 never runs real providers.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = {
        "status": "BLOCKED",
        "error_code": "REAL_PROVIDER_RUN_NOT_IMPLEMENTED_IN_D7",
        "basDd": args.bas_dd,
        "message": "STEP 13-D7 exposes only a fail-safe CLI; use tests/fake sources for pipeline execution.",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"BLOCKED: {payload['error_code']}")
        print(payload["message"])
    return 2


if __name__ == "__main__":
    sys.exit(main())
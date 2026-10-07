"""Run offline STEP 35-C research from the fixed local 2026-10-07 snapshot."""

from __future__ import annotations

import json
from pathlib import Path

from src.research.pipeline import OUTPUT_RELATIVE, run_research


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = run_research(root)
    print(json.dumps({
        "output": str(root / OUTPUT_RELATIVE), "counts": result["counts"],
    }, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

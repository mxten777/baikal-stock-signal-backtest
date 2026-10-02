"""Print independent analysis without writing reports or updating ledgers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.benchmark import load_benchmark
from src.expanded_performance_analysis import build_performance_analysis, diagnose_benchmark_coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff-date", required=True)
    parser.add_argument("--check-benchmark", action="store_true", help="Read external index prices for diagnostics only")
    parser.add_argument("--include-rows", action="store_true")
    args = parser.parse_args(argv)
    result = build_performance_analysis(Path.cwd(), cutoff_date=args.cutoff_date)
    errors = {}
    if args.check_benchmark and result["rows"]:
        start = min(row["signal_date"] for row in result["rows"])
        benchmarks: dict[str, pd.DataFrame] = {}
        for symbol in ("KS11", "KQ11"):
            try:
                benchmarks[symbol] = load_benchmark(symbol, start, args.cutoff_date)
            except Exception as exc:
                errors[symbol] = f"{type(exc).__name__}: {exc}"
        result["benchmark_coverage"] = diagnose_benchmark_coverage(
            result["rows"], benchmarks, result["cutoff_date"],
        )
    result["benchmark_errors"] = errors
    result["analysis_row_count"] = len(result["rows"])
    if not args.include_rows:
        del result["rows"]
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
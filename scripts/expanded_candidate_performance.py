"""Manual entry point for Expanded Candidate Performance Tracking."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.benchmark import load_benchmark
from src.expanded_candidate_performance import run_expanded_candidate_performance
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.shadow_tracking import normalize_market


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Candidate Performance Tracker")
    parser.add_argument("--source-date", required=True, help="Prepared Expanded snapshot date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        source_date = _validate_source_date(args.source_date)
        repo_root = Path.cwd()
        paths = ExpandedShadowPaths(repo_root)
        signal_ledger = _load_signal_ledger(paths)
        candidates = signal_ledger.loc[signal_ledger.get("decision", pd.Series(dtype=str)).astype(str) == "CANDIDATE"]
        price_map = _load_price_map(paths, source_date, candidates)
        benchmark_map, benchmark_errors = _load_benchmark_map(candidates, source_date)
        stats = run_expanded_candidate_performance(
            repo_root=repo_root,
            price_map=price_map,
            benchmark_map=benchmark_map,
            dry_run=args.dry_run,
        )
        payload = {
            "status": "SUCCESS",
            "source_date": source_date,
            "dry_run": args.dry_run,
            "ledger_path": str(paths.output_root / "expanded_candidate_performance_ledger.csv"),
            "benchmark_errors": benchmark_errors,
            **stats,
        }
    except Exception as exc:  # noqa: BLE001 - CLI returns machine-readable failure
        payload = {
            "status": "FAILED",
            "source_date": args.source_date,
            "error_code": type(exc).__name__,
            "message": str(exc),
        }
        _print(payload, args.json)
        return 1

    _print(payload, args.json)
    return 0


def _load_signal_ledger(paths: ExpandedShadowPaths) -> pd.DataFrame:
    if not paths.signal_ledger_path.exists():
        return pd.DataFrame()
    return pd.read_csv(
        paths.signal_ledger_path,
        dtype={"basDd": str, "stock_code": str, "signal_date": str, "engine_version": str},
    )


def _load_price_map(
    paths: ExpandedShadowPaths,
    source_date: str,
    candidates: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    if candidates.empty or "stock_code" not in candidates:
        return result
    for ticker in sorted(set(candidates["stock_code"].astype(str))):
        path = paths.market_dir(source_date) / f"{ticker}.csv"
        if path.is_file():
            result[ticker] = pd.read_csv(path)
    return result


def _load_benchmark_map(
    candidates: pd.DataFrame,
    source_date: str,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    if candidates.empty or "market" not in candidates or "signal_date" not in candidates:
        return {}, {}
    symbols = sorted({symbol for symbol in candidates["market"].map(normalize_market) if symbol is not None})
    start_date = (pd.to_datetime(candidates["signal_date"]).min() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    result: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            result[symbol] = load_benchmark(symbol, start_date, source_date)
        except Exception as exc:  # noqa: BLE001 - unavailable benchmark remains pending
            errors[symbol] = f"{type(exc).__name__}: {exc}"
    return result, errors


def _validate_source_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"source date must use YYYY-MM-DD: {value!r}")
    return value


def _print(payload: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"{payload['status']}: {payload['source_date']}")
    if payload.get("message"):
        print(payload["message"])


if __name__ == "__main__":
    sys.exit(main())
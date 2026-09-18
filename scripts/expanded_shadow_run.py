"""Fail-safe CLI for the Expanded Shadow pipeline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from src.expanded_shadow_data import FinanceDataReaderMarketSource, NaverInvestorFlowSource, RetryPolicy
from src.expanded_shadow_pipeline import STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES, run_expanded_shadow_pipeline


MARKET_START_DATE = "2024-01-01"


class FixedStartMarketSource:
    """Force the D8-A validated market history start date for CLI real runs."""

    def __init__(self, source: FinanceDataReaderMarketSource, start_date: str = MARKET_START_DATE) -> None:
        self.source = source
        self.start_date = start_date

    def fetch(self, ticker: str, start: str, end: str):
        del start
        return self.source.fetch(ticker, self.start_date, end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow daily pipeline")
    parser.add_argument("--bas-dd", default="2026-09-17")
    parser.add_argument(
        "--allow-real-providers",
        action="store_true",
        help="Explicitly permit real FDR/Naver providers for the full Expanded Shadow run.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.allow_real_providers:
        return _run_real_providers(args.bas_dd, args.json)

    payload = {
        "status": "BLOCKED",
        "error_code": "REAL_PROVIDER_RUN_REQUIRES_ALLOW_FLAG",
        "basDd": args.bas_dd,
        "message": "Real Expanded providers require explicit --allow-real-providers approval.",
    }
    _print_payload(payload, args.json)
    return 2


def _run_real_providers(basDd: str, json_output: bool) -> int:
    try:
        result = run_expanded_shadow_pipeline(
            repo_root=Path.cwd(),
            basDd=basDd,
            market_source=FixedStartMarketSource(FinanceDataReaderMarketSource()),
            investor_source=NaverInvestorFlowSource(),
            retry_policy=RetryPolicy(max_attempts=2),
            source_commit=_source_commit(Path.cwd()),
        )
    except Exception as exc:  # noqa: BLE001 - CLI must return machine-readable failure
        payload = {
            "status": "FAILED",
            "error_code": type(exc).__name__,
            "basDd": basDd,
            "message": str(exc),
        }
        _print_payload(payload, json_output)
        return 1

    payload = {
        "status": result.status,
        "run_id": result.run_id,
        "basDd": result.basDd,
        "market_start_date": MARKET_START_DATE,
        "canonical_universe_count": result.manifest.canonical_universe_count,
        "attempted_ticker_count": result.manifest.attempted_ticker_count,
        "ready_count": result.manifest.ready_count,
        "quarantine_count": result.manifest.quarantine_count,
        "signal_count": result.manifest.signal_count,
        "new_candidate_count": result.manifest.new_candidate_count,
        "status_counts": result.manifest.status_counts,
    }
    _print_payload(payload, json_output)
    return 0 if result.status in {STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES} else 1


def _source_commit(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else "UNKNOWN"


def _print_payload(payload: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{payload['status']}: {payload.get('error_code') or payload.get('run_id', '')}")
        if payload.get("message"):
            print(payload["message"])


if __name__ == "__main__":
    sys.exit(main())
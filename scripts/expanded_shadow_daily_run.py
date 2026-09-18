"""Manual entry point for one prepared Expanded Shadow daily run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from src.expanded_shadow_daily import ACTION_SKIP, run_expanded_shadow_daily


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow incremental daily run")
    parser.add_argument("--source-date", required=True, help="Prepared trading date in YYYY-MM-DD format")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run_expanded_shadow_daily(
            repo_root=Path.cwd(),
            source_date=args.source_date,
            source_commit=_source_commit(Path.cwd()),
        )
        payload = result.to_dict()
    except Exception as exc:  # noqa: BLE001 - CLI returns machine-readable failure
        payload = {
            "status": "FAILED",
            "action": "STOP",
            "source_date": args.source_date,
            "error_code": type(exc).__name__,
            "message": str(exc),
        }
        _print_payload(payload, args.json)
        return 1

    _print_payload(payload, args.json)
    return 0 if result.action != ACTION_SKIP or result.status in {"ALREADY_COMPLETED", "DATA_NOT_READY"} else 1


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
        return
    print(f"{payload['status']} / {payload['action']}: {payload['source_date']}")
    if payload.get("message"):
        print(payload["message"])


if __name__ == "__main__":
    sys.exit(main())
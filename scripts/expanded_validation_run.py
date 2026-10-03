"""Read-only Naver provider smoke and isolated Expanded operational validation."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import multiprocessing
import subprocess
import sys
import time
import traceback
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts.expanded_candidate_performance import PROVIDER_LEGACY, PROVIDER_NAVER
from src.expanded_benchmark_provider import (
    NAVER_SOURCE_BY_SYMBOL,
    ExpandedBenchmark,
    ExpandedBenchmarkTimeoutError,
    load_expanded_benchmark,
)


def run_provider_smoke(
    *,
    start_date: str,
    cutoff_date: str,
    benchmark_loader: Callable[[str, str, str], ExpandedBenchmark] = load_expanded_benchmark,
) -> tuple[list[dict[str, Any]], list[int]]:
    children_before = {child.pid for child in multiprocessing.active_children()}
    results: list[dict[str, Any]] = []
    for symbol, source in NAVER_SOURCE_BY_SYMBOL.items():
        started = time.perf_counter()
        item: dict[str, Any] = {
            "symbol": symbol,
            "provider": PROVIDER_NAVER,
            "source": source,
            "requested_start_date": start_date,
            "requested_cutoff_date": cutoff_date,
        }
        try:
            benchmark = benchmark_loader(symbol, start_date, cutoff_date)
            item.update(
                status="SUCCESS",
                diagnostics=benchmark.diagnostics(),
                returned_start_date=benchmark.dates[0] if benchmark.dates else None,
                returned_end_date=benchmark.dates[-1] if benchmark.dates else None,
            )
        except Exception as exc:
            item.update(
                status="FAILED",
                error_code=type(exc).__name__,
                error_message=str(exc),
                timeout=isinstance(exc, ExpandedBenchmarkTimeoutError),
            )
            traceback.print_exc()
        finally:
            item["runtime_seconds"] = max(time.perf_counter() - started, 0.0)
            results.append(item)
    remaining = [
        child.pid
        for child in multiprocessing.active_children()
        if child.pid not in children_before
    ]
    return results, remaining


def run_validation(
    *,
    mode: str,
    repo_root: Path,
    artifact_dir: Path,
    benchmark_provider: str = PROVIDER_NAVER,
    source_date: str | None = None,
    start_date: str | None = None,
    cutoff_date: str | None = None,
    benchmark_loader: Callable[[str, str, str], ExpandedBenchmark] = load_expanded_benchmark,
    process_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[int, Path]:
    root = repo_root.resolve()
    target_dir = artifact_dir.resolve()
    if target_dir == root or root in target_dir.parents or target_dir in root.parents:
        raise ValueError("artifact directory must be outside the repository and its parent directories")
    if mode not in {"smoke", "operational"}:
        raise ValueError(f"unsupported validation mode: {mode!r}")
    if mode == "operational" and benchmark_provider not in {PROVIDER_LEGACY, PROVIDER_NAVER}:
        raise ValueError(f"unsupported benchmark provider: {benchmark_provider!r}")
    if mode == "smoke" and (start_date is None or cutoff_date is None):
        raise ValueError("smoke validation requires start_date and cutoff_date")

    target_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    monotonic_start = time.perf_counter()
    stdout = io.StringIO()
    stderr = io.StringIO()
    record: dict[str, Any] = {
        "mode": mode,
        "benchmark_provider": PROVIDER_NAVER if mode == "smoke" else benchmark_provider,
        "started_at": started_at.isoformat(timespec="seconds"),
        "requested_date_range": (
            {"start_date": start_date, "cutoff_date": cutoff_date}
            if mode == "smoke"
            else {"source_date": source_date}
        ),
        "stdout": "",
        "stderr": "",
        "result": None,
        "exit_code": 1,
    }
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            if mode == "smoke":
                assert start_date is not None and cutoff_date is not None
                results, remaining_children = run_provider_smoke(
                    start_date=start_date,
                    cutoff_date=cutoff_date,
                    benchmark_loader=benchmark_loader,
                )
                record["result"] = {
                    "symbols": results,
                    "child_processes_remaining": remaining_children,
                }
                record["exit_code"] = 0 if all(item["status"] == "SUCCESS" for item in results) else 1
            else:
                command = [
                    sys.executable,
                    "-m",
                    "scripts.expanded_operational_run",
                    "--json",
                    "--benchmark-provider",
                    benchmark_provider,
                ]
                if source_date is not None:
                    command.extend(["--source-date", source_date])
                completed = process_runner(
                    command,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                stdout.write(completed.stdout)
                stderr.write(completed.stderr)
                record["exit_code"] = completed.returncode
                try:
                    record["result"] = json.loads(completed.stdout)
                except json.JSONDecodeError as exc:
                    record["result_parse_error"] = str(exc)
        except Exception:
            traceback.print_exc()
            record["exit_code"] = 1

    completed_at = datetime.now(timezone.utc)
    record.update(
        completed_at=completed_at.isoformat(timespec="seconds"),
        runtime_seconds=max(time.perf_counter() - monotonic_start, 0.0),
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )
    artifact_path = target_dir / (
        f"expanded-validation-{mode}-{started_at.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex}.json"
    )
    with artifact_path.open("x", encoding="utf-8", newline="\n") as artifact:
        json.dump(record, artifact, ensure_ascii=False, indent=2, sort_keys=True)
        artifact.write("\n")
    return int(record["exit_code"]), artifact_path


def _validate_iso_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated Expanded Naver validation.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    smoke = subparsers.add_parser("smoke", help="Fetch both Naver indices using the hard-timeout provider.")
    smoke.add_argument("--artifact-dir", type=Path, required=True)
    smoke.add_argument("--start-date", type=_validate_iso_date, required=True)
    smoke.add_argument("--cutoff-date", type=_validate_iso_date, required=True)

    operational = subparsers.add_parser("operational", help="Run the operational entry point and capture its output.")
    operational.add_argument("--artifact-dir", type=Path, required=True)
    operational.add_argument("--benchmark-provider", choices=(PROVIDER_LEGACY, PROVIDER_NAVER), required=True)
    operational.add_argument("--source-date", type=_validate_iso_date)

    args = parser.parse_args(argv)
    try:
        exit_code, artifact_path = run_validation(
            mode=args.mode,
            repo_root=Path.cwd(),
            artifact_dir=args.artifact_dir,
            benchmark_provider=getattr(args, "benchmark_provider", PROVIDER_NAVER),
            source_date=getattr(args, "source_date", None),
            start_date=getattr(args, "start_date", None),
            cutoff_date=getattr(args, "cutoff_date", None),
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"validation_exit_code={exit_code}")
    print(f"validation_artifact={artifact_path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

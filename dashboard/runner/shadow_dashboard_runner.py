from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from scripts import shadow_daily_pipeline
from src.shadow_tracking import SHADOW_RECORD_FIELDS


ROOT = Path(__file__).resolve().parents[2]
METADATA_SOURCE = "output/shadow_dashboard_run_metadata.json"
LEDGER_SOURCE = "output/shadow_signal_records.csv"
RUNNER_VERSION = "1.0"
INPUT_FRESHNESS_STALE_AFTER_DAYS = 5
INPUT_FRESHNESS_POLICY = (
    "CURRENT when each required local input CSV group has max(date) within "
    f"{INPUT_FRESHNESS_STALE_AFTER_DAYS} calendar days of finished_at date; "
    "STALE when older; MISSING when source/date is absent; UNAVAILABLE on read errors."
)


@dataclass(frozen=True)
class DashboardRunResult:
    metadata: dict[str, Any]
    metadata_path: Path
    pipeline_result: Any | None = None
    exception: BaseException | None = None
    traceback_text: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def inspect_ledger(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.exists():
        return {
            "ledger_status": "MISSING",
            "ledger_path": LEDGER_SOURCE,
            "record_count": 0,
            "ledger_warning": "shadow ledger file is missing",
        }
    if ledger_path.stat().st_size == 0:
        return {
            "ledger_status": "EMPTY",
            "ledger_path": LEDGER_SOURCE,
            "record_count": 0,
            "ledger_warning": "shadow ledger file is empty",
        }

    try:
        with ledger_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return {
                    "ledger_status": "EMPTY",
                    "ledger_path": LEDGER_SOURCE,
                    "record_count": 0,
                    "ledger_warning": "shadow ledger has no header",
                }
            missing = [column for column in SHADOW_RECORD_FIELDS if column not in reader.fieldnames]
            if missing:
                return {
                    "ledger_status": "MALFORMED",
                    "ledger_path": LEDGER_SOURCE,
                    "record_count": 0,
                    "ledger_warning": f"shadow ledger missing required columns: {', '.join(missing)}",
                }
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return {
            "ledger_status": "MALFORMED",
            "ledger_path": LEDGER_SOURCE,
            "record_count": 0,
            "ledger_warning": f"shadow ledger could not be read: {type(exc).__name__}: {exc}",
        }

    if not rows:
        return {
            "ledger_status": "EMPTY",
            "ledger_path": LEDGER_SOURCE,
            "record_count": 0,
            "ledger_warning": "shadow ledger has no records",
        }
    return {
        "ledger_status": "AVAILABLE",
        "ledger_path": LEDGER_SOURCE,
        "record_count": len(rows),
        "ledger_warning": None,
    }


def inspect_input_data(repo_root: Path, finished_at: str) -> dict[str, Any]:
    market = _inspect_csv_group(repo_root / "data" / "raw", "*.csv", finished_at)
    investor = _inspect_csv_group(repo_root / "data" / "investor", "*.csv", finished_at)
    return {
        "market_data_max_date": market["max_date"],
        "investor_data_max_date": investor["max_date"],
        "input_data_freshness": _combined_input_freshness(
            str(market["freshness"]),
            str(investor["freshness"]),
        ),
        "input_data_freshness_policy": INPUT_FRESHNESS_POLICY,
        "input_data_stale_after_days": INPUT_FRESHNESS_STALE_AFTER_DAYS,
    }


def _inspect_csv_group(directory: Path, pattern: str, finished_at: str) -> dict[str, Any]:
    if not directory.exists():
        return {"max_date": None, "freshness": "MISSING"}

    paths = sorted(directory.glob(pattern))
    if not paths:
        return {"max_date": None, "freshness": "MISSING"}

    max_date: str | None = None
    try:
        for path in paths:
            file_max = _csv_max_date(path)
            if file_max and (max_date is None or file_max > max_date):
                max_date = file_max
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {"max_date": max_date, "freshness": "UNAVAILABLE", "warning": f"{type(exc).__name__}: {exc}"}

    if max_date is None:
        return {"max_date": None, "freshness": "MISSING"}
    return {"max_date": max_date, "freshness": _date_freshness(max_date, finished_at)}


def _csv_max_date(path: Path) -> str | None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "date" not in reader.fieldnames:
            return None
        values = [row.get("date") for row in reader if row.get("date")]
    dates = [_date_text(value) for value in values]
    dates = [value for value in dates if value]
    return max(dates) if dates else None


def _date_text(value: object) -> str | None:
    try:
        return datetime.fromisoformat(str(value).strip()).date().isoformat()
    except ValueError:
        return None


def _date_freshness(max_date: str, finished_at: str) -> str:
    try:
        age_days = (datetime.fromisoformat(finished_at).date() - datetime.fromisoformat(max_date).date()).days
    except ValueError:
        return "UNAVAILABLE"
    return "STALE" if age_days > INPUT_FRESHNESS_STALE_AFTER_DAYS else "CURRENT"


def _combined_input_freshness(market_status: str, investor_status: str) -> str:
    priority = ["UNAVAILABLE", "MISSING", "STALE", "CURRENT"]
    for status in priority:
        if status in {market_status, investor_status}:
            return status
    return "UNAVAILABLE"


def write_metadata_atomic(metadata_path: Path, metadata: dict[str, Any]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{metadata_path.name}.",
        suffix=".tmp",
        dir=str(metadata_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, metadata_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def run_dashboard_pipeline(
    *,
    repo_root: Path = ROOT,
    dry_run: bool = False,
    pipeline_func: Callable[..., Any] = shadow_daily_pipeline.run_pipeline,
    now_func: Callable[[], str] = utc_now_iso,
) -> DashboardRunResult:
    metadata_path = repo_root / METADATA_SOURCE
    ledger_path = repo_root / LEDGER_SOURCE
    started_at = now_func()
    pipeline_result: Any | None = None
    exception: BaseException | None = None
    traceback_text: str | None = None
    pipeline_status = "FAILED"
    error: str | None = None
    signal_base_date: str | None = None

    try:
        pipeline_result = pipeline_func(dry_run=dry_run)
        pipeline_status = "SUCCESS" if pipeline_result.ok else "FAILED"
        signal_base_date = _signal_base_date(pipeline_result)
        if not pipeline_result.ok:
            error = _pipeline_error_summary(pipeline_result)
    except Exception as exc:  # CLI reports traceback; dashboard metadata keeps only a short summary.
        exception = exc
        traceback_text = traceback.format_exc()
        error = f"{type(exc).__name__}: {exc}"

    finished_at = now_func()
    metadata = {
        "schema_version": "1.0",
        "mode": "SHADOW",
        "read_only": True,
        "pipeline_status": pipeline_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": _duration_seconds(started_at, finished_at),
        "signal_base_date": signal_base_date,
        "error": error,
        "source_commit": _source_commit(repo_root),
        "runner_version": RUNNER_VERSION,
    }
    metadata.update(inspect_input_data(repo_root, finished_at))
    ledger = inspect_ledger(ledger_path)
    ledger_warning = ledger.pop("ledger_warning")
    metadata.update(ledger)
    if ledger_warning and metadata["error"] is None:
        metadata["ledger_warning"] = ledger_warning

    write_metadata_atomic(metadata_path, metadata)
    return DashboardRunResult(
        metadata=metadata,
        metadata_path=metadata_path,
        pipeline_result=pipeline_result,
        exception=exception,
        traceback_text=traceback_text,
    )


def _signal_base_date(pipeline_result: Any) -> str | None:
    for phase in getattr(pipeline_result, "phases", []):
        value = getattr(phase, "stats", {}).get("signal_base_date")
        if value:
            return str(value)
    return None


def _pipeline_error_summary(pipeline_result: Any) -> str | None:
    for phase in getattr(pipeline_result, "phases", []):
        error = getattr(phase, "error", None)
        if error:
            title = getattr(phase, "title", "pipeline")
            return f"{title}: {error}"
    return "pipeline failed without an error summary"


def _duration_seconds(started_at: str, finished_at: str) -> float:
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return 0.0
    return max((finished - started).total_seconds(), 0.0)


def _source_commit(repo_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def render_dashboard_summary(result: DashboardRunResult) -> str:
    metadata = result.metadata
    return "\n".join(
        [
            "=" * 56,
            "BAIKAL STOCK SIGNAL",
            "SHADOW DASHBOARD RUNNER",
            "=" * 56,
            f"Pipeline Status: {metadata['pipeline_status']}",
            f"Started At: {metadata['started_at']}",
            f"Finished At: {metadata['finished_at']}",
            f"Duration Seconds: {metadata['duration_seconds']}",
            f"Signal Base Date: {metadata.get('signal_base_date') or 'unavailable'}",
            f"Market Data Date: {metadata.get('market_data_max_date') or 'unavailable'}",
            f"Investor Data Date: {metadata.get('investor_data_max_date') or 'unavailable'}",
            f"Input Freshness: {metadata['input_data_freshness']}",
            f"Ledger Status: {metadata['ledger_status']}",
            f"Operational Record Count: {metadata['record_count']}",
            f"Metadata Artifact: {result.metadata_path}",
            f"Error: {metadata.get('error') or 'none'}",
            "=" * 56,
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Shadow Daily Pipeline and write dashboard metadata.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pass dry-run through to the existing Shadow Daily Pipeline.",
    )
    args = parser.parse_args(argv)

    print("Starting Shadow Dashboard Runner...")
    result = run_dashboard_pipeline(dry_run=args.dry_run)
    if result.pipeline_result is not None:
        print(shadow_daily_pipeline.render_summary(result.pipeline_result))
    print(render_dashboard_summary(result))
    if result.traceback_text:
        print(result.traceback_text, file=sys.stderr)
    return 0 if result.metadata["pipeline_status"] == "SUCCESS" else 1
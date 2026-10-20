"""STEP 9-D: isolated DART weekday scheduler; preview-only unless --execute."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.dart_disclosure_accumulate import (
    ROOT, STORE_RELATIVE, AccumulationError, run_accumulation,
)
from scripts.dart_disclosure_collect import run_collection
from scripts.dart_disclosure_query import QueryError, query_disclosures
from src.dart_disclosure_classification import ClassificationError
from src.data_provider.dart_disclosure_provider import DartDisclosureError
from src.expanded_shadow_universe import ExpandedUniverseValidationError

KST = timezone(timedelta(hours=9), name="Asia/Seoul")
SCHEDULE_TIME = time(20, 20)
OUTPUT_RELATIVE = Path("output") / "dart_disclosure_scheduler"
SAFE_ERRORS = (AccumulationError, ClassificationError, DartDisclosureError, QueryError)
EXPECTED_ERRORS = (
    OSError, ValueError, sqlite3.Error, ExpandedUniverseValidationError,
    DartDisclosureError,
)


def preflight(repo_root: Path = ROOT) -> dict:
    paths = [
        repo_root / "output" / name for name in (
            "dart_disclosures", "dart_disclosure_classification",
            "dart_disclosure_store", "dart_disclosure_scheduler",
        )
    ]
    checks = []
    for path in paths:
        parent = path
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        checks.append({
            "path": str(path), "existing_parent": str(parent),
            "access_hint": parent.is_dir() and os.access(parent, os.R_OK | os.W_OK | os.X_OK),
        })
    store = repo_root / STORE_RELATIVE
    key_present = bool(os.environ.get("DART_API_KEY", "").strip())
    return {
        "verdict": "PREFLIGHT_ONLY",
        "current_account": getpass.getuser(), "dart_api_key_present": key_present,
        "interpreter_exists": (repo_root / ".venv" / "Scripts" / "python.exe").is_file(),
        "folders": checks,
        "store_read_write_access_hint": os.access(store, os.R_OK | os.W_OK) if store.exists() else None,
        "key_validity": "NOT_CHECKED_NO_API_CALL",
        "scheduled_account": "NOT_VERIFIED_NO_TASK_REGISTERED",
        "permissions": "READ_ONLY_HINTS_NOT_A_WINDOWS_ACL_OR_WRITE_PROBE",
    }


def _save(path: Path, report: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_scheduler(
    repo_root: Path = ROOT, *, now: datetime | None = None, execute: bool = False,
) -> dict:
    current = now if now is not None else datetime.now(KST)
    if current.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    current = current.astimezone(KST)
    day = current.date()
    report = {
        "verdict": "SKIPPED", "scheduler_date": day.isoformat(),
        "checked_at": current.isoformat(), "schedule": "MON-FRI 20:20 Asia/Seoul",
        "executed": False, "accumulation_committed": False,
        "new_count": None, "duplicate_count": None, "total_count": None,
        "actual_collected_at": None, "last_success_at": None,
    }
    if current.weekday() >= 5:
        return {**report, "reason": "WEEKEND"}
    if current.time() < SCHEDULE_TIME:
        return {**report, "reason": "NOT_DUE"}
    directory = repo_root / OUTPUT_RELATIVE
    if not execute:
        return {**report, "verdict": "DRY_RUN", "reason": "EXECUTION_NOT_APPROVED"}
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "execution.lock.sqlite3", timeout=0)) as lock:
        try:
            # Dedicated SQLite transaction is an OS-released cross-process lease.
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
            return {**report, "reason": "ALREADY_RUNNING"}
        previous = []
        for path in directory.glob("*.json"):
            if path.name == "coverage.json":
                continue
            saved = json.loads(path.read_bytes())
            if saved.get("verdict") == "PASS":
                previous.append(saved)
        if previous:
            latest = max(previous, key=lambda item: item["last_success_at"])
            report["last_success_at"] = latest["last_success_at"]
        if any(item["scheduler_date"] == day.isoformat() for item in previous):
            return {**report, "reason": "ALREADY_SUCCEEDED"}
        store = repo_root / STORE_RELATIVE
        path = directory / f"{day.isoformat()}_{uuid.uuid4().hex}.json"
        report.update(
            verdict="RUNNING", executed=True, stage="PRECHECK",
            run_report=str(path.resolve()),
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        _save(path, report)
        try:
            start = day - timedelta(days=2)
            if store.exists():
                stored = query_disclosures(store, high=True, limit=1)
                report["last_success_at"] = stored["last_success_at"]
                report["total_count"] = stored["total_count"]
                if stored["collection_time_status"] != "VERIFIED_STORED_SOURCE":
                    raise QueryError("COLLECTION_TIME_NOT_VERIFIED")
                stored_end = date.fromisoformat(stored["source_window"]["end_date"])
                if stored_end > day:
                    raise QueryError("COVERAGE_DATE_IN_FUTURE")
                start = min(start, stored_end)
            checkpoint = directory / "coverage.json"
            if checkpoint.exists():
                coverage = json.loads(checkpoint.read_bytes())
                if (not isinstance(coverage, dict)
                        or coverage.get("schema_version") != 1
                        or not isinstance(coverage.get("last_success_end_date"), str)
                        or (not store.exists() and coverage.get("baseline_only") is not True)):
                    raise QueryError("INVALID_COVERAGE_STATE")
                covered_end = date.fromisoformat(coverage["last_success_end_date"])
                if covered_end > day:
                    raise QueryError("COVERAGE_DATE_IN_FUTURE")
                start = min(start, covered_end)
            if start > day:
                raise QueryError("COVERAGE_DATE_IN_FUTURE")
            if not checkpoint.exists():
                _save(checkpoint, {
                    "schema_version": 1, "last_success_end_date": start.isoformat(),
                    "baseline_only": True,
                })
            report.update(
                recovery_start_date=start.isoformat(), target_end_date=day.isoformat(),
                segments=[], new_count=0, duplicate_count=0,
            )
            while start <= day:
                end = min(start + timedelta(days=2), day)
                segment = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                           "verdict": "RUNNING"}
                report["segments"].append(segment)
                report["stage"] = "COLLECTION"
                _save(path, report)
                collection, source = run_collection(repo_root, start, end, scope="all")
                report["collection_report"] = str(source.resolve())
                report["actual_collected_at"] = collection.get("collected_at")
                if collection["verdict"] != "PASS":
                    segment["verdict"] = collection["verdict"]
                    report.update(
                        verdict=collection["verdict"],
                        error_code=collection["blocked_reason"] or "COLLECTION_NOT_PASS",
                    )
                    break
                if (collection["start_date"] != start.isoformat()
                        or collection["end_date"] != end.isoformat()):
                    raise QueryError("COLLECTION_WINDOW_MISMATCH")
                report["stage"] = "CLASSIFICATION_AND_ACCUMULATION"
                summary = run_accumulation(source, repo_root)
                report.update(
                    new_count=report["new_count"] + summary["new_count"],
                    duplicate_count=report["duplicate_count"] + summary["duplicate_count"],
                    total_count=summary["total_count"],
                    last_success_at=summary["last_success_at"], accumulation_committed=True,
                )
                report["stage"] = "READ_ONLY_VERIFICATION"
                verified = query_disclosures(store, high=True, limit=1)
                if (verified["total_count"] != summary["total_count"]
                        or verified["last_success_at"] != summary["last_success_at"]):
                    raise QueryError("POST_UPDATE_STORE_MISMATCH")
                if verified["collection_time_status"] != "VERIFIED_STORED_SOURCE":
                    raise QueryError("COLLECTION_TIME_NOT_VERIFIED")
                _save(checkpoint, {
                    "schema_version": 1, "last_success_end_date": end.isoformat(),
                    "baseline_only": False, "last_success_at": summary["last_success_at"],
                })
                segment.update(
                    verdict="PASS", new_count=summary["new_count"],
                    duplicate_count=summary["duplicate_count"],
                )
                start = end + timedelta(days=1)
            else:
                report.update(
                    verdict="PASS", stage="COMPLETE",
                    actual_collected_at=verified["last_collection_success_at"],
                )
        except EXPECTED_ERRORS as exc:
            if report.get("segments") and report["segments"][-1]["verdict"] == "RUNNING":
                report["segments"][-1]["verdict"] = "FAIL"
            report.update(
                verdict="FAIL",
                error_code=str(exc) if isinstance(exc, SAFE_ERRORS) else type(exc).__name__,
            )
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        _save(path, report)
        return report


def task_xml(repo_root: Path = ROOT) -> str:
    """Return a registration proposal only; never call Windows Task Scheduler."""
    namespace = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}Task", version="1.2")

    def child(parent: ET.Element, name: str, text: str | None = None) -> ET.Element:
        item = ET.SubElement(parent, f"{{{namespace}}}{name}")
        item.text = text
        return item

    trigger = child(child(root, "Triggers"), "CalendarTrigger")
    child(trigger, "StartBoundary", f"{datetime.now(KST).date()}T20:20:00+09:00")
    child(trigger, "Enabled", "true")
    week = child(trigger, "ScheduleByWeek")
    child(week, "WeeksInterval", "1")
    days = child(week, "DaysOfWeek")
    for name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        child(days, name)
    settings = child(root, "Settings")
    child(settings, "MultipleInstancesPolicy", "IgnoreNew")
    child(settings, "StartWhenAvailable", "true")
    child(settings, "ExecutionTimeLimit", "PT2H")
    action = child(child(root, "Actions"), "Exec")
    child(action, "Command", "powershell.exe")
    child(action, "Arguments",
          f'-NoLogo -NoProfile -NonInteractive -File "{repo_root / "scripts" / "dart_secure_runner.ps1"}" -Mode Execute')
    child(action, "WorkingDirectory", str(repo_root))
    return ET.tostring(root, encoding="unicode")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Live execution; approval required")
    mode.add_argument("--task-xml", action="store_true", help="Print unregistered task proposal")
    mode.add_argument("--preflight", action="store_true", help="Read-only account/key/access hints")
    args = parser.parse_args(argv)
    if args.task_xml:
        print(task_xml())
        return 0
    if args.preflight:
        print(json.dumps(preflight(ROOT), ensure_ascii=False, indent=2))
        return 0
    try:
        report = run_scheduler(ROOT, execute=args.execute)
    except EXPECTED_ERRORS as exc:
        print(json.dumps({
            "verdict": "FAIL",
            "error_code": str(exc) if isinstance(exc, SAFE_ERRORS) else type(exc).__name__,
        }))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"FAIL": 1, "BLOCKED": 2}.get(report["verdict"], 0)


if __name__ == "__main__":
    raise SystemExit(main())

"""Independent gated card runs. Preview by default; never schedule collection."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.dart_disclosure_query import QueryError, query_disclosures
from src import dart_analysis_cards as cards

KST = timezone(timedelta(hours=9))
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=30)
RUN_TIME_LIMIT_SECONDS = 600
SAFE_ERRORS = (cards.CardError, QueryError)
EXPECTED_ERRORS = (OSError, ValueError, sqlite3.Error, KeyError, TypeError)


def _stamp(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise cards.CardError("TIMEZONE_REQUIRED")
    return stamp


def _save(path: Path, report: dict) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def collection_gate(root: Path, now: datetime, collection_date: date | None = None) -> dict:
    """Verify the latest attempt for the requested date against the stored source."""
    today = now.astimezone(KST).date()
    if collection_date is not None and collection_date > today:
        raise cards.CardError("COLLECTION_DATE_IN_FUTURE")
    day = (collection_date or today).isoformat()
    directory = root / "output" / "dart_disclosure_scheduler"
    reports = []
    for path in directory.glob("*.json"):
        if path.name == "coverage.json":
            continue
        saved = json.loads(path.read_bytes())
        if saved["scheduler_date"] == day:
            reports.append((path, saved))
    if not reports:
        raise cards.CardError("TODAY_COLLECTION_NOT_FOUND")
    path, report = max(reports, key=lambda item: _stamp(
        item[1].get("started_at", item[1]["checked_at"]),
    ))
    if (report["verdict"] != "PASS" or report["stage"] != "COMPLETE"
            or report["accumulation_committed"] is not True or report["executed"] is not True):
        raise cards.CardError("TODAY_COLLECTION_NOT_COMPLETE")
    started = _stamp(report["started_at"])
    completed = _stamp(report["completed_at"])
    collected = _stamp(report["actual_collected_at"])
    if (started > completed or completed > now or collected > completed
            or started.astimezone(KST).date().isoformat() != day
            or completed.astimezone(KST).date().isoformat() != day
            or collected.astimezone(KST).date().isoformat() != day):
        raise cards.CardError("COLLECTION_TIME_MISMATCH")
    checkpoint = json.loads((directory / "coverage.json").read_bytes())
    source = root / cards.SOURCE_STORE.relative_to(cards.ROOT)
    stored = query_disclosures(source, high=True, limit=1)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        summary = json.loads(conn.execute(
            "SELECT summary_json FROM last_success WHERE id=1",
        ).fetchone()[0])
    if (checkpoint["schema_version"] != 1 or checkpoint["baseline_only"] is not False
            or checkpoint["last_success_end_date"] != day
            or checkpoint["last_success_at"] != report["last_success_at"]
            or stored["last_success_at"] != report["last_success_at"]
            or summary["last_success_at"] != report["last_success_at"]
            or stored["total_count"] != report["total_count"]
            or stored["collection_time_status"] != "VERIFIED_STORED_SOURCE"
            or stored["last_collection_success_at"] != report["actual_collected_at"]
            or stored["source_window"]["end_date"] != day
            or Path(summary["source_path"]).resolve() != Path(report["collection_report"]).resolve()):
        raise cards.CardError("COLLECTION_CHECKPOINT_DB_MISMATCH")
    return {
        "report": str(path.resolve()), "last_success_at": report["last_success_at"],
        "total_count": report["total_count"], "collection_date": day,
    }


def select_pending(
    source: Path, store: Path, directory: Path, now: datetime,
    receipt_no: str | None = None,
) -> dict:
    if receipt_no is not None and re.fullmatch(r"[0-9]{14}", receipt_no) is None:
        raise cards.CardError("INVALID_RECEIPT")
    candidates = cards.read_candidates(source)
    if receipt_no is not None:
        candidates = [row for row in candidates if row["rcept_no"] == receipt_no]
        if not candidates:
            raise cards.CardError("RECEIPT_NOT_SUPPORTED_CANDIDATE")
    done = set()
    unsupported = set()
    legacy_failures: dict[str, list[datetime]] = {}
    if store.exists():
        with closing(sqlite3.connect(
            store.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
        )) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise cards.CardError("UNSUPPORTED_CARD_STORE")
            for receipt, payload in conn.execute(
                "SELECT receipt,payload_json FROM cards",
            ):
                status = json.loads(payload)["status"]
                if status in ("COMPLETE", "PARTIAL"):
                    done.add(receipt)
                elif status == "UNSUPPORTED":
                    unsupported.add(receipt)
            for receipt, status, stamp, version in conn.execute(
                "SELECT receipt,status,attempted_at,extractor_version FROM attempts",
            ):
                if status == "UNSUPPORTED":
                    unsupported.add(receipt)
                elif status == "FAILED" and version in (cards.EXTRACTOR_VERSION, None):
                    legacy_failures.setdefault(receipt, []).append(_stamp(stamp))
    trials: dict[str, list[datetime]] = {}
    reserved_counts: dict[str, int] = {}
    for path in directory.glob("*.json"):
        report = json.loads(path.read_bytes())
        if report["extractor_version"] != cards.EXTRACTOR_VERSION:
            continue
        for trial in report["attempts"]:
            trials.setdefault(trial["receipt"], []).append(_stamp(trial["started_at"]))
            reserved_counts[trial["receipt"]] = max(
                reserved_counts.get(trial["receipt"], 0), trial.get("attempt_number", 1),
            )
    selected = []
    skipped = []
    attempt_counts = {}
    for row in candidates:
        receipt = row["rcept_no"]
        history = trials.get(receipt, [])
        failures = legacy_failures.get(receipt, [])
        count = max(len(history), len(failures), reserved_counts.get(receipt, 0))
        attempt_counts[receipt] = count
        stamps = history + failures
        reason = (
            "ALREADY_PROCESSED" if receipt in done else
            "UNSUPPORTED_PREVIOUS_RESULT" if receipt in unsupported else
            "RETRY_EXHAUSTED" if count >= MAX_ATTEMPTS else
            "RETRY_COOLDOWN" if stamps and now - max(stamps) < RETRY_DELAY else None
        )
        if reason:
            skipped.append({"receipt": receipt, "reason": reason})
        else:
            selected.append(receipt)
    return {
        "candidate_count": len(candidates), "selected": selected, "skipped": skipped,
        "attempt_counts": attempt_counts,
    }


def run_cards(
    root: Path = cards.ROOT, *, now: datetime | None = None, execute: bool = False,
    max_items: int | None = None, allow_unlimited: bool = False,
    receipt_no: str | None = None, collection_date: date | None = None,
) -> tuple[dict, Path]:
    if receipt_no is not None and re.fullmatch(r"[0-9]{14}", receipt_no) is None:
        raise cards.CardError("INVALID_RECEIPT")
    if receipt_no is not None and allow_unlimited:
        raise cards.CardError("CONFLICTING_RECEIPT_UNLIMITED_OPTIONS")
    if max_items is not None and (type(max_items) is not int or max_items < 1):
        raise cards.CardError("INVALID_MAX_ITEMS")
    if max_items is not None and allow_unlimited:
        raise cards.CardError("CONFLICTING_LIMIT_OPTIONS")
    if execute and max_items is None and not allow_unlimited and receipt_no is None:
        raise cards.CardError("EXPLICIT_UNLIMITED_APPROVAL_REQUIRED")
    current = now if now is not None else datetime.now(KST)
    if current.utcoffset() is None:
        raise cards.CardError("TIMEZONE_REQUIRED")
    current = current.astimezone(KST)
    source = root / cards.SOURCE_STORE.relative_to(cards.ROOT)
    store = root / cards.CARD_STORE.relative_to(cards.ROOT)
    directory = store.parent / "runs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (current.strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex + ".json")
    report = {
        "schema_version": 1, "verdict": "RUNNING", "exit_code": 1,
        "started_at": current.isoformat(), "execute": execute,
        "extractor_version": cards.EXTRACTOR_VERSION, "max_attempts": MAX_ATTEMPTS,
        "retry_delay_minutes": int(RETRY_DELAY.total_seconds() / 60),
        "candidate_count": 0, "selected_count": 0, "success_count": 0, "partial_count": 0,
        "unsupported_count": 0, "failed_count": 0, "deferred_count": 0,
        "created_count": 0, "duplicate_count": 0, "attempts": [], "skipped": [],
        "selected": [], "reason": None,
        "max_items": max_items, "allow_unlimited": allow_unlimited,
        "receipt_no": receipt_no,
        "requested_collection_date": collection_date.isoformat() if collection_date else None,
    }
    with closing(sqlite3.connect(directory / "execution.lock.sqlite3", timeout=0)) as lock:
        try:
            lock.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
            report.update(verdict="DEFERRED", exit_code=2, reason="ALREADY_RUNNING")
            report["completed_at"] = datetime.now(KST).isoformat()
            _save(path, report)
            return report, path
        try:
            _save(path, report)
            report["collection_gate"] = (
                collection_gate(root, current, collection_date)
                if collection_date else collection_gate(root, current)
            )
            selection = (
                select_pending(source, store, directory, current, receipt_no=receipt_no)
                if receipt_no is not None else select_pending(source, store, directory, current)
            )
            eligible = sorted(selection["selected"], reverse=True)
            selection["eligible_count"] = len(eligible)
            selection["selected"] = eligible[:max_items] if max_items is not None else eligible
            selection["limit_excluded_count"] = len(eligible) - len(selection["selected"])
            report.update(selection)
            report["selected_count"] = len(selection["selected"])
            report["deferred_count"] = sum(
                row["reason"] in ("RETRY_COOLDOWN", "RETRY_EXHAUSTED")
                for row in selection["skipped"]
            )
            report["excluded_processed_count"] = sum(
                row["reason"] == "ALREADY_PROCESSED" for row in selection["skipped"]
            )
            report["excluded_unsupported_count"] = sum(
                row["reason"] == "UNSUPPORTED_PREVIOUS_RESULT" for row in selection["skipped"]
            )
            report.update(verdict="RUNNING" if execute else "PREVIEW", exit_code=0)
            _save(path, report)
            if execute:
                deadline = time.monotonic() + RUN_TIME_LIMIT_SECONDS
                for index, receipt in enumerate(selection["selected"]):
                    if time.monotonic() >= deadline:
                        report["deferred_count"] += len(selection["selected"]) - index
                        report["reason"] = "RUN_TIME_LIMIT_EXCEEDED"
                        break
                    try:
                        gate_now = current if now is not None else datetime.now(KST)
                        gate = (
                            collection_gate(root, gate_now, collection_date)
                            if collection_date else collection_gate(root, gate_now)
                        )
                        if gate != report["collection_gate"]:
                            raise cards.CardError("COLLECTION_CHANGED_DURING_RUN")
                    except EXPECTED_ERRORS:
                        report["deferred_count"] += len(selection["selected"]) - index
                        raise
                    trial = {
                        "receipt": receipt, "started_at": (
                            current if now is not None else datetime.now(KST)
                        ).isoformat(), "status": "RUNNING",
                        "attempt_number": selection["attempt_counts"][receipt] + 1,
                    }
                    report["attempts"].append(trial)
                    # Reserve the attempt durably before HTTP; interrupted trials consume the budget.
                    _save(path, report)
                    try:
                        result = cards.generate_cards(source, store, [receipt])
                        item = result["results"][0]
                        trial.update(item)
                        report["created_count"] += result["created"]
                        report["duplicate_count"] += result["duplicates"]
                        counter = {
                            "COMPLETE": "success_count", "PARTIAL": "partial_count",
                            "UNSUPPORTED": "unsupported_count", "FAILED": "failed_count",
                        }[item["status"]]
                        report[counter] += 1
                    except Exception as exc:
                        # The generator returns expected document failures as data.
                        # An escaped exception is a batch failure, never permission to continue.
                        trial.update(
                            status="FAILED", error_code=str(exc) if isinstance(exc, SAFE_ERRORS)
                            else type(exc).__name__,
                        )
                        report["failed_count"] += 1
                        report["deferred_count"] += len(selection["selected"]) - index - 1
                        report.update(
                            verdict="FAIL", exit_code=1, reason=trial["error_code"],
                            aborted=True,
                        )
                        break
                    _save(path, report)
                if not report.get("aborted"):
                    report.update(
                        verdict="FAIL" if report["failed_count"] else (
                            "DEFERRED" if report["reason"] or (
                                report["deferred_count"] and not report["attempts"]
                            ) else "PASS_WITH_PENDING" if report["deferred_count"] else "PASS"
                        ),
                        exit_code=1 if report["failed_count"] else (
                            2 if report["reason"] or (
                                report["deferred_count"] and not report["attempts"]
                            ) else 0
                        ),
                    )
        except EXPECTED_ERRORS as exc:
            report.update(
                verdict="DEFERRED" if "collection_gate" not in report or not report["attempts"] else "FAIL",
                exit_code=2 if not report["attempts"] else 1,
                reason=str(exc) if isinstance(exc, SAFE_ERRORS) else type(exc).__name__,
            )
        except Exception as exc:
            report.update(
                verdict="FAIL", exit_code=1, reason=type(exc).__name__, aborted=True,
            )
        finally:
            report["completed_at"] = datetime.now(KST).isoformat()
            _save(path, report)
    return report, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--receipt-no")
    parser.add_argument("--collection-date", type=date.fromisoformat)
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument("--max-items", type=_positive_int)
    limits.add_argument("--allow-unlimited", action="store_true")
    args = parser.parse_args(argv)
    if args.receipt_no is not None and re.fullmatch(r"[0-9]{14}", args.receipt_no) is None:
        parser.error("--receipt-no must be exactly 14 digits")
    if args.receipt_no is not None and args.allow_unlimited:
        parser.error("--receipt-no cannot be combined with --allow-unlimited")
    if args.execute and args.max_items is None and not args.allow_unlimited and args.receipt_no is None:
        parser.error("--execute requires --receipt-no, --max-items N or --allow-unlimited")
    try:
        report, path = run_cards(
            execute=args.execute, max_items=args.max_items, allow_unlimited=args.allow_unlimited,
            receipt_no=args.receipt_no, collection_date=args.collection_date,
        )
    except EXPECTED_ERRORS as exc:
        print(json.dumps({
            "verdict": "FAIL", "exit_code": 1,
            "reason": str(exc) if isinstance(exc, SAFE_ERRORS) else type(exc).__name__,
            "report_saved": False,
        }))
        return 1
    print(json.dumps({**report, "run_report": str(path)}, ensure_ascii=False, indent=2))
    return report["exit_code"]


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("N must be an integer >= 1") from None
    if number < 1:
        raise argparse.ArgumentTypeError("N must be an integer >= 1")
    return number


if __name__ == "__main__":
    raise SystemExit(main())

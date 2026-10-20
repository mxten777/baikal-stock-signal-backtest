"""STEP 9-B: transactional receipt-keyed storage, independent of stock pipelines."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.dart_disclosure_classify import run_classification
from scripts.dart_disclosure_collect import run_collection
from src.dart_disclosure_classification import ClassificationError
from src.data_provider.dart_disclosure_provider import DartDisclosureError
from src.expanded_shadow_universe import ExpandedUniverseValidationError

ROOT = Path(__file__).resolve().parents[1]
STORE_RELATIVE = Path("output") / "dart_disclosure_store" / "disclosures.sqlite3"
IDENTITY_FIELDS = (
    "ticker", "corp_code", "report_nm", "rcept_dt", "original_url",
    "is_correction", "correction_type",
)


class AccumulationError(ValueError):
    """Locally defined error codes only."""


def run_accumulation(source_path: Path, repo_root: Path = ROOT) -> dict:
    report, _ = run_classification(source_path, repo_root)
    if report["verdict"] != "PASS":
        raise AccumulationError("CLASSIFICATION_NOT_PASS")
    store_path = repo_root / STORE_RELATIVE
    store_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(store_path, timeout=5)) as connection:
        with connection:
            # Serialize overlapping runs before reading or inserting any receipts.
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise AccumulationError("UNSUPPORTED_STORE_VERSION")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS disclosures ("
                "rcept_no TEXT PRIMARY KEY NOT NULL, payload_json TEXT NOT NULL, "
                "source_sha256 TEXT NOT NULL, rule_version TEXT NOT NULL, "
                "stored_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS last_success ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "summary_json TEXT NOT NULL)"
            )
            new_count = 0
            stored_at = datetime.now(timezone.utc).isoformat()
            for row in report["disclosures"]:
                existing = connection.execute(
                    "SELECT payload_json FROM disclosures WHERE rcept_no = ?",
                    (row["rcept_no"],),
                ).fetchone()
                if existing is not None:
                    previous = json.loads(existing[0])
                    if any(previous[field] != row[field] for field in IDENTITY_FIELDS):
                        raise AccumulationError("CONFLICTING_STORED_RECEIPT")
                    continue
                connection.execute(
                    "INSERT INTO disclosures VALUES (?, ?, ?, ?, ?)",
                    (row["rcept_no"], json.dumps(row, ensure_ascii=False, allow_nan=False),
                     report["source_sha256"], report["rule_version"], stored_at),
                )
                new_count += 1
            total_count = connection.execute(
                "SELECT COUNT(*) FROM disclosures"
            ).fetchone()[0]
            summary = {
                "verdict": "PASS", "input_count": report["input_count"],
                "new_count": new_count,
                "duplicate_count": report["input_count"] - new_count,
                "total_count": total_count,
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "source_path": report["source_path"],
                "source_sha256": report["source_sha256"],
                "store_path": str(store_path.resolve()),
            }
            connection.execute(
                "INSERT INTO last_success VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET summary_json = excluded.summary_json",
                (json.dumps(summary, ensure_ascii=False, allow_nan=False),),
            )
            connection.execute("PRAGMA user_version = 1")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Existing all-universe collection JSON")
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--lookback-days", type=int, choices=range(1, 4))
    args = parser.parse_args(argv)
    if args.input is not None and (args.end_date is not None or args.lookback_days is not None):
        parser.error("--input cannot be combined with collection date options")
    today = datetime.now(timezone(timedelta(hours=9))).date()
    end = args.end_date if args.end_date is not None else today
    if end > today:
        parser.error("--end-date must not be in the future")
    try:
        source_path = args.input
        if source_path is None:
            start = end - timedelta(days=(args.lookback_days or 3) - 1)
            collection, source_path = run_collection(ROOT, start, end, scope="all")
            if collection["verdict"] != "PASS":
                print(json.dumps({
                    "verdict": collection["verdict"], "stage": "COLLECTION",
                    "error_code": collection["blocked_reason"] or "COLLECTION_NOT_PASS",
                    "collection_report": str(source_path),
                }))
                return 2 if collection["verdict"] == "BLOCKED" else 1
        summary = run_accumulation(source_path, ROOT)
    except (OSError, ValueError, UnicodeError, sqlite3.Error,
            DartDisclosureError, ExpandedUniverseValidationError) as exc:
        code = str(exc) if isinstance(
            exc, (AccumulationError, ClassificationError, DartDisclosureError)
        ) else type(exc).__name__
        print(json.dumps({"verdict": "FAIL", "error_code": code}))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

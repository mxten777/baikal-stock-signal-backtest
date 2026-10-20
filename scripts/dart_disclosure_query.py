"""STEP 9-C: read-only queries of stored DART evidence, without collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from scripts.dart_disclosure_accumulate import ROOT, STORE_RELATIVE
from src.dart_disclosure_classification import IMPORTANCES


class QueryError(ValueError):
    """Safe local query error codes."""


def collection_time(summary: dict) -> dict:
    # STEP 9-B stores processing time, not collection time; verify source evidence.
    path = Path(summary["source_path"])
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {
            "last_collection_success_at": None,
            "collection_time_status": "SOURCE_FILE_NOT_FOUND",
        }
    if hashlib.sha256(raw).hexdigest() != summary["source_sha256"]:
        raise QueryError("SOURCE_HASH_MISMATCH")
    source = json.loads(raw)
    if (source.get("verdict") != "PASS"
            or source.get("collection_complete") is not True
            or source.get("scope") != "all"):
        raise QueryError("SOURCE_NOT_COMPLETE")
    stamp = source.get("collected_at")
    if stamp is None:
        return {
            "last_collection_success_at": None,
            "collection_time_status": "NOT_RECORDED_IN_SOURCE",
        }
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp).utcoffset() is None:
        raise QueryError("INVALID_COLLECTION_TIME")
    return {
        "last_collection_success_at": stamp,
        "collection_time_status": "VERIFIED_STORED_SOURCE",
        "source_window": {key: source[key] for key in ("start_date", "end_date")},
        "includes_end_date_in_progress": source.get("includes_end_date_in_progress"),
    }


def query_disclosures(
    store_path: Path, *, ticker: str | None = None, high: bool = False, limit: int = 20,
) -> dict:
    if (ticker is None and not high) or (ticker is not None and high):
        raise QueryError("SELECT_EXACTLY_ONE_OF_TICKER_OR_HIGH")
    if ticker is not None and re.fullmatch(r"[0-9A-Z]{6}", ticker) is None:
        raise QueryError("INVALID_TICKER")
    if type(limit) is not int or limit < 1:
        raise QueryError("INVALID_LIMIT")
    with closing(sqlite3.connect(
        store_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5,
    )) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise QueryError("UNSUPPORTED_STORE_VERSION")
        saved = connection.execute(
            "SELECT summary_json FROM last_success WHERE id = 1"
        ).fetchone()
        if saved is None:
            raise QueryError("LAST_SUCCESS_NOT_FOUND")
        summary = json.loads(saved[0])
        if summary["verdict"] != "PASS":
            raise QueryError("LAST_SUCCESS_NOT_PASS")
        total = connection.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0]
        if total != summary["total_count"]:
            raise QueryError("STORE_COUNT_MISMATCH")
        field, value = ("$.classification.importance", "HIGH") if high else ("$.ticker", ticker)
        predicate = "json_extract(payload_json, ?) = ?"
        matches = connection.execute(
            f"SELECT COUNT(*) FROM disclosures WHERE {predicate}", (field, value),
        ).fetchone()[0]
        rows = connection.execute(
            f"SELECT rcept_no, payload_json FROM disclosures WHERE {predicate} "
            "ORDER BY json_extract(payload_json, '$.rcept_dt') DESC, rcept_no DESC LIMIT ?",
            (field, value, limit),
        ).fetchall()
        disclosures = []
        for receipt, payload in rows:
            row = json.loads(payload)
            importance = row["classification"]["importance"]
            if (row["rcept_no"] != receipt or importance not in IMPORTANCES
                    or type(row["is_correction"]) is not bool
                    or row["original_url"]
                    != f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"):
                raise QueryError("INVALID_STORED_DISCLOSURE")
            disclosures.append({
                key: row[key] for key in (
                    "rcept_no", "ticker", "name", "rcept_dt", "report_nm",
                    "is_correction", "original_url",
                )
            } | {"importance": importance})
    return {
        "verdict": "PASS", "mode": "HIGH" if high else "TICKER", "ticker": ticker,
        "store_path": str(store_path.resolve()), "limit": limit,
        "query_count": len(disclosures), "matching_count": matches, "total_count": total,
        "last_success_at": summary["last_success_at"],
        "last_success_kind": "ACCUMULATION_PROCESSING_NOT_COLLECTION",
        **collection_time(summary),
        "freshness_note": (
            "Stored evidence only; no collection performed. Times describe the last "
            "successful input, not guaranteed complete coverage or current freshness. "
            "Collection time is never inferred from processing time."
        ),
        "disclosures": disclosures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ticker")
    mode.add_argument("--high", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--store", type=Path, default=ROOT / STORE_RELATIVE)
    args = parser.parse_args(argv)
    try:
        report = query_disclosures(
            args.store, ticker=args.ticker, high=args.high, limit=args.limit,
        )
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, QueryError) else type(exc).__name__
        print(json.dumps({"verdict": "FAIL", "error_code": code}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

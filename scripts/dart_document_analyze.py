"""STEP 6-D-1: official public DART access for exactly five selected receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from src.dart_document_analysis import (
    DocumentError,
    analyze_document,
    now_iso,
    select_documents,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_RELATIVE = Path("output") / "dart_document_analysis"


def run_analysis(source_path: Path, repo_root: Path = ROOT) -> tuple[dict, Path]:
    raw = source_path.read_bytes()
    source = json.loads(raw)
    selected = select_documents(source)
    results = [analyze_document(row, repo_root) for row in selected]
    access_success = sum(row["access_status"] == "SUCCESS" for row in results)
    verified_fields = sum(row.get("verified_field_count", 0) for row in results)
    market_errors = sum(
        row["market_linkage"][dataset]["status"] == "ERROR"
        for row in results for dataset in ("market", "investor")
    )
    verdict = "BLOCKED" if access_success < 5 else (
        "PASS" if all(row.get("verified_field_count", 0) > 0 for row in results)
        and any(row.get("correction_changes") for row in results if row["is_correction"])
        and market_errors == 0
        else "FAIL"
    )
    report = {
        "schema_version": 1, "verdict": verdict, "checked_at": now_iso(),
        "source_path": str(source_path.resolve()),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "selection_count": len(selected), "access_success": access_success,
        "access_failure": len(selected) - access_success,
        "requests_attempted": sum(row["requests_attempted"] for row in results),
        "verified_field_count": verified_fields,
        "market_linkage_error_count": market_errors,
        "unconfirmed_field_count": sum(
            field["status"] == "UNCONFIRMED" for row in results for field in row["fields"]
        ),
        "contract_condition_counts": {
            status: sum(
                condition["status"] == status
                for row in results for condition in row["contract_conditions"]
            )
            for status in ("VERIFIED", "UNCONFIRMED")
        },
        "limitations": [
            "Not investment advice, a price prediction or an economic-impact conclusion.",
            "Only five selected receipts; no attachments or other original receipts requested.",
            "Evidence is official HTML table content with XPath, URL, SHA-256 and UTC access time.",
            "Named fields and explicit contract conditions only; original_reviewed=false is retained.",
            "Correction before/after is from the correction table, not an independent check of the original.",
            "Missing same-day market/flow data stays unavailable; no forward/nearest-day fill.",
        ],
        "documents": results,
    }
    output_dir = repo_root / OUTPUT_RELATIVE
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / (now_iso().replace(":", "").replace("-", "") + "_" + uuid.uuid4().hex + ".json")
    descriptor, temporary_name = tempfile.mkstemp(dir=output_dir, suffix=".tmp")
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
    return report, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report, path = run_analysis(args.input)
    except (OSError, ValueError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, DocumentError) else type(exc).__name__
        print(json.dumps({"verdict": "BLOCKED", "error_code": code}))
        return 2
    print(json.dumps({key: report[key] for key in (
        "verdict", "selection_count", "access_success", "access_failure",
        "requests_attempted", "verified_field_count", "unconfirmed_field_count",
    )}, indent=2))
    print(f"Report: {path}")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())

"""Classify an existing complete DART JSON locally; no credentials or API access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.dart_disclosure_classification import ClassificationError, classify_collection

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_RELATIVE = Path("output") / "dart_disclosure_classification"


def run_classification(source_path: Path, repo_root: Path = ROOT) -> tuple[dict, Path]:
    raw = source_path.read_bytes()
    source = json.loads(raw)
    report = classify_collection(source)
    now = datetime.now(timezone.utc)
    report.update(
        source_path=str(source_path.resolve()), source_sha256=hashlib.sha256(raw).hexdigest(),
        classified_at=now.isoformat(timespec="seconds"),
    )
    output_dir = repo_root / OUTPUT_RELATIVE
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{now:%Y%m%dT%H%M%S}_{uuid.uuid4().hex}.json"
    descriptor, temporary_name = tempfile.mkstemp(dir=output_dir, suffix=".tmp")
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return report, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report, path = run_classification(args.input)
    except (OSError, ValueError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, ClassificationError) else type(exc).__name__
        print(json.dumps({"verdict": "FAIL", "error_code": code}))
        return 1
    print(json.dumps({key: report[key] for key in (
        "verdict", "input_count", "processed_count", "duplicates_removed",
        "correction_count", "importance_counts", "category_counts", "unclassified_count",
    )}, ensure_ascii=False, indent=2))
    print(f"Report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

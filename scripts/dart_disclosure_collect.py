"""STEP 6-A/6-B: manual-only DART collection, separate from Expanded output."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.data_provider.dart_disclosure_provider import (
    DartClient,
    DartDisclosureError,
    collect_disclosures,
)
from src.expanded_shadow_universe import (
    ExpandedUniverseValidationError,
    load_expanded_universe,
)

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_RELATIVE = Path("data") / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
OUTPUT_RELATIVE = Path("output") / "dart_disclosures"


def run_collection(
    repo_root: Path, start: date, end: date, *, scope: str = "sample",
) -> tuple[dict, Path]:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    universe = load_expanded_universe(repo_root / UNIVERSE_RELATIVE)
    key = os.environ.get("DART_API_KEY", "").strip()
    report = collect_disclosures(
        universe.tickers, DartClient(key) if key else None, start, end, scope=scope,
    )
    now = datetime.now(timezone.utc)
    report.update(
        collected_at=now.isoformat(timespec="seconds"),
        started_at=started_at, elapsed_seconds=round(time.perf_counter() - started, 3),
        includes_end_date_in_progress=end == datetime.now(timezone(timedelta(hours=9))).date(),
        universe_source=str(UNIVERSE_RELATIVE), universe_sha256=universe.sha256,
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
    parser.add_argument("--end-date", type=date.fromisoformat,
                        default=datetime.now(timezone(timedelta(hours=9))).date())
    parser.add_argument("--scope", choices=("sample", "all"), default="sample")
    parser.add_argument("--lookback-days", type=int, choices=range(1, 32))
    args = parser.parse_args(argv)
    lookback = args.lookback_days if args.lookback_days is not None else (
        3 if args.scope == "all" else 30
    )
    if args.scope == "all" and lookback > 3:
        parser.error("--scope all supports at most 3 calendar days")
    if args.end_date > datetime.now(timezone(timedelta(hours=9))).date():
        parser.error("--end-date must not be in the future")
    start = args.end_date - timedelta(days=lookback - 1)
    try:
        report, path = run_collection(ROOT, start, args.end_date, scope=args.scope)
    except (DartDisclosureError, ExpandedUniverseValidationError, OSError) as exc:
        code = exc.code if isinstance(exc, DartDisclosureError) else type(exc).__name__
        print(json.dumps({"verdict": "BLOCKED", "error_code": code}))
        return 2
    print(json.dumps({
        key: report[key] for key in (
            "verdict", "blocked_reason", "universe_count", "mapping_counts",
            "scope", "collection_method", "start_date", "end_date", "elapsed_seconds",
            "collection_complete", "uncollected_count", "companies_with_disclosures",
            "error_codes", "includes_end_date_in_progress",
            "mapping_unverified_count",
            "collection_counts", "api_request_count", "disclosure_count",
            "duplicates_removed", "correction_count",
        )
    }, indent=2))
    print(f"Report: {path}")
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())

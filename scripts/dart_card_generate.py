"""Generate isolated evidence cards; never collect or update disclosure data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.dart_disclosure_query import QueryError
from src.dart_analysis_cards import (
    ARCHIVE_REANALYSIS_RECEIPTS, CARD_STORE, EXTRACTOR_VERSION, SOURCE_STORE,
    CardError, generate_cards, reanalyze_archived,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_STORE)
    parser.add_argument("--store", type=Path, default=CARD_STORE)
    parser.add_argument("--receipt", action="append", help="Repeat for multiple receipts; default: supported candidates")
    parser.add_argument("--reanalyze-archive", action="store_true", help="Offline fixed seven-receipt batch; default: preview")
    parser.add_argument("--execute", action="store_true", help="Save the archive batch atomically")
    args = parser.parse_args(argv)
    if args.execute and not args.reanalyze_archive:
        parser.error("--execute requires --reanalyze-archive")
    try:
        report = (
            reanalyze_archived(args.store, execute=args.execute, receipts=args.receipt)
            if args.reanalyze_archive else generate_cards(args.source, args.store, args.receipt)
        )
    except Exception as exc:
        code = str(exc) if isinstance(exc, (CardError, QueryError)) else type(exc).__name__
        print(json.dumps({
            "verdict": "FAIL", "error_code": code,
            **({"mode": "ARCHIVED_REANALYSIS", "http_request_count": 0,
                "created": 0, "attempts_created": 0, "extractor_version": EXTRACTOR_VERSION,
                "receipts": ARCHIVE_REANALYSIS_RECEIPTS} if args.reanalyze_archive else {}),
        }))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] in ("PASS", "PREVIEW") else 1


if __name__ == "__main__":
    raise SystemExit(main())

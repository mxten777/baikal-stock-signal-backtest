"""Read stored cards and evidence without HTTP or database writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from src.dart_analysis_cards import CARD_STORE, CardError, query_cards, render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=CARD_STORE)
    parser.add_argument("--receipt")
    parser.add_argument("--ticker")
    parser.add_argument("--status", choices=("COMPLETE", "PARTIAL", "UNSUPPORTED", "FAILED"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        results = query_cards(
            args.store, receipt=args.receipt, ticker=args.ticker, status=args.status, limit=args.limit,
        )
        output = json.dumps({
            "verdict": "PASS", "query_count": len(results),
            "freshness_note": "Stored cards only; no collection or original recheck performed.",
            "results": results,
        }, ensure_ascii=False, indent=2) if args.format == "json" else (
            f"저장된 카드 조회 {len(results)}건; 수집·본문 재확인 없음\n\n"
            + "\n\n".join(render_markdown(result) for result in results)
        )
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, CardError) else type(exc).__name__
        print(json.dumps({"verdict": "FAIL", "error_code": code}))
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

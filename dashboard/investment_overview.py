from __future__ import annotations

import csv
import json
import logging
import math
import sqlite3
from datetime import date
from pathlib import Path

from scripts.dart_disclosure_accumulate import STORE_RELATIVE
from scripts.dart_disclosure_query import query_disclosures
from scripts.safe_market_update import DEFAULT_TICKERS


def _saved_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _latest_row(
    path: Path, date_key: str, fields: tuple[str, ...], *, exact_date: str | None = None,
) -> dict | None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            row = max(csv.DictReader(stream), key=lambda item: _saved_date(item[date_key]), default=None)
        if row is None:
            return None
        stamp = _saved_date(row[date_key])
        if exact_date is not None and stamp != exact_date:
            return None
        result = {key: float(row[key]) if row[key] else None for key in fields}
        if any(value is not None and not math.isfinite(value) for value in result.values()):
            raise ValueError("Non-finite stored value")
        return {**result, "date": stamp, **({"period": row["report_period"]} if date_key == "disclosure_date" else {})}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, csv.Error):
        logging.getLogger(__name__).warning("Could not read stored investment data: %s", path.name)
        return None


def build_investment_overview(repo_root: Path, snapshot_root: Path) -> dict:
    snapshot_date = None
    try:
        manifest_path = snapshot_root / "output" / "expanded_shadow" / "expanded_shadow_run.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest.get("status") == "SUCCESS":
            snapshot_date = _saved_date(manifest["basDd"])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        logging.getLogger(__name__).warning("Could not read the stored market snapshot manifest")

    data = repo_root / "data"
    snapshots = snapshot_root / "data" / "expanded_shadow"
    companies = []
    for ticker, name in DEFAULT_TICKERS.items():
        market_fields = ("close", "volume")
        flow_fields = ("foreign_net_buy", "institution_net_buy")
        market = _latest_row(data / "raw" / f"{ticker}.csv", "date", market_fields)
        flow = _latest_row(data / "investor" / f"{ticker}_investor.csv", "date", flow_fields)
        if snapshot_date:
            market_snapshot = _latest_row(
                snapshots / "market" / snapshot_date / f"{ticker}.csv", "date", market_fields, exact_date=snapshot_date,
            )
            flow_snapshot = _latest_row(
                snapshots / "investor" / snapshot_date / f"{ticker}_investor.csv", "date", flow_fields, exact_date=snapshot_date,
            )
            if market_snapshot and (market is None or market_snapshot["date"] > market["date"]):
                market = market_snapshot
            if flow_snapshot and (flow is None or flow_snapshot["date"] > flow["date"]):
                flow = flow_snapshot
        companies.append({
            "ticker": ticker, "name": name, "market": market, "flow": flow,
            "finance": _latest_row(
                data / "fundamentals" / f"{ticker}_fundamentals.csv", "disclosure_date",
                ("revenue", "operating_income", "net_income"),
            ),
        })

    try:
        report = query_disclosures(repo_root / STORE_RELATIVE, high=True, limit=8)
        dart = {
            "items": report["disclosures"],
            "collected_at": report["last_collection_success_at"],
            "window_end": report.get("source_window", {}).get("end_date"),
        }
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        logging.getLogger(__name__).warning("Could not verify the saved DART disclosure store")
        dart = None
    return {"companies": companies, "dart": dart}

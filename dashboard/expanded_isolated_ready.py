"""Project verified isolated completed results without operational publication."""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedManifestError, ExpandedRunManifest, validate_manifest
from src.expanded_shadow_universe import load_expanded_universe
from src.expanded_trading_halts import TradingHaltStatusUnknown, _verified_status


ISOLATED_READY_ENDPOINT = "/api/dashboard/expanded-shadow/isolated-ready"
ISOLATED_READY_ROOT_ENV = "BAIKAL_EXPANDED_ISOLATED_READY_ROOT"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def build_isolated_ready(root: Path, operational_root: Path) -> dict[str, Any]:
    try:
        _require(root.is_absolute() and root.resolve() == root.absolute() and root.is_dir(),
                 "Isolated READY root must be an existing absolute non-redirected directory")
        operating = operational_root.resolve()
        _require(not root.is_relative_to(operating) and not operating.is_relative_to(root),
                 "Isolated READY and operational roots must not overlap")

        def read_path(path: Path) -> Path:
            _require(path.is_absolute() and path.is_relative_to(root)
                     and path.resolve() == path.absolute(), "Redirected or out-of-root isolated READY path")
            return path

        def read_json(path: Path) -> Any:
            return json.loads(read_path(path).read_text(encoding="utf-8"))

        report = read_json(root / "analysis-once" / "verified_result.json")
        _require(isinstance(report, dict) and report.get("status") == "READY"
                 and report.get("analysis_calls") == 1
                 and report.get("operating_publication") == "PROHIBITED_BY_OPERATOR",
                 "Verified independent READY report required")
        checks = report.get("checks")
        _require(isinstance(checks, dict) and bool(checks)
                 and all(value is True for value in checks.values()), "Isolated READY verification failed")
        source_date, run_id = report["source_date"], report["run_id"]
        _require(isinstance(source_date, str) and date.fromisoformat(source_date).isoformat() == source_date
                 and isinstance(run_id, str) and bool(run_id), "Invalid isolated READY identity")
        manifest_path = read_path(root / "output" / "expanded_shadow" / "manifests" / source_date / f"{run_id}.json")
        _require(report["manifest_path"] == str(manifest_path), "Isolated READY manifest identity mismatch")
        payload = read_json(manifest_path)
        manifest = ExpandedRunManifest(**payload)
        validate_manifest(manifest)
        universe = load_expanded_universe(
            read_path(root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"),
        )
        _require(universe.basDd <= source_date, "Isolated READY universe approval is after the source date")
        members = {item.ticker: item for item in universe.tickers}
        excluded = {item["ticker"] for item in manifest.trading_halt_exclusions}
        count = universe.row_count - len(excluded)
        _require(manifest.status == "SUCCESS" and manifest.basDd == source_date and manifest.run_id == run_id
                 and manifest.universe_sha256 == universe.sha256
                 and manifest.canonical_universe_count == universe.row_count
                 and manifest.ready_count == manifest.attempted_ticker_count == count
                 and manifest.market_success_count == manifest.investor_success_count == count
                 and not manifest.unresolved_tickers and manifest.quarantine_count == 0
                 and not manifest.system_failures and excluded <= members.keys(),
                 "Isolated READY manifest/universe/count mismatch")
        _require(manifest.market_source_date_distribution == {source_date: count}
                 and manifest.investor_source_date_distribution == {source_date: count}
                 and all(type(value) is int and value >= 0 for value in manifest.status_counts.values())
                 and {key: value for key, value in manifest.status_counts.items() if value}
                 == {"READY": count, "TRADING_HALT_EXCLUDED": len(excluded)},
                 "Isolated READY source dates/status counts mismatch")

        inventory = read_json(root / "snapshot_hashes.json")
        expected = {
            str(Path("data") / "expanded_shadow" / kind / source_date / f"{ticker}{suffix}.csv")
            for ticker in members.keys() - excluded
            for kind, suffix in (("market", ""), ("investor", "_investor"))
        }
        _require(isinstance(inventory, list) and len(inventory) == len(expected)
                 and {entry["path"] for entry in inventory} == expected,
                 "Isolated READY input inventory membership mismatch")
        for entry in inventory:
            path = read_path(root / entry["path"])
            _require(hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"],
                     "Isolated READY input hash mismatch")

        ledger = read_path(root / "output" / "expanded_shadow" / "expanded_shadow_signal_ledger.csv")
        _require(manifest.ledger_path == str(ledger), "Isolated READY ledger identity mismatch")
        with ledger.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            _require(reader.fieldnames == LEDGER_FIELDS, "Isolated READY signal schema mismatch")
            rows = list(reader)
        seen: set[str] = set()
        records = []
        for row in rows:
            ticker = row["stock_code"]
            _require(None not in row and all(value is not None for value in row.values())
                     and ticker in members and ticker not in excluded | seen,
                     "Invalid, duplicate or held isolated signal ticker")
            member = members[ticker]
            _require(row["stock_name"] == member.name and row["market"] == member.market
                     and row["basDd"] == row["signal_date"] == source_date
                     and row["run_id"] == run_id and row["source_commit"] == manifest.source_commit,
                     "Isolated READY signal provenance mismatch")
            _require(all(math.isfinite(float(row[field])) for field in ("signal_price", "raw_score", "signal_score")),
                     "Isolated READY non-finite signal value")
            _require(
                (row["decision"] == "CANDIDATE" and row["foreign_status"] in {"POSITIVE", "NEUTRAL", "UNKNOWN"}
                 and not row["exclusion_reason"])
                or (row["decision"] == "EXCLUDED" and row["foreign_status"] == "NEGATIVE"
                    and row["exclusion_reason"] == "FOREIGN_NEGATIVE"),
                "Isolated READY signal decision mismatch",
            )
            seen.add(ticker)
            records.append({"ticker": ticker, "name": member.name, "market": member.market,
                            "decision": row["decision"], "signal_type": row["signal_type"],
                            "signal_score": float(row["signal_score"])})
        candidates = sum(row["decision"] == "CANDIDATE" for row in rows)
        _require(len(rows) == manifest.signal_count and candidates == manifest.new_candidate_count,
                 "Isolated READY signal/candidate count mismatch")
        for key, value in {"universe": universe.row_count, "processed": count, "halt_excluded": len(excluded),
                           "unknown": 0, "signals": len(rows), "candidates": candidates}.items():
            _require(type(report.get(key)) is int and report[key] == value, "Isolated READY report count mismatch")
        status = read_json(root / "evidence" / "unison_current_status.json")
        as_of, updated_at, state = _verified_status(status, date.fromisoformat(source_date))
        _require(state == "ACTIVE" and "018000" in members and "018000" not in excluded,
                 "Isolated READY Unison ACTIVE evidence mismatch")
        return {
            "read_only": True, "status": "READY", "scope": "ISOLATED",
            "operational_published": False, "promoted": False,
            "source_date": source_date, "run_id": run_id, "universe": universe.row_count,
            "processed": count, "signals": len(rows), "candidates": candidates, "unknown": 0,
            "excluded": [{"ticker": ticker, "name": members[ticker].name, "status": "HALTED_EXCLUDED"}
                         for ticker in sorted(excluded)],
            "active": [{"ticker": "018000", "name": members["018000"].name, "status": state,
                        "as_of": as_of, "updated_at": updated_at}],
            "records": records,
        }
    except (OSError, ValueError, TypeError, KeyError, AttributeError, csv.Error,
            ExpandedManifestError, TradingHaltStatusUnknown) as exc:
        raise ValueError(f"Isolated READY validation failed: {exc}") from exc

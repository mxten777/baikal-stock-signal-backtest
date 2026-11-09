"""Read isolated PARTIAL runs without promoting them to completed results."""

from __future__ import annotations

import csv
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedManifestError, ExpandedRunManifest, validate_manifest
from src.expanded_shadow_universe import load_expanded_universe


PARTIAL_ENDPOINT = "/api/dashboard/expanded-shadow/partial"
PARTIAL_VALIDATION_ENDPOINT = "/api/dashboard/expanded-shadow/partial-validation"
PARTIAL_ROOT_ENV = "BAIKAL_EXPANDED_PARTIAL_ROOT"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_path(path: Path, root: Path) -> Path:
    _require(path.resolve() == path.absolute() and path.is_relative_to(root),
             f"Redirected or out-of-root PARTIAL path: {path}")
    return path


def _read_run(manifest_path: Path, root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_read_path(manifest_path, root).read_text(encoding="utf-8"))
        _require(isinstance(payload, dict), "PARTIAL manifest must be an object")
        for field in ("status_counts", "market_source_date_distribution",
                      "investor_source_date_distribution"):
            _require(isinstance(payload.get(field), dict), f"Invalid {field}")
        for field in ("trading_halt_exclusions", "unresolved_tickers"):
            _require(isinstance(payload.get(field), list)
                     and all(isinstance(item, dict) for item in payload[field]),
                     f"Invalid {field}")
        for field in ("canonical_universe_count", "attempted_ticker_count",
                      "market_success_count", "investor_success_count", "ready_count",
                      "quarantine_count", "signal_count", "new_candidate_count"):
            _require(type(payload.get(field)) is int and payload[field] >= 0,
                     f"Invalid {field}")
        _require(all(isinstance(payload.get(field), str)
                     for field in ("basDd", "run_id", "ledger_path", "universe_sha256")),
                 "Invalid PARTIAL identity")
        manifest = ExpandedRunManifest(**payload)
        validate_manifest(manifest)
        _require(manifest.status == "PARTIAL", "Expected PARTIAL, not a completed run")
        _require(date.fromisoformat(manifest.basDd).isoformat() == manifest.basDd,
                 "Invalid source date")
        _require(manifest_path.parent.name == manifest.run_id
                 and manifest_path.parent.parent.name == manifest.basDd,
                 "PARTIAL directory date/run does not match manifest")
        universe_path = _read_path(
            root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv", root,
        )
        universe = load_expanded_universe(universe_path)
        members = {item.ticker: item for item in universe.tickers}
        _require(manifest.canonical_universe_count == universe.row_count
                 and manifest.universe_sha256 == universe.sha256,
                 "Approved universe count/hash mismatch")
        excluded = {item["ticker"] for item in manifest.trading_halt_exclusions}
        unresolved = {item["ticker"] for item in manifest.unresolved_tickers}
        unknown = {
            item["ticker"] for item in manifest.unresolved_tickers
            if item.get("status") == "UNKNOWN"
        }
        _require(not (excluded & unresolved) and (excluded | unresolved) <= members.keys(),
                 "Excluded/unresolved membership conflict")
        _require(all(item.get("source_date", manifest.basDd) == manifest.basDd
                     for item in manifest.unresolved_tickers), "Unresolved source date mismatch")
        _require(manifest.attempted_ticker_count == len(members) - len(excluded) - len(unknown),
                 "Attempted count mismatch")
        _require(manifest.status_counts.get("READY") == manifest.ready_count
                 and manifest.status_counts.get("UNKNOWN", 0) == len(unknown)
                 and manifest.status_counts.get("TRADING_HALT_EXCLUDED", 0) == len(excluded)
                 and all(type(value) is int and value >= 0
                         for value in manifest.status_counts.values())
                 and sum(manifest.status_counts.values()) == len(members),
                 "PARTIAL status counts mismatch")
        for count, distribution in (
            (manifest.market_success_count, manifest.market_source_date_distribution),
            (manifest.investor_success_count, manifest.investor_source_date_distribution),
        ):
            _require(distribution == {manifest.basDd: count} and count == manifest.ready_count,
                     "PARTIAL source date/count mismatch")
        signal_path = _read_path(manifest_path.parent / "signals.csv", root)
        _require(Path(manifest.ledger_path).is_absolute()
                 and Path(manifest.ledger_path) == signal_path,
                 "PARTIAL ledger must be the adjacent signals.csv")
        with signal_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            _require(reader.fieldnames == LEDGER_FIELDS, "Signal schema mismatch")
            rows = list(reader)
        seen: set[str] = set()
        signals = []
        for row in rows:
            ticker = row["stock_code"]
            _require(None not in row and all(value is not None for value in row.values()),
                     "Malformed signal CSV row")
            _require(ticker in members and ticker not in excluded | unresolved | seen,
                     f"Invalid, duplicate or held signal ticker: {ticker}")
            seen.add(ticker)
            _require(row["basDd"] == row["signal_date"] == manifest.basDd
                     and row["run_id"] == manifest.run_id
                     and row["source_commit"] == manifest.source_commit,
                     f"Signal date/run/source mismatch: {ticker}")
            _require(row["stock_name"] == members[ticker].name
                     and row["market"] == members[ticker].market,
                     f"Signal universe identity mismatch: {ticker}")
            for field in ("signal_price", "raw_score", "signal_score"):
                _require(math.isfinite(float(row[field])), f"Invalid {field}: {ticker}")
            _require(row["decision"] in {"CANDIDATE", "EXCLUDED"},
                     f"Invalid signal decision: {ticker}")
            _require(
                (row["decision"] == "EXCLUDED"
                 and row["foreign_status"] == "NEGATIVE"
                 and row["exclusion_reason"] == "FOREIGN_NEGATIVE")
                or (row["decision"] == "CANDIDATE"
                    and row["foreign_status"] in {"POSITIVE", "NEUTRAL", "UNKNOWN"}
                    and not row["exclusion_reason"]),
                f"Signal decision/reason mismatch: {ticker}",
            )
            signals.append({
                "ticker": ticker, "name": row["stock_name"], "market": row["market"],
                "decision": row["decision"], "signal_type": row["signal_type"],
                "signal_score": float(row["signal_score"]),
            })
        candidates = sum(row["decision"] == "CANDIDATE" for row in rows)
        _require(len(rows) == manifest.signal_count <= manifest.ready_count
                 and candidates == manifest.new_candidate_count,
                 "Signal/candidate counts do not match PARTIAL manifest")
        return {
            "status": "PARTIAL", "source_date": manifest.basDd, "run_id": manifest.run_id,
            "validation_status": "NOT_RUN", "promoted": False,
            "universe": manifest.canonical_universe_count,
            "attempted": manifest.attempted_ticker_count, "ready": manifest.ready_count,
            "suspended": len(excluded), "unknown": len(unknown),
            "unresolved": len(unresolved), "signals": len(rows), "candidates": candidates,
            "signal_excluded": len(rows) - candidates, "records": signals,
            "excluded_tickers": sorted(excluded), "unknown_tickers": sorted(unknown),
        }
    except (OSError, ValueError, TypeError, KeyError, csv.Error, ExpandedManifestError) as exc:
        raise ValueError(f"{manifest_path}: {exc}") from exc


def build_expanded_partial_board(root: Path) -> dict[str, Any]:
    _require(root.is_absolute(), "PARTIAL root must be absolute")
    root = _read_path(root, root)
    _require(root.is_dir(), f"PARTIAL root does not exist: {root}")
    partial_dir = _read_path(root / "output" / "expanded_shadow" / "partial", root)
    manifests = sorted(partial_dir.glob("*/*/manifest.json"), reverse=True)
    return {
        "read_only": True, "status": "PARTIAL" if manifests else "MISSING",
        "items": [_read_run(path, root) for path in manifests],
        "warnings": ["Isolated PARTIAL only; not completed, promoted or forward-validated."],
    }


def build_expanded_partial_validation(root: Path) -> dict[str, Any]:
    board = build_expanded_partial_board(root)
    for run in board["items"]:
        for record in run["records"]:
            record["forward_evaluation"] = {
                horizon: {
                    "status": "UNCONFIRMED",
                    "reason": "NO_VERIFIED_FORWARD_DATA",
                    "return_pct": None,
                    "benchmark_pct": None,
                    "excess_pct": None,
                }
                for horizon in ("5D", "10D", "20D")
            }
    return {**board, "view": "PARTIAL_VALIDATION"}

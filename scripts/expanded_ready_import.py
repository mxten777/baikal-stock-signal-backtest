"""Approval-pinned, offline import of an existing isolated November 10 READY.

Plan is read-only. Apply never invokes collection, analysis, or performance.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import io
import json
import os
from pathlib import Path
import sys
from typing import Callable

import pandas as pd

from dashboard.expanded_isolated_ready import build_isolated_ready
from src.expanded_continuation import (
    CONTINUE_FROM, EXCEPTION_SHA_ENV, assert_execution_date, checked_json, confined, continuation_policy,
    require, sha256, verify_approval, verify_inventory,
)
from src.expanded_publication import archive_transaction, assert_publication_idle, recover_transaction, write_transaction
from src.expanded_shadow_data import ExpandedDataError, validate_investor_frame, validate_market_frame
from src.expanded_shadow_ledger import ExpandedLedgerError, ExpandedLedgerRecord, ExpandedShadowLedgerStore, LEDGER_FIELDS
from src.expanded_shadow_ops import (
    COMPLETED_RUN_STATUSES, REGISTRY_STATUSES,
    ExpandedRegistryEvent, ExpandedRunLock, ExpandedRunManifest, ExpandedShadowPaths,
    ExpandedShadowOpsError, compute_event_id, validate_manifest,
)
from src.expanded_trading_halts import OFFICIAL_HALT_EVIDENCE, UNISON_RELEASE_SHA256


def _bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class ImportPlan:
    changes: dict[str, bytes]
    source_inventory: list[dict[str, str]]
    binding: dict
    run_id: str

    def summary(self) -> dict:
        return {**self.binding, "source_inventory": self.source_inventory, "performance_stage": "NOT_RUN"}


def plan_import(source: Path, destination: Path) -> ImportPlan:
    assert_publication_idle(destination)
    require(continuation_policy(destination) is not None, "Separate continuation approval required")
    assert_execution_date(destination, CONTINUE_FROM)
    board = build_isolated_ready(source, destination)
    require(board["source_date"] == CONTINUE_FROM, "Only the reviewed November 10 isolated READY is importable")
    paths = ExpandedShadowPaths(destination)
    source_paths = ExpandedShadowPaths(source)
    require(paths.controlled_universe_path.read_bytes() == source_paths.controlled_universe_path.read_bytes(),
            "Source and operational universe differ")
    run_id = board["run_id"]
    source_manifest_path = source_paths.run_manifest_path(CONTINUE_FROM, run_id)
    manifest = ExpandedRunManifest(**checked_json(source_manifest_path))
    require(paths.current_run_path.exists() and paths.signal_ledger_path.exists() and paths.registry_path.exists(),
            "Existing operational current, signal ledger and Registry are required")
    require(not paths.latest_manifest_path(CONTINUE_FROM).exists()
            and not paths.run_manifest_path(CONTINUE_FROM, run_id).exists(),
            "Target date/run already has publication evidence; no replacement allowed")
    if paths.current_run_path.exists():
        current = checked_json(paths.current_run_path)
        require(current["basDd"] < CONTINUE_FROM, "Current date would move backwards or overwrite same-date publication")
        validate_manifest(ExpandedRunManifest(**current))
        require(current["status"] in COMPLETED_RUN_STATUSES, "Operational current must be completed")
        require(current["ledger_path"] == str(paths.signal_ledger_path), "Operational ledger path mismatch")
        require(checked_json(paths.resolve_manifest_path(current["basDd"])) == current,
                "Operational current differs from immutable manifest/latest")
    changes: dict[str, bytes] = {}
    source_files: set[Path] = {
        source_manifest_path, source_paths.signal_ledger_path, source_paths.controlled_universe_path,
        confined(source, "snapshot_hashes.json"), confined(source, str(Path("analysis-once") / "verified_result.json")),
        confined(source, str(Path("evidence") / "unison_current_status.json")),
        confined(source, str(Path("evidence") / "unison_release.html")),
    }
    release = confined(source, str(Path("evidence") / "unison_release.html"))
    require(sha256(release.read_bytes()) == UNISON_RELEASE_SHA256, "Saved official ACTIVE release hash mismatch")
    for exclusion in manifest.trading_halt_exclusions:
        evidence = OFFICIAL_HALT_EVIDENCE[(exclusion["ticker"], CONTINUE_FROM)]
        for receipt, _, digest in evidence.documents:
            path = confined(source, str(Path("evidence") / f"{receipt}.html"))
            require(sha256(path.read_bytes()) == digest, "Saved official halt document hash mismatch")
            source_files.add(path)
        history = confined(source, str(Path("evidence") / "fidelix_filings_through_20261110.html"))
        from lxml import html
        import re

        doc = html.fromstring(history.read_bytes().decode("utf-8"))
        links = doc.xpath('//a[contains(@href, "rcpNo=")]')
        ids = tuple(re.search(r"rcpNo=(\d{14})", item.get("href", "")).group(1) for item in links)
        text = " ".join(doc.text_content().split())
        require(ids == evidence.filing_ids and "[1/1]" in text and f"[총 {len(ids)}건]" in text
                and not any(word in item.text_content() for item in links
                            for word in ("거래재개", "거래정지해제", "거래정지 해제")),
                "Saved complete official history differs from reviewed session")
        source_files.add(history)
    inventory = json.loads((source / "snapshot_hashes.json").read_text(encoding="utf-8"))
    for entry in inventory:
        path = confined(source, entry["path"])
        frame = pd.read_csv(io.BytesIO(path.read_bytes()), dtype={"ticker": str})
        ticker = path.stem.split("_")[0]
        validator = validate_investor_frame if path.parent.parent.name == "investor" else validate_market_frame
        _, latest = validator(frame, ticker, CONTINUE_FROM)
        require(latest == CONTINUE_FROM, "Snapshot not aligned to import session")
        target = confined(destination, entry["path"])
        data = path.read_bytes()
        require(sha256(data) == entry["sha256"], "Source snapshot changed during import planning")
        require(not target.exists() or target.read_bytes() == data, "Existing operational input conflicts")
        changes[str(target.relative_to(destination))] = data
        source_files.add(path)
    frame = pd.read_csv(source_paths.signal_ledger_path, dtype=str, keep_default_na=False)
    records = []
    for row in frame.to_dict(orient="records"):
        row["signal_price"] = float(row["signal_price"])
        require(float(row["raw_score"]).is_integer(), "Raw signal score must be an integer")
        row["raw_score"] = int(float(row["raw_score"]))
        row["signal_score"] = float(row["signal_score"])
        row["foreign_5d_ratio"] = float(row["foreign_5d_ratio"]) if row["foreign_5d_ratio"] else None
        records.append(ExpandedLedgerRecord(**row))
    require(list(frame.columns) == LEDGER_FIELDS, "Import signal schema mismatch")
    ledger, added = ExpandedShadowLedgerStore(paths).preview_merge(records)
    require(added == manifest.signal_count, "Target already contains same-date signals without matching publication")
    changes[str(paths.signal_ledger_path.relative_to(destination))] = ledger
    published = replace(manifest, ledger_path=str(paths.signal_ledger_path),
                        quarantine_path=str(paths.quarantine_path(CONTINUE_FROM)))
    validate_manifest(published)
    changes[str(paths.run_manifest_path(CONTINUE_FROM, run_id).relative_to(destination))] = _bytes(published.to_dict())
    event = ExpandedRegistryEvent(
        event_id=compute_event_id(run_id, CONTINUE_FROM, "RUN_COMPLETED", published.status),
        run_id=run_id, basDd=CONTINUE_FROM, event_type="RUN_COMPLETED", status=published.status,
        started_at=published.started_at, finished_at=published.finished_at,
        canonical_universe_count=published.canonical_universe_count,
        attempted_ticker_count=published.attempted_ticker_count, ready_count=published.ready_count,
        signal_count=published.signal_count, quarantine_count=published.quarantine_count,
        manifest_path=str(paths.run_manifest_path(CONTINUE_FROM, run_id)),
        created_at=published.finished_at, trading_halt_exclusions=published.trading_halt_exclusions,
    )
    existing = paths.registry_path.read_bytes() if paths.registry_path.exists() else b""
    events = [json.loads(line) for line in existing.splitlines() if line.strip()]
    for item in events:
        require(isinstance(item, dict), "Operational Registry event must be an object")
        registered = ExpandedRegistryEvent(**item)
        require(registered.status in REGISTRY_STATUSES
                and registered.event_id == compute_event_id(registered.run_id, registered.basDd,
                                                            registered.event_type, registered.status),
                "Operational Registry identity/status is invalid")
        require(not (registered.event_type == "RUN_COMPLETED" and registered.basDd > CONTINUE_FROM),
                "Operational Registry contains later completed history")
    if paths.current_run_path.exists():
        require(any(item["run_id"] == current["run_id"] and item["basDd"] == current["basDd"]
                    and item["event_type"] == "RUN_COMPLETED" and item["status"] == current["status"]
                    for item in events), "Operational current has no matching completion event")
    require(not any(item.get("event_id") == event.event_id or
                    (item.get("basDd") == CONTINUE_FROM and item.get("event_type") == "RUN_COMPLETED")
                    for item in events), "Registry already contains publication identity/date")
    changes[str(paths.registry_path.relative_to(destination))] = (
        existing + (b"\n" if existing and not existing.endswith(b"\n") else b"") + _bytes(event.to_dict()) + b"\n"
    )
    changes[str(paths.latest_manifest_path(CONTINUE_FROM).relative_to(destination))] = _bytes({
        "basDd": CONTINUE_FROM, "run_id": run_id,
        "manifest_path": str(paths.run_manifest_path(CONTINUE_FROM, run_id)),
        "status": published.status, "updated_at": published.finished_at,
    })
    changes[str(paths.current_run_path.relative_to(destination))] = _bytes(published.to_dict())
    source_inventory = [
        {"path": str(path.relative_to(source)), "sha256": sha256(path.read_bytes())}
        for path in sorted(source_files)
    ]
    write_set = [
        {"path": name, "before": sha256((destination / name).read_bytes()) if (destination / name).exists() else None,
         "after": sha256(data)}
        for name, data in changes.items()
    ]
    binding = {
        "source_date": CONTINUE_FROM, "run_id": run_id, "source_root": str(source),
        "destination_root": str(destination), "write_set": write_set,
        "write_set_sha256": sha256(_bytes({"writes": write_set})),
        "continuation_approval_id": continuation_policy(destination)["approval_id"],
        "continuation_approval_sha256": os.environ[EXCEPTION_SHA_ENV],
        "operational_boundary_sha256": sha256(paths.operational_resume_path.read_bytes()),
        "destination_universe_sha256": sha256(paths.controlled_universe_path.read_bytes()),
        "performance": False,
    }
    return ImportPlan(changes, source_inventory, binding, run_id)


def apply_import(
    source: Path, destination: Path, approval_path: Path, approval_sha: str, *,
    checkpoint: Callable[[str], None] = lambda stage: None,
) -> dict:
    approval = verify_approval(approval_path, approval_sha, "IMPORT_ISOLATED_READY")
    require(approval.get("operational_publication") is True and approval.get("performance") is False,
            "Import approval does not grant publication-only scope")
    require(approval.get("source_root") == str(source) and approval.get("destination_root") == str(destination),
            "Import approval root mismatch")
    assert_publication_idle(destination)
    journal = confined(destination, str(Path("output") / "expanded_shadow" / "publication_transaction" / "active.json"))
    if journal.exists():
        previous = checked_json(journal)
        require(previous.get("state") == "COMMITTED" and previous.get("approval_sha256") == approval_sha,
                "Different or rolled-back import journal requires explicit archival")
        verify_inventory(source, approval["source_inventory"])
        for entry in previous["entries"]:
            require(sha256(confined(destination, entry["target"]).read_bytes()) == entry["after"],
                    "Committed import changed; duplicate verification failed")
        return {"status": "ALREADY_IMPORTED", "run_id": previous["run_id"], "performance": "NOT_RUN"}
    lock = ExpandedRunLock(ExpandedShadowPaths(destination))
    lock.acquire(approval["run_id"], CONTINUE_FROM)
    try:
        plan = plan_import(source, destination)
        for key, value in plan.binding.items():
            require(approval.get(key) == value, f"Import approval binding mismatch: {key}")
        require(approval.get("source_inventory") == plan.source_inventory, "Import evidence inventory changed")
        verify_inventory(source, approval["source_inventory"])
        write_transaction(destination, plan.changes, day=CONTINUE_FROM, run_id=plan.run_id,
                          approval_sha=approval_sha, approval_raw=approval_path.read_bytes(), checkpoint=checkpoint)
        return {"status": "IMPORTED", "run_id": plan.run_id, "performance": "NOT_RUN",
                "write_set_sha256": plan.binding["write_set_sha256"]}
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "recover", "archive"))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action in {"recover", "archive"}:
            require(bool(args.approval_sha256), "Recovery approval SHA256 required")
            action = recover_transaction if args.action == "recover" else archive_transaction
            result = {"status": action(args.destination, args.approval_sha256)}
        else:
            require(args.source is not None, "Source root required")
            if args.action == "plan":
                result = plan_import(args.source, args.destination).summary()
            else:
                require(args.approval is not None and bool(args.approval_sha256), "Separate publication approval required")
                result = apply_import(args.source, args.destination, args.approval, args.approval_sha256)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, ExpandedShadowOpsError, ExpandedDataError, ExpandedLedgerError) as exc:
        status = "SAFETY_STOP" if args.action == "recover" else "BLOCKED"
        print(json.dumps({"status": status, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

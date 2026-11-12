"""Fail-closed, externally pinned continuation approval; never marks a gap complete."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


EXCEPTION_SHA_ENV = "BAIKAL_EXPANDED_CONTINUATION_APPROVAL_SHA256"
EXCEPTION_DATE = "2026-11-09"
CONTINUE_FROM = "2026-11-10"


class ContinuationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContinuationError(message)


def confined(root: Path, relative: str) -> Path:
    require(root.is_absolute() and root.resolve() == root.absolute(), "Root must be absolute and non-redirected")
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts and bool(rel.parts), "Invalid relative evidence path")
    path = root / rel
    require(path.resolve() == path.absolute() and path.is_relative_to(root), "Redirected or escaping path")
    return path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def checked_json(path: Path) -> dict[str, Any]:
    return approval_object(path.read_bytes())


def approval_object(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_object)
    require(isinstance(value, dict), "Approval/evidence must be an object")
    return value


def verify_approval(path: Path, expected_sha: str, kind: str) -> dict[str, Any]:
    require(bool(re.fullmatch(r"[a-f0-9]{64}", expected_sha)), "External approval SHA256 pin is required")
    require(path.is_absolute() and path.resolve() == path.absolute(), "Approval path is redirected or relative")
    raw = path.read_bytes()
    require(sha256(raw) == expected_sha, "Approval hash mismatch")
    payload = approval_object(raw)
    require(payload.get("version") == 1 and payload.get("kind") == kind, "Approval kind/version mismatch")
    for field in ("approval_id", "reason", "approver", "approval_reference", "code_sha"):
        require(isinstance(payload.get(field), str) and bool(payload[field].strip()), f"Missing approval {field}")
    require(bool(re.fullmatch(r"[a-f0-9]{40}", payload["code_sha"])), "Invalid approval code SHA")
    stamp = datetime.fromisoformat(payload["approved_at"])
    require(stamp.tzinfo is not None, "Approval timestamp must be timezone aware")
    require(payload.get("approved") is True, "Explicit approval required")
    require(payload.get("implementation_sha256") == implementation_sha256(),
            "Approval was not issued for this exact implementation")
    return payload


def implementation_sha256() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = sorted([
        *root.glob("src/expanded_*.py"), *root.glob("scripts/expanded_*.py"),
        *root.glob("dashboard/expanded_*.py"), root / "dashboard" / "api.py",
    ])
    inventory = {str(path.relative_to(root)): sha256(path.read_bytes()) for path in paths}
    return sha256(json.dumps(inventory, sort_keys=True).encode("utf-8"))


def verify_inventory(root: Path, entries: object) -> dict[str, str]:
    require(isinstance(entries, list) and bool(entries), "Nonempty evidence inventory required")
    result: dict[str, str] = {}
    for item in entries:
        require(isinstance(item, dict) and set(item) == {"path", "sha256"}, "Invalid evidence inventory entry")
        name, digest = item["path"], item["sha256"]
        require(isinstance(name, str) and isinstance(digest, str)
                and bool(re.fullmatch(r"[a-f0-9]{64}", digest)), "Invalid evidence hash/path")
        path = confined(root, name)
        key = str(path).casefold()
        require(key not in result, "Duplicate evidence path")
        require(sha256(path.read_bytes()) == digest, "Evidence hash mismatch")
        result[key] = digest
    return result


def continuation_policy(repo_root: Path) -> dict[str, Any] | None:
    path = confined(repo_root, str(Path("output") / "expanded_shadow" / "continuation_exception.json"))
    pin = os.environ.get(EXCEPTION_SHA_ENV, "")
    if not path.exists():
        require(not pin, "Approval pin configured but exception record missing")
        return None
    payload = verify_approval(path, pin, "PRESERVE_PARTIAL_ALLOW_CONTINUATION")
    require(payload.get("exception_date") == EXCEPTION_DATE
            and payload.get("allow_from") == CONTINUE_FROM
            and payload.get("scope") == "DATE_GATE_ONLY"
            and payload.get("history_status") == "PARTIAL_UNRECOVERED"
            and payload.get("operational_publication") is False
            and payload.get("performance") is False, "Continuation approval scope mismatch")
    root = Path(payload["evidence_root"])
    inventory = verify_inventory(root, payload["evidence"])
    manifest_path = confined(root, payload["partial_manifest"])
    signal_path = manifest_path.with_name("signals.csv")
    require(str(manifest_path).casefold() in inventory and str(signal_path).casefold() in inventory,
            "PARTIAL manifest and signals must both be pinned")
    manifest = checked_json(manifest_path)
    from src.expanded_shadow_ops import ExpandedRunManifest, validate_manifest

    validate_manifest(ExpandedRunManifest(**manifest))
    require(manifest.get("basDd") == EXCEPTION_DATE and manifest.get("status") == "PARTIAL"
            and manifest.get("run_id") == payload.get("partial_run_id"), "PARTIAL evidence identity mismatch")
    unresolved = manifest.get("unresolved_tickers", [])
    require(any(item.get("ticker") == "018000" and item.get("status") == "UNKNOWN" for item in unresolved),
            "Original Unison UNKNOWN hold must remain")
    from dashboard.expanded_partial import build_expanded_partial_board

    board = build_expanded_partial_board(root)
    require(any(item["run_id"] == payload["partial_run_id"] and "018000" in item["unknown_tickers"]
                for item in board["items"]), "PARTIAL signals/hold validation failed")
    return payload


def history_warning(repo_root: Path) -> dict[str, Any] | None:
    payload = continuation_policy(repo_root)
    if payload is None:
        return None
    return {
        "date": EXCEPTION_DATE, "status": "PARTIAL_UNRECOVERED", "unknown_tickers": ["018000"],
        "approval_id": payload["approval_id"], "allow_from": CONTINUE_FROM,
        "scope": "DATE_GATE_ONLY", "completed": False, "performance_included": False,
    }


def assert_execution_date(repo_root: Path, source_date: str) -> None:
    from src.expanded_publication import assert_publication_idle
    from src.expanded_snapshot_preparation import resolve_expanded_source_date

    assert_publication_idle(repo_root)
    journal = confined(repo_root, str(Path("output") / "expanded_shadow" / "publication_transaction" / "active.json"))
    require(not journal.exists(), "Archive the terminal import journal before further analysis execution")
    policy = continuation_policy(repo_root)
    require(not policy or source_date != EXCEPTION_DATE, "Preserved PARTIAL cannot be executed under continuation approval")
    resolve_expanded_source_date(repo_root=repo_root, explicit_source_date=source_date)

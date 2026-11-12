"""Recoverable bounded publication writes. An unfinished journal blocks readers/runners."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Callable

from src.expanded_continuation import checked_json, confined, require, sha256


TERMINAL = {"COMMITTED", "ROLLED_BACK"}


def _journal(root: Path) -> Path:
    return confined(root, str(Path("output") / "expanded_shadow" / "publication_transaction" / "active.json"))


def _durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    require(not temporary.is_symlink() and temporary.resolve() == temporary.absolute(), "Redirected temporary path")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "nt":
        move = ctypes.windll.kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move.restype = ctypes.c_int
        if not move(str(temporary), str(path), 0x1 | 0x8):
            raise ctypes.WinError()
    else:
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _save(path: Path, value: dict) -> None:
    _durable(path, json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8"))


def _hash(path: Path) -> str | None:
    return sha256(path.read_bytes()) if path.exists() else None


def assert_publication_idle(root: Path) -> None:
    journal = _journal(root)
    require(not journal.parent.exists() or journal.exists(),
            "PUBLICATION_RECOVERY_REQUIRED: incomplete staging without journal")
    if journal.exists():
        payload = checked_json(journal)
        require(payload.get("state") in TERMINAL, "PUBLICATION_RECOVERY_REQUIRED: incomplete publication")
        _validate_binding(root, payload, payload.get("approval_sha256", ""))
        field = "after" if payload["state"] == "COMMITTED" else "before"
        for entry in payload["entries"]:
            require(_hash(_target(root, entry["target"], payload["source_date"], payload["run_id"])) == entry[field],
                    "PUBLICATION_RECOVERY_REQUIRED: terminal publication hash mismatch")


def publication_epoch(root: Path) -> tuple[str | None, tuple[str, ...]]:
    assert_publication_idle(root)
    journal = _journal(root)
    archive = journal.parent.parent / "publication_archive"
    return _hash(journal), tuple(sorted(path.name for path in archive.iterdir())) if archive.exists() else ()


def _target(root: Path, name: str, day: str, run_id: str) -> Path:
    from src.expanded_shadow_ops import ExpandedShadowPaths

    paths = ExpandedShadowPaths(root)
    path = confined(root, name)
    allowed = {
        paths.signal_ledger_path, paths.registry_path, paths.current_run_path,
        paths.run_manifest_path(day, run_id), paths.latest_manifest_path(day),
    }
    inputs = (paths.market_dir(day), paths.investor_dir(day))
    require(path in allowed or (path.parent in inputs and path.suffix == ".csv"),
            "Transaction target is outside approved publication/input surface")
    return path


def write_transaction(
    root: Path, changes: dict[str, bytes], *, day: str, run_id: str, approval_sha: str,
    approval_raw: bytes,
    checkpoint: Callable[[str], None] = lambda stage: None,
) -> None:
    assert_publication_idle(root)
    journal = _journal(root)
    require(not journal.exists(), "Existing journal must be explicitly archived before a different import")
    directory = journal.parent
    require(not directory.exists(), "Unfinished staging directory requires inspection")
    entries = []
    staged: list[tuple[str, bytes]] = []
    for name, data in changes.items():
        target = _target(root, name, day, run_id)
        before = target.read_bytes() if target.exists() else None
        if before == data:
            continue
        index = len(entries)
        backup = f"{index}.before"
        after = f"{index}.after"
        if before is not None:
            staged.append((backup, before))
        staged.append((after, data))
        entries.append({
            "target": name, "before": None if before is None else sha256(before), "after": sha256(data),
            "backup": backup, "staged": after,
        })
    payload = {
        "version": 1, "state": "STAGING", "pid": os.getpid(), "source_date": day,
        "run_id": run_id, "approval_sha256": approval_sha, "entries": entries,
        "approval_raw": approval_raw.decode("utf-8"),
    }
    _validate_binding(root, payload, approval_sha)
    _save(journal, payload)
    checkpoint("STAGING")
    for name, data in staged:
        _durable(directory / name, data)
    payload["state"] = "PREPARED"
    _save(journal, payload)
    checkpoint("PREPARED")
    payload["state"] = "APPLYING"
    _save(journal, payload)
    for index, entry in enumerate(entries):
        target = _target(root, entry["target"], day, run_id)
        require(_hash(target) == entry["before"], "Target changed after preparation")
        data = confined(directory, entry["staged"]).read_bytes()
        require(sha256(data) == entry["after"], "Staged payload hash mismatch")
        _durable(target, data)
        require(_hash(target) == entry["after"], "Publication read-back mismatch")
        checkpoint(f"WRITE:{index}")
    for entry in entries:
        require(_hash(_target(root, entry["target"], day, run_id)) == entry["after"], "Final publication hash mismatch")
    checkpoint("BEFORE_COMMIT")
    payload["state"] = "COMMITTED"
    _save(journal, payload)


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        kernel.GetExitCodeProcess.restype = ctypes.c_int
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            require(ctypes.get_last_error() == 87, "Cannot establish publication owner liveness")
            return False
        try:
            code = ctypes.c_uint()
            require(bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))),
                    "Cannot read publication owner exit state")
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _validate_binding(root: Path, payload: dict, approval_sha: str) -> None:
    require(payload.get("version") == 1 and payload.get("approval_sha256") == approval_sha
            and len(approval_sha) == 64, "Recovery approval pin mismatch")
    raw = payload.get("approval_raw")
    require(isinstance(raw, str) and sha256(raw.encode("utf-8")) == approval_sha,
            "Recovery approval document hash mismatch")
    approval = json.loads(raw)
    require(isinstance(approval, dict) and approval.get("kind") == "IMPORT_ISOLATED_READY"
            and approval.get("approved") is True
            and approval.get("operational_publication") is True and approval.get("performance") is False,
            "Recovery approval scope mismatch")
    require(approval.get("destination_root") == str(root)
            and approval.get("source_date") == payload.get("source_date")
            and approval.get("run_id") == payload.get("run_id"), "Recovery approval identity mismatch")
    approved = approval.get("write_set")
    require(isinstance(approved, list) and bool(approved), "Approved write set required")
    writes = {item["path"]: item for item in approved}
    require(len(writes) == len(approved), "Duplicate approved target")
    expected = {name: item for name, item in writes.items() if item["before"] != item["after"]}
    entries = payload.get("entries")
    require(isinstance(entries, list) and len(entries) == len(expected), "Recovery write set differs from approval")
    seen = set()
    for index, entry in enumerate(entries):
        require(isinstance(entry, dict) and entry.get("target") in expected, "Unapproved recovery target")
        name = entry["target"]
        require(name not in seen and entry.get("before") == expected[name]["before"]
                and entry.get("after") == expected[name]["after"]
                and entry.get("backup") == f"{index}.before" and entry.get("staged") == f"{index}.after",
                "Recovery hashes/backup names differ from approval")
        seen.add(name)


def recover_transaction(
    root: Path, approval_sha: str, *, checkpoint: Callable[[str], None] = lambda stage: None,
) -> str:
    from src.expanded_shadow_ops import ExpandedRunLock, ExpandedShadowPaths

    journal = _journal(root)
    initial_pending = journal.with_name("active.json.pending")
    require(journal.exists() or initial_pending.exists(), "No complete transaction journal; inspection required")
    payload = checked_json(journal if journal.exists() else initial_pending)
    _validate_binding(root, payload, approval_sha)
    if payload.get("state") in TERMINAL:
        assert_publication_idle(root)
        return payload["state"]
    require(payload.get("state") in {"STAGING", "PREPARED", "APPLYING", "RECOVERING"}, "Unknown transaction state")
    staging_recovery = payload["state"] == "STAGING" or (
        payload["state"] == "RECOVERING" and payload.get("recovery_origin") == "STAGING"
    )
    require(type(payload.get("pid")) is int and not process_alive(payload["pid"]), "Publication owner is still alive")
    if not journal.exists():
        require(payload["state"] == "STAGING", "Orphan journal must be initial STAGING")
        _save(journal, payload)
    paths = ExpandedShadowPaths(root)
    old_lock = None
    if paths.lock_path.exists():
        old_lock = checked_json(paths.lock_path)
        require(old_lock.get("pid") == payload["pid"] and old_lock.get("run_id") == payload["run_id"],
                "Lock does not belong to interrupted import")
    entries = payload["entries"]
    require(isinstance(entries, list), "Invalid transaction entries")
    targets: set[Path] = set()
    for entry in entries:
        target = _target(root, entry["target"], payload["source_date"], payload["run_id"])
        require(target not in targets, "Duplicate recovery target")
        targets.add(target)
        require(_hash(target) in {entry["before"], entry["after"]}, "External target change: automatic recovery blocked")
        after = confined(journal.parent, entry["staged"])
        require((staging_recovery and not after.exists() and _hash(target) == entry["before"])
                or _hash(after) == entry["after"], "Staged recovery hash mismatch")
        backup = confined(journal.parent, entry["backup"])
        if backup.exists() or (entry["before"] is not None and _hash(target) != entry["before"]):
            require(_hash(backup) == entry["before"], "Recovery backup hash mismatch")
    if old_lock:
        require(checked_json(paths.lock_path) == old_lock, "Lock changed during recovery preflight")
        paths.lock_path.unlink()
    lock = ExpandedRunLock(paths)
    lock.acquire(payload["run_id"], payload["source_date"])
    try:
        if payload["state"] != "RECOVERING":
            payload["recovery_origin"] = payload["state"]
        payload["state"] = "RECOVERING"
        payload["pid"] = os.getpid()
        _save(journal, payload)
        for index, entry in enumerate(reversed(entries)):
            target = _target(root, entry["target"], payload["source_date"], payload["run_id"])
            require(_hash(target) in {entry["before"], entry["after"]}, "External write during rollback")
            if _hash(target) == entry["before"]:
                continue
            if entry["before"] is None:
                if target.exists():
                    target.unlink()
            else:
                _durable(target, confined(journal.parent, entry["backup"]).read_bytes())
            require(_hash(target) == entry["before"], "Recovery verification failed")
            checkpoint(f"ROLLBACK:{index}")
        payload["state"] = "ROLLED_BACK"
        _save(journal, payload)
    finally:
        lock.release()
    return "ROLLED_BACK"


def archive_transaction(root: Path, approval_sha: str) -> str:
    from src.expanded_shadow_ops import ExpandedRunLock, ExpandedShadowPaths

    journal = _journal(root)
    payload = checked_json(journal)
    _validate_binding(root, payload, approval_sha)
    require(payload["state"] in TERMINAL, "Incomplete transaction cannot be archived")
    lock = ExpandedRunLock(ExpandedShadowPaths(root))
    lock.acquire(payload["run_id"], payload["source_date"])
    try:
        require(checked_json(journal) == payload, "Transaction changed before archival")
        field = "after" if payload["state"] == "COMMITTED" else "before"
        for entry in payload["entries"]:
            require(_hash(_target(root, entry["target"], payload["source_date"], payload["run_id"])) == entry[field],
                    "Terminal transaction target changed; archival blocked")
        archive = confined(root, str(Path("output") / "expanded_shadow" / "publication_archive" /
                                    f"{approval_sha}-{payload['state']}"))
        require(not archive.exists(), "Archive already exists; preserve audit records")
        archive.parent.mkdir(parents=True, exist_ok=True)
        os.replace(journal.parent, archive)
        return "ARCHIVED"
    finally:
        lock.release()

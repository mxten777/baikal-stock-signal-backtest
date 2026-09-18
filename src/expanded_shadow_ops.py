"""Expanded Shadow operational foundation.

STEP 13-D2 only: isolated paths, lock, atomic JSON writes, manifest,
registry, and quarantine storage. This module does not collect market or
investor data, evaluate signals, write ledgers, schedule runs, or touch
Production/Shadow/DUAL operational files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.expanded_shadow_universe import EXPECTED_UNIVERSE_COUNT, TICKER_PATTERN


EXPANDED_ROOT_NAME = "expanded_shadow"
LOCK_STALE_AFTER = timedelta(hours=12)
RUN_STATUSES = frozenset({"SUCCESS", "SUCCESS_WITH_TICKER_FAILURES", "SYSTEM_FAILURE", "LOCK_CONFLICT"})


class ExpandedShadowOpsError(RuntimeError):
    """Base error for Expanded Shadow operational foundation failures."""


class ExpandedPathError(ExpandedShadowOpsError):
    """Raised when a path escapes the Expanded Shadow namespace."""


class ExpandedLockConflictError(ExpandedShadowOpsError):
    """Raised when an active Expanded Shadow lock already exists."""


class ExpandedMalformedLockError(ExpandedShadowOpsError):
    """Raised when an existing lock is malformed and cannot be trusted."""


class ExpandedManifestError(ExpandedShadowOpsError):
    """Raised when an Expanded Shadow manifest is invalid or conflicting."""


class ExpandedRegistryError(ExpandedShadowOpsError):
    """Raised when registry append/dedupe cannot be performed safely."""


class ExpandedQuarantineError(ExpandedShadowOpsError):
    """Raised when quarantine evidence cannot be appended safely."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExpandedManifestError(f"malformed JSON at {path}: {exc}") from exc


@dataclass(frozen=True)
class ExpandedShadowPaths:
    repo_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_root", Path(self.repo_root))

    @property
    def data_root(self) -> Path:
        return self.repo_root / "data" / EXPANDED_ROOT_NAME

    @property
    def output_root(self) -> Path:
        return self.repo_root / "output" / EXPANDED_ROOT_NAME

    @property
    def universe_dir(self) -> Path:
        return self.data_root / "universe"

    @property
    def universe_snapshots_dir(self) -> Path:
        return self.universe_dir / "snapshots"

    @property
    def controlled_universe_path(self) -> Path:
        return self.universe_dir / "expanded_universe_574.csv"

    def market_dir(self, basDd: str) -> Path:
        return self.data_root / "market" / basDd

    def investor_dir(self, basDd: str) -> Path:
        return self.data_root / "investor" / basDd

    @property
    def quarantine_dir(self) -> Path:
        return self.output_root / "quarantine"

    @property
    def manifests_dir(self) -> Path:
        return self.output_root / "manifests"

    def manifest_path(self, basDd: str) -> Path:
        return self.manifests_dir / f"{basDd}.json"

    def quarantine_path(self, basDd: str) -> Path:
        return self.quarantine_dir / f"{basDd}.jsonl"

    @property
    def current_run_path(self) -> Path:
        return self.output_root / "expanded_shadow_run.json"

    @property
    def registry_path(self) -> Path:
        return self.output_root / "expanded_shadow_run_registry.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.output_root / "expanded_shadow_run.lock"

    @property
    def signal_ledger_path(self) -> Path:
        return self.output_root / "expanded_shadow_signal_ledger.csv"

    def validate_data_path(self, path: str | Path) -> Path:
        target = _resolve(Path(path))
        root = _resolve(self.data_root)
        if not _is_relative_to(target, root):
            raise ExpandedPathError(f"path escapes Expanded data namespace: {path}")
        return target

    def validate_output_path(self, path: str | Path) -> Path:
        target = _resolve(Path(path))
        root = _resolve(self.output_root)
        if not _is_relative_to(target, root):
            raise ExpandedPathError(f"path escapes Expanded output namespace: {path}")
        return target

    def validate_known_paths(self) -> None:
        for path in (
            self.universe_dir,
            self.universe_snapshots_dir,
            self.controlled_universe_path,
            self.market_dir("2026-09-17"),
            self.investor_dir("2026-09-17"),
        ):
            self.validate_data_path(path)
        for path in (
            self.quarantine_dir,
            self.manifests_dir,
            self.current_run_path,
            self.registry_path,
            self.lock_path,
            self.signal_ledger_path,
        ):
            self.validate_output_path(path)


def atomic_write_json(path: str | Path, payload: dict[str, Any], paths: ExpandedShadowPaths | None = None) -> None:
    target = Path(path)
    if paths is not None:
        target = paths.validate_output_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


@dataclass(frozen=True)
class ExpandedRunManifest:
    run_id: str
    basDd: str
    started_at: str
    finished_at: str
    runtime_seconds: float
    status: str
    canonical_universe_count: int
    attempted_ticker_count: int
    market_success_count: int
    investor_success_count: int
    ready_count: int
    quarantine_count: int
    signal_count: int
    new_candidate_count: int
    status_counts: dict[str, int] = field(default_factory=dict)
    market_source_date_distribution: dict[str, int] = field(default_factory=dict)
    investor_source_date_distribution: dict[str, int] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    universe_sha256: str = ""
    source_commit: str = "UNKNOWN"
    ledger_path: str = ""
    quarantine_path: str = ""
    system_failures: list[dict[str, str]] = field(default_factory=list)
    ticker_failure_sample: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_manifest(manifest: ExpandedRunManifest) -> None:
    if manifest.status not in RUN_STATUSES:
        raise ExpandedManifestError(f"invalid run status: {manifest.status!r}")
    if manifest.canonical_universe_count != EXPECTED_UNIVERSE_COUNT:
        raise ExpandedManifestError(
            f"canonical_universe_count must be {EXPECTED_UNIVERSE_COUNT}, got {manifest.canonical_universe_count}"
        )
    if manifest.attempted_ticker_count != EXPECTED_UNIVERSE_COUNT:
        raise ExpandedManifestError(
            f"attempted_ticker_count must be {EXPECTED_UNIVERSE_COUNT}, got {manifest.attempted_ticker_count}"
        )
    if not 0 <= manifest.ready_count <= EXPECTED_UNIVERSE_COUNT:
        raise ExpandedManifestError(f"ready_count out of range: {manifest.ready_count}")
    for field_name in (
        "market_success_count",
        "investor_success_count",
        "quarantine_count",
        "signal_count",
        "new_candidate_count",
    ):
        value = getattr(manifest, field_name)
        if value < 0:
            raise ExpandedManifestError(f"{field_name} must be non-negative")


def write_manifest(
    paths: ExpandedShadowPaths,
    manifest: ExpandedRunManifest,
    write_current: bool = True,
) -> bool:
    """Write a same-basDd manifest once; identical repeats are idempotent."""
    paths.validate_known_paths()
    validate_manifest(manifest)
    payload = manifest.to_dict()
    manifest_path = paths.manifest_path(manifest.basDd)
    paths.validate_output_path(manifest_path)

    if manifest_path.exists():
        existing = _load_json(manifest_path)
        if existing == payload:
            if write_current:
                atomic_write_json(paths.current_run_path, payload, paths=paths)
            return False
        raise ExpandedManifestError(f"conflicting completed manifest exists: {manifest_path}")

    atomic_write_json(manifest_path, payload, paths=paths)
    if write_current:
        atomic_write_json(paths.current_run_path, payload, paths=paths)
    return True


@dataclass(frozen=True)
class ExpandedRegistryEvent:
    event_id: str
    run_id: str
    basDd: str
    event_type: str
    status: str
    started_at: str
    finished_at: str
    canonical_universe_count: int
    attempted_ticker_count: int
    ready_count: int
    signal_count: int
    quarantine_count: int
    error_code: str | None = None
    error_message: str | None = None
    manifest_path: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["created_at"] is None:
            payload["created_at"] = utc_now_iso()
        return payload


def compute_event_id(run_id: str, basDd: str, event_type: str, status: str) -> str:
    key = f"{run_id}:{basDd}:{event_type}:{status}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path, error_type: type[Exception]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise error_type(f"malformed JSONL line {line_number} in {path}: {exc}") from exc
            if not isinstance(payload, dict):
                raise error_type(f"JSONL line {line_number} is not an object in {path}")
            records.append(payload)
    return records


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_registry(paths: ExpandedShadowPaths, event: ExpandedRegistryEvent) -> bool:
    paths.validate_known_paths()
    path = paths.validate_output_path(paths.registry_path)
    if event.status not in RUN_STATUSES:
        raise ExpandedRegistryError(f"invalid registry status: {event.status!r}")
    records = _read_jsonl(path, ExpandedRegistryError)
    if any(record.get("event_id") == event.event_id for record in records):
        return False
    _append_jsonl(path, event.to_dict())
    return True


@dataclass(frozen=True)
class ExpandedQuarantineRecord:
    run_id: str
    basDd: str
    ticker: str
    ticker_status: str
    stage: str
    attempt_count: int
    error_code: str
    error_class: str
    error_message: str
    source_date: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["created_at"] is None:
            payload["created_at"] = utc_now_iso()
        return payload


def append_quarantine(paths: ExpandedShadowPaths, record: ExpandedQuarantineRecord) -> bool:
    paths.validate_known_paths()
    path = paths.validate_output_path(paths.quarantine_path(record.basDd))
    if not isinstance(record.ticker, str) or not __import__("re").fullmatch(TICKER_PATTERN, record.ticker):
        raise ExpandedQuarantineError(f"invalid quarantine ticker: {record.ticker!r}")
    if record.attempt_count < 0:
        raise ExpandedQuarantineError("attempt_count must be non-negative")
    records = _read_jsonl(path, ExpandedQuarantineError)
    payload = record.to_dict()
    key = (record.run_id, record.basDd, record.ticker, record.ticker_status, record.stage, record.error_code)
    for existing in records:
        existing_key = (
            existing.get("run_id"),
            existing.get("basDd"),
            existing.get("ticker"),
            existing.get("ticker_status"),
            existing.get("stage"),
            existing.get("error_code"),
        )
        if existing_key == key:
            return False
    _append_jsonl(path, payload)
    return True


class ExpandedRunLock:
    def __init__(
        self,
        paths: ExpandedShadowPaths,
        stale_after: timedelta = LOCK_STALE_AFTER,
        now_func: Any = utc_now_iso,
    ) -> None:
        self.paths = paths
        self.lock_path = paths.lock_path
        self.stale_after = stale_after
        self.now_func = now_func
        self.run_id: str | None = None
        self.acquired = False

    def acquire(self, run_id: str, basDd: str) -> bool:
        self.paths.validate_known_paths()
        lock_path = self.paths.validate_output_path(self.lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle_existing_lock(lock_path)
        payload = {"run_id": run_id, "basDd": basDd, "pid": os.getpid(), "started_at": self.now_func()}
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise ExpandedLockConflictError(f"Expanded Shadow lock already exists: {lock_path}")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        self.run_id = run_id
        self.acquired = True
        return True

    def release(self) -> bool:
        if self.run_id is None:
            return False
        lock_path = self.paths.validate_output_path(self.lock_path)
        if not lock_path.exists():
            self.acquired = False
            return False
        payload = self._read_lock_payload(lock_path)
        if payload.get("run_id") != self.run_id:
            return False
        lock_path.unlink()
        self.acquired = False
        return True

    def _handle_existing_lock(self, lock_path: Path) -> None:
        if not lock_path.exists():
            return
        payload = self._read_lock_payload(lock_path)
        started_at = _parse_aware_datetime(payload.get("started_at"))
        if datetime.now(timezone.utc) - started_at <= self.stale_after:
            raise ExpandedLockConflictError(f"active Expanded Shadow lock exists: {lock_path}")
        stale_path = lock_path.with_name(f"{lock_path.name}.stale-{started_at.strftime('%Y%m%d%H%M%S')}")
        self.paths.validate_output_path(stale_path)
        shutil.move(str(lock_path), str(stale_path))

    @staticmethod
    def _read_lock_payload(lock_path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExpandedMalformedLockError(f"malformed lock JSON: {lock_path}") from exc
        if not isinstance(payload, dict):
            raise ExpandedMalformedLockError(f"lock payload is not an object: {lock_path}")
        for field_name in ("run_id", "basDd", "pid", "started_at"):
            if field_name not in payload:
                raise ExpandedMalformedLockError(f"lock missing {field_name}: {lock_path}")
        _parse_aware_datetime(payload.get("started_at"))
        return payload


def _parse_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ExpandedMalformedLockError("lock started_at is missing or invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExpandedMalformedLockError(f"lock started_at is malformed: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ExpandedMalformedLockError("lock started_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)
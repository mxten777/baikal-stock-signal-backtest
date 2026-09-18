from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.expanded_shadow_ops import (
    LOCK_STALE_AFTER,
    RUN_STATUSES,
    ExpandedLockConflictError,
    ExpandedMalformedLockError,
    ExpandedManifestError,
    ExpandedPathError,
    ExpandedQuarantineRecord,
    ExpandedRegistryError,
    ExpandedRegistryEvent,
    ExpandedRunLock,
    ExpandedRunManifest,
    ExpandedShadowPaths,
    atomic_write_json,
    append_quarantine,
    append_registry,
    compute_event_id,
    publish_completed_run,
    write_current_manifest,
    write_latest_manifest,
    write_manifest,
)
from src.expanded_shadow_universe import compute_universe_sha256


EXPECTED_UNIVERSE_SHA = "073982938b6dd222d6b0ca3621ce763a15fd9af43c835ddd7676e78bcd71c6d2"


def _paths(tmp_path: Path) -> ExpandedShadowPaths:
    return ExpandedShadowPaths(tmp_path)


def _manifest(**overrides) -> ExpandedRunManifest:
    values = {
        "run_id": "run-1",
        "basDd": "2026-09-17",
        "started_at": "2026-09-17T09:00:00+00:00",
        "finished_at": "2026-09-17T09:01:00+00:00",
        "runtime_seconds": 60.0,
        "status": "SUCCESS",
        "canonical_universe_count": 574,
        "attempted_ticker_count": 574,
        "market_success_count": 0,
        "investor_success_count": 0,
        "ready_count": 0,
        "quarantine_count": 0,
        "signal_count": 0,
        "new_candidate_count": 0,
        "status_counts": {},
        "market_source_date_distribution": {},
        "investor_source_date_distribution": {},
        "retry_counts": {},
        "universe_sha256": EXPECTED_UNIVERSE_SHA,
        "source_commit": "deadbeef",
        "ledger_path": "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        "quarantine_path": "output/expanded_shadow/quarantine/2026-09-17.jsonl",
        "system_failures": [],
        "ticker_failure_sample": [],
    }
    values.update(overrides)
    return ExpandedRunManifest(**values)


def _event(**overrides) -> ExpandedRegistryEvent:
    values = {
        "event_id": "event-1",
        "run_id": "run-1",
        "basDd": "2026-09-17",
        "event_type": "RUN_COMPLETED",
        "status": "SUCCESS",
        "started_at": "2026-09-17T09:00:00+00:00",
        "finished_at": "2026-09-17T09:01:00+00:00",
        "canonical_universe_count": 574,
        "attempted_ticker_count": 574,
        "ready_count": 0,
        "signal_count": 0,
        "quarantine_count": 0,
        "error_code": None,
        "error_message": None,
        "last_stage": None,
        "manifest_path": "output/expanded_shadow/manifests/2026-09-17.json",
        "created_at": "2026-09-17T09:01:01+00:00",
    }
    values.update(overrides)
    return ExpandedRegistryEvent(**values)


def _quarantine(**overrides) -> ExpandedQuarantineRecord:
    values = {
        "run_id": "run-1",
        "basDd": "2026-09-17",
        "ticker": "0015N0",
        "ticker_status": "MARKET_FAILED",
        "stage": "MARKET",
        "attempt_count": 1,
        "error_code": "FETCH_TIMEOUT",
        "error_class": "TimeoutError",
        "error_message": "timeout",
        "source_date": None,
        "created_at": "2026-09-17T09:01:01+00:00",
    }
    values.update(overrides)
    return ExpandedQuarantineRecord(**values)


def test_all_expanded_paths_resolve_under_correct_namespace(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.validate_known_paths()

    assert paths.data_root == tmp_path / "data" / "expanded_shadow"
    assert paths.output_root == tmp_path / "output" / "expanded_shadow"
    assert paths.market_dir("2026-09-17") == paths.data_root / "market" / "2026-09-17"
    assert paths.investor_dir("2026-09-17") == paths.data_root / "investor" / "2026-09-17"


def test_production_raw_path_cannot_be_selected(tmp_path: Path):
    paths = _paths(tmp_path)

    with pytest.raises(ExpandedPathError):
        paths.validate_data_path(tmp_path / "data" / "raw" / "005930.csv")


def test_production_investor_path_cannot_be_selected(tmp_path: Path):
    paths = _paths(tmp_path)

    with pytest.raises(ExpandedPathError):
        paths.validate_data_path(tmp_path / "data" / "investor" / "005930_investor.csv")


def test_path_escape_fails_closed(tmp_path: Path):
    paths = _paths(tmp_path)

    with pytest.raises(ExpandedPathError):
        paths.validate_output_path(tmp_path / "output" / "expanded_shadow_escape" / "x.json")


def test_signal_ledger_path_defined_but_not_created_by_d2(tmp_path: Path):
    paths = _paths(tmp_path)

    assert paths.signal_ledger_path == paths.output_root / "expanded_shadow_signal_ledger.csv"
    assert not paths.signal_ledger_path.exists()


def test_first_lock_acquisition_passes(tmp_path: Path):
    lock = ExpandedRunLock(_paths(tmp_path), now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    assert lock.acquire("run-1", "2026-09-17") is True
    assert lock.lock_path.exists()


def test_second_active_lock_is_conflict(tmp_path: Path):
    paths = _paths(tmp_path)
    ExpandedRunLock(paths, now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")).acquire("run-1", "2026-09-17")

    with pytest.raises(ExpandedLockConflictError):
        ExpandedRunLock(paths, now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")).acquire("run-2", "2026-09-17")


def test_owner_can_release(tmp_path: Path):
    lock = ExpandedRunLock(_paths(tmp_path), now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    lock.acquire("run-1", "2026-09-17")

    assert lock.release() is True
    assert not lock.lock_path.exists()


def test_non_owner_cannot_release(tmp_path: Path):
    paths = _paths(tmp_path)
    owner = ExpandedRunLock(paths, now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    owner.acquire("run-1", "2026-09-17")
    other = ExpandedRunLock(paths, now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    other.run_id = "run-2"

    assert other.release() is False
    assert paths.lock_path.exists()


def test_stale_lock_older_than_12_hours_is_detected(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.lock_path.parent.mkdir(parents=True)
    old = (datetime.now(timezone.utc) - LOCK_STALE_AFTER - timedelta(minutes=1)).isoformat(timespec="seconds")
    paths.lock_path.write_text(json.dumps({"run_id": "old", "basDd": "2026-09-17", "pid": 1, "started_at": old}), encoding="utf-8")

    assert ExpandedRunLock(paths).acquire("new", "2026-09-17") is True
    assert paths.lock_path.exists()
    assert list(paths.output_root.glob("expanded_shadow_run.lock.stale-*"))


def test_stale_handling_remains_expanded_only(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.lock_path.parent.mkdir(parents=True)
    production_lock = tmp_path / "output" / "daily_operational_run.lock"
    production_lock.parent.mkdir(parents=True, exist_ok=True)
    production_lock.write_text("production", encoding="utf-8")
    old = (datetime.now(timezone.utc) - LOCK_STALE_AFTER - timedelta(minutes=1)).isoformat(timespec="seconds")
    paths.lock_path.write_text(json.dumps({"run_id": "old", "basDd": "2026-09-17", "pid": 1, "started_at": old}), encoding="utf-8")

    ExpandedRunLock(paths).acquire("new", "2026-09-17")

    assert production_lock.read_text(encoding="utf-8") == "production"


def test_malformed_lock_fails_closed(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.lock_path.parent.mkdir(parents=True)
    paths.lock_path.write_text("not json", encoding="utf-8")

    with pytest.raises(ExpandedMalformedLockError):
        ExpandedRunLock(paths).acquire("run-1", "2026-09-17")
    assert paths.lock_path.exists()


def test_valid_json_atomic_write(tmp_path: Path):
    paths = _paths(tmp_path)
    target = paths.output_root / "manifests" / "sample.json"

    atomic_write_json(target, {"b": 2, "a": 1}, paths=paths)

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}


def test_replacement_produces_valid_complete_json(tmp_path: Path):
    paths = _paths(tmp_path)
    target = paths.output_root / "current.json"
    atomic_write_json(target, {"version": 1}, paths=paths)
    atomic_write_json(target, {"version": 2, "ok": True}, paths=paths)

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2, "ok": True}


def test_simulated_write_failure_does_not_corrupt_prior_destination(tmp_path: Path):
    paths = _paths(tmp_path)
    target = paths.output_root / "current.json"
    atomic_write_json(target, {"version": 1}, paths=paths)

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()}, paths=paths)

    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}


def test_valid_synthetic_574_manifest_passes(tmp_path: Path):
    paths = _paths(tmp_path)

    assert write_manifest(paths, _manifest()) is True
    assert paths.run_manifest_path("2026-09-17", "run-1").exists()
    assert not paths.manifest_path("2026-09-17").exists()


def test_573_attempted_manifest_fails(tmp_path: Path):
    with pytest.raises(ExpandedManifestError):
        write_manifest(_paths(tmp_path), _manifest(attempted_ticker_count=573))


def test_575_attempted_manifest_fails(tmp_path: Path):
    with pytest.raises(ExpandedManifestError):
        write_manifest(_paths(tmp_path), _manifest(attempted_ticker_count=575))


def test_ready_count_above_574_fails(tmp_path: Path):
    with pytest.raises(ExpandedManifestError):
        write_manifest(_paths(tmp_path), _manifest(ready_count=575))


def test_allowed_manifest_statuses_are_accepted(tmp_path: Path):
    for status in RUN_STATUSES:
        paths = _paths(tmp_path / status)
        failures = [{"attempted": 0, "error_class": "RuntimeError", "error_message": "failed", "last_stage": "START"}] if status == "SYSTEM_FAILURE" else []
        assert write_manifest(paths, _manifest(status=status, run_id=f"run-{status}", system_failures=failures)) is True


def test_invalid_manifest_status_rejected(tmp_path: Path):
    with pytest.raises(ExpandedManifestError):
        write_manifest(_paths(tmp_path), _manifest(status="FAILED"))


def test_identical_same_basdd_manifest_is_idempotent(tmp_path: Path):
    paths = _paths(tmp_path)
    manifest = _manifest()

    assert write_manifest(paths, manifest) is True
    assert write_manifest(paths, manifest) is False


def test_same_run_id_conflicting_manifest_fails_closed(tmp_path: Path):
    paths = _paths(tmp_path)
    manifest = _manifest()
    write_manifest(paths, manifest)

    with pytest.raises(ExpandedManifestError):
        write_manifest(paths, replace(manifest, ready_count=1))


def test_different_run_id_same_basdd_is_allowed_and_legacy_is_unchanged(tmp_path: Path):
    paths = _paths(tmp_path)
    legacy = paths.manifest_path("2026-09-17")
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"legacy":true}\n', encoding="utf-8")
    before = legacy.read_bytes()

    assert write_manifest(paths, _manifest(run_id="run-1")) is True
    assert write_manifest(paths, _manifest(run_id="run-2")) is True

    assert legacy.read_bytes() == before
    assert paths.run_manifest_path("2026-09-17", "run-1").exists()
    assert paths.run_manifest_path("2026-09-17", "run-2").exists()


def test_manifest_resolver_prefers_latest_then_falls_back_to_legacy(tmp_path: Path):
    paths = _paths(tmp_path)
    legacy = paths.manifest_path("2026-09-17")
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"legacy":true}\n', encoding="utf-8")
    assert paths.resolve_manifest_path("2026-09-17") == legacy

    manifest = _manifest()
    write_manifest(paths, manifest)
    write_latest_manifest(paths, manifest)

    assert paths.resolve_manifest_path("2026-09-17") == paths.run_manifest_path("2026-09-17", "run-1").resolve()


def test_latest_and_current_only_accept_completed_manifests(tmp_path: Path):
    paths = _paths(tmp_path)
    failed = _manifest(
        status="SYSTEM_FAILURE",
        attempted_ticker_count=1,
        system_failures=[{"attempted": 1, "error_class": "RuntimeError", "error_message": "failed", "last_stage": "SIGNAL"}],
    )
    write_manifest(paths, failed)

    with pytest.raises(ExpandedManifestError):
        write_latest_manifest(paths, failed)
    with pytest.raises(ExpandedManifestError):
        write_current_manifest(paths, failed)
    assert not paths.latest_manifest_path("2026-09-17").exists()
    assert not paths.current_run_path.exists()


def test_completed_publication_order_stops_before_latest_on_registry_failure(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    manifest = _manifest()
    event = _event(manifest_path=str(paths.run_manifest_path("2026-09-17", manifest.run_id)))

    def fail_registry(*_args):
        raise ExpandedRegistryError("registry failed")

    monkeypatch.setattr("src.expanded_shadow_ops.append_registry", fail_registry)
    with pytest.raises(ExpandedRegistryError, match="registry failed"):
        publish_completed_run(paths, manifest, event)

    assert paths.run_manifest_path("2026-09-17", manifest.run_id).exists()
    assert not paths.latest_manifest_path("2026-09-17").exists()
    assert not paths.current_run_path.exists()


def test_append_valid_registry_event(tmp_path: Path):
    assert append_registry(_paths(tmp_path), _event()) is True


def test_registry_previous_line_preserved(tmp_path: Path):
    paths = _paths(tmp_path)
    append_registry(paths, _event(event_id="event-1"))
    append_registry(paths, _event(event_id="event-2", run_id="run-2"))
    lines = paths.registry_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["event_id"] == "event-1"


def test_duplicate_registry_event_id_does_not_duplicate(tmp_path: Path):
    paths = _paths(tmp_path)
    event = _event(event_id=compute_event_id("run-1", "2026-09-17", "RUN_COMPLETED", "SUCCESS"))

    assert append_registry(paths, event) is True
    assert append_registry(paths, event) is False
    assert len(paths.registry_path.read_text(encoding="utf-8").splitlines()) == 1


def test_malformed_existing_registry_fails_closed_for_dedupe(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.registry_path.parent.mkdir(parents=True)
    paths.registry_path.write_text("{bad\n", encoding="utf-8")

    with pytest.raises(ExpandedRegistryError):
        append_registry(paths, _event())


def test_append_valid_quarantine_record(tmp_path: Path):
    assert append_quarantine(_paths(tmp_path), _quarantine()) is True


def test_quarantine_preserves_alphanumeric_ticker(tmp_path: Path):
    paths = _paths(tmp_path)
    append_quarantine(paths, _quarantine(ticker="0015N0"))
    row = json.loads(paths.quarantine_path("2026-09-17").read_text(encoding="utf-8").splitlines()[0])

    assert row["ticker"] == "0015N0"
    assert isinstance(row["ticker"], str)


def test_quarantine_previous_evidence_preserved(tmp_path: Path):
    paths = _paths(tmp_path)
    append_quarantine(paths, _quarantine(ticker="0015N0"))
    append_quarantine(paths, _quarantine(ticker="0126Z0", error_code="SCHEMA_INVALID"))
    lines = paths.quarantine_path("2026-09-17").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert json.loads(lines[0])["ticker"] == "0015N0"


def test_duplicate_quarantine_record_is_explicitly_deduped(tmp_path: Path):
    paths = _paths(tmp_path)
    record = _quarantine()

    assert append_quarantine(paths, record) is True
    assert append_quarantine(paths, record) is False
    assert len(paths.quarantine_path("2026-09-17").read_text(encoding="utf-8").splitlines()) == 1


def test_no_production_file_created_or_modified_in_temp_repo(tmp_path: Path):
    paths = _paths(tmp_path)
    write_manifest(paths, _manifest())
    append_registry(paths, _event())
    append_quarantine(paths, _quarantine())

    assert not (tmp_path / "data" / "raw").exists()
    assert not (tmp_path / "data" / "investor").exists()
    assert not (tmp_path / "output" / "daily_operational_run.json").exists()


def test_no_dual_file_created_or_modified_in_temp_repo(tmp_path: Path):
    paths = _paths(tmp_path)
    write_manifest(paths, _manifest())
    append_registry(paths, _event())
    append_quarantine(paths, _quarantine())

    assert not (tmp_path / "output" / "dual_shadow_signal_ledger.csv").exists()
    assert not (tmp_path / "output" / "dual_shadow_run_registry.jsonl").exists()
    assert not (tmp_path / "output" / "dual_shadow_scheduler_state.json").exists()


def test_d1_universe_and_snapshot_unchanged():
    root = Path(__file__).resolve().parents[1]
    controlled = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    snapshot = root / "data" / "expanded_shadow" / "universe" / "snapshots" / "2026-09-17_expanded_universe_574.csv"

    assert compute_universe_sha256(controlled) == EXPECTED_UNIVERSE_SHA
    assert compute_universe_sha256(snapshot) == EXPECTED_UNIVERSE_SHA
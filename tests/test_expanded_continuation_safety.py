from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pandas as pd
import pytest

from dashboard.api import route_dashboard_request
from scripts.expanded_ready_import import apply_import, main, plan_import
from src.expanded_continuation import (
    EXCEPTION_SHA_ENV, ContinuationError, assert_execution_date, continuation_policy,
    history_warning, implementation_sha256, sha256,
)
from src.expanded_publication import archive_transaction, assert_publication_idle, recover_transaction, write_transaction
from src.expanded_shadow_daily import run_expanded_shadow_daily
from src.expanded_shadow_ops import ExpandedRunLock, ExpandedShadowPaths, compute_event_id
from src.expanded_shadow_pipeline import run_expanded_shadow_pipeline
from src.expanded_snapshot_preparation import resolve_expanded_source_date
import src.expanded_trading_halts as halts
from tests.test_expanded_isolated_ready import ready_root, _completed
from tests.test_expanded_partial_dashboard import partial_root, _hashes, _manifest
from tests.test_expanded_shadow_pipeline import _market_frame, _investor_frame


DAY = "2026-11-10"
PIN = "a" * 64


def save(path: Path, payload: dict | list) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return sha256(data)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def approval(kind: str) -> dict:
    return {
        "version": 1, "kind": kind, "approval_id": f"synthetic-{kind}", "approved": True,
        "approved_at": "2026-11-10T12:00:00+09:00", "reason": "SYNTHETIC TEST ONLY",
        "approver": "synthetic-fixture", "approval_reference": "synthetic-only",
        "code_sha": "a" * 40, "implementation_sha256": implementation_sha256(),
    }


@pytest.fixture
def continuation(ready_root: Path, monkeypatch):
    source = ready_root
    destination = source.parent / (source.name + "-operator")
    paths = ExpandedShadowPaths(destination)
    paths.controlled_universe_path.parent.mkdir(parents=True)
    paths.controlled_universe_path.write_bytes(ExpandedShadowPaths(source).controlled_universe_path.read_bytes())
    save(paths.operational_resume_path, {
        "operation_start_date": "2026-11-06", "history_status": "UNRECOVERED", "unrecovered_dates": [],
    })
    paths.registry_path.write_text(json.dumps({
        "basDd": "2026-11-06", "event_type": "RUN_COMPLETED", "status": "SUCCESS",
        "event_id": compute_event_id("previous-ready", "2026-11-06", "RUN_COMPLETED", "SUCCESS"),
        "run_id": "previous-ready", "started_at": "2026-11-06T21:00:00+09:00",
        "finished_at": "2026-11-06T21:01:00+09:00", "canonical_universe_count": 574,
        "attempted_ticker_count": 573, "ready_count": 573, "signal_count": 1, "quarantine_count": 0,
    }) + "\n", encoding="utf-8")
    previous = load(_completed(source))
    previous.update(
        basDd="2026-11-06", run_id="previous-ready",
        started_at="2026-11-06T21:00:00+09:00", finished_at="2026-11-06T21:01:00+09:00",
        market_source_date_distribution={"2026-11-06": 573},
        investor_source_date_distribution={"2026-11-06": 573},
        signal_count=1, new_candidate_count=1, ledger_path=str(paths.signal_ledger_path),
        quarantine_path=str(paths.quarantine_path("2026-11-06")),
        trading_halt_exclusions=[halts.OFFICIAL_HALT_EVIDENCE[("032580", "2026-11-06")].record()],
    )
    save(paths.current_run_path, previous)
    previous_manifest = paths.run_manifest_path("2026-11-06", "previous-ready")
    save(previous_manifest, previous)
    save(paths.latest_manifest_path("2026-11-06"), {
        "basDd": "2026-11-06", "run_id": "previous-ready", "manifest_path": str(previous_manifest),
        "status": "SUCCESS", "updated_at": previous["finished_at"],
    })
    previous_signals = pd.read_csv(ExpandedShadowPaths(source).signal_ledger_path, dtype=str, keep_default_na=False).iloc[:1].copy()
    previous_signals.loc[:, "basDd"] = previous_signals.loc[:, "signal_date"] = "2026-11-06"
    previous_signals.loc[:, "run_id"] = "previous-ready"
    previous_signals.to_csv(paths.signal_ledger_path, index=False, lineterminator="\n")
    manifest = _manifest(source)
    signals = manifest.with_name("signals.csv")
    payload = {
        **approval("PRESERVE_PARTIAL_ALLOW_CONTINUATION"),
        "exception_date": "2026-11-09", "allow_from": DAY, "scope": "DATE_GATE_ONLY",
        "history_status": "PARTIAL_UNRECOVERED", "operational_publication": False, "performance": False,
        "evidence_root": str(source), "partial_manifest": str(manifest.relative_to(source)),
        "partial_run_id": "partial-test",
        "evidence": [
            {"path": str(path.relative_to(source)), "sha256": sha256(path.read_bytes())}
            for path in (manifest, signals)
        ],
    }
    record = paths.output_root / "continuation_exception.json"
    pin = save(record, payload)
    monkeypatch.setenv(EXCEPTION_SHA_ENV, pin)
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("Network forbidden"))
    return source, destination, record


def test_exact_gap_preserved_and_all_date_gates(continuation):
    source, destination, record = continuation
    before = _hashes(source)
    result = resolve_expanded_source_date(repo_root=destination, now=datetime.fromisoformat("2026-11-10T22:00:00+09:00"))
    assert result.source_date == DAY and result.reason == "OLDEST_GAP"
    assert "2026-11-09" not in result.completed_dates
    assert_execution_date(destination, DAY)
    with pytest.raises(ValueError, match="Preserved PARTIAL"):
        assert_execution_date(destination, "2026-11-09")
    with pytest.raises(ValueError, match="Uncompleted|after latest"):
        assert_execution_date(destination, "2026-11-11")
    warning = history_warning(destination)
    assert warning["status"] == "PARTIAL_UNRECOVERED" and not warning["completed"]
    assert not warning["performance_included"]
    assert load(_manifest(source))["unresolved_tickers"][0]["ticker"] == "018000"
    assert _hashes(source) == before


@pytest.mark.parametrize("damage", ["pin", "missing", "manifest", "signals", "approver", "scope", "date", "code", "kind", "approval"])
def test_exception_missing_tampered_or_out_of_scope_blocks(continuation, monkeypatch, damage):
    source, destination, record = continuation
    payload = load(record)
    if damage == "pin":
        monkeypatch.delenv(EXCEPTION_SHA_ENV)
    elif damage == "missing":
        record.unlink()
    elif damage in {"manifest", "signals"}:
        path = _manifest(source) if damage == "manifest" else _manifest(source).with_name("signals.csv")
        path.write_text("changed", encoding="utf-8")
    else:
        field, value = {
            "approver": ("approver", ""), "scope": ("scope", "PUBLISH"),
            "date": ("exception_date", "2026-11-10"), "code": ("implementation_sha256", PIN),
            "kind": ("kind", "IMPORT_ISOLATED_READY"), "approval": ("approved", False),
        }[damage]
        payload[field] = value
        monkeypatch.setenv(EXCEPTION_SHA_ENV, save(record, payload))
    with pytest.raises((ValueError, OSError)):
        assert_execution_date(destination, DAY)
    assert route_dashboard_request("GET", "/api/dashboard/expanded-shadow", destination)[0] == 503


def test_missing_pin_cannot_be_bypassed_by_direct_entrypoints(continuation, monkeypatch):
    _, destination, _ = continuation
    monkeypatch.delenv(EXCEPTION_SHA_ENV)
    for action in (
        lambda: resolve_expanded_source_date(repo_root=destination, explicit_source_date=DAY),
        lambda: run_expanded_shadow_daily(repo_root=destination, source_date=DAY),
        lambda: run_expanded_shadow_pipeline(repo_root=destination, basDd=DAY, market_source=None, investor_source=None),
    ):
        with pytest.raises(ValueError, match="pin"):
            action()
    assert not ExpandedShadowPaths(destination).lock_path.exists()


def test_unreviewed_later_halt_is_unknown():
    result = halts.assess_trading_status("2026-11-11", ("032580", "005930"))
    assert not result.exclusions and result.unknown[0]["ticker"] == "032580"
    assert result.unknown[0]["status"] == "UNKNOWN"


@pytest.fixture
def import_case(continuation, monkeypatch):
    source, destination, record = continuation
    inventory = json.loads((source / "snapshot_hashes.json").read_text(encoding="utf-8"))
    for entry in inventory:
        path = source / entry["path"]
        ticker = path.stem.split("_")[0]
        frame = _investor_frame(ticker, DAY) if path.parent.parent.name == "investor" else _market_frame(ticker, source_date=DAY)
        data = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        path.write_bytes(data)
        entry["sha256"] = sha256(data)
    save(source / "snapshot_hashes.json", inventory)
    evidence = halts.OFFICIAL_HALT_EVIDENCE[("032580", DAY)]
    documents = []
    for receipt, document, _ in evidence.documents:
        body = f"synthetic reviewed original {receipt}".encode()
        (source / "evidence" / f"{receipt}.html").write_bytes(body)
        documents.append((receipt, document, sha256(body)))
    evidence = replace(evidence, documents=tuple(documents))
    monkeypatch.setitem(halts.OFFICIAL_HALT_EVIDENCE, ("032580", DAY), evidence)
    manifest = load(_completed(source))
    manifest["trading_halt_exclusions"] = [evidence.record()]
    save(_completed(source), manifest)
    release = b"synthetic reviewed Unison official release"
    (source / "evidence" / "unison_release.html").write_bytes(release)
    monkeypatch.setattr("scripts.expanded_ready_import.UNISON_RELEASE_SHA256", sha256(release))
    history = "<html>[1/1] [총 7건]" + "".join(
        f'<a href="?rcpNo={receipt}">공시</a>' for receipt in evidence.filing_ids
    ) + "</html>"
    (source / "evidence" / "fidelix_filings_through_20261110.html").write_text(history, encoding="utf-8")
    plan = plan_import(source, destination)
    payload = {**approval("IMPORT_ISOLATED_READY"), **plan.binding,
               "operational_publication": True, "source_inventory": plan.source_inventory}
    path = source.parent / (source.name + "-import-approval.json")
    pin = save(path, payload)
    return source, destination, path, pin, plan


def test_import_plan_apply_idempotent_and_no_performance(import_case):
    source, destination, path, pin, plan = import_case
    before = _hashes(source)
    before_dest = _hashes(destination)
    again = plan_import(source, destination)
    assert again.binding == plan.binding and before_dest == _hashes(destination)
    result = apply_import(source, destination, path, pin)
    assert result["status"] == "IMPORTED"
    paths = ExpandedShadowPaths(destination)
    manifest = load(paths.current_run_path)
    assert manifest["ready_count"] == 573 and manifest["signal_count"] == 5
    assert manifest["new_candidate_count"] == 4 and str(destination) in manifest["ledger_path"]
    ledger = pd.read_csv(paths.signal_ledger_path, dtype=str)
    imported = ledger.loc[ledger.basDd == DAY]
    assert len(ledger) == 6 and len(imported) == 5 and (imported.decision == "CANDIDATE").sum() == 4
    assert (ledger.basDd == "2026-11-06").sum() == 1
    assert apply_import(source, destination, path, pin)["status"] == "ALREADY_IMPORTED"
    with pytest.raises(ValueError, match="Archive"):
        assert_execution_date(destination, DAY)
    assert not list(paths.output_root.glob("*performance*"))
    assert not any(e.get("basDd") == "2026-11-09" and e.get("event_type") == "RUN_COMPLETED"
                   for e in map(json.loads, paths.registry_path.read_text().splitlines()))
    assert before == _hashes(source)


@pytest.mark.parametrize("damage", ["approval", "scope", "write_set", "source", "input_conflict", "date_backwards", "path", "policy_pin"])
def test_import_blocks_changed_binding(import_case, monkeypatch, damage):
    source, destination, path, pin, plan = import_case
    payload = load(path)
    paths = ExpandedShadowPaths(destination)
    if damage == "approval":
        pin = PIN
    elif damage in {"scope", "write_set", "path"}:
        key, value = {"scope": ("performance", True), "write_set": ("write_set_sha256", PIN),
                      "path": ("destination_root", str(destination.parent))}[damage]
        payload[key] = value
        pin = save(path, payload)
    elif damage == "source":
        (source / "snapshot_hashes.json").write_text("[]")
    elif damage == "input_conflict":
        entry = json.loads((source / "snapshot_hashes.json").read_text())[0]
        target = destination / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"conflict")
    elif damage == "date_backwards":
        save(paths.current_run_path, {"basDd": "2026-11-11"})
    else:
        monkeypatch.setenv(EXCEPTION_SHA_ENV, PIN)
    before = _hashes(destination)
    with pytest.raises((ValueError, OSError)):
        apply_import(source, destination, path, pin)
    assert before == _hashes(destination)


CRASH_CODE = r"""
import json,os,sys,time
from pathlib import Path
from src.expanded_shadow_ops import ExpandedShadowPaths,ExpandedRunLock
from src.expanded_publication import write_transaction
root=Path(sys.argv[1]); stage=sys.argv[2]; ready=Path(sys.argv[3])
paths=ExpandedShadowPaths(root)
lock=ExpandedRunLock(paths); lock.acquire('crash-test','2026-11-10')
changes={str(p.relative_to(root)):v for p,v in [
 (paths.signal_ledger_path,b'new ledger'),(paths.registry_path,b'new registry'),
 (paths.run_manifest_path('2026-11-10','crash-test'),b'new manifest'),
 (paths.latest_manifest_path('2026-11-10'),b'new latest'),(paths.current_run_path,b'new current')]}
from src.expanded_continuation import sha256
writes=[{'path':n,'before':sha256((root/n).read_bytes()) if (root/n).exists() else None,'after':sha256(v)} for n,v in changes.items()]
approval=json.dumps({'kind':'IMPORT_ISOLATED_READY','approved':True,'operational_publication':True,'performance':False,
 'destination_root':str(root),'source_date':'2026-11-10','run_id':'crash-test','write_set':writes}).encode()
(ready.parent/'approval-pin').write_text(sha256(approval))
def checkpoint(name):
 if name==stage:
  ready.write_text(name)
  time.sleep(120)
if stage=='INITIAL_PENDING':
 import src.expanded_publication as publication
 original_save=publication._save
 def initial_save(path,value):
  if value['state']=='STAGING' and not path.exists():
   path.parent.mkdir(parents=True,exist_ok=True)
   path.with_name(path.name+'.pending').write_text(json.dumps(value))
   checkpoint('INITIAL_PENDING')
  original_save(path,value)
 publication._save=initial_save
write_transaction(root,changes,day='2026-11-10',run_id='crash-test',approval_sha=sha256(approval),approval_raw=approval,checkpoint=checkpoint)
lock.release()
"""


@pytest.mark.parametrize("stage", ["INITIAL_PENDING", "STAGING", "PREPARED", "WRITE:0", "WRITE:1", "WRITE:2", "WRITE:3", "WRITE:4", "BEFORE_COMMIT"])
def test_killed_subprocess_recovery_restores_exact_bytes(tmp_path, stage):
    root = tmp_path / "synthetic-operator"
    paths = ExpandedShadowPaths(root)
    paths.output_root.mkdir(parents=True)
    paths.signal_ledger_path.write_bytes(b"old ledger")
    paths.registry_path.write_bytes(b"old registry")
    paths.current_run_path.write_bytes(b"old current")
    before = _hashes(root)
    ready = tmp_path / "crash-ready"
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", CRASH_CODE, str(root), stage, str(ready)],
        cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        import time
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), process.communicate(timeout=2)
        pin = (tmp_path / "approval-pin").read_text()
        assert route_dashboard_request("GET", "/api/dashboard/expanded-shadow", root)[0] == 503
        with pytest.raises(ValueError, match="still alive"):
            recover_transaction(root, pin)
        process.kill()
        process.wait(timeout=5)
        assert recover_transaction(root, pin) == "ROLLED_BACK"
        assert recover_transaction(root, pin) == "ROLLED_BACK"
        assert_publication_idle(root)
        after = {name: digest for name, digest in _hashes(root).items() if "publication_transaction" not in name}
        assert before == after
        assert archive_transaction(root, pin) == "ARCHIVED"
        assert_publication_idle(root)
        assert not (paths.output_root / "publication_transaction").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_recovery_rejects_external_change_and_wrong_approval(tmp_path):
    root = tmp_path / "operator"
    paths = ExpandedShadowPaths(root)
    paths.output_root.mkdir(parents=True)
    target = paths.current_run_path
    target.write_bytes(b"before")
    raw = json.dumps({
        "kind": "IMPORT_ISOLATED_READY", "approved": True, "operational_publication": True, "performance": False,
        "destination_root": str(root), "source_date": DAY, "run_id": "test",
        "write_set": [{"path": str(target.relative_to(root)), "before": sha256(b"before"), "after": sha256(b"after")}],
    }).encode()
    pin = sha256(raw)
    with pytest.raises(RuntimeError):
        write_transaction(root, {str(target.relative_to(root)): b"after"}, day=DAY, run_id="test",
                          approval_sha=pin, approval_raw=raw,
                          checkpoint=lambda s: (_ for _ in ()).throw(RuntimeError("failure")) if s == "BEFORE_COMMIT" else None)
    journal = paths.output_root / "publication_transaction" / "active.json"
    payload = load(journal)
    payload["pid"] = 4294967294
    save(journal, payload)
    with pytest.raises(ValueError, match="pin"):
        recover_transaction(root, "b" * 64)
    target.write_bytes(b"external")
    with pytest.raises(ValueError, match="External target"):
        recover_transaction(root, pin)
    assert target.read_bytes() == b"external"


def test_orphan_staging_and_other_endpoints_are_blocked(tmp_path):
    root = tmp_path / "operator"
    (root / "output" / "expanded_shadow" / "publication_transaction").mkdir(parents=True)
    for path in ("/api/dashboard/expanded-shadow", "/api/dashboard/expanded-shadow/partial",
                 "/api/dashboard/expanded-shadow/isolated-ready", "/api/dashboard/expanded-shadow/stored-performance"):
        status, _, body = route_dashboard_request("GET", path, root)
        assert status == 503 and json.loads(body)["status"] == "RECOVERY_REQUIRED"


def stopped_transaction(root: Path, stage: str = "BEFORE_COMMIT"):
    paths = ExpandedShadowPaths(root)
    paths.output_root.mkdir(parents=True)
    changes = {str(path.relative_to(root)): b"after" for path in (paths.signal_ledger_path, paths.current_run_path)}
    for name in changes:
        (root / name).write_bytes(b"before")
    raw = json.dumps({
        "kind": "IMPORT_ISOLATED_READY", "approved": True, "operational_publication": True, "performance": False,
        "destination_root": str(root), "source_date": DAY, "run_id": "test",
        "write_set": [{"path": name, "before": sha256(b"before"), "after": sha256(data)} for name, data in changes.items()],
    }).encode()
    pin = sha256(raw)
    with pytest.raises(RuntimeError):
        write_transaction(root, changes, day=DAY, run_id="test", approval_sha=pin, approval_raw=raw,
                          checkpoint=lambda s: (_ for _ in ()).throw(RuntimeError("failure")) if s == stage else None)
    journal = paths.output_root / "publication_transaction" / "active.json"
    payload = load(journal)
    payload["pid"] = 4294967294
    save(journal, payload)
    return paths, journal, pin


@pytest.mark.parametrize("tamper", ["backup", "staged", "target", "entry_hash", "approval_raw"])
def test_recovery_preflight_is_bound_to_external_approval(tmp_path, tamper):
    root = tmp_path / "operator"
    paths, journal, pin = stopped_transaction(root)
    payload = load(journal)
    if tamper in {"backup", "staged"}:
        (journal.parent / payload["entries"][0][tamper]).write_bytes(b"tampered")
    elif tamper == "target":
        payload["entries"][0]["target"] = str(paths.registry_path.relative_to(root))
        save(journal, payload)
    elif tamper == "entry_hash":
        payload["entries"][0]["before"] = sha256(b"tampered")
        save(journal, payload)
    else:
        payload["approval_raw"] += " "
        save(journal, payload)
    before = _hashes(root)
    with pytest.raises(ValueError):
        recover_transaction(root, pin)
    assert _hashes(root) == before


def test_second_interruption_during_rollback_can_resume(tmp_path):
    root = tmp_path / "operator"
    paths, journal, pin = stopped_transaction(root)
    code = (
        "import os,sys; from pathlib import Path; "
        "from src.expanded_publication import recover_transaction; "
        "recover_transaction(Path(sys.argv[1]),sys.argv[2],"
        "checkpoint=lambda s:os._exit(77) if s.startswith('ROLLBACK:') else None)"
    )
    child = subprocess.run([sys.executable, "-B", "-c", code, str(root), pin],
                           cwd=Path(__file__).parents[1], capture_output=True, timeout=15)
    assert child.returncode == 77, child.stderr
    assert load(journal)["state"] == "RECOVERING"
    assert recover_transaction(root, pin) == "ROLLED_BACK"
    assert paths.signal_ledger_path.read_bytes() == paths.current_run_path.read_bytes() == b"before"


@pytest.mark.parametrize("stage", ["STAGING", "PREPARED"])
@pytest.mark.parametrize("partial_staging", [False, True])
def test_recovery_reinterrupted_immediately_after_recovering_save(tmp_path, stage, partial_staging):
    root = tmp_path / "synthetic-operator"
    paths, journal, pin = stopped_transaction(root, stage)
    original = load(journal)
    if stage == "STAGING" and partial_staging:
        entry = original["entries"][0]
        (journal.parent / entry["backup"]).write_bytes(b"before")
        (journal.parent / entry["staged"]).write_bytes(b"after")
    snapshots = {path.name: path.read_bytes() for path in journal.parent.iterdir() if path != journal}
    code = (
        "import os,sys; from pathlib import Path; import src.expanded_publication as p; "
        "save=p._save\n"
        "def interrupted_save(path,payload):\n"
        " save(path,payload)\n"
        " if payload['state']=='RECOVERING': os._exit(77)\n"
        "p._save=interrupted_save\n"
        "p.recover_transaction(Path(sys.argv[1]),sys.argv[2])\n"
    )
    for _ in range(2):
        child = subprocess.run([sys.executable, "-B", "-c", code, str(root), pin],
                               cwd=Path(__file__).parents[1], capture_output=True, timeout=15)
        assert child.returncode == 77, child.stderr
        recovering = load(journal)
        assert recovering["state"] == "RECOVERING"
        assert recovering["entries"] == original["entries"]
        assert recovering["approval_raw"] == original["approval_raw"]
        assert recovering["approval_sha256"] == pin
    assert recover_transaction(root, pin) == "ROLLED_BACK"
    terminal = journal.read_bytes()
    assert recover_transaction(root, pin) == "ROLLED_BACK"
    assert journal.read_bytes() == terminal
    assert_publication_idle(root)
    assert paths.signal_ledger_path.read_bytes() == paths.current_run_path.read_bytes() == b"before"
    assert all((journal.parent / name).read_bytes() == data for name, data in snapshots.items())


@pytest.mark.parametrize("state", ["STAGING", "RECOVERING"])
@pytest.mark.parametrize("tamper", ["backup", "staged", "target", "entry_hash", "approval_raw"])
def test_staging_recovery_rejects_tampered_snapshot_bytes(tmp_path, state, tamper, capsys):
    root = tmp_path / "synthetic-operator"
    _, journal, pin = stopped_transaction(root, "STAGING")
    payload = load(journal)
    if state == "RECOVERING":
        payload.update(state=state, recovery_origin="STAGING")
    entry = payload["entries"][0]
    if tamper in {"backup", "staged"}:
        (journal.parent / entry[tamper]).write_bytes(b"tampered")
    elif tamper == "target":
        (root / entry["target"]).write_bytes(b"tampered")
    elif tamper == "entry_hash":
        entry["after"] = sha256(b"tampered")
    else:
        payload["approval_raw"] += " "
    save(journal, payload)
    before = _hashes(root)
    with pytest.raises(ContinuationError):
        recover_transaction(root, pin)
    assert main(["recover", "--destination", str(root), "--approval-sha256", pin]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "SAFETY_STOP" and result["error"]
    assert _hashes(root) == before


def test_recovering_without_staging_origin_still_requires_staged_bytes(tmp_path):
    root = tmp_path / "synthetic-operator"
    _, journal, pin = stopped_transaction(root, "STAGING")
    payload = load(journal)
    payload["state"] = "RECOVERING"
    save(journal, payload)
    before = _hashes(root)
    with pytest.raises(ContinuationError, match="Staged recovery hash mismatch"):
        recover_transaction(root, pin)
    assert _hashes(root) == before


def test_repeated_recovery_rejects_changed_terminal_target(tmp_path, capsys):
    root = tmp_path / "synthetic-operator"
    paths, _, pin = stopped_transaction(root, "STAGING")
    assert recover_transaction(root, pin) == "ROLLED_BACK"
    paths.current_run_path.write_bytes(b"external")
    before = _hashes(root)
    assert main(["recover", "--destination", str(root), "--approval-sha256", pin]) == 1
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "SAFETY_STOP" and "terminal publication hash mismatch" in result["error"]
    assert _hashes(root) == before


def test_read_that_straddles_publication_cannot_return_ready(tmp_path, monkeypatch):
    import dashboard.api as api

    def race(*args):
        paths = ExpandedShadowPaths(tmp_path)
        save(paths.output_root / "publication_transaction" / "active.json", {"state": "APPLYING"})
        return 200, {}, b'{"status":"READY"}'

    monkeypatch.setattr(api, "_route_dashboard_request", race)
    status, _, body = api.route_dashboard_request("GET", "/api/dashboard/expanded-shadow", tmp_path)
    assert status == 503 and json.loads(body)["status"] == "RECOVERY_REQUIRED"


def test_import_cli_reports_malformed_operational_current_without_writes(import_case, capsys):
    source, destination, path, pin, _ = import_case
    save(ExpandedShadowPaths(destination).current_run_path, {"basDd": "2026-11-06"})
    before = _hashes(destination)
    result = main(["apply", "--source", str(source), "--destination", str(destination),
                   "--approval", str(path), "--approval-sha256", pin])
    assert result == 1
    assert json.loads(capsys.readouterr().err)["status"] == "BLOCKED"
    assert _hashes(destination) == before

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import socket

import pytest

from dashboard.api import route_dashboard_request
from dashboard.expanded_isolated_ready import ISOLATED_READY_ENDPOINT, ISOLATED_READY_ROOT_ENV, build_isolated_ready
from src.expanded_shadow_universe import load_expanded_universe
from src.expanded_trading_halts import OFFICIAL_HALT_EVIDENCE
from tests.test_expanded_partial_dashboard import partial_root, _hashes, _manifest, _write_rows


DAY = "2026-11-10"
RUN = "isolated-test"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _completed(root: Path) -> Path:
    return root / "output" / "expanded_shadow" / "manifests" / DAY / f"{RUN}.json"


@pytest.fixture
def ready_root(partial_root: Path) -> Path:
    root = partial_root
    universe = load_expanded_universe(root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv")
    manifest = _json(_manifest(root))
    ledger = root / "output" / "expanded_shadow" / "expanded_shadow_signal_ledger.csv"
    with _manifest(root).with_name("signals.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.append(dict(rows[0], stock_code=universe.tickers[6].ticker,
                     stock_name=universe.tickers[6].name, market=universe.tickers[6].market))
    for index, row in enumerate(rows):
        row.update(basDd=DAY, signal_date=DAY, run_id=RUN)
        if index != 3:
            row.update(decision="CANDIDATE", foreign_status="POSITIVE", exclusion_reason="")
    _write_rows(ledger, rows)
    manifest.update(
        status="SUCCESS", basDd=DAY, run_id=RUN, ready_count=573, attempted_ticker_count=573,
        market_success_count=573, investor_success_count=573, quarantine_count=0,
        signal_count=5, new_candidate_count=4, status_counts={"READY": 573, "TRADING_HALT_EXCLUDED": 1},
        unresolved_tickers=[], market_source_date_distribution={DAY: 573}, investor_source_date_distribution={DAY: 573},
        ledger_path=str(ledger), trading_halt_exclusions=[OFFICIAL_HALT_EVIDENCE[("032580", DAY)].record()],
    )
    _save(_completed(root), manifest)
    _save(root / "analysis-once" / "verified_result.json", {
        "status": "READY", "analysis_calls": 1, "operating_publication": "PROHIBITED_BY_OPERATOR",
        "checks": {"verified": True}, "source_date": DAY, "run_id": RUN, "manifest_path": str(_completed(root)),
        "universe": 574, "processed": 573, "halt_excluded": 1, "unknown": 0, "signals": 5, "candidates": 4,
    })
    inventory = []
    for member in universe.tickers:
        if member.ticker == "032580":
            continue
        for kind, suffix in (("market", ""), ("investor", "_investor")):
            path = root / "data" / "expanded_shadow" / kind / DAY / f"{member.ticker}{suffix}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Date\n{DAY}\n", encoding="utf-8")
            inventory.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    _save(root / "snapshot_hashes.json", inventory)
    _save(root / "evidence" / "unison_current_status.json", {
        "itemCode": "018000", "localTradedAt": f"{DAY}T15:30:00+09:00",
        "tradableStatusUpdatedAt": f"{DAY}T08:00:00+09:00",
        "tradeStopType": {"code": "1"}, "tradableStatus": "tradable",
    })
    return root


def test_completed_ready_read_only_no_network(ready_root: Path, monkeypatch):
    before = _hashes(ready_root)
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Network forbidden"))
    board = build_isolated_ready(ready_root, ready_root.parent / "operational")
    assert board["source_date"] == DAY and board["scope"] == "ISOLATED" and board["read_only"]
    assert not board["operational_published"] and not board["promoted"]
    assert [board[key] for key in ("processed", "universe", "signals", "candidates", "unknown")] == [573, 574, 5, 4, 0]
    assert board["excluded"][0]["ticker"] == "032580" and board["active"][0]["status"] == "ACTIVE"
    assert str(ready_root) not in json.dumps(board)
    assert before == _hashes(ready_root)


@pytest.mark.parametrize(("field", "value"), [
    ("status", "PARTIAL"), ("basDd", "2026-11-09"), ("run_id", "other"),
    ("ready_count", 572), ("signal_count", 4), ("new_candidate_count", 3),
    ("ledger_path", r"C:\outside\ledger.csv"), ("universe_sha256", "bad"),
    ("investor_source_date_distribution", {"2026-11-09": 573}),
    ("unresolved_tickers", [{"ticker": "018000", "status": "UNKNOWN"}]),
    ("trading_halt_exclusions", []),
])
def test_invalid_manifest(ready_root: Path, field: str, value: object):
    manifest = _json(_completed(ready_root))
    manifest[field] = value
    _save(_completed(ready_root), manifest)
    with pytest.raises(ValueError):
        build_isolated_ready(ready_root, ready_root.parent / "operational")


@pytest.mark.parametrize(("field", "value"), [
    ("stock_code", "032580"), ("stock_code", "079160"), ("stock_name", "Wrong"),
    ("run_id", "other"), ("signal_date", "2026-11-09"), ("signal_score", "nan"),
    ("decision", "WAIT"), ("foreign_status", "NEGATIVE"),
])
def test_invalid_signals(ready_root: Path, field: str, value: str):
    path = Path(_json(_completed(ready_root))["ledger_path"])
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0][field] = value
    _write_rows(path, rows)
    with pytest.raises(ValueError):
        build_isolated_ready(ready_root, ready_root.parent / "operational")


@pytest.mark.parametrize("target", ["input", "inventory", "report", "status", "report_path"])
def test_bad_proof_rejected(ready_root: Path, target: str):
    if target in {"input", "inventory"}:
        path = ready_root / "snapshot_hashes.json"
        inventory = json.loads(path.read_text(encoding="utf-8"))
        if target == "input":
            (ready_root / inventory[0]["path"]).write_text("tampered", encoding="utf-8")
        else:
            inventory[0]["path"] = r"..\outside.csv"
            _save(path, inventory)
    elif target == "status":
        path = ready_root / "evidence" / "unison_current_status.json"
        _save(path, {**_json(path), "localTradedAt": "2026-11-09T15:30:00+09:00"})
    else:
        path = ready_root / "analysis-once" / "verified_result.json"
        report = _json(path)
        if target == "report":
            report["checks"]["verified"] = False
        else:
            report["run_id"] = r"..\..\outside"
        _save(path, report)
    with pytest.raises(ValueError):
        build_isolated_ready(ready_root, ready_root.parent / "operational")


def test_operational_root_rejected(ready_root: Path):
    for operational in (ready_root, ready_root.parent, ready_root / "nested"):
        with pytest.raises(ValueError, match="overlap"):
            build_isolated_ready(ready_root, operational)


def test_api_configuration_and_methods(ready_root: Path, monkeypatch):
    operational = ready_root.parent / "operational"
    monkeypatch.delenv(ISOLATED_READY_ROOT_ENV, raising=False)
    assert route_dashboard_request("GET", ISOLATED_READY_ENDPOINT, operational)[0] == 503
    monkeypatch.setenv(ISOLATED_READY_ROOT_ENV, str(ready_root))
    status, headers, body = route_dashboard_request("GET", ISOLATED_READY_ENDPOINT, operational)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert json.loads(body)["operational_published"] is False
    assert route_dashboard_request("GET", ISOLATED_READY_ENDPOINT + "?date=other", operational)[0] == 400
    assert route_dashboard_request("POST", ISOLATED_READY_ENDPOINT, operational)[0] == 405
    monkeypatch.setenv(ISOLATED_READY_ROOT_ENV, "missing-relative-root")
    assert route_dashboard_request("GET", ISOLATED_READY_ENDPOINT, operational)[0] == 422

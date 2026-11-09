from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import socket

import pytest

from dashboard.api import route_dashboard_request
from dashboard.expanded_partial import (
    PARTIAL_ENDPOINT, PARTIAL_VALIDATION_ENDPOINT, PARTIAL_ROOT_ENV,
    build_expanded_partial_board, build_expanded_partial_validation,
)
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.expanded_shadow_ops import ExpandedRunManifest
from src.expanded_shadow_universe import load_expanded_universe
from src.expanded_trading_halts import OFFICIAL_HALT_EVIDENCE


SOURCE_DATE = "2026-11-09"
RUN_ID = "partial-test"


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def partial_root(tmp_path: Path) -> Path:
    universe_path = tmp_path / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    universe_path.parent.mkdir(parents=True)
    tickers = ["032580", "018000", "443670", "079160", "181710", "006650"]
    tickers += [f"{value:06d}" for value in range(100000, 100568)]
    with universe_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "name", "market", "source_basDd"])
        for index, ticker in enumerate(tickers):
            writer.writerow([ticker, f"Stock {ticker}", "KOSPI" if index < 267 else "KOSDAQ", "2026-09-17"])
    universe = load_expanded_universe(universe_path)
    directory = tmp_path / "output" / "expanded_shadow" / "partial" / SOURCE_DATE / RUN_ID
    directory.mkdir(parents=True)
    rows = []
    for index, ticker in enumerate(tickers[2:6]):
        member = universe.tickers[index + 2]
        row = dict.fromkeys(LEDGER_FIELDS, "")
        row.update(
            basDd=SOURCE_DATE, stock_code=ticker, stock_name=member.name, market=member.market,
            signal_date=SOURCE_DATE, signal_price="100", raw_score="52", signal_score="80",
            signal_type="BUY_WATCH", foreign_status="POSITIVE" if index < 2 else "NEGATIVE",
            decision="CANDIDATE" if index < 2 else "EXCLUDED",
            exclusion_reason="" if index < 2 else "FOREIGN_NEGATIVE", engine_version="v0.1",
            source_commit="test", run_id=RUN_ID, created_at="2026-11-09T12:00:00+00:00",
        )
        rows.append(row)
    _write_rows(directory / "signals.csv", rows)
    manifest = ExpandedRunManifest(
        run_id=RUN_ID, basDd=SOURCE_DATE, started_at="2026-11-09T12:00:00+00:00",
        finished_at="2026-11-09T12:01:00+00:00", runtime_seconds=60, status="PARTIAL",
        canonical_universe_count=574, attempted_ticker_count=572,
        market_success_count=572, investor_success_count=572, ready_count=572,
        quarantine_count=1, signal_count=4, new_candidate_count=2,
        status_counts={"READY": 572, "TRADING_HALT_EXCLUDED": 1, "UNKNOWN": 1},
        market_source_date_distribution={SOURCE_DATE: 572},
        investor_source_date_distribution={SOURCE_DATE: 572},
        universe_sha256=universe.sha256, source_commit="test",
        ledger_path=str(directory / "signals.csv"),
        trading_halt_exclusions=[OFFICIAL_HALT_EVIDENCE[("032580", SOURCE_DATE)].record()],
        unresolved_tickers=[{"ticker": "018000", "status": "UNKNOWN", "source_date": SOURCE_DATE}],
    )
    (directory / "manifest.json").write_text(json.dumps(asdict(manifest)), encoding="utf-8")
    return tmp_path


def _manifest(root: Path) -> Path:
    return root / "output" / "expanded_shadow" / "partial" / SOURCE_DATE / RUN_ID / "manifest.json"


def _hashes(root: Path) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_partial_accounting_read_only_and_no_network(partial_root: Path, monkeypatch):
    before = _hashes(partial_root)
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("HTTP forbidden"))
    board = build_expanded_partial_board(partial_root)
    assert board["status"] == "PARTIAL" and board["read_only"]
    run = board["items"][0]
    assert {key: run[key] for key in ("universe", "attempted", "ready", "suspended", "unknown", "signals", "candidates")} == {
        "universe": 574, "attempted": 572, "ready": 572, "suspended": 1, "unknown": 1, "signals": 4, "candidates": 2,
    }
    assert run["validation_status"] == "NOT_RUN" and not run["promoted"]
    assert run["excluded_tickers"] == ["032580"] and run["unknown_tickers"] == ["018000"]
    assert {row["ticker"] for row in run["records"] if row["decision"] == "CANDIDATE"} == {"443670", "079160"}
    assert before == _hashes(partial_root)


@pytest.mark.parametrize(("field", "value"), [
    ("status", "SUCCESS"), ("basDd", "2026-11-06"), ("run_id", "other-run"),
    ("canonical_universe_count", 573), ("ready_count", 571), ("signal_count", 5),
    ("new_candidate_count", 3), ("universe_sha256", "bad"), ("ledger_path", r"C:\outside\signals.csv"),
    ("unresolved_tickers", []), ("unresolved_tickers", [{"ticker": "443670", "status": "UNKNOWN"}]),
    ("unresolved_tickers", None), ("status_counts", []), ("ready_count", True),
    ("signal_count", 4.0), ("market_source_date_distribution", {"2026-11-06": 572}),
    ("investor_source_date_distribution", {SOURCE_DATE: 571}),
    ("trading_halt_exclusions", [{}]), ("attempted_ticker_count", 573),
])
@pytest.mark.parametrize("builder", [build_expanded_partial_board, build_expanded_partial_validation])
def test_manifest_mismatch_rejected(partial_root: Path, field: str, value: object, builder):
    path = _manifest(partial_root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        builder(partial_root)


@pytest.mark.parametrize(("field", "value"), [
    ("basDd", "2026-11-06"), ("signal_date", "2026-11-06"), ("run_id", "other"),
    ("stock_code", "018000"), ("stock_code", "032580"), ("stock_code", "999999"),
    ("stock_code", "079160"), ("stock_name", "Wrong"), ("market", "OTHER"),
    ("source_commit", "other"), ("decision", "WAIT"), ("decision", "EXCLUDED"),
    ("foreign_status", "NEGATIVE"), ("signal_score", "nan"), ("raw_score", ""),
])
@pytest.mark.parametrize("builder", [build_expanded_partial_board, build_expanded_partial_validation])
def test_csv_mismatch_rejected(partial_root: Path, field: str, value: str, builder):
    path = _manifest(partial_root).with_name("signals.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0][field] = value
    _write_rows(path, rows)
    with pytest.raises(ValueError):
        builder(partial_root)


@pytest.mark.parametrize("filename", ["manifest.json", "signals.csv"])
def test_unreadable_or_bad_input_rejected(partial_root: Path, filename: str):
    _manifest(partial_root).with_name(filename).write_bytes(b"not valid data")
    with pytest.raises(ValueError):
        build_expanded_partial_board(partial_root)


def test_missing_signal_rejected(partial_root: Path):
    _manifest(partial_root).with_name("signals.csv").unlink()
    with pytest.raises(ValueError, match="signals.csv"):
        build_expanded_partial_board(partial_root)


def test_path_escape_rejected(partial_root: Path):
    with pytest.raises(ValueError, match="Redirected"):
        build_expanded_partial_board(partial_root / "..")


def test_redirected_input_rejected(partial_root: Path, monkeypatch):
    signal_path = _manifest(partial_root).with_name("signals.csv")
    resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve",
        lambda path, *args, **kwargs: (
            partial_root.parent / "outside.csv" if path == signal_path
            else resolve(path, *args, **kwargs)
        ),
    )
    with pytest.raises(ValueError, match="Redirected"):
        build_expanded_partial_board(partial_root)


def test_missing_root_is_explicit(tmp_path: Path):
    with pytest.raises(ValueError, match="does not exist"):
        build_expanded_partial_board(tmp_path / "absent")
    assert build_expanded_partial_board(tmp_path)["status"] == "MISSING"
    with pytest.raises(ValueError, match="absolute"):
        build_expanded_partial_board(Path("relative-root"))


@pytest.mark.parametrize("endpoint", [PARTIAL_ENDPOINT, PARTIAL_VALIDATION_ENDPOINT])
def test_api_is_get_only_and_explicitly_configured(partial_root: Path, tmp_path: Path, monkeypatch, endpoint):
    monkeypatch.delenv(PARTIAL_ROOT_ENV, raising=False)
    assert route_dashboard_request("GET", endpoint, tmp_path)[0] == 503
    monkeypatch.setenv(PARTIAL_ROOT_ENV, str(partial_root))
    assert route_dashboard_request("POST", endpoint, tmp_path)[0] == 405
    assert route_dashboard_request("GET", endpoint + "?path=outside", tmp_path)[0] == 400
    status, headers, body = route_dashboard_request("GET", endpoint, tmp_path)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert json.loads(body)["items"][0]["signals"] == 4
    _manifest(partial_root).with_name("signals.csv").unlink()
    status, _, body = route_dashboard_request("GET", endpoint, tmp_path)
    assert status == 422 and json.loads(body)["status"] == "MALFORMED"


def test_validation_reuses_reader_without_performance_io(partial_root: Path, monkeypatch):
    import dashboard.expanded_partial as partial

    completed = partial_root / "output" / "expanded_shadow" / "expanded_shadow_run.json"
    completed.write_text("invalid completed data must not be used", encoding="utf-8")
    performance = completed.with_name("expanded_candidate_performance_ledger.csv")
    performance.write_text("invalid performance data must not be used", encoding="utf-8")
    before = _hashes(partial_root)
    original_reader = partial.build_expanded_partial_board
    calls = []

    def reader(root: Path):
        calls.append(root)
        return original_reader(root)

    monkeypatch.setattr(partial, "build_expanded_partial_board", reader)
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("HTTP forbidden"))
    board = partial.build_expanded_partial_validation(partial_root)
    assert calls == [partial_root]
    assert board["view"] == "PARTIAL_VALIDATION"
    run = board["items"][0]
    assert run["status"] == "PARTIAL" and run["validation_status"] == "NOT_RUN"
    assert len(run["records"]) == 4 and run["candidates"] == 2 and not run["promoted"]
    for record in run["records"]:
        assert set(record["forward_evaluation"]) == {"5D", "10D", "20D"}
        for value in record["forward_evaluation"].values():
            assert value == {
                "status": "UNCONFIRMED", "reason": "NO_VERIFIED_FORWARD_DATA",
                "return_pct": None, "benchmark_pct": None, "excess_pct": None,
            }
    assert before == _hashes(partial_root)
    plain = original_reader(partial_root)
    assert all("forward_evaluation" not in record for record in plain["items"][0]["records"])


def test_validation_missing_results(tmp_path: Path):
    payload = build_expanded_partial_validation(tmp_path)
    assert payload["status"] == "MISSING" and payload["items"] == []

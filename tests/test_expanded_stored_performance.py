import csv
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboard.api import route_dashboard_request
import dashboard.expanded_stored_performance as performance_module
from dashboard.expanded_stored_performance import STORED_PERFORMANCE_ENDPOINT, build_stored_performance
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_performance_analysis import AnalysisIntegrityError
from src.expanded_shadow_ledger import LEDGER_FIELDS


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(performance_module, "datetime", FixedDatetime)


def write_rows(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def ledgers(tmp_path):
    directory = tmp_path / "output" / "expanded_shadow"
    directory.mkdir(parents=True)
    signals, performance = [], []
    for ticker, day in [("000001", "2026-09-10"), ("115180", "2026-10-01"), ("000002", "2027-01-04")]:
        signal = dict.fromkeys(LEDGER_FIELDS, "")
        signal.update(basDd=day, stock_code=ticker, stock_name=ticker, market="KOSDAQ",
                      signal_date=day, signal_price="100", signal_score="80", foreign_status="NEUTRAL",
                      decision="CANDIDATE", engine_version="v0.1", run_id="run", source_commit="abc", created_at="created")
        row = dict.fromkeys(PERFORMANCE_FIELDS, "")
        row.update(source_basDd=day, ticker=ticker, stock_name=ticker, market="KOSDAQ",
                   signal_date=day, entry_price="100", signal_score="80", foreign_status="NEUTRAL",
                   engine_version="v0.1", source_run_id="run", source_commit="abc", source_created_at="created",
                   registered_at="registered", tracking_status="OPEN",
                   return_5d="10", benchmark_5d="2", excess_5d="8")
        signals.append(signal)
        performance.append(row)
    write_rows(directory / "expanded_shadow_signal_ledger.csv", LEDGER_FIELDS, signals)
    write_rows(directory / "expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, performance)
    return tmp_path, performance


def test_stored_summary_holds_conflicts_and_never_fetches_or_writes(ledgers, monkeypatch):
    root, _ = ledgers
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("Network forbidden"))
    result = build_stored_performance(root)
    five, ten, twenty = result["horizons"]
    assert five["evaluated"] == 1 and five["average_return"] == 10 and five["win_rate"] == 100
    assert five["not_due"] == 1 and five["conflict_held"] == 1
    assert ten["unconfirmed"] == twenty["unconfirmed"] == 1
    assert ten["average_return"] is None and ten["win_rate"] is None
    assert result["conflicts"][0]["ticker"] == "115180"
    assert result["conflicts"][0]["conflict_horizon"] == 5
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in before.items())


@pytest.mark.parametrize("field,value", [
    ("source_run_id", "wrong"), ("entry_price", "99"), ("return_5d", "nan"),
    ("excess_5d", "7"), ("tracking_status", "BROKEN"),
])
def test_malformed_evidence_is_not_reported_as_performance(ledgers, field, value):
    root, rows = ledgers
    rows[0][field] = value
    write_rows(root / "output" / "expanded_shadow" / "expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, rows)
    with pytest.raises(AnalysisIntegrityError):
        build_stored_performance(root)
    status, _, body = route_dashboard_request("GET", STORED_PERFORMANCE_ENDPOINT, root)
    assert status == 422 and json.loads(body)["status"] == "MALFORMED"


def test_missing_source_and_duplicate_identity_fail_closed(ledgers):
    root, rows = ledgers
    path = root / "output" / "expanded_shadow" / "expanded_candidate_performance_ledger.csv"
    write_rows(path, PERFORMANCE_FIELDS, rows + rows[:1])
    with pytest.raises(AnalysisIntegrityError, match="Duplicate"):
        build_stored_performance(root)
    path.unlink()
    assert route_dashboard_request("GET", STORED_PERFORMANCE_ENDPOINT, root)[0] == 422


def test_missing_candidate_join_is_an_error_not_an_incomplete_success(ledgers):
    root, rows = ledgers
    write_rows(root / "output" / "expanded_shadow" / "expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, rows[:1])
    with pytest.raises(AnalysisIntegrityError, match="missing stored performance"):
        build_stored_performance(root)


def test_api_is_read_only_no_cache_and_rejects_queries(ledgers):
    root, _ = ledgers
    status, headers, body = route_dashboard_request("GET", STORED_PERFORMANCE_ENDPOINT, root)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert json.loads(body)["read_only"]
    assert route_dashboard_request("POST", STORED_PERFORMANCE_ENDPOINT, root)[0] == 405
    assert route_dashboard_request("GET", STORED_PERFORMANCE_ENDPOINT + "?date=2026-11-09", root)[0] == 400

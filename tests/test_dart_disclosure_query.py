"""Read-only query tests using the existing accumulator and persisted evidence."""

from __future__ import annotations

import json
import socket
import sqlite3

import pytest

from scripts.dart_disclosure_accumulate import ROOT, STORE_RELATIVE, run_accumulation
from scripts import dart_disclosure_query as cli

SOURCE = ROOT / "output" / "dart_disclosures" / (
    "20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Queries must not access the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "source.json"
    path.write_bytes(SOURCE.read_bytes())
    run_accumulation(path, tmp_path)
    return tmp_path / STORE_RELATIVE


@pytest.mark.parametrize("ticker", ["005930", "000720"])
def test_company_fields_order_and_no_writes(store, ticker, tmp_path):
    before = store.read_bytes()
    files = sorted(str(p) for p in tmp_path.rglob("*"))
    report = cli.query_disclosures(store, ticker=ticker)
    assert report["query_count"] > 0
    assert report["matching_count"] == report["query_count"]
    assert report["total_count"] == 168
    rows = report["disclosures"]
    assert all(row["ticker"] == ticker for row in rows)
    assert rows == sorted(rows, key=lambda row: (row["rcept_dt"], row["rcept_no"]), reverse=True)
    for row in rows:
        assert row["report_nm"] and row["importance"]
        assert type(row["is_correction"]) is bool
        assert row["original_url"].endswith(row["rcept_no"])
    assert report["last_success_kind"] == "ACCUMULATION_PROCESSING_NOT_COLLECTION"
    assert report["last_collection_success_at"] == json.loads(SOURCE.read_bytes())["collected_at"]
    assert report["collection_time_status"] == "VERIFIED_STORED_SOURCE"
    assert store.read_bytes() == before
    assert sorted(str(p) for p in tmp_path.rglob("*")) == files


def test_high_counts_limit_and_order(store):
    report = cli.query_disclosures(store, high=True, limit=3)
    assert report["query_count"] == 3
    assert report["matching_count"] == 41
    rows = report["disclosures"]
    assert all(row["importance"] == "HIGH" for row in rows)
    assert rows == sorted(rows, key=lambda row: (row["rcept_dt"], row["rcept_no"]), reverse=True)
    assert cli.query_disclosures(store, high=True, limit=1000)["query_count"] == 41


def test_no_matches_is_not_a_claim_of_no_live_disclosures(store):
    result = cli.query_disclosures(store, ticker="999999")
    assert result["query_count"] == result["matching_count"] == 0
    assert result["last_collection_success_at"]
    assert "no collection performed" in result["freshness_note"]


@pytest.mark.parametrize("kwargs", [
    {}, {"ticker": "005930", "high": True}, {"ticker": "5930"},
    {"ticker": "' OR 1"}, {"ticker": "005930", "limit": 0},
    {"high": True, "limit": -1}, {"high": True, "limit": True},
])
def test_invalid_query_is_explicit_without_creating_store(tmp_path, kwargs):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(cli.QueryError):
        cli.query_disclosures(path, **kwargs)
    assert not path.exists()


def test_missing_database_not_created(tmp_path, capsys):
    path = tmp_path / "missing.sqlite3"
    assert cli.main(["--store", str(path), "--high"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "FAIL"
    assert not path.exists()


def test_missing_source_does_not_invent_collection_time(store, tmp_path):
    (tmp_path / "source.json").unlink()
    result = cli.query_disclosures(store, high=True)
    assert result["query_count"] > 0
    assert result["last_collection_success_at"] is None
    assert result["collection_time_status"] == "SOURCE_FILE_NOT_FOUND"
    assert result["last_success_at"]


def test_tampered_source_is_an_explicit_error(store, tmp_path, capsys):
    with (tmp_path / "source.json").open("ab") as handle:
        handle.write(b"\n")
    assert cli.main(["--store", str(store), "--high"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "SOURCE_HASH_MISMATCH"


def test_schema_and_count_failures_are_explicit(store, capsys):
    with sqlite3.connect(store) as connection:
        connection.execute("PRAGMA user_version = 99")
    assert cli.main(["--store", str(store), "--high"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "UNSUPPORTED_STORE_VERSION"
    with sqlite3.connect(store) as connection:
        connection.execute("PRAGMA user_version = 1")
        connection.execute("DELETE FROM disclosures WHERE rcept_no = (SELECT MIN(rcept_no) FROM disclosures)")
    assert cli.main(["--store", str(store), "--high"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "STORE_COUNT_MISMATCH"


def test_cli_json_result_and_required_mode(store, capsys):
    assert cli.main(["--store", str(store), "--ticker", "000720", "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["query_count"] == 1
    for args in ([], ["--ticker", "005930", "--high"]):
        with pytest.raises(SystemExit):
            cli.main(args)


@pytest.mark.parametrize("stamp,status,error", [
    (None, "NOT_RECORDED_IN_SOURCE", None),
    ("2026-10-20T13:43:47", None, "INVALID_COLLECTION_TIME"),
    (123, None, "INVALID_COLLECTION_TIME"),
])
def test_collection_time_is_never_inferred(tmp_path, stamp, status, error):
    source = json.loads(SOURCE.read_bytes())
    source["collected_at"] = stamp
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    run_accumulation(path, tmp_path)
    if error:
        with pytest.raises(cli.QueryError, match=error):
            cli.query_disclosures(tmp_path / STORE_RELATIVE, high=True)
    else:
        result = cli.query_disclosures(tmp_path / STORE_RELATIVE, high=True)
        assert result["last_collection_success_at"] is None
        assert result["collection_time_status"] == status


def test_empty_successful_store_can_be_queried(tmp_path):
    source = json.loads(SOURCE.read_bytes())
    for company in source["companies"]:
        company.update(status="NO_DATA", disclosures=[])
    source.update(disclosure_count=0, correction_count=0, companies_with_disclosures=0)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    run_accumulation(path, tmp_path)
    result = cli.query_disclosures(tmp_path / STORE_RELATIVE, high=True)
    assert result["query_count"] == result["matching_count"] == result["total_count"] == 0
    assert result["last_collection_success_at"]

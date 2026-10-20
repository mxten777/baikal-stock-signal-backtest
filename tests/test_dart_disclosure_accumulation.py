"""Offline accumulation checks; all writes stay in pytest temporary directories."""

from __future__ import annotations

import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from scripts import dart_disclosure_accumulate as cli
from src.dart_disclosure_classification import ClassificationError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access forbidden in accumulation tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def source(receipts=("20261020000001",)):
    rows = [{
        "rcept_no": receipt, "ticker": "005930", "corp_code": "00126380",
        "report_nm": "사업보고서", "rcept_dt": "2026-10-20", "rcept_time": None,
        "rcept_time_source": "NOT_PROVIDED_BY_LIST_API",
        "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "is_correction": False, "correction_type": None,
    } for receipt in receipts]
    return {
        "verdict": "PASS", "collection_complete": True, "scope": "all",
        "uncollected_count": 0, "universe_count": 1, "target_count": 1,
        "disclosure_count": len(rows), "correction_count": 0,
        "companies_with_disclosures": int(bool(rows)),
        "start_date": "2026-10-18", "end_date": "2026-10-20",
        "companies": [{
            "ticker": "005930", "name": "Samsung", "corp_code": "00126380",
            "market": "KOSPI", "status": "SUCCESS" if rows else "NO_DATA",
            "disclosures": rows,
        }],
    }


def write_source(root, data):
    path = root / "collection.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def stored(root):
    with sqlite3.connect(root / cli.STORE_RELATIVE) as connection:
        rows = connection.execute(
            "SELECT * FROM disclosures ORDER BY rcept_no"
        ).fetchall()
        summary = json.loads(connection.execute(
            "SELECT summary_json FROM last_success WHERE id = 1"
        ).fetchone()[0])
    return rows, summary


def test_same_source_twice_and_only_new_receipts_are_added(tmp_path):
    data = source()
    path = write_source(tmp_path, data)
    raw = path.read_bytes()
    first = cli.run_accumulation(path, tmp_path)
    original_rows, saved = stored(tmp_path)
    assert (first["new_count"], first["duplicate_count"], first["total_count"]) == (1, 0, 1)
    assert saved == first
    second = cli.run_accumulation(path, tmp_path)
    assert (second["new_count"], second["duplicate_count"], second["total_count"]) == (0, 1, 1)
    assert stored(tmp_path)[0] == original_rows
    assert path.read_bytes() == raw
    path = write_source(tmp_path, source(("20261020000001", "20261020000002")))
    third = cli.run_accumulation(path, tmp_path)
    assert (third["new_count"], third["duplicate_count"], third["total_count"]) == (1, 1, 2)
    assert stored(tmp_path)[1] == third
    assert third["last_success_at"] >= second["last_success_at"] >= first["last_success_at"]
    payload = json.loads(original_rows[0][1])
    assert payload["classification"]["category"] == "EARNINGS"
    assert payload["original_reviewed"] is False


@pytest.mark.parametrize("change", [
    {"verdict": "FAIL"}, {"verdict": "BLOCKED"},
    {"collection_complete": False}, {"scope": "sample"}, {"disclosure_count": 999},
])
def test_invalid_collection_preserves_store_and_last_success(tmp_path, change):
    path = write_source(tmp_path, source())
    cli.run_accumulation(path, tmp_path)
    before = stored(tmp_path)
    database_bytes = (tmp_path / cli.STORE_RELATIVE).read_bytes()
    write_source(tmp_path, {**source(), **change})
    with pytest.raises(ClassificationError):
        cli.run_accumulation(path, tmp_path)
    assert stored(tmp_path) == before
    assert (tmp_path / cli.STORE_RELATIVE).read_bytes() == database_bytes
    write_source(tmp_path, source(("20261020000002",)))
    assert cli.run_accumulation(path, tmp_path)["total_count"] == 2


def test_failed_first_classification_does_not_create_store(tmp_path):
    path = write_source(tmp_path, {**source(), "verdict": "FAIL"})
    with pytest.raises(ClassificationError):
        cli.run_accumulation(path, tmp_path)
    assert not (tmp_path / cli.STORE_RELATIVE).exists()


def test_database_failure_rolls_back_insertions_and_timestamp_then_retries(tmp_path):
    path = write_source(tmp_path, source())
    cli.run_accumulation(path, tmp_path)
    before = stored(tmp_path)
    with sqlite3.connect(tmp_path / cli.STORE_RELATIVE) as connection:
        connection.execute(
            "CREATE TRIGGER fail_summary BEFORE UPDATE ON last_success "
            "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
        )
    write_source(tmp_path, source(("20261020000001", "20261020000002")))
    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        cli.run_accumulation(path, tmp_path)
    assert stored(tmp_path) == before
    with sqlite3.connect(tmp_path / cli.STORE_RELATIVE) as connection:
        connection.execute("DROP TRIGGER fail_summary")
    assert cli.run_accumulation(path, tmp_path)["new_count"] == 1


def test_conflict_rolls_back_other_new_receipts(tmp_path):
    path = write_source(tmp_path, source())
    cli.run_accumulation(path, tmp_path)
    before = stored(tmp_path)
    data = source(("20261020000002", "20261020000001"))
    data["companies"][0]["disclosures"][1]["report_nm"] = "Changed title"
    write_source(tmp_path, data)
    with pytest.raises(cli.AccumulationError, match="CONFLICTING_STORED_RECEIPT"):
        cli.run_accumulation(path, tmp_path)
    assert stored(tmp_path) == before


def test_duplicates_within_source_and_empty_complete_collection(tmp_path):
    path = write_source(tmp_path, source(("20261020000001", "20261020000001")))
    result = cli.run_accumulation(path, tmp_path)
    assert (result["input_count"], result["new_count"], result["duplicate_count"]) == (2, 1, 1)
    write_source(tmp_path, source(()))
    result = cli.run_accumulation(path, tmp_path)
    assert (result["new_count"], result["duplicate_count"], result["total_count"]) == (0, 0, 1)


def test_correction_is_a_separate_receipt_without_guessing_original(tmp_path):
    data = source(("20261020000001", "20261020000002"))
    correction = data["companies"][0]["disclosures"][1]
    correction.update(report_nm="[기재정정]사업보고서",
                      is_correction=True, correction_type="기재정정")
    data["correction_count"] = 1
    result = cli.run_accumulation(write_source(tmp_path, data), tmp_path)
    assert result["total_count"] == 2
    corrected = json.loads(stored(tmp_path)[0][1][1])
    assert corrected["original_receipt_no"] is None
    assert corrected["original_relationship"] == "NOT_ESTABLISHED"


def test_concurrent_runs_do_not_double_count(tmp_path):
    path = write_source(tmp_path, source())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: cli.run_accumulation(path, tmp_path), range(2),
        ))
    assert sorted(result["new_count"] for result in results) == [0, 1]
    assert len(stored(tmp_path)[0]) == 1


def test_cli_reuses_live_collector_and_classifies_its_result(tmp_path, monkeypatch, capsys):
    path = write_source(tmp_path, source())
    calls = []
    def collect(root, start, end, *, scope):
        calls.append((root, start, end, scope))
        return source(), path
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "run_collection", collect)
    assert cli.main(["--end-date", "2026-10-20", "--lookback-days", "2"]) == 0
    assert calls == [(tmp_path, date(2026, 10, 19), date(2026, 10, 20), "all")]
    assert json.loads(capsys.readouterr().out)["total_count"] == 1


@pytest.mark.parametrize("verdict,exit_code", [("FAIL", 1), ("BLOCKED", 2)])
def test_live_collection_failure_never_classifies_or_writes(
    tmp_path, monkeypatch, capsys, verdict, exit_code,
):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "run_collection", lambda *a, **k: (
        {"verdict": verdict, "blocked_reason": "LIVE_COLLECTION_UNAVAILABLE"},
        tmp_path / "failed.json",
    ))
    monkeypatch.setattr(cli, "run_classification",
                        lambda *a, **k: pytest.fail("Must not classify failed collection"))
    assert cli.main([]) == exit_code
    assert json.loads(capsys.readouterr().out)["verdict"] == verdict
    assert not (tmp_path / cli.STORE_RELATIVE).exists()


def test_cli_offline_errors_and_success_are_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    path = write_source(tmp_path, source())
    monkeypatch.setattr(cli, "run_collection",
                        lambda *a, **k: pytest.fail("Offline mode must not collect"))
    assert cli.main(["--input", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["new_count"] == 1
    write_source(tmp_path, {**source(), "disclosure_count": 99})
    assert cli.main(["--input", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "SOURCE_COUNT_MISMATCH"
    path.write_text("{", encoding="utf-8")
    assert cli.main(["--input", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "JSONDecodeError"
    assert len(stored(tmp_path)[0]) == 1


def test_classification_nonpass_never_creates_store(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_classification",
                        lambda *a, **k: ({"verdict": "FAIL"}, tmp_path / "failed.json"))
    with pytest.raises(cli.AccumulationError, match="CLASSIFICATION_NOT_PASS"):
        cli.run_accumulation(tmp_path / "source.json", tmp_path)
    assert not (tmp_path / cli.STORE_RELATIVE).exists()


def test_classification_output_failure_preserves_store(tmp_path, monkeypatch):
    path = write_source(tmp_path, source())
    cli.run_accumulation(path, tmp_path)
    before = stored(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("injected classification output failure")
    monkeypatch.setattr(cli, "run_classification", fail)
    with pytest.raises(OSError):
        cli.run_accumulation(path, tmp_path)
    assert stored(tmp_path) == before


def test_actual_168_receipts_remain_idempotent(tmp_path):
    path = cli.ROOT / "output" / "dart_disclosures" / (
        "20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json"
    )
    first = cli.run_accumulation(path, tmp_path)
    second = cli.run_accumulation(path, tmp_path)
    assert (first["new_count"], first["total_count"]) == (168, 168)
    assert (second["new_count"], second["duplicate_count"], second["total_count"]) == (0, 168, 168)
    data = json.loads(path.read_bytes())
    company = next(company for company in data["companies"] if company["disclosures"])
    # Synthetic extra receipt only; the real source file is never changed.
    extra = {**company["disclosures"][0], **source(("20261020999999",))["companies"][0]["disclosures"][0]}
    extra.update(ticker=company["ticker"], corp_code=company["corp_code"])
    company["disclosures"].append(extra)
    data["disclosure_count"] += 1
    augmented = write_source(tmp_path, data)
    third = cli.run_accumulation(augmented, tmp_path)
    assert (third["new_count"], third["duplicate_count"], third["total_count"]) == (1, 168, 169)
    fourth = cli.run_accumulation(augmented, tmp_path)
    assert (fourth["new_count"], fourth["duplicate_count"], fourth["total_count"]) == (0, 169, 169)


@pytest.mark.parametrize("args", [
    ["--input", "source.json", "--lookback-days", "2"],
    ["--input", "source.json", "--end-date", "2026-10-20"],
    ["--lookback-days", "4"], ["--end-date", "2999-01-01"],
])
def test_cli_rejects_ambiguous_or_invalid_dates(args):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2

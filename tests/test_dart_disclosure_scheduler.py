"""Local-only scheduler checks with fake collection and real accumulation/query."""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import pytest

from scripts import dart_disclosure_scheduler as cli
from scripts.dart_disclosure_accumulate import ROOT, STORE_RELATIVE
from scripts.dart_disclosure_query import query_disclosures
from src.data_provider.dart_disclosure_provider import DartDisclosureError

NOW = datetime(2026, 10, 20, 20, 20, tzinfo=cli.KST)
SOURCE = ROOT / "output" / "dart_disclosures" / (
    "20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("No network or live operations allowed in scheduler tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def collector(tmp_path, monkeypatch):
    calls = []
    def collect(root, start, end, *, scope):
        calls.append((root, start, end, scope))
        data = json.loads(SOURCE.read_bytes())
        data.update(start_date=start.isoformat(), end_date=end.isoformat())
        for company in data["companies"]:
            company["disclosures"] = [
                row for row in company["disclosures"]
                if start.isoformat() <= row["rcept_dt"] <= end.isoformat()
            ]
            company["status"] = "SUCCESS" if company["disclosures"] else "NO_DATA"
        data.update(
            disclosure_count=sum(len(c["disclosures"]) for c in data["companies"]),
            correction_count=sum(row["is_correction"] for c in data["companies"]
                                 for row in c["disclosures"]),
            companies_with_disclosures=sum(bool(c["disclosures"]) for c in data["companies"]),
        )
        path = tmp_path / f"collection_{len(calls)}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return data, path
    monkeypatch.setattr(cli, "run_collection", collect)
    return calls


def reports(root):
    return [json.loads(path.read_bytes()) for path in (root / cli.OUTPUT_RELATIVE).glob("*.json")
            if path.name != "coverage.json"]


@pytest.mark.parametrize("now,reason", [
    (NOW.replace(hour=19, minute=30), "NOT_DUE"),
    (NOW.replace(hour=20, minute=19, second=59), "NOT_DUE"),
    (NOW.replace(day=24), "WEEKEND"),
    (NOW.replace(day=25), "WEEKEND"),
])
def test_not_due_never_collects_or_writes(tmp_path, collector, now, reason):
    result = cli.run_scheduler(tmp_path, now=now, execute=True)
    assert result["reason"] == reason
    assert collector == []
    assert list(tmp_path.iterdir()) == []


def test_default_preview_has_no_side_effects(tmp_path, collector):
    result = cli.run_scheduler(tmp_path, now=NOW)
    assert result["verdict"] == "DRY_RUN"
    assert not result["executed"]
    assert collector == []
    assert list(tmp_path.iterdir()) == []


def test_kst_conversion_and_naive_clock_rejection(tmp_path, collector):
    result = cli.run_scheduler(tmp_path, now=NOW.astimezone(timezone.utc))
    assert result["verdict"] == "DRY_RUN"
    assert result["scheduler_date"] == "2026-10-20"
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        cli.run_scheduler(tmp_path, now=NOW.replace(tzinfo=None))


def test_success_reuses_574_collection_accumulation_and_query(tmp_path, collector):
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "PASS"
    assert result["schedule"] == "MON-FRI 20:20 Asia/Seoul"
    assert result["accumulation_committed"]
    root, start, end, scope = collector[0]
    assert root == tmp_path and scope == "all"
    assert (start.isoformat(), end.isoformat()) == ("2026-10-18", "2026-10-20")
    assert (result["new_count"], result["duplicate_count"], result["total_count"]) == (168, 0, 168)
    query = query_disclosures(tmp_path / STORE_RELATIVE, ticker="005930")
    assert query["query_count"] == 1
    assert query["last_success_at"] == result["last_success_at"]
    assert query["last_collection_success_at"] == result["actual_collected_at"]
    assert reports(tmp_path) == [result]
    again = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert again["reason"] == "ALREADY_SUCCEEDED"
    assert len(collector) == 1


def test_new_receipt_is_visible_on_next_query_without_duplicate_growth(
    tmp_path, collector, monkeypatch,
):
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    data = json.loads(SOURCE.read_bytes())
    company = next(company for company in data["companies"] if company["ticker"] == "005930")
    extra = {**company["disclosures"][0], "rcept_no": "20261021999999",
             "rcept_dt": "2026-10-21",
             "original_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261021999999"}
    company["disclosures"].append(extra)
    data.update(disclosure_count=169, start_date="2026-10-19", end_date="2026-10-21",
                collected_at="2026-10-21T20:20:10+09:00")
    path = tmp_path / "augmented.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: (data, path))
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert (result["new_count"], result["duplicate_count"], result["total_count"]) == (1, 168, 169)
    query = query_disclosures(tmp_path / STORE_RELATIVE, ticker="005930")
    assert query["disclosures"][0]["rcept_no"] == extra["rcept_no"]
    assert query["query_count"] == 2
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert result["reason"] == "ALREADY_SUCCEEDED"
    assert query_disclosures(tmp_path / STORE_RELATIVE, high=True)["total_count"] == 169


@pytest.mark.parametrize("verdict", ["FAIL", "BLOCKED"])
def test_collection_failure_logged_and_next_attempt_retries(
    tmp_path, collector, monkeypatch, verdict,
):
    original = cli.run_collection
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: (
        {"verdict": verdict, "blocked_reason": "LIVE_COLLECTION_UNAVAILABLE"},
        tmp_path / "failed.json",
    ))
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == verdict
    assert result["last_success_at"] is None
    assert result["new_count"] == 0
    assert reports(tmp_path) == [result]
    assert not (tmp_path / STORE_RELATIVE).exists()
    monkeypatch.setattr(cli, "run_collection", original)
    assert cli.run_scheduler(tmp_path, now=NOW, execute=True)["verdict"] == "PASS"


def test_classification_failure_preserves_database_and_can_retry(tmp_path, collector, monkeypatch):
    first = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    store = tmp_path / STORE_RELATIVE
    raw = store.read_bytes()
    original = cli.run_collection
    def bad(*args, **kwargs):
        data, path = original(*args, **kwargs)
        data["disclosure_count"] = 999
        path.write_text(json.dumps(data), encoding="utf-8")
        return data, path
    monkeypatch.setattr(cli, "run_collection", bad)
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert result["verdict"] == "FAIL"
    assert result["error_code"] == "SOURCE_COUNT_MISMATCH"
    assert result["last_success_at"] == first["last_success_at"]
    assert not result["accumulation_committed"]
    assert store.read_bytes() == raw
    monkeypatch.setattr(cli, "run_collection", original)
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert result["verdict"] == "PASS" and result["new_count"] == 0


def test_storage_failure_rolls_back_and_next_execution_retries(tmp_path, collector):
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    store = tmp_path / STORE_RELATIVE
    query = query_disclosures(store, high=True)
    with sqlite3.connect(store) as connection:
        connection.execute(
            "CREATE TRIGGER fail_update BEFORE UPDATE ON last_success "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    failed = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert failed["verdict"] == "FAIL"
    assert failed["error_code"] == "IntegrityError"
    assert query_disclosures(store, high=True) == query
    with sqlite3.connect(store) as connection:
        connection.execute("DROP TRIGGER fail_update")
    assert cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)["verdict"] == "PASS"


def test_active_lock_blocks_second_execution_and_releases_after_crash(tmp_path, collector):
    directory = tmp_path / cli.OUTPUT_RELATIVE
    directory.mkdir(parents=True)
    connection = sqlite3.connect(directory / "execution.lock.sqlite3")
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
        assert result["reason"] == "ALREADY_RUNNING"
        assert collector == []
    finally:
        connection.close()
    stale = directory / "interrupted.json"
    stale.write_text(json.dumps({"verdict": "RUNNING", "scheduler_date": "2026-10-20"}),
                     encoding="utf-8")
    assert cli.run_scheduler(tmp_path, now=NOW, execute=True)["verdict"] == "PASS"
    assert json.loads(stale.read_bytes())["verdict"] == "RUNNING"


def test_post_commit_report_failure_does_not_overwrite_or_double_insert(
    tmp_path, collector, monkeypatch,
):
    original = cli._save
    def fail_final(path, report):
        if report.get("verdict") == "PASS":
            raise OSError("injected disk error")
        original(path, report)
    monkeypatch.setattr(cli, "_save", fail_final)
    with pytest.raises(OSError):
        cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert reports(tmp_path)[0]["verdict"] == "RUNNING"
    assert query_disclosures(tmp_path / STORE_RELATIVE, high=True)["total_count"] == 168
    monkeypatch.setattr(cli, "_save", original)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["new_count"] == 0 and result["total_count"] == 168


def test_safe_network_error_logged_without_exception_body(tmp_path, collector, monkeypatch):
    def fail(*_args, **_kwargs):
        raise DartDisclosureError("HTTP_REQUEST_FAILED")
    monkeypatch.setattr(cli, "run_collection", fail)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["error_code"] == "HTTP_REQUEST_FAILED"
    assert reports(tmp_path)[0]["verdict"] == "FAIL"


def test_task_proposal_is_kst_weekdays_and_never_registers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["--task-xml"]) == 0
    xml = ET.fromstring(capsys.readouterr().out)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert xml.findtext(".//t:StartBoundary", namespaces=ns).endswith("20:20:00+09:00")
    days = xml.find(".//t:DaysOfWeek", ns)
    assert [item.tag.split("}")[1] for item in days] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    ]
    assert xml.findtext(".//t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
    assert xml.findtext(".//t:Command", namespaces=ns) == "powershell.exe"
    arguments = xml.findtext(".//t:Arguments", namespaces=ns)
    assert "dart_secure_runner.ps1" in arguments
    assert arguments.endswith("-Mode Execute")
    assert "DART_API_KEY" not in arguments
    assert list(tmp_path.iterdir()) == []


def test_cli_default_never_approves_execution(tmp_path, monkeypatch, capsys):
    calls = []
    def run(root, *, execute):
        calls.append((root, execute))
        return {"verdict": "DRY_RUN"}
    monkeypatch.setattr(cli, "run_scheduler", run)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main([]) == 0
    assert calls == [(tmp_path, False)]
    assert json.loads(capsys.readouterr().out)["verdict"] == "DRY_RUN"


def test_post_commit_verification_failure_is_explicit_and_retry_is_idempotent(
    tmp_path, collector, monkeypatch,
):
    real_query = cli.query_disclosures
    def fail(*_args, **_kwargs):
        raise cli.QueryError("POST_UPDATE_STORE_MISMATCH")
    monkeypatch.setattr(cli, "query_disclosures", fail)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "FAIL"
    assert result["accumulation_committed"]
    assert result["total_count"] == 168
    assert result["stage"] == "READ_ONLY_VERIFICATION"
    assert reports(tmp_path) == [result]
    monkeypatch.setattr(cli, "query_disclosures", real_query)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert (result["new_count"], result["duplicate_count"], result["total_count"]) == (0, 168, 168)


@pytest.mark.parametrize("verdict,code", [("FAIL", 1), ("BLOCKED", 2)])
def test_cli_failure_exit_codes_and_safe_output(tmp_path, monkeypatch, capsys, verdict, code):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "run_scheduler", lambda *_args, **_kwargs: {
        "verdict": verdict, "error_code": "TEST_FAILURE",
    })
    assert cli.main(["--execute"]) == code
    assert json.loads(capsys.readouterr().out)["verdict"] == verdict


@pytest.mark.parametrize("target_day", [26, 30])
def test_friday_failure_then_monday_or_holiday_recovery(tmp_path, collector, monkeypatch, target_day):
    cli.run_scheduler(tmp_path, now=NOW + timedelta(days=2), execute=True)  # Thursday
    original = cli.run_collection
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: (
        {"verdict": "BLOCKED", "blocked_reason": "LIVE_COLLECTION_UNAVAILABLE"},
        tmp_path / "failed.json",
    ))
    friday = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=3), execute=True)
    assert friday["verdict"] == "BLOCKED"
    monkeypatch.setattr(cli, "run_collection", original)
    collector.clear()
    result = cli.run_scheduler(tmp_path, now=NOW.replace(day=target_day), execute=True)
    assert result["verdict"] == "PASS"
    assert result["recovery_start_date"] == "2026-10-22"
    days = set()
    for _, start, end, scope in collector:
        assert 0 <= (end - start).days <= 2
        assert scope == "all"
        days.update(start + timedelta(days=i) for i in range((end - start).days + 1))
    assert all(NOW.replace(day=d).date() in days for d in range(22, target_day + 1))


def test_long_outage_stops_at_failed_segment_and_resumes_without_skipping(tmp_path, collector, monkeypatch):
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    original = cli.run_collection
    collector.clear()
    def fail_second(root, start, end, *, scope):
        if start.isoformat() == "2026-10-23":
            return {"verdict": "FAIL", "blocked_reason": None}, tmp_path / "failed.json"
        return original(root, start, end, scope=scope)
    monkeypatch.setattr(cli, "run_collection", fail_second)
    target = NOW + timedelta(days=20)  # Monday, after three weekends
    result = cli.run_scheduler(tmp_path, now=target, execute=True)
    assert result["verdict"] == "FAIL"
    assert [s["verdict"] for s in result["segments"]] == ["PASS", "FAIL"]
    assert len(collector) == 1
    coverage = json.loads((tmp_path / cli.OUTPUT_RELATIVE / "coverage.json").read_bytes())
    assert coverage["last_success_end_date"] == "2026-10-22"
    assert not any(r["verdict"] == "PASS" and r["scheduler_date"] == target.date().isoformat()
                   for r in reports(tmp_path))
    monkeypatch.setattr(cli, "run_collection", original)
    collector.clear()
    recovered = cli.run_scheduler(tmp_path, now=target, execute=True)
    assert recovered["verdict"] == "PASS"
    assert collector[0][1].isoformat() == "2026-10-22"
    assert collector[-1][2] == target.date()
    for previous, following in zip(collector, collector[1:]):
        assert following[1] == previous[2] + timedelta(days=1)
    assert query_disclosures(tmp_path / STORE_RELATIVE, high=True)["total_count"] == 168


def test_checkpoint_write_failure_after_commit_retries_same_window(tmp_path, collector, monkeypatch):
    original = cli._save
    def fail(path, report):
        if path.name == "coverage.json" and report.get("baseline_only") is False:
            raise OSError("injected checkpoint failure")
        original(path, report)
    monkeypatch.setattr(cli, "_save", fail)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "FAIL" and result["accumulation_committed"]
    monkeypatch.setattr(cli, "_save", original)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "PASS"
    assert result["new_count"] == 0 and result["total_count"] == 168


def test_preflight_never_leaks_key_or_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DART_API_KEY", "test-secret-never-print")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["--preflight"]) == 0
    raw = capsys.readouterr().out
    assert "test-secret-never-print" not in raw
    result = json.loads(raw)
    assert result["dart_api_key_present"] is True
    assert result["key_validity"] == "NOT_CHECKED_NO_API_CALL"
    assert result["current_account"]
    assert list(tmp_path.iterdir()) == []
    monkeypatch.delenv("DART_API_KEY")
    assert cli.preflight(tmp_path)["dart_api_key_present"] is False


def test_bootstrap_failure_then_long_delay_keeps_initial_baseline(tmp_path, collector, monkeypatch):
    original = cli.run_collection
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: (
        {"verdict": "BLOCKED", "blocked_reason": "LIVE_COLLECTION_UNAVAILABLE"},
        tmp_path / "failed.json",
    ))
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    monkeypatch.setattr(cli, "run_collection", original)
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=20), execute=True)
    assert result["verdict"] == "PASS"
    assert collector[0][1].isoformat() == "2026-10-18"
    assert collector[-1][2] == (NOW + timedelta(days=20)).date()


def test_every_calendar_day_receipt_is_recovered_and_queryable(tmp_path, collector, monkeypatch):
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    calls = []
    target = NOW + timedelta(days=20)
    def daily(root, start, end, *, scope):
        calls.append((start, end))
        data = json.loads(SOURCE.read_bytes())
        for company in data["companies"]:
            company.update(status="NO_DATA", disclosures=[])
        company = next(c for c in data["companies"] if c["ticker"] == "005930")
        template = json.loads(SOURCE.read_bytes())["companies"]
        row = next(c for c in template if c["ticker"] == "005930")["disclosures"][0]
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            receipt = f"{day:%Y%m%d}999999"
            company["disclosures"].append({
                **row, "rcept_no": receipt, "rcept_dt": day.isoformat(),
                "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            })
        company["status"] = "SUCCESS"
        data.update(
            start_date=start.isoformat(), end_date=end.isoformat(),
            collected_at=target.isoformat(), disclosure_count=len(company["disclosures"]),
            correction_count=0, companies_with_disclosures=1,
        )
        path = tmp_path / f"daily_{start}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return data, path
    monkeypatch.setattr(cli, "run_collection", daily)
    report = cli.run_scheduler(tmp_path, now=target, execute=True)
    assert report["verdict"] == "PASS"
    assert report["new_count"] == 21
    result = query_disclosures(tmp_path / STORE_RELATIVE, ticker="005930", limit=100)
    added = {r["rcept_dt"] for r in result["disclosures"] if r["rcept_no"].endswith("999999")}
    assert added == {(NOW + timedelta(days=i)).date().isoformat() for i in range(21)}
    assert result["total_count"] == 189


def test_no_future_checkpoint_is_accepted(tmp_path, collector):
    cli.run_scheduler(tmp_path, now=NOW, execute=True)
    coverage = tmp_path / cli.OUTPUT_RELATIVE / "coverage.json"
    coverage.write_text(json.dumps({
        "schema_version": 1, "last_success_end_date": "2999-01-01", "baseline_only": False,
    }), encoding="utf-8")
    collector.clear()
    result = cli.run_scheduler(tmp_path, now=NOW + timedelta(days=1), execute=True)
    assert result["verdict"] == "FAIL"
    assert result["error_code"] == "COVERAGE_DATE_IN_FUTURE"
    assert collector == []


def test_wrong_collection_window_is_not_accumulated(tmp_path, collector, monkeypatch):
    original = cli.run_collection
    def wrong(*args, **kwargs):
        data, path = original(*args, **kwargs)
        data["end_date"] = "2026-10-19"
        path.write_text(json.dumps(data), encoding="utf-8")
        return data, path
    monkeypatch.setattr(cli, "run_collection", wrong)
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "FAIL"
    assert result["error_code"] == "COLLECTION_WINDOW_MISMATCH"
    assert not (tmp_path / STORE_RELATIVE).exists()


@pytest.mark.parametrize("state", [[], {"schema_version": 1}, {"schema_version": 99}])
def test_invalid_checkpoint_fails_explicitly(tmp_path, collector, state):
    directory = tmp_path / cli.OUTPUT_RELATIVE
    directory.mkdir(parents=True)
    (directory / "coverage.json").write_text(json.dumps(state), encoding="utf-8")
    result = cli.run_scheduler(tmp_path, now=NOW, execute=True)
    assert result["verdict"] == "FAIL"
    assert result["error_code"] == "INVALID_COVERAGE_STATE"
    assert collector == []

import json
from types import SimpleNamespace

from dashboard.api import route_dashboard_request
from dashboard.operations import manual_run_capability


def _write_state(root, **updates):
    state = {
        "target_trade_date": "2026-09-04",
        "scheduler_date": "2026-09-04",
        "current_status": "SUCCESS",
        "attempt": 1,
        "last_attempt_at": "2026-09-04T19:00:00+09:00",
        "completed_at": "2026-09-04T19:00:00+09:00",
        "last_run_id": "run-1",
        "last_daily_status": "SUCCESS",
        "latest_market_date": "2026-09-04",
        "latest_investor_date": "2026-09-04",
        "last_successful_run_at": "2026-09-04T19:00:00+09:00",
        "last_successful_trade_date": "2026-09-04",
        "timezone": "Asia/Seoul",
    }
    state.update(updates)
    output = root / "output"
    output.mkdir(exist_ok=True)
    (output / "daily_scheduler_state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_record(root, **updates):
    record = {
        "target_trade_date": "2026-09-04", "scheduler_date": "2026-09-04", "event_type": "ATTEMPT_COMPLETED",
        "orchestration_status": "SUCCESS", "attempt": 1, "slot": 0, "started_at": "18:30",
        "finished_at": "19:00", "last_run_id": "run-1", "operator_action_required": False,
    }
    record.update(updates)
    output = root / "output"
    output.mkdir(exist_ok=True)
    (output / "daily_run_registry.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _body(result):
    return json.loads(result[2])


def test_operations_status_and_detail_are_read_only(tmp_path):
    _write_state(tmp_path, current_status="RETRY_PENDING", next_retry_at="2026-09-04T19:30:00+09:00", error_code="SOURCE_LAG")
    _write_record(tmp_path, orchestration_status="RETRY_PENDING", error_code="SOURCE_LAG")
    status, _, body = route_dashboard_request("GET", "/api/operations/status", tmp_path)
    assert status == 200
    assert json.loads(body)["current_status"] == "RETRY_PENDING"
    detail = route_dashboard_request("GET", "/api/operations/history/2026-09-04", tmp_path)
    assert detail[0] == 200
    assert _body(detail)["attempts"][0]["error_code"] == "SOURCE_LAG"
    assert route_dashboard_request("POST", "/api/operations/status", tmp_path)[0] == 405


def test_operations_missing_state_is_no_data(tmp_path):
    status, _, body = route_dashboard_request("GET", "/api/operations/status", tmp_path)
    assert status == 200
    payload = json.loads(body)
    assert payload["current_status"] == "NO_DATA"
    assert payload["health_status"] == "NO_RUN"
    assert route_dashboard_request("GET", "/api/operations/history", tmp_path)[0] == 200


def test_operations_skips_malformed_registry_lines(tmp_path):
    _write_state(tmp_path)
    registry = tmp_path / "output" / "daily_run_registry.jsonl"
    registry.write_text("not-json\n", encoding="utf-8")
    _write_record(tmp_path)
    # The fixture helper replaces the file with a valid record; append corruption as a trailing line.
    registry.open("a", encoding="utf-8").write("{broken\n")
    status, _, body = route_dashboard_request("GET", "/api/operations/history", tmp_path)
    assert status == 200
    assert len(json.loads(body)["items"]) == 1


def test_exception_detail_is_read_only_and_uses_state_precedence(tmp_path):
    _write_state(tmp_path, current_status="BLOCKED", failed_phase="INPUT_GATE", error_code="INTEGRITY_GATE_FAIL", error_message="integrity failed", operator_action_required=True, operator_action_code="CHECK_INTEGRITY")
    _write_record(tmp_path, orchestration_status="FAILED", error_code="STALE_REGISTRY_VALUE", failed_phase="PIPELINE")
    status, _, body = route_dashboard_request("GET", "/api/operations/exceptions/2026-09-04", tmp_path)
    payload = json.loads(body)
    assert status == 200
    assert payload["exception"]["status"] == "BLOCKED"
    assert payload["exception"]["severity"] == "BLOCKING"
    assert payload["exception"]["error_code"] == "INTEGRITY_GATE_FAIL"
    assert payload["exception"]["affected_components"] == ["INTEGRITY"]
    assert payload["exception"]["manual_rerun_allowed"] is False
    assert route_dashboard_request("POST", "/api/operations/exceptions/2026-09-04", tmp_path)[0] == 405


def test_exception_detail_empty_is_stable(tmp_path):
    status, _, body = route_dashboard_request("GET", "/api/operations/exceptions/2026-09-04", tmp_path)
    assert status == 200
    assert json.loads(body) == {"exception": None, "trade_date": "2026-09-04"}


def test_warning_and_failed_exception_guidance(tmp_path):
    _write_state(tmp_path, current_status="SUCCESS_WITH_WARNING", last_daily_status="SUCCESS_WITH_WARNING")
    _write_record(tmp_path, orchestration_status="SUCCESS_WITH_WARNING", daily_status="SUCCESS_WITH_WARNING", integrity_status="PASS_WITH_WARNING", operator_action_required=True, operator_action_code="CHECK_INTEGRITY")
    warning = json.loads(route_dashboard_request("GET", "/api/operations/exceptions/2026-09-04", tmp_path)[2])["exception"]
    assert warning["severity"] == "WARNING"
    assert warning["affected_components"] == ["INTEGRITY"]
    assert warning["retryable"] is False

    _write_state(tmp_path, current_status="FAILED", error_code="RETRY_EXHAUSTED", error_message="retry window exhausted", operator_action_required=True, operator_action_code="MANUAL_RERUN_ALLOWED")
    failed = json.loads(route_dashboard_request("GET", "/api/operations/exceptions/2026-09-04", tmp_path)[2])["exception"]
    assert failed["severity"] == "ERROR"
    assert failed["manual_rerun_allowed"] is True


def test_manual_capability_is_restricted_to_explicit_failed_rerun(tmp_path):
    for state, allowed in (("SUCCESS", False), ("SUCCESS_WITH_WARNING", False), ("RETRY_PENDING", False), ("BLOCKED", False), ("NON_TRADING_DAY", False)):
        _write_state(tmp_path, current_status=state, operator_action_code=None)
        assert manual_run_capability(tmp_path)["allowed"] is allowed
    _write_state(tmp_path, current_status="FAILED", operator_action_code="DO_NOT_RERUN")
    assert manual_run_capability(tmp_path)["allowed"] is False
    _write_state(tmp_path, current_status="FAILED", operator_action_code="MANUAL_RERUN_ALLOWED")
    assert manual_run_capability(tmp_path)["allowed"] is True


def test_manual_run_executes_official_orchestrator_and_appends_audit(monkeypatch, tmp_path):
    _write_state(tmp_path, current_status="FAILED", operator_action_code="MANUAL_RERUN_ALLOWED")
    fake_result = SimpleNamespace(run_id="manual-1", overall_status="SUCCESS", started_at="2026-09-04T10:00:00+00:00", finished_at="2026-09-04T10:01:00+00:00", failed_phase=None, errors=[], warnings=[])
    called = []
    monkeypatch.setattr("dashboard.api.run_daily_operation", lambda **kwargs: called.append(kwargs) or fake_result)
    status, _, body = route_dashboard_request("POST", "/api/operations/manual-run", tmp_path, b"{}")
    payload = json.loads(body)
    assert status == 200
    assert payload["run_id"] == "manual-1"
    assert payload["scheduler_reconciliation_required"] is True
    assert called == [{"repo_root": tmp_path}]
    audit = json.loads((tmp_path / "output" / "daily_run_registry.jsonl").read_text().splitlines()[-1])
    assert audit["event_type"] == "MANUAL_RUN_COMPLETED"
    assert audit["source"] == "MANUAL"
    assert audit["last_run_id"] == "manual-1"
    assert json.loads((tmp_path / "output" / "daily_scheduler_state.json").read_text())["current_status"] == "FAILED"


def test_manual_run_rejects_disallowed_and_locks(tmp_path):
    _write_state(tmp_path, current_status="BLOCKED", operator_action_code="CHECK_INTEGRITY")
    assert route_dashboard_request("POST", "/api/operations/manual-run", tmp_path, b"{}")[0] == 403
    _write_state(tmp_path, current_status="FAILED", operator_action_code="MANUAL_RERUN_ALLOWED")
    output = tmp_path / "output"
    (output / "daily_scheduler.lock").write_text("active", encoding="utf-8")
    status, _, body = route_dashboard_request("POST", "/api/operations/manual-run", tmp_path, b"{}")
    assert status == 409
    assert json.loads(body)["error_code"] == "CONCURRENT_RUN"
    assert route_dashboard_request("GET", "/api/operations/manual-run", tmp_path)[0] == 405


def test_manual_run_duplicate_is_rejected_until_reconciliation(monkeypatch, tmp_path):
    _write_state(tmp_path, current_status="FAILED", operator_action_code="MANUAL_RERUN_ALLOWED")
    fake_result = SimpleNamespace(run_id="manual-1", overall_status="SUCCESS", started_at="start", finished_at="done", failed_phase=None, errors=[], warnings=[])
    monkeypatch.setattr("dashboard.api.run_daily_operation", lambda **kwargs: fake_result)
    assert route_dashboard_request("POST", "/api/operations/manual-run", tmp_path, b"{}")[0] == 200
    status, _, body = route_dashboard_request("POST", "/api/operations/manual-run", tmp_path, b"{}")
    assert status == 403
    assert json.loads(body)["error_code"] == "MANUAL_RUN_NOT_ALLOWED"
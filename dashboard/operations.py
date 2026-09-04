from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.daily_run_registry import EVENT_MANUAL_RUN_COMPLETED, get_runs_for_trade_date, read_registry
from scripts.daily_scheduler import ALL_STATUSES, SchedulerState, TIMEZONE_NAME


EXCEPTION_STATUSES = {"SUCCESS_WITH_WARNING", "BLOCKED", "FAILED"}
RETRYABLE_CODES = {"CONCURRENT_RUN", "TRANSIENT_FAILURE", "PIPELINE_TRANSIENT_FAILURE", "SOURCE_LAG"}
MANUAL_RUN_ALLOWED_STATUS = "FAILED"


def manual_run_capability(repo_root: Path) -> dict[str, Any]:
    state = _read_state(repo_root / "output" / "daily_scheduler_state.json")
    if state is None:
        return {"allowed": False, "reason_code": "MANUAL_RUN_NOT_ALLOWED", "reason": "Scheduler state is unavailable; manual execution is disabled.", "requires_confirmation": True}
    if state.current_status == MANUAL_RUN_ALLOWED_STATUS and state.operator_action_code == "MANUAL_RERUN_ALLOWED":
        records = read_registry(repo_root / "output" / "daily_run_registry.jsonl")
        if any(record.target_trade_date == state.target_trade_date and record.event_type == EVENT_MANUAL_RUN_COMPLETED for record in records):
            return {"allowed": False, "reason_code": "MANUAL_RUN_ALREADY_COMPLETED", "reason": "A manual operation already completed for this trade date; await scheduler reconciliation.", "requires_confirmation": True}
        return {"allowed": True, "reason_code": "MANUAL_RERUN_ALLOWED", "reason": "The failed operation is explicitly marked for manual rerun.", "requires_confirmation": True}
    reasons = {
        "SUCCESS": "The daily operation already completed successfully.",
        "SUCCESS_WITH_WARNING": "The daily operation completed with warnings; manual rerun is not recommended.",
        "RETRY_PENDING": "An automatic retry is pending; wait for the scheduled retry.",
        "BLOCKED": "Resolve the blocking condition before considering a rerun.",
        "FAILED": "This failed operation is not marked MANUAL_RERUN_ALLOWED.",
        "NON_TRADING_DAY": "Manual operation is disabled on non-trading days.",
    }
    return {"allowed": False, "reason_code": "MANUAL_RUN_NOT_ALLOWED", "reason": reasons.get(state.current_status, "Manual execution is disabled for the current scheduler state."), "requires_confirmation": True}


def _severity(status: str | None) -> str:
    return {"SUCCESS_WITH_WARNING": "WARNING", "BLOCKED": "BLOCKING", "FAILED": "ERROR", "RETRY_PENDING": "INFO"}.get(status or "", "INFO")


def _affected_components(status: str | None, failed_phase: str | None, error_code: str | None, integrity_status: str | None) -> list[str]:
    if status == "SUCCESS_WITH_WARNING" and integrity_status and "WARNING" in integrity_status:
        return ["INTEGRITY"]
    phase = (failed_phase or "").upper()
    code = (error_code or "").upper()
    if "INTEGRITY" in phase or "INTEGRITY" in code or code == "STRUCTURAL_FAILURE":
        return ["INTEGRITY"]
    for marker, component in (("MARKET", "MARKET"), ("INVESTOR", "INVESTOR"), ("PIPELINE", "PIPELINE"), ("HEALTH", "HEALTH"), ("SCHEDULER", "SCHEDULER"), ("REGISTRY", "REGISTRY")):
        if marker in phase or marker in code:
            return [component]
    return ["SCHEDULER"] if status in {"BLOCKED", "FAILED"} else []


def _guidance(operator_action_code: str | None, status: str | None) -> str | None:
    if status == "RETRY_PENDING":
        return "다음 자동 재시도를 기다리십시오."
    return {"CHECK_INPUT_DATA": "Market / Investor 입력 최신일을 확인하십시오.", "CHECK_INTEGRITY": "Integrity Gate 실패 원인을 확인한 뒤 강제 진행하지 마십시오.", "CHECK_APPLICATION_ERROR": "오류 원인을 확인한 뒤 수동 rerun 여부를 판단하십시오.", "DO_NOT_RERUN": "원인 확인 전 수동 rerun 금지.", "MANUAL_RERUN_ALLOWED": "오류 원인을 확인한 뒤 수동 rerun 여부를 판단하십시오."}.get(operator_action_code or "")


def _exception_from_record(record: Any, state: SchedulerState | None = None) -> dict[str, Any]:
    status = state.current_status if state else record.orchestration_status
    failed_phase = state.failed_phase if state else record.failed_phase
    error_code = state.error_code if state else record.error_code
    error_message = state.error_message if state else record.error_message
    action_required = state.operator_action_required if state else record.operator_action_required
    action_code = state.operator_action_code if state else record.operator_action_code
    market_date = (state.latest_market_date if state else None) or getattr(record, "latest_market_date", None)
    investor_date = (state.latest_investor_date if state else None) or getattr(record, "latest_investor_date", None)
    integrity_status = getattr(record, "integrity_status", None)
    pipeline_status = (state.last_daily_status if state else None) or getattr(record, "pipeline_status", None) or getattr(record, "daily_status", None)
    manual_allowed: bool | None = None
    if action_code == "MANUAL_RERUN_ALLOWED":
        manual_allowed = True
    elif action_code in {"DO_NOT_RERUN", "CHECK_INTEGRITY", "CHECK_INPUT_DATA", "CHECK_APPLICATION_ERROR"}:
        manual_allowed = False
    return {
        "trade_date": state.target_trade_date if state else record.target_trade_date,
        "status": status,
        "severity": _severity(status),
        "failed_phase": failed_phase,
        "error_code": error_code,
        "summary": "Operation completed with warning" if status == "SUCCESS_WITH_WARNING" else (error_message or error_code or "Operational issue recorded"),
        "details": error_message,
        "retryable": status == "RETRY_PENDING" or error_code in RETRYABLE_CODES,
        "operator_action_required": bool(action_required),
        "operator_action_code": action_code,
        "manual_rerun_allowed": manual_allowed,
        "operator_guidance": _guidance(action_code, status),
        "affected_components": _affected_components(status, failed_phase, error_code, integrity_status),
        "data_context": {"target_trade_date": state.target_trade_date if state else record.target_trade_date, "latest_market_date": market_date, "latest_investor_date": investor_date, "integrity_status": integrity_status, "pipeline_status": pipeline_status},
        "run_context": {"last_run_id": state.last_run_id if state else record.last_run_id, "attempt": state.attempt if state else record.attempt, "last_attempt_at": state.last_attempt_at if state else record.finished_at, "next_retry_at": state.next_retry_at if state else record.next_retry_at},
    }


def _read_state(path: Path) -> SchedulerState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SchedulerState.from_dict(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _state_payload(state: SchedulerState | None, records: list[Any]) -> dict[str, Any]:
    if state is None:
        return {
            "target_trade_date": None,
            "current_status": "NO_DATA",
            "attempt": None,
            "next_retry_at": None,
            "last_attempt_at": None,
            "completed_at": None,
            "latest_market_date": None,
            "latest_investor_date": None,
            "integrity_status": None,
            "pipeline_status": None,
            "health_status": "NO_RUN",
            "failed_phase": None,
            "error_code": None,
            "error_message": None,
            "operator_action_required": False,
            "operator_action_code": None,
            "last_run_id": None,
            "last_daily_status": None,
            "last_successful_run_at": None,
            "last_successful_trade_date": None,
            "timezone": TIMEZONE_NAME,
        }

    health_status = "HEALTHY" if state.current_status in {"SUCCESS", "NON_TRADING_DAY"} else "WARNING"
    if state.current_status in {"BLOCKED", "FAILED"}:
        health_status = "FAILED"
    return {
        "target_trade_date": state.target_trade_date,
        "current_status": state.current_status if state.current_status in ALL_STATUSES else "NO_DATA",
        "attempt": state.attempt,
        "next_retry_at": state.next_retry_at,
        "last_attempt_at": state.last_attempt_at,
        "completed_at": state.completed_at,
        "latest_market_date": state.latest_market_date,
        "latest_investor_date": state.latest_investor_date,
        "integrity_status": _record_value(records, "integrity_status", state.target_trade_date),
        "pipeline_status": state.last_daily_status,
        "health_status": health_status,
        "failed_phase": state.failed_phase,
        "error_code": state.error_code,
        "error_message": state.error_message,
        "operator_action_required": state.operator_action_required,
        "operator_action_code": state.operator_action_code,
        "last_run_id": state.last_run_id,
        "last_daily_status": state.last_daily_status,
        "last_successful_run_at": state.last_successful_run_at,
        "last_successful_trade_date": state.last_successful_trade_date,
        "timezone": state.timezone or TIMEZONE_NAME,
    }


def _record_value(records: list[Any], field: str, trade_date: str) -> Any:
    for record in reversed(records):
        if record.target_trade_date == trade_date and getattr(record, field, None) is not None:
            return getattr(record, field)
    return None


def _summary(records: list[Any], trade_date: str) -> dict[str, Any] | None:
    matching = [record for record in records if record.target_trade_date == trade_date]
    attempts = [record for record in matching if record.attempt is not None]
    if not attempts:
        return None
    terminal = [record for record in matching if record.event_type in {"ATTEMPT_COMPLETED", "NON_TRADING_DAY", "MISSED_RUN"}]
    final = terminal[-1] if terminal else attempts[-1]
    return {
        "trade_date": trade_date,
        "final_status": final.orchestration_status,
        "attempts": len(attempts),
        "first_attempt_at": attempts[0].started_at,
        "last_attempt_at": attempts[-1].finished_at,
        "last_run_id": final.last_run_id,
        "error_code": final.error_code,
        "operator_action_required": final.operator_action_required,
    }


def operations_status(repo_root: Path) -> dict[str, Any]:
    state_path = repo_root / "output" / "daily_scheduler_state.json"
    registry_path = repo_root / "output" / "daily_run_registry.jsonl"
    state = _read_state(state_path)
    records = read_registry(registry_path)
    payload = _state_payload(state, records)
    payload["manual_run"] = manual_run_capability(repo_root)
    return payload


def operations_history(repo_root: Path, limit: int = 30) -> list[dict[str, Any]]:
    records = read_registry(repo_root / "output" / "daily_run_registry.jsonl")
    dates: list[str] = []
    for record in reversed(records):
        if record.target_trade_date and record.target_trade_date not in dates:
            dates.append(record.target_trade_date)
    return [summary for trade_date in dates[:limit] if (summary := _summary(records, trade_date)) is not None]


def operations_detail(repo_root: Path, trade_date: str) -> list[dict[str, Any]]:
    records = get_runs_for_trade_date(repo_root / "output" / "daily_run_registry.jsonl", trade_date)
    return [
        {
            "slot": record.slot,
            "attempt": record.attempt,
            "orchestration_status": record.orchestration_status,
            "daily_status": record.daily_status,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "next_retry_at": record.next_retry_at,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "failed_phase": record.failed_phase,
            "operator_action_required": record.operator_action_required,
            "operator_action_code": record.operator_action_code,
            "last_run_id": record.last_run_id,
        }
        for record in records
        if record.attempt is not None
    ]


def operations_exception(repo_root: Path, trade_date: str) -> dict[str, Any] | None:
    state = _read_state(repo_root / "output" / "daily_scheduler_state.json")
    records = get_runs_for_trade_date(repo_root / "output" / "daily_run_registry.jsonl", trade_date)
    if state and state.target_trade_date == trade_date and state.current_status in EXCEPTION_STATUSES:
        record = next((item for item in reversed(records) if item.target_trade_date == trade_date), None)
        if record is not None:
            return _exception_from_record(record, state)
        return None
    record = next((item for item in reversed(records) if item.orchestration_status in EXCEPTION_STATUSES), None)
    return _exception_from_record(record) if record else None


def operations_exceptions(repo_root: Path, limit: int = 30) -> list[dict[str, Any]]:
    records = read_registry(repo_root / "output" / "daily_run_registry.jsonl")
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for record in reversed(records):
        if record.target_trade_date in seen or record.orchestration_status not in EXCEPTION_STATUSES:
            continue
        seen.add(record.target_trade_date)
        exception = operations_exception(repo_root, record.target_trade_date)
        if exception:
            items.append(exception)
        if len(items) >= limit:
            break
    return items
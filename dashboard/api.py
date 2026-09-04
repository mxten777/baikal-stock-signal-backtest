from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dashboard.adapter.service import DashboardService
from dashboard.operations import manual_run_capability, operations_detail, operations_exception, operations_exceptions, operations_history, operations_status
from scripts.daily_operational_run import run_daily_operation
from scripts.daily_run_registry import EVENT_MANUAL_RUN_COMPLETED, RegistryRecord, _compute_event_id, append_record
from scripts.daily_scheduler import SchedulerLock, TIMEZONE_NAME, _to_seoul


READ_ONLY_ENDPOINTS = frozenset(
    {
        "/api/dashboard/overview",
        "/api/dashboard/signals",
        "/api/dashboard/health",
    }
)
OPERATIONS_ENDPOINTS = frozenset({"/api/operations/status", "/api/operations/history", "/api/operations/exceptions"})
MANUAL_RUN_ENDPOINT = "/api/operations/manual-run"


def route_dashboard_request(method: str, path: str, repo_root: Path, body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "GET"}
    parsed_path = urlparse(path).path
    if method.upper() == "POST" and parsed_path == MANUAL_RUN_ENDPOINT:
        return _manual_run_response(repo_root, body)
    if parsed_path == MANUAL_RUN_ENDPOINT:
        return _json_response(405, {"Content-Type": "application/json; charset=utf-8", "Allow": "POST"}, {"error": "method_not_allowed", "allowed_methods": ["POST"]})
    if method.upper() != "GET":
        return _json_response(405, headers, {"error": "method_not_allowed", "allowed_methods": ["GET"]})
    is_detail = parsed_path.startswith("/api/operations/history/")
    is_exception_detail = parsed_path.startswith("/api/operations/exceptions/")
    if parsed_path not in READ_ONLY_ENDPOINTS and parsed_path not in OPERATIONS_ENDPOINTS and not is_detail and not is_exception_detail:
        return _json_response(404, headers, {"error": "not_found"})

    if parsed_path == "/api/operations/status":
        payload: dict[str, Any] = operations_status(repo_root)
    elif parsed_path == "/api/operations/history":
        payload = {"items": operations_history(repo_root)}
    elif parsed_path == "/api/operations/exceptions":
        payload = {"items": operations_exceptions(repo_root)}
    elif is_exception_detail:
        trade_date = parsed_path.removeprefix("/api/operations/exceptions/")
        payload = {"trade_date": trade_date, "exception": operations_exception(repo_root, trade_date)}
    elif is_detail:
        trade_date = parsed_path.removeprefix("/api/operations/history/")
        payload = {"trade_date": trade_date, "attempts": operations_detail(repo_root, trade_date)}
    else:
        service = DashboardService(repo_root=repo_root)
        if parsed_path == "/api/dashboard/overview":
            payload = service.overview()
        elif parsed_path == "/api/dashboard/signals":
            payload = service.signals()
        else:
            payload = service.health()
    return _json_response(200, headers, payload)


def _manual_run_response(repo_root: Path, body: bytes | None) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "POST"}
    if body not in (None, b"", b"{}"):
        return _json_response(400, headers, {"error_code": "INVALID_REQUEST", "error_message": "Manual run accepts an empty JSON object."})
    capability = manual_run_capability(repo_root)
    if not capability["allowed"]:
        return _json_response(403, headers, {"error_code": "MANUAL_RUN_NOT_ALLOWED", "error_message": capability["reason"], "manual_run": capability})
    scheduler_lock = SchedulerLock(repo_root / "output" / "daily_scheduler.lock")
    if not scheduler_lock.acquire():
        return _json_response(409, headers, {"error_code": "CONCURRENT_RUN", "error_message": "Another Daily Operation is running.", "manual_run": capability})
    try:
        if (repo_root / "output" / "daily_operational_run.lock").exists():
            return _json_response(409, headers, {"error_code": "CONCURRENT_RUN", "error_message": "Another Daily Operation is running.", "manual_run": capability})
        try:
            result = run_daily_operation(repo_root=repo_root)
        except Exception as exc:
            return _json_response(500, headers, {"error_code": "MANUAL_RUN_FAILED", "error_message": f"Manual operation failed: {type(exc).__name__}"})
        _append_manual_audit(repo_root, result)
        payload = {"accepted": True, "executed": True, "run_id": result.run_id, "daily_status": result.overall_status, "overall_status": result.overall_status, "started_at": result.started_at, "completed_at": result.finished_at, "error_code": result.errors[0] if result.errors else None, "error_message": result.errors[0] if result.errors else None, "warning": result.warnings[0] if result.warnings else None, "scheduler_reconciliation_required": True}
        return _json_response(200, headers, payload)
    finally:
        scheduler_lock.release()


def _append_manual_audit(repo_root: Path, result: Any) -> None:
    try:
        state = json.loads((repo_root / "output" / "daily_scheduler_state.json").read_text(encoding="utf-8"))
        target_trade_date = state["target_trade_date"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = RegistryRecord(registry_version=1, event_id=_compute_event_id(target_trade_date, None, None, f"MANUAL:{result.run_id}"), scheduler_date=_to_seoul(datetime.now(timezone.utc)).date().isoformat(), target_trade_date=target_trade_date, timezone=TIMEZONE_NAME, event_type=EVENT_MANUAL_RUN_COMPLETED, source="MANUAL", orchestration_status=result.overall_status, daily_status=result.overall_status, started_at=result.started_at, finished_at=result.finished_at, last_run_id=result.run_id, failed_phase=result.failed_phase, error_code=result.errors[0] if result.errors else None, error_message=result.errors[0] if result.errors else None, created_at=timestamp)
    append_record(repo_root / "output" / "daily_run_registry.jsonl", record)


def _json_response(status: int, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return status, headers, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    repo_root = Path.cwd()

    def do_GET(self) -> None:
        self._send(*route_dashboard_request("GET", self.path, self.repo_root))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self._send(*route_dashboard_request("POST", self.path, self.repo_root, self.rfile.read(length)))

    def do_PUT(self) -> None:
        self._send(*route_dashboard_request("PUT", self.path, self.repo_root))

    def do_PATCH(self) -> None:
        self._send(*route_dashboard_request("PATCH", self.path, self.repo_root))

    def do_DELETE(self) -> None:
        self._send(*route_dashboard_request("DELETE", self.path, self.repo_root))

    def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(host: str = "127.0.0.1", port: int = 8765, repo_root: Path | None = None) -> None:
    handler = type("ConfiguredDashboardRequestHandler", (DashboardRequestHandler,), {"repo_root": repo_root or Path.cwd()})
    server = ThreadingHTTPServer((host, port), handler)
    server.serve_forever()


if __name__ == "__main__":
    run_server()

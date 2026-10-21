from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dashboard.adapter.service import DashboardService
from dashboard.daily_report_docx import build_docx_report, build_summary_docx_report
from dashboard.daily_report_model import NEW_CANDIDATES_NOT_FOUND, STATUS_READY
from dashboard.daily_report_pdf import build_pdf_report, build_summary_pdf_report
from dashboard.daily_signal_board import build_daily_signal_board
from dashboard.dual_shadow import READ_ONLY_ENDPOINTS as DUAL_READ_ONLY_ENDPOINTS, DualShadowDashboardService
from dashboard.expanded_daily_report import build_daily_report_model
from dashboard.expanded_signal_board import EXPANDED_BOARD_ENDPOINT, build_expanded_signal_board
from dashboard.operations import manual_run_capability, operations_detail, operations_exception, operations_exceptions, operations_history, operations_status
from scripts.daily_operational_run import run_daily_operation
from scripts.daily_run_registry import EVENT_MANUAL_RUN_COMPLETED, RegistryRecord, _compute_event_id, append_record
from scripts.daily_scheduler import SchedulerLock, TIMEZONE_NAME, _to_seoul


READ_ONLY_ENDPOINTS = frozenset(
    {
        "/api/dashboard/overview",
        "/api/dashboard/signals",
        "/api/dashboard/health",
        "/api/dashboard/daily-signal-board",
    }
)
EXPANDED_READ_ONLY_ENDPOINTS = frozenset({EXPANDED_BOARD_ENDPOINT})
OPERATIONS_ENDPOINTS = frozenset({"/api/operations/status", "/api/operations/history", "/api/operations/exceptions"})
MANUAL_RUN_ENDPOINT = "/api/operations/manual-run"
DAILY_REPORT_ENDPOINT = "/api/dashboard/expanded-shadow/daily-report"
DISCLOSURE_ANALYSIS_ENDPOINT = "/api/disclosure-analysis"
_DISCLOSURE_PRINT_SCRIPT = """
const printButton = document.getElementById("disclosure-print");
let detailStates;
window.addEventListener("beforeprint", () => {
  if (detailStates) return;
  detailStates = new Map([...document.querySelectorAll("details")].map(detail => [detail, detail.open]));
  detailStates.forEach((wasOpen, detail) => { detail.open = true; });
});
window.addEventListener("afterprint", () => {
  if (!detailStates) return;
  detailStates.forEach((wasOpen, detail) => { detail.open = wasOpen; });
  detailStates = undefined;
});
printButton.addEventListener("click", () => window.print());
"""
_DISCLOSURE_PRINT_HASH = base64.b64encode(
    hashlib.sha256(_DISCLOSURE_PRINT_SCRIPT.encode("utf-8")).digest(),
).decode("ascii")
_DISCLOSURE_SCRIPT_POLICY = f"script-src 'sha256-{_DISCLOSURE_PRINT_HASH}'; "
_DISCLOSURE_PRINT_STYLE = """
.print-toolbar{margin:0 0 24px}
.print-toolbar button{font:inherit;font-weight:700;cursor:pointer;padding:10px 18px;
  color:#164ea0;background:white;border:1px solid #164ea0;border-radius:8px}
.print-toolbar button:focus-visible{outline:3px solid #2563eb;outline-offset:4px}
@page{size:A4 portrait;margin:12mm}
@media print{
  html,body{background:white!important;color:#111;font-size:9pt;line-height:1.45}
  main{max-width:none;margin:0;padding:0}
  .print-toolbar,nav,main>header .notice,main>header .lead{display:none!important}
  .card{break-before:page;page-break-before:always;break-inside:avoid-page;
    page-break-inside:avoid;margin:0;padding:4mm;border:1px solid #aaa;
    border-radius:0;box-shadow:none;overflow:visible}
  .card-head{display:block;break-inside:avoid;break-after:avoid}
  h1{font-size:18pt}h2{font-size:15pt}h3{font-size:11pt;margin:4mm 0 2mm}
  h2,h3,summary{break-after:avoid-page}
  .badge{display:inline-block;font-size:9pt}
  .dates{grid-template-columns:repeat(2,minmax(0,1fr));margin:3mm 0}
  .metrics{grid-template-columns:repeat(3,minmax(0,1fr));gap:2mm;break-inside:avoid}
  .metric{display:block;padding:2mm}.metric strong{font-size:10pt;text-align:left}
  .columns{display:block}.links{margin:3mm 0}.links a{padding:1mm 2mm}
  details{padding:2mm;margin:2mm 0}
  .field-evidence,.source,.facts li,.unknown li,.cells li{break-inside:avoid-page}
  .facts li{padding:1mm 0}li{margin:1mm 0}.cells li{padding:1mm}
  .source{margin-top:2mm;padding-top:2mm}
  p,li{orphans:3;widows:3}
  code{font-size:8pt;overflow-wrap:anywhere}
  a{color:inherit}footer{margin-top:4mm}
}
"""
DAILY_REPORT_FORMATS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def route_dashboard_request(method: str, path: str, repo_root: Path, body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "GET"}
    parsed_path = urlparse(path).path
    if method.upper() == "POST" and parsed_path == MANUAL_RUN_ENDPOINT:
        return _manual_run_response(repo_root, body)
    if parsed_path == MANUAL_RUN_ENDPOINT:
        return _json_response(405, {"Content-Type": "application/json; charset=utf-8", "Allow": "POST"}, {"error": "method_not_allowed", "allowed_methods": ["POST"]})
    if method.upper() != "GET":
        return _json_response(405, headers, {"error": "method_not_allowed", "allowed_methods": ["GET"]})
    if parsed_path == DISCLOSURE_ANALYSIS_ENDPOINT:
        return _disclosure_analysis_response(repo_root, path)
    if parsed_path == DAILY_REPORT_ENDPOINT:
        return _daily_report_response(repo_root, path)
    is_detail = parsed_path.startswith("/api/operations/history/")
    is_exception_detail = parsed_path.startswith("/api/operations/exceptions/")
    if parsed_path not in READ_ONLY_ENDPOINTS and parsed_path not in EXPANDED_READ_ONLY_ENDPOINTS and parsed_path not in OPERATIONS_ENDPOINTS and parsed_path not in DUAL_READ_ONLY_ENDPOINTS and not is_detail and not is_exception_detail:
        return _json_response(404, headers, {"error": "not_found"})

    if parsed_path in DUAL_READ_ONLY_ENDPOINTS:
        service = DualShadowDashboardService(repo_root=repo_root)
        if parsed_path == "/api/dual-shadow/status":
            payload = service.status()
        elif parsed_path == "/api/dual-shadow/latest":
            payload = service.latest()
        elif parsed_path == "/api/dual-shadow/performance":
            payload = service.performance()
        else:
            payload = service.runs()
    elif parsed_path == "/api/operations/status":
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
        elif parsed_path == "/api/dashboard/daily-signal-board":
            payload = build_daily_signal_board(repo_root)
        elif parsed_path == EXPANDED_BOARD_ENDPOINT:
            payload = build_expanded_signal_board(repo_root)
        else:
            payload = service.health()
    return _json_response(200, headers, payload)


def _disclosure_analysis_response(repo_root: Path, path: str) -> tuple[int, dict[str, str], bytes]:
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; " + _DISCLOSURE_SCRIPT_POLICY +
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        ),
        "Allow": "GET",
    }
    snapshot = repo_root.resolve() / "output" / "dart_cards_preview.html"
    if urlparse(path).query:
        status, message = 400, "공시 분석은 고정 경로만 지원합니다. 파일 경로를 지정할 수 없습니다."
    elif snapshot.resolve() != snapshot:
        status, message = 403, "공시 분석 HTML 경로에 접근할 수 없습니다."
    else:
        try:
            document = snapshot.read_bytes().decode("utf-8")
            markers = ("</head>", "<main>", "</body>", "style-src 'unsafe-inline';")
            if any(document.count(marker) != 1 for marker in markers):
                raise ValueError("INVALID_DISCLOSURE_HTML_TEMPLATE")
            document = document.replace(
                "style-src 'unsafe-inline';",
                "style-src 'unsafe-inline'; " + _DISCLOSURE_SCRIPT_POLICY, 1,
            ).replace(
                "</head>", f"<style>{_DISCLOSURE_PRINT_STYLE}</style></head>", 1,
            ).replace(
                "<main>", '<main><div class="print-toolbar">'
                '<button id="disclosure-print" type="button">PDF 저장 / 인쇄</button>'
                '<p class="muted">A4 세로 · 전체 카드와 검증 근거 출력 · '
                '긴 카드는 다음 페이지로 이어집니다.</p></div>', 1,
            ).replace(
                "</body>", f"<script>{_DISCLOSURE_PRINT_SCRIPT}</script></body>", 1,
            )
            return 200, headers, document.encode("utf-8")
        except FileNotFoundError:
            status, message = 404, "저장된 공시 분석 HTML이 없습니다. 정상 HTML 생성 여부를 확인하세요."
        except OSError:
            status, message = 500, "공시 분석 HTML을 읽지 못했습니다. 파일 접근 권한과 상태를 확인하세요."
        except (UnicodeError, ValueError):
            status, message = 500, "공시 분석 HTML 형식이 올바르지 않아 인쇄 화면을 제공할 수 없습니다."
    error_html = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>공시 분석 조회 오류</title></head><body>"
        f"<h1>공시 분석 조회 오류</h1><p>{message}</p>"
        '<p><a href="/">대시보드로 돌아가기</a></p></body></html>'
    )
    return status, headers, error_html.encode("utf-8")


def _daily_report_response(repo_root: Path, path: str) -> tuple[int, dict[str, str], bytes]:
    error_headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "GET"}
    query = parse_qs(urlparse(path).query, keep_blank_values=True)
    date_values = query.get("date", [])
    if len(date_values) != 1 or not _ISO_DATE_PATTERN.match(date_values[0].strip()):
        return _json_response(400, error_headers, {"error_code": "INVALID_DATE", "error_message": "Query parameter 'date' is required as YYYY-MM-DD."})
    date_param = date_values[0].strip()
    try:
        datetime.strptime(date_param, "%Y-%m-%d")
    except ValueError:
        return _json_response(400, error_headers, {"error_code": "INVALID_DATE", "error_message": f"'date' is not a valid calendar date: {date_param}"})

    format_values = query.get("format", [])
    if len(format_values) != 1 or format_values[0] not in DAILY_REPORT_FORMATS:
        return _json_response(400, error_headers, {"error_code": "INVALID_FORMAT", "error_message": "Query parameter 'format' must be 'docx' or 'pdf'."})
    report_format = format_values[0]
    mode_values = query.get("mode", ["summary"])
    if len(mode_values) != 1 or mode_values[0] not in {"summary", "detail"}:
        return _json_response(400, error_headers, {"error_code": "INVALID_MODE", "error_message": "Query parameter 'mode' must be 'summary' or 'detail'."})
    mode = mode_values[0]

    model = build_daily_report_model(repo_root, source_date=date_param)
    if model.status != STATUS_READY:
        return _json_response(404, error_headers, {"error_code": f"REPORT_{model.status}", "error_message": "Daily report source data is not available.", "warnings": model.warnings})
    if model.new_candidates_status == NEW_CANDIDATES_NOT_FOUND:
        return _json_response(404, error_headers, {"error_code": "REPORT_DATE_NOT_FOUND", "error_message": f"No signal ledger data found for date {date_param}.", "warnings": model.warnings})

    if report_format == "docx":
        binary = build_summary_docx_report(model) if mode == "summary" else build_docx_report(model)
    else:
        binary = build_summary_pdf_report(model) if mode == "summary" else build_pdf_report(model)
    suffix = "_summary" if mode == "summary" else ""
    filename = f"BAIKAL_Daily_Report_{date_param}{suffix}.{report_format}"
    response_headers = {
        "Content-Type": DAILY_REPORT_FORMATS[report_format],
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Allow": "GET",
    }
    return 200, response_headers, binary


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

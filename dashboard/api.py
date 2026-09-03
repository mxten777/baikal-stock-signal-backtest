from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dashboard.adapter.service import DashboardService


READ_ONLY_ENDPOINTS = frozenset(
    {
        "/api/dashboard/overview",
        "/api/dashboard/signals",
        "/api/dashboard/health",
    }
)


def route_dashboard_request(method: str, path: str, repo_root: Path) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "GET"}
    parsed_path = urlparse(path).path
    if method.upper() != "GET":
        return _json_response(405, headers, {"error": "method_not_allowed", "allowed_methods": ["GET"]})
    if parsed_path not in READ_ONLY_ENDPOINTS:
        return _json_response(404, headers, {"error": "not_found"})

    service = DashboardService(repo_root=repo_root)
    if parsed_path == "/api/dashboard/overview":
        payload: dict[str, Any] = service.overview()
    elif parsed_path == "/api/dashboard/signals":
        payload = service.signals()
    else:
        payload = service.health()
    return _json_response(200, headers, payload)


def _json_response(status: int, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    return status, headers, json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    repo_root = Path.cwd()

    def do_GET(self) -> None:
        self._send(*route_dashboard_request("GET", self.path, self.repo_root))

    def do_POST(self) -> None:
        self._send(*route_dashboard_request("POST", self.path, self.repo_root))

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

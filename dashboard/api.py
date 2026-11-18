from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dashboard.investment_overview import build_investment_overview


SNAPSHOT_ROOT_ENV = "BAIKAL_EXPANDED_DATA_ROOT"
DISCLOSURE_ANALYSIS_ENDPOINT = "/api/disclosure-analysis"
_DISCLOSURE_PRINT_SCRIPT = """
const printButton = document.getElementById("disclosure-print");
const summaryButton = document.getElementById("disclosure-summary-print");
const disclosureHistory = document.getElementById("disclosure-history");
let disclosureDay;
function refreshDisclosureWindow() {
  if (!disclosureHistory) return;
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit"
  }).formatToParts(new Date()).map(part => [part.type, part.value]));
  const today = `${parts.year}-${parts.month}-${parts.day}`;
  if (today === disclosureDay) return;
  const cutoff = new Date(Date.UTC(+parts.year, +parts.month - 1, +parts.day - 29)).toISOString().slice(0, 10);
  const main = document.querySelector("main");
  const recentEnd = document.getElementById("disclosure-recent-end");
  const recentNav = document.getElementById("disclosure-recent-nav");
  const historyNav = document.getElementById("disclosure-history-nav");
  const cards = [...document.querySelectorAll("article.card")].sort((a, b) =>
    (b.dataset.disclosureDate + b.dataset.receipt).localeCompare(a.dataset.disclosureDate + a.dataset.receipt));
  recentNav.replaceChildren();
  historyNav.replaceChildren();
  let recentCount = 0;
  cards.forEach(card => {
    const recent = card.dataset.disclosureDate >= cutoff && card.dataset.disclosureDate <= today;
    const link = document.createElement("a");
    link.href = "#" + card.id;
    link.textContent = card.querySelector("h2").textContent;
    if (/^\\d{4}-\\d{2}-\\d{2}$/.test(card.dataset.disclosureDate)) {
      const stamp = document.createElement("small");
      stamp.className = "disclosure-nav-date";
      stamp.textContent = card.dataset.disclosureDate.slice(5).replace("-", ".");
      link.append(stamp);
    }
    (recent ? recentNav : historyNav).append(link);
    if (recent) {
      recentCount++;
      main.insertBefore(card, recentEnd);
    } else {
      disclosureHistory.append(card);
    }
  });
  document.getElementById("disclosure-recent-empty").hidden = recentCount !== 0;
  document.getElementById("disclosure-history-empty").hidden = recentCount === cards.length;
  disclosureDay = today;
}
refreshDisclosureWindow();
setInterval(refreshDisclosureWindow, 60000);
let detailStates;
function restorePrintState() {
  if (detailStates) {
    detailStates.forEach((wasOpen, detail) => { detail.open = wasOpen; });
    detailStates = undefined;
  }
  delete document.body.dataset.printMode;
  document.getElementById("disclosure-summary")?.remove();
}
function buildSummary() {
  const cards = [...document.querySelectorAll("main > article.card")];
  const receipts = cards.map(card => card.dataset.receipt);
  if (!cards.length || receipts.some(receipt => !receipt) || new Set(receipts).size !== cards.length) {
    throw new Error("요약 대상 카드가 없거나 접수번호가 중복되었습니다.");
  }
  const summary = document.createElement("div");
  summary.id = "disclosure-summary";
  function element(tag, text, parent) {
    const node = document.createElement(tag);
    node.textContent = text;
    parent.append(node);
    return node;
  }
  function required(card, selector) {
    const node = card.querySelector(selector);
    if (!node) throw new Error("요약에 필요한 카드 항목이 없습니다: " + selector);
    return node;
  }
  const cover = element("section", "", summary);
  cover.className = "summary-page summary-cover";
  element("h1", "공시 분석 요약", cover);
  element("p", "저장된 공시 기준 정적 스냅샷 · 상세본과 동일한 접수번호 기준", cover);
  const names = cards.map(card => required(card, "h2").textContent);
  const statuses = cards.map(card => required(card, ".badge").dataset.status);
  if (statuses.some(status => !["COMPLETE", "PARTIAL"].includes(status))) {
    throw new Error("지원하지 않는 카드 검증 상태입니다.");
  }
  element("h2", `${cards.length}건 / ${new Set(names).size}개 기업`, cover);
  element("p", `COMPLETE ${statuses.filter(s => s === "COMPLETE").length} / PARTIAL ${statuses.filter(s => s === "PARTIAL").length}`, cover);
  element("p", document.querySelector("main > header > p.muted")?.textContent ?? "", cover);
  element("h2", "검증 범위 및 읽는 방법", cover);
  element("p", "COMPLETE는 기본 추출 필드의 확인 상태이며, 추가 조건까지 모두 확인됐다는 뜻이 아닙니다. 미확인 항목은 조건의 부재를 의미하지 않습니다.", cover);
  element("p", "확인된 사실의 수치·단위·날짜는 상세본 그대로 표시합니다. 미확인 값은 추정하지 않으며 카드 상태를 변경하지 않습니다.", cover);
  element("p", "긴 원문 근거·XPath·본문 해시·추가 확인 체크리스트는 요약에서 생략합니다. 카드별 공식 링크 및 상세 PDF의 검증 근거와 주의사항을 참조하세요.", cover);
  element("h2", "종합 요약", cover);
  element("p", "아래는 공시별 저장 사실을 모은 목록입니다. 계약 이행, 조사 결과, 정정·변경 여부를 새로 검증한 자료가 아니며 투자 추천이나 가격 전망을 포함하지 않습니다.", cover);
  let page;
  cards.forEach((card, index) => {
    if (index % 3 === 0) {
      page = element("section", "", summary);
      page.className = "summary-page";
      element("h2", `공시별 핵심 사실 · ${Math.floor(index / 3) + 1}`, page);
    }
    const item = element("section", "", page);
    item.className = "summary-card";
    item.dataset.receipt = card.dataset.receipt;
    element("h3", names[index], item);
    element("p", required(card, ".eyebrow").textContent, item);
    element("p", required(card, ".disclosure").textContent, item);
    element("p", required(card, ".badge").textContent, item).className = "summary-status";
    element("p", "공시일: " + required(card, ".dates dd").textContent, item);
    element("h4", "확인된 사실 · VERIFIED", item);
    const facts = required(card, ".facts").cloneNode(true);
    facts.className = "summary-facts";
    item.append(facts);
    element("h4", "미확인 항목 (추가 조건 포함)", item);
    const unknown = [...card.querySelectorAll(".unknown li")].map(li => {
      const label = li.querySelector("strong");
      const status = li.querySelector(".tag");
      return label && status ? `${label.textContent} · ${status.textContent}` : li.textContent;
    });
    element("p", unknown.join(" / ") || "미확인 항목 없음", item);
    item.append(required(card, ".links").cloneNode(true));
    element("p", "원문 근거·해시·검증 한계 및 정정 관련 주의사항: 상세본 참조", item).className = "summary-reference";
  });
  return summary;
}
window.addEventListener("beforeprint", () => {
  if (document.body.dataset.printMode === "summary") return;
  if (detailStates) return;
  detailStates = new Map([...document.querySelectorAll("details")].map(detail => [detail, detail.open]));
  detailStates.forEach((wasOpen, detail) => { detail.open = true; });
});
window.addEventListener("afterprint", restorePrintState);
printButton.addEventListener("click", () => window.print());
summaryButton.addEventListener("click", () => {
  const error = document.getElementById("disclosure-print-error");
  error.textContent = "";
  try {
    restorePrintState();
    document.querySelector("main").append(buildSummary());
    document.body.dataset.printMode = "summary";
    window.print();
  } catch (failure) {
    restorePrintState();
    error.textContent = "요약 인쇄 실패: " + failure.message;
  }
});
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
#disclosure-summary{display:none}
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
  body[data-print-mode="summary"] main>*{display:none!important}
  body[data-print-mode="summary"] #disclosure-summary{display:block!important}
  .summary-page{break-before:page}
  .summary-page:first-child{break-before:auto}
  .summary-card{break-inside:avoid-page;border-bottom:1px solid #aaa;padding:2mm 0}
  .summary-page>h2{font-size:12pt;margin:0 0 2mm}
  .summary-card h3{font-size:11pt;margin:1mm 0}
  .summary-card h4{font-size:9pt;margin:1mm 0}
  .summary-card p,.summary-facts li{font-size:8pt;line-height:1.25;margin:.7mm 0}
  .summary-facts{display:grid;grid-template-columns:1fr 1fr;gap:0 5mm;padding-left:4mm;margin:1mm 0}
  .summary-facts li{break-inside:avoid-page;overflow-wrap:anywhere}
  .summary-facts .fact-label{display:inline;font-size:8pt;font-weight:700;margin-right:2mm}
  .summary-card .links{display:flex;margin:1mm 0}
  .summary-card .links a{font-size:8pt;padding:0;border:0;background:none}
  .summary-reference{color:#444}
  .summary-cover p{margin:5mm 0}
}
"""
def _snapshot_root(repo_root: Path) -> Path:
    configured = os.environ.get(SNAPSHOT_ROOT_ENV)
    if configured is None:
        return repo_root
    root = Path(configured)
    if not configured or not root.is_absolute() or not root.is_dir() or root.resolve() != root.absolute():
        raise ValueError(f"{SNAPSHOT_ROOT_ENV} must be an existing absolute, non-redirected directory")
    return root


def route_dashboard_request(method: str, path: str, repo_root: Path) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json; charset=utf-8", "Allow": "GET"}
    target = urlparse(path)
    is_disclosure = target.path == DISCLOSURE_ANALYSIS_ENDPOINT
    is_overview = target.path == "/api/dashboard/overview" and target.query == "view=investment"
    if not (is_disclosure or is_overview):
        return _json_response(404, headers, {"error": "not_found"})
    if method.upper() != "GET":
        return _json_response(405, headers, {"error": "method_not_allowed", "allowed_methods": ["GET"]})
    if is_disclosure:
        return _disclosure_analysis_response(repo_root, path)
    headers["Cache-Control"] = "no-store"
    try:
        snapshot_root = _snapshot_root(repo_root)
    except ValueError as exc:
        return _json_response(503, headers, {"error": str(exc)})
    return _json_response(200, headers, build_investment_overview(repo_root, snapshot_root))


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
                '<button id="disclosure-summary-print" type="button">요약 PDF 저장 / 인쇄</button>'
                '<p id="disclosure-print-error" role="alert"></p>'
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

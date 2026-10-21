from pathlib import Path

import pytest

from dashboard.api import (
    DISCLOSURE_ANALYSIS_ENDPOINT,
    _DISCLOSURE_PRINT_HASH,
    _DISCLOSURE_PRINT_SCRIPT,
    route_dashboard_request,
)


def snapshot_html(label="공시 분석"):
    return (
        "<!doctype html><html lang=\"ko\"><head>"
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\';">'
        f"</head><body><main>{label}</main></body></html>"
    ).encode("utf-8")


def test_disclosure_serves_current_snapshot_without_writing(tmp_path):
    snapshot = tmp_path / "output" / "dart_cards_preview.html"
    snapshot.parent.mkdir()
    content = snapshot_html()
    snapshot.write_bytes(content)
    before = snapshot.stat()

    status, headers, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)

    assert status == 200
    assert "공시 분석" in body.decode("utf-8")
    assert snapshot.read_bytes() == content
    assert 'id="disclosure-print"' in body.decode("utf-8")
    assert "PDF 저장 / 인쇄" in body.decode("utf-8")
    assert 'id="disclosure-summary-print"' in body.decode("utf-8")
    assert 'id="disclosure-print-error" role="alert"' in body.decode("utf-8")
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert snapshot.stat().st_mtime_ns == before.st_mtime_ns
    snapshot.write_bytes(snapshot_html("new snapshot"))
    assert b"new snapshot" in route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)[2]


def test_disclosure_print_policy_and_full_evidence(tmp_path):
    from lxml import html
    import base64
    import hashlib

    snapshot = tmp_path / "output" / "dart_cards_preview.html"
    snapshot.parent.mkdir()
    articles = "".join(
        f'<article class="card" data-receipt="{i}"><h2>기업 {i}</h2>'
        '<details><summary>VERIFIED</summary><p>검증 근거</p></details></article>'
        for i in range(23)
    )
    snapshot.write_bytes(snapshot_html(articles))
    status, headers, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)
    tree = html.fromstring(body.decode("utf-8"))
    script = tree.xpath("//script")[0].text
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    assert status == 200
    assert script == _DISCLOSURE_PRINT_SCRIPT
    assert digest == _DISCLOSURE_PRINT_HASH
    for policy in (headers["Content-Security-Policy"], tree.xpath("//meta/@content")[0]):
        assert f"script-src 'sha256-{digest}'" in policy
        assert "script-src 'unsafe-inline'" not in policy
    assert len(tree.xpath("//article")) == 23
    assert len(tree.xpath("//details")) == 23
    assert "beforeprint" in script and "afterprint" in script
    assert "detail.open = true" in script
    assert "detail.open = wasOpen" in script
    assert "window.print()" in script
    assert "@page{size:A4 portrait" in body.decode("utf-8")
    assert "break-before:page" in body.decode("utf-8")
    assert "break-inside:avoid-page" in body.decode("utf-8")
    assert "buildSummary()" in script
    assert 'required(card, ".facts").cloneNode(true)' in script
    assert 'card.querySelectorAll(".unknown li")' in script
    assert 'required(card, ".links").cloneNode(true)' in script
    assert 'document.body.dataset.printMode = "summary"' in script
    assert 'delete document.body.dataset.printMode' in script
    assert 'new Set(receipts).size !== cards.length' in script
    assert 'body[data-print-mode="summary"] main>*{display:none!important}' in body.decode("utf-8")


@pytest.mark.parametrize("content", [b"invalid", b"\xff", snapshot_html().replace(b"<main>", b"<main><main>")])
def test_disclosure_invalid_template_is_explicit(tmp_path, content):
    snapshot = tmp_path / "output" / "dart_cards_preview.html"
    snapshot.parent.mkdir()
    snapshot.write_bytes(content)
    status, _, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)
    assert status == 500
    assert "형식이 올바르지 않아" in body.decode("utf-8")
    assert snapshot.read_bytes() == content


def test_disclosure_missing_snapshot_is_clear_html_error(tmp_path):
    status, headers, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)
    assert status == 404
    assert headers["Content-Type"].startswith("text/html")
    assert "저장된 공시 분석 HTML이 없습니다." in body.decode("utf-8")
    assert not (tmp_path / "output").exists()


def test_disclosure_read_failure_is_explicit(tmp_path, monkeypatch):
    def denied(path):
        assert path.name == "dart_cards_preview.html"
        raise PermissionError("private filesystem details")

    monkeypatch.setattr(Path, "read_bytes", denied)
    status, _, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)
    assert status == 500
    assert "읽지 못했습니다" in body.decode("utf-8")
    assert b"private filesystem details" not in body


@pytest.mark.parametrize("suffix", [
    "?path=secret.txt", "?file=../../README.md", "?path=C:\\Windows\\win.ini",
    "/../secret.txt", "/%2e%2e/secret.txt", "/cards.sqlite3",
])
def test_disclosure_rejects_arbitrary_paths(tmp_path, suffix):
    (tmp_path / "secret.txt").write_text("PRIVATE", encoding="utf-8")
    status, _, body = route_dashboard_request("GET", DISCLOSURE_ANALYSIS_ENDPOINT + suffix, tmp_path)
    assert status in (400, 404)
    assert b"PRIVATE" not in body


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_disclosure_is_get_only(tmp_path, method):
    assert route_dashboard_request(method, DISCLOSURE_ANALYSIS_ENDPOINT, tmp_path)[0] == 405

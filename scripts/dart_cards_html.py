"""Render a static investor page from stored, evidence-verified DART cards."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lxml import etree, html as lxml_html

from scripts.dart_card_runner import select_pending
from scripts.dart_disclosure_query import query_disclosures
from src.dart_analysis_cards import CARD_STORE, FIELD_LABELS, SOURCE_STORE, query_cards

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "dart_cards_preview.html"
KST = timezone(timedelta(hours=9))
STATUS_LABELS = {
    "COMPLETE": "모든 추출 필드 확인",
    "PARTIAL": "일부 항목 미확인",
    "UNCONFIRMED": "미확인",
    "UNSUPPORTED": "추출 미지원",
}
REASON_LABELS = {
    "MISSING_OR_AMBIGUOUS_OR_UNSUPPORTED_CONDITION":
        "원문에서 값을 확정할 근거를 확인하지 못했습니다.",
}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _checked_url(url: str, receipt: str, *, document: bool = False) -> str:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    expected_path = "/report/viewer.do" if document else "/dsaf001/main.do"
    if (parsed.scheme != "https" or parsed.hostname != "dart.fss.or.kr"
            or parsed.path != expected_path or query.get("rcpNo") != [receipt]):
        raise ValueError("INVALID_DART_LINK")
    return url


def _latest_cards() -> list[dict]:
    with closing(sqlite3.connect(CARD_STORE.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        limit = max(1, conn.execute("SELECT COUNT(DISTINCT receipt) FROM attempts").fetchone()[0])
    results = query_cards(CARD_STORE, limit=limit)
    by_receipt = {}
    for result in results:
        card = result["card"]
        if card is None or card["status"] not in ("COMPLETE", "PARTIAL"):
            continue
        if result["previous_result_only"] or result["latest_attempt_status"] != card["status"]:
            card = {**card, "latest_attempt_status": result["latest_attempt_status"]}
        receipt = card["rcept_no"]
        if receipt in by_receipt:
            raise ValueError("DUPLICATE_LATEST_RECEIPT")
        by_receipt[receipt] = card
    return sorted(by_receipt.values(), key=lambda card: (card.get("rcept_dt") or "", card["rcept_no"]), reverse=True)


def _freshness(now: datetime) -> dict:
    source = query_disclosures(SOURCE_STORE, high=True, limit=1)
    if source["collection_time_status"] != "VERIFIED_STORED_SOURCE":
        raise ValueError("COLLECTION_TIME_NOT_VERIFIED")
    pending = select_pending(SOURCE_STORE, CARD_STORE, CARD_STORE.parent / "runs", now)
    cooldown = sum(item["reason"] == "RETRY_COOLDOWN" for item in pending["skipped"])
    exhausted = sum(item["reason"] == "RETRY_EXHAUSTED" for item in pending["skipped"])
    return {
        "collected_at": source["last_collection_success_at"],
        "pending": len(pending["selected"]) + cooldown + exhausted,
        "cooldown": cooldown,
        "exhausted": exhausted,
    }


def _kst_stamp(value: str) -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return stamp.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _field_label(field: dict) -> str:
    name = field["field"]
    return FIELD_LABELS.get(name, name)


def _field_value(field: dict) -> str:
    value = field["raw_value"]
    unit = {"KRW": "원", "%": "%", "persons": "명"}.get(field.get("unit"), "")
    return f"{value}{unit}"


def _evidence_markup(field: dict, receipt: str) -> str:
    items = []
    for evidence in field["evidence"]:
        url = _escape(_checked_url(evidence["url"], receipt, document=True))
        cells = "".join(f"<li>{_escape(cell)}</li>" for cell in evidence["cells"])
        items.append(
            '<div class="source"><p>원문 근거 · '
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">공식 본문 열기 ↗</a>'
            f"</p><p>XPath: <code>{_escape(evidence['xpath'])}</code></p>"
            f"<ol class=\"cells\">{cells}</ol>"
            f"<p>본문 SHA-256: <code>{_escape(evidence['sha256'])}</code></p>"
            f"<p>근거 확인: {_escape(evidence['checked_at'])}</p></div>"
        )
    if not items:
        return '<p class="muted">저장된 원문 근거가 없습니다.</p>'
    return "".join(items)


def _card_markup(card: dict) -> str:
    receipt = card["rcept_no"]
    original_url = _escape(_checked_url(card["original_url"], receipt))
    document_url = _escape(_checked_url(card["document"]["url"], receipt, document=True))
    fields = card["fields"] + card.get("conditions", [])
    verified = [field for field in fields if field["status"] == "VERIFIED"]
    unknown = [field for field in fields if field["status"] != "VERIFIED"]
    badge = "complete" if card["status"] == "COMPLETE" else "partial"
    note = (
        "모든 추출 필드가 VERIFIED입니다."
        if card["status"] == "COMPLETE"
        else "미확인·미지원 필드와 확인된 값을 구분해 표시합니다."
    )
    metrics = "".join(
        '<div class="metric"><span>'
        f"{_escape(_field_label(field))}</span><strong>{_escape(_field_value(field))}"
        "</strong></div>"
        for field in verified[:3]
    ) or '<p class="muted">확인된 주요 값이 없습니다.</p>'
    verified_list = "".join(
        f'<li><span class="fact-label">{_escape(_field_label(field))}</span>'
        f"<span>{_escape(_field_value(field))}</span></li>"
        for field in verified
    ) or '<li class="muted">검증된 필드가 없습니다.</li>'
    unknown_list = "".join(
        '<li><strong>'
        f"{_escape(_field_label(field))}</strong> "
        f'<span class="tag" data-status="{_escape(field["status"])}">'
        f'{_escape(STATUS_LABELS.get(field["status"], field["status"]))}</span>'
        f"<p>{_escape(REASON_LABELS.get(field.get('reason'), field.get('reason') or '미확인'))}</p>"
        "</li>"
        for field in unknown
    ) or '<li class="muted">미확인 필드가 없습니다.</li>'
    evidence = "".join(
        '<details class="field-evidence"><summary>'
        f'{_escape(_field_label(field))} · {_escape(field["status"])} · '
        f'근거 {len(field["evidence"])}개</summary>{_evidence_markup(field, receipt)}</details>'
        for field in fields
    )
    checklist = "".join(f"<li>{_escape(item)}</li>" for item in card["checklist"])
    cautions = "".join(f"<li>{_escape(item)}</li>" for item in card["cautions"])
    correction_note = (
        "<p class=\"muted\">정정공시입니다. 원공시 관계는 자동으로 연결하지 않았습니다.</p>"
        if card["is_correction"] else ""
    )
    previous_note = (
        f'<p class="state-note">이전 저장 카드 · 최근 시도 상태: {_escape(card["latest_attempt_status"])}</p>'
        if "latest_attempt_status" in card else ""
    )
    return (
        f'<article class="card" id="r{_escape(receipt)}" data-receipt="{_escape(receipt)}" '
        f'data-disclosure-date="{_escape(card.get("rcept_dt") or "")}">'
        '<header class="card-head"><div>'
        f'<p class="eyebrow">{_escape(card["ticker"])} · 접수번호 {_escape(receipt)}</p>'
        f'<h2>{_escape(card["name"])}</h2>'
        f'<p class="disclosure">{_escape(card["report_nm"].strip())}</p></div>'
        f'<span class="badge {badge}" data-status="{card["status"]}">'
        f'{_escape(card["status"])} · {_escape(STATUS_LABELS[card["status"]])}</span></header>'
        f'<p class="state-note">{_escape(note)}</p>{previous_note}'
        '<dl class="dates"><div><dt>공시일</dt>'
        f'<dd>{_escape(card.get("rcept_dt") or "미확인")}</dd></div><div><dt>마지막 본문 확인</dt>'
        f'<dd>{_escape(card["document"]["checked_at"])}</dd></div></dl>'
        f'<div class="metrics">{metrics}</div>'
        f'<p class="links"><a href="{original_url}" target="_blank" rel="noopener noreferrer">'
        "공식 DART 원문 열기 ↗</a>"
        f'<a href="{document_url}" target="_blank" rel="noopener noreferrer">'
        "분석한 공식 본문 열기 ↗</a></p>"
        f'<div class="columns"><section><h3>확인된 사실 <small>{len(verified)}개</small></h3>'
        f'<ul class="facts">{verified_list}</ul></section>'
        f'<section class="unknown"><h3>미확인 정보 <small>{len(unknown)}개</small></h3>'
        f"<ul>{unknown_list}</ul><p class=\"muted\">미확인은 해당 조건이 없다는 뜻이 아닙니다. "
        "확인되지 않은 값은 표시하거나 추정하지 않았습니다.</p></section></div>"
        f"{correction_note}"
        '<details class="evidence"><summary>저장된 원문 근거 확인 · '
        f'{card["verified_evidence_count"]}개 검증 필드</summary>'
        '<p>저장된 본문과 필드별 XPath·표 셀을 조회 시 검증한 결과입니다. '
        "원문을 다시 수집하지 않은 정적 기록입니다.</p>"
        f"{evidence}<details><summary>본문 식별 정보</summary>"
        f'<p>본문 SHA-256: <code>{_escape(card["body_sha256"])}</code></p>'
        f'<p>추출기 버전: <code>{_escape(card["extractor_version"])}</code></p>'
        f'<p>카드 생성: {_escape(card["generated_at"])}</p></details></details>'
        '<details class="cautions"><summary>추가 확인 및 해석상 주의사항</summary>'
        f'<h3>추가 확인 사항</h3><ul>{checklist}</ul>'
        f'<h3>주의사항</h3><ul>{cautions}</ul></details></article>'
    )


def render_document(cards: list[dict], template: str, generated_at: datetime, freshness: dict) -> str:
    stylesheet = re.search(r"<style>(.*?)</style>", template, flags=re.DOTALL)
    if stylesheet is None:
        raise ValueError("HTML_TEMPLATE_STYLESHEET_MISSING")
    extra_style = "@media print{#disclosure-history{display:none!important}}[hidden]{display:none!important}"
    style = stylesheet.group(1)
    if extra_style not in style:
        style += extra_style
    nav_style = (
        "#disclosure-recent-nav a,#disclosure-history-nav a{display:inline-flex;flex-direction:column}"
        ".disclosure-nav-date{font-size:.72em;font-weight:400;color:#64748b}"
    )
    if nav_style not in style:
        style += nav_style
    counts = Counter(card["status"] for card in cards)
    if (set(counts) - {"COMPLETE", "PARTIAL"}
            or len({card["rcept_no"] for card in cards}) != len(cards)):
        raise ValueError("HTML_CARD_COUNTS_MISMATCH")
    today = generated_at.astimezone(KST).date()
    cutoff = today - timedelta(days=29)
    ordered = sorted(cards, key=lambda card: (card.get("rcept_dt") or "", card["rcept_no"]), reverse=True)
    for card in ordered:
        disclosure_date = card.get("rcept_dt")
        if disclosure_date and date.fromisoformat(disclosure_date) > today:
            raise ValueError("DISCLOSURE_DATE_IN_FUTURE")
    recent = [card for card in ordered if card.get("rcept_dt") and date.fromisoformat(card["rcept_dt"]) >= cutoff]
    archive = [card for card in ordered if not card.get("rcept_dt") or date.fromisoformat(card["rcept_dt"]) < cutoff]

    def links(items: list[dict]) -> str:
        return "".join(
            f'<a href="#r{_escape(card["rcept_no"])}">{_escape(card["name"])}'
            + (f'<small class="disclosure-nav-date">{date.fromisoformat(card["rcept_dt"]):%m.%d}</small>'
               if card.get("rcept_dt") else "")
            + "</a>"
            for card in items
        )

    stamp = generated_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    latest_card = max((datetime.fromisoformat(card["generated_at"]) for card in cards), default=None)
    card_stamp = _kst_stamp(latest_card.isoformat()) if latest_card else "없음"
    collection_stamp = _kst_stamp(freshness["collected_at"])
    document = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<title>DART 투자자용 공시 분석 카드 | 저장 결과</title>'
        f"<style>{style}</style></head><body><main>"
        '<header><p class="eyebrow">BAIKAL · DART FACT CARDS · STEP 12-B</p>'
        '<h1>공시의 사실과 근거를 한눈에</h1>'
        '<p class="lead">공시일 기준 최근 30일의 저장 카드를 최신순으로 표시합니다. '
        "검증된 사실과 미확인 항목을 구분해 표시합니다.</p>"
        '<div class="notice"><strong>저장된 공시 기준 정적 스냅샷</strong>'
        "<p>이 화면은 생성 시점의 저장 카드만 표시하며 실시간 정보가 아닙니다. "
        "공시 원문을 다시 수집하거나 카드를 재분석하지 않았습니다. "
        "이후 정정·변경 여부는 공식 원문에서 확인하세요.</p></div>"
        f'<p class="muted">마지막 실제 DART 수집: {_escape(collection_stamp)} · '
        f'저장 카드의 최근 생성: {_escape(card_stamp)} · '
        f'미처리 지원 공시: {_escape(freshness["pending"])}건 '
        f'(재시도 대기 {_escape(freshness["cooldown"])}건 · '
        f'재시도 한도 초과 {_escape(freshness["exhausted"])}건)</p>'
        f'<p class="muted">보고서 생성: {_escape(stamp)} · '
        f'{len(cards)}건 저장 · 외부 라이브러리·스크립트·서버 없음</p></header>'
        '<details id="disclosure-history"><summary>30일 이전 공시 이력</summary>'
        f'<nav id="disclosure-history-nav" aria-label="과거 공시 카드 바로가기">{links(archive)}</nav>'
        f'<p id="disclosure-history-empty"{" hidden" if archive else ""}>30일 이전 공시 카드가 없습니다.</p>'
        f'{"".join(_card_markup(card) for card in archive)}</details>'
        f'<nav id="disclosure-recent-nav" aria-label="기업별 카드 바로가기">{links(recent)}</nav>'
        f'<p id="disclosure-recent-empty"{" hidden" if recent else ""}>최근 30일 공시 카드가 없습니다.</p>'
        f'{"".join(_card_markup(card) for card in recent)}'
        '<div id="disclosure-recent-end"></div>'
        '<footer>이 보고서는 생성 당시 저장된 카드의 정적 사본입니다. '
        "공식 링크를 열 때만 인터넷 연결이 필요합니다. "
        "이후 정정·변경과 실제 계약 이행·조사 결과는 별도로 확인해야 합니다.</footer>"
        "</main></body></html>\n"
    )
    try:
        tree = lxml_html.fromstring(
            document, parser=lxml_html.HTMLParser(no_network=True),
        )
    except (etree.ParserError, UnicodeError, LookupError) as exc:
        raise ValueError("HTML_RENDER_VALIDATION_FAILED") from exc
    rendered_cards = tree.xpath('//article[@class="card"]')
    if (len(rendered_cards) != len(cards)
            or len({item.get("data-receipt") for item in rendered_cards}) != len(cards)
            or len(tree.xpath('//a[starts-with(@href, "https://dart.fss.or.kr/")]')) < len(cards) * 2):
        raise ValueError("HTML_RENDER_VALIDATION_FAILED")
    return document


def _write_atomic(path: Path, document: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_html(
    output: Path = OUTPUT, *, template_path: Path | None = None,
) -> dict:
    template = template_path or output
    original_template = template.read_text(encoding="utf-8")
    cards = _latest_cards()
    now = datetime.now(KST)
    document = render_document(cards, original_template, now, _freshness(now))
    _write_atomic(output, document)
    counts = Counter(card["status"] for card in cards)
    return {
        "verdict": "PASS", "output": str(output.resolve()), "card_count": len(cards),
        "complete_count": counts["COMPLETE"], "partial_count": counts["PARTIAL"],
        "http_request_count": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--template", type=Path)
    args = parser.parse_args(argv)
    try:
        report = generate_html(args.output, template_path=args.template)
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        print(json.dumps({"verdict": "FAIL", "error": str(exc) or type(exc).__name__}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

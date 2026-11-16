from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from lxml import html

import pytest

from scripts import dart_cards_html

FRESHNESS = {
    "collected_at": "2026-10-21T11:20:00+00:00",
    "pending": 7, "cooldown": 1, "exhausted": 2,
}


def card(receipt: str, status: str) -> dict:
    field_status = "VERIFIED" if status == "COMPLETE" else "UNCONFIRMED"
    return {
        "rcept_no": receipt,
        "ticker": "000001",
        "name": f"기업 <{receipt}>",
        "report_nm": "단일판매ㆍ공급계약체결",
        "rcept_dt": "2026-10-21",
        "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "is_correction": False,
        "profile": "CONTRACT",
        "status": status,
        "generated_at": "2026-10-21T20:40:00+09:00",
        "body_sha256": "a" * 64,
        "extractor_version": "cards-v1:test",
        "document": {
            "url": (
                f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&dcmNo=1"
            ),
            "checked_at": "2026-10-21T20:40:00+09:00",
        },
        "fields": [{
            "field": "contract_amount",
            "status": field_status,
            "raw_value": "1000" if field_status == "VERIFIED" else None,
            "unit": "KRW",
            "reason": None if field_status == "VERIFIED" else "MISSING_OR_AMBIGUOUS_OR_UNSUPPORTED_CONDITION",
            "evidence": ([{
                "url": (
                    f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&dcmNo=1"
                ),
                "xpath": "/html/body/table/tr",
                "cells": ["계약금액", "1000"],
                "sha256": "a" * 64,
                "checked_at": "2026-10-21T20:40:00+09:00",
            }] if field_status == "VERIFIED" else []),
        }],
        "conditions": [],
        "verified_evidence_count": 1 if field_status == "VERIFIED" else 0,
        "checklist": ["추가 확인"],
        "cautions": ["주의"],
    }


def test_render_document_shows_verified_unknown_and_official_evidence():
    template = "<style>.card{color:#123}</style>"
    cards = [card(f"20261021{i:06d}", "COMPLETE") for i in range(13)]
    cards.extend(card(f"20261020{i:06d}", "PARTIAL") for i in range(10))

    document = dart_cards_html.render_document(
        cards, template, datetime(2026, 10, 21, 20, 50, tzinfo=timezone.utc),
        FRESHNESS,
    )

    assert document.count('<article class="card"') == 23
    assert 'data-status="COMPLETE"' in document
    assert 'data-status="PARTIAL"' in document
    assert "COMPLETE · 모든 추출 필드 확인" in document
    assert "PARTIAL · 일부 항목 미확인" in document
    assert "저장된 공시 기준 정적 스냅샷" in document
    assert "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" in document
    assert "https://dart.fss.or.kr/report/viewer.do?rcpNo=" in document
    assert "원문 근거" in document
    assert "1000원" in document
    assert "&lt;20261021" in document
    assert "원문에서 값을 확정할 근거를 확인하지 못했습니다." in document
    assert "None원" not in document
    assert "마지막 실제 DART 수집: 2026-10-21 20:20:00 KST" in document
    assert "저장 카드의 최근 생성: 2026-10-21 20:40:00 KST" in document
    assert "미처리 지원 공시: 7건 (재시도 대기 1건 · 재시도 한도 초과 2건)" in document


def test_render_document_rejects_unexpected_card_counts():
    duplicate = card("20261021000001", "COMPLETE")
    with pytest.raises(ValueError, match="HTML_CARD_COUNTS_MISMATCH"):
        dart_cards_html.render_document(
            [duplicate, duplicate],
            "<style>.card{}</style>",
            datetime.now(timezone.utc),
            FRESHNESS,
        )


def test_recent_30_days_boundary_history_sort_and_empty():
    cards = [
        {**card("20261018000001", "COMPLETE"), "rcept_dt": "2026-10-18"},
        {**card("20261019000001", "PARTIAL"), "rcept_dt": "2026-10-19"},
        {**card("20261117000001", "COMPLETE"), "rcept_dt": "2026-11-17"},
    ]
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    tree = html.fromstring(dart_cards_html.render_document(cards, "<style></style>", now, FRESHNESS))
    assert tree.xpath("//main/article/@data-receipt") == ["20261117000001", "20261019000001"]
    assert tree.xpath('//*[@id="disclosure-history"]/article/@data-receipt') == ["20261018000001"]
    assert tree.xpath('//*[@id="disclosure-recent-empty"]/@hidden')
    empty = html.fromstring(dart_cards_html.render_document(cards[:1], "<style></style>", now, FRESHNESS))
    assert not empty.xpath("//main/article")
    assert not empty.xpath('//*[@id="disclosure-recent-empty"]/@hidden')
    assert empty.xpath('//*[@id="disclosure-history"]/article/@data-receipt') == ["20261018000001"]


def test_navigation_shows_disclosure_date_without_inventing_missing_date():
    dated = {**card("20261116000001", "COMPLETE"), "rcept_dt": "2026-11-16"}
    undated = {**card("20261018000001", "PARTIAL"), "rcept_dt": None}
    tree = html.fromstring(dart_cards_html.render_document(
        [dated, undated], "<style></style>", datetime(2026, 11, 17, tzinfo=timezone.utc), FRESHNESS,
    ))
    recent = tree.xpath('//*[@id="disclosure-recent-nav"]/a')[0]
    assert recent.get("href") == "#r20261116000001"
    assert recent.xpath("./small/text()") == ["11.16"]
    history = tree.xpath('//*[@id="disclosure-history-nav"]/a')[0]
    assert history.get("href") == "#r20261018000001"
    assert not history.xpath("./small")
    assert tree.xpath('//*[@id="r20261018000001"]//dl/div[1]/dd/text()') == ["미확인"]


def test_generation_failure_preserves_existing_html(tmp_path, monkeypatch):
    output = tmp_path / "cards.html"
    output.write_text("existing snapshot", encoding="utf-8")

    def fail_query(*args, **kwargs):
        assert args and kwargs
        raise ValueError("QUERY_FAILED")

    store = tmp_path / "cards.sqlite3"
    with sqlite3.connect(store) as connection:
        connection.execute("CREATE TABLE attempts (receipt TEXT)")
    monkeypatch.setattr(dart_cards_html, "CARD_STORE", store)
    monkeypatch.setattr(dart_cards_html, "query_cards", fail_query)

    with pytest.raises(ValueError, match="QUERY_FAILED"):
        dart_cards_html.generate_html(output)

    assert output.read_text(encoding="utf-8") == "existing snapshot"
    assert set(tmp_path.iterdir()) == {output, store}


def test_render_validation_failure_preserves_existing_html(tmp_path, monkeypatch):
    output = tmp_path / "cards.html"
    output.write_text("<style>.old{color:#123}</style>", encoding="utf-8")
    monkeypatch.setattr(
        dart_cards_html, "_latest_cards", lambda: [
            card("20261021000001", "COMPLETE"), card("20261021000001", "COMPLETE"),
        ],
    )
    monkeypatch.setattr(dart_cards_html, "_freshness", lambda _now: FRESHNESS)

    with pytest.raises(ValueError, match="HTML_CARD_COUNTS_MISMATCH"):
        dart_cards_html.generate_html(output)

    assert output.read_text(encoding="utf-8") == "<style>.old{color:#123}</style>"
    assert list(tmp_path.iterdir()) == [output]


def test_freshness_counts_retryable_and_exhausted_backlog(monkeypatch):
    monkeypatch.setattr(dart_cards_html, "query_disclosures", lambda *_a, **_kw: {
        "collection_time_status": "VERIFIED_STORED_SOURCE",
        "last_collection_success_at": FRESHNESS["collected_at"],
    })
    monkeypatch.setattr(dart_cards_html, "select_pending", lambda *_a: {
        "selected": ["a", "b", "c", "d"],
        "skipped": [
            {"reason": "RETRY_COOLDOWN"},
            {"reason": "RETRY_EXHAUSTED"},
            {"reason": "RETRY_EXHAUSTED"},
            {"reason": "ALREADY_PROCESSED"},
        ],
    })
    assert dart_cards_html._freshness(datetime.now(timezone.utc)) == FRESHNESS


def test_atomic_replace_failure_preserves_existing_html(tmp_path, monkeypatch):
    output = tmp_path / "cards.html"
    output.write_text("existing snapshot", encoding="utf-8")

    def fail_replace(*args):
        assert len(args) == 2
        raise OSError("replace failed")

    monkeypatch.setattr(dart_cards_html.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        dart_cards_html._write_atomic(output, "new snapshot")

    assert output.read_text(encoding="utf-8") == "existing snapshot"
    assert list(tmp_path.iterdir()) == [output]

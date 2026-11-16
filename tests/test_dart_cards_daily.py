from __future__ import annotations

import subprocess

from scripts import dart_cards_daily, dart_cards_html


def sample_card(index: int, status: str) -> dict:
    receipt = f"20261021{index:06d}"
    return {
        "rcept_no": receipt,
        "ticker": "000001",
        "name": f"기업 {index}",
        "report_nm": "단일판매ㆍ공급계약체결",
        "rcept_dt": "2026-10-21",
        "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "is_correction": False,
        "status": status,
        "generated_at": "2026-10-21T20:40:00+09:00",
        "body_sha256": "a" * 64,
        "extractor_version": "cards-v1:test",
        "document": {
            "url": f"https://dart.fss.or.kr/report/viewer.do?rcpNo={receipt}&dcmNo=1",
            "checked_at": "2026-10-21T20:40:00+09:00",
        },
        "fields": [],
        "conditions": [],
        "verified_evidence_count": 0,
        "checklist": [],
        "cautions": [],
    }


def install_sample_cards(monkeypatch):
    cards = [sample_card(index, "COMPLETE") for index in range(13)]
    cards.extend(sample_card(index + 13, "PARTIAL") for index in range(10))
    monkeypatch.setattr(dart_cards_html, "_latest_cards", lambda: cards)
    monkeypatch.setattr(dart_cards_html, "_freshness", lambda _now: {
        "collected_at": "2026-10-21T11:20:00+00:00",
        "pending": 0, "cooldown": 0, "exhausted": 0,
    })


def test_success_runs_existing_html_generator_after_analysis(tmp_path, monkeypatch):
    install_sample_cards(monkeypatch)
    output = tmp_path / "cards.html"
    template = tmp_path / "template.html"
    output.write_text("old snapshot", encoding="utf-8")
    template.write_text("<style>.card{color:#123}</style>", encoding="utf-8")
    calls = []

    def run_command(command, **kwargs):
        assert kwargs == {"cwd": dart_cards_daily.ROOT, "check": False}
        calls.append((command, kwargs))
        if command == dart_cards_daily.CARD_ANALYSIS_COMMAND:
            return subprocess.CompletedProcess(command, 0)
        assert command == dart_cards_daily.HTML_GENERATION_COMMAND
        report = dart_cards_html.generate_html(output, template_path=template)
        assert report["verdict"] == "PASS"
        return subprocess.CompletedProcess(command, 0)

    result = dart_cards_daily.run_pipeline(run_command)

    assert result == {
        "verdict": "PASS",
        "exit_code": 0,
        "stage": "COMPLETE",
        "card_analysis_exit_code": 0,
        "html_generation_exit_code": 0,
    }
    assert [call[0] for call in calls] == [
        dart_cards_daily.CARD_ANALYSIS_COMMAND, dart_cards_daily.HTML_GENERATION_COMMAND,
    ]
    assert calls[0][1] == {"cwd": dart_cards_daily.ROOT, "check": False}
    assert calls[1][1] == {"cwd": dart_cards_daily.ROOT, "check": False}
    assert "기업 0" in output.read_text(encoding="utf-8")


def test_analysis_failure_preserves_html_and_skips_generator(tmp_path):
    output = tmp_path / "cards.html"
    output.write_text("old snapshot", encoding="utf-8")
    calls = []

    def run_command(command, **kwargs):
        assert kwargs == {"cwd": dart_cards_daily.ROOT, "check": False}
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    result = dart_cards_daily.run_pipeline(run_command)

    assert result["verdict"] == "FAIL"
    assert result["stage"] == "CARD_ANALYSIS"
    assert result["html_generation_exit_code"] is None
    assert calls == [dart_cards_daily.CARD_ANALYSIS_COMMAND]
    assert output.read_text(encoding="utf-8") == "old snapshot"


def test_analysis_deferred_preserves_html_and_skips_generator(tmp_path):
    output = tmp_path / "cards.html"
    output.write_text("old snapshot", encoding="utf-8")
    calls = []

    def run_command(command, **kwargs):
        assert kwargs == {"cwd": dart_cards_daily.ROOT, "check": False}
        calls.append(command)
        return subprocess.CompletedProcess(command, 2)

    result = dart_cards_daily.run_pipeline(run_command)

    assert result["verdict"] == "DEFERRED"
    assert result["stage"] == "CARD_ANALYSIS"
    assert result["html_generation_exit_code"] is None
    assert calls == [dart_cards_daily.CARD_ANALYSIS_COMMAND]
    assert output.read_text(encoding="utf-8") == "old snapshot"


def test_html_generation_failure_returns_failure(tmp_path):
    output = tmp_path / "cards.html"
    output.write_text("old snapshot", encoding="utf-8")

    def run_command(command, **kwargs):
        assert kwargs == {"cwd": dart_cards_daily.ROOT, "check": False}
        code = 0 if command == dart_cards_daily.CARD_ANALYSIS_COMMAND else 1
        return subprocess.CompletedProcess(command, code)

    result = dart_cards_daily.run_pipeline(run_command)

    assert result["verdict"] == "FAIL"
    assert result["stage"] == "HTML_GENERATION"
    assert result["card_analysis_exit_code"] == 0
    assert result["html_generation_exit_code"] == 1
    assert output.read_text(encoding="utf-8") == "old snapshot"

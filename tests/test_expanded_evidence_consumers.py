from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from dashboard import expanded_daily_report, expanded_signal_board
from dashboard.expanded_display import build_easy_stock_analysis
from dashboard.expanded_evidence import (
    EVIDENCE_PARTIAL,
    EVIDENCE_UNAVAILABLE,
    DecisionEvidence,
    ExpandedPerformanceEvidence,
    ExpandedSignalRecord,
)
from dashboard.expanded_daily_report import build_daily_report_model
from dashboard.expanded_signal_board import build_expanded_signal_board
from src.expanded_company_profiles import CompanyProfile
from src.expanded_shadow_ledger import LEDGER_FIELDS


SOURCE_DATE = "2026-09-17"


def _record(*, ticker: str, decision: str, profile: CompanyProfile | None) -> ExpandedSignalRecord:
    excluded = decision == "EXCLUDED"
    return ExpandedSignalRecord(
        basDd=SOURCE_DATE,
        ticker=ticker,
        stock_name=f"Ledger {ticker}",
        market="KOSDAQ" if excluded else "KOSPI",
        signal_date=SOURCE_DATE,
        signal_price=100.5,
        raw_score=52,
        signal_score=80.0,
        signal_type="BUY_WATCH",
        profile=profile,
        evidence=DecisionEvidence(
            signal_reason="Score crossed threshold: 73.8 -> 80.0 (threshold 75)",
            prev_score=73.8,
            current_score=80.0,
            trend_score=None if excluded else 25,
            volume_score=None if excluded else 15,
            momentum_score=None if excluded else 12,
            foreign_status="NEGATIVE" if excluded else "POSITIVE",
            foreign_5d_ratio=-0.2 if excluded else 0.2,
            decision=decision,
            decision_reason="FOREIGN_NEGATIVE" if excluded else "Foreign status POSITIVE is not NEGATIVE; existing rule classifies as CANDIDATE.",
            evidence_status=EVIDENCE_UNAVAILABLE if excluded else EVIDENCE_PARTIAL,
        ),
        performance=None
        if excluded
        else ExpandedPerformanceEvidence(
            tracking_status="5D",
            return_5d=1.25,
            benchmark_5d=0.5,
            excess_5d=0.75,
            return_10d=None,
            benchmark_10d=None,
            excess_10d=None,
            return_20d=None,
            benchmark_20d=None,
            excess_20d=None,
        ),
    )


def _profile() -> CompanyProfile:
    return CompanyProfile(
        ticker="000001",
        company_name="Profile Name",
        market="KOSPI",
        one_line_description="업종은 반도체이며, 주요 제품은 메모리입니다.",
        sector="반도체",
        main_business_products="메모리",
        market_cap=None,
        market_cap_date=None,
        profile_as_of="2026-10-02",
        source="KRX_KIND_LISTING; one_line_description=GENERATED_TEMPLATE",
        collected_at="2026-10-02T01:00:00+00:00",
    )


def _write_inputs(root: Path) -> None:
    manifest = {
        "run_id": "run-1",
        "basDd": SOURCE_DATE,
        "status": "SUCCESS",
        "canonical_universe_count": 574,
        "attempted_ticker_count": 574,
        "ready_count": 574,
        "signal_count": 2,
        "new_candidate_count": 1,
    }
    manifest_path = root / "output/expanded_shadow/expanded_shadow_run.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rows = []
    for ticker, decision in (("000001", "CANDIDATE"), ("000002", "EXCLUDED")):
        row = {field: "" for field in LEDGER_FIELDS}
        row.update(
            {
                "basDd": SOURCE_DATE,
                "stock_code": ticker,
                "stock_name": f"Ledger {ticker}",
                "market": "KOSPI" if ticker == "000001" else "KOSDAQ",
                "signal_date": SOURCE_DATE,
                "signal_price": 100.5,
                "raw_score": 52,
                "signal_score": 80.0,
                "signal_type": "BUY_WATCH",
                "foreign_5d_ratio": 0.2 if decision == "CANDIDATE" else -0.2,
                "foreign_status": "POSITIVE" if decision == "CANDIDATE" else "NEGATIVE",
                "decision": decision,
                "exclusion_reason": "FOREIGN_NEGATIVE" if decision == "EXCLUDED" else "",
                "engine_version": "v0.1",
                "source_commit": "test",
                "run_id": "run-1",
                "created_at": "2026-09-17T00:00:00+00:00",
            }
        )
        rows.append(row)
    signal_path = root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    with signal_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dashboard_and_report_share_additive_records_and_fallbacks(tmp_path: Path, monkeypatch):
    _write_inputs(tmp_path)
    shared_records = [
        _record(ticker="000001", decision="CANDIDATE", profile=_profile()),
        _record(ticker="000002", decision="EXCLUDED", profile=None),
    ]
    builder_calls: list[tuple[Path, str | None]] = []

    def shared_builder(repo_root: Path, *, basdd: str | None = None):
        builder_calls.append((Path(repo_root), basdd))
        return shared_records, ["one shared evidence warning"]

    monkeypatch.setattr(expanded_signal_board, "build_expanded_signal_records", shared_builder)
    monkeypatch.setattr(expanded_daily_report, "build_expanded_signal_records", shared_builder)

    manifest_path = tmp_path / "output/expanded_shadow/expanded_shadow_run.json"
    signal_path = tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    before = {manifest_path: _sha256(manifest_path), signal_path: _sha256(signal_path)}

    board = build_expanded_signal_board(tmp_path)
    report = build_daily_report_model(tmp_path)

    assert builder_calls == [(tmp_path, SOURCE_DATE), (tmp_path, SOURCE_DATE)]
    assert board["new_candidates"]["count"] == 1
    assert set(board["new_candidates"]["records"][0]) == {
        "stock_name", "ticker", "market", "signal_date", "entry_price", "signal_score", "signal_type", "foreign_status"
    }
    assert board["new_candidates"]["records"][0]["entry_price"] == 100.5
    assert board["new_candidates"]["records"][0]["signal_type"] == "BUY_WATCH"
    assert len(board["signal_records"]) == 2
    candidate, excluded = board["signal_records"]
    assert candidate["company_profile"] == {
        "company_name": "Profile Name",
        "sector": "반도체",
        "main_business_products": "메모리",
        "one_line_description": "업종은 반도체이며, 주요 제품은 메모리입니다.",
        "market_cap": None,
        "market_cap_date": None,
        "profile_as_of": "2026-10-02",
        "source": "KRX_KIND_LISTING; one_line_description=GENERATED_TEMPLATE",
    }
    assert candidate["decision_evidence"]["evidence_status"] == EVIDENCE_PARTIAL
    assert candidate["decision_evidence"]["delta_score"] is None
    assert candidate["easy_analysis"] == asdict(build_easy_stock_analysis(shared_records[0]))
    assert candidate["performance"]["return_5d"] == 1.25
    assert candidate["performance"]["benchmark_5d"] == 0.5
    assert candidate["performance"]["excess_5d"] == 0.75
    assert candidate["performance"]["tracking_status"] == "5D"
    assert excluded["company_profile"]["company_name"] == "Ledger 000002"
    assert excluded["company_profile"]["source"] == "PROFILE_UNAVAILABLE"
    assert excluded["decision_evidence"]["decision"] == "EXCLUDED"
    assert excluded["decision_evidence"]["evidence_status"] == EVIDENCE_UNAVAILABLE
    assert excluded["decision_evidence"]["decision_reason"] == "FOREIGN_NEGATIVE"
    assert excluded["performance"] is None
    assert excluded["easy_analysis"] == asdict(build_easy_stock_analysis(shared_records[1]))
    assert board["warnings"] == []
    assert board["signal_records_warnings"] == ["one shared evidence warning"]

    assert len(report.new_candidates) == 1
    assert report.expanded_signals == shared_records
    assert report.expanded_signals[0] is shared_records[0]
    assert report.expanded_signals[1].performance is None
    assert report.warnings == []
    assert report.expanded_signal_warnings == ["one shared evidence warning"]
    assert before == {path: _sha256(path) for path in before}


def test_signal_records_empty_when_run_manifest_is_missing(tmp_path: Path):
    payload = build_expanded_signal_board(tmp_path)

    assert payload["signal_records"] == []
    assert payload["signal_records_warnings"] == []


def test_easy_analysis_explains_verified_evidence_without_mutation():
    record = _record(ticker="000001", decision="CANDIDATE", profile=_profile())
    record = replace(
        record,
        raw_score=58,
        signal_score=89.2,
        signal_type="STRONG_WATCH",
        evidence=replace(
            record.evidence,
            evidence_status="AVAILABLE",
            signal_reason="Score crossed threshold: 66.2 -> 89.2 (threshold 75)",
            prev_score=66.2,
            current_score=89.2,
            trend_score=25,
            volume_score=20,
            momentum_score=13,
            foreign_5d_ratio=0.4868349737371525,
        ),
        performance=replace(record.performance, tracking_status="OPEN"),
    )
    before = asdict(record)
    analysis = build_easy_stock_analysis(record)
    assert "66.2에서 89.2" in analysis.summary
    assert "관심 기준 75점" in analysis.summary
    assert "STRONG_WATCH" in analysis.summary
    assert "CANDIDATE" in analysis.summary
    assert any("추세 25/25점" in item for item in analysis.positives)
    assert any("거래량 20/20점" in item and "2배 이상" in item for item in analysis.positives)
    assert any("모멘텀 13/20점" in item for item in analysis.positives)
    assert any("48.7%" in item and "지분율이 아닙니다" in item for item in analysis.positives)
    assert any("성과는 측정 중" in item for item in analysis.risks)
    assert any("시가총액" in item for item in analysis.checks)
    assert any(SOURCE_DATE in item for item in analysis.sources)
    assert any("2026-10-02" in item and "KRX_KIND_LISTING" in item for item in analysis.sources)
    assert "규칙 기반" in analysis.method
    assert "AI API를 사용하지 않습니다" in analysis.method
    assert "매수·매도 추천이 아닙니다" in analysis.disclaimer
    assert asdict(record) == before


@pytest.mark.parametrize("status", ["PARTIAL", "UNAVAILABLE"])
def test_easy_analysis_does_not_assert_technical_improvement_for_incomplete_evidence(status):
    record = _record(ticker="000001", decision="CANDIDATE", profile=_profile())
    record = replace(record, evidence=replace(record.evidence, evidence_status=status))
    analysis = build_easy_stock_analysis(record)
    assert "새로 충족" not in analysis.summary
    assert not any("추세 25/25점" in item for item in analysis.positives)
    assert any("확정적으로 해석하지 않습니다" in item for item in analysis.risks)
    assert any("해석을 보류" in item for item in analysis.checks)
    assert any("POSITIVE" in item for item in analysis.positives)


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf")])
def test_easy_analysis_explicitly_marks_missing_numeric_evidence(missing):
    record = _record(ticker="000001", decision="CANDIDATE", profile=None)
    record = replace(
        record,
        signal_score=missing,
        performance=None,
        evidence=replace(
            record.evidence,
            evidence_status="UNAVAILABLE",
            prev_score=missing,
            current_score=missing,
            trend_score=missing,
            volume_score=missing,
            momentum_score=missing,
            foreign_5d_ratio=missing,
        ),
    )
    analysis = build_easy_stock_analysis(record)
    assert "Score는 확인 불가" in analysis.summary
    assert analysis.positives == ("현재 확인된 근거로 설명할 긍정 요인은 없습니다.",)
    for label in ("추세", "거래량", "모멘텀", "외국인", "기업정보", "전일 점수", "당일 근거 점수", "성과"):
        assert any(label in item and "확인 불가" in item for item in analysis.checks)
    assert any("기업정보 기준일: 확인 불가 / 출처: 확인 불가" == item for item in analysis.sources)
    assert "nan" not in str(analysis)
    assert "inf" not in str(analysis)


def test_easy_analysis_preserves_exclusion_and_overheated_warning():
    record = _record(ticker="000002", decision="EXCLUDED", profile=None)
    record = replace(record, signal_type="OVERHEATED")
    analysis = build_easy_stock_analysis(record)
    assert "EXCLUDED" in analysis.summary
    assert "OVERHEATED" in analysis.summary
    assert any("순매도" in item for item in analysis.risks)
    assert any("되돌림 위험" in item for item in analysis.risks)
    assert not any("성과 데이터" in item for item in analysis.checks)


def test_easy_analysis_neutral_or_inconsistent_flow_never_invents_positive_flow():
    record = _record(ticker="000001", decision="CANDIDATE", profile=_profile())
    for status, ratio in (("NEUTRAL", 0.01), ("POSITIVE", -0.2), (None, 0.2)):
        analysis = build_easy_stock_analysis(
            replace(record, evidence=replace(record.evidence, foreign_status=status, foreign_5d_ratio=ratio))
        )
        assert not any("누적 순매수" in item for item in analysis.positives)
        assert any("외국인" in item for item in analysis.checks)


def test_easy_analysis_does_not_assert_crossing_when_available_values_disagree():
    record = _record(ticker="000001", decision="CANDIDATE", profile=_profile())
    record = replace(
        record,
        evidence=replace(record.evidence, evidence_status="AVAILABLE", current_score=90.0),
    )
    analysis = build_easy_stock_analysis(record)
    assert "새로 충족" not in analysis.summary
    assert any("일치 여부" in item for item in analysis.checks)
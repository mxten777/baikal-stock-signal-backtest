from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from dashboard import expanded_daily_report, expanded_signal_board
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
            excess_5d=0.75,
            return_10d=None,
            excess_10d=None,
            return_20d=None,
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
    assert candidate["performance"]["return_5d"] == 1.25
    assert candidate["performance"]["excess_5d"] == 0.75
    assert candidate["performance"]["tracking_status"] == "5D"
    assert excluded["company_profile"]["company_name"] == "Ledger 000002"
    assert excluded["company_profile"]["source"] == "PROFILE_UNAVAILABLE"
    assert excluded["decision_evidence"]["decision"] == "EXCLUDED"
    assert excluded["decision_evidence"]["evidence_status"] == EVIDENCE_UNAVAILABLE
    assert excluded["decision_evidence"]["decision_reason"] == "FOREIGN_NEGATIVE"
    assert excluded["performance"] is None
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
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path

from docx import Document
from PyPDF2 import PdfReader
from PyPDF2.generic import ContentStream
import pytest

from dashboard.daily_report_docx import build_docx_report
from dashboard.daily_report_model import (
    STATUS_READY,
    DailyReportModel,
    NewCandidateRecord,
    PerformanceRecord,
    RunSummary,
)
from dashboard.daily_report_pdf import build_pdf_report
from dashboard.expanded_display import (
    build_easy_stock_analysis,
    candidate_summary_rows,
    display_decision_reason,
    display_evidence_status,
    display_signal_reason,
    display_tracking_status,
)
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

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("status", ["AVAILABLE", "PARTIAL", "UNAVAILABLE"])
def test_easy_analysis_matches_shared_dashboard_content_in_both_reports(status):
    model = build_daily_report_model(REPO_ROOT, source_date="2026-10-08")
    record = next(record for record in model.expanded_signals if record.ticker == "373220")
    if status != "AVAILABLE":
        record = replace(
            record,
            profile=None,
            evidence=replace(
                record.evidence,
                evidence_status=status,
                prev_score=None,
                trend_score=None,
                volume_score=None,
                momentum_score=None,
            ),
        )
    model = replace(model, new_candidates=[], performance=[], expanded_signals=[record])
    before = asdict(model)
    analysis = build_easy_stock_analysis(record)
    if status == "AVAILABLE":
        board = build_expanded_signal_board(REPO_ROOT)
        dashboard_record = next(row for row in board["signal_records"] if row["ticker"] == "373220")
        assert dashboard_record["easy_analysis"] == asdict(analysis)
        assert record.signal_score == 89.2
        assert record.signal_type == "STRONG_WATCH"
        assert record.evidence.decision == "CANDIDATE"
        assert "66.2에서 89.2" in analysis.summary
    else:
        assert "새로 충족" not in analysis.summary
        assert any("확인 불가" in item for item in analysis.checks)

    docx_data = build_docx_report(model)
    pdf_data = build_pdf_report(model)
    docx_text = _squash(_docx_all_text(docx_data))
    pdf_text = _squash(_pdf_cid_text(pdf_data))
    for text in (
        "쉬운 종목 분석", "핵심 요약", "긍정 요인", "위험 요인",
        "추가 확인 사항", "데이터 출처·기준일",
        analysis.method, analysis.summary, analysis.disclaimer,
        *analysis.positives, *analysis.risks, *analysis.checks, *analysis.sources,
    ):
        assert _squash(text) in docx_text
        assert _squash(text) in pdf_text
    assert asdict(model) == before
    assert b"HYGothic-Medium" in pdf_data
    document = Document(BytesIO(docx_data))
    style = document.styles["Easy Analysis"]
    assert style.font.name == "Malgun Gothic"
    assert style.element.rPr.rFonts.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia") == "Malgun Gothic"
    assert style.paragraph_format.widow_control is True
    assert document.styles["Easy Analysis Heading"].paragraph_format.keep_with_next is True


def test_easy_analysis_long_korean_source_wraps_and_splits_across_pdf_pages():
    model = build_daily_report_model(REPO_ROOT, source_date="2026-10-08")
    record = next(record for record in model.expanded_signals if record.ticker == "373220")
    long_source = "긴 한국어 출처와 설명 확인 <태그> & 원문 " * 300
    record = replace(record, profile=replace(record.profile, source=long_source))
    model = replace(model, new_candidates=[], performance=[], expanded_signals=[record])
    analysis = build_easy_stock_analysis(record)
    pdf_data = build_pdf_report(model)
    reader = PdfReader(BytesIO(pdf_data))
    assert len(reader.pages) > 3
    pdf_text = _squash(_pdf_cid_text(pdf_data))
    assert _squash(next(item for item in analysis.sources if "기업정보 기준일" in item)) in pdf_text
    assert _squash(analysis.disclaimer) in pdf_text
    document = Document(BytesIO(build_docx_report(model)))
    assert long_source in _docx_all_text(build_docx_report(model))
    assert document.styles["Easy Analysis"].paragraph_format.keep_together is not True


def _run_summary(**overrides: object) -> RunSummary:
    base = dict(
        source_date="2026-09-18",
        run_id="run-abc",
        universe=574,
        attempted=574,
        ready=574,
        signals=38,
        candidate=23,
        excluded=15,
        no_signal=536,
        failure=0,
    )
    base.update(overrides)
    return RunSummary(**base)


def _candidate(ticker: str, **overrides: object) -> NewCandidateRecord:
    base = dict(
        stock_name=f"Stock {ticker}",
        ticker=ticker,
        market="KOSPI",
        signal_date="2026-09-18",
        entry_price=12345.75,
        signal_score=91.35,
        foreign_status="POSITIVE",
    )
    base.update(overrides)
    return NewCandidateRecord(**base)


def _performance(ticker: str, **overrides: object) -> PerformanceRecord:
    base = dict(
        ticker=ticker,
        stock_name=f"Stock {ticker}",
        signal_date="2026-09-18",
        entry_price=12345.75,
        tracking_status="OPEN",
        return_5d=None,
        benchmark_5d=None,
        excess_5d=None,
        return_10d=None,
        benchmark_10d=None,
        excess_10d=None,
        return_20d=None,
        benchmark_20d=None,
        excess_20d=None,
    )
    base.update(overrides)
    return PerformanceRecord(**base)


def _pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "".join(page.extract_text() for page in reader.pages).replace("\x00", "")


def _pdf_cid_text(pdf_bytes: bytes) -> str:
    # PyPDF2 does not decode UniKS-UCS2-H; inspect the actual CID text operands.
    reader = PdfReader(BytesIO(pdf_bytes))
    parts: list[str] = []
    for page in reader.pages:
        for operands, operator in ContentStream(page.get_contents(), reader).operations:
            if operator == b"Tj":
                parts.append(operands[0].original_bytes.decode("utf-16-be"))
            elif operator == b"TJ":
                parts.extend(
                    item.original_bytes.decode("utf-16-be")
                    for item in operands[0] if hasattr(item, "original_bytes")
                )
    return "\n".join(parts)


def _squash(text: str) -> str:
    # narrow table columns wrap mid-token, so strip all whitespace before substring checks
    return "".join(text.split())


def _docx_all_text(docx_bytes: bytes) -> str:
    document = Document(BytesIO(docx_bytes))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


# 1: DOCX generation succeeds
def test_docx_generation_succeeds():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[_performance("000001")],
    )

    data = build_docx_report(model)

    assert isinstance(data, bytes) and len(data) > 0
    document = Document(BytesIO(data))
    assert len(document.paragraphs) > 0


# 2: PDF generation succeeds
def test_pdf_generation_succeeds():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[_performance("000001")],
    )

    data = build_pdf_report(model)

    assert isinstance(data, bytes) and len(data) > 0
    reader = PdfReader(BytesIO(data))
    assert len(reader.pages) >= 1


# 3: both documents include the source date
def test_both_documents_include_source_date():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(source_date="2026-09-18"),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _pdf_text(build_pdf_report(model))

    assert "2026-09-18" in docx_text
    assert "2026-09-18" in pdf_text


# 4: real 23-candidate cohort reflected in both documents (historical 2026-09-18 ledger day)
def test_real_23_candidates_reflected_in_both_documents():
    model = build_daily_report_model(REPO_ROOT, source_date="2026-09-18")
    assert len(model.new_candidates) == 23

    docx_document = Document(BytesIO(build_docx_report(model)))
    candidate_tables = [t for t in docx_document.tables if len(t.rows[0].cells) == 7]
    assert len(candidate_tables) == 1
    assert len(candidate_tables[0].rows) == 24  # header + 23 candidates

    pdf_text = _squash(_pdf_text(build_pdf_report(model)))
    for record in model.new_candidates:
        assert record.ticker in pdf_text


# 5 & 6: Signal Score and Entry Price included (never omitted), comma/decimal formatted
def test_signal_score_and_entry_price_included():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000777", entry_price=54321.5, signal_score=88.8)],
        performance=[],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _pdf_text(build_pdf_report(model))

    assert "54,321.50" in docx_text and "88.8" in docx_text
    assert "54,321.50" in _squash(pdf_text) and "88.8" in _squash(pdf_text)
    assert "Signal Price" in docx_text
    assert "Entry Price" not in docx_text


# 7: Foreign Status included
def test_foreign_status_included():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000888", foreign_status="NEGATIVE")],
        performance=[],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "NEGATIVE" in docx_text
    assert "NEGATIVE" in pdf_text


# 8: Performance tracking included
def test_performance_tracking_included():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="EMPTY",
        new_candidates=[],
        performance=[
            _performance(
                "000999",
                tracking_status="COMPLETE",
                return_5d=1.23,
                benchmark_5d=0.45,
                excess_5d=0.78,
                return_10d=2.34,
                benchmark_10d=1.11,
                excess_10d=1.23,
                return_20d=3.45,
                benchmark_20d=2.22,
                excess_20d=1.23,
            )
        ],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "성과 추적 완료" in docx_text
    for value in (
        "+1.23%", "+0.45%", "+0.78%p",
        "+2.34%", "+1.11%", "+1.23%p",
        "+3.45%", "+2.22%",
    ):
        assert value in docx_text
        assert value in pdf_text
    for label in (
        "5D Return", "5D Benchmark", "5D Excess",
        "10D Return", "10D Benchmark", "10D Excess",
        "20D Return", "20D Benchmark", "20D Excess",
    ):
        assert label in docx_text
    pdf_bytes = build_pdf_report(model)
    assert b"HYGothic-Medium" in pdf_bytes


# 9: None -> em dash placeholder
def test_none_values_render_as_em_dash():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="EMPTY",
        new_candidates=[],
        performance=[_performance("000111", tracking_status="OPEN")],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _pdf_text(build_pdf_report(model))

    assert "—" in docx_text
    assert "None" not in docx_text
    assert "None" not in pdf_text


# STEP 15-G 1: generation timestamp is Asia/Seoul, without seconds/microseconds
def test_generated_at_is_kst_formatted_without_seconds():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="EMPTY",
        new_candidates=[],
        performance=[],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _pdf_text(build_pdf_report(model))

    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} KST", docx_text)
    assert re.search(r"\d{4}-\d{2}-\d{2}\d{2}:\d{2}KST", _squash(pdf_text))
    assert "+00:00" not in docx_text
    assert "+00:00" not in pdf_text


# STEP 15-G 2: entry price uses a thousands separator, model value itself untouched
def test_entry_price_uses_thousands_separator():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("060370", entry_price=33650)],
        performance=[_performance("060370", entry_price=33650)],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "33,650" in docx_text
    assert "33,650" in pdf_text
    assert "Signal Price" in docx_text
    assert model.new_candidates[0].entry_price == 33650  # model value unchanged
    assert model.performance[0].entry_price == 33650


# STEP 15-G 3: signal score keeps exactly one decimal place
def test_signal_score_keeps_one_decimal():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("060370", signal_score=92.3), _candidate("000002", signal_score=80)],
        performance=[],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "92.3" in docx_text
    assert "80.0" in docx_text
    assert "92.3" in pdf_text
    assert "80.0" in pdf_text


# STEP 15-G 4: performance return/benchmark/excess use a consistent percent format
def test_performance_values_use_percent_format():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="EMPTY",
        new_candidates=[],
        performance=[
            _performance("000333", tracking_status="5D", return_5d=-1.5, benchmark_5d=0.0, excess_5d=-1.5)
        ],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "-1.50%" in docx_text and "0.00%" in docx_text
    assert "-1.50%" in pdf_text and "0.00%" in pdf_text


# 10: no-candidate date still generates normally
def test_no_candidate_date_generates_normally():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(candidate=0, signals=15, excluded=15),
        new_candidates_status="EMPTY",
        new_candidates=[],
        performance=[],
    )

    docx_data = build_docx_report(model)
    pdf_data = build_pdf_report(model)

    assert len(docx_data) > 0
    assert len(pdf_data) > 0
    docx_text = _docx_all_text(docx_data)
    assert "신규 후보 없음" in docx_text


# 11: protected artifacts unchanged after building both documents
def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protected_artifacts_unchanged_after_building_documents():
    protected = [
        REPO_ROOT / "output/expanded_shadow/expanded_shadow_run.json",
        REPO_ROOT / "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        REPO_ROOT / "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
    ]
    before = {path: _sha256(path) for path in protected}

    model = build_daily_report_model(REPO_ROOT)
    build_docx_report(model)
    build_pdf_report(model)

    after = {path: _sha256(path) for path in protected}
    assert before == after

    protected_paths = [
        "src",
        "scripts",
        "dashboard/operations.py",
        "dashboard/daily_signal_board.py",
        ":(exclude)dashboard/expanded_signal_board.py",  # STEP 18-C3: adds shared evidence payload fields
        "dashboard/dual_shadow.py",
        "dashboard/adapter",
        "dashboard/runner",
        ":(exclude)dashboard/daily_report_model.py",  # STEP 18-C3: exposes shared Expanded records
        ":(exclude)dashboard/expanded_daily_report.py",  # STEP 18-C3: connects shared Expanded records
        ":(exclude)scripts/daily_scheduler.py",
        ":(exclude)scripts/safe_investor_update.py",
        ":(exclude)scripts/daily_operational_run.py",
        ":(exclude)scripts/daily_health_report.py",
        ":(exclude)src/expanded_shadow_pipeline.py",
        ":(exclude)src/expanded_candidate_performance.py",  # STEP 19-I: exact-date Expanded benchmark
        ":(exclude)scripts/expanded_candidate_performance.py",  # STEP 19-I: opt-in Naver benchmark provider
        ":(exclude)scripts/expanded_daily_orchestrator.py",  # STEP 19-M1: benchmark provider diagnostics
        ":(exclude)src/expanded_benchmark_provider.py",  # STEP 19-M2: Naver benchmark hard-timeout provider
        ":(exclude)scripts/expanded_operational_run.py",
        ":(exclude)dashboard/api.py",  # STEP 15-D: adds the read-only daily-report download endpoint
    ]
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--", *protected_paths],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert completed.stdout.strip() == ""


# Both builders consume the exact same DailyReportModel instance
def test_both_builders_reflect_the_same_model_instance():
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(run_id="shared-run-id"),
        new_candidates_status="READY",
        new_candidates=[_candidate("000222")],
        performance=[_performance("000222")],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _pdf_text(build_pdf_report(model))

    for value in (model.run_summary.run_id, model.run_summary.source_date, "000222"):
        assert value in docx_text
        assert value in pdf_text


def test_expanded_signal_details_render_in_word_and_pdf_with_long_korean_text():
    long_products = "반도체 메모리 제품과 시스템 솔루션을 공급하는 회사 " * 120
    company_profile = CompanyProfile(
        ticker="000001",
        company_name="한글 반도체 회사",
        market="KOSPI",
        one_line_description="업종은 반도체이며 주요 제품은 메모리입니다.",
        sector="반도체 및 전자부품",
        main_business_products=long_products,
        market_cap=None,
        market_cap_date=None,
        profile_as_of="2026-10-02",
        source="KRX_KIND_LISTING; one_line_description=GENERATED_TEMPLATE",
        collected_at="2026-10-02T01:00:00+00:00",
    )
    candidate = ExpandedSignalRecord(
        basDd="2026-09-30",
        ticker="000001",
        stock_name="한글 반도체 회사",
        market="KOSPI",
        signal_date="2026-09-30",
        signal_price=12500,
        raw_score=52,
        signal_score=80.0,
        signal_type="BUY_WATCH",
        profile=company_profile,
        evidence=DecisionEvidence(
            signal_reason="Score crossed threshold: 74.2 -> 80.0 (threshold 75)",
            prev_score=74.2,
            current_score=80.0,
            trend_score=25,
            volume_score=15,
            momentum_score=12,
            foreign_status="POSITIVE",
            foreign_5d_ratio=0.25,
            decision="CANDIDATE",
            decision_reason="Foreign status POSITIVE is not NEGATIVE; existing rule classifies as CANDIDATE.",
            evidence_status=EVIDENCE_PARTIAL,
        ),
        performance=ExpandedPerformanceEvidence(
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
    excluded = ExpandedSignalRecord(
        basDd="2026-09-30",
        ticker="000002",
        stock_name="제외 회사",
        market="KOSDAQ",
        signal_date="2026-09-30",
        signal_price=5000,
        raw_score=49,
        signal_score=75.4,
        signal_type="BUY_WATCH",
        profile=None,
        evidence=DecisionEvidence(
            signal_reason="Score crossed threshold: 73.8 -> 75.4 (threshold 75)",
            prev_score=73.8,
            current_score=75.4,
            trend_score=None,
            volume_score=None,
            momentum_score=None,
            foreign_status="NEGATIVE",
            foreign_5d_ratio=-0.12,
            decision="EXCLUDED",
            decision_reason="FOREIGN_NEGATIVE",
            evidence_status=EVIDENCE_UNAVAILABLE,
        ),
        performance=None,
    )
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(source_date="2026-09-30", signals=2, candidate=1, excluded=1),
        new_candidates_status="READY",
        new_candidates=[],
        performance=[],
        expanded_signals=[candidate, excluded],
    )

    docx_data = build_docx_report(model)
    pdf_data = build_pdf_report(model)
    docx_text = _docx_all_text(docx_data)
    pdf_text = _squash(_pdf_text(pdf_data))

    for value in (
        "한글 반도체 회사",
        "반도체 및 전자부품",
        "주요 사업/제품",
        "정보 기준일",
        "확인 보류",
        "74.2",
        "80.0",
        "추세 / 거래량 / 모멘텀",
        "점수가 기준 75를 상향 돌파: 74.2 → 80.0",
        "외국인 수급이 NEGATIVE가 아니므로 CANDIDATE로 분류",
        "외국인 수급이 NEGATIVE여서 EXCLUDED로 분류",
        "성과 추적 대상 아님",
        "근거 확인 불가",
        "일부 근거 확인",
        "+1.25%",
        "+0.50%",
        "+0.75%p",
        "5D Return / Benchmark / Excess",
        "10D Return / Benchmark / Excess",
        "20D Return / Benchmark / Excess",
    ):
        assert value in docx_text

    assert len(PdfReader(BytesIO(pdf_data)).pages) > 1
    assert b"HYGothic-Medium" in pdf_data
    assert len(docx_data) > 0


def _summary_signal(status: str = "AVAILABLE") -> ExpandedSignalRecord:
    return ExpandedSignalRecord(
        basDd="2026-09-18",
        ticker="000001",
        stock_name="Summary Company",
        market="KOSPI",
        signal_date="2026-09-18",
        signal_price=12345,
        raw_score=52,
        signal_score=78.1,
        signal_type="BUY_WATCH",
        profile=CompanyProfile(
            ticker="000001",
            company_name="Summary Company",
            market="KOSPI",
            one_line_description="Existing full description",
            sector="반도체",
            main_business_products="메모리 <제품> & 솔루션 " * 10,
            market_cap=None,
            market_cap_date=None,
            profile_as_of="2026-10-02",
            source="KRX_KIND_LISTING",
            collected_at="2026-10-02T01:00:00+00:00",
        ),
        evidence=DecisionEvidence(
            signal_reason="Score crossed threshold: 72.4 -> 78.1 (threshold 75)",
            prev_score=72.4,
            current_score=78.1,
            trend_score=25,
            volume_score=15,
            momentum_score=12,
            foreign_status="POSITIVE",
            foreign_5d_ratio=0.2,
            decision="CANDIDATE",
            decision_reason="Foreign status POSITIVE is not NEGATIVE; existing rule classifies as CANDIDATE.",
            evidence_status=status,
        ),
        performance=None,
    )


@pytest.mark.parametrize("status", ["AVAILABLE", "PARTIAL", "UNAVAILABLE"])
def test_candidate_summaries_render_before_details_in_both_reports(status, monkeypatch):
    from dashboard import daily_report_pdf

    signal = _summary_signal(status)
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[],
        expanded_signals=[signal],
    )
    before = asdict(model)
    expected = candidate_summary_rows(signal)
    captured = []
    original = daily_report_pdf._detail_table

    def capture(rows, style):
        captured.append(rows)
        return original(rows, style)

    monkeypatch.setattr(daily_report_pdf, "_detail_table", capture)
    docx_data = build_docx_report(model)
    pdf_data = build_pdf_report(model)
    document = Document(BytesIO(docx_data))
    summary_rows = tuple(tuple(cell.text for cell in row.cells) for row in document.tables[3].rows)
    assert summary_rows == expected
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            assert bool(row._tr.xpath("./w:trPr/w:cantSplit")) == (
                table_index == 3 and row_index == 2
            )
    assert tuple(captured[0]) == expected
    assert expected[0] == ("업종", "반도체")
    assert signal.profile is not None
    business = signal.profile.main_business_products
    assert business is not None
    assert expected[1][1] == business[:60] + "…"
    summary = expected[2][1]
    if status == "AVAILABLE":
        assert summary == "점수 72.4 → 78.1 (+5.7), 기준 75 상향 돌파 · 외국인 POSITIVE"
    else:
        assert status in summary
        assert display_evidence_status(status) in summary
        assert "상향 돌파" not in summary
    assert "외국인 POSITIVE" in summary
    assert business in _docx_all_text(docx_data)
    assert "Existing full description" in _docx_all_text(docx_data)
    assert "확인 보류" in _docx_all_text(docx_data)
    assert ("시가총액", "확인 보류") in captured[1]
    assert len(PdfReader(BytesIO(pdf_data)).pages) >= 1
    assert asdict(model) == before


def test_available_score_delta_and_overheated_are_shared_in_reports():
    signal = replace(_summary_signal("AVAILABLE"), signal_type="OVERHEATED")
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[],
        expanded_signals=[signal],
    )

    docx_text = _docx_all_text(build_docx_report(model))
    pdf_text = _squash(_pdf_text(build_pdf_report(model)))

    assert "72.4 → 78.1 (+5.7)" in docx_text
    assert "OVERHEATED" in docx_text


@pytest.mark.parametrize("mismatch", ["missing", "date", "ticker", "excluded"])
def test_candidate_summary_never_uses_unmatched_signal(mismatch, monkeypatch):
    from dashboard import daily_report_pdf

    signal = _summary_signal()
    signals = [signal]
    if mismatch == "missing":
        signals = []
    elif mismatch == "date":
        signals = [replace(signal, signal_date="2026-09-17")]
    elif mismatch == "ticker":
        signals = [replace(signal, ticker="000002")]
    else:
        signals = [replace(signal, evidence=replace(signal.evidence, decision="EXCLUDED"))]
    model = DailyReportModel(
        status=STATUS_READY,
        run_summary=_run_summary(),
        new_candidates_status="READY",
        new_candidates=[_candidate("000001")],
        performance=[],
        expanded_signals=signals,
    )
    captured = []
    original = daily_report_pdf._detail_table

    def capture(rows, style):
        captured.append(rows)
        return original(rows, style)

    monkeypatch.setattr(daily_report_pdf, "_detail_table", capture)
    document = Document(BytesIO(build_docx_report(model)))
    build_pdf_report(model)
    expected = candidate_summary_rows(None)
    assert tuple(tuple(cell.text for cell in row.cells) for row in document.tables[3].rows) == expected
    assert tuple(captured[0]) == expected
    assert "상향 돌파" not in expected[2][1]


@pytest.mark.parametrize("field", ["signal_reason", "prev_score", "current_score"])
def test_available_summary_without_required_evidence_does_not_invent_crossing(field):
    signal = _summary_signal()
    signal = replace(signal, evidence=replace(signal.evidence, **{field: None}))
    summary = candidate_summary_rows(signal)[2][1]
    assert summary == "AVAILABLE · 근거 확인 완료 · 외국인 POSITIVE"
    assert "상향 돌파" not in summary


def test_candidate_summary_missing_profile_and_market_cap_display():
    from dashboard.daily_report_docx import _format_market_cap as docx_cap
    from dashboard.daily_report_pdf import _format_market_cap as pdf_cap

    assert candidate_summary_rows(replace(_summary_signal(), profile=None))[:2] == (
        ("업종", "—"), ("주요사업", "—"),
    )
    for formatter in (docx_cap, pdf_cap):
        assert formatter(None) == "확인 보류"
        assert formatter(825_000_000_000) == "825,000,000,000"


def test_expanded_display_translations_leave_source_values_untouched():
    source_reason = "Score crossed threshold: 41.5 -> 81.5 (threshold 75)"
    source_candidate_reason = "Foreign status POSITIVE is not NEGATIVE; existing rule classifies as CANDIDATE."

    assert display_signal_reason(source_reason, 41.5, 81.5) == "점수가 기준 75를 상향 돌파: 41.5 → 81.5"
    assert display_decision_reason(source_candidate_reason) == "외국인 수급이 NEGATIVE가 아니므로 CANDIDATE로 분류"
    assert display_decision_reason("FOREIGN_NEGATIVE") == "외국인 수급이 NEGATIVE여서 EXCLUDED로 분류"
    assert display_evidence_status("AVAILABLE") == "근거 확인 완료"
    assert display_evidence_status("PARTIAL") == "일부 근거 확인"
    assert display_evidence_status("UNAVAILABLE") == "근거 확인 불가"
    assert display_tracking_status("OPEN") == "성과 측정 중"
    assert display_tracking_status("COMPLETE") == "성과 추적 완료"
    assert source_reason == "Score crossed threshold: 41.5 -> 81.5 (threshold 75)"

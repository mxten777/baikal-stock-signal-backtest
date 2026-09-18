from __future__ import annotations

import hashlib
import re
import subprocess
from io import BytesIO
from pathlib import Path

from docx import Document
from PyPDF2 import PdfReader

from dashboard.daily_report_docx import build_docx_report
from dashboard.daily_report_model import (
    STATUS_READY,
    DailyReportModel,
    NewCandidateRecord,
    PerformanceRecord,
    RunSummary,
)
from dashboard.daily_report_pdf import build_pdf_report
from dashboard.expanded_daily_report import build_daily_report_model

REPO_ROOT = Path(__file__).resolve().parents[1]


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


# 4: real 23-candidate cohort reflected in both documents
def test_real_23_candidates_reflected_in_both_documents():
    model = build_daily_report_model(REPO_ROOT)
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

    for value in ("COMPLETE", "+1.23%", "+0.45%", "+0.78%", "+2.34%", "+3.45%"):
        assert value in docx_text
        assert value in pdf_text


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
        "dashboard/expanded_signal_board.py",
        "dashboard/dual_shadow.py",
        "dashboard/adapter",
        "dashboard/runner",
        "dashboard/daily_report_model.py",
        "dashboard/expanded_daily_report.py",
        ":(exclude)scripts/daily_scheduler.py",
        ":(exclude)scripts/safe_investor_update.py",
        ":(exclude)scripts/daily_operational_run.py",
        ":(exclude)scripts/daily_health_report.py",
        ":(exclude)src/expanded_shadow_pipeline.py",
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

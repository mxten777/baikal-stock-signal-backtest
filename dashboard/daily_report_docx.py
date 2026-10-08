"""Read-only DOCX renderer for DailyReportModel.

Takes only a DailyReportModel as input. Never reads artifacts, ledgers, or
manifests directly, and never touches Signal Engine / Production / Shadow /
DUAL / Scheduler / Expanded operational modules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from dashboard.daily_report_model import DailyReportModel
from dashboard.daily_report_summary import summary_sections
from dashboard.expanded_display import (
    build_easy_stock_analysis,
    candidate_summary_rows,
    display_decision_reason,
    display_evidence_status,
    display_score_movement,
    display_signal_reason,
    display_tracking_status,
)

try:
    from zoneinfo import ZoneInfo

    _KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover - tzdata missing fallback
    _KST = timezone(timedelta(hours=9))

TITLE_LINES = (
    "BAIKAL Stock Signal",
    "Daily Recommendation Report",
    "Expanded Shadow / Research & Monitoring",
)
FOOTER_LINES = (
    "BAIKAL Stock Signal",
    "Expanded Shadow / Research & Monitoring",
    "본 보고서는 연구 및 모니터링을 위한 참고자료이며 투자판단 및 투자결과에 대한 책임은 투자자 본인에게 있습니다.",
)

NEW_CANDIDATE_HEADERS = ("종목명", "Ticker", "Market", "Signal Date", "Signal Price", "Signal Score", "Foreign Status")
NEW_CANDIDATE_COL_WIDTHS_CM = (3.4, 2.0, 1.8, 2.4, 2.4, 2.2, 2.6)
NEW_CANDIDATE_RIGHT_ALIGN = (4, 5)

PERFORMANCE_HEADERS = (
    "종목명",
    "Ticker",
    "Signal Date",
    "Signal Price",
    "Status",
    "5D Return",
    "5D Benchmark",
    "5D Excess",
    "10D Return",
    "10D Benchmark",
    "10D Excess",
    "20D Return",
    "20D Benchmark",
    "20D Excess",
)
PERFORMANCE_RIGHT_ALIGN = (3, 5, 6, 7, 8, 9, 10, 11, 12, 13)


def build_docx_report(model: DailyReportModel) -> bytes:
    document = Document()
    analysis_style = document.styles.add_style("Easy Analysis", WD_STYLE_TYPE.PARAGRAPH)
    analysis_style.base_style = document.styles["Normal"]
    analysis_style.font.name = "Malgun Gothic"
    analysis_style.font.size = Pt(9)
    analysis_style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    analysis_style.paragraph_format.widow_control = True
    analysis_heading = document.styles.add_style("Easy Analysis Heading", WD_STYLE_TYPE.PARAGRAPH)
    analysis_heading.base_style = analysis_style
    analysis_heading.font.bold = True
    analysis_heading.paragraph_format.keep_with_next = True
    analysis_heading.paragraph_format.keep_together = True
    analysis_heading.paragraph_format.space_before = Pt(6)

    document.add_heading(TITLE_LINES[0], level=0)
    for line in TITLE_LINES[1:]:
        document.add_heading(line, level=1)

    document.add_heading("1. 기준정보", level=1)
    _add_kv_table(
        document,
        (
            ("Report Date", _display(model.run_summary.source_date)),
            ("생성일", _generated_at_kst()),
            ("Run ID", _display(model.run_summary.run_id)),
        ),
    )

    document.add_heading("2. Expanded Run Summary", level=1)
    performance_summary = model.performance_summary
    _add_kv_table(
        document,
        (
            ("Universe", _display(model.run_summary.universe)),
            ("Attempted", _display(model.run_summary.attempted)),
            ("READY", _display(model.run_summary.ready)),
            ("Signals", _display(model.run_summary.signals)),
            ("CANDIDATE", _display(model.run_summary.candidate)),
            ("EXCLUDED", _display(model.run_summary.excluded)),
            ("NO_SIGNAL", _display(model.run_summary.no_signal)),
            ("Failure", _display(model.run_summary.failure)),
            ("OPEN Candidates", _display(performance_summary["open"])),
            ("5D Matured Candidates", _display(performance_summary["matured_5d"])),
            ("10D Matured Candidates", _display(performance_summary["matured_10d"])),
            ("20D Matured Candidates", _display(performance_summary["matured_20d"])),
        ),
    )

    document.add_heading("3. Today's New Candidates", level=1)
    if model.new_candidates:
        table = document.add_table(rows=1, cols=len(NEW_CANDIDATE_HEADERS))
        table.style = "Table Grid"
        _set_header_row(table, NEW_CANDIDATE_HEADERS)
        for record in model.new_candidates:
            cells = table.add_row().cells
            values = (
                record.stock_name,
                record.ticker,
                record.market,
                record.signal_date,
                _format_price(record.entry_price),
                _format_score(record.signal_score),
                record.foreign_status,
            )
            _set_row(cells, values, NEW_CANDIDATE_RIGHT_ALIGN)
        _set_column_widths(table, NEW_CANDIDATE_COL_WIDTHS_CM)
        signals_by_key = {
            (record.ticker, record.signal_date): record
            for record in model.expanded_signals
            if record.evidence.decision == "CANDIDATE"
        }
        for record in model.new_candidates:
            document.add_paragraph(f"{record.stock_name} ({record.ticker})")
            _add_kv_table(
                document,
                candidate_summary_rows(signals_by_key.get((record.ticker, record.signal_date))),
                unsplit_labels=("선정근거 요약",),
            )
    else:
        document.add_paragraph("신규 후보 없음")

    document.add_heading("4. Candidate Performance Tracking", level=1)
    if model.performance:
        table = document.add_table(rows=1, cols=len(PERFORMANCE_HEADERS))
        table.style = "Table Grid"
        _set_header_row(table, PERFORMANCE_HEADERS)
        for record in model.performance:
            cells = table.add_row().cells
            values = (
                record.stock_name,
                record.ticker,
                record.signal_date,
                _format_price(record.entry_price),
                display_tracking_status(record.tracking_status),
                _format_percent(record.return_5d),
                _format_percent(record.benchmark_5d),
                _format_percentage_points(record.excess_5d),
                _format_percent(record.return_10d),
                _format_percent(record.benchmark_10d),
                _format_percentage_points(record.excess_10d),
                _format_percent(record.return_20d),
                _format_percent(record.benchmark_20d),
                _format_percentage_points(record.excess_20d),
            )
            _set_row(cells, values, PERFORMANCE_RIGHT_ALIGN)
    else:
        document.add_paragraph("추적 중인 성과 데이터 없음")

    if model.expanded_signals:
        document.add_heading("5. Expanded Signal Details", level=1)
        for record in model.expanded_signals:
            evidence = record.evidence
            profile = record.profile
            performance = record.performance
            decision = _display(evidence.decision)
            document.add_heading(
                f"{record.stock_name} ({record.ticker}) / {decision} / 점수 {_format_score(evidence.current_score)}",
                level=2,
            )
            _add_kv_table(
                document,
                (
                    ("회사정보", _display(profile.one_line_description if profile else None)),
                    ("업종", _display(profile.sector if profile else None)),
                    ("주요 사업/제품", _display(profile.main_business_products if profile else None)),
                    ("시가총액", _format_market_cap(profile.market_cap if profile else None)),
                    ("정보 기준일", _display(profile.profile_as_of if profile else None)),
                    ("Signal Date", record.signal_date),
                    ("점수", display_score_movement(evidence.prev_score, evidence.current_score, evidence.delta_score)),
                    ("Signal 발생 이유", display_signal_reason(evidence.signal_reason, evidence.prev_score, evidence.current_score)),
                    (
                        "추세 / 거래량 / 모멘텀",
                        f"{_format_score(evidence.trend_score)} / {_format_score(evidence.volume_score)} / {_format_score(evidence.momentum_score)}",
                    ),
                    ("외국인 수급", f"{_display(evidence.foreign_status)} / {_format_ratio(evidence.foreign_5d_ratio)}"),
                    ("판정 이유", display_decision_reason(evidence.decision_reason)),
                    ("근거 상태", display_evidence_status(evidence.evidence_status)),
                ),
            )
            analysis = build_easy_stock_analysis(record)
            document.add_paragraph("쉬운 종목 분석", style=analysis_heading)
            document.add_paragraph(analysis.method, style=analysis_style)
            for label, items in (
                ("핵심 요약", (analysis.summary,)),
                ("긍정 요인", analysis.positives),
                ("위험 요인", analysis.risks),
                ("추가 확인 사항", analysis.checks),
                ("데이터 출처·기준일", analysis.sources),
            ):
                document.add_paragraph(label, style=analysis_heading)
                for item in items:
                    document.add_paragraph(item, style=analysis_style)
            document.add_paragraph(analysis.disclaimer, style=analysis_style)
            if performance is None:
                message = "성과 추적 대상 아님" if evidence.decision == "EXCLUDED" else "성과 데이터 없음"
                document.add_paragraph(message)
            else:
                _add_kv_table(
                    document,
                    (
                        ("성과 상태", display_tracking_status(performance.tracking_status)),
                        ("5D Return / Benchmark / Excess", f"{_format_percent(performance.return_5d)} / {_format_percent(performance.benchmark_5d)} / {_format_percentage_points(performance.excess_5d)}"),
                        ("10D Return / Benchmark / Excess", f"{_format_percent(performance.return_10d)} / {_format_percent(performance.benchmark_10d)} / {_format_percentage_points(performance.excess_10d)}"),
                        ("20D Return / Benchmark / Excess", f"{_format_percent(performance.return_20d)} / {_format_percent(performance.benchmark_20d)} / {_format_percentage_points(performance.excess_20d)}"),
                    ),
                )

    document.add_paragraph()
    for line in FOOTER_LINES:
        paragraph = document.add_paragraph(line)
        paragraph.runs[0].font.size = Pt(9)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_summary_docx_report(model: DailyReportModel) -> bytes:
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(1.2)
    section.left_margin = section.right_margin = Cm(1.2)
    style = document.styles["Normal"]
    style.font.name = "Malgun Gothic"
    style.font.size = Pt(9)
    style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
    style.paragraph_format.space_after = Pt(3)
    style.paragraph_format.widow_control = True
    for name in ("Title", "Heading 1"):
        heading = document.styles[name]
        heading.font.name = "Malgun Gothic"
        heading.font.size = Pt(14 if name == "Title" else 11)
        heading.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Malgun Gothic")
        heading.paragraph_format.space_before = Pt(5)
        heading.paragraph_format.space_after = Pt(4)
        heading.paragraph_format.keep_with_next = True
    document.add_paragraph("BAIKAL Stock Signal · 일일 요약", style="Title")
    for index, content in enumerate(summary_sections(model)):
        if index == 1 and len(model.new_candidates) > 10:
            document.add_page_break()
        document.add_heading(content.title, level=1)
        if content.rows:
            table = document.add_table(rows=1, cols=len(content.headers))
            table.style = "Table Grid"
            _set_header_row(table, content.headers)
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
            for values in content.rows:
                cells = table.add_row().cells
                _set_row(cells, values, ())
            widths = {
                7: (5.7, 2.0, 2.2, 1.5, 3.2, 2.0, 2.0),
                2: (2.5, 16.1),
                3: (12.0, 2.0, 4.6),
                5: (1.3, 3.5, 5.0, 3.5, 5.3),
            }[len(content.headers)]
            _set_column_widths(table, widths)
        elif content.headers:
            document.add_paragraph("해당 종목 없음")
        document.add_paragraph(content.note)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _add_kv_table(
    document: Document,
    rows: tuple[tuple[str, str], ...],
    *,
    unsplit_labels: tuple[str, ...] = (),
) -> None:
    table = document.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        row = table.add_row()
        if label in unsplit_labels:
            # python-docx has no public row pagination property.
            row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        cells = row.cells
        cells[0].text = label
        cells[1].text = value


def _set_header_row(table, headers: tuple[str, ...]) -> None:
    for cell, header in zip(table.rows[0].cells, headers):
        cell.text = header
        for run in cell.paragraphs[0].runs:
            run.font.bold = True


def _set_row(cells, values: tuple, right_align_indices: tuple[int, ...]) -> None:
    for index, (cell, value) in enumerate(zip(cells, values)):
        cell.text = str(value)
        if index in right_align_indices:
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT


def _set_column_widths(table, widths_cm: tuple[float, ...]) -> None:
    table.autofit = False
    for row in table.rows:
        for cell, width_cm in zip(row.cells, widths_cm):
            cell.width = Cm(width_cm)


def _generated_at_kst() -> str:
    now = datetime.now(timezone.utc).astimezone(_KST)
    return now.strftime("%Y-%m-%d %H:%M KST")


def _display(value: object) -> str:
    return "—" if value is None else str(value)


def _format_price(value: object) -> str:
    if value is None:
        return "—"
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _format_market_cap(value: object) -> str:
    return "확인 보류" if value is None else _format_price(value)


def _format_score(value: object) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}"


def _format_ratio(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.4f}"


def _format_percent(value: object) -> str:
    if value is None:
        return "—"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def _format_percentage_points(value: object) -> str:
    formatted = _format_percent(value)
    return formatted if formatted == "—" else f"{formatted}p"

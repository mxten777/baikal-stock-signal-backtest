"""Read-only PDF renderer for DailyReportModel.

Takes only a DailyReportModel as input. Never reads artifacts, ledgers, or
manifests directly, and never touches Signal Engine / Production / Shadow /
DUAL / Scheduler / Expanded operational modules.

Korean text uses reportlab's built-in Adobe-Korea1 CID font (HYGothic-Medium),
a standard non-embedded CJK font defined by the PDF spec (no system font path
is read or hardcoded; PDF viewers substitute an available Korean font).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from dashboard.daily_report_model import DailyReportModel
from dashboard.expanded_display import (
    candidate_summary_rows,
    display_decision_reason,
    display_evidence_status,
    display_signal_reason,
    display_tracking_status,
)

try:
    from zoneinfo import ZoneInfo

    _KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover - tzdata missing fallback
    _KST = timezone(timedelta(hours=9))

KOREAN_FONT_NAME = "HYGothic-Medium"

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

NEW_CANDIDATE_HEADERS = ("종목명", "Ticker", "Market", "Signal Date", "Entry Price", "Signal Score", "Foreign Status")
NEW_CANDIDATE_COL_WIDTHS = (95, 55, 50, 62, 62, 55, 68)
NEW_CANDIDATE_RIGHT_ALIGN = (4, 5)

PERFORMANCE_HEADERS = (
    "종목명",
    "Ticker",
    "Signal Date",
    "Entry Price",
    "Status",
    "5D Return",
    "5D Bench",
    "5D Excess",
    "10D Return",
    "10D Bench",
    "10D Excess",
    "20D Return",
    "20D Bench",
    "20D Excess",
)
PERFORMANCE_COL_WIDTHS = (58, 40, 46, 44, 34, 30, 30, 30, 30, 30, 30, 30, 30, 30)
PERFORMANCE_RIGHT_ALIGN = (3, 5, 6, 7, 8, 9, 10, 11, 12, 13)


def _ensure_korean_font_registered() -> None:
    if KOREAN_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(KOREAN_FONT_NAME))


def build_pdf_report(model: DailyReportModel) -> bytes:
    _ensure_korean_font_registered()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("KrTitle", parent=styles["Title"], fontName=KOREAN_FONT_NAME, fontSize=16, leading=20)
    subtitle_style = ParagraphStyle("KrSubtitle", parent=styles["Normal"], fontName=KOREAN_FONT_NAME, fontSize=11, leading=14, alignment=1)
    heading_style = ParagraphStyle("KrHeading", parent=styles["Heading2"], fontName=KOREAN_FONT_NAME, fontSize=12, leading=15)
    body_style = ParagraphStyle("KrBody", parent=styles["Normal"], fontName=KOREAN_FONT_NAME, fontSize=9, leading=12)
    header_style = ParagraphStyle("KrTableHeader", parent=body_style, fontName=KOREAN_FONT_NAME, fontSize=9, leading=12)
    right_style = ParagraphStyle("KrBodyRight", parent=body_style, alignment=2)
    footer_style = ParagraphStyle("KrFooter", parent=styles["Normal"], fontName=KOREAN_FONT_NAME, fontSize=8, leading=10)

    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm)
    story: list = []

    story.append(Paragraph(TITLE_LINES[0], title_style))
    for line in TITLE_LINES[1:]:
        story.append(Paragraph(line, subtitle_style))
    story.append(Spacer(1, 10))

    story.append(Paragraph("1. 기준정보", heading_style))
    story.append(
        _kv_table(
            (
                ("기준일", _display(model.run_summary.source_date)),
                ("생성일", _generated_at_kst()),
                ("Run ID", _display(model.run_summary.run_id)),
            ),
            body_style,
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("2. Expanded Run Summary", heading_style))
    story.append(
        _kv_table(
            (
                ("Universe", _display(model.run_summary.universe)),
                ("Attempted", _display(model.run_summary.attempted)),
                ("READY", _display(model.run_summary.ready)),
                ("Signals", _display(model.run_summary.signals)),
                ("CANDIDATE", _display(model.run_summary.candidate)),
                ("EXCLUDED", _display(model.run_summary.excluded)),
                ("NO_SIGNAL", _display(model.run_summary.no_signal)),
                ("Failure", _display(model.run_summary.failure)),
            ),
            body_style,
        )
    )
    story.append(Spacer(1, 8))

    story.append(Paragraph("3. Today's New Candidates", heading_style))
    if model.new_candidates:
        rows = [NEW_CANDIDATE_HEADERS]
        for record in model.new_candidates:
            rows.append(
                (
                    record.stock_name,
                    record.ticker,
                    record.market,
                    record.signal_date,
                    _format_price(record.entry_price),
                    _format_score(record.signal_score),
                    record.foreign_status,
                )
            )
        story.append(
            _data_table(rows, body_style, header_style, right_style, NEW_CANDIDATE_COL_WIDTHS, NEW_CANDIDATE_RIGHT_ALIGN)
        )
        signals_by_key = {
            (record.ticker, record.signal_date): record
            for record in model.expanded_signals
            if record.evidence.decision == "CANDIDATE"
        }
        for record in model.new_candidates:
            story.append(Spacer(1, 5))
            story.append(Paragraph(_paragraph_text(f"{record.stock_name} ({record.ticker})"), body_style))
            story.append(
                _detail_table(
                    list(candidate_summary_rows(signals_by_key.get((record.ticker, record.signal_date)))),
                    body_style,
                )
            )
    else:
        story.append(Paragraph("신규 후보 없음", body_style))
    story.append(Spacer(1, 8))

    story.append(Paragraph("4. Candidate Performance Tracking", heading_style))
    if model.performance:
        rows = [PERFORMANCE_HEADERS]
        for record in model.performance:
            rows.append(
                (
                    record.stock_name,
                    record.ticker,
                    record.signal_date,
                    _format_price(record.entry_price),
                    display_tracking_status(record.tracking_status),
                    _format_percent(record.return_5d),
                    _format_percent(record.benchmark_5d),
                    _format_percent(record.excess_5d),
                    _format_percent(record.return_10d),
                    _format_percent(record.benchmark_10d),
                    _format_percent(record.excess_10d),
                    _format_percent(record.return_20d),
                    _format_percent(record.benchmark_20d),
                    _format_percent(record.excess_20d),
                )
            )
        story.append(
            _data_table(rows, body_style, header_style, right_style, PERFORMANCE_COL_WIDTHS, PERFORMANCE_RIGHT_ALIGN)
        )
    else:
        story.append(Paragraph("추적 중인 성과 데이터 없음", body_style))

    if model.expanded_signals:
        story.append(Spacer(1, 8))
        story.append(Paragraph("5. Expanded Signal Details", heading_style))
        for record in model.expanded_signals:
            evidence = record.evidence
            profile = record.profile
            performance = record.performance
            decision = _display(evidence.decision)
            story.append(Spacer(1, 5))
            story.append(
                Paragraph(
                    _paragraph_text(
                        f"{record.stock_name} ({record.ticker}) / {decision} / 점수 {_format_score(evidence.current_score)}"
                    ),
                    heading_style,
                )
            )
            detail_rows = [
                ("회사정보", _display(profile.one_line_description if profile else None)),
                ("업종", _display(profile.sector if profile else None)),
                ("주요 사업/제품", _display(profile.main_business_products if profile else None)),
                ("시가총액", _format_market_cap(profile.market_cap if profile else None)),
                ("정보 기준일", _display(profile.profile_as_of if profile else None)),
                ("Signal Date", record.signal_date),
                ("점수", f"{_format_score(evidence.prev_score)} → {_format_score(evidence.current_score)}"),
                ("Signal 발생 이유", display_signal_reason(evidence.signal_reason, evidence.prev_score, evidence.current_score)),
                (
                    "추세 / 거래량 / 모멘텀",
                    f"{_format_score(evidence.trend_score)} / {_format_score(evidence.volume_score)} / {_format_score(evidence.momentum_score)}",
                ),
                ("외국인 수급", f"{_display(evidence.foreign_status)} / {_format_ratio(evidence.foreign_5d_ratio)}"),
                ("판정 이유", display_decision_reason(evidence.decision_reason)),
            ]
            detail_rows.append(("근거 상태", display_evidence_status(evidence.evidence_status)))
            story.append(_detail_table(detail_rows, body_style))
            if performance is None:
                message = "성과 추적 대상 아님" if evidence.decision == "EXCLUDED" else "성과 데이터 없음"
                story.append(Paragraph(message, body_style))
            else:
                performance_rows = [
                    ("성과 상태", display_tracking_status(performance.tracking_status)),
                    ("5D Return / Excess", f"{_format_percent(performance.return_5d)} / {_format_percent(performance.excess_5d)}"),
                    ("10D Return / Excess", f"{_format_percent(performance.return_10d)} / {_format_percent(performance.excess_10d)}"),
                    ("20D Return / Excess", f"{_format_percent(performance.return_20d)} / {_format_percent(performance.excess_20d)}"),
                ]
                story.append(_detail_table(performance_rows, body_style))

    story.append(Spacer(1, 14))
    for line in FOOTER_LINES:
        story.append(Paragraph(line, footer_style))

    document.build(story)
    return buffer.getvalue()


def _kv_table(rows: tuple[tuple[str, str], ...], style: ParagraphStyle) -> Table:
    data = [[Paragraph(str(key), style), Paragraph(str(value), style)] for key, value in rows]
    table = Table(data, colWidths=[100, 300])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _detail_table(rows: list[tuple[str, str]], style: ParagraphStyle) -> Table:
    data = [[Paragraph(_paragraph_text(key), style), Paragraph(_paragraph_text(value), style)] for key, value in rows]
    table = Table(data, colWidths=[100, 390], splitByRow=1, splitInRow=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _data_table(
    rows: list[tuple],
    style: ParagraphStyle,
    header_style: ParagraphStyle,
    right_style: ParagraphStyle,
    col_widths: tuple[int, ...],
    right_align_indices: tuple[int, ...],
) -> Table:
    header, *body_rows = rows
    data = [[Paragraph(f"<b>{cell}</b>", header_style) for cell in header]]
    for row in body_rows:
        data.append(
            [
                Paragraph(str(cell), right_style if index in right_align_indices else style)
                for index, cell in enumerate(row)
            ]
        )
    table = Table(data, colWidths=list(col_widths), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


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


def _paragraph_text(value: object) -> str:
    return escape(str(value)).replace("\n", "<br/>")


def _format_percent(value: object) -> str:
    if value is None:
        return "—"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"

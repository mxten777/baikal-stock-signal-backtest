"""Compact report content shared by the Word and PDF renderers."""

from __future__ import annotations

import math
from dataclasses import dataclass

from dashboard.daily_report_model import DailyReportModel


@dataclass(frozen=True)
class SummarySection:
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    note: str = ""


def _number(value: float | int | None, *, percent: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "확인 불가"
    return f"{value:+.2f}%" if percent else f"{value:,}".removesuffix(".0")


def summary_sections(model: DailyReportModel) -> tuple[SummarySection, ...]:
    source_date = model.report_date or model.run_summary.source_date
    signals = {(row.ticker, row.signal_date): row for row in model.expanded_signals}
    changes = {row.ticker: row.change for row in model.comparison.changes}
    candidates = tuple(
        (
            f"{row.stock_name} ({row.ticker})", row.market,
            _number(row.entry_price), _number(row.signal_score),
            signals[(row.ticker, row.signal_date)].signal_type or "확인 불가"
            if (row.ticker, row.signal_date) in signals else "확인 불가",
            row.foreign_status, changes.get(row.ticker, "비교 불가"),
        )
        for row in model.new_candidates
    )
    run = model.run_summary
    counts = (
        f"분석 {run.ready}/{run.universe} · 발생 신호 {run.signals} · 관심 후보 {len(candidates)} · 제외 {run.excluded}"
        if run.source_date == source_date else "당일 실행 집계 확인 불가"
    )
    comparison = model.comparison
    comparison_rows: list[tuple[str, ...]] = []
    if comparison.status == "AVAILABLE":
        for label in ("신규", "변경", "유지"):
            rows = [row for row in comparison.changes if row.change == label]
            names = " · ".join(
                f"{row.stock_name}({row.ticker})"
                + (f": {row.previous} → {row.current}" if label == "변경" else "")
                for row in rows
            )
            comparison_rows.append((f"{label} {len(rows)}", names or "없음"))
        comparison_rows.append((f"전일만 발생 {len(comparison.previous_only)}", " · ".join(comparison.previous_only) or "없음"))
    else:
        comparison_rows.append(("비교 불가", comparison.reason))
    overheated = tuple(
        (f"{row.stock_name} ({row.ticker})", _number(row.signal_score), row.evidence.decision or "확인 불가")
        for row in model.expanded_signals if row.signal_type == "OVERHEATED"
    )
    prior_performance = [row for row in model.performance if source_date and row.signal_date < source_date]
    available = (
        source_date is not None and model.performance_as_of is not None
        and model.performance_as_of <= source_date
    )
    performance_rows: list[tuple[str, ...]] = []
    for horizon in (5, 10, 20):
        returns = [
            getattr(row, f"return_{horizon}d") for row in prior_performance
            if getattr(row, f"return_{horizon}d") is not None
            and math.isfinite(getattr(row, f"return_{horizon}d"))
        ] if available else []
        excess = [
            getattr(row, f"excess_{horizon}d") for row in prior_performance
            if getattr(row, f"return_{horizon}d") is not None
            and math.isfinite(getattr(row, f"return_{horizon}d"))
            and getattr(row, f"excess_{horizon}d") is not None
            and math.isfinite(getattr(row, f"excess_{horizon}d"))
        ] if available else []
        performance_rows.append((
            f"{horizon}D", str(len(returns)) if available else "확인 불가",
            _number(sum(returns) / len(returns), percent=True) if returns else "확인 불가",
            str(len(excess)) if available else "확인 불가",
            _number(sum(excess) / len(excess), percent=True).replace("%", "%p") if excess else "확인 불가",
        ))
    performance_note = (
        f"저장 성과 최종 갱신일 {model.performance_as_of or '확인 불가'}(UTC·기록별 갱신일 상이) · 당일 이전 발생 후보 "
        f"{len(prior_performance)}건 · OPEN {sum(row.tracking_status == 'OPEN' for row in prior_performance) if available else '확인 불가'}건. "
        "신호별 단순 평균·비용 미반영이며 계좌 수익률이 아닙니다. 반복 발생 종목은 별도 신호로 집계."
    )
    if not available:
        performance_note += " 보고서 기준일의 성과 스냅샷을 확인할 수 없어 성과 요약 확인 불가(이후 데이터 대체 금지)."
    warnings = (*model.warnings, *model.expanded_signal_warnings)
    return (
        SummarySection(
            f"1. 오늘 분석 결과 · {source_date or '확인 불가'}",
            ("신규 관심 후보 전체", "시장", "Signal 가격", "Score", "Signal", "외국인", "전일 비교"),
            candidates,
            counts + " · Signal 가격은 기준일 종가이며 현재가가 아닙니다.",
        ),
        SummarySection(
            f"2. 전 거래일 발생 신호 비교 · {comparison.previous_date or '확인 불가'}",
            ("구분", "발생 종목 전체"), tuple(comparison_rows),
            comparison.reason + " 신규=오늘만 발생, 유지=양일 발생·비교 항목 동일, 변경=양일 발생·비교 항목 차이. 전일만 발생은 해제·매도 의미가 아닙니다.",
        ),
        SummarySection(
            "3. 단기 과열 주의", ("종목 전체", "Score", "저장 판정"), overheated,
            "기존 OVERHEATED 분류 그대로 표시. CANDIDATE도 과열일 수 있으며 매수 추천이 아닙니다.",
        ),
        SummarySection(
            "4. 기존 후보 성과 요약", ("기간", "수익률 건수", "평균 Return", "초과 건수", "평균 Excess"),
            tuple(performance_rows), performance_note,
        ),
        SummarySection(
            "5. 출처·주의", (), (),
            "규칙 기반 요약·생성형 AI API 미사용. 매수·매도 판단 및 수익 보장 아님. "
            "출처: Expanded 신호 원장·완료 manifest·기존 후보 성과 원장·저장 시세. "
            "기업정보·상세 근거·개별 성과는 상세형에서 확인. "
            + ("자료 경고: " + " / ".join(warnings) if warnings else ""),
        ),
    )

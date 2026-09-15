"""STEP 15: Daily Signal Board (read-only aggregation).

Combines already-computed values from the Shadow ledger, the DUAL ledger, and
the daily operational manifest into a single board payload for the dashboard
home screen. No new scoring/decision logic is introduced here: every value is
read from existing CSV/JSON outputs and only filtered, joined by stock_code,
or formatted for display.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from dashboard.adapter.service import DashboardService
from dashboard.dual_shadow import DUAL_LEDGER_SOURCE, DualShadowDashboardService
from dashboard.operations import coverage_status

WATCH_SIGNAL_TYPE = "WATCH"
WATCH_DISPLAY_LIMIT = 5
CANDIDATE_DECISION = "CANDIDATE"


def build_daily_signal_board(repo_root: Path, today: date | None = None) -> dict[str, Any]:
    repo_root = Path(repo_root)
    reference_today = today or date.today()

    overview = DashboardService(repo_root).overview(today=reference_today)
    system = overview["system"]
    ledger_records = overview["signal_ledger"].get("records", [])

    dual_latest = DualShadowDashboardService(repo_root=repo_root).latest()
    dual_by_ticker = {row.get("stock_code"): row for row in dual_latest.get("records", [])}
    dual_history = _read_dual_ledger_rows(repo_root)

    analysis_date = system.get("data_date", {}).get("value")
    is_today = bool(analysis_date) and analysis_date == reference_today.isoformat()

    new_candidates = _new_candidates_section(ledger_records, analysis_date)
    watch_list = _watch_list_section(dual_latest, dual_history, analysis_date)
    candidate_tracking = _candidate_tracking_section(ledger_records, dual_by_ticker, dual_latest.get("trade_date"))
    dual_comparison = _dual_comparison_section(dual_latest)

    return {
        "status": _status_section(system, coverage_status(repo_root), analysis_date, is_today),
        "new_signals": _new_signals_section(ledger_records, analysis_date),
        "new_candidates": new_candidates,
        "watch_list": watch_list,
        "candidate_tracking": candidate_tracking,
        "dual_comparison": dual_comparison,
        "production_vs_dual": _production_vs_dual_section(system, analysis_date, dual_latest, dual_comparison),
        "summary": _summary_section(is_today, analysis_date, new_candidates, watch_list, candidate_tracking, dual_latest),
    }


def _status_section(system: dict[str, Any], coverage: dict[str, Any], analysis_date: str | None, is_today: bool) -> dict[str, Any]:
    return {
        "analysis_date": analysis_date,
        "market_data_date": system.get("market_data_date", {}).get("value"),
        "investor_data_date": system.get("investor_data_date", {}).get("value"),
        "coverage": coverage,
        "production_status": system.get("pipeline_status", {}).get("value"),
        "is_today": is_today,
        "waiting_for_today": not is_today,
    }


def _new_signals_section(ledger_records: list[dict[str, Any]], analysis_date: str | None) -> dict[str, Any]:
    rows = [row for row in ledger_records if analysis_date and row.get("signal_date") == analysis_date]
    return {
        "count": len(rows),
        "records": [
            {
                "stock_name": row.get("stock_name"),
                "stock_code": row.get("stock_code"),
                "signal_score": _number_or_none(row.get("signal_score")),
                "decision": row.get("decision"),
            }
            for row in rows
        ],
        "empty_message": None if rows else "신규 매수 후보 없음",
    }


def _new_candidates_section(ledger_records: list[dict[str, Any]], analysis_date: str | None) -> dict[str, Any]:
    """오늘 signal 중 decision=='CANDIDATE'만 별도로 뽑은 신규 매수후보 섹션 (new_signals 원본은 훼손하지 않음)."""
    rows = [
        row for row in ledger_records
        if analysis_date and row.get("signal_date") == analysis_date and row.get("decision") == CANDIDATE_DECISION
    ]
    return {
        "count": len(rows),
        "records": [
            {
                "stock_name": row.get("stock_name"),
                "stock_code": row.get("stock_code"),
                "signal_price": _number_or_none(row.get("signal_price")),
                "signal_score": _number_or_none(row.get("signal_score")),
            }
            for row in rows
        ],
        "empty_message": None if rows else "신규 매수후보 없음",
    }


def _watch_list_section(dual_latest: dict[str, Any], dual_history: list[dict[str, Any]], analysis_date: str | None) -> dict[str, Any]:
    records = dual_latest.get("records", [])
    trade_date = dual_latest.get("trade_date")
    watch_rows = [row for row in records if row.get("baseline_signal") == WATCH_SIGNAL_TYPE]
    watch_rows = sorted(watch_rows, key=lambda row: row.get("baseline_score") if row.get("baseline_score") is not None else -1, reverse=True)
    top = watch_rows[:WATCH_DISPLAY_LIMIT]
    previous_by_stock = _previous_trade_date_scores(dual_history, trade_date, {row.get("stock_code") for row in top})

    is_current = bool(trade_date) and bool(analysis_date) and trade_date == analysis_date
    return {
        "trade_date": trade_date,
        "as_of_is_current": is_current,
        "stale_note": None if is_current else "전일 기준",
        "total_watch_count": len(watch_rows),
        "records": [
            {
                "stock_name": row.get("stock_name"),
                "stock_code": row.get("stock_code"),
                "evaluation_close": row.get("evaluation_close"),
                "baseline_score": row.get("baseline_score"),
                "previous_score": previous_by_stock.get(row.get("stock_code")),
                "score_change": _score_change(row.get("baseline_score"), previous_by_stock.get(row.get("stock_code"))),
            }
            for row in top
        ],
    }


def _candidate_tracking_section(
    ledger_records: list[dict[str, Any]],
    dual_by_ticker: dict[str, dict[str, Any]],
    dual_trade_date: str | None,
) -> dict[str, Any]:
    candidates = [row for row in ledger_records if row.get("decision") == CANDIDATE_DECISION]
    tracked = []
    for row in candidates:
        stock_code = row.get("stock_code")
        current = dual_by_ticker.get(stock_code)
        signal_price = _number_or_none(row.get("signal_price"))
        evaluation_close = current.get("evaluation_close") if current else None
        price_change_pct = None
        if signal_price not in (None, 0) and evaluation_close is not None:
            price_change_pct = round((evaluation_close - signal_price) / signal_price * 100, 2)
        tracked.append({
            "stock_name": row.get("stock_name"),
            "stock_code": stock_code,
            "signal_date": row.get("signal_date"),
            "signal_price": signal_price,
            "signal_score": _number_or_none(row.get("signal_score")),
            "current_evaluation_close": evaluation_close,
            "current_baseline_score": current.get("baseline_score") if current else None,
            "current_baseline_signal_type": current.get("baseline_signal") if current else None,
            "price_change_pct": price_change_pct,
            "dual_match_found": current is not None,
            "tracking_status": row.get("status"),
            "return_5d": _number_or_none(row.get("return_5d")),
            "return_10d": _number_or_none(row.get("return_10d")),
            "return_20d": _number_or_none(row.get("return_20d")),
            "excess_5d": _number_or_none(row.get("excess_5d")),
            "excess_10d": _number_or_none(row.get("excess_10d")),
            "excess_20d": _number_or_none(row.get("excess_20d")),
        })
    return {
        "as_of": dual_trade_date,
        "records": tracked,
    }


def _dual_comparison_section(dual_latest: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_date": dual_latest.get("trade_date"),
        "status": dual_latest.get("status"),
        "counts": dual_latest.get("counts"),
    }


def _production_vs_dual_section(
    system: dict[str, Any],
    analysis_date: str | None,
    dual_latest: dict[str, Any],
    dual_comparison: dict[str, Any],
) -> dict[str, Any]:
    dual_trade_date = dual_latest.get("trade_date")
    date_mismatch = bool(analysis_date) and bool(dual_trade_date) and analysis_date != dual_trade_date
    return {
        "production_status": system.get("pipeline_status", {}).get("value"),
        "production_date": analysis_date,
        "dual_status": dual_latest.get("status"),
        "dual_date": dual_trade_date,
        "date_mismatch": date_mismatch,
        "mismatch_note": "기준일 불일치" if date_mismatch else None,
        "counts": dual_comparison.get("counts"),
    }


def _summary_section(
    is_today: bool,
    analysis_date: str | None,
    new_candidates: dict[str, Any],
    watch_list: dict[str, Any],
    candidate_tracking: dict[str, Any],
    dual_latest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "data_status": "DATA_READY" if is_today else "WAITING",
        "analysis_date": analysis_date,
        "new_candidate_count": new_candidates.get("count", 0),
        "watch_count": watch_list.get("total_watch_count", 0),
        "tracked_candidate_count": len(candidate_tracking.get("records", [])),
        "dual_latest_trade_date": dual_latest.get("trade_date"),
    }


def _read_dual_ledger_rows(repo_root: Path) -> list[dict[str, Any]]:
    """DUAL ledger 전체 이력을 그대로 읽는다 (WATCH 전일 대비 Score 계산용, 읽기 전용)."""
    path = Path(repo_root) / DUAL_LEDGER_SOURCE
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                return []
            return list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def _previous_trade_date_scores(
    dual_history: list[dict[str, Any]],
    latest_trade_date: str | None,
    stock_codes: set[str | None],
) -> dict[str, float | int | None]:
    if not latest_trade_date or not dual_history:
        return {}
    earlier_dates = sorted(
        {str(row.get("trade_date")) for row in dual_history if row.get("trade_date") and str(row.get("trade_date")) < latest_trade_date},
        reverse=True,
    )
    if not earlier_dates:
        return {}
    previous_trade_date = earlier_dates[0]
    result: dict[str, float | int | None] = {}
    for row in dual_history:
        if str(row.get("trade_date")) != previous_trade_date:
            continue
        stock_code = row.get("stock_code")
        if stock_code in stock_codes:
            result[stock_code] = _number_or_none(row.get("baseline_score"))
    return result


def _score_change(current_score: Any, previous_score: Any) -> float | int | None:
    current = _number_or_none(current_score)
    previous = _number_or_none(previous_score)
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def _number_or_none(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number

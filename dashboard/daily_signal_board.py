"""STEP 15: Daily Signal Board (read-only aggregation).

Combines already-computed values from the Shadow ledger, the DUAL ledger, and
the daily operational manifest into a single board payload for the dashboard
home screen. No new scoring/decision logic is introduced here: every value is
read from existing CSV/JSON outputs and only filtered, joined by stock_code,
or formatted for display.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from dashboard.adapter.service import DashboardService
from dashboard.dual_shadow import DualShadowDashboardService
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

    analysis_date = system.get("data_date", {}).get("value")
    is_today = bool(analysis_date) and analysis_date == reference_today.isoformat()

    return {
        "status": _status_section(system, coverage_status(repo_root), analysis_date, is_today),
        "new_signals": _new_signals_section(ledger_records, analysis_date),
        "watch_list": _watch_list_section(dual_latest),
        "candidate_tracking": _candidate_tracking_section(ledger_records, dual_by_ticker, dual_latest.get("trade_date")),
        "dual_comparison": _dual_comparison_section(dual_latest),
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


def _watch_list_section(dual_latest: dict[str, Any]) -> dict[str, Any]:
    records = dual_latest.get("records", [])
    watch_rows = [row for row in records if row.get("baseline_signal") == WATCH_SIGNAL_TYPE]
    watch_rows = sorted(watch_rows, key=lambda row: row.get("baseline_score") if row.get("baseline_score") is not None else -1, reverse=True)
    top = watch_rows[:WATCH_DISPLAY_LIMIT]
    return {
        "trade_date": dual_latest.get("trade_date"),
        "total_watch_count": len(watch_rows),
        "records": [
            {
                "stock_name": row.get("stock_name"),
                "stock_code": row.get("stock_code"),
                "evaluation_close": row.get("evaluation_close"),
                "baseline_score": row.get("baseline_score"),
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


def _number_or_none(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number

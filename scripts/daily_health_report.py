"""Read-only daily operational health and exception report."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

ROOT_DIR = Path(__file__).resolve().parents[1]
MANIFEST_SOURCE = "output/daily_operational_run.json"

HEALTHY = "HEALTHY"
WARNING = "WARNING"
FAILED = "FAILED"
NO_RUN = "NO_RUN"

REQUIRED_FIELDS = {
    "run_id", "started_at", "finished_at", "overall_status", "failed_phase",
    "market_update_status", "investor_update_status", "gate_status", "pipeline_allowed",
    "dashboard_status", "market_latest_date", "investor_latest_date", "signal_count",
    "zero_signal", "warnings", "errors",
}


def _operational_timezone():
    """Asia/Seoul via the standard library; fixed UTC+09:00 fallback if the
    system tz database is unavailable (Asia/Seoul has had no DST since 1988)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Seoul")
        except Exception:  # tz database missing (e.g. Windows without tzdata)
            pass
    return timezone(timedelta(hours=9), name="Asia/Seoul")


OPERATIONAL_TIMEZONE = _operational_timezone()


def _run_date(value: Any) -> str | None:
    """Return the operational date (Asia/Seoul) of an ISO-8601 timestamp.

    Timezone-aware timestamps are converted to Asia/Seoul before the date is
    taken, so a UTC timestamp from the previous UTC calendar day is correctly
    recognized as today's run when it falls on the same Asia/Seoul date.
    Naive timestamps return None: their timezone is unknown, they are never
    assumed to be UTC or local time, and therefore can never be misjudged as
    \"today\". The caller reports this as an UNKNOWN run date.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(OPERATIONAL_TIMEZONE).date().isoformat()


def _is_naive_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None
    except ValueError:
        return False


def _actions_for(report: dict[str, Any]) -> list[str]:
    content = " ".join([str(report["failed_phase"]), *report["warnings"], *report["errors"]]).upper()
    actions: list[str] = []
    if "CONCURRENT_RUN" in content:
        actions.append("Check the active Daily Run process and lock.")
    if report["failed_phase"] == "MARKET_UPDATE" or "MARKET" in content:
        actions.append("Check the market source and staging, then rerun Daily Operational Run.")
    if report["failed_phase"] == "INVESTOR_UPDATE" or "INVESTOR" in content:
        actions.append("Check the Naver source and investor ticker coverage.")
    if report["failed_phase"] == "INPUT_GATE" or report["gate_status"] in {"FAIL", "FAILED"}:
        actions.append("Check missing, partial, or stale inputs before rerunning.")
    if report["failed_phase"] == "DASHBOARD_RUNNER" or report["dashboard_status"] == "FAILED":
        actions.append("Check the dashboard pipeline phase and its metadata.")
    return actions


def _empty_report(error: str | None = None) -> dict[str, Any]:
    errors = [error] if error else []
    return {
        "health_status": FAILED if error else NO_RUN,
        "run_present": False,
        "run_is_today": False,
        "run_id": None,
        "overall_status": None,
        "failed_phase": None,
        "market_status": None,
        "investor_status": None,
        "market_latest_date": None,
        "investor_latest_date": None,
        "gate_status": None,
        "pipeline_allowed": None,
        "dashboard_status": "UNKNOWN",
        "signal_count": None,
        "zero_signal": False,
        "warnings": [],
        "errors": errors,
        "operator_actions": ["Run Daily Operational Run."] if not error else ["Repair the Daily Operational Run manifest and rerun the operation."],
    }


def build_report(manifest_path: Path, today_date: str | None = None) -> dict[str, Any]:
    """Build a report from the canonical manifest without changing any artifact."""
    if not manifest_path.exists():
        return _empty_report()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _empty_report(f"REPORT_ERROR: corrupt manifest ({type(exc).__name__})")

    if not isinstance(payload, dict) or REQUIRED_FIELDS - payload.keys():
        missing = sorted(REQUIRED_FIELDS - payload.keys()) if isinstance(payload, dict) else ["root object"]
        return _empty_report(f"REPORT_ERROR: missing required fields: {', '.join(missing)}")

    run_date = _run_date(payload["started_at"])
    reference_date = today_date or datetime.now(OPERATIONAL_TIMEZONE).date().isoformat()
    run_is_today = run_date == reference_date
    warnings = list(payload["warnings"] or [])
    errors = list(payload["errors"] or [])
    overall_status = payload["overall_status"]
    if overall_status == "FAILED":
        health_status = FAILED
    elif not run_is_today:
        health_status = WARNING
        if run_date is None and _is_naive_timestamp(payload["started_at"]):
            warnings.append(f"NAIVE_TIMESTAMP: manifest started_at {payload['started_at']!r} has no timezone info; operational date is UNKNOWN")
        warnings.append(f"PREVIOUS_RUN: manifest run date is {run_date or 'UNKNOWN'}, expected {reference_date}")
    elif overall_status == "SUCCESS_WITH_WARNING":
        health_status = WARNING
    elif overall_status == "SUCCESS":
        health_status = HEALTHY
    else:
        health_status = FAILED
        errors.append(f"REPORT_ERROR: unknown overall status {overall_status!r}")

    report = {
        "health_status": health_status,
        "run_present": True,
        "run_is_today": run_is_today,
        "run_id": payload["run_id"],
        "started_at": payload["started_at"],
        "finished_at": payload["finished_at"],
        "run_date": run_date,
        "overall_status": overall_status,
        "failed_phase": payload["failed_phase"],
        "market_status": payload["market_update_status"],
        "investor_status": payload["investor_update_status"],
        "market_latest_date": payload["market_latest_date"],
        "investor_latest_date": payload["investor_latest_date"],
        "gate_status": payload["gate_status"],
        "pipeline_allowed": payload["pipeline_allowed"],
        "dashboard_status": payload["dashboard_status"],
        "signal_count": payload["signal_count"],
        "zero_signal": bool(payload["zero_signal"]),
        "warnings": warnings,
        "errors": errors,
    }
    report["operator_actions"] = _actions_for(report)
    return report


def render_report(report: dict[str, Any]) -> str:
    lines = ["=== DAILY HEALTH REPORT ===", f"HEALTH: {report['health_status']}"]
    if not report["run_present"]:
        lines.extend(["RUN: NOT FOUND", "PIPELINE: UNKNOWN"])
    else:
        lines.extend([
            f"RUN ID: {report['run_id']}",
            f"STARTED: {report['started_at']}",
            f"FINISHED: {report['finished_at']}",
            f"RUN DATE: {report['run_date']} ({'TODAY' if report['run_is_today'] else 'PREVIOUS_RUN'})",
            f"OVERALL STATUS: {report['overall_status']}",
            f"FAILED PHASE: {report['failed_phase'] or 'NONE'}",
            f"MARKET: {report['market_status']} ({report['market_latest_date']})",
            f"INVESTOR: {report['investor_status']} ({report['investor_latest_date']})",
            f"GATE: {report['gate_status']} | PIPELINE ALLOWED: {report['pipeline_allowed']}",
            f"PIPELINE: {report['dashboard_status']}",
            f"SIGNALS: {report['signal_count']}",
        ])
        if report["zero_signal"]:
            lines.append("SIGNAL STATUS: NO SIGNAL TODAY")
    lines.extend([f"WARNING COUNT: {len(report['warnings'])}", f"ERROR COUNT: {len(report['errors'])}"])
    lines.extend(f"WARNING: {item}" for item in report["warnings"])
    lines.extend(f"ERROR: {item}" for item in report["errors"])
    lines.extend(f"ACTION: {item}" for item in report["operator_actions"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Daily Health / Exception Report")
    parser.add_argument("--manifest", type=Path, default=ROOT_DIR / MANIFEST_SOURCE)
    parser.add_argument("--today-date", help="Reference date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()
    report = build_report(args.manifest, args.today_date)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True) if args.json else render_report(report))
    sys.exit(1 if report["health_status"] == FAILED else 0)


if __name__ == "__main__":
    main()
"""
DUAL Shadow STEP 6 - manual daily pipeline orchestration.

This module connects the already verified DUAL STEP 3-5 surfaces:

Evaluate -> Ledger -> Forward Returns -> Performance Summary

It is intentionally independent from the production shadow pipeline,
production scheduler, dashboard, API, and production run registry.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from src import config
from src.config import OUTPUT_DIR
from src.data_provider.csv_provider import CsvDataProvider
from src.dual_shadow_forward_returns import (
    DEFAULT_FORWARD_RETURN_PATH,
    DualForwardReturnStore,
    build_forward_return_records,
)
from src.dual_shadow_ledger import (
    DEFAULT_LEDGER_PATH,
    DualShadowLedgerStore,
    get_source_commit,
    run_dual_shadow_ledger_build,
)
from src.dual_shadow_performance import (
    DEFAULT_PERFORMANCE_SUMMARY_PATH,
    build_performance_summary,
)

STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_NO_NEW_EVIDENCE = "SUCCESS_NO_NEW_EVIDENCE"
STATUS_DATA_NOT_READY = "DATA_NOT_READY"
STATUS_FAILED = "FAILED"

DEFAULT_RUN_REGISTRY_PATH = OUTPUT_DIR / "dual_shadow_run_registry.jsonl"

MARKET_REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}
INVESTOR_REQUIRED_COLUMNS = {"date"}


@dataclass
class PreflightReport:
    ready: bool
    trade_date: str | None
    market_latest_date: str | None
    investor_latest_date: str | None
    status: str
    error_code: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class DualDailyPipelineResult:
    run_id: str
    trade_date: str | None
    status: str
    started_at: str
    finished_at: str
    source_commit: str
    preflight: dict[str, object]
    ledger: dict[str, int] = field(default_factory=dict)
    forward_returns: dict[str, int] = field(default_factory=dict)
    performance: dict[str, object] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_target_date(target_trade_date: str | date | None) -> str | None:
    if target_trade_date is None:
        return None
    if isinstance(target_trade_date, date):
        return target_trade_date.isoformat()
    return date.fromisoformat(str(target_trade_date)).isoformat()


def _latest_date_from_frame(
    frame: pd.DataFrame,
    required_columns: set[str],
    label: str,
    ticker: str,
) -> str:
    columns = {str(c).strip().lower() for c in frame.columns}
    missing = required_columns - columns
    if missing:
        raise ValueError(f"{label} data for {ticker} missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{label} data for {ticker} has 0 rows")

    date_series = pd.to_datetime(frame["date"], errors="coerce")
    if date_series.isna().any():
        raise ValueError(f"{label} data for {ticker} has invalid date values")
    return date_series.max().strftime("%Y-%m-%d")


def _load_market_data(
    tickers: dict[str, str],
    price_data: dict[str, pd.DataFrame] | None,
    data_raw_dir: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    provider = CsvDataProvider(data_raw_dir)
    frames: dict[str, pd.DataFrame] = {}
    latest_dates: dict[str, str] = {}
    for ticker in tickers:
        frame = price_data.get(ticker) if price_data is not None else provider.load(ticker)
        if frame is None:
            raise FileNotFoundError(f"market data missing for ticker {ticker}")
        frames[ticker] = frame
        latest_dates[ticker] = _latest_date_from_frame(frame, MARKET_REQUIRED_COLUMNS, "market", ticker)
    return frames, latest_dates


def _load_investor_dates(
    tickers: dict[str, str],
    investor_data: dict[str, pd.DataFrame] | None,
    investor_dir: Path,
) -> dict[str, str]:
    latest_dates: dict[str, str] = {}
    for ticker in tickers:
        if investor_data is not None:
            frame = investor_data.get(ticker)
            if frame is None:
                raise FileNotFoundError(f"investor data missing for ticker {ticker}")
        else:
            path = investor_dir / f"{ticker}_investor.csv"
            if not path.exists():
                raise FileNotFoundError(f"investor data missing for ticker {ticker}")
            frame = pd.read_csv(path, usecols=lambda col: str(col).strip().lower() == "date")
        latest_dates[ticker] = _latest_date_from_frame(frame, INVESTOR_REQUIRED_COLUMNS, "investor", ticker)
    return latest_dates


def _uniform_latest(dates_by_ticker: dict[str, str], label: str) -> str:
    unique = set(dates_by_ticker.values())
    if len(unique) != 1:
        detail = ", ".join(f"{ticker}={value}" for ticker, value in sorted(dates_by_ticker.items()))
        raise ValueError(f"{label} latest dates differ across tickers: {detail}")
    return next(iter(unique))


def run_preflight(
    target_trade_date: str | date | None = None,
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    investor_data: dict[str, pd.DataFrame] | None = None,
    data_raw_dir: Path | None = None,
    investor_dir: Path | None = None,
) -> tuple[PreflightReport, dict[str, pd.DataFrame]]:
    """Read-only DUAL source readiness check.

    A ready report means all target tickers have uniform market and investor
    latest dates, and both match the selected trade date.
    """
    tickers = tickers or config.TICKERS
    data_raw_dir = Path(data_raw_dir or config.DATA_RAW_DIR)
    investor_dir = Path(investor_dir or (config.ROOT_DIR / "data" / "investor"))
    target = _normalize_target_date(target_trade_date)

    try:
        market_frames, market_dates = _load_market_data(tickers, price_data, data_raw_dir)
        investor_dates = _load_investor_dates(tickers, investor_data, investor_dir)
        market_latest = _uniform_latest(market_dates, "market")
        investor_latest = _uniform_latest(investor_dates, "investor")
    except FileNotFoundError as exc:
        return PreflightReport(False, target, None, None, STATUS_DATA_NOT_READY, "SOURCE_MISSING", str(exc)), {}
    except ValueError as exc:
        return PreflightReport(False, target, None, None, STATUS_FAILED, "MALFORMED_SOURCE", str(exc)), {}

    trade_date = target or market_latest
    if market_latest != trade_date or investor_latest != trade_date:
        detail = f"market_latest={market_latest}, investor_latest={investor_latest}, target={trade_date}"
        return (
            PreflightReport(
                False,
                trade_date,
                market_latest,
                investor_latest,
                STATUS_DATA_NOT_READY,
                "SOURCE_DATE_NOT_READY",
                detail,
            ),
            market_frames,
        )

    return (
        PreflightReport(
            True,
            trade_date,
            market_latest,
            investor_latest,
            STATUS_SUCCESS,
            None,
            "target trade date data ready",
        ),
        market_frames,
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_registry(path: Path | None, result: DualDailyPipelineResult) -> None:
    if path is None:
        return
    payload = result.to_dict()
    row = {
        "run_id": result.run_id,
        "trade_date": result.trade_date,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "status": result.status,
        "source_commit": result.source_commit,
        "ledger_saved": int(result.ledger.get("saved", 0)),
        "forward_return_saved": int(result.forward_returns.get("saved", 0)),
        "performance_status": result.performance.get("status"),
        "error_code": result.errors[0]["code"] if result.errors else None,
        "error_message": result.errors[0]["message"] if result.errors else None,
        "result": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _final_status(ledger: dict[str, int], forward_returns: dict[str, int]) -> str:
    if int(ledger.get("saved", 0)) > 0 or int(forward_returns.get("saved", 0)) > 0:
        return STATUS_SUCCESS
    return STATUS_SUCCESS_NO_NEW_EVIDENCE


def _error(code: str, exc: BaseException) -> dict[str, str]:
    return {"code": code, "message": f"{type(exc).__name__}: {exc}"}


def run_dual_shadow_daily_pipeline(
    target_trade_date: str | date | None = None,
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    investor_data: dict[str, pd.DataFrame] | None = None,
    ledger_store: DualShadowLedgerStore | None = None,
    forward_store: DualForwardReturnStore | None = None,
    performance_summary_path: Path | None = DEFAULT_PERFORMANCE_SUMMARY_PATH,
    run_registry_path: Path | None = DEFAULT_RUN_REGISTRY_PATH,
    data_raw_dir: Path | None = None,
    investor_dir: Path | None = None,
    repo_root: Path | None = None,
) -> DualDailyPipelineResult:
    """Run the manual DUAL daily pipeline once.

    Evidence stores remain append-only and idempotent. A later phase failure
    never rolls back evidence appended by an earlier successful phase.
    """
    tickers = tickers or config.TICKERS
    run_id = str(uuid.uuid4())
    started_at = _utc_now()
    source_commit = get_source_commit(repo_root)
    empty_preflight = PreflightReport(False, None, None, None, STATUS_FAILED).to_dict()
    result = DualDailyPipelineResult(
        run_id=run_id,
        trade_date=None,
        status=STATUS_FAILED,
        started_at=started_at,
        finished_at=started_at,
        source_commit=source_commit,
        preflight=empty_preflight,
    )

    try:
        preflight, resolved_price_data = run_preflight(
            target_trade_date=target_trade_date,
            tickers=tickers,
            price_data=price_data,
            investor_data=investor_data,
            data_raw_dir=data_raw_dir,
            investor_dir=investor_dir,
        )
        result.preflight = preflight.to_dict()
        result.trade_date = preflight.trade_date
        if not preflight.ready:
            result.status = preflight.status
            if preflight.status == STATUS_FAILED:
                result.errors.append({"code": preflight.error_code or "PREFLIGHT_FAILED", "message": preflight.detail})
            return result

        ledger_store = ledger_store or DualShadowLedgerStore(path=DEFAULT_LEDGER_PATH)
        forward_store = forward_store or DualForwardReturnStore(path=DEFAULT_FORWARD_RETURN_PATH)

        _, ledger_stats = run_dual_shadow_ledger_build(
            tickers=tickers,
            price_data=resolved_price_data,
            store=ledger_store,
            source_commit=source_commit,
        )
        result.ledger = {
            "checked": int(ledger_stats.get("checked", 0)),
            "saved": int(ledger_stats.get("saved", 0)),
            "duplicate": int(ledger_stats.get("duplicate_skip", 0)),
            "not_evaluable": int(ledger_stats.get("not_evaluable", 0)),
            "both_yes": int(ledger_stats.get("both_yes", 0)),
            "baseline_only": int(ledger_stats.get("baseline_only", 0)),
            "challenger_only": int(ledger_stats.get("challenger_only", 0)),
            "both_no": int(ledger_stats.get("both_no", 0)),
        }

        ledger_df = ledger_store.load()
        records, forward_stats = build_forward_return_records(
            ledger_df,
            forward_store=forward_store,
            price_map=resolved_price_data,
            source_commit=source_commit,
        )
        saved = 0
        for record in records:
            if forward_store.add(record):
                saved += 1
        forward_stats["saved"] = saved
        result.forward_returns = {
            "ledger_rows": int(forward_stats.get("ledger_rows", 0)),
            "available": int(forward_stats.get("available", 0)),
            "saved": int(forward_stats.get("saved", 0)),
            "duplicate": int(forward_stats.get("already_recorded", 0)),
            "not_available": int(forward_stats.get("not_available", 0)),
            "skipped_not_evaluable": int(forward_stats.get("skipped_not_evaluable", 0)),
            "missing_price": int(forward_stats.get("missing_price", 0)),
        }

        summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
        summary_payload = summary.to_dict()
        result.performance = {
            "status": summary.status,
            "total_evidence_rows": summary.total_evidence_rows,
        }
        if performance_summary_path is not None:
            _write_json(Path(performance_summary_path), summary_payload)

        result.status = _final_status(result.ledger, result.forward_returns)
        return result
    except Exception as exc:
        result.status = STATUS_FAILED
        result.errors.append(_error("PIPELINE_FAILED", exc))
        return result
    finally:
        result.finished_at = _utc_now()
        _append_registry(Path(run_registry_path) if run_registry_path is not None else None, result)

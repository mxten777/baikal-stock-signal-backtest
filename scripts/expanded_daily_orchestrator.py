"""Single operational entry point for the isolated Expanded Shadow workflow."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.expanded_candidate_performance import (
    PROVIDER_LEGACY,
    PROVIDER_NAVER,
    _load_benchmark_map,
    _load_price_map,
    _load_signal_ledger,
)
from scripts.expanded_shadow_daily_run import _source_commit
from src.expanded_candidate_performance import (
    ExpandedCandidatePerformanceStore,
    benchmark_symbols_needed,
    run_expanded_candidate_performance,
)
from src.expanded_benchmark_provider import ExpandedBenchmark
from src.expanded_forward_validation import (
    run_forward_validation,
    save_forward_validation_status,
    validation_error_payload,
)
from src.expanded_shadow_daily import STATUS_ALREADY_COMPLETED, STATUS_DATA_NOT_READY, ExpandedDailyResult, run_expanded_shadow_daily
from src.expanded_shadow_ops import ExpandedShadowPaths, utc_now_iso
from src.expanded_shadow_pipeline import STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES
from src.shadow_tracking import normalize_market


FINAL_SUCCESS = "SUCCESS"
FINAL_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
FINAL_DAILY_RUN_FAILED = "DAILY_RUN_FAILED"
FINAL_PERFORMANCE_UPDATE_FAILED = "PERFORMANCE_UPDATE_FAILED"
PERFORMANCE_NOT_RUN = "NOT_RUN"
PERFORMANCE_SUCCESS = "SUCCESS"
PERFORMANCE_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
PERFORMANCE_FAILED = "FAILED"
SUCCESS_EXIT_STATUSES = {
    FINAL_SUCCESS,
    FINAL_SUCCESS_WITH_WARNING,
    STATUS_DATA_NOT_READY,
    STATUS_ALREADY_COMPLETED,
}


@dataclass(frozen=True)
class ExpandedOrchestrationResult:
    source_date: str
    daily_run_status: str
    performance_status: str
    candidates_registered: int
    candidates_updated: int
    final_status: str
    started_at: str
    completed_at: str
    runtime_seconds: float
    daily_run: dict[str, Any] | None = None
    performance: dict[str, Any] | None = None
    benchmark_errors: dict[str, str] = field(default_factory=dict)
    benchmark_warnings: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_performance_stage(
    *,
    repo_root: Path,
    source_date: str,
    benchmark_provider: str = PROVIDER_LEGACY,
    now_func: Callable[[], str] = utc_now_iso,
) -> dict[str, Any]:
    if benchmark_provider not in {PROVIDER_LEGACY, PROVIDER_NAVER}:
        raise ValueError(f"unsupported benchmark provider: {benchmark_provider!r}")
    paths = ExpandedShadowPaths(repo_root)
    signal_ledger = _load_signal_ledger(paths)
    candidates = _candidate_rows(signal_ledger)
    price_map = _load_price_map(paths, source_date, candidates)
    performance_store = ExpandedCandidatePerformanceStore(paths)
    benchmark_candidates = candidates
    if benchmark_provider == PROVIDER_NAVER:
        needed_symbols = benchmark_symbols_needed(
            signal_ledger,
            price_map=price_map,
            store=performance_store,
        )
        benchmark_candidates = candidates.loc[
            candidates["market"].map(normalize_market).isin(needed_symbols)
        ]
    benchmark_map, benchmark_errors = _load_benchmark_map(
        benchmark_candidates, source_date, provider=benchmark_provider,
    )
    stats = run_expanded_candidate_performance(
        repo_root=repo_root,
        price_map=price_map,
        benchmark_map=benchmark_map,
        now_func=now_func,
        store=performance_store,
    )
    try:
        validation = run_forward_validation(
            paths=paths,
            signal_ledger=signal_ledger,
            price_map=price_map,
            benchmark_map=benchmark_map,
            now_func=now_func,
        )
    except Exception as exc:  # noqa: BLE001 - isolated Validation must not change operational status
        validation = validation_error_payload(paths, exc)
        try:
            save_forward_validation_status(paths, validation)
        except Exception as status_exc:  # noqa: BLE001 - status persistence must not block operations
            validation["warnings"].append(
                f"Forward Validation status could not be saved: {status_exc}"
            )
    diagnostics = _benchmark_source_diagnostics(
        benchmark_candidates, source_date, benchmark_provider, benchmark_map, benchmark_errors,
    )
    warnings = _benchmark_warning_details(diagnostics, stats)
    return {
        **stats,
        "benchmark_provider": benchmark_provider,
        "benchmark_errors": benchmark_errors,
        "benchmark_diagnostics": diagnostics,
        "benchmark_status_by_horizon": _benchmark_status_by_horizon(stats),
        "benchmark_warnings": warnings,
        "validation": validation,
    }


def run_expanded_daily_orchestration(
    *,
    repo_root: Path,
    source_date: str,
    source_commit: str = "UNKNOWN",
    daily_runner: Callable[..., ExpandedDailyResult] | None = None,
    performance_runner: Callable[..., dict[str, Any]] | None = None,
    benchmark_provider: str = PROVIDER_LEGACY,
    now_func: Callable[[], str] = utc_now_iso,
) -> ExpandedOrchestrationResult:
    started_at = now_func()
    resolved_daily_runner = daily_runner or run_expanded_shadow_daily
    resolved_performance_runner = performance_runner or run_performance_stage

    try:
        daily = resolved_daily_runner(
            repo_root=repo_root,
            source_date=source_date,
            source_commit=source_commit,
        )
    except Exception as exc:  # noqa: BLE001 - orchestration must isolate phase failures
        return _failed_result(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=FINAL_DAILY_RUN_FAILED,
            daily_run_status=FINAL_DAILY_RUN_FAILED,
            performance_status=PERFORMANCE_NOT_RUN,
            exc=exc,
        )

    daily_payload = daily.to_dict()
    if daily.status == STATUS_DATA_NOT_READY:
        return _result(
            source_date=source_date,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_NOT_RUN,
            final_status=STATUS_DATA_NOT_READY,
            started_at=started_at,
            completed_at=now_func(),
            daily_run=daily_payload,
        )

    allowed_daily_statuses = {STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES, STATUS_ALREADY_COMPLETED}
    if daily.status not in allowed_daily_statuses:
        completed_at = now_func()
        return ExpandedOrchestrationResult(
            source_date=source_date,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_NOT_RUN,
            candidates_registered=0,
            candidates_updated=0,
            final_status=FINAL_DAILY_RUN_FAILED,
            started_at=started_at,
            completed_at=completed_at,
            runtime_seconds=_runtime_seconds(started_at, completed_at),
            daily_run=daily_payload,
            error_code=daily.status,
            error_message=daily.message or "Expanded daily run did not complete successfully.",
        )

    try:
        performance = resolved_performance_runner(
            repo_root=repo_root,
            source_date=source_date,
            benchmark_provider=benchmark_provider,
            now_func=now_func,
        )
    except Exception as exc:  # noqa: BLE001 - completed daily artifacts are never rolled back
        return _failed_result(
            source_date=source_date,
            started_at=started_at,
            completed_at=now_func(),
            final_status=FINAL_PERFORMANCE_UPDATE_FAILED,
            daily_run_status=daily.status,
            performance_status=PERFORMANCE_FAILED,
            exc=exc,
            daily_run=daily_payload,
        )

    benchmark_errors = dict(performance.get("benchmark_errors") or {})
    benchmark_warnings = list(performance.get("benchmark_warnings") or [])
    has_warning = (
        daily.status == STATUS_SUCCESS_WITH_TICKER_FAILURES
        or bool(benchmark_errors)
        or bool(benchmark_warnings)
        or int(performance.get("mismatch") or 0) > 0
    )
    performance_status = (
        PERFORMANCE_SUCCESS_WITH_WARNING
        if benchmark_errors or benchmark_warnings or int(performance.get("mismatch") or 0)
        else PERFORMANCE_SUCCESS
    )
    if has_warning:
        final_status = FINAL_SUCCESS_WITH_WARNING
    elif daily.status == STATUS_ALREADY_COMPLETED:
        final_status = STATUS_ALREADY_COMPLETED
    else:
        final_status = FINAL_SUCCESS
    return _result(
        source_date=source_date,
        daily_run_status=daily.status,
        performance_status=performance_status,
        final_status=final_status,
        started_at=started_at,
        completed_at=now_func(),
        daily_run=daily_payload,
        performance=performance,
        benchmark_errors=benchmark_errors,
        benchmark_warnings=benchmark_warnings,
    )


def _benchmark_source_diagnostics(
    candidates: pd.DataFrame,
    cutoff: str,
    provider: str,
    benchmark_map: dict[str, pd.DataFrame | ExpandedBenchmark],
    benchmark_errors: dict[str, str],
) -> dict[str, dict[str, Any]]:
    symbols = sorted({symbol for symbol in candidates.get("market", pd.Series(dtype=str)).map(normalize_market) if symbol})
    diagnostics: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        source = benchmark_map.get(symbol)
        item: dict[str, Any] = {
            "provider": provider,
            "source": None,
            "cutoff": cutoff,
            "latest_row_date": None,
            "latest_valid_close_date": None,
            "stale": None,
            "duplicate": False,
            "duplicate_dates": [],
            "invalid_source": source is None,
            "invalid_close_dates": [],
            "provider_error": benchmark_errors.get(symbol),
        }
        if isinstance(source, ExpandedBenchmark):
            details = source.diagnostics()
            item.update(
                source=source.source,
                cutoff=details["cutoff_date"],
                latest_row_date=details["latest_row_date"],
                latest_valid_close_date=details["latest_valid_close_date"],
                stale=details["stale"],
                duplicate=bool(details["duplicate_dates"]),
                duplicate_dates=details["duplicate_dates"],
                invalid_source=not source.is_valid,
                invalid_close_dates=details["invalid_close_dates"],
                provider_error=item["provider_error"] or ("; ".join(details["errors"]) or None),
            )
        elif isinstance(source, pd.DataFrame):
            item.update(_frame_benchmark_diagnostics(source, cutoff))
            item["source"] = provider
        diagnostics[symbol] = item
    return diagnostics


def _frame_benchmark_diagnostics(frame: pd.DataFrame, cutoff: str) -> dict[str, Any]:
    if frame.empty or "close" not in frame.columns:
        return {"invalid_source": True}
    dates = pd.to_datetime(frame["date"] if "date" in frame.columns else frame.index, errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce").reset_index(drop=True)
    date_values = pd.Series(dates).dt.strftime("%Y-%m-%d").reset_index(drop=True)
    within_cutoff = date_values.notna() & date_values.le(cutoff)
    duplicates = sorted(set(date_values[within_cutoff & date_values.duplicated(keep=False)]))
    valid_close = closes.map(lambda value: pd.notna(value) and math.isfinite(float(value)) and float(value) > 0)
    valid = within_cutoff & valid_close
    invalid_dates = sorted(set(date_values[within_cutoff & ~valid_close & date_values.notna()]))
    latest_row = date_values[within_cutoff].max()
    latest_valid = date_values[valid].max()
    return {
        "latest_row_date": None if pd.isna(latest_row) else latest_row,
        "latest_valid_close_date": None if pd.isna(latest_valid) else latest_valid,
        "stale": latest_valid is pd.NaT or pd.isna(latest_valid) or latest_valid < cutoff,
        "duplicate": bool(duplicates),
        "duplicate_dates": duplicates,
        "invalid_source": not bool(valid.any()) or bool(duplicates),
        "invalid_close_dates": invalid_dates,
    }


def _benchmark_status_by_horizon(stats: dict[str, int]) -> dict[str, dict[str, int]]:
    return {
        f"{horizon}d": {
            "benchmark_calculated": stats.get(f"benchmark_{horizon}d_calculated", 0),
            "benchmark_already_filled": stats.get(f"benchmark_{horizon}d_already_filled", 0),
            "benchmark_not_due": stats.get(f"benchmark_{horizon}d_not_due", 0),
            "benchmark_missing_start": stats.get(f"benchmark_{horizon}d_missing_start", 0),
            "benchmark_missing_end": stats.get(f"benchmark_{horizon}d_missing_end", 0),
            "benchmark_date_mismatch": stats.get(f"benchmark_{horizon}d_date_mismatch", 0),
            "benchmark_stock_return_conflict": stats.get(f"benchmark_{horizon}d_stock_return_conflict", 0),
            "existing_value_mismatch": stats.get(f"existing_value_mismatch_{horizon}d", 0),
            "existing_benchmark_excess_mismatch": stats.get(f"existing_benchmark_excess_mismatch_{horizon}d", 0),
            "matured_return_missing_endpoint": sum(
                stats.get(f"benchmark_{horizon}d_matured_return_{status}", 0)
                for status in (
                    "missing_start", "missing_end", "stale_source", "date_mismatch",
                    "invalid_source", "after_cutoff", "stock_date_unavailable",
                )
            ) + stats.get(f"benchmark_{horizon}d_matured_return_no_source", 0),
        }
        for horizon in (5, 10, 20)
    }


def _benchmark_warning_details(
    diagnostics: dict[str, dict[str, Any]],
    stats: dict[str, int],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for symbol, item in diagnostics.items():
        if item.get("provider_error"):
            warnings.append({"code": "PROVIDER_ERROR", "symbol": symbol, "detail": item["provider_error"]})
        if item.get("stale") is True:
            warnings.append({"code": "STALE_SOURCE", "symbol": symbol})
        if item.get("invalid_source"):
            warnings.append({"code": "INVALID_SOURCE", "symbol": symbol})
        if item.get("duplicate"):
            warnings.append({"code": "DUPLICATE_DATES", "symbol": symbol, "dates": item.get("duplicate_dates", [])})
        if item.get("invalid_close_dates"):
            warnings.append({"code": "INVALID_CLOSE", "symbol": symbol, "dates": item["invalid_close_dates"]})

    for horizon in (5, 10, 20):
        for status in (
            "missing_start", "missing_end", "stale_source", "date_mismatch",
            "invalid_source", "after_cutoff", "stock_date_unavailable",
        ):
            count = stats.get(f"benchmark_{horizon}d_matured_return_{status}", 0)
            if count:
                warnings.append({
                    "code": "MATURED_RETURN_BENCHMARK_UNAVAILABLE",
                    "horizon": f"{horizon}d",
                    "benchmark_status": status.upper(),
                    "count": count,
                })
        no_source = stats.get(f"benchmark_{horizon}d_matured_return_no_source", 0)
        if no_source:
            warnings.append({
                "code": "MATURED_RETURN_BENCHMARK_UNAVAILABLE",
                "horizon": f"{horizon}d",
                "benchmark_status": "NO_SOURCE",
                "count": no_source,
            })
        conflicts = stats.get(f"benchmark_{horizon}d_stock_return_conflict", 0)
        if conflicts:
            warnings.append({"code": "STOCK_RETURN_CONFLICT", "horizon": f"{horizon}d", "count": conflicts})
        mismatches = stats.get(f"existing_benchmark_excess_mismatch_{horizon}d", 0)
        if mismatches:
            warnings.append({"code": "EXISTING_BENCHMARK_EXCESS_MISMATCH", "horizon": f"{horizon}d", "count": mismatches})
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expanded Shadow daily orchestration")
    parser.add_argument("--source-date", help="Trading date in YYYY-MM-DD; defaults to the current Asia/Seoul date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    source_date = args.source_date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    result = run_expanded_daily_orchestration(
        repo_root=Path.cwd(),
        source_date=source_date,
        source_commit=_source_commit(Path.cwd()),
    )
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{result.final_status}: {result.source_date}")
        print(f"Daily={result.daily_run_status} Performance={result.performance_status}")
    return 0 if result.final_status in SUCCESS_EXIT_STATUSES else 1


def _candidate_rows(signal_ledger: pd.DataFrame) -> pd.DataFrame:
    if signal_ledger.empty or "decision" not in signal_ledger:
        return signal_ledger.iloc[0:0]
    return signal_ledger.loc[signal_ledger["decision"].astype(str) == "CANDIDATE"]


def _result(
    *,
    source_date: str,
    daily_run_status: str,
    performance_status: str,
    final_status: str,
    started_at: str,
    completed_at: str,
    daily_run: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    benchmark_errors: dict[str, str] | None = None,
    benchmark_warnings: list[dict[str, Any]] | None = None,
) -> ExpandedOrchestrationResult:
    performance = performance or {}
    return ExpandedOrchestrationResult(
        source_date=source_date,
        daily_run_status=daily_run_status,
        performance_status=performance_status,
        candidates_registered=int(performance.get("registered") or 0),
        candidates_updated=int(performance.get("updated") or 0),
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        daily_run=daily_run,
        performance=performance or None,
        benchmark_errors=benchmark_errors or {},
        benchmark_warnings=benchmark_warnings or [],
    )


def _failed_result(
    *,
    source_date: str,
    started_at: str,
    completed_at: str,
    final_status: str,
    daily_run_status: str,
    performance_status: str,
    exc: Exception,
    daily_run: dict[str, Any] | None = None,
) -> ExpandedOrchestrationResult:
    return ExpandedOrchestrationResult(
        source_date=source_date,
        daily_run_status=daily_run_status,
        performance_status=performance_status,
        candidates_registered=0,
        candidates_updated=0,
        final_status=final_status,
        started_at=started_at,
        completed_at=completed_at,
        runtime_seconds=_runtime_seconds(started_at, completed_at),
        daily_run=daily_run,
        error_code=type(exc).__name__,
        error_message=str(exc),
    )


def _runtime_seconds(started_at: str, completed_at: str) -> float:
    try:
        return max((datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)).total_seconds(), 0.0)
    except ValueError:
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
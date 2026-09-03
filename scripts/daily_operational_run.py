"""Canonical daily operational orchestration for the Shadow dashboard."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
MANIFEST_SOURCE = "output/daily_operational_run.json"
LOCK_SOURCE = "output/daily_operational_run.lock"
LOCK_STALE_AFTER = timedelta(hours=12)

STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
STATUS_FAILED = "FAILED"
PHASE_PRECHECK = "PRECHECK"
PHASE_MARKET_UPDATE = "MARKET_UPDATE"
PHASE_INVESTOR_UPDATE = "INVESTOR_UPDATE"
PHASE_INPUT_GATE = "INPUT_GATE"
PHASE_DASHBOARD_RUNNER = "DASHBOARD_RUNNER"


@dataclass
class PhaseResult:
    name: str
    status: str
    started_at: str
    finished_at: str
    message: str
    duration_seconds: float = 0.0
    error_code: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DailyOperationalResult:
    run_id: str
    started_at: str
    finished_at: str
    overall_status: str
    failed_phase: str | None
    market_update_status: str | None
    investor_update_status: str | None
    gate_status: str | None
    pipeline_allowed: bool | None
    dashboard_status: str | None
    market_latest_date: str | None
    investor_latest_date: str | None
    signal_count: int | None
    zero_signal: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    phases: list[PhaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "phases": [phase.to_dict() for phase in self.phases]}


@dataclass(frozen=True)
class DailyOperationDependencies:
    market_update: Callable[[], Any]
    investor_update: Callable[[], Any]
    input_gate: Callable[[], Any]
    dashboard_runner: Callable[[], Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _duration_seconds(started_at: str, finished_at: str) -> float:
    try:
        return max((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds(), 0.0)
    except ValueError:
        return 0.0


def default_dependencies(repo_root: Path, allow_source_lag: bool) -> DailyOperationDependencies:
    """Create thin adapters over the existing production components."""
    from dashboard.runner import run_dashboard_pipeline
    from scripts.input_integrity_gate import get_default_tickers, run_input_integrity_gate
    from scripts.safe_investor_update import NaverInvestorSource, SafeInvestorUpdater
    from scripts.safe_market_update import DEFAULT_TICKERS, FinanceDataReaderSource, SafeMarketUpdater

    tickers = dict(DEFAULT_TICKERS)
    raw_dir = repo_root / "data" / "raw"
    investor_dir = repo_root / "data" / "investor"
    return DailyOperationDependencies(
        market_update=lambda: SafeMarketUpdater(tickers, raw_dir, FinanceDataReaderSource()).run(),
        investor_update=lambda: SafeInvestorUpdater(tickers, investor_dir, raw_dir, NaverInvestorSource()).run(),
        input_gate=lambda: run_input_integrity_gate(
            raw_dir=raw_dir, investor_dir=investor_dir, tickers=get_default_tickers(), allow_source_lag=allow_source_lag
        ),
        dashboard_runner=lambda: run_dashboard_pipeline(repo_root=repo_root),
    )


def write_manifest_atomic(manifest_path: Path, result: DailyOperationalResult) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{manifest_path.name}.", suffix=".tmp", dir=str(manifest_path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, manifest_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


class DailyRunLock:
    def __init__(self, lock_path: Path, now_func: Callable[[], str] = utc_now_iso) -> None:
        self.lock_path = lock_path
        self.now_func = now_func
        self.acquired = False

    def acquire(self, run_id: str) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_lock()
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"run_id": run_id, "started_at": self.now_func()}, handle)
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
            self.acquired = False

    def _remove_stale_lock(self) -> None:
        if not self.lock_path.exists():
            return
        modified_at = datetime.fromtimestamp(self.lock_path.stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified_at > LOCK_STALE_AFTER:
            self.lock_path.unlink(missing_ok=True)


def _phase_from_result(name: str, value: Any, started_at: str, finished_at: str) -> PhaseResult:
    payload = value.to_dict() if hasattr(value, "to_dict") else {}
    status = str(getattr(value, "status", payload.get("status", "UNKNOWN")))
    metrics = {key: item for key, item in payload.items() if key not in {"status", "errors", "warnings"}}
    errors = list(getattr(value, "errors", payload.get("errors", [])) or [])
    return PhaseResult(
        name, status, started_at, finished_at, "; ".join(errors) if errors else status,
        duration_seconds=_duration_seconds(started_at, finished_at), metrics=metrics,
    )


def _run_phase(name: str, action: Callable[[], Any], now_func: Callable[[], str]) -> tuple[PhaseResult, Any | None]:
    started_at = now_func()
    try:
        value = action()
    except Exception as exc:
        finished_at = now_func()
        return PhaseResult(
            name, "FAILED", started_at, finished_at, f"{type(exc).__name__}: {exc}",
            duration_seconds=_duration_seconds(started_at, finished_at), error_code=type(exc).__name__,
        ), None
    return _phase_from_result(name, value, started_at, now_func()), value


def _precheck(repo_root: Path) -> tuple[str, dict[str, Any], str]:
    required = [repo_root / "data" / "raw", repo_root / "data" / "investor", repo_root / "output"]
    missing = [str(path) for path in required if not path.is_dir()]
    if missing:
        return "FAILED", {"missing_directories": missing}, "required directories are missing"
    from scripts.safe_market_update import DEFAULT_TICKERS
    if not DEFAULT_TICKERS:
        return "FAILED", {}, "configured ticker universe is empty"
    if not os.access(repo_root / "output", os.W_OK):
        return "FAILED", {}, "output directory is not writable"
    return "PASS", {"ticker_count": len(DEFAULT_TICKERS)}, "repository and environment ready"


def run_daily_operation(*, repo_root: Path = ROOT_DIR, dependencies: DailyOperationDependencies | None = None,
                        allow_source_lag: bool = True, now_func: Callable[[], str] = utc_now_iso,
                        write_manifest: bool = True, use_lock: bool = True) -> DailyOperationalResult:
    """Run one ordered daily operation without directly invoking Shadow pipeline scripts."""
    repo_root = Path(repo_root)
    dependencies = dependencies or default_dependencies(repo_root, allow_source_lag)
    run_id, started_at = uuid.uuid4().hex, now_func()
    lock = DailyRunLock(repo_root / LOCK_SOURCE, now_func)
    phases: list[PhaseResult] = []
    warnings: list[str] = []
    errors: list[str] = []
    failed_phase: str | None = None
    market_status = investor_status = gate_status = dashboard_status = None
    pipeline_allowed: bool | None = None
    market_latest_date = investor_latest_date = None
    signal_count: int | None = None

    if use_lock and not lock.acquire(run_id):
        result = DailyOperationalResult(run_id, started_at, now_func(), STATUS_FAILED, PHASE_PRECHECK, None, None, None, None, None, None, None, None, False, errors=["CONCURRENT_RUN: another daily operation is active"])
        if write_manifest:
            _write_manifest_or_warning(repo_root / MANIFEST_SOURCE, result)
        return result

    try:
        status, metrics, message = _precheck(repo_root)
        precheck_finished_at = now_func()
        phases.append(PhaseResult(
            PHASE_PRECHECK, status, started_at, precheck_finished_at, message,
            duration_seconds=_duration_seconds(started_at, precheck_finished_at), metrics=metrics,
        ))
        if status == "FAILED":
            failed_phase = PHASE_PRECHECK
            errors.append(message)
        else:
            market_phase, market_value = _run_phase(PHASE_MARKET_UPDATE, dependencies.market_update, now_func)
            phases.append(market_phase)
            market_status = market_phase.status
            market_latest_date = getattr(market_value, "published_latest_date", None)
            if market_status == "FAILED":
                failed_phase, errors = PHASE_MARKET_UPDATE, [market_phase.message]
            else:
                investor_phase, investor_value = _run_phase(PHASE_INVESTOR_UPDATE, dependencies.investor_update, now_func)
                phases.append(investor_phase)
                investor_status = investor_phase.status
                investor_latest_date = getattr(investor_value, "published_latest_date", None)
                if investor_status == "FAILED":
                    failed_phase, errors = PHASE_INVESTOR_UPDATE, [investor_phase.message]
                else:
                    if investor_status == "SOURCE_LAG":
                        warnings.append("INVESTOR_SOURCE_LAG: deferred to input gate policy")
                    gate_phase, gate_value = _run_phase(PHASE_INPUT_GATE, dependencies.input_gate, now_func)
                    phases.append(gate_phase)
                    gate_status = gate_phase.status
                    pipeline_allowed = bool(getattr(gate_value, "pipeline_allowed", False))
                    market_latest_date = getattr(gate_value, "market_latest_date", market_latest_date)
                    investor_latest_date = getattr(gate_value, "investor_latest_date", investor_latest_date)
                    if gate_status == "FAILED" or not pipeline_allowed:
                        failed_phase, errors = PHASE_INPUT_GATE, [gate_phase.message]
                    else:
                        if gate_status == "PASS_WITH_WARNING":
                            warnings.append("INPUT_GATE_WARNING")
                        runner_phase, runner_value = _run_phase(PHASE_DASHBOARD_RUNNER, dependencies.dashboard_runner, now_func)
                        if runner_value is not None:
                            metadata = getattr(runner_value, "metadata", {})
                            dashboard_status = str(metadata.get("pipeline_status", runner_phase.status))
                            runner_phase.status, runner_phase.message = dashboard_status, dashboard_status
                            runner_phase.metrics.update(metadata)
                            signal_count = metadata.get("record_count")
                        else:
                            dashboard_status = runner_phase.status
                        phases.append(runner_phase)
                        if dashboard_status != "SUCCESS":
                            failed_phase, errors = PHASE_DASHBOARD_RUNNER, [runner_phase.message]
    finally:
        lock.release()

    zero_signal = signal_count == 0 and dashboard_status == "SUCCESS"
    overall_status = STATUS_FAILED if failed_phase else (STATUS_SUCCESS_WITH_WARNING if warnings else STATUS_SUCCESS)
    result = DailyOperationalResult(run_id, started_at, now_func(), overall_status, failed_phase, market_status, investor_status, gate_status, pipeline_allowed, dashboard_status, market_latest_date, investor_latest_date, signal_count, zero_signal, warnings, errors, phases)
    if write_manifest:
        _write_manifest_or_warning(repo_root / MANIFEST_SOURCE, result)
    return result


def _write_manifest_or_warning(manifest_path: Path, result: DailyOperationalResult) -> None:
    try:
        write_manifest_atomic(manifest_path, result)
    except OSError as exc:
        result.warnings.append(f"MANIFEST_WRITE_FAILED: {type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the canonical BAIKAL daily operation.")
    parser.add_argument("--json", action="store_true", help="Print the structured operational result as JSON.")
    parser.add_argument("--disallow-source-lag", action="store_true", help="Require the gate to reject source lag.")
    args = parser.parse_args(argv)
    result = run_daily_operation(allow_source_lag=not args.disallow_source_lag)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) if args.json else f"Daily operational status: {result.overall_status}\nRun ID: {result.run_id}")
    return 0 if result.overall_status != STATUS_FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
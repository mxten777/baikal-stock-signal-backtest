"""Integrated operational safety checks for Dashboard STEP 7-8."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from dashboard.operations import manual_run_capability, operations_detail, operations_exception, operations_history
from scripts.daily_run_registry import (
    EVENT_ATTEMPT_COMPLETED,
    RegistryRecord,
    _compute_event_id,
    append_record,
    read_registry,
)
from scripts.daily_operational_run import DailyOperationalResult, PhaseResult
from scripts.daily_scheduler import (
    OPERATIONAL_TIMEZONE,
    PHASE_INPUT_GATE,
    run_scheduler_tick,
)

TICKERS = {"005930": "Samsung", "000660": "Hynix"}
TRADE_DATE = date(2026, 9, 4)


def _seed_repo(root: Path, latest: str = "2026-09-04") -> None:
    for folder in (root / "data/raw", root / "data/investor", root / "output"):
        folder.mkdir(parents=True, exist_ok=True)
    for ticker in TICKERS:
        (root / "data/raw" / f"{ticker}.csv").write_text(
            "date,open,high,low,close,volume\n2026-09-03,1,2,1,2,10\n"
            f"{latest},1,2,1,2,10\n",
            encoding="utf-8",
        )
        (root / "data/investor" / f"{ticker}_investor.csv").write_text(
            "date,ticker,foreign_net_buy,institution_net_buy\n"
            f"2026-09-03,{ticker},1,1\n{latest},{ticker},1,1\n",
            encoding="utf-8",
        )
    for name, content in {
        "signals.csv": "ticker,signal_date\n005930,2026-09-03\n",
        "shadow_signal_records.csv": "ticker,signal_date\n005930,2026-09-03\n",
        "validation_artifact.csv": "check,status\ninput,PASS\n",
        "existing_manifest.json": '{"status":"SUCCESS"}\n',
    }.items():
        (root / "output" / name).write_text(content, encoding="utf-8")


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 4, hour, minute, tzinfo=OPERATIONAL_TIMEZONE)


def _result(status: str = "SUCCESS", *, failed_phase: str | None = None, message: str = "") -> DailyOperationalResult:
    phases = []
    if failed_phase:
        phases = [PhaseResult(failed_phase, "FAILED", "start", "finish", message)]
    return DailyOperationalResult(
        f"run-{status.lower()}", "2026-09-04T09:30:00+00:00", "2026-09-04T09:31:00+00:00",
        status, failed_phase, "UPDATED", "UPDATED", "PASS", True, "SUCCESS" if not failed_phase else None,
        "2026-09-04", "2026-09-04", 1, False, [], [message] if message else [], phases,
    )


def _write_state(root: Path, **updates: object) -> None:
    state = {
        "target_trade_date": "2026-09-04", "scheduler_date": "2026-09-04", "current_status": "FAILED",
        "attempt": 4, "operator_action_required": True, "operator_action_code": "MANUAL_RERUN_ALLOWED",
    }
    state.update(updates)
    (root / "output/daily_scheduler_state.json").write_text(json.dumps(state), encoding="utf-8")


def test_realistic_full_day_warning_has_one_terminal_run_and_read_only_operations(tmp_path: Path) -> None:
    _seed_repo(tmp_path, latest="2026-09-03")
    calls: list[str] = []
    results = iter([_result("FAILED", failed_phase="MARKET_UPDATE", message="connection reset"), _result("SUCCESS_WITH_WARNING")])

    def operation(**_: object) -> DailyOperationalResult:
        calls.append("run")
        return next(results)

    first = run_scheduler_tick(repo_root=tmp_path, now=_at(18, 30), tickers=TICKERS, run_operation=operation)
    assert first.scheduler_status == "RETRY_PENDING"
    assert calls == []
    assert run_scheduler_tick(repo_root=tmp_path, now=_at(18, 45), tickers=TICKERS, run_operation=operation).action == "WAITING_RETRY_SLOT"

    _seed_repo(tmp_path)
    second = run_scheduler_tick(repo_root=tmp_path, now=_at(19, 0), tickers=TICKERS, run_operation=operation)
    assert second.scheduler_status == "RETRY_PENDING"
    assert len(calls) == 1
    assert manual_run_capability(tmp_path)["allowed"] is False

    _seed_repo(tmp_path)
    final = run_scheduler_tick(repo_root=tmp_path, now=_at(19, 30), tickers=TICKERS, run_operation=operation)
    assert final.scheduler_status == "SUCCESS_WITH_WARNING"
    assert run_scheduler_tick(repo_root=tmp_path, now=_at(19, 31), tickers=TICKERS, run_operation=operation).action == "ALREADY_TERMINAL"
    assert len(calls) == 2
    assert len(read_registry(tmp_path / "output/daily_run_registry.jsonl")) == 3
    assert operations_history(tmp_path)[0]["final_status"] == "SUCCESS_WITH_WARNING"
    assert operations_detail(tmp_path, "2026-09-04")
    assert operations_exception(tmp_path, "2026-09-04")["retryable"] is False


def test_failure_day_exhausts_retry_and_stays_manual_capability_gated(tmp_path: Path) -> None:
    _seed_repo(tmp_path, latest="2026-09-03")
    operation = lambda **_: (_ for _ in ()).throw(AssertionError("readiness failure must not call orchestrator"))
    for hour, minute in ((18, 30), (19, 0), (19, 30), (20, 0)):
        result = run_scheduler_tick(repo_root=tmp_path, now=_at(hour, minute), tickers=TICKERS, run_operation=operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "RETRY_EXHAUSTED"
    assert manual_run_capability(tmp_path)["allowed"] is True
    after = run_scheduler_tick(repo_root=tmp_path, now=_at(20, 10), tickers=TICKERS, run_operation=operation)
    assert after.action == "ALREADY_TERMINAL"
    assert len(read_registry(tmp_path / "output/daily_run_registry.jsonl")) == 4


def test_integrity_block_and_non_trading_day_are_closed_paths(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    calls = 0

    def blocked_operation(**_: object) -> DailyOperationalResult:
        nonlocal calls
        calls += 1
        return _result("FAILED", failed_phase=PHASE_INPUT_GATE, message="integrity failed")

    blocked = run_scheduler_tick(repo_root=tmp_path, now=_at(18, 30), tickers=TICKERS, run_operation=blocked_operation)
    assert blocked.scheduler_status == "BLOCKED"
    assert calls == 1
    assert manual_run_capability(tmp_path)["allowed"] is False
    assert run_scheduler_tick(repo_root=tmp_path, now=_at(19, 0), tickers=TICKERS, run_operation=blocked_operation).action == "ALREADY_TERMINAL"

    holiday = datetime(2026, 9, 25, 18, 30, tzinfo=OPERATIONAL_TIMEZONE)
    non_trading = run_scheduler_tick(repo_root=tmp_path / "holiday", now=holiday, tickers=TICKERS, run_operation=blocked_operation)
    assert non_trading.scheduler_status == "NON_TRADING_DAY"
    assert run_scheduler_tick(repo_root=tmp_path / "holiday", now=holiday, tickers=TICKERS, run_operation=blocked_operation).action == "ALREADY_TERMINAL"
    assert len(read_registry(tmp_path / "holiday/output/daily_run_registry.jsonl")) == 1
    assert calls == 1


def test_state_loss_and_registry_idempotency_do_not_duplicate_daily_operation(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    operation_calls = 0

    def operation(**_: object) -> DailyOperationalResult:
        nonlocal operation_calls
        operation_calls += 1
        return _result()

    run_scheduler_tick(repo_root=tmp_path, now=_at(18, 30), tickers=TICKERS, run_operation=operation)
    state = tmp_path / "output/daily_scheduler_state.json"
    state.unlink()
    recovered = run_scheduler_tick(repo_root=tmp_path, now=_at(19, 0), tickers=TICKERS, run_operation=operation)
    assert recovered.action == "ALREADY_TERMINAL"
    assert operation_calls == 1

    record = RegistryRecord(1, _compute_event_id("2026-09-04", 0, 1, "SUCCESS"), "2026-09-04", "2026-09-04", "Asia/Seoul", EVENT_ATTEMPT_COMPLETED, "SUCCESS")
    registry = tmp_path / "output/duplicate.jsonl"
    assert append_record(registry, record) == (True, None)
    assert append_record(registry, record) == (True, None)
    assert len(read_registry(registry)) == 1
    registry.write_text(registry.read_text(encoding="utf-8") + "{partial\n", encoding="utf-8")
    assert len(read_registry(registry)) == 1


def test_scheduler_lock_and_historical_hashes_protect_concurrency_and_inputs(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    historical = [
        *sorted((tmp_path / "data/raw").glob("*.csv")),
        *sorted((tmp_path / "data/investor").glob("*.csv")),
        *sorted((tmp_path / "output").glob("signals.csv")),
        *sorted((tmp_path / "output").glob("shadow_signal_records.csv")),
        *sorted((tmp_path / "output").glob("validation_artifact.csv")),
        *sorted((tmp_path / "output").glob("existing_manifest.json")),
    ]
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in historical}
    lock = tmp_path / "output/daily_scheduler.lock"
    lock.write_text("held", encoding="utf-8")
    blocked = run_scheduler_tick(repo_root=tmp_path, now=_at(18, 30), tickers=TICKERS, run_operation=lambda **_: _result())
    assert blocked.action == "DUPLICATE_INVOCATION"
    assert not (tmp_path / "output/daily_scheduler_state.json").exists()
    assert {path: hashlib.sha256(path.read_bytes()).digest() for path in historical} == before

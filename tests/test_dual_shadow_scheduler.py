"""Tests for DUAL Shadow scheduler: STEP 2 readiness/trade-date + STEP 3 lock/state/pipeline."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.daily_run_registry import EVENT_ATTEMPT_COMPLETED, RegistryRecord, append_record, read_registry
from scripts.dual_shadow_scheduler import (
    NON_TRADING_DAY,
    PRODUCTION_NOT_READY,
    READY,
    SCHEDULER_STATUS_COMPLETED,
    SCHEDULER_STATUS_FAILED,
    SCHEDULER_STATUS_SKIPPED_DUPLICATE_RUN,
    DualShadowSchedulerLock,
    DualSchedulerTickResult,
    _exit_code,
    check_dual_readiness,
    determine_trade_date,
    evaluate_production_readiness,
    main as scheduler_main,
    run_dual_shadow_scheduler_tick,
)
from src.dual_shadow_daily_pipeline import STATUS_FAILED, STATUS_SUCCESS, run_dual_shadow_daily_pipeline
from src.dual_shadow_forward_returns import DualForwardReturnStore
from src.dual_shadow_ledger import DualShadowLedgerStore

KST = ZoneInfo("Asia/Seoul")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _record(trade_date: str, status: str, event_id: str) -> RegistryRecord:
    return RegistryRecord(
        registry_version=1,
        event_id=event_id,
        scheduler_date=trade_date,
        target_trade_date=trade_date,
        timezone="Asia/Seoul",
        event_type=EVENT_ATTEMPT_COMPLETED,
        orchestration_status=status,
    )


def test_latest_success_is_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "SUCCESS", "e1"))
    report = evaluate_production_readiness(date(2026, 9, 11), registry_path=registry_path)
    assert report.status == READY


def test_latest_success_with_warning_is_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "SUCCESS_WITH_WARNING", "e1"))
    report = evaluate_production_readiness(date(2026, 9, 11), registry_path=registry_path)
    assert report.status == READY


def test_latest_failed_is_not_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "FAILED", "e1"))
    report = evaluate_production_readiness(date(2026, 9, 11), registry_path=registry_path)
    assert report.status == PRODUCTION_NOT_READY


def test_past_success_then_retry_pending_is_not_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "SUCCESS", "e1"))
    append_record(registry_path, _record("2026-09-11", "RETRY_PENDING", "e2"))
    report = evaluate_production_readiness(date(2026, 9, 11), registry_path=registry_path)
    assert report.status == PRODUCTION_NOT_READY
    assert report.latest_attempt_status == "RETRY_PENDING"


def test_no_records_is_not_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    report = evaluate_production_readiness(date(2026, 9, 11), registry_path=registry_path)
    assert report.status == PRODUCTION_NOT_READY
    assert report.latest_attempt_status is None


def test_non_trading_day_is_not_execution_target(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    # 2026-09-11 is a Friday (trading day); use a Saturday instead.
    report = check_dual_readiness(
        explicit_trade_date="2026-09-12",
        registry_path=registry_path,
        repo_root=Path(__file__).resolve().parents[1],
    )
    assert report.status == NON_TRADING_DAY
    assert report.is_trading_day is False


def test_explicit_trade_date_overrides_today() -> None:
    resolved = determine_trade_date("2026-01-15", now=datetime(2026, 9, 11, tzinfo=KST))
    assert resolved == date(2026, 1, 15)


def test_kst_today_used_when_no_explicit_date() -> None:
    # 09:00 UTC -> 18:00 KST same day; ensures Asia/Seoul conversion, not naive UTC date.
    now_utc = datetime(2026, 9, 11, 9, 0, tzinfo=ZoneInfo("UTC"))
    resolved = determine_trade_date(None, now=now_utc)
    assert resolved == date(2026, 9, 11)


def test_kst_rolls_to_next_day_near_midnight_utc() -> None:
    # 16:00 UTC == 01:00 KST next day.
    now_utc = datetime(2026, 9, 11, 16, 0, tzinfo=ZoneInfo("UTC"))
    resolved = determine_trade_date(None, now=now_utc)
    assert resolved == date(2026, 9, 12)


def test_full_readiness_flow_ready(tmp_path: Path) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "SUCCESS", "e1"))
    report = check_dual_readiness(
        explicit_trade_date="2026-09-11",
        registry_path=registry_path,
        repo_root=Path(__file__).resolve().parents[1],
    )
    assert report.status == READY
    assert report.is_trading_day is True


# --- STEP 3: lock / state / pipeline connection -----------------------------

TICKER = "005930"
TICKERS = {TICKER: "TEST"}


def _price_df(periods: int = 80, start: str | None = "2026-07-01", end: str | None = None) -> pd.DataFrame:
    if end is not None:
        dates = pd.bdate_range(end=end, periods=periods, freq="B")
    else:
        dates = pd.bdate_range(start, periods=periods, freq="B")
    closes = [100.0 + idx for idx in range(periods)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * periods,
        }
    )


def _investor_df(price_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": price_df["date"],
            "foreign_net_buy": [0.0] * len(price_df),
            "institution_net_buy": [0.0] * len(price_df),
        }
    )


@pytest.fixture
def ready_production_registry(tmp_path):
    path = tmp_path / "daily_run_registry.jsonl"
    append_record(path, _record("2026-09-11", "SUCCESS", "prod-e1"))
    return path


@pytest.fixture
def counting_pipeline_stub():
    calls: list[dict] = []

    class _Result:
        status = STATUS_SUCCESS

        def to_dict(self):
            return {"status": self.status}

    def _fn(**kwargs):
        calls.append(kwargs)
        return _Result()

    _fn.calls = calls
    return _fn


def test_ready_calls_pipeline_once(tmp_path, ready_production_registry, counting_pipeline_stub) -> None:
    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert len(counting_pipeline_stub.calls) == 1
    assert result.scheduler_status == SCHEDULER_STATUS_COMPLETED
    assert result.dual_status == STATUS_SUCCESS


def test_production_not_ready_skips_pipeline(tmp_path, counting_pipeline_stub) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    append_record(registry_path, _record("2026-09-11", "FAILED", "prod-e1"))
    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=registry_path,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert len(counting_pipeline_stub.calls) == 0
    assert result.scheduler_status == PRODUCTION_NOT_READY


def test_non_trading_day_skips_pipeline(tmp_path, counting_pipeline_stub) -> None:
    registry_path = tmp_path / "daily_run_registry.jsonl"
    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-12",  # Saturday
        production_registry_path=registry_path,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert len(counting_pipeline_stub.calls) == 0
    assert result.scheduler_status == NON_TRADING_DAY


def test_active_lock_blocks_duplicate_run(tmp_path, ready_production_registry, counting_pipeline_stub) -> None:
    lock_path = tmp_path / "output" / "dual_shadow_scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 999999, "started_at": "now"}), encoding="utf-8")

    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=lock_path,
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert len(counting_pipeline_stub.calls) == 0
    assert result.scheduler_status == SCHEDULER_STATUS_SKIPPED_DUPLICATE_RUN
    assert lock_path.exists()  # the pre-existing (foreign) lock is left untouched


def test_stale_lock_is_recovered_and_run_proceeds(tmp_path, ready_production_registry, counting_pipeline_stub) -> None:
    lock_path = tmp_path / "output" / "dual_shadow_scheduler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 999999, "started_at": "old"}), encoding="utf-8")
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()
    os.utime(lock_path, (old_time, old_time))

    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=lock_path,
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert len(counting_pipeline_stub.calls) == 1
    assert result.scheduler_status == SCHEDULER_STATUS_COMPLETED


def test_normal_completion_removes_lock(tmp_path, ready_production_registry, counting_pipeline_stub) -> None:
    lock_path = tmp_path / "output" / "dual_shadow_scheduler.lock"
    run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=lock_path,
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    assert not lock_path.exists()


def test_pipeline_exception_sets_failed_state_and_releases_lock(tmp_path, ready_production_registry) -> None:
    lock_path = tmp_path / "output" / "dual_shadow_scheduler.lock"
    state_path = tmp_path / "output" / "dual_shadow_scheduler_state.json"

    def _raising_fn(**kwargs):
        raise RuntimeError("boom")

    result = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=lock_path,
        state_path=state_path,
        pipeline_fn=_raising_fn,
    )
    assert result.scheduler_status == SCHEDULER_STATUS_FAILED
    assert result.dual_status == "FAILED"
    assert not lock_path.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["scheduler_status"] == SCHEDULER_STATUS_FAILED
    assert state["error_code"] == "RuntimeError"


def test_no_write_to_production_registry(tmp_path, ready_production_registry, counting_pipeline_stub) -> None:
    before = ready_production_registry.read_text(encoding="utf-8")
    run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=counting_pipeline_stub,
    )
    after = ready_production_registry.read_text(encoding="utf-8")
    assert before == after
    # No stray production-named files created by the DUAL scheduler.
    assert not (tmp_path / "output" / "daily_scheduler.lock").exists()
    assert not (tmp_path / "output" / "daily_scheduler_state.json").exists()


def test_tick_does_not_break_pipeline_own_dedupe(tmp_path, ready_production_registry) -> None:
    """The scheduler must not add its own dedupe layer on top of the pipeline's."""
    ledger = DualShadowLedgerStore(tmp_path / "dual_shadow_signal_ledger.csv")
    forward = DualForwardReturnStore(tmp_path / "dual_shadow_forward_returns.csv")
    frame = _price_df(80, end="2026-09-11")
    pipeline_kwargs = dict(
        tickers=TICKERS,
        price_data={TICKER: frame},
        investor_data={TICKER: _investor_df(frame)},
        ledger_store=ledger,
        forward_store=forward,
        performance_summary_path=tmp_path / "dual_shadow_performance_summary.json",
        run_registry_path=tmp_path / "dual_shadow_run_registry.jsonl",
        repo_root=REPO_ROOT,
    )

    first = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=run_dual_shadow_daily_pipeline,
        pipeline_kwargs=pipeline_kwargs,
    )
    second = run_dual_shadow_scheduler_tick(
        explicit_trade_date="2026-09-11",
        production_registry_path=ready_production_registry,
        repo_root=tmp_path,
        lock_path=tmp_path / "output" / "dual_shadow_scheduler.lock",
        state_path=tmp_path / "output" / "dual_shadow_scheduler_state.json",
        pipeline_fn=run_dual_shadow_daily_pipeline,
        pipeline_kwargs=pipeline_kwargs,
    )

    assert first.pipeline_result.ledger.get("saved", 0) >= 1
    # Second run against the same trade_date/data must be idempotent (pipeline's own dedupe).
    assert second.pipeline_result.ledger.get("duplicate", 0) >= 1
    assert second.pipeline_result.ledger.get("saved", 0) == 0


# --- STEP 4: CLI / exit-code policy -----------------------------------------


def _result(scheduler_status: str, dual_status: str | None) -> DualSchedulerTickResult:
    return DualSchedulerTickResult(
        trade_date="2026-09-11",
        scheduler_status=scheduler_status,
        production_status=READY,
        dual_status=dual_status,
        detail="test",
    )


def test_exit_code_success_is_zero() -> None:
    assert _exit_code(_result(SCHEDULER_STATUS_COMPLETED, STATUS_SUCCESS)) == 0


def test_exit_code_production_not_ready_is_zero() -> None:
    assert _exit_code(_result(PRODUCTION_NOT_READY, None)) == 0


def test_exit_code_non_trading_day_is_zero() -> None:
    assert _exit_code(_result(NON_TRADING_DAY, None)) == 0


def test_exit_code_duplicate_skip_is_zero() -> None:
    assert _exit_code(_result(SCHEDULER_STATUS_SKIPPED_DUPLICATE_RUN, None)) == 0


def test_exit_code_pipeline_failed_status_is_nonzero() -> None:
    # scheduler completed the tick but the pipeline itself reported FAILED (no exception raised).
    assert _exit_code(_result(SCHEDULER_STATUS_COMPLETED, STATUS_FAILED)) != 0


def test_exit_code_unexpected_exception_is_nonzero() -> None:
    assert _exit_code(_result(SCHEDULER_STATUS_FAILED, "FAILED")) != 0


def test_cli_json_output_is_valid_json_and_exit_zero(tmp_path, ready_production_registry, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "scripts.dual_shadow_scheduler.run_dual_shadow_scheduler_tick",
        lambda explicit_trade_date=None: _result(SCHEDULER_STATUS_COMPLETED, STATUS_SUCCESS),
    )
    monkeypatch.setattr("sys.argv", ["dual_shadow_scheduler.py", "--trade-date", "2026-09-11", "--json"])
    exit_code = scheduler_main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # raises if not valid JSON
    assert payload["scheduler_status"] == SCHEDULER_STATUS_COMPLETED
    assert exit_code == 0


def test_cli_unexpected_exception_no_traceback_and_nonzero_exit(monkeypatch, capsys) -> None:
    def _raise(explicit_trade_date=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("scripts.dual_shadow_scheduler.run_dual_shadow_scheduler_tick", _raise)
    monkeypatch.setattr("sys.argv", ["dual_shadow_scheduler.py", "--json"])
    exit_code = scheduler_main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["scheduler_status"] == SCHEDULER_STATUS_FAILED
    assert payload["error_code"] == "RuntimeError"
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert exit_code != 0

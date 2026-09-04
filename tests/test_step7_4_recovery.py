from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from scripts.daily_run_registry import (
    EVENT_ATTEMPT_COMPLETED,
    RegistryRecord,
    _compute_event_id,
    append_record,
    read_registry,
)
from scripts.daily_scheduler import (
    CATEGORY_BLOCKED,
    CATEGORY_FAILED,
    CATEGORY_RETRYABLE,
    PHASE_DASHBOARD_RUNNER,
    PHASE_INPUT_GATE,
    OPERATIONAL_TIMEZONE,
    classify_daily_failure,
    run_scheduler_tick,
)
from scripts.daily_operational_run import DailyOperationalResult, PhaseResult


TICKERS = {"005930": "Samsung", "000660": "Hynix"}
TARGET = date(2026, 9, 4)


def _seed_repo(root: Path) -> None:
    for folder in (root / "data/raw", root / "data/investor", root / "output"):
        folder.mkdir(parents=True)
    for ticker in TICKERS:
        (root / "data/raw" / f"{ticker}.csv").write_text(
            "date,open,high,low,close,volume\n2026-09-03,1,2,1,2,10\n2026-09-04,1,2,1,2,10\n",
            encoding="utf-8",
        )
        (root / "data/investor" / f"{ticker}_investor.csv").write_text(
            f"date,ticker,foreign_net_buy,institution_net_buy\n2026-09-03,{ticker},1,1\n2026-09-04,{ticker},1,1\n",
            encoding="utf-8",
        )


def _failed(phase: str, error_code: str | None, message: str) -> DailyOperationalResult:
    phase_result = PhaseResult(phase, "FAILED", "2026-09-04T09:30:00+00:00", "2026-09-04T09:30:01+00:00", message, error_code=error_code)
    return DailyOperationalResult(
        "run-1", "2026-09-04T09:30:00+00:00", "2026-09-04T09:30:01+00:00", "FAILED", phase,
        None, None, None, False, None, None, None, None, False, [], [message], [phase_result],
    )


def _success() -> DailyOperationalResult:
    return DailyOperationalResult(
        "run-success", "2026-09-04T11:30:00+00:00", "2026-09-04T11:31:00+00:00", "SUCCESS", None,
        "UPDATED", "UPDATED", "PASS", True, "SUCCESS", "2026-09-04", "2026-09-04", 1, False, [], [], [],
    )


def _now() -> datetime:
    return datetime(2026, 9, 4, 18, 30, tzinfo=OPERATIONAL_TIMEZONE)


def test_failure_classification_policy_matrix() -> None:
    assert classify_daily_failure(_failed("MARKET_UPDATE", None, "DATA_NOT_READY"))[0] == CATEGORY_RETRYABLE
    assert classify_daily_failure(_failed("MARKET_UPDATE", "TimeoutError", "timeout"))[0] == CATEGORY_RETRYABLE
    assert classify_daily_failure(_failed("MARKET_UPDATE", "ConnectionError", "connection reset"))[0] == CATEGORY_RETRYABLE
    assert classify_daily_failure(_failed("INPUT_GATE", None, "integrity failed"))[0] == CATEGORY_BLOCKED
    assert classify_daily_failure(_failed("MARKET_UPDATE", None, "SCHEMA corruption"))[0] == CATEGORY_BLOCKED
    assert classify_daily_failure(_failed("MARKET_UPDATE", "TypeError", "bad code"))[0] == CATEGORY_FAILED
    assert classify_daily_failure(_failed("MARKET_UPDATE", None, "unknown structural failure"))[0] == CATEGORY_FAILED


def test_pipeline_retry_is_one_in_attempt_and_exhausts(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    failed = _failed(PHASE_DASHBOARD_RUNNER, "TimeoutError", "temporary pipeline timeout")
    calls = {"count": 0}

    def operation(**_: object) -> DailyOperationalResult:
        calls["count"] += 1
        return failed

    result = run_scheduler_tick(repo_root=tmp_path, now=_now(), tickers=TICKERS, run_operation=operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "RETRY_EXHAUSTED"
    assert result.operator_action_required is True
    assert result.operator_action_code == "MANUAL_RERUN_ALLOWED"
    assert calls["count"] == 2


def test_registry_duplicate_event_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "registry.jsonl"
    record = RegistryRecord(1, _compute_event_id("2026-09-04", 0, 1, "SUCCESS"), "2026-09-04", "2026-09-04", "Asia/Seoul", EVENT_ATTEMPT_COMPLETED, "SUCCESS")
    assert append_record(path, record) == (True, None)
    assert append_record(path, record) == (True, None)
    assert len(read_registry(path)) == 1


def test_malformed_state_with_terminal_registry_does_not_rerun(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    state_path = tmp_path / "output/daily_scheduler_state.json"
    state_path.write_text("{malformed", encoding="utf-8")
    registry_path = tmp_path / "output/daily_run_registry.jsonl"
    record = RegistryRecord(1, "terminal-1", "2026-09-04", "2026-09-04", "Asia/Seoul", EVENT_ATTEMPT_COMPLETED, "SUCCESS", slot=0, attempt=1, daily_status="SUCCESS", finished_at="2026-09-04T18:35:00+09:00")
    append_record(registry_path, record)
    calls = {"count": 0}

    def operation(**_: object) -> DailyOperationalResult:
        calls["count"] += 1
        raise AssertionError("terminal registry history must guard against rerun")

    result = run_scheduler_tick(repo_root=tmp_path, now=_now(), tickers=TICKERS, run_operation=operation)
    assert result.action == "ALREADY_TERMINAL"
    assert result.scheduler_status == "SUCCESS"
    assert calls["count"] == 0
    assert list(tmp_path.glob("output/daily_scheduler_state.corrupt-*.json"))


def test_registry_write_failure_does_not_block_result(tmp_path: Path, monkeypatch) -> None:
    _seed_repo(tmp_path)
    import scripts.daily_scheduler as scheduler

    monkeypatch.setattr(scheduler, "append_record", lambda *_: (False, "disk full"))
    result = run_scheduler_tick(repo_root=tmp_path, now=_now(), tickers=TICKERS, run_operation=lambda **_: _failed(PHASE_INPUT_GATE, None, "integrity"))
    assert result.scheduler_status == "BLOCKED"
    assert any("REGISTRY_APPEND_FAILED" in note for note in result.notes)


def test_controlled_recovery_preserves_attempts_and_history(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    for ticker in TICKERS:
        (tmp_path / "data/raw" / f"{ticker}.csv").write_text(
            "date,open,high,low,close,volume\n2026-09-03,1,2,1,2,10\n", encoding="utf-8"
        )
    operation_results = iter([
        _failed("MARKET_UPDATE", "TimeoutError", "transient source timeout"),
        _failed("MARKET_UPDATE", "ConnectionError", "transient connection error"),
        _success(),
    ])

    def operation(**_: object) -> DailyOperationalResult:
        return next(operation_results)

    first = run_scheduler_tick(repo_root=tmp_path, now=_now(), tickers=TICKERS, run_operation=operation)
    assert first.error_code == "DATA_NOT_READY"
    for ticker in TICKERS:
        (tmp_path / "data/raw" / f"{ticker}.csv").write_text(
            "date,open,high,low,close,volume\n2026-09-03,1,2,1,2,10\n2026-09-04,1,2,1,2,10\n", encoding="utf-8"
        )
    at_1900 = run_scheduler_tick(repo_root=tmp_path, now=_now().replace(hour=19, minute=0), tickers=TICKERS, run_operation=operation)
    assert at_1900.scheduler_status == "RETRY_PENDING"
    (tmp_path / "output/daily_scheduler_state.json").unlink()  # restart between slots
    at_1930 = run_scheduler_tick(repo_root=tmp_path, now=_now().replace(hour=19, minute=30), tickers=TICKERS, run_operation=operation)
    assert at_1930.scheduler_status == "RETRY_PENDING"
    at_2000 = run_scheduler_tick(repo_root=tmp_path, now=_now().replace(hour=20, minute=0), tickers=TICKERS, run_operation=operation)
    assert at_2000.scheduler_status == "SUCCESS"
    assert at_2000.attempt == 4
    assert len(read_registry(tmp_path / "output/daily_run_registry.jsonl")) == 4
    assert len({record.event_id for record in read_registry(tmp_path / "output/daily_run_registry.jsonl")}) == 4
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.daily_operational_run import (
    PHASE_DASHBOARD_RUNNER,
    PHASE_INPUT_GATE,
    PHASE_INVESTOR_UPDATE,
    PHASE_MARKET_UPDATE,
    DailyOperationalResult,
    DailyOperationDependencies,
    DailyRunLock,
    PhaseResult,
)
from scripts.daily_operational_run import run_daily_operation as real_run_daily_operation
from scripts.input_integrity_gate import RECOVERABLE_SOURCE_LAG, run_input_integrity_gate
from scripts.safe_investor_update import (
    ERROR_CODE_INVESTOR_VALIDATION, HistoricalMutationError, SafeInvestorUpdater,
)
from scripts.safe_market_update import SafeMarketUpdater
from scripts.daily_scheduler import (
    OPERATIONAL_TIMEZONE as SEOUL,
    SchedulerLock,
    SchedulerState,
    classify_daily_failure,
    probe_data_readiness,
    run_scheduler_tick,
    main as scheduler_main,
)

TICKERS = {"005930": "삼성전자", "000660": "SK하이닉스"}
TRADING_DAY = date(2026, 9, 4)  # Friday
HOLIDAY = date(2026, 9, 25)  # 추석 (embedded holiday)
SATURDAY = date(2026, 9, 5)


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SEOUL)


def _write_market_csv(path: Path, latest: str) -> None:
    path.write_text(
        "date,open,high,low,close,volume\n"
        "2026-09-02,100,110,100,105,1000\n"
        f"{latest},105,115,105,112,1200\n",
        encoding="utf-8",
    )


def _write_investor_csv(path: Path, ticker: str, latest: str) -> None:
    path.write_text(
        "date,ticker,foreign_net_buy,institution_net_buy\n"
        f"2026-09-02,{ticker},100,-50\n"
        f"{latest},{ticker},200,-80\n",
        encoding="utf-8",
    )


def _make_repo(tmp_path: Path, market_latest: str = "2026-09-04", investor_latest: str = "2026-09-04") -> Path:
    for sub in ("data/raw", "data/investor", "output"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    for ticker in TICKERS:
        _write_market_csv(tmp_path / "data" / "raw" / f"{ticker}.csv", market_latest)
        _write_investor_csv(tmp_path / "data" / "investor" / f"{ticker}_investor.csv", ticker, investor_latest)
    return tmp_path


def _daily_result(
    overall: str = "SUCCESS",
    failed_phase: str | None = None,
    errors: tuple[str, ...] = (),
    phase_error_code: str | None = None,
    phase_message: str = "",
    market_status: str = "UPDATED",
    investor_status: str = "UPDATED",
    market_latest_date: str = "2026-09-04",
    investor_latest_date: str = "2026-09-04",
) -> DailyOperationalResult:
    phases: list[PhaseResult] = []
    if failed_phase:
        phases.append(
            PhaseResult(
                failed_phase,
                "FAILED",
                "2026-09-04T09:30:00+00:00",
                "2026-09-04T09:30:01+00:00",
                phase_message or "phase failed",
                error_code=phase_error_code,
            )
        )
    return DailyOperationalResult(
        "run-test-1",
        "2026-09-04T09:30:00+00:00",
        "2026-09-04T09:30:05+00:00",
        overall,
        failed_phase,
        market_status,
        investor_status,
        "PASS",
        True,
        "SUCCESS" if failed_phase is None else None,
        market_latest_date,
        investor_latest_date,
        2,
        False,
        [],
        list(errors),
        phases,
    )


class FakeOperation:
    """run_daily_operation 대체 callable. 결과 queue를 순서대로 반환한다."""

    def __init__(self, *results):
        self._results = list(results) or [_daily_result()]
        self.calls = 0

    def __call__(self, repo_root, *, target_trade_date):
        self.calls += 1
        result = self._results[min(self.calls - 1, len(self._results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _tick(repo: Path, now: datetime, operation: FakeOperation | None = None, **kwargs) -> object:
    return run_scheduler_tick(
        repo_root=repo,
        now=now,
        tickers=TICKERS,
        run_operation=operation or FakeOperation(),
        **kwargs,
    )


def _read_state(repo: Path) -> dict:
    return json.loads((repo / "output" / "daily_scheduler_state.json").read_text(encoding="utf-8"))


# --- 1. Asia/Seoul timezone ---


def test_aware_utc_now_is_converted_to_seoul(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    # 2026-09-04 09:30 UTC == 2026-09-04 18:30 Asia/Seoul -> first slot due
    result = _tick(repo, datetime(2026, 9, 4, 9, 30, tzinfo=timezone.utc), operation)
    assert result.action == "EXECUTED"
    assert result.scheduler_status == "SUCCESS"
    assert operation.calls == 1


def test_naive_now_is_interpreted_as_seoul(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, datetime(2026, 9, 4, 18, 30), operation)  # naive -> Asia/Seoul
    assert result.action == "EXECUTED"
    assert operation.calls == 1
    # UTC 09:29 == KST 18:29 -> 아직 first slot 전
    waiting = _tick(_make_repo(tmp_path / "b"), datetime(2026, 9, 4, 9, 29, tzinfo=timezone.utc), FakeOperation())
    assert waiting.action == "WAITING_FIRST_SLOT"


# --- 2. 정상 거래일 18:30 first run ---


def test_first_run_at_1830_success(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.action == "EXECUTED"
    assert result.scheduler_status == "SUCCESS"
    assert result.attempt == 1
    assert operation.calls == 1
    state = _read_state(repo)
    assert state["current_status"] == "SUCCESS"
    assert state["target_trade_date"] == "2026-09-04"
    assert state["timezone"] == "Asia/Seoul"
    assert state["first_scheduled_at"] == "2026-09-04T18:30:00+09:00"
    assert state["last_run_id"] == "run-test-1"
    assert state["last_daily_status"] == "SUCCESS"
    assert state["last_successful_trade_date"] == "2026-09-04"
    assert state["operator_action_required"] is False


def test_before_first_slot_does_nothing_and_writes_no_state(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 0), operation)
    assert result.action == "WAITING_FIRST_SLOT"
    assert operation.calls == 0
    assert not (repo / "output" / "daily_scheduler_state.json").exists()


# --- 3/4/5/6. retry schedule and limit ---


def test_retry_schedule_1830_1900_1930_2000_then_exhausted(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")  # target 미도착
    # updater가 실제로 매 attempt 실행되지만, source가 여전히 target date를 제공하지 않는다.
    stale = _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03")
    operation = FakeOperation(stale)

    first = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert first.scheduler_status == "RETRY_PENDING"
    assert first.next_retry_at == "2026-09-04T19:00:00+09:00"
    assert first.error_code == "DATA_NOT_READY"
    assert operation.calls == 1  # due attempt마다 run_operation은 항상 호출된다

    second = _tick(repo, _at(TRADING_DAY, 19, 0), operation)
    assert second.attempt == 2
    assert second.next_retry_at == "2026-09-04T19:30:00+09:00"
    assert operation.calls == 2

    third = _tick(repo, _at(TRADING_DAY, 19, 30), operation)
    assert third.attempt == 3
    assert third.next_retry_at == "2026-09-04T20:00:00+09:00"
    assert operation.calls == 3

    fourth = _tick(repo, _at(TRADING_DAY, 20, 0), operation)
    assert fourth.attempt == 4
    assert fourth.scheduler_status == "RETRY_PENDING"
    assert fourth.next_retry_at == "2026-09-04T22:00:00+09:00"
    assert fourth.error_code == "DATA_NOT_READY"
    assert operation.calls == 4

    fifth = _tick(repo, _at(TRADING_DAY, 22, 0), operation)
    assert fifth.attempt == 5  # first + retry 4회 = 최대 5회 (22:00이 final)
    assert fifth.scheduler_status == "FAILED"
    assert fifth.error_code == "RETRY_EXHAUSTED"
    assert fifth.next_retry_at is None
    assert fifth.operator_action_required is True
    assert operation.calls == 5

    # retry 소진 후에는 terminal 상태이므로 더 실행하지 않는다
    after = _tick(repo, _at(TRADING_DAY, 22, 10), operation)
    assert after.action == "ALREADY_TERMINAL"
    assert operation.calls == 5


def test_retry_success_on_second_attempt(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    operation = FakeOperation(
        _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03"),
        _daily_result(),
    )
    first = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert first.scheduler_status == "RETRY_PENDING"
    # 데이터가 도착한 뒤 19:00 retry
    _make_repo(repo, market_latest="2026-09-04", investor_latest="2026-09-04")
    second = _tick(repo, _at(TRADING_DAY, 19, 0), operation)
    assert second.scheduler_status == "SUCCESS"
    assert second.attempt == 2
    assert operation.calls == 2


def test_same_slot_is_not_executed_twice(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    operation = FakeOperation(_daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03"))
    _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    waiting = _tick(repo, _at(TRADING_DAY, 18, 45), operation)
    assert waiting.action == "WAITING_RETRY_SLOT"
    assert waiting.attempt == 1
    assert _read_state(repo)["attempt"] == 1


# --- 7. SUCCESS 이후 duplicate scheduler invocation 방지 ---


def test_success_then_duplicate_invocation_does_not_rerun(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    assert _tick(repo, _at(TRADING_DAY, 18, 30), operation).scheduler_status == "SUCCESS"
    duplicate = _tick(repo, _at(TRADING_DAY, 19, 0), operation)
    assert duplicate.action == "ALREADY_TERMINAL"
    assert duplicate.scheduler_status == "SUCCESS"
    assert operation.calls == 1  # orchestrator는 1회만 호출됐다


# --- 8. SUCCESS_WITH_WARNING ---


def test_success_with_warning_is_terminal_success_with_warning(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(_daily_result(overall="SUCCESS_WITH_WARNING", investor_status="SOURCE_LAG"))
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "SUCCESS_WITH_WARNING"
    assert result.operator_action_required is False
    state = _read_state(repo)
    assert state["last_successful_trade_date"] == "2026-09-04"
    assert _tick(repo, _at(TRADING_DAY, 19, 30), operation).action == "ALREADY_TERMINAL"


# --- 9. Integrity FAIL → BLOCKED ---


def test_integrity_gate_fail_is_blocked_without_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_INPUT_GATE,
            errors=("MARKET_INVESTOR_MISALIGNED: investor date is ahead",),
            phase_message="MARKET_INVESTOR_MISALIGNED: investor date is ahead",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "INTEGRITY_GATE_FAIL"
    assert result.failed_phase == PHASE_INPUT_GATE
    assert result.operator_action_required is True
    assert result.next_retry_at is None
    # BLOCKED는 terminal: 이후 slot에서도 자동 재실행하지 않는다
    assert _tick(repo, _at(TRADING_DAY, 19, 0), operation).action == "ALREADY_TERMINAL"
    assert operation.calls == 1


# --- 10. transient error → RETRY_PENDING ---


def test_transient_error_is_retry_pending(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="ConnectionError",
            phase_message="ConnectionError: connection reset by peer",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "RETRY_PENDING"
    assert result.error_code == "TRANSIENT_FAILURE"
    assert result.next_retry_at == "2026-09-04T19:00:00+09:00"
    assert result.operator_action_required is False


# --- 11. non-retryable error → FAILED ---


def test_programming_error_is_failed_without_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="TypeError",
            phase_message="TypeError: unsupported operand type(s)",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "PROGRAMMING_ERROR"
    assert result.operator_action_required is True


def test_unclassified_failure_is_failed_without_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(overall="FAILED", failed_phase=PHASE_MARKET_UPDATE, phase_message="something unknown happened")
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "UNCLASSIFIED_FAILURE"


def test_structural_failure_is_blocked_without_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_message="FUTURE_DATE: market file contains future date 2026-09-05",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "STRUCTURAL_FAILURE"
    assert result.operator_action_required is True


# --- 12/13/14. NON_TRADING_DAY ---


def test_official_holiday_is_non_trading_day(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(HOLIDAY, 18, 30), operation)  # 2026-09-25 추석
    assert result.action == "RECORDED_NON_TRADING_DAY"
    assert result.scheduler_status == "NON_TRADING_DAY"
    assert operation.calls == 0
    state = _read_state(repo)
    assert state["current_status"] == "NON_TRADING_DAY"
    assert state["attempt"] == 0
    assert state["operator_action_required"] is False


def test_weekend_is_non_trading_day(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(SATURDAY, 18, 30), operation)
    assert result.scheduler_status == "NON_TRADING_DAY"
    assert operation.calls == 0


def test_override_holiday_makes_friday_non_trading(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = run_scheduler_tick(
        repo_root=repo,
        now=_at(TRADING_DAY, 18, 30),
        holidays=frozenset({date(2026, 9, 4)}),
        tickers=TICKERS,
        run_operation=operation,
    )
    assert result.scheduler_status == "NON_TRADING_DAY"
    assert operation.calls == 0


def test_non_trading_day_is_terminal_and_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    _tick(repo, _at(HOLIDAY, 18, 30), operation)
    again = _tick(repo, _at(HOLIDAY, 19, 30), operation)
    assert again.action == "ALREADY_TERMINAL"
    assert again.scheduler_status == "NON_TRADING_DAY"
    assert operation.calls == 0


# --- 15. NO_NEW_DATA but target ready → retry 안 함 ---


def test_no_new_data_with_target_ready_is_success_without_retry(tmp_path):
    repo = _make_repo(tmp_path)  # latest == target(2026-09-04)
    operation = FakeOperation(_daily_result(overall="SUCCESS", market_status="NO_NEW_DATA", investor_status="NO_NEW_DATA"))
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "SUCCESS"
    assert result.next_retry_at is None
    assert operation.calls == 1


# --- 16. target data missing → retry ---


def test_target_data_missing_is_retry_pending_after_running_orchestrator(tmp_path):
    # local data가 target보다 오래되어도 run_operation은 항상 호출된다(§4 STALE LOCAL DATA != BLOCK).
    # source가 updater 실행 후에도 target date를 제공하지 못하면 post-run 판단으로 RETRY_PENDING이 된다.
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    operation = FakeOperation(_daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03"))
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "RETRY_PENDING"
    assert result.error_code == "DATA_NOT_READY"
    assert "post-update latest dates below target 2026-09-04" in result.error_message
    assert "market=2026-09-03" in result.error_message
    assert operation.calls == 1  # run_operation이 실제로 호출되었다


def test_investor_uniform_lag_is_retry_pending(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-04", investor_latest="2026-09-03")
    operation = FakeOperation(_daily_result(investor_latest_date="2026-09-03"))
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "RETRY_PENDING"
    assert "investor=2026-09-03" in result.error_message
    assert operation.calls == 1


def test_partial_market_mismatch_is_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    # ticker별 market latest date 불일치 → structural → BLOCKED
    _write_market_csv(repo / "data" / "raw" / "000660.csv", "2026-09-03")
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "MARKET_PARTIAL_MISMATCH"
    assert result.operator_action_required is True
    assert operation.calls == 0


def test_partial_investor_mismatch_is_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    # ticker별 investor latest date 불일치 → structural → BLOCKED (run_operation 미호출)
    _write_investor_csv(repo / "data" / "investor" / "000660_investor.csv", "000660", "2026-09-03")
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "INVESTOR_PARTIAL_MISMATCH"
    assert result.operator_action_required is True
    assert operation.calls == 0


def test_future_date_detected_is_blocked_without_calling_orchestrator(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-05", investor_latest="2026-09-05")
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "FUTURE_DATE_DETECTED"
    assert result.operator_action_required is True
    assert operation.calls == 0


def test_missing_csv_is_structural_blocked(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "data" / "investor" / "000660_investor.csv").unlink()
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "READINESS_STRUCTURAL"
    assert operation.calls == 0


# --- 17/18. state persistence + restart recovery ---


def test_state_persistence_roundtrip(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    stale = _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03")
    _tick(repo, _at(TRADING_DAY, 18, 30), FakeOperation(stale))
    state_path = repo / "output" / "daily_scheduler_state.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    for key in (
        "target_trade_date", "scheduler_date", "timezone", "current_status", "attempt",
        "first_scheduled_at", "last_attempt_at", "next_retry_at", "completed_at",
        "last_run_id", "last_daily_status", "failed_phase", "error_code", "error_message",
        "operator_action_required", "updated_at",
    ):
        assert key in payload, key
    restored = SchedulerState.from_dict(payload)
    assert restored.current_status == "RETRY_PENDING"
    assert restored.attempt == 1
    assert restored.next_retry_at == "2026-09-04T19:00:00+09:00"


def test_restart_recovery_continues_from_persisted_state(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    stale = _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03")
    first = _tick(repo, _at(TRADING_DAY, 18, 30), FakeOperation(stale))
    assert first.scheduler_status == "RETRY_PENDING"
    # 프로세스 재시작(= 새로운 호출) 후 데이터 도착 상태에서 19:00 slot 재개
    _make_repo(repo, market_latest="2026-09-04", investor_latest="2026-09-04")
    operation = FakeOperation()
    resumed = _tick(repo, _at(TRADING_DAY, 19, 1), operation)
    assert resumed.action == "EXECUTED"
    assert resumed.attempt == 2
    assert resumed.scheduler_status == "SUCCESS"
    assert operation.calls == 1


def test_last_successful_fields_carry_over_to_next_day(tmp_path):
    repo = _make_repo(tmp_path)
    _tick(repo, _at(TRADING_DAY, 18, 30), FakeOperation())
    # 다음 거래일(2026-09-07 월요일): state rollover, 성공 이력만 유지
    monday = date(2026, 9, 7)
    _make_repo(repo, market_latest="2026-09-07", investor_latest="2026-09-07")
    result = _tick(
        repo, _at(monday, 18, 30),
        FakeOperation(_daily_result(market_latest_date="2026-09-07", investor_latest_date="2026-09-07")),
    )
    assert result.scheduler_status == "SUCCESS"
    state = _read_state(repo)
    assert state["target_trade_date"] == "2026-09-07"
    assert state["attempt"] == 1


# --- 19. missed run ---


def test_missed_first_slot_runs_once_at_current_slot(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    operation = FakeOperation(_daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03"))
    # 18:30~19:00 다운, 19:10에 기동 → 현재 유효 slot(19:00)으로 1회만 실행
    result = _tick(repo, _at(TRADING_DAY, 19, 10), operation)
    assert result.action == "EXECUTED"
    assert result.attempt == 1
    assert result.scheduler_status == "RETRY_PENDING"
    # 과거 slot을 연속 재실행하지 않고 다음 slot은 19:30
    assert result.next_retry_at == "2026-09-04T19:30:00+09:00"


def test_missed_run_within_grace_executes_final_slot(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 22, 10), operation)
    assert result.action == "EXECUTED"
    assert result.scheduler_status == "SUCCESS"
    assert result.attempt == 1


def test_missed_run_after_window_is_failed_and_preserved(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 22, 40), operation)
    assert result.action == "WINDOW_EXPIRED"
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "MISSED_RUN_WINDOW_EXPIRED"
    assert result.operator_action_required is True
    assert operation.calls == 0
    # 다음 날에도 자동 성공 처리되지 않고 실패 상태가 보존된다
    state = _read_state(repo)
    assert state["current_status"] == "FAILED"
    assert state["attempt"] == 0


def test_retry_window_expiry_finalizes_failed(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    stale = _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03")
    _tick(repo, _at(TRADING_DAY, 22, 0), FakeOperation(stale))  # final slot -> FAILED (RETRY_EXHAUSTED)
    # terminal FAILED 이후 window 지난 호출은 상태를 바꾸지 않는다
    late = _tick(repo, _at(TRADING_DAY, 22, 30), FakeOperation())
    assert late.action == "ALREADY_TERMINAL"


def test_pending_state_after_window_becomes_failed(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-03", investor_latest="2026-09-03")
    stale = _daily_result(market_latest_date="2026-09-03", investor_latest_date="2026-09-03")
    first = _tick(repo, _at(TRADING_DAY, 18, 30), FakeOperation(stale))
    assert first.scheduler_status == "RETRY_PENDING"
    # PC가 꺼져 있다가 window(22:30) 이후 재기동
    late = _tick(repo, _at(TRADING_DAY, 22, 40), FakeOperation())
    assert late.action == "WINDOW_EXPIRED"
    assert late.scheduler_status == "FAILED"
    assert late.error_code == "RETRY_WINDOW_EXPIRED"
    assert late.operator_action_required is True


# --- 20. concurrent scheduler invocation ---


def test_concurrent_scheduler_invocation_is_rejected(tmp_path):
    repo = _make_repo(tmp_path)
    lock = SchedulerLock(repo / "output" / "daily_scheduler.lock")
    assert lock.acquire() is True
    try:
        operation = FakeOperation()
        result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
        assert result.action == "DUPLICATE_INVOCATION"
        assert result.scheduler_status is None
        assert operation.calls == 0
        assert not (repo / "output" / "daily_scheduler_state.json").exists()
    finally:
        lock.release()


def test_stale_scheduler_lock_is_removed(tmp_path):
    repo = _make_repo(tmp_path)
    import os

    lock_path = repo / "output" / "daily_scheduler.lock"
    lock_path.write_text("{}", encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    os.utime(lock_path, (old.timestamp(), old.timestamp()))
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.action == "EXECUTED"
    assert operation.calls == 1


# --- 21/23. existing daily lock / manual rerun interaction ---


def _stub_daily_dependencies():
    from dataclasses import dataclass

    @dataclass
    class StubResult:
        status: str
        published_latest_date: str | None = "2026-09-04"
        pipeline_allowed: bool = True

        def to_dict(self):
            return {"status": self.status}

    @dataclass
    class StubRunner:
        metadata: dict

    return DailyOperationDependencies(
        lambda: StubResult("NO_NEW_DATA"),
        lambda: StubResult("NO_NEW_DATA"),
        lambda: StubResult("PASS"),
        lambda: StubRunner({"pipeline_status": "SUCCESS", "record_count": 0}),
    )


def test_manual_daily_run_lock_makes_scheduler_retry(tmp_path):
    repo = _make_repo(tmp_path)

    def real_operation(repo_root, *, target_trade_date):
        return real_run_daily_operation(repo_root=repo_root, dependencies=_stub_daily_dependencies(),
                                        target_trade_date=target_trade_date)

    # 운영자 수동 rerun이 daily run lock을 잡고 있는 상황
    daily_lock = DailyRunLock(repo / "output" / "daily_operational_run.lock")
    assert daily_lock.acquire("manual-run") is True
    try:
        result = run_scheduler_tick(repo_root=repo, now=_at(TRADING_DAY, 18, 30), tickers=TICKERS, run_operation=real_operation)
        assert result.scheduler_status == "RETRY_PENDING"
        assert result.error_code == "CONCURRENT_RUN"
        assert result.operator_action_required is False
    finally:
        daily_lock.release()

    # lock 해제 후 다음 slot에서 정상 실행된다
    resumed = run_scheduler_tick(repo_root=repo, now=_at(TRADING_DAY, 19, 0), tickers=TICKERS, run_operation=real_operation)
    assert resumed.scheduler_status == "SUCCESS"


# --- 22. historical immutability ---


def test_scheduler_does_not_mutate_data_files(tmp_path):
    repo = _make_repo(tmp_path)
    data_files = sorted((repo / "data").rglob("*.csv"))
    before = {path: path.read_bytes() for path in data_files}
    operation = FakeOperation(
        _daily_result(overall="FAILED", failed_phase=PHASE_MARKET_UPDATE, phase_error_code="ConnectionError", phase_message="ConnectionError"),
        _daily_result(),
    )
    _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    _tick(repo, _at(TRADING_DAY, 19, 0), operation)
    assert operation.calls == 2
    after = {path: path.read_bytes() for path in data_files}
    assert before == after


# --- 24. malformed scheduler state ---


def test_malformed_state_is_backed_up_and_recovered(tmp_path):
    repo = _make_repo(tmp_path)
    state_path = repo / "output" / "daily_scheduler_state.json"
    state_path.write_text("{not valid json", encoding="utf-8")
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.action == "EXECUTED"
    assert result.scheduler_status == "SUCCESS"
    assert any("SCHEDULER_STATE_CORRUPT" in note for note in result.notes)
    backups = list((repo / "output").glob("daily_scheduler_state.corrupt-*.json"))
    assert len(backups) == 1  # 손상 파일은 삭제가 아니라 백업 보존
    assert json.loads(state_path.read_text(encoding="utf-8"))["current_status"] == "SUCCESS"


def test_state_with_unknown_status_is_recovered(tmp_path):
    repo = _make_repo(tmp_path)
    state_path = repo / "output" / "daily_scheduler_state.json"
    state_path.write_text(
        json.dumps({"target_trade_date": "2026-09-04", "current_status": "WEIRD", "attempt": 3}), encoding="utf-8"
    )
    operation = FakeOperation()
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "SUCCESS"
    assert _read_state(repo)["attempt"] == 1  # 잘못된 attempt를 이어받지 않는다


# --- pipeline transient retry (정책 §7.2: 자동 재시도 1회) ---


def test_pipeline_transient_failure_retries_once_only(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_DASHBOARD_RUNNER,
            phase_error_code="TimeoutError",
            phase_message="TimeoutError: temporary file access conflict",
        )
    )
    first = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert first.scheduler_status == "FAILED"
    assert first.error_code == "RETRY_EXHAUSTED"
    assert _read_state(repo)["pipeline_retried"] is True

    # pipeline 자동 재시도는 동일 scheduler attempt 안에서 1회뿐이다.
    second = _tick(repo, _at(TRADING_DAY, 19, 0), operation)
    assert second.action == "ALREADY_TERMINAL"
    assert operation.calls == 2


def test_pipeline_transient_then_success_on_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_DASHBOARD_RUNNER,
            phase_error_code="TimeoutError",
            phase_message="TimeoutError: temporary resource busy",
        ),
        _daily_result(),
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "SUCCESS"
    assert result.attempt == 1
    assert _read_state(repo)["pipeline_retried"] is True
    assert operation.calls == 2


# --- orchestrator 자체 비정상 예외 ---


def test_orchestrator_raising_unexpectedly_is_failed(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(RuntimeError("orchestrator blew up"))
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "SCHEDULER_INTERNAL_ERROR"
    assert result.operator_action_required is True


# --- readiness probe / failure classification 단위 테스트 ---


def test_probe_ready_when_all_tickers_at_target(tmp_path):
    repo = _make_repo(tmp_path)
    report = probe_data_readiness(repo, TRADING_DAY, TICKERS)
    assert report.ready is True
    assert report.market_latest_date == "2026-09-04"
    assert report.investor_latest_date == "2026-09-04"


def test_probe_blocks_future_date(tmp_path):
    repo = _make_repo(tmp_path, market_latest="2026-09-05")
    report = probe_data_readiness(repo, TRADING_DAY, TICKERS)
    assert report.ready is False
    assert report.error_code == "FUTURE_DATE_DETECTED"


def test_classify_gate_fail_is_blocked():
    category, code, _ = classify_daily_failure(
        _daily_result(overall="FAILED", failed_phase=PHASE_INPUT_GATE, phase_message="gate error")
    )
    assert (category, code) == ("BLOCKED", "INTEGRITY_GATE_FAIL")


def test_classify_concurrent_run_is_retryable():
    category, code, _ = classify_daily_failure(
        _daily_result(overall="FAILED", failed_phase="PRECHECK", errors=("CONCURRENT_RUN: another daily operation is active",))
    )
    assert (category, code) == ("RETRYABLE", "CONCURRENT_RUN")


# --- STEP 7-10D: structured historical mutation error propagation ---


def test_classify_historical_mutation_is_blocked_via_structured_code():
    category, code, _ = classify_daily_failure(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="HISTORICAL_MUTATION_DETECTED",
            phase_message="005930: historical mutation detected (foreign_net_buy)",
        )
    )
    assert (category, code) == ("BLOCKED", "HISTORICAL_MUTATION_DETECTED")


def test_scheduler_receives_historical_mutation_not_unclassified(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="HISTORICAL_MUTATION_DETECTED",
            phase_message="005930: historical mutation detected (foreign_net_buy)",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "HISTORICAL_MUTATION_DETECTED"
    assert result.error_code != "UNCLASSIFIED_FAILURE"


def test_historical_mutation_operator_action_is_do_not_rerun(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="HISTORICAL_MUTATION_DETECTED",
            phase_message="005930: historical mutation detected (foreign_net_buy)",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.operator_action_required is True
    assert result.operator_action_code == "DO_NOT_RERUN"


def test_historical_mutation_no_automatic_retry(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(
            overall="FAILED",
            failed_phase=PHASE_MARKET_UPDATE,
            phase_error_code="HISTORICAL_MUTATION_DETECTED",
            phase_message="005930: historical mutation detected (foreign_net_buy)",
        )
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert operation.calls == 1  # 자동 retry 없음 — 다음 slot에서 재호출되지 않음


def test_unrelated_unknown_failure_still_unclassified(tmp_path):
    repo = _make_repo(tmp_path)
    operation = FakeOperation(
        _daily_result(overall="FAILED", failed_phase=PHASE_MARKET_UPDATE, phase_message="something unknown happened")
    )
    result = _tick(repo, _at(TRADING_DAY, 18, 30), operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "UNCLASSIFIED_FAILURE"





def test_cli_json_non_trading_day_exit_zero(tmp_path, capsys):
    _make_repo(tmp_path)
    exit_code = scheduler_main(["--json", "--now", "2026-09-25T18:30:00+09:00"], repo_root=tmp_path)
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scheduler_status"] == "NON_TRADING_DAY"
    assert payload["action"] == "RECORDED_NON_TRADING_DAY"


def test_cli_exit_one_on_blocked(tmp_path, capsys):
    # tmp repo에는 CSV가 없으므로 readiness structural → BLOCKED (orchestrator 미호출)
    for sub in ("data/raw", "data/investor", "output"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    exit_code = scheduler_main(["--json", "--now", "2026-09-04T18:30:00+09:00"], repo_root=tmp_path)
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["scheduler_status"] == "BLOCKED"
    assert payload["error_code"] == "READINESS_STRUCTURAL"


def test_cli_exit_one_on_missed_window(tmp_path, capsys):
    _make_repo(tmp_path)
    exit_code = scheduler_main(["--json", "--now", "2026-09-04T22:40:00+09:00"], repo_root=tmp_path)
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_code"] == "MISSED_RUN_WINDOW_EXPIRED"


LAG_DAY = date(2026, 10, 6)


def _lag_operation(tmp_path, monkeypatch, lag_days=4):
    import scripts.daily_operational_run as daily_module

    raw_dir = tmp_path / "data" / "raw"
    investor_dir = tmp_path / "data" / "investor"
    for directory in (raw_dir, investor_dir, tmp_path / "output"):
        directory.mkdir(parents=True, exist_ok=True)
    lag_date = (LAG_DAY - timedelta(days=lag_days)).isoformat()
    calls = []
    source_state = {"recovered": False}
    results = []
    for ticker in TICKERS:
        (raw_dir / f"{ticker}.csv").write_text(
            "date,open,high,low,close,volume\n2026-10-06,100,110,95,105,1000\n", encoding="utf-8",
        )
        (investor_dir / f"{ticker}_investor.csv").write_text(
            f"date,ticker,foreign_net_buy,institution_net_buy\n{lag_date},{ticker},100,-50\n", encoding="utf-8",
        )

    class MarketSource:
        def fetch(self, ticker, start, end):
            return pd.read_csv(raw_dir / f"{ticker}.csv")

    class InvestorSource:
        def fetch(self, ticker, start, end):
            frame = pd.read_csv(investor_dir / f"{ticker}_investor.csv", dtype={"ticker": str})
            if source_state["recovered"] and frame["date"].max() < end:
                frame = pd.concat([frame, pd.DataFrame([{
                    "date": end, "ticker": ticker, "foreign_net_buy": 200, "institution_net_buy": -80,
                }])], ignore_index=True)
            return frame

    def dependencies(repo_root, allow_source_lag, *, target_trade_date):
        assert repo_root == tmp_path
        assert target_trade_date == LAG_DAY.isoformat()

        def market():
            calls.append("market")
            return SafeMarketUpdater(TICKERS, raw_dir, MarketSource(), today=LAG_DAY).run()

        def investor():
            calls.append("investor")
            return SafeInvestorUpdater(TICKERS, investor_dir, raw_dir, InvestorSource(), today=LAG_DAY).run()

        def gate():
            calls.append("gate")
            return run_input_integrity_gate(
                raw_dir, investor_dir, TICKERS, allow_source_lag=allow_source_lag,
                target_trade_date=target_trade_date,
            )

        def runner():
            calls.append("runner")
            return SimpleNamespace(metadata={"pipeline_status": "SUCCESS", "record_count": 0})

        return DailyOperationDependencies(market, investor, gate, runner)

    monkeypatch.setattr(daily_module, "default_dependencies", dependencies)

    def operation(**kwargs):
        result = real_run_daily_operation(**kwargs)
        results.append(result)
        return result

    return operation, calls, source_state, results


@pytest.mark.parametrize("lag_days", [1, 2, 3, 4])
def test_scheduled_lag_recovers_on_next_slot_with_real_gate_and_updaters(tmp_path, monkeypatch, lag_days):
    operation, calls, source, results = _lag_operation(tmp_path, monkeypatch, lag_days)
    first = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert first.scheduler_status == "RETRY_PENDING"
    assert first.error_code == RECOVERABLE_SOURCE_LAG
    assert first.next_retry_at == "2026-10-06T19:00:00+09:00"
    assert first.operator_action_required is False
    assert results[0].gate_status == "FAIL"
    assert results[0].pipeline_allowed is False
    assert results[0].phases[-1].error_code == RECOVERABLE_SOURCE_LAG
    assert calls == ["market", "investor", "gate"]
    waiting = _tick(tmp_path, _at(LAG_DAY, 18, 45), operation)
    assert waiting.action == "WAITING_RETRY_SLOT"
    assert len(results) == 1
    source["recovered"] = True
    second = _tick(tmp_path, _at(LAG_DAY, 19, 0), operation)
    assert second.scheduler_status == "SUCCESS"
    assert second.attempt == 2
    assert results[-1].gate_status == "PASS"
    assert calls == ["market", "investor", "gate", "market", "investor", "gate", "runner"]
    assert _tick(tmp_path, _at(LAG_DAY, 19, 30), operation).action == "ALREADY_TERMINAL"
    assert calls.count("runner") == 1


def test_recoverable_lag_exhausts_existing_slots_without_dashboard(tmp_path, monkeypatch):
    operation, calls, _, results = _lag_operation(tmp_path, monkeypatch)
    for hour, minute in ((18, 30), (19, 0), (19, 30), (20, 0), (22, 0)):
        result = _tick(tmp_path, _at(LAG_DAY, hour, minute), operation)
        assert result.scheduler_status == ("FAILED" if hour == 22 else "RETRY_PENDING")
    assert result.error_code == "RETRY_EXHAUSTED"
    assert RECOVERABLE_SOURCE_LAG in result.error_message
    assert result.next_retry_at is None
    assert result.operator_action_code == "MANUAL_RERUN_ALLOWED"
    assert len(results) == 5
    assert calls.count("runner") == 0
    assert _tick(tmp_path, _at(LAG_DAY, 22, 10), operation).action == "ALREADY_TERMINAL"
    assert len(results) == 5


def test_recoverable_lag_pending_recovers_from_registry(tmp_path, monkeypatch):
    operation, calls, source, _ = _lag_operation(tmp_path, monkeypatch)
    assert _tick(tmp_path, _at(LAG_DAY, 18, 30), operation).scheduler_status == "RETRY_PENDING"
    (tmp_path / "output" / "daily_scheduler_state.json").unlink()
    source["recovered"] = True
    result = _tick(tmp_path, _at(LAG_DAY, 19, 0), operation)
    assert result.scheduler_status == "SUCCESS"
    assert result.attempt == 2
    assert "SCHEDULER_STATE_RECOVERED_FROM_REGISTRY" in result.notes
    assert calls.count("runner") == 1


def test_historical_mutation_in_updater_remains_blocked(tmp_path, monkeypatch):
    operation, calls, _, results = _lag_operation(tmp_path, monkeypatch)

    def mutation(*args):
        raise HistoricalMutationError("historical mutation detected")

    monkeypatch.setattr(SafeInvestorUpdater, "_assert_existing_preserved", staticmethod(mutation))
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "HISTORICAL_MUTATION_DETECTED"
    assert results[0].investor_update_status == "FAILED"
    assert calls == ["market", "investor"]


@pytest.mark.parametrize("problem", ["future", "ahead", "mixed", "numeric", "missing", "empty", "schema", "duplicate", "order"])
def test_scheduled_structural_input_remains_blocked(tmp_path, monkeypatch, problem):
    operation, calls, _, results = _lag_operation(tmp_path, monkeypatch)
    ticker = next(iter(TICKERS))
    path = tmp_path / "data" / "investor" / f"{ticker}_investor.csv"
    frame = pd.read_csv(path, dtype={"ticker": str})
    if problem == "missing":
        path.unlink()
    elif problem == "empty":
        path.write_text("", encoding="utf-8")
    elif problem == "schema":
        frame.drop(columns=["foreign_net_buy"]).to_csv(path, index=False)
    elif problem == "numeric":
        frame.loc[0, "foreign_net_buy"] = None
        frame.to_csv(path, index=False)
    elif problem == "duplicate":
        pd.concat([frame, frame]).to_csv(path, index=False)
    elif problem == "order":
        second = frame.copy()
        second["date"] = "2026-10-01"
        pd.concat([frame, second]).to_csv(path, index=False)
    else:
        frame["date"] = "2026-10-01" if problem == "mixed" else "2026-10-07"
        frame.to_csv(path, index=False)
        if problem == "ahead":
            for code in TICKERS:
                other = frame.copy()
                other["ticker"] = code
                other.to_csv(tmp_path / "data" / "investor" / f"{code}_investor.csv", index=False)
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.next_retry_at is None
    assert calls.count("runner") == 0
    if problem in {"numeric", "schema", "duplicate"}:
        assert result.error_code == "STRUCTURAL_FAILURE"
        assert result.operator_action_code == "CHECK_INPUT_DATA"
        assert calls == ["market", "investor"]
        assert results[0].phases[-1].error_code == ERROR_CODE_INVESTOR_VALIDATION


@pytest.mark.parametrize("problem", [
    "no_code", "unknown_code", "gate_status", "allowed", "market_date", "investor_date",
    "missing_phase", "market_failure", "investor_failure", "mutation", "fetch_missing",
    "fetch_failed", "fetch_count", "failure_map", "source_date", "source_lag_type",
    "target_date", "published_date", "gap_days", "universe_count", "gate_count",
])
def test_recoverable_code_requires_consistent_updater_evidence(tmp_path, monkeypatch, problem):
    operation, calls, _, _ = _lag_operation(tmp_path, monkeypatch)
    daily = deepcopy(operation(repo_root=tmp_path, target_trade_date=LAG_DAY.isoformat()))
    market, investor, gate = daily.phases[1:]
    if problem == "no_code":
        gate.error_code = None
    elif problem == "unknown_code":
        gate.error_code = "UNKNOWN"
    elif problem == "gate_status":
        gate.status = "PASS"
    elif problem == "allowed":
        daily.pipeline_allowed = True
    elif problem == "market_date":
        daily.market_latest_date = "2026-10-05"
    elif problem == "investor_date":
        daily.investor_latest_date = "2026-10-07"
    elif problem == "missing_phase":
        daily.phases.remove(investor)
    elif problem == "market_failure":
        market.status = "FAILED"
    elif problem == "investor_failure":
        investor.status = "FAILED"
    elif problem == "mutation":
        investor.error_code = "HISTORICAL_MUTATION_DETECTED"
    elif problem == "fetch_missing":
        investor.metrics.pop("fetch_success_count")
    elif problem == "fetch_failed":
        investor.metrics["fetch_failed_count"] = 1
    elif problem == "fetch_count":
        investor.metrics["fetch_success_count"] = 1
    elif problem == "failure_map":
        investor.metrics["failed_tickers"] = {"005930": "failure"}
    elif problem == "source_date":
        investor.metrics["source_latest_date"] = "2026-10-06"
    elif problem == "source_lag_type":
        investor.metrics["source_lag_type"] = "PARTIAL"
    elif problem == "target_date":
        investor.metrics["market_target_date"] = "2026-10-05"
    elif problem == "published_date":
        investor.metrics["published_investor_latest_date"] = "2026-10-01"
    elif problem == "gap_days":
        investor.metrics["gap_days"] = 0
    elif problem == "universe_count":
        investor.metrics["ticker_count"] = 1
    elif problem == "gate_count":
        gate.metrics["expected_ticker_count"] = 1
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), FakeOperation(daily))
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "INTEGRITY_GATE_FAIL"
    assert result.next_retry_at is None
    assert calls.count("runner") == 0


@pytest.mark.parametrize("code", [
    None, "UNKNOWN", "TEST_SOURCE_FAILURE", "SOURCE_TIMEOUT", "SOURCE_CONNECTION_ERROR",
    "INVESTOR_SOURCE_TIMEOUT", "INVESTOR_CONNECTION_FAILURE", "UNKNOWN_TIMEOUT",
    "TimeoutError", "ConnectionError",
])
def test_investor_unknown_and_source_codes_do_not_gain_retry(tmp_path, monkeypatch, code):
    import scripts.daily_operational_run as daily_module

    operation, calls, _, _ = _lag_operation(tmp_path, monkeypatch)
    dependencies_factory = daily_module.default_dependencies

    def factory(*args, **kwargs):
        dependencies = dependencies_factory(*args, **kwargs)

        def investor():
            result = dependencies.investor_update()
            result.status = "FAILED"
            result.error_code = code
            result.failures = {"005930": "source failure"}
            result.fetch_failed_count = 1
            return result

        return DailyOperationDependencies(
            dependencies.market_update, investor, dependencies.input_gate, dependencies.dashboard_runner,
        )

    monkeypatch.setattr(daily_module, "default_dependencies", factory)
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert result.scheduler_status == "FAILED"
    assert result.error_code == "UNCLASSIFIED_FAILURE"
    assert result.next_retry_at is None
    assert calls == ["market", "investor"]


@pytest.mark.parametrize("exception", [
    TimeoutError("request timed out"),
    ConnectionError("connection reset"),
    RuntimeError("temporary network failure"),
])
def test_investor_raised_transient_exception_retains_legacy_retry(tmp_path, monkeypatch, exception):
    import scripts.daily_operational_run as daily_module

    operation, calls, _, results = _lag_operation(tmp_path, monkeypatch)
    dependencies_factory = daily_module.default_dependencies

    def factory(*args, **kwargs):
        dependencies = dependencies_factory(*args, **kwargs)

        def investor():
            calls.append("investor")
            raise exception

        return DailyOperationDependencies(
            dependencies.market_update, investor, dependencies.input_gate, dependencies.dashboard_runner,
        )

    monkeypatch.setattr(daily_module, "default_dependencies", factory)
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert result.scheduler_status == "RETRY_PENDING"
    assert result.error_code == "TRANSIENT_FAILURE"
    assert result.next_retry_at == "2026-10-06T19:00:00+09:00"
    assert calls == ["market", "investor"]
    phase = results[0].phases[-1]
    assert phase.error_code == type(exception).__name__
    assert phase.metrics == {}


@pytest.mark.parametrize("metric_code", ["SOURCE_TIMEOUT", None, "CONTRADICTORY"])
def test_returned_investor_code_cannot_use_transient_message_or_contradictory_metrics(metric_code):
    daily = _daily_result(
        overall="FAILED", failed_phase=PHASE_INVESTOR_UPDATE,
        phase_error_code="SOURCE_TIMEOUT",
        phase_message="CONCURRENT_RUN: temporary connection timeout",
    )
    daily.phases[-1].metrics["error_code"] = metric_code
    category, code, _ = classify_daily_failure(daily)
    assert (category, code) == ("FAILED", "UNCLASSIFIED_FAILURE")


def test_investor_validation_with_source_failure_is_blocked(tmp_path, monkeypatch):
    import scripts.daily_operational_run as daily_module

    operation, calls, _, _ = _lag_operation(tmp_path, monkeypatch)
    dependencies_factory = daily_module.default_dependencies

    def factory(*args, **kwargs):
        dependencies = dependencies_factory(*args, **kwargs)

        def investor():
            result = dependencies.investor_update()
            result.status = "FAILED"
            result.error_code = ERROR_CODE_INVESTOR_VALIDATION
            result.failures = {"005930": "validation failure", "000660": "source failure"}
            result.fetch_failed_count = 2
            return result

        return DailyOperationDependencies(
            dependencies.market_update, investor, dependencies.input_gate, dependencies.dashboard_runner,
        )

    monkeypatch.setattr(daily_module, "default_dependencies", factory)
    result = _tick(tmp_path, _at(LAG_DAY, 18, 30), operation)
    assert result.scheduler_status == "BLOCKED"
    assert result.error_code == "STRUCTURAL_FAILURE"
    assert calls == ["market", "investor"]

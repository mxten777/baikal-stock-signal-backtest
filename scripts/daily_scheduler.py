"""STEP 7-2 Scheduler / Operations Layer for the BAIKAL daily operation.

이 모듈은 STEP 6 Daily Operational Orchestrator(scripts/daily_operational_run.py)
를 수정하지 않고 그 상위에 자동 실행 계층을 추가한다.

책임 (scheduler):
    1) Asia/Seoul 현재 시각 확인
    2) 한국 증시 거래일 여부 판단 (scripts/korean_market_calendar.py)
    3) target trade date 결정 (Asia/Seoul 기준 "오늘")
    4) 실행 시각/attempt 판단 (18:30 / 19:00 / 19:30 / 20:00 KST)
    5) data readiness 판단 (read-only probe)
    6) STEP 6 Daily Orchestrator 호출 (in-process callable)
    7) retry 상태 저장
    8) 최종 orchestration status 저장
    9) duplicate scheduler invocation 방지
    10) restart recovery 지원

비책임 (절대 수행하지 않음):
    Signal / Shadow / Validation 로직 호출 및 수정, 기존 output schema 변경,
    historical data mutation. 모든 데이터 갱신은 기존 STEP 6 orchestrator의
    staging/validation/atomic publish 경로만 통해 이루어진다.

운영 명령:
    python -m scripts.daily_scheduler --json

스케줄 (Asia/Seoul, 정책 DAILY_OPERATIONS_RUN_POLICY_V1):
    18:30 first attempt / 19:00 retry 1 / 19:30 retry 2 / 20:00 retry 3
    마지막 slot 이후 30분의 grace 이후에는 자동 실행하지 않고 missed 상태를
    FAILED + operator_action_required 로 보존한다.

테스트/운영 검증을 위한 --now ISO datetime 주입은 scheduler 판단 clock만
바꾸며, STEP 6 manifest의 UTC timestamp 저장 방식은 그대로 유지된다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields as dataclass_fields
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

import pandas as pd

from scripts.daily_operational_run import (
    PHASE_DASHBOARD_RUNNER,
    PHASE_INPUT_GATE,
    STATUS_SUCCESS as DAILY_STATUS_SUCCESS,
    STATUS_SUCCESS_WITH_WARNING as DAILY_STATUS_SUCCESS_WITH_WARNING,
    DailyOperationalResult,
    run_daily_operation,
)
from scripts.daily_run_registry import (
    EVENT_ATTEMPT_COMPLETED,
    EVENT_MANUAL_RUN_COMPLETED,
    EVENT_MISSED_RUN,
    EVENT_NON_TRADING_DAY,
    EVENT_RETRY_SCHEDULED,
    RegistryRecord,
    _compute_event_id,
    _now_iso,
    append_record,
    get_runs_for_trade_date,
    read_registry,
)
from scripts.input_integrity_gate import get_default_tickers
from scripts.korean_market_calendar import is_trading_day, load_holidays

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_SOURCE = "output/daily_scheduler_state.json"
REGISTRY_SOURCE = "output/daily_run_registry.jsonl"
LOCK_SOURCE = "output/daily_scheduler.lock"
# Tick 내부에서 실제 orchestrator 실행이 끝날 때까지 lock을 잡는다. 비정상
# 종료 시 다음 retry slot 전에 stale 판정되어야 하므로 12시간(daily lock)보다
# 훨씬 짧은 15분을 사용한다. 동시성 안전의 최종 보루는 기존 daily run lock이다.
LOCK_STALE_AFTER = timedelta(minutes=15)

# STEP 7 orchestration status (기존 STEP 6 status와 구분되는 scheduler layer 상태)
STATUS_SUCCESS = "SUCCESS"
STATUS_SUCCESS_WITH_WARNING = "SUCCESS_WITH_WARNING"
STATUS_RETRY_PENDING = "RETRY_PENDING"
STATUS_BLOCKED = "BLOCKED"
STATUS_FAILED = "FAILED"
STATUS_NON_TRADING_DAY = "NON_TRADING_DAY"
ALL_STATUSES = frozenset(
    {STATUS_SUCCESS, STATUS_SUCCESS_WITH_WARNING, STATUS_RETRY_PENDING, STATUS_BLOCKED, STATUS_FAILED, STATUS_NON_TRADING_DAY}
)
TERMINAL_STATUSES = frozenset(
    {STATUS_SUCCESS, STATUS_SUCCESS_WITH_WARNING, STATUS_BLOCKED, STATUS_FAILED, STATUS_NON_TRADING_DAY}
)

# Asia/Seoul 자동 실행 시각. attempt 0 = first run, 1..3 = retry (최대 retry 3회).
SCHEDULE_SLOTS: tuple[time, ...] = (time(18, 30), time(19, 0), time(19, 30), time(20, 0))
LAST_SLOT_GRACE = timedelta(minutes=30)
MAX_ATTEMPTS = len(SCHEDULE_SLOTS)

# 실패 분류 (heuristic, 정책 §7). 분류 불가는 retry 금지(FAILED)가 안전 기본값이다.
CATEGORY_RETRYABLE = "RETRYABLE"
 # Public classifier values remain the STEP 7-3 status names.
CATEGORY_BLOCKING = "BLOCKED"
CATEGORY_FATAL = "FAILED"
# Backward-compatible names for callers of the STEP 7-3 helper.
CATEGORY_BLOCKED = CATEGORY_BLOCKING
CATEGORY_FAILED = CATEGORY_FATAL

def _state_from_registry(records: list[RegistryRecord], target_date: str) -> SchedulerState | None:
    """Recover only a known scheduler outcome; never turn history into success."""
    relevant = [record for record in records if record.target_trade_date == target_date]
    if not relevant:
        return None
    record = relevant[-1]
    if record.orchestration_status not in ALL_STATUSES:
        return None
    return SchedulerState(
        target_trade_date=target_date,
        scheduler_date=record.scheduler_date or target_date,
        current_status=record.orchestration_status,
        attempt=record.attempt or 0,
        first_scheduled_at=record.started_at,
        last_attempt_at=record.finished_at,
        next_retry_at=record.next_retry_at,
        completed_at=record.finished_at if record.orchestration_status in TERMINAL_STATUSES else None,
        last_run_id=record.last_run_id,
        last_daily_status=record.daily_status,
        latest_market_date=record.latest_market_date,
        latest_investor_date=record.latest_investor_date,
        failed_phase=record.failed_phase,
        error_code=record.error_code,
        error_message=record.error_message,
        operator_action_required=record.operator_action_required,
        operator_action_code=getattr(record, "operator_action_code", None),
    )


def _state_from_manifest(repo_root: Path, target_date: str) -> SchedulerState | None:
    """Use a completed STEP 6 manifest as a crash guard after state was lost."""
    path = repo_root / "output" / "daily_operational_run.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("overall_status") not in {DAILY_STATUS_SUCCESS, DAILY_STATUS_SUCCESS_WITH_WARNING}:
        return None
    started = _parse_dt(payload.get("started_at"))
    if started is None or started.astimezone(OPERATIONAL_TIMEZONE).date().isoformat() != target_date:
        return None
    status = STATUS_SUCCESS if payload["overall_status"] == DAILY_STATUS_SUCCESS else STATUS_SUCCESS_WITH_WARNING
    return SchedulerState(
        target_trade_date=target_date, scheduler_date=target_date, current_status=status,
        attempt=1, first_scheduled_at=_iso(started.astimezone(OPERATIONAL_TIMEZONE)),
        last_attempt_at=payload.get("finished_at"), completed_at=payload.get("finished_at"),
        last_run_id=payload.get("run_id"), last_daily_status=payload.get("overall_status"),
        latest_market_date=payload.get("market_latest_date"), latest_investor_date=payload.get("investor_latest_date"),
        last_successful_run_at=payload.get("finished_at"), last_successful_trade_date=target_date,
    )


def _reconcile_manual_manifest(repo_root: Path, state: SchedulerState, records: list[RegistryRecord]) -> bool:
    manual = next((record for record in reversed(records) if record.target_trade_date == state.target_trade_date and record.event_type == EVENT_MANUAL_RUN_COMPLETED), None)
    manifest = _state_from_manifest(repo_root, state.target_trade_date)
    if manual is None or manifest is None or manual.last_run_id != manifest.last_run_id:
        return False
    state.current_status = manifest.current_status
    state.last_run_id = manifest.last_run_id
    state.last_daily_status = manifest.last_daily_status
    state.last_attempt_at = manifest.last_attempt_at
    state.completed_at = manifest.completed_at
    state.latest_market_date = manifest.latest_market_date
    state.latest_investor_date = manifest.latest_investor_date
    state.last_successful_run_at = manifest.last_successful_run_at
    state.last_successful_trade_date = manifest.last_successful_trade_date
    state.failed_phase = None
    state.error_code = None
    state.error_message = None
    state.operator_action_required = False
    state.operator_action_code = None
    return True
_STRUCTURAL_MARKERS = (
    "SCHEMA",
    "DUPLICATE",
    "FUTURE_DATE",
    "MUTATION",
    "MISMATCH",
    "PARTIAL",
    "COVERAGE",
    "MALFORMED",
    "CORRUPT",
    "IMMUTABLE",
    "DATE_ORDER",
    "INVALID",
    "EMPTY_FILE",
    "FILE_MISSING",
    "HISTORICAL",
)
_TRANSIENT_MARKERS = (
    "DATA_NOT_READY",
    "SOURCE_LAG",
    "UNEXPECTED EMPTY SOURCE RESPONSE",
    "TIMEOUT",
    "TIMED OUT",
    "CONNECTION",
    "NETWORK",
    "TEMPORAR",
)
_TRANSIENT_HTTP = re.compile(r"\b(408|429|500|502|503|504)\b")
_PROGRAMMING_ERRORS = frozenset(
    {"TypeError", "ImportError", "ModuleNotFoundError", "AttributeError", "KeyError", "NameError", "IndexError"}
)


def _operational_timezone():
    """Asia/Seoul via stdlib; fixed UTC+09:00 fallback (동일 패턴: daily_health_report)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Seoul")
        except Exception:  # tz database missing (e.g. Windows without tzdata)
            pass
    return timezone(timedelta(hours=9), name="Asia/Seoul")


OPERATIONAL_TIMEZONE = _operational_timezone()
TIMEZONE_NAME = "Asia/Seoul"


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _to_seoul(now: datetime | None) -> datetime:
    """Naive 입력은 Asia/Seoul로 간주하고, aware 입력은 Asia/Seoul로 변환한다."""
    if now is None:
        return datetime.now(OPERATIONAL_TIMEZONE)
    if now.tzinfo is None:
        return now.replace(tzinfo=OPERATIONAL_TIMEZONE)
    return now.astimezone(OPERATIONAL_TIMEZONE)


def _slot_datetime(day: date, index: int) -> datetime:
    return datetime.combine(day, SCHEDULE_SLOTS[index], tzinfo=OPERATIONAL_TIMEZONE)


def _due_slot_index(now_seoul: datetime) -> int | None:
    """현재 시각 기준 이미 도래한 가장 늦은 slot index. 아직 18:30 전이면 None."""
    due: int | None = None
    for index in range(len(SCHEDULE_SLOTS)):
        if now_seoul >= _slot_datetime(now_seoul.date(), index):
            due = index
    return due


@dataclass
class SchedulerState:
    """output/daily_scheduler_state.json 에 atomic write되는 scheduler 전용 상태.

    기존 STEP 6 manifest(output/daily_operational_run.json) schema는 변경하지 않는다.
    """

    target_trade_date: str
    scheduler_date: str
    current_status: str
    attempt: int = 0
    first_scheduled_at: str | None = None
    last_attempt_at: str | None = None
    next_retry_at: str | None = None
    completed_at: str | None = None
    last_run_id: str | None = None
    last_daily_status: str | None = None
    latest_market_date: str | None = None
    latest_investor_date: str | None = None
    failed_phase: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    pipeline_retried: bool = False
    operator_action_required: bool = False
    operator_action_code: str | None = None
    last_successful_run_at: str | None = None
    last_successful_trade_date: str | None = None
    timezone: str = TIMEZONE_NAME
    version: int = 1
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "SchedulerState":
        if not isinstance(payload, dict):
            raise ValueError("scheduler state is not a JSON object")
        target = payload.get("target_trade_date")
        if not isinstance(target, str) or not target:
            raise ValueError("scheduler state is missing target_trade_date")
        date.fromisoformat(target)  # malformed date -> ValueError
        status = payload.get("current_status")
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown scheduler status: {status!r}")
        attempt = payload.get("attempt", 0)
        if not isinstance(attempt, int) or isinstance(attempt, bool):
            raise ValueError("scheduler state has invalid attempt")
        known = {f.name for f in dataclass_fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known})


@dataclass
class SchedulerTickResult:
    """한 번의 scheduler invocation 결과 (CLI/테스트 출력용)."""

    action: str
    target_trade_date: str
    timezone: str = TIMEZONE_NAME
    scheduler_status: str | None = None
    attempt: int = 0
    next_retry_at: str | None = None
    last_attempt_at: str | None = None
    last_run_id: str | None = None
    last_daily_status: str | None = None
    failed_phase: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    operator_action_required: bool = False
    operator_action_code: str | None = None
    notes: list[str] = field(default_factory=list)
    state_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SchedulerLock:
    """Scheduler 중복 invocation 방지 lock (daily run lock과 별개).

    프로세스 비정상 종료 시 stale timeout(기본 15분) 이후 자동 해제된다.
    데이터 안전의 최종 보루는 기존 output/daily_operational_run.lock 이며,
    이 lock은 scheduler tick 자체의 중복 실행만 막는다.
    """

    def __init__(self, lock_path: Path, stale_after: timedelta = LOCK_STALE_AFTER) -> None:
        self.lock_path = Path(lock_path)
        self.stale_after = stale_after
        self.acquired = False

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_lock()
        try:
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, handle)
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
        if datetime.now(timezone.utc) - modified_at > self.stale_after:
            self.lock_path.unlink(missing_ok=True)


@dataclass
class ReadinessReport:
    ready: bool
    market_latest_date: str | None
    investor_latest_date: str | None
    error_code: str | None  # 설정되면 structural → 자동 retry 금지(BLOCKED)
    detail: str


def _last_csv_date(path: Path, label: str, ticker: str, problems: list[str]) -> str | None:
    if not path.exists():
        problems.append(f"{label} file missing for ticker {ticker}")
        return None
    try:
        frame = pd.read_csv(path, usecols=["date"])
    except Exception as exc:
        problems.append(f"{label} file unreadable for ticker {ticker}: {type(exc).__name__}: {exc}")
        return None
    if frame.empty:
        problems.append(f"{label} file has 0 rows for ticker {ticker}")
        return None
    latest = str(frame["date"].iloc[-1])
    try:
        date.fromisoformat(latest)
    except ValueError:
        problems.append(f"{label} file has invalid latest date {latest!r} for ticker {ticker}")
        return None
    return latest


def probe_data_readiness(repo_root: Path, target_trade_date: date, tickers: dict[str, str]) -> ReadinessReport:
    """Target trade date 데이터 준비도 read-only 판단.

    NO_NEW_DATA 와 구분하기 위해 updater status가 아니라 실제 CSV의 latest
    date를 target trade date와 비교한다. CSV는 읽기만 하며 절대 수정하지 않는다.
    """
    raw_dir = Path(repo_root) / "data" / "raw"
    investor_dir = Path(repo_root) / "data" / "investor"
    problems: list[str] = []
    market_latest: dict[str, str] = {}
    investor_latest: dict[str, str] = {}
    for ticker in tickers:
        market_value = _last_csv_date(raw_dir / f"{ticker}.csv", "market", ticker, problems)
        if market_value is not None:
            market_latest[ticker] = market_value
        investor_value = _last_csv_date(investor_dir / f"{ticker}_investor.csv", "investor", ticker, problems)
        if investor_value is not None:
            investor_latest[ticker] = investor_value

    if problems:
        return ReadinessReport(False, None, None, "READINESS_STRUCTURAL", "; ".join(problems))
    if not market_latest or not investor_latest:
        return ReadinessReport(False, None, None, "READINESS_STRUCTURAL", "no ticker data could be probed")

    target = target_trade_date.isoformat()
    if len(set(market_latest.values())) > 1:
        detail = ", ".join(f"{ticker}={value}" for ticker, value in sorted(market_latest.items()))
        return ReadinessReport(False, None, None, "MARKET_PARTIAL_MISMATCH", f"market latest dates differ across tickers: {detail}")
    if len(set(investor_latest.values())) > 1:
        detail = ", ".join(f"{ticker}={value}" for ticker, value in sorted(investor_latest.items()))
        return ReadinessReport(False, None, None, "INVESTOR_PARTIAL_MISMATCH", f"investor latest dates differ across tickers: {detail}")

    market = next(iter(market_latest.values()))
    investor = next(iter(investor_latest.values()))
    if market > target or investor > target:
        return ReadinessReport(
            False, market, investor, "FUTURE_DATE_DETECTED",
            f"latest date beyond target {target}: market={market}, investor={investor}",
        )
    if market == target and investor == target:
        return ReadinessReport(True, market, investor, None, "target trade date data ready")

    lagging: list[str] = []
    if market < target:
        lagging.append(f"market latest {market} < target {target}")
    if investor < target:
        lagging.append(f"investor latest {investor} < target {target}")
    return ReadinessReport(False, market, investor, None, "; ".join(lagging))


def classify_daily_failure(result: DailyOperationalResult) -> tuple[str, str, str]:
    """STEP 6 실패 결과를 scheduler 분류(RETRYABLE/BLOCKED/FAILED)로 매핑한다.

    - Integrity Gate FAIL       → BLOCKED (자동 retry 금지, 정책 §9)
    - CONCURRENT_RUN            → RETRYABLE (manual run / 다른 run과의 일시 충돌)
    - schema/duplicate/future date/partial mismatch/historical mutation 계열 → BLOCKED
    - programming error 계열    → FAILED (자동 retry 무의미)
    - 명백한 transient 계열     → RETRYABLE
    - 분류 불가                 → FAILED (안전 기본값, 자동 retry 금지)
    """
    failed = next((phase for phase in result.phases if phase.name == result.failed_phase), None)
    exc_name = failed.error_code if failed else None
    content = " ".join([*result.errors, failed.message if failed else ""]).upper()
    message = (failed.message if failed else "") or "; ".join(result.errors) or "daily operation failed"

    if result.failed_phase == PHASE_INPUT_GATE:
        return CATEGORY_BLOCKING, "INTEGRITY_GATE_FAIL", message
    if "CONCURRENT_RUN" in content:
        return CATEGORY_RETRYABLE, "CONCURRENT_RUN", message
    if any(marker in content for marker in _STRUCTURAL_MARKERS):
        return CATEGORY_BLOCKING, "STRUCTURAL_FAILURE", message
    if exc_name in _PROGRAMMING_ERRORS:
        return CATEGORY_FATAL, "PROGRAMMING_ERROR", message
    if (
        (exc_name is not None and any(token in exc_name.lower() for token in ("timeout", "connection")))
        or any(marker in content for marker in _TRANSIENT_MARKERS)
        or _TRANSIENT_HTTP.search(content)
    ):
        code = "PIPELINE_TRANSIENT_FAILURE" if result.failed_phase == PHASE_DASHBOARD_RUNNER else "TRANSIENT_FAILURE"
        return CATEGORY_RETRYABLE, code, message
    return CATEGORY_FATAL, "UNCLASSIFIED_FAILURE", message


def _operator_action_code(category: str, error_code: str | None) -> str | None:
    if category == CATEGORY_RETRYABLE:
        return None
    if error_code == "INTEGRITY_GATE_FAIL":
        return "CHECK_INTEGRITY"
    if error_code in {"READINESS_STRUCTURAL", "MARKET_PARTIAL_MISMATCH", "INVESTOR_PARTIAL_MISMATCH", "FUTURE_DATE_DETECTED", "STRUCTURAL_FAILURE"}:
        return "CHECK_INPUT_DATA"
    if error_code in {"PROGRAMMING_ERROR", "SCHEDULER_INTERNAL_ERROR"}:
        return "CHECK_APPLICATION_ERROR"
    if error_code in {"MISSED_RUN_WINDOW_EXPIRED", "RETRY_WINDOW_EXPIRED", "RETRY_EXHAUSTED"}:
        return "MANUAL_RERUN_ALLOWED"
    return "DO_NOT_RERUN" if category == CATEGORY_FATAL else None


def _load_state(state_path: Path) -> tuple[SchedulerState | None, str | None]:
    """상태 파일을 읽는다. 손상된 파일은 백업 후 fresh 상태로 복구한다."""
    if not state_path.exists():
        return None, None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        return SchedulerState.from_dict(payload), None
    except (OSError, ValueError, TypeError) as exc:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = state_path.with_name(f"{state_path.stem}.corrupt-{stamp}{state_path.suffix}")
        try:
            os.replace(state_path, backup)
        except OSError:
            pass
        return None, f"SCHEDULER_STATE_CORRUPT: {exc}; previous file backed up to {backup.name}"


def _write_state(state_path: Path, state: SchedulerState, now_seoul: datetime, notes: list[str]) -> None:
    state.updated_at = _iso(now_seoul)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{state_path.name}.", suffix=".tmp", dir=str(state_path.parent))
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, state_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
    except OSError as exc:
        notes.append(f"SCHEDULER_STATE_WRITE_FAILED: {type(exc).__name__}: {exc}")


def _append_to_registry(
    registry_path: Path,
    state: SchedulerState,
    now_seoul: datetime,
    notes: list[str],
) -> None:
    """Append state to run registry (non-blocking write).
    
    Registry write failure is logged but doesn't prevent scheduler execution.
    """
    # Determine event type based on status
    event_type: str
    if state.current_status == STATUS_NON_TRADING_DAY:
        event_type = EVENT_NON_TRADING_DAY
    elif state.current_status == STATUS_FAILED and state.error_code == "MISSED_RUN_WINDOW_EXPIRED":
        event_type = EVENT_MISSED_RUN
    elif state.current_status == STATUS_RETRY_PENDING:
        event_type = EVENT_RETRY_SCHEDULED
    else:
        event_type = EVENT_ATTEMPT_COMPLETED
    
    # Compute deterministic event_id for idempotency
    event_id = _compute_event_id(
        state.target_trade_date,
        state.attempt - 1 if state.attempt > 0 else None,  # slot index = attempt - 1
        state.attempt,
        state.current_status,
    )
    
    # Create registry record
    record = RegistryRecord(
        registry_version=1,
        event_id=event_id,
        scheduler_date=state.scheduler_date,
        target_trade_date=state.target_trade_date,
        timezone=state.timezone,
        slot=state.attempt - 1 if state.attempt > 0 else None,
        attempt=state.attempt if state.attempt > 0 else None,
        event_type=event_type,
        orchestration_status=state.current_status,
        daily_status=state.last_daily_status,
        started_at=state.first_scheduled_at if state.attempt == 1 else state.last_attempt_at,
        finished_at=state.last_attempt_at,
        next_retry_at=state.next_retry_at if state.current_status == STATUS_RETRY_PENDING else None,
        last_run_id=state.last_run_id,
        failed_phase=state.failed_phase,
        error_code=state.error_code,
        error_message=state.error_message,
        operator_action_required=state.operator_action_required,
        operator_action_code=state.operator_action_code,
        latest_market_date=state.latest_market_date,
        latest_investor_date=state.latest_investor_date,
        created_at=_now_iso(),
    )
    
    success, error = append_record(registry_path, record)
    if not success and error:
        notes.append(f"REGISTRY_APPEND_FAILED: {error}")


def _finalize(state: SchedulerState, status: str, now_seoul: datetime, *, error_code: str | None = None,
              error_message: str | None = None, operator: bool = False,
              operator_action_code: str | None = None) -> None:
    state.current_status = status
    state.completed_at = _iso(now_seoul)
    state.next_retry_at = None
    state.error_code = error_code
    state.error_message = error_message
    state.operator_action_required = operator
    state.operator_action_code = operator_action_code or (
        _operator_action_code(CATEGORY_BLOCKING if status == STATUS_BLOCKED else CATEGORY_FATAL, error_code)
        if operator else None
    )


def _pending(state: SchedulerState, now_seoul: datetime, due_index: int, error_code: str, error_message: str) -> None:
    state.current_status = STATUS_RETRY_PENDING
    state.next_retry_at = _iso(_slot_datetime(now_seoul.date(), due_index + 1))
    state.completed_at = None
    state.error_code = error_code
    state.error_message = error_message
    state.operator_action_required = False
    state.operator_action_code = None


def _result_from_state(action: str, state: SchedulerState, notes: list[str], state_path: Path) -> SchedulerTickResult:
    return SchedulerTickResult(
        action=action,
        target_trade_date=state.target_trade_date,
        scheduler_status=state.current_status,
        attempt=state.attempt,
        next_retry_at=state.next_retry_at,
        last_attempt_at=state.last_attempt_at,
        last_run_id=state.last_run_id,
        last_daily_status=state.last_daily_status,
        failed_phase=state.failed_phase,
        error_code=state.error_code,
        error_message=state.error_message,
        operator_action_required=state.operator_action_required,
        operator_action_code=state.operator_action_code,
        notes=list(notes),
        state_path=str(state_path),
    )


def run_scheduler_tick(
    *,
    repo_root: Path = ROOT_DIR,
    now: datetime | None = None,
    holidays: frozenset[date] | None = None,
    tickers: dict[str, str] | None = None,
    run_operation: Callable[..., DailyOperationalResult] = run_daily_operation,
    state_path: Path | None = None,
    registry_path: Path | None = None,
    lock_path: Path | None = None,
) -> SchedulerTickResult:
    """Scheduler 1회 tick. 매 호출은 독립적이며 상태는 state 파일로만 이어진다.

    now: naive이면 Asia/Seoul로 간주, aware이면 Asia/Seoul로 변환한다.
    run_operation: STEP 6 orchestrator callable (기본 run_daily_operation).
    """
    repo_root = Path(repo_root)
    now_seoul = _to_seoul(now)
    today = now_seoul.date()
    today_iso = today.isoformat()
    state_path = Path(state_path) if state_path is not None else repo_root / STATE_SOURCE
    registry_path = Path(registry_path) if registry_path is not None else repo_root / REGISTRY_SOURCE
    lock_path = Path(lock_path) if lock_path is not None else repo_root / LOCK_SOURCE
    notes: list[str] = []

    if holidays is None:
        holidays, holiday_warning = load_holidays(repo_root)
        if holiday_warning:
            notes.append(holiday_warning)

    lock = SchedulerLock(lock_path)
    if not lock.acquire():
        return SchedulerTickResult(
            action="DUPLICATE_INVOCATION",
            target_trade_date=today_iso,
            notes=["another scheduler instance holds the scheduler lock; this invocation did nothing"],
            state_path=str(state_path),
        )

    try:
        state, state_warning = _load_state(state_path)
        if state_warning:
            notes.append(state_warning)

        registry_state = _state_from_registry(read_registry(registry_path), today_iso)
        records = read_registry(registry_path)
        if state is None and registry_state is not None:
            state = registry_state
            notes.append("SCHEDULER_STATE_RECOVERED_FROM_REGISTRY")
        if state is None:
            manifest_state = _state_from_manifest(repo_root, today_iso)
            if manifest_state is not None:
                state = manifest_state
                notes.append("SCHEDULER_STATE_RECOVERED_FROM_MANIFEST")
        elif _reconcile_manual_manifest(repo_root, state, records):
            _write_state(state_path, state, now_seoul, notes)
            notes.append("SCHEDULER_STATE_RECONCILED_FROM_MANUAL_MANIFEST")

        carry_success_run_at = state.last_successful_run_at if state else None
        carry_success_trade_date = state.last_successful_trade_date if state else None
        if state is not None and state.target_trade_date != today_iso:
            state = None  # 새 날짜: 이전 미해결 상태를 자동 성공 처리하지 않고 새로 시작

        if state is not None and state.current_status in TERMINAL_STATUSES:
            return _result_from_state("ALREADY_TERMINAL", state, notes, state_path)

        if not is_trading_day(today, holidays):
            state = SchedulerState(
                target_trade_date=today_iso,
                scheduler_date=today_iso,
                current_status=STATUS_NON_TRADING_DAY,
                attempt=0,
                first_scheduled_at=_iso(_slot_datetime(today, 0)),
                completed_at=_iso(now_seoul),
                last_successful_run_at=carry_success_run_at,
                last_successful_trade_date=carry_success_trade_date,
            )
            _write_state(state_path, state, now_seoul, notes)
            _append_to_registry(registry_path, state, now_seoul, notes)
            return _result_from_state("RECORDED_NON_TRADING_DAY", state, notes, state_path)

        due_index = _due_slot_index(now_seoul)
        if due_index is None:
            return SchedulerTickResult(
                action="WAITING_FIRST_SLOT",
                target_trade_date=today_iso,
                scheduler_status=state.current_status if state else None,
                attempt=state.attempt if state else 0,
                notes=[f"first slot is {_iso(_slot_datetime(today, 0))}; nothing to do yet"],
                state_path=str(state_path),
            )

        window_end = _slot_datetime(today, len(SCHEDULE_SLOTS) - 1) + LAST_SLOT_GRACE
        if now_seoul > window_end:
            if state is None or state.attempt == 0:
                state = SchedulerState(
                    target_trade_date=today_iso,
                    scheduler_date=today_iso,
                    current_status=STATUS_FAILED,
                    attempt=0,
                    first_scheduled_at=_iso(_slot_datetime(today, 0)),
                    completed_at=_iso(now_seoul),
                    error_code="MISSED_RUN_WINDOW_EXPIRED",
                    error_message=(
                        f"all scheduled slots ({', '.join(s.strftime('%H:%M') for s in SCHEDULE_SLOTS)} KST) "
                        "were missed; operator must review and use the manual daily run command"
                    ),
                    operator_action_required=True,
                    last_successful_run_at=carry_success_run_at,
                    last_successful_trade_date=carry_success_trade_date,
                )
            else:
                state.current_status = STATUS_FAILED
                state.completed_at = _iso(now_seoul)
                state.next_retry_at = None
                state.error_code = "RETRY_WINDOW_EXPIRED"
                state.error_message = "retry window expired with the daily operation still unresolved"
                state.operator_action_required = True
                state.operator_action_code = "MANUAL_RERUN_ALLOWED"
            _write_state(state_path, state, now_seoul, notes)
            _append_to_registry(registry_path, state, now_seoul, notes)
            return _result_from_state("WINDOW_EXPIRED", state, notes, state_path)

        if state is not None and state.current_status == STATUS_RETRY_PENDING:
            next_retry = _parse_dt(state.next_retry_at)
            if next_retry is not None and now_seoul < next_retry:
                return _result_from_state("WAITING_RETRY_SLOT", state, notes, state_path)

        # --- attempt 실행 ---
        if state is None:
            state = SchedulerState(
                target_trade_date=today_iso,
                scheduler_date=today_iso,
                current_status=STATUS_RETRY_PENDING,
                first_scheduled_at=_iso(_slot_datetime(today, 0)),
                last_successful_run_at=carry_success_run_at,
                last_successful_trade_date=carry_success_trade_date,
            )
        state.attempt += 1
        state.last_attempt_at = _iso(now_seoul)

        active_tickers = tickers if tickers is not None else get_default_tickers()
        readiness = probe_data_readiness(repo_root, today, active_tickers)
        state.latest_market_date = readiness.market_latest_date or state.latest_market_date
        state.latest_investor_date = readiness.investor_latest_date or state.latest_investor_date
        last_slot = len(SCHEDULE_SLOTS) - 1

        if readiness.error_code is not None:
            # structural 문제 → 자동 retry 금지, 운영자 확인 필요
            _finalize(state, STATUS_BLOCKED, now_seoul, error_code=readiness.error_code, error_message=readiness.detail, operator=True)
        elif not readiness.ready:
            if due_index < last_slot:
                _pending(state, now_seoul, due_index, "DATA_NOT_READY", readiness.detail)
            else:
                _finalize(
                    state, STATUS_FAILED, now_seoul,
                    error_code="RETRY_EXHAUSTED",
                    error_message=f"DATA_NOT_READY after the final retry slot: {readiness.detail}",
                    operator=True,
                )
        else:
            try:
                daily = run_operation(repo_root=repo_root)
            except Exception as exc:  # orchestrator 자체가 못 돌아간 비정상 상황
                daily = None
                _finalize(
                    state, STATUS_FAILED, now_seoul,
                    error_code="SCHEDULER_INTERNAL_ERROR",
                    error_message=f"{type(exc).__name__}: {exc}",
                    operator=True,
                )
            if daily is not None:
                state.last_run_id = daily.run_id
                state.last_daily_status = daily.overall_status
                state.latest_market_date = daily.market_latest_date or state.latest_market_date
                state.latest_investor_date = daily.investor_latest_date or state.latest_investor_date
                if daily.overall_status in (DAILY_STATUS_SUCCESS, DAILY_STATUS_SUCCESS_WITH_WARNING):
                    final_status = STATUS_SUCCESS if daily.overall_status == DAILY_STATUS_SUCCESS else STATUS_SUCCESS_WITH_WARNING
                    _finalize(state, final_status, now_seoul)
                    state.last_successful_run_at = daily.finished_at
                    state.last_successful_trade_date = today_iso
                else:
                    category, code, message = classify_daily_failure(daily)
                    state.failed_phase = daily.failed_phase
                    if category == CATEGORY_RETRYABLE and daily.failed_phase == PHASE_DASHBOARD_RUNNER and not state.pipeline_retried:
                        # A transient dashboard pipeline error gets one retry
                        # inside this scheduler attempt, not a hidden loop.
                        state.pipeline_retried = True
                        try:
                            retry_daily = run_operation(repo_root=repo_root)
                        except Exception as exc:
                            _finalize(state, STATUS_FAILED, now_seoul, error_code="SCHEDULER_INTERNAL_ERROR",
                                      error_message=f"{type(exc).__name__}: {exc}", operator=True)
                        else:
                            daily = retry_daily
                            state.last_run_id = daily.run_id
                            state.last_daily_status = daily.overall_status
                            state.latest_market_date = daily.market_latest_date or state.latest_market_date
                            state.latest_investor_date = daily.investor_latest_date or state.latest_investor_date
                            if daily.overall_status in (DAILY_STATUS_SUCCESS, DAILY_STATUS_SUCCESS_WITH_WARNING):
                                final_status = STATUS_SUCCESS if daily.overall_status == DAILY_STATUS_SUCCESS else STATUS_SUCCESS_WITH_WARNING
                                _finalize(state, final_status, now_seoul)
                                state.last_successful_run_at = daily.finished_at
                                state.last_successful_trade_date = today_iso
                                daily = None
                            else:
                                category, code, message = classify_daily_failure(daily)
                                state.failed_phase = daily.failed_phase
                    if daily is None and state.current_status in TERMINAL_STATUSES:
                        pass
                    elif category == CATEGORY_BLOCKED:
                        _finalize(state, STATUS_BLOCKED, now_seoul, error_code=code, error_message=message, operator=True)
                    elif category == CATEGORY_RETRYABLE:
                        pipeline_phase = daily.failed_phase == PHASE_DASHBOARD_RUNNER
                        # pipeline transient 실패는 정책상 자동 재시도 1회만 허용
                        budget_left = due_index < last_slot and (not pipeline_phase or not state.pipeline_retried)
                        if budget_left:
                            if pipeline_phase:
                                state.pipeline_retried = True
                            _pending(state, now_seoul, due_index, code, message)
                        else:
                            _finalize(state, STATUS_FAILED, now_seoul, error_code="RETRY_EXHAUSTED",
                                      error_message=f"{code}: {message}", operator=True)
                    else:
                        _finalize(state, STATUS_FAILED, now_seoul, error_code=code, error_message=message, operator=True)

        _write_state(state_path, state, now_seoul, notes)
        _append_to_registry(registry_path, state, now_seoul, notes)
        return _result_from_state("EXECUTED", state, notes, state_path)
    finally:
        lock.release()


def _parse_now(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main(argv: list[str] | None = None, *, repo_root: Path = ROOT_DIR) -> int:
    parser = argparse.ArgumentParser(description="Run one STEP 7 scheduler tick for the BAIKAL daily operation.")
    parser.add_argument("--json", action="store_true", help="Print the scheduler tick result as JSON.")
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "ISO-8601 datetime used as the scheduler decision clock (naive values are read as Asia/Seoul). "
            "For testing/verification only: the STEP 6 manifest keeps its real UTC timestamps."
        ),
    )
    args = parser.parse_args(argv)
    now = _parse_now(args.now) if args.now else None
    result = run_scheduler_tick(repo_root=repo_root, now=now)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Scheduler action: {result.action}")
        print(f"Scheduler status: {result.scheduler_status}")
        print(f"Target trade date: {result.target_trade_date}")
    return 1 if result.scheduler_status in (STATUS_BLOCKED, STATUS_FAILED) else 0


if __name__ == "__main__":
    raise SystemExit(main())

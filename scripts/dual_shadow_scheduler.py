"""DUAL Shadow automated operation scheduler.

이 모듈은 Production 스케줄러(scripts/daily_scheduler.py)를 전혀 수정하지 않고,
그 결과를 read-only로 조회하여 DUAL Shadow(READ-ONLY) 실행 여부만 판정/호출한다.

STEP 2: trade date 결정 + Production readiness 판정 (읽기 전용).
STEP 3: DUAL 전용 lock + DUAL 전용 scheduler state + 기존
    src.dual_shadow_daily_pipeline.run_dual_shadow_daily_pipeline() 호출 연결.
    새 평가/Signal 로직은 추가하지 않는다 — 기존 pipeline을 그대로 호출하고,
    그 반환 status를 그대로 dual_status에 기록한다.

STEP 3에서도 하지 않는 것: Windows Task Scheduler 등록, Production
파일/registry/state에 대한 쓰기, Production 상태 변경.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

from scripts.daily_run_registry import RegistryRecord, get_runs_for_trade_date
from scripts.korean_market_calendar import is_trading_day, load_holidays
from src.dual_shadow_daily_pipeline import (
    STATUS_FAILED as PIPELINE_STATUS_FAILED,
    DualDailyPipelineResult,
    run_dual_shadow_daily_pipeline,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
PRODUCTION_REGISTRY_SOURCE = "output/daily_run_registry.jsonl"
DUAL_LOCK_SOURCE = "output/dual_shadow_scheduler.lock"
DUAL_STATE_SOURCE = "output/dual_shadow_scheduler_state.json"
# DUAL scheduler tick 자체의 중복 실행만 막는다 (production SchedulerLock과 동일한
# stale-timeout 패턴이지만 완전히 별도 lock 파일/클래스 — 서로 절대 간섭하지 않는다).
DUAL_LOCK_STALE_AFTER = timedelta(minutes=15)

# scheduler_status (DUAL scheduler tick 자체의 결과 — production orchestration_status와 무관)
SCHEDULER_STATUS_COMPLETED = "COMPLETED"
SCHEDULER_STATUS_SKIPPED_DUPLICATE_RUN = "SKIPPED_DUPLICATE_RUN"
SCHEDULER_STATUS_FAILED = "FAILED"

READY = "READY"
PRODUCTION_NOT_READY = "PRODUCTION_NOT_READY"
NON_TRADING_DAY = "NON_TRADING_DAY"

# Production orchestration statuses that count as "operation succeeded".
_PRODUCTION_SUCCESS_STATUSES = frozenset({"SUCCESS", "SUCCESS_WITH_WARNING"})


def _operational_timezone():
    """Asia/Seoul via stdlib; fixed UTC+09:00 fallback (동일 패턴: daily_scheduler)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Asia/Seoul")
        except Exception:  # tz database missing (e.g. Windows without tzdata)
            pass
    return timezone(timedelta(hours=9), name="Asia/Seoul")


OPERATIONAL_TIMEZONE = _operational_timezone()


def determine_trade_date(explicit_trade_date: str | None = None, now: datetime | None = None) -> date:
    """Resolve the target trade date: explicit override wins, else Asia/Seoul "today"."""
    if explicit_trade_date is not None:
        return date.fromisoformat(explicit_trade_date)
    reference = now if now is not None else datetime.now(OPERATIONAL_TIMEZONE)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=OPERATIONAL_TIMEZONE)
    else:
        reference = reference.astimezone(OPERATIONAL_TIMEZONE)
    return reference.date()


@dataclass
class DualReadinessReport:
    trade_date: str
    is_trading_day: bool
    status: str  # READY / PRODUCTION_NOT_READY / NON_TRADING_DAY
    latest_attempt_status: str | None
    latest_attempt_event_id: str | None
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_production_readiness(
    trade_date: date,
    registry_path: Path | None = None,
    repo_root: Path | None = None,
) -> DualReadinessReport:
    """Read-only readiness check against the Production run registry.

    Judged strictly by the LATEST attempt recorded for trade_date — an
    earlier SUCCESS does not make this READY if the latest attempt is not
    SUCCESS/SUCCESS_WITH_WARNING.
    """
    repo_root = repo_root or ROOT_DIR
    path = Path(registry_path) if registry_path is not None else repo_root / PRODUCTION_REGISTRY_SOURCE
    trade_date_str = trade_date.isoformat()

    records: list[RegistryRecord] = get_runs_for_trade_date(path, trade_date_str)
    if not records:
        return DualReadinessReport(
            trade_date=trade_date_str,
            is_trading_day=True,
            status=PRODUCTION_NOT_READY,
            latest_attempt_status=None,
            latest_attempt_event_id=None,
            detail="no production registry record for trade_date",
        )

    latest = records[-1]
    if latest.orchestration_status in _PRODUCTION_SUCCESS_STATUSES:
        status = READY
        detail = f"latest attempt orchestration_status={latest.orchestration_status}"
    else:
        status = PRODUCTION_NOT_READY
        detail = f"latest attempt orchestration_status={latest.orchestration_status} (not success)"

    return DualReadinessReport(
        trade_date=trade_date_str,
        is_trading_day=True,
        status=status,
        latest_attempt_status=latest.orchestration_status,
        latest_attempt_event_id=latest.event_id,
        detail=detail,
    )


def check_dual_readiness(
    explicit_trade_date: str | None = None,
    now: datetime | None = None,
    registry_path: Path | None = None,
    repo_root: Path | None = None,
    holidays_repo_root: Path | None = None,
) -> DualReadinessReport:
    """Full STEP 2 decision: trade date -> trading day check -> production readiness."""
    trade_date = determine_trade_date(explicit_trade_date, now)
    holidays, _warning = load_holidays(repo_root=holidays_repo_root or (repo_root or ROOT_DIR))
    if not is_trading_day(trade_date, holidays):
        return DualReadinessReport(
            trade_date=trade_date.isoformat(),
            is_trading_day=False,
            status=NON_TRADING_DAY,
            latest_attempt_status=None,
            latest_attempt_event_id=None,
            detail="trade_date is not a KRX trading day",
        )

    return evaluate_production_readiness(trade_date, registry_path=registry_path, repo_root=repo_root)


class DualShadowSchedulerLock:
    """DUAL 전용 중복 실행 방지 lock. Production SchedulerLock과 완전히 분리된 별도 파일/클래스.

    Production의 output/daily_scheduler.lock 은 절대 읽거나 쓰지 않는다.
    """

    def __init__(self, lock_path: Path, stale_after: timedelta = DUAL_LOCK_STALE_AFTER) -> None:
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
        # Only release a lock this instance itself acquired.
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
class DualSchedulerState:
    trade_date: str | None
    scheduler_status: str
    started_at: str
    finished_at: str
    production_status: str
    dual_status: str | None
    detail: str
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write_dual_state(path: Path, state: DualSchedulerState) -> None:
    """Best-effort atomic write. Failure here must never affect Production."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".dual_shadow_scheduler_state_", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
    except OSError:
        # State persistence is diagnostic only; never raise out of the scheduler tick.
        pass


@dataclass
class DualSchedulerTickResult:
    trade_date: str | None
    scheduler_status: str
    production_status: str
    dual_status: str | None
    detail: str
    pipeline_result: DualDailyPipelineResult | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pipeline_result"] = self.pipeline_result.to_dict() if self.pipeline_result is not None else None
        return payload


def run_dual_shadow_scheduler_tick(
    explicit_trade_date: str | None = None,
    now: datetime | None = None,
    production_registry_path: Path | None = None,
    repo_root: Path | None = None,
    lock_path: Path | None = None,
    state_path: Path | None = None,
    pipeline_fn: Callable[..., DualDailyPipelineResult] = run_dual_shadow_daily_pipeline,
    pipeline_kwargs: dict[str, Any] | None = None,
) -> DualSchedulerTickResult:
    """One DUAL scheduler tick: readiness gate -> lock -> existing pipeline call -> state.

    Only calls the existing run_dual_shadow_daily_pipeline() (or an injected
    pipeline_fn for tests) when readiness.status == READY and the DUAL lock
    is acquired. Never touches Production registry/state/lock files.
    """
    repo_root = repo_root or ROOT_DIR
    lock_path = Path(lock_path) if lock_path is not None else repo_root / DUAL_LOCK_SOURCE
    state_path = Path(state_path) if state_path is not None else repo_root / DUAL_STATE_SOURCE
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    readiness = check_dual_readiness(
        explicit_trade_date=explicit_trade_date,
        now=now,
        registry_path=production_registry_path,
        repo_root=repo_root,
    )

    if readiness.status != READY:
        result = DualSchedulerTickResult(
            trade_date=readiness.trade_date,
            scheduler_status=readiness.status,
            production_status=readiness.status,
            dual_status=None,
            detail=readiness.detail,
        )
        _write_dual_state(
            state_path,
            DualSchedulerState(
                trade_date=result.trade_date,
                scheduler_status=result.scheduler_status,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                production_status=result.production_status,
                dual_status=result.dual_status,
                detail=result.detail,
            ),
        )
        return result

    lock = DualShadowSchedulerLock(lock_path)
    if not lock.acquire():
        result = DualSchedulerTickResult(
            trade_date=readiness.trade_date,
            scheduler_status=SCHEDULER_STATUS_SKIPPED_DUPLICATE_RUN,
            production_status=readiness.status,
            dual_status=None,
            detail="DUAL scheduler lock already held (duplicate run prevented)",
        )
        _write_dual_state(
            state_path,
            DualSchedulerState(
                trade_date=result.trade_date,
                scheduler_status=result.scheduler_status,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                production_status=result.production_status,
                dual_status=result.dual_status,
                detail=result.detail,
            ),
        )
        return result

    error_code: str | None = None
    error_message: str | None = None
    try:
        pipeline_result = pipeline_fn(target_trade_date=readiness.trade_date, **(pipeline_kwargs or {}))
        scheduler_status = SCHEDULER_STATUS_COMPLETED
        dual_status = pipeline_result.status
        detail = f"pipeline status={pipeline_result.status}"
    except Exception as exc:  # DUAL failure must never propagate as a Production failure.
        pipeline_result = None
        scheduler_status = SCHEDULER_STATUS_FAILED
        dual_status = "FAILED"
        error_code = type(exc).__name__
        error_message = str(exc)
        detail = f"pipeline raised {error_code}: {error_message}"
    finally:
        lock.release()

    result = DualSchedulerTickResult(
        trade_date=readiness.trade_date,
        scheduler_status=scheduler_status,
        production_status=readiness.status,
        dual_status=dual_status,
        detail=detail,
        pipeline_result=pipeline_result,
    )
    _write_dual_state(
        state_path,
        DualSchedulerState(
            trade_date=result.trade_date,
            scheduler_status=result.scheduler_status,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            production_status=result.production_status,
            dual_status=result.dual_status,
            detail=result.detail,
            error_code=error_code,
            error_message=error_message,
        ),
    )
    return result


def _exit_code(result: DualSchedulerTickResult) -> int:
    """0 = normal completion or normal non-execution (gate/duplicate-skip); non-zero = real failure.

    NON_TRADING_DAY / PRODUCTION_NOT_READY / SKIPPED_DUPLICATE_RUN are normal gate
    outcomes (wait for the next scheduled attempt), not operational failures, so they
    are 0. A pipeline-internal FAILED status (dual_status) and an unexpected exception
    (scheduler_status == FAILED) are the only non-zero cases.
    """
    if result.scheduler_status == SCHEDULER_STATUS_FAILED:
        return 1
    if result.dual_status == PIPELINE_STATUS_FAILED:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="DUAL Shadow scheduler (readiness gate + pipeline call)")
    parser.add_argument("--trade-date", help="Explicit trade date YYYY-MM-DD. Defaults to Asia/Seoul today.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON result")
    args = parser.parse_args()

    try:
        result = run_dual_shadow_scheduler_tick(explicit_trade_date=args.trade_date)
    except Exception as exc:  # CLI safety net: never leak a raw traceback into operational output.
        payload = {
            "scheduler_status": SCHEDULER_STATUS_FAILED,
            "dual_status": None,
            "error_code": type(exc).__name__,
            "error_message": str(exc),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"[DUAL Shadow Scheduler] scheduler_status=FAILED error={type(exc).__name__}: {exc}")
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            f"[DUAL Shadow Scheduler] trade_date={result.trade_date} "
            f"scheduler_status={result.scheduler_status} dual_status={result.dual_status} detail={result.detail}"
        )
    return _exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())

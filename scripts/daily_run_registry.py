"""Run Registry for BAIKAL daily operations - append-only operational history.

이 모듈은 daily scheduler의 각 attempt를 기록하는 append-only registry를
제공한다. STEP 6 Daily Operational Orchestrator의 상태를 변경하지 않고,
scheduler layer의 운영 이력만 보존한다.

주요 특징:
- Append-only JSONL format
- 동일 event 중복 기록 방지 (deterministic event_id)
- Atomicity: flush/fsync로 부분 쓰기 방지
- Malformed final line 처리
- Read-only query 함수 제공
- Registry 쓰기 실패는 scheduler 실행을 막지 않음
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any

# Registry version
REGISTRY_VERSION = 1

# Registry event types
EVENT_ATTEMPT_COMPLETED = "ATTEMPT_COMPLETED"
EVENT_RETRY_SCHEDULED = "RETRY_SCHEDULED"
EVENT_NON_TRADING_DAY = "NON_TRADING_DAY"
EVENT_MISSED_RUN = "MISSED_RUN"
EVENT_MANUAL_RUN_COMPLETED = "MANUAL_RUN_COMPLETED"


@dataclass
class RegistryRecord:
    """Run registry record for append-only storage."""

    registry_version: int
    event_id: str  # deterministic hash to prevent duplicates
    scheduler_date: str  # ISO date when scheduler ran
    target_trade_date: str  # ISO date of target trading day
    timezone: str  # "Asia/Seoul"
    event_type: str  # EVENT_ATTEMPT_COMPLETED, EVENT_RETRY_SCHEDULED, etc.
    orchestration_status: str  # scheduler status: SUCCESS, RETRY_PENDING, BLOCKED, FAILED, etc.
    source: str | None = None  # SCHEDULER or MANUAL
    slot: int | None = None  # 0-3, None for non-trading-day/missed
    attempt: int | None = None  # attempt number, None for non-trading-day/missed
    daily_status: str | None = None  # STEP 6 status if operation ran (SUCCESS, SUCCESS_WITH_WARNING, FAILED)
    started_at: str | None = None  # ISO datetime when attempt started
    finished_at: str | None = None  # ISO datetime when attempt finished
    next_retry_at: str | None = None  # ISO datetime for next retry slot (if RETRY_SCHEDULED)
    last_run_id: str | None = None  # run_id from DailyOperationalResult
    failed_phase: str | None = None  # phase that failed
    error_code: str | None = None  # error code
    error_message: str | None = None  # error message
    operator_action_required: bool = False
    operator_action_code: str | None = None
    latest_market_date: str | None = None  # latest market data date
    latest_investor_date: str | None = None  # latest investor data date
    integrity_status: str | None = None  # gate status if available
    pipeline_status: str | None = None  # dashboard pipeline status if available
    created_at: str | None = None  # ISO datetime when record was created

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Any) -> "RegistryRecord":
        """Parse record from dict, ignoring unknown fields and providing defaults for missing required fields."""
        if not isinstance(payload, dict):
            raise ValueError("registry record is not a JSON object")
        known = {f.name for f in cls.__dataclass_fields__.values()}
        data = {k: v for k, v in payload.items() if k in known}
        
        # Provide defaults for critical fields if missing
        if "registry_version" not in data:
            data["registry_version"] = REGISTRY_VERSION
        if "event_id" not in data:
            data["event_id"] = ""
        if "scheduler_date" not in data:
            data["scheduler_date"] = ""
        if "target_trade_date" not in data:
            data["target_trade_date"] = ""
        if "timezone" not in data:
            data["timezone"] = "Asia/Seoul"
        if "event_type" not in data:
            data["event_type"] = ""
        if "orchestration_status" not in data:
            data["orchestration_status"] = ""
        
        return cls(**data)


def _compute_event_id(
    target_trade_date: str,
    slot: int | None,
    attempt: int | None,
    orchestration_status: str,
) -> str:
    """Deterministic event_id to prevent duplicate records.
    
    Combines target_trade_date + slot + attempt + final_status.
    Returns stable hash even if called multiple times with same inputs.
    """
    key = f"{target_trade_date}:{slot}:{attempt}:{orchestration_status}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    """Current UTC datetime as ISO string."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def append_record(
    registry_path: Path,
    record: RegistryRecord,
) -> tuple[bool, str | None]:
    """Append record to registry atomically.
    
    Returns (success, error_message).
    Failure does not prevent scheduler execution.
    """
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)

        # Scheduler retries may repeat a completed write after a crash. Keep
        # the log append-only while making the deterministic event idempotent.
        if any(existing.event_id == record.event_id for existing in read_registry(registry_path)):
            return True, None
        
        # Set creation timestamp if not already set
        if record.created_at is None:
            record.created_at = _now_iso()
        
        # Append to JSONL file with fsync for durability
        with open(registry_path, "a", encoding="utf-8", newline="") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        
        return True, None
    except Exception as exc:
        return False, f"registry append failed: {type(exc).__name__}: {exc}"


def read_registry(registry_path: Path) -> list[RegistryRecord]:
    """Read all records from registry, skipping malformed final line.
    
    Returns list of RegistryRecord objects in order (oldest first).
    Malformed lines are skipped with a warning logged to stderr.
    """
    if not registry_path.exists():
        return []
    
    records: list[RegistryRecord] = []
    try:
        with open(registry_path, "r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.rstrip("\n\r")
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    record = RegistryRecord.from_dict(payload)
                    records.append(record)
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    # Skip malformed line (likely incomplete write at EOF)
                    import sys
                    print(f"Warning: skipping malformed registry line {line_no}: {exc}", file=sys.stderr)
                    continue
    except OSError as exc:
        import sys
        print(f"Warning: failed to read registry: {exc}", file=sys.stderr)
        return []
    
    return records


def get_recent_runs(registry_path: Path, limit: int = 10) -> list[RegistryRecord]:
    """Get the most recent terminal records (SUCCESS, BLOCKED, FAILED, NON_TRADING_DAY, MISSED_RUN).
    
    Returns up to `limit` terminal records, most recent first.
    """
    all_records = read_registry(registry_path)
    # Filter to terminal records only (those with event_type that marks completion)
    terminal_events = {EVENT_ATTEMPT_COMPLETED, EVENT_NON_TRADING_DAY, EVENT_MISSED_RUN}
    terminal = [r for r in all_records if r.event_type in terminal_events]
    return list(reversed(terminal))[:limit]


def get_runs_for_trade_date(registry_path: Path, trade_date: str | date) -> list[RegistryRecord]:
    """Get all records for a specific trade_date.
    
    Args:
        trade_date: ISO date string or date object
    
    Returns records in chronological order.
    """
    if isinstance(trade_date, date):
        trade_date = trade_date.isoformat()
    
    all_records = read_registry(registry_path)
    return [r for r in all_records if r.target_trade_date == trade_date]


def get_last_successful_run(registry_path: Path) -> RegistryRecord | None:
    """Get most recent successful run (SUCCESS or SUCCESS_WITH_WARNING).
    
    Returns the latest completed record with success status, or None.
    """
    all_records = read_registry(registry_path)
    success_statuses = {"SUCCESS", "SUCCESS_WITH_WARNING"}
    for record in reversed(all_records):
        if record.orchestration_status in success_statuses and record.event_type == EVENT_ATTEMPT_COMPLETED:
            return record
    return None


def get_last_failed_run(registry_path: Path) -> RegistryRecord | None:
    """Get most recent failed run (BLOCKED or FAILED).
    
    Returns the latest completed record with failure status, or None.
    """
    all_records = read_registry(registry_path)
    failed_statuses = {"BLOCKED", "FAILED"}
    for record in reversed(all_records):
        if record.orchestration_status in failed_statuses and record.event_type == EVENT_ATTEMPT_COMPLETED:
            return record
    return None


@dataclass
class DailySummary:
    """Summary of daily operation attempts."""
    trade_date: str
    attempts_count: int
    attempts: list[RegistryRecord] = field(default_factory=list)
    final_status: str | None = None
    first_attempt_at: str | None = None
    last_attempt_at: str | None = None
    last_run_id: str | None = None
    operator_action_required: bool = False


def get_daily_summary(registry_path: Path, trade_date: str | date) -> DailySummary | None:
    """Get summary of all attempts for a trade_date.
    
    Returns DailySummary with attempt count, final status, and timeline.
    Counts all records with a non-None attempt field (including retries).
    Final status comes from the last terminal record.
    """
    if isinstance(trade_date, date):
        trade_date_str = trade_date.isoformat()
    else:
        trade_date_str = trade_date
    
    records = get_runs_for_trade_date(registry_path, trade_date_str)
    if not records:
        return None
    
    # Count all records that represent an attempt (have attempt field)
    attempt_records = [r for r in records if r.attempt is not None]
    if not attempt_records:
        return None
    
    # Terminal records are those with event_type that marks completion
    terminal_events = {EVENT_ATTEMPT_COMPLETED, EVENT_NON_TRADING_DAY, EVENT_MISSED_RUN}
    terminal_records = [r for r in records if r.event_type in terminal_events]
    
    # Use the last terminal record (if exists) as the final status
    final_record = terminal_records[-1] if terminal_records else attempt_records[-1]
    
    summary = DailySummary(
        trade_date=trade_date_str,
        attempts_count=len(attempt_records),
        attempts=attempt_records,
        final_status=final_record.orchestration_status,
        first_attempt_at=attempt_records[0].started_at,
        last_attempt_at=attempt_records[-1].finished_at,
        last_run_id=final_record.last_run_id,
        operator_action_required=final_record.operator_action_required,
    )
    return summary

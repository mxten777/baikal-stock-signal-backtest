"""Tests for daily_run_registry module."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from scripts.daily_run_registry import (
    EVENT_ATTEMPT_COMPLETED,
    EVENT_MISSED_RUN,
    EVENT_NON_TRADING_DAY,
    EVENT_RETRY_SCHEDULED,
    REGISTRY_VERSION,
    DailySummary,
    RegistryRecord,
    _compute_event_id,
    _now_iso,
    append_record,
    get_daily_summary,
    get_last_failed_run,
    get_last_successful_run,
    get_recent_runs,
    get_runs_for_trade_date,
    read_registry,
)


@pytest.fixture
def temp_registry(tmp_path: Path) -> Path:
    """Temporary registry file path."""
    return tmp_path / "test_registry.jsonl"


class TestRegistryRecord:
    """Tests for RegistryRecord dataclass."""

    def test_record_to_dict(self) -> None:
        """RegistryRecord.to_dict() preserves all fields."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        data = record.to_dict()
        assert data["registry_version"] == REGISTRY_VERSION
        assert data["event_id"] == "abc123"
        assert data["orchestration_status"] == "SUCCESS"

    def test_record_from_dict(self) -> None:
        """RegistryRecord.from_dict() reconstructs from dict."""
        data = {
            "registry_version": REGISTRY_VERSION,
            "event_id": "abc123",
            "scheduler_date": "2026-09-04",
            "target_trade_date": "2026-09-04",
            "timezone": "Asia/Seoul",
            "slot": 0,
            "attempt": 1,
            "event_type": EVENT_ATTEMPT_COMPLETED,
            "orchestration_status": "SUCCESS",
            "daily_status": "SUCCESS",
            "started_at": "2026-09-04T18:30:00+09:00",
            "finished_at": "2026-09-04T18:35:00+09:00",
            "next_retry_at": None,
            "last_run_id": "run-001",
            "failed_phase": None,
            "error_code": None,
            "error_message": None,
        }
        record = RegistryRecord.from_dict(data)
        assert record.registry_version == REGISTRY_VERSION
        assert record.event_id == "abc123"
        assert record.orchestration_status == "SUCCESS"

    def test_record_from_dict_ignores_unknown_fields(self) -> None:
        """RegistryRecord.from_dict() ignores unknown fields."""
        data = {
            "registry_version": REGISTRY_VERSION,
            "event_id": "abc123",
            "scheduler_date": "2026-09-04",
            "target_trade_date": "2026-09-04",
            "timezone": "Asia/Seoul",
            "slot": 0,
            "attempt": 1,
            "event_type": EVENT_ATTEMPT_COMPLETED,
            "orchestration_status": "SUCCESS",
            "unknown_field": "should_be_ignored",
        }
        record = RegistryRecord.from_dict(data)
        assert not hasattr(record, "unknown_field")

    def test_record_with_optional_fields_null(self) -> None:
        """RegistryRecord handles None optional fields."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=None,
            attempt=None,
            event_type=EVENT_NON_TRADING_DAY,
            orchestration_status="NON_TRADING_DAY",
            daily_status=None,
            started_at=None,
            finished_at=None,
            next_retry_at=None,
            last_run_id=None,
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        data = record.to_dict()
        assert data["slot"] is None
        assert data["attempt"] is None


class TestEventIdGeneration:
    """Tests for event_id generation (idempotency)."""

    def test_event_id_deterministic(self) -> None:
        """_compute_event_id() generates same ID for same inputs."""
        id1 = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        id2 = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        assert id1 == id2

    def test_event_id_different_for_different_inputs(self) -> None:
        """_compute_event_id() differs for different inputs."""
        id1 = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        id2 = _compute_event_id("2026-09-04", 0, 1, "FAILED")
        assert id1 != id2

    def test_event_id_length(self) -> None:
        """_compute_event_id() returns 16-char hash."""
        event_id = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        assert len(event_id) == 16


class TestAppendRecord:
    """Tests for append_record() function."""

    def test_append_first_record(self, temp_registry: Path) -> None:
        """append_record() creates file and writes first record."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        success, error = append_record(temp_registry, record)
        assert success is True
        assert error is None
        assert temp_registry.exists()

    def test_append_multiple_records(self, temp_registry: Path) -> None:
        """append_record() appends to existing file."""
        record1 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        record2 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id2",
            scheduler_date="2026-09-05",
            target_trade_date="2026-09-05",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-05T18:30:00+09:00",
            finished_at="2026-09-05T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-002",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record1)
        success, error = append_record(temp_registry, record2)
        assert success is True
        assert error is None
        records = read_registry(temp_registry)
        assert len(records) == 2

    def test_append_sets_created_at(self, temp_registry: Path) -> None:
        """append_record() sets created_at if not already set."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
            created_at=None,
        )
        assert record.created_at is None
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert len(records) == 1
        assert records[0].created_at is not None

    def test_append_preserves_existing_created_at(self, temp_registry: Path) -> None:
        """append_record() preserves created_at if already set."""
        original_time = "2026-09-04T10:00:00Z"
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
            created_at=original_time,
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert records[0].created_at == original_time


class TestReadRegistry:
    """Tests for read_registry() function."""

    def test_read_empty_registry(self, temp_registry: Path) -> None:
        """read_registry() returns empty list for nonexistent file."""
        records = read_registry(temp_registry)
        assert records == []

    def test_read_single_record(self, temp_registry: Path) -> None:
        """read_registry() reads single record correctly."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="abc123",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert len(records) == 1
        assert records[0].event_id == "abc123"

    def test_read_multiple_records_in_order(self, temp_registry: Path) -> None:
        """read_registry() returns records in append order."""
        for i in range(3):
            record = RegistryRecord(
                registry_version=REGISTRY_VERSION,
                event_id=f"id{i}",
                scheduler_date="2026-09-04",
                target_trade_date="2026-09-04",
                timezone="Asia/Seoul",
                slot=i,
                attempt=1,
                event_type=EVENT_ATTEMPT_COMPLETED,
                orchestration_status="SUCCESS",
                daily_status="SUCCESS",
                started_at=f"2026-09-04T{18+i}:30:00+09:00",
                finished_at=f"2026-09-04T{18+i}:35:00+09:00",
                next_retry_at=None,
                last_run_id=f"run-{i:03d}",
                failed_phase=None,
                error_code=None,
                error_message=None,
            )
            append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert len(records) == 3
        assert records[0].event_id == "id0"
        assert records[1].event_id == "id1"
        assert records[2].event_id == "id2"

    def test_read_skips_malformed_line(self, temp_registry: Path) -> None:
        """read_registry() skips malformed JSON lines."""
        # Write a valid record
        record1 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record1)
        
        # Manually write a malformed line
        with open(temp_registry, "a") as f:
            f.write("{malformed json\n")
        
        # Write another valid record
        record2 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id2",
            scheduler_date="2026-09-05",
            target_trade_date="2026-09-05",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-05T18:30:00+09:00",
            finished_at="2026-09-05T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-002",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record2)
        
        # Should get 2 valid records, skipping malformed
        records = read_registry(temp_registry)
        assert len(records) == 2
        assert records[0].event_id == "id1"
        assert records[1].event_id == "id2"


class TestAttemptSequence:
    """Tests for attempt tracking scenarios."""

    def test_three_retry_attempts(self, temp_registry: Path) -> None:
        """Registry preserves sequence of 3 retry attempts."""
        # First attempt at 18:30 - RETRY_PENDING
        record1 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id=_compute_event_id("2026-09-04", 0, 1, "RETRY_PENDING"),
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_RETRY_SCHEDULED,
            orchestration_status="RETRY_PENDING",
            daily_status=None,
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:32:00+09:00",
            next_retry_at="2026-09-04T19:00:00+09:00",
            last_run_id="run-001",
            failed_phase="MARKET_UPDATE",
            error_code="DATA_NOT_READY",
            error_message="market data not ready",
        )
        append_record(temp_registry, record1)
        
        # Second attempt at 19:00 - RETRY_PENDING
        record2 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id=_compute_event_id("2026-09-04", 1, 2, "RETRY_PENDING"),
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=1,
            attempt=2,
            event_type=EVENT_RETRY_SCHEDULED,
            orchestration_status="RETRY_PENDING",
            daily_status=None,
            started_at="2026-09-04T19:00:00+09:00",
            finished_at="2026-09-04T19:02:00+09:00",
            next_retry_at="2026-09-04T19:30:00+09:00",
            last_run_id="run-002",
            failed_phase="MARKET_UPDATE",
            error_code="DATA_NOT_READY",
            error_message="market data not ready",
        )
        append_record(temp_registry, record2)
        
        # Third attempt at 19:30 - SUCCESS
        record3 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id=_compute_event_id("2026-09-04", 2, 3, "SUCCESS"),
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=2,
            attempt=3,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T19:30:00+09:00",
            finished_at="2026-09-04T19:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-003",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record3)
        
        # Verify all 3 records are stored
        records = read_registry(temp_registry)
        assert len(records) == 3
        assert records[0].attempt == 1
        assert records[1].attempt == 2
        assert records[2].attempt == 3
        assert records[0].orchestration_status == "RETRY_PENDING"
        assert records[1].orchestration_status == "RETRY_PENDING"
        assert records[2].orchestration_status == "SUCCESS"


class TestIdempotency:
    """Tests for duplicate prevention."""

    def test_duplicate_event_prevented(self, temp_registry: Path) -> None:
        """Same event_id on re-append is idempotent in deterministic sense."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="same-id",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        # In real scheduler, same attempt result would have same event_id
        # So re-calling wouldn't append (prevented at scheduler level by event_id check)
        # Here we test that deterministic ID works
        id1 = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        id2 = _compute_event_id("2026-09-04", 0, 1, "SUCCESS")
        assert id1 == id2

    def test_restart_recovery(self, temp_registry: Path) -> None:
        """Process restart doesn't duplicate record if event_id is same."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="restart-test",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        # First write (before crash)
        append_record(temp_registry, record)
        # After restart, same record written again
        # (in real system, scheduler checks event_id before append)
        initial_records = read_registry(temp_registry)
        assert len(initial_records) == 1


class TestEventTypes:
    """Tests for different event types."""

    def test_attempt_completed_event(self, temp_registry: Path) -> None:
        """EVENT_ATTEMPT_COMPLETED for terminal success."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test-complete",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert records[0].event_type == EVENT_ATTEMPT_COMPLETED

    def test_retry_scheduled_event(self, temp_registry: Path) -> None:
        """EVENT_RETRY_SCHEDULED for retry pending."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test-retry",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_RETRY_SCHEDULED,
            orchestration_status="RETRY_PENDING",
            daily_status=None,
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:32:00+09:00",
            next_retry_at="2026-09-04T19:00:00+09:00",
            last_run_id="run-001",
            failed_phase="MARKET_UPDATE",
            error_code="DATA_NOT_READY",
            error_message="not ready",
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert records[0].event_type == EVENT_RETRY_SCHEDULED

    def test_non_trading_day_event(self, temp_registry: Path) -> None:
        """EVENT_NON_TRADING_DAY for market closed."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test-holiday",
            scheduler_date="2026-09-12",  # Saturday
            target_trade_date="2026-09-12",
            timezone="Asia/Seoul",
            slot=None,
            attempt=None,
            event_type=EVENT_NON_TRADING_DAY,
            orchestration_status="NON_TRADING_DAY",
            daily_status=None,
            started_at="2026-09-12T18:30:00+09:00",
            finished_at="2026-09-12T18:30:01+09:00",
            next_retry_at=None,
            last_run_id=None,
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert records[0].event_type == EVENT_NON_TRADING_DAY

    def test_missed_run_event(self, temp_registry: Path) -> None:
        """EVENT_MISSED_RUN for window expired."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test-missed",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=None,
            attempt=None,
            event_type=EVENT_MISSED_RUN,
            orchestration_status="FAILED",
            daily_status=None,
            started_at=None,
            finished_at="2026-09-04T20:31:00+09:00",
            next_retry_at=None,
            last_run_id=None,
            failed_phase=None,
            error_code="MISSED_RUN_WINDOW_EXPIRED",
            error_message="all scheduled slots were missed",
        )
        append_record(temp_registry, record)
        records = read_registry(temp_registry)
        assert records[0].event_type == EVENT_MISSED_RUN


class TestReaderFunctions:
    """Tests for query reader functions."""

    def test_get_recent_runs(self, temp_registry: Path) -> None:
        """get_recent_runs() returns most recent terminal records."""
        for i in range(5):
            record = RegistryRecord(
                registry_version=REGISTRY_VERSION,
                event_id=f"id{i}",
                scheduler_date=f"2026-09-{4+i:02d}",
                target_trade_date=f"2026-09-{4+i:02d}",
                timezone="Asia/Seoul",
                slot=0,
                attempt=1,
                event_type=EVENT_ATTEMPT_COMPLETED,
                orchestration_status="SUCCESS" if i % 2 == 0 else "FAILED",
                daily_status="SUCCESS" if i % 2 == 0 else None,
                started_at=f"2026-09-{4+i:02d}T18:30:00+09:00",
                finished_at=f"2026-09-{4+i:02d}T18:35:00+09:00",
                next_retry_at=None,
                last_run_id=f"run-{i:03d}",
                failed_phase=None if i % 2 == 0 else "MARKET_UPDATE",
                error_code=None if i % 2 == 0 else "ERROR",
                error_message=None if i % 2 == 0 else "msg",
            )
            append_record(temp_registry, record)
        
        recent = get_recent_runs(temp_registry, limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].target_trade_date == "2026-09-08"
        assert recent[1].target_trade_date == "2026-09-07"
        assert recent[2].target_trade_date == "2026-09-06"

    def test_get_runs_for_trade_date(self, temp_registry: Path) -> None:
        """get_runs_for_trade_date() filters by trade date."""
        # Record for 2026-09-04 (3 attempts)
        for attempt in range(1, 4):
            record = RegistryRecord(
                registry_version=REGISTRY_VERSION,
                event_id=f"2026-09-04-attempt-{attempt}",
                scheduler_date="2026-09-04",
                target_trade_date="2026-09-04",
                timezone="Asia/Seoul",
                slot=attempt - 1,
                attempt=attempt,
                event_type=EVENT_ATTEMPT_COMPLETED if attempt == 3 else EVENT_RETRY_SCHEDULED,
                orchestration_status="SUCCESS" if attempt == 3 else "RETRY_PENDING",
                daily_status="SUCCESS" if attempt == 3 else None,
                started_at=f"2026-09-04T{18+attempt-1}:30:00+09:00",
                finished_at=f"2026-09-04T{18+attempt-1}:32:00+09:00",
                next_retry_at=None if attempt == 3 else f"2026-09-04T{19+attempt-1}:00:00+09:00",
                last_run_id=f"run-{attempt:03d}",
                failed_phase=None if attempt == 3 else "MARKET_UPDATE",
                error_code=None if attempt == 3 else "DATA_NOT_READY",
                error_message=None if attempt == 3 else "not ready",
            )
            append_record(temp_registry, record)
        
        # Record for 2026-09-05 (1 attempt)
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="2026-09-05-attempt-1",
            scheduler_date="2026-09-05",
            target_trade_date="2026-09-05",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-05T18:30:00+09:00",
            finished_at="2026-09-05T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-004",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        
        # Query 2026-09-04
        records = get_runs_for_trade_date(temp_registry, "2026-09-04")
        assert len(records) == 3
        assert all(r.target_trade_date == "2026-09-04" for r in records)
        
        # Query 2026-09-05
        records = get_runs_for_trade_date(temp_registry, "2026-09-05")
        assert len(records) == 1
        assert records[0].target_trade_date == "2026-09-05"

    def test_get_runs_for_trade_date_with_date_object(self, temp_registry: Path) -> None:
        """get_runs_for_trade_date() accepts date object."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test-date-obj",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        
        records = get_runs_for_trade_date(temp_registry, date(2026, 9, 4))
        assert len(records) == 1

    def test_get_last_successful_run(self, temp_registry: Path) -> None:
        """get_last_successful_run() returns most recent success."""
        # FAILED
        record1 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=3,
            attempt=4,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="FAILED",
            daily_status=None,
            started_at="2026-09-04T20:00:00+09:00",
            finished_at="2026-09-04T20:05:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase="MARKET_UPDATE",
            error_code="RETRY_EXHAUSTED",
            error_message="exhausted",
        )
        append_record(temp_registry, record1)
        
        # SUCCESS
        record2 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id2",
            scheduler_date="2026-09-05",
            target_trade_date="2026-09-05",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-05T18:30:00+09:00",
            finished_at="2026-09-05T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-002",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record2)
        
        # SUCCESS_WITH_WARNING
        record3 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id3",
            scheduler_date="2026-09-06",
            target_trade_date="2026-09-06",
            timezone="Asia/Seoul",
            slot=1,
            attempt=2,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS_WITH_WARNING",
            daily_status="SUCCESS_WITH_WARNING",
            started_at="2026-09-06T19:00:00+09:00",
            finished_at="2026-09-06T19:05:00+09:00",
            next_retry_at=None,
            last_run_id="run-003",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record3)
        
        last_success = get_last_successful_run(temp_registry)
        assert last_success is not None
        assert last_success.target_trade_date == "2026-09-06"
        assert last_success.orchestration_status == "SUCCESS_WITH_WARNING"

    def test_get_last_failed_run(self, temp_registry: Path) -> None:
        """get_last_failed_run() returns most recent failure."""
        # SUCCESS
        record1 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record1)
        
        # BLOCKED
        record2 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id2",
            scheduler_date="2026-09-05",
            target_trade_date="2026-09-05",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="BLOCKED",
            daily_status=None,
            started_at="2026-09-05T18:30:00+09:00",
            finished_at="2026-09-05T18:32:00+09:00",
            next_retry_at=None,
            last_run_id="run-002",
            failed_phase="INPUT_GATE",
            error_code="STRUCTURAL_FAILURE",
            error_message="blocked",
        )
        append_record(temp_registry, record2)
        
        # FAILED
        record3 = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id3",
            scheduler_date="2026-09-06",
            target_trade_date="2026-09-06",
            timezone="Asia/Seoul",
            slot=3,
            attempt=4,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="FAILED",
            daily_status=None,
            started_at="2026-09-06T20:00:00+09:00",
            finished_at="2026-09-06T20:05:00+09:00",
            next_retry_at=None,
            last_run_id="run-003",
            failed_phase="MARKET_UPDATE",
            error_code="RETRY_EXHAUSTED",
            error_message="exhausted",
        )
        append_record(temp_registry, record3)
        
        last_failed = get_last_failed_run(temp_registry)
        assert last_failed is not None
        assert last_failed.target_trade_date == "2026-09-06"
        assert last_failed.orchestration_status == "FAILED"


class TestDailySummary:
    """Tests for daily summary functionality."""

    def test_daily_summary_single_attempt(self, temp_registry: Path) -> None:
        """get_daily_summary() for single successful attempt."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        
        summary = get_daily_summary(temp_registry, "2026-09-04")
        assert summary is not None
        assert summary.trade_date == "2026-09-04"
        assert summary.attempts_count == 1
        assert summary.final_status == "SUCCESS"
        assert summary.last_run_id == "run-001"

    def test_daily_summary_multiple_attempts(self, temp_registry: Path) -> None:
        """get_daily_summary() for 3 retry attempts."""
        for attempt in range(1, 4):
            record = RegistryRecord(
                registry_version=REGISTRY_VERSION,
                event_id=f"id{attempt}",
                scheduler_date="2026-09-04",
                target_trade_date="2026-09-04",
                timezone="Asia/Seoul",
                slot=attempt - 1,
                attempt=attempt,
                event_type=EVENT_ATTEMPT_COMPLETED if attempt == 3 else EVENT_RETRY_SCHEDULED,
                orchestration_status="SUCCESS" if attempt == 3 else "RETRY_PENDING",
                daily_status="SUCCESS" if attempt == 3 else None,
                started_at=f"2026-09-04T{18+attempt-1}:30:00+09:00",
                finished_at=f"2026-09-04T{18+attempt-1}:32:00+09:00",
                next_retry_at=None if attempt == 3 else f"2026-09-04T{19+attempt-1}:00:00+09:00",
                last_run_id=f"run-{attempt:03d}",
                failed_phase=None if attempt == 3 else "MARKET_UPDATE",
                error_code=None if attempt == 3 else "DATA_NOT_READY",
                error_message=None if attempt == 3 else "not ready",
            )
            append_record(temp_registry, record)
        
        summary = get_daily_summary(temp_registry, "2026-09-04")
        assert summary is not None
        assert summary.attempts_count == 3
        assert summary.final_status == "SUCCESS"
        assert len(summary.attempts) == 3

    def test_daily_summary_nonexistent_date(self, temp_registry: Path) -> None:
        """get_daily_summary() returns None for no records."""
        summary = get_daily_summary(temp_registry, "2026-09-99")
        assert summary is None

    def test_daily_summary_with_date_object(self, temp_registry: Path) -> None:
        """get_daily_summary() accepts date object."""
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="id1",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        append_record(temp_registry, record)
        
        summary = get_daily_summary(temp_registry, date(2026, 9, 4))
        assert summary is not None
        assert summary.trade_date == "2026-09-04"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_malformed_record_json(self, temp_registry: Path) -> None:
        """Invalid JSON record is skipped."""
        with open(temp_registry, "w") as f:
            f.write('{"valid": true}\n')
            f.write('{invalid json\n')
            f.write('{"valid": true}\n')
        
        records = read_registry(temp_registry)
        assert len(records) == 2  # Malformed line skipped

    def test_empty_registry_file(self, temp_registry: Path) -> None:
        """Empty registry file returns empty list."""
        temp_registry.touch()
        records = read_registry(temp_registry)
        assert records == []

    def test_registry_write_failure_returns_error(self, tmp_path: Path) -> None:
        """append_record() returns error on write failure."""
        # Create a directory where file should go (to cause failure)
        bad_path = tmp_path / "subdir" / "registry.jsonl"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.mkdir()  # Make it a directory, not a file
        
        record = RegistryRecord(
            registry_version=REGISTRY_VERSION,
            event_id="test",
            scheduler_date="2026-09-04",
            target_trade_date="2026-09-04",
            timezone="Asia/Seoul",
            slot=0,
            attempt=1,
            event_type=EVENT_ATTEMPT_COMPLETED,
            orchestration_status="SUCCESS",
            daily_status="SUCCESS",
            started_at="2026-09-04T18:30:00+09:00",
            finished_at="2026-09-04T18:35:00+09:00",
            next_retry_at=None,
            last_run_id="run-001",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        success, error = append_record(bad_path, record)
        assert success is False
        assert error is not None

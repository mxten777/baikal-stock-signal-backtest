"""Controlled verification of registry integration with scheduler.

This script demonstrates the registry capturing a realistic retry sequence
without corrupting production data.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path

from scripts.daily_run_registry import (
    EVENT_ATTEMPT_COMPLETED,
    EVENT_RETRY_SCHEDULED,
    RegistryRecord,
    _compute_event_id,
    _now_iso,
    append_record,
    get_daily_summary,
    get_recent_runs,
    get_runs_for_trade_date,
    read_registry,
)


def verify_retry_sequence_scenario() -> None:
    """Verify a realistic 3-retry sequence is properly recorded.
    
    Scenario:
    2026-09-04 (target_trade_date)
    
    18:30 UTC+9 - attempt 1 failed (DATA_NOT_READY) -> RETRY_PENDING
    19:00 UTC+9 - attempt 2 failed (DATA_NOT_READY) -> RETRY_PENDING
    19:30 UTC+9 - attempt 3 succeeded -> SUCCESS
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "test_registry.jsonl"
        
        # Simulate attempt 1 at 18:30 - DATA_NOT_READY, will retry
        attempt1 = RegistryRecord(
            registry_version=1,
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
            last_run_id="run-20260904-001",
            failed_phase="MARKET_UPDATE",
            error_code="DATA_NOT_READY",
            error_message="market data not ready for 2026-09-04",
        )
        success1, error1 = append_record(registry_path, attempt1)
        assert success1, f"Append attempt 1 failed: {error1}"
        print("✓ Recorded attempt 1: RETRY_PENDING at 18:30")
        
        # Simulate attempt 2 at 19:00 - DATA_NOT_READY, will retry
        attempt2 = RegistryRecord(
            registry_version=1,
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
            last_run_id="run-20260904-002",
            failed_phase="MARKET_UPDATE",
            error_code="DATA_NOT_READY",
            error_message="market data not ready for 2026-09-04",
        )
        success2, error2 = append_record(registry_path, attempt2)
        assert success2, f"Append attempt 2 failed: {error2}"
        print("✓ Recorded attempt 2: RETRY_PENDING at 19:00")
        
        # Simulate attempt 3 at 19:30 - SUCCESS
        attempt3 = RegistryRecord(
            registry_version=1,
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
            last_run_id="run-20260904-003",
            failed_phase=None,
            error_code=None,
            error_message=None,
        )
        success3, error3 = append_record(registry_path, attempt3)
        assert success3, f"Append attempt 3 failed: {error3}"
        print("✓ Recorded attempt 3: SUCCESS at 19:30")
        
        # Read back all records
        print("\n=== Registry Contents ===")
        records = read_registry(registry_path)
        assert len(records) == 3, f"Expected 3 records, got {len(records)}"
        for i, record in enumerate(records, 1):
            print(f"Record {i}: attempt={record.attempt}, status={record.orchestration_status}, "
                  f"event_type={record.event_type}, error={record.error_code}")
        
        # Verify sequence correctness
        print("\n=== Verification ===")
        assert records[0].attempt == 1, "First record should be attempt 1"
        assert records[0].orchestration_status == "RETRY_PENDING", "First attempt should be RETRY_PENDING"
        assert records[0].event_type == EVENT_RETRY_SCHEDULED, "First should be RETRY_SCHEDULED event"
        print("✓ Attempt 1 sequence correct")
        
        assert records[1].attempt == 2, "Second record should be attempt 2"
        assert records[1].orchestration_status == "RETRY_PENDING", "Second attempt should be RETRY_PENDING"
        print("✓ Attempt 2 sequence correct")
        
        assert records[2].attempt == 3, "Third record should be attempt 3"
        assert records[2].orchestration_status == "SUCCESS", "Third attempt should be SUCCESS"
        assert records[2].event_type == EVENT_ATTEMPT_COMPLETED, "Third should be ATTEMPT_COMPLETED event"
        print("✓ Attempt 3 sequence correct")
        
        # Verify recent runs
        print("\n=== Recent Runs ===")
        recent = get_recent_runs(registry_path, limit=1)
        assert len(recent) == 1, f"Expected 1 recent run, got {len(recent)}"
        assert recent[0].orchestration_status == "SUCCESS", "Recent run should be SUCCESS"
        print("✓ Most recent run is SUCCESS")
        
        # Verify daily summary
        print("\n=== Daily Summary ===")
        summary = get_daily_summary(registry_path, "2026-09-04")
        assert summary is not None, "Summary should exist for trade date"
        assert summary.attempts_count == 3, f"Expected 3 attempts, got {summary.attempts_count}"
        assert summary.final_status == "SUCCESS", f"Expected final status SUCCESS, got {summary.final_status}"
        assert summary.first_attempt_at == "2026-09-04T18:30:00+09:00", "First attempt time mismatch"
        assert summary.last_attempt_at == "2026-09-04T19:35:00+09:00", "Last attempt time mismatch"
        print(f"✓ Daily Summary: {summary.attempts_count} attempts, final status={summary.final_status}")
        print(f"  Timeline: {summary.first_attempt_at} → {summary.last_attempt_at}")
        print(f"  Last run ID: {summary.last_run_id}")
        
        # Verify no data mutation
        print("\n=== Data Integrity ===")
        records_reread = read_registry(registry_path)
        assert len(records_reread) == 3, "Re-read should have same count"
        assert records_reread[0].event_id == records[0].event_id, "Record IDs should not change"
        print("✓ No data mutation detected")
        
        # Verify idempotency (same event_id on re-append would be deduplicated at scheduler level)
        print("\n=== Idempotency Check ===")
        id1 = _compute_event_id("2026-09-04", 0, 1, "RETRY_PENDING")
        id2 = _compute_event_id("2026-09-04", 0, 1, "RETRY_PENDING")
        assert id1 == id2, "Event ID should be deterministic"
        print(f"✓ Event IDs are deterministic (same hash for same inputs): {id1}")
        
        print("\n✅ All verifications passed!")
        print(f"\n📊 Registry file: {registry_path}")
        print(f"   Size: {registry_path.stat().st_size} bytes")
        print(f"   Records: {len(records_reread)}")


if __name__ == "__main__":
    verify_retry_sequence_scenario()

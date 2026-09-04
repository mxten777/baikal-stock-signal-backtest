# Daily Operations Safety Invariants

These invariants define the minimum safety contract for the STEP 6 daily operation and STEP 7 scheduler.

1. Historical data is immutable. Operational tests use temporary fixtures and must not alter source history, ledgers, validation artifacts, or manifests.
2. Integrity `FAIL` cannot be bypassed. It produces `BLOCKED`, stops the pipeline, and requires integrity review.
3. Only one Daily Operation may run at a time. Scheduler and manual execution both honor the daily run lock.
4. Retry is bounded. Scheduler retries are limited to the configured slots; a dashboard pipeline transient may receive at most one in-attempt retry.
5. Terminal states do not auto-rerun. `SUCCESS`, `SUCCESS_WITH_WARNING`, `BLOCKED`, `FAILED`, and `NON_TRADING_DAY` are terminal for the target date.
6. Manual Run is capability-gated. It is available only for `FAILED` with `MANUAL_RERUN_ALLOWED` and no completed manual audit.
7. Registry is append-only. Records are never edited or deleted, and deterministic event IDs make repeated appends idempotent.
8. Scheduler state cannot erase history. A missing or corrupt state file is recovered from registry or manifest without converting unresolved history into success.
9. Registry failure cannot duplicate Daily Operation. Registry persistence is ancillary to the execution lock and terminal state guard.
10. `NON_TRADING_DAY` does not execute Daily Operation and repeated polling does not create additional events.
11. Unknown failures fail closed. Unclassified failures become `FAILED` and are not automatically retried.
12. No automatic action may convert `BLOCKED` or `FAILED` history to success. Any manual success is recorded separately and reconciled only with matching manifest and run ID.

## Verification

`tests/test_step7_8_operational_safety.py` exercises the invariants through temporary repositories and public scheduler, registry, and operations APIs. Historical fixture files are compared by SHA-256 before and after the concurrency/lock check.

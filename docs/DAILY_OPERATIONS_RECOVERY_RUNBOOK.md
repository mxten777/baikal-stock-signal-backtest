# Daily Operations Recovery Runbook

This runbook covers the STEP 7 scheduler layer. The STEP 6 Daily Operation command remains unchanged.

## Status Actions

- `SUCCESS`: no action.
- `SUCCESS_WITH_WARNING`: review the warning; no automatic rerun.
- `RETRY_PENDING`: wait for the next scheduled slot.
- `BLOCKED`: inspect the input or integrity cause. Do not force automatic execution.
- `FAILED`: inspect the recorded error and decide whether a manual rerun is appropriate.
- `NON_TRADING_DAY`: normal; no action.

The scheduler state is the current operational authority. The JSONL registry is immutable audit history. A repeated event id is ignored so crash recovery cannot duplicate an attempt record.

## Manual Daily Run

Official command:

```powershell
python -m scripts.daily_operational_run --json
```

A manual run does not silently change a prior scheduler `BLOCKED` or `FAILED` state to `SUCCESS`. Review the result and reconcile the scheduler state through the normal operational process before the next automated decision.

## Operator Action Codes

- `CHECK_INPUT_DATA`: inspect missing, partial, future, or malformed input data.
- `CHECK_INTEGRITY`: inspect the Input Integrity Gate failure.
- `CHECK_APPLICATION_ERROR`: inspect the application or scheduler error.
- `MANUAL_RERUN_ALLOWED`: review the failure, then use the official command if appropriate.
- `DO_NOT_RERUN`: do not retry a fatal or structurally unsafe failure automatically.

## Never Do This

- Do not edit or delete registry JSONL records.
- Do not force a `BLOCKED` or fatal `FAILED` attempt through the scheduler.
- Do not rerun the Daily Operation merely because registry append failed.
- Do not modify historical market or investor rows to make readiness pass.
- Do not change the scheduler slots or bypass the existing Daily Run lock.

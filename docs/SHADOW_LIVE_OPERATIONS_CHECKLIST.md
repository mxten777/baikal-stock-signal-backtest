# Shadow Live Operations Checklist

This checklist is for the first automated shadow-live trading day. It observes real data and runtime artifacts only; it never places orders or bypasses an integrity gate.

## Before First Live Day

- [ ] Confirm branch and working tree; do not reset or clean STEP 7 artifacts.
- [ ] Confirm Python regression, frontend tests, and frontend build are green.
- [ ] Confirm Windows Task Scheduler task `BAIKAL Stock Daily Scheduler` is enabled and invokes `python -m scripts.daily_scheduler --json` from the repository root.
- [ ] Record `LastRunTime`, `LastTaskResult`, `NextRunTime`, and `NumberOfMissedRuns`.
- [ ] Read scheduler state, registry, manifest, data dates, health, and dashboard status without editing runtime artifacts.
- [ ] Confirm no manual run is active or queued.

## 18:30 Check

- [ ] Record target trade date, scheduler status, attempt, and action.
- [ ] Confirm market and investor readiness and whether the Daily Operation ran.
- [ ] If `SUCCESS` or `SUCCESS_WITH_WARNING`, continue observation without rerun.
- [ ] If `RETRY_PENDING`, record reason and `next_retry_at`; wait for the next slot.
- [ ] If `BLOCKED`, hold and inspect failed phase and integrity details.
- [ ] If `FAILED`, classify the failure; do not rerun unless the capability explicitly allows it.
- [ ] If `NON_TRADING_DAY`, record a normal no-run and stop for the day.

## 19:00 Check

- [ ] Confirm terminal states are unchanged and no duplicate operation was started.
- [ ] For `RETRY_PENDING`, confirm attempt and readiness before the retry.
- [ ] Record registry event and manifest/run ID, if present.

## 19:30 Check

- [ ] Repeat the 19:00 state, idempotency, and evidence checks.
- [ ] Confirm retry count remains within the four-attempt bound.

## 20:00 Check

- [ ] Confirm the final retry decision and terminal or pending state.
- [ ] Confirm no operation runs after a terminal result.

## 20:30 Final Check

- [ ] Confirm final status is `SUCCESS`, `SUCCESS_WITH_WARNING`, `BLOCKED`, or `FAILED`.
- [ ] Confirm dashboard status matches scheduler state, registry, and manifest.
- [ ] Confirm `operator_action_required` matches the final status.
- [ ] Compare historical artifacts before and after; only new-day append activity is allowed.
- [ ] Record the final judgment and all evidence in the validation report.

## Warning Procedure

For `SUCCESS_WITH_WARNING`, record the warning source, integrity result, pipeline completion, and operator action. Do not rerun when the warning is within the documented allowed range.

## Blocked Procedure

Set the day to `HOLD`. Inspect failed phase, integrity details, action code, and input dates. Do not force a run, bypass integrity, or edit state or registry.

## Failed Procedure

Classify retry exhaustion, application error, concurrency, or unknown/fatal failure. Manual rerun is permitted only when the system reports `MANUAL_RERUN_ALLOWED` and the Dashboard capability check agrees.

## Manual Run Procedure

Use the documented manual operation only for an explicitly allowed `FAILED` state. Record the audit event, reconcile the resulting manifest/run ID, and never use manual execution to replace an automatic retry or bypass a block.

## End-of-Day Sign-off

- Trade date:
- Final status:
- Registry and manifest consistent: Yes / No
- Dashboard consistent: Yes / No
- Historical mutation: None / Found
- Operator action required:
- Final judgment: PASS / PASS WITH WARNING / HOLD
- Operator and timestamp:
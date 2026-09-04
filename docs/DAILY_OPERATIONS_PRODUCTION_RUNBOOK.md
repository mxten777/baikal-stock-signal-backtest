# Daily Operations Production Runbook

**Branch:** main  
**HEAD:** 983486912670ff27f2b81d6663466d79ba57baa2  
**Effective Date:** 2026-09-04  
**Status:** Production Ready (STEP 7-9)

This runbook provides operators with complete guidance for safe, daily execution of the BAIKAL Stock Signal Dashboard's automated operations system.

---

## Table of Contents

1. [Purpose & Scope](#purpose--scope)
2. [Production Architecture](#production-architecture)
3. [Production Commands](#production-commands)
4. [Scheduler Invocation](#scheduler-invocation)
5. [Daily Timeline](#daily-timeline)
6. [Dashboard Operations](#dashboard-operations)
7. [Status Response Matrix](#status-response-matrix)
8. [Manual Run Procedure](#manual-run-procedure)
9. [Runtime Artifacts](#runtime-artifacts)
10. [Restart & Recovery](#restart--recovery)
11. [Failure Response Matrix](#failure-response-matrix)
12. [Operator Prohibited Actions](#operator-prohibited-actions)
13. [Daily Checklist](#daily-checklist)
14. [Weekly & Monthly Maintenance](#weekly--monthly-maintenance)
15. [Production Readiness Gaps](#production-readiness-gaps)
16. [Shadow Live Entry Criteria](#shadow-live-entry-criteria)
17. [Emergency Quick Reference](#emergency-quick-reference)

---

## Purpose & Scope

The BAIKAL Daily Operations system executes the following on each trading day:

1. **Market Data Update** – Fetch latest equity prices from configured data sources
2. **Investor Data Update** – Fetch latest institutional investor positions  
3. **Input Integrity Gate** – Validate data readiness and consistency
4. **Dashboard Pipeline** – Generate signal ledger, shadow performance, and summary reports
5. **Health Check** – Verify system health and operational artifacts

**Operator Role:**  
Monitor and ensure safe daily execution. The system is designed to fail safely: most failures are either automatically retried or explicitly require operator review before manual action.

**Scope:**  
This runbook covers the STEP 7 Scheduler Layer (one-tick scheduler and operations management). The STEP 6 Daily Orchestrator and underlying pipeline components remain unchanged.

---

## Production Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  External Scheduler (Windows Task Scheduler / cron / etc.)   │
│  Invokes scheduler tick at: 18:30, 19:00, 19:30, 20:00 KST  │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 7 Scheduler Layer                                      │
│  python -m scripts.daily_scheduler --json                   │
│                                                              │
│  - Decide target trade date (Asia/Seoul based)              │
│  - Check market/investor data readiness (read-only probe)   │
│  - Invoke STEP 6 Daily Orchestrator (if ready)              │
│  - Record outcome in registry (append-only)                 │
│  - Save scheduler state (crash recovery)                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  STEP 6 Daily Orchestrator                                   │
│  (unchanged from previous steps)                            │
│                                                              │
│  ├─ Market Update (Safe Market Updater)                     │
│  ├─ Investor Update (Safe Investor Updater)                 │
│  ├─ Input Integrity Gate                                    │
│  ├─ Dashboard Pipeline (Signal / Shadow / Summary)          │
│  └─ Save manifest (daily_operational_run.json)              │
└─────────────────────────────────────────────────────────────┘
```

**Key Facts:**
- **Timezone:** All scheduling, state, and operator decisions use **Asia/Seoul (UTC+09:00)**
- **Daily Schedule:** Four fixed time slots: 18:30, 19:00, 19:30, 20:00 KST
- **Max Retries:** 3 (first run + 3 retries = 4 attempts maximum)
- **Grace Period:** 30 minutes after 20:00 slot; after 20:30, window closes
- **Concurrency:** Only one Daily Operation may run at a time (scheduler + manual both check locks)
- **External Scheduler:** Windows Task Scheduler is configured as `BAIKAL Stock Daily Scheduler`; verify its live task metadata before each first-live-day acceptance.

---

## Production Commands

### 1. Scheduler One Tick

**Official Command:**
```powershell
python -m scripts.daily_scheduler --json
```

**Options:**
- `--json` – Print result as JSON (recommended for production)
- `--now <ISO-8601>` – Inject custom scheduler clock (testing only; manifest keeps real UTC times)

**Output:**
Structured JSON with scheduler decision, status, attempt, next retry time, and any error details.

**Exit Code:**
- `0` – Success (including `SUCCESS_WITH_WARNING`, `RETRY_PENDING`, `NON_TRADING_DAY`)
- `1` – `BLOCKED` or `FAILED` (requires operator review)

**Example:**
```powershell
python -m scripts.daily_scheduler --json
```

Expected output on success:
```json
{
  "action": "OPERATION_COMPLETED",
  "attempt": 1,
  "last_daily_status": "SUCCESS",
  "scheduler_status": "SUCCESS",
  "target_trade_date": "2026-09-04",
  "timezone": "Asia/Seoul"
}
```

---

### 2. Manual Daily Operation

**Official Command:**
```powershell
python -m scripts.daily_operational_run --json
```

**Options:**
- `--json` – Print result as JSON
- `--disallow-source-lag` – Require up-to-date data (normally tolerates lag)

**Output:**
Structured JSON with run ID, phases, warnings, errors, and final status.

**Exit Code:**
- `0` – SUCCESS (including `SUCCESS_WITH_WARNING`)
- `1` – FAILED

**Use Only When:**
1. Scheduler status is `FAILED`
2. `operator_action_code` is `MANUAL_RERUN_ALLOWED`
3. Dashboard `/api/operations/manual-run` endpoint confirms capability
4. Operator has reviewed the failure reason and determined rerun is safe

---

### 3. Backend API Server

**Command:**
```powershell
python -m dashboard.api
```

**Default:**
- Host: `127.0.0.1`
- Port: `8765`
- URL: `http://127.0.0.1:8765`

**Endpoints (Read-Only):**
- `/api/dashboard/overview` – Current signal summary
- `/api/dashboard/signals` – Signal ledger
- `/api/dashboard/health` – System health

**Endpoints (Operations):**
- `/api/operations/status` – Current scheduler state
- `/api/operations/history` – All operation history
- `/api/operations/exceptions` – Exceptions and warnings
- `POST /api/operations/manual-run` – Trigger manual operation (gated by capability check)

---

### 4. Frontend Development Server

**Command:**
```powershell
cd dashboard/frontend
npm run dev
```

**Default:**
- URL: `http://localhost:5173`

---

### 5. Frontend Production Build

**Command:**
```powershell
cd dashboard/frontend
npm run build
```

**Output:**
- Compiled files → `dist/`
- Can be served via static web server

---

## Scheduler Invocation

### Current Status

**Current verified state (2026-09-04):** Windows Task Scheduler task `BAIKAL Stock Daily Scheduler` is configured with four daily triggers at 18:30, 19:00, 19:30, and 20:00 KST. The task invokes the project virtual-environment Python with `-m scripts.daily_scheduler --json` from the repository root. The task metadata reported `LastTaskResult = 0` and `NumberOfMissedRuns = 0` during STEP 7-10 validation.

The scheduler remains a one-tick design: each invocation performs one atomic decision. Re-verify the task action, working directory, trigger times, and last result after any host or virtual-environment change.

The scheduler is a **one-tick** design: each invocation performs one atomic decision. The system does not contain built-in scheduler daemon or cron configuration.

### Recommended Setup

For production on **Windows**, use **Windows Task Scheduler**:

#### Step 1: Create Scheduler Task

Open Task Scheduler (taskschd.msc) and create a new task:

**General Tab:**
- Name: `BAIKAL Daily Scheduler 18:30`
- Run with highest privileges: ✓
- Hidden: ✓
- Run whether user is logged in or not: ✓

**Triggers Tab:**
Create 4 triggers (one per slot):

| Trigger | Time | Recurrence |
|---------|------|-----------|
| 1 | 18:30 | Daily |
| 2 | 19:00 | Daily |
| 3 | 19:30 | Daily |
| 4 | 20:00 | Daily |

**Actions Tab:**

```
Program: C:\path\to\python.exe
Arguments: -m scripts.daily_scheduler --json >> C:\path\to\logs\scheduler.log 2>&1
Start in: C:\path\to\repository\
```

Or, using the project's virtual environment:

```
Program: C:\path\to\.venv\Scripts\python.exe
Arguments: -m scripts.daily_scheduler --json >> C:\path\to\logs\scheduler.log 2>&1
Start in: C:\path\to\repository\
```

**Conditions Tab:**
- Idle: Uncheck (run regardless of system idle state)
- Power: Uncheck (run on battery if needed)

#### Step 2: Verify Setup

Open Command Prompt and manually test:

```powershell
cd C:\path\to\repository
python -m scripts.daily_scheduler --json
```

Confirm JSON output (no errors).

#### Step 3: Verify Logs

After first automatic run, check:
```
C:\path\to\logs\scheduler.log
```

Expected output: JSON result from scheduler tick.

### Alternative: Linux / Mac with cron

```bash
# Edit crontab
crontab -e

# Add 4 entries (KST = UTC+9, adjust for your timezone):
# Assuming server is in KST:
30 18 * * 1-5 cd /path/to/repo && python -m scripts.daily_scheduler --json >> logs/scheduler.log 2>&1
0  19 * * 1-5 cd /path/to/repo && python -m scripts.daily_scheduler --json >> logs/scheduler.log 2>&1
30 19 * * 1-5 cd /path/to/repo && python -m scripts.daily_scheduler --json >> logs/scheduler.log 2>&1
0  20 * * 1-5 cd /path/to/repo && python -m scripts.daily_scheduler --json >> logs/scheduler.log 2>&1
```

---

## Daily Timeline

### Before Market Close (~15:30 KST)

**No operator action required.**

System is idle. Market and investor data are being updated by external sources.

### 18:30 KST – First Attempt

**External Scheduler triggers:**
```
python -m scripts.daily_scheduler --json
```

**Scheduler decides:**
1. Is today a trading day? (Korean market calendar)
2. Are market and investor data ready?
3. If yes, run Daily Operation
4. Record outcome in state and registry

**Expected outcomes:**
- **SUCCESS** → Operation completed; audit log saved
- **SUCCESS_WITH_WARNING** → Completed; check warning details
- **RETRY_PENDING** → Data not ready; await automatic retry at 19:00
- **BLOCKED** → Integrity check failed; operator review required
- **FAILED** → Operation failed; operator review required
- **NON_TRADING_DAY** → Scheduled job is no-op; normal

### 19:00 KST – First Retry (if needed)

**External Scheduler triggers again.**

If previous status is `RETRY_PENDING`, scheduler retries. Otherwise, scheduler confirms terminal state.

### 19:30 KST – Second Retry (if needed)

Similar to 19:00.

### 20:00 KST – Final Retry (if needed)

Final slot. After this:
- If success → done
- If still retrying → becomes `FAILED` / `RETRY_EXHAUSTED`
- If already terminal → no change

### 20:30 KST – Window Closes

30 minutes after final slot. If operation never ran, status becomes `FAILED` / `MISSED_RUN`.

### 20:30 – Next Trading Day

System enters idle state until next trading day 18:30 KST.

---

## Dashboard Operations

### Accessing Operations Dashboard

1. Start backend API:
   ```powershell
   python -m dashboard.api
   ```

2. Start frontend (development) or open static build:
   ```powershell
   cd dashboard/frontend && npm run dev
   ```
   
   Or visit: `http://127.0.0.1:8765` (backend serves static frontend if built)

3. Navigate to **Operations** tab

### Operations Tab Layout

**Current Status Card:**
- Trade date
- Scheduler status (SUCCESS, RETRY_PENDING, etc.)
- Attempt number
- Last run time
- Next retry time (if applicable)

**Data Context:**
- Target trade date
- Latest market data date
- Latest investor data date
- Integrity status

**Pipeline Status:**
- Phase results (Market Update, Investor Update, Gate, Pipeline)
- Phase status (SUCCESS, FAILED, etc.)
- Error codes and messages

**Exception Detail (if present):**
- Severity (INFO, WARNING, BLOCKING, ERROR)
- Affected components
- Operator action code and guidance

**Manual Run Control:**
- Enabled only if status=`FAILED` and code=`MANUAL_RERUN_ALLOWED`
- Shows confirmation requirement

### Typical Operation Procedure

1. **Access Operations tab** (on frontend)
2. **Check Current Status:**
   - Is it terminal (SUCCESS, SUCCESS_WITH_WARNING, BLOCKED, FAILED, NON_TRADING_DAY)?
   - Is it retrying (RETRY_PENDING)?
3. **If SUCCESS or SUCCESS_WITH_WARNING:**
   - Review warning if present
   - No operator action needed
   - Proceed with end-of-day process
4. **If RETRY_PENDING:**
   - Check Next Retry Time
   - Wait for automatic retry
   - Do not manually run
5. **If BLOCKED:**
   - Review Exception Detail
   - Check Affected Components (usually Integrity)
   - Manually resolve blocking condition
   - Do NOT force a manual run
6. **If FAILED:**
   - Review Exception Detail
   - Check Operator Action Code
   - If code = `MANUAL_RERUN_ALLOWED` and manual run is enabled:
     - Click **Manual Run**
     - Confirm the action
     - Wait for completion
   - If code = `DO_NOT_RERUN` or `CHECK_*`:
     - Resolve the issue manually
     - Await next trading day or escalate

---

## Status Response Matrix

| Status | Meaning | Auto-Retry | Manual Run | Operator Action | Next Step |
|--------|---------|-----------|-----------|-----------------|-----------|
| **SUCCESS** | Operation completed without issues | No | No | None | Monitor end-of-day; proceed with normal workflow |
| **SUCCESS_WITH_WARNING** | Completed but detected data/integrity warning | No | No | Review warning; no rerun needed | Verify data quality; proceed with workflow |
| **RETRY_PENDING** | Data not ready; awaiting next slot | Yes (auto) | No (forbidden) | Wait for 19:00 / 19:30 / 20:00 | Monitor next slot result |
| **BLOCKED** | Integrity gate failed; pipeline not executed | No | No (forbidden) | Review and resolve blocking condition | Fix input data/integrity; escalate if structural |
| **FAILED** | Operation execution failed | No | Depends on action_code | Review error; decide manual rerun | If MANUAL_RERUN_ALLOWED: use Dashboard manual run. Otherwise: debug and escalate |
| **NON_TRADING_DAY** | Scheduler ran but today is not a trading day | No | No | None | Normal; system idle until next trading day |

---

## Manual Run Procedure

### Prerequisites

Manual run is **only available** when ALL conditions are met:

1. **Scheduler Status** = `FAILED`
2. **Operator Action Code** = `MANUAL_RERUN_ALLOWED`
3. **No Prior Manual Run** completed for this trade date (checked against registry)
4. **Dashboard Manual Run Control** shows "Enabled"
5. **No Concurrent Run** (neither scheduler nor another manual run active)

### Step-by-Step

1. **Access Operations Dashboard**
   - Navigate to `/operations` or **Operations** tab
   - Verify current status (must be `FAILED`)

2. **Review Exception Details**
   - Click **Exception Detail** or **Expand**
   - Read error code, message, and operator guidance
   - Verify the error is transient or safely recoverable

3. **Verify Manual Run Capability**
   - Check **Manual Run** button state
   - If greyed out: reason is displayed in Operator Guidance section
   - Read the reason

4. **Confirm Manual Run (if enabled)**
   - Button label: **"Manual Run"** (not greyed)
   - Click the button
   - System shows confirmation dialog:
     ```
     "Confirm manual operation for trade date: YYYY-MM-DD?
      This will execute the Daily Operation outside the normal retry schedule."
     ```

5. **Approve**
   - Click **Confirm** (or equivalent)
   - System executes `python -m scripts.daily_operational_run --json`
   - Operation runs in foreground (blocking)

6. **Monitor Execution**
   - Dashboard shows progress
   - Phases execute sequentially: Market → Investor → Gate → Pipeline
   - Wait for completion

7. **Verify Result**
   - Check final status (SUCCESS, SUCCESS_WITH_WARNING, or FAILED)
   - If SUCCESS: operation complete; next scheduler tick will reconcile
   - If SUCCESS_WITH_WARNING: review warnings; next scheduler tick reconciles
   - If FAILED: operation still failed; escalate or await operator root-cause analysis

8. **Scheduler Reconciliation (next tick)**
   - On next scheduler invocation (19:00 / 19:30 / 20:00), scheduler will:
     - Detect manual run audit in registry
     - Compare manifest run_id with registry record
     - If match: adopt the new status in state
     - If no match: leave state unchanged (audit mismatch)

### Important Constraints

**Do NOT:**
- Force manual run when `BLOCKED` (structural failure)
- Force manual run when `CHECK_INPUT_DATA` code (resolve data first)
- Manually modify input data to make manual run pass
- Run manual operation multiple times for same trade date
- Change target date or use `--disallow-source-lag` flag
- Use force/override options in the command

---

## Runtime Artifacts

### Scheduler Layer Artifacts

| File | Location | Purpose | Created By | Operator Editable? | Operator Deletable? |
|------|----------|---------|-----------|------------------|------------------|
| **Scheduler State** | `output/daily_scheduler_state.json` | Current scheduler state (status, attempt, retry time, target date) | Scheduler tick | ❌ No | ❌ No (recovery ignores missing file) |
| **Scheduler Registry** | `output/daily_run_registry.jsonl` | Append-only audit log of all scheduler decisions and manual runs | Scheduler tick / Manual run | ❌ No | ❌ No (immutable audit) |
| **Scheduler Lock** | `output/daily_scheduler.lock` | Concurrent tick prevention (auto-expires after 15 minutes) | Scheduler tick | ❌ No | ⚠️ Only if stale (>15 min old) |

### Daily Operation Artifacts

| File | Location | Purpose | Created By | Operator Editable? | Operator Deletable? |
|------|----------|---------|-----------|------------------|------------------|
| **Operation Manifest** | `output/daily_operational_run.json` | Daily operation result: phases, status, metrics, run_id, timestamps | Daily orchestrator | ❌ No | ❌ No (history) |
| **Operation Lock** | `output/daily_operational_run.lock` | Concurrent operation prevention (auto-expires after 12 hours) | Daily orchestrator | ❌ No | ⚠️ Only if stale (>12 hours old) |

### Key Principles

1. **Scheduler State:** Do not edit manually. If corrupted or missing, recovery uses registry or manifest.
2. **Registry:** Never edit or delete records. Append-only design prevents rewriting history.
3. **Locks:** Stale locks (>15 min for scheduler, >12 hours for operation) may be safely deleted after verifying no process is running.
4. **Manifest:** Historical record. Do not edit to force a different result.

---

## Restart & Recovery

### Scenario A: PC/Server Restart Before 18:30 KST

**What Happens:**
- System boots; all state files are present from previous day
- Scheduler first tick occurs at 18:30 (normal slot)
- Scheduler detects existing state for previous trade date
- If previous trade date is terminal (SUCCESS, BLOCKED, FAILED, etc.), state is locked (no rerun)
- New trade date begins at 18:30

**Operator Action:** None.

---

### Scenario B: Restart Between Retry Slots (e.g., 19:10)

**What Happens:**
- System boots
- Scheduler state shows RETRY_PENDING
- Next scheduled slot (19:30 or 20:00) will invoke scheduler normally
- Scheduler re-reads state from file and continues retry countdown

**Operator Action:** None. Next slot will auto-retry.

---

### Scenario C: Restart After Daily Operation Succeeds (20:05)

**What Happens:**
- System boots
- Scheduler state shows SUCCESS or SUCCESS_WITH_WARNING
- State is terminal; no auto-retry
- Scheduler remains in this state for auditing
- Next trading day begins fresh at 18:30

**Operator Action:** None.

---

### Scenario D: Missing or Corrupted Scheduler State

**What Happens:**
- Scheduler tick runs but state file is unreadable/missing
- Scheduler attempts recovery:
  1. Reads registry (append-only log) for matching trade date
  2. If found: reconstructs state from latest registry event
  3. If not found: checks if Daily Operation manifest exists and is SUCCESS (crash guard after state loss)
  4. If no registry or manifest: assumes new trading day, begins fresh

**Operator Action:** None. Recovery is automatic and fail-safe.

---

### Scenario E: Malformed Registry Tail (Partial Write)

**What Happens:**
- Registry append was interrupted (power loss, crash)
- Last line may be incomplete JSON
- Scheduler reads registry, skips malformed final line
- Registry read continues with earlier records (append-only guarantee)

**Operator Action:** None. Malformed tail is ignored; recovery continues.

---

### Scenario F: Stale Locks

**Condition:** Lock file exists but is >15 min old (scheduler) or >12 hours old (operation).

**Recovery (Manual):**

Check process status:
```powershell
# Windows: check if python processes are running
Get-Process python | Where-Object { $_.CommandLine -like "*daily_scheduler*" }

# Linux: check if python processes are running
ps aux | grep daily_scheduler
```

If no matching process is running:

```powershell
# Safe to remove stale scheduler lock (15 min old)
Remove-Item output/daily_scheduler.lock -Force

# Safe to remove stale operation lock (12 hours old)
Remove-Item output/daily_operational_run.lock -Force
```

**After removing:** Next scheduler invocation (18:30 / 19:00 / etc.) will acquire lock normally.

**Operator Action:** 
1. Confirm no process is running
2. Delete stale lock file
3. Trigger next scheduler tick manually (optional):
   ```powershell
   python -m scripts.daily_scheduler --json
   ```

---

## Failure Response Matrix

### Failure Types

| Category | Examples | Auto-Retry? | Manual Action? | Escalation? |
|----------|----------|-----------|-----------|-----------|
| **DATA_NOT_READY** | Market/investor data lag | ✓ Yes (retry slots) | None | Only if lag persists after 20:00 |
| **SOURCE_LAG** | Data source slow to publish | ✓ Yes (retry slots) | None | Escalate if lag > 24 hours |
| **CONCURRENT_RUN** | Another scheduler/manual already running | ❌ No (lock blocks) | Wait for current run; retry next slot | Only if lock is stale |
| **INPUT_GATE_WARNING** | Integrity gate warning (minor) | ❌ No | Review; no rerun needed | None (warning is not blocking) |
| **INTEGRITY_FAIL** | Integrity gate failure (structural) | ❌ No (blocking) | Resolve condition; no force | Escalate; investigate data |
| **RETRY_EXHAUSTED** | All 4 attempts failed | ❌ No | Review root cause | Escalate (may indicate persistent issue) |
| **PROGRAMMING_ERROR** | TypeError, ImportError, etc. | ❌ No (fatal) | Debug and fix code | Escalate (code defect) |
| **REGISTRY_WRITE_WARNING** | Registry append failed (non-fatal) | ✓ Yes (operation ran) | Review audit trail | Escalate if registry corruption suspected |
| **STATE_CORRUPTION** | State file corrupted/malformed | ✓ Yes (recovery) | Manual recovery possible | Escalate if recovery fails |
| **STALE_LOCK** | Lock >15 min (scheduler) or >12 hrs (operation) | ❌ No (lock blocks) | Remove stale lock; retry | None (automated if lock verified stale) |

---

## Operator Prohibited Actions

### NEVER Do These

1. **Edit Registry JSONL Records**
   - ❌ Manually add/remove/modify lines in `output/daily_run_registry.jsonl`
   - ❌ Change event types, statuses, or dates in registry
   - **Reason:** Breaks audit trail and crash recovery
   - **If corruption occurs:** Contact developer; restore from backup

2. **Forcibly Change Scheduler State to SUCCESS**
   - ❌ Manually edit `output/daily_scheduler_state.json` to set `current_status: SUCCESS`
   - ❌ Delete state file to "reset" status
   - **Reason:** Bypasses safety checks (Integrity, retry limits)
   - **Safe alternative:** Use Dashboard manual run (only if FAILED + MANUAL_RERUN_ALLOWED)

3. **Modify Historical CSV or Market Data**
   - ❌ Edit `data/raw/` or `data/investor/` to backfill missing data
   - ❌ Change published dates to make input gate pass
   - **Reason:** Violates immutability; breaks downstream analytics
   - **Safe alternative:** Wait for external data provider to publish; next day's data will reconcile

4. **Delete Manifest to Force Rerun**
   - ❌ Remove `output/daily_operational_run.json` to trigger re-execution
   - **Reason:** Breaks manifest recovery and audit history
   - **Safe alternative:** Use Dashboard manual run (if eligible)

5. **Delete Lock Files Arbitrarily**
   - ❌ Remove `output/daily_scheduler.lock` when scheduler is still running
   - ❌ Remove `output/daily_operational_run.lock` when operation is still running
   - **Reason:** Enables concurrent runs (data corruption risk)
   - **Safe alternative:** Verify process is stopped; then delete only if lock is confirmed stale (>15 min or >12 hrs)

6. **Force BLOCKED Status to Run**
   - ❌ Attempt to bypass Integrity gate failure
   - ❌ Use scheduler `--force` or `--override` flags (if added in future)
   - **Reason:** Structural safety failure; data is not ready
   - **Safe alternative:** Fix the integrity issue (resolve data gaps, consistency); next retry will pass

7. **Force RETRY_PENDING to Manual Run**
   - ❌ Attempt manual run when status is RETRY_PENDING
   - **Reason:** May create duplicate audit or confuse retry schedule
   - **Safe alternative:** Wait for next automatic retry slot

8. **Modify Target Trade Date**
   - ❌ Run scheduler with manual `--now` injection to change trade date in production
   - ❌ Edit manifest to claim a different trade date
   - **Reason:** Breaks daily operation semantics and audit trail
   - **Safe alternative:** Historical reruns require explicit manual procedure (outside this runbook)

9. **Use `--disallow-source-lag` in Production**
   - ❌ Run manual operation with `--disallow-source-lag` flag
   - **Reason:** Overrides designed retry tolerance; may fail unnecessarily
   - **Safe alternative:** Normal manual run (without flag) respects source lag tolerance

10. **Assume State from Absence**
    - ❌ Assume operation "didn't run" because manifest is missing
    - ❌ Assume status is "SUCCESS" because no error file exists
    - **Reason:** Absence does not equal success
    - **Safe alternative:** Check scheduler state and registry; recovery reconstructs state from audit trail

---

## Daily Checklist

Use this checklist on each trading day. On **SUCCESS** days, this should take <1 minute.

```
Scheduler Invocation (18:30 KST)
[ ] Scheduler process started
[ ] JSON output received (check logs)

Current Status (18:35 KST)
[ ] Navigate to /operations dashboard
[ ] Verify Current Status card displays correct target trade date
[ ] Check scheduler status (SUCCESS, RETRY_PENDING, BLOCKED, FAILED, NON_TRADING_DAY)

Data Context
[ ] Latest Market Date = today or today-1 (expected range)
[ ] Latest Investor Date = today or today-1 (expected range)
[ ] Integrity Status = PASS or PASS_WITH_WARNING (not FAIL)

Pipeline Status
[ ] Market Update = SUCCESS
[ ] Investor Update = SUCCESS
[ ] Input Gate = PASS or PASS_WITH_WARNING
[ ] Dashboard Pipeline = SUCCESS or WARNING

Exception Review (if present)
[ ] Read Exception Detail
[ ] Check Affected Components
[ ] Verify Operator Action Code
[ ] If MANUAL_RERUN_ALLOWED: decide if rerun is appropriate

Final Status (20:05 KST, after window closes)
[ ] Check scheduler status is terminal
[ ] If SUCCESS or SUCCESS_WITH_WARNING: operation complete, proceed
[ ] If BLOCKED or FAILED: escalate if needed
[ ] If RETRY_PENDING: note that retry was not possible (should not occur after 20:00)

Daily Operation Summary
[ ] Document any warnings, exceptions, or manual actions
[ ] Archive logs if needed
[ ] Prepare end-of-day report
```

---

## Weekly & Monthly Maintenance

### Weekly (Every Monday or next trading day)

1. **Review Failed/Blocked History**
   - Access `/operations/history`
   - Filter for `BLOCKED` and `FAILED` entries
   - Check if there are recurring patterns
   - Escalate persistent failures to development

2. **Verify Registry Health**
   ```powershell
   # Check file size (should grow ~1-2 KB per day)
   (Get-Item output/daily_run_registry.jsonl).Length
   
   # Check last few lines (ensure no truncation)
   Get-Content output/daily_run_registry.jsonl -Tail 5
   ```

3. **Test Manual Run Procedure (optional)**
   - Manually trigger scheduler tick (even outside trading hours)
   - Verify output format and logging

### Monthly (First day or end of month)

1. **Full System Health Check**
   - Verify all STEP 7 and STEP 6 tests pass:
     ```powershell
     python -m pytest tests/test_daily_scheduler.py -v
     python -m pytest tests/test_daily_run_registry.py -v
     python -m pytest tests/test_operations_api.py -v
     ```
   
2. **Registry Size & Retention**
   ```powershell
   # Check registry size
   (Get-Item output/daily_run_registry.jsonl).Length
   
   # Count records (each operation ~1 JSON line)
   (Get-Content output/daily_run_registry.jsonl | Measure-Object -Line).Lines
   ```
   - Expected: ~20-25 lines/month (trading days + manual runs + retries)
   - If >1 MB: consider archiving older entries (future feature)

3. **Backup State/Registry** (if no automated backup)
   ```powershell
   # Create monthly snapshot
   Copy-Item output/daily_scheduler_state.json backup/state_2026-09.json
   Copy-Item output/daily_run_registry.jsonl backup/registry_2026-09.jsonl
   ```

4. **Dependency & Environment Check**
   - Verify all Python dependencies still installed
   - Verify frontend npm packages up-to-date (optional)
   - Check for security updates

### ⚠️ Important: KRX Holiday Calendar

**Current embedded calendar coverage: through February 2027**

The STEP 7 scheduler uses `scripts/korean_market_calendar.py` to determine trading days. This embedded calendar is static and covers through 2027-02.

**Action Required Before 2027-03-01:**

Update the KRX holiday calendar. Process:
1. Verify with [KRX official holidays](https://www.krx.co.kr/por/bbs/all/286)
2. Update `scripts/korean_market_calendar.py` holidays dict
3. Re-run tests to verify
4. Commit and deploy before month boundary

**Failure to update:**
- Scheduler may run on declared holidays (false positive)
- Scheduler may skip actual trading days (false negative)
- This MUST be corrected before production operation beyond 2027-02

---

## Production Readiness Gaps

This section documents known gaps between current implementation and production requirements. **P0** gaps must be resolved before Shadow Live operation. **P1** gaps improve stability. **P2** gaps are nice-to-have.

### P0 (Blocking for Shadow Live)

| Gap | Impact | Mitigation / Owner |
|-----|--------|-------------------|
| **External OS Scheduler Configuration Drift** | Scheduler may stop invoking or use the wrong runtime | Verify the registered Windows Task Scheduler action, working directory, triggers, and `LastTaskResult` before first live day. |
| **KRX Calendar Future Maintenance** | After 2027-02, scheduler may misclassify trading days | Update `scripts/korean_market_calendar.py` before 2027-03-01. Set calendar update reminder in calendar. |
| **Dashboard/API Process Supervision** | Dashboard availability may require manual restart after a host or process failure | Use OS-level supervision (e.g., Windows Service, systemd, supervisord) if continuous Dashboard availability is required. This is outside the one-tick automated daily-operation acceptance gate. |

### P1 (Stability Improvements)

| Gap | Impact | Mitigation / Future |
|-----|--------|-------------------|
| **Centralized Logging** | Logs scattered in output/ or stdout | Implement structured logging (e.g., JSON logs to file). Separate task. |
| **Dashboard Authentication** | Backend API is open (127.0.0.1 only; behind firewall). No user auth. | Add simple token/key auth if exposing to wider network. Not needed for localhost. |
| **Alert / Notification System** | Operator must manually monitor dashboard | Add email/Slack alerts on BLOCKED / FAILED. Separate task; integrate with status endpoint. |
| **Artifact Backup / Retention Policy** | No automated backup of state/registry | Implement daily backup to network drive or S3. Separate task. |
| **Manifest Archive / Rotation** | No size limit on daily_operational_run.json | Implement rolling manifest (e.g., daily_operational_run_YYYY-MM-DD.json). Separate task. |

### P2 (Future Enhancements)

| Gap | Impact | Mitigation / Future |
|-----|--------|-------------------|
| **Historical Rerun Support** | Cannot easily rerun past trade dates | Implement historical rerun API and dashboard UI. Separate large task. |
| **Operator Audit Trail** | Manual runs recorded but not tied to operator identity | Add operator name/ID to manual run audit. Separate task. |
| **Scheduler Performance Metrics** | No built-in metrics on scheduler tick duration | Collect and visualize tick timing (market update, gate, pipeline, etc.). Separate task. |
| **Custom Retry Schedule** | Retry slots are fixed 18:30/19:00/19:30/20:00 | Make slots configurable without code change. Separate task; requires policy decision. |

---

## Shadow Live Entry Criteria

### Entry Conditions (All Must Be Met)

**1. Baseline Regression Tests**
- [ ] Python: ≥567 passed
- [ ] Frontend: ≥20 passed
- [ ] Frontend build: PASS

**2. Scheduler & Operations Verified**
- [ ] Scheduler invocation method confirmed (Windows Task Scheduler / cron / other)
- [x] External scheduler configured and tested at the task level; first trading-day invocation remains pending
- [ ] Manual run procedure tested via dashboard
- [ ] Recovery procedures documented and tested (stale lock removal, state corruption)

**3. Safety Invariants Verified**
- [ ] No historical data mutations detected
- [ ] Integrity gate blocks appropriately
- [ ] Retry limits enforced (max 4 attempts)
- [ ] Terminal states do not auto-rerun
- [ ] Manual run capability gated correctly
- [ ] Registry append-only verified

**4. Production Runbook Reviewed**
- [ ] Operator team reviewed all sections
- [ ] Production commands tested (scheduler, manual run, API)
- [ ] Daily timeline and status matrix understood
- [ ] Prohibited actions clearly communicated
- [ ] Emergency procedures walkthrough completed

**5. KRX Calendar Current**
- [ ] Holiday calendar updated if current date >2026-09 and next update needed
- [ ] Calendar coverage extends ≥3 months beyond planned go-live
- [ ] Test with known holidays to verify

**6. P0 Gaps Resolved**
- [x] External scheduler invocation configured; first trading-day invocation acceptance remains pending
- [ ] Optional Dashboard/API process supervision configured if continuous Dashboard availability is required
- [ ] KRX calendar maintenance plan documented

### Entry Confirmation

When ALL criteria are met:

1. **Operator signs off** on Production Readiness
2. **Development confirms** test baseline maintained
3. **Scheduler** executes first automated tick at 18:30 KST on first scheduled trading day
4. **Operator monitors** entire 18:30–20:00 window
5. **Upon SUCCESS:** Proceed to normal operations

### If Criteria Not Met

- Do NOT proceed to Shadow Live
- Document blocking items
- Return to development for remediation
- Revalidate before next attempt

---

## Emergency Quick Reference

### Scheduler Not Invoked at Scheduled Time

**Symptom:** 18:30 passed; no scheduler output in logs; no state file update.

**Possible Causes:**
1. External scheduler not configured or broken
2. Windows Task Scheduler task disabled or deleted
3. cron entry missing or malformed
4. Network/firewall blocking process startup

**Immediate Action:**
1. Manually trigger scheduler:
   ```powershell
   python -m scripts.daily_scheduler --json
   ```
2. Check output; if successful, state file updates
3. Verify external scheduler configuration
4. Escalate to infrastructure if scheduler is broken

---

### Integrity Gate Stuck in BLOCKED

**Symptom:** Status shows `BLOCKED`; Integrity Status = `FAIL`.

**Possible Causes:**
1. Market data missing or inconsistent
2. Investor data missing or inconsistent
3. Schema mismatch (column missing, type change)
4. Date mismatch (data is from wrong day)

**Immediate Action:**
1. Do NOT force a manual run
2. Access dashboard; check affected data
3. Verify latest market date: should be today-1 or today
4. Verify latest investor date: should be today-1 or today
5. Check data quality report (if available) or raw CSV files
6. Once condition resolved, next scheduler tick will retry and likely succeed

---

### All 4 Retry Slots Failed

**Symptom:** Status = `FAILED`, attempt = 4, next_retry_at = null.

**Possible Causes:**
1. Persistent transient issue (network, source lag)
2. Structural issue (data schema, integrity)
3. Application error (code defect)

**Immediate Action:**
1. Check error code and message in Exception Detail
2. If transient: do NOT manually run (will likely fail again); escalate to dev
3. If structural: resolve root cause (data quality, schema); await next trading day
4. If code error: escalate to development; await fix

---

### Manual Run Failed

**Symptom:** Confirmed manual run; but still shows FAILED after completion.

**Possible Causes:**
1. Root cause of original failure still exists
2. Transient network/source issue recurred
3. Manual run was attempted too soon (concurrent operation)

**Immediate Action:**
1. Check error details from manual run result
2. If same error as before: root cause persists; escalate
3. If different error: retry after waiting 5 minutes (allow external sources to recover)
4. If concurrent operation: wait and retry tomorrow

---

### Registry File Corrupted or Growing Too Large

**Symptom:** `output/daily_run_registry.jsonl` unreadable or >10 MB.

**Possible Causes:**
1. Disk write failure; file truncated/partial
2. Registry running since inception (Feb 2024) without rotation
3. Malware or accidental corruption

**Immediate Action:**
1. Check file size:
   ```powershell
   (Get-Item output/daily_run_registry.jsonl).Length
   ```
2. If partially corrupted: delete last few lines if malformed (tail); recover from backup if available
3. If >10 MB: consider archiving (backup) older entries to separate file
4. Verify scheduler still works (reads around corruption)
5. Escalate for long-term rotation policy

---

### Operator Accidentally Deleted Registry

**Symptom:** `output/daily_run_registry.jsonl` is missing or empty.

**Possible Causes:**
1. Accidental `rm` or delete command
2. Disk space cleanup script ran

**Immediate Action:**
1. ❌ **DO NOT manually recreate** – this will create fake audit history
2. **STOP** all scheduler/manual operations immediately
3. Restore from backup (daily backup should exist)
   ```powershell
   Copy-Item backup/registry_2026-09.jsonl output/daily_run_registry.jsonl
   ```
4. If no backup: escalate to dev; state recovery from manifest will be attempted but may be incomplete
5. Re-enable scheduler after restore confirmed

---

### Process Will Not Start (Python Import Error, etc.)

**Symptom:** Scheduler or manual run fails with import error, missing module, or similar.

**Possible Causes:**
1. Virtual environment not activated
2. Dependencies not installed
3. Python path incorrect
4. Code defect after recent deployment

**Immediate Action:**
1. Verify virtual environment:
   ```powershell
   .venv\Scripts\Activate.ps1
   python --version
   ```
2. Reinstall dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Test manually:
   ```powershell
   python -m scripts.daily_scheduler --json
   ```
4. If still fails: escalate to development; check recent commits for breaking changes

---

## Related Documents

- **[DAILY_OPERATIONS_RUN_POLICY_V1.md](DAILY_OPERATIONS_RUN_POLICY_V1.md)** – Operational policy foundation
- **[DAILY_OPERATIONS_SAFETY_INVARIANTS.md](DAILY_OPERATIONS_SAFETY_INVARIANTS.md)** – Safety contract and verification
- **[DAILY_OPERATIONS_RECOVERY_RUNBOOK.md](DAILY_OPERATIONS_RECOVERY_RUNBOOK.md)** – Detailed recovery procedures
- **[DAILY_OPERATIONS_MANUAL.md](DAILY_OPERATIONS_MANUAL.md)** – STEP 6 Daily Operation reference

---

## Document Control

| Item | Value |
|------|-------|
| **Document** | DAILY_OPERATIONS_PRODUCTION_RUNBOOK.md |
| **Version** | 1.0 |
| **Date** | 2026-09-04 |
| **Branch** | main |
| **Status** | Production Ready (STEP 7-9) |
| **Owner** | Operations / Development |
| **Next Review** | 2026-09-11 (end of first week) or upon first exception |

---

**END OF PRODUCTION RUNBOOK**

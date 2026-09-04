# Dashboard STEP 7-7 완료보고

## 1. Baseline
- Branch: `main`
- HEAD: `983486912670ff27f2b81d6663466d79ba57baa2`
- Python Baseline: 558 passed (제공된 기준)
- Frontend Baseline: 18 passed (제공된 기준)
- Initial Git Status: STEP 7-1~7-6 관련 작업트리 변경 및 untracked 산출물 존재; 보존

## 2. Manual Capability
- Allowed Conditions: `FAILED` + `operator_action_code=MANUAL_RERUN_ALLOWED`, 해당 trade date에 미완료 manual audit
- Disallowed Conditions: `SUCCESS`, `SUCCESS_WITH_WARNING`, `RETRY_PENDING`, `BLOCKED`, `FAILED` + 기타 code, `NON_TRADING_DAY`, missing/malformed state, 이미 manual completion된 상태
- Source of Truth: backend `operations_status()`가 반환하는 additive `manual_run` capability

## 3. Manual Run API
- Endpoint: `/api/operations/manual-run`
- Method: `POST` only; body는 empty 또는 `{}`만 허용
- Validation: backend capability 재검사, scheduler lock, daily operational lock, duplicate manual audit guard
- Orchestrator Invocation: `run_daily_operation(repo_root=repo_root)` 직접 호출; subprocess, force, target date, bypass 옵션 없음

## 4. Scheduler / Registry Interaction
- Scheduler State Mutation: 없음. scheduler state를 직접 SUCCESS로 변경하지 않음
- Reconciliation: 응답에 `scheduler_reconciliation_required=true` 반환; 다음 scheduler tick이 같은 run id의 manual audit와 성공 manifest를 확인해 state를 reconciliation
- Manual Audit: append-only `MANUAL_RUN_COMPLETED`, run id/result/timestamps/source 의미를 보존
- Historical Failed Records: 삭제/수정하지 않음; manual event는 자동 attempt 집계에서 제외

## 5. Concurrency Safety
- Scheduler Conflict: scheduler lock 획득 실패 시 `409 CONCURRENT_RUN`
- Daily Run Conflict: daily operational lock 존재 시 `409 CONCURRENT_RUN`
- Double Click: UI running state에서 버튼 disabled
- Duplicate POST: 같은 trade date의 manual completion audit가 있으면 reconciliation 전 재실행 거절

## 6. UI
- Panel: Operations page에 Manual Operations 패널 추가
- Confirmation: 명시적 `window.confirm` 후 POST
- Running: `Running Daily Operation...` 및 재클릭 방지
- Success: 결과 status/run id 표시
- Warning: warning 메시지 표시
- Failure: backend 오류 요약 표시; stack trace 미노출

## 7. Backend Tests
- Added: capability 정책, allowed execution, blocked state, scheduler/daily lock conflict, POST-only, audit, state immutability, duplicate guard
- Passed: focused 10; full Python 562
- Failed: 0

## 8. Frontend Tests
- Passed: 20
- Failed: 0
- React act warning: 없음

## 9. Full Regression
- Python: `562 passed`
- Frontend: `20 passed`
- Build: PASS (`npm run build`)

## 10. Controlled Verification
- Allowed Scenario: FAILED + MANUAL_RERUN_ALLOWED -> POST -> official orchestrator -> SUCCESS response
- Blocked Scenario: BLOCKED -> `403 MANUAL_RUN_NOT_ALLOWED`
- Concurrent Scenario: scheduler lock -> `409 CONCURRENT_RUN`
- Audit: `MANUAL_RUN_COMPLETED` append 확인
- Mutation: scheduler state current status FAILED 유지 확인
- Duplicate: reconciliation 전 두 번째 POST 거절 확인

## 11. Changed Files
- 신규/수정: `dashboard/api.py`, `dashboard/operations.py`, `scripts/daily_run_registry.py`, `scripts/daily_scheduler.py`, frontend Operations API/types/component/style/tests, `tests/test_operations_api.py`
- Protected logic 변경 여부: STEP 6 orchestrator 및 기존 scheduler 실행/재시도 로직 변경 없음; manual manifest reconciliation만 additive하게 추가

## 12. Git Status
- Result: commit/push 없음. `git diff --check` PASS. 기존 작업트리 변경 보존.

## 13. STEP 7-7 Final Judgment
- PASS

## 14. STEP 7-8 Readiness
- GO
- 이유: manual run capability, POST validation, lock/idempotency, audit, UI confirmation, controlled verification 및 전체 회귀가 완료됨. STEP 7-8 Operational Safety Tests는 시작하지 않음.

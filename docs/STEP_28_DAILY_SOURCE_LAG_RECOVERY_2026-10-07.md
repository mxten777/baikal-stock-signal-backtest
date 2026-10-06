# STEP 28 — Daily Source Lag Recovery

기록일: 2026-10-07 (KST)

기준 branch: `main`

기준 HEAD / origin/main: `bef1c249f5a50dd40ec7d3421bd25a7e8232b58a`

이 문서는 STEP 28-F까지 승인된 코드/테스트 commit candidate와 실제 검증 결과를 기록한다. 문서 작성 시점에는 구현 변경 및 이 문서 모두 commit/push 전이다.

## 1. 목적

2026-10-06 실제 운영에서 18:30 Daily는 Market latest `2026-10-06`, Investor latest `2026-10-02`로 INPUT_GATE에서 실패했다. Scheduler는 이를 `BLOCKED`로 확정했고, `BLOCKED`가 terminal 상태이므로 후속 슬롯에서는 updater/gate를 다시 실행하지 않았다.

독립 Source Availability Probe에서 이후 다음 날짜가 관찰됐다.

| 시각 (KST) | Investor latest |
|---|---|
| 20:00 | 2026-10-02 |
| 21:00 | 2026-10-06 |
| 22:00 | 2026-10-06 |
| 23:00 | 2026-10-06 |

외부 데이터는 늦게 도착한 뒤 정상화됐지만 Daily는 18:30의 terminal BLOCKED 때문에 자동 회복할 수 없었다. Probe 결과는 Daily 재실행 성공의 증거가 아니라 별도 source 관찰이다.

핵심 문제는 lag threshold가 아니라 **일시적인 source 미도착과 구조적 integrity 오류를 동일한 terminal 상태로 분류한 것**이다. STEP 28은 이 상태 분류만 좁게 보정하며 Engine이나 source provider를 변경하지 않는다.

## 2. 해결 원칙

- 검증된 recoverable source lag만 기존 슬롯 retry 대상으로 추가한다.
- 구조적 오류와 historical mutation은 `BLOCKED`를 유지한다.
- Returned source/unknown coded failure에는 새 자동 retry 권한을 부여하지 않는다.
- 최종 슬롯까지 미회복이면 `FAILED / RETRY_EXHAUSTED`로 종료한다.
- Scheduled source가 target 거래일까지 정상화되기 전에는 Dashboard/Engine을 실행하지 않는다.
- 기존 manual/default 호출의 source-lag 정책은 보존한다.
- 기존 retry 슬롯 `18:30 / 19:00 / 19:30 / 20:00 / 22:00`을 사용하며 Windows Task 설정은 변경하지 않는다.

## 3. 구현 내용

### 3.1 Input Integrity Gate

[input_integrity_gate.py](../scripts/input_integrity_gate.py)

- Optional keyword-only `target_trade_date=None`을 추가했다.
- 미지정 시 기존 default/manual lag 허용 및 freshness 동작을 유지한다.
- Target mode에서는 Market/Investor 모두 target 날짜에 도달해야 pipeline을 허용한다. 기존 허용 범위의 1~3일 lag도 scheduled pipeline 실행을 허용하지 않는다.
- `GateResult.error_code`를 default `None`으로 추가했다. 기존 필드는 삭제하거나 rename하지 않았다.
- `RECOVERABLE_SOURCE_LAG`는 다음 조건에서만 생성한다:
  - Universe가 비어 있지 않음.
  - Market/Investor 파일이 존재하고 비어 있지 않으며 ticker 날짜 coverage가 완전함.
  - Market uniform latest가 target과 동일함.
  - Investor uniform latest가 target보다 이전임.
  - Future date, investor ahead, mixed latest date, schema/numeric/duplicate/order/parse 오류 등 다른 integrity 오류가 없음.
- 기타 Gate 실패는 recoverable code를 받지 않으며 Scheduler에서 BLOCKED로 처리한다.
- Historical mutation은 upstream updater 검증으로 차단한다.

### 3.2 Daily Operational Run

[daily_operational_run.py](../scripts/daily_operational_run.py)

- `run_daily_operation`과 default dependency 생성에 optional target 전달을 추가했다.
- Scheduler가 target 거래일을 전달하며 기존 manual API/CLI는 target을 지정하지 않는다.
- 기존 phase serialization을 이용해 Gate/updater의 structured code를 전달한다.
- Updater FAILED이면 Gate를 실행하지 않고, Gate가 pipeline을 허용하지 않으면 Dashboard를 실행하지 않는다.
- 기존 순차 실행, lock, manifest 동작은 유지한다.

### 3.3 Daily Scheduler

[daily_scheduler.py](../scripts/daily_scheduler.py)

- Gate의 recoverable code만 신뢰하지 않고 target, 날짜, coverage, updater 성공 상태 및 source/published 증거를 함께 검사한다.
- Expected ticker count가 일치하고 fetch가 완전 성공했으며 failure map과 updater error code가 없어야 한다.
- Market source/published 날짜는 target이어야 한다. Investor source/published 날짜는 Gate 날짜와 일치하고 lag type은 `UNIFORM`, gap은 실제 날짜 차이와 같아야 한다.
- 검증된 lag는 기존 pending/next-slot/exhaustion 흐름을 사용한다. 무한 retry나 신규 슬롯을 추가하지 않는다.
- Mutation 및 Investor validation의 명시적 분기 이후, legacy message heuristic 이전에 returned Investor coded failure boundary를 둔다.
- Investor FAILED phase에 code가 있고 metrics에 직렬화된 `error_code` 필드가 있으면 `FAILED / UNCLASSIFIED_FAILURE`로 종료한다. Code 이름의 timeout/connection 또는 transient message는 retry 허가가 아니다.
- `_run_phase`가 직접 잡은 exception은 class code를 기록하지만 metrics는 비어 있으므로 기존 exception/message retry 정책을 유지한다.
- STEP 28에서 추가했던 structural 문자열 marker 3개를 제거했다. 기존 HEAD의 legacy marker는 그대로 유지한다.

### 3.4 Safe Investor Updater

[safe_investor_update.py](../scripts/safe_investor_update.py)

- `InvestorValidationError(ValueError)`와 `INVESTOR_VALIDATION_FAILED`를 도입했다.
- 명시적 schema/numeric/date/duplicate/order/future 및 production 입력 검증 오류를 code로 전달한다.
- 모든 ValueError나 network exception을 validation error로 감싸지 않는다. 알려진 production CSV empty/parse 오류만 명시적으로 변환한다.
- `failed_tickers`의 기존 `dict[str, str]` serialization 계약을 유지한다.
- Batch failure code 우선순위:
  1. `HISTORICAL_MUTATION_DETECTED`
  2. `INVESTOR_VALIDATION_FAILED`
  3. 모든 실패에서 동일한 명시적 code
  4. Unknown/mixed code는 `None`
- Source normalize 및 market-target 초과 검사는 staging 이전에 수행한다.
- Normalized source staging write 이후 duplicate/order 등을 검증하고, candidate/historical 검증 및 batch gate 통과 후에만 publish한다.
- Validation 실패 시 production publish는 발생하지 않는다. 기존 atomic publish/rollback 구현을 유지한다.
- Caller-owned staging에서는 duplicate 실패의 staged artifact가 유지된다. Default temporary staging은 기존 finally cleanup을 유지하므로 모든 실패 artifact의 영구 보존을 약속하지 않는다.
- Provider/transport 구현을 변경하거나 source 실패를 transient로 추정하지 않는다.

## 4. 상태 분류표

| 상황 | Scheduler 결과 | 정책 |
|---|---|---|
| 검증된 recoverable lag | `RETRY_PENDING` | 다음 기존 슬롯에서 updater/gate 재실행; Dashboard 금지 |
| Recovered sources 및 정상 pipeline | `SUCCESS` | 기존 warning 조건이 있으면 기존 SUCCESS_WITH_WARNING 정책 적용 |
| Final-slot unresolved lag | `FAILED / RETRY_EXHAUSTED` | 자동 retry 종료 |
| Investor validation failure | `BLOCKED / STRUCTURAL_FAILURE` | 자동 retry 금지 |
| Historical mutation | `BLOCKED / HISTORICAL_MUTATION_DETECTED` | 자동 retry 금지 |
| Returned source/unknown coded failure | `FAILED / UNCLASSIFIED_FAILURE` | 새 자동 retry 없음 |
| Actual phase TimeoutError/ConnectionError 또는 기존 transient-message exception | 기존 legacy retry 정책 | Returned updater code와 구분; 기존 retry budget 유지 |
| 기타 integrity Gate 실패 | `BLOCKED / INTEGRITY_GATE_FAIL` | 자동 retry 금지 |

## 5. 변경 범위

아래 통계는 이 신규 문서를 제외한 코드/테스트 8개 파일의 기준 HEAD 대비 누적 diff이다.

| 구분 | 파일 수 | Insertions | Deletions |
|---|---:|---:|---:|
| Production | 4 | 181 | 40 |
| Tests | 4 | 596 | 4 |
| Total | 8 | 777 | 44 |

Production 파일은 위 4개이며 테스트는 다음 4개다.

- [test_input_integrity_gate.py](../tests/test_input_integrity_gate.py)
- [test_daily_operational_run.py](../tests/test_daily_operational_run.py)
- [test_daily_scheduler.py](../tests/test_daily_scheduler.py)
- [test_safe_investor_update.py](../tests/test_safe_investor_update.py)

변경하지 않은 범위:

- Windows Task 및 retry trigger times
- Signal Engine, signal rules, thresholds
- Universe 및 benchmark provider
- Expanded operational path 및 M3-F provider selection
- Report/document code

기존 staging isolation assertion과 보호 경로 assertion도 완화하지 않았다.

## 6. 검증 기록

실제 실행 결과:

| 단계 | 결과 |
|---|---|
| Staging isolation 단독 | PASS |
| Updater targeted | 21 PASS |
| Scheduler structural targeted | 3 PASS |
| STEP 28-C3 focused | 198 PASS / 0 FAIL |
| STEP 28-E boundary targeted 최종 재시험 | 16 PASS / 0 FAIL |
| STEP 28-E Scheduler + legacy retry targeted | 47 PASS / 0 FAIL |
| STEP 28-E focused | 211 PASS / 0 FAIL |

Boundary targeted의 최초 실행은 13 PASS / 3 FAIL이었다. 신규 테스트의 phase constant import 누락으로 assertion 전에 NameError가 발생했고, 테스트 import만 보정한 뒤 16건 모두 PASS했다.

Boundary regression은 `SOURCE_TIMEOUT`, `SOURCE_CONNECTION_ERROR`, `INVESTOR_SOURCE_TIMEOUT`, `INVESTOR_CONNECTION_FAILURE`, `UNKNOWN_TIMEOUT` 및 exception class와 같은 이름의 returned code에도 새 retry 권한이 없음을 검사한다.

별도 actual phase exception 테스트는 TimeoutError/ConnectionError/transient-message RuntimeError의 기존 retry 유지와 Gate/Dashboard 미실행을 검사한다. Contradictory metrics 및 transient/concurrent message도 returned-code boundary를 우회하지 못함을 검사한다.

## 7. Full Suite 기록

STEP 28-C6에서 외부 detached worktree에 당시 8개 변경을 복제하고 임시 validation commit 후 clean baseline에서 전체 pytest를 끝까지 실행했다.

**실제 결과: 1,328 PASS / 4 FAIL / 15 warnings, 219.24 seconds.**

보호 테스트 4건은 모두 PASS했다. Expanded 테스트 집계는 424 PASS / 4 FAIL이었다.

실패한 정확한 node id:

1. `tests/test_expanded_shadow_data.py::test_d1_universe_unchanged`
2. `tests/test_expanded_shadow_eligibility.py::test_d1_universe_unchanged`
3. `tests/test_expanded_shadow_ops.py::test_d1_universe_and_snapshot_unchanged`
4. `tests/test_expanded_shadow_pipeline.py::test_repo_d1_universe_unchanged`

4건 모두 controlled universe CSV의 raw SHA-256 assertion에서 실패했다.

| 확인 항목 | 원본 working file | 임시 checkout |
|---|---|---|
| Git index 줄바꿈 | LF | LF |
| 실제 파일 줄바꿈 | LF | CRLF |
| Raw SHA-256 | Expected와 동일 | Expected와 다름 |
| CRLF를 LF로 메모리 정규화한 SHA-256 | Expected와 동일 | Expected와 동일 |

확보된 원인과 증거:

- System Git 설정은 `core.autocrlf=true`였다.
- 임시 detached worktree checkout에서 CSV가 LF에서 CRLF로 변환됐다.
- Controlled universe와 snapshot 모두 같은 줄바꿈 차이가 확인됐다.
- Expected SHA-256: `073982938b6dd222d6b0ca3621ce763a15fd9af43c835ddd7676e78bcd71c6d2`
- Temporary raw SHA-256: `ee7281969a9ac433e530151db4eab62cfc4bec74682ea8295dccbc2d0563dad4`
- CRLF→LF 메모리 정규화 후 SHA-256은 expected와 일치했다. 파일을 수정해 결과를 맞추지 않았다.
- 해당 controlled universe/snapshot은 STEP 28 patch 밖이었다.
- 기준 HEAD에서 임시 validation commit `6d57d1fe1a7411d22ae313b93feaaefe72297ab4`까지 해당 경로 diff도 없었다.
- 원본 main/working tree는 보존됐고 임시 worktree 및 patch는 제거됐다. Validation commit은 main/origin에 연결하거나 push하지 않았다.

판정:

> Environment/checkout representation issue, not STEP 28 regression.

단, 이 결과는 전체 suite의 무실패 결과가 아니다. **STEP 28-E retry-boundary 보정 이후 Full suite는 재실행하지 않았다. 최신 실행 검증은 focused 211 PASS / 0 FAIL이다.**

## 8. Audit History

| 단계 | 판정 / 조치 |
|---|---|
| STEP 28-D | NO-GO: returned source code가 legacy transient heuristic을 통해 retry 권한을 얻을 가능성; 신규 structural 문자열 marker 3개 |
| STEP 28-E | Returned-code retry boundary 추가, 신규 marker 제거, regression tests 추가; focused 211 PASS |
| STEP 28-F | Final delta READ-ONLY audit: 두 NO-GO 사유 해소, GO WITH NOTE |

제거한 신규 marker는 `FUTURE DATE`, `DATES NOT ASCENDING`, `NULL DATE`다. STEP 28-F에서 structural marker 전체가 기준 HEAD와 동일함을 확인했다.

## 9. 남은 제한

- 실제 운영 슬롯에서 late Investor data가 회복되는지는 향후 운영일에 관찰해야 한다. Mock source 기반 regression 결과를 실제 운영 회복 성공으로 표현하지 않는다.
- 기존 terminal BLOCKED artifact를 소급 해제하지 않는다.
- Provider가 내부적으로 삼킨 transport error는 이번 STEP 범위에서 복원하지 않는다.
- Returned updater error-code serialization 계약 변경 시 retry-boundary regression을 재검증해야 한다.
- STEP 28-E 이후 최신 전체-suite 실행 결과는 없다. 기존 Full-suite의 checkout representation 제한을 후속 검증/commit 기록에도 명시해야 한다.

## 10. 최종 상태

**STEP 28: GO WITH NOTE**

- 코드/테스트: commit candidate 승인.
- 최신 focused 검증: 211 PASS / 0 FAIL.
- 기존 Full-suite 결과: 1,328 PASS / 4 FAIL / 15 warnings; 환경/checkout representation 제한 포함.
- 이 문서 작성 시점: 코드/테스트 및 문서 모두 commit/push 전.
- 이 문서화 단계에서는 production/test/Task를 수정하거나 테스트를 실행하지 않는다.

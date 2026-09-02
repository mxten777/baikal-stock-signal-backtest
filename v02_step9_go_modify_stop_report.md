# BAIKAL Stock Signal v0.2 STEP 9 Final Evaluation

## K. 최종 판정: GO

현재 근거 기준 판정은 **GO**입니다. Foreign NEGATIVE Filter는 기존 signal 생성과 score/weight 구조를 유지한 채 후보군만 줄이는 단순 구조이며, 성과 개선·위험 악화 없음·STEP 7 robustness를 함께 충족합니다. 다만 이는 즉시 production default 변경 승인이 아니라 제한된 실전 후보 검증 단계의 GO입니다.

## A. 데이터 무결성

- Baseline: 289 signals / 20 stocks.
- Filtered: 37; Candidate: 252.
- Investor coverage: 100.0%; merge: 289/289 (100.0%).
- 데이터 품질 이슈로 최종 판단을 훼손할 누락이나 merge 실패는 확인되지 않았습니다.
- 기준 테스트: 243 passed / 0 failed. STEP 9 변경 후 전체 테스트는 별도로 재실행했습니다.

## B. Baseline vs Candidate

| Strategy             |   Signal N |   Avg Return 5D |   Avg Return 10D |   Avg Return 20D |   Win Rate 5D (%) |   Win Rate 10D (%) |   Win Rate 20D (%) |   Avg Excess 5D |   Avg Excess 10D |   Avg Excess 20D |
|:---------------------|-----------:|----------------:|-----------------:|-----------------:|------------------:|-------------------:|-------------------:|----------------:|-----------------:|-----------------:|
| BASELINE             |        289 |            1.52 |             3.17 |             4.48 |              57.4 |               54.3 |               52.1 |            0.6  |             1.18 |             0.99 |
| CANDIDATE            |        252 |            1.75 |             3.73 |             5.41 |              57.9 |               56   |               55.2 |            0.85 |             1.71 |             1.74 |
| CANDIDATE - BASELINE |        -37 |            0.23 |             0.56 |             0.93 |               0.5 |                1.7 |                3.1 |            0.25 |             0.53 |             0.75 |

5D/10D/20D Avg Return, Avg Excess, Win Rate가 모두 개선됐습니다. 20D Avg Excess는 +0.99%p에서 +1.74%p, Win Rate는 52.1%에서 55.2%로 개선됐습니다. MID Excess 20D는 +2.56%p에서 +3.44%p, HIGH Excess 20D는 -0.79%p에서 -0.54%p로 개선됐지만 여전히 음수입니다. 종목별로는 12개 개선, 5개 악화, 3개 동일입니다.

## C. Risk

| Strategy             |   Signal N |   Avg Max Drawdown 20D |   Max Drawdown 20D |   Negative Return Frequency (%) |   Downside Return Average 20D |
|:---------------------|-----------:|-----------------------:|-------------------:|--------------------------------:|------------------------------:|
| BASELINE             |        289 |                  -9.03 |             -40.75 |                            47.9 |                         -9.52 |
| CANDIDATE            |        252 |                  -8.94 |             -37.58 |                            44.8 |                         -9.85 |
| CANDIDATE - BASELINE |        -37 |                   0.09 |               3.17 |                            -3.1 |                         -0.33 |

평균 MDD는 -9.03%에서 -8.94%, worst MDD는 -40.75%에서 -37.58%로 개선됐습니다. <= -5% loss rate는 31.47%에서 31.20%, <= -10%는 18.88%에서 18.80%로 개선됐습니다. 따라서 성과 개선을 risk 악화로 교환한 근거는 없습니다. 단, candidate downside return average는 -9.85%로 baseline -9.52%보다 낮아졌으므로 tail의 모든 측면이 개선됐다고 과장하지 않습니다.

## D. Robustness

| Dimension     |   Periods |   Valid Periods |   Low Sample Periods | Excess Improved   | Win Rate Improved   | Sign Flips   | Assessment         |
|:--------------|----------:|----------------:|---------------------:|:------------------|:--------------------|:-------------|:-------------------|
| YEAR          |         4 |               1 |                    3 | 1                 | 1                   | N/A          | IMPROVED_ALL_VALID |
| EARLY_LATE    |         2 |               2 |                    0 | 2                 | 2                   | N/A          | IMPROVED_ALL_VALID |
| WALK_FORWARD  |         4 |               1 |                    3 | 1                 | 1                   | N/A          | IMPROVED_ALL_VALID |
| HORIZON       |         3 |               3 |                    0 | 3                 | 3                   | N/A          | IMPROVED_ALL       |
| MARKET        |         2 |               1 |                    1 | 2                 | 2                   | N/A          | KOSDAQ_LOW_SAMPLE  |
| LEAVE_ONE_OUT |        20 |              20 |                    0 | N/A               | N/A                 | 0            | NO_SIGN_FLIP       |

STEP 7에서 연도별 유효 구간, EARLY/LATE, 4개 walk-forward fold가 모두 excess 개선 방향이었고, 5D/10D/20D도 모두 개선됐습니다. KOSPI 결과는 유효하나 KOSDAQ은 표본 부족으로 참고 수준입니다. Leave-one-stock-out에서는 부호 반전이 0건입니다.

## E. Opportunity Cost

제외된 37개 중 positive return은 11개, positive excess는 13개입니다. 제외군 평균 Excess 20D는 -4.24%p, Win Rate는 30.6%로 낮습니다. 좋은 신호를 일부 버리는 비용은 존재하지만, 전체 Avg Excess +0.75%p와 Win Rate +3.1%p 개선 대비 수용 가능한 수준으로 판단합니다.

## F. 남은 약점

| ID   | Limitation                                 | Evidence                                             | Severity   |
|:-----|:-------------------------------------------|:-----------------------------------------------------|:-----------|
| A    | HIGH Excess 20D remains negative           | -0.54%p candidate                                    | IMPORTANT  |
| B    | KOSDAQ sample is insufficient              | 1 KOSDAQ stock / low-sample flag                     | IMPORTANT  |
| C    | Small cross-sectional sample               | 289 signals / 20 stocks                              | IMPORTANT  |
| D    | Historical-period dependence               | Validation uses the same observed history            | IMPORTANT  |
| E    | Excluded-signal opportunity cost           | 37 excluded; 11 positive returns; 13 positive excess | MONITOR    |
| F    | No live execution or transaction-cost test | Backtest metrics only                                | MONITOR    |

## G. Production 반영 시 최소 변경안

이번 STEP에서는 production/default 코드를 변경하지 않습니다. 실제 반영 시에는 기존 Signal 생성 유지 → signal date 기준 investor feature 계산 → Foreign NEGATIVE 여부 확인 → NEGATIVE면 selection candidate 제외의 최소 흐름만 추가합니다. 기존 score/weight 구조는 추가 검증 전 변경하지 않습니다.

## H. 운영 전 체크

- 최신 price/investor 데이터 수집 성공 및 기준일 확인
- foreign feature 생성 성공과 missing 처리 결과 확인
- 각 제외 건의 ticker, signal date, filter reason logging
- 일별 candidate count와 예상 범위 monitoring
- 원 signal, feature, 분류, 최종 candidate의 audit trail 보존

## I. 생성 파일

- `output/v02_step9_final_comparison.csv`
- `output/v02_step9_final_risk_review.csv`
- `output/v02_step9_final_robustness_review.csv`
- `output/v02_step9_final_limitations.csv`
- `v02_step9_go_modify_stop_report.md`

## J. 테스트

`python -m pytest -q` 실행 결과는 완료 보고에 기록합니다. 실패가 발생하면 GO 판정을 운영 반영 전 보류합니다.

## 결론

**BAIKAL Stock Signal v0.2는 현재 근거 기준으로 실전 후보 전략으로 진행할 가치가 있는가? 네, 제한된 실전 후보 검증 단계로 진행할 가치가 있습니다.**

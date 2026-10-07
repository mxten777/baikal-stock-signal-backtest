# BAIKAL Stock Signal Backtest

## 목적
BAIKAL Signal Score v0.1의 유효성을 과거 3년 주가 데이터로 검증한다.
투자 권유 목적이 아닌 알고리즘 검증용 프로젝트다.

## 설치
```bash
pip install -r requirements.txt
```

## 데이터 위치
`data/raw/` 폴더에 종목별 CSV 파일을 저장한다.

파일명 형식: `{ticker}.csv`
예: `005930.csv`, `000660.csv`, `080220.csv`

필수 컬럼: `date, open, high, low, close, volume`

## 실행
```bash
python -m src.main
```

## Score 구조
| 구분 | 최대점 | 내용 |
|------|--------|------|
| A. Trend | 25점 | MA5/MA20/MA60 정배열 |
| B. Volume | 20점 | 거래량 급증 |
| C. Momentum | 20점 | RSI, MACD, 5일수익률 |
| **합계** | **65점** | → 100점 환산 |

## Signal 판정
| 점수 | 판정 |
|------|------|
| 0~49 | RISK |
| 50~64 | WATCH |
| 65~74 | WAIT |
| 75~84 | BUY_WATCH |
| 85~100 | STRONG_WATCH |

## 결과 파일
- `output/signals.csv` — 전체 Signal 상세
- `output/summary.csv` — summary

## Expanded benchmark diagnostics (STEP 19-M3-D)

- The global benchmark provider default remains `legacy`; Naver is opt-in.
- When Naver preflight suppresses a fetch, performance diagnostics distinguish
  an existing Benchmark (`benchmark_already_filled`) from an unmatured Return
  (`benchmark_not_due`). Both counters are also exposed per horizon in
  `benchmark_status_by_horizon`.
- `NO_SOURCE` and `MATURED_RETURN_BENCHMARK_UNAVAILABLE` apply only when a
  matured Return needs a missing Benchmark and the source is absent or cannot
  supply the required return.
  `missing_benchmark` counts affected candidates once, not already-filled or
  not-due candidates. Provider failures retain their provider/source warnings.
- Existing metrics remain fill-only. Excess filling and mismatch checks still
  use the stored Benchmark when no fetch is required; supplied benchmark
  sources still undergo the existing mismatch checks. Calculations, maturity,
  fetch conditions, Signal Engine, and scheduling are unchanged.

## Expanded Forward Validation (STEP 31-C)

- Validation tracks only CANDIDATE signals whose `source_basDd >= 2026-10-08`.
- It writes to `output/expanded_shadow/expanded_validation_candidate_performance_ledger.csv`;
  the Discovery performance ledger and its schema are unchanged.
- Industry membership is snapshotted at Validation registration in
  `output/expanded_shadow/expanded_validation_sector_membership.csv`.
- Latest Validation execution status is recorded separately in
  `output/expanded_shadow/expanded_forward_validation_status.json`.
- The Expanded dashboard presents Validation separately from Discovery and
  reports 5D/10D/20D maturity, H1 Foreign, H2 score/Foreign, H3 industry, and
  H4 score-vs-5D-Excess Spearman correlation. An empty post-cutoff cohort is a
  normal state. Validation update errors are reported separately and do not
  change the existing Expanded/Daily operational result.

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
- `output/summary.csv` — 종목별 요약

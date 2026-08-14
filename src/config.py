"""
백테스트 대상 종목 및 전역 설정
"""

from pathlib import Path

# 프로젝트 루트
ROOT_DIR = Path(__file__).parent.parent

# 경로
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
OUTPUT_DIR = ROOT_DIR / "output"

# 대상 종목 (20종목)
TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "000270": "기아",
    "035420": "NAVER",
    "035720": "카카오",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "012450": "한화에어로스페이스",
    "034020": "두산에너빌리티",
    "080220": "제주반도체",
    "105560": "KB금융",
    "055550": "신한지주",
    "006400": "삼성SDI",
    "051910": "LG화학",
    "373220": "LG에너지솔루션",
    "028260": "삼성물산",
    "096770": "SK이노베이션",
    "009540": "HD한국조선해양",
    "042660": "한화오션",
}

# Signal 판정 임계값
SIGNAL_THRESHOLD = 75       # 신규 Signal 발생 기준 (100점 환산)
SIGNAL_PREV_THRESHOLD = 75  # 전일이 이 미만이어야 신규로 인정

# 과열 필터
OVERHEATED_RSI = 75
OVERHEATED_RETURN_5D = 20.0  # %
OVERHEATED_VOLUME_RATIO = 4.0

# 종목별 시장 구분 (KS11=KOSPI, KQ11=KOSDAQ)
MARKET_MAP: dict[str, str] = {
    "005930": "KS11",  # 삼성전자
    "000660": "KS11",  # SK하이닉스
    "005380": "KS11",  # 현대차
    "000270": "KS11",  # 기아
    "035420": "KS11",  # NAVER
    "035720": "KS11",  # 카카오
    "207940": "KS11",  # 삼성바이오로직스
    "068270": "KS11",  # 셀트리온
    "012450": "KS11",  # 한화에어로스페이스
    "034020": "KS11",  # 두산에너빌리티
    "080220": "KQ11",  # 제주반도체
    "105560": "KS11",  # KB금융
    "055550": "KS11",  # 신한지주
    "006400": "KS11",  # 삼성SDI
    "051910": "KS11",  # LG화학
    "373220": "KS11",  # LG에너지솔루션
    "028260": "KS11",  # 삼성물산
    "096770": "KS11",  # SK이노베이션
    "009540": "KS11",  # HD한국조선해양
    "042660": "KS11",  # 한화오션
}

# 벤치마크 지수 심볼
BENCHMARK_SYMBOLS = ["KS11", "KQ11"]

# 기술지표 파라미터
MA_SHORT = 5
MA_MID = 20
MA_LONG = 60
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_MA_PERIOD = 20
RETURN_PERIOD = 5

# 성과 측정 기간
RETURN_PERIODS = [5, 10, 20]
DRAWDOWN_PERIOD = 20

# 원점수 최대값
RAW_SCORE_MAX = 65

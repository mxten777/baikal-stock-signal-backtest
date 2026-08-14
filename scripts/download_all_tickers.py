"""
20종목 최근 3년 일봉 데이터 수집
"""
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

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

end_date = date.today().strftime("%Y-%m-%d")
start_date = (date.today() - timedelta(days=365 * 3 + 1)).strftime("%Y-%m-%d")
out_dir = Path("data/raw")
out_dir.mkdir(parents=True, exist_ok=True)

success = []
failed = []

for ticker, name in TICKERS.items():
    print(f"[{ticker}] {name} 다운로드 중 ({start_date} ~ {end_date}) ...", end=" ", flush=True)
    try:
        df = fdr.DataReader(ticker, start_date, end_date)
        if df is None or df.empty:
            raise ValueError("빈 데이터 반환")

        df = df.reset_index()
        df.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close", "Volume": "volume",
        }, inplace=True)
        df = df[["date", "open", "high", "low", "close", "volume"]]
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset="date", keep="last")
        df = df.sort_values("date").reset_index(drop=True)
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])

        # 비정상 행 제거 (0 이하 가격)
        df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]

        if len(df) < 60:
            raise ValueError(f"거래일 수 부족: {len(df)}일")

        out_path = out_dir / f"{ticker}.csv"
        df.to_csv(out_path, index=False)
        print(f"OK ({len(df)}일)")
        success.append((ticker, name, len(df)))

    except Exception as e:
        print(f"FAILED — {e}")
        failed.append((ticker, name, str(e)))

print()
print(f"성공: {len(success)}종목 / 실패: {len(failed)}종목")
if failed:
    print("\n실패 종목 목록:")
    for ticker, name, reason in failed:
        print(f"  [{ticker}] {name}: {reason}")

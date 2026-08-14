"""
삼성전자(005930) 최근 3년 일봉 다운로드 및 저장
"""
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path
from datetime import date, timedelta

end_date = date.today().strftime("%Y-%m-%d")
start_date = (date.today() - timedelta(days=365 * 3 + 1)).strftime("%Y-%m-%d")

print(f"Fetching 005930: {start_date} ~ {end_date}")
df = fdr.DataReader("005930", start_date, end_date)

df = df.reset_index()
df.rename(columns={"Date": "date", "Open": "open", "High": "high",
                   "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
df = df[["date", "open", "high", "low", "close", "volume"]]
df["date"] = pd.to_datetime(df["date"])

dupes = df.duplicated(subset="date").sum()
df = df.drop_duplicates(subset="date", keep="last")
df = df.sort_values("date").reset_index(drop=True)

missing = df[["open", "high", "low", "close", "volume"]].isna().sum().sum()
abnormal = ((df[["open", "high", "low", "close"]] <= 0).any(axis=1) | (df["volume"] < 0)).sum()

print(f"첫 거래일    : {df['date'].iloc[0].date()}")
print(f"마지막 거래일 : {df['date'].iloc[-1].date()}")
print(f"총 거래일수  : {len(df)}")
print(f"결측치 수    : {int(missing)}")
print(f"중복 날짜    : {int(dupes)}")
print(f"비정상 행    : {int(abnormal)}")
print()
print("첫 3행:")
print(df.head(3).to_string(index=False))
print()
print("마지막 3행:")
print(df.tail(3).to_string(index=False))

out = Path("data/raw/005930.csv")
out.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(out, index=False)
print(f"\n저장 완료: {out}")

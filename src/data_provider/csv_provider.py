"""
CSV 기반 데이터 Provider
data/raw/{ticker}.csv 파일을 읽는다.
"""

from __future__ import annotations

from pathlib import Path
import pandas as pd

from src.data_provider.base import BaseDataProvider

REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}


class CsvDataProvider(BaseDataProvider):
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)

    def load(self, ticker: str) -> pd.DataFrame:
        path = self._data_dir / f"{ticker}.csv"
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")

        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]

        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"[{ticker}] 필수 컬럼 누락: {missing}")

        df["date"] = pd.to_datetime(df["date"])

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 중복 날짜 제거 (최신 데이터 우선)
        df = df.drop_duplicates(subset="date", keep="last")

        # 날짜 오름차순 정렬
        df = df.sort_values("date").reset_index(drop=True)

        # 결측치가 있는 행 제거
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])

        return df

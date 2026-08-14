"""
데이터 Provider 추상 기반 클래스
Signal Engine은 이 인터페이스만 의존한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import pandas as pd


class BaseDataProvider(ABC):
    """
    반환 DataFrame 필수 컬럼:
        date (datetime64), open, high, low, close, volume (모두 numeric)
    날짜 오름차순 정렬 보장.
    """

    @abstractmethod
    def load(self, ticker: str) -> pd.DataFrame:
        """ticker에 해당하는 OHLCV DataFrame을 반환한다."""
        ...

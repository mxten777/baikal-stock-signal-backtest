"""
Benchmark (KS11 / KQ11) 데이터 로드 및 수익률 계산
"""

from __future__ import annotations

import pandas as pd
from datetime import datetime, timedelta


def load_benchmark(symbol: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    """
    FinanceDataReader로 지수 데이터 다운로드.
    실패 시 예외를 그대로 전파한다 (임의 대체 금지).

    Returns
    -------
    DataFrame with columns: date (datetime), close (float)
    """
    try:
        import FinanceDataReader as fdr
    except ImportError as e:
        raise ImportError("finance-datareader가 설치되지 않았습니다. pip install finance-datareader") from e

    kwargs: dict = {"symbol": symbol, "start": start_date}
    if end_date:
        kwargs["end"] = end_date

    df = fdr.DataReader(**kwargs)

    if df is None or df.empty:
        raise ValueError(f"Benchmark 데이터 없음: {symbol} ({start_date} ~ {end_date})")

    df = df.reset_index()

    # 컬럼 정규화
    df.columns = [c.strip().lower() for c in df.columns]

    # 날짜 컬럼 탐색
    date_col = next((c for c in df.columns if c in ("date", "index")), None)
    if date_col is None:
        # FinanceDataReader가 인덱스를 'date'로 리셋하기도 함
        raise ValueError(f"Benchmark DataFrame에서 날짜 컬럼을 찾을 수 없습니다: {list(df.columns)}")

    df = df.rename(columns={date_col: "date"})
    df["date"] = pd.to_datetime(df["date"])

    # close 컬럼 선택: 'close' 또는 첫 번째 숫자형 컬럼
    if "close" not in df.columns:
        num_cols = [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]
        if not num_cols:
            raise ValueError(f"Benchmark DataFrame에 숫자형 가격 컬럼이 없습니다: {list(df.columns)}")
        df = df.rename(columns={num_cols[-1]: "close"})

    df = df[["date", "close"]].dropna()
    df = df.drop_duplicates(subset="date", keep="last")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def build_benchmark_index(benchmark_df: pd.DataFrame) -> dict[pd.Timestamp, int]:
    """날짜 → 행 인덱스 매핑 (O(1) 조회용)"""
    return {d: i for i, d in enumerate(benchmark_df["date"])}


def get_forward_return(
    benchmark_df: pd.DataFrame,
    date_to_idx: dict,
    signal_date: pd.Timestamp,
    period: int,
) -> float | None:
    """
    signal_date 기준으로 benchmark의 period 거래일 후 수익률(%) 반환.
    데이터 부족 시 None 반환.
    """
    if signal_date not in date_to_idx:
        # 가장 가까운 이전 거래일 탐색 (최대 5일)
        for lag in range(1, 6):
            candidate = signal_date - pd.Timedelta(days=lag)
            if candidate in date_to_idx:
                signal_date = candidate
                break
        else:
            return None

    i = date_to_idx[signal_date]
    sig_close = benchmark_df.loc[i, "close"]
    future_idx = i + period
    if future_idx >= len(benchmark_df):
        return None
    future_close = benchmark_df.loc[future_idx, "close"]
    return round((future_close - sig_close) / sig_close * 100, 2)


def add_benchmark_returns(
    signals_df: pd.DataFrame,
    benchmarks: dict[str, pd.DataFrame],
    market_map: dict[str, str],
    periods: list[int] | None = None,
) -> pd.DataFrame:
    """
    signals_df에 다음 컬럼을 추가한다.
      benchmark
      benchmark_return_5d / 10d / 20d
      excess_return_5d / 10d / 20d

    Parameters
    ----------
    benchmarks : {"KS11": DataFrame, "KQ11": DataFrame}
    market_map : ticker → "KS11" | "KQ11"
    """
    if periods is None:
        periods = [5, 10, 20]

    signals = signals_df.copy()

    # 벤치마크 인덱스 사전 빌드
    bm_indices: dict[str, dict] = {
        sym: build_benchmark_index(df) for sym, df in benchmarks.items()
    }

    # 결과 컬럼 초기화
    signals["benchmark"] = ""
    for p in periods:
        signals[f"benchmark_return_{p}d"] = float("nan")
        signals[f"excess_return_{p}d"] = float("nan")

    for idx, row in signals.iterrows():
        ticker = row["ticker"]
        bm_symbol = market_map.get(ticker, "KS11")
        signals.at[idx, "benchmark"] = bm_symbol

        bm_df = benchmarks.get(bm_symbol)
        if bm_df is None:
            continue
        date_to_idx = bm_indices[bm_symbol]
        sig_date = pd.to_datetime(row["signal_date"])

        for p in periods:
            bm_ret = get_forward_return(bm_df, date_to_idx, sig_date, p)
            ret_col = f"return_{p}d"
            if bm_ret is not None:
                signals.at[idx, f"benchmark_return_{p}d"] = bm_ret
                stock_ret = row.get(ret_col)
                if pd.notna(stock_ret):
                    signals.at[idx, f"excess_return_{p}d"] = round(float(stock_ret) - bm_ret, 2)

    return signals

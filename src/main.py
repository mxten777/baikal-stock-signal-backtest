"""
진입점 — python -m src.main
"""

from __future__ import annotations

import sys
import pandas as pd
from pathlib import Path

from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.indicators import add_all_indicators
from src.signal_engine import generate_signals
from src.backtest import compute_forward_returns
from src.benchmark import load_benchmark, add_benchmark_returns
from src.report import build_summary, print_console_report


def _load_benchmarks(start_date: str) -> dict[str, pd.DataFrame]:
    """KS11 / KQ11 데이터 다운로드. 실패 시 예외 전파."""
    benchmarks: dict[str, pd.DataFrame] = {}
    for symbol in config.BENCHMARK_SYMBOLS:
        print(f"  Benchmark 다운로드: {symbol} ({start_date} ~) ...")
        df = load_benchmark(symbol, start_date)
        benchmarks[symbol] = df
        print(f"    → {len(df)}행 ({df['date'].min().date()} ~ {df['date'].max().date()})")
    return benchmarks


def run(tickers: dict[str, str] | None = None) -> None:
    if tickers is None:
        tickers = config.TICKERS

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Benchmark 데이터 로드 ──────────────────────────────────────────
    print("[Benchmark 데이터 로드]")
    try:
        benchmarks = _load_benchmarks("2023-01-01")
    except Exception as e:
        print(f"  !! Benchmark 로드 실패: {e}")
        print("  !! 원인을 확인하고 재실행하세요. 임의 대체하지 않습니다.")
        sys.exit(1)

    provider = CsvDataProvider(config.DATA_RAW_DIR)
    all_signals: list[pd.DataFrame] = []

    for ticker, name in tickers.items():
        print(f"Processing [{ticker}] {name} ...")

        try:
            df = provider.load(ticker)
        except FileNotFoundError as e:
            print(f"  !! 데이터 없음: {e}")
            continue
        except ValueError as e:
            print(f"  !! 데이터 오류: {e}")
            continue

        df_with_indicators = add_all_indicators(df)
        signals = generate_signals(df_with_indicators, ticker, name)

        if signals.empty:
            print(f"  → Signal 없음")
            continue

        signals = compute_forward_returns(signals, df_with_indicators)
        all_signals.append(signals)
        print(f"  → Signal {len(signals)}건")

    if not all_signals:
        print("\n결과 없음. data/raw/ 폴더에 CSV 파일을 확인하세요.")
        sys.exit(1)

    signals_df = pd.concat(all_signals, ignore_index=True)

    # ── Benchmark 수익률 및 초과수익 추가 ──────────────────────────────
    signals_df = add_benchmark_returns(
        signals_df,
        benchmarks=benchmarks,
        market_map=config.MARKET_MAP,
        periods=config.RETURN_PERIODS,
    )

    summary_df = build_summary(signals_df)

    # CSV 저장
    signals_out = config.OUTPUT_DIR / "signals.csv"
    summary_out = config.OUTPUT_DIR / "summary.csv"
    signals_df.to_csv(signals_out, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_out, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {signals_out}")
    print(f"저장 완료: {summary_out}\n")

    print_console_report(signals_df, summary_df)


if __name__ == "__main__":
    run()

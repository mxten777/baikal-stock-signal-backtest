"""Expanded-only benchmark provider: validated Naver index closes and exact-date returns.

The shared src.benchmark loader is intentionally left untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import pandas as pd


NAVER_SOURCE_BY_SYMBOL = {"KS11": "NAVER:KOSPI", "KQ11": "NAVER:KOSDAQ"}

STATUS_CALCULATED = "CALCULATED"
STATUS_NO_SOURCE = "NO_SOURCE"
STATUS_INVALID_SOURCE = "INVALID_SOURCE"
STATUS_STOCK_DATE_UNAVAILABLE = "STOCK_DATE_UNAVAILABLE"
STATUS_AFTER_CUTOFF = "AFTER_CUTOFF"
STATUS_MISSING_START = "MISSING_START"
STATUS_MISSING_END = "MISSING_END"
STATUS_STALE_SOURCE = "STALE_SOURCE"
STATUS_DATE_MISMATCH = "DATE_MISMATCH"
BENCHMARK_STATUSES = (
    STATUS_CALCULATED,
    STATUS_NO_SOURCE,
    STATUS_INVALID_SOURCE,
    STATUS_STOCK_DATE_UNAVAILABLE,
    STATUS_AFTER_CUTOFF,
    STATUS_MISSING_START,
    STATUS_MISSING_END,
    STATUS_STALE_SOURCE,
    STATUS_DATE_MISMATCH,
)


class ExpandedBenchmarkError(RuntimeError):
    """Raised when the benchmark source cannot be fetched or normalized."""


@dataclass(frozen=True)
class ExpandedBenchmark:
    symbol: str
    source: str
    cutoff_date: str | None
    dates: tuple[str, ...]
    closes: dict[str, float]
    latest_row_date: str | None
    latest_valid_close_date: str | None
    invalid_close_dates: tuple[str, ...] = ()
    after_cutoff_rows: int = 0
    duplicate_dates: tuple[str, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.duplicate_dates

    def diagnostics(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "cutoff_date": self.cutoff_date,
            "valid": self.is_valid,
            "row_count": len(self.dates),
            "latest_row_date": self.latest_row_date,
            "latest_valid_close_date": self.latest_valid_close_date,
            "stale": self.cutoff_date is not None
            and (self.latest_valid_close_date is None or self.latest_valid_close_date < self.cutoff_date),
            "invalid_close_dates": list(self.invalid_close_dates),
            "after_cutoff_rows": self.after_cutoff_rows,
            "duplicate_dates": list(self.duplicate_dates),
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class BenchmarkReturn:
    status: str
    value: float | None = None
    source: str | None = None
    start_date: str | None = None
    end_date: str | None = None


def validate_benchmark_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    cutoff_date: str | None = None,
) -> ExpandedBenchmark:
    """Validate a ``date``/``close`` frame; never fills, shifts or drops ambiguous rows silently."""
    cutoff = None if cutoff_date is None else date.fromisoformat(cutoff_date).isoformat()

    def invalid(*errors: str) -> ExpandedBenchmark:
        return ExpandedBenchmark(symbol, source, cutoff, (), {}, None, None, errors=errors)

    if frame is None or frame.empty:
        return invalid("EMPTY_SOURCE")
    if not {"date", "close"}.issubset(frame.columns):
        return invalid("MISSING_DATE_OR_CLOSE_COLUMN")
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    if parsed.isna().any():
        return invalid("INVALID_DATE")
    if (parsed != parsed.dt.normalize()).any():
        return invalid("NON_DAILY_TIMESTAMP")
    day_strings = parsed.dt.strftime("%Y-%m-%d").reset_index(drop=True)
    closes = pd.to_numeric(frame["close"], errors="coerce").reset_index(drop=True)

    in_scope = pd.Series(True, index=day_strings.index) if cutoff is None else day_strings <= cutoff
    after_cutoff = int((~in_scope).sum())
    day_strings = day_strings[in_scope]
    closes = closes[in_scope]

    duplicates = tuple(sorted(set(day_strings[day_strings.duplicated(keep=False)])))
    ordered = sorted(zip(day_strings, closes), key=lambda item: item[0])
    valid_closes: dict[str, float] = {}
    invalid_dates: list[str] = []
    for day, close in ordered:
        if pd.notna(close) and math.isfinite(float(close)) and float(close) > 0:
            valid_closes[day] = float(close)
        else:
            invalid_dates.append(day)
    if duplicates:
        valid_closes = {}
    dates = tuple(day for day, _ in ordered)
    return ExpandedBenchmark(
        symbol=symbol,
        source=source,
        cutoff_date=cutoff,
        dates=dates,
        closes=valid_closes,
        latest_row_date=dates[-1] if dates else None,
        latest_valid_close_date=max(valid_closes) if valid_closes else None,
        invalid_close_dates=tuple(invalid_dates),
        after_cutoff_rows=after_cutoff,
        duplicate_dates=duplicates,
    )


def load_expanded_benchmark(
    symbol: str,
    start_date: str,
    cutoff_date: str,
    *,
    reader: Callable[..., pd.DataFrame] | None = None,
) -> ExpandedBenchmark:
    """Fetch one Naver daily index series through FinanceDataReader and validate it."""
    source = NAVER_SOURCE_BY_SYMBOL.get(symbol)
    if source is None:
        raise ExpandedBenchmarkError(f"Unsupported Expanded benchmark symbol: {symbol!r}")
    if reader is None:
        import FinanceDataReader as fdr

        reader = fdr.DataReader
    raw = reader(source, start_date, cutoff_date)
    if raw is None or raw.empty:
        raise ExpandedBenchmarkError(f"Benchmark source returned no rows: {source}")
    if "Close" not in raw.columns:
        raise ExpandedBenchmarkError(f"Benchmark source has no Close column: {source} {list(raw.columns)}")
    normalized = pd.DataFrame({"date": raw.index, "close": raw["Close"].to_numpy()})
    return validate_benchmark_frame(normalized, symbol=symbol, source=source, cutoff_date=cutoff_date)


def compute_benchmark_return_for_dates(
    benchmark: ExpandedBenchmark | None,
    start_date: str | None,
    end_date: str | None,
    horizon: int,
) -> BenchmarkReturn:
    """Return 100 * (end_close / start_close - 1) for the stock's exact entry/evaluation dates."""
    if benchmark is None:
        return BenchmarkReturn(STATUS_NO_SOURCE)
    source = benchmark.source
    if not benchmark.is_valid:
        return BenchmarkReturn(STATUS_INVALID_SOURCE, source=source)
    if start_date is None or end_date is None:
        return BenchmarkReturn(STATUS_STOCK_DATE_UNAVAILABLE, source=source)
    def result(status: str, value: float | None = None) -> BenchmarkReturn:
        return BenchmarkReturn(status, value, source, start_date, end_date)

    if benchmark.cutoff_date is not None and end_date > benchmark.cutoff_date:
        return result(STATUS_AFTER_CUTOFF)
    if start_date not in benchmark.closes:
        return result(STATUS_MISSING_START)
    if end_date not in benchmark.closes:
        latest = benchmark.latest_valid_close_date
        stale = latest is not None and end_date > latest
        return result(STATUS_STALE_SOURCE if stale else STATUS_MISSING_END)
    if benchmark.dates.index(end_date) - benchmark.dates.index(start_date) != horizon:
        return result(STATUS_DATE_MISMATCH)
    value = 100.0 * (benchmark.closes[end_date] / benchmark.closes[start_date] - 1.0)
    return result(STATUS_CALCULATED, value)


def stock_endpoint_dates(price_frame: pd.DataFrame | None, signal_date: str, horizon: int) -> tuple[str | None, str | None]:
    """Mirror compute_forward_returns' positional row rule to expose its entry/evaluation dates."""
    if price_frame is None or price_frame.empty or "date" not in price_frame or "close" not in price_frame:
        return None, None
    dates = pd.to_datetime(price_frame["date"]).reset_index(drop=True)
    closes = pd.to_numeric(price_frame["close"], errors="coerce").reset_index(drop=True)
    matches = dates[dates.dt.normalize() == pd.Timestamp(signal_date).normalize()].index
    if len(matches) == 0:
        return None, None
    index = int(matches[0])
    start = dates.iloc[index].strftime("%Y-%m-%d")
    future = index + horizon
    if future >= len(closes) or pd.isna(closes.iloc[future]):
        return start, None
    return start, dates.iloc[future].strftime("%Y-%m-%d")

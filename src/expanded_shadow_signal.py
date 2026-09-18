"""Expanded Shadow signal/candidate adapter.

STEP 13-D5 only: evaluate D4 READY ticker evidence against Expanded market
and investor snapshots by reusing existing Production/Shadow signal and
Foreign NEGATIVE candidate rules. This module does not write ledgers, run all
574 tickers, schedule jobs, or modify Production/Shadow/DUAL state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import re

import pandas as pd

from scripts.shadow_step1_track_signals import foreign_status_from_ratio
from scripts.step9_investor_effect import compute_investor_features
from src.expanded_shadow_eligibility import EligibilityResult, STATUS_READY
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_universe import TICKER_PATTERN
from src.indicators import add_all_indicators
from src.shadow_tracking import decide_candidate
from src.signal_engine import generate_signals


NO_SIGNAL = "NO_SIGNAL"


class ExpandedSignalError(RuntimeError):
    """Raised when Expanded Shadow signal evaluation is not allowed."""


@dataclass(frozen=True)
class ExpandedSignalEvaluation:
    ticker: str
    name: str
    market: str
    basDd: str
    evaluated: bool
    signal_present: bool
    signal_date: str | None
    signal_price: float | None
    raw_score: int | None
    signal_score: float | None
    signal_type: str | None
    foreign_5d_ratio: float | None
    foreign_status: str | None
    decision: str | None
    exclusion_reason: str | None
    reason: str | None = None


def evaluate_ready_ticker_from_snapshots(
    *,
    eligibility: EligibilityResult,
    name: str,
    market: str,
    paths: ExpandedShadowPaths,
) -> ExpandedSignalEvaluation:
    """Load Expanded snapshots and evaluate a single D4 READY ticker."""
    _assert_ready(eligibility)
    market_path = paths.validate_data_path(paths.market_dir(eligibility.basDd) / f"{eligibility.ticker}.csv")
    investor_path = paths.validate_data_path(paths.investor_dir(eligibility.basDd) / f"{eligibility.ticker}_investor.csv")
    market_frame = pd.read_csv(market_path, dtype={"ticker": str}, parse_dates=["date"])
    investor_frame = pd.read_csv(investor_path, dtype={"ticker": str}, parse_dates=["date"])
    return evaluate_ready_ticker(
        eligibility=eligibility,
        name=name,
        market=market,
        market_frame=market_frame,
        investor_frame=investor_frame,
    )


def evaluate_ready_ticker(
    *,
    eligibility: EligibilityResult,
    name: str,
    market: str,
    market_frame: pd.DataFrame,
    investor_frame: pd.DataFrame,
) -> ExpandedSignalEvaluation:
    """Evaluate one READY ticker without changing the Signal Engine rules."""
    _assert_ready(eligibility)
    ticker = eligibility.ticker
    _assert_ticker(ticker)

    market_data = _prepare_market_frame(market_frame, ticker)
    investor_data = _prepare_investor_frame(investor_frame, ticker)
    indicators = add_all_indicators(market_data)
    signals = generate_signals(indicators, ticker, name)

    if signals.empty:
        return _no_signal(ticker, name, market, eligibility.basDd)

    signal_dates = pd.to_datetime(signals["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    latest_signals = signals.loc[signal_dates == eligibility.basDd].copy()
    if latest_signals.empty:
        return _no_signal(ticker, name, market, eligibility.basDd)

    latest_signals["signal_date"] = pd.to_datetime(latest_signals["signal_date"])
    features = compute_investor_features(
        latest_signals,
        investor_map={ticker: investor_data},
        raw_map={ticker: market_data},
    )
    row = features.iloc[-1]
    ratio = row.get("foreign_5d_ratio")
    foreign_status = foreign_status_from_ratio(ratio)
    decision, exclusion_reason = decide_candidate(foreign_status)

    return ExpandedSignalEvaluation(
        ticker=ticker,
        name=name,
        market=market,
        basDd=eligibility.basDd,
        evaluated=True,
        signal_present=True,
        signal_date=str(pd.Timestamp(row["signal_date"]).date()),
        signal_price=float(row["signal_close"]),
        raw_score=int(row["raw_score"]),
        signal_score=float(row["score"]),
        signal_type=str(row["signal_type"]),
        foreign_5d_ratio=None if pd.isna(ratio) else float(ratio),
        foreign_status=foreign_status,
        decision=decision,
        exclusion_reason=exclusion_reason,
        reason=None,
    )


def _assert_ready(eligibility: EligibilityResult) -> None:
    if eligibility.status != STATUS_READY:
        raise ExpandedSignalError(
            f"ticker {eligibility.ticker} is not eligible for signal evaluation: {eligibility.status}"
        )


def _assert_ticker(ticker: str) -> None:
    if not isinstance(ticker, str) or not re.fullmatch(TICKER_PATTERN, ticker):
        raise ExpandedSignalError(f"invalid ticker identifier: {ticker!r}")


def _prepare_market_frame(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ExpandedSignalError(f"market snapshot missing columns: {sorted(missing)}")
    if "ticker" in frame.columns and frame["ticker"].astype(str).ne(ticker).any():
        raise ExpandedSignalError(f"market snapshot ticker mismatch for {ticker}")
    result = frame[["date", "open", "high", "low", "close", "volume"]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    if result["date"].isna().any():
        raise ExpandedSignalError("market snapshot has invalid date")
    return result.sort_values("date").reset_index(drop=True)


def _prepare_investor_frame(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    required = {"date", "ticker", "foreign_net_buy", "institution_net_buy"}
    missing = required - set(frame.columns)
    if missing:
        raise ExpandedSignalError(f"investor snapshot missing columns: {sorted(missing)}")
    if frame["ticker"].astype(str).ne(ticker).any():
        raise ExpandedSignalError(f"investor snapshot ticker mismatch for {ticker}")
    result = frame[["date", "ticker", "foreign_net_buy", "institution_net_buy"]].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    if result["date"].isna().any():
        raise ExpandedSignalError("investor snapshot has invalid date")
    result["ticker"] = result["ticker"].astype(str)
    result["foreign_net_buy"] = pd.to_numeric(result["foreign_net_buy"], errors="coerce")
    result["institution_net_buy"] = pd.to_numeric(result["institution_net_buy"], errors="coerce")
    if result[["foreign_net_buy", "institution_net_buy"]].isna().any().any():
        raise ExpandedSignalError("investor snapshot has invalid numeric values")
    return result.sort_values("date").reset_index(drop=True)


def _no_signal(ticker: str, name: str, market: str, basDd: str) -> ExpandedSignalEvaluation:
    return ExpandedSignalEvaluation(
        ticker=ticker,
        name=name,
        market=market,
        basDd=basDd,
        evaluated=True,
        signal_present=False,
        signal_date=None,
        signal_price=None,
        raw_score=None,
        signal_score=None,
        signal_type=None,
        foreign_5d_ratio=None,
        foreign_status=None,
        decision=None,
        exclusion_reason=None,
        reason=NO_SIGNAL,
    )
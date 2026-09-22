"""STEP 17-H Source Availability Probe.

Independent, READ-ONLY probe that checks whether the external market
(FinanceDataReader) and investor-flow (Naver) sources already have data for
today's target base date, for a small fixed set of representative tickers.

This module does NOT modify Production/DUAL/Expanded operational code, the
Signal Engine, or any existing artifact. It never writes to data/raw,
data/investor, data/expanded_shadow, or any ledger/registry file. Its only
write output is the append-only JSONL log at
output/source_availability_probe_log.jsonl.

One-tick structure: running this module executes the probe exactly once and
exits (no scheduling/looping, no Windows Task registration here).

CLI:
    python -m scripts.source_availability_probe
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from src.expanded_shadow_data import FinanceDataReaderMarketSource, NaverInvestorFlowSource

SEOUL_TZ = ZoneInfo("Asia/Seoul")
LOOKBACK_DAYS = 10

PROBE_LOG_PATH = Path("output") / "source_availability_probe_log.jsonl"

# Fixed representative tickers (must remain exactly 4, no duplicates).
PROBE_TICKERS: tuple[dict[str, str], ...] = (
    {"ticker": "005930", "name": "삼성전자", "group": "PRODUCTION_KOSPI"},
    {"ticker": "080220", "name": "제주반도체", "group": "PRODUCTION_KOSDAQ"},
    {"ticker": "000660", "name": "SK하이닉스", "group": "EXPANDED_KOSPI"},
    {"ticker": "196170", "name": "알테오젠", "group": "EXPANDED_KOSDAQ"},
)


class MarketSource(Protocol):
    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        ...


class InvestorSource(Protocol):
    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        ...


@dataclass(frozen=True)
class ProbeResult:
    ticker: str
    probe_time: str
    target_basDd: str
    market_latest_basDd: str | None
    investor_latest_basDd: str | None
    target_basDd_match: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_probe_tickers() -> None:
    tickers = [entry["ticker"] for entry in PROBE_TICKERS]
    if len(tickers) != 4:
        raise ValueError(f"PROBE_TICKERS must contain exactly 4 tickers, got {len(tickers)}")
    if len(set(tickers)) != len(tickers):
        raise ValueError(f"PROBE_TICKERS must not contain duplicate tickers: {tickers}")


def now_seoul() -> datetime:
    return datetime.now(SEOUL_TZ)


def compute_target_basDd(now: datetime) -> str:
    return now.date().isoformat()


def _latest_basDd(frame: pd.DataFrame | None) -> str | None:
    if frame is None or frame.empty or "date" not in frame.columns:
        return None
    sorted_frame = frame.sort_values("date")
    latest = sorted_frame["date"].iloc[-1]
    return pd.Timestamp(latest).strftime("%Y-%m-%d")


def probe_one_ticker(
    ticker: str,
    *,
    market_source: MarketSource,
    investor_source: InvestorSource,
    now: datetime,
) -> ProbeResult:
    target_basDd = compute_target_basDd(now)
    start = (now.date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    end = target_basDd

    market_latest_basDd: str | None = None
    investor_latest_basDd: str | None = None
    errors: list[str] = []

    try:
        market_frame = market_source.fetch(ticker, start, end)
        market_latest_basDd = _latest_basDd(market_frame)
    except Exception as exc:  # noqa: BLE001 - probe must never crash on source failure
        errors.append(f"MARKET_FETCH_ERROR: {exc!r}")

    try:
        investor_frame = investor_source.fetch(ticker, start, end)
        investor_latest_basDd = _latest_basDd(investor_frame)
    except Exception as exc:  # noqa: BLE001 - probe must never crash on source failure
        errors.append(f"INVESTOR_FETCH_ERROR: {exc!r}")

    target_basDd_match = (
        market_latest_basDd == target_basDd and investor_latest_basDd == target_basDd
    )

    return ProbeResult(
        ticker=ticker,
        probe_time=now.isoformat(),
        target_basDd=target_basDd,
        market_latest_basDd=market_latest_basDd,
        investor_latest_basDd=investor_latest_basDd,
        target_basDd_match=target_basDd_match,
        error="; ".join(errors) if errors else None,
    )


def run_probe(
    *,
    market_source: MarketSource | None = None,
    investor_source: InvestorSource | None = None,
    now: datetime | None = None,
) -> list[ProbeResult]:
    _validate_probe_tickers()
    market_source = market_source or FinanceDataReaderMarketSource()
    investor_source = investor_source or NaverInvestorFlowSource()
    now = now or now_seoul()

    return [
        probe_one_ticker(
            entry["ticker"],
            market_source=market_source,
            investor_source=investor_source,
            now=now,
        )
        for entry in PROBE_TICKERS
    ]


def append_probe_log(results: list[ProbeResult], log_path: Path = PROBE_LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False))
            handle.write("\n")


def main() -> int:
    results = run_probe()
    append_probe_log(results)
    for result in results:
        print(
            f"{result.ticker} target={result.target_basDd} "
            f"market={result.market_latest_basDd} investor={result.investor_latest_basDd} "
            f"match={result.target_basDd_match} error={result.error}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

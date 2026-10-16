"""Reviewed, exact-session trading halts; never infer a halt from missing prices."""

from __future__ import annotations

from typing import Iterable


UNISON_HALTED_SESSIONS = frozenset({"2026-10-15", "2026-10-16"})


def trading_halt_exclusions(source_date: str, tickers: Iterable[str]) -> list[dict[str, str]]:
    """Return fresh evidence records only for explicitly verified sessions."""
    if source_date not in UNISON_HALTED_SESSIONS or "018000" not in tickers:
        return []
    return [{
        "ticker": "018000",
        "name": "Unison",
        "source_date": source_date,
        "reason": "TRADING_HALT_SHARE_CONSOLIDATION",
        "halt_start_date": "2026-10-15",
        "halt_end_condition": "Until the day before the new shares are listed",
        "evidence_provider": "KOSCOM disclosure via Naver",
        "evidence_id": "20261012_102662",
        "evidence_url": "https://m.stock.naver.com/api/stock/018000/disclosure/153834975",
        "status_provider": "Naver",
        "status_as_of": "2026-10-16T20:20:23+09:00",
        "status_url": "https://m.stock.naver.com/api/stock/018000/basic",
    }]

"""Common Report Model for the Daily Report (read-only projection).

These dataclasses hold values copied verbatim from the Expanded Shadow
source-of-truth artifacts. No field here is derived by recalculating a
signal, score, or performance metric.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_READY = "READY"
STATUS_MISSING = "MISSING"
STATUS_MALFORMED = "MALFORMED"

NEW_CANDIDATES_READY = "READY"
NEW_CANDIDATES_EMPTY = "EMPTY"
NEW_CANDIDATES_NOT_FOUND = "NOT_FOUND"
NEW_CANDIDATES_UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class RunSummary:
    source_date: str | None
    run_id: str | None
    universe: int | None
    attempted: int | None
    ready: int | None
    signals: int | None
    candidate: int | None
    excluded: int | None
    no_signal: int | None
    failure: int | None


@dataclass(frozen=True)
class NewCandidateRecord:
    stock_name: str
    ticker: str
    market: str
    signal_date: str
    entry_price: float | int | None
    signal_score: float | int | None
    foreign_status: str


@dataclass(frozen=True)
class PerformanceRecord:
    ticker: str
    stock_name: str
    signal_date: str
    entry_price: float | int | None
    tracking_status: str
    return_5d: float | int | None
    benchmark_5d: float | int | None
    excess_5d: float | int | None
    return_10d: float | int | None
    benchmark_10d: float | int | None
    excess_10d: float | int | None
    return_20d: float | int | None
    benchmark_20d: float | int | None
    excess_20d: float | int | None


@dataclass(frozen=True)
class DailyReportModel:
    status: str
    run_summary: RunSummary
    new_candidates_status: str
    new_candidates: list[NewCandidateRecord] = field(default_factory=list)
    performance: list[PerformanceRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

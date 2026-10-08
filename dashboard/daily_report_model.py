"""Common Report Model for the Daily Report (read-only projection)."""

from __future__ import annotations

from dataclasses import dataclass, field

from dashboard.expanded_evidence import ExpandedSignalRecord

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
class SignalChange:
    ticker: str
    stock_name: str
    change: str
    previous: str | None
    current: str


@dataclass(frozen=True)
class DailyComparison:
    previous_date: str | None = None
    status: str = "UNAVAILABLE"
    reason: str = "비교 불가: 전 거래일 발생 신호 자료를 확인하지 못했습니다."
    changes: tuple[SignalChange, ...] = ()
    previous_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class DailyReportModel:
    status: str
    run_summary: RunSummary
    new_candidates_status: str
    new_candidates: list[NewCandidateRecord] = field(default_factory=list)
    performance: list[PerformanceRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expanded_signals: list[ExpandedSignalRecord] = field(default_factory=list)
    expanded_signal_warnings: list[str] = field(default_factory=list)
    report_date: str | None = None
    comparison: DailyComparison = field(default_factory=DailyComparison)
    performance_as_of: str | None = None

    @property
    def performance_summary(self) -> dict[str, int | None]:
        """Summarize the report-date candidates when all have tracking records."""
        ranks = {"OPEN": 0, "5D": 1, "10D": 2, "20D": 3, "COMPLETE": 4}
        candidate_keys = {(record.ticker, record.signal_date) for record in self.new_candidates}
        if not candidate_keys:
            return {"open": 0, "matured_5d": 0, "matured_10d": 0, "matured_20d": 0}
        performance_by_key = {
            (record.ticker, record.signal_date): record
            for record in self.performance
        }
        if any(key not in performance_by_key for key in candidate_keys):
            return {"open": None, "matured_5d": None, "matured_10d": None, "matured_20d": None}
        statuses = [
            ranks.get(performance_by_key[key].tracking_status, -1)
            for key in candidate_keys
        ]
        if any(status < 0 for status in statuses):
            return {"open": None, "matured_5d": None, "matured_10d": None, "matured_20d": None}
        return {
            "open": sum(status == 0 for status in statuses),
            "matured_5d": sum(status >= 1 for status in statuses),
            "matured_10d": sum(status >= 2 for status in statuses),
            "matured_20d": sum(status >= 3 for status in statuses),
        }

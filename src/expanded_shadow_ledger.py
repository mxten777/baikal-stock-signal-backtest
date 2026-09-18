"""Expanded Shadow signal ledger.

STEP 13-D6 only: append D5 signal/candidate evaluations to the isolated
Expanded Shadow ledger. This module does not evaluate signals, run tickers,
schedule jobs, or write Production/Shadow/DUAL ledgers.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_signal import ExpandedSignalEvaluation
from src.shadow_tracking import DECISION_CANDIDATE, DECISION_EXCLUDED, EXCLUSION_REASON_FOREIGN_NEGATIVE


ENGINE_VERSION = "v0.1"
LEDGER_STATUSES = frozenset({DECISION_CANDIDATE, DECISION_EXCLUDED})


class ExpandedLedgerError(RuntimeError):
    """Raised when the Expanded Shadow ledger cannot be safely updated."""


@dataclass(frozen=True)
class ExpandedLedgerRecord:
    basDd: str
    stock_code: str
    stock_name: str
    market: str
    signal_date: str
    signal_price: float
    raw_score: int
    signal_score: float
    signal_type: str
    foreign_5d_ratio: float | None
    foreign_status: str
    decision: str
    exclusion_reason: str | None
    engine_version: str
    source_commit: str
    run_id: str
    created_at: str


LEDGER_FIELDS = [field.name for field in fields(ExpandedLedgerRecord)]
DEDUPE_FIELDS = ("basDd", "stock_code", "signal_date", "engine_version")


def build_ledger_record(
    evaluation: ExpandedSignalEvaluation,
    *,
    run_id: str,
    source_commit: str,
    engine_version: str = ENGINE_VERSION,
    created_at: str | None = None,
) -> ExpandedLedgerRecord | None:
    """Convert a D5 signal evaluation to a ledger record; NO_SIGNAL returns None."""
    if not evaluation.signal_present:
        return None
    missing = [
        name
        for name in ("signal_date", "signal_price", "raw_score", "signal_score", "signal_type", "foreign_status", "decision")
        if getattr(evaluation, name) is None
    ]
    if missing:
        raise ExpandedLedgerError(f"signal evaluation missing ledger fields: {missing}")
    if evaluation.decision not in LEDGER_STATUSES:
        raise ExpandedLedgerError(f"unsupported decision for ledger: {evaluation.decision!r}")
    if evaluation.decision == DECISION_EXCLUDED and evaluation.exclusion_reason != EXCLUSION_REASON_FOREIGN_NEGATIVE:
        raise ExpandedLedgerError(f"unsupported exclusion reason: {evaluation.exclusion_reason!r}")

    return ExpandedLedgerRecord(
        basDd=evaluation.basDd,
        stock_code=evaluation.ticker,
        stock_name=evaluation.name,
        market=evaluation.market,
        signal_date=str(evaluation.signal_date),
        signal_price=float(evaluation.signal_price),
        raw_score=int(evaluation.raw_score),
        signal_score=float(evaluation.signal_score),
        signal_type=str(evaluation.signal_type),
        foreign_5d_ratio=None if evaluation.foreign_5d_ratio is None or pd.isna(evaluation.foreign_5d_ratio) else float(evaluation.foreign_5d_ratio),
        foreign_status=str(evaluation.foreign_status),
        decision=str(evaluation.decision),
        exclusion_reason=evaluation.exclusion_reason,
        engine_version=engine_version,
        source_commit=source_commit,
        run_id=run_id,
        created_at=created_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


class ExpandedShadowLedgerStore:
    """Append-only Expanded Shadow ledger with idempotent signal keys."""

    def __init__(self, paths: ExpandedShadowPaths, path: Path | None = None) -> None:
        self.paths = paths
        self.path = paths.validate_output_path(path or paths.signal_ledger_path)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=LEDGER_FIELDS)
        return pd.read_csv(self.path, dtype={"basDd": str, "stock_code": str, "signal_date": str, "engine_version": str})

    def add_evaluation(
        self,
        evaluation: ExpandedSignalEvaluation,
        *,
        run_id: str,
        source_commit: str,
        engine_version: str = ENGINE_VERSION,
        created_at: str | None = None,
    ) -> bool:
        record = build_ledger_record(
            evaluation,
            run_id=run_id,
            source_commit=source_commit,
            engine_version=engine_version,
            created_at=created_at,
        )
        if record is None:
            return False
        return self.add(record)

    def add(self, record: ExpandedLedgerRecord) -> bool:
        existing = self.load()
        if not existing.empty:
            matched = _matching_rows(existing, record)
            if not matched.empty:
                if _record_matches_existing(matched.iloc[0], record):
                    return False
                raise ExpandedLedgerError(f"conflicting duplicate ledger record: {_dedupe_key(record)}")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = pd.DataFrame([asdict(record)], columns=LEDGER_FIELDS)
        _append_row_atomic(self.path, row)
        return True


def _matching_rows(existing: pd.DataFrame, record: ExpandedLedgerRecord) -> pd.DataFrame:
    mask = pd.Series(True, index=existing.index)
    for field in DEDUPE_FIELDS:
        mask &= existing[field].astype(str).eq(str(getattr(record, field)))
    return existing.loc[mask]


def _record_matches_existing(row: pd.Series, record: ExpandedLedgerRecord) -> bool:
    expected = asdict(record)
    for field in LEDGER_FIELDS:
        actual = _normalize_value(row.get(field))
        wanted = _normalize_value(expected[field])
        if actual != wanted:
            return False
    return True


def _normalize_value(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value)


def _dedupe_key(record: ExpandedLedgerRecord) -> tuple[str, str, str, str]:
    return tuple(str(getattr(record, field)) for field in DEDUPE_FIELDS)  # type: ignore[return-value]


def _append_row_atomic(path: Path, row: pd.DataFrame) -> None:
    existing = pd.read_csv(path, dtype=str) if path.exists() and path.stat().st_size else pd.DataFrame(columns=LEDGER_FIELDS)
    combined = pd.concat([existing, row], ignore_index=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            combined.to_csv(handle, index=False, lineterminator="\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
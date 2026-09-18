"""Incremental performance tracking for Expanded Shadow CANDIDATE signals."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from src.expanded_shadow_ops import ExpandedShadowPaths
from src.shadow_tracking import (
    BENCHMARK_FIELD_BY_HORIZON,
    FORWARD_HORIZONS,
    RETURN_FIELD_BY_HORIZON,
    RETURN_MISMATCH_TOLERANCE,
    compute_benchmark_returns,
    compute_excess,
    compute_forward_returns,
    normalize_market,
)


STATUS_OPEN = "OPEN"
STATUS_5D = "5D"
STATUS_10D = "10D"
STATUS_20D = "20D"
STATUS_COMPLETE = "COMPLETE"
STATUS_RANK = {
    STATUS_OPEN: 0,
    STATUS_5D: 1,
    STATUS_10D: 2,
    STATUS_20D: 3,
    STATUS_COMPLETE: 4,
}

DEFAULT_LEDGER_NAME = "expanded_candidate_performance_ledger.csv"
REQUIRED_SIGNAL_COLUMNS = {
    "basDd",
    "stock_code",
    "stock_name",
    "market",
    "signal_date",
    "signal_price",
    "signal_score",
    "foreign_status",
    "decision",
    "engine_version",
    "source_commit",
    "run_id",
    "created_at",
}


class ExpandedCandidatePerformanceError(RuntimeError):
    """Raised when candidate performance evidence is malformed or conflicting."""


@dataclass(frozen=True)
class ExpandedCandidatePerformanceRecord:
    source_basDd: str
    ticker: str
    stock_name: str
    market: str
    signal_date: str
    entry_price: float
    signal_score: float
    foreign_status: str
    engine_version: str
    source_run_id: str
    source_commit: str
    source_created_at: str
    registered_at: str
    tracking_status: str = STATUS_OPEN
    return_5d: float | None = None
    return_10d: float | None = None
    return_20d: float | None = None
    benchmark_5d: float | None = None
    benchmark_10d: float | None = None
    benchmark_20d: float | None = None
    excess_5d: float | None = None
    excess_10d: float | None = None
    excess_20d: float | None = None
    completed_at: str | None = None
    updated_at: str | None = None


PERFORMANCE_FIELDS = [field.name for field in fields(ExpandedCandidatePerformanceRecord)]
IDENTITY_FIELDS = ("source_basDd", "ticker", "signal_date", "engine_version")
IMMUTABLE_FIELDS = (
    "source_basDd",
    "ticker",
    "stock_name",
    "market",
    "signal_date",
    "entry_price",
    "signal_score",
    "foreign_status",
    "engine_version",
    "source_run_id",
    "source_commit",
    "source_created_at",
    "registered_at",
)
RETURN_FIELDS = {horizon: RETURN_FIELD_BY_HORIZON[horizon] for horizon in FORWARD_HORIZONS}
BENCHMARK_FIELDS = {horizon: f"benchmark_{horizon}d" for horizon in FORWARD_HORIZONS}
EXCESS_FIELDS = {horizon: f"excess_{horizon}d" for horizon in FORWARD_HORIZONS}
METRIC_FIELDS = tuple(RETURN_FIELDS.values()) + tuple(BENCHMARK_FIELDS.values()) + tuple(EXCESS_FIELDS.values())


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_candidate_records(
    signal_ledger: pd.DataFrame,
    *,
    registered_at: str,
) -> list[ExpandedCandidatePerformanceRecord]:
    if signal_ledger is None or signal_ledger.empty:
        return []
    missing = sorted(REQUIRED_SIGNAL_COLUMNS - set(signal_ledger.columns))
    if missing:
        raise ExpandedCandidatePerformanceError(f"Expanded signal ledger missing columns: {missing}")

    candidates = signal_ledger.loc[signal_ledger["decision"].astype(str) == "CANDIDATE"]
    records: list[ExpandedCandidatePerformanceRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for _, row in candidates.iterrows():
        record = ExpandedCandidatePerformanceRecord(
            source_basDd=str(row["basDd"]),
            ticker=str(row["stock_code"]),
            stock_name=str(row["stock_name"]),
            market=str(row["market"]),
            signal_date=str(row["signal_date"]),
            entry_price=_positive_float(row["signal_price"], "signal_price"),
            signal_score=float(row["signal_score"]),
            foreign_status=str(row["foreign_status"]),
            engine_version=str(row["engine_version"]),
            source_run_id=str(row["run_id"]),
            source_commit=str(row["source_commit"]),
            source_created_at=str(row["created_at"]),
            registered_at=registered_at,
        )
        key = _record_key(record)
        if key in seen:
            raise ExpandedCandidatePerformanceError(f"duplicate candidate identity in signal ledger: {key}")
        seen.add(key)
        records.append(record)
    return records


class ExpandedCandidatePerformanceStore:
    def __init__(self, paths: ExpandedShadowPaths, path: Path | None = None) -> None:
        self.paths = paths
        self.path = paths.validate_output_path(path or (paths.output_root / DEFAULT_LEDGER_NAME))

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame(columns=PERFORMANCE_FIELDS)
        frame = pd.read_csv(
            self.path,
            dtype={
                "source_basDd": str,
                "ticker": str,
                "signal_date": str,
                "engine_version": str,
                "source_run_id": str,
            },
        )
        if list(frame.columns) != PERFORMANCE_FIELDS:
            raise ExpandedCandidatePerformanceError(
                f"performance ledger columns must be exactly {PERFORMANCE_FIELDS}, got {list(frame.columns)}"
            )
        if frame.duplicated(subset=list(IDENTITY_FIELDS)).any():
            raise ExpandedCandidatePerformanceError("duplicate identities in performance ledger")
        invalid_statuses = sorted(set(frame["tracking_status"].dropna().astype(str)) - set(STATUS_RANK))
        if invalid_statuses:
            raise ExpandedCandidatePerformanceError(f"invalid tracking statuses: {invalid_statuses}")
        for column in ("completed_at", "updated_at"):
            frame[column] = frame[column].astype(object)
        return frame

    def synchronize(
        self,
        signal_ledger: pd.DataFrame,
        *,
        price_map: dict[str, pd.DataFrame],
        benchmark_map: dict[str, pd.DataFrame],
        now_func: Callable[[], str] = utc_now_iso,
        dry_run: bool = False,
    ) -> dict[str, int]:
        now = now_func()
        incoming = build_candidate_records(signal_ledger, registered_at=now)
        frame = self.load()
        stats = _empty_stats(len(incoming))

        frame, registered, duplicates = _register_candidates(frame, incoming)
        stats["registered"] = registered
        stats["duplicate_candidates"] = duplicates
        frame, update_stats = _update_pending(frame, price_map, benchmark_map, now)
        for key, value in update_stats.items():
            stats[key] += value

        if not dry_run and (registered or stats["updated"]):
            _write_atomic(self.path, frame)
        return stats


def run_expanded_candidate_performance(
    *,
    repo_root: Path,
    price_map: dict[str, pd.DataFrame],
    benchmark_map: dict[str, pd.DataFrame],
    now_func: Callable[[], str] = utc_now_iso,
    dry_run: bool = False,
    store: ExpandedCandidatePerformanceStore | None = None,
) -> dict[str, int]:
    paths = ExpandedShadowPaths(repo_root)
    signal_path = paths.signal_ledger_path
    if signal_path.exists():
        signal_ledger = pd.read_csv(
            signal_path,
            dtype={"basDd": str, "stock_code": str, "signal_date": str, "engine_version": str},
        )
    else:
        signal_ledger = pd.DataFrame(columns=sorted(REQUIRED_SIGNAL_COLUMNS))
    resolved_store = store or ExpandedCandidatePerformanceStore(paths)
    return resolved_store.synchronize(
        signal_ledger,
        price_map=price_map,
        benchmark_map=benchmark_map,
        now_func=now_func,
        dry_run=dry_run,
    )


def _register_candidates(
    frame: pd.DataFrame,
    incoming: list[ExpandedCandidatePerformanceRecord],
) -> tuple[pd.DataFrame, int, int]:
    registered = 0
    duplicates = 0
    rows = frame.to_dict(orient="records")
    existing_by_key = {_row_key(row): row for row in rows}
    for record in incoming:
        key = _record_key(record)
        existing = existing_by_key.get(key)
        if existing is not None:
            _assert_same_candidate(existing, record)
            duplicates += 1
            continue
        payload = asdict(record)
        rows.append(payload)
        existing_by_key[key] = payload
        registered += 1
    result = pd.DataFrame(rows, columns=PERFORMANCE_FIELDS)
    for column in ("completed_at", "updated_at"):
        result[column] = result[column].astype(object)
    return result, registered, duplicates


def _update_pending(
    frame: pd.DataFrame,
    price_map: dict[str, pd.DataFrame],
    benchmark_map: dict[str, pd.DataFrame],
    now: str,
) -> tuple[pd.DataFrame, dict[str, int]]:
    stats = _empty_stats(0)
    if frame.empty:
        return frame, stats

    for index, row in frame.iterrows():
        if str(row["tracking_status"]) == STATUS_COMPLETE:
            stats["already_complete"] += 1
            continue

        ticker = str(row["ticker"])
        price_frame = price_map.get(ticker)
        stock_returns = (
            None
            if price_frame is None
            else compute_forward_returns(price_frame, str(row["signal_date"]), float(row["entry_price"]))
        )
        if stock_returns is None:
            stats["missing_price"] += 1
            continue

        symbol = normalize_market(row["market"])
        if symbol is None:
            stats["unknown_market"] += 1
            benchmark_returns = None
        else:
            benchmark_frame = benchmark_map.get(symbol)
            benchmark_returns = (
                None
                if benchmark_frame is None
                else compute_benchmark_returns(benchmark_frame, str(row["signal_date"]))
            )
            if benchmark_returns is None:
                stats["missing_benchmark"] += 1

        changed = False
        for horizon in FORWARD_HORIZONS:
            return_field = RETURN_FIELDS[horizon]
            benchmark_field = BENCHMARK_FIELDS[horizon]
            excess_field = EXCESS_FIELDS[horizon]
            changed |= _merge_metric(frame, index, return_field, stock_returns[return_field], stats)
            benchmark_value = None if benchmark_returns is None else benchmark_returns[BENCHMARK_FIELD_BY_HORIZON[horizon]]
            changed |= _merge_metric(frame, index, benchmark_field, benchmark_value, stats)
            final_return = _optional_float(frame.at[index, return_field])
            final_benchmark = _optional_float(frame.at[index, benchmark_field])
            changed |= _merge_metric(
                frame,
                index,
                excess_field,
                compute_excess(final_return, final_benchmark),
                stats,
            )

        old_status = str(row["tracking_status"])
        new_status = _resolve_tracking_status(frame.loc[index])
        if STATUS_RANK[new_status] < STATUS_RANK[old_status]:
            new_status = old_status
        if new_status != old_status:
            frame.at[index, "tracking_status"] = new_status
            stats[f"advanced_to_{new_status.lower()}"] += 1
            changed = True
        if new_status == STATUS_COMPLETE and _is_missing(frame.at[index, "completed_at"]):
            frame.at[index, "completed_at"] = now
            changed = True
        if changed:
            frame.at[index, "updated_at"] = now
            stats["updated"] += 1
        else:
            stats["pending"] += 1
    return frame, stats


def _merge_metric(
    frame: pd.DataFrame,
    index: int,
    field: str,
    computed: float | None,
    stats: dict[str, int],
) -> bool:
    existing = _optional_float(frame.at[index, field])
    if existing is not None:
        if computed is not None and abs(existing - float(computed)) > RETURN_MISMATCH_TOLERANCE:
            stats["mismatch"] += 1
        return False
    if computed is None:
        return False
    frame.at[index, field] = float(computed)
    stats[f"new_{field}"] += 1
    return True


def _resolve_tracking_status(row: pd.Series) -> str:
    has_5 = _has_metric(row, RETURN_FIELDS[5])
    has_10 = has_5 and _has_metric(row, RETURN_FIELDS[10])
    has_20 = has_10 and _has_metric(row, RETURN_FIELDS[20])
    if has_20 and all(_has_metric(row, field) for field in METRIC_FIELDS):
        return STATUS_COMPLETE
    if has_20:
        return STATUS_20D
    if has_10:
        return STATUS_10D
    if has_5:
        return STATUS_5D
    return STATUS_OPEN


def _empty_stats(candidate_count: int) -> dict[str, int]:
    stats = {
        "candidate_count": candidate_count,
        "registered": 0,
        "duplicate_candidates": 0,
        "updated": 0,
        "pending": 0,
        "already_complete": 0,
        "missing_price": 0,
        "missing_benchmark": 0,
        "unknown_market": 0,
        "mismatch": 0,
        "advanced_to_5d": 0,
        "advanced_to_10d": 0,
        "advanced_to_20d": 0,
        "advanced_to_complete": 0,
    }
    for field in METRIC_FIELDS:
        stats[f"new_{field}"] = 0
    return stats


def _assert_same_candidate(existing: dict[str, object], record: ExpandedCandidatePerformanceRecord) -> None:
    expected = asdict(record)
    for field in IMMUTABLE_FIELDS:
        if field == "registered_at":
            continue
        actual = existing.get(field)
        wanted = expected[field]
        if field in {"entry_price", "signal_score"}:
            if _optional_float(actual) is not None and abs(float(actual) - float(wanted)) <= RETURN_MISMATCH_TOLERANCE:
                continue
        elif str(actual) == str(wanted):
            continue
        raise ExpandedCandidatePerformanceError(f"conflicting candidate identity {_record_key(record)}: {field}")


def _record_key(record: ExpandedCandidatePerformanceRecord) -> tuple[str, str, str, str]:
    return tuple(str(getattr(record, field)) for field in IDENTITY_FIELDS)  # type: ignore[return-value]


def _row_key(row: dict[str, object]) -> tuple[str, str, str, str]:
    return tuple(str(row[field]) for field in IDENTITY_FIELDS)  # type: ignore[return-value]


def _positive_float(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpandedCandidatePerformanceError(f"invalid {label}: {value!r}") from exc
    if not pd.notna(result) or result <= 0:
        raise ExpandedCandidatePerformanceError(f"invalid {label}: {value!r}")
    return result


def _optional_float(value: object) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_missing(value: object) -> bool:
    return value is None or bool(pd.isna(value))


def _has_metric(row: pd.Series, field: str) -> bool:
    return _optional_float(row.get(field)) is not None


def _write_atomic(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False, lineterminator="\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
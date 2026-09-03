from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.adapter.sources import GIT_BASELINE_SOURCE, SourceAllowlist
from dashboard.contracts.dashboard_contract import (
    DATA_HISTORICAL_VALIDATION,
    DATA_METADATA,
    DATA_OPERATIONAL,
    DEFAULT_BASELINE_COMMIT,
    STATUS_AVAILABLE,
    STATUS_EMPTY,
    STATUS_MISSING,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
)


SHADOW_LEDGER_SOURCE = "output/shadow_signal_records.csv"
FINAL_COMPARISON_SOURCE = "output/v02_step9_final_comparison.csv"
RISK_REVIEW_SOURCE = "output/v02_step9_final_risk_review.csv"
OPPORTUNITY_COST_SOURCE = "output/v02_step8_filtered_opportunity_cost.csv"
FOREIGN_FLOW_SOURCE = "output/v02_step3_foreign_score_performance.csv"
FILTER_OPPORTUNITY_SOURCE = "output/v02_step6_filter_opportunity_cost.csv"

REQUIRED_LEDGER_COLUMNS = (
    "stock_code",
    "stock_name",
    "market",
    "signal_date",
    "signal_price",
    "signal_score",
    "foreign_status",
    "decision",
    "created_at",
    "status",
)

VALID_DECISIONS = {"CANDIDATE", "EXCLUDED"}
FORWARD_HORIZONS = (5, 10, 20)


@dataclass(frozen=True)
class ReadResult:
    status: str
    source: str
    data_kind: str
    rows: list[dict[str, Any]]
    as_of: str | None = None
    warnings: list[str] | None = None

    @property
    def sample_size(self) -> int:
        return len(self.rows)


class ShadowLedgerReader:
    def __init__(self, allowlist: SourceAllowlist, stale_after_days: int = 5):
        self.allowlist = allowlist
        self.stale_after_days = stale_after_days

    def read(self, today: date | None = None) -> ReadResult:
        path = self.allowlist.resolve(SHADOW_LEDGER_SOURCE)
        if not path.exists():
            return ReadResult(
                status=STATUS_MISSING,
                source=SHADOW_LEDGER_SOURCE,
                data_kind=DATA_OPERATIONAL,
                rows=[],
                warnings=["shadow ledger file is missing"],
            )
        if path.stat().st_size == 0:
            return ReadResult(
                status=STATUS_EMPTY,
                source=SHADOW_LEDGER_SOURCE,
                data_kind=DATA_OPERATIONAL,
                rows=[],
                warnings=["shadow ledger file is empty"],
            )

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    return ReadResult(STATUS_EMPTY, SHADOW_LEDGER_SOURCE, DATA_OPERATIONAL, [], warnings=["shadow ledger has no header"])
                missing_columns = [column for column in REQUIRED_LEDGER_COLUMNS if column not in reader.fieldnames]
                if missing_columns:
                    return ReadResult(
                        STATUS_UNAVAILABLE,
                        SHADOW_LEDGER_SOURCE,
                        DATA_OPERATIONAL,
                        [],
                        warnings=[f"shadow ledger missing required columns: {', '.join(missing_columns)}"],
                    )
                rows = list(reader)
        except (OSError, csv.Error, UnicodeDecodeError) as exc:
            return ReadResult(STATUS_UNAVAILABLE, SHADOW_LEDGER_SOURCE, DATA_OPERATIONAL, [], warnings=[str(exc)])

        if not rows:
            return ReadResult(STATUS_EMPTY, SHADOW_LEDGER_SOURCE, DATA_OPERATIONAL, [], warnings=["shadow ledger has no records"])

        valid_rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for row_number, row in enumerate(rows, start=2):
            row_warning = self._validate_row(row, row_number)
            if row_warning:
                warnings.append(row_warning)
                continue
            valid_rows.append(row)

        if warnings:
            return ReadResult(
                STATUS_UNAVAILABLE,
                SHADOW_LEDGER_SOURCE,
                DATA_OPERATIONAL,
                valid_rows,
                as_of=self._max_signal_date(valid_rows),
                warnings=warnings,
            )

        as_of = self._max_signal_date(valid_rows)
        status = STATUS_AVAILABLE
        if as_of and self._is_stale(as_of, today or date.today()):
            return ReadResult(
                STATUS_STALE,
                SHADOW_LEDGER_SOURCE,
                DATA_OPERATIONAL,
                valid_rows,
                as_of=as_of,
                warnings=["shadow ledger data is stale"],
            )
        return ReadResult(status, SHADOW_LEDGER_SOURCE, DATA_OPERATIONAL, valid_rows, as_of=as_of)

    def _validate_row(self, row: dict[str, Any], row_number: int) -> str | None:
        for column in REQUIRED_LEDGER_COLUMNS:
            if row.get(column) in (None, ""):
                return f"row {row_number} missing required value: {column}"
        if row.get("decision") not in VALID_DECISIONS:
            return f"row {row_number} has invalid decision: {row.get('decision')}"
        try:
            float(row["signal_price"])
            float(row["signal_score"])
            date.fromisoformat(str(row["signal_date"]))
        except (TypeError, ValueError) as exc:
            return f"row {row_number} is malformed: {exc}"
        return None

    def _max_signal_date(self, rows: list[dict[str, Any]]) -> str | None:
        dates: list[str] = []
        for row in rows:
            value = row.get("signal_date")
            if value:
                dates.append(str(value))
        return max(dates) if dates else None

    def _is_stale(self, as_of: str, today: date) -> bool:
        try:
            return (today - date.fromisoformat(as_of)).days > self.stale_after_days
        except ValueError:
            return False


class HistoricalValidationReader:
    def __init__(self, allowlist: SourceAllowlist):
        self.allowlist = allowlist

    def read_csv(self, source: str) -> ReadResult:
        path = self.allowlist.resolve(source)
        if not path.exists():
            return ReadResult(STATUS_MISSING, source, DATA_HISTORICAL_VALIDATION, [], warnings=["historical validation file is missing"])
        try:
            frame = pd.read_csv(path)
        except (OSError, pd.errors.ParserError, UnicodeDecodeError) as exc:
            return ReadResult(STATUS_UNAVAILABLE, source, DATA_HISTORICAL_VALIDATION, [], warnings=[str(exc)])
        if frame.empty:
            return ReadResult(STATUS_EMPTY, source, DATA_HISTORICAL_VALIDATION, [], warnings=["historical validation file has no rows"])
        return ReadResult(
            STATUS_AVAILABLE,
            source,
            DATA_HISTORICAL_VALIDATION,
            frame.where(pd.notna(frame), None).to_dict("records"),
            as_of=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat(),
        )

    def metric_value(self, source: str, *, strategy: str, metric: str) -> dict[str, Any]:
        result = self.read_csv(source)
        if result.status != STATUS_AVAILABLE:
            return {"value": None, "sample_size": 0, "status": result.status, "source": source, "as_of": result.as_of, "data_kind": result.data_kind, "warnings": result.warnings or []}
        for row in result.rows:
            if row.get("Strategy") == strategy and row.get("Metric") == metric:
                return {"value": row.get("Value"), "sample_size": result.sample_size, "status": STATUS_AVAILABLE, "source": source, "as_of": result.as_of, "data_kind": result.data_kind}
        return {"value": None, "sample_size": result.sample_size, "status": STATUS_UNAVAILABLE, "source": source, "as_of": result.as_of, "data_kind": result.data_kind, "warnings": [f"metric not found: {strategy}/{metric}"]}


class BaselineMetadataReader:
    def __init__(self, allowlist: SourceAllowlist, baseline_commit: str = DEFAULT_BASELINE_COMMIT):
        self.allowlist = allowlist
        self.baseline_commit = baseline_commit

    def read(self) -> dict[str, Any]:
        source = self.allowlist.require_metadata(GIT_BASELINE_SOURCE)
        return {
            "baseline_commit": self.baseline_commit,
            "status": STATUS_AVAILABLE,
            "source": source,
            "data_kind": DATA_METADATA,
        }

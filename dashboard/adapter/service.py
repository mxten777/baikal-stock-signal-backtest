from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from dashboard.adapter.readers import (
    FILTER_OPPORTUNITY_SOURCE,
    FINAL_COMPARISON_SOURCE,
    FOREIGN_FLOW_SOURCE,
    OPERATIONAL_METADATA_SOURCE,
    OPPORTUNITY_COST_SOURCE,
    RISK_REVIEW_SOURCE,
    BaselineMetadataReader,
    HistoricalValidationReader,
    OperationalMetadataReader,
    ReadResult,
    ShadowLedgerReader,
)
from dashboard.adapter.sources import SourceAllowlist
from dashboard.contracts.dashboard_contract import (
    DATA_HISTORICAL_VALIDATION,
    DATA_OPERATIONAL,
    MODE_SHADOW,
    STATUS_AVAILABLE,
    STATUS_EMPTY,
    STATUS_MISSING,
    STATUS_STALE,
    STATUS_UNAVAILABLE,
    unavailable_metric,
)


READABLE_LEDGER_STATUSES = {STATUS_AVAILABLE, STATUS_STALE}


class DashboardService:
    def __init__(self, repo_root: Path, baseline_commit: str = "38e56c5"):
        self.allowlist = SourceAllowlist(repo_root=repo_root)
        self.ledger_reader = ShadowLedgerReader(self.allowlist)
        self.historical_reader = HistoricalValidationReader(self.allowlist)
        self.baseline_reader = BaselineMetadataReader(self.allowlist, baseline_commit=baseline_commit)
        self.operational_metadata_reader = OperationalMetadataReader(self.allowlist)

    def overview(self, today: date | None = None) -> dict[str, Any]:
        ledger = self.ledger_reader.read(today=today)
        metadata = self.baseline_reader.read()
        operational_metadata = self.operational_metadata_reader.read()
        return {
            "system": self._system(metadata, ledger, operational_metadata, today=today),
            "today": self._today(ledger),
            "maturity": self._maturity(ledger),
            "performance": self._performance(),
            "foreign_flow": self._foreign_flow(),
            "weakness": self._weakness(),
            "risk": self._risk(ledger),
            "opportunity_cost": self._opportunity_cost(),
            "signal_ledger": self._signal_ledger(ledger),
        }

    def signals(self) -> dict[str, Any]:
        ledger = self.ledger_reader.read()
        return self._signal_ledger(ledger)

    def health(self) -> dict[str, Any]:
        ledger = self.ledger_reader.read()
        metadata = self.baseline_reader.read()
        return {
            "mode": MODE_SHADOW,
            "read_only": True,
            "baseline_commit": metadata["baseline_commit"],
            "ledger_status": ledger.status,
            "allowed_sources": sorted(self.allowlist.allowed_files),
            "write_endpoints": [],
        }

    def _system(
        self,
        metadata: dict[str, Any],
        ledger: ReadResult,
        operational_metadata: ReadResult,
        today: date | None = None,
    ) -> dict[str, Any]:
        operational_payload = operational_metadata.rows[0] if operational_metadata.status == STATUS_AVAILABLE and operational_metadata.rows else None
        warnings = (ledger.warnings or []) + (operational_metadata.warnings or [])
        return {
            "mode": MODE_SHADOW,
            "read_only": True,
            "baseline_commit": metadata["baseline_commit"],
            "pipeline_status": self._metadata_metric(operational_payload, operational_metadata, "pipeline_status"),
            "last_run": self._metadata_metric(operational_payload, operational_metadata, "finished_at"),
            "data_date": self._data_date_metric(operational_payload, operational_metadata, ledger, today=today),
            "market_data_date": self._input_date_metric(operational_payload, operational_metadata, "market_data_max_date"),
            "investor_data_date": self._input_date_metric(operational_payload, operational_metadata, "investor_data_max_date"),
            "input_data_freshness": self._input_freshness_metric(operational_payload, operational_metadata),
            "ledger_status": self._ledger_status_metric(operational_payload, operational_metadata, ledger),
            "freshness": self._input_freshness_metric(operational_payload, operational_metadata),
            "warnings": warnings,
        }

    def _metadata_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
        field: str,
    ) -> dict[str, Any]:
        if payload is None:
            return unavailable_metric(OPERATIONAL_METADATA_SOURCE, DATA_OPERATIONAL, "; ".join(metadata.warnings or ["shadow dashboard run metadata is unavailable"]))
        value = payload.get(field)
        if value in (None, ""):
            return unavailable_metric(OPERATIONAL_METADATA_SOURCE, DATA_OPERATIONAL, f"shadow dashboard run metadata missing {field}")
        return {
            "value": value,
            "sample_size": int(payload.get("record_count") or 0),
            "status": STATUS_AVAILABLE,
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
        }

    def _data_date_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
        ledger: ReadResult,
        today: date | None = None,
    ) -> dict[str, Any]:
        if payload is None:
            return {"value": ledger.as_of, "status": ledger.status, "source": ledger.source, "data_kind": DATA_OPERATIONAL}
        value = payload.get("signal_base_date")
        if value in (None, ""):
            return unavailable_metric(OPERATIONAL_METADATA_SOURCE, DATA_OPERATIONAL, "shadow dashboard run metadata signal_base_date is unavailable")
        return {
            "value": value,
            "sample_size": int(payload.get("record_count") or 0),
            "status": STATUS_AVAILABLE,
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
        }

    def _freshness_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
        ledger: ReadResult,
        today: date | None = None,
    ) -> dict[str, Any]:
        if payload is None:
            return {"value": ledger.status, "status": ledger.status, "source": ledger.source, "data_kind": DATA_OPERATIONAL}
        signal_base_date = payload.get("signal_base_date")
        ledger_status = str(payload.get("ledger_status"))
        signal_base_date_text = None if signal_base_date in (None, "") else str(signal_base_date)
        status = self._freshness_status(signal_base_date_text, ledger_status, today)
        return {
            "value": status,
            "sample_size": int(payload.get("record_count") or 0),
            "status": status,
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
        }

    def _freshness_status(self, signal_base_date: str | None, ledger_status: str, today: date | None) -> str:
        if ledger_status == STATUS_MISSING:
            return STATUS_MISSING
        if ledger_status == STATUS_EMPTY:
            return STATUS_EMPTY
        if ledger_status != STATUS_AVAILABLE:
            return STATUS_UNAVAILABLE
        if not signal_base_date:
            return STATUS_UNAVAILABLE
        try:
            age = ((today or date.today()) - date.fromisoformat(signal_base_date)).days
        except ValueError:
            return STATUS_UNAVAILABLE
        return STATUS_STALE if age > self.ledger_reader.stale_after_days else STATUS_AVAILABLE

    def _input_date_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
        field: str,
    ) -> dict[str, Any]:
        if payload is None:
            return unavailable_metric(OPERATIONAL_METADATA_SOURCE, DATA_OPERATIONAL, "; ".join(metadata.warnings or ["shadow dashboard run metadata is unavailable"]))
        value = payload.get(field)
        if value in (None, ""):
            return {
                "value": None,
                "sample_size": int(payload.get("record_count") or 0),
                "status": STATUS_MISSING,
                "source": OPERATIONAL_METADATA_SOURCE,
                "as_of": metadata.as_of,
                "data_kind": DATA_OPERATIONAL,
                "warnings": [f"shadow dashboard run metadata missing {field}"],
            }
        freshness = str(payload.get("input_data_freshness") or STATUS_UNAVAILABLE)
        status = STATUS_STALE if freshness == STATUS_STALE else STATUS_AVAILABLE
        if freshness in {STATUS_MISSING, STATUS_UNAVAILABLE}:
            status = freshness
        return {
            "value": value,
            "sample_size": int(payload.get("record_count") or 0),
            "status": status,
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
        }

    def _input_freshness_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
    ) -> dict[str, Any]:
        if payload is None:
            return unavailable_metric(OPERATIONAL_METADATA_SOURCE, DATA_OPERATIONAL, "; ".join(metadata.warnings or ["shadow dashboard run metadata is unavailable"]))
        value = str(payload.get("input_data_freshness") or STATUS_UNAVAILABLE)
        return {
            "value": value,
            "sample_size": int(payload.get("record_count") or 0),
            "status": _contract_status(value),
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
        }

    def _ledger_status_metric(
        self,
        payload: dict[str, Any] | None,
        metadata: ReadResult,
        ledger: ReadResult,
    ) -> dict[str, Any]:
        if payload is None:
            return {"value": ledger.status, "sample_size": ledger.sample_size, "status": ledger.status, "source": ledger.source, "as_of": ledger.as_of, "data_kind": DATA_OPERATIONAL, "warnings": ledger.warnings or []}
        value = str(payload.get("ledger_status") or STATUS_UNAVAILABLE)
        return {
            "value": value,
            "sample_size": int(payload.get("record_count") or 0),
            "status": _contract_status(value),
            "source": OPERATIONAL_METADATA_SOURCE,
            "as_of": metadata.as_of,
            "data_kind": DATA_OPERATIONAL,
            "warnings": [str(payload.get("ledger_warning"))] if payload.get("ledger_warning") else [],
        }

    def _today(self, ledger: ReadResult) -> dict[str, Any]:
        if ledger.status not in READABLE_LEDGER_STATUSES:
            return {
                "new_signals": self._count_metric(None, ledger),
                "candidates": self._count_metric(None, ledger),
                "excluded": self._count_metric(None, ledger),
                "kosdaq": self._count_metric(None, ledger),
                "high": unavailable_metric(ledger.source, DATA_OPERATIONAL, "HIGH classification is not present in the operational ledger contract"),
            }
        today_value = ledger.as_of
        rows = [row for row in ledger.rows if row.get("signal_date") == today_value]
        decisions = Counter(row.get("decision") for row in rows)
        kosdaq_count = sum(1 for row in rows if str(row.get("market", "")).upper() in {"KOSDAQ", "KQ11"})
        return {
            "new_signals": self._count_metric(len(rows), ledger),
            "candidates": self._count_metric(decisions.get("CANDIDATE", 0), ledger),
            "excluded": self._count_metric(decisions.get("EXCLUDED", 0), ledger),
            "kosdaq": self._count_metric(kosdaq_count, ledger),
            "high": unavailable_metric(ledger.source, DATA_OPERATIONAL, "HIGH classification is not present in the operational ledger contract"),
        }

    def _maturity(self, ledger: ReadResult) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for horizon in (5, 10, 20):
            field = f"return_{horizon}d"
            matured = sum(1 for row in ledger.rows if row.get(field) not in (None, "")) if ledger.status in READABLE_LEDGER_STATUSES else None
            pending = (ledger.sample_size - matured) if matured is not None else None
            result[f"{horizon}d"] = {
                "matured": self._count_metric(matured, ledger),
                "pending": self._count_metric(pending, ledger),
            }
        return result

    def _performance(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for horizon in (5, 10, 20):
            key = f"{horizon}d"
            output[key] = {
                "candidate_return": self.historical_reader.metric_value(FINAL_COMPARISON_SOURCE, strategy="CANDIDATE", metric=f"Avg Return {horizon}D"),
                "candidate_excess_return": self.historical_reader.metric_value(FINAL_COMPARISON_SOURCE, strategy="CANDIDATE", metric=f"Avg Excess {horizon}D"),
                "candidate_win_rate": self.historical_reader.metric_value(FINAL_COMPARISON_SOURCE, strategy="CANDIDATE", metric=f"Win Rate {horizon}D (%)"),
                "candidate_vs_excluded": self.historical_reader.metric_value(FINAL_COMPARISON_SOURCE, strategy="CANDIDATE - EXCLUDED", metric=f"Avg Excess {horizon}D"),
            }
        return output

    def _foreign_flow(self) -> dict[str, Any]:
        result = self.historical_reader.read_csv(FOREIGN_FLOW_SOURCE)
        return {"status": result.status, "source": result.source, "data_kind": result.data_kind, "sample_size": result.sample_size, "rows": result.rows if result.status == STATUS_AVAILABLE else [], "warnings": result.warnings or []}

    def _weakness(self) -> dict[str, Any]:
        return {
            "HIGH": unavailable_metric(FOREIGN_FLOW_SOURCE, DATA_HISTORICAL_VALIDATION, "HIGH weakness requires an explicit validation mapping in a later step"),
            "KOSDAQ": unavailable_metric("output/v02_step7_filter_by_market.csv", DATA_HISTORICAL_VALIDATION, "KOSDAQ weakness is allowed but not normalized in STEP 2"),
            "HIGH_x_KOSDAQ": unavailable_metric(None, DATA_HISTORICAL_VALIDATION, "HIGH x KOSDAQ cross metric is unavailable in STEP 2"),
        }

    def _risk(self, ledger: ReadResult) -> dict[str, Any]:
        historical = self.historical_reader.read_csv(RISK_REVIEW_SOURCE)
        return {
            "operational": {"status": ledger.status, "source": ledger.source, "data_kind": DATA_OPERATIONAL, "sample_size": ledger.sample_size, "warnings": ledger.warnings or []},
            "historical_validation": {"status": historical.status, "source": historical.source, "data_kind": historical.data_kind, "sample_size": historical.sample_size, "rows": historical.rows if historical.status == STATUS_AVAILABLE else [], "warnings": historical.warnings or []},
        }

    def _opportunity_cost(self) -> dict[str, Any]:
        filtered = self.historical_reader.read_csv(OPPORTUNITY_COST_SOURCE)
        filter_summary = self.historical_reader.read_csv(FILTER_OPPORTUNITY_SOURCE)
        return {
            "filtered_opportunity_cost": {"status": filtered.status, "source": filtered.source, "data_kind": filtered.data_kind, "sample_size": filtered.sample_size, "rows": filtered.rows if filtered.status == STATUS_AVAILABLE else [], "warnings": filtered.warnings or []},
            "filter_summary": {"status": filter_summary.status, "source": filter_summary.source, "data_kind": filter_summary.data_kind, "sample_size": filter_summary.sample_size, "rows": filter_summary.rows if filter_summary.status == STATUS_AVAILABLE else [], "warnings": filter_summary.warnings or []},
        }

    def _signal_ledger(self, ledger: ReadResult) -> dict[str, Any]:
        return {
            "status": ledger.status,
            "source": ledger.source,
            "data_kind": ledger.data_kind,
            "as_of": ledger.as_of,
            "sample_size": ledger.sample_size,
            "records": ledger.rows if ledger.status in READABLE_LEDGER_STATUSES else [],
            "warnings": ledger.warnings or [],
        }

    def _count_metric(self, value: int | None, ledger: ReadResult) -> dict[str, Any]:
        return {
            "value": value,
            "sample_size": ledger.sample_size,
            "status": ledger.status,
            "source": ledger.source,
            "as_of": ledger.as_of,
            "data_kind": DATA_OPERATIONAL,
            "warnings": ledger.warnings or [],
        }


def _contract_status(value: str) -> str:
    if value == "CURRENT":
        return STATUS_AVAILABLE
    if value in {STATUS_STALE, STATUS_MISSING, STATUS_UNAVAILABLE, STATUS_EMPTY}:
        return value
    return STATUS_UNAVAILABLE

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from dashboard.adapter.readers import FINAL_COMPARISON_SOURCE, RISK_REVIEW_SOURCE, BaselineMetadataReader, HistoricalValidationReader, ShadowLedgerReader
from dashboard.adapter.service import DashboardService
from dashboard.adapter.sources import SourceAllowlist
from dashboard.api import READ_ONLY_ENDPOINTS, route_dashboard_request


LEDGER_HEADER = [
    "stock_code",
    "stock_name",
    "market",
    "signal_date",
    "signal_price",
    "signal_score",
    "foreign_status",
    "decision",
    "exclusion_reason",
    "created_at",
    "status",
    "return_5d",
    "return_10d",
    "return_20d",
    "benchmark_return_5d",
    "benchmark_return_10d",
    "benchmark_return_20d",
    "excess_5d",
    "excess_10d",
    "excess_20d",
]


def _root(tmp_path: Path) -> Path:
    (tmp_path / "output").mkdir()
    return tmp_path


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_minimal_historical(root: Path) -> None:
    _write_csv(
        root / FINAL_COMPARISON_SOURCE,
        ["Scope", "Group", "Strategy", "Metric", "Value"],
        [
            ["OVERALL", "ALL", "CANDIDATE", "Avg Return 5D", 1.0],
            ["OVERALL", "ALL", "CANDIDATE", "Avg Excess 5D", 0.5],
            ["OVERALL", "ALL", "CANDIDATE", "Win Rate 5D (%)", 55.0],
            ["OVERALL", "ALL", "CANDIDATE - EXCLUDED", "Avg Excess 5D", 2.5],
        ],
    )
    _write_csv(root / RISK_REVIEW_SOURCE, ["Strategy", "Metric", "Value"], [["CANDIDATE", "Max Drawdown 20D", -10.0]])
    _write_csv(root / "output/v02_step8_filtered_opportunity_cost.csv", ["Filtered N", "Avg Excess 20D"], [[37, -4.24]])
    _write_csv(root / "output/v02_step6_filter_opportunity_cost.csv", ["Filtered Class", "N"], [["NEGATIVE", 37]])
    _write_csv(root / "output/v02_step3_foreign_score_performance.csv", ["Factor", "Group", "N"], [["FOREIGN", "HIGH", 28]])


class TestShadowLedgerReader:
    def test_ledger_missing_is_normal_state(self, tmp_path):
        root = _root(tmp_path)
        result = ShadowLedgerReader(SourceAllowlist(root)).read()
        assert result.status == "MISSING"
        assert result.sample_size == 0


    def test_stale_ledger_keeps_rows_with_stale_status(self, tmp_path):
        root = _root(tmp_path)
        _write_csv(
            root / "output/shadow_signal_records.csv",
            LEDGER_HEADER,
            [["005930", "Samsung", "KOSPI", "2026-01-01", 70000, 80, "POSITIVE", "CANDIDATE", "", "2026-01-01T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
        )
        result = ShadowLedgerReader(SourceAllowlist(root)).read()
        signals = DashboardService(root).signals()
        assert result.status == "STALE"
        assert signals["records"][0]["stock_code"] == "005930"

    def test_ledger_empty_header_is_empty(self, tmp_path):
        root = _root(tmp_path)
        _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
        result = ShadowLedgerReader(SourceAllowlist(root)).read()
        assert result.status == "EMPTY"
        assert result.rows == []

    def test_valid_ledger_is_available(self, tmp_path):
        root = _root(tmp_path)
        _write_csv(
            root / "output/shadow_signal_records.csv",
            LEDGER_HEADER,
            [["005930", "Samsung", "KOSPI", "2026-09-04", 70000, 80, "POSITIVE", "CANDIDATE", "", "2026-09-04T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
        )
        result = ShadowLedgerReader(SourceAllowlist(root)).read()
        assert result.status == "AVAILABLE"
        assert result.sample_size == 1
        assert result.as_of == "2026-09-04"

    def test_malformed_row_is_unavailable_not_crash(self, tmp_path):
        root = _root(tmp_path)
        _write_csv(
            root / "output/shadow_signal_records.csv",
            LEDGER_HEADER,
            [["005930", "Samsung", "KOSPI", "bad-date", "not-number", 80, "POSITIVE", "CANDIDATE", "", "2026-09-04T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
        )
        result = ShadowLedgerReader(SourceAllowlist(root)).read()
        assert result.status == "UNAVAILABLE"
        assert result.rows == []
        assert "malformed" in result.warnings[0]


class TestHistoricalAndAllowlist:
    def test_historical_file_missing(self, tmp_path):
        root = _root(tmp_path)
        result = HistoricalValidationReader(SourceAllowlist(root)).read_csv(FINAL_COMPARISON_SOURCE)
        assert result.status == "MISSING"
        assert result.data_kind == "historical_validation"

    def test_allowlist_enforcement_rejects_generic_file(self, tmp_path):
        root = _root(tmp_path)
        (root / "README.md").write_text("not dashboard data", encoding="utf-8")
        with pytest.raises(PermissionError):
            SourceAllowlist(root).resolve("README.md")

    def test_baseline_metadata(self, tmp_path):
        root = _root(tmp_path)
        metadata = BaselineMetadataReader(SourceAllowlist(root)).read()
        assert metadata["baseline_commit"] == "38e56c5"
        assert metadata["source"] == "git:baseline"


class TestDashboardContractAndApi:
    def test_unavailable_metric_for_pipeline_metadata(self, tmp_path):
        root = _root(tmp_path)
        _write_minimal_historical(root)
        overview = DashboardService(root).overview()
        assert overview["system"]["pipeline_status"]["status"] == "UNAVAILABLE"
        assert overview["system"]["pipeline_status"]["data_kind"] == "operational"
        assert overview["risk"]["historical_validation"]["data_kind"] == "historical_validation"

    def test_read_only_endpoints_and_no_write_endpoint(self, tmp_path):
        root = _root(tmp_path)
        _write_minimal_historical(root)
        assert READ_ONLY_ENDPOINTS == {"/api/dashboard/overview", "/api/dashboard/signals", "/api/dashboard/health"}

        status, headers, body = route_dashboard_request("GET", "/api/dashboard/overview", root)
        payload = json.loads(body.decode("utf-8"))
        assert status == 200
        assert headers["Allow"] == "GET"
        assert payload["system"]["read_only"] is True
        assert payload["system"]["baseline_commit"] == "38e56c5"

        status, _headers, body = route_dashboard_request("POST", "/api/dashboard/overview", root)
        assert status == 405
        assert json.loads(body.decode("utf-8"))["allowed_methods"] == ["GET"]

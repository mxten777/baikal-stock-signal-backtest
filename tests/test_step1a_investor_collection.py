from __future__ import annotations

import pandas as pd

from scripts import step1a_collect_investor_all as collection


def test_audit_reports_duplicate_invalid_and_missing_dates(monkeypatch):
    expected = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]))
    monkeypatch.setattr(collection, "_load_expected_dates", lambda ticker: expected)
    data = pd.DataFrame({
        "date": ["2024-01-03", "2024-01-02", "2024-01-02", "bad-date", "2024-01-05"],
        "foreign_net_buy": [10, 20, 20, 30, 40],
        "institution_net_buy": [5, 6, 6, "bad-value", 8],
    })

    audit = collection.audit_investor_data("005930", "삼성전자", data)

    assert audit["expected_trading_days"] == 3
    assert audit["available_investor_days"] == 2
    assert audit["missing_days"] == 1
    assert audit["duplicate_rows"] == 1
    assert audit["invalid_rows"] == 1
    assert audit["non_trading_day_rows"] == 1
    assert audit["date_order_violations"] == 1
    assert audit["status"] == "PARTIAL"


def test_audit_passes_only_when_both_investor_fields_cover_all_expected_days(monkeypatch):
    expected = pd.DatetimeIndex(pd.to_datetime(["2024-01-02", "2024-01-03"]))
    monkeypatch.setattr(collection, "_load_expected_dates", lambda ticker: expected)
    data = pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03"],
        "foreign_net_buy": [10, 20],
        "institution_net_buy": [5, 6],
    })

    audit = collection.audit_investor_data("005930", "삼성전자", data)

    assert audit["foreign_coverage_pct"] == 100.0
    assert audit["institution_coverage_pct"] == 100.0
    assert audit["status"] == "PASS"


def test_coverage_output_schema_has_required_columns():
    required = {
        "ticker", "name", "start_date", "end_date", "expected_trading_days",
        "available_investor_days", "coverage_pct", "missing_days", "duplicate_rows", "invalid_rows",
    }
    assert required.issubset(collection.COVERAGE_COLUMNS)


def test_existing_data_comparison_detects_value_difference():
    previous = pd.DataFrame({
        "date": ["2024-01-02"], "foreign_net_buy": [10], "institution_net_buy": [5],
    })
    current = pd.DataFrame({
        "date": ["2024-01-02"], "ticker": ["005930"], "foreign_net_buy": [11], "institution_net_buy": [5],
    })

    result = collection.compare_existing_data("005930", "삼성전자", previous, current)

    assert result["foreign_value_differences"] == 1
    assert result["institution_value_differences"] == 0
    assert result["comparison_result"] == "source_snapshot_difference"
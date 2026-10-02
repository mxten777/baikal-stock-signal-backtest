from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import pytest

from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_performance_analysis import (
    AnalysisIntegrityError, _concentration, _horizon_date, _summary,
    build_performance_analysis, sample_warning,
)
from scripts.korean_market_calendar import load_holidays
from tests.test_expanded_evidence import _ledger_row, _ready_root, _write_csv


def _root(tmp_path: Path, **metrics) -> Path:
    signal = _ledger_row()
    root = _ready_root(tmp_path, [signal])
    row = dict.fromkeys(PERFORMANCE_FIELDS, "")
    row.update({
        "source_basDd": signal["basDd"], "ticker": signal["stock_code"],
        "stock_name": signal["stock_name"], "market": signal["market"],
        "signal_date": signal["signal_date"], "entry_price": signal["signal_price"],
        "signal_score": signal["signal_score"], "foreign_status": signal["foreign_status"],
        "engine_version": signal["engine_version"], "source_run_id": signal["run_id"],
        "source_commit": signal["source_commit"], "source_created_at": signal["created_at"],
        "registered_at": "2026-09-18T00:00:00+00:00", "tracking_status": "OPEN",
        **metrics,
    })
    _write_csv(root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, [row])
    return root


def test_official_only_three_horizons_and_read_only(tmp_path):
    root = _root(tmp_path, return_5d=2.5, tracking_status="5D")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = build_performance_analysis(root, cutoff_date="2026-09-30")
    assert len(result["rows"]) == 3
    assert result["summaries"]["5"]["avg_return"] == 2.5
    assert result["summaries"]["5"]["N_excess"] == 0
    assert result["summaries"]["5"]["avg_excess"] is None
    assert result["rows"][0]["evidence_status"] == "AVAILABLE"
    assert result["rows"][0]["sector"] is not None
    assert result["rows"][0]["score_delta"] > 0
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_diagnostic_benchmark_never_backfills_metrics(tmp_path):
    root = _root(tmp_path, return_5d=2.0)
    prices = pd.DataFrame({"date": pd.bdate_range("2026-01-01", "2026-09-30"), "close": 100.0})
    result = build_performance_analysis(root, cutoff_date="2026-09-30", benchmark_map={"KS11": prices})
    assert result["benchmark_coverage"]["KS11"]["status"] == "AVAILABLE"
    assert all(row["benchmark_return"] is None and row["excess_return"] is None for row in result["rows"])


def test_cutoff_masks_future_official_metrics(tmp_path):
    root = _root(tmp_path, return_5d=2, return_10d=3)
    signal_date = pd.read_csv(root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv").signal_date.iloc[0]
    root_signal = root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    signals = pd.read_csv(root_signal, dtype=str, keep_default_na=False)
    signals["basDd"] = signal_date
    signals.to_csv(root_signal, index=False)
    perf_path = root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv"
    perf = pd.read_csv(perf_path, dtype=str, keep_default_na=False)
    perf["source_basDd"] = signal_date
    perf.to_csv(perf_path, index=False)
    result = build_performance_analysis(root, cutoff_date=signal_date)
    assert len(result["rows"]) == 3
    assert all(row["maturity_status"] == "OPEN" and row["return"] is None for row in result["rows"])


def test_due_missing_returns_are_not_counted_as_open(tmp_path):
    root = _root(tmp_path)
    result = build_performance_analysis(root, cutoff_date="2026-09-30")
    assert result["summaries"]["5"]["N_missing"] == 1
    assert result["summaries"]["5"]["N_open"] == 0


@pytest.mark.parametrize("which", ["signal", "performance"])
def test_duplicate_keys_fail_closed(tmp_path, which):
    root = _root(tmp_path)
    filename = "expanded_shadow_signal_ledger.csv" if which == "signal" else "expanded_candidate_performance_ledger.csv"
    path = root / "output/expanded_shadow" / filename
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    pd.concat([frame, frame]).to_csv(path, index=False)
    with pytest.raises(AnalysisIntegrityError, match="Duplicate"):
        build_performance_analysis(root, cutoff_date="2026-09-30")


@pytest.mark.parametrize("count,expected", [(0,"NO_DATA"),(1,"SMALL_SAMPLE"),(9,"SMALL_SAMPLE"),(10,"CAUTION"),(19,"CAUTION"),(20,"DESCRIPTIVE_ONLY")])
def test_sample_boundaries(count, expected):
    assert sample_warning(count) == expected


def test_excess_uses_separate_denominator_and_validates_arithmetic(tmp_path):
    root = _root(tmp_path, return_5d=2, benchmark_5d=1, excess_5d=1)
    result = build_performance_analysis(root, cutoff_date="2026-09-30")
    assert result["summaries"]["5"]["N_excess"] == 1
    assert result["summaries"]["5"]["excess_win_rate"] == 1
    path = root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame["excess_5d"] = "99"
    frame.to_csv(path, index=False)
    with pytest.raises(AnalysisIntegrityError, match="arithmetic"):
        build_performance_analysis(root, cutoff_date="2026-09-30")


def test_missing_performance_and_empty_repository(tmp_path):
    empty = build_performance_analysis(tmp_path, cutoff_date="2026-09-30")
    assert empty["rows"] == []
    assert empty["summaries"]["5"]["avg_return"] is None
    root = _root(tmp_path)
    (root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv").unlink()
    result = build_performance_analysis(root, cutoff_date="2026-09-30")
    assert result["quality"]["missing_performance_joins"] == 1
    assert all(row["performance_source"] == "MISSING_OFFICIAL_LEDGER" for row in result["rows"])


def test_aggregation_denominators_zero_returns_and_median():
    rows = [
        {"ticker": "000001", "signal_date": "2026-09-17", "return": -2.0, "excess_return": -1.0, "maturity_status": "MATURED"},
        {"ticker": "000002", "signal_date": "2026-09-18", "return": 0.0, "excess_return": None, "maturity_status": "MATURED"},
        {"ticker": "000003", "signal_date": "2026-09-21", "return": 5.0, "excess_return": 3.0, "maturity_status": "MATURED"},
        {"ticker": "000004", "signal_date": "2026-09-22", "return": None, "excess_return": None, "maturity_status": "OPEN"},
        {"ticker": "000005", "signal_date": "2026-09-23", "return": None, "excess_return": None, "maturity_status": "DATA_MISSING"},
    ]
    summary = _summary(rows)
    assert (summary["N_total"], summary["N_return"], summary["N_excess"], summary["N_open"], summary["N_missing"]) == (5, 3, 2, 1, 1)
    assert summary["avg_return"] == 1
    assert summary["median_return"] == 0
    assert summary["return_win_rate"] == pytest.approx(1 / 3)
    assert summary["avg_excess"] == summary["median_excess"] == 1
    assert summary["excess_win_rate"] == 0.5


def test_concentration_uses_signals_not_horizon_triplication():
    rows = [{"ticker": "000001", "signal_date": "2026-09-17"}, {"ticker": "000001", "signal_date": "2026-09-18"}, {"ticker": "000002", "signal_date": "2026-09-18"}]
    concentration = _concentration(rows)
    assert concentration["repeated_tickers"] == 1
    assert concentration["repeat_record_fraction"] == pytest.approx(1 / 3)
    assert concentration["max_date_share"] == pytest.approx(2 / 3)
    assert set(concentration["warnings"]) == {"DATE_CONCENTRATION", "TICKER_CONCENTRATION"}


def test_horizon_dates_use_existing_krx_holidays(tmp_path):
    holidays, _ = load_holidays(tmp_path)
    assert _horizon_date("2026-09-17", 5, holidays) == "2026-09-28"
    assert _horizon_date("2026-09-22", 5, holidays) == "2026-10-01"
    assert _horizon_date("2026-09-17", 10, holidays) == "2026-10-06"
    with pytest.raises(AnalysisIntegrityError, match="supported"):
        _horizon_date("2027-02-26", 5, holidays)


def test_stale_benchmark_missing_endpoint_and_duplicate_dates(tmp_path):
    root = _root(tmp_path, return_5d=2)
    stale = pd.DataFrame({"date": ["2026-01-02"], "close": [100]})
    result = build_performance_analysis(root, cutoff_date="2026-09-30", benchmark_map={"KS11": stale})
    coverage = result["benchmark_coverage"]["KS11"]
    assert coverage["status"] == "INSUFFICIENT"
    assert coverage["latest_index_date"] == "2026-01-02"
    assert coverage["N_covered"] == 0
    assert coverage["missing_dates"] == coverage["required_dates"]
    result = build_performance_analysis(root, cutoff_date="2026-09-30", benchmark_map={"KS11": pd.concat([stale, stale])})
    assert result["benchmark_coverage"]["KS11"]["duplicate_dates"] == 1


@pytest.mark.parametrize("field,value,message", [("ticker","999999","Orphan"),("entry_price","99","conflict"),("return_5d","nan","Nonfinite"),("tracking_status","INVALID","tracking status")])
def test_invalid_performance_fails_closed(tmp_path, field, value, message):
    root = _root(tmp_path)
    path = root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv"
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame[field] = value
    frame.to_csv(path, index=False)
    with pytest.raises(AnalysisIntegrityError, match=message):
        build_performance_analysis(root, cutoff_date="2026-09-30")


def test_partial_snapshot_never_adds_official_returns(tmp_path):
    root = _root(tmp_path)
    partial = root / "data/expanded_shadow/market/2026-10-01/000001.csv"
    partial.parent.mkdir(parents=True)
    pd.DataFrame({"date": pd.bdate_range("2026-01-01", "2026-10-01"), "close": 1000}).to_csv(partial, index=False)
    result = build_performance_analysis(root, cutoff_date="2026-10-02")
    assert all(row["return"] is None for row in result["rows"])
    assert result["summaries"]["5"]["N_return"] == 0


def test_missing_profile_and_evidence_are_diagnosed(tmp_path):
    root = _root(tmp_path)
    (root / "data/expanded_shadow/company_profiles/expanded_company_profiles.json").unlink()
    (root / "data/expanded_shadow/market/2026-09-17/000001.csv").unlink()
    result = build_performance_analysis(root, cutoff_date="2026-09-30")
    assert result["quality"]["missing_profile_joins"] == 1
    assert result["quality"]["evidence_status"] == {"UNAVAILABLE": 1}
    assert result["quality"]["missing_fields"]["score_delta"] == 1


def test_cli_no_network_default_and_diagnostic_only_check(tmp_path, monkeypatch, capsys):
    from scripts import expanded_performance_analysis as cli

    root = _root(tmp_path)
    monkeypatch.chdir(root)
    calls = []
    def load(symbol, start, end):
        calls.append(symbol)
        return pd.DataFrame({"date": ["2026-01-02"], "close": [100]})
    monkeypatch.setattr(cli, "load_benchmark", load)
    assert cli.main(["--cutoff-date", "2026-09-30"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == []
    assert result["analysis_row_count"] == 3 and "rows" not in result
    assert cli.main(["--cutoff-date", "2026-09-30", "--check-benchmark", "--include-rows"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == ["KS11", "KQ11"]
    assert all(row["return"] is None for row in result["rows"])
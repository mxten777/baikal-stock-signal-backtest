from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dashboard.runner.shadow_dashboard_runner import (
    METADATA_SOURCE,
    inspect_input_data,
    inspect_ledger,
    run_dashboard_pipeline,
    write_metadata_atomic,
)
from scripts.shadow_daily_pipeline import PHASE_SCAN, PhaseResult, PipelineResult
from src.shadow_tracking import SHADOW_RECORD_FIELDS


def _root(tmp_path: Path) -> Path:
    (tmp_path / "output").mkdir()
    return tmp_path


def _write_input_csv(path: Path, dates: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("date,close\n" + "".join(f"{value},100\n" for value in dates), encoding="utf-8")


def _write_ledger(path: Path, rows: list[list[object]], header: list[str] | None = None) -> None:
    columns = header or SHADOW_RECORD_FIELDS
    path.write_text(
        ",".join(columns)
        + "\n"
        + "".join(",".join(str(value) for value in row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _success_result(signal_base_date: str | None = "2026-09-04") -> PipelineResult:
    return PipelineResult(
        started_at="2026-09-04T09:00:00",
        dry_run=False,
        phases=[PhaseResult(title=PHASE_SCAN, passed=True, stats={"signal_base_date": signal_base_date})],
    )


def test_successful_metadata_write(tmp_path):
    root = _root(tmp_path)
    _write_input_csv(root / "data/raw/005930.csv", ["2026-09-04"])
    _write_input_csv(root / "data/investor/005930_investor.csv", ["2026-09-04"])
    _write_ledger(
        root / "output/shadow_signal_records.csv",
        [["005930", "Samsung", "KOSPI", "2026-09-04", 70000, 80, "POSITIVE", "CANDIDATE", "", "2026-09-04T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    times = iter(["2026-09-04T00:00:00+00:00", "2026-09-04T00:00:03+00:00"])

    result = run_dashboard_pipeline(
        repo_root=root,
        pipeline_func=lambda dry_run=False: _success_result(),
        now_func=lambda: next(times),
    )

    assert result.metadata_path == root / METADATA_SOURCE
    assert result.metadata["pipeline_status"] == "SUCCESS"
    assert result.metadata["signal_base_date"] == "2026-09-04"
    assert result.metadata["ledger_status"] == "AVAILABLE"
    assert result.metadata["record_count"] == 1
    assert result.metadata["duration_seconds"] == 3.0
    assert result.metadata["market_data_max_date"] == "2026-09-04"
    assert result.metadata["investor_data_max_date"] == "2026-09-04"
    assert result.metadata["input_data_freshness"] == "CURRENT"
    assert result.metadata["input_data_stale_after_days"] == 5
    assert json.loads((root / METADATA_SOURCE).read_text(encoding="utf-8"))["read_only"] is True


def test_stale_market_data_classification(tmp_path):
    root = _root(tmp_path)
    _write_input_csv(root / "data/raw/005930.csv", ["2026-08-14"])
    _write_input_csv(root / "data/investor/005930_investor.csv", ["2026-09-04"])

    result = inspect_input_data(root, "2026-09-04T00:00:00+00:00")

    assert result["market_data_max_date"] == "2026-08-14"
    assert result["investor_data_max_date"] == "2026-09-04"
    assert result["input_data_freshness"] == "STALE"


def test_current_market_data_classification(tmp_path):
    root = _root(tmp_path)
    _write_input_csv(root / "data/raw/005930.csv", ["2026-09-01"])
    _write_input_csv(root / "data/investor/005930_investor.csv", ["2026-09-01"])

    result = inspect_input_data(root, "2026-09-04T00:00:00+00:00")

    assert result["market_data_max_date"] == "2026-09-01"
    assert result["input_data_freshness"] == "CURRENT"


def test_missing_market_source_classification(tmp_path):
    root = _root(tmp_path)
    _write_input_csv(root / "data/investor/005930_investor.csv", ["2026-09-04"])

    result = inspect_input_data(root, "2026-09-04T00:00:00+00:00")

    assert result["market_data_max_date"] is None
    assert result["investor_data_max_date"] == "2026-09-04"
    assert result["input_data_freshness"] == "MISSING"


def test_investor_date_extraction(tmp_path):
    root = _root(tmp_path)
    _write_input_csv(root / "data/raw/005930.csv", ["2026-09-04"])
    _write_input_csv(root / "data/investor/005930_investor.csv", ["2026-08-30", "2026-09-02"])

    result = inspect_input_data(root, "2026-09-04T00:00:00+00:00")

    assert result["investor_data_max_date"] == "2026-09-02"


def test_failed_pipeline_metadata_write(tmp_path):
    root = _root(tmp_path)
    failed = PipelineResult(
        started_at="2026-09-04T09:00:00",
        dry_run=False,
        phases=[PhaseResult(title=PHASE_SCAN, passed=False, error="RuntimeError: boom")],
    )

    result = run_dashboard_pipeline(repo_root=root, pipeline_func=lambda dry_run=False: failed)

    assert result.metadata["pipeline_status"] == "FAILED"
    assert "RuntimeError: boom" in result.metadata["error"]
    assert result.metadata["ledger_status"] == "MISSING"
    assert (root / METADATA_SOURCE).exists()


def test_failed_pipeline_exception_keeps_short_metadata_error(tmp_path):
    root = _root(tmp_path)

    def raise_error(dry_run=False):
        raise ValueError("bad upstream state")

    result = run_dashboard_pipeline(repo_root=root, pipeline_func=raise_error)

    assert result.metadata["pipeline_status"] == "FAILED"
    assert result.metadata["error"] == "ValueError: bad upstream state"
    assert result.traceback_text is not None
    assert "Traceback" not in json.dumps(result.metadata)


def test_atomic_write_preserves_existing_file_when_replace_fails(tmp_path, monkeypatch):
    root = _root(tmp_path)
    target = root / METADATA_SOURCE
    target.write_text('{"old": true}\n', encoding="utf-8")

    def fail_replace(source, destination):
        raise RuntimeError("replace failed")

    monkeypatch.setattr("dashboard.runner.shadow_dashboard_runner.os.replace", fail_replace)

    with pytest.raises(RuntimeError):
        write_metadata_atomic(target, {"new": True})

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_missing_ledger_status(tmp_path):
    root = _root(tmp_path)
    result = inspect_ledger(root / "output/shadow_signal_records.csv")
    assert result["ledger_status"] == "MISSING"
    assert result["record_count"] == 0


def test_empty_ledger_status(tmp_path):
    root = _root(tmp_path)
    _write_ledger(root / "output/shadow_signal_records.csv", [])
    result = inspect_ledger(root / "output/shadow_signal_records.csv")
    assert result["ledger_status"] == "EMPTY"
    assert result["record_count"] == 0


def test_ledger_with_records_status(tmp_path):
    root = _root(tmp_path)
    _write_ledger(
        root / "output/shadow_signal_records.csv",
        [["005930", "Samsung", "KOSPI", "2026-09-04", 70000, 80, "POSITIVE", "CANDIDATE", "", "2026-09-04T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    result = inspect_ledger(root / "output/shadow_signal_records.csv")
    assert result["ledger_status"] == "AVAILABLE"
    assert result["record_count"] == 1


def test_malformed_ledger_status(tmp_path):
    root = _root(tmp_path)
    _write_ledger(root / "output/shadow_signal_records.csv", [["005930"]], header=["stock_code"])
    result = inspect_ledger(root / "output/shadow_signal_records.csv")
    assert result["ledger_status"] == "MALFORMED"
    assert result["record_count"] == 0


def test_protected_core_paths_are_not_modified():
    protected_paths = [
        "src",
        "scripts",
        "output/v02_step9_final_comparison.csv",
        "output/v02_step9_final_risk_review.csv",
        "output/v02_step8_filtered_opportunity_cost.csv",
        "output/v02_step6_filter_opportunity_cost.csv",
        "output/v02_step3_foreign_score_performance.csv",
    ]
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--", *protected_paths],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == ""
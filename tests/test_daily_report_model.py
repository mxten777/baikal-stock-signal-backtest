from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from dashboard.daily_report_model import (
    NEW_CANDIDATES_EMPTY,
    NEW_CANDIDATES_NOT_FOUND,
    NEW_CANDIDATES_READY,
    NEW_CANDIDATES_UNAVAILABLE,
    STATUS_MALFORMED,
    STATUS_MISSING,
    STATUS_READY,
)
from dashboard.expanded_daily_report import build_daily_report_model
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_shadow_ledger import LEDGER_FIELDS

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATE = "2026-09-17"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(root: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "run_id": "run-1",
        "basDd": SOURCE_DATE,
        "status": "SUCCESS",
        "canonical_universe_count": 574,
        "attempted_ticker_count": 574,
        "ready_count": 574,
        "signal_count": 26,
        "new_candidate_count": 14,
        "started_at": "2026-09-17T09:00:00+00:00",
        "finished_at": "2026-09-17T09:06:15+00:00",
        "runtime_seconds": 375.0,
    }
    payload.update(overrides)
    path = root / "output/expanded_shadow/expanded_shadow_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _signal(ticker: str, *, basdd: str = SOURCE_DATE, decision: str = "CANDIDATE", **overrides: object) -> dict[str, object]:
    row = {field: "" for field in LEDGER_FIELDS}
    row.update(
        {
            "basDd": basdd,
            "stock_code": ticker,
            "stock_name": f"Stock {ticker}",
            "market": "KOSPI",
            "signal_date": basdd,
            "signal_price": 100.5,
            "raw_score": 52,
            "signal_score": 80.25,
            "signal_type": "BUY_WATCH",
            "foreign_5d_ratio": 0.2,
            "foreign_status": "POSITIVE",
            "decision": decision,
            "engine_version": "v0.1",
            "source_commit": "abc123",
            "run_id": "run-1",
            "created_at": "2026-09-17T09:00:00+00:00",
        }
    )
    row.update(overrides)
    return row


def _performance(ticker: str, status: str = "OPEN", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in PERFORMANCE_FIELDS}
    row.update(
        {
            "source_basDd": SOURCE_DATE,
            "ticker": ticker,
            "stock_name": f"Stock {ticker}",
            "market": "KOSPI",
            "signal_date": SOURCE_DATE,
            "entry_price": 100.5,
            "signal_score": 80.25,
            "foreign_status": "POSITIVE",
            "engine_version": "v0.1",
            "source_run_id": "run-1",
            "source_commit": "abc123",
            "source_created_at": "2026-09-17T09:00:00+00:00",
            "registered_at": "2026-09-18T00:00:00+00:00",
            "tracking_status": status,
        }
    )
    row.update(overrides)
    return row


def _ready_root(tmp_path: Path) -> Path:
    _write_manifest(tmp_path)
    rows = [_signal(f"{index:06d}") for index in range(1, 15)]
    rows.extend([_signal(f"{index:06d}", decision="EXCLUDED") for index in range(101, 113)])
    _write_csv(tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv", LEDGER_FIELDS, rows)
    return tmp_path


# 1 & 2: real 2026-09-18 data mapping + CANDIDATE == 23
def test_real_2026_09_18_data_mapping_candidate_count():
    model = build_daily_report_model(REPO_ROOT)

    assert model.status == STATUS_READY
    assert model.run_summary.source_date == "2026-09-18"
    assert model.new_candidates_status == NEW_CANDIDATES_READY
    assert len(model.new_candidates) == 23
    assert model.run_summary.candidate == 23


# 3, 4, 5: exact preservation of signal_score / signal_price->entry_price / foreign_status
def test_new_candidate_fields_are_preserved_exactly(tmp_path: Path):
    root = _ready_root(tmp_path)
    path = root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    rows = [
        _signal("000001", signal_price=12345.75, signal_score=91.35, foreign_status="NEGATIVE"),
    ]
    _write_csv(path, LEDGER_FIELDS, rows)

    model = build_daily_report_model(root)

    assert len(model.new_candidates) == 1
    record = model.new_candidates[0]
    assert record.entry_price == 12345.75
    assert record.signal_score == 91.35
    assert record.foreign_status == "NEGATIVE"
    assert record.ticker == "000001"
    assert record.stock_name == "Stock 000001"
    assert record.market == "KOSPI"
    assert record.signal_date == SOURCE_DATE


# 6: performance / benchmark / excess exact preservation
def test_performance_fields_are_preserved_exactly(tmp_path: Path):
    root = _ready_root(tmp_path)
    _write_csv(
        root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
        PERFORMANCE_FIELDS,
        [
            _performance(
                "000001",
                status="COMPLETE",
                entry_price=12345.75,
                return_5d=1.23,
                benchmark_5d=0.45,
                excess_5d=0.78,
                return_10d=2.34,
                benchmark_10d=1.11,
                excess_10d=1.23,
                return_20d=3.45,
                benchmark_20d=2.22,
                excess_20d=1.23,
            )
        ],
    )

    model = build_daily_report_model(root)

    assert len(model.performance) == 1
    record = model.performance[0]
    assert record.ticker == "000001"
    assert record.entry_price == 12345.75
    assert record.tracking_status == "COMPLETE"
    assert record.return_5d == 1.23
    assert record.benchmark_5d == 0.45
    assert record.excess_5d == 0.78
    assert record.return_10d == 2.34
    assert record.benchmark_10d == 1.11
    assert record.excess_10d == 1.23
    assert record.return_20d == 3.45
    assert record.benchmark_20d == 2.22
    assert record.excess_20d == 1.23


# 7: OPEN missing values -> None
def test_open_missing_values_are_none(tmp_path: Path):
    root = _ready_root(tmp_path)
    _write_csv(
        root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
        PERFORMANCE_FIELDS,
        [_performance("000001", status="OPEN")],
    )

    model = build_daily_report_model(root)

    record = model.performance[0]
    assert record.tracking_status == "OPEN"
    for field_name in (
        "return_5d",
        "benchmark_5d",
        "excess_5d",
        "return_10d",
        "benchmark_10d",
        "excess_10d",
        "return_20d",
        "benchmark_20d",
        "excess_20d",
    ):
        assert getattr(record, field_name) is None


# 8: invalid/missing date handling
def test_missing_manifest_is_reported_as_missing(tmp_path: Path):
    model = build_daily_report_model(tmp_path)

    assert model.status == STATUS_MISSING
    assert model.new_candidates_status == NEW_CANDIDATES_UNAVAILABLE
    assert model.new_candidates == []
    assert model.run_summary.source_date is None


def test_unrecognized_source_date_returns_not_found(tmp_path: Path):
    root = _ready_root(tmp_path)

    model = build_daily_report_model(root, source_date="2099-01-01")

    assert model.status == STATUS_READY
    assert model.new_candidates_status == NEW_CANDIDATES_NOT_FOUND
    assert model.new_candidates == []


def test_malformed_manifest_is_reported_as_malformed(tmp_path: Path):
    path = tmp_path / "output/expanded_shadow/expanded_shadow_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"basDd": SOURCE_DATE}), encoding="utf-8")

    model = build_daily_report_model(tmp_path)

    assert model.status == STATUS_MALFORMED
    assert model.new_candidates_status == NEW_CANDIDATES_UNAVAILABLE


# 9: no-candidate date handling
def test_no_candidate_date_returns_empty(tmp_path: Path):
    root = _ready_root(tmp_path)
    path = root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    rows = [_signal(f"{index:06d}", decision="EXCLUDED") for index in range(1, 5)]
    _write_csv(path, LEDGER_FIELDS, rows)

    model = build_daily_report_model(root)

    assert model.status == STATUS_READY
    assert model.new_candidates_status == NEW_CANDIDATES_EMPTY
    assert model.new_candidates == []


# 10: protected artifacts unchanged before/after report build
def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_protected_artifacts_unchanged_after_report_build():
    protected = [
        REPO_ROOT / "output/expanded_shadow/expanded_shadow_run.json",
        REPO_ROOT / "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        REPO_ROOT / "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
    ]
    before = {path: _sha256(path) for path in protected}

    build_daily_report_model(REPO_ROOT)

    after = {path: _sha256(path) for path in protected}
    assert before == after

    protected_paths = [
        "src",
        "scripts",
        "dashboard/operations.py",
        "dashboard/daily_signal_board.py",
        "dashboard/expanded_signal_board.py",
        "dashboard/dual_shadow.py",
        "dashboard/adapter",
        "dashboard/runner",
        ":(exclude)scripts/daily_scheduler.py",
        ":(exclude)scripts/safe_investor_update.py",
        ":(exclude)scripts/daily_operational_run.py",
        ":(exclude)scripts/daily_health_report.py",
        ":(exclude)src/expanded_shadow_pipeline.py",
        ":(exclude)dashboard/api.py",  # STEP 15-D: adds the read-only daily-report download endpoint
    ]
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--", *protected_paths],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert completed.stdout.strip() == ""

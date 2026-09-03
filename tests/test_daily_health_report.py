import json
import subprocess
import sys

import pytest

from scripts.daily_health_report import REQUIRED_FIELDS, build_report, render_report


def _manifest(tmp_path, **overrides):
    payload = {
        "run_id": "run-001", "started_at": "2026-09-04T01:02:03+00:00", "finished_at": "2026-09-04T01:03:03+00:00",
        "overall_status": "SUCCESS", "failed_phase": None, "market_update_status": "UPDATED",
        "investor_update_status": "UPDATED", "gate_status": "PASS", "pipeline_allowed": True,
        "dashboard_status": "SUCCESS", "market_latest_date": "2026-09-04", "investor_latest_date": "2026-09-04",
        "signal_count": 2, "zero_signal": False, "warnings": [], "errors": [],
    }
    payload.update(overrides)
    path = tmp_path / "daily_operational_run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_healthy_successful_run(tmp_path):
    report = build_report(_manifest(tmp_path), "2026-09-04")
    assert report["health_status"] == "HEALTHY"
    assert report["run_is_today"] is True


def test_zero_signal_is_healthy_and_explicit(tmp_path):
    report = build_report(_manifest(tmp_path, signal_count=0, zero_signal=True), "2026-09-04")
    assert report["health_status"] == "HEALTHY"
    assert "SIGNAL STATUS: NO SIGNAL TODAY" in render_report(report)


def test_source_lag_is_warning(tmp_path):
    report = build_report(_manifest(tmp_path, overall_status="SUCCESS_WITH_WARNING", investor_update_status="SOURCE_LAG", warnings=["INVESTOR SOURCE_LAG"]), "2026-09-04")
    assert report["health_status"] == "WARNING"
    assert report["warnings"] == ["INVESTOR SOURCE_LAG"]


@pytest.mark.parametrize(
    ("phase", "action"),
    [
        ("MARKET_UPDATE", "Check the market source"),
        ("INVESTOR_UPDATE", "Check the Naver source"),
        ("INPUT_GATE", "Check missing"),
        ("DASHBOARD_RUNNER", "Check the dashboard"),
    ],
)
def test_failure_phases_and_actions(tmp_path, phase, action):
    report = build_report(_manifest(tmp_path, overall_status="FAILED", failed_phase=phase, errors=[f"{phase}_FAILED"]), "2026-09-04")
    assert report["health_status"] == "FAILED"
    assert report["failed_phase"] == phase
    assert any(action in value for value in report["operator_actions"])


def test_no_manifest_is_no_run(tmp_path):
    report = build_report(tmp_path / "missing.json", "2026-09-04")
    assert report["health_status"] == "NO_RUN"
    assert report["dashboard_status"] == "UNKNOWN"
    assert report["operator_actions"] == ["Run Daily Operational Run."]


def test_corrupt_manifest_is_failed(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    assert build_report(corrupt)["health_status"] == "FAILED"


def test_incomplete_manifest_is_failed(tmp_path):
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text("{}", encoding="utf-8")
    assert build_report(incomplete)["health_status"] == "FAILED"


def test_previous_run_warning_and_concurrent_action(tmp_path):
    report = build_report(_manifest(tmp_path, errors=["CONCURRENT_RUN"]), "2026-09-05")
    assert report["health_status"] == "WARNING"
    assert report["run_is_today"] is False
    assert any("lock" in value for value in report["operator_actions"])


def test_json_contract_determinism_and_read_only(tmp_path):
    path = _manifest(tmp_path)
    original = path.read_bytes()
    first = build_report(path, "2026-09-04")
    second = build_report(path, "2026-09-04")
    assert REQUIRED_FIELDS <= set(json.loads(path.read_text(encoding="utf-8")))
    assert set(first) >= {"health_status", "run_present", "run_is_today", "run_id", "overall_status", "failed_phase", "market_status", "investor_status", "market_latest_date", "investor_latest_date", "gate_status", "pipeline_allowed", "dashboard_status", "signal_count", "zero_signal", "warnings", "errors", "operator_actions"}
    assert first == second
    assert path.read_bytes() == original


def test_warning_and_error_counts_are_rendered(tmp_path):
    report = build_report(_manifest(tmp_path, overall_status="FAILED", failed_phase="INPUT_GATE", warnings=["SOURCE_LAG"], errors=["MARKET_PARTIAL_DATE"]), "2026-09-04")
    rendered = render_report(report)
    assert "WARNING COUNT: 1" in rendered
    assert "ERROR COUNT: 1" in rendered


def test_cli_json_contract(tmp_path):
    path = _manifest(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.daily_health_report", "--manifest", str(path), "--today-date", "2026-09-04", "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout)["health_status"] == "HEALTHY"


# --- STEP 6-H: operational timezone (Asia/Seoul) today detection ---


def test_utc_previous_date_is_kst_today(tmp_path):
    # 2026-09-03 22:50 UTC == 2026-09-04 07:50 Asia/Seoul
    report = build_report(
        _manifest(tmp_path, started_at="2026-09-03T22:50:00+00:00", finished_at="2026-09-03T22:51:00+00:00"),
        "2026-09-04",
    )
    assert report["run_is_today"] is True
    assert report["run_date"] == "2026-09-04"
    assert report["health_status"] == "HEALTHY"
    assert not any("PREVIOUS_RUN" in warning for warning in report["warnings"])


def test_utc_and_kst_same_date_is_today(tmp_path):
    # 2026-09-04 01:00 UTC == 2026-09-04 10:00 Asia/Seoul (same date in both zones)
    report = build_report(
        _manifest(tmp_path, started_at="2026-09-04T01:00:00+00:00", finished_at="2026-09-04T01:01:00+00:00"),
        "2026-09-04",
    )
    assert report["run_is_today"] is True
    assert report["run_date"] == "2026-09-04"
    assert report["health_status"] == "HEALTHY"


def test_actual_previous_operational_day(tmp_path):
    # 2026-09-02 22:50 UTC == 2026-09-03 07:50 Asia/Seoul -> previous operational day
    report = build_report(
        _manifest(tmp_path, started_at="2026-09-02T22:50:00+00:00", finished_at="2026-09-02T22:51:00+00:00"),
        "2026-09-04",
    )
    assert report["run_is_today"] is False
    assert report["run_date"] == "2026-09-03"
    assert report["health_status"] == "WARNING"
    assert any("PREVIOUS_RUN" in warning for warning in report["warnings"])


def test_kst_offset_timestamp_input(tmp_path):
    report = build_report(
        _manifest(tmp_path, started_at="2026-09-04T07:50:00+09:00", finished_at="2026-09-04T07:51:00+09:00"),
        "2026-09-04",
    )
    assert report["run_is_today"] is True
    assert report["run_date"] == "2026-09-04"
    assert report["health_status"] == "HEALTHY"


def test_naive_timestamp_is_never_misjudged_as_today(tmp_path):
    # Documented fallback: naive timestamps are never assumed to be UTC or local.
    report = build_report(
        _manifest(tmp_path, started_at="2026-09-04T01:00:00", finished_at="2026-09-04T01:01:00"),
        "2026-09-04",
    )
    assert report["run_is_today"] is False
    assert report["run_date"] is None
    assert report["health_status"] == "WARNING"
    assert any("NAIVE_TIMESTAMP" in warning for warning in report["warnings"])
    assert any("PREVIOUS_RUN" in warning and "UNKNOWN" in warning for warning in report["warnings"])


def test_kst_today_warning_run_with_zero_signal(tmp_path):
    # Production STEP 6-G scenario: UTC previous calendar date, KST today,
    # SUCCESS_WITH_WARNING with zero signals must keep WARNING without PREVIOUS_RUN.
    report = build_report(
        _manifest(
            tmp_path,
            started_at="2026-09-03T22:48:37+00:00",
            finished_at="2026-09-03T22:49:18+00:00",
            overall_status="SUCCESS_WITH_WARNING",
            gate_status="PASS_WITH_WARNING",
            warnings=["INPUT_GATE_WARNING"],
            signal_count=0,
            zero_signal=True,
        ),
        "2026-09-04",
    )
    assert report["health_status"] == "WARNING"
    assert report["run_is_today"] is True
    assert report["run_date"] == "2026-09-04"
    assert report["warnings"] == ["INPUT_GATE_WARNING"]
    assert report["signal_count"] == 0
    assert report["zero_signal"] is True
    assert not any("PREVIOUS_RUN" in warning for warning in report["warnings"])
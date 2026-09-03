import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.daily_operational_run import DailyOperationDependencies, DailyRunLock, PHASE_DASHBOARD_RUNNER, PHASE_INPUT_GATE, STATUS_FAILED, STATUS_SUCCESS, STATUS_SUCCESS_WITH_WARNING, run_daily_operation, write_manifest_atomic


@dataclass
class FakeResult:
    status: str
    published_latest_date: str | None = "2026-09-03"
    pipeline_allowed: bool = True
    market_latest_date: str | None = "2026-09-03"
    investor_latest_date: str | None = "2026-09-03"

    def to_dict(self):
        return {"status": self.status, "published_latest_date": self.published_latest_date}


@dataclass
class FakeRunnerResult:
    metadata: dict


def _repo(tmp_path: Path) -> Path:
    for path in ("data/raw", "data/investor", "output"):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _dependencies(calls, market="UPDATED", investor="UPDATED", gate="PASS", allowed=True, runner="SUCCESS", records=2):
    def action(name, result):
        def run():
            calls.append(name)
            return result
        return run
    return DailyOperationDependencies(action("market", FakeResult(market)), action("investor", FakeResult(investor)), action("gate", FakeResult(gate, pipeline_allowed=allowed)), action("runner", FakeRunnerResult({"pipeline_status": runner, "record_count": records})))


def test_all_phases_success_and_json_contract(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls))
    assert calls == ["market", "investor", "gate", "runner"]
    assert result.overall_status == STATUS_SUCCESS
    assert json.loads(json.dumps(result.to_dict()))["pipeline_allowed"] is True
    assert (tmp_path / "output/daily_operational_run.json").exists()


def test_market_failure_stops_later_phases(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls, market="FAILED"))
    assert calls == ["market"]
    assert result.failed_phase == "MARKET_UPDATE"


def test_investor_failure_stops_gate_and_runner(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls, investor="FAILED"))
    assert calls == ["market", "investor"]
    assert result.failed_phase == "INVESTOR_UPDATE"


def test_gate_blocked_stops_runner(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls, gate="FAIL", allowed=False))
    assert calls == ["market", "investor", "gate"]
    assert result.failed_phase == PHASE_INPUT_GATE


def test_source_lag_allowed_is_warning_success(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls, investor="SOURCE_LAG"))
    assert calls == ["market", "investor", "gate", "runner"]
    assert result.overall_status == STATUS_SUCCESS_WITH_WARNING


def test_source_lag_blocked_stops_runner(tmp_path):
    calls = []
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies(calls, investor="SOURCE_LAG", gate="FAIL", allowed=False))
    assert calls == ["market", "investor", "gate"]
    assert result.failed_phase == PHASE_INPUT_GATE


def test_runner_failure_marks_operation_failed(tmp_path):
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies([], runner="FAILED"))
    assert result.failed_phase == PHASE_DASHBOARD_RUNNER
    assert result.overall_status == STATUS_FAILED


def test_zero_signal_is_success(tmp_path):
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=_dependencies([], records=0))
    assert result.overall_status == STATUS_SUCCESS
    assert result.zero_signal is True


def test_unexpected_exception_isolated(tmp_path):
    dependencies = _dependencies([])
    dependencies = DailyOperationDependencies(lambda: (_ for _ in ()).throw(RuntimeError("network exploded")), dependencies.investor_update, dependencies.input_gate, dependencies.dashboard_runner)
    result = run_daily_operation(repo_root=_repo(tmp_path), dependencies=dependencies)
    assert result.failed_phase == "MARKET_UPDATE"
    assert "RuntimeError: network exploded" in result.errors[0]


def test_manifest_atomic_failure_preserves_previous_file(tmp_path, monkeypatch):
    target = _repo(tmp_path) / "output/daily_operational_run.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    result = run_daily_operation(repo_root=tmp_path, dependencies=_dependencies([]), write_manifest=False)
    monkeypatch.setattr("scripts.daily_operational_run.os.replace", lambda source, destination: (_ for _ in ()).throw(OSError("replace failed")))
    with pytest.raises(OSError):
        write_manifest_atomic(target, result)
    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not list(target.parent.glob(".daily_operational_run.json.*.tmp"))


def test_rerun_safe_and_lock_rejects_concurrent_run(tmp_path):
    repo = _repo(tmp_path)
    assert run_daily_operation(repo_root=repo, dependencies=_dependencies([])).overall_status == STATUS_SUCCESS
    assert run_daily_operation(repo_root=repo, dependencies=_dependencies([])).overall_status == STATUS_SUCCESS
    lock = DailyRunLock(repo / "output/daily_operational_run.lock")
    assert lock.acquire("first") is True
    blocked = run_daily_operation(repo_root=repo, dependencies=_dependencies([]))
    assert blocked.overall_status == STATUS_FAILED
    assert "CONCURRENT_RUN" in blocked.errors[0]
    lock.release()


def test_stale_lock_is_removed(tmp_path):
    repo = _repo(tmp_path)
    lock_path = repo / "output/daily_operational_run.lock"
    lock_path.write_text("{}", encoding="utf-8")
    os.utime(lock_path, (0, 0))
    lock = DailyRunLock(lock_path)
    assert lock.acquire("next") is True
    lock.release()
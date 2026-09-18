from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.expanded_operational_run as operational
from scripts.expanded_daily_orchestrator import ExpandedOrchestrationResult
from src.expanded_snapshot_preparation import SnapshotPreparationResult, SourceDateResolution


SOURCE_DATE = "2026-09-18"


def _clock():
    values = iter(["2026-09-18T14:10:00+00:00", "2026-09-18T14:10:03+00:00"])
    return lambda: next(values)


def _resolution(reason: str = "LATEST_COMPLETED") -> SourceDateResolution:
    return SourceDateResolution(SOURCE_DATE, reason, SOURCE_DATE, ("2026-09-17",))


def _preparation(status: str = "READY") -> SnapshotPreparationResult:
    ready = 3 if status == "READY" else 2
    failures = () if status == "READY" else ({"ticker": "000003", "error_code": "SOURCE_DATE_NOT_READY"},)
    return SnapshotPreparationResult(SOURCE_DATE, 3, ready, 3, ready, failures, (), 5, 1, status, 2, 3, 1, 0)


def _orchestration(status: str = "SUCCESS") -> ExpandedOrchestrationResult:
    return ExpandedOrchestrationResult(
        source_date=SOURCE_DATE,
        daily_run_status=status,
        performance_status="SUCCESS",
        candidates_registered=1,
        candidates_updated=2,
        final_status=status,
        started_at="2026-09-18T14:10:01+00:00",
        completed_at="2026-09-18T14:10:02+00:00",
        runtime_seconds=1.0,
    )


def test_master_calls_resolver_preparer_then_e1(tmp_path: Path):
    order = []

    def resolver(**kwargs):
        order.append(("resolve", kwargs["explicit_source_date"]))
        return _resolution("EXPLICIT")

    def preparer(**kwargs):
        order.append(("prepare", kwargs["source_date"]))
        return _preparation()

    def orchestrator(**kwargs):
        order.append(("orchestrate", kwargs["source_date"]))
        return _orchestration()

    result = operational.run_expanded_operational_run(repo_root=tmp_path, explicit_source_date=SOURCE_DATE, market_source=object(), investor_source=object(), resolver=resolver, preparer=preparer, orchestrator=orchestrator, now_func=_clock())
    assert order == [("resolve", SOURCE_DATE), ("prepare", SOURCE_DATE), ("orchestrate", SOURCE_DATE)]
    assert result.final_status == "SUCCESS"
    assert result.source_date_resolution["reason"] == "EXPLICIT"
    assert result.snapshot_preparation["ready_count"] == 3


def test_data_not_ready_is_exit_zero_and_does_not_call_e1(tmp_path: Path):
    calls = []
    result = operational.run_expanded_operational_run(repo_root=tmp_path, resolver=lambda **_kwargs: _resolution(), preparer=lambda **_kwargs: _preparation("DATA_NOT_READY"), orchestrator=lambda **kwargs: calls.append(kwargs), now_func=_clock())
    assert result.final_status == "DATA_NOT_READY"
    assert result.orchestration is None
    assert calls == []


def test_preparation_failure_is_exit_one_and_does_not_call_e1(tmp_path: Path):
    calls = []
    result = operational.run_expanded_operational_run(repo_root=tmp_path, resolver=lambda **_kwargs: _resolution(), preparer=lambda **_kwargs: _preparation("SNAPSHOT_PREPARATION_FAILED"), orchestrator=lambda **kwargs: calls.append(kwargs), now_func=_clock())
    assert result.final_status == "SNAPSHOT_PREPARATION_FAILED"
    assert calls == []


@pytest.mark.parametrize("status", ["SUCCESS", "SUCCESS_WITH_WARNING", "ALREADY_COMPLETED", "DAILY_RUN_FAILED", "PERFORMANCE_UPDATE_FAILED"])
def test_e1_final_status_is_propagated(tmp_path: Path, status: str):
    result = operational.run_expanded_operational_run(repo_root=tmp_path, resolver=lambda **_kwargs: _resolution(), preparer=lambda **_kwargs: _preparation(), orchestrator=lambda **_kwargs: _orchestration(status), now_func=_clock())
    assert result.final_status == status


def test_repeated_main_and_safety_reuse_snapshots(tmp_path: Path):
    from tests.test_expanded_snapshot_preparation import FakeSource, TICKERS, _investor, _market
    from src.expanded_snapshot_preparation import prepare_expanded_snapshots

    market = FakeSource(_market)
    investor = FakeSource(_investor)
    statuses = iter(["SUCCESS", "ALREADY_COMPLETED"])

    def orchestrator(**_kwargs):
        return _orchestration(next(statuses))

    kwargs = dict(repo_root=tmp_path, market_source=market, investor_source=investor, tickers=TICKERS, resolver=lambda **_kwargs: _resolution(), preparer=prepare_expanded_snapshots, orchestrator=orchestrator)
    first = operational.run_expanded_operational_run(**kwargs, now_func=_clock())
    market.calls.clear(); investor.calls.clear()
    second = operational.run_expanded_operational_run(**kwargs, now_func=_clock())

    assert first.final_status == "SUCCESS"
    assert second.final_status == "ALREADY_COMPLETED"
    assert second.snapshot_preparation["reused_count"] == 6
    assert second.snapshot_preparation["collected_count"] == 0
    assert market.calls == []
    assert investor.calls == []


@pytest.mark.parametrize(
    "status, expected_exit",
    [("SUCCESS", 0), ("SUCCESS_WITH_WARNING", 0), ("DATA_NOT_READY", 0), ("ALREADY_COMPLETED", 0), ("SNAPSHOT_PREPARATION_FAILED", 1), ("DAILY_RUN_FAILED", 1), ("PERFORMANCE_UPDATE_FAILED", 1)],
)
def test_cli_json_and_exit_codes(monkeypatch, capsys, status: str, expected_exit: int):
    result = operational.ExpandedOperationalResult(SOURCE_DATE, status, "start", "end", 1.0)
    monkeypatch.setattr(operational, "run_expanded_operational_run", lambda **_kwargs: result)
    monkeypatch.setattr(operational, "_source_commit", lambda _root: "deadbeef")
    exit_code = operational.main(["--source-date", SOURCE_DATE, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == expected_exit
    assert payload["final_status"] == status


def test_protected_artifacts_unchanged_on_preparation_skip(tmp_path: Path):
    protected = {
        tmp_path / "output/signals.csv": b"production",
        tmp_path / "output/shadow_signal_records.csv": b"shadow",
        tmp_path / "output/dual_shadow_signal_ledger.csv": b"dual",
        tmp_path / "output/daily_scheduler_state.json": b"scheduler",
        tmp_path / "output/expanded_shadow/manifests/2026-09-17/run.json": b"immutable",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    operational.run_expanded_operational_run(repo_root=tmp_path, resolver=lambda **_kwargs: _resolution(), preparer=lambda **_kwargs: _preparation("DATA_NOT_READY"), now_func=_clock())
    assert {path: path.read_bytes() for path in protected} == protected

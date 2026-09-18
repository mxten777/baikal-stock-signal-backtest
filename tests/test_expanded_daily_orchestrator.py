from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import scripts.expanded_daily_orchestrator as orchestrator
from src.expanded_candidate_performance import ExpandedCandidatePerformanceStore
from src.expanded_shadow_daily import ACTION_RUN, ACTION_SKIP, STATUS_ALREADY_COMPLETED, STATUS_DATA_NOT_READY, ExpandedDailyResult
from src.expanded_shadow_ops import ExpandedShadowPaths


SOURCE_DATE = "2026-09-18"
TIMES = iter(["2026-09-18T09:00:00+00:00", "2026-09-18T09:00:03+00:00"])


def _daily(status: str, action: str) -> ExpandedDailyResult:
    return ExpandedDailyResult(
        status=status,
        action=action,
        source_date=SOURCE_DATE,
        completed_run_id="daily-run-1" if status == STATUS_ALREADY_COMPLETED else None,
    )


def _clock():
    values = iter(["2026-09-18T09:00:00+00:00", "2026-09-18T09:00:03+00:00"])
    return lambda: next(values)


def _tracker_clock():
    values = iter(["2026-09-18T09:00:00+00:00", "2026-09-18T09:00:01+00:00", "2026-09-18T09:00:03+00:00"])
    return lambda: next(values)


def _performance(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {"registered": 1, "updated": 2, "mismatch": 0, "benchmark_errors": {}}
    result.update(overrides)
    return result


def test_data_not_ready_is_clean_skip_and_does_not_call_performance(tmp_path: Path):
    protected = tmp_path / "output/shadow_signal_records.csv"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"production")
    calls = []

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily(STATUS_DATA_NOT_READY, ACTION_SKIP),
        performance_runner=lambda **kwargs: calls.append(kwargs),
        now_func=_clock(),
    )

    assert result.final_status == STATUS_DATA_NOT_READY
    assert result.daily_run_status == STATUS_DATA_NOT_READY
    assert result.performance_status == orchestrator.PERFORMANCE_NOT_RUN
    assert result.candidates_registered == 0
    assert result.candidates_updated == 0
    assert calls == []
    assert protected.read_bytes() == b"production"


def test_success_runs_daily_then_performance_and_reports_metadata(tmp_path: Path):
    order = []

    def daily_runner(**_kwargs):
        order.append("daily")
        return _daily("SUCCESS", ACTION_RUN)

    def performance_runner(**_kwargs):
        order.append("performance")
        return _performance()

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        source_commit="deadbeef",
        daily_runner=daily_runner,
        performance_runner=performance_runner,
        now_func=_clock(),
    )

    assert order == ["daily", "performance"]
    assert result.final_status == "SUCCESS"
    assert result.daily_run_status == "SUCCESS"
    assert result.performance_status == "SUCCESS"
    assert result.candidates_registered == 1
    assert result.candidates_updated == 2
    assert result.runtime_seconds == 3.0


def test_already_completed_still_runs_performance_progression(tmp_path: Path):
    calls = []

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily(STATUS_ALREADY_COMPLETED, ACTION_SKIP),
        performance_runner=lambda **kwargs: calls.append(kwargs) or _performance(registered=0, updated=3),
        now_func=_clock(),
    )

    assert len(calls) == 1
    assert result.final_status == STATUS_ALREADY_COMPLETED
    assert result.performance_status == "SUCCESS"
    assert result.candidates_registered == 0
    assert result.candidates_updated == 3


def test_daily_failure_isolated_and_performance_not_called(tmp_path: Path):
    calls = []

    def fail_daily(**_kwargs):
        raise RuntimeError("daily failed")

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=fail_daily,
        performance_runner=lambda **kwargs: calls.append(kwargs),
        now_func=_clock(),
    )

    assert result.final_status == "DAILY_RUN_FAILED"
    assert result.performance_status == "NOT_RUN"
    assert result.error_code == "RuntimeError"
    assert calls == []


def test_performance_failure_does_not_modify_completed_daily_artifact(tmp_path: Path):
    manifest = tmp_path / "output/expanded_shadow/manifests/2026-09-18/run-1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b"immutable-daily")

    def fail_performance(**_kwargs):
        raise ValueError("performance failed")

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily("SUCCESS", ACTION_RUN),
        performance_runner=fail_performance,
        now_func=_clock(),
    )

    assert result.final_status == "PERFORMANCE_UPDATE_FAILED"
    assert result.daily_run_status == "SUCCESS"
    assert result.performance_status == "FAILED"
    assert manifest.read_bytes() == b"immutable-daily"


def test_warning_status_propagates_without_failure(tmp_path: Path):
    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily("SUCCESS_WITH_TICKER_FAILURES", ACTION_RUN),
        performance_runner=lambda **_kwargs: _performance(benchmark_errors={"KS11": "not ready"}),
        now_func=_clock(),
    )

    assert result.final_status == "SUCCESS_WITH_WARNING"
    assert result.performance_status == "SUCCESS_WITH_WARNING"


def test_performance_stage_reuses_existing_loaders_and_tracker(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    signals = _signal_ledger()
    candidates = signals.copy()
    price_map = {"000001": _price(5)}
    benchmark_map = {"KS11": _price(5)}
    calls = []

    monkeypatch.setattr(orchestrator, "_load_signal_ledger", lambda actual_paths: signals if actual_paths == paths else None)
    monkeypatch.setattr(orchestrator, "_load_price_map", lambda actual_paths, source_date, rows: price_map if actual_paths == paths and source_date == SOURCE_DATE and rows.equals(candidates) else None)
    monkeypatch.setattr(orchestrator, "_load_benchmark_map", lambda rows, source_date: (benchmark_map, {"KQ11": "not ready"}) if source_date == SOURCE_DATE and rows.equals(candidates) else ({}, {}))

    def tracker(**kwargs):
        calls.append(kwargs)
        return {"registered": 1, "updated": 1, "mismatch": 0}

    monkeypatch.setattr(orchestrator, "run_expanded_candidate_performance", tracker)
    result = orchestrator.run_performance_stage(repo_root=tmp_path, source_date=SOURCE_DATE, now_func=lambda: NOW)

    assert len(calls) == 1
    assert calls[0]["price_map"] == price_map
    assert calls[0]["benchmark_map"] == benchmark_map
    assert result["registered"] == 1
    assert result["benchmark_errors"] == {"KQ11": "not ready"}


def _signal_ledger() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "basDd": SOURCE_DATE,
                "stock_code": "000001",
                "stock_name": "Fixture",
                "market": "KOSPI",
                "signal_date": SOURCE_DATE,
                "signal_price": 100.0,
                "signal_score": 80.0,
                "foreign_status": "POSITIVE",
                "decision": "CANDIDATE",
                "engine_version": "v0.1",
                "source_commit": "deadbeef",
                "run_id": "run-1",
                "created_at": "2026-09-18T00:00:00+00:00",
            }
        ]
    )


def _price(days: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.bdate_range(SOURCE_DATE, periods=days + 1),
            "close": [100.0 + index for index in range(days + 1)],
        }
    )


def test_repeated_invocation_uses_tracker_idempotency_and_progresses(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    paths.signal_ledger_path.parent.mkdir(parents=True)
    _signal_ledger().to_csv(paths.signal_ledger_path, index=False)
    store = ExpandedCandidatePerformanceStore(paths)
    market = {"000001": _price(10)}
    benchmark = {"KS11": _price(10)}

    def performance_runner(**kwargs):
        return orchestrator.run_expanded_candidate_performance(
            repo_root=kwargs["repo_root"],
            price_map=market,
            benchmark_map=benchmark,
            now_func=kwargs["now_func"],
            store=store,
        )

    first = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily(STATUS_ALREADY_COMPLETED, ACTION_SKIP),
        performance_runner=performance_runner,
        now_func=_tracker_clock(),
    )
    ledger_before = store.path.read_bytes()
    second = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily(STATUS_ALREADY_COMPLETED, ACTION_SKIP),
        performance_runner=performance_runner,
        now_func=_tracker_clock(),
    )

    assert first.candidates_registered == 1
    assert first.candidates_updated == 1
    assert second.candidates_registered == 0
    assert second.candidates_updated == 0
    assert store.path.read_bytes() == ledger_before
    assert len(store.load()) == 1
    assert store.load().iloc[0]["tracking_status"] == "10D"


@pytest.mark.parametrize(
    "final_status, expected_exit",
    [
        ("SUCCESS", 0),
        ("SUCCESS_WITH_WARNING", 0),
        ("DATA_NOT_READY", 0),
        ("ALREADY_COMPLETED", 0),
        ("DAILY_RUN_FAILED", 1),
        ("PERFORMANCE_UPDATE_FAILED", 1),
    ],
)
def test_cli_json_and_exit_semantics(monkeypatch, capsys, final_status: str, expected_exit: int):
    result = orchestrator.ExpandedOrchestrationResult(
        source_date=SOURCE_DATE,
        daily_run_status=final_status,
        performance_status="NOT_RUN",
        candidates_registered=0,
        candidates_updated=0,
        final_status=final_status,
        started_at="2026-09-18T09:00:00+00:00",
        completed_at="2026-09-18T09:00:03+00:00",
        runtime_seconds=3.0,
    )
    monkeypatch.setattr(orchestrator, "run_expanded_daily_orchestration", lambda **_kwargs: result)
    monkeypatch.setattr(orchestrator, "_source_commit", lambda _root: "deadbeef")

    exit_code = orchestrator.main(["--source-date", SOURCE_DATE, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == expected_exit
    assert payload["final_status"] == final_status
    assert payload["source_date"] == SOURCE_DATE


def test_production_shadow_dual_and_scheduler_artifacts_unchanged(tmp_path: Path):
    protected = {
        tmp_path / "output/signals.csv": b"production",
        tmp_path / "output/shadow_signal_records.csv": b"shadow",
        tmp_path / "output/dual_shadow_signal_ledger.csv": b"dual",
        tmp_path / "output/daily_scheduler_state.json": b"scheduler",
        tmp_path / "data/raw/000001.csv": b"production-market",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily(STATUS_DATA_NOT_READY, ACTION_SKIP),
        performance_runner=lambda **_kwargs: _performance(),
        now_func=_clock(),
    )

    assert {path: path.read_bytes() for path in protected} == protected
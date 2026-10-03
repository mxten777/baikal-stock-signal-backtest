from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import scripts.expanded_candidate_performance as performance_cli
import scripts.expanded_daily_orchestrator as orchestrator
from src.expanded_candidate_performance import ExpandedCandidatePerformanceStore
from src.expanded_benchmark_provider import ExpandedBenchmarkTimeoutError
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
    performance_calls = []

    def daily_runner(**_kwargs):
        order.append("daily")
        return _daily("SUCCESS", ACTION_RUN)

    def performance_runner(**_kwargs):
        order.append("performance")
        performance_calls.append(_kwargs)
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
    assert performance_calls[0]["benchmark_provider"] == "legacy"


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


def test_explicit_benchmark_provider_is_forwarded(tmp_path: Path):
    calls = []

    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily("SUCCESS", ACTION_RUN),
        performance_runner=lambda **kwargs: calls.append(kwargs) or _performance(),
        benchmark_provider="naver",
        now_func=_clock(),
    )

    assert result.final_status == "SUCCESS"
    assert calls[0]["benchmark_provider"] == "naver"

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
    warnings = [{"code": "STALE_SOURCE", "symbol": "KS11"}]
    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily("SUCCESS", ACTION_RUN),
        performance_runner=lambda **_kwargs: _performance(benchmark_warnings=warnings),
        now_func=_clock(),
    )

    assert result.final_status == "SUCCESS_WITH_WARNING"
    assert result.performance_status == "SUCCESS_WITH_WARNING"
    assert result.benchmark_warnings == warnings


def test_performance_stage_reuses_existing_loaders_and_tracker(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    signals = _signal_ledger()
    candidates = signals.copy()
    price_map = {"000001": _price(5)}
    benchmark_map = {"KS11": _price(5)}
    calls = []
    loader_calls = []

    monkeypatch.setattr(orchestrator, "_load_signal_ledger", lambda actual_paths: signals if actual_paths == paths else None)
    monkeypatch.setattr(orchestrator, "_load_price_map", lambda actual_paths, source_date, rows: price_map if actual_paths == paths and source_date == SOURCE_DATE and rows.equals(candidates) else None)
    def load_benchmarks(rows, source_date, *, provider):
        loader_calls.append(provider)
        if source_date == SOURCE_DATE and rows.equals(candidates):
            return benchmark_map, {"KQ11": "not ready"}
        return {}, {}

    monkeypatch.setattr(orchestrator, "_load_benchmark_map", load_benchmarks)

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
    assert result["benchmark_provider"] == "legacy"
    assert loader_calls == ["legacy"]
    assert result["benchmark_diagnostics"]["KS11"]["provider"] == "legacy"
    assert result["benchmark_diagnostics"]["KS11"]["cutoff"] == SOURCE_DATE


def test_performance_stage_forwards_naver_and_returns_market_diagnostics(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    signals = _signal_ledger()
    candidates = signals.copy()
    benchmark_map = {"KS11": _price(5)}
    loader_calls = []

    monkeypatch.setattr(orchestrator, "_load_signal_ledger", lambda _paths: signals)
    monkeypatch.setattr(orchestrator, "_load_price_map", lambda _paths, _date, _rows: {"000001": _price(5)})

    def load_benchmarks(rows, source_date, *, provider):
        loader_calls.append((rows, source_date, provider))
        return benchmark_map, {}

    monkeypatch.setattr(orchestrator, "_load_benchmark_map", load_benchmarks)
    monkeypatch.setattr(
        orchestrator,
        "run_expanded_candidate_performance",
        lambda **_kwargs: {"updated": 0, "mismatch": 0, "benchmark_5d_calculated": 1},
    )

    result = orchestrator.run_performance_stage(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        benchmark_provider="naver",
        now_func=lambda: NOW,
    )

    assert len(loader_calls) == 1
    assert loader_calls[0][0].equals(candidates)
    assert loader_calls[0][1:] == (SOURCE_DATE, "naver")
    market = result["benchmark_diagnostics"]["KS11"]
    assert market["provider"] == "naver"
    assert market["cutoff"] == SOURCE_DATE
    assert market["latest_row_date"] == SOURCE_DATE
    assert market["latest_valid_close_date"] == SOURCE_DATE
    assert market["stale"] is False
    assert market["duplicate"] is False
    assert market["invalid_close_dates"] == []
    assert market["provider_error"] is None
    assert result["benchmark_status_by_horizon"]["5d"]["benchmark_calculated"] == 1


@pytest.mark.parametrize("with_snapshot", [False, True], ids=["no-snapshot", "all-horizons-not-due"])
def test_naver_not_due_candidate_skips_provider_and_warnings(tmp_path: Path, monkeypatch, with_snapshot: bool):
    paths = ExpandedShadowPaths(tmp_path)
    paths.signal_ledger_path.parent.mkdir(parents=True)
    _signal_ledger().to_csv(paths.signal_ledger_path, index=False)
    if with_snapshot:
        paths.market_dir(SOURCE_DATE).mkdir(parents=True)
        _price(4).to_csv(paths.market_dir(SOURCE_DATE) / "000001.csv", index=False)
    provider_calls = []

    def fetch(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("Naver provider must not be called before any horizon matures")

    monkeypatch.setattr(performance_cli, "load_expanded_benchmark", fetch)
    result = orchestrator.run_performance_stage(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        benchmark_provider="naver",
        now_func=lambda: "2026-09-18T09:00:00+00:00",
    )

    assert provider_calls == []
    assert result["benchmark_errors"] == {}
    assert result["benchmark_diagnostics"] == {}
    assert result["benchmark_warnings"] == []


def test_naver_timeout_is_warning_and_preserves_stock_return(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    paths.signal_ledger_path.parent.mkdir(parents=True)
    signals = _signal_ledger()
    signals.loc[0, "signal_date"] = "2026-09-10"
    signals.to_csv(paths.signal_ledger_path, index=False)
    market_dir = paths.market_dir(SOURCE_DATE)
    market_dir.mkdir(parents=True)
    dates = pd.bdate_range("2026-09-10", end=SOURCE_DATE)
    pd.DataFrame(
        {"date": dates, "close": [100.0 + index for index in range(len(dates))]}
    ).to_csv(market_dir / "000001.csv", index=False)

    def timeout(*_args, **_kwargs):
        raise ExpandedBenchmarkTimeoutError("simulated hard timeout")

    monkeypatch.setattr(performance_cli, "load_expanded_benchmark", timeout)
    result = orchestrator.run_expanded_daily_orchestration(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        daily_runner=lambda **_kwargs: _daily("SUCCESS", ACTION_RUN),
        benchmark_provider="naver",
        now_func=lambda: "2026-09-18T09:00:00+00:00",
    )

    row = ExpandedCandidatePerformanceStore(paths).load().iloc[0]
    assert result.final_status == "SUCCESS_WITH_WARNING"
    assert result.performance_status == "SUCCESS_WITH_WARNING"
    assert result.benchmark_errors == {"KS11": "ExpandedBenchmarkTimeoutError: simulated hard timeout"}
    assert any(warning["code"] == "PROVIDER_ERROR" for warning in result.benchmark_warnings)
    assert row["return_5d"] == pytest.approx(5.0)
    assert pd.isna(row["benchmark_5d"]) and pd.isna(row["excess_5d"])


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
    updated_at_before = store.load().iloc[0]["updated_at"]
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
    assert store.load().iloc[0]["updated_at"] == updated_at_before
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
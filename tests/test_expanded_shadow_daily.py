from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.expanded_shadow_daily as daily
from src.expanded_shadow_ops import ExpandedRunManifest, ExpandedShadowPaths
from src.expanded_shadow_pipeline import ExpandedPipelineResult


SOURCE_DATE = "2026-09-18"
TICKERS = ("000001", "000002", "000003")


def _patch_small_universe(monkeypatch) -> None:
    universe = SimpleNamespace(tickers=tuple(SimpleNamespace(ticker=ticker) for ticker in TICKERS))
    monkeypatch.setattr(daily, "load_expanded_universe", lambda *_args, **_kwargs: universe)


def _write_snapshot(path: Path, source_date: str = SOURCE_DATE, *, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"date{extra}\n2026-09-17{extra}\n{source_date}{extra}\n", encoding="utf-8")


def _prepare_snapshots(root: Path, *, investor_dates: dict[str, str] | None = None) -> None:
    paths = ExpandedShadowPaths(root)
    investor_dates = investor_dates or {}
    for ticker in TICKERS:
        _write_snapshot(paths.market_dir(SOURCE_DATE) / f"{ticker}.csv")
        _write_snapshot(
            paths.investor_dir(SOURCE_DATE) / f"{ticker}_investor.csv",
            investor_dates.get(ticker, SOURCE_DATE),
        )


def _manifest(*, status: str = "SUCCESS", failed: bool = False) -> ExpandedRunManifest:
    ready = 2 if failed else 3
    signal_count = 2
    return ExpandedRunManifest(
        run_id="daily-run-1",
        basDd=SOURCE_DATE,
        started_at="2026-09-18T09:00:00+00:00",
        finished_at="2026-09-18T09:00:03+00:00",
        runtime_seconds=3.0,
        status=status,
        canonical_universe_count=3,
        attempted_ticker_count=3,
        market_success_count=3,
        investor_success_count=ready,
        ready_count=ready,
        quarantine_count=int(failed),
        signal_count=signal_count,
        new_candidate_count=1,
        status_counts={"READY": ready, "INVESTOR_FAILED": int(failed)},
        market_source_date_distribution={SOURCE_DATE: 3},
        investor_source_date_distribution={SOURCE_DATE: ready, "missing": int(failed)},
        retry_counts={ticker: 1 for ticker in TICKERS},
        universe_sha256="fixture",
        source_commit="deadbeef",
        ledger_path="output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        quarantine_path=f"output/expanded_shadow/quarantine/{SOURCE_DATE}.jsonl",
        ticker_failure_sample=(
            [{"ticker": "000003", "status": "INVESTOR_FAILED", "error_code": "FETCH_FAILED"}]
            if failed
            else []
        ),
    )


def _pipeline_result(*, failed: bool = False) -> ExpandedPipelineResult:
    status = "SUCCESS_WITH_TICKER_FAILURES" if failed else "SUCCESS"
    return ExpandedPipelineResult(
        run_id="daily-run-1",
        basDd=SOURCE_DATE,
        status=status,
        manifest=_manifest(status=status, failed=failed),
        registry_appended=True,
    )


def _write_completed_artifacts(root: Path, result: ExpandedPipelineResult) -> dict[Path, bytes]:
    paths = ExpandedShadowPaths(root)
    run_path = paths.run_manifest_path(SOURCE_DATE, result.run_id)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(result.manifest.to_dict(), sort_keys=True) + "\n", encoding="utf-8")
    paths.latest_manifest_path(SOURCE_DATE).write_text(
        json.dumps(
            {
                "basDd": SOURCE_DATE,
                "run_id": result.run_id,
                "manifest_path": str(run_path.resolve()),
                "status": result.status,
                "updated_at": result.manifest.finished_at,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    paths.registry_path.write_text('{"event_id":"one"}\n', encoding="utf-8")
    paths.signal_ledger_path.write_text("basDd,stock_code\n2026-09-18,000001\n", encoding="utf-8")
    return {path: path.read_bytes() for path in (run_path, paths.latest_manifest_path(SOURCE_DATE), paths.registry_path, paths.signal_ledger_path)}


def test_normal_daily_run_dispatches_once_after_matching_gate(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    _prepare_snapshots(tmp_path)
    calls = []

    def fake_pipeline(**kwargs):
        calls.append(kwargs)
        return _pipeline_result()

    result = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        source_commit="deadbeef",
        run_id="daily-run-1",
        pipeline_runner=fake_pipeline,
    )

    assert result.action == "RUN"
    assert result.status == "SUCCESS"
    assert result.readiness is not None and result.readiness.ready
    assert result.readiness.market_ready_count == 3
    assert result.readiness.investor_ready_count == 3
    assert len(calls) == 1
    assert calls[0]["use_lock"] is False
    assert calls[0]["retry_policy"].max_attempts == 1
    assert not ExpandedShadowPaths(tmp_path).lock_path.exists()
    assert result.to_dict()["excluded_count"] == 1
    assert result.to_dict()["no_signal_count"] == 1


def test_data_not_ready_skips_without_artifacts(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    _prepare_snapshots(tmp_path, investor_dates={"000003": "2026-09-17"})
    calls = []

    result = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        pipeline_runner=lambda **kwargs: calls.append(kwargs),
    )

    paths = ExpandedShadowPaths(tmp_path)
    assert result.status == "DATA_NOT_READY"
    assert result.action == "SKIP"
    assert calls == []
    assert result.readiness is not None
    assert result.readiness.investor_source_dates == {SOURCE_DATE: 2, "2026-09-17": 1}
    assert result.readiness.failed_tickers == (
        {
            "ticker": "000003",
            "dataset": "INVESTOR",
            "source_date": "2026-09-17",
            "expected_source_date": SOURCE_DATE,
            "error_code": "SOURCE_DATE_MISMATCH",
        },
    )
    assert not paths.registry_path.exists()
    assert not paths.manifests_dir.exists()


def test_same_date_rerun_skips_and_preserves_immutable_artifacts(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    before = _write_completed_artifacts(tmp_path, _pipeline_result())
    calls = []

    result = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        pipeline_runner=lambda **kwargs: calls.append(kwargs),
    )

    assert result.status == "ALREADY_COMPLETED"
    assert result.action == "SKIP"
    assert result.completed_run_id == "daily-run-1"
    assert calls == []
    assert {path: path.read_bytes() for path in before} == before


def test_first_run_then_rerun_has_no_ledger_or_registry_duplicate(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    _prepare_snapshots(tmp_path)
    calls = []

    def publishing_pipeline(**kwargs):
        calls.append(kwargs)
        result = _pipeline_result()
        _write_completed_artifacts(tmp_path, result)
        return result

    first = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        run_id="daily-run-1",
        pipeline_runner=publishing_pipeline,
    )
    paths = ExpandedShadowPaths(tmp_path)
    immutable_before = paths.run_manifest_path(SOURCE_DATE, "daily-run-1").read_bytes()
    second = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        pipeline_runner=publishing_pipeline,
    )

    assert first.action == "RUN"
    assert second.status == "ALREADY_COMPLETED"
    assert len(calls) == 1
    assert len(paths.registry_path.read_text(encoding="utf-8").splitlines()) == 1
    assert len(paths.signal_ledger_path.read_text(encoding="utf-8").splitlines()) == 2
    assert paths.run_manifest_path(SOURCE_DATE, "daily-run-1").read_bytes() == immutable_before


def test_partial_ticker_failure_is_isolated_and_reported(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    _prepare_snapshots(tmp_path)

    result = daily.run_expanded_shadow_daily(
        repo_root=tmp_path,
        source_date=SOURCE_DATE,
        pipeline_runner=lambda **_kwargs: _pipeline_result(failed=True),
    )
    payload = result.to_dict()

    assert result.status == "SUCCESS_WITH_TICKER_FAILURES"
    assert payload["attempted"] == 3
    assert payload["ready"] == 2
    assert payload["failure_count"] == 1
    assert payload["failed_tickers"] == [
        {"ticker": "000003", "status": "INVESTOR_FAILED", "error_code": "FETCH_FAILED"}
    ]


def test_production_shadow_and_dual_files_remain_unchanged(tmp_path: Path, monkeypatch):
    _patch_small_universe(monkeypatch)
    _prepare_snapshots(tmp_path, investor_dates={"000003": "2026-09-17"})
    protected = {
        tmp_path / "data/raw/005930.csv": b"production-market",
        tmp_path / "data/investor/005930_investor.csv": b"production-investor",
        tmp_path / "output/signals.csv": b"production-ledger",
        tmp_path / "output/shadow_signal_records.csv": b"shadow-ledger",
        tmp_path / "output/dual_shadow_signal_ledger.csv": b"dual-ledger",
        tmp_path / "output/dual_shadow_run_registry.jsonl": b"dual-registry",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    daily.run_expanded_shadow_daily(repo_root=tmp_path, source_date=SOURCE_DATE)

    assert {path: path.read_bytes() for path in protected} == protected


@pytest.mark.parametrize("source_date", ["20260918", "../2026-09-18", "2026-02-30"])
def test_invalid_source_date_fails_before_path_access(tmp_path: Path, monkeypatch, source_date: str):
    _patch_small_universe(monkeypatch)

    with pytest.raises(daily.ExpandedDailyError, match="source_date"):
        daily.run_expanded_shadow_daily(repo_root=tmp_path, source_date=source_date)
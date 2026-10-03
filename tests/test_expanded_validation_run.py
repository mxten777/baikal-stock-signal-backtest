from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import scripts.expanded_validation_run as validation
from src.expanded_benchmark_provider import ExpandedBenchmark, ExpandedBenchmarkTimeoutError


def _benchmark(symbol: str, cutoff: str) -> ExpandedBenchmark:
    source = validation.NAVER_SOURCE_BY_SYMBOL[symbol]
    return ExpandedBenchmark(
        symbol=symbol,
        source=source,
        cutoff_date=cutoff,
        dates=("2026-09-01", cutoff),
        closes={"2026-09-01": 100.0, cutoff: 101.0},
        latest_row_date=cutoff,
        latest_valid_close_date=cutoff,
    )


def test_provider_smoke_uses_both_hard_timeout_provider_symbols():
    calls = []

    def loader(symbol: str, start: str, cutoff: str) -> ExpandedBenchmark:
        calls.append((symbol, start, cutoff))
        return _benchmark(symbol, cutoff)

    results, remaining_children = validation.run_provider_smoke(
        start_date="2026-09-01",
        cutoff_date="2026-10-02",
        benchmark_loader=loader,
    )

    assert calls == [
        ("KS11", "2026-09-01", "2026-10-02"),
        ("KQ11", "2026-09-01", "2026-10-02"),
    ]
    assert [item["source"] for item in results] == ["NAVER:KOSPI", "NAVER:KOSDAQ"]
    assert all(item["status"] == "SUCCESS" for item in results)
    assert all(item["requested_start_date"] == "2026-09-01" for item in results)
    assert all(item["returned_start_date"] == "2026-09-01" for item in results)
    assert all(item["returned_end_date"] == "2026-10-02" for item in results)
    assert all(item["runtime_seconds"] >= 0 for item in results)
    assert remaining_children == []


def test_smoke_timeout_is_recorded_and_other_market_is_still_attempted():
    calls = []

    def loader(symbol: str, start: str, cutoff: str) -> ExpandedBenchmark:
        calls.append(symbol)
        if symbol == "KS11":
            raise ExpandedBenchmarkTimeoutError("timeout")
        return _benchmark(symbol, cutoff)

    results, _ = validation.run_provider_smoke(
        start_date="2026-09-01",
        cutoff_date="2026-10-02",
        benchmark_loader=loader,
    )

    assert calls == ["KS11", "KQ11"]
    assert results[0]["status"] == "FAILED"
    assert results[0]["timeout"] is True
    assert results[0]["error_code"] == "ExpandedBenchmarkTimeoutError"
    assert results[1]["status"] == "SUCCESS"


def test_operational_validation_captures_logs_outside_production_artifacts(tmp_path: Path):
    repo = tmp_path / "repo"
    artifacts = tmp_path / "validation-logs"
    repo.mkdir()
    ledger = repo / "output" / "expanded_shadow" / "expanded_candidate_performance_ledger.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"official-ledger")
    before = ledger.read_bytes()
    invoked = {}

    def process_runner(command, **kwargs):
        invoked.update(command=command, **kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"final_status":"SUCCESS","orchestration":{"performance":{"updated":0}}}\n',
            stderr="operational stderr",
        )

    exit_code, artifact_path = validation.run_validation(
        mode="operational",
        repo_root=repo,
        artifact_dir=artifacts,
        benchmark_provider="naver",
        source_date="2026-10-02",
        process_runner=process_runner,
    )

    record = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert invoked["command"][-4:] == ["--benchmark-provider", "naver", "--source-date", "2026-10-02"]
    assert invoked["cwd"] == repo.resolve()
    assert invoked["check"] is False
    assert record["benchmark_provider"] == "naver"
    assert record["exit_code"] == 0
    assert record["stdout"].startswith('{"final_status":"SUCCESS"')
    assert record["stderr"] == "operational stderr"
    assert record["runtime_seconds"] >= 0
    assert record["started_at"] and record["completed_at"]
    assert record["result"]["orchestration"]["performance"]["updated"] == 0
    assert ledger.read_bytes() == before
    assert artifact_path.parent == artifacts.resolve()
    assert not artifact_path.is_relative_to(repo.resolve())


def test_provider_smoke_logs_to_separate_artifact_without_ledger_writes(tmp_path: Path):
    repo = tmp_path / "repo"
    artifacts = tmp_path / "validation-logs"
    repo.mkdir()
    ledger = repo / "output" / "expanded_shadow" / "expanded_candidate_performance_ledger.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"official-ledger")
    before = ledger.read_bytes()

    exit_code, artifact_path = validation.run_validation(
        mode="smoke",
        repo_root=repo,
        artifact_dir=artifacts,
        start_date="2026-09-01",
        cutoff_date="2026-10-02",
        benchmark_loader=lambda symbol, _start, cutoff: _benchmark(symbol, cutoff),
    )

    record = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert record["benchmark_provider"] == "naver"
    assert [item["source"] for item in record["result"]["symbols"]] == ["NAVER:KOSPI", "NAVER:KOSDAQ"]
    assert record["result"]["child_processes_remaining"] == []
    assert ledger.read_bytes() == before
    assert artifact_path.parent == artifacts.resolve()


def test_validation_refuses_artifact_directory_inside_repository(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="outside the repository"):
        validation.run_validation(
            mode="operational",
            repo_root=repo,
            artifact_dir=repo / "output" / "validation",
        )
    assert not (repo / "output").exists()


def test_validation_rejects_invalid_dates_from_cli():
    with pytest.raises(SystemExit) as exc_info:
        validation.main([
            "smoke",
            "--artifact-dir", "C:\\validation",
            "--start-date", "2026-9-1",
            "--cutoff-date", "2026-10-02",
        ])
    assert exc_info.value.code == 2

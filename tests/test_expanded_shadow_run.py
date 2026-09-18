from __future__ import annotations

import json
from dataclasses import dataclass

from scripts import expanded_shadow_run as cli


@dataclass(frozen=True)
class DummyManifest:
    canonical_universe_count: int = 574
    attempted_ticker_count: int = 574
    ready_count: int = 574
    quarantine_count: int = 0
    signal_count: int = 0
    new_candidate_count: int = 0
    status_counts: dict[str, int] | None = None


@dataclass(frozen=True)
class DummyPipelineResult:
    status: str = "SUCCESS"
    run_id: str = "run-1"
    basDd: str = "2026-09-17"
    manifest: DummyManifest = DummyManifest(status_counts={"READY": 574})


class FakeMarketSource:
    def fetch(self, ticker: str, start: str, end: str):
        return {"ticker": ticker, "start": start, "end": end}


class ForbiddenInvestorSource:
    def fetch(self, *_args):
        raise AssertionError("unit test must not call real provider")


def test_default_cli_blocks_without_real_provider_approval(capsys):
    exit_code = cli.main(["--bas-dd", "2026-09-17", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "BLOCKED"
    assert payload["error_code"] == "REAL_PROVIDER_RUN_REQUIRES_ALLOW_FLAG"
    assert payload["basDd"] == "2026-09-17"


def test_allow_real_providers_wires_mocked_pipeline(monkeypatch, capsys):
    seen = {}

    def fake_pipeline(**kwargs):
        seen.update(kwargs)
        return DummyPipelineResult(basDd=kwargs["basDd"])

    monkeypatch.setattr(cli, "FinanceDataReaderMarketSource", FakeMarketSource)
    monkeypatch.setattr(cli, "NaverInvestorFlowSource", ForbiddenInvestorSource)
    monkeypatch.setattr(cli, "run_expanded_shadow_pipeline", fake_pipeline)
    monkeypatch.setattr(cli, "_source_commit", lambda repo_root: "deadbeef")

    exit_code = cli.main(["--bas-dd", "2026-09-17", "--allow-real-providers", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "SUCCESS"
    assert payload["basDd"] == "2026-09-17"
    assert payload["market_start_date"] == "2024-01-01"
    assert seen["basDd"] == "2026-09-17"
    assert seen["retry_policy"].max_attempts == 2
    assert seen["source_commit"] == "deadbeef"
    assert isinstance(seen["market_source"], cli.FixedStartMarketSource)
    assert isinstance(seen["investor_source"], ForbiddenInvestorSource)


def test_fixed_start_market_source_forces_d8a_start_date():
    source = FakeMarketSource()
    wrapped = cli.FixedStartMarketSource(source)

    result = wrapped.fetch("0015N0", "1900-01-01", "2026-09-17")

    assert result == {"ticker": "0015N0", "start": "2024-01-01", "end": "2026-09-17"}


def test_cli_reports_pipeline_failure_without_real_provider_call(monkeypatch, capsys):
    def fake_pipeline(**_kwargs):
        raise RuntimeError("mock failure")

    monkeypatch.setattr(cli, "FinanceDataReaderMarketSource", FakeMarketSource)
    monkeypatch.setattr(cli, "NaverInvestorFlowSource", ForbiddenInvestorSource)
    monkeypatch.setattr(cli, "run_expanded_shadow_pipeline", fake_pipeline)
    monkeypatch.setattr(cli, "_source_commit", lambda repo_root: "deadbeef")

    exit_code = cli.main(["--bas-dd", "2026-09-17", "--allow-real-providers", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "FAILED"
    assert payload["error_code"] == "RuntimeError"
    assert payload["message"] == "mock failure"
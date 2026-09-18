from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.expanded_shadow_data import DATASET_INVESTOR, DATASET_MARKET
from src.expanded_shadow_eligibility import (
    STATUS_DATA_INVALID,
    STATUS_INSUFFICIENT_HISTORY,
    STATUS_INVESTOR_FAILED,
    STATUS_MARKET_FAILED,
    STATUS_READY,
    STATUS_SOURCE_LAG,
)
from src.expanded_shadow_ops import ExpandedLockConflictError, ExpandedRunLock, ExpandedShadowPaths
from src.expanded_shadow_pipeline import STATUS_SUCCESS, STATUS_SUCCESS_WITH_TICKER_FAILURES, run_expanded_shadow_pipeline
from src.expanded_shadow_signal import ExpandedSignalEvaluation
from src.expanded_shadow_universe import EXPECTED_BAS_DD, compute_universe_sha256
from src.shadow_tracking import DECISION_CANDIDATE, DECISION_EXCLUDED, EXCLUSION_REASON_FOREIGN_NEGATIVE


BAS_DD = EXPECTED_BAS_DD
EXPECTED_SHA = "073982938b6dd222d6b0ca3621ce763a15fd9af43c835ddd7676e78bcd71c6d2"


def _valid_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    kospi_codes = ["0126Z0", "0120G0"] + [f"{number:06d}" for number in range(1, 266)]
    kosdaq_codes = ["0015N0", "0007C0", "0011A0"] + [f"{number:06d}" for number in range(300000, 300304)]
    for code in kospi_codes:
        rows.append({"ticker": code, "name": f"KOSPI {code}", "market": "KOSPI", "source_basDd": BAS_DD})
    for code in kosdaq_codes:
        rows.append({"ticker": code, "name": f"KOSDAQ {code}", "market": "KOSDAQ", "source_basDd": BAS_DD})
    assert len(rows) == 574
    return rows


def _write_universe(repo_root: Path) -> None:
    path = repo_root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "name", "market", "source_basDd"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(_valid_rows())


def _market_frame(ticker: str, rows: int = 60, source_date: str = BAS_DD, invalid: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(source_date), periods=rows)
    close = [100.0 + index for index in range(rows)]
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": [value + 2 for value in close],
            "low": [value - 2 for value in close],
            "close": close,
            "volume": [1_000_000] * rows,
            "ticker": [ticker] * rows,
        }
    )
    if invalid:
        frame = frame.drop(columns=["close"])
    return frame


def _investor_frame(ticker: str, source_date: str = BAS_DD) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp(source_date), periods=5)
    return pd.DataFrame(
        {
            "date": dates,
            "ticker": [ticker] * 5,
            "foreign_net_buy": [1, 2, 3, 4, 5],
            "institution_net_buy": [0, 0, 0, 0, 0],
        }
    )


class FakeMarketSource:
    def __init__(self, plans: dict[str, str]):
        self.plans = plans
        self.calls: list[str] = []

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append(ticker)
        plan = self.plans.get(ticker, "READY")
        if plan == "MARKET_FAILED":
            raise TimeoutError("market timeout")
        if plan == "DATA_INVALID":
            return _market_frame(ticker, invalid=True)
        if plan == "SOURCE_LAG_MARKET":
            return _market_frame(ticker, source_date="2026-09-16")
        if plan == "INSUFFICIENT_HISTORY":
            return _market_frame(ticker, rows=59)
        return _market_frame(ticker)


class FakeInvestorSource:
    def __init__(self, plans: dict[str, str]):
        self.plans = plans
        self.calls: list[str] = []

    def fetch(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append(ticker)
        plan = self.plans.get(ticker, "READY")
        if plan == "INVESTOR_FAILED":
            raise TimeoutError("investor timeout")
        if plan == "SOURCE_LAG_INVESTOR":
            return _investor_frame(ticker, source_date="2026-09-16")
        return _investor_frame(ticker)


class FakeSignalEvaluator:
    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, *, eligibility, name: str, market: str, paths: ExpandedShadowPaths) -> ExpandedSignalEvaluation:
        self.calls.append(eligibility.ticker)
        if eligibility.ticker == "000007":
            return _signal(eligibility.ticker, name, market, decision=DECISION_EXCLUDED)
        if eligibility.ticker == "000006":
            return _signal(eligibility.ticker, name, market, decision=DECISION_CANDIDATE)
        return _signal(eligibility.ticker, name, market, signal_present=False)


def _signal(ticker: str, name: str, market: str, signal_present: bool = True, decision: str = DECISION_CANDIDATE) -> ExpandedSignalEvaluation:
    excluded = decision == DECISION_EXCLUDED
    return ExpandedSignalEvaluation(
        ticker=ticker,
        name=name,
        market=market,
        basDd=BAS_DD,
        evaluated=True,
        signal_present=signal_present,
        signal_date=BAS_DD if signal_present else None,
        signal_price=100.0 if signal_present else None,
        raw_score=52 if signal_present else None,
        signal_score=80.0 if signal_present else None,
        signal_type="BUY_WATCH" if signal_present else None,
        foreign_5d_ratio=-0.25 if excluded else (None if not signal_present else 0.25),
        foreign_status="NEGATIVE" if excluded else (None if not signal_present else "POSITIVE"),
        decision=decision if signal_present else None,
        exclusion_reason=EXCLUSION_REASON_FOREIGN_NEGATIVE if excluded else None,
        reason=None if signal_present else "NO_SIGNAL",
    )


def _run_fake(repo_root: Path, run_id: str = "run-fake-574"):
    _write_universe(repo_root)
    rows = _valid_rows()
    plans = {
        rows[0]["ticker"]: "MARKET_FAILED",
        rows[1]["ticker"]: "INVESTOR_FAILED",
        rows[2]["ticker"]: "SOURCE_LAG_INVESTOR",
        rows[3]["ticker"]: "INSUFFICIENT_HISTORY",
        rows[4]["ticker"]: "DATA_INVALID",
        rows[5]["ticker"]: "SOURCE_LAG_MARKET",
    }
    evaluator = FakeSignalEvaluator()
    result = run_expanded_shadow_pipeline(
        repo_root=repo_root,
        basDd=BAS_DD,
        market_source=FakeMarketSource(plans),
        investor_source=FakeInvestorSource(plans),
        run_id=run_id,
        source_commit="deadbeef",
        signal_evaluator=evaluator,
        now_func=lambda: "2026-09-17T00:00:00+00:00",
    )
    return result, evaluator, plans, repo_root


@pytest.fixture(scope="module")
def fake_574_run(tmp_path_factory):
    return _run_fake(tmp_path_factory.mktemp("expanded_fake_574"))


def test_fake_574_e2e_attempts_all_tickers(fake_574_run):
    result, _evaluator, _plans, _repo_root = fake_574_run

    assert result.manifest.canonical_universe_count == 574
    assert result.manifest.attempted_ticker_count == 574
    assert len(result.ticker_results) == 574


def test_status_counts_sum_to_574_and_all_statuses_reachable(fake_574_run):
    result, _evaluator, _plans, _repo_root = fake_574_run

    assert sum(result.status_counts.values()) == 574
    assert result.status_counts[STATUS_MARKET_FAILED] == 1
    assert result.status_counts[STATUS_INVESTOR_FAILED] == 1
    assert result.status_counts[STATUS_SOURCE_LAG] == 2
    assert result.status_counts[STATUS_INSUFFICIENT_HISTORY] == 1
    assert result.status_counts[STATUS_DATA_INVALID] == 1
    assert result.status_counts[STATUS_READY] == 568


def test_ticker_failures_do_not_stop_batch(fake_574_run):
    result, evaluator, _plans, _repo_root = fake_574_run

    assert result.status == STATUS_SUCCESS_WITH_TICKER_FAILURES
    assert len(evaluator.calls) == result.status_counts[STATUS_READY]
    assert result.status_counts[STATUS_READY] > 0


def test_ready_only_signal_evaluation(fake_574_run):
    result, evaluator, plans, _repo_root = fake_574_run
    failed_tickers = set(plans)

    assert failed_tickers.isdisjoint(evaluator.calls)
    assert set(evaluator.calls) == {item.ticker for item in result.ticker_results if item.eligibility.status == STATUS_READY}


def test_no_signal_is_not_quarantined_or_written_to_ledger(fake_574_run):
    result, _evaluator, _plans, repo_root = fake_574_run
    ledger = pd.read_csv(repo_root / "output" / "expanded_shadow" / "expanded_shadow_signal_ledger.csv", dtype={"stock_code": str})
    no_signal_tickers = {item.ticker for item in result.ticker_results if item.signal is not None and not item.signal.signal_present}

    assert no_signal_tickers
    assert no_signal_tickers.isdisjoint(set(ledger["stock_code"].astype(str)))
    assert result.manifest.quarantine_count == 6


def test_candidate_and_excluded_written_to_ledger(fake_574_run):
    _result, _evaluator, _plans, repo_root = fake_574_run
    ledger = pd.read_csv(repo_root / "output" / "expanded_shadow" / "expanded_shadow_signal_ledger.csv", dtype={"stock_code": str})

    assert DECISION_CANDIDATE in set(ledger["decision"])
    assert DECISION_EXCLUDED in set(ledger["decision"])
    assert EXCLUSION_REASON_FOREIGN_NEGATIVE in set(ledger["exclusion_reason"].dropna())


def test_quarantine_manifest_registry_written(fake_574_run):
    result, _evaluator, _plans, repo_root = fake_574_run
    output = repo_root / "output" / "expanded_shadow"
    paths = ExpandedShadowPaths(repo_root)

    manifest = json.loads(paths.resolve_manifest_path(BAS_DD).read_text(encoding="utf-8"))
    latest = json.loads(paths.latest_manifest_path(BAS_DD).read_text(encoding="utf-8"))
    current = json.loads((output / "expanded_shadow_run.json").read_text(encoding="utf-8"))
    registry_lines = (output / "expanded_shadow_run_registry.jsonl").read_text(encoding="utf-8").splitlines()
    quarantine_lines = (output / "quarantine" / f"{BAS_DD}.jsonl").read_text(encoding="utf-8").splitlines()

    assert manifest["attempted_ticker_count"] == 574
    assert latest["run_id"] == result.run_id
    assert current["run_id"] == result.run_id
    assert len(registry_lines) == 1
    assert len(quarantine_lines) == 6


def test_rerun_same_run_id_is_idempotent(tmp_path: Path):
    first, _evaluator1, _plans1, _repo_root1 = _run_fake(tmp_path, run_id="stable-run")
    second, _evaluator2, _plans2, _repo_root2 = _run_fake(tmp_path, run_id="stable-run")
    output = tmp_path / "output" / "expanded_shadow"
    registry_lines = (output / "expanded_shadow_run_registry.jsonl").read_text(encoding="utf-8").splitlines()
    quarantine_lines = (output / "quarantine" / f"{BAS_DD}.jsonl").read_text(encoding="utf-8").splitlines()
    ledger = pd.read_csv(output / "expanded_shadow_signal_ledger.csv")

    assert first.manifest.to_dict() == second.manifest.to_dict()
    assert len(registry_lines) == 1
    assert len(quarantine_lines) == 6
    assert len(ledger) == first.manifest.signal_count


def test_second_run_same_basdd_publishes_new_manifest_and_latest(tmp_path: Path):
    first, _evaluator1, _plans1, _repo_root1 = _run_fake(tmp_path, run_id="run-1")
    second, _evaluator2, _plans2, _repo_root2 = _run_fake(tmp_path, run_id="run-2")
    paths = ExpandedShadowPaths(tmp_path)
    latest = json.loads(paths.latest_manifest_path(BAS_DD).read_text(encoding="utf-8"))
    current = json.loads(paths.current_run_path.read_text(encoding="utf-8"))
    registry = (paths.registry_path).read_text(encoding="utf-8").splitlines()

    assert paths.run_manifest_path(BAS_DD, first.run_id).exists()
    assert paths.run_manifest_path(BAS_DD, second.run_id).exists()
    assert latest["run_id"] == second.run_id
    assert current["run_id"] == second.run_id
    assert len(registry) == 2


def test_failed_run_is_recorded_without_advancing_latest_or_current(tmp_path: Path):
    _write_universe(tmp_path)
    paths = ExpandedShadowPaths(tmp_path)
    legacy = paths.manifest_path(BAS_DD)
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"legacy":true}\n', encoding="utf-8")
    paths.current_run_path.parent.mkdir(parents=True, exist_ok=True)
    paths.current_run_path.write_text('{"run_id":"prior"}\n', encoding="utf-8")
    current_before = paths.current_run_path.read_bytes()

    def fail_signal(**_kwargs):
        raise RuntimeError("signal failed")

    with pytest.raises(RuntimeError, match="signal failed"):
        run_expanded_shadow_pipeline(
            repo_root=tmp_path,
            basDd=BAS_DD,
            market_source=FakeMarketSource({}),
            investor_source=FakeInvestorSource({}),
            run_id="failed-run",
            source_commit="deadbeef",
            signal_evaluator=fail_signal,
            now_func=lambda: "2026-09-17T00:00:00+00:00",
        )

    failure = json.loads(paths.run_manifest_path(BAS_DD, "failed-run").read_text(encoding="utf-8"))
    registry = [json.loads(line) for line in paths.registry_path.read_text(encoding="utf-8").splitlines()]
    assert failure["status"] == "SYSTEM_FAILURE"
    assert failure["attempted_ticker_count"] == 1
    assert failure["system_failures"] == [
        {
            "attempted": 1,
            "error_class": "RuntimeError",
            "error_message": "signal failed",
            "last_stage": "SIGNAL:0126Z0",
        }
    ]
    assert registry[0]["event_type"] == "RUN_FAILED"
    assert registry[0]["last_stage"] == "SIGNAL:0126Z0"
    assert paths.resolve_manifest_path(BAS_DD) == legacy
    assert paths.current_run_path.read_bytes() == current_before
    assert not paths.latest_manifest_path(BAS_DD).exists()


def test_all_ready_success_status(tmp_path: Path):
    _write_universe(tmp_path)
    evaluator = FakeSignalEvaluator()
    result = run_expanded_shadow_pipeline(
        repo_root=tmp_path,
        basDd=BAS_DD,
        market_source=FakeMarketSource({}),
        investor_source=FakeInvestorSource({}),
        run_id="all-ready",
        source_commit="deadbeef",
        signal_evaluator=evaluator,
        now_func=lambda: "2026-09-17T00:00:00+00:00",
    )

    assert result.status == STATUS_SUCCESS
    assert result.manifest.ready_count == 574
    assert result.manifest.quarantine_count == 0


def test_lock_conflict_blocks_run(tmp_path: Path):
    _write_universe(tmp_path)
    paths = ExpandedShadowPaths(tmp_path)
    ExpandedRunLock(paths, now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")).acquire("owner", BAS_DD)

    with pytest.raises(ExpandedLockConflictError):
        run_expanded_shadow_pipeline(
            repo_root=tmp_path,
            basDd=BAS_DD,
            market_source=FakeMarketSource({}),
            investor_source=FakeInvestorSource({}),
            run_id="blocked",
            source_commit="deadbeef",
            signal_evaluator=FakeSignalEvaluator(),
            now_func=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


def test_fail_safe_cli_blocks_real_execution():
    from scripts.expanded_shadow_run import main

    assert main(["--json"]) == 2


def test_production_shadow_dual_paths_not_written(tmp_path: Path):
    _run_fake(tmp_path)

    assert not (tmp_path / "data" / "raw").exists()
    assert not (tmp_path / "data" / "investor").exists()
    assert not (tmp_path / "output" / "signals.csv").exists()
    assert not (tmp_path / "output" / "shadow_signal_records.csv").exists()
    assert not (tmp_path / "output" / "dual_shadow_signal_ledger.csv").exists()


def test_repo_d1_universe_unchanged():
    root = Path(__file__).resolve().parents[1]
    controlled = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    snapshot = root / "data" / "expanded_shadow" / "universe" / "snapshots" / "2026-09-17_expanded_universe_574.csv"

    assert compute_universe_sha256(controlled) == EXPECTED_SHA
    assert compute_universe_sha256(snapshot) == EXPECTED_SHA
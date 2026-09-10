"""DUAL Shadow STEP 6 daily pipeline tests.

All tests use synthetic tmp_path stores and never touch real output files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import src.dual_shadow_daily_pipeline as pipeline
from src.dual_shadow_daily_pipeline import (
    STATUS_DATA_NOT_READY,
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_SUCCESS_NO_NEW_EVIDENCE,
    run_dual_shadow_daily_pipeline,
)
from src.dual_shadow_forward_returns import DualForwardReturnRecord, DualForwardReturnStore
from src.dual_shadow_ledger import LEDGER_FIELDS, DualShadowLedgerStore
from src.dual_shadow_performance import STATUS_NO_AVAILABLE_EVIDENCE, STATUS_OK

TICKER = "005930"
TICKERS = {TICKER: "TEST"}


def _price_df(periods: int, start: str = "2026-07-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods, freq="B")
    closes = [100.0 + idx for idx in range(periods)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * periods,
        }
    )


def _investor_df(price_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": price_df["date"],
            "foreign_net_buy": [0.0] * len(price_df),
            "institution_net_buy": [0.0] * len(price_df),
        }
    )


def _ledger_row(
    trade_date: str,
    stock_code: str = TICKER,
    horizon_ready_close: float = 100.0,
    comparison_group: str = "BOTH_YES",
    baseline_signal_present: bool = True,
    challenger_signal_present: bool = True,
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": "TEST",
        "evaluation_close": horizon_ready_close,
        "baseline_raw_score": 5,
        "baseline_score": 80.0,
        "baseline_signal_type": "BUY_WATCH",
        "baseline_signal_present": baseline_signal_present,
        "challenger_raw_score": 5,
        "challenger_score": 80.0,
        "challenger_signal_type": "BUY_WATCH",
        "challenger_signal_present": challenger_signal_present,
        "challenger_volume_penalty": 0,
        "challenger_pre_return_penalty": 0,
        "challenger_rsi_penalty": 0,
        "challenger_total_penalty": 0,
        "comparison_group": comparison_group,
        "evaluation_status": "OK",
        "baseline_engine_version": "v0.1",
        "challenger_engine_version": "v0.2",
        "source_commit": "seed",
        "created_at": "2026-07-01T00:00:00+00:00",
    }


@pytest.fixture
def stores(tmp_path):
    ledger = DualShadowLedgerStore(tmp_path / "dual_shadow_signal_ledger.csv")
    forward = DualForwardReturnStore(tmp_path / "dual_shadow_forward_returns.csv")
    summary = tmp_path / "dual_shadow_performance_summary.json"
    registry = tmp_path / "dual_shadow_run_registry.jsonl"
    return ledger, forward, summary, registry


def _run(stores, price_df=None, target_trade_date=None, tickers=None):
    ledger, forward, summary, registry = stores
    frame = price_df if price_df is not None else _price_df(80)
    return run_dual_shadow_daily_pipeline(
        target_trade_date=target_trade_date,
        tickers=tickers or TICKERS,
        price_data={TICKER: frame},
        investor_data={TICKER: _investor_df(frame)},
        ledger_store=ledger,
        forward_store=forward,
        performance_summary_path=summary,
        run_registry_path=registry,
        repo_root=Path(__file__).parent.parent,
    )


def _seed_ledger(ledger: DualShadowLedgerStore, rows: list[dict[str, object]]) -> None:
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=LEDGER_FIELDS).to_csv(ledger.path, index=False)


def _seed_forward_horizons(
    forward: DualForwardReturnStore,
    frame: pd.DataFrame,
    trade_date: str,
    horizons: list[int],
) -> None:
    for horizon in horizons:
        target_close = float(frame["close"].iloc[horizon])
        forward.add(
            DualForwardReturnRecord(
                trade_date=trade_date,
                stock_code=TICKER,
                stock_name="TEST",
                evaluation_close=float(frame["close"].iloc[0]),
                baseline_signal_present=True,
                challenger_signal_present=True,
                comparison_group="BOTH_YES",
                horizon=horizon,
                target_date=frame["date"].iloc[horizon].strftime("%Y-%m-%d"),
                target_close=target_close,
                forward_return=(target_close / float(frame["close"].iloc[0]) - 1.0) * 100.0,
                return_status="AVAILABLE",
                baseline_engine_version="v0.1",
                challenger_engine_version="v0.2",
                source_commit="seed",
                created_at="2026-07-01T00:00:00+00:00",
            )
        )


def _registry_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_normal_new_trade_day_daily_run(stores):
    result = _run(stores)

    assert result.status == STATUS_SUCCESS
    assert result.ledger["checked"] == 1
    assert result.ledger["saved"] == 1
    assert result.ledger["duplicate"] == 0
    assert result.performance["status"] == STATUS_NO_AVAILABLE_EVIDENCE


def test_b_c_same_trade_date_rerun_is_idempotent_and_duplicate_safe(stores):
    first = _run(stores)
    second = _run(stores)

    assert first.ledger["saved"] == 1
    assert second.status == STATUS_SUCCESS_NO_NEW_EVIDENCE
    assert second.ledger["saved"] == 0
    assert second.ledger["duplicate"] == 1
    assert stores[0].load().shape[0] == 1


@pytest.mark.parametrize(
    "periods,existing_horizons,expected_horizon",
    [(6, [], 5), (11, [5], 10), (21, [5, 10], 20)],
)
def test_d_e_f_incremental_maturity_catch_up(stores, periods, existing_horizons, expected_horizon):
    ledger, forward, _, _ = stores
    frame = _price_df(periods)
    trade_date = frame["date"].iloc[0].strftime("%Y-%m-%d")
    _seed_ledger(ledger, [_ledger_row(trade_date=trade_date, horizon_ready_close=float(frame["close"].iloc[0]))])
    _seed_forward_horizons(forward, frame, trade_date, existing_horizons)

    result = _run(stores, price_df=frame)

    assert result.forward_returns["saved"] == 1
    assert set(forward.load()["horizon"].astype(int)) == set(existing_horizons + [expected_horizon])


def test_g_existing_horizon_duplicate_is_not_saved_again(stores):
    ledger, forward, _, _ = stores
    frame = _price_df(6)
    trade_date = frame["date"].iloc[0].strftime("%Y-%m-%d")
    _seed_ledger(ledger, [_ledger_row(trade_date=trade_date, horizon_ready_close=float(frame["close"].iloc[0]))])

    first = _run(stores, price_df=frame)
    second = _run(stores, price_df=frame)

    assert first.forward_returns["saved"] == 1
    assert second.forward_returns["saved"] == 0
    assert second.forward_returns["duplicate"] >= 1
    assert len(forward.load()) == 1


def test_h_not_available_is_reported_but_not_persisted(stores):
    ledger, forward, _, _ = stores
    frame = _price_df(3)
    trade_date = frame["date"].iloc[0].strftime("%Y-%m-%d")
    _seed_ledger(ledger, [_ledger_row(trade_date=trade_date, horizon_ready_close=float(frame["close"].iloc[0]))])

    result = _run(stores, price_df=frame)

    assert result.forward_returns["not_available"] >= 3
    assert not forward.path.exists()


def test_i_empty_forward_return_yields_no_available_evidence(stores):
    result = _run(stores, price_df=_price_df(3))

    assert result.performance["status"] == STATUS_NO_AVAILABLE_EVIDENCE
    payload = json.loads(stores[2].read_text(encoding="utf-8"))
    assert payload["status"] == STATUS_NO_AVAILABLE_EVIDENCE


def test_j_performance_ok_after_available_evidence(stores):
    ledger, _, summary, _ = stores
    frame = _price_df(6)
    trade_date = frame["date"].iloc[0].strftime("%Y-%m-%d")
    _seed_ledger(ledger, [_ledger_row(trade_date=trade_date, horizon_ready_close=float(frame["close"].iloc[0]))])

    result = _run(stores, price_df=frame)

    assert result.performance["status"] == STATUS_OK
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == STATUS_OK


def test_k_data_not_ready_is_not_failed(stores):
    frame = _price_df(10)
    future_target = "2099-01-01"
    result = _run(stores, price_df=frame, target_trade_date=future_target)

    assert result.status == STATUS_DATA_NOT_READY
    assert result.errors == []
    assert not stores[0].path.exists()


def test_l_malformed_source_fails_before_evidence_write(stores):
    bad = _price_df(10).drop(columns=["close"])
    result = _run(stores, price_df=bad)

    assert result.status == STATUS_FAILED
    assert result.errors[0]["code"] == "MALFORMED_SOURCE"
    assert not stores[0].path.exists()


def test_m_step3_success_then_step4_failure_preserves_ledger(stores, monkeypatch):
    def fail_step4(*args, **kwargs):
        raise RuntimeError("step4 boom")

    monkeypatch.setattr(pipeline, "build_forward_return_records", fail_step4)

    result = _run(stores)

    assert result.status == STATUS_FAILED
    assert result.ledger["saved"] == 1
    assert len(stores[0].load()) == 1


def test_n_failed_rerun_recovers_with_ledger_duplicate(stores, monkeypatch):
    calls = {"count": 0}
    original = pipeline.build_forward_return_records

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "build_forward_return_records", fail_once)

    failed = _run(stores)
    recovered = _run(stores)

    assert failed.status == STATUS_FAILED
    assert recovered.ledger["duplicate"] == 1
    assert recovered.status == STATUS_SUCCESS_NO_NEW_EVIDENCE


def test_o_run_result_schema(stores):
    result = _run(stores).to_dict()

    assert {"trade_date", "status", "ledger", "forward_returns", "performance", "errors"} <= set(result)
    assert {"checked", "saved", "duplicate", "not_evaluable"} <= set(result["ledger"])
    assert {"available", "saved", "duplicate", "not_available"} <= set(result["forward_returns"])


def test_p_run_registry_is_append_only(stores):
    _run(stores)
    _run(stores)

    rows = _registry_rows(stores[3])
    assert len(rows) == 2
    assert rows[0]["run_id"] != rows[1]["run_id"]
    assert all(row["trade_date"] for row in rows)


def test_q_source_commit_recorded_in_result_registry_and_evidence(stores):
    result = _run(stores)

    rows = _registry_rows(stores[3])
    ledger_df = stores[0].load()
    assert result.source_commit
    assert rows[-1]["source_commit"] == result.source_commit
    assert set(ledger_df["source_commit"].astype(str)) == {result.source_commit}


def test_r_s_t_production_outputs_and_dashboard_api_are_not_touched_by_tests(stores):
    result = _run(stores)

    assert result.status in {STATUS_SUCCESS, STATUS_SUCCESS_NO_NEW_EVIDENCE}
    assert stores[0].path.parent != Path("output")
    assert stores[3].name == "dual_shadow_run_registry.jsonl"


def test_u_step2_to_step5_surfaces_still_drive_pipeline(stores, monkeypatch):
    calls: list[str] = []
    original_ledger = pipeline.run_dual_shadow_ledger_build
    original_forward = pipeline.build_forward_return_records
    original_summary = pipeline.build_performance_summary

    def ledger_wrapper(*args, **kwargs):
        calls.append("ledger")
        return original_ledger(*args, **kwargs)

    def forward_wrapper(*args, **kwargs):
        calls.append("forward")
        return original_forward(*args, **kwargs)

    def summary_wrapper(*args, **kwargs):
        calls.append("performance")
        return original_summary(*args, **kwargs)

    monkeypatch.setattr(pipeline, "run_dual_shadow_ledger_build", ledger_wrapper)
    monkeypatch.setattr(pipeline, "build_forward_return_records", forward_wrapper)
    monkeypatch.setattr(pipeline, "build_performance_summary", summary_wrapper)

    _run(stores)

    assert calls == ["ledger", "forward", "performance"]


def test_v_actual_output_files_are_protected_by_injected_paths(stores):
    _run(stores)

    ledger, forward, summary, registry = stores
    assert ledger.path.exists()
    assert summary.exists()
    assert registry.exists()
    assert forward.path.parent == ledger.path.parent

"""tests/test_dual_shadow_step5_performance.py

DUAL Shadow STEP 5 — Baseline vs Challenger 성과 비교 집계 계층 테스트.

Synthetic fixture만 사용하며 실제 output/dual_shadow_signal_ledger.csv,
output/dual_shadow_forward_returns.csv는 절대 건드리지 않는다 (tmp_path 전용 store).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.dual_shadow_forward_returns import DualForwardReturnStore
from src.dual_shadow_ledger import DualShadowLedgerStore
from src.dual_shadow_performance import (
    STATUS_NO_AVAILABLE_EVIDENCE,
    STATUS_OK,
    DuplicateEvidenceError,
    EngineVersionMismatchError,
    InvalidComparisonGroupError,
    MalformedEvidenceError,
    MissingJoinSourceError,
    build_performance_summary,
    validate_forward_return_evidence,
    validate_join_with_ledger,
)

BASELINE_V = "v0.1"
CHALLENGER_V = "v0.2"


def _forward_row(
    trade_date="2026-09-10",
    stock_code="005930",
    stock_name="삼성전자",
    evaluation_close=100.0,
    baseline_signal_present=True,
    challenger_signal_present=True,
    comparison_group="BOTH_YES",
    horizon=5,
    target_date="2026-09-17",
    target_close=105.0,
    forward_return=5.0,
    return_status="AVAILABLE",
    baseline_engine_version=BASELINE_V,
    challenger_engine_version=CHALLENGER_V,
    source_commit="abc123",
    created_at="2026-09-10T00:00:00+00:00",
) -> dict:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "evaluation_close": evaluation_close,
        "baseline_signal_present": baseline_signal_present,
        "challenger_signal_present": challenger_signal_present,
        "comparison_group": comparison_group,
        "horizon": horizon,
        "target_date": target_date,
        "target_close": target_close,
        "forward_return": forward_return,
        "return_status": return_status,
        "baseline_engine_version": baseline_engine_version,
        "challenger_engine_version": challenger_engine_version,
        "source_commit": source_commit,
        "created_at": created_at,
    }


def _ledger_row(
    trade_date="2026-09-10",
    stock_code="005930",
    stock_name="삼성전자",
    evaluation_close=100.0,
    comparison_group="BOTH_YES",
    evaluation_status="OK",
    baseline_engine_version=BASELINE_V,
    challenger_engine_version=CHALLENGER_V,
) -> dict:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "evaluation_close": evaluation_close,
        "baseline_raw_score": 5,
        "baseline_score": 90.0,
        "baseline_signal_type": "BUY",
        "baseline_signal_present": True,
        "challenger_raw_score": 5,
        "challenger_score": 90.0,
        "challenger_signal_type": "BUY",
        "challenger_signal_present": True,
        "challenger_volume_penalty": 0,
        "challenger_pre_return_penalty": 0,
        "challenger_rsi_penalty": 0,
        "challenger_total_penalty": 0,
        "comparison_group": comparison_group,
        "evaluation_status": evaluation_status,
        "baseline_engine_version": baseline_engine_version,
        "challenger_engine_version": challenger_engine_version,
        "source_commit": "abc123",
        "created_at": "2026-09-10T00:00:00+00:00",
    }


def _write_forward_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_ledger_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


@pytest.fixture
def stores(tmp_path):
    forward_path = tmp_path / "dual_shadow_forward_returns.csv"
    ledger_path = tmp_path / "dual_shadow_signal_ledger.csv"
    return DualShadowLedgerStore(path=ledger_path), DualForwardReturnStore(path=forward_path)


# A. Empty Forward Return -> 정상 empty summary
def test_empty_forward_return_yields_no_available_evidence(stores):
    ledger_store, forward_store = stores
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    assert summary.status == STATUS_NO_AVAILABLE_EVIDENCE
    assert summary.total_evidence_rows == 0
    for horizon_data in summary.horizons.values():
        assert horizon_data["baseline"]["signal_count"] == 0
        assert horizon_data["baseline"]["avg_return"] is None
        assert horizon_data["delta"]["avg_return_delta"] is None


# B. Baseline only sample
def test_baseline_only_sample(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BASELINE_ONLY")])
    _write_forward_csv(
        forward_store.path,
        [_forward_row(comparison_group="BASELINE_ONLY", baseline_signal_present=True, challenger_signal_present=False, forward_return=3.0)],
    )
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    h5 = summary.horizons["5"]
    assert h5["baseline"]["signal_count"] == 1
    assert h5["challenger"]["signal_count"] == 0
    assert h5["challenger"]["avg_return"] is None


# C. Challenger only sample
def test_challenger_only_sample(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="CHALLENGER_ONLY")])
    _write_forward_csv(
        forward_store.path,
        [_forward_row(comparison_group="CHALLENGER_ONLY", baseline_signal_present=False, challenger_signal_present=True, forward_return=-2.0)],
    )
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    h5 = summary.horizons["5"]
    assert h5["challenger"]["signal_count"] == 1
    assert h5["baseline"]["signal_count"] == 0
    assert h5["baseline"]["avg_return"] is None


# D. BOTH_YES sample
def test_both_yes_sample_counts_for_both_engines(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BOTH_YES")])
    _write_forward_csv(forward_store.path, [_forward_row(comparison_group="BOTH_YES", forward_return=4.0)])
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    h5 = summary.horizons["5"]
    assert h5["baseline"]["signal_count"] == 1
    assert h5["challenger"]["signal_count"] == 1
    assert h5["baseline"]["avg_return"] == 4.0
    assert h5["challenger"]["avg_return"] == 4.0


# E. BOTH_NO는 엔진 성과 표본에서 제외 (comparison-group count는 보존)
def test_both_no_excluded_from_engine_samples_but_counted(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BOTH_NO")])
    _write_forward_csv(
        forward_store.path,
        [_forward_row(comparison_group="BOTH_NO", baseline_signal_present=False, challenger_signal_present=False, forward_return=1.0)],
    )
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    h5 = summary.horizons["5"]
    assert h5["baseline"]["signal_count"] == 0
    assert h5["challenger"]["signal_count"] == 0
    assert h5["comparison_groups"]["BOTH_NO"]["count"] == 1


# F. NOT_EVALUABLE 제외 (STEP4 store never persists these; validate rejects if present)
def test_not_evaluable_comparison_group_is_invalid(stores):
    ledger_store, forward_store = stores
    df = pd.DataFrame([_forward_row(comparison_group="NOT_EVALUABLE")])
    with pytest.raises(InvalidComparisonGroupError):
        validate_forward_return_evidence(df)


# G/H/I. 5D/10D/20D 집계
def test_all_three_horizons_aggregated_independently(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BOTH_YES")])
    rows = [
        _forward_row(horizon=5, forward_return=1.0),
        _forward_row(horizon=10, forward_return=2.0),
        _forward_row(horizon=20, forward_return=3.0),
    ]
    _write_forward_csv(forward_store.path, rows)
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    assert summary.horizons["5"]["baseline"]["avg_return"] == 1.0
    assert summary.horizons["10"]["baseline"]["avg_return"] == 2.0
    assert summary.horizons["20"]["baseline"]["avg_return"] == 3.0


# J/K/L/M/N. Average/Median/WinRate/Best/Worst/SignalCount 정확성
def test_stats_accuracy(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(
        ledger_store.path,
        [
            _ledger_row(stock_code="005930", comparison_group="BOTH_YES"),
            _ledger_row(stock_code="000660", comparison_group="BOTH_YES"),
            _ledger_row(stock_code="005380", comparison_group="BOTH_YES"),
        ],
    )
    rows = [
        _forward_row(stock_code="005930", forward_return=10.0),
        _forward_row(stock_code="000660", forward_return=-5.0),
        _forward_row(stock_code="005380", forward_return=2.0),
    ]
    _write_forward_csv(forward_store.path, rows)
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    h5 = summary.horizons["5"]["baseline"]
    assert h5["signal_count"] == 3
    assert h5["avg_return"] == pytest.approx((10.0 - 5.0 + 2.0) / 3)
    assert h5["median_return"] == 2.0
    assert h5["win_count"] == 2
    assert h5["loss_count"] == 1
    assert h5["win_rate"] == pytest.approx(2 / 3 * 100.0)
    assert h5["best_return"] == 10.0
    assert h5["worst_return"] == -5.0


# O. Delta 정확성
def test_delta_accuracy(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(
        ledger_store.path,
        [
            _ledger_row(stock_code="005930", comparison_group="BASELINE_ONLY"),
            _ledger_row(stock_code="000660", comparison_group="CHALLENGER_ONLY"),
        ],
    )
    rows = [
        _forward_row(stock_code="005930", comparison_group="BASELINE_ONLY", baseline_signal_present=True, challenger_signal_present=False, forward_return=2.0),
        _forward_row(stock_code="000660", comparison_group="CHALLENGER_ONLY", baseline_signal_present=False, challenger_signal_present=True, forward_return=6.0),
    ]
    _write_forward_csv(forward_store.path, rows)
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    delta = summary.horizons["5"]["delta"]
    assert delta["avg_return_delta"] == pytest.approx(6.0 - 2.0)
    assert delta["signal_count_delta"] == 0


# P. 한쪽 sample=0 -> Delta N/A
def test_delta_is_none_when_one_side_has_zero_samples(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BASELINE_ONLY")])
    _write_forward_csv(
        forward_store.path,
        [_forward_row(comparison_group="BASELINE_ONLY", baseline_signal_present=True, challenger_signal_present=False, forward_return=2.0)],
    )
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    delta = summary.horizons["5"]["delta"]
    assert delta["avg_return_delta"] is None
    assert delta["median_return_delta"] is None
    assert delta["win_rate_delta"] is None
    assert delta["signal_count_delta"] == -1


# Q. duplicate evidence fail-closed
def test_duplicate_evidence_key_fails_closed():
    df = pd.DataFrame([_forward_row(), _forward_row()])
    with pytest.raises(DuplicateEvidenceError):
        validate_forward_return_evidence(df)


# R. malformed evidence fail-closed
def test_malformed_horizon_fails_closed():
    df = pd.DataFrame([_forward_row(horizon=7)])
    with pytest.raises(MalformedEvidenceError):
        validate_forward_return_evidence(df)


def test_missing_required_column_fails_closed():
    df = pd.DataFrame([_forward_row()]).drop(columns=["forward_return"])
    with pytest.raises(MalformedEvidenceError):
        validate_forward_return_evidence(df)


def test_invalid_signal_flag_fails_closed():
    row = _forward_row()
    row["baseline_signal_present"] = "maybe"
    df = pd.DataFrame([row], dtype=object)
    with pytest.raises(MalformedEvidenceError):
        validate_forward_return_evidence(df)


def test_available_but_missing_forward_return_fails_closed():
    df = pd.DataFrame([_forward_row(forward_return=None)])
    with pytest.raises(MalformedEvidenceError):
        validate_forward_return_evidence(df)


# S. engine version mismatch fail-closed
def test_engine_version_mismatch_fails_closed():
    df = pd.DataFrame([_forward_row(baseline_engine_version="v0.9")])
    with pytest.raises(EngineVersionMismatchError):
        validate_forward_return_evidence(df)


def test_missing_join_source_fails_closed():
    forward_df = pd.DataFrame([_forward_row()])
    with pytest.raises(MissingJoinSourceError):
        validate_join_with_ledger(forward_df, pd.DataFrame())


def test_join_comparison_group_mismatch_fails_closed():
    forward_df = pd.DataFrame([_forward_row(comparison_group="BOTH_YES")])
    ledger_df = pd.DataFrame([_ledger_row(comparison_group="BASELINE_ONLY")])
    with pytest.raises(MissingJoinSourceError):
        validate_join_with_ledger(forward_df, ledger_df)


# T. STEP 3/4 input 파일 불변 (store가 load-only로 쓰였는지 확인 — 파일 내용 변경 없음)
def test_step5_never_writes_to_ledger_or_forward_return_files(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BOTH_YES")])
    _write_forward_csv(forward_store.path, [_forward_row(comparison_group="BOTH_YES")])
    ledger_before = ledger_store.path.read_bytes()
    forward_before = forward_store.path.read_bytes()
    build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    assert ledger_store.path.read_bytes() == ledger_before
    assert forward_store.path.read_bytes() == forward_before


def test_win_definition_documented_in_summary(stores):
    ledger_store, forward_store = stores
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    assert summary.win_definition == "forward_return > 0"


def test_status_ok_when_evidence_present(stores):
    ledger_store, forward_store = stores
    _write_ledger_csv(ledger_store.path, [_ledger_row(comparison_group="BOTH_YES")])
    _write_forward_csv(forward_store.path, [_forward_row(comparison_group="BOTH_YES")])
    summary = build_performance_summary(ledger_store=ledger_store, forward_store=forward_store)
    assert summary.status == STATUS_OK
    assert summary.total_evidence_rows == 1

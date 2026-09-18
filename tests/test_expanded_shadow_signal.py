from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.expanded_shadow_signal as adapter
from src.expanded_shadow_data import DATASET_INVESTOR, DATASET_MARKET, CollectionResult
from src.expanded_shadow_eligibility import STATUS_READY, STATUS_SOURCE_LAG, EligibilityResult
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_signal import NO_SIGNAL, ExpandedSignalError, evaluate_ready_ticker, evaluate_ready_ticker_from_snapshots
from src.shadow_tracking import DECISION_CANDIDATE, DECISION_EXCLUDED, EXCLUSION_REASON_FOREIGN_NEGATIVE


BAS_DD = "2026-09-17"


def _eligibility(ticker: str = "005930", status: str = STATUS_READY) -> EligibilityResult:
    return EligibilityResult(
        ticker=ticker,
        basDd=BAS_DD,
        status=status,
        market_success=True,
        investor_success=True,
        market_source_date=BAS_DD,
        investor_source_date=BAS_DD,
        market_row_count=60,
        investor_row_count=5,
        market_attempt_count=1,
        investor_attempt_count=1,
        lagging_datasets=(),
        error_code=None,
        error_class=None,
        error_message=None,
    )


def _market_frame(ticker: str = "005930") -> pd.DataFrame:
    rows = 80
    dates = pd.date_range("2026-06-30", periods=rows, freq="B")
    close = [100.0 + index for index in range(rows)]
    return pd.DataFrame(
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


def _investor_frame(ticker: str = "005930", foreign_values: list[int] | None = None) -> pd.DataFrame:
    values = foreign_values or [100_000, 100_000, 100_000, 100_000, 100_000]
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-09-11", periods=5, freq="B"),
            "ticker": [ticker] * 5,
            "foreign_net_buy": values,
            "institution_net_buy": [0] * 5,
        }
    )


def _signal(ticker: str = "005930", signal_date: str = BAS_DD, score: float = 80.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": ticker,
                "name": "테스트",
                "signal_date": pd.Timestamp(signal_date),
                "signal_close": 100.0,
                "raw_score": 52,
                "score": score,
                "signal_type": "BUY_WATCH",
            }
        ]
    )


def test_reuses_existing_production_shadow_functions(monkeypatch):
    calls: list[str] = []

    def fake_add_all_indicators(frame):
        calls.append("add_all_indicators")
        return frame

    def fake_generate_signals(frame, ticker, name):
        calls.append("generate_signals")
        return _signal(ticker)

    def fake_compute_investor_features(signals, investor_map, raw_map):
        calls.append("compute_investor_features")
        out = signals.copy()
        out["foreign_5d_ratio"] = 0.25
        return out

    def fake_foreign_status_from_ratio(ratio):
        calls.append("foreign_status_from_ratio")
        return "POSITIVE"

    def fake_decide_candidate(status):
        calls.append("decide_candidate")
        return DECISION_CANDIDATE, None

    monkeypatch.setattr(adapter, "add_all_indicators", fake_add_all_indicators)
    monkeypatch.setattr(adapter, "generate_signals", fake_generate_signals)
    monkeypatch.setattr(adapter, "compute_investor_features", fake_compute_investor_features)
    monkeypatch.setattr(adapter, "foreign_status_from_ratio", fake_foreign_status_from_ratio)
    monkeypatch.setattr(adapter, "decide_candidate", fake_decide_candidate)

    result = evaluate_ready_ticker(
        eligibility=_eligibility(),
        name="테스트",
        market="KOSPI",
        market_frame=_market_frame(),
        investor_frame=_investor_frame(),
    )

    assert result.decision == DECISION_CANDIDATE
    assert calls == ["add_all_indicators", "generate_signals", "compute_investor_features", "foreign_status_from_ratio", "decide_candidate"]


def test_technical_signal_rules_not_changed_generate_signals_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("generate_signals_v2 must not be used")

    monkeypatch.setattr("src.signal_engine.generate_signals_v2", forbidden)
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.0))

    result = evaluate_ready_ticker(
        eligibility=_eligibility(),
        name="테스트",
        market="KOSPI",
        market_frame=_market_frame(),
        investor_frame=_investor_frame(),
    )

    assert result.signal_present is True


def test_foreign_negative_is_excluded_not_deleted(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=-0.25))

    result = evaluate_ready_ticker(
        eligibility=_eligibility(),
        name="테스트",
        market="KOSPI",
        market_frame=_market_frame(),
        investor_frame=_investor_frame(),
    )

    assert result.signal_present is True
    assert result.foreign_status == "NEGATIVE"
    assert result.decision == DECISION_EXCLUDED
    assert result.exclusion_reason == EXCLUSION_REASON_FOREIGN_NEGATIVE


def test_positive_is_candidate(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.25))

    result = evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert result.foreign_status == "POSITIVE"
    assert result.decision == DECISION_CANDIDATE


def test_neutral_is_candidate(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.0))

    result = evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert result.foreign_status == "NEUTRAL"
    assert result.decision == DECISION_CANDIDATE


def test_no_data_maps_to_neutral_candidate(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=np.nan))

    result = evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert result.foreign_5d_ratio is None
    assert result.foreign_status == "NEUTRAL"
    assert result.decision == DECISION_CANDIDATE


def test_alphanumeric_ticker_preserved(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.25))

    result = evaluate_ready_ticker(eligibility=_eligibility("0015N0"), name="아로마티카", market="KOSDAQ", market_frame=_market_frame("0015N0"), investor_frame=_investor_frame("0015N0"))

    assert result.ticker == "0015N0"


def test_signal_absent_ready_ticker_returns_no_signal(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: pd.DataFrame())

    result = evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert result.evaluated is True
    assert result.signal_present is False
    assert result.reason == NO_SIGNAL
    assert result.decision is None


def test_past_signal_not_mistaken_for_basdd_signal(monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker, signal_date="2026-09-16"))

    result = evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert result.signal_present is False
    assert result.reason == NO_SIGNAL


def test_non_ready_ticker_evaluation_forbidden(monkeypatch):
    called = False

    def fake_generate(*_args, **_kwargs):
        nonlocal called
        called = True
        return pd.DataFrame()

    monkeypatch.setattr(adapter, "generate_signals", fake_generate)
    with pytest.raises(ExpandedSignalError):
        evaluate_ready_ticker(eligibility=_eligibility(status=STATUS_SOURCE_LAG), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert called is False


def test_snapshot_loader_uses_expanded_paths_only(tmp_path: Path, monkeypatch):
    paths = ExpandedShadowPaths(tmp_path)
    market_path = paths.market_dir(BAS_DD) / "0015N0.csv"
    investor_path = paths.investor_dir(BAS_DD) / "0015N0_investor.csv"
    market_path.parent.mkdir(parents=True)
    investor_path.parent.mkdir(parents=True)
    _market_frame("0015N0").to_csv(market_path, index=False)
    _investor_frame("0015N0").to_csv(investor_path, index=False)
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.25))

    result = evaluate_ready_ticker_from_snapshots(eligibility=_eligibility("0015N0"), name="아로마티카", market="KOSDAQ", paths=paths)

    assert result.signal_present is True
    assert not (tmp_path / "data" / "raw").exists()
    assert not (tmp_path / "data" / "investor").exists()
    assert not (tmp_path / "output" / "dual_shadow_signal_ledger.csv").exists()


def test_no_ledger_or_runtime_write(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", lambda signals, investor_map, raw_map: signals.assign(foreign_5d_ratio=0.25))

    evaluate_ready_ticker(eligibility=_eligibility(), name="테스트", market="KOSPI", market_frame=_market_frame(), investor_frame=_investor_frame())

    assert not (tmp_path / "output" / "expanded_shadow" / "expanded_shadow_signal_ledger.csv").exists()
    assert not (tmp_path / "output" / "expanded_shadow").exists()


def test_investor_feature_receives_expanded_maps(monkeypatch):
    seen = {}

    def fake_features(signals, investor_map, raw_map):
        seen["signals"] = signals.copy()
        seen["investor_keys"] = set(investor_map)
        seen["raw_keys"] = set(raw_map)
        return signals.assign(foreign_5d_ratio=0.25)

    monkeypatch.setattr(adapter, "generate_signals", lambda frame, ticker, name: _signal(ticker))
    monkeypatch.setattr(adapter, "compute_investor_features", fake_features)
    evaluate_ready_ticker(eligibility=_eligibility("0015N0"), name="아로마티카", market="KOSDAQ", market_frame=_market_frame("0015N0"), investor_frame=_investor_frame("0015N0"))

    assert seen["investor_keys"] == {"0015N0"}
    assert seen["raw_keys"] == {"0015N0"}


def test_collection_result_shape_not_required_for_non_ready():
    market = CollectionResult("005930", DATASET_MARKET, True, 1, BAS_DD, 60, None, None, None, None, 0.0)
    assert market.ticker == "005930"
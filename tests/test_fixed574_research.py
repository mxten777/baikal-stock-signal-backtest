from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.indicators import add_all_indicators
from src.research import pipeline
from src.research.panel import (
    HORIZONS, build_ticker_panel, cooldown_flags, crossing_flags, normalize_history,
)
from src.research.report import bootstrap_report, comparison_table, metrics, overlap_report, strategy_events
from src.signal_engine import compute_raw_score_v2, is_overheated, raw_to_score, score_momentum, score_trend, score_volume


def history(rows: int = 160, start: str = "2024-01-02") -> pd.DataFrame:
    x = np.arange(rows)
    close = 100 + 0.2 * x + 8 * np.sin(x / 6)
    volume = 1000 + x % 7 * 10
    volume[x % 17 == 0] *= 2
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=rows),
        "open": close, "high": close + 2, "low": close - 2, "close": close, "volume": volume,
    })


def build(frame: pd.DataFrame, calendar: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    return build_ticker_panel(
        frame, pd.DatetimeIndex(frame["date"]) if calendar is None else calendar,
        ticker="0007C0", market="KOSPI",
    )


def feature_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column for column in frame.columns
        if not column.startswith(("target_", "future_", "split_purged_", "evaluation_ready_"))
    ]


def test_future_changes_cannot_change_features_or_signals():
    source = history()
    before = build(source)
    changed = source.copy()
    changed.loc[110:, ["open", "high", "low", "close"]] *= 3
    changed.loc[110:, "volume"] *= 20
    after = build(changed)
    pd.testing.assert_frame_equal(before.loc[:109, feature_columns(before)], after.loc[:109, feature_columns(after)])
    assert before.loc[109, "future_return_5d"] != after.loc[109, "future_return_5d"]


@pytest.mark.parametrize("length", [59, 60, 61, 95, 130])
def test_truncated_history_matches_full_features(length):
    source = history()
    full = build(source)
    truncated = build(source.iloc[:length])
    pd.testing.assert_frame_equal(
        full.loc[:length - 1, feature_columns(full)], truncated[feature_columns(truncated)],
    )


def test_reuses_exact_existing_components_and_indicators():
    source = history()
    expected = add_all_indicators(source)
    panel = build(source)
    for column in ("ma5", "ma20", "ma60", "volume_ma20", "rsi", "macd", "macd_signal", "return_5d_pct"):
        pd.testing.assert_series_equal(panel[column], expected[column], check_names=False)
    for index in panel.index[panel["feature_ready"]]:
        row = panel.loc[index]
        assert row["trend_raw"] == score_trend(row, row["prev_ma20"])
        assert row["volume_raw"] == score_volume(row)
        assert row["momentum_raw"] == score_momentum(row, row["prev_macd_histogram"])
        assert row["score_A"] == raw_to_score(compute_raw_score_v2(row, row["prev_ma20"], row["prev_macd_histogram"]))
        assert row["score_B"] == round(row["momentum_raw"] / 20 * 100, 1)
        assert row["score_C"] == round((row["momentum_raw"] + row["volume_raw"]) / 40 * 100, 1)
        assert row["overheated"] == is_overheated(row)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_target_uses_calendar_sessions_and_never_delays_exit(horizon):
    source = history(100)
    calendar = pd.DatetimeIndex(source["date"])
    missing = source.drop(index=75).reset_index(drop=True)
    panel = build(missing, calendar)
    origin = 75 - horizon
    assert panel.loc[origin, f"target_date_{horizon}d"] == calendar[75]
    assert panel.loc[origin, f"target_status_{horizon}d"] == "MISSING_PATH"
    assert pd.isna(panel.loc[origin, f"future_return_{horizon}d"])
    intact = build(source)
    expected = (source.loc[60 + horizon, "close"] / source.loc[60, "close"] - 1) * 100
    assert intact.loc[60, f"future_return_{horizon}d"] == pytest.approx(expected)
    assert intact.loc[60, f"evaluation_ready_{horizon}d"]
    assert not intact.loc[60, f"split_purged_{horizon}d"]
    assert intact.iloc[-horizon:][f"target_status_{horizon}d"].eq("IMMATURE").all()
    assert not intact.iloc[-horizon:][f"split_purged_{horizon}d"].any()


def test_gap_restarts_warmup_and_ewm_without_filling():
    source = history(160)
    source.loc[80, "volume"] = 0
    panel = build(source)
    assert panel.loc[79, "feature_ready"]
    assert panel.loc[80, "gap_reason"] == "ZERO_VOLUME"
    assert panel.loc[81, "valid_history_count"] == 1
    assert not panel.loc[139, "feature_ready"]
    assert panel.loc[140, "feature_ready"]
    for strategy in ("A", "B", "C"):
        assert not panel.loc[140, f"signal_{strategy}"]
    restarted = add_all_indicators(source.loc[81:].reset_index(drop=True))
    assert panel.loc[140, "rsi"] == restarted.loc[59, "rsi"]
    assert panel.loc[140, "macd"] == restarted.loc[59, "macd"]


def test_warmup_and_crossing_never_invents_first_or_post_gap_event():
    panel = build(history())
    assert not panel.loc[:58, "feature_ready"].any()
    assert panel.loc[59, "feature_ready"]
    for strategy in ("A", "B", "C"):
        assert not panel.loc[:59, f"signal_{strategy}"].any()
    score = pd.Series([80.0, 80, 70, 75, 80, 80, 80, 70, 76])
    ready = pd.Series([True, True, True, True, True, False, True, True, True])
    assert crossing_flags(score, ready).tolist() == [False, False, False, True, False, False, False, False, True]


def test_overheated_does_not_reset_crossing_state():
    score = pd.Series([70.0, 80, 80, 70, 80])
    heated = pd.Series([False, True, False, False, False])
    flags = crossing_flags(score, pd.Series(True, index=score.index))
    assert (flags & ~heated).tolist() == [False, False, False, False, True]


def test_cooldown_suppresses_next_twenty_sessions():
    signals = pd.Series(False, index=range(50))
    signals.loc[[0, 1, 20, 21, 40, 42]] = True
    assert cooldown_flags(signals)[lambda values: values].index.tolist() == [0, 21, 42]


def test_year_boundary_targets_purged_without_resetting_features():
    panel = build(history(100, "2024-10-01"))
    origin = panel.index[panel["trading_date"].eq(pd.Timestamp("2024-12-31"))][0]
    assert panel.loc[origin, "feature_ready"]
    for horizon in HORIZONS:
        assert panel.loc[origin, f"target_status_{horizon}d"] == "AVAILABLE"
        assert panel.loc[origin, f"split_purged_{horizon}d"]
        assert not panel.loc[origin, f"evaluation_ready_{horizon}d"]
    assert panel.loc[origin + 1, "valid_history_count"] == origin + 2


@pytest.mark.parametrize("kind", ["duplicate", "future", "early", "missing_column"])
def test_normalization_rejects_invalid_input_contract(kind):
    source = history(10)
    if kind == "duplicate":
        source.loc[1, "date"] = source.loc[0, "date"]
    elif kind == "future":
        source.loc[1, "date"] = pd.Timestamp("2026-10-08")
    elif kind == "early":
        source.loc[1, "date"] = pd.Timestamp("2023-12-29")
    else:
        source = source.drop(columns="close")
    with pytest.raises(ValueError):
        normalize_history(source, "2026-10-07")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 0.0])
def test_invalid_price_is_not_filled_or_scored(value):
    source = history(100)
    source.loc[75, "close"] = value
    panel = build(source)
    assert not panel.loc[75, "feature_ready"]
    assert panel.loc[70, "target_status_5d"] == "INVALID_PRICE"
    assert pd.isna(panel.loc[70, "future_return_5d"])


def test_date_weighted_metrics_not_signal_weighted_and_zero_is_not_win():
    rows = pd.DataFrame({
        "trading_date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"]),
        "future_return_5d": [0.0, 20.0, -5.0],
        "evaluation_ready_5d": [True] * 3,
        "target_status_5d": ["AVAILABLE"] * 3, "split_purged_5d": [False] * 3,
    })
    result = metrics(rows, 5)
    assert result["mean_return_pct"] == 5
    assert result["date_weighted_mean_return_pct"] == 2.5
    assert result["win_rate_pct"] == pytest.approx(100 / 3)
    assert result["date_weighted_win_rate_pct"] == 25


def test_ranking_equal_counts_ties_and_targets_do_not_affect_selection():
    panels = []
    for index in range(11):
        panel = build(history(100))
        panel["ticker"] = f"{index:06d}"
        for strategy in ("A", "B", "C"):
            panel[f"score_{strategy}"] = 50.0
        panels.append(panel)
    panel = pd.concat(panels, ignore_index=True)
    events = strategy_events(panel)
    ranking = events.loc[events["cohort"].eq("top10_daily")]
    assert ranking.groupby(["strategy", "trading_date"]).size().eq(2).all()
    assert set(ranking["ticker"]) == {"000000", "000001"}
    changed = panel.copy()
    for horizon in HORIZONS:
        changed[f"evaluation_ready_{horizon}d"] = False
        changed[f"future_return_{horizon}d"] = np.nan
    after = strategy_events(changed)
    columns = ["ticker", "trading_date", "strategy", "cohort", "score"]
    pd.testing.assert_frame_equal(
        events.loc[events["cohort"].isin(["top10_daily", "bottom10_daily"]), columns].reset_index(drop=True),
        after.loc[after["cohort"].isin(["top10_daily", "bottom10_daily"]), columns].reset_index(drop=True),
    )


def test_overlap_counts_same_key_across_strategies_without_deduplication():
    panel = build(history(100))
    for strategy in ("A", "B", "C"):
        panel[f"signal_{strategy}"] = False
    panel.loc[70, ["signal_A", "signal_B", "signal_C"]] = True
    panel.loc[71, ["signal_A", "signal_C"]] = True
    panel["overheated"] = False
    overlap = overlap_report(panel)["non_overheated"]
    assert overlap["exclusive_patterns_ABC"]["111"] == 1
    assert overlap["exclusive_patterns_ABC"]["101"] == 1
    assert overlap["pairs"]["A_C"]["intersection"] == 2
    assert overlap["pairs"]["A_B"]["jaccard"] == 0.5


def test_bootstrap_is_deterministic_and_uses_paired_date_blocks():
    panel = build(history(160))
    for strategy in ("A", "B", "C"):
        panel[f"signal_{strategy}"] = panel["feature_ready"] & panel["session_index"].mod(5).eq(0)
    events = strategy_events(panel)
    calendar = pd.DatetimeIndex(panel["trading_date"])
    first = bootstrap_report(events, calendar, repeats=100)
    assert first == bootstrap_report(events, calendar, repeats=100)
    assert all(row["lower_95"] == 0 and row["upper_95"] == 0 for row in first["paired_differences"])


def test_no_signals_and_empty_months_are_explicit():
    panel = build(history(40))
    events = strategy_events(panel)
    table = comparison_table(events, panel)
    rows = table.loc[table["cohort"].eq("crossing")]
    assert not rows.empty
    assert rows["signal_count"].eq(0).all()
    assert rows["mean_return_pct"].isna().all()


def test_write_boundary_and_deterministic_payload_guard(tmp_path):
    with pytest.raises(ValueError, match="direct child"):
        pipeline.write_research_file(tmp_path, "..\\unsafe.csv", b"x")
    pipeline.write_research_file(tmp_path, "test.json", b"identical")
    pipeline.write_research_file(tmp_path, "test.json", b"identical")
    with pytest.raises(RuntimeError, match="Non-deterministic"):
        pipeline.write_research_file(tmp_path, "test.json", b"changed")


def test_operational_inventory_detects_modification_and_new_files(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    ledger = output / "ledger.csv"
    ledger.write_text("original", encoding="utf-8")
    original = pipeline.operational_inventory(tmp_path)
    pipeline.write_research_file(tmp_path, "test.json", b"research")
    pipeline.verify_inventory(tmp_path, original)
    ledger.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Operational files changed"):
        pipeline.verify_inventory(tmp_path, original)


def test_end_to_end_offline_frozen_deterministic_and_operationally_immutable(tmp_path, monkeypatch):
    universe_dir = tmp_path / "data" / "expanded_shadow" / "universe"
    universe_dir.mkdir(parents=True)
    universe_file = universe_dir / "expanded_universe_574.csv"
    universe_file.write_text("ticker,name,market,source_basDd\n0007C0,fixture,KOSPI,2026-09-17\n", encoding="utf-8")
    stock = SimpleNamespace(ticker="0007C0", name="fixture", market="KOSPI")
    fake = SimpleNamespace(
        tickers=(stock,), basDd="2026-09-17", row_count=1,
        market_counts={"KOSPI": 1, "KOSDAQ": 0}, sha256=hashlib.sha256(universe_file.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(pipeline, "load_expanded_universe", lambda *args: fake)
    market_dir = tmp_path / "data" / "expanded_shadow" / "market" / pipeline.SNAPSHOT
    market_dir.mkdir(parents=True)
    source = market_dir / "0007C0.csv"
    history(100).to_csv(source, index=False)
    (tmp_path / "output").mkdir()
    ledger = tmp_path / "output" / "forward_validation.csv"
    ledger.write_text("do not change", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Network access forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    protected = pipeline.operational_inventory(tmp_path)
    first = pipeline.run_research(tmp_path, bootstrap_repeats=20)
    output = tmp_path / pipeline.OUTPUT_RELATIVE
    hashes = {path.name: pipeline.file_hash(path) for path in output.iterdir()}
    second = pipeline.run_research(tmp_path, bootstrap_repeats=20)
    assert first == second
    assert hashes == {path.name: pipeline.file_hash(path) for path in output.iterdir()}
    pipeline.verify_inventory(tmp_path, protected)
    frozen = json.loads((output / "input_freeze_manifest.json").read_text())
    assert frozen["input_files"][0]["sha256"] == pipeline.file_hash(source)
    assert frozen["price_adjustment_status"] == "UNVERIFIED"
    assert (output / "panel.csv.gz").exists()
    source.write_text(source.read_text().replace("100.0", "101.0"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="Non-deterministic"):
        pipeline.run_research(tmp_path, bootstrap_repeats=20)

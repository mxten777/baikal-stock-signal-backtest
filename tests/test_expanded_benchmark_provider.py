from __future__ import annotations

import json
import math
import multiprocessing
import os
from pathlib import Path
import time

import pandas as pd
import pytest

import src.expanded_benchmark_provider as benchmark_provider
import scripts.expanded_candidate_performance as cli
import scripts.expanded_daily_orchestrator as orchestrator
from src.expanded_benchmark_provider import (
    STATUS_AFTER_CUTOFF,
    STATUS_CALCULATED,
    STATUS_DATE_MISMATCH,
    STATUS_INVALID_SOURCE,
    STATUS_MISSING_END,
    STATUS_MISSING_START,
    STATUS_STALE_SOURCE,
    ExpandedBenchmark,
    ExpandedBenchmarkError,
    ExpandedBenchmarkTimeoutError,
    _naver_fetch_worker,
    _run_isolated_worker,
    compute_benchmark_return_for_dates,
    load_expanded_benchmark,
    stock_endpoint_dates,
    validate_benchmark_frame,
)
from src.expanded_candidate_performance import ExpandedCandidatePerformanceStore
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.shadow_tracking import compute_forward_returns


SIGNAL_DATE = "2026-09-17"
NOW = "2026-10-03T00:00:00+00:00"
DATES = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(SIGNAL_DATE, periods=11)]
END_5D = DATES[5]


def _child_write_payload(result_path: str, payload: dict[str, str]) -> None:
    Path(result_path).write_text(json.dumps(payload), encoding="utf-8")


def _child_raise(result_path: str) -> None:
    raise RuntimeError("child reader failed")


def _child_block(result_path: str, pid_path: str) -> None:
    Path(pid_path).write_text(str(os.getpid()), encoding="ascii")
    time.sleep(30)


def _signal_row(ticker: str = "000001", market: str = "KOSPI") -> dict[str, object]:
    return {
        "basDd": SIGNAL_DATE, "stock_code": ticker, "stock_name": f"Stock {ticker}", "market": market,
        "signal_date": SIGNAL_DATE, "signal_price": 100.0, "signal_score": 80.0, "foreign_status": "POSITIVE",
        "decision": "CANDIDATE", "engine_version": "v0.1", "source_commit": "deadbeef", "run_id": "run-1",
        "created_at": "2026-09-17T12:00:00+00:00",
    }


def _prices(dates: list[str] = DATES[:6], step: float = 2.0) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "close": [100.0 + step * index for index in range(len(dates))]})


def _index_frame(dates: list[str] = DATES[:6], closes: list[float] | None = None) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "close": closes or [200.0 + 2.0 * index for index in range(len(dates))]})


def _benchmark(frame: pd.DataFrame, symbol: str = "KS11", cutoff: str | None = END_5D) -> ExpandedBenchmark:
    return validate_benchmark_frame(frame, symbol=symbol, source=f"NAVER:{symbol}", cutoff_date=cutoff)


def _naver_raw(dates: list[str], closes: list[float]) -> pd.DataFrame:
    frame = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1, "Change": 0.0},
                         index=pd.DatetimeIndex(pd.to_datetime(dates), name="Date"))
    return frame


def _store(tmp_path: Path) -> ExpandedCandidatePerformanceStore:
    return ExpandedCandidatePerformanceStore(ExpandedShadowPaths(tmp_path))


def _sync(store, signals, prices, benchmarks):
    return store.synchronize(signals, price_map=prices, benchmark_map=benchmarks, now_func=lambda: NOW)


def _only_row(store) -> pd.Series:
    return store.load().iloc[0]


def _warnings_for(signals, benchmarks, stats, *, errors=None, cutoff=END_5D):
    diagnostics = orchestrator._benchmark_source_diagnostics(
        signals,
        cutoff,
        cli.PROVIDER_NAVER,
        benchmarks,
        errors or {},
    )
    return orchestrator._benchmark_warning_details(diagnostics, stats)


def test_spawn_worker_returns_small_serializable_payload():
    payload = {"status": "success", "close_date": END_5D}

    assert _run_isolated_worker(
        _child_write_payload,
        (payload,),
        timeout_seconds=10.0,
    ) == payload
    assert multiprocessing.active_children() == []


def test_spawn_worker_exception_becomes_provider_error():
    with pytest.raises(ExpandedBenchmarkError, match="worker exited with code"):
        _run_isolated_worker(_child_raise, (), timeout_seconds=10.0)
    assert multiprocessing.active_children() == []


def test_naver_worker_serializes_child_exception(monkeypatch):
    payload = _run_isolated_worker(
        _naver_fetch_worker,
        ("UNSUPPORTED", "2026-09-01", END_5D),
        timeout_seconds=10.0,
    )
    assert payload["status"] == "error"
    assert payload["error_type"] == "KeyError"

    monkeypatch.setattr(benchmark_provider, "_run_isolated_worker", lambda *_args, **_kwargs: payload)
    with pytest.raises(ExpandedBenchmarkError, match="Naver benchmark worker KeyError"):
        benchmark_provider.load_expanded_benchmark("KS11", "2026-09-01", END_5D)


def test_spawn_worker_hard_timeout_reaps_child(tmp_path: Path):
    pid_path = tmp_path / "child.pid"
    started = time.perf_counter()

    with pytest.raises(ExpandedBenchmarkTimeoutError, match="hard timeout"):
        _run_isolated_worker(_child_block, (str(pid_path),), timeout_seconds=3.0)

    elapsed = time.perf_counter() - started
    assert 3.0 <= elapsed < 8.0
    assert pid_path.is_file()
    assert multiprocessing.active_children() == []


@pytest.mark.parametrize("symbol, market, source", [("KS11", "KOSPI", "NAVER:KOSPI"), ("KQ11", "KOSDAQ", "NAVER:KOSDAQ")])
def test_normal_naver_index_fills_benchmark_and_excess(tmp_path: Path, symbol: str, market: str, source: str):
    calls = []

    def reader(name, start, end):
        calls.append((name, start, end))
        return _naver_raw(DATES[:6], [200.0, 201.0, 202.0, 203.0, 204.0, 210.0])

    benchmark = load_expanded_benchmark(symbol, "2026-09-01", END_5D, reader=reader)
    store = _store(tmp_path)
    stats = _sync(store, pd.DataFrame([_signal_row(market=market)]), {"000001": _prices()}, {symbol: benchmark})

    row = _only_row(store)
    assert calls == [(source, "2026-09-01", END_5D)]
    assert benchmark.source == source and benchmark.latest_valid_close_date == END_5D
    assert row["return_5d"] == pytest.approx(10.0)
    assert row["benchmark_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(5.0)
    assert stats["benchmark_calculated"] == 1


def test_stale_source_keeps_benchmark_na(tmp_path: Path):
    benchmark = _benchmark(_index_frame(DATES[:5]))
    store = _store(tmp_path)
    stats = _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {"KS11": benchmark})

    row = _only_row(store)
    assert benchmark.diagnostics()["stale"] is True
    assert row["return_5d"] == pytest.approx(10.0)
    assert pd.isna(row["benchmark_5d"]) and pd.isna(row["excess_5d"])
    assert stats["benchmark_stale_source"] == 1


def test_missing_start_date():
    outcome = compute_benchmark_return_for_dates(_benchmark(_index_frame(DATES[1:6])), SIGNAL_DATE, END_5D, 5)
    assert outcome.status == STATUS_MISSING_START and outcome.value is None


def test_missing_end_date_is_not_shifted():
    frame = _index_frame([day for day in DATES[:8] if day != END_5D])
    outcome = compute_benchmark_return_for_dates(_benchmark(frame, cutoff=DATES[7]), SIGNAL_DATE, END_5D, 5)
    assert outcome.status == STATUS_MISSING_END and outcome.value is None


def test_null_close_is_not_forward_filled():
    benchmark = _benchmark(_index_frame(closes=[200, 201, 202, 203, 204, float("nan")]))
    outcome = compute_benchmark_return_for_dates(benchmark, SIGNAL_DATE, END_5D, 5)
    assert benchmark.invalid_close_dates == (END_5D,)
    assert benchmark.latest_valid_close_date == DATES[4]
    assert outcome.value is None and outcome.status in {STATUS_MISSING_END, STATUS_STALE_SOURCE}


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf])
def test_non_positive_or_nonfinite_close_is_rejected(bad: float):
    benchmark = _benchmark(_index_frame(closes=[bad, 201, 202, 203, 204, 205]))
    outcome = compute_benchmark_return_for_dates(benchmark, SIGNAL_DATE, END_5D, 5)
    assert outcome.status == STATUS_MISSING_START and outcome.value is None


def test_duplicate_date_invalidates_source(tmp_path: Path):
    frame = pd.concat([_index_frame(), _index_frame(DATES[2:3], [999.0])], ignore_index=True)
    benchmark = _benchmark(frame)
    store = _store(tmp_path)
    stats = _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {"KS11": benchmark})

    assert benchmark.duplicate_dates == (DATES[2],) and not benchmark.is_valid
    assert pd.isna(_only_row(store)["benchmark_5d"])
    assert stats["benchmark_invalid_source"] == 3
    assert stats["missing_benchmark"] == 1


def test_rows_after_cutoff_are_never_used():
    benchmark = _benchmark(_index_frame(DATES[:8]), cutoff=DATES[4])
    assert benchmark.after_cutoff_rows == 3
    assert max(benchmark.dates) == DATES[4]
    outcome = compute_benchmark_return_for_dates(benchmark, SIGNAL_DATE, END_5D, 5)
    assert outcome.status == STATUS_AFTER_CUTOFF and outcome.value is None


def test_start_end_trading_day_mismatch_is_not_stored(tmp_path: Path):
    suspended = [day for day in DATES[:7] if day != DATES[3]]
    store = _store(tmp_path)
    stats = _sync(
        store,
        pd.DataFrame([_signal_row()]),
        {"000001": _prices(suspended)},
        {"KS11": _benchmark(_index_frame(DATES[:7]), cutoff=DATES[6])},
    )

    row = _only_row(store)
    assert stock_endpoint_dates(_prices(suspended), SIGNAL_DATE, 5) == (SIGNAL_DATE, DATES[6])
    assert row["return_5d"] == pytest.approx(10.0)
    assert pd.isna(row["benchmark_5d"]) and pd.isna(row["excess_5d"])
    assert stats["benchmark_date_mismatch"] == 1


@pytest.mark.parametrize(
    "scenario, expected_code, expected_status",
    [
        ("stale", "STALE_SOURCE", "STALE_SOURCE"),
        ("duplicate", "DUPLICATE_DATES", "INVALID_SOURCE"),
        ("invalid_close", "INVALID_CLOSE", "STALE_SOURCE"),
        ("missing_start", "MATURED_RETURN_BENCHMARK_UNAVAILABLE", "MISSING_START"),
        ("missing_end", "MATURED_RETURN_BENCHMARK_UNAVAILABLE", "MISSING_END"),
        ("date_mismatch", "MATURED_RETURN_BENCHMARK_UNAVAILABLE", "DATE_MISMATCH"),
    ],
)
def test_matured_return_survives_benchmark_gaps_with_warning(
    tmp_path: Path, scenario: str, expected_code: str, expected_status: str,
):
    prices = _prices()
    if scenario == "stale":
        benchmark = _benchmark(_index_frame(DATES[:5]))
    elif scenario == "duplicate":
        duplicate = pd.concat([_index_frame(), _index_frame(DATES[2:3], [999.0])], ignore_index=True)
        benchmark = _benchmark(duplicate)
    elif scenario == "invalid_close":
        benchmark = _benchmark(_index_frame(closes=[200, 201, 202, 203, 204, float("nan")]))
    elif scenario == "missing_start":
        benchmark = _benchmark(_index_frame(DATES[1:6]))
    elif scenario == "missing_end":
        frame = _index_frame(DATES[:8])
        frame = frame.loc[frame["date"] != END_5D]
        benchmark = _benchmark(frame, cutoff=DATES[7])
    else:
        suspended = [day for day in DATES[:7] if day != DATES[3]]
        prices = pd.DataFrame({"date": suspended, "close": [100, 102, 104, 106, 108, 110]})
        benchmark = _benchmark(_index_frame(DATES[:7]), cutoff=DATES[6])

    signals = pd.DataFrame([_signal_row()])
    store = _store(tmp_path)
    stats = _sync(store, signals, {"000001": prices}, {"KS11": benchmark})
    warnings = _warnings_for(signals, {"KS11": benchmark}, stats, cutoff=benchmark.cutoff_date or END_5D)

    row = _only_row(store)
    assert row["return_5d"] == pytest.approx(10.0)
    assert pd.isna(row["benchmark_5d"]) and pd.isna(row["excess_5d"])
    assert stats["benchmark_5d_matured_return_" + expected_status.lower()] == 1
    assert any(warning["code"] == expected_code for warning in warnings)


def test_open_horizon_does_not_warn_for_not_due_endpoint(tmp_path: Path):
    signals = pd.DataFrame([_signal_row()])
    prices = _prices(DATES[:5])
    benchmark = _benchmark(_index_frame(DATES[:5]), cutoff=DATES[4])
    store = _store(tmp_path)

    stats = _sync(store, signals, {"000001": prices}, {"KS11": benchmark})
    warnings = _warnings_for(signals, {"KS11": benchmark}, stats, cutoff=DATES[4])

    assert stats["benchmark_5d_stock_date_unavailable"] == 1
    assert stats["benchmark_5d_matured_return_stock_date_unavailable"] == 0
    assert store.load().iloc[0]["tracking_status"] == "OPEN"
    assert warnings == []


def test_benchmark_uses_stock_dates_not_own_row_offset():
    benchmark = _benchmark(_index_frame(DATES[:6], [100, 1, 1, 1, 1, 120]))
    outcome = compute_benchmark_return_for_dates(benchmark, SIGNAL_DATE, END_5D, 5)
    assert outcome.status == STATUS_CALCULATED
    assert outcome.value == pytest.approx(20.0)
    assert (outcome.start_date, outcome.end_date, outcome.source) == (SIGNAL_DATE, END_5D, "NAVER:KS11")


def _seed_with_stored(tmp_path: Path, **stored: float) -> ExpandedCandidatePerformanceStore:
    store = _store(tmp_path)
    _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {})
    frame = store.load()
    for column, value in stored.items():
        frame[column] = frame[column].astype(object)
        frame.at[0, column] = value
    frame.to_csv(store.path, index=False)
    return store


def test_existing_benchmark_value_is_preserved(tmp_path: Path):
    store = _seed_with_stored(tmp_path, benchmark_5d=1.25)
    stats = _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {"KS11": _benchmark(_index_frame())})

    row = _only_row(store)
    assert row["benchmark_5d"] == pytest.approx(1.25)
    assert row["excess_5d"] == pytest.approx(10.0 - 1.25)
    assert stats["mismatch"] == 1
    warnings = _warnings_for(pd.DataFrame([_signal_row()]), {"KS11": _benchmark(_index_frame())}, stats)
    assert any(warning["code"] == "EXISTING_BENCHMARK_EXCESS_MISMATCH" for warning in warnings)


def test_existing_excess_value_is_preserved(tmp_path: Path):
    store = _seed_with_stored(tmp_path, excess_5d=7.5)
    stats = _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {"KS11": _benchmark(_index_frame())})

    row = _only_row(store)
    assert row["benchmark_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(7.5)
    assert stats["mismatch"] == 1


def test_conflicting_stored_return_blocks_benchmark_fill(tmp_path: Path):
    store = _seed_with_stored(tmp_path, return_5d=3.0)
    stats = _sync(store, pd.DataFrame([_signal_row()]), {"000001": _prices()}, {"KS11": _benchmark(_index_frame())})

    row = _only_row(store)
    assert row["return_5d"] == pytest.approx(3.0)
    assert pd.isna(row["benchmark_5d"]) and pd.isna(row["excess_5d"])
    assert stats["benchmark_stock_return_conflict"] == 1
    warnings = _warnings_for(
        pd.DataFrame([_signal_row()]),
        {"KS11": _benchmark(_index_frame())},
        stats,
    )
    assert any(warning["code"] == "STOCK_RETURN_CONFLICT" for warning in warnings)


def test_provider_failure_keeps_na(tmp_path: Path, monkeypatch):
    def failing(*_args, **_kwargs):
        raise ExpandedBenchmarkError("naver down")

    monkeypatch.setattr(cli, "load_expanded_benchmark", failing)
    signals = pd.DataFrame([_signal_row(), _signal_row("000002", "KOSDAQ")])
    benchmark_map, errors = cli._load_benchmark_map(signals, END_5D, provider=cli.PROVIDER_NAVER)
    store = _store(tmp_path)
    stats = _sync(store, signals, {"000001": _prices(), "000002": _prices()}, benchmark_map)

    assert benchmark_map == {}
    assert set(errors) == {"KS11", "KQ11"}
    frame = store.load()
    assert frame["benchmark_5d"].isna().all() and frame["excess_5d"].isna().all()
    assert stats["missing_benchmark"] == 2
    assert frame["return_5d"].tolist() == pytest.approx([10.0, 10.0])
    warnings = _warnings_for(signals, benchmark_map, stats, errors=errors)
    assert {warning["code"] for warning in warnings} >= {"PROVIDER_ERROR", "INVALID_SOURCE"}


def test_timeout_then_retry_fills_only_missing_metrics_idempotently(tmp_path: Path, monkeypatch):
    signals = pd.DataFrame([_signal_row()])
    long_dates = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(SIGNAL_DATE, periods=21)]
    prices = {"000001": _prices(long_dates, step=2.0)}
    store = _store(tmp_path)
    _sync(store, signals, prices, {})

    seeded = store.load()
    seeded["benchmark_5d"] = seeded["benchmark_5d"].astype(object)
    seeded["excess_5d"] = seeded["excess_5d"].astype(object)
    seeded.at[0, "benchmark_5d"] = 1.25
    seeded.at[0, "excess_5d"] = 2.5
    seeded.to_csv(store.path, index=False, lineterminator="\n")
    before_timeout = store.path.read_bytes()
    returns_before = store.load().loc[0, ["return_5d", "return_10d", "return_20d"]].copy()

    def timed_out(*_args, **_kwargs):
        raise ExpandedBenchmarkTimeoutError("simulated hard timeout")

    monkeypatch.setattr(cli, "load_expanded_benchmark", timed_out)
    benchmark_map, errors = cli._load_benchmark_map(
        signals, long_dates[20], provider=cli.PROVIDER_NAVER,
    )
    timeout_stats = _sync(store, signals, prices, benchmark_map)
    warnings = _warnings_for(
        signals,
        benchmark_map,
        timeout_stats,
        errors=errors,
        cutoff=long_dates[20],
    )

    assert benchmark_map == {}
    assert errors == {"KS11": "ExpandedBenchmarkTimeoutError: simulated hard timeout"}
    assert any(warning["code"] == "PROVIDER_ERROR" for warning in warnings)
    assert timeout_stats["updated"] == 0
    assert store.path.read_bytes() == before_timeout

    benchmark = load_expanded_benchmark(
        "KS11",
        long_dates[0],
        long_dates[20],
        reader=lambda *_args: _naver_raw(long_dates, [200.0 + index for index in range(21)]),
    )
    retry_stats = _sync(
        store,
        signals,
        prices,
        {"KS11": benchmark},
    )
    recovered = store.load().iloc[0]
    recovered_bytes = store.path.read_bytes()
    updated_at = recovered["updated_at"]

    assert retry_stats["new_benchmark_10d"] == 1
    assert retry_stats["new_benchmark_20d"] == 1
    assert recovered["return_5d"] == pytest.approx(returns_before["return_5d"])
    assert recovered["return_10d"] == pytest.approx(returns_before["return_10d"])
    assert recovered["return_20d"] == pytest.approx(returns_before["return_20d"])
    assert recovered["benchmark_5d"] == pytest.approx(1.25)
    assert recovered["excess_5d"] == pytest.approx(2.5)
    assert pd.notna(recovered["benchmark_10d"]) and pd.notna(recovered["excess_10d"])
    assert pd.notna(recovered["benchmark_20d"]) and pd.notna(recovered["excess_20d"])

    repeated = _sync(
        store,
        signals,
        prices,
        {"KS11": benchmark},
    )
    assert repeated["updated"] == 0
    assert store.path.read_bytes() == recovered_bytes
    assert store.load().iloc[0]["updated_at"] == updated_at


def test_reader_exception_and_missing_close_propagate_as_provider_errors():
    with pytest.raises(ExpandedBenchmarkError):
        load_expanded_benchmark("KS11", "2026-09-01", END_5D, reader=lambda *_: _naver_raw(DATES[:2], [1.0, 2.0]).drop(columns="Close"))
    with pytest.raises(ExpandedBenchmarkError):
        load_expanded_benchmark("XX", "2026-09-01", END_5D, reader=lambda *_: None)


def test_default_loader_path_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load_benchmark", lambda *args: calls.append(args) or _index_frame())
    monkeypatch.setattr(cli, "load_expanded_benchmark", lambda *_args: pytest.fail("Naver provider must be opt-in"))
    benchmark_map, errors = cli._load_benchmark_map(pd.DataFrame([_signal_row()]), END_5D)
    assert set(benchmark_map) == {"KS11"} and errors == {}
    assert calls == [("KS11", "2026-08-18", END_5D)]


def test_stock_returns_are_identical_with_and_without_benchmark(tmp_path: Path):
    prices = _prices(DATES[:11], step=3.0)
    expected = compute_forward_returns(prices, SIGNAL_DATE, 100.0)
    without = _store(tmp_path / "a")
    with_benchmark = _store(tmp_path / "b")
    _sync(without, pd.DataFrame([_signal_row()]), {"000001": prices}, {})
    _sync(with_benchmark, pd.DataFrame([_signal_row()]), {"000001": prices},
          {"KS11": _benchmark(_index_frame(DATES[:11]), cutoff=DATES[10])})

    for field in ("return_5d", "return_10d"):
        assert _only_row(without)[field] == _only_row(with_benchmark)[field] == pytest.approx(expected[field])
    assert _only_row(with_benchmark)["benchmark_10d"] == pytest.approx(10.0)


def test_naver_preserves_existing_5d_values_for_88_candidates(tmp_path: Path):
    signals = pd.DataFrame([_signal_row(f"{number:06d}") for number in range(88)])
    prices = {ticker: _prices(step=2.0) for ticker in signals["stock_code"]}
    store = _store(tmp_path)

    initial_benchmark = load_expanded_benchmark(
        "KS11",
        "2026-09-01",
        END_5D,
        reader=lambda *_args: _naver_raw(DATES[:6], [200, 201, 202, 203, 204, 205]),
    )
    _sync(store, signals, prices, {"KS11": initial_benchmark})
    fields = ["return_5d", "benchmark_5d", "excess_5d", "updated_at"]
    before = store.load().set_index("ticker")[fields].copy()
    before_bytes = store.path.read_bytes()

    revised_naver = load_expanded_benchmark(
        "KS11",
        "2026-09-01",
        END_5D,
        reader=lambda *_args: _naver_raw(DATES[:6], [200, 202, 204, 206, 208, 220]),
    )
    stats = _sync(store, signals, prices, {"KS11": revised_naver})

    after = store.load().set_index("ticker")[fields]
    assert stats["duplicate_candidates"] == 88
    assert stats["updated"] == 0
    assert stats["existing_value_mismatch_5d"] == 88
    pd.testing.assert_frame_equal(after, before)
    assert store.path.read_bytes() == before_bytes


@pytest.mark.parametrize(
    "field",
    [f"{metric}_{horizon}d" for horizon in (5, 10, 20) for metric in ("return", "benchmark", "excess")],
)
def test_naver_never_overwrites_existing_metric_at_any_horizon(tmp_path: Path, field: str):
    dates = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(SIGNAL_DATE, periods=21)]
    signals = pd.DataFrame([_signal_row()])
    store = _store(tmp_path)
    _sync(store, signals, {}, {})

    seeded = store.load()
    seeded[field] = seeded[field].astype(object)
    seeded.at[0, field] = 123.456
    seeded.at[0, "tracking_status"] = "OPEN"
    store.path.write_text(seeded.to_csv(index=False, lineterminator="\n"), encoding="utf-8")
    benchmark = load_expanded_benchmark(
        "KS11",
        dates[0],
        dates[-1],
        reader=lambda *_args: _naver_raw(dates, [200.0 + index for index in range(21)]),
    )
    prices = pd.DataFrame({"date": dates, "close": [100.0 + 2.0 * index for index in range(21)]})

    stats = _sync(store, signals, {"000001": prices}, {"KS11": benchmark})

    assert _only_row(store)[field] == pytest.approx(123.456)
    horizon = int(field.rsplit("_", 1)[1][:-1])
    assert stats[f"existing_value_mismatch_{horizon}d"] == 1


def test_naver_horizons_fill_sequentially_without_overwrite(tmp_path: Path):
    dates = [day.strftime("%Y-%m-%d") for day in pd.bdate_range(SIGNAL_DATE, periods=21)]
    stock_prices = pd.DataFrame({"date": dates, "close": [100.0 + 2.0 * index for index in range(21)]})
    naver_closes = [200.0 + index for index in range(21)]
    store = _store(tmp_path)
    signals = pd.DataFrame([_signal_row()])
    previous: dict[int, dict[str, float]] = {}

    for horizon, expected_status in ((5, "5D"), (10, "10D"), (20, "COMPLETE")):
        cutoff = dates[horizon]
        benchmark = load_expanded_benchmark(
            "KS11",
            dates[0],
            cutoff,
            reader=lambda *_args: _naver_raw(dates, naver_closes),
        )
        stats = _sync(
            store,
            signals,
            {"000001": stock_prices.iloc[:horizon + 1].copy()},
            {"KS11": benchmark},
        )
        row = _only_row(store)
        for fields in previous.values():
            for field, value in fields.items():
                assert row[field] == pytest.approx(value)
        current_fields = [f"return_{horizon}d", f"benchmark_{horizon}d", f"excess_{horizon}d"]
        assert all(pd.notna(row[field]) for field in current_fields)
        assert row["tracking_status"] == expected_status
        assert stats[f"new_return_{horizon}d"] == 1
        assert stats[f"new_benchmark_{horizon}d"] == 1
        assert stats[f"new_excess_{horizon}d"] == 1
        previous[horizon] = {field: float(row[field]) for field in current_fields}


def test_injected_frame_without_issues_matches_naver_object():
    frame = _index_frame()
    assert validate_benchmark_frame(frame, symbol="KS11", source="X").closes == _benchmark(frame).closes
    assert _benchmark(frame).diagnostics()["errors"] == []
    assert compute_benchmark_return_for_dates(validate_benchmark_frame(pd.DataFrame(), symbol="KS11", source="X"),
                                              SIGNAL_DATE, END_5D, 5).status == STATUS_INVALID_SOURCE

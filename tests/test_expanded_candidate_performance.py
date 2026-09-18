from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.expanded_candidate_performance import (
    PERFORMANCE_FIELDS,
    STATUS_10D,
    STATUS_20D,
    STATUS_5D,
    STATUS_COMPLETE,
    STATUS_OPEN,
    ExpandedCandidatePerformanceStore,
)
from src.expanded_shadow_ops import ExpandedShadowPaths


SIGNAL_DATE = "2026-09-17"
NOW = "2026-09-18T12:00:00+00:00"


def _signal_row(ticker: str = "000001", **overrides) -> dict[str, object]:
    row: dict[str, object] = {
        "basDd": SIGNAL_DATE,
        "stock_code": ticker,
        "stock_name": f"Stock {ticker}",
        "market": "KOSPI",
        "signal_date": SIGNAL_DATE,
        "signal_price": 100.0,
        "signal_score": 80.0,
        "foreign_status": "POSITIVE",
        "decision": "CANDIDATE",
        "engine_version": "v0.1",
        "source_commit": "deadbeef",
        "run_id": "source-run-1",
        "created_at": "2026-09-17T12:00:00+00:00",
    }
    row.update(overrides)
    return row


def _signals(*rows: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(rows or [_signal_row()])


def _prices(n_after_signal: int, *, base: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range(SIGNAL_DATE, periods=n_after_signal + 1)
    return pd.DataFrame({"date": dates, "close": [base + step * index for index in range(len(dates))]})


def _store(tmp_path: Path) -> ExpandedCandidatePerformanceStore:
    return ExpandedCandidatePerformanceStore(ExpandedShadowPaths(tmp_path))


def _sync(
    store: ExpandedCandidatePerformanceStore,
    signal_ledger: pd.DataFrame | None = None,
    *,
    prices: dict[str, pd.DataFrame] | None = None,
    benchmarks: dict[str, pd.DataFrame] | None = None,
    now: str = NOW,
    dry_run: bool = False,
) -> dict[str, int]:
    return store.synchronize(
        signal_ledger if signal_ledger is not None else _signals(),
        price_map=prices or {},
        benchmark_map=benchmarks or {},
        now_func=lambda: now,
        dry_run=dry_run,
    )


def test_registers_only_new_candidates_with_canonical_entry_price(tmp_path: Path):
    store = _store(tmp_path)
    signals = _signals(
        _signal_row("000001", signal_price=123.45),
        _signal_row("000002", decision="EXCLUDED", foreign_status="NEGATIVE"),
    )

    stats = _sync(store, signals)
    row = store.load().iloc[0]

    assert stats["candidate_count"] == 1
    assert stats["registered"] == 1
    assert len(store.load()) == 1
    assert row["ticker"] == "000001"
    assert row["entry_price"] == pytest.approx(123.45)
    assert row["tracking_status"] == STATUS_OPEN
    assert list(store.load().columns) == PERFORMANCE_FIELDS


def test_duplicate_candidate_is_idempotent_and_file_unchanged(tmp_path: Path):
    store = _store(tmp_path)
    _sync(store)
    before = store.path.read_bytes()

    stats = _sync(store, now="2026-09-19T12:00:00+00:00")

    assert stats["registered"] == 0
    assert stats["duplicate_candidates"] == 1
    assert len(store.load()) == 1
    assert store.path.read_bytes() == before


def test_less_than_five_trading_days_stays_open(tmp_path: Path):
    store = _store(tmp_path)
    stats = _sync(store, prices={"000001": _prices(4)}, benchmarks={"KS11": _prices(4, base=200.0)})

    row = store.load().iloc[0]
    assert row["tracking_status"] == STATUS_OPEN
    assert pd.isna(row["return_5d"])
    assert stats["updated"] == 0


@pytest.mark.parametrize(
    "horizon, expected_status",
    [(5, STATUS_5D), (10, STATUS_10D)],
)
def test_advances_at_completed_trading_horizons(tmp_path: Path, horizon: int, expected_status: str):
    store = _store(tmp_path)

    _sync(
        store,
        prices={"000001": _prices(horizon)},
        benchmarks={"KS11": _prices(horizon, base=200.0, step=2.0)},
    )

    row = store.load().iloc[0]
    assert row["tracking_status"] == expected_status
    assert row[f"return_{horizon}d"] == pytest.approx(float(horizon))
    assert row[f"benchmark_{horizon}d"] == pytest.approx(float(horizon))
    assert row[f"excess_{horizon}d"] == pytest.approx(0.0)


def test_twenty_day_price_then_benchmark_completes_lifecycle(tmp_path: Path):
    store = _store(tmp_path)
    prices = {"000001": _prices(20)}

    _sync(store, prices=prices, benchmarks={"KS11": _prices(19, base=200.0, step=2.0)})
    at_twenty = store.load().iloc[0]
    assert at_twenty["tracking_status"] == STATUS_20D
    assert at_twenty["return_20d"] == pytest.approx(20.0)
    assert pd.isna(at_twenty["benchmark_20d"])
    assert pd.isna(at_twenty["completed_at"])

    stats = _sync(
        store,
        prices=prices,
        benchmarks={"KS11": _prices(20, base=200.0, step=2.0)},
        now="2026-10-20T12:00:00+00:00",
    )
    complete = store.load().iloc[0]
    assert complete["tracking_status"] == STATUS_COMPLETE
    assert complete["benchmark_20d"] == pytest.approx(20.0)
    assert complete["excess_20d"] == pytest.approx(0.0)
    assert complete["completed_at"] == "2026-10-20T12:00:00+00:00"
    assert stats["advanced_to_complete"] == 1


def test_uses_actual_price_rows_across_weekend_and_holiday(tmp_path: Path):
    store = _store(tmp_path)
    dates = pd.bdate_range(SIGNAL_DATE, periods=7).delete(3)
    prices = pd.DataFrame({"date": dates, "close": [100, 101, 102, 103, 104, 110]})
    benchmark = pd.DataFrame({"date": dates, "close": [200, 202, 204, 206, 208, 220]})

    _sync(store, prices={"000001": prices}, benchmarks={"KS11": benchmark})

    row = store.load().iloc[0]
    assert row["return_5d"] == pytest.approx(10.0)
    assert row["benchmark_5d"] == pytest.approx(10.0)
    assert row["tracking_status"] == STATUS_5D


def test_market_selects_canonical_benchmark_and_calculates_excess(tmp_path: Path):
    store = _store(tmp_path)
    signals = _signals(_signal_row("000001", market="KOSPI"), _signal_row("000002", market="KOSDAQ"))
    prices = {ticker: _prices(5, step=2.0) for ticker in ("000001", "000002")}
    benchmarks = {
        "KS11": _prices(5, base=100.0, step=1.0),
        "KQ11": _prices(5, base=100.0, step=3.0),
    }

    _sync(store, signals, prices=prices, benchmarks=benchmarks)
    rows = store.load().set_index("ticker")

    assert rows.loc["000001", "return_5d"] == pytest.approx(10.0)
    assert rows.loc["000001", "benchmark_5d"] == pytest.approx(5.0)
    assert rows.loc["000001", "excess_5d"] == pytest.approx(5.0)
    assert rows.loc["000002", "benchmark_5d"] == pytest.approx(15.0)
    assert rows.loc["000002", "excess_5d"] == pytest.approx(-5.0)


def test_existing_values_are_never_overwritten_but_later_horizon_advances(tmp_path: Path):
    store = _store(tmp_path)
    _sync(store, prices={"000001": _prices(5)}, benchmarks={"KS11": _prices(5, base=200.0, step=2.0)})
    fixed_5d = float(store.load().iloc[0]["return_5d"])
    changed_prices = _prices(10)
    changed_prices.loc[5, "close"] = 999.0

    stats = _sync(
        store,
        prices={"000001": changed_prices},
        benchmarks={"KS11": _prices(10, base=200.0, step=2.0)},
    )
    row = store.load().iloc[0]

    assert row["return_5d"] == pytest.approx(fixed_5d)
    assert row["return_10d"] == pytest.approx(10.0)
    assert row["tracking_status"] == STATUS_10D
    assert stats["mismatch"] == 1


def test_tracking_status_never_regresses_with_shorter_later_data(tmp_path: Path):
    store = _store(tmp_path)
    _sync(store, prices={"000001": _prices(10)}, benchmarks={"KS11": _prices(10, base=200.0, step=2.0)})
    before = store.load().iloc[0]

    _sync(store, prices={"000001": _prices(5)}, benchmarks={"KS11": _prices(5, base=200.0, step=2.0)})
    after = store.load().iloc[0]

    assert after["tracking_status"] == STATUS_10D
    assert after["return_5d"] == pytest.approx(before["return_5d"])
    assert after["return_10d"] == pytest.approx(before["return_10d"])


def test_complete_record_is_not_recalculated(tmp_path: Path):
    store = _store(tmp_path)
    _sync(store, prices={"000001": _prices(20)}, benchmarks={"KS11": _prices(20, base=200.0, step=2.0)})
    before = store.path.read_bytes()
    changed = _prices(20, step=10.0)

    stats = _sync(store, prices={"000001": changed}, benchmarks={"KS11": changed})

    assert stats["already_complete"] == 1
    assert stats["updated"] == 0
    assert stats["mismatch"] == 0
    assert store.path.read_bytes() == before


def test_missing_future_or_benchmark_data_is_normal_pending_state(tmp_path: Path):
    store = _store(tmp_path)

    stats = _sync(store, prices={"000001": _prices(3)}, benchmarks={})

    row = store.load().iloc[0]
    assert row["tracking_status"] == STATUS_OPEN
    assert stats["missing_benchmark"] == 1
    assert pd.isna(row["completed_at"])


def test_multiple_signals_and_later_candidate_enrollment(tmp_path: Path):
    store = _store(tmp_path)
    first = _signals(_signal_row("000001"), _signal_row("000002", market="KOSDAQ"))
    assert _sync(store, first)["registered"] == 2

    later = pd.concat(
        [first, _signals(_signal_row("000003", basDd="2026-09-18", signal_date="2026-09-18", run_id="source-run-2"))],
        ignore_index=True,
    )
    stats = _sync(store, later)

    assert stats["registered"] == 1
    assert stats["duplicate_candidates"] == 2
    assert set(store.load()["ticker"]) == {"000001", "000002", "000003"}


def test_any_candidate_count_is_supported_without_hardcoding(tmp_path: Path):
    store = _store(tmp_path)
    signals = _signals(*[_signal_row(f"{number:06d}") for number in range(1, 15)])

    stats = _sync(store, signals)

    assert stats["registered"] == 14
    assert len(store.load()) == 14


def test_dry_run_does_not_create_performance_ledger(tmp_path: Path):
    store = _store(tmp_path)

    stats = _sync(store, dry_run=True)

    assert stats["registered"] == 1
    assert not store.path.exists()


def test_production_shadow_and_dual_artifacts_are_unchanged(tmp_path: Path):
    protected = {
        tmp_path / "output/signals.csv": b"production",
        tmp_path / "output/shadow_signal_records.csv": b"shadow",
        tmp_path / "output/dual_shadow_signal_ledger.csv": b"dual",
        tmp_path / "data/raw/000001.csv": b"production-price",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    _sync(_store(tmp_path), prices={"000001": _prices(5)}, benchmarks={"KS11": _prices(5)})

    assert {path: path.read_bytes() for path in protected} == protected
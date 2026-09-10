"""
tests/test_dual_shadow_step4_forward_returns.py
=================================================
DUAL Shadow STEP 4 — Forward Return Tracking 검증.

STEP 3 Ledger는 읽기 전용으로만 다루며, Production Shadow(output/shadow_signal_records.csv)나
실제 STEP 3 Ledger(output/dual_shadow_signal_ledger.csv)를 건드리지 않도록 모든 테스트는
tmp_path 기반 fixture만 사용한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.dual_shadow_forward_returns import (
    DEFAULT_FORWARD_RETURN_PATH,
    FORWARD_RETURN_FIELDS,
    RETURN_STATUS_AVAILABLE,
    RETURN_STATUS_NOT_AVAILABLE,
    DualForwardReturnRecord,
    DualForwardReturnStore,
    MalformedLedgerError,
    build_forward_return_records,
    compute_dual_forward_return,
    run_dual_shadow_forward_returns_update,
)
from src.dual_shadow_ledger import DualShadowLedgerStore, LEDGER_FIELDS
from src.shadow_tracking import FORWARD_HORIZONS


def _business_day_price_df(n_rows: int, start_close: float = 100.0) -> pd.DataFrame:
    """월~금만 포함한 합성 가격 데이터 (거래일 오프셋 검증용, 실제 데이터 아님)."""
    dates = pd.bdate_range("2026-07-01", periods=n_rows, freq="B")
    closes = [start_close + i for i in range(n_rows)]
    return pd.DataFrame({"date": dates, "close": closes})


def _ledger_row(
    trade_date: str = "2026-07-08",
    stock_code: str = "096770",
    stock_name: str = "SK이노베이션",
    evaluation_close: float | None = 100.0,
    baseline_signal_present: bool = False,
    challenger_signal_present: bool = False,
    comparison_group: str = "BASELINE_ONLY",
    evaluation_status: str = "OK",
    baseline_engine_version: str = "v0.1",
    challenger_engine_version: str = "v0.2",
) -> dict:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "evaluation_close": evaluation_close,
        "baseline_raw_score": 5,
        "baseline_score": 81.5,
        "baseline_signal_type": "BUY_WATCH",
        "baseline_signal_present": baseline_signal_present,
        "challenger_raw_score": 3,
        "challenger_score": 66.2,
        "challenger_signal_type": "WAIT",
        "challenger_signal_present": challenger_signal_present,
        "challenger_volume_penalty": 0,
        "challenger_pre_return_penalty": 10,
        "challenger_rsi_penalty": 0,
        "challenger_total_penalty": 10,
        "comparison_group": comparison_group,
        "evaluation_status": evaluation_status,
        "baseline_engine_version": baseline_engine_version,
        "challenger_engine_version": challenger_engine_version,
        "source_commit": "deadbeef",
        "created_at": "2026-09-10T00:00:00+00:00",
    }


def _ledger_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=LEDGER_FIELDS)


@pytest.fixture
def forward_store(tmp_path):
    return DualForwardReturnStore(path=tmp_path / "dual_shadow_forward_returns.csv")


class TestSchema:
    def test_field_order(self):
        assert FORWARD_RETURN_FIELDS == [
            "trade_date",
            "stock_code",
            "stock_name",
            "evaluation_close",
            "baseline_signal_present",
            "challenger_signal_present",
            "comparison_group",
            "horizon",
            "target_date",
            "target_close",
            "forward_return",
            "return_status",
            "baseline_engine_version",
            "challenger_engine_version",
            "source_commit",
            "created_at",
        ]

    def test_default_path(self):
        from src import config

        assert DEFAULT_FORWARD_RETURN_PATH.name == "dual_shadow_forward_returns.csv"
        assert DEFAULT_FORWARD_RETURN_PATH.parent == config.OUTPUT_DIR

    def test_horizons_reused_from_production_shadow(self):
        assert FORWARD_HORIZONS == (5, 10, 20)


class TestStep3LedgerReadOnly:
    def test_ledger_file_untouched_by_step4(self, tmp_path, forward_store):
        ledger_store = DualShadowLedgerStore(path=tmp_path / "dual_shadow_signal_ledger.csv")
        df = _ledger_df([_ledger_row()])
        df.to_csv(ledger_store.path, index=False)
        before = ledger_store.path.read_bytes()

        price_map = {"096770": _business_day_price_df(40)}
        run_dual_shadow_forward_returns_update(
            ledger_store=ledger_store, forward_store=forward_store, price_map=price_map
        )

        after = ledger_store.path.read_bytes()
        assert before == after


class TestForwardReturnComputation:
    def test_5d_10d_20d_calculation(self):
        price_df = _business_day_price_df(40)
        trade_date = price_df["date"].iloc[5].strftime("%Y-%m-%d")
        evaluation_close = float(price_df["close"].iloc[5])

        for horizon in (5, 10, 20):
            detail = compute_dual_forward_return(price_df, trade_date, evaluation_close, horizon)
            assert detail["return_status"] == RETURN_STATUS_AVAILABLE
            expected_target_close = float(price_df["close"].iloc[5 + horizon])
            expected_target_date = price_df["date"].iloc[5 + horizon].strftime("%Y-%m-%d")
            expected_return = (expected_target_close / evaluation_close - 1.0) * 100.0
            assert detail["target_close"] == pytest.approx(expected_target_close)
            assert detail["target_date"] == expected_target_date
            assert detail["forward_return"] == pytest.approx(expected_return)

    def test_target_trading_date_skips_no_extra_days(self):
        # 합성 데이터는 월~금만 포함하므로 +5 거래일 = 정확히 5번째 다음 행이어야 한다.
        price_df = _business_day_price_df(40)
        trade_date = price_df["date"].iloc[0].strftime("%Y-%m-%d")
        detail = compute_dual_forward_return(price_df, trade_date, float(price_df["close"].iloc[0]), 5)
        assert detail["target_date"] == price_df["date"].iloc[5].strftime("%Y-%m-%d")

    def test_not_available_when_future_missing(self):
        price_df = _business_day_price_df(10)
        trade_date = price_df["date"].iloc[8].strftime("%Y-%m-%d")
        detail = compute_dual_forward_return(price_df, trade_date, float(price_df["close"].iloc[8]), 20)
        assert detail["return_status"] == RETURN_STATUS_NOT_AVAILABLE
        assert detail["target_date"] is None
        assert detail["target_close"] is None
        assert detail["forward_return"] is None

    def test_not_available_is_never_zero_percent(self):
        price_df = _business_day_price_df(3)
        trade_date = price_df["date"].iloc[0].strftime("%Y-%m-%d")
        detail = compute_dual_forward_return(price_df, trade_date, float(price_df["close"].iloc[0]), 5)
        assert detail["return_status"] == RETURN_STATUS_NOT_AVAILABLE
        assert detail["forward_return"] != 0
        assert detail["forward_return"] is None

    def test_missing_price_df_is_not_available(self):
        detail = compute_dual_forward_return(None, "2026-09-10", 100.0, 5)
        assert detail["return_status"] == RETURN_STATUS_NOT_AVAILABLE


class TestBuildRecordsFromLedger:
    def test_available_and_not_available_split(self, forward_store):
        ledger_df = _ledger_df([_ledger_row(trade_date="2026-07-06")])  # 4th business day, index 3
        price_df = _business_day_price_df(15)  # not enough rows for 20D from index 3
        price_map = {"096770": price_df}

        records, stats = build_forward_return_records(ledger_df, forward_store, price_map=price_map)

        statuses = {r.horizon: r.return_status for r in records}
        assert statuses.get(5) == RETURN_STATUS_AVAILABLE
        assert statuses.get(10) == RETURN_STATUS_AVAILABLE
        assert 20 not in statuses  # NOT_AVAILABLE horizon is not persisted as a record
        assert stats["not_available"] == 1
        assert stats["available"] == 2

    def test_not_evaluable_skipped_safely(self, forward_store):
        ledger_df = _ledger_df(
            [_ledger_row(evaluation_status="NOT_EVALUABLE", evaluation_close=None, comparison_group="NOT_EVALUABLE")]
        )
        records, stats = build_forward_return_records(ledger_df, forward_store, price_map={})
        assert records == []
        assert stats["skipped_not_evaluable"] == 1

    def test_missing_price_data_counted_and_not_available(self, forward_store):
        ledger_df = _ledger_df([_ledger_row()])
        records, stats = build_forward_return_records(ledger_df, forward_store, price_map={})
        assert records == []
        assert stats["missing_price"] == 1
        assert stats["not_available"] == len(FORWARD_HORIZONS)

    def test_malformed_ledger_fails_closed(self, forward_store):
        ledger_df = _ledger_df([_ledger_row()]).drop(columns=["evaluation_close"])
        with pytest.raises(MalformedLedgerError):
            build_forward_return_records(ledger_df, forward_store, price_map={})

    def test_empty_ledger_returns_empty(self, forward_store):
        records, stats = build_forward_return_records(pd.DataFrame(columns=LEDGER_FIELDS), forward_store)
        assert records == []
        assert stats["ledger_rows"] == 0


class TestComparisonGroupHandling:
    @pytest.mark.parametrize(
        "comparison_group,baseline_present,challenger_present",
        [
            ("BOTH_YES", True, True),
            ("BASELINE_ONLY", True, False),
            ("CHALLENGER_ONLY", False, True),
            ("BOTH_NO", False, False),
        ],
    )
    def test_each_comparison_group_is_processed(
        self, forward_store, comparison_group, baseline_present, challenger_present
    ):
        price_df = _business_day_price_df(40)
        trade_date = price_df["date"].iloc[5].strftime("%Y-%m-%d")
        ledger_df = _ledger_df(
            [
                _ledger_row(
                    trade_date=trade_date,
                    comparison_group=comparison_group,
                    baseline_signal_present=baseline_present,
                    challenger_signal_present=challenger_present,
                )
            ]
        )
        price_map = {"096770": price_df}
        records, stats = build_forward_return_records(ledger_df, forward_store, price_map=price_map)

        assert len(records) == len(FORWARD_HORIZONS)
        for r in records:
            assert r.comparison_group == comparison_group
            assert r.baseline_signal_present == baseline_present
            assert r.challenger_signal_present == challenger_present


class TestIdempotencyAndAppendOnly:
    def test_rerun_same_key_no_duplicates(self, tmp_path, forward_store):
        ledger_store = DualShadowLedgerStore(path=tmp_path / "ledger.csv")
        price_df = _business_day_price_df(40)
        trade_date = price_df["date"].iloc[5].strftime("%Y-%m-%d")
        df = _ledger_df([_ledger_row(trade_date=trade_date)])
        df.to_csv(ledger_store.path, index=False)
        price_map = {"096770": price_df}

        stats1 = run_dual_shadow_forward_returns_update(
            ledger_store=ledger_store, forward_store=forward_store, price_map=price_map
        )
        assert stats1["saved"] == len(FORWARD_HORIZONS)

        stats2 = run_dual_shadow_forward_returns_update(
            ledger_store=ledger_store, forward_store=forward_store, price_map=price_map
        )
        assert stats2["saved"] == 0
        assert stats2["already_recorded"] == len(FORWARD_HORIZONS)

        final = forward_store.load()
        assert len(final) == len(FORWARD_HORIZONS)

    def test_existing_row_never_mutated(self, forward_store):
        record = DualForwardReturnRecord(
            trade_date="2026-09-10",
            stock_code="096770",
            stock_name="SK이노베이션",
            evaluation_close=100.0,
            baseline_signal_present=True,
            challenger_signal_present=False,
            comparison_group="BASELINE_ONLY",
            horizon=5,
            target_date="2026-09-17",
            target_close=105.0,
            forward_return=5.0,
            return_status=RETURN_STATUS_AVAILABLE,
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
            source_commit="deadbeef",
            created_at="2026-09-10T00:00:00+00:00",
        )
        assert forward_store.add(record) is True
        before = forward_store.load()

        # 동일 key로 다른 값을 넣으려 해도 기존 row는 유지되어야 한다.
        mutated = DualForwardReturnRecord(**{**record.__dict__, "forward_return": 999.0})
        assert forward_store.add(mutated) is False
        after = forward_store.load()
        pd.testing.assert_frame_equal(before, after)
        assert after.iloc[0]["forward_return"] == 5.0

    def test_new_horizon_can_be_appended_later(self, tmp_path, forward_store):
        ledger_store = DualShadowLedgerStore(path=tmp_path / "ledger.csv")
        df = _ledger_df([_ledger_row(trade_date="2026-07-06")])
        df.to_csv(ledger_store.path, index=False)

        short_price_df = _business_day_price_df(15)  # only 5D/10D available
        stats1 = run_dual_shadow_forward_returns_update(
            ledger_store=ledger_store,
            forward_store=forward_store,
            price_map={"096770": short_price_df},
        )
        assert stats1["saved"] == 2
        assert stats1["not_available"] == 1

        longer_price_df = _business_day_price_df(40)  # now 20D is available too
        stats2 = run_dual_shadow_forward_returns_update(
            ledger_store=ledger_store,
            forward_store=forward_store,
            price_map={"096770": longer_price_df},
        )
        assert stats2["saved"] == 1  # only new 20D horizon appended
        assert stats2["already_recorded"] == 2

        final = forward_store.load()
        assert set(final["horizon"].astype(int)) == {5, 10, 20}

    def test_engine_version_change_allows_new_evidence(self, forward_store):
        record_v1 = DualForwardReturnRecord(
            trade_date="2026-09-10",
            stock_code="096770",
            stock_name="SK이노베이션",
            evaluation_close=100.0,
            baseline_signal_present=True,
            challenger_signal_present=False,
            comparison_group="BASELINE_ONLY",
            horizon=5,
            target_date="2026-09-17",
            target_close=105.0,
            forward_return=5.0,
            return_status=RETURN_STATUS_AVAILABLE,
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
            source_commit="deadbeef",
            created_at="2026-09-10T00:00:00+00:00",
        )
        record_v2 = DualForwardReturnRecord(**{**record_v1.__dict__, "challenger_engine_version": "v0.3"})

        assert forward_store.add(record_v1) is True
        assert forward_store.add(record_v2) is True
        assert len(forward_store.load()) == 2

    def test_store_rejects_not_available_record(self, forward_store):
        record = DualForwardReturnRecord(
            trade_date="2026-09-10",
            stock_code="096770",
            stock_name="SK이노베이션",
            evaluation_close=100.0,
            baseline_signal_present=True,
            challenger_signal_present=False,
            comparison_group="BASELINE_ONLY",
            horizon=5,
            target_date="",
            target_close=0.0,
            forward_return=0.0,
            return_status=RETURN_STATUS_NOT_AVAILABLE,
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
            source_commit="deadbeef",
            created_at="2026-09-10T00:00:00+00:00",
        )
        with pytest.raises(ValueError):
            forward_store.add(record)

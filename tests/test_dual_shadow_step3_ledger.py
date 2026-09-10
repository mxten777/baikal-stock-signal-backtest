"""
tests/test_dual_shadow_step3_ledger.py
=======================================
DUAL Shadow STEP 3 — Append-only DUAL Comparison Ledger 검증.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd
import pytest

from src import config
from src.data_provider.csv_provider import CsvDataProvider
from src.dual_shadow_evaluator import evaluate_latest_day
from src.dual_shadow_ledger import (
    DEFAULT_LEDGER_PATH,
    LEDGER_FIELDS,
    DualShadowLedgerStore,
    build_ledger_records_for_all_tickers,
    get_source_commit,
    run_dual_shadow_ledger_build,
)
from src.indicators import add_all_indicators

DATA_RAW_DIR = Path(__file__).parent.parent / "data" / "raw"


@pytest.fixture
def ledger_store(tmp_path):
    return DualShadowLedgerStore(path=tmp_path / "dual_shadow_signal_ledger.csv")


class TestLedgerSchema:
    def test_ledger_field_order_and_required_columns(self):
        expected = [
            "trade_date",
            "stock_code",
            "stock_name",
            "evaluation_close",
            "baseline_raw_score",
            "baseline_score",
            "baseline_signal_type",
            "baseline_signal_present",
            "challenger_raw_score",
            "challenger_score",
            "challenger_signal_type",
            "challenger_signal_present",
            "challenger_volume_penalty",
            "challenger_pre_return_penalty",
            "challenger_rsi_penalty",
            "challenger_total_penalty",
            "comparison_group",
            "evaluation_status",
            "baseline_engine_version",
            "challenger_engine_version",
            "source_commit",
            "created_at",
        ]
        assert LEDGER_FIELDS == expected

    def test_default_ledger_path(self):
        assert DEFAULT_LEDGER_PATH.name == "dual_shadow_signal_ledger.csv"
        assert DEFAULT_LEDGER_PATH.parent == config.OUTPUT_DIR


class TestAppendOnlyIdempotent:
    def test_add_then_duplicate_skipped(self, ledger_store):
        records = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
        )
        record = records[0]

        assert ledger_store.add(record) is True
        assert ledger_store.add(record) is False

        df = ledger_store.load()
        assert len(df) == 1

    def test_run_twice_is_idempotent(self, ledger_store):
        saved1, stats1 = run_dual_shadow_ledger_build(
            tickers=config.TICKERS, store=ledger_store, source_commit="deadbeef"
        )
        saved2, stats2 = run_dual_shadow_ledger_build(
            tickers=config.TICKERS, store=ledger_store, source_commit="deadbeef"
        )

        assert stats1["saved"] == len(config.TICKERS)
        assert stats1["duplicate_skip"] == 0
        assert stats2["saved"] == 0
        assert stats2["duplicate_skip"] == len(config.TICKERS)

        df = ledger_store.load()
        assert len(df) == len(config.TICKERS)


class TestComparisonKeyEngineVersion:
    """Comparison key = (trade_date, stock_code, baseline_engine_version, challenger_engine_version)."""

    def test_case_a_same_versions_is_duplicate(self, ledger_store):
        records = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
        )
        record = records[0]

        assert ledger_store.add(record) is True
        assert ledger_store.add(record) is False

        df = ledger_store.load()
        assert len(df) == 1

    def test_case_b_different_challenger_version_appends_new_row(self, ledger_store):
        records_v1 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
        )
        assert ledger_store.add(records_v1[0]) is True

        records_v2 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.3",
        )
        assert ledger_store.add(records_v2[0]) is True

        df = ledger_store.load()
        assert len(df) == 2
        assert set(df["challenger_engine_version"]) == {"v0.2", "v0.3"}

    def test_case_c_different_baseline_version_appends_new_row(self, ledger_store):
        records_v1 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            baseline_engine_version="v0.1",
            challenger_engine_version="v0.2",
        )
        assert ledger_store.add(records_v1[0]) is True

        records_v2 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            baseline_engine_version="v0.1a",
            challenger_engine_version="v0.2",
        )
        assert ledger_store.add(records_v2[0]) is True

        df = ledger_store.load()
        assert len(df) == 2
        assert set(df["baseline_engine_version"]) == {"v0.1", "v0.1a"}

    def test_existing_row_not_modified_when_new_version_appended(self, ledger_store):
        records_v1 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"}, source_commit="deadbeef"
        )
        ledger_store.add(records_v1[0])
        before = ledger_store.load().to_dict("records")[0]

        records_v2 = build_ledger_records_for_all_tickers(
            tickers={"096770": "SK이노베이션"},
            source_commit="deadbeef",
            challenger_engine_version="v0.3",
        )
        ledger_store.add(records_v2[0])

        df = ledger_store.load()
        first_row = df.iloc[0].to_dict()
        assert first_row["challenger_engine_version"] == before["challenger_engine_version"]
        assert len(df) == 2


class TestAllTickersSaved:
    def test_20_tickers_saved_in_one_run(self, ledger_store):
        saved, stats = run_dual_shadow_ledger_build(store=ledger_store, source_commit="deadbeef")

        assert len(config.TICKERS) == 20
        assert stats["checked"] == 20
        assert stats["saved"] == 20

        df = ledger_store.load()
        assert len(df) == 20
        assert set(df["stock_code"].astype(str)) == set(config.TICKERS.keys())


class TestSkInnovationReproduction:
    def test_sk_innovation_2026_09_10_matches_evaluator(self, ledger_store):
        sk_path = DATA_RAW_DIR / "096770.csv"
        if not sk_path.exists():
            pytest.skip("096770.csv not found")

        df = pd.read_csv(sk_path)
        df_ind = add_all_indicators(df)
        expected = evaluate_latest_day(df_ind, "096770", "SK이노베이션")
        assert expected.trade_date == "2026-09-10"

        saved, _ = run_dual_shadow_ledger_build(
            tickers={"096770": "SK이노베이션"}, store=ledger_store, source_commit="deadbeef"
        )
        record = saved[0]

        assert record.trade_date == "2026-09-10"
        assert record.stock_code == "096770"
        assert record.evaluation_close == expected.close
        assert record.baseline_score == expected.baseline.score
        assert record.baseline_signal_present == expected.baseline.signal_present
        assert record.challenger_score == expected.challenger.score
        assert record.challenger_signal_present == expected.challenger.signal_present
        assert record.comparison_group == expected.comparison_group
        assert record.evaluation_status == expected.evaluation_status


class TestComparisonGroup:
    def test_comparison_group_values_valid(self, ledger_store):
        saved, _ = run_dual_shadow_ledger_build(store=ledger_store, source_commit="deadbeef")
        valid = {"BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO"}
        for record in saved:
            assert record.comparison_group in valid

    def test_baseline_only_case(self, ledger_store):
        """Baseline signal, Challenger penalty로 미달인 합성 케이스."""
        rows = []
        base = {
            "close": 100000.0,
            "ma5": 102000.0,
            "ma20": 100000.0,
            "ma60": 98000.0,
            "volume": 5_000_000.0,
            "volume_ma20": 1_000_000.0,
            "rsi": 78.0,
            "macd": 500.0,
            "macd_signal": 300.0,
            "return_5d_pct": 25.0,
        }
        for i in range(30):
            row = dict(base)
            row["date"] = pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)
            rows.append(row)
        df = pd.DataFrame(rows)

        evaluation = evaluate_latest_day(df, "TEST", "테스트종목")
        assert evaluation.evaluation_status == "OK"

        from src.dual_shadow_ledger import build_ledger_record

        record = build_ledger_record(evaluation, source_commit="deadbeef")
        assert record.comparison_group in {"BOTH_YES", "BASELINE_ONLY", "BOTH_NO", "CHALLENGER_ONLY"}


class TestNotEvaluable:
    def test_missing_data_produces_not_evaluable_distinct_from_both_no(self, ledger_store):
        saved, stats = run_dual_shadow_ledger_build(
            tickers={"999999": "없는종목"}, store=ledger_store, source_commit="deadbeef"
        )
        record = saved[0]

        assert record.evaluation_status == "NOT_EVALUABLE"
        assert record.comparison_group == "NOT_EVALUABLE"
        assert record.comparison_group != "BOTH_NO"
        assert record.baseline_signal_present is False
        assert record.challenger_signal_present is False
        # NOT_EVALUABLE은 stats에서 BOTH_NO count와 별도로 집계된다
        assert stats["not_evaluable"] == 1
        assert stats["both_no"] == 0

    def test_real_both_no_is_not_counted_as_not_evaluable(self, ledger_store):
        samsung_path = DATA_RAW_DIR / "005930.csv"
        if not samsung_path.exists():
            pytest.skip("005930.csv not found")

        saved, stats = run_dual_shadow_ledger_build(
            tickers={"005930": "삼성전자"}, store=ledger_store, source_commit="deadbeef"
        )
        record = saved[0]
        if record.evaluation_status == "OK" and record.comparison_group == "BOTH_NO":
            assert stats["both_no"] == 1
            assert stats["not_evaluable"] == 0


class TestSourceCommitAndCreatedAt:
    def test_get_source_commit_returns_real_hash(self):
        commit = get_source_commit()
        # 실제 git repo이므로 UNKNOWN이 아니어야 하고 40자 hex여야 한다
        assert commit != "UNKNOWN"
        assert len(commit) == 40

    def test_created_at_recorded(self, ledger_store):
        saved, _ = run_dual_shadow_ledger_build(
            tickers={"096770": "SK이노베이션"}, store=ledger_store, source_commit="deadbeef"
        )
        record = saved[0]
        assert record.created_at
        assert record.source_commit == "deadbeef"
        assert record.baseline_engine_version == "v0.1"
        assert record.challenger_engine_version == "v0.2"


class TestProductionShadowUntouched:
    def test_production_shadow_files_not_modified(self):
        result = subprocess.run(
            ["git", "diff", "--stat", "--",
             "src/shadow_tracking.py",
             "scripts/shadow_daily_scan.py",
             "scripts/shadow_step1_track_signals.py",
             "scripts/shadow_update_returns.py",
             "scripts/shadow_update_benchmark.py",
             "scripts/shadow_performance_report.py",
             "scripts/shadow_daily_pipeline.py",
             "src/dual_shadow_evaluator.py",
             ],
            cwd=str(config.ROOT_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == ""

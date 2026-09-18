from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.expanded_shadow_ledger import (
    DEDUPE_FIELDS,
    ENGINE_VERSION,
    EXECUTION_METADATA_FIELDS,
    FLOAT_ABS_TOLERANCE,
    FLOAT_REL_TOLERANCE,
    LEDGER_FIELDS,
    NUMERIC_SIGNAL_FIELDS,
    SIGNAL_IDENTITY_FIELDS,
    ExpandedLedgerError,
    ExpandedShadowLedgerStore,
    build_ledger_record,
)
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_signal import ExpandedSignalEvaluation
from src.shadow_tracking import DECISION_CANDIDATE, DECISION_EXCLUDED, EXCLUSION_REASON_FOREIGN_NEGATIVE


BAS_DD = "2026-09-17"


def _paths(tmp_path: Path) -> ExpandedShadowPaths:
    return ExpandedShadowPaths(tmp_path)


def _evaluation(
    *,
    ticker: str = "005930",
    foreign_status: str = "POSITIVE",
    decision: str = DECISION_CANDIDATE,
    exclusion_reason: str | None = None,
    signal_present: bool = True,
    signal_score: float = 80.0,
) -> ExpandedSignalEvaluation:
    return ExpandedSignalEvaluation(
        ticker=ticker,
        name="테스트",
        market="KOSPI",
        basDd=BAS_DD,
        evaluated=True,
        signal_present=signal_present,
        signal_date=BAS_DD if signal_present else None,
        signal_price=100.0 if signal_present else None,
        raw_score=52 if signal_present else None,
        signal_score=signal_score if signal_present else None,
        signal_type="BUY_WATCH" if signal_present else None,
        foreign_5d_ratio=0.25 if signal_present else None,
        foreign_status=foreign_status if signal_present else None,
        decision=decision if signal_present else None,
        exclusion_reason=exclusion_reason,
        reason=None if signal_present else "NO_SIGNAL",
    )


def test_ledger_schema_and_dedupe_key():
    assert LEDGER_FIELDS == [
        "basDd",
        "stock_code",
        "stock_name",
        "market",
        "signal_date",
        "signal_price",
        "raw_score",
        "signal_score",
        "signal_type",
        "foreign_5d_ratio",
        "foreign_status",
        "decision",
        "exclusion_reason",
        "engine_version",
        "source_commit",
        "run_id",
        "created_at",
    ]
    assert DEDUPE_FIELDS == ("basDd", "stock_code", "signal_date", "engine_version")
    assert EXECUTION_METADATA_FIELDS == frozenset({"source_commit", "run_id", "created_at"})
    assert SIGNAL_IDENTITY_FIELDS == tuple(field for field in LEDGER_FIELDS if field not in EXECUTION_METADATA_FIELDS)
    assert NUMERIC_SIGNAL_FIELDS == frozenset({"signal_price", "raw_score", "signal_score", "foreign_5d_ratio"})
    assert FLOAT_REL_TOLERANCE == 1e-12
    assert FLOAT_ABS_TOLERANCE == 1e-12


def test_candidate_append(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))

    assert store.add_evaluation(_evaluation(), run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00") is True
    df = store.load()
    assert len(df) == 1
    assert df.iloc[0]["decision"] == DECISION_CANDIDATE


def test_foreign_negative_excluded_append(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    evaluation = _evaluation(foreign_status="NEGATIVE", decision=DECISION_EXCLUDED, exclusion_reason=EXCLUSION_REASON_FOREIGN_NEGATIVE)

    assert store.add_evaluation(evaluation, run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00") is True
    row = store.load().iloc[0]
    assert row["decision"] == DECISION_EXCLUDED
    assert row["exclusion_reason"] == EXCLUSION_REASON_FOREIGN_NEGATIVE


def test_no_signal_not_recorded(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))

    assert store.add_evaluation(_evaluation(signal_present=False), run_id="run-1", source_commit="deadbeef") is False
    assert not store.path.exists()


def test_same_signal_rerun_duplicate_zero(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    evaluation = _evaluation()

    assert store.add_evaluation(evaluation, run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00") is True
    assert store.add_evaluation(evaluation, run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00") is False
    assert len(store.load()) == 1


@pytest.mark.parametrize(
    ("decision", "foreign_status", "exclusion_reason"),
    [
        (DECISION_CANDIDATE, "POSITIVE", None),
        (DECISION_EXCLUDED, "NEGATIVE", EXCLUSION_REASON_FOREIGN_NEGATIVE),
    ],
)
@pytest.mark.parametrize(
    "rerun_metadata",
    [
        {"run_id": "run-2", "source_commit": "deadbeef", "created_at": "2026-09-17T00:00:00+00:00"},
        {"run_id": "run-1", "source_commit": "cafebabe", "created_at": "2026-09-17T00:00:00+00:00"},
        {"run_id": "run-1", "source_commit": "deadbeef", "created_at": "2026-09-17T00:01:00+00:00"},
    ],
)
def test_same_signal_with_different_execution_metadata_is_noop(
    tmp_path: Path,
    decision: str,
    foreign_status: str,
    exclusion_reason: str | None,
    rerun_metadata: dict[str, str],
):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    evaluation = _evaluation(
        foreign_status=foreign_status,
        decision=decision,
        exclusion_reason=exclusion_reason,
    )
    initial_metadata = {
        "run_id": "run-1",
        "source_commit": "deadbeef",
        "created_at": "2026-09-17T00:00:00+00:00",
    }
    assert store.add_evaluation(evaluation, **initial_metadata) is True
    before = store.path.read_bytes()

    assert store.add_evaluation(evaluation, **rerun_metadata) is False

    assert store.path.read_bytes() == before
    assert len(store.load()) == 1


def test_real_foreign_ratio_round_trip_noise_is_noop(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    record = build_ledger_record(
        _evaluation(),
        run_id="run-1",
        source_commit="deadbeef",
        created_at="2026-09-17T00:00:00+00:00",
    )
    assert record is not None
    existing = replace(record, foreign_5d_ratio=-0.0142028833781292)
    rerun = replace(
        record,
        foreign_5d_ratio=-0.014202883378129225,
        run_id="run-2",
        source_commit="cafebabe",
        created_at="2026-09-17T00:01:00+00:00",
    )
    assert store.add(existing) is True
    before = store.path.read_bytes()

    assert store.add(rerun) is False
    assert store.path.read_bytes() == before


@pytest.mark.parametrize(
    ("field", "existing_value", "rerun_value"),
    [
        ("signal_price", 100.0, 100.00000000001),
        ("raw_score", 52, 52.00000000001),
        ("signal_score", 80.0, 80.00000000001),
        ("foreign_5d_ratio", 0.25, 0.2500000000001),
    ],
)
def test_numeric_signal_round_trip_noise_is_noop(
    tmp_path: Path,
    field: str,
    existing_value: float,
    rerun_value: float,
):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    record = build_ledger_record(
        _evaluation(),
        run_id="run-1",
        source_commit="deadbeef",
        created_at="2026-09-17T00:00:00+00:00",
    )
    assert record is not None
    assert store.add(replace(record, **{field: existing_value})) is True
    before = store.path.read_bytes()

    assert store.add(replace(record, **{field: rerun_value}, run_id="run-2")) is False
    assert store.path.read_bytes() == before


def test_numeric_signal_change_beyond_tolerance_fails_closed(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    record = build_ledger_record(
        _evaluation(),
        run_id="run-1",
        source_commit="deadbeef",
        created_at="2026-09-17T00:00:00+00:00",
    )
    assert record is not None
    assert store.add(record) is True

    with pytest.raises(ExpandedLedgerError, match="conflicting duplicate"):
        store.add(replace(record, signal_score=80.000001, run_id="run-2"))


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_signal_values_fail_closed(tmp_path: Path, non_finite: float):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    record = build_ledger_record(
        _evaluation(),
        run_id="run-1",
        source_commit="deadbeef",
        created_at="2026-09-17T00:00:00+00:00",
    )
    assert record is not None
    invalid = replace(record, signal_score=non_finite)
    assert store.add(invalid) is True

    with pytest.raises(ExpandedLedgerError, match="conflicting duplicate"):
        store.add(replace(invalid, run_id="run-2"))


def test_alphanumeric_ticker_preserved(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))

    store.add_evaluation(_evaluation(ticker="0015N0"), run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00")
    row = store.load().iloc[0]
    assert row["stock_code"] == "0015N0"


def test_existing_row_preserved_on_append(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    store.add_evaluation(_evaluation(ticker="005930"), run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00")
    before = store.load().fillna("").iloc[0].to_dict()

    store.add_evaluation(_evaluation(ticker="0015N0"), run_id="run-2", source_commit="deadbeef", created_at="2026-09-17T00:01:00+00:00")
    after = store.load().fillna("").iloc[0].to_dict()

    assert after == before
    assert len(store.load()) == 2


@pytest.mark.parametrize(
    ("decision", "foreign_status", "exclusion_reason"),
    [
        (DECISION_CANDIDATE, "POSITIVE", None),
        (DECISION_EXCLUDED, "NEGATIVE", EXCLUSION_REASON_FOREIGN_NEGATIVE),
    ],
)
def test_conflicting_duplicate_fails_closed(
    tmp_path: Path,
    decision: str,
    foreign_status: str,
    exclusion_reason: str | None,
):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    original = _evaluation(
        foreign_status=foreign_status,
        decision=decision,
        exclusion_reason=exclusion_reason,
        signal_score=80.0,
    )
    changed = _evaluation(
        foreign_status=foreign_status,
        decision=decision,
        exclusion_reason=exclusion_reason,
        signal_score=81.0,
    )
    store.add_evaluation(original, run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00")

    with pytest.raises(ExpandedLedgerError, match="conflicting duplicate"):
        store.add_evaluation(changed, run_id="run-2", source_commit="cafebabe", created_at="2026-09-17T00:01:00+00:00")


def test_different_engine_version_appends(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    evaluation = _evaluation()

    assert store.add_evaluation(evaluation, run_id="run-1", source_commit="deadbeef", engine_version=ENGINE_VERSION, created_at="2026-09-17T00:00:00+00:00") is True
    assert store.add_evaluation(evaluation, run_id="run-1", source_commit="deadbeef", engine_version="v0.2", created_at="2026-09-17T00:00:00+00:00") is True
    assert len(store.load()) == 2


def test_build_record_rejects_unsupported_exclusion_reason():
    evaluation = _evaluation(foreign_status="NEGATIVE", decision=DECISION_EXCLUDED, exclusion_reason="OTHER")

    with pytest.raises(ExpandedLedgerError):
        build_ledger_record(evaluation, run_id="run-1", source_commit="deadbeef")


def test_default_ledger_path_is_expanded_only(tmp_path: Path):
    paths = _paths(tmp_path)
    store = ExpandedShadowLedgerStore(paths)

    assert store.path == paths.output_root / "expanded_shadow_signal_ledger.csv"


def test_production_shadow_dual_ledgers_not_touched(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    store.add_evaluation(_evaluation(), run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00")

    assert not (tmp_path / "output" / "signals.csv").exists()
    assert not (tmp_path / "output" / "shadow_signal_records.csv").exists()
    assert not (tmp_path / "output" / "dual_shadow_signal_ledger.csv").exists()


def test_custom_path_must_remain_expanded_namespace(tmp_path: Path):
    paths = _paths(tmp_path)

    with pytest.raises(Exception):
        ExpandedShadowLedgerStore(paths, path=tmp_path / "output" / "shadow_signal_records.csv")


def test_append_only_file_contains_no_no_signal_rows(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))
    store.add_evaluation(_evaluation(signal_present=False), run_id="run-1", source_commit="deadbeef")
    store.add_evaluation(_evaluation(), run_id="run-1", source_commit="deadbeef", created_at="2026-09-17T00:00:00+00:00")

    df = store.load()
    assert len(df) == 1
    assert df.iloc[0]["signal_date"] == BAS_DD


def test_store_load_missing_returns_schema(tmp_path: Path):
    store = ExpandedShadowLedgerStore(_paths(tmp_path))

    df = store.load()
    assert df.empty
    assert list(df.columns) == LEDGER_FIELDS
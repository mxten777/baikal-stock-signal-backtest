"""
Shadow STEP 1 — 신규 Signal Shadow Record 저장 기능 단위 테스트.

검증 대상:
  - POSITIVE / NEUTRAL → CANDIDATE 저장
  - NEGATIVE → EXCLUDED 저장 (삭제되지 않음)
  - foreign_status Snapshot 보존
  - 동일 (stock_code, signal_date) 중복 저장 방지
  - 기존 Foreign 판정 로직(classify_flow) 재사용 및 미변경
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts.step1b_flow_verification import classify_flow
from scripts.shadow_step1_track_signals import foreign_status_from_ratio, track_new_signal
from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    EXCLUSION_REASON_FOREIGN_NEGATIVE,
    FOREIGN_STATUS_NEGATIVE,
    FOREIGN_STATUS_NEUTRAL,
    FOREIGN_STATUS_POSITIVE,
    STATUS_OPEN,
    ShadowStore,
    build_shadow_record,
    decide_candidate,
)


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


def _sample_kwargs(**overrides) -> dict:
    base = dict(
        stock_code="005930",
        stock_name="삼성전자",
        market="KS11",
        signal_date="2026-01-05",
        signal_price=70000.0,
        signal_score=80.0,
        foreign_status=FOREIGN_STATUS_POSITIVE,
    )
    base.update(overrides)
    return base


# ─────────────────────────────────────────────
# decide_candidate
# ─────────────────────────────────────────────
class TestDecideCandidate:
    def test_positive_is_candidate(self):
        decision, reason = decide_candidate(FOREIGN_STATUS_POSITIVE)
        assert decision == DECISION_CANDIDATE
        assert reason is None

    def test_neutral_is_candidate(self):
        decision, reason = decide_candidate(FOREIGN_STATUS_NEUTRAL)
        assert decision == DECISION_CANDIDATE
        assert reason is None

    def test_negative_is_excluded(self):
        decision, reason = decide_candidate(FOREIGN_STATUS_NEGATIVE)
        assert decision == DECISION_EXCLUDED
        assert reason == EXCLUSION_REASON_FOREIGN_NEGATIVE

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError):
            decide_candidate("UNKNOWN")


# ─────────────────────────────────────────────
# ShadowStore.record_signal — CANDIDATE / EXCLUDED 저장
# ─────────────────────────────────────────────
class TestShadowStoreDecision:
    def test_positive_saved_as_candidate(self, store):
        record = store.record_signal(**_sample_kwargs(foreign_status=FOREIGN_STATUS_POSITIVE))
        assert record is not None
        assert record.decision == DECISION_CANDIDATE
        assert record.exclusion_reason is None
        saved = store.load()
        assert len(saved) == 1
        assert saved.iloc[0]["decision"] == DECISION_CANDIDATE

    def test_neutral_saved_as_candidate(self, store):
        record = store.record_signal(**_sample_kwargs(foreign_status=FOREIGN_STATUS_NEUTRAL))
        assert record is not None
        assert record.decision == DECISION_CANDIDATE

    def test_negative_saved_as_excluded(self, store):
        record = store.record_signal(**_sample_kwargs(foreign_status=FOREIGN_STATUS_NEGATIVE))
        assert record is not None
        assert record.decision == DECISION_EXCLUDED
        assert record.exclusion_reason == EXCLUSION_REASON_FOREIGN_NEGATIVE

    def test_negative_record_not_deleted(self, store):
        store.record_signal(**_sample_kwargs(foreign_status=FOREIGN_STATUS_NEGATIVE))
        saved = store.load()
        assert len(saved) == 1
        assert saved.iloc[0]["decision"] == DECISION_EXCLUDED
        assert saved.iloc[0]["stock_code"] == "005930"


# ─────────────────────────────────────────────
# Snapshot 보존
# ─────────────────────────────────────────────
class TestSnapshotPreservation:
    def test_foreign_status_snapshot_preserved_in_record(self, store):
        store.record_signal(**_sample_kwargs(foreign_status=FOREIGN_STATUS_NEGATIVE, signal_date="2026-01-05"))
        saved = store.load()
        assert saved.iloc[0]["foreign_status"] == FOREIGN_STATUS_NEGATIVE

    def test_status_defaults_to_open(self, store):
        record = build_shadow_record(**_sample_kwargs())
        assert record.status == STATUS_OPEN

    def test_future_performance_fields_default_none(self, store):
        record = build_shadow_record(**_sample_kwargs())
        assert record.return_5d is None
        assert record.return_10d is None
        assert record.return_20d is None
        assert record.excess_20d is None


# ─────────────────────────────────────────────
# 중복 방지
# ─────────────────────────────────────────────
class TestDuplicatePrevention:
    def test_same_stock_and_signal_date_not_duplicated(self, store):
        first = store.record_signal(**_sample_kwargs())
        second = store.record_signal(**_sample_kwargs())
        assert first is not None
        assert second is None
        assert len(store.load()) == 1

    def test_different_signal_date_is_new_record(self, store):
        store.record_signal(**_sample_kwargs(signal_date="2026-01-05"))
        second = store.record_signal(**_sample_kwargs(signal_date="2026-01-06"))
        assert second is not None
        assert len(store.load()) == 2

    def test_different_stock_same_date_is_new_record(self, store):
        store.record_signal(**_sample_kwargs(stock_code="005930", signal_date="2026-01-05"))
        second = store.record_signal(**_sample_kwargs(stock_code="000660", signal_date="2026-01-05"))
        assert second is not None
        assert len(store.load()) == 2


# ─────────────────────────────────────────────
# 기존 Foreign 판정 로직 재사용 (변경 없음)
# ─────────────────────────────────────────────
class TestReusesExistingForeignLogic:
    def test_foreign_status_from_ratio_matches_classify_flow(self):
        assert foreign_status_from_ratio(0.30) == classify_flow(0.30) == "POSITIVE"
        assert foreign_status_from_ratio(-0.30) == classify_flow(-0.30) == "NEGATIVE"
        assert foreign_status_from_ratio(0.0) == classify_flow(0.0) == "NEUTRAL"

    def test_no_data_ratio_treated_as_neutral(self):
        assert classify_flow(np.nan) == "NO_DATA"
        assert foreign_status_from_ratio(np.nan) == FOREIGN_STATUS_NEUTRAL

    def test_track_new_signal_end_to_end(self, store):
        record = track_new_signal(
            stock_code="005930",
            stock_name="삼성전자",
            market="KS11",
            signal_date="2026-01-05",
            signal_price=70000.0,
            signal_score=80.0,
            foreign_5d_ratio=-0.35,
            store=store,
        )
        assert record is not None
        assert record.foreign_status == "NEGATIVE"
        assert record.decision == DECISION_EXCLUDED


# ─────────────────────────────────────────────
# 기존 Signal 결과 불변 확인 (회귀 방지)
# ─────────────────────────────────────────────
class TestExistingSignalUnaffected:
    def test_step13_stock_selection_score_output_untouched(self):
        from src.config import OUTPUT_DIR

        path = OUTPUT_DIR / "step13_stock_selection_score.csv"
        df = pd.read_csv(path)
        assert len(df) == 289
        assert df["ticker"].nunique() == 20

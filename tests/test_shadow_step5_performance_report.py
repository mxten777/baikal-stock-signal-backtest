"""
Shadow STEP 5 — CANDIDATE / EXCLUDED 누적 성과 리포트 단위 테스트.

검증 대상:
  1. Candidate N 계산
  2. Excluded N 계산
  3. Avg Return
  4. Median Return
  5. Win Rate
  6. Avg Benchmark
  7. Avg Excess
  8. Excess Win Rate
  9. Filter Difference
  10. None/NaN 제외
  11. N < 10 warning (VERY SMALL SAMPLE)
  12. 10 <= N < 30 warning (SMALL SAMPLE)
  13. N >= 30 warning 없음
  14. 20D Candidate N < 30 → INSUFFICIENT SAMPLE
  15. 20D Candidate N >= 30 → SUFFICIENT FOR INITIAL REVIEW
  16. 빈 CSV 처리
  17. 한쪽 그룹만 있는 경우
  18. Shadow CSV 파일 불변 확인 (리포트 생성은 READ-ONLY)
  19. Markdown report 생성 확인
  20. (전체 테스트 PASS는 pytest 전체 실행으로 별도 확인)

네트워크/외부 데이터 호출 없이 fixture DataFrame만 사용한다.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.shadow_performance_report import run_report
from src.shadow_performance import (
    STATUS_INSUFFICIENT_SAMPLE,
    STATUS_SUFFICIENT_FOR_INITIAL_REVIEW,
    WARNING_SMALL_SAMPLE,
    WARNING_VERY_SMALL_SAMPLE,
    compute_20d_summary,
    compute_filter_effect,
    compute_horizon_group_stats,
    compute_operating_status,
    resolve_20d_review_status,
    sample_warning,
)
from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    FOREIGN_STATUS_NEGATIVE,
    FOREIGN_STATUS_POSITIVE,
    SHADOW_RECORD_FIELDS,
    ShadowStore,
)

TICKER = "005930"
SIGNAL_DATE_BASE = "2024-01-01"


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


def _add_record(
    store: ShadowStore,
    index: int,
    foreign_status: str = FOREIGN_STATUS_POSITIVE,
    **perf_fields,
) -> None:
    signal_date = (pd.Timestamp(SIGNAL_DATE_BASE) + pd.Timedelta(days=index)).strftime("%Y-%m-%d")
    store.record_signal(
        stock_code=TICKER,
        stock_name="테스트종목",
        market="KS11",
        signal_date=signal_date,
        signal_price=100.0,
        signal_score=80.0,
        foreign_status=foreign_status,
    )
    if perf_fields:
        store.update_performance({(TICKER, signal_date): perf_fields})


# ──────────────────────────────────────────────
# 1~2. Candidate / Excluded N 계산
# ──────────────────────────────────────────────
def test_candidate_and_excluded_n(store):
    _add_record(store, 0, foreign_status=FOREIGN_STATUS_POSITIVE, return_5d=1.0)
    _add_record(store, 1, foreign_status=FOREIGN_STATUS_POSITIVE, return_5d=2.0)
    _add_record(store, 2, foreign_status=FOREIGN_STATUS_NEGATIVE, return_5d=-1.0)

    records = store.load()
    candidate = compute_horizon_group_stats(records, 5, DECISION_CANDIDATE)
    excluded = compute_horizon_group_stats(records, 5, DECISION_EXCLUDED)

    assert candidate.n == 2
    assert excluded.n == 1


# ──────────────────────────────────────────────
# 3~5. Avg / Median / Win Rate
# ──────────────────────────────────────────────
def test_avg_median_win_rate(store):
    _add_record(store, 0, return_5d=10.0)
    _add_record(store, 1, return_5d=-2.0)
    _add_record(store, 2, return_5d=4.0)

    records = store.load()
    stats = compute_horizon_group_stats(records, 5, DECISION_CANDIDATE)

    assert stats.n == 3
    assert stats.avg_return == pytest.approx((10.0 - 2.0 + 4.0) / 3)
    assert stats.median_return == pytest.approx(4.0)
    assert stats.win_rate == pytest.approx(2 / 3)


def test_win_rate_zero_return_is_not_a_win(store):
    _add_record(store, 0, return_5d=0.0)
    _add_record(store, 1, return_5d=1.0)

    records = store.load()
    stats = compute_horizon_group_stats(records, 5, DECISION_CANDIDATE)

    assert stats.n == 2
    assert stats.win_rate == pytest.approx(0.5)


# ──────────────────────────────────────────────
# 6~8. Avg Benchmark / Avg Excess / Excess Win Rate
# ──────────────────────────────────────────────
def test_avg_benchmark_and_excess(store):
    _add_record(store, 0, return_5d=5.0, benchmark_return_5d=2.0, excess_5d=3.0)
    _add_record(store, 1, return_5d=-1.0, benchmark_return_5d=1.0, excess_5d=-2.0)

    records = store.load()
    stats = compute_horizon_group_stats(records, 5, DECISION_CANDIDATE)

    assert stats.avg_benchmark == pytest.approx(1.5)
    assert stats.avg_excess == pytest.approx(0.5)
    assert stats.excess_win_rate == pytest.approx(0.5)


# ──────────────────────────────────────────────
# 9. Filter Difference
# ──────────────────────────────────────────────
def test_filter_difference(store):
    _add_record(store, 0, foreign_status=FOREIGN_STATUS_POSITIVE, excess_5d=1.4)
    _add_record(store, 1, foreign_status=FOREIGN_STATUS_NEGATIVE, excess_5d=-1.4)

    records = store.load()
    effect = compute_filter_effect(records, 5)

    assert effect["candidate_avg_excess"] == pytest.approx(1.4)
    assert effect["excluded_avg_excess"] == pytest.approx(-1.4)
    assert effect["difference"] == pytest.approx(2.8)


def test_filter_difference_none_when_one_side_missing(store):
    _add_record(store, 0, foreign_status=FOREIGN_STATUS_POSITIVE, excess_5d=1.4)

    records = store.load()
    effect = compute_filter_effect(records, 5)

    assert effect["excluded_avg_excess"] is None
    assert effect["difference"] is None


# ──────────────────────────────────────────────
# 10. None/NaN 제외 (아직 도래하지 않은 horizon 등)
# ──────────────────────────────────────────────
def test_none_horizon_excluded_from_stats(store):
    _add_record(store, 0, return_5d=5.0)  # return_20d는 아직 없음(OPEN 단계)
    _add_record(store, 1, return_5d=3.0, return_10d=6.0, return_20d=9.0)

    records = store.load()
    stats_20d = compute_horizon_group_stats(records, 20, DECISION_CANDIDATE)

    assert stats_20d.n == 1
    assert stats_20d.avg_return == pytest.approx(9.0)


# ──────────────────────────────────────────────
# 11~13. 표본 크기 경고
# ──────────────────────────────────────────────
def test_sample_warning_very_small():
    assert sample_warning(9) == WARNING_VERY_SMALL_SAMPLE


def test_sample_warning_small():
    assert sample_warning(10) == WARNING_SMALL_SAMPLE
    assert sample_warning(29) == WARNING_SMALL_SAMPLE


def test_sample_warning_none_for_large_n():
    assert sample_warning(30) is None
    assert sample_warning(100) is None


# ──────────────────────────────────────────────
# 14~15. 20D Review Status
# ──────────────────────────────────────────────
def test_20d_review_status_insufficient():
    assert resolve_20d_review_status(29) == STATUS_INSUFFICIENT_SAMPLE


def test_20d_review_status_sufficient():
    assert resolve_20d_review_status(30) == STATUS_SUFFICIENT_FOR_INITIAL_REVIEW


def test_20d_summary_uses_candidate_n(store):
    for i in range(30):
        _add_record(store, i, foreign_status=FOREIGN_STATUS_POSITIVE, return_20d=1.0, excess_20d=0.5)

    records = store.load()
    summary = compute_20d_summary(records)

    assert summary["candidate_n"] == 30
    assert summary["review_status"] == STATUS_SUFFICIENT_FOR_INITIAL_REVIEW


# ──────────────────────────────────────────────
# 16. 빈 CSV 처리
# ──────────────────────────────────────────────
def test_empty_records_returns_zero_not_error(store):
    records = store.load()
    assert records.empty

    status = compute_operating_status(records)
    assert status["total"] == 0
    assert status["candidate"] == 0
    assert status["earliest_signal_date"] is None

    stats = compute_horizon_group_stats(records, 5, DECISION_CANDIDATE)
    assert stats.n == 0
    assert stats.avg_return is None


# ──────────────────────────────────────────────
# 17. 한쪽 그룹만 있는 경우
# ──────────────────────────────────────────────
def test_only_candidate_group_present(store):
    _add_record(store, 0, foreign_status=FOREIGN_STATUS_POSITIVE, return_5d=2.0)

    records = store.load()
    excluded = compute_horizon_group_stats(records, 5, DECISION_EXCLUDED)

    assert excluded.n == 0
    assert excluded.avg_return is None


# ──────────────────────────────────────────────
# 18~19. CSV 불변 + Markdown 생성 확인
# ──────────────────────────────────────────────
def test_report_does_not_modify_shadow_csv(store, tmp_path):
    _add_record(store, 0, return_5d=1.0)
    before = store.path.read_bytes()

    report_path = tmp_path / "shadow_performance_report.md"
    run_report(store=store, report_path=report_path)

    after = store.path.read_bytes()
    assert before == after


def test_report_generates_markdown_file(store, tmp_path):
    _add_record(store, 0, return_5d=1.0)
    report_path = tmp_path / "shadow_performance_report.md"

    text = run_report(store=store, report_path=report_path)

    assert report_path.exists()
    saved_text = report_path.read_text(encoding="utf-8")
    assert saved_text == text
    assert "SHADOW OPERATING STATUS" in text
    assert "5D PERFORMANCE" in text
    assert "20D SUMMARY" in text
    assert "BASELINE REFERENCE" in text


def test_report_handles_no_csv_file(tmp_path):
    store = ShadowStore(path=tmp_path / "nonexistent.csv")
    report_path = tmp_path / "report.md"

    text = run_report(store=store, report_path=report_path)

    assert "Total Shadow Records  0" in text
    assert report_path.exists()


def test_shadow_record_fields_untouched_by_import():
    # STEP 5 모듈 import만으로 Shadow Record 스키마가 바뀌지 않는지 확인.
    assert "decision" in SHADOW_RECORD_FIELDS
    assert "return_20d" in SHADOW_RECORD_FIELDS

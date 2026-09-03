"""
Shadow STEP 6 — Shadow Daily Pipeline 통합 테스트.

검증 대상 (기존 STEP 1~5 로직은 재검증하지 않고 orchestration만 검증):
  1. 전체 Phase 순서 (SCAN → FORWARD RETURNS → BENCHMARK/EXCESS → REPORT)
  2. Signal 없음 → 전체 Pipeline PASS
  3. 신규 Candidate 발생 → 저장
  4. 신규 Excluded 발생 → 저장
  5. 동일 Pipeline 2회 실행 → 중복 없음 / 불필요 변경 없음 (Idempotency)
  6. 5D 도래 → Forward Return 업데이트
  7. 10D 도래 → 업데이트
  8. 20D 도래 → COMPLETE
  9. Benchmark / Excess 업데이트
  10. Performance Report 생성
  11. Candidate / Excluded 모두 Report 반영
  12. dry-run → Shadow CSV / 운영 리포트 byte 불변
  13. immutable fields 불변
  14. 기존 값 mismatch → overwrite 없음
  15. unknown market 안전 처리
  16. missing price 안전 처리
  17. empty Shadow Store 정상 처리
  18. 한 Phase 실패 시 FAIL 표시 + 이후 Phase SKIP
  19. 실패 원인 확인 가능 + non-zero exit code
  20. (별도 검증) 기존 전체 테스트 PASS — pytest 전체 실행으로 확인

네트워크/외부 데이터 호출 없이 fixture DataFrame만 사용한다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import scripts.shadow_daily_pipeline as pipeline
from scripts.shadow_daily_pipeline import (
    PHASE_BENCHMARK,
    PHASE_REPORT,
    PHASE_RETURNS,
    PHASE_SCAN,
    PipelineResult,
    PhaseResult,
    main,
    render_summary,
    run_pipeline,
)
from src.shadow_performance import STATUS_INSUFFICIENT_SAMPLE
from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    FOREIGN_STATUS_NEGATIVE,
    FOREIGN_STATUS_POSITIVE,
    IMMUTABLE_FIELDS,
    STATUS_COMPLETE,
    ShadowStore,
)

TICKER = "005930"
TICKER2 = "000660"
TICKERS = {TICKER: "삼성전자", TICKER2: "SK하이닉스"}
SIGNAL_DATE = "2024-01-01"


@pytest.fixture
def store(tmp_path) -> ShadowStore:
    return ShadowStore(path=tmp_path / "shadow_signal_records.csv")


@pytest.fixture
def report_path(tmp_path) -> Path:
    return tmp_path / "shadow_performance_report.md"


# ──────────────────────────────────────────────
# Fixture 데이터 생성 (STEP 2/4 테스트 패턴 재사용)
# ──────────────────────────────────────────────
def _make_signal_price_df(n: int = 90) -> pd.DataFrame:
    """마지막 거래일에만 Signal이 발생하도록 설계된 가격 데이터."""
    closes = [float(100 + i * 0.5) for i in range(n)]
    volume = [500_000] * (n - 1) + [2_000_000]
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": volume,
    })


def _make_flat_price_df(n: int = 90, start: str = "2022-01-01") -> pd.DataFrame:
    """Signal이 전혀 발생하지 않는 평탄한 가격 데이터."""
    closes = [100.0] * n
    return pd.DataFrame({
        "date": pd.date_range(start, periods=n, freq="B"),
        "open": closes,
        "high": [c + 0.1 for c in closes],
        "low": [c - 0.1 for c in closes],
        "close": closes,
        "volume": [500_000] * n,
    })


def _forward_price_df(n_after_signal: int) -> pd.DataFrame:
    """signal_date를 첫 행으로 두고 이후 n거래일을 갖는 평탄 가격 데이터 (Signal 미발생)."""
    return _make_flat_price_df(n=n_after_signal + 1, start=SIGNAL_DATE)


def _benchmark_df(n_after_signal: int, start: str = SIGNAL_DATE) -> pd.DataFrame:
    """close = 1000 * (1 + 0.01 * index) → +N거래일 benchmark_return = N %."""
    dates = pd.date_range(start, periods=n_after_signal + 1, freq="D")
    return pd.DataFrame({
        "date": dates,
        "close": [1000.0 * (1 + 0.01 * i) for i in range(len(dates))],
    })


def _investor_df(dates: pd.DatetimeIndex, foreign_net_buy: float) -> pd.DataFrame:
    return pd.DataFrame({
        "date": dates,
        "foreign_net_buy": [foreign_net_buy] * len(dates),
        "institution_net_buy": [0.0] * len(dates),
    })


def _add_record(
    store: ShadowStore,
    ticker: str = TICKER,
    market: str = "KS11",
    foreign_status: str = FOREIGN_STATUS_POSITIVE,
    signal_date: str = SIGNAL_DATE,
) -> None:
    store.record_signal(
        stock_code=ticker,
        stock_name="테스트종목",
        market=market,
        signal_date=signal_date,
        signal_price=100.0,
        signal_score=80.0,
        foreign_status=foreign_status,
    )


def _run(
    store: ShadowStore,
    report_path: Path,
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    investor_map: dict | None = None,
    raw_map: dict | None = None,
    benchmark_map: dict | None = None,
    dry_run: bool = False,
) -> PipelineResult:
    """외부 호출 없는 fixture 기반 Pipeline 실행 (benchmark_map은 항상 주입해 네트워크 차단)."""
    return run_pipeline(
        store=store,
        tickers=tickers if tickers is not None else TICKERS,
        price_data=price_data if price_data is not None else {},
        investor_map=investor_map if investor_map is not None else {},
        raw_map=raw_map if raw_map is not None else {},
        benchmark_map=benchmark_map if benchmark_map is not None else {},
        report_path=report_path,
        dry_run=dry_run,
    )


def _scan_stats() -> dict:
    return {"checked": 0, "new_signals": 0, "candidate": 0, "excluded": 0,
            "duplicate_skip": 0, "no_data": 0, "signal_base_date": None}


def _return_stats() -> dict:
    return {"total": 0, "checked": 0, "new_5d": 0, "new_10d": 0, "new_20d": 0,
            "complete": 0, "pending": 0, "missing_price": 0, "mismatch": 0, "updated": 0}


def _benchmark_stats() -> dict:
    return {"total": 0, "checked": 0, "kospi": 0, "kosdaq": 0,
            "new_benchmark_5d": 0, "new_benchmark_10d": 0, "new_benchmark_20d": 0,
            "new_excess_5d": 0, "new_excess_10d": 0, "new_excess_20d": 0,
            "missing_benchmark": 0, "unknown_market": 0, "mismatch": 0, "updated": 0}


def _report_stats() -> dict:
    return {"total": 0, "candidate": 0, "excluded": 0, "candidate_n_20d": 0,
            "review_status": STATUS_INSUFFICIENT_SAMPLE, "report_path": "report.md"}


# ──────────────────────────────────────────────
# 1. Phase 실행 순서
# ──────────────────────────────────────────────
def test_phase_execution_order(store, report_path, monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(pipeline, "run_daily_scan",
                        lambda **kw: calls.append("scan") or ({k: v for k, v in _scan_stats().items() if k != "signal_base_date"}, None))
    monkeypatch.setattr(pipeline, "run_update_returns",
                        lambda **kw: calls.append("returns") or _return_stats())
    monkeypatch.setattr(pipeline, "run_update_benchmark",
                        lambda **kw: calls.append("benchmark") or _benchmark_stats())
    monkeypatch.setattr(pipeline, "run_report",
                        lambda **kw: calls.append("report") or "report text")

    result = run_pipeline(store=store, report_path=report_path)

    assert calls == ["scan", "returns", "benchmark", "report"]
    assert [p.title for p in result.phases] == [PHASE_SCAN, PHASE_RETURNS, PHASE_BENCHMARK, PHASE_REPORT]
    assert result.ok


# ──────────────────────────────────────────────
# 2. Signal 없음 → 전체 Pipeline PASS
# ──────────────────────────────────────────────
def test_no_signal_full_pipeline_passes(store, report_path):
    price_data = {TICKER: _make_flat_price_df(), TICKER2: _make_flat_price_df()}
    result = _run(store, report_path, price_data=price_data)

    assert result.ok
    assert all(p.passed for p in result.phases)
    scan = result.phase(PHASE_SCAN).stats
    assert scan["new_signals"] == 0
    assert scan["checked"] == 2
    assert store.load().empty


# ──────────────────────────────────────────────
# 3~4. 신규 Candidate / Excluded 저장
# ──────────────────────────────────────────────
def test_new_candidate_saved(store, report_path):
    df = _make_signal_price_df()
    result = _run(
        store, report_path,
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: df},
        investor_map={TICKER: _investor_df(df["date"], foreign_net_buy=1_000_000)},
        raw_map={TICKER: df},
    )

    assert result.ok
    assert result.phase(PHASE_SCAN).stats["candidate"] == 1
    saved = store.load()
    assert len(saved) == 1
    assert saved.iloc[0]["decision"] == DECISION_CANDIDATE
    assert saved.iloc[0]["foreign_status"] == "POSITIVE"


def test_new_excluded_saved(store, report_path):
    df = _make_signal_price_df()
    result = _run(
        store, report_path,
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: df},
        investor_map={TICKER: _investor_df(df["date"], foreign_net_buy=-1_000_000)},
        raw_map={TICKER: df},
    )

    assert result.ok
    assert result.phase(PHASE_SCAN).stats["excluded"] == 1
    saved = store.load()
    assert len(saved) == 1
    assert saved.iloc[0]["decision"] == DECISION_EXCLUDED
    assert saved.iloc[0]["exclusion_reason"] == "FOREIGN_NEGATIVE"


# ──────────────────────────────────────────────
# 5. Idempotency — 동일 상태 2회 실행
# ──────────────────────────────────────────────
def test_pipeline_rerun_is_idempotent(store, report_path):
    df = _make_signal_price_df()
    benchmark_map = {"KS11": _benchmark_df(30, start=str(df["date"].max().date()))}
    kwargs = dict(
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: df},
        investor_map={TICKER: _investor_df(df["date"], foreign_net_buy=1_000_000)},
        raw_map={TICKER: df},
        benchmark_map=benchmark_map,
    )

    result1 = _run(store, report_path, **kwargs)
    assert result1.ok
    bytes_after_first = store.path.read_bytes()

    result2 = _run(store, report_path, **kwargs)

    assert result2.ok
    assert result2.phase(PHASE_SCAN).stats["duplicate_skip"] == 1
    assert result2.phase(PHASE_SCAN).stats["candidate"] == 0
    assert result2.phase(PHASE_RETURNS).stats["updated"] == 0
    assert result2.phase(PHASE_BENCHMARK).stats["updated"] == 0
    assert len(store.load()) == 1
    # 두 번째 실행은 새 데이터가 없으므로 Shadow CSV가 바이트 단위로 동일해야 한다
    assert store.path.read_bytes() == bytes_after_first


# ──────────────────────────────────────────────
# 6~8. Forward Return 5D / 10D / 20D
# ──────────────────────────────────────────────
def test_forward_5d_updated(store, report_path):
    _add_record(store)
    result = _run(store, report_path, price_data={TICKER: _forward_price_df(6)})

    assert result.ok
    assert result.phase(PHASE_RETURNS).stats["new_5d"] == 1
    row = store.load().iloc[0]
    assert row["return_5d"] == pytest.approx(0.0)
    assert pd.isna(row["return_10d"])


def test_forward_10d_updated(store, report_path):
    _add_record(store)
    result = _run(store, report_path, price_data={TICKER: _forward_price_df(12)})

    assert result.ok
    stats = result.phase(PHASE_RETURNS).stats
    assert stats["new_5d"] == 1
    assert stats["new_10d"] == 1
    row = store.load().iloc[0]
    assert row["return_10d"] == pytest.approx(0.0)
    assert pd.isna(row["return_20d"])


def test_forward_20d_marks_complete(store, report_path):
    _add_record(store)
    result = _run(store, report_path, price_data={TICKER: _forward_price_df(25)})

    assert result.ok
    stats = result.phase(PHASE_RETURNS).stats
    assert stats["new_20d"] == 1
    assert stats["complete"] == 1
    row = store.load().iloc[0]
    assert row["status"] == STATUS_COMPLETE


# ──────────────────────────────────────────────
# 9. Benchmark / Excess 업데이트
# ──────────────────────────────────────────────
def test_benchmark_and_excess_updated(store, report_path):
    _add_record(store)
    result = _run(
        store, report_path,
        price_data={TICKER: _forward_price_df(25)},
        benchmark_map={"KS11": _benchmark_df(25)},
    )

    assert result.ok
    stats = result.phase(PHASE_BENCHMARK).stats
    assert stats["new_benchmark_5d"] == 1
    assert stats["new_benchmark_10d"] == 1
    assert stats["new_benchmark_20d"] == 1
    assert stats["new_excess_5d"] == 1
    row = store.load().iloc[0]
    assert row["benchmark_return_5d"] == pytest.approx(5.0)
    assert row["excess_5d"] == pytest.approx(-5.0)  # stock 0.0% - benchmark 5.0%


# ──────────────────────────────────────────────
# 10~11. Performance Report 생성 + Candidate/Excluded 반영
# ──────────────────────────────────────────────
def test_report_generated(store, report_path):
    _add_record(store)
    result = _run(store, report_path, price_data={TICKER: _forward_price_df(25)})

    assert result.ok
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert len(text) > 0
    assert result.phase(PHASE_REPORT).stats["total"] == 1


def test_report_reflects_candidate_and_excluded(store, report_path):
    _add_record(store, ticker=TICKER, market="KS11", foreign_status=FOREIGN_STATUS_POSITIVE)
    _add_record(store, ticker=TICKER2, market="KQ11", foreign_status=FOREIGN_STATUS_NEGATIVE)
    price_data = {TICKER: _forward_price_df(25), TICKER2: _forward_price_df(25)}
    benchmark_map = {"KS11": _benchmark_df(25), "KQ11": _benchmark_df(25)}

    result = _run(store, report_path, price_data=price_data, benchmark_map=benchmark_map)

    assert result.ok
    report = result.phase(PHASE_REPORT).stats
    assert report["total"] == 2
    assert report["candidate"] == 1
    assert report["excluded"] == 1
    text = report_path.read_text(encoding="utf-8")
    # 리포트는 CANDIDATE / EXCLUDED 두 그룹을 각각 집계하여 표시한다 (title case 라벨)
    assert "Candidate" in text
    assert "Excluded" in text
    assert f"{'':18}{'Candidate':>14}{'Excluded':>14}" in text


# ──────────────────────────────────────────────
# 12. dry-run — Shadow CSV / 운영 리포트 byte 불변
# ──────────────────────────────────────────────
def test_dry_run_keeps_csv_and_report_bytes(store, report_path):
    _add_record(store)
    report_path.write_text("기존 운영 리포트", encoding="utf-8")
    csv_before = store.path.read_bytes()
    report_before = report_path.read_bytes()

    df = _make_signal_price_df()
    result = _run(
        store, report_path,
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: df},
        investor_map={TICKER: _investor_df(df["date"], foreign_net_buy=1_000_000)},
        raw_map={TICKER: df},
        benchmark_map={"KS11": _benchmark_df(25)},
        dry_run=True,
    )

    assert result.ok
    assert result.dry_run
    # dry-run은 신규 Signal을 '탐지'하지만 저장하지 않는다
    assert result.phase(PHASE_SCAN).stats["new_signals"] == 1
    assert store.path.read_bytes() == csv_before
    assert report_path.read_bytes() == report_before


# ──────────────────────────────────────────────
# 13. immutable fields 불변
# ──────────────────────────────────────────────
def test_immutable_fields_unchanged(store, report_path):
    _add_record(store)
    before = store.load()[list(IMMUTABLE_FIELDS)].copy()

    result = _run(
        store, report_path,
        price_data={TICKER: _forward_price_df(25)},
        benchmark_map={"KS11": _benchmark_df(25)},
    )

    assert result.ok
    after = store.load()[list(IMMUTABLE_FIELDS)]
    pd.testing.assert_frame_equal(before, after)


# ──────────────────────────────────────────────
# 14. 기존 값 mismatch → overwrite 없음 (경고이지 실패는 아님)
# ──────────────────────────────────────────────
def test_mismatch_is_not_overwritten(store, report_path):
    _add_record(store)
    store.update_performance({(TICKER, SIGNAL_DATE): {"return_5d": 99.0}})

    result = _run(store, report_path, price_data={TICKER: _forward_price_df(25)})

    assert result.ok
    assert result.phase(PHASE_RETURNS).stats["mismatch"] >= 1
    row = store.load().iloc[0]
    assert row["return_5d"] == pytest.approx(99.0)  # 기존 값 보존


# ──────────────────────────────────────────────
# 15~16. 안전 처리 — unknown market / missing price
# ──────────────────────────────────────────────
def test_unknown_market_handled_safely(store, report_path):
    _add_record(store, market="UNKNOWN_MARKET")
    result = _run(
        store, report_path,
        price_data={TICKER: _forward_price_df(25)},
        benchmark_map={"KS11": _benchmark_df(25)},
    )

    assert result.ok
    stats = result.phase(PHASE_BENCHMARK).stats
    assert stats["unknown_market"] == 1
    row = store.load().iloc[0]
    assert pd.isna(row["benchmark_return_5d"])


def test_missing_price_handled_safely(store, report_path):
    _add_record(store)
    result = _run(store, report_path, price_data={})  # 해당 종목 가격 데이터 없음

    assert result.ok
    assert result.phase(PHASE_RETURNS).stats["missing_price"] == 1
    row = store.load().iloc[0]
    assert pd.isna(row["return_5d"])


# ──────────────────────────────────────────────
# 17. empty Shadow Store 정상 처리
# ──────────────────────────────────────────────
def test_empty_store_pipeline_passes(store, report_path):
    result = _run(store, report_path, price_data={TICKER: _make_flat_price_df()})

    assert result.ok
    report = result.phase(PHASE_REPORT).stats
    assert report["total"] == 0
    assert report["candidate_n_20d"] == 0
    assert report["review_status"] == STATUS_INSUFFICIENT_SAMPLE
    assert "FINAL STATUS: PASS" in render_summary(result)


# ──────────────────────────────────────────────
# 18~19. Failure Isolation
# ──────────────────────────────────────────────
def test_phase_failure_isolates_and_skips_rest(store, report_path, monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("가격 데이터 손상 시뮬레이션")

    monkeypatch.setattr(pipeline, "run_update_returns", _boom)

    result = run_pipeline(
        store=store,
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: _make_flat_price_df()},
        investor_map={},
        raw_map={},
        benchmark_map={},
        report_path=report_path,
    )

    assert not result.ok
    scan, returns, benchmark, report = result.phases
    assert scan.passed is True
    assert returns.passed is False
    assert benchmark.passed is None  # 이전 실패로 미실행
    assert report.passed is None


def test_failure_reason_visible_and_summary_fail(store, report_path, monkeypatch):
    def _boom(**kwargs):
        raise ValueError("benchmark 데이터 정합성 오류")

    monkeypatch.setattr(pipeline, "run_update_benchmark", _boom)

    result = run_pipeline(
        store=store,
        tickers={TICKER: "삼성전자"},
        price_data={TICKER: _make_flat_price_df()},
        investor_map={},
        raw_map={},
        benchmark_map={},
        report_path=report_path,
    )

    failed = result.phase(PHASE_BENCHMARK)
    assert "ValueError" in failed.error
    assert "benchmark 데이터 정합성 오류" in failed.error

    summary = render_summary(result)
    assert f"{PHASE_BENCHMARK} ... FAIL" in summary
    assert "benchmark 데이터 정합성 오류" in summary
    assert "FINAL STATUS: FAIL" in summary


def test_main_exit_code(store, report_path, monkeypatch, capsys):
    ok_result = PipelineResult(
        started_at="2024-01-02T00:00:00",
        dry_run=False,
        phases=[
            PhaseResult(title=PHASE_SCAN, passed=True, stats=_scan_stats()),
            PhaseResult(title=PHASE_RETURNS, passed=True, stats=_return_stats()),
            PhaseResult(title=PHASE_BENCHMARK, passed=True, stats=_benchmark_stats()),
            PhaseResult(title=PHASE_REPORT, passed=True, stats=_report_stats()),
        ],
    )
    fail_result = PipelineResult(
        started_at="2024-01-02T00:00:00",
        dry_run=False,
        phases=[PhaseResult(title=PHASE_SCAN, passed=False, error="RuntimeError: boom")],
    )

    monkeypatch.setattr(pipeline, "run_pipeline", lambda **kw: ok_result)
    assert main([]) == 0

    monkeypatch.setattr(pipeline, "run_pipeline", lambda **kw: fail_result)
    assert main([]) == 1
    assert "FINAL STATUS: FAIL" in capsys.readouterr().out


# ──────────────────────────────────────────────
# 운영 Summary 출력 형식
# ──────────────────────────────────────────────
def test_summary_contains_required_sections(store, report_path):
    _add_record(store)
    result = _run(
        store, report_path,
        price_data={TICKER: _forward_price_df(25)},
        benchmark_map={"KS11": _benchmark_df(25)},
    )
    summary = render_summary(result)

    assert "BAIKAL STOCK SIGNAL" in summary
    assert "v0.2 SHADOW DAILY RUN" in summary
    assert "Dry Run: NO" in summary
    for title in (PHASE_SCAN, PHASE_RETURNS, PHASE_BENCHMARK, PHASE_REPORT):
        assert f"{title} ... PASS" in summary
    for label in ("Stocks scanned", "New Signals", "Candidate", "Excluded",
                  "Duplicate Skip", "NO_DATA"):
        assert label in summary
    for label in ("5D updated", "10D updated", "20D updated",
                  "Complete transitions", "Missing price", "Mismatch"):
        assert label in summary
    for label in ("Benchmark 5D updated", "Excess 20D updated",
                  "Missing benchmark", "Unknown market"):
        assert label in summary
    for label in ("Total Shadow Records", "20D Candidate N", "Review Status", "Report path"):
        assert label in summary
    assert "FINAL STATUS: PASS" in summary

"""
Shadow STEP 6 — Shadow Daily Pipeline 통합 실행 (Orchestration 전용).

목적:
  지금까지 구축한 Shadow 운영체계(STEP 1~5)가 매일 안전하게 반복 실행 가능한지를
  하나의 Pipeline으로 연결해 검증한다. 새로운 전략 기능 개발이 아니라
  Integration / Operation 단계다.

실행 순서 (반드시 이 순서를 유지):
  PHASE 1 DAILY SCAN          → scripts.shadow_daily_scan.run_daily_scan        (STEP 2)
  PHASE 2 FORWARD RETURNS     → scripts.shadow_update_returns.run_update_returns (STEP 3)
  PHASE 3 BENCHMARK / EXCESS  → scripts.shadow_update_benchmark.run_update_benchmark (STEP 4)
  PHASE 4 PERFORMANCE REPORT  → scripts.shadow_performance_report.run_report     (STEP 5)

이번 STEP에서 하지 않는 것:
  - 각 Phase의 계산 로직 재작성 (기존 함수를 import해서 순서 연결만 한다)
  - Signal 조건 / Foreign 기준 / threshold / weight / Candidate-Excluded 규칙 변경
  - Candidate / Excluded 재분류, 과거 Shadow Record 삭제, mismatch 자동 overwrite
  - 새로운 투자 전략 로직, 실매수, 대시보드, 알림, 스케줄러, 과거 데이터 재백테스트

Failure Isolation:
  - Phase별 PASS / FAIL을 명확히 표시하고, 예외를 숨기고 계속 진행하지 않는다.
  - 한 Phase에서 예외가 발생하면 그 시점의 실행은 FAIL로 종료하고 이후 Phase는 SKIP한다.
  - main()은 최종 결과에 따라 exit code 0(PASS) / 1(FAIL)을 반환한다.

dry-run:
  - PHASE 1~3에는 기존 dry-run 동작을 그대로 전달한다 (shadow_signal_records.csv 불변).
  - PHASE 4는 리포트 텍스트 생성까지만 검증하고, 실제 운영 리포트 파일은 덮어쓰지 않는다
    (임시 경로에 작성 후 폐기).

실행: python scripts/shadow_daily_pipeline.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.shadow_daily_scan import run_daily_scan
from scripts.shadow_performance_report import DEFAULT_REPORT_PATH, run_report
from scripts.shadow_update_benchmark import run_update_benchmark
from scripts.shadow_update_returns import run_update_returns
from src.shadow_performance import compute_20d_summary, compute_operating_status
from src.shadow_tracking import ShadowStore

# ──────────────────────────────────────────────
# Phase 정의 (실행 순서 = 정의 순서)
# ──────────────────────────────────────────────
PHASE_SCAN = "PHASE 1 — DAILY SCAN"
PHASE_RETURNS = "PHASE 2 — FORWARD RETURNS"
PHASE_BENCHMARK = "PHASE 3 — BENCHMARK / EXCESS"
PHASE_REPORT = "PHASE 4 — PERFORMANCE REPORT"


@dataclass
class PhaseResult:
    """단일 Phase 실행 결과. passed=None이면 이전 Phase 실패로 미실행(SKIP)."""

    title: str
    passed: bool | None = None
    stats: dict[str, object] = field(default_factory=dict)
    error: str | None = None


@dataclass
class PipelineResult:
    """Pipeline 1회 실행 결과."""

    started_at: str
    dry_run: bool
    phases: list[PhaseResult]

    @property
    def ok(self) -> bool:
        return all(phase.passed for phase in self.phases)

    def phase(self, title: str) -> PhaseResult:
        return next(p for p in self.phases if p.title == title)


# ──────────────────────────────────────────────
# Phase 래퍼 (기존 STEP entry point 재사용)
# ──────────────────────────────────────────────
def _phase_scan(
    store: ShadowStore,
    tickers: dict[str, str] | None,
    price_data: dict[str, pd.DataFrame] | None,
    investor_map: dict[str, pd.DataFrame] | None,
    raw_map: dict[str, pd.DataFrame] | None,
    dry_run: bool,
) -> dict[str, object]:
    stats, run_date = run_daily_scan(
        tickers=tickers,
        price_data=price_data,
        investor_map=investor_map,
        raw_map=raw_map,
        store=store,
        dry_run=dry_run,
    )
    result: dict[str, object] = dict(stats)
    result["signal_base_date"] = run_date.date().isoformat() if run_date is not None else None
    return result


def _phase_forward_returns(
    store: ShadowStore,
    price_data: dict[str, pd.DataFrame] | None,
    dry_run: bool,
) -> dict[str, object]:
    return dict(run_update_returns(store=store, price_map=price_data, dry_run=dry_run))


def _phase_benchmark(
    store: ShadowStore,
    benchmark_map: dict[str, pd.DataFrame] | None,
    dry_run: bool,
) -> dict[str, object]:
    return dict(run_update_benchmark(store=store, benchmark_map=benchmark_map, dry_run=dry_run))


def _phase_report(
    store: ShadowStore,
    report_path: Path,
    dry_run: bool,
) -> dict[str, object]:
    records = store.load()
    operating = compute_operating_status(records)
    summary_20d = compute_20d_summary(records)

    if dry_run:
        # 기존 run_report entry point를 그대로 검증하되, 운영 리포트 파일은 덮어쓰지 않는다.
        with tempfile.TemporaryDirectory(prefix="shadow_report_dryrun_") as tmpdir:
            run_report(store=store, report_path=Path(tmpdir) / "shadow_performance_report.md")
    else:
        run_report(store=store, report_path=report_path)

    return {
        "total": operating["total"],
        "candidate": operating["candidate"],
        "excluded": operating["excluded"],
        "candidate_n_20d": summary_20d["candidate_n"],
        "review_status": summary_20d["review_status"],
        "report_path": str(report_path),
    }


# ──────────────────────────────────────────────
# Pipeline 실행
# ──────────────────────────────────────────────
def run_pipeline(
    store: ShadowStore | None = None,
    tickers: dict[str, str] | None = None,
    price_data: dict[str, pd.DataFrame] | None = None,
    investor_map: dict[str, pd.DataFrame] | None = None,
    raw_map: dict[str, pd.DataFrame] | None = None,
    benchmark_map: dict[str, pd.DataFrame] | None = None,
    report_path: Path = DEFAULT_REPORT_PATH,
    dry_run: bool = False,
) -> PipelineResult:
    """Shadow Daily Pipeline 1회 실행.

    각 Phase는 기존 STEP 2~5의 entry point를 재사용하며, 계산 로직은 전혀 포함하지 않는다.
    한 Phase에서 예외가 발생하면 FAIL로 기록하고 이후 Phase는 실행하지 않는다.
    """
    store = store or ShadowStore()
    result = PipelineResult(
        started_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=dry_run,
        phases=[],
    )

    phase_calls: list[tuple[str, object]] = [
        (PHASE_SCAN, lambda: _phase_scan(store, tickers, price_data, investor_map, raw_map, dry_run)),
        (PHASE_RETURNS, lambda: _phase_forward_returns(store, price_data, dry_run)),
        (PHASE_BENCHMARK, lambda: _phase_benchmark(store, benchmark_map, dry_run)),
        (PHASE_REPORT, lambda: _phase_report(store, report_path, dry_run)),
    ]

    failed = False
    for title, call in phase_calls:
        if failed:
            result.phases.append(PhaseResult(title=title, passed=None))
            continue
        try:
            stats = call()  # type: ignore[operator]
        except Exception as e:  # 예외를 숨기지 않고 Phase 단위로 FAIL 표시
            result.phases.append(
                PhaseResult(title=title, passed=False, error=f"{type(e).__name__}: {e}")
            )
            failed = True
            continue
        result.phases.append(PhaseResult(title=title, passed=True, stats=stats))

    return result


# ──────────────────────────────────────────────
# 운영 Summary 렌더링
# ──────────────────────────────────────────────
def _status_label(passed: bool | None) -> str:
    if passed is None:
        return "SKIP"
    return "PASS" if passed else "FAIL"


def _phase_lines(phase: PhaseResult, dry_run: bool) -> list[str]:
    if phase.passed is not True:
        return []

    s = phase.stats
    if phase.title == PHASE_SCAN:
        lines = [f"  Signal base date: {s.get('signal_base_date') or 'N/A'}"]
        lines += [
            f"  Stocks scanned: {s['checked']}",
            f"  New Signals: {s['new_signals']}",
            f"  Candidate: {s['candidate']}",
            f"  Excluded: {s['excluded']}",
            f"  Duplicate Skip: {s['duplicate_skip']}",
            f"  NO_DATA: {s['no_data']}",
        ]
        return lines
    if phase.title == PHASE_RETURNS:
        return [
            f"  5D updated: {s['new_5d']}",
            f"  10D updated: {s['new_10d']}",
            f"  20D updated: {s['new_20d']}",
            f"  Complete transitions: {s['complete']}",
            f"  Missing price: {s['missing_price']}",
            f"  Mismatch: {s['mismatch']}",
        ]
    if phase.title == PHASE_BENCHMARK:
        return [
            f"  Benchmark 5D updated: {s['new_benchmark_5d']}",
            f"  Benchmark 10D updated: {s['new_benchmark_10d']}",
            f"  Benchmark 20D updated: {s['new_benchmark_20d']}",
            f"  Excess 5D updated: {s['new_excess_5d']}",
            f"  Excess 10D updated: {s['new_excess_10d']}",
            f"  Excess 20D updated: {s['new_excess_20d']}",
            f"  Missing benchmark: {s['missing_benchmark']}",
            f"  Unknown market: {s['unknown_market']}",
            f"  Mismatch: {s['mismatch']}",
        ]
    # PHASE_REPORT
    report_note = " (dry-run: 파일 미작성)" if dry_run else ""
    return [
        f"  Total Shadow Records: {s['total']}",
        f"  Candidate: {s['candidate']}",
        f"  Excluded: {s['excluded']}",
        f"  20D Candidate N: {s['candidate_n_20d']}",
        f"  Review Status: {s['review_status']}",
        f"  Report path: {s['report_path']}{report_note}",
    ]


def render_summary(result: PipelineResult) -> str:
    """Pipeline 실행 결과를 사람이 매일 빠르게 볼 수 있는 형태로 렌더링한다."""
    lines = [
        "=" * 56,
        "BAIKAL STOCK SIGNAL",
        "v0.2 SHADOW DAILY RUN",
        "=" * 56,
        f"Run Date/Time: {result.started_at}",
        f"Dry Run: {'YES' if result.dry_run else 'NO'}",
        "",
    ]
    for phase in result.phases:
        lines.append(f"{phase.title} ... {_status_label(phase.passed)}")
        if phase.error:
            lines.append(f"  Error: {phase.error}")
        if phase.passed is None:
            lines.append("  (이전 Phase 실패로 실행하지 않음)")
        lines.extend(_phase_lines(phase, result.dry_run))
        lines.append("")

    lines.append(f"FINAL STATUS: {'PASS' if result.ok else 'FAIL'}")
    lines.append("=" * 56)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow STEP 6 — Shadow Daily Pipeline 통합 실행 (Orchestration 전용)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="전 Phase를 계산/검증만 수행하고 Shadow CSV와 운영 리포트 파일은 수정하지 않는다.",
    )
    args = parser.parse_args(argv)

    result = run_pipeline(dry_run=args.dry_run)
    print(render_summary(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())

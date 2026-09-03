"""
Shadow STEP 5 — CANDIDATE / EXCLUDED 누적 성과 리포트 (READ-ONLY 분석).

이 모듈에서 하지 않는 것:
  - Shadow Record 수정 (output/shadow_signal_records.csv는 읽기만 한다)
  - Signal / Foreign / Candidate / threshold / weight 변경
  - 통계적 유의성 검정, 새로운 threshold 탐색, 자동 GO/STOP 판정

원칙:
  - 아직 도래하지 않은 horizon은 0으로 계산하지 않고 표본에서 제외한다 (None/NaN 제외).
  - 표본이 적으면 경고만 표시한다 (Signal 판정에는 사용하지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.shadow_tracking import (
    DECISION_CANDIDATE,
    DECISION_EXCLUDED,
    STATUS_5D_DONE,
    STATUS_10D_DONE,
    STATUS_COMPLETE,
    STATUS_OPEN,
)

FORWARD_HORIZONS = (5, 10, 20)

VERY_SMALL_SAMPLE_THRESHOLD = 10
SMALL_SAMPLE_THRESHOLD = 30

WARNING_VERY_SMALL_SAMPLE = "VERY SMALL SAMPLE"
WARNING_SMALL_SAMPLE = "SMALL SAMPLE"

STATUS_INSUFFICIENT_SAMPLE = "INSUFFICIENT SAMPLE"
STATUS_SUFFICIENT_FOR_INITIAL_REVIEW = "SUFFICIENT FOR INITIAL REVIEW"

TWENTY_D_REVIEW_SAMPLE_THRESHOLD = 30

# 과거 검증 결과 (Shadow 계산에 사용하지 않는 참고값 전용)
BASELINE_REFERENCE = {
    "signals": 289,
    "avg_excess_20d": 0.99,
    "win_rate_20d": 52.1,
    "candidate": 252,
    "excluded": 37,
    "candidate_avg_excess_20d": 1.74,
    "candidate_win_rate_20d": 55.2,
}


def _numeric_series(records: pd.DataFrame, column: str) -> pd.Series:
    """column의 값을 숫자로 변환하고 None/NaN을 제외한 Series를 반환한다."""
    if records.empty or column not in records.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(records[column], errors="coerce").dropna()


@dataclass
class StatBlock:
    """단일 지표(N/평균/중앙값/승률)의 계산 결과. 표본이 없으면 None."""

    n: int
    avg: float | None
    median: float | None
    win_rate: float | None


def _stat_block(series: pd.Series) -> StatBlock:
    n = int(len(series))
    if n == 0:
        return StatBlock(n=0, avg=None, median=None, win_rate=None)
    wins = int((series > 0).sum())
    return StatBlock(n=n, avg=float(series.mean()), median=float(series.median()), win_rate=wins / n)


@dataclass
class MeanBlock:
    """단일 평균 지표(N/평균)의 계산 결과. 표본이 없으면 None."""

    n: int
    avg: float | None


def _mean_block(series: pd.Series) -> MeanBlock:
    n = int(len(series))
    if n == 0:
        return MeanBlock(n=0, avg=None)
    return MeanBlock(n=n, avg=float(series.mean()))


@dataclass
class WinRateBlock:
    """비율 지표(N/승률)의 계산 결과. 표본이 없으면 None."""

    n: int
    win_rate: float | None


def _win_rate_block(series: pd.Series) -> WinRateBlock:
    n = int(len(series))
    if n == 0:
        return WinRateBlock(n=0, win_rate=None)
    wins = int((series > 0).sum())
    return WinRateBlock(n=n, win_rate=wins / n)


@dataclass
class HorizonGroupStats:
    """CANDIDATE 또는 EXCLUDED 한쪽의 특정 horizon 성과."""

    horizon: int
    decision: str
    n: int
    avg_return: float | None
    median_return: float | None
    win_rate: float | None
    n_benchmark: int
    avg_benchmark: float | None
    n_excess: int
    avg_excess: float | None
    excess_win_rate: float | None


def compute_operating_status(records: pd.DataFrame) -> dict[str, object]:
    """리포트 1 — 운영 현황 집계."""
    total = int(len(records))
    if total == 0:
        return {
            "total": 0,
            "candidate": 0,
            "excluded": 0,
            "open": 0,
            "5d_done": 0,
            "10d_done": 0,
            "complete": 0,
            "earliest_signal_date": None,
            "latest_signal_date": None,
        }

    decision = records.get("decision", pd.Series(dtype=str))
    status = records.get("status", pd.Series(dtype=str))
    dates = pd.to_datetime(records["signal_date"], errors="coerce") if "signal_date" in records else pd.Series(dtype="datetime64[ns]")
    dates = dates.dropna()

    return {
        "total": total,
        "candidate": int((decision == DECISION_CANDIDATE).sum()),
        "excluded": int((decision == DECISION_EXCLUDED).sum()),
        "open": int((status == STATUS_OPEN).sum()),
        "5d_done": int((status == STATUS_5D_DONE).sum()),
        "10d_done": int((status == STATUS_10D_DONE).sum()),
        "complete": int((status == STATUS_COMPLETE).sum()),
        "earliest_signal_date": dates.min().strftime("%Y-%m-%d") if not dates.empty else None,
        "latest_signal_date": dates.max().strftime("%Y-%m-%d") if not dates.empty else None,
    }


def compute_horizon_group_stats(records: pd.DataFrame, horizon: int, decision: str) -> HorizonGroupStats:
    """리포트 2 — 특정 horizon/decision(CANDIDATE|EXCLUDED)의 성과 지표."""
    group = records[records.get("decision") == decision] if not records.empty else records

    return_stats = _stat_block(_numeric_series(group, f"return_{horizon}d"))
    benchmark_stats = _mean_block(_numeric_series(group, f"benchmark_return_{horizon}d"))
    excess_return_stats = _mean_block(_numeric_series(group, f"excess_{horizon}d"))
    excess_win_stats = _win_rate_block(_numeric_series(group, f"excess_{horizon}d"))

    return HorizonGroupStats(
        horizon=horizon,
        decision=decision,
        n=return_stats.n,
        avg_return=return_stats.avg,
        median_return=return_stats.median,
        win_rate=return_stats.win_rate,
        n_benchmark=benchmark_stats.n,
        avg_benchmark=benchmark_stats.avg,
        n_excess=excess_return_stats.n,
        avg_excess=excess_return_stats.avg,
        excess_win_rate=excess_win_stats.win_rate,
    )


def compute_filter_effect(records: pd.DataFrame, horizon: int) -> dict[str, object]:
    """리포트 3 — Foreign NEGATIVE Filter의 Forward Excess 단순 비교(차이만 계산, 검정 없음)."""
    candidate = compute_horizon_group_stats(records, horizon, DECISION_CANDIDATE)
    excluded = compute_horizon_group_stats(records, horizon, DECISION_EXCLUDED)

    difference = None
    if candidate.avg_excess is not None and excluded.avg_excess is not None:
        difference = candidate.avg_excess - excluded.avg_excess

    return {
        "horizon": horizon,
        "candidate_avg_excess": candidate.avg_excess,
        "candidate_n_excess": candidate.n_excess,
        "excluded_avg_excess": excluded.avg_excess,
        "excluded_n_excess": excluded.n_excess,
        "difference": difference,
    }


def sample_warning(n: int) -> str | None:
    """표본 크기 경고 문구(전략 조건이 아닌 리포트 표시 전용)."""
    if n < VERY_SMALL_SAMPLE_THRESHOLD:
        return WARNING_VERY_SMALL_SAMPLE
    if n < SMALL_SAMPLE_THRESHOLD:
        return WARNING_SMALL_SAMPLE
    return None


def resolve_20d_review_status(candidate_n_20d: int) -> str:
    """20D Candidate 표본 수만으로 리포트 상태 문구를 결정한다 (전략 변경 조건 아님)."""
    if candidate_n_20d < TWENTY_D_REVIEW_SAMPLE_THRESHOLD:
        return STATUS_INSUFFICIENT_SAMPLE
    return STATUS_SUFFICIENT_FOR_INITIAL_REVIEW


def compute_20d_summary(records: pd.DataFrame) -> dict[str, object]:
    """20D 결과 별도 Summary (v0.2 핵심 평가 Horizon)."""
    candidate = compute_horizon_group_stats(records, 20, DECISION_CANDIDATE)
    excluded = compute_horizon_group_stats(records, 20, DECISION_EXCLUDED)
    filter_effect = compute_filter_effect(records, 20)

    return {
        "candidate_n": candidate.n,
        "candidate_avg_return": candidate.avg_return,
        "candidate_win_rate": candidate.win_rate,
        "candidate_avg_excess": candidate.avg_excess,
        "candidate_excess_win_rate": candidate.excess_win_rate,
        "excluded_n": excluded.n,
        "excluded_avg_excess": excluded.avg_excess,
        "filter_difference": filter_effect["difference"],
        "review_status": resolve_20d_review_status(candidate.n),
    }


# ──────────────────────────────────────────────
# 텍스트 렌더링 (콘솔 출력 / Markdown 저장 공용)
# ──────────────────────────────────────────────

def _fmt_pct(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}{suffix}"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%"


def _fmt_n(n: int) -> str:
    return f"{n}"


def _fmt_date(value: str | None) -> str:
    return value if value is not None else "N/A"


def _render_operating_status(status: dict[str, object]) -> list[str]:
    lines = [
        "SHADOW OPERATING STATUS",
        "-" * 40,
        f"Total Records        {status['total']}",
        f"Candidate            {status['candidate']}",
        f"Excluded             {status['excluded']}",
        f"Open                 {status['open']}",
        f"5D Done              {status['5d_done']}",
        f"10D Done             {status['10d_done']}",
        f"Complete             {status['complete']}",
        f"Earliest Signal Date {_fmt_date(status['earliest_signal_date'])}",
        f"Latest Signal Date   {_fmt_date(status['latest_signal_date'])}",
    ]
    return lines


def _render_horizon_table(records: pd.DataFrame, horizon: int) -> list[str]:
    candidate = compute_horizon_group_stats(records, horizon, DECISION_CANDIDATE)
    excluded = compute_horizon_group_stats(records, horizon, DECISION_EXCLUDED)

    rows = [
        ("N", _fmt_n(candidate.n), _fmt_n(excluded.n)),
        ("Avg Return", _fmt_pct(candidate.avg_return), _fmt_pct(excluded.avg_return)),
        ("Median Return", _fmt_pct(candidate.median_return), _fmt_pct(excluded.median_return)),
        ("Win Rate", _fmt_rate(candidate.win_rate), _fmt_rate(excluded.win_rate)),
        ("Avg Benchmark", _fmt_pct(candidate.avg_benchmark), _fmt_pct(excluded.avg_benchmark)),
        ("Avg Excess", _fmt_pct(candidate.avg_excess), _fmt_pct(excluded.avg_excess)),
        ("Excess Win Rate", _fmt_rate(candidate.excess_win_rate), _fmt_rate(excluded.excess_win_rate)),
    ]
    lines = [f"{horizon}D PERFORMANCE", "-" * 40, f"{'':18}{'Candidate':>14}{'Excluded':>14}"]
    for label, cand_val, excl_val in rows:
        lines.append(f"{label:18}{cand_val:>14}{excl_val:>14}")

    for group_label, n in (("Candidate", candidate.n), ("Excluded", excluded.n)):
        warning = sample_warning(n)
        if warning is not None:
            lines.append(f"  ! {group_label} N={n} -> {warning}")
    return lines


def _render_filter_effect(records: pd.DataFrame, horizon: int) -> list[str]:
    effect = compute_filter_effect(records, horizon)
    return [
        f"{horizon}D Filter Effect",
        "-" * 40,
        f"Candidate Avg Excess   {_fmt_pct(effect['candidate_avg_excess'])}",
        f"Excluded Avg Excess    {_fmt_pct(effect['excluded_avg_excess'])}",
        f"Difference             {_fmt_pct(effect['difference'], suffix='%p')}",
    ]


def _render_20d_summary(records: pd.DataFrame) -> list[str]:
    summary = compute_20d_summary(records)
    return [
        "20D SUMMARY (v0.2 핵심 평가 Horizon)",
        "-" * 40,
        f"Candidate N             {_fmt_n(summary['candidate_n'])}",
        f"Candidate Avg Return    {_fmt_pct(summary['candidate_avg_return'])}",
        f"Candidate Win Rate      {_fmt_rate(summary['candidate_win_rate'])}",
        f"Candidate Avg Excess    {_fmt_pct(summary['candidate_avg_excess'])}",
        f"Candidate Excess WinRate{_fmt_rate(summary['candidate_excess_win_rate'])}",
        f"Excluded N              {_fmt_n(summary['excluded_n'])}",
        f"Excluded Avg Excess     {_fmt_pct(summary['excluded_avg_excess'])}",
        f"Filter Difference       {_fmt_pct(summary['filter_difference'], suffix='%p')}",
        f"Review Status           {summary['review_status']}",
    ] + [
        f"  ! {group_label} N={n} -> {warning}"
        for group_label, n in (("Candidate", summary['candidate_n']), ("Excluded", summary['excluded_n']))
        for warning in (sample_warning(n),)
        if warning is not None
    ]


def _render_baseline_reference() -> list[str]:
    ref = BASELINE_REFERENCE
    return [
        "BASELINE REFERENCE (참고값 전용, Shadow 계산에 사용하지 않음)",
        "-" * 40,
        f"Baseline Signals              {ref['signals']}",
        f"Baseline Avg Excess 20D       {_fmt_pct(ref['avg_excess_20d'], suffix='%p')}",
        f"Baseline Win Rate 20D         {ref['win_rate_20d']:.1f}%",
        f"Foreign Filter Candidate      {ref['candidate']}",
        f"Foreign Filter Excluded       {ref['excluded']}",
        f"Candidate Avg Excess 20D      {_fmt_pct(ref['candidate_avg_excess_20d'], suffix='%p')}",
        f"Candidate Win Rate 20D        {ref['candidate_win_rate_20d']:.1f}%",
    ]


def render_report(records: pd.DataFrame, generated_at: str) -> str:
    """전체 리포트를 콘솔/Markdown 공용 텍스트로 렌더링한다 (READ-ONLY, records는 수정하지 않음)."""
    status = compute_operating_status(records)

    lines: list[str] = [
        "BAIKAL Shadow Performance Report (STEP 5)",
        "=" * 40,
        f"Generated At          {generated_at}",
        f"Total Shadow Records  {status['total']}",
        f"Latest Signal Date    {_fmt_date(status['latest_signal_date'])}",
        "",
    ]
    lines += _render_operating_status(status)
    lines.append("")

    for horizon in FORWARD_HORIZONS:
        lines += _render_horizon_table(records, horizon)
        lines.append("")

    for horizon in FORWARD_HORIZONS:
        lines += _render_filter_effect(records, horizon)
        lines.append("")

    lines += _render_20d_summary(records)
    lines.append("")

    lines += _render_baseline_reference()
    lines.append("")

    return "\n".join(lines)

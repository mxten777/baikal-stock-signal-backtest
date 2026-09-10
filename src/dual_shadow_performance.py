"""
src/dual_shadow_performance.py
================================
DUAL Shadow STEP 5 — Baseline vs Challenger 성과 비교 집계 계층 (READ-ONLY).

목적:
  STEP 3 Comparison Ledger(output/dual_shadow_signal_ledger.csv)의 당시 판단과
  STEP 4 Forward Return Evidence(output/dual_shadow_forward_returns.csv)를
  읽어서 Baseline(v0.1)과 Challenger(v0.2)의 사후 성과를 동일 조건에서 비교 집계한다.

이번 STEP에서 하는 것: Read → Aggregate → Compare 만 수행한다.
이번 STEP에서 하지 않는 것:
  - Signal 재계산 / Ledger 수정 / Forward Return 재계산
  - Scheduler 연결, Dashboard/API 변경
  - 자동 승자 판정(Challenger WIN / Baseline WIN / GO / STOP)

시장 Forward Return은 comparison_group당 하나이며, 각 엔진은
baseline_signal_present / challenger_signal_present == True인 evidence만
자신의 성과 표본에 포함한다 (BOTH_YES는 두 엔진 표본 모두에 동일 forward_return이 들어간다).

Win 정의: forward_return > 0 (기존 프로젝트에 별도 Win 정의가 없어 이 값을 채택).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import OUTPUT_DIR
from src.dual_shadow_forward_returns import (
    DEFAULT_FORWARD_RETURN_PATH,
    DualForwardReturnStore,
)
from src.dual_shadow_ledger import (
    BASELINE_ENGINE_VERSION,
    CHALLENGER_ENGINE_VERSION,
    DEFAULT_LEDGER_PATH,
    DualShadowLedgerStore,
)
from src.shadow_tracking import FORWARD_HORIZONS

WIN_DEFINITION = "forward_return > 0"

STATUS_OK = "OK"
STATUS_NO_AVAILABLE_EVIDENCE = "NO_AVAILABLE_EVIDENCE"

VALID_COMPARISON_GROUPS = {"BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY", "BOTH_NO"}
SIGNAL_PRODUCING_GROUPS = ("BOTH_YES", "BASELINE_ONLY", "CHALLENGER_ONLY")

REQUIRED_FORWARD_RETURN_COLUMNS = [
    "trade_date",
    "stock_code",
    "stock_name",
    "evaluation_close",
    "baseline_signal_present",
    "challenger_signal_present",
    "comparison_group",
    "horizon",
    "forward_return",
    "return_status",
    "baseline_engine_version",
    "challenger_engine_version",
]

DEFAULT_PERFORMANCE_SUMMARY_PATH = OUTPUT_DIR / "dual_shadow_performance_summary.json"


class EvidenceIntegrityError(ValueError):
    """Evidence(STEP 4 Forward Return) 무결성 검증 실패 시 fail-closed로 발생시키는 예외."""


class MalformedEvidenceError(EvidenceIntegrityError):
    """필수 컬럼 누락 / malformed horizon / invalid signal flag 등 구조적 결함."""


class DuplicateEvidenceError(EvidenceIntegrityError):
    """동일 comparison key(trade_date+stock_code+engine versions+horizon) 중복."""


class EngineVersionMismatchError(EvidenceIntegrityError):
    """기대 엔진 버전과 다른 baseline/challenger engine version이 evidence에 존재."""


class InvalidComparisonGroupError(EvidenceIntegrityError):
    """comparison_group이 4개 정의된 값(BOTH_YES/BASELINE_ONLY/CHALLENGER_ONLY/BOTH_NO)이 아님."""


class MissingJoinSourceError(EvidenceIntegrityError):
    """STEP 4 Forward Return이 참조하는 STEP 3 Ledger row/파일이 없음."""


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise MalformedEvidenceError(f"invalid signal flag 값: {value!r}")


def validate_forward_return_evidence(
    df: pd.DataFrame,
    baseline_engine_version: str = BASELINE_ENGINE_VERSION,
    challenger_engine_version: str = CHALLENGER_ENGINE_VERSION,
) -> None:
    """STEP 4 Forward Return DataFrame의 구조/값 무결성을 fail-closed로 검증한다.

    df가 비어 있으면 검증할 것이 없으므로 통과시킨다 (empty는 호출부에서
    NO_AVAILABLE_EVIDENCE로 별도 처리).
    """
    if df is None or df.empty:
        return

    missing = [c for c in REQUIRED_FORWARD_RETURN_COLUMNS if c not in df.columns]
    if missing:
        raise MalformedEvidenceError(f"Forward Return evidence에 필수 컬럼이 없습니다: {missing}")

    for horizon in df["horizon"]:
        try:
            h = int(horizon)
        except (TypeError, ValueError):
            raise MalformedEvidenceError(f"malformed horizon 값: {horizon!r}") from None
        if h not in FORWARD_HORIZONS:
            raise MalformedEvidenceError(f"malformed horizon 값: {horizon!r} (허용: {FORWARD_HORIZONS})")

    for flag_col in ("baseline_signal_present", "challenger_signal_present"):
        for value in df[flag_col]:
            _to_bool(value)

    for group in df["comparison_group"]:
        if group not in VALID_COMPARISON_GROUPS:
            raise InvalidComparisonGroupError(f"잘못된 comparison_group: {group!r}")

    available_mask = df["return_status"].astype(str) == "AVAILABLE"
    if available_mask.any():
        missing_return = df.loc[available_mask, "forward_return"].isna()
        if missing_return.any():
            raise MalformedEvidenceError("return_status=AVAILABLE인데 forward_return이 없는 evidence가 있습니다.")

    key_cols = [
        "trade_date",
        "stock_code",
        "baseline_engine_version",
        "challenger_engine_version",
        "horizon",
    ]
    dup_mask = df.duplicated(subset=key_cols, keep=False)
    if dup_mask.any():
        dup_keys = df.loc[dup_mask, key_cols].drop_duplicates().to_dict(orient="records")
        raise DuplicateEvidenceError(f"duplicate evidence key 발견: {dup_keys}")

    bad_baseline = set(df["baseline_engine_version"].astype(str).unique()) - {baseline_engine_version}
    bad_challenger = set(df["challenger_engine_version"].astype(str).unique()) - {challenger_engine_version}
    if bad_baseline or bad_challenger:
        raise EngineVersionMismatchError(
            f"engine version mismatch: baseline={sorted(bad_baseline)} "
            f"(expected {baseline_engine_version}), challenger={sorted(bad_challenger)} "
            f"(expected {challenger_engine_version})"
        )


def validate_join_with_ledger(
    forward_df: pd.DataFrame,
    ledger_df: pd.DataFrame | None,
) -> None:
    """Forward Return row가 참조하는 STEP 3 Ledger row 존재/일관성을 검증한다.

    forward_df가 비어 있으면 검증할 것이 없다. forward_df가 non-empty인데 ledger가
    비어있거나 없으면 필요한 join source 누락으로 fail-closed 처리한다.
    """
    if forward_df is None or forward_df.empty:
        return
    if ledger_df is None or ledger_df.empty:
        raise MissingJoinSourceError("Forward Return evidence가 존재하지만 STEP 3 Ledger를 찾을 수 없습니다.")

    ledger_key_cols = [
        "trade_date",
        "stock_code",
        "baseline_engine_version",
        "challenger_engine_version",
    ]
    missing_ledger_cols = [c for c in ledger_key_cols + ["comparison_group", "evaluation_close"] if c not in ledger_df.columns]
    if missing_ledger_cols:
        raise MissingJoinSourceError(f"STEP 3 Ledger에 필수 join 컬럼이 없습니다: {missing_ledger_cols}")

    ledger_lookup: dict[tuple[str, str, str, str], str] = {}
    for _, ledger_row in ledger_df.iterrows():
        key = tuple(str(ledger_row[c]) for c in ledger_key_cols)
        ledger_lookup[key] = str(ledger_row["comparison_group"])

    for _, row in forward_df.iterrows():
        key = tuple(str(row[c]) for c in ledger_key_cols)
        if key not in ledger_lookup:
            raise MissingJoinSourceError(f"Forward Return evidence key가 STEP 3 Ledger에 없습니다: {key}")
        if ledger_lookup[key] != str(row["comparison_group"]):
            raise MissingJoinSourceError(f"comparison_group join 불일치: {key}")


def _stats_from_returns(returns: list[float]) -> dict[str, object]:
    count = len(returns)
    if count == 0:
        return {
            "signal_count": 0,
            "avg_return": None,
            "median_return": None,
            "win_count": None,
            "win_rate": None,
            "loss_count": None,
            "best_return": None,
            "worst_return": None,
        }
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    return {
        "signal_count": count,
        "avg_return": statistics.fmean(returns),
        "median_return": statistics.median(returns),
        "win_count": len(wins),
        "win_rate": len(wins) / count * 100.0,
        "loss_count": len(losses),
        "best_return": max(returns),
        "worst_return": min(returns),
    }


def compute_horizon_engine_stats(df_horizon: pd.DataFrame) -> dict[str, dict[str, object]]:
    """단일 horizon df에 대해 baseline/challenger 각각의 성과 표본 통계를 계산한다."""
    baseline_mask = df_horizon["baseline_signal_present"].apply(_to_bool)
    challenger_mask = df_horizon["challenger_signal_present"].apply(_to_bool)

    baseline_returns = df_horizon.loc[baseline_mask, "forward_return"].astype(float).tolist()
    challenger_returns = df_horizon.loc[challenger_mask, "forward_return"].astype(float).tolist()

    return {
        "baseline": _stats_from_returns(baseline_returns),
        "challenger": _stats_from_returns(challenger_returns),
    }


def compute_comparison_group_stats(df_horizon: pd.DataFrame) -> dict[str, dict[str, object]]:
    """단일 horizon df에 대해 comparison_group별 표본수/성과를 계산한다.

    BOTH_NO는 표본수만 보존하고(엔진 성과 표본에서는 이미 제외됨) 별도 평균/승률은 계산하지 않는다.
    """
    result: dict[str, dict[str, object]] = {}
    for group in VALID_COMPARISON_GROUPS:
        sub = df_horizon[df_horizon["comparison_group"] == group]
        count = len(sub)
        if group in SIGNAL_PRODUCING_GROUPS:
            returns = sub["forward_return"].astype(float).tolist()
            stats = _stats_from_returns(returns)
            result[group] = {
                "count": count,
                "avg_return": stats["avg_return"],
                "median_return": stats["median_return"],
                "win_rate": stats["win_rate"],
            }
        else:
            result[group] = {"count": count}
    return result


def compute_delta(baseline_stats: dict[str, object], challenger_stats: dict[str, object]) -> dict[str, object]:
    """Challenger - Baseline delta를 계산한다. 한쪽 표본이 0이면 해당 delta는 None(N/A)."""
    if baseline_stats["signal_count"] == 0 or challenger_stats["signal_count"] == 0:
        return {
            "avg_return_delta": None,
            "median_return_delta": None,
            "win_rate_delta": None,
            "signal_count_delta": challenger_stats["signal_count"] - baseline_stats["signal_count"],
        }
    return {
        "avg_return_delta": challenger_stats["avg_return"] - baseline_stats["avg_return"],
        "median_return_delta": challenger_stats["median_return"] - baseline_stats["median_return"],
        "win_rate_delta": challenger_stats["win_rate"] - baseline_stats["win_rate"],
        "signal_count_delta": challenger_stats["signal_count"] - baseline_stats["signal_count"],
    }


@dataclass
class DualShadowPerformanceSummary:
    generated_at: str
    status: str
    win_definition: str
    engine_versions: dict[str, str]
    total_evidence_rows: int
    horizons: dict[str, dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "win_definition": self.win_definition,
            "engine_versions": self.engine_versions,
            "total_evidence_rows": self.total_evidence_rows,
            "horizons": self.horizons,
        }


def _empty_summary(
    baseline_engine_version: str,
    challenger_engine_version: str,
    created_at: str | None,
) -> DualShadowPerformanceSummary:
    empty_engine_stats = _stats_from_returns([])
    empty_group_stats = {
        group: (
            {"count": 0, "avg_return": None, "median_return": None, "win_rate": None}
            if group in SIGNAL_PRODUCING_GROUPS
            else {"count": 0}
        )
        for group in VALID_COMPARISON_GROUPS
    }
    empty_delta = {
        "avg_return_delta": None,
        "median_return_delta": None,
        "win_rate_delta": None,
        "signal_count_delta": 0,
    }
    horizons = {
        str(h): {
            "baseline": empty_engine_stats,
            "challenger": empty_engine_stats,
            "comparison_groups": empty_group_stats,
            "delta": empty_delta,
        }
        for h in FORWARD_HORIZONS
    }
    return DualShadowPerformanceSummary(
        generated_at=created_at or datetime.now(timezone.utc).isoformat(),
        status=STATUS_NO_AVAILABLE_EVIDENCE,
        win_definition=WIN_DEFINITION,
        engine_versions={"baseline": baseline_engine_version, "challenger": challenger_engine_version},
        total_evidence_rows=0,
        horizons=horizons,
    )


def build_performance_summary(
    ledger_store: DualShadowLedgerStore | None = None,
    forward_store: DualForwardReturnStore | None = None,
    baseline_engine_version: str = BASELINE_ENGINE_VERSION,
    challenger_engine_version: str = CHALLENGER_ENGINE_VERSION,
    created_at: str | None = None,
) -> DualShadowPerformanceSummary:
    """STEP 3 Ledger(join 검증용) + STEP 4 Forward Return을 READ-ONLY로 읽어 성과를 집계한다.

    Forward Return evidence가 없으면(파일 부재 또는 empty) 정상 NO_AVAILABLE_EVIDENCE
    상태의 empty summary를 반환한다 (오류 아님).
    """
    ledger_store = ledger_store or DualShadowLedgerStore(path=DEFAULT_LEDGER_PATH)
    forward_store = forward_store or DualForwardReturnStore(path=DEFAULT_FORWARD_RETURN_PATH)

    forward_df = forward_store.load()

    if forward_df is None or forward_df.empty:
        return _empty_summary(baseline_engine_version, challenger_engine_version, created_at)

    validate_forward_return_evidence(
        forward_df,
        baseline_engine_version=baseline_engine_version,
        challenger_engine_version=challenger_engine_version,
    )

    ledger_df = ledger_store.load()
    validate_join_with_ledger(forward_df, ledger_df)

    horizons: dict[str, dict[str, object]] = {}
    for horizon in FORWARD_HORIZONS:
        df_h = forward_df[forward_df["horizon"].astype(int) == horizon]
        engine_stats = compute_horizon_engine_stats(df_h)
        group_stats = compute_comparison_group_stats(df_h)
        delta = compute_delta(engine_stats["baseline"], engine_stats["challenger"])
        horizons[str(horizon)] = {
            "baseline": engine_stats["baseline"],
            "challenger": engine_stats["challenger"],
            "comparison_groups": group_stats,
            "delta": delta,
        }

    return DualShadowPerformanceSummary(
        generated_at=created_at or datetime.now(timezone.utc).isoformat(),
        status=STATUS_OK,
        win_definition=WIN_DEFINITION,
        engine_versions={"baseline": baseline_engine_version, "challenger": challenger_engine_version},
        total_evidence_rows=int(len(forward_df)),
        horizons=horizons,
    )

"""User-facing labels and compact report summaries for existing Expanded evidence."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dashboard.expanded_evidence import ExpandedSignalRecord


_SIGNAL_REASON_PATTERN = re.compile(
    r"^Score crossed threshold: ([0-9]+(?:\.[0-9]+)?) -> ([0-9]+(?:\.[0-9]+)?) "
    r"\(threshold ([0-9]+(?:\.[0-9]+)?)\)$"
)
_CANDIDATE_REASON_PATTERN = re.compile(
    r"^Foreign status .+ is not NEGATIVE; existing rule classifies as CANDIDATE\.$"
)
_EVIDENCE_STATUS_LABELS = {
    "AVAILABLE": "근거 확인 완료",
    "PARTIAL": "일부 근거 확인",
    "UNAVAILABLE": "근거 확인 불가",
}
_TRACKING_STATUS_LABELS = {
    "OPEN": "성과 측정 중",
    "5D": "5거래일 성과 확인",
    "10D": "10거래일 성과 확인",
    "20D": "20거래일 성과 확인",
    "COMPLETE": "성과 추적 완료",
}


def display_signal_reason(
    reason: str | None,
    prev_score: float | None,
    current_score: float | None,
) -> str:
    if reason is None:
        return "—"
    match = _SIGNAL_REASON_PATTERN.fullmatch(reason)
    if match is None or prev_score is None or current_score is None:
        return reason
    threshold = float(match.group(3))
    return f"점수가 기준 {threshold:g}를 상향 돌파: {prev_score:.1f} → {current_score:.1f}"


def display_decision_reason(reason: str | None) -> str:
    if reason is None:
        return "—"
    if _CANDIDATE_REASON_PATTERN.fullmatch(reason):
        return "외국인 수급이 NEGATIVE가 아니므로 CANDIDATE로 분류"
    if reason == "FOREIGN_NEGATIVE":
        return "외국인 수급이 NEGATIVE여서 EXCLUDED로 분류"
    return reason


def display_evidence_status(status: str) -> str:
    return _EVIDENCE_STATUS_LABELS.get(status, status)


def display_tracking_status(status: str) -> str:
    return _TRACKING_STATUS_LABELS.get(status, status)


def candidate_summary_rows(record: ExpandedSignalRecord | None) -> tuple[tuple[str, str], ...]:
    """Project existing evidence only; incomplete evidence never asserts a crossing."""
    profile = record.profile if record else None
    business = profile.main_business_products if profile else None
    short_business = business[:60] + "…" if business and len(business) > 60 else business
    summary = "UNAVAILABLE · 근거 확인 불가"
    if record:
        evidence = record.evidence
        foreign = f"외국인 {evidence.foreign_status or '—'}"
        summary = f"{evidence.evidence_status} · {display_evidence_status(evidence.evidence_status)} · {foreign}"
        match = _SIGNAL_REASON_PATTERN.fullmatch(evidence.signal_reason or "")
        if (
            evidence.evidence_status == "AVAILABLE"
            and match
            and evidence.prev_score is not None
            and evidence.current_score is not None
        ):
            summary = (
                f"점수 {evidence.prev_score:.1f} → {evidence.current_score:.1f}, "
                f"기준 {float(match.group(3)):g} 상향 돌파 · {foreign}"
            )
    return (
        ("업종", (profile.sector or "—") if profile else "—"),
        ("주요사업", short_business or "—"),
        ("선정근거 요약", summary),
    )
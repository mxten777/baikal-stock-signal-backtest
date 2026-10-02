"""User-facing labels for existing Expanded evidence values."""

from __future__ import annotations

import re


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
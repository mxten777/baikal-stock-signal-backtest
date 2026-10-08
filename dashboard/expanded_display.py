"""User-facing labels and compact report summaries for existing Expanded evidence."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
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


def display_score_movement(
    prev_score: float | None,
    current_score: float | None,
    delta_score: float | None,
) -> str:
    previous = f"{prev_score:.1f}" if prev_score is not None else "N/A"
    current = f"{current_score:.1f}" if current_score is not None else "N/A"
    movement = f"{previous} → {current}"
    if delta_score is not None and prev_score is not None and current_score is not None:
        return f"{movement} ({delta_score:+.1f})"
    return movement


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
            movement = (
                f"{evidence.prev_score:.1f} → {evidence.current_score:.1f}"
                f" ({evidence.delta_score:+.1f})"
                if evidence.delta_score is not None
                else f"{evidence.prev_score:.1f} → {evidence.current_score:.1f}"
            )
            summary = (
                f"점수 {movement}, "
                f"기준 {float(match.group(3)):g} 상향 돌파 · {foreign}"
            )
        if record.signal_type == "OVERHEATED":
            summary = f"{summary} · OVERHEATED"
    return (
        ("업종", (profile.sector or "—") if profile else "—"),
        ("주요사업", short_business or "—"),
        ("선정근거 요약", summary),
    )


@dataclass(frozen=True)
class EasyStockAnalysis:
    summary: str
    positives: tuple[str, ...]
    risks: tuple[str, ...]
    checks: tuple[str, ...]
    sources: tuple[str, ...]
    method: str
    disclaimer: str


def build_easy_stock_analysis(record: ExpandedSignalRecord) -> EasyStockAnalysis:
    """Explain stored evidence without generating facts or changing decisions."""
    evidence = record.evidence
    profile = record.profile
    verified = evidence.evidence_status == "AVAILABLE"
    positives: list[str] = []
    risks = ["점수는 상승 확률이 아니며, 가격·거래량 지표만으로 향후 수익이나 기업 가치를 확정할 수 없습니다."]
    checks = ["최신 실적·재무건전성·공시·뉴스·가격 변동의 원인은 이번 설명에서 확인하지 않았습니다. 별도로 확인해야 합니다."]
    score = record.signal_score
    score_text = f"{score:.1f}점" if score is not None and math.isfinite(score) else "확인 불가"
    summary = (
        f"저장된 Signal Score는 {score_text}, Signal은 {record.signal_type or '확인 불가'}, "
        f"판정은 {evidence.decision or '확인 불가'}입니다. "
        f"기술적 근거 상태: {display_evidence_status(evidence.evidence_status)}."
    )
    match = _SIGNAL_REASON_PATTERN.fullmatch(evidence.signal_reason or "")
    previous, current = evidence.prev_score, evidence.current_score
    if (
        verified and match and previous is not None and current is not None
        and math.isfinite(previous) and math.isfinite(current)
        and previous == float(match.group(1)) and current == float(match.group(2))
        and current == score and previous < float(match.group(3)) <= current
    ):
        summary += f" 점수가 {previous:.1f}에서 {current:.1f}로 올라 기존 관심 기준 {float(match.group(3)):g}점을 새로 충족했습니다."
    elif verified:
        checks.append("기준 상향 돌파를 설명할 전일·당일 점수와 발생 이유의 일치 여부를 확인해야 합니다.")
    if not verified:
        risks.append("기술적 근거를 완전히 확인하지 못했으므로 점수 상승·추세 개선을 확정적으로 해석하지 않습니다.")
        checks.append("시세 스냅샷과 저장 점수·Signal 발생 조건의 일치 여부를 확인해야 합니다.")

    for label, value, maximum in (
        ("추세", evidence.trend_score, 25),
        ("거래량", evidence.volume_score, 20),
        ("모멘텀", evidence.momentum_score, 20),
    ):
        if value is None or not math.isfinite(value) or not 0 <= value <= maximum:
            checks.append(f"{label} 구성점수는 확인 불가입니다.")
        elif verified:
            if label == "추세" and value == maximum:
                positives.append("추세 25/25점: 단기 평균가격이 중기 평균보다 높고, 중기 평균도 장기 평균보다 높습니다. 종가는 중기 평균 위에 있고 중기 평균은 전일보다 올랐습니다.")
            elif label == "거래량" and value == maximum:
                positives.append("거래량 20/20점: 거래량이 최근 20거래일 평균의 2배 이상이며, 평균보다 많은 거래와 함께 종가가 전일보다 올랐습니다.")
            elif value > 0:
                positives.append(f"{label} {value}/{maximum}점: 기존 {label} 평가 조건 중 일부를 충족했습니다. 개별 지표 값은 이 설명에 포함되지 않습니다.")
        else:
            checks.append(f"{label} 구성점수는 저장 근거에 있지만, 기술적 근거가 완전하지 않아 해석을 보류합니다.")

    ratio = evidence.foreign_5d_ratio
    if ratio is None or not math.isfinite(ratio):
        checks.append("외국인 5거래일 수급 비율은 확인 불가입니다. 수급 분류만으로 순매수 여부를 단정하지 않습니다.")
    elif evidence.foreign_status == "POSITIVE" and ratio > 0:
        positives.append(f"외국인 수급은 저장 분류상 POSITIVE입니다. 최근 5거래일 누적 순매수는 20거래일 평균 거래량의 약 {ratio * 100:.1f}%에 해당합니다. 지분율이 아닙니다.")
    elif evidence.foreign_status == "NEGATIVE" and ratio < 0:
        risks.append("외국인 수급은 저장 분류상 NEGATIVE이며, 최근 5거래일 누적 순매도입니다.")
    elif evidence.foreign_status == "NEUTRAL":
        checks.append("외국인 수급은 저장 분류상 NEUTRAL입니다. 강한 순매수 신호로 해석하지 않습니다.")
    else:
        checks.append("외국인 수급 분류와 비율의 일치 여부는 확인이 필요합니다.")

    if record.signal_type == "OVERHEATED":
        risks.append("기존 Signal이 OVERHEATED로 기록됐습니다. 과열 조건에 해당하므로 급등 뒤 되돌림 위험을 확인해야 합니다.")
    if record.performance is not None and record.performance.tracking_status == "OPEN":
        risks.append("Signal 이후 성과는 측정 중입니다. 수익이 검증된 후보라는 뜻이 아닙니다.")
    elif record.performance is None and evidence.decision == "CANDIDATE":
        checks.append("Signal 이후 성과 데이터는 확인 불가입니다.")
    if profile is None:
        checks.append("기업정보·기업정보 기준일·출처는 확인 불가입니다.")
    else:
        for label, value in (
            ("업종", profile.sector),
            ("주요 사업/제품", profile.main_business_products),
            ("기업정보 기준일", profile.profile_as_of),
            ("기업정보 출처", profile.source),
        ):
            if not value:
                checks.append(f"{label}은 확인 불가입니다.")
        if profile.market_cap is None or not math.isfinite(profile.market_cap):
            checks.append("시가총액은 저장 기업정보에서 확인 불가입니다.")
    for label, value in (("전일 점수", previous), ("당일 근거 점수", current)):
        if value is None or not math.isfinite(value):
            checks.append(f"{label}는 확인 불가입니다.")

    return EasyStockAnalysis(
        summary=summary,
        positives=tuple(positives) or ("현재 확인된 근거로 설명할 긍정 요인은 없습니다.",),
        risks=tuple(risks),
        checks=tuple(checks),
        sources=(
            f"Signal 기준일: {record.signal_date} / 데이터 기준일: {record.basDd}",
            "점수·Signal·판정 출처: output/expanded_shadow/expanded_shadow_signal_ledger.csv",
            f"기술적 근거 조회 대상: data/expanded_shadow/market/{record.basDd}/{record.ticker}.csv",
            f"외국인 수급 조회 대상: data/expanded_shadow/investor/{record.basDd}/{record.ticker}_investor.csv",
            f"기업정보 기준일: {profile.profile_as_of if profile and profile.profile_as_of else '확인 불가'} / 출처: {profile.source if profile and profile.source else '확인 불가'}",
            "기업정보 저장본: data/expanded_shadow/company_profiles/expanded_company_profiles.json",
            "표시된 기준일 이후의 실시간 정보는 반영하지 않습니다.",
        ),
        method="기존 데이터와 규칙 기반 한국어 설명입니다. 생성형 AI 및 외부 AI API를 사용하지 않습니다.",
        disclaimer="투자정보 설명이며 매수·매도 추천이 아닙니다. 투자 판단을 대신하거나 수익을 보장하지 않습니다.",
    )
"""Offline, explainable information-review priorities; not investment signals."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

IMPORTANCES = ("HIGH", "MEDIUM", "LOW", "REVIEW")
RULE_VERSION = "6-C.1"
DISCLAIMER = (
    "정보 확인 우선순위이며 투자 추천이나 주가 예측이 아닙니다. "
    "제목과 수집 메타데이터만 사용했으며 실제 경제적 영향과 원문 내용은 확인하지 않았습니다."
)


class ClassificationError(ValueError):
    """A locally defined error code; no source payload is exposed."""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    label: str
    importance: str
    pattern: str
    rationale: str


# First match wins. Trading-status notices precede contracts mentioned in subtitles.
RULES = (
    Rule("R01", "TRADING_STATUS", "거래정지·상장상태", "HIGH",
         r"매매거래정지|매매거래재개|상장폐지|관리종목",
         "거래 또는 상장상태 관련 제목으로 적용 사유·기간을 먼저 확인해야 합니다."),
    Rule("R02", "SAFETY_LEGAL", "안전·법적사건", "HIGH",
         r"중대재해|횡령|배임|회생절차|파산|부도",
         "안전·법적사건 관련 제목으로 사건 내용과 회사 설명 확인이 필요합니다."),
    Rule("R03", "RESTRUCTURING", "합병·영업양수도·공개매수", "HIGH",
         r"합병|회사분할|영업양도|영업양수|공개매수",
         "기업구조·사업 이전·공개매수 관련 조건과 진행상태를 확인할 항목입니다."),
    Rule("R04", "EARNINGS_NOTICE", "실적발표 예고", "LOW",
         r"결산실적공시예고",
         "실적 수치 발표 자체가 아닌 공시 일정 안내입니다."),
    Rule("R05", "EARNINGS", "실적·정기보고서", "HIGH",
         r"영업\(잠정\)실적|매출액.*손익구조|사업보고서|반기보고서|분기보고서",
         "실적 또는 정기보고서 제목으로 실제 수치와 비교 기준의 원문 확인이 필요합니다."),
    Rule("R06", "CONTRACT", "판매·공급계약", "HIGH",
         r"단일판매[ㆍ·]?공급계약|수주계약",
         "계약 관련 금액·기간·상대방·변경사항을 확인할 항목이며 영향은 미확정입니다."),
    Rule("R07", "CAPITAL_FINANCING", "증자·주식관련사채·자본증권", "HIGH",
         r"유상증자|무상증자|감자결정|전환사채|신주인수권부사채|교환사채|조건부자본증권",
         "자본·자금조달 관련 조건과 진행단계 확인이 필요하며 희석·성과를 단정하지 않습니다."),
    Rule("R08", "INVESTMENT_ASSET", "출자·자산취득처분", "HIGH",
         r"타법인주식및출자증권|유형자산취득|유형자산처분|신규시설투자",
         "투자·자산 거래 관련 규모·목적·조건을 확인할 항목입니다."),
    Rule("R09", "CREDIT_EXPOSURE", "보증·대여·차입", "MEDIUM",
         r"채무보증|금전대여|차입금증가|담보제공",
         "금융 약정 관련 금액·상대방·조건 확인이 필요하며 위험 규모는 미확정입니다."),
    Rule("R10", "SHAREHOLDER_RETURN", "자기주식·배당", "MEDIUM",
         r"자기주식|배당결정",
         "자기주식·배당의 결정·계약·결과 단계와 조건을 구별해 확인할 항목입니다."),
    Rule("R11", "CONVERSION_PRICE", "전환가격 조정", "MEDIUM",
         r"전환가액|전환주식의전환가액",
         "전환가격 조정 조건과 대상 증권 확인이 필요하며 실제 희석 여부는 미확정입니다."),
    Rule("R12", "OWNERSHIP", "주주·임원 지분보고", "MEDIUM",
         r"대량보유상황보고서|특정증권등소유상황보고서|특정증권등거래계획보고서|소유주식변동신고서",
         "지분 보유·변동·거래계획 보고로 매수·매도 방향이나 실현 여부는 제목만으로 확정하지 않습니다."),
    Rule("R13", "MANAGEMENT_CHANGE", "대표이사 변경", "MEDIUM",
         r"대표이사변경",
         "경영진 변경 사유와 효력일을 확인할 항목입니다."),
    Rule("R14", "RELATED_PARTY", "특수관계인 거래", "MEDIUM",
         r"특수관계인",
         "특수관계인 거래의 상대방·금액·조건 확인이 필요합니다."),
    Rule("R15", "SECURITIES_DOCUMENT", "증권 발행·모집서류", "MEDIUM",
         r"증권발행실적보고서|증권신고서|투자설명서|일괄신고추가서류",
         "발행·모집 서류로 증권 종류와 발행 주체를 확인해야 하며 주식 증자로 단정하지 않습니다."),
    Rule("R16", "MEETING_IR", "주주총회·IR·기준일", "LOW",
         r"기업설명회|주주총회소집|주주명부폐쇄|의결권대리행사권유",
         "회의·설명회·기준일 안내로 구체적 안건과 내용을 확인할 보조 항목입니다."),
    Rule("R17", "DISCLOSURE_COMPLIANCE", "공시 준수 관련", "MEDIUM",
         r"불성실공시법인",
         "지정·미지정 등 실제 결정 내용을 확인해야 하며 제목의 일부만으로 제재를 단정하지 않습니다."),
    Rule("R18", "BUSINESS_PLAN", "장래 사업·경영계획", "MEDIUM",
         r"장래사업[ㆍ·]?경영계획",
         "미래 계획의 전제·불확실성을 확인할 항목이며 실현된 성과가 아닙니다."),
    Rule("R19", "MANAGEMENT_REVIEW", "경영사항 내용 확인 필요", "REVIEW",
         r"투자판단관련주요경영사항|기타경영사항|상장채권관련기타주요사항",
         "포괄적인 제목만으로 세부 유형을 확정할 수 없어 사람의 원문 검토가 필요합니다."),
)


def classify_title(title: str) -> dict:
    normalized = re.sub(r"\s+", "", title)
    for rule in RULES:
        match = re.search(rule.pattern, normalized)
        if match:
            return {
                "category": rule.category, "category_label": rule.label,
                "importance": rule.importance, "rule_id": rule.rule_id,
                "matched_text": match.group(0), "reason": rule.rationale,
            }
    return {
        "category": "UNCLASSIFIED", "category_label": "미분류",
        "importance": "REVIEW", "rule_id": "FALLBACK",
        "matched_text": None, "reason": "일치하는 제목 규칙이 없어 누락하지 않고 원문 검토 대상으로 보존합니다.",
    }


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def classify_collection(source: Any) -> dict:
    if not isinstance(source, dict):
        raise ClassificationError("INVALID_SOURCE")
    if (source.get("verdict") != "PASS" or source.get("collection_complete") is not True
            or source.get("scope") != "all" or source.get("uncollected_count") != 0):
        raise ClassificationError("SOURCE_NOT_COMPLETE")
    companies = source.get("companies")
    if (not isinstance(companies, list) or not companies
            or source.get("universe_count") != len(companies)
            or source.get("target_count") != len(companies)):
        raise ClassificationError("INVALID_COMPANY_COUNT")
    for field in ("disclosure_count", "correction_count", "companies_with_disclosures"):
        if not _integer(source.get(field)):
            raise ClassificationError("INVALID_SOURCE_COUNTS")
    unique: dict[str, dict] = {}
    raw_count = corrections = active_companies = duplicates = 0
    tickers: set[str] = set()
    corporate_codes: set[str] = set()
    for company in companies:
        if not isinstance(company, dict):
            raise ClassificationError("INVALID_COMPANY")
        ticker, corp = company.get("ticker"), company.get("corp_code")
        if (not isinstance(ticker, str) or not re.fullmatch(r"[0-9A-Z]{6}", ticker)
                or ticker in tickers or not isinstance(corp, str)
                or not re.fullmatch(r"[0-9]{8}", corp) or corp in corporate_codes
                or not isinstance(company.get("name"), str) or not company["name"].strip()
                or company.get("market") not in {"KOSPI", "KOSDAQ"}):
            raise ClassificationError("INVALID_COMPANY_IDENTITY")
        tickers.add(ticker)
        corporate_codes.add(corp)
        rows = company.get("disclosures")
        if (not isinstance(rows, list)
                or company.get("status") != ("SUCCESS" if rows else "NO_DATA")):
            raise ClassificationError("INVALID_COMPANY_STATUS")
        active_companies += bool(rows)
        for row in rows:
            if not isinstance(row, dict):
                raise ClassificationError("INVALID_DISCLOSURE")
            receipt, title, day = (row.get(key) for key in ("rcept_no", "report_nm", "rcept_dt"))
            if (row.get("ticker") != ticker or row.get("corp_code") != corp
                    or not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt)
                    or not isinstance(title, str) or not title.strip()
                    or not isinstance(day, str)
                    or row.get("original_url")
                    != f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
                    or type(row.get("is_correction")) is not bool
                    or "correction_type" not in row):
                raise ClassificationError("INVALID_DISCLOSURE")
            try:
                parsed_day = date.fromisoformat(day)
                start, end = (date.fromisoformat(source[key]) for key in ("start_date", "end_date"))
            except (ValueError, TypeError, KeyError):
                raise ClassificationError("INVALID_SOURCE_DATE") from None
            if not start <= parsed_day <= end:
                raise ClassificationError("DISCLOSURE_OUTSIDE_WINDOW")
            raw_count += 1
            corrections += row["is_correction"]
            record = {
                **row, "name": company["name"], "market": company["market"],
                "dart_corp_name": company.get("dart_corp_name"),
                "classification": classify_title(title),
                "classification_evidence": {
                    "inputs": ["report_nm", "ticker", "corp_code", "is_correction",
                               "correction_type", "rcept_dt"],
                    "identity_verified_against_company": True,
                    "category_basis": "TITLE_RULE_ONLY",
                    "metadata_limitation": "수집 결과에 공시 상세유형 코드와 원문 내용이 없어 제목 규칙을 사용합니다.",
                },
                "original_reviewed": False, "economic_impact": "NOT_ASSESSED",
                "original_receipt_no": None,
                "original_relationship": "NOT_ESTABLISHED" if row["is_correction"] else "NOT_APPLICABLE",
            }
            tag = re.match(r"^\[([^\]]*정정[^\]]*)\]", title.strip())
            if bool(tag) != row["is_correction"]:
                raise ClassificationError("INCONSISTENT_CORRECTION_FLAG")
            if row["correction_type"] != (tag.group(1) if tag else None):
                raise ClassificationError("INCONSISTENT_CORRECTION_TYPE")
            if receipt in unique:
                if unique[receipt] != record:
                    raise ClassificationError("CONFLICTING_RECEIPT")
                duplicates += 1
            else:
                unique[receipt] = record
    if (raw_count != source["disclosure_count"] or corrections != source["correction_count"]
            or active_companies != source["companies_with_disclosures"]):
        raise ClassificationError("SOURCE_COUNT_MISMATCH")
    # Recency is just a stable display order within each priority, not an impact score.
    records = sorted(unique.values(), key=lambda row: (
        IMPORTANCES.index(row["classification"]["importance"]),
        -date.fromisoformat(row["rcept_dt"]).toordinal(), -int(row["rcept_no"]),
    ))
    groups = {level: [row for row in records if row["classification"]["importance"] == level]
              for level in IMPORTANCES}
    return {
        "schema_version": 1, "rule_version": RULE_VERSION, "verdict": "PASS",
        "disclaimer": DISCLAIMER, "original_reviewed": False,
        "source_window": {key: source[key] for key in ("start_date", "end_date")},
        "universe_count": len(companies), "companies_with_disclosures": active_companies,
        "input_count": raw_count, "processed_count": len(records),
        "duplicates_removed": duplicates,
        "correction_count": sum(row["is_correction"] for row in records),
        "unresolved_correction_count": sum(row["is_correction"] for row in records),
        "importance_counts": {level: len(rows) for level, rows in groups.items()},
        "category_counts": dict(sorted(Counter(
            row["classification"]["category"] for row in records
        ).items())),
        "unclassified_count": sum(row["classification"]["category"] == "UNCLASSIFIED"
                                  for row in records),
        "rules": [asdict(rule) for rule in RULES],
        "rule_selection": "FIRST_MATCH_WINS; unmatched -> FALLBACK/UNCLASSIFIED/REVIEW",
        "display_order": "HIGH, MEDIUM, LOW, REVIEW; date DESC, receipt DESC; not an impact rank",
        "top_by_importance": {level: rows[:10] for level, rows in groups.items()},
        "disclosures": records,
    }

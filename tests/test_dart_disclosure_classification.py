"""Rule semantics and all 168 local source disclosures, without network access."""

from __future__ import annotations

import copy
import json
import socket

import pytest

from scripts import dart_disclosure_classify as cli
from src.dart_disclosure_classification import (
    IMPORTANCES,
    RULES,
    ClassificationError,
    classify_collection,
    classify_title,
)

ACTUAL_SOURCE = (
    cli.ROOT / "output" / "dart_disclosures"
    / "20261020T044347_fa35ed73fa1e4f96a8417d1fbd58e6e6.json"
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access forbidden in classification tests")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def source(title="알 수 없는 새 공시"):
    correction = title.startswith("[기재정정]")
    row = {
        "rcept_no": "20261020000001", "ticker": "005930", "corp_code": "00126380",
        "report_nm": title, "rcept_dt": "2026-10-20", "rcept_time": None,
        "rcept_time_source": "NOT_PROVIDED_BY_LIST_API",
        "original_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261020000001",
        "is_correction": correction, "correction_type": "기재정정" if correction else None,
    }
    return {
        "verdict": "PASS", "collection_complete": True, "scope": "all",
        "uncollected_count": 0, "universe_count": 1, "target_count": 1,
        "disclosure_count": 1, "correction_count": int(correction),
        "companies_with_disclosures": 1,
        "start_date": "2026-10-18", "end_date": "2026-10-20",
        "companies": [{
            "ticker": "005930", "name": "삼성전자", "corp_code": "00126380",
            "market": "KOSPI", "status": "SUCCESS", "disclosures": [row],
        }],
    }


@pytest.mark.parametrize("title,category,importance", [
    ("주권매매거래정지 (단일판매공급계약)", "TRADING_STATUS", "HIGH"),
    ("매매거래정지및정지해제(중요내용공시)", "TRADING_STATUS", "HIGH"),
    ("중대재해발생", "SAFETY_LEGAL", "HIGH"),
    ("[기재정정]증권신고서(합병)", "RESTRUCTURING", "HIGH"),
    ("특수관계인으로부터영업양수", "RESTRUCTURING", "HIGH"),
    ("[기재정정]공개매수설명서", "RESTRUCTURING", "HIGH"),
    ("연결재무제표기준영업(잠정)실적(공정공시)", "EARNINGS", "HIGH"),
    ("[기재정정]반기보고서 (2026.06)", "EARNINGS", "HIGH"),
    ("결산실적공시예고(안내공시)", "EARNINGS_NOTICE", "LOW"),
    ("[기재정정]단일판매ㆍ공급계약체결", "CONTRACT", "HIGH"),
    ("단일판매ㆍ공급계약체결(자회사의 주요경영사항)", "CONTRACT", "HIGH"),
    ("유상증자또는주식관련사채등의청약결과(자율공시)", "CAPITAL_FINANCING", "HIGH"),
    ("주요사항보고서(상각형조건부자본증권발행결정)", "CAPITAL_FINANCING", "HIGH"),
    ("타법인주식및출자증권처분결정", "INVESTMENT_ASSET", "HIGH"),
    ("유형자산취득결정(종속회사의주요경영사항)", "INVESTMENT_ASSET", "HIGH"),
    ("[기재정정]금전대여결정", "CREDIT_EXPOSURE", "MEDIUM"),
    ("타인에대한채무보증결정", "CREDIT_EXPOSURE", "MEDIUM"),
    ("주요사항보고서(자기주식취득신탁계약체결결정)", "SHAREHOLDER_RETURN", "MEDIUM"),
    ("현금ㆍ현물배당결정 (분기배당)", "SHAREHOLDER_RETURN", "MEDIUM"),
    ("전환주식의전환가액조정 (상환전환우선주)", "CONVERSION_PRICE", "MEDIUM"),
    ("임원ㆍ주요주주특정증권등거래계획보고서", "OWNERSHIP", "MEDIUM"),
    ("주식등의대량보유상황보고서(약식)", "OWNERSHIP", "MEDIUM"),
    ("대표이사변경", "MANAGEMENT_CHANGE", "MEDIUM"),
    ("특수관계인으로부터부동산임차", "RELATED_PARTY", "MEDIUM"),
    ("증권신고서(채무증권)", "SECURITIES_DOCUMENT", "MEDIUM"),
    ("증권발행실적보고서", "SECURITIES_DOCUMENT", "MEDIUM"),
    ("기업설명회(IR)개최(안내공시)", "MEETING_IR", "LOW"),
    ("불성실공시법인미지정 (공시불이행)", "DISCLOSURE_COMPLIANCE", "MEDIUM"),
    ("장래사업ㆍ경영계획(공정공시)", "BUSINESS_PLAN", "MEDIUM"),
    ("투자판단관련주요경영사항 (플랫폼평가 계약 체결)", "MANAGEMENT_REVIEW", "REVIEW"),
    ("상장채권관련기타주요사항", "MANAGEMENT_REVIEW", "REVIEW"),
    ("기타경영사항(자율공시)", "MANAGEMENT_REVIEW", "REVIEW"),
    ("처음 보는 공시", "UNCLASSIFIED", "REVIEW"),
])
def test_rules_do_not_infer_economic_outcomes(title, category, importance):
    result = classify_title(title)
    assert result["category"] == category
    assert result["importance"] == importance
    assert result["reason"]
    if category != "UNCLASSIFIED":
        assert result["matched_text"]


def test_correction_is_separate_and_original_is_never_guessed():
    data = source("[기재정정]단일판매ㆍ공급계약체결")
    original = copy.deepcopy(data)
    result = classify_collection(data)
    assert data == original
    row = result["disclosures"][0]
    for key, value in data["companies"][0]["disclosures"][0].items():
        assert row[key] == value
    assert row["name"] == "삼성전자"
    assert row["original_relationship"] == "NOT_ESTABLISHED"
    assert row["original_receipt_no"] is None
    assert row["original_reviewed"] is False
    assert row["economic_impact"] == "NOT_ASSESSED"
    assert result["correction_count"] == result["unresolved_correction_count"] == 1
    assert result["rules"] and result["disclaimer"]


def test_exact_duplicate_receipt_is_removed_once():
    data = source()
    data["companies"][0]["disclosures"] *= 2
    data["disclosure_count"] = 2
    result = classify_collection(data)
    assert result["input_count"] == 2
    assert result["processed_count"] == result["duplicates_removed"] == 1
    assert result["importance_counts"]["REVIEW"] == 1


def test_conflicting_duplicate_is_not_silently_dropped():
    data = source()
    changed = copy.deepcopy(data["companies"][0]["disclosures"][0])
    changed["report_nm"] = "다른 제목"
    data["companies"][0]["disclosures"].append(changed)
    data["disclosure_count"] = 2
    with pytest.raises(ClassificationError, match="CONFLICTING_RECEIPT"):
        classify_collection(data)


@pytest.mark.parametrize("change,code", [
    ({"verdict": "FAIL"}, "SOURCE_NOT_COMPLETE"),
    ({"collection_complete": False}, "SOURCE_NOT_COMPLETE"),
    ({"uncollected_count": 1}, "SOURCE_NOT_COMPLETE"),
    ({"scope": "sample"}, "SOURCE_NOT_COMPLETE"),
    ({"universe_count": 574}, "INVALID_COMPANY_COUNT"),
    ({"disclosure_count": 2}, "SOURCE_COUNT_MISMATCH"),
    ({"correction_count": 1}, "SOURCE_COUNT_MISMATCH"),
    ({"companies_with_disclosures": 0}, "SOURCE_COUNT_MISMATCH"),
    ({"disclosure_count": -1}, "INVALID_SOURCE_COUNTS"),
])
def test_source_failures_are_explicit(change, code):
    data = {**source(), **change}
    with pytest.raises(ClassificationError, match=code):
        classify_collection(data)


@pytest.mark.parametrize("change,code", [
    ({"ticker": "000660"}, "INVALID_DISCLOSURE"),
    ({"rcept_dt": "not-a-date"}, "INVALID_SOURCE_DATE"),
    ({"rcept_dt": "2026-10-17"}, "DISCLOSURE_OUTSIDE_WINDOW"),
    ({"original_url": "https://example.com"}, "INVALID_DISCLOSURE"),
    ({"report_nm": ""}, "INVALID_DISCLOSURE"),
    ({"is_correction": True}, "INCONSISTENT_CORRECTION_FLAG"),
    ({"correction_type": "기재정정"}, "INCONSISTENT_CORRECTION_TYPE"),
])
def test_invalid_record_is_not_hidden_by_review_fallback(change, code):
    data = source()
    data["companies"][0]["disclosures"][0].update(change)
    with pytest.raises(ClassificationError, match=code):
        classify_collection(data)


def test_all_actual_168_records_are_preserved_and_explained():
    raw = ACTUAL_SOURCE.read_bytes()
    data = json.loads(raw)
    result = classify_collection(data)
    assert result["verdict"] == "PASS"
    assert result["universe_count"] == 574
    assert result["companies_with_disclosures"] == 91
    assert result["input_count"] == result["processed_count"] == 168
    assert result["duplicates_removed"] == 0
    assert result["correction_count"] == result["unresolved_correction_count"] == 19
    assert result["importance_counts"] == {
        "HIGH": 41, "MEDIUM": 82, "LOW": 37, "REVIEW": 8,
    }
    assert result["unclassified_count"] == 0
    assert sum(result["importance_counts"].values()) == 168
    assert sum(result["category_counts"].values()) == 168
    original = {
        row["rcept_no"]: (company, row)
        for company in data["companies"] for row in company["disclosures"]
    }
    assert set(original) == {row["rcept_no"] for row in result["disclosures"]}
    for row in result["disclosures"]:
        company, before = original[row["rcept_no"]]
        assert all(row[key] == value for key, value in before.items())
        assert row["name"] == company["name"]
        assert row["classification"]["reason"]
        assert row["classification"]["importance"] in IMPORTANCES
        assert row["original_reviewed"] is False
        assert row["original_receipt_no"] is None
    for level, top in result["top_by_importance"].items():
        assert len(top) == min(10, result["importance_counts"][level])
        assert all(row["classification"]["importance"] == level for row in top)
    assert classify_collection(data) == result
    assert ACTUAL_SOURCE.read_bytes() == raw


def test_actual_report_persistence_keeps_source_and_previous_outputs(tmp_path):
    raw = ACTUAL_SOURCE.read_bytes()
    report, path = cli.run_classification(ACTUAL_SOURCE, tmp_path)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == report
    assert persisted["source_sha256"]
    assert path.parent == tmp_path / cli.OUTPUT_RELATIVE
    second, second_path = cli.run_classification(ACTUAL_SOURCE, tmp_path)
    assert second_path != path and path.exists()
    assert second["source_sha256"] == report["source_sha256"]
    assert ACTUAL_SOURCE.read_bytes() == raw
    assert not list(path.parent.glob("*.tmp"))
    assert not (tmp_path / "output" / "expanded_shadow").exists()


def test_cli_missing_or_incomplete_source_fails_without_report(tmp_path, monkeypatch, capsys):
    assert cli.main(["--input", str(tmp_path / "missing.json")]) == 1
    assert "FAIL" in capsys.readouterr().out
    invalid = tmp_path / "incomplete.json"
    invalid.write_text('{"verdict":"FAIL"}', encoding="utf-8")
    monkeypatch.setattr(cli, "run_classification",
                        lambda path: classify_collection(json.loads(path.read_bytes())))
    assert cli.main(["--input", str(invalid)]) == 1
    assert "SOURCE_NOT_COMPLETE" in capsys.readouterr().out


def test_rule_catalog_ids_unique_and_every_rule_has_reason():
    assert len({rule.rule_id for rule in RULES}) == len(RULES)
    assert all(rule.rationale and rule.pattern and rule.importance in IMPORTANCES for rule in RULES)

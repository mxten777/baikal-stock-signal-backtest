"""Offline safety-table fixtures, not fresh official-page verification."""

from __future__ import annotations

import copy

import pytest
import requests

from src.dart_document_analysis import extract_fields

ROW = {"classification": {"category": "SAFETY_LEGAL"}}
EVIDENCE = {
    "url": "https://dart.fss.or.kr/report/viewer.do?rcpNo=20261020800720",
    "sha256": "synthetic-fixture", "checked_at": "2026-10-20T00:00:00+00:00",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("No HTTP in tests"))


def rows():
    return [
        ["1. 중대재해내용", "발생 장소", "변압기 신공장 공사현장 콘덴서룸"],
        ["발생 재해 내용", "콘덴서룸 개구부에서 하부로 추락"],
        ["사망자 수", "1"], ["부상자 수", "0"],
        ["2. 중대재해 발생일자", "2026-10-19"],
        ["3. 고용노동부 보고일자", "2026-10-20"],
        ["4. 조치사항 및 향후대책", "- 경찰 및 고용노동부 현장 조사 실시 - 관련 자료 확인 및 재발방지대책 수립 예정"],
        ["5. 기타 투자판단과 관련한 중요사항",
         "- 중대재해 발생일자 부 부산지방고용노동청 울산동부지청의 명령에 따른 부분 작업중지 이행 중입니다."
         " - 본 공시는 종속회사의 주요경영사항입니다."],
    ]


def extract(items, encoding="utf-8"):
    body = "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in items
    ) + "</table>"
    content = f'<html><head><meta charset="{encoding}"></head><body>{body}</body></html>'
    return extract_fields(content.encode(encoding), ROW, EVIDENCE)


@pytest.mark.parametrize("encoding", ["utf-8", "euc-kr"])
def test_observed_table_shape_fields_evidence_and_partial_scope(encoding):
    result = extract(rows(), encoding)
    assert result["extraction_status"] == "SUCCESS"
    fields = {field["field"]: field for field in result["fields"]}
    assert len(fields) == 8
    assert fields["fatalities"]["numeric_value"] == "1"
    assert fields["injuries"]["numeric_value"] == "0"
    assert fields["accident_date"]["raw_value"] == "2026-10-19"
    assert fields["authority_report_date"]["raw_value"] == "2026-10-20"
    assert "추락" in fields["accident_description"]["raw_value"]
    assert "예정" in fields["follow_up_actions"]["raw_value"]
    assert fields["work_stoppage"]["normalized_value"] == "IN_EFFECT"
    assert fields["work_stoppage"]["scope"] == "PARTIAL"
    for field in fields.values():
        assert field["status"] == "VERIFIED"
        evidence = field["evidence"][0]
        assert evidence["url"] == EVIDENCE["url"]
        assert evidence["sha256"] == EVIDENCE["sha256"]
        assert evidence["checked_at"] == EVIDENCE["checked_at"]
        assert evidence["cells"] and evidence["xpath"].startswith("/html/body/table/tr")
    assert result["original_reviewed"] is False


@pytest.mark.parametrize("text,status,value,scope", [
    ("전체 작업중지 실시 중입니다.", "VERIFIED", "IN_EFFECT", "FULL"),
    ("작업중지는 실시하지 않았습니다.", "VERIFIED", "NOT_IN_EFFECT", "NONE"),
    ("작업중지 조치는 없습니다.", "VERIFIED", "NOT_IN_EFFECT", "NONE"),
    ("부분 작업중지 이행 중이 아닙니다.", "UNCONFIRMED", None, None),
    ("부분 작업중지 예정입니다.", "UNCONFIRMED", None, None),
    ("부분 작업중지 이행 여부 확인 중입니다.", "UNCONFIRMED", None, None),
    ("작업중지 이행 중입니다.", "UNCONFIRMED", None, None),
    ("부분 작업중지 해제 후 재개했습니다.", "UNCONFIRMED", None, None),
    ("전체 작업중지는 아니며 일부 공사를 중지했습니다.", "UNCONFIRMED", None, None),
    ("- 부분 작업중지 이행 중입니다. - 작업중지 조치는 없습니다.", "UNCONFIRMED", None, None),
    ("- 부분 작업중지 이행 중입니다. - 이후 명령이 해제되었습니다.", "UNCONFIRMED", None, None),
    ("-", "UNCONFIRMED", None, None),
    ("", "UNCONFIRMED", None, None),
])
def test_stoppage_negation_ambiguity_and_scope(text, status, value, scope):
    items = rows()
    items[-1][-1] = text
    field = extract(items)["fields"][-1]
    assert (field["status"], field["normalized_value"], field["scope"]) == (status, value, scope)
    assert field["evidence"][0]["cells"][-1] == text


ORDER = (
    "현대아산㈜은 2026년 10월 19일 대구지방고용노동청 안동지청으로부터 "
    "산업재해 또는 작업 중 질병발생의 급박한 위험이 있으므로 "
    "부분 작업중지 명령(작업중지 범위 : 거푸집 조립 및 해체 작업 일체)을 받았고, "
    "안전조치를 완료한 후 지방고용노동관서장의 확인을 받아 작업을 재개할 예정입니다."
)


@pytest.mark.parametrize("encoding", ["utf-8", "euc-kr"])
def test_explicit_partial_order_with_future_resumption_preserves_target(encoding):
    items = rows()
    items[-1][-1] = "- 종속회사 관련 공시입니다. - " + ORDER
    field = extract(items, encoding)["fields"][-1]
    assert (field["status"], field["normalized_value"], field["scope"]) == (
        "VERIFIED", "IN_EFFECT", "PARTIAL",
    )
    assert field["scope_detail"] == "거푸집 조립 및 해체 작업 일체"
    assert field["raw_value"] == items[-1][-1]
    assert field["evidence"][0]["cells"][-1] == items[-1][-1]


@pytest.mark.parametrize("text", [
    ORDER.replace("받았고,", "받을 예정이며,"),
    ORDER.replace("받았고,", "받지 않았고,"),
    ORDER.replace("재개할 예정입니다.", "재개하였습니다."),
    ORDER.replace("재개할 예정입니다.", "재개 여부는 미정입니다."),
    ORDER.replace("거푸집 조립 및 해체 작업 일체", "-"),
    ORDER.replace("거푸집 조립 및 해체 작업 일체", "범위 미상"),
    ORDER.replace("거푸집 조립 및 해체 작업 일체", "회사 전체"),
    ORDER.replace("거푸집 조립 및 해체 작업 일체", "전체 공장 작업 일체"),
    ORDER.replace("거푸집 조립 및 해체 작업 일체", "미정 작업 일체"),
    ORDER.replace("10월 19일", "2월 30일"),
    ORDER.replace("(작업중지 범위 : 거푸집 조립 및 해체 작업 일체)", ""),
    ORDER + " 이후 명령이 해제되었습니다.",
    "- " + ORDER + " - 작업을 재개했습니다.",
    "- " + ORDER + " - 작업중지 조치는 없습니다.",
    "- " + ORDER + " - 전체 작업중지 실시 중입니다.",
])
def test_partial_order_does_not_promote_plans_release_conflict_or_unknown_scope(text):
    items = rows()
    items[-1][-1] = text
    field = extract(items)["fields"][-1]
    assert field["status"] == "UNCONFIRMED"
    assert field["normalized_value"] is field["scope"] is None
    assert field.get("scope_detail") is None


@pytest.mark.parametrize("label,value", [
    ("사망자 수", "-"), ("사망자 수", "-1"), ("사망자 수", "1.5"),
    ("사망자 수", "1명(추정)"), ("부상자 수", "미정"),
    ("2. 중대재해 발생일자", "2026-02-30"),
    ("2. 중대재해 발생일자", "2026-10-19 예정"),
    ("3. 고용노동부 보고일자", "-"),
    ("4. 조치사항 및 향후대책", "-"),
    ("4. 조치사항 및 향후대책", "확인 중"),
])
def test_missing_invalid_and_ambiguous_values(label, value):
    items = rows()
    index = next(i for i, row in enumerate(items) if row[0] == label)
    items[index][-1] = value
    field_index = {"사망자 수": 2, "부상자 수": 3,
                   "2. 중대재해 발생일자": 4, "3. 고용노동부 보고일자": 5,
                   "4. 조치사항 및 향후대책": 6}[label]
    field = extract(items)["fields"][field_index]
    assert field["status"] == "UNCONFIRMED"
    assert field["numeric_value"] is None


def test_missing_row_and_conflicting_rows_are_not_filled():
    items = rows()
    del items[3]
    items.append(["사망자 수", "2"])
    fields = extract(items)["fields"]
    assert fields[2]["status"] == fields[3]["status"] == "UNCONFIRMED"
    assert len(fields[2]["evidence"]) == 2
    assert fields[3]["evidence"] == []


@pytest.mark.parametrize("index,value", [
    (0, "발생 장소 확인 중입니다."),
    (1, "콘덴서룸에서 추락한 것으로 추정"),
    (1, "구체적인 경위 조사 중"),
])
def test_uncertain_accident_text_is_not_verified(index, value):
    items = rows()
    items[index][-1] = value
    field = extract(items)["fields"][index]
    assert field["status"] == "UNCONFIRMED"
    assert field["raw_value"] == value


def test_duplicate_and_unknown_layouts_are_unsupported():
    for items in ([], [["사망", "1"]], rows() + copy.deepcopy(rows())):
        result = extract(items)
        # Repeated labels in one recognizable table are ambiguous, not a new format.
        expected = "PARTIAL" if len(items) > len(rows()) else "UNSUPPORTED"
        assert result["extraction_status"] == expected
    result = extract_fields(b"<html><body>No tables</body></html>", ROW, EVIDENCE)
    assert result["extraction_status"] == "UNSUPPORTED"
    assert all(field["status"] == "UNSUPPORTED" for field in result["fields"])


def test_stoppage_in_an_unrelated_table_cannot_confirm():
    items = rows()
    del items[-1]
    assert extract(items)["fields"][-1]["status"] == "UNCONFIRMED"
    content = '<html><meta charset="utf-8"><body><table><tr><td>1. 중대재해내용</td></tr></table>'
    content += '<table><tr><td>5. 기타 투자판단과 관련한 중요사항</td><td>전체 작업중지 실시 중입니다.</td></tr></table></body></html>'
    assert extract_fields(content.encode(), ROW, EVIDENCE)["fields"][-1]["status"] == "UNCONFIRMED"


def test_multiple_safety_tables_and_correction_values_are_not_current_facts():
    table = "<table><tr><td>1. 중대재해내용</td><td>발생 장소</td><td>현장</td></tr></table>"
    content = f'<html><meta charset="utf-8"><body>{table}{table}</body></html>'
    assert extract_fields(content.encode(), ROW, EVIDENCE)["extraction_status"] == "UNSUPPORTED"
    correction = (
        "<table><tr><th>항목</th><th>정정사유</th><th>정정전</th><th>정정후</th></tr>"
        "<tr><td>1. 중대재해내용</td><td>수정</td><td>전</td><td>후</td></tr>"
        "<tr><td>사망자 수</td><td>2</td><td>1</td><td>2</td></tr></table>"
    )
    content = f'<html><meta charset="utf-8"><body>{correction}{table}</body></html>'
    fields = extract_fields(content.encode(), ROW, EVIDENCE)["fields"]
    assert fields[0]["raw_value"] == "현장"
    assert fields[2]["status"] == "UNCONFIRMED"
    assert fields[2]["evidence"] == []

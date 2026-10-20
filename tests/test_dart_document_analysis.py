"""Offline synthetic HTML fixtures and bounded official-access contracts."""

from __future__ import annotations

import csv
import json

import pytest
import requests

from scripts import dart_document_analyze as cli
from src import dart_document_analysis as analysis

SOURCE = (
    cli.ROOT / "output" / "dart_disclosure_classification"
    / "20261020T044712_db81de1c8684428c997682585a0e3d59.json"
)
EVIDENCE = {
    "url": "https://dart.fss.or.kr/report/viewer.do?rcpNo=20261020800241",
    "checked_at": "2026-10-20T05:00:00+00:00", "sha256": "fixture", "http_status": 200,
}


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("No real HTTP in tests"))


def selected():
    return analysis.select_documents(json.loads(SOURCE.read_text(encoding="utf-8")))


def document(body: str, encoding="utf-8"):
    return (
        f'<html><head><meta charset="{encoding}"></head><body>{body}</body></html>'
    ).encode(encoding)


def table(rows):
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    ) + "</table>"


def test_selection_exactly_five_distinct_receipts_and_multiple_categories():
    rows = selected()
    assert len(rows) == len({r["rcept_no"] for r in rows}) == 5
    assert all(row["classification"]["importance"] == "HIGH" for row in rows)
    assert len({r["classification"]["category"] for r in rows}) == 4
    assert sum(row["is_correction"] for row in rows) == 1


@pytest.mark.parametrize("encoding", ["utf-8", "euc-kr"])
def test_declared_encoding_contract_values_and_absent_fields(encoding):
    row = selected()[0]
    body = table([
        ["2. 계약내역", "계약금액(원)", "1,200"],
        ["매출액대비(%)", "3.4"], ["3. 계약상대", "테스트기업"],
        ["5. 계약기간", "시작일", "2026-10-01"], ["종료일", "2027-09-30"],
        ["대금지급 조건 등", "-"],
    ])
    result = analysis.extract_fields(document(body, encoding), row, EVIDENCE)
    fields = {f["field"]: f for f in result["fields"]}
    assert fields["contract_amount"]["numeric_value"] == "1200"
    assert fields["contract_amount"]["unit"] == "KRW"
    assert fields["counterparty"]["raw_value"] == "테스트기업"
    assert fields["start_date"]["raw_value"] == "2026-10-01"
    assert fields["payment_terms"]["status"] == "UNCONFIRMED"
    for field in fields.values():
        for evidence in field["evidence"]:
            assert evidence["checked_at"] == EVIDENCE["checked_at"]
            assert evidence["url"] == EVIDENCE["url"]
            assert evidence["xpath"].endswith("tr") or "/tr[" in evidence["xpath"]
            assert evidence["cells"]


def test_earnings_preserve_current_vs_cumulative_and_explicit_units():
    rows = [
        ["실적기간"], ["당기실적", "2026-07-01", "~", "2026-09-30"],
        ["1. 연결실적내용", "단위 : 백만원, %"],
        ["구분", "당기실적", "전기실적", "전기대비", "전년동기실적"],
        ["매출액", "당해실적", "100", "90", "11.1", "80"],
        ["누계실적", "300", "-", "-"],
        ["영업이익", "당해실적", "-2", "3", "-166.7", "4"],
        ["당기순이익", "당해실적", "5", "4", "25", "3"],
    ]
    result = analysis.extract_fields(document(table(rows)), selected()[1], EVIDENCE)
    fields = {f["field"]: f for f in result["fields"]}
    assert fields["revenue_current"]["numeric_value"] == "100"
    assert fields["revenue_current"]["unit"] == "백만원"
    assert fields["operating_income_current"]["numeric_value"] == "-2"
    assert fields["reporting_period"]["raw_value"] == "2026-07-01 ~ 2026-09-30"
    assert fields["revenue_current"]["context_rows"]
    without_unit = [r for r in rows if "단위" not in " ".join(r)]
    result = analysis.extract_fields(document(table(without_unit)), selected()[1], EVIDENCE)
    assert next(f for f in result["fields"] if f["field"] == "revenue_current")["status"] == "UNCONFIRMED"


def test_multi_pair_subscription_row_not_wrong_number():
    body = table([
        ["1. 증권의 종류", "기명식 보통주"],
        ["2. 발행방법", "주주배정 유상증자"],
        ["5. 청약결과", "발행예정주식수(주)", "100"],
        ["해당 청약주식수(주)", "110"],
        ["청약주식수(누계)(주)", "110", "청약률(%)", "110.0"],
    ])
    result = analysis.extract_fields(document(body), selected()[2], EVIDENCE)
    fields = {f["field"]: f for f in result["fields"]}
    assert fields["subscription_ratio"]["numeric_value"] == "110.0"
    assert fields["planned_shares"]["numeric_value"] == "100"
    assert "bond_face_amount" not in fields


def test_correction_before_after_not_confused_with_current_value():
    body = table([
        ["항 목", "정정사유", "정 정 전", "정 정 후"],
        ["3. 자금조달의 목적", "조건 변경", "운영자금 100", "채무상환자금 100"],
        ["11. 납입일", "조건 변경", "-", "2026년 10월 29일"],
    ]) + table([
        ["2. 사채의 권면(전자등록)총액 (원)", "100"],
        ["채무상환자금(원)", "100"], ["표면이자율 (%)", "-"],
        ["11. 납입일", "2026년 10월 29일"],
    ])
    row = selected()[3]
    result = analysis.extract_fields(document(body), row, EVIDENCE)
    fields = {f["field"]: f for f in result["fields"]}
    assert fields["bond_face_amount"]["numeric_value"] == "100"
    assert fields["coupon_rate"]["status"] == "UNCONFIRMED"
    assert fields["payment_date"]["status"] == "VERIFIED"
    assert len(result["correction_changes"]) == 2
    assert result["correction_changes"][0]["before"] == "운영자금 100"
    assert result["correction_changes"][0]["after"] == "채무상환자금 100"
    assert result["original_receipt_no"] is None
    assert result["original_relationship"] == "NOT_ESTABLISHED"


def test_conflicting_or_missing_values_stay_unconfirmed():
    body = table([["계약금액(원)", "100"], ["계약금액(원)", "200"]])
    result = analysis.extract_fields(document(body), selected()[0], EVIDENCE)
    amount = result["fields"][0]
    assert amount["status"] == "UNCONFIRMED"
    assert amount["numeric_value"] is None
    assert len(amount["evidence"]) == 2


def test_viewer_literal_link_requires_identity_and_same_receipt():
    row = selected()[0]
    entry = (
        f'<title>{row["name"]}/{row["report_nm"]}/{row["rcept_dt"].replace("-", ".")}</title>'
        f'viewDoc("{row["rcept_no"]}", "12345", "0", "0", "0", "HTML", "");'
    )
    url = analysis.document_url(entry.encode("utf-8"), row)
    assert url.startswith("https://dart.fss.or.kr/report/viewer.do?")
    assert "dcmNo=12345" in url
    with pytest.raises(analysis.DocumentError, match="VIEWER_IDENTITY_NOT_VERIFIED"):
        analysis.document_url(entry.replace(row["name"], "다른기업").encode("utf-8"), row)
    with pytest.raises(analysis.DocumentError, match="FULL_DOCUMENT_LINK_UNCONFIRMED"):
        analysis.document_url(entry.replace(row["rcept_no"], "20261020999999").encode("utf-8"), row)


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_market_join_date_and_ticker_exact_no_forward_fill(tmp_path):
    paths = analysis.ExpandedShadowPaths(tmp_path)
    path = paths.market_dir("2026-10-19") / "247540.csv"
    write_csv(path, [{"date": "2026-10-19", "open": "1", "high": "2", "low": "1",
                      "close": "2", "volume": "100"}],
              ["date", "open", "high", "low", "close", "volume"])
    result = analysis.market_linkage(tmp_path, "247540", "2026-10-19")
    assert result["market"]["status"] == "MATCHED"
    assert result["investor"]["status"] == "FILE_NOT_FOUND"
    next_day = analysis.market_linkage(tmp_path, "247540", "2026-10-20")
    assert next_day["market"]["status"] == "DATE_NOT_AVAILABLE"
    assert next_day["market"]["matched_row"] is None
    assert next_day["market"]["latest_local_date"] == "2026-10-19"
    assert analysis.market_linkage(tmp_path, "247540", "2026-10-18")["market"]["status"] == "FILE_NOT_FOUND"


def test_investor_ticker_mismatch_is_explicit(tmp_path):
    paths = analysis.ExpandedShadowPaths(tmp_path)
    path = paths.investor_dir("2026-10-19") / "247540_investor.csv"
    write_csv(path, [{"date": "2026-10-19", "ticker": "999999", "foreign_net_buy": "1",
                      "institution_net_buy": "2"}],
              ["date", "ticker", "foreign_net_buy", "institution_net_buy"])
    result = analysis.market_linkage(tmp_path, "247540", "2026-10-19")
    assert result["investor"]["status"] == "ERROR"
    assert result["investor"]["matched_row"] is None


def test_access_failure_never_fabricates_fields(tmp_path, monkeypatch):
    def fail(url):
        raise analysis.DocumentError("OFFICIAL_REQUEST_FAILED")
    monkeypatch.setattr(analysis, "_get", fail)
    result = analysis.analyze_document(selected()[0], tmp_path)
    assert result["access_status"] == "BLOCKED"
    assert result["fields"] == []
    assert result["requests_attempted"] == 1
    assert result["original_reviewed"] is False


def test_nonselected_receipt_never_makes_request(tmp_path):
    row = {**selected()[0], "rcept_no": "20261020999999"}
    with pytest.raises(analysis.DocumentError, match="OUTSIDE_FIVE_DOCUMENT_ALLOWLIST"):
        analysis.analyze_document(row, tmp_path)


def test_http_errors_and_redirects_are_safe(monkeypatch):
    class Response:
        status_code = 302
    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    with pytest.raises(analysis.DocumentError, match="OFFICIAL_HTTP_302"):
        analysis._get("https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261020800241")


def test_extraction_no_tables_is_not_success():
    with pytest.raises(analysis.DocumentError, match="DOCUMENT_TABLES_UNAVAILABLE"):
        analysis.extract_fields(document("오류 페이지"), selected()[0], EVIDENCE)


def test_runner_persists_blocked_report_and_preserves_source(tmp_path, monkeypatch):
    original = SOURCE.read_bytes()
    def fail(url):
        raise analysis.DocumentError("OFFICIAL_REQUEST_FAILED")
    monkeypatch.setattr(analysis, "_get", fail)
    report, path = cli.run_analysis(SOURCE, tmp_path)
    assert report["verdict"] == "BLOCKED"
    assert report["access_failure"] == report["requests_attempted"] == 5
    assert len(report["documents"]) == 5
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert SOURCE.read_bytes() == original
    assert not list(path.parent.glob("*.tmp"))
    assert not (tmp_path / "output" / "expanded_shadow").exists()


@pytest.mark.parametrize("data", [None, [], {"verdict": "FAIL"},
                                {"verdict": "PASS", "disclosures": [None]}])
def test_invalid_source_explicitly_rejected(data):
    with pytest.raises(analysis.DocumentError):
        analysis.select_documents(data)


def test_production_exact_date_not_masked_by_stale_expanded_snapshot(tmp_path):
    paths = analysis.ExpandedShadowPaths(tmp_path)
    fields = ["date", "open", "high", "low", "close", "volume"]
    row = {"date": "2026-10-19", "open": "1", "high": "2", "low": "1",
           "close": "2", "volume": "100"}
    write_csv(paths.market_dir("2026-10-19") / "247540.csv", [row], fields)
    write_csv(tmp_path / "data" / "raw" / "247540.csv",
              [{**row, "date": "2026-10-20"}], fields)
    result = analysis.market_linkage(tmp_path, "247540", "2026-10-20")
    assert result["market"]["status"] == "MATCHED"
    assert result["market"]["matched_row"]["date"] == "2026-10-20"


def test_actual_five_document_evidence_report_without_network():
    path = (
        cli.ROOT / cli.OUTPUT_RELATIVE
        / "20261020T051719+0000_54ae09aca717435396c85ede4ca04d7b.json"
    )
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["access_success"] == 5
    assert report["access_failure"] == 0
    assert report["verified_field_count"] == 24
    assert report["unconfirmed_field_count"] == 2
    assert report["requests_attempted"] == 10
    documents = {d["name"]: d for d in report["documents"]}
    values = {
        name: {field["field"]: field for field in doc["fields"]}
        for name, doc in documents.items()
    }
    assert values["S-Oil"]["contract_amount"]["numeric_value"] == "1160000000000"
    assert values["LS에코에너지"]["revenue_current"]["numeric_value"] == "351521"
    assert values["LS에코에너지"]["revenue_current"]["unit"] == "백만원"
    assert values["에코프로비엠"]["subscription_ratio"]["numeric_value"] == "103.25"
    assert values["하나금융지주"]["bond_face_amount"]["numeric_value"] == "270000000000"
    assert values["현대위아"]["transfer_amount"]["numeric_value"] == "358386000000"
    for doc in documents.values():
        assert doc["original_reviewed"] is False
        assert doc["document_evidence"]["sha256"]
        assert doc["document_evidence"]["checked_at"]
        assert doc["document_evidence"]["http_status"] == 200
        for field in doc["fields"]:
            if field["status"] == "VERIFIED":
                assert field["evidence"]
                assert all(e["cells"] and e["xpath"] for e in field["evidence"])
        for dataset in ("market", "investor"):
            linkage = doc["market_linkage"][dataset]
            if linkage["status"] == "MATCHED":
                assert linkage["matched_row"]["date"] == doc["rcept_dt"]
            else:
                assert linkage["matched_row"] is None
    correction = documents["하나금융지주"]
    assert len(correction["correction_changes"]) == 7
    assert correction["original_receipt_no"] is None
    assert correction["correction_change_status"] == "VERIFIED_FROM_CORRECTION_TABLE"

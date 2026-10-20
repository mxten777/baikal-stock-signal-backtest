"""Offline STEP 6-A contract tests; no authenticated requests are made."""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
import pandas as pd
import pytest
import requests

from scripts import dart_disclosure_collect as cli
from src.data_provider.dart_disclosure_provider import (
    ALL_MARKET_MAX_PAGES,
    MAX_PAGES,
    SAMPLE_TICKERS,
    CompanyMapping,
    DartClient,
    DartDisclosureError,
    collect_company,
    collect_disclosures,
    map_companies,
)
from src.expanded_shadow_universe import ExpandedTicker, load_expanded_universe

START = date(2026, 10, 1)
END = date(2026, 10, 20)
MAPPING = CompanyMapping("005930", "Samsung", "KOSPI", "SUCCESS", "00126380")
SECRET = "never-print-this-key"


def disclosure(receipt: str = "20261020000001", title: str = "Report") -> dict:
    return {
        "rcept_no": receipt, "report_nm": title, "rcept_dt": "20261020",
        "corp_code": MAPPING.corp_code, "stock_code": MAPPING.ticker,
    }


def page(rows: list[dict], *, number: int = 1, count: int | None = None) -> dict:
    count = len(rows) if count is None else count
    return {
        "status": "000", "page_no": number, "total_count": count,
        "total_page": (count + 99) // 100, "list": rows,
    }


class FakeClient(DartClient):
    def __init__(self, payloads: list, frame: pd.DataFrame | None = None):
        super().__init__(SECRET)
        self.payloads = payloads
        self.frame = frame
        self.pages: list[int] = []

    def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
        self.request_count += 1
        self.pages.append(page_no)
        payload = self.payloads[min(page_no - 1, len(self.payloads) - 1)]
        if isinstance(payload, DartDisclosureError):
            raise payload
        return copy.deepcopy(payload)

    def corp_codes(self) -> pd.DataFrame:
        self.request_count += 1
        if self.frame is None:
            raise DartDisclosureError("CORP_CODES_UNAVAILABLE")
        return self.frame


def universe() -> tuple[ExpandedTicker, ...]:
    return tuple(
        ExpandedTicker(ticker, f"Name{index}", "KOSPI" if index < 5 else "KOSDAQ",
                       "2026-09-17")
        for index, ticker in enumerate(SAMPLE_TICKERS)
    )


def codes(items: tuple[ExpandedTicker, ...]) -> pd.DataFrame:
    return pd.DataFrame([
        {"stock_code": item.ticker, "corp_code": f"{index + 1:08d}",
         "corp_name": item.name}
        for index, item in enumerate(items)
    ])


def test_mapping_exact_identifiers_renames_missing_ambiguous_invalid():
    items = universe()
    frame = codes(items)
    frame.loc[0, "corp_name"] = "Renamed"
    frame = frame.drop(index=1)
    frame = pd.concat([frame, pd.DataFrame([{
        "stock_code": items[2].ticker, "corp_code": "99999999", "corp_name": "Other",
    }])], ignore_index=True)
    frame.loc[frame["stock_code"] == items[3].ticker, "corp_code"] = "123"
    result = {row.ticker: row for row in map_companies(items, frame)}
    assert result[items[0].ticker].status == "SUCCESS"
    assert result[items[0].ticker].name_matches is False
    assert result[items[1].ticker].status == "UNMAPPED"
    assert result[items[2].ticker].error_code == "AMBIGUOUS_CORP_CODE"
    assert result[items[3].ticker].error_code == "INVALID_CORP_CODE"
    assert result[items[4].ticker].corp_code == "00000005"


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"stock_code": ["005930"]})])
def test_invalid_corporate_source_is_not_unmapped(frame):
    with pytest.raises(DartDisclosureError, match="INVALID_CORP_CODES"):
        map_companies(universe(), frame)


def test_actual_controlled_574_mapping_contract_with_synthetic_codes():
    controlled = load_expanded_universe(cli.ROOT / cli.UNIVERSE_RELATIVE)
    frame = codes(controlled.tickers)
    mapped = map_companies(controlled.tickers, frame)
    assert len(mapped) == 574
    assert all(row.status == "SUCCESS" for row in mapped)
    sample = [row for row in mapped if row.ticker in SAMPLE_TICKERS]
    assert len(sample) == 10
    assert sum(row.market == "KOSPI" for row in sample) == 5
    assert sum(row.market == "KOSDAQ" for row in sample) == 5


def test_receipt_dedup_corrections_and_unknown_time():
    original = disclosure()
    corrected = disclosure("20261020000002", "[기재정정]Report")
    attachment = disclosure("20261020000003", "[첨부정정]Report")
    result = collect_company(FakeClient([page([original, original, corrected, attachment])]),
                             MAPPING, START, END)
    assert result["status"] == "SUCCESS"
    assert len(result["disclosures"]) == 3
    assert result["duplicates_removed"] == 1
    assert result["correction_count"] == 2
    assert {row["correction_type"] for row in result["disclosures"]} == {
        None, "기재정정", "첨부정정",
    }
    for row in result["disclosures"]:
        assert row["rcept_time"] is None
        assert row["rcept_time_source"] == "NOT_PROVIDED_BY_LIST_API"
        assert row["original_url"].endswith(row["rcept_no"])
        assert row["ticker"] == "005930"
        assert row["rcept_dt"] == "2026-10-20"


@pytest.mark.parametrize("status", ["010", "020", "100", "800", "900", None, SECRET])
def test_api_error_never_becomes_no_data(status):
    result = collect_company(FakeClient([{"status": status, "message": SECRET}]),
                             MAPPING, START, END)
    assert result["status"] == "ERROR"
    assert result["disclosures"] == []
    assert SECRET not in json.dumps(result)


def test_only_explicit_013_is_no_data():
    result = collect_company(FakeClient([{"status": "013"}]), MAPPING, START, END)
    assert result["status"] == "NO_DATA"
    assert result["api_status"] == "013"
    assert result["error_code"] is None


@pytest.mark.parametrize("payload,expected", [
    (page([]), "INVALID_PAGINATION"),
    ({"status": "000"}, "INVALID_PAGINATION"),
    ({"status": "013", "list": [disclosure()]}, "INCONSISTENT_NO_DATA"),
    (page([disclosure()], count=301), "PAGE_LIMIT_EXCEEDED"),
    (page([disclosure()], count=2), "INCOMPLETE_RESPONSE"),
])
def test_bad_or_truncated_success_is_error(payload, expected):
    client = FakeClient([payload])
    result = collect_company(client, MAPPING, START, END)
    assert result["status"] == "ERROR"
    assert result["error_code"] == expected
    assert result["disclosures"] == []
    assert client.pages == [1]


@pytest.mark.parametrize("change", [
    {"rcept_no": "bad"}, {"corp_code": "99999999"}, {"stock_code": "000660"},
    {"rcept_dt": "20260230"}, {"rcept_dt": "20260930"}, {"report_nm": ""},
])
def test_malformed_record_not_published(change):
    row = {**disclosure(), **change}
    result = collect_company(FakeClient([page([row])]), MAPPING, START, END)
    assert result["status"] == "ERROR"
    assert not result["disclosures"]


def test_conflicting_duplicate_is_error():
    result = collect_company(FakeClient([page([disclosure(), disclosure(title="Other")])]),
                             MAPPING, START, END)
    assert result["error_code"] == "CONFLICTING_RECEIPT"
    assert result["disclosures"] == []


def test_pagination_dedup_and_small_call_limit():
    rows = [disclosure(f"20261020{index:06d}") for index in range(100)]
    client = FakeClient([page(rows, count=101), page([rows[-1]], number=2, count=101)])
    result = collect_company(client, MAPPING, START, END)
    assert result["status"] == "SUCCESS"
    assert len(result["disclosures"]) == 100
    assert result["duplicates_removed"] == 1
    assert client.pages == [1, 2]
    assert client.request_count <= MAX_PAGES


@pytest.mark.parametrize("second", [
    DartDisclosureError("HTTP_REQUEST_FAILED"),
    {"status": "013"},
    page([disclosure()], number=2, count=102),
])
def test_second_page_failure_discards_partial_records(second):
    client = FakeClient([page([disclosure()] * 100, count=101), second])
    result = collect_company(client, MAPPING, START, END)
    assert result["status"] == "ERROR"
    assert result["disclosures"] == []
    assert result["correction_count"] == 0


def test_unmapped_skips_network():
    mapping = CompanyMapping("999999", "Unknown", "KOSDAQ", "UNMAPPED",
                             error_code="STOCK_CODE_NOT_FOUND")
    client = FakeClient([])
    result = collect_company(client, mapping, START, END)
    assert result["status"] == "UNMAPPED"
    assert client.request_count == 0


def test_corporate_download_failure_blocks_not_unmapped():
    client = FakeClient([])
    result = collect_disclosures(universe(), client, START, END)
    assert result["verdict"] == "BLOCKED"
    assert result["mapping_unverified_count"] == 10
    assert result["mapping_counts"]["UNMAPPED"] == 0
    assert result["collection_counts"]["ERROR"] == 10
    assert client.request_count == 1


def test_live_unreachable_blocks_while_mapping_is_verified():
    client = FakeClient([DartDisclosureError("HTTP_REQUEST_FAILED")], codes(universe()))
    result = collect_disclosures(universe(), client, START, END)
    assert result["verdict"] == "BLOCKED"
    assert result["blocked_reason"] == "LIVE_COLLECTION_UNAVAILABLE"
    assert result["mapping_unverified_count"] == 0
    assert result["mapping_counts"]["SUCCESS"] == 10


def test_all_no_data_is_valid_pass():
    client = FakeClient([{"status": "013"}], codes(universe()))
    result = collect_disclosures(universe(), client, START, END)
    assert result["verdict"] == "PASS"
    assert result["collection_counts"]["NO_DATA"] == 10
    assert result["api_request_count"] == 11


def test_unmapped_is_fail_not_no_data():
    frame = codes(universe()).iloc[1:]
    result = collect_disclosures(universe(), FakeClient([{"status": "013"}], frame), START, END)
    assert result["verdict"] == "FAIL"
    assert result["mapping_counts"]["UNMAPPED"] == 1
    assert result["collection_counts"]["UNMAPPED"] == 1
    assert result["collection_counts"]["NO_DATA"] == 9


def test_ten_successful_companies_reach_exact_31_call_ceiling():
    items = universe()

    class FullClient(FakeClient):
        def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
            self.request_count += 1
            index = int(corp_code) - 1
            rows = [
                {
                    **disclosure(f"20261020{index * 300 + offset:06d}"),
                    "corp_code": corp_code, "stock_code": items[index].ticker,
                }
                for offset in range((page_no - 1) * 100, page_no * 100)
            ]
            return page(rows, number=page_no, count=300)

    result = collect_disclosures(items, FullClient([], codes(items)), START, END)
    assert result["verdict"] == "PASS"
    assert result["api_request_count"] == 31
    assert result["disclosure_count"] == 3000
    assert result["collection_counts"]["SUCCESS"] == 10
    assert len({
        row["rcept_no"] for company in result["companies"] for row in company["disclosures"]
    }) == 3000


def test_cross_ticker_receipt_conflict_is_not_silently_deduplicated():
    items = universe()

    class ConflictClient(FakeClient):
        def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
            self.request_count += 1
            index = int(corp_code) - 1
            receipt = "20261020000001" if index < 2 else f"20261020{index + 1:06d}"
            return page([{
                **disclosure(receipt), "corp_code": corp_code,
                "stock_code": items[index].ticker,
            }])

    result = collect_disclosures(items, ConflictClient([], codes(items)), START, END)
    assert result["verdict"] == "FAIL"
    assert result["companies"][1]["error_code"] == "CROSS_TICKER_RECEIPT_CONFLICT"
    assert result["companies"][1]["disclosures"] == []
    assert result["disclosure_count"] == 9


def test_missing_key_persists_separate_blocked_report(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    source = cli.ROOT / cli.UNIVERSE_RELATIVE
    destination = tmp_path / cli.UNIVERSE_RELATIVE
    destination.parent.mkdir(parents=True)
    destination.write_bytes(source.read_bytes())
    original = destination.read_bytes()
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Network forbidden"))
    report, path = cli.run_collection(tmp_path, START, END)
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert path.parent == tmp_path / "output" / "dart_disclosures"
    assert report["verdict"] == "BLOCKED"
    assert report["blocked_reason"] == "MISSING_DART_API_KEY"
    assert report["universe_count"] == report["mapping_unverified_count"] == 574
    assert report["mapping_counts"] == {
        "SUCCESS": 0, "NO_DATA": 0, "ERROR": 574, "UNMAPPED": 0,
    }
    assert report["collection_counts"]["ERROR"] == 10
    assert report["collection_counts"]["NO_DATA"] == 0
    assert report["api_request_count"] == 0
    assert destination.read_bytes() == original
    second, second_path = cli.run_collection(tmp_path, START, END)
    assert path != second_path and path.exists()
    assert second["universe_sha256"] == report["universe_sha256"]
    assert not list(path.parent.glob("*.tmp"))
    assert not (tmp_path / "output" / "expanded_shadow").exists()


def test_cli_blocked_exit_code(tmp_path, monkeypatch, capsys):
    source = tmp_path / cli.UNIVERSE_RELATIVE
    source.parent.mkdir(parents=True)
    source.write_bytes((cli.ROOT / cli.UNIVERSE_RELATIVE).read_bytes())
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.delenv("DART_API_KEY", raising=False)
    assert cli.main(["--end-date", END.isoformat()]) == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_invalid_local_universe_blocks_without_request(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("DART_API_KEY", SECRET)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Network forbidden"))
    assert cli.main([]) == 2
    output = capsys.readouterr().out
    assert "BLOCKED" in output
    assert SECRET not in output


@pytest.mark.parametrize("days", [-1, 31])
def test_invalid_date_window(days):
    with pytest.raises(DartDisclosureError, match="INVALID_DATE_WINDOW"):
        collect_disclosures(universe(), None, START, START + timedelta(days=days))


def test_missing_sample_rejected():
    with pytest.raises(DartDisclosureError, match="SAMPLE_NOT_IN_UNIVERSE"):
        collect_disclosures(universe()[1:], None, START, END)


@pytest.mark.parametrize("failure,expected", [
    (requests.Timeout(SECRET), "HTTP_REQUEST_FAILED"),
    (requests.HTTPError(SECRET), "HTTP_REQUEST_FAILED"),
    (ValueError(SECRET), "INVALID_JSON"),
])
def test_http_failures_are_sanitized(monkeypatch, failure, expected):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(requests, "get", fail)
    client = DartClient(SECRET)
    with pytest.raises(DartDisclosureError, match=expected) as caught:
        client.page("00126380", START, END, 1)
    assert SECRET not in str(caught.value)


def test_http_adapter_preserves_api_status_and_requests_corrections(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "013"}

    def get(url, *, params, timeout):
        assert url == "https://opendart.fss.or.kr/api/list.json"
        assert params["last_reprt_at"] == "N"
        assert params["page_count"] == 100
        assert params["bgn_de"] == "20261001"
        assert timeout == (5, 20)
        return Response()
    monkeypatch.setattr(requests, "get", get)
    client = DartClient(SECRET)
    assert client.page("00126380", START, END, 1) == {"status": "013"}
    assert client.request_count == 1


def test_reuses_reader_downloader_without_constructor_or_cache(monkeypatch, tmp_path, capsys):
    from OpenDartReader import dart_list

    frame = codes(universe())

    def download(key):
        assert key == SECRET
        print(SECRET)
        return frame
    monkeypatch.setattr(dart_list, "corp_codes", download)
    monkeypatch.chdir(tmp_path)
    client = DartClient(SECRET)
    assert client.corp_codes() is frame
    assert client.request_count == 1
    assert not (tmp_path / "docs_cache").exists()
    assert SECRET not in capsys.readouterr().out


def test_reader_download_failure_hides_remote_details(monkeypatch, capsys):
    from OpenDartReader import dart_list

    def download(key):
        print(SECRET)
        raise ValueError(SECRET)
    monkeypatch.setattr(dart_list, "corp_codes", download)
    with pytest.raises(DartDisclosureError, match="CORP_CODES_UNAVAILABLE") as caught:
        DartClient(SECRET).corp_codes()
    assert SECRET not in str(caught.value)
    assert SECRET not in capsys.readouterr().out


FULL_START = date(2026, 10, 18)


def full_universe():
    return load_expanded_universe(cli.ROOT / cli.UNIVERSE_RELATIVE).tickers


def market_disclosure(items, index=0, *, receipt="20261020000001", title="Report"):
    return {
        **disclosure(receipt, title), "stock_code": items[index].ticker,
        "corp_code": f"{index + 1:08d}",
    }


class MarketClient(FakeClient):
    def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
        assert corp_code == ""
        assert start == FULL_START and end == END
        return super().page(corp_code, start, end, page_no)


def test_full_574_filter_corrections_duplicates_nonlisted_and_no_data():
    items = full_universe()
    original = market_disclosure(items)
    corrected = market_disclosure(items, receipt="20261020000002", title="[기재정정]Report")
    other = market_disclosure(items, 307, receipt="20261020000003")
    outside = {**disclosure("20261020000004"), "stock_code": "999999",
               "corp_code": "99999999"}
    unlisted = {**disclosure("20261020000005"), "stock_code": "",
                "corp_code": "99999998"}
    client = MarketClient([page([original, corrected, original, other, outside, unlisted])],
                          codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "PASS"
    assert result["collection_complete"] is True
    assert result["target_count"] == result["universe_count"] == 574
    assert result["mapping_counts"]["SUCCESS"] == 574
    assert result["collection_counts"] == {
        "SUCCESS": 2, "NO_DATA": 572, "ERROR": 0, "UNMAPPED": 0,
    }
    assert result["companies_with_disclosures"] == 2
    assert result["disclosure_count"] == 3
    assert result["correction_count"] == result["duplicates_removed"] == 1
    assert result["source_raw_count"] == 6
    assert result["source_unique_count"] == 5
    assert result["source_duplicates_removed"] == 1
    assert result["filtered_out_count"] == 2
    assert result["api_request_count"] == 2
    assert result["uncollected_count"] == 0
    records = [row for company in result["companies"] for row in company["disclosures"]]
    assert len({row["rcept_no"] for row in records}) == 3
    assert all(row["original_url"].endswith(row["rcept_no"]) for row in records)
    assert all(row["rcept_time"] is None for row in records)


@pytest.mark.parametrize("payload", [{"status": "013"}, page([
    {**disclosure(), "stock_code": "", "corp_code": "99999999"},
])])
def test_full_scan_empty_targets_are_verified_no_data(payload):
    items = full_universe()
    result = collect_disclosures(items, MarketClient([payload], codes(items)),
                                 FULL_START, END, scope="all")
    assert result["verdict"] == "PASS"
    assert result["collection_counts"]["NO_DATA"] == 574
    assert result["api_request_count"] == 2
    assert result["disclosure_count"] == result["companies_with_disclosures"] == 0


@pytest.mark.parametrize("second,expected", [
    (DartDisclosureError("HTTP_REQUEST_FAILED"), "HTTP_REQUEST_FAILED"),
    ({"status": "020"}, "DART_STATUS_020"),
    ({"status": "013"}, "DART_STATUS_013"),
    (page([disclosure()], number=2, count=102), "PAGINATION_CHANGED"),
    (page([disclosure()], number=3, count=101), "INVALID_PAGINATION"),
    (page([], number=2, count=101), "INVALID_DISCLOSURE_LIST"),
])
def test_full_second_page_failure_invalidates_all_574(second, expected):
    items = full_universe()
    rows = [market_disclosure(items, receipt=f"20261020{n:06d}") for n in range(100)]
    client = MarketClient([page(rows, count=101), second], codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] != "PASS"
    assert result["collection_complete"] is False
    assert result["collection_counts"]["ERROR"] == result["uncollected_count"] == 574
    assert result["collection_counts"]["NO_DATA"] == 0
    assert result["disclosure_count"] == result["correction_count"] == 0
    assert result["source_unique_count"] is None
    assert result["error_codes"] == [expected]
    assert client.request_count == 3
    assert client.pages == [1, 2]


def test_full_page_limit_exceeded_stops_after_first_page():
    items = full_universe()
    client = MarketClient([
        page([market_disclosure(items)], count=ALL_MARKET_MAX_PAGES * 100 + 1),
    ], codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["error_codes"] == ["PAGE_LIMIT_EXCEEDED"]
    assert result["api_request_count"] == 2
    assert client.pages == [1]


def test_full_exact_101_request_ceiling_and_every_target_collected():
    items = full_universe()

    class CeilingClient(MarketClient):
        def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
            assert corp_code == ""
            self.request_count += 1
            rows = [
                market_disclosure(items, n % 574, receipt=f"20261020{n:06d}")
                for n in range((page_no - 1) * 100, page_no * 100)
            ]
            return page(rows, number=page_no, count=ALL_MARKET_MAX_PAGES * 100)

    client = CeilingClient([], codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "PASS"
    assert result["api_request_count"] == result["api_request_limit"] == 101
    assert result["source_pages_fetched"] == result["list_page_limit"] == 100
    assert result["disclosure_count"] == 10000
    assert result["companies_with_disclosures"] == 574
    assert result["uncollected_count"] == 0


def test_full_repeated_page_is_not_accepted_as_deduplication():
    items = full_universe()
    rows = [market_disclosure(items, receipt=f"20261020{n:06d}") for n in range(100)]
    client = MarketClient([page(rows, count=200), page(rows, number=2, count=200)],
                          codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["error_codes"] == ["REPEATED_PAGE"]
    assert result["disclosure_count"] == 0
    assert result["uncollected_count"] == 574


@pytest.mark.parametrize("rows,expected", [
    ([disclosure()], "INVALID_DISCLOSURE"),
    ([None], "INVALID_DISCLOSURE"),
    ([{**disclosure(), "stock_code": "", "corp_code": "bad"}], "INVALID_DISCLOSURE"),
    ([{**disclosure(), "stock_code": "", "corp_code": "99999999",
       "rcept_dt": "20261017"}], "DISCLOSURE_OUTSIDE_WINDOW"),
])
def test_full_malformed_or_mismatched_rows_fail_even_outside_targets(rows, expected):
    items = full_universe()
    client = MarketClient([page(rows)], codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["error_codes"] == [expected]
    assert result["collection_counts"]["ERROR"] == 574


def test_full_conflicting_receipt_invalidates_scan():
    items = full_universe()
    rows = [market_disclosure(items), market_disclosure(items, 1)]
    result = collect_disclosures(items, MarketClient([page(rows)], codes(items)),
                                 FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["error_codes"] == ["CONFLICTING_RECEIPT"]


def test_full_short_nonfinal_page_fails_without_fetching_later_page():
    items = full_universe()
    client = MarketClient([page([market_disclosure(items)], count=101)], codes(items))
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["error_codes"] == ["INCOMPLETE_RESPONSE"]
    assert client.pages == [1]
    assert result["uncollected_count"] == 574


def test_full_cross_page_duplicate_preserves_new_rows():
    items = full_universe()
    rows = [market_disclosure(items, receipt=f"20261020{n:06d}") for n in range(100)]
    tail = [rows[-1], market_disclosure(items, 1, receipt="20261020000100")]
    result = collect_disclosures(
        items, MarketClient([page(rows, count=102), page(tail, number=2, count=102)],
                            codes(items)), FULL_START, END, scope="all",
    )
    assert result["verdict"] == "PASS"
    assert result["disclosure_count"] == 101
    assert result["duplicates_removed"] == 1


def test_full_mapping_failure_prevents_market_requests():
    items = full_universe()
    client = MarketClient([], codes(items).iloc[1:])
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["collection_counts"]["UNMAPPED"] == 1
    assert result["collection_counts"]["ERROR"] == 573
    assert result["uncollected_count"] == 574
    assert result["api_request_count"] == 1
    assert not client.pages


def test_full_duplicate_corporate_identity_prevents_market_requests():
    items = full_universe()
    frame = codes(items)
    frame.loc[1, "corp_code"] = frame.loc[0, "corp_code"]
    client = MarketClient([], frame)
    result = collect_disclosures(items, client, FULL_START, END, scope="all")
    assert result["verdict"] == "FAIL"
    assert result["error_codes"] == ["AMBIGUOUS_CORP_CODE"]
    assert client.request_count == 1


def test_full_missing_key_is_unverified_not_empty_market():
    result = collect_disclosures(full_universe(), None, FULL_START, END, scope="all")
    assert result["verdict"] == "BLOCKED"
    assert result["mapping_unverified_count"] == result["uncollected_count"] == 574
    assert result["collection_counts"]["ERROR"] == 574
    assert result["api_request_count"] == 0
    assert result["collection_complete"] is False


@pytest.mark.parametrize("days", [-1, 3, 30])
def test_full_date_window_is_bounded(days):
    with pytest.raises(DartDisclosureError, match="INVALID_DATE_WINDOW"):
        collect_disclosures(universe(), None, START, START + timedelta(days=days), scope="all")


def test_full_cli_three_day_default_persists_metrics_without_network(tmp_path, monkeypatch, capsys):
    source = tmp_path / cli.UNIVERSE_RELATIVE
    source.parent.mkdir(parents=True)
    source.write_bytes((cli.ROOT / cli.UNIVERSE_RELATIVE).read_bytes())
    original = source.read_bytes()
    items = load_expanded_universe(source).tickers
    client = MarketClient([page([market_disclosure(items)])], codes(items))
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("DART_API_KEY", SECRET)
    monkeypatch.setattr(cli, "DartClient", lambda key: client)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Network forbidden"))
    assert cli.main(["--scope", "all", "--end-date", "2026-10-20"]) == 0
    output = capsys.readouterr().out
    assert SECRET not in output
    report = json.loads(next((tmp_path / cli.OUTPUT_RELATIVE).glob("*.json")).read_text(
        encoding="utf-8",
    ))
    assert report["start_date"] == "2026-10-18"
    assert report["end_date"] == "2026-10-20"
    assert report["scope"] == "all"
    assert report["api_request_count"] == 2
    assert report["elapsed_seconds"] >= 0
    assert report["started_at"] <= report["collected_at"]
    assert report["collection_complete"] is True
    assert len(report["companies"]) == 574
    assert source.read_bytes() == original
    assert not (tmp_path / "output" / "expanded_shadow").exists()
    assert not list((tmp_path / cli.OUTPUT_RELATIVE).glob("*.tmp"))


def test_full_cli_rejects_long_window_before_reading_credentials(monkeypatch):
    monkeypatch.setattr(cli, "run_collection", lambda *a, **k: pytest.fail("Must not run"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["--scope", "all", "--lookback-days", "4"])
    assert caught.value.code == 2

"""Independent, bounded DART disclosure collection; never invokes signal logic."""

from __future__ import annotations

import io
import re
import zipfile
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any
from xml.etree.ElementTree import ParseError

import pandas as pd
import requests

from src.expanded_shadow_universe import ExpandedTicker

SAMPLE_TICKERS = (
    "005930", "000660", "005380", "035420", "068270",
    "196170", "086520", "247540", "036930", "277810",
)
MAX_PAGES = 3
ALL_MARKET_MAX_PAGES = 100
PAGE_SIZE = 100
STATUSES = ("SUCCESS", "NO_DATA", "ERROR", "UNMAPPED")


class DartDisclosureError(Exception):
    """Only safe, locally defined codes may reach reports or console output."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DartClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self.request_count = 0

    def corp_codes(self) -> pd.DataFrame:
        try:
            # Reuse the existing reader's downloader without its constructor:
            # the constructor deletes shared docs_cache files.
            from OpenDartReader.dart_list import corp_codes
        except ImportError:
            raise DartDisclosureError("OPENDARTREADER_UNAVAILABLE") from None
        self.request_count += 1
        try:
            with redirect_stdout(io.StringIO()):
                frame = corp_codes(self._api_key)
        except (requests.RequestException, ValueError, KeyError, AttributeError,
                TypeError, zipfile.BadZipFile, ParseError):
            raise DartDisclosureError("CORP_CODES_UNAVAILABLE") from None
        if not isinstance(frame, pd.DataFrame):
            raise DartDisclosureError("INVALID_CORP_CODES")
        return frame

    def page(self, corp_code: str, start: date, end: date, page_no: int) -> dict:
        self.request_count += 1
        try:
            response = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": self._api_key, "corp_code": corp_code,
                    "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": end.strftime("%Y%m%d"),
                    "last_reprt_at": "N", "sort": "date", "sort_mth": "desc",
                    "page_no": page_no, "page_count": PAGE_SIZE,
                },
                timeout=(5, 20),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            # Request exception strings can contain the authenticated URL.
            raise DartDisclosureError("HTTP_REQUEST_FAILED") from None
        except ValueError:
            raise DartDisclosureError("INVALID_JSON") from None
        if not isinstance(payload, dict):
            raise DartDisclosureError("INVALID_RESPONSE")
        return payload


@dataclass(frozen=True)
class CompanyMapping:
    ticker: str
    name: str
    market: str
    status: str
    corp_code: str | None = None
    dart_corp_name: str | None = None
    name_matches: bool | None = None
    error_code: str | None = None


def map_companies(
    universe: tuple[ExpandedTicker, ...], frame: pd.DataFrame,
) -> list[CompanyMapping]:
    required = {"stock_code", "corp_code", "corp_name"}
    if not required.issubset(frame.columns) or frame.empty:
        raise DartDisclosureError("INVALID_CORP_CODES")
    mappings = []
    for item in universe:
        matches = frame.loc[frame["stock_code"] == item.ticker,
                            ["corp_code", "corp_name"]].drop_duplicates()
        values = dict(ticker=item.ticker, name=item.name, market=item.market)
        if matches.empty:
            mappings.append(CompanyMapping(**values, status="UNMAPPED",
                                           error_code="STOCK_CODE_NOT_FOUND"))
        elif len(matches) != 1:
            mappings.append(CompanyMapping(**values, status="ERROR",
                                           error_code="AMBIGUOUS_CORP_CODE"))
        else:
            corp_code, corp_name = matches.iloc[0].tolist()
            if (not isinstance(corp_code, str)
                    or not re.fullmatch(r"[0-9]{8}", corp_code)
                    or not isinstance(corp_name, str) or not corp_name.strip()):
                mappings.append(CompanyMapping(**values, status="ERROR",
                                               error_code="INVALID_CORP_CODE"))
            else:
                mappings.append(CompanyMapping(
                    **values, status="SUCCESS", corp_code=corp_code,
                    dart_corp_name=corp_name, name_matches=corp_name == item.name,
                ))
    return mappings


def _normalize_disclosure(
    row: Any, mapping: CompanyMapping, start: date, end: date,
) -> dict:
    if not isinstance(row, dict):
        raise DartDisclosureError("INVALID_DISCLOSURE")
    receipt, title, day = (row.get(key) for key in ("rcept_no", "report_nm", "rcept_dt"))
    if (not isinstance(receipt, str) or not re.fullmatch(r"[0-9]{14}", receipt)
            or not isinstance(title, str) or not title.strip()
            or not isinstance(day, str) or not re.fullmatch(r"[0-9]{8}", day)
            or row.get("corp_code") != mapping.corp_code
            or row.get("stock_code") != mapping.ticker):
        raise DartDisclosureError("INVALID_DISCLOSURE")
    try:
        disclosure_date = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    except ValueError:
        raise DartDisclosureError("INVALID_DISCLOSURE_DATE") from None
    if not start <= disclosure_date <= end:
        raise DartDisclosureError("DISCLOSURE_OUTSIDE_WINDOW")
    correction = re.match(r"^\[([^\]]*정정[^\]]*)\]", title)
    return {
        "rcept_no": receipt, "ticker": mapping.ticker, "corp_code": mapping.corp_code,
        "report_nm": title, "rcept_dt": disclosure_date.isoformat(),
        "rcept_time": None, "rcept_time_source": "NOT_PROVIDED_BY_LIST_API",
        "original_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "is_correction": correction is not None,
        "correction_type": correction.group(1) if correction else None,
    }


def _page_content(payload: dict, page_no: int, max_pages: int) -> tuple[list, int, int]:
    pages, count = payload.get("total_page"), payload.get("total_count")
    if (type(pages) is not int or type(count) is not int
            or pages < 1 or count < 1
            or pages != (count + PAGE_SIZE - 1) // PAGE_SIZE
            or type(payload.get("page_no")) is not int
            or payload["page_no"] != page_no):
        raise DartDisclosureError("INVALID_PAGINATION")
    if pages > max_pages:
        raise DartDisclosureError("PAGE_LIMIT_EXCEEDED")
    rows = payload.get("list")
    if not isinstance(rows, list) or not rows or len(rows) > PAGE_SIZE:
        raise DartDisclosureError("INVALID_DISCLOSURE_LIST")
    return rows, pages, count


def collect_company(
    client: DartClient, mapping: CompanyMapping, start: date, end: date,
) -> dict:
    result: dict[str, Any] = {
        "ticker": mapping.ticker, "name": mapping.name, "market": mapping.market,
        "corp_code": mapping.corp_code, "status": mapping.status,
        "error_code": mapping.error_code, "api_status": None,
        "duplicates_removed": 0, "correction_count": 0, "disclosures": [],
    }
    if mapping.status != "SUCCESS":
        return result
    if mapping.corp_code is None:
        raise DartDisclosureError("INVALID_MAPPING")
    unique: dict[str, dict] = {}
    duplicates = 0
    try:
        total_pages = total_count = None
        raw_count = 0
        for page_no in range(1, MAX_PAGES + 1):
            payload = client.page(mapping.corp_code, start, end, page_no)
            status = payload.get("status")
            # Never retain remote messages or unexpected status strings.
            result["api_status"] = (
                status if isinstance(status, str) and re.fullmatch(r"[0-9]{3}", status)
                else None
            )
            if status == "013" and page_no == 1:
                if (payload.get("list") or payload.get("total_count", 0) != 0
                        or payload.get("total_page", 0) != 0):
                    raise DartDisclosureError("INCONSISTENT_NO_DATA")
                result["status"] = "NO_DATA"
                return result
            if status != "000":
                raise DartDisclosureError(
                    f"DART_STATUS_{result['api_status']}"
                    if result["api_status"] else "INVALID_API_STATUS"
                )
            rows, pages, count = _page_content(payload, page_no, MAX_PAGES)
            if total_pages is not None and (pages != total_pages or count != total_count):
                raise DartDisclosureError("PAGINATION_CHANGED")
            total_pages, total_count = pages, count
            raw_count += len(rows)
            for row in rows:
                disclosure = _normalize_disclosure(row, mapping, start, end)
                receipt = disclosure["rcept_no"]
                if receipt in unique:
                    if unique[receipt] != disclosure:
                        raise DartDisclosureError("CONFLICTING_RECEIPT")
                    duplicates += 1
                else:
                    unique[receipt] = disclosure
            if page_no == total_pages:
                break
        if raw_count != total_count:
            raise DartDisclosureError("INCOMPLETE_RESPONSE")
    except DartDisclosureError as exc:
        result.update(status="ERROR", error_code=exc.code)
        return result
    records = sorted(unique.values(), key=lambda row: (row["rcept_dt"], row["rcept_no"]),
                     reverse=True)
    result.update(status="SUCCESS", disclosures=records, duplicates_removed=duplicates,
                  correction_count=sum(row["is_correction"] for row in records))
    return result


def _collect_all_market(
    client: DartClient, mappings: list[CompanyMapping], start: date, end: date,
) -> tuple[list[dict], dict]:
    results = [
        {**asdict(mapping), "api_status": None, "duplicates_removed": 0,
         "correction_count": 0, "disclosures": []}
        for mapping in mappings
    ]
    source: dict[str, Any] = {
        "collection_method": "ALL_MARKET_FILTER",
        "list_page_limit": ALL_MARKET_MAX_PAGES,
        "api_request_limit": ALL_MARKET_MAX_PAGES + 1,
        "source_pages_fetched": 0, "source_raw_count": 0,
        "source_unique_count": None, "source_duplicates_removed": None,
        "filtered_out_count": None,
    }
    error = "MAPPING_INCOMPLETE"
    api_status = None
    if all(mapping.status == "SUCCESS" for mapping in mappings):
        by_ticker = {mapping.ticker: mapping for mapping in mappings}
        by_corp = {mapping.corp_code: mapping for mapping in mappings}
        unique: dict[str, dict] = {}
        duplicate_counts: dict[str, int] = {}
        try:
            if len(by_corp) != len(mappings):
                raise DartDisclosureError("AMBIGUOUS_CORP_CODE")
            total_pages = total_count = None
            for page_no in range(1, ALL_MARKET_MAX_PAGES + 1):
                # An empty corporate code requests all filers, including unlisted firms.
                payload = client.page("", start, end, page_no)
                source["source_pages_fetched"] += 1
                status = payload.get("status")
                api_status = (
                    status if isinstance(status, str) and re.fullmatch(r"[0-9]{3}", status)
                    else None
                )
                if status == "013" and page_no == 1:
                    if payload.get("list"):
                        raise DartDisclosureError("INCONSISTENT_NO_DATA")
                    break
                if status != "000":
                    raise DartDisclosureError(
                        f"DART_STATUS_{api_status}" if api_status else "INVALID_API_STATUS"
                    )
                rows, pages, count = _page_content(payload, page_no, ALL_MARKET_MAX_PAGES)
                if total_pages is not None and (pages != total_pages or count != total_count):
                    raise DartDisclosureError("PAGINATION_CHANGED")
                total_pages, total_count = pages, count
                expected_size = min(PAGE_SIZE, count - (page_no - 1) * PAGE_SIZE)
                if len(rows) != expected_size:
                    raise DartDisclosureError("INCOMPLETE_RESPONSE")
                source["source_raw_count"] += len(rows)
                page_receipts: set[str] = set()
                previous_receipts = set(unique)
                for row in rows:
                    if not isinstance(row, dict):
                        raise DartDisclosureError("INVALID_DISCLOSURE")
                    stock, corp = row.get("stock_code"), row.get("corp_code")
                    if (not isinstance(stock, str)
                            or (stock != "" and not re.fullmatch(r"[0-9A-Z]{6}", stock))
                            or not isinstance(corp, str)
                            or not re.fullmatch(r"[0-9]{8}", corp)):
                        raise DartDisclosureError("INVALID_DISCLOSURE")
                    mapping = by_ticker.get(stock) or by_corp.get(corp)
                    if mapping is None:
                        mapping = CompanyMapping(stock, "", "", "SUCCESS", corp)
                    disclosure = _normalize_disclosure(row, mapping, start, end)
                    receipt = disclosure["rcept_no"]
                    page_receipts.add(receipt)
                    if receipt in unique:
                        if unique[receipt] != disclosure:
                            raise DartDisclosureError("CONFLICTING_RECEIPT")
                        duplicate_counts[stock] = duplicate_counts.get(stock, 0) + 1
                    else:
                        unique[receipt] = disclosure
                if page_no > 1 and page_receipts.issubset(previous_receipts):
                    raise DartDisclosureError("REPEATED_PAGE")
                if page_no == total_pages:
                    break
            if total_count is not None and source["source_raw_count"] != total_count:
                raise DartDisclosureError("INCOMPLETE_RESPONSE")
        except DartDisclosureError as exc:
            error = exc.code
        else:
            for result in results:
                records = sorted(
                    (row for row in unique.values() if row["ticker"] == result["ticker"]),
                    key=lambda row: (row["rcept_dt"], row["rcept_no"]), reverse=True,
                )
                result.update(
                    status="SUCCESS" if records else "NO_DATA", api_status=api_status,
                    error_code=None, disclosures=records,
                    duplicates_removed=duplicate_counts.get(result["ticker"], 0),
                    correction_count=sum(row["is_correction"] for row in records),
                )
            source.update(
                source_unique_count=len(unique),
                source_duplicates_removed=sum(duplicate_counts.values()),
                filtered_out_count=sum(row["ticker"] not in by_ticker for row in unique.values()),
            )
            return results, source
    # Nothing is published as collected until every source page is validated.
    for result in results:
        if result["status"] == "SUCCESS":
            result.update(status="ERROR", error_code=error, api_status=api_status)
    return results, source


def collect_disclosures(
    universe: tuple[ExpandedTicker, ...], client: DartClient | None,
    start: date, end: date, *, scope: str = "sample",
) -> dict:
    if scope not in {"sample", "all"}:
        raise DartDisclosureError("INVALID_COLLECTION_SCOPE")
    if not 0 <= (end - start).days <= (2 if scope == "all" else 30):
        raise DartDisclosureError("INVALID_DATE_WINDOW")
    if scope == "sample" and not set(SAMPLE_TICKERS).issubset(item.ticker for item in universe):
        raise DartDisclosureError("SAMPLE_NOT_IN_UNIVERSE")
    targets = SAMPLE_TICKERS if scope == "sample" else tuple(item.ticker for item in universe)
    mappings: list[CompanyMapping] = []
    blocked_reason = None
    if client is None:
        blocked_reason = "MISSING_DART_API_KEY"
    else:
        try:
            mappings = map_companies(universe, client.corp_codes())
        except DartDisclosureError as exc:
            blocked_reason = exc.code
    if blocked_reason:
        mappings = [
            CompanyMapping(item.ticker, item.name, item.market, "ERROR",
                           error_code=blocked_reason)
            for item in universe
        ]
    by_ticker = {row.ticker: row for row in mappings}
    results = []
    source = {
        "list_page_limit": ALL_MARKET_MAX_PAGES if scope == "all" else MAX_PAGES,
        "api_request_limit": ALL_MARKET_MAX_PAGES + 1 if scope == "all" else 31,
    }
    if scope == "all" and client is not None:
        results, source = _collect_all_market(client, mappings, start, end)
    seen: set[str] = set()
    for ticker in (() if results else targets):
        mapping = by_ticker[ticker]
        if client is None:
            result = {
                **asdict(mapping), "api_status": None, "duplicates_removed": 0,
                "correction_count": 0, "disclosures": [],
            }
        else:
            result = collect_company(client, mapping, start, end)
        receipts = {row["rcept_no"] for row in result["disclosures"]}
        if seen & receipts:
            result.update(status="ERROR", error_code="CROSS_TICKER_RECEIPT_CONFLICT",
                          disclosures=[], duplicates_removed=0, correction_count=0)
        else:
            seen.update(receipts)
        results.append(result)
    mapping_counts = {status: sum(row.status == status for row in mappings)
                      for status in STATUSES}
    collection_counts = {status: sum(row["status"] == status for row in results)
                         for status in STATUSES}
    mapping_unverified_count = len(universe) if blocked_reason else 0
    if (not blocked_reason and collection_counts["ERROR"] == len(targets)
            and all(row["error_code"] in {
                "HTTP_REQUEST_FAILED", "INVALID_JSON", "DART_STATUS_010",
                "DART_STATUS_011", "DART_STATUS_012", "DART_STATUS_020",
                "DART_STATUS_800", "DART_STATUS_900",
            } for row in results)):
        blocked_reason = "LIVE_COLLECTION_UNAVAILABLE"
    verdict = "BLOCKED" if blocked_reason else (
        "FAIL" if mapping_counts["ERROR"] or mapping_counts["UNMAPPED"]
        or collection_counts["ERROR"] or collection_counts["UNMAPPED"] else "PASS"
    )
    return {
        "schema_version": 1, "verdict": verdict, "blocked_reason": blocked_reason,
        "scope": scope, "target_count": len(targets),
        "collection_method": "ALL_MARKET_FILTER" if scope == "all" else "PER_COMPANY",
        "collection_complete": verdict == "PASS",
        "uncollected_count": collection_counts["ERROR"] + collection_counts["UNMAPPED"],
        "companies_with_disclosures": collection_counts["SUCCESS"],
        "error_codes": sorted({row["error_code"] for row in results if row["error_code"]}),
        **source,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "universe_count": len(universe), "mapping_counts": mapping_counts,
        "mapping_unverified_count": mapping_unverified_count,
        "collection_counts": collection_counts,
        "api_request_count": client.request_count if client else 0,
        "mapping": [asdict(row) for row in mappings], "companies": results,
        "disclosure_count": sum(len(row["disclosures"]) for row in results),
        "duplicates_removed": sum(row["duplicates_removed"] for row in results),
        "correction_count": sum(row["correction_count"] for row in results),
    }

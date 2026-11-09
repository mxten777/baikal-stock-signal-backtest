"""Evidence-based exclusions for explicitly disclosed trading halts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol

import requests


UNISON_TICKER = "018000"
UNISON_DISCLOSURE_ID = "153834975"
UNISON_EVIDENCE_ID = "20261012_102662"
UNISON_DISCLOSURE_URL = (
    f"https://m.stock.naver.com/api/stock/{UNISON_TICKER}/disclosure/{UNISON_DISCLOSURE_ID}"
)
UNISON_STATUS_URL = f"https://m.stock.naver.com/api/stock/{UNISON_TICKER}/basic"
UNISON_HALT_REASON = "TRADING_HALT_SHARE_CONSOLIDATION"
UNISON_END_CONDITION = "신주권 변경상장일 전일까지"
NAVER_BASE_URL = "https://m.stock.naver.com/api/stock"
NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
UNISON_RELEASE_URL = (
    "https://dart.fss.or.kr/report/viewer.do?"
    "rcpNo=20261103900406&dcmNo=11639419&eleId=0&offset=0&length=0&dtd=HTML"
)
UNISON_RELEASE_SHA256 = "226297f7fe4b12bb851f5a4d1536a72d3dd9d42d81a725a4620e1e781bff3087"
UNISON_REVIEWED_HISTORICAL_SESSION = "2026-11-06"


@dataclass(frozen=True)
class OfficialHaltEvidence:
    ticker: str
    name: str
    source_date: str
    halt_start_date: str
    halt_end_condition: str
    reason: str
    company_code: str
    documents: tuple[tuple[str, str, str], ...]
    filing_ids: tuple[str, ...]

    def record(self) -> dict[str, str]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "source_date": self.source_date,
            "status": "HALTED",
            "processing_status": "HALTED_EXCLUDED",
            "reason": self.reason,
            "halt_start_date": self.halt_start_date,
            "halt_end_condition": self.halt_end_condition,
            "evidence_provider": "DART / KOSDAQ Market Division",
            "evidence_id": ",".join(item[0] for item in self.documents),
            "evidence_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.documents[0][0]}",
            "document_sha256": ",".join(item[2] for item in self.documents),
            "search_company_code": self.company_code,
            "search_start_date": "2026-10-28",
            "search_end_date": self.source_date,
            "search_filing_ids": ",".join(self.filing_ids),
            "resumption_search_status": "NO_RESUMPTION_IN_COMPLETE_REVIEWED_FILING_LIST",
        }


OFFICIAL_HALT_EVIDENCE = {
    ("032580", "2026-11-06"): OfficialHaltEvidence(
        ticker="032580", name="피델릭스", source_date="2026-11-06",
        halt_start_date="2026-10-28",
        halt_end_condition="상장적격성 실질심사 대상여부에 관한 결정일까지",
        reason="TRADING_HALT_LISTING_ELIGIBILITY_REVIEW",
        company_code="00189538",
        documents=(
            ("20261028900784", "11631904", "59ce74e71078d555302e855db05c12986a32a4ec8ff4c9b71678814b5a851bd4"),
            ("20261029900159", "11632252", "96bc204b2d78a7ccc1e3e8a4e3c6a1868d624eda1dfcb709e89d3338d6e59907"),
        ),
        filing_ids=(
            "20261029900159", "20261029000004", "20261028900784",
            "20261028900781", "20261028900777", "20261028900737", "20261028900730",
        ),
    ),
}


@dataclass(frozen=True)
class HistoricalTradingStatus:
    ticker: str
    source_date: str
    release_date: str
    release_url: str
    release_sha256: str
    market_source: str
    market_row: Mapping[str, Any]


class TradingHaltStatusUnknown(RuntimeError):
    """Raised when the disclosure or current status cannot safely be verified."""


class TradingHaltEvidenceProvider(Protocol):
    def fetch_disclosure(self) -> Mapping[str, Any]:
        ...

    def fetch_status(self, ticker: str) -> Mapping[str, Any]:
        ...


class NaverTradingHaltEvidenceProvider:
    """Fetch the cited KOSCOM disclosure and Naver's current trading status."""

    def fetch_disclosure(self) -> Mapping[str, Any]:
        return _get_json(UNISON_DISCLOSURE_URL, "disclosure")

    def fetch_status(self, ticker: str) -> Mapping[str, Any]:
        return _get_json(f"{NAVER_BASE_URL}/{ticker}/basic", "trading status")

    def fetch_status_for_date(
        self, ticker: str, source_date: str,
    ) -> Mapping[str, Any] | HistoricalTradingStatus:
        today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        if source_date != today:
            if ticker == UNISON_TICKER and source_date == UNISON_REVIEWED_HISTORICAL_SESSION:
                return _fetch_historical_trading_status(source_date)
            raise TradingHaltStatusUnknown("Current status API cannot verify a historical session")
        return self.fetch_status(ticker)


@dataclass(frozen=True)
class TradingStatusAssessment:
    source_date: str
    exclusions: tuple[dict[str, str], ...] = ()
    unknown: tuple[dict[str, str], ...] = ()


def assess_trading_status(
    source_date: str,
    tickers: Iterable[str],
    provider: TradingHaltEvidenceProvider | None = None,
) -> TradingStatusAssessment:
    _parse_date(source_date)
    exclusions: list[dict[str, str]] = []
    unknown: list[dict[str, str]] = []
    for ticker in dict.fromkeys(tickers):
        try:
            exclusions.extend(trading_halt_exclusions(source_date, (ticker,), provider))
        except TradingHaltStatusUnknown as exc:
            evidence = OFFICIAL_HALT_EVIDENCE.get((ticker, source_date))
            unknown.append({
                "ticker": ticker, "source_date": source_date,
                "dataset": "TRADING_STATUS", "status": "UNKNOWN",
                "error_code": "TRADING_STATUS_UNVERIFIED", "error_message": str(exc),
                "evidence_url": evidence.record()["evidence_url"] if evidence else UNISON_DISCLOSURE_URL,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
    return TradingStatusAssessment(source_date, tuple(exclusions), tuple(unknown))


def validate_assessment(
    assessment: TradingStatusAssessment, source_date: str, tickers: Iterable[str],
) -> None:
    ticker_set = set(tickers)
    excluded = [item["ticker"] for item in assessment.exclusions]
    unknown = [item["ticker"] for item in assessment.unknown]
    if (
        assessment.source_date != source_date
        or not validate_trading_halt_exclusions(source_date, list(assessment.exclusions))
        or len(set(excluded + unknown)) != len(excluded + unknown)
        or not set(excluded + unknown).issubset(ticker_set)
        or any(
            item.get("source_date") != source_date
            or item.get("status") != "UNKNOWN"
            or not item.get("error_code")
            for item in assessment.unknown
        )
    ):
        raise ValueError("Invalid date-specific trading status assessment")


def trading_halt_exclusions(
    source_date: str,
    tickers: Iterable[str],
    provider: TradingHaltEvidenceProvider | None = None,
) -> list[dict[str, str]]:
    _parse_date(source_date)
    ticker_set = set(tickers)
    exclusions = _unison_trading_halt_exclusions(source_date, ticker_set, provider)
    for ticker in sorted(ticker_set):
        evidence = OFFICIAL_HALT_EVIDENCE.get((ticker, source_date))
        if evidence is not None:
            verify_official_halt_evidence(evidence)
            exclusions.append(evidence.record())
    return exclusions


def _unison_trading_halt_exclusions(
    source_date: str,
    tickers: Iterable[str],
    provider: TradingHaltEvidenceProvider | None = None,
) -> list[dict[str, str]]:
    """Exclude Unison only when its disclosed halt and date-specific status agree."""
    ticker_set = set(tickers)
    if UNISON_TICKER not in ticker_set:
        return []
    target = _parse_date(source_date)
    evidence_provider = provider or NaverTradingHaltEvidenceProvider()
    disclosure = _verified_disclosure(evidence_provider.fetch_disclosure())
    if target < disclosure["halt_start_date"]:
        return []

    date_fetch = getattr(evidence_provider, "fetch_status_for_date", None)
    status = (
        date_fetch(UNISON_TICKER, source_date)
        if date_fetch is not None
        else evidence_provider.fetch_status(UNISON_TICKER)
    )
    status_as_of, status_updated_at, state = _verified_status(status, target)
    if state != "HALTED":
        return []
    return [
        {
            "ticker": UNISON_TICKER,
            "name": "Unison",
            "source_date": source_date,
            "reason": UNISON_HALT_REASON,
            "halt_start_date": disclosure["halt_start_date"].isoformat(),
            "halt_end_condition": disclosure["halt_end_condition"],
            "evidence_provider": "KOSCOM disclosure via Naver",
            "evidence_id": UNISON_EVIDENCE_ID,
            "evidence_url": UNISON_DISCLOSURE_URL,
            "status_provider": "Naver",
            "status_as_of": status_as_of,
            "status_updated_at": status_updated_at,
            "status": state,
            "status_url": UNISON_STATUS_URL,
        }
    ]


def validate_trading_halt_exclusions(
    source_date: str,
    exclusions: list[dict[str, str]],
) -> bool:
    """Validate persisted evidence without depending on live provider availability."""
    _parse_date(source_date)
    if len({item.get("ticker") for item in exclusions}) != len(exclusions):
        return False
    for record in exclusions:
        evidence = OFFICIAL_HALT_EVIDENCE.get((record.get("ticker"), source_date))
        if evidence is not None:
            if record != evidence.record():
                return False
        elif not _validate_unison_exclusion(source_date, record):
            return False
    return True


def _validate_unison_exclusion(source_date: str, record: dict[str, str]) -> bool:
    target = _parse_date(source_date)
    if (
        record.get("ticker") != UNISON_TICKER
        or record.get("source_date") != source_date
        or record.get("reason") != UNISON_HALT_REASON
        or record.get("halt_start_date") != "2026-10-15"
        or record.get("halt_end_condition") != UNISON_END_CONDITION
        or record.get("evidence_id") != UNISON_EVIDENCE_ID
        or record.get("evidence_url") != UNISON_DISCLOSURE_URL
        or target < date(2026, 10, 15)
    ):
        return False
    if record.get("status_provider") not in (None, "Naver"):
        return False
    if record.get("status") not in (None, "HALTED"):
        return False
    if record.get("status_as_of") and record["status_as_of"][:10] != source_date:
        return False
    return record.get("status_url") in (None, UNISON_STATUS_URL)


def verify_official_halt_evidence(evidence: OfficialHaltEvidence) -> dict[str, bytes]:
    """Recheck pinned official originals and the complete reviewed filing list."""
    from lxml import html

    if OFFICIAL_HALT_EVIDENCE.get((evidence.ticker, evidence.source_date)) != evidence:
        raise TradingHaltStatusUnknown("Official halt evidence is not registered for this ticker/session")
    documents: dict[str, bytes] = {}
    try:
        for receipt, document_id, digest in evidence.documents:
            response = requests.get(
                "https://dart.fss.or.kr/report/viewer.do",
                params={"rcpNo": receipt, "dcmNo": document_id, "eleId": 0,
                        "offset": 0, "length": 0, "dtd": "HTML"},
                headers=NAVER_HEADERS, timeout=15,
            )
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != digest:
                raise TradingHaltStatusUnknown("Official halt original differs from reviewed evidence")
            documents[receipt] = response.content
        response = requests.post(
            "https://dart.fss.or.kr/dsab001/search.ax",
            data={"currentPage": "1", "maxResults": "100", "maxLinks": "10",
                  "sort": "date", "series": "desc", "textCrpCik": evidence.company_code,
                  "pageGubun": "corp", "textCrpNm": evidence.name,
                  "startDate": "20261028", "endDate": evidence.source_date.replace("-", ""),
                  "autoSearchCorp": "Y", "attachDocNm": ""},
            headers=NAVER_HEADERS, timeout=15,
        )
        response.raise_for_status()
        document = html.fromstring(response.content.decode("utf-8"))
        text = " ".join(document.text_content().split())
        filings = document.xpath('//a[contains(@href, "rcpNo=")]')
        ids = tuple(re.search(r"rcpNo=(\d{14})", item.get("href", "")).group(1) for item in filings)
        if (
            ids != evidence.filing_ids or "[1/1]" not in text
            or f"[총 {len(ids)}건]" not in text
            or any(word in " ".join(item.text_content().split())
                   for item in filings for word in ("거래재개", "거래정지해제", "거래정지 해제"))
        ):
            raise TradingHaltStatusUnknown("Complete official filing history differs; halt requires review")
        documents["filing_search"] = response.content
    except (requests.RequestException, UnicodeError, ValueError, AttributeError) as exc:
        raise TradingHaltStatusUnknown(f"Official halt evidence verification failed: {exc}") from exc
    return documents


def _verified_disclosure(payload: Mapping[str, Any]) -> dict[str, Any]:
    disclosure = payload.get("disclosure")
    if (
        payload.get("itemCode") != UNISON_TICKER
        or not isinstance(disclosure, Mapping)
        or str(disclosure.get("disclosureId")) != UNISON_DISCLOSURE_ID
        or disclosure.get("itemCode") != UNISON_TICKER
    ):
        raise TradingHaltStatusUnknown("Unison trading-halt disclosure identity is unverified")
    contents = disclosure.get("contents")
    if not isinstance(contents, str) or "주권매매거래정지" not in contents:
        raise TradingHaltStatusUnknown("Unison trading-halt disclosure content is unavailable")
    start_match = re.search(r"정지일시.*?(\d{4}-\d{2}-\d{2})", contents, re.DOTALL)
    if not start_match or UNISON_END_CONDITION not in contents:
        raise TradingHaltStatusUnknown("Unison trading-halt period is unverified")
    try:
        halt_start = date.fromisoformat(start_match.group(1))
    except ValueError as exc:
        raise TradingHaltStatusUnknown("Unison trading-halt start date is invalid") from exc
    if halt_start.isoformat() != "2026-10-15":
        raise TradingHaltStatusUnknown("Unison trading-halt start date differs from reviewed disclosure")
    return {
        "halt_start_date": halt_start,
        "halt_end_condition": UNISON_END_CONDITION,
    }


def _verified_status(
    payload: Mapping[str, Any] | HistoricalTradingStatus,
    source_date: date,
) -> tuple[str, str, str]:
    if isinstance(payload, HistoricalTradingStatus):
        if (
            payload.ticker != UNISON_TICKER
            or payload.source_date != source_date.isoformat()
            or payload.source_date != UNISON_REVIEWED_HISTORICAL_SESSION
            or payload.release_date != "2026-11-04"
            or payload.release_url != UNISON_RELEASE_URL
            or payload.release_sha256 != UNISON_RELEASE_SHA256
            or payload.market_source != "FinanceDataReader"
        ):
            raise TradingHaltStatusUnknown("Historical trading evidence identity is unverified")
        _validate_historical_market_row(payload.market_row, payload.source_date)
        return payload.source_date, payload.release_date, "ACTIVE"
    item_code = payload.get("itemCode")
    local_traded_at = payload.get("localTradedAt")
    updated_at = payload.get("tradableStatusUpdatedAt")
    stop_type = payload.get("tradeStopType")
    state = payload.get("tradableStatus")
    if (
        item_code != UNISON_TICKER
        or not isinstance(local_traded_at, str)
        or not isinstance(updated_at, str)
        or not isinstance(stop_type, Mapping)
        or not isinstance(state, str)
    ):
        raise TradingHaltStatusUnknown("Unison current trading status is incomplete")
    try:
        as_of_datetime = datetime.fromisoformat(local_traded_at)
        updated_datetime = datetime.fromisoformat(updated_at)
    except ValueError as exc:
        raise TradingHaltStatusUnknown("Unison trading-status timestamps are invalid") from exc
    if as_of_datetime.date() != source_date or updated_datetime.date() > source_date:
        raise TradingHaltStatusUnknown(
            f"Unison trading status is not verified for {source_date.isoformat()}"
        )
    if state == "halt" and str(stop_type.get("code")) == "2":
        return local_traded_at, updated_at, "HALTED"
    if state in {"tradable", "trading", "normal"} and str(stop_type.get("code")) != "2":
        return local_traded_at, updated_at, "ACTIVE"
    raise TradingHaltStatusUnknown("Unison trading status is ambiguous")


def _verify_release_document(body: bytes) -> None:
    from lxml import html

    if hashlib.sha256(body).hexdigest() != UNISON_RELEASE_SHA256:
        raise TradingHaltStatusUnknown("Reviewed official release document hash differs")
    try:
        document = html.fromstring(body.decode("euc-kr"))
        rows = [
            [" ".join(cell.text_content().split()) for cell in row.xpath("./td|./th")]
            for row in document.xpath("//tr")
        ]
    except (UnicodeError, ValueError) as exc:
        raise TradingHaltStatusUnknown("Official release document cannot be decoded") from exc
    required = [
        ["1.대상종목", "유니슨(주)", "보통주"],
        ["2.해제사유", "액면병합 주권 변경상장"],
        ["3.해제일시", "2026-11-04", "-"],
    ]
    if any(rows.count(row) != 1 for row in required):
        raise TradingHaltStatusUnknown("Official release company, reason or date is unverified")


def _validate_historical_market_row(row: Mapping[str, Any], source_date: str) -> None:
    import pandas as pd
    from src.expanded_shadow_data import ExpandedDataValidationError, validate_market_frame

    try:
        frame, latest = validate_market_frame(pd.DataFrame([dict(row)]), UNISON_TICKER, source_date)
        if latest != source_date or len(frame) != 1 or frame.iloc[0]["volume"] <= 0:
            raise ValueError("Exact-session positive-volume trade is required")
    except (ExpandedDataValidationError, ValueError, TypeError, KeyError) as exc:
        raise TradingHaltStatusUnknown(f"Historical trade is unverified: {exc}") from exc


def _fetch_historical_trading_status(source_date: str) -> HistoricalTradingStatus:
    import pandas as pd
    from src.expanded_shadow_data import ExpandedDataError, FinanceDataReaderMarketSource

    try:
        response = requests.get(UNISON_RELEASE_URL, headers=NAVER_HEADERS, timeout=15)
        response.raise_for_status()
        _verify_release_document(response.content)
        frame = FinanceDataReaderMarketSource().fetch(UNISON_TICKER, source_date, source_date)
    except (requests.RequestException, ExpandedDataError, ValueError, OSError) as exc:
        raise TradingHaltStatusUnknown(f"Historical evidence source failed: {exc}") from exc
    if not isinstance(frame, pd.DataFrame) or "date" not in frame.columns:
        raise TradingHaltStatusUnknown("Historical market source has no date column")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any() or len(frame) != 1 or dates.iloc[0].date().isoformat() != source_date:
        raise TradingHaltStatusUnknown("Historical market source did not return the exact session")
    row = frame.iloc[0].to_dict()
    row["date"] = source_date
    row.setdefault("ticker", UNISON_TICKER)
    _validate_historical_market_row(row, source_date)
    return HistoricalTradingStatus(
        UNISON_TICKER, source_date, "2026-11-04", UNISON_RELEASE_URL,
        UNISON_RELEASE_SHA256, "FinanceDataReader", row,
    )


def _get_json(url: str, label: str) -> Mapping[str, Any]:
    try:
        response = requests.get(url, headers=NAVER_HEADERS, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, TypeError, ValueError) as exc:
        raise TradingHaltStatusUnknown(f"Unable to verify Unison {label}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise TradingHaltStatusUnknown(f"Unison {label} response is not an object")
    return payload


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TradingHaltStatusUnknown(f"Invalid trading-halt source date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise TradingHaltStatusUnknown(f"Invalid trading-halt source date: {value!r}")
    return parsed

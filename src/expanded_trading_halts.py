"""Evidence-based exclusions for explicitly disclosed trading halts."""

from __future__ import annotations

import re
from datetime import date, datetime
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


def trading_halt_exclusions(
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

    status = evidence_provider.fetch_status(UNISON_TICKER)
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
    target = _parse_date(source_date)
    if not exclusions:
        return True
    if len(exclusions) != 1:
        return False
    record = exclusions[0]
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
    payload: Mapping[str, Any],
    source_date: date,
) -> tuple[str, str, str]:
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

"""Isolated Company Profile collection and storage for Expanded Shadow.

Market cap is an exact KRW integer with an independent trading date and source.
AVAILABLE requires a positive JS-safe integer, ISO date, and source; UNAVAILABLE
and ERROR require null value/date, and ERROR requires an error code. Legacy rows
default to UNAVAILABLE. Invalid rows are rejected with loader warnings, never
coerced. This contract does not collect market caps or change signal decisions.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal

import pandas as pd

from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_universe import ExpandedTicker, load_expanded_universe


PROFILE_FILENAME = "expanded_company_profiles.json"
MarketCapStatus = Literal["AVAILABLE", "UNAVAILABLE", "ERROR"]
MAX_SAFE_MARKET_CAP = 2**53 - 1
PROFILE_FIELDS = (
    "ticker",
    "company_name",
    "market",
    "one_line_description",
    "sector",
    "main_business_products",
    "market_cap",
    "market_cap_date",
    "profile_as_of",
    "source",
    "collected_at",
    "market_cap_source",
    "market_cap_status",
    "market_cap_error",
)


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    company_name: str
    market: str
    one_line_description: str | None
    sector: str | None
    main_business_products: str | None
    market_cap: int | None
    market_cap_date: str | None
    profile_as_of: str
    source: str
    collected_at: str
    market_cap_source: str | None = None
    market_cap_status: MarketCapStatus = "UNAVAILABLE"
    market_cap_error: str | None = None

    def __post_init__(self) -> None:
        _validate_market_cap(
            self.market_cap,
            self.market_cap_date,
            self.market_cap_source,
            self.market_cap_status,
            self.market_cap_error,
        )


def _validate_market_cap(
    value: object,
    as_of: object,
    source: object,
    status: object,
    error: object,
) -> tuple[int | None, str | None, str | None, MarketCapStatus, str | None]:
    if status not in ("AVAILABLE", "UNAVAILABLE", "ERROR"):
        raise ValueError("market_cap_status must be AVAILABLE, UNAVAILABLE, or ERROR")
    for field, text in (("market_cap_source", source), ("market_cap_error", error)):
        if text is not None and (
            not isinstance(text, str) or not text or text != text.strip()
        ):
            raise ValueError(f"{field} must be a nonempty string without surrounding whitespace or null")
    if status == "AVAILABLE":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= MAX_SAFE_MARKET_CAP:
            raise ValueError("market_cap must be a positive JS-safe integer in KRW")
        if not isinstance(as_of, str) or len(as_of) != 10:
            raise ValueError("market_cap_date must be a valid YYYY-MM-DD date")
        try:
            parsed_date = date.fromisoformat(as_of)
        except ValueError as exc:
            raise ValueError("market_cap_date must be a valid YYYY-MM-DD date") from exc
        if parsed_date.isoformat() != as_of:
            raise ValueError("market_cap_date must be a valid YYYY-MM-DD date")
        if not isinstance(source, str):
            raise ValueError("AVAILABLE market_cap requires market_cap_source")
        if error is not None:
            raise ValueError("AVAILABLE market_cap must have null market_cap_error")
    else:
        if value is not None or as_of is not None:
            raise ValueError("UNAVAILABLE/ERROR market_cap must have null value and date")
        if status == "ERROR" and error is None:
            raise ValueError("ERROR market_cap requires market_cap_error")
        if status == "UNAVAILABLE" and error is not None:
            raise ValueError("UNAVAILABLE market_cap must have null market_cap_error")
    # Explicit narrowing keeps untrusted JSON out of the typed profile constructor.
    return (
        value if isinstance(value, int) else None,
        as_of if isinstance(as_of, str) else None,
        source if isinstance(source, str) else None,
        status,
        error if isinstance(error, str) else None,
    )


def build_company_profiles(
    universe: Iterable[ExpandedTicker],
    listing: pd.DataFrame | None,
    *,
    collected_at: str | None = None,
) -> list[CompanyProfile]:
    """Join KRX/KIND descriptive fields onto the controlled universe."""
    timestamp = collected_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    profile_date = _profile_date(timestamp)
    listing_by_ticker = _listing_by_ticker(listing)
    profiles = []

    for ticker_info in universe:
        listing_row = listing_by_ticker.get(ticker_info.ticker, {})
        sector = _optional_text(listing_row.get("Sector"))
        products = _optional_text(listing_row.get("Industry"))
        description = _description(sector, products)
        source_parts = ["KRX_KIND_LISTING" if listing_row else "EXPANDED_UNIVERSE_FALLBACK"]
        if description is not None:
            source_parts.append("one_line_description=GENERATED_TEMPLATE")
        else:
            source_parts.append("one_line_description=UNAVAILABLE")
        source_parts.append("market_cap=UNAVAILABLE")

        profiles.append(
            CompanyProfile(
                ticker=ticker_info.ticker,
                company_name=_optional_text(listing_row.get("Name")) or ticker_info.name,
                market=_optional_text(listing_row.get("Market")) or ticker_info.market,
                one_line_description=description,
                sector=sector,
                main_business_products=products,
                market_cap=None,
                market_cap_date=None,
                profile_as_of=profile_date,
                source="; ".join(source_parts),
                collected_at=timestamp,
            )
        )
    return profiles


def collect_company_profiles(
    repo_root: Path,
    *,
    listing: pd.DataFrame | None = None,
    listing_fetcher: Callable[[], pd.DataFrame] | None = None,
    now_func: Callable[[], str] | None = None,
) -> tuple[list[CompanyProfile], dict[str, int]]:
    """Build one fallback-safe profile per controlled Expanded ticker."""
    paths = ExpandedShadowPaths(Path(repo_root))
    universe = load_expanded_universe(paths.controlled_universe_path)
    resolved_listing = listing
    if resolved_listing is None:
        try:
            fetch = listing_fetcher or _fetch_krx_kind_listing
            resolved_listing = fetch()
        except Exception:
            resolved_listing = None

    timestamp = now_func() if now_func is not None else datetime.now(timezone.utc).isoformat(timespec="seconds")
    profiles = build_company_profiles(universe.tickers, resolved_listing, collected_at=timestamp)
    stats = {
        "universe_count": len(universe.tickers),
        "profile_count": len(profiles),
        "krx_kind_count": sum(profile.source.startswith("KRX_KIND_LISTING") for profile in profiles),
        "fallback_count": sum(profile.source.startswith("EXPANDED_UNIVERSE_FALLBACK") for profile in profiles),
        "description_count": sum(profile.one_line_description is not None for profile in profiles),
        "missing_sector_count": sum(profile.sector is None for profile in profiles),
        "missing_products_count": sum(profile.main_business_products is None for profile in profiles),
        "market_cap_count": sum(profile.market_cap is not None for profile in profiles),
    }
    return profiles, stats


def save_company_profiles(path: Path, profiles: Iterable[CompanyProfile]) -> Path:
    """Atomically write UTF-8 JSON inside the Expanded data namespace."""
    rows = [asdict(profile) for profile in profiles]
    if any(tuple(row) != PROFILE_FIELDS for row in rows):
        raise ValueError("Company Profile fields do not match the profile schema")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return target


def load_company_profiles(path: Path) -> tuple[dict[str, CompanyProfile], list[str]]:
    """Load valid profile rows; isolate missing or malformed rows."""
    target = Path(path)
    if not target.is_file():
        return {}, [f"Company Profile file not found: {target}"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"Company Profile file unavailable: {type(exc).__name__}"]
    if not isinstance(payload, list):
        return {}, ["Company Profile payload must be a list"]

    profiles: dict[str, CompanyProfile] = {}
    warnings: list[str] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            warnings.append(f"Company Profile row {index} is not an object")
            continue
        ticker = _optional_text(row.get("ticker"))
        company_name = _optional_text(row.get("company_name"))
        market = _optional_text(row.get("market"))
        if not ticker or not company_name or not market:
            warnings.append(f"Company Profile row {index} is missing identity fields")
            continue
        if ticker in profiles:
            warnings.append(f"Duplicate Company Profile ticker: {ticker}")
            continue
        try:
            cap, cap_date, cap_source, cap_status, cap_error = _validate_market_cap(
                row.get("market_cap"),
                row.get("market_cap_date"),
                row.get("market_cap_source"),
                row.get("market_cap_status", "UNAVAILABLE"),
                row.get("market_cap_error"),
            )
        except ValueError as exc:
            warnings.append(f"Company Profile row {index} ({ticker}) has invalid market cap: {exc}")
            continue
        profiles[ticker] = CompanyProfile(
            ticker=ticker,
            company_name=company_name,
            market=market,
            one_line_description=_optional_text(row.get("one_line_description")),
            sector=_optional_text(row.get("sector")),
            main_business_products=_optional_text(row.get("main_business_products")),
            market_cap=cap,
            market_cap_date=cap_date,
            profile_as_of=_optional_text(row.get("profile_as_of")) or "",
            source=_optional_text(row.get("source")) or "UNKNOWN",
            collected_at=_optional_text(row.get("collected_at")) or "",
            market_cap_source=cap_source,
            market_cap_status=cap_status,
            market_cap_error=cap_error,
        )
    return profiles, warnings


def profile_store_path(repo_root: Path) -> Path:
    paths = ExpandedShadowPaths(Path(repo_root))
    return paths.validate_data_path(paths.data_root / "company_profiles" / PROFILE_FILENAME)


def _fetch_krx_kind_listing() -> pd.DataFrame:
    from FinanceDataReader.krx.listing import KrxStockListing

    original_context_factory = ssl._create_default_https_context
    try:
        return KrxStockListing("KRX-DESC").read()
    finally:
        ssl._create_default_https_context = original_context_factory


def _listing_by_ticker(listing: pd.DataFrame | None) -> dict[str, dict[str, object]]:
    if listing is None or listing.empty or "Code" not in listing.columns:
        return {}
    result: dict[str, dict[str, object]] = {}
    for row in listing.to_dict(orient="records"):
        ticker = _normalize_ticker(row.get("Code"))
        if ticker and ticker not in result:
            result[ticker] = row
    return result


def _normalize_ticker(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6)


def _optional_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _description(sector: str | None, products: str | None) -> str | None:
    if sector is None and products is None:
        return None
    return f"업종은 {sector or '정보 없음'}이며, 주요 제품은 {products or '정보 없음'}입니다."


def _profile_date(timestamp: str) -> str:
    try:
        moment = datetime.fromisoformat(timestamp)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    except ValueError:
        moment = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    except Exception:  # pragma: no cover - timezone database fallback
        return moment.astimezone(timezone.utc).date().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Expanded Company Profiles from KRX/KIND")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    profiles, stats = collect_company_profiles(arguments.repo_root)
    if not arguments.dry_run:
        save_company_profiles(profile_store_path(arguments.repo_root), profiles)
    print(json.dumps({**stats, "dry_run": arguments.dry_run}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
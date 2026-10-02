from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.expanded_company_profiles import (
    CompanyProfile,
    PROFILE_FIELDS,
    build_company_profiles,
    collect_company_profiles,
    load_company_profiles,
    save_company_profiles,
)
from src.expanded_shadow_universe import ExpandedTicker


def _universe() -> tuple[ExpandedTicker, ...]:
    return (
        ExpandedTicker("000001", "Universe Name", "KOSPI", "2026-09-17"),
        ExpandedTicker("000002", "Missing Listing", "KOSDAQ", "2026-09-17"),
    )


def test_company_profiles_join_krx_fields_and_keep_market_cap_null(tmp_path: Path):
    profiles = build_company_profiles(
        _universe(),
        pd.DataFrame([{"Code": "1", "Name": "KRX Name", "Market": "KOSPI", "Sector": "반도체", "Industry": "메모리"}]),
        collected_at="2026-10-02T01:00:00+00:00",
    )

    profile = profiles[0]
    assert profile.ticker == "000001"
    assert profile.company_name == "KRX Name"
    assert profile.sector == "반도체"
    assert profile.main_business_products == "메모리"
    assert profile.one_line_description == "업종은 반도체이며, 주요 제품은 메모리입니다."
    assert "GENERATED_TEMPLATE" in profile.source
    assert profile.market_cap is None
    assert profile.market_cap_date is None
    assert profile.profile_as_of == "2026-10-02"


def test_missing_listing_fields_fall_back_to_universe_identity():
    profiles = build_company_profiles(_universe(), pd.DataFrame(columns=["Code", "Name", "Market", "Sector", "Industry"]))

    assert len(profiles) == 2
    assert profiles[1].company_name == "Missing Listing"
    assert profiles[1].market == "KOSDAQ"
    assert profiles[1].sector is None
    assert profiles[1].main_business_products is None
    assert profiles[1].one_line_description is None
    assert profiles[1].source.startswith("EXPANDED_UNIVERSE_FALLBACK")


def test_profile_collection_source_failure_keeps_all_universe_records(tmp_path: Path):
    universe_path = tmp_path / "data/expanded_shadow/universe/expanded_universe_574.csv"
    universe_path.parent.mkdir(parents=True)
    rows = [
        {
            "ticker": f"{index:06d}",
            "name": f"Company {index}",
            "market": "KOSPI" if index < 267 else "KOSDAQ",
            "source_basDd": "2026-09-17",
        }
        for index in range(574)
    ]
    pd.DataFrame(rows).to_csv(universe_path, index=False)

    profiles, stats = collect_company_profiles(
        tmp_path,
        listing_fetcher=lambda: (_ for _ in ()).throw(RuntimeError("source unavailable")),
        now_func=lambda: "2026-10-02T01:00:00+00:00",
    )

    assert stats["universe_count"] == 574
    assert stats["fallback_count"] == 574
    assert stats["market_cap_count"] == 0
    assert len(profiles) == 574
    assert [profiles[0].ticker, profiles[-1].ticker] == ["000000", "000573"]


def test_profile_json_round_trip_is_utf8_and_missing_fields_are_nullable(tmp_path: Path):
    profile = CompanyProfile(
        ticker="000001",
        company_name="한글 회사",
        market="KOSPI",
        one_line_description="업종은 반도체이며, 주요 제품은 메모리입니다.",
        sector="반도체",
        main_business_products="메모리",
        market_cap=None,
        market_cap_date=None,
        profile_as_of="2026-10-02",
        source="KRX_KIND_LISTING; one_line_description=GENERATED_TEMPLATE",
        collected_at="2026-10-02T01:00:00+00:00",
    )
    path = save_company_profiles(tmp_path / "profiles.json", [profile])
    payload = path.read_text(encoding="utf-8")
    loaded, warnings = load_company_profiles(path)

    assert "한글 회사" in payload
    assert tuple(json.loads(payload)[0]) == PROFILE_FIELDS
    assert loaded["000001"] == profile
    assert warnings == []

    partial_path = tmp_path / "partial.json"
    partial_path.write_text(json.dumps([{"ticker": "000002", "company_name": "Two", "market": "KOSDAQ"}]), encoding="utf-8")
    partial, partial_warnings = load_company_profiles(partial_path)
    assert partial["000002"].market_cap is None
    assert partial["000002"].sector is None
    assert partial_warnings == []
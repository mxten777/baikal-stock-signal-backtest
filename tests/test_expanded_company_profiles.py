from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
import pytest

from src.expanded_company_profiles import (
    CompanyProfile,
    PROFILE_FIELDS,
    MAX_SAFE_MARKET_CAP,
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
    assert profile.market_cap_status == "UNAVAILABLE"
    assert profile.market_cap_source is None
    assert profile.market_cap_error is None
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


def _profile() -> CompanyProfile:
    return CompanyProfile(
        ticker="000001",
        company_name="Company",
        market="KOSPI",
        one_line_description="Existing description",
        sector="Existing industry",
        main_business_products="Existing business",
        market_cap=None,
        market_cap_date=None,
        profile_as_of="2026-10-02",
        source="KRX_KIND_LISTING",
        collected_at="2026-10-02T01:00:00+00:00",
    )


@pytest.mark.parametrize("status", ["AVAILABLE", "UNAVAILABLE", "ERROR"])
def test_market_cap_contract_round_trip_preserves_description(tmp_path: Path, status: str):
    original = _profile()
    row = asdict(original)
    row["market_cap_status"] = status
    if status == "AVAILABLE":
        row.update(
            market_cap=12_345_000_000_000,
            market_cap_date="2026-10-02",
            market_cap_source="KRX_MDCSTAT01501",
        )
    elif status == "ERROR":
        row.update(market_cap_source="KRX_MDCSTAT01501", market_cap_error="FETCH_FAILED")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps([row]), encoding="utf-8")
    profiles, warnings = load_company_profiles(fixture)
    assert warnings == []
    profile = profiles[original.ticker]
    assert asdict(profile) == row
    assert profile.sector == original.sector
    assert profile.main_business_products == original.main_business_products
    assert profile.one_line_description == original.one_line_description
    saved = save_company_profiles(tmp_path / "round_trip.json", [profile])
    assert json.loads(saved.read_text(encoding="utf-8")) == [row]
    assert load_company_profiles(saved) == (profiles, [])
    if status == "AVAILABLE":
        assert type(profile.market_cap) is int


def test_legacy_json_preserves_all_existing_company_information(tmp_path: Path):
    original = _profile()
    row = asdict(original)
    for field in ("market_cap_source", "market_cap_status", "market_cap_error"):
        del row[field]
    fixture = tmp_path / "legacy.json"
    fixture.write_text(json.dumps([row]), encoding="utf-8")
    profiles, warnings = load_company_profiles(fixture)
    assert warnings == []
    assert profiles[original.ticker] == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("market_cap", None),
        ("market_cap", 0),
        ("market_cap", -1),
        ("market_cap", True),
        ("market_cap", 1.0),
        ("market_cap", 1.5),
        ("market_cap", "123"),
        ("market_cap", float("nan")),
        ("market_cap", float("inf")),
        ("market_cap", MAX_SAFE_MARKET_CAP + 1),
        ("market_cap_date", None),
        ("market_cap_date", "2026-02-30"),
        ("market_cap_date", "2026-13-01"),
        ("market_cap_date", "20261002"),
        ("market_cap_date", "2026-1-02"),
        ("market_cap_date", "2026-10-02T00:00:00"),
        ("market_cap_date", " 2026-10-02"),
        ("market_cap_date", 20261002),
        ("market_cap_source", None),
        ("market_cap_source", ""),
        ("market_cap_source", " "),
        ("market_cap_source", 123),
        ("market_cap_source", " KRX"),
        ("market_cap_error", "FETCH_FAILED"),
        ("market_cap_status", "UNKNOWN"),
        ("market_cap_status", None),
        ("market_cap_status", []),
    ],
)
def test_invalid_available_market_cap_is_rejected_without_coercion(
    tmp_path: Path, field: str, value: object
):
    valid = replace(
        _profile(),
        market_cap=1,
        market_cap_date="2024-02-29",
        market_cap_source="KRX_MDCSTAT01501",
        market_cap_status="AVAILABLE",
    )
    row = asdict(valid)
    row[field] = value
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps([row, asdict(replace(_profile(), ticker="000002"))]), encoding="utf-8")
    profiles, warnings = load_company_profiles(fixture)
    assert set(profiles) == {"000002"}
    assert len(warnings) == 1
    assert "000001" in warnings[0]
    assert "invalid market cap" in warnings[0]
    with pytest.raises(ValueError):
        replace(valid, **{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"market_cap": 1},
        {"market_cap_date": "2026-10-02"},
        {"market_cap_error": "FETCH_FAILED"},
        {"market_cap_status": "ERROR"},
        {"market_cap_status": "ERROR", "market_cap_error": ""},
        {"market_cap_status": "ERROR", "market_cap_error": 123},
        {"market_cap_status": "ERROR", "market_cap_error": "FETCH_FAILED", "market_cap": 1},
        {"market_cap_status": "ERROR", "market_cap_error": "FETCH_FAILED", "market_cap_date": "2026-10-02"},
    ],
)
def test_invalid_unavailable_or_error_contract_is_rejected(tmp_path: Path, changes: dict[str, object]):
    row = asdict(_profile())
    row.update(changes)
    fixture = tmp_path / "invalid_state.json"
    fixture.write_text(json.dumps([row]), encoding="utf-8")
    profiles, warnings = load_company_profiles(fixture)
    assert profiles == {}
    assert len(warnings) == 1
    with pytest.raises(ValueError):
        replace(_profile(), **changes)


def test_market_cap_safe_integer_boundary():
    profile = replace(
        _profile(),
        market_cap=MAX_SAFE_MARKET_CAP,
        market_cap_date="2026-10-02",
        market_cap_source="KRX_MDCSTAT01501",
        market_cap_status="AVAILABLE",
    )
    assert profile.market_cap == MAX_SAFE_MARKET_CAP


def test_existing_snapshot_loads_without_changes():
    path = (
        Path(__file__).resolve().parents[1]
        / "data/expanded_shadow/company_profiles/expanded_company_profiles.json"
    )
    before = path.read_bytes()
    rows = json.loads(before)
    profiles, warnings = load_company_profiles(path)
    assert warnings == []
    assert len(profiles) == len(rows) == 574
    for row in rows:
        profile = asdict(profiles[row["ticker"]])
        assert {field: profile[field] for field in row} == row
        assert profile["market_cap_status"] == "UNAVAILABLE"
        assert profile["market_cap_source"] is None
        assert profile["market_cap_error"] is None
    assert path.read_bytes() == before
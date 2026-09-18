from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.expanded_shadow_universe import (
    EXPECTED_BAS_DD,
    ExpandedUniverseValidationError,
    compute_universe_sha256,
    create_universe_snapshot,
    load_expanded_universe,
)


KNOWN_ALPHANUMERIC = ["0015N0", "0007C0", "0011A0", "0126Z0", "0120G0"]


def _valid_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    kospi_codes = ["0126Z0", "0120G0"] + [f"{number:06d}" for number in range(1, 266)]
    kosdaq_codes = ["0015N0", "0007C0", "0011A0"] + [f"{number:06d}" for number in range(300000, 300304)]
    for code in kospi_codes:
        rows.append({"ticker": code, "name": f"KOSPI {code}", "market": "KOSPI", "source_basDd": EXPECTED_BAS_DD})
    for code in kosdaq_codes:
        rows.append({"ticker": code, "name": f"KOSDAQ {code}", "market": "KOSDAQ", "source_basDd": EXPECTED_BAS_DD})
    assert len(rows) == 574
    return rows


def _write_universe(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "name", "market", "source_basDd"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _valid_universe_file(tmp_path: Path) -> Path:
    return _write_universe(tmp_path / "expanded_universe_574.csv", _valid_rows())


def test_valid_574_passes(tmp_path: Path):
    universe = load_expanded_universe(_valid_universe_file(tmp_path))

    assert universe.row_count == 574
    assert universe.market_counts == {"KOSPI": 267, "KOSDAQ": 307}


def test_valid_alphanumeric_tickers_pass(tmp_path: Path):
    universe = load_expanded_universe(_valid_universe_file(tmp_path))

    loaded = {item.ticker for item in universe.tickers}

    assert set(KNOWN_ALPHANUMERIC).issubset(loaded)


def test_known_pattern_preserved_exactly(tmp_path: Path):
    universe = load_expanded_universe(_valid_universe_file(tmp_path))
    record = next(item for item in universe.tickers if item.ticker == "0015N0")

    assert record.ticker == "0015N0"


def test_573_rows_fail(tmp_path: Path):
    path = _write_universe(tmp_path / "universe.csv", _valid_rows()[:-1])

    with pytest.raises(ExpandedUniverseValidationError):
        load_expanded_universe(path)


def test_575_rows_fail(tmp_path: Path):
    rows = _valid_rows()
    rows.append({"ticker": "999999", "name": "extra", "market": "KOSDAQ", "source_basDd": EXPECTED_BAS_DD})
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError):
        load_expanded_universe(path)


def test_duplicate_ticker_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[10]["ticker"] = rows[0]["ticker"]
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="duplicate"):
        load_expanded_universe(path)


def test_invalid_identifier_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["ticker"] = "BAD!"
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="invalid ticker"):
        load_expanded_universe(path)


def test_lowercase_identifier_fails_without_uppercasing(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["ticker"] = "0015n0"
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="invalid ticker"):
        load_expanded_universe(path)


def test_empty_name_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["name"] = ""
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="empty name"):
        load_expanded_universe(path)


def test_invalid_market_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["market"] = "KONEX"
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="invalid market"):
        load_expanded_universe(path)


def test_market_split_mismatch_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["market"] = "KOSDAQ"
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="KOSPI count"):
        load_expanded_universe(path)


def test_basdd_mismatch_fails(tmp_path: Path):
    rows = _valid_rows()
    rows[0]["source_basDd"] = "2026-09-16"
    path = _write_universe(tmp_path / "universe.csv", rows)

    with pytest.raises(ExpandedUniverseValidationError, match="source_basDd mismatch"):
        load_expanded_universe(path)


def test_deterministic_sha(tmp_path: Path):
    path = _valid_universe_file(tmp_path)

    assert compute_universe_sha256(path) == compute_universe_sha256(path)


def test_changed_content_changes_sha(tmp_path: Path):
    path = _valid_universe_file(tmp_path)
    original = compute_universe_sha256(path)
    rows = _valid_rows()
    rows[0]["name"] = "changed name"
    changed = _write_universe(tmp_path / "changed.csv", rows)

    assert compute_universe_sha256(changed) != original


def test_valid_snapshot_creation(tmp_path: Path):
    path = _valid_universe_file(tmp_path)
    result = create_universe_snapshot(path)

    assert result.created is True
    assert result.path.exists()
    assert result.sha256 == compute_universe_sha256(path)


def test_identical_snapshot_rerun_is_idempotent(tmp_path: Path):
    path = _valid_universe_file(tmp_path)
    first = create_universe_snapshot(path)
    second = create_universe_snapshot(path)

    assert first.created is True
    assert second.created is False
    assert second.sha256 == first.sha256


def test_conflicting_snapshot_fails_closed(tmp_path: Path):
    path = _valid_universe_file(tmp_path)
    result = create_universe_snapshot(path)
    result.path.write_text("conflict\n", encoding="utf-8")

    with pytest.raises(ExpandedUniverseValidationError, match="conflicting"):
        create_universe_snapshot(path)


def test_invalid_universe_creates_no_snapshot(tmp_path: Path):
    path = _write_universe(tmp_path / "universe.csv", _valid_rows()[:-1])

    with pytest.raises(ExpandedUniverseValidationError):
        create_universe_snapshot(path)

    assert not (tmp_path / "snapshots").exists()


def test_leading_zero_preserved(tmp_path: Path):
    universe = load_expanded_universe(_valid_universe_file(tmp_path))

    assert next(item.ticker for item in universe.tickers if item.ticker == "000001") == "000001"


def test_no_ticker_numeric_coercion(tmp_path: Path):
    universe = load_expanded_universe(_valid_universe_file(tmp_path))

    by_ticker = {item.ticker: item for item in universe.tickers}

    assert isinstance(by_ticker["000001"].ticker, str)
    assert by_ticker["000001"].ticker == "000001"
    assert by_ticker["0015N0"].ticker == "0015N0"
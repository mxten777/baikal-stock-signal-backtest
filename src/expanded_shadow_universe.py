"""Expanded Shadow universe contract utilities.

STEP 13-D1 only: controlled universe loading, validation, hashing, and
immutable snapshot creation. This module does not fetch market/investor data
or call the Signal Engine.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


EXPECTED_BAS_DD = "2026-09-17"
EXPECTED_UNIVERSE_COUNT = 574
EXPECTED_MARKET_COUNTS = {"KOSPI": 267, "KOSDAQ": 307}
REQUIRED_COLUMNS = ["ticker", "name", "market", "source_basDd"]
TICKER_PATTERN = r"^[0-9A-Z]{6}$"


class ExpandedUniverseValidationError(ValueError):
    """Raised when the controlled Expanded Shadow universe contract fails."""


@dataclass(frozen=True)
class ExpandedTicker:
    ticker: str
    name: str
    market: str
    source_basDd: str


@dataclass(frozen=True)
class ExpandedUniverse:
    path: Path
    basDd: str
    tickers: tuple[ExpandedTicker, ...]
    sha256: str

    @property
    def row_count(self) -> int:
        return len(self.tickers)

    @property
    def market_counts(self) -> dict[str, int]:
        return {
            market: sum(1 for ticker in self.tickers if ticker.market == market)
            for market in EXPECTED_MARKET_COUNTS
        }


@dataclass(frozen=True)
class UniverseSnapshotResult:
    path: Path
    sha256: str
    created: bool


def compute_universe_sha256(path: str | Path) -> str:
    """Return SHA-256 of the exact controlled CSV bytes."""
    target = Path(path)
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_expanded_universe(
    path: str | Path,
    basDd: str = EXPECTED_BAS_DD,
) -> ExpandedUniverse:
    """Validate and load the controlled 574-ticker Expanded Shadow universe."""
    return validate_expanded_universe(path, basDd=basDd)


def validate_expanded_universe(
    path: str | Path,
    basDd: str = EXPECTED_BAS_DD,
) -> ExpandedUniverse:
    """Validate the controlled universe without changing membership or values."""
    if basDd != EXPECTED_BAS_DD:
        raise ExpandedUniverseValidationError(
            f"unexpected basDd {basDd!r}; expected {EXPECTED_BAS_DD!r}"
        )

    target = Path(path)
    if not target.exists():
        raise ExpandedUniverseValidationError(f"universe file not found: {target}")
    if not target.is_file():
        raise ExpandedUniverseValidationError(f"universe path is not a file: {target}")

    try:
        frame = pd.read_csv(target, dtype=str, keep_default_na=False)
    except Exception as exc:  # noqa: BLE001 - convert parser/IO failures to contract failure
        raise ExpandedUniverseValidationError(
            f"universe file is not readable: {type(exc).__name__}: {exc}"
        ) from exc

    if list(frame.columns) != REQUIRED_COLUMNS:
        raise ExpandedUniverseValidationError(
            f"universe columns must be exactly {REQUIRED_COLUMNS}, got {list(frame.columns)}"
        )
    if len(frame) != EXPECTED_UNIVERSE_COUNT:
        raise ExpandedUniverseValidationError(
            f"universe row count must be {EXPECTED_UNIVERSE_COUNT}, got {len(frame)}"
        )

    for column in REQUIRED_COLUMNS:
        values = frame[column].astype(str)
        empty = values.eq("")
        if empty.any():
            raise ExpandedUniverseValidationError(
                f"empty {column} at row {int(empty[empty].index[0]) + 2}"
            )
        changed_by_strip = values.ne(values.str.strip())
        if changed_by_strip.any():
            raise ExpandedUniverseValidationError(
                f"surrounding whitespace in {column} at row {int(changed_by_strip[changed_by_strip].index[0]) + 2}"
            )

    ticker_valid = frame["ticker"].str.fullmatch(TICKER_PATTERN, na=False)
    if not ticker_valid.all():
        bad = frame.loc[~ticker_valid, "ticker"].head(5).tolist()
        raise ExpandedUniverseValidationError(f"invalid ticker identifier(s): {bad}")

    duplicate_mask = frame["ticker"].duplicated(keep=False)
    if duplicate_mask.any():
        duplicates = sorted(frame.loc[duplicate_mask, "ticker"].unique().tolist())
        raise ExpandedUniverseValidationError(f"duplicate ticker identifier(s): {duplicates}")
    if frame["ticker"].nunique() != EXPECTED_UNIVERSE_COUNT:
        raise ExpandedUniverseValidationError("unique ticker count mismatch")

    valid_markets = set(EXPECTED_MARKET_COUNTS)
    invalid_market = ~frame["market"].isin(valid_markets)
    if invalid_market.any():
        bad = frame.loc[invalid_market, "market"].head(5).tolist()
        raise ExpandedUniverseValidationError(f"invalid market value(s): {bad}")

    market_counts = frame["market"].value_counts().to_dict()
    for market, expected in EXPECTED_MARKET_COUNTS.items():
        actual = int(market_counts.get(market, 0))
        if actual != expected:
            raise ExpandedUniverseValidationError(
                f"{market} count must be {expected}, got {actual}"
            )

    basdd_valid = frame["source_basDd"].eq(basDd)
    if not basdd_valid.all():
        bad = sorted(frame.loc[~basdd_valid, "source_basDd"].unique().tolist())
        raise ExpandedUniverseValidationError(f"source_basDd mismatch: {bad}")

    tickers = tuple(
        ExpandedTicker(
            ticker=row.ticker,
            name=row.name,
            market=row.market,
            source_basDd=row.source_basDd,
        )
        for row in frame.itertuples(index=False)
    )
    return ExpandedUniverse(
        path=target,
        basDd=basDd,
        tickers=tickers,
        sha256=compute_universe_sha256(target),
    )


def create_universe_snapshot(
    path: str | Path,
    snapshots_dir: str | Path | None = None,
    basDd: str = EXPECTED_BAS_DD,
) -> UniverseSnapshotResult:
    """Create an immutable snapshot after validation.

    Existing identical snapshots are accepted as idempotent success. Existing
    snapshots with different bytes fail closed and are never overwritten.
    """
    universe = validate_expanded_universe(path, basDd=basDd)
    source_path = universe.path
    target_dir = Path(snapshots_dir) if snapshots_dir is not None else source_path.parent / "snapshots"
    snapshot_path = target_dir / f"{basDd}_expanded_universe_574.csv"

    if snapshot_path.exists():
        snapshot_hash = compute_universe_sha256(snapshot_path)
        if snapshot_hash != universe.sha256:
            raise ExpandedUniverseValidationError(
                f"conflicting universe snapshot exists: {snapshot_path}"
            )
        return UniverseSnapshotResult(snapshot_path, snapshot_hash, created=False)

    target_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{snapshot_path.name}.", suffix=".tmp", dir=str(target_dir)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as out, source_path.open("rb") as source:
            shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary_path, snapshot_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return UniverseSnapshotResult(snapshot_path, universe.sha256, created=True)
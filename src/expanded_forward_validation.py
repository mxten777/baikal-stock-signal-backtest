"""Isolated forward validation tracking for post-cutoff Expanded candidates."""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.expanded_candidate_performance import (
    PERFORMANCE_FIELDS,
    ExpandedCandidatePerformanceError,
    ExpandedCandidatePerformanceStore,
    IDENTITY_FIELDS,
    utc_now_iso,
)
from src.expanded_company_profiles import load_company_profiles, profile_store_path
from src.expanded_shadow_ops import ExpandedShadowPaths, atomic_write_json
from src.shadow_tracking import FORWARD_HORIZONS


VALIDATION_CUTOFF = "2026-10-08"
VALIDATION_LEDGER_NAME = "expanded_validation_candidate_performance_ledger.csv"
SECTOR_MEMBERSHIP_NAME = "expanded_validation_sector_membership.csv"
VALIDATION_STATUS_NAME = "expanded_forward_validation_status.json"
SECTOR_FIELDS = (
    "source_basDd",
    "ticker",
    "signal_date",
    "engine_version",
    "sector",
    "profile_as_of",
    "profile_source",
)
SIGNAL_IDENTITY_FIELDS = ("basDd", "stock_code", "signal_date", "engine_version")
H3_SECTORS = ("전자부품 제조업", "특수 목적용 기계 제조업")
MATURITY_LABELS = {5: "5D", 10: "10D", 20: "20D"}


class ForwardValidationError(RuntimeError):
    """Raised when isolated Forward Validation evidence is invalid."""


def validation_ledger_path(paths: ExpandedShadowPaths) -> Path:
    return paths.validate_output_path(paths.output_root / VALIDATION_LEDGER_NAME)


def sector_membership_path(paths: ExpandedShadowPaths) -> Path:
    return paths.validate_output_path(paths.output_root / SECTOR_MEMBERSHIP_NAME)


def run_forward_validation(
    *,
    paths: ExpandedShadowPaths,
    signal_ledger: pd.DataFrame,
    price_map: dict[str, pd.DataFrame],
    benchmark_map: dict[str, Any],
    now_func: Callable[[], str] = utc_now_iso,
) -> dict[str, Any]:
    candidates = _eligible_candidates(signal_ledger)
    performance_store = ExpandedCandidatePerformanceStore(
        paths,
        path=validation_ledger_path(paths),
    )
    existing = performance_store.load()
    _validate_validation_cohort(existing)

    membership_path = sector_membership_path(paths)
    memberships = _read_memberships(membership_path)
    existing_keys = {_performance_key(row) for row in existing.to_dict(orient="records")}
    missing_memberships = existing_keys - set(memberships)
    if missing_memberships:
        raise ForwardValidationError(
            "Validation sector snapshot is missing existing candidate identities"
        )

    new_memberships, profile_warnings = _snapshot_new_memberships(
        paths=paths,
        candidates=candidates,
        existing_memberships=memberships,
    )
    if new_memberships:
        _append_memberships(membership_path, new_memberships)
        memberships.update({_membership_key(row): row for row in new_memberships})

    tracking = performance_store.synchronize(
        candidates,
        price_map=price_map,
        benchmark_map=benchmark_map,
        now_func=now_func,
    )
    frame = performance_store.load()
    _validate_validation_cohort(frame)
    summary = _summarize(
        frame,
        memberships,
        paths=paths,
        warnings=profile_warnings,
        tracking=tracking,
    )
    save_forward_validation_status(paths, summary)
    return summary


def read_forward_validation_summary(paths: ExpandedShadowPaths) -> dict[str, Any]:
    """Read the isolated Validation artifacts for the dashboard without writing."""
    saved_warnings: list[str] = []
    saved_tracking: dict[str, Any] | None = None
    try:
        status_path = paths.validate_output_path(paths.output_root / VALIDATION_STATUS_NAME)
        if status_path.is_file():
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(status_payload, dict) or status_payload.get("cutoff") != VALIDATION_CUTOFF:
                raise ForwardValidationError("Validation status record is malformed")
            if status_payload.get("status") == "ERROR":
                return status_payload
            if status_payload.get("status") not in {"EMPTY", "READY"}:
                raise ForwardValidationError("Validation status record has an invalid status")
            saved_warnings = [
                str(warning) for warning in status_payload.get("warnings", [])
            ]
            tracking = status_payload.get("tracking")
            saved_tracking = tracking if isinstance(tracking, dict) else None

        store = ExpandedCandidatePerformanceStore(paths, path=validation_ledger_path(paths))
        frame = store.load()
        _validate_validation_cohort(frame)
        memberships = _read_memberships(sector_membership_path(paths))
        missing_memberships = {
            _performance_key(row)
            for row in frame.to_dict(orient="records")
        } - set(memberships)
        if missing_memberships:
            raise ForwardValidationError(
                "Validation sector snapshot is missing existing candidate identities"
            )
        summary = _summarize(
            frame,
            memberships,
            paths=paths,
            warnings=saved_warnings,
            tracking=saved_tracking,
        )
        return summary
    except Exception as exc:
        return {
            **_empty_summary(paths),
            "status": "ERROR",
            "error_code": type(exc).__name__,
            "warnings": [f"Forward Validation artifacts unavailable: {exc}"],
        }


def validation_error_payload(paths: ExpandedShadowPaths, exc: Exception) -> dict[str, Any]:
    return {
        **_empty_summary(paths),
        "status": "ERROR",
        "error_code": type(exc).__name__,
        "warnings": [f"Forward Validation update failed: {exc}"],
    }


def save_forward_validation_status(
    paths: ExpandedShadowPaths,
    payload: dict[str, Any],
) -> None:
    path = paths.validate_output_path(paths.output_root / VALIDATION_STATUS_NAME)
    atomic_write_json(path, payload, paths=paths)


def _eligible_candidates(signal_ledger: pd.DataFrame) -> pd.DataFrame:
    if signal_ledger is None or signal_ledger.empty:
        return pd.DataFrame(columns=signal_ledger.columns if signal_ledger is not None else [])
    missing = {"basDd", "decision"} - set(signal_ledger.columns)
    if missing:
        raise ForwardValidationError(
            f"Expanded signal ledger missing Validation fields: {sorted(missing)}"
        )

    candidate_mask = signal_ledger["decision"].astype(str).eq("CANDIDATE")
    candidates = signal_ledger.loc[candidate_mask].copy()
    dates = candidates["basDd"].map(_validate_basdd)
    return candidates.loc[dates.ge(VALIDATION_CUTOFF)].copy()


def _validate_basdd(value: object) -> str:
    text = str(value)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ForwardValidationError(f"candidate source_basDd is invalid: {text!r}") from exc
    if parsed.isoformat() != text:
        raise ForwardValidationError(f"candidate source_basDd must use YYYY-MM-DD: {text!r}")
    return text


def _validate_validation_cohort(frame: pd.DataFrame) -> None:
    if list(frame.columns) != PERFORMANCE_FIELDS:
        raise ExpandedCandidatePerformanceError(
            f"Validation performance ledger columns must be exactly {PERFORMANCE_FIELDS}"
        )
    for value in frame["source_basDd"]:
        basdd = _validate_basdd(value)
        if basdd < VALIDATION_CUTOFF:
            raise ForwardValidationError(
                f"pre-cutoff candidate found in Validation ledger: {basdd}"
            )


def _snapshot_new_memberships(
    *,
    paths: ExpandedShadowPaths,
    candidates: pd.DataFrame,
    existing_memberships: dict[tuple[str, str, str, str], dict[str, str]],
) -> tuple[list[dict[str, str]], list[str]]:
    if candidates.empty:
        return [], []
    if not set(SIGNAL_IDENTITY_FIELDS).issubset(candidates.columns):
        raise ForwardValidationError(
            "candidate ledger missing identity fields: "
            f"{sorted(set(SIGNAL_IDENTITY_FIELDS) - set(candidates.columns))}"
        )

    new_rows: list[dict[str, str]] = []
    identities: set[tuple[str, str, str, str]] = set()
    for _, candidate in candidates.iterrows():
        key = tuple(str(candidate[field]) for field in SIGNAL_IDENTITY_FIELDS)
        if key in identities:
            raise ForwardValidationError(f"duplicate Validation candidate identity: {key}")
        identities.add(key)
        if key in existing_memberships:
            continue
        new_rows.append(
            {
                "source_basDd": key[0],
                "ticker": key[1],
                "signal_date": key[2],
                "engine_version": key[3],
                "sector": "",
                "profile_as_of": "",
                "profile_source": "PROFILE_UNAVAILABLE",
            }
        )

    if not new_rows:
        return [], []
    profiles, warnings = load_company_profiles(profile_store_path(paths.repo_root))
    for row in new_rows:
        profile = profiles.get(row["ticker"])
        if profile is not None:
            row["sector"] = profile.sector or ""
            row["profile_as_of"] = profile.profile_as_of
            row["profile_source"] = profile.source
    return new_rows, warnings


def _read_memberships(path: Path) -> dict[tuple[str, str, str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    rows: dict[tuple[str, str, str, str], dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(SECTOR_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ForwardValidationError(
                f"Validation sector snapshot missing fields: {sorted(missing)}"
            )
        for row in reader:
            normalized = {field: row.get(field) or "" for field in SECTOR_FIELDS}
            key = _membership_key(normalized)
            if key in rows:
                raise ForwardValidationError(f"duplicate Validation sector snapshot identity: {key}")
            normalized["source_basDd"] = _validate_basdd(normalized["source_basDd"])
            if normalized["source_basDd"] < VALIDATION_CUTOFF:
                raise ForwardValidationError(
                    f"pre-cutoff candidate found in Validation sector snapshot: {normalized['source_basDd']}"
                )
            rows[key] = normalized
    return rows


def _append_memberships(path: Path, rows: list[dict[str, str]]) -> None:
    existing = list(_read_memberships(path).values())
    combined = existing + rows
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SECTOR_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(combined)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _summarize(
    frame: pd.DataFrame,
    memberships: dict[tuple[str, str, str, str], dict[str, str]],
    *,
    paths: ExpandedShadowPaths,
    warnings: list[str],
    tracking: dict[str, int] | None = None,
) -> dict[str, Any]:
    _validate_validation_cohort(frame)
    keys = [_performance_key(row) for row in frame.to_dict(orient="records")]
    sectors = [memberships.get(key, {}).get("sector", "") for key in keys]
    frame = frame.copy()
    frame["_sector_snapshot"] = sectors
    bucket = _group_summary(frame)
    h1 = {
        "groups": {
            status: _group_summary(frame.loc[frame["foreign_status"].astype(str).eq(status)])
            for status in ("POSITIVE", "NEUTRAL")
        },
        "not_compared_count": int(
            (~frame["foreign_status"].astype(str).isin({"POSITIVE", "NEUTRAL"})).sum()
        ),
    }
    h2_mask = (
        pd.to_numeric(frame["signal_score"], errors="coerce").ge(75)
        & pd.to_numeric(frame["signal_score"], errors="coerce").lt(80)
        & frame["foreign_status"].astype(str).eq("POSITIVE")
    )
    h2 = _group_summary(frame.loc[h2_mask])
    h3 = {
        "sectors": {
            sector: _group_summary(frame.loc[frame["_sector_snapshot"].eq(sector)])
            for sector in H3_SECTORS
        },
        "unclassified_count": int((~frame["_sector_snapshot"].isin(H3_SECTORS)).sum()),
    }

    pairs = pd.DataFrame(
        {
            "score": pd.to_numeric(frame["signal_score"], errors="coerce"),
            "excess_5d": pd.to_numeric(frame["excess_5d"], errors="coerce"),
        }
    ).dropna()
    correlation: float | None = None
    correlation_status = "INSUFFICIENT_PAIRS"
    if len(pairs) >= 2:
        score_ranks = pairs["score"].rank(method="average")
        excess_ranks = pairs["excess_5d"].rank(method="average")
        value = score_ranks.corr(excess_ranks)
        if pd.notna(value) and math.isfinite(float(value)):
            correlation = round(float(value), 6)
            correlation_status = "AVAILABLE"
        else:
            correlation_status = "CONSTANT_INPUT"

    summary = {
        "status": "READY" if not frame.empty else "EMPTY",
        "source": str(validation_ledger_path(paths)),
        "sector_membership_source": str(sector_membership_path(paths)),
        "cutoff": VALIDATION_CUTOFF,
        "candidate_count": int(len(frame)),
        "matured": bucket["matured"],
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "h4": {
            "method": "Spearman",
            "pairs": int(len(pairs)),
            "rho": correlation,
            "status": correlation_status,
        },
        "warnings": warnings,
    }
    if tracking is not None:
        summary["tracking"] = {
            "registered": int(tracking.get("registered", 0)),
            "updated": int(tracking.get("updated", 0)),
        }
    return summary


def _group_summary(frame: pd.DataFrame) -> dict[str, Any]:
    matured: dict[str, int] = {}
    mean_excess: dict[str, float | None] = {}
    for horizon in FORWARD_HORIZONS:
        label = MATURITY_LABELS[horizon]
        returns = pd.to_numeric(frame[f"return_{horizon}d"], errors="coerce")
        matured[label] = int(returns.notna().sum())
        excess = pd.to_numeric(frame[f"excess_{horizon}d"], errors="coerce").dropna()
        mean_excess[label] = round(float(excess.mean()), 6) if not excess.empty else None
    return {
        "candidate_count": int(len(frame)),
        "matured": matured,
        "mean_excess": mean_excess,
    }


def _empty_summary(paths: ExpandedShadowPaths) -> dict[str, Any]:
    return _summarize(
        pd.DataFrame(columns=PERFORMANCE_FIELDS),
        {},
        paths=paths,
        warnings=[],
    )


def _performance_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(row[field]) for field in IDENTITY_FIELDS)  # type: ignore[return-value]


def _membership_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(row[field]) for field in IDENTITY_FIELDS)  # type: ignore[return-value]

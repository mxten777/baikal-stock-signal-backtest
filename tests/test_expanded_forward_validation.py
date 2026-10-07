from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import src.expanded_forward_validation as validation
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_shadow_ops import ExpandedShadowPaths
from src.expanded_shadow_ledger import LEDGER_FIELDS


CUTOFF = "2026-10-08"
NOW = "2026-10-09T12:00:00+00:00"


def _signal(ticker: str, *, basdd: str = CUTOFF, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in LEDGER_FIELDS}
    row.update(
        {
            "basDd": basdd,
            "stock_code": ticker,
            "stock_name": f"Stock {ticker}",
            "market": "KOSPI",
            "signal_date": CUTOFF,
            "signal_price": 100.0,
            "raw_score": 50,
            "signal_score": 76.0,
            "signal_type": "BUY_WATCH",
            "foreign_5d_ratio": 0.1,
            "foreign_status": "POSITIVE",
            "decision": "CANDIDATE",
            "exclusion_reason": "",
            "engine_version": "v0.1",
            "source_commit": "deadbeef",
            "run_id": f"run-{ticker}",
            "created_at": NOW,
        }
    )
    row.update(overrides)
    return row


def _prices(step: float, periods: int = 21) -> pd.DataFrame:
    dates = pd.bdate_range(CUTOFF, periods=periods)
    return pd.DataFrame({"date": dates, "close": [100.0 + index * step for index in range(periods)]})


def _profile_loader(monkeypatch, sectors: dict[str, str]):
    monkeypatch.setattr(
        validation,
        "load_company_profiles",
        lambda _path: (
            {
                ticker: SimpleNamespace(
                    sector=sector,
                    profile_as_of="2026-10-08",
                    source="KRX_KIND_LISTING",
                )
                for ticker, sector in sectors.items()
            },
            [],
        ),
    )


def _run(tmp_path: Path, signals: list[dict[str, object]], now: str = NOW):
    paths = ExpandedShadowPaths(tmp_path)
    candidates = [row for row in signals if row["decision"] == "CANDIDATE" and row["basDd"] >= CUTOFF]
    tickers = sorted({str(row["stock_code"]) for row in candidates})
    return validation.run_forward_validation(
        paths=paths,
        signal_ledger=pd.DataFrame(signals),
        price_map={ticker: _prices(float(index + 1)) for index, ticker in enumerate(tickers)},
        benchmark_map={"KS11": _prices(0.0)},
        now_func=lambda: now,
    )


def test_cutoff_hypotheses_horizons_and_sector_snapshot_are_isolated(tmp_path: Path, monkeypatch):
    _profile_loader(
        monkeypatch,
        {
            "000001": "전자부품 제조업",
            "000002": "특수 목적용 기계 제조업",
            "000003": "다른 업종",
            "000004": "다른 업종",
        },
    )
    signals = [
        _signal("000000", basdd="2026-10-07", signal_score=99.0),
        _signal("000001", signal_score=76.0, foreign_status="POSITIVE"),
        _signal("000002", signal_score=77.0, foreign_status="NEUTRAL"),
        _signal("000003", signal_score=78.0, foreign_status="POSITIVE"),
        _signal("000004", signal_score=79.0, foreign_status="POSITIVE"),
    ]
    discovery_path = tmp_path / "output/expanded_shadow/expanded_candidate_performance_ledger.csv"
    discovery_path.parent.mkdir(parents=True)
    discovery_path.write_bytes(b"pre-existing Discovery ledger bytes\n")

    first = _run(tmp_path, signals)
    validation_path = tmp_path / "output/expanded_shadow/expanded_validation_candidate_performance_ledger.csv"
    membership_path = tmp_path / "output/expanded_shadow/expanded_validation_sector_membership.csv"
    first_memberships = membership_path.read_bytes()

    assert first["status"] == "READY"
    assert first["cutoff"] == CUTOFF
    assert first["candidate_count"] == 4
    assert first["matured"] == {"5D": 4, "10D": 4, "20D": 4}
    assert first["h1"]["groups"]["POSITIVE"]["candidate_count"] == 3
    assert first["h1"]["groups"]["NEUTRAL"]["candidate_count"] == 1
    assert first["h2"]["candidate_count"] == 3
    assert first["h3"]["sectors"]["전자부품 제조업"]["candidate_count"] == 1
    assert first["h3"]["sectors"]["특수 목적용 기계 제조업"]["candidate_count"] == 1
    assert first["h4"]["method"] == "Spearman"
    assert first["h4"]["pairs"] == 4
    assert first["h4"]["rho"] == pytest.approx(1.0)

    frame = pd.read_csv(validation_path, dtype={"source_basDd": str, "ticker": str})
    assert list(frame.columns) == PERFORMANCE_FIELDS
    assert set(frame["source_basDd"]) == {CUTOFF}
    assert "000000" not in set(frame["ticker"])
    assert discovery_path.read_bytes() == b"pre-existing Discovery ledger bytes\n"

    _profile_loader(monkeypatch, {"000001": "changed sector"})
    second = _run(tmp_path, signals, now="2026-10-10T12:00:00+00:00")
    assert second["candidate_count"] == 4
    assert membership_path.read_bytes() == first_memberships
    with membership_path.open(encoding="utf-8", newline="") as handle:
        snapshot_rows = list(csv.DictReader(handle))
    assert next(row for row in snapshot_rows if row["ticker"] == "000001")["sector"] == "전자부품 제조업"


def test_zero_validation_candidates_are_a_normal_empty_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        validation,
        "load_company_profiles",
        lambda _path: (_ for _ in ()).throw(AssertionError("no profile read for empty cohort")),
    )

    result = _run(tmp_path, [_signal("000001", basdd="2026-10-07")])

    assert result["status"] == "EMPTY"
    assert result["candidate_count"] == 0
    assert result["matured"] == {"5D": 0, "10D": 0, "20D": 0}
    assert result["h4"] == {
        "method": "Spearman",
        "pairs": 0,
        "rho": None,
        "status": "INSUFFICIENT_PAIRS",
    }
    assert not (tmp_path / "output/expanded_shadow/expanded_validation_candidate_performance_ledger.csv").exists()


def test_h2_includes_75_excludes_80_and_requires_positive_foreign(tmp_path: Path, monkeypatch):
    _profile_loader(monkeypatch, {})

    result = _run(
        tmp_path,
        [
            _signal("000001", signal_score=75.0, foreign_status="POSITIVE"),
            _signal("000002", signal_score=80.0, foreign_status="POSITIVE"),
            _signal("000003", signal_score=79.0, foreign_status="NEUTRAL"),
        ],
    )

    assert result["h2"]["candidate_count"] == 1


def test_pre_cutoff_rows_in_validation_ledger_are_rejected(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    store = validation.ExpandedCandidatePerformanceStore(
        paths,
        path=validation.validation_ledger_path(paths),
    )
    row = {field: "" for field in PERFORMANCE_FIELDS}
    row.update(
        {
            "source_basDd": "2026-10-07",
            "ticker": "000001",
            "stock_name": "Legacy",
            "market": "KOSPI",
            "signal_date": "2026-10-07",
            "entry_price": 100,
            "signal_score": 80,
            "foreign_status": "POSITIVE",
            "engine_version": "v0.1",
            "source_run_id": "run-1",
            "source_commit": "deadbeef",
            "source_created_at": NOW,
            "registered_at": NOW,
            "tracking_status": "OPEN",
        }
    )
    store.path.parent.mkdir(parents=True)
    pd.DataFrame([row], columns=PERFORMANCE_FIELDS).to_csv(store.path, index=False)

    with pytest.raises(validation.ForwardValidationError, match="pre-cutoff"):
        validation.run_forward_validation(
            paths=paths,
            signal_ledger=pd.DataFrame([_signal("000001")]),
            price_map={},
            benchmark_map={},
        )


def test_dashboard_reader_preserves_nonfatal_validation_failure_status(tmp_path: Path):
    paths = ExpandedShadowPaths(tmp_path)
    error = validation.validation_error_payload(paths, RuntimeError("isolated failure"))
    validation.save_forward_validation_status(paths, error)

    summary = validation.read_forward_validation_summary(paths)

    assert summary["status"] == "ERROR"
    assert summary["error_code"] == "RuntimeError"
    assert "isolated failure" in summary["warnings"][0]

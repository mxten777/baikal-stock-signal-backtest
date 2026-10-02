from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from dashboard.expanded_evidence import (
    EVIDENCE_AVAILABLE,
    EVIDENCE_PARTIAL,
    EVIDENCE_UNAVAILABLE,
    build_expanded_signal_records,
)
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_company_profiles import CompanyProfile, profile_store_path, save_company_profiles
from src.expanded_shadow_ledger import LEDGER_FIELDS
from src.indicators import add_all_indicators
from src.signal_engine import generate_signals


SOURCE_DATE = "2026-09-17"


def _market_frame() -> pd.DataFrame:
    close = [100 + index * 0.03 + 2 * np.sin(index * 0.7) for index in range(75)]
    close.extend([close[-1] + step for step in (0.2, 0.5, 0.9, 1.4, 2.0)])
    dates = pd.bdate_range("2026-01-01", periods=len(close))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * (len(close) - 1) + [2600],
        }
    )


def _ledger_row(*, decision: str = "CANDIDATE", score_delta: float = 0.0, ticker: str = "000001") -> dict[str, object]:
    frame = _market_frame()
    signal = generate_signals(add_all_indicators(frame), ticker, "Ledger Name").iloc[-1]
    row: dict[str, object] = {field: "" for field in LEDGER_FIELDS}
    row.update(
        {
            "basDd": SOURCE_DATE,
            "stock_code": ticker,
            "stock_name": "Ledger Name",
            "market": "KOSPI",
            "signal_date": pd.Timestamp(signal["signal_date"]).strftime("%Y-%m-%d"),
            "signal_price": float(signal["signal_close"]),
            "raw_score": int(signal["raw_score"]),
            "signal_score": float(signal["score"]) + score_delta,
            "signal_type": str(signal["signal_type"]),
            "foreign_5d_ratio": -0.25 if decision == "EXCLUDED" else 0.2,
            "foreign_status": "NEGATIVE" if decision == "EXCLUDED" else "POSITIVE",
            "decision": decision,
            "exclusion_reason": "FOREIGN_NEGATIVE" if decision == "EXCLUDED" else "",
            "engine_version": "v0.1",
            "source_commit": "test-commit",
            "run_id": "test-run",
            "created_at": "2026-09-17T00:00:00+00:00",
        }
    )
    return row


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _ready_root(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    signal_path = tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    _write_csv(signal_path, LEDGER_FIELDS, rows)
    for ticker in {str(row["stock_code"]) for row in rows}:
        market_path = tmp_path / "data/expanded_shadow/market" / SOURCE_DATE / f"{ticker}.csv"
        market_path.parent.mkdir(parents=True, exist_ok=True)
        _market_frame().to_csv(market_path, index=False)
    profile = CompanyProfile(
        ticker="000001",
        company_name="Profile Name",
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
    save_company_profiles(profile_store_path(tmp_path), [profile])
    (tmp_path / "output/expanded_shadow/expanded_shadow_run.json").write_text(
        json.dumps({"run_id": "test-run"}), encoding="utf-8"
    )
    return tmp_path


def test_builder_reconstructs_scores_and_joins_profile_and_performance(tmp_path: Path):
    row = _ledger_row()
    root = _ready_root(tmp_path, [row])
    performance = {field: "" for field in PERFORMANCE_FIELDS}
    performance.update(
        {
            "source_basDd": row["basDd"],
            "ticker": row["stock_code"],
            "stock_name": row["stock_name"],
            "market": row["market"],
            "signal_date": row["signal_date"],
            "entry_price": row["signal_price"],
            "signal_score": row["signal_score"],
            "foreign_status": row["foreign_status"],
            "engine_version": row["engine_version"],
            "source_run_id": row["run_id"],
            "source_commit": row["source_commit"],
            "source_created_at": row["created_at"],
            "registered_at": "2026-09-18T00:00:00+00:00",
            "tracking_status": "5D",
            "return_5d": 1.25,
            "excess_5d": 0.75,
            "return_10d": "",
            "excess_10d": "",
            "return_20d": "",
            "excess_20d": "",
        }
    )
    _write_csv(root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, [performance])

    records, warnings = build_expanded_signal_records(root)

    record = records[0]
    assert warnings == []
    assert record.profile is not None and record.profile.company_name == "Profile Name"
    assert record.evidence.evidence_status == EVIDENCE_AVAILABLE
    assert record.evidence.prev_score is not None and record.evidence.prev_score < 75
    assert record.evidence.current_score == row["signal_score"]
    assert record.evidence.signal_reason == (
        f"Score crossed threshold: {record.evidence.prev_score:.1f} -> {record.evidence.current_score:.1f} (threshold 75)"
    )
    components = (record.evidence.trend_score, record.evidence.volume_score, record.evidence.momentum_score)
    assert all(component is not None for component in components)
    assert sum(components) == row["raw_score"]
    assert record.evidence.foreign_status == "POSITIVE"
    assert record.evidence.decision == "CANDIDATE"
    assert "not NEGATIVE" in record.evidence.decision_reason
    assert record.performance is not None
    assert record.performance.tracking_status == "5D"
    assert record.performance.return_5d == 1.25
    assert record.performance.excess_5d == 0.75
    assert record.performance.return_10d is None


def test_score_mismatch_keeps_authoritative_decision_and_nulls_components(tmp_path: Path):
    row = _ledger_row(decision="EXCLUDED", score_delta=2.0)
    root = _ready_root(tmp_path, [row])

    records, warnings = build_expanded_signal_records(root)

    evidence = records[0].evidence
    assert evidence.evidence_status == EVIDENCE_PARTIAL
    assert evidence.current_score == row["signal_score"]
    assert evidence.prev_score is not None
    assert evidence.trend_score is None
    assert evidence.volume_score is None
    assert evidence.momentum_score is None
    assert evidence.foreign_status == "NEGATIVE"
    assert evidence.decision == "EXCLUDED"
    assert evidence.decision_reason == "FOREIGN_NEGATIVE"
    assert records[0].performance is None
    assert any("score mismatch" in warning for warning in warnings)


def test_missing_profile_and_snapshot_are_graceful(tmp_path: Path):
    row = _ledger_row()
    _write_csv(tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv", LEDGER_FIELDS, [row])

    records, warnings = build_expanded_signal_records(tmp_path)

    assert len(records) == 1
    assert records[0].profile is None
    assert records[0].evidence.evidence_status == EVIDENCE_UNAVAILABLE
    assert records[0].evidence.current_score == row["signal_score"]
    assert records[0].evidence.decision == row["decision"]
    assert any("Profile file not found" in warning for warning in warnings)
    assert any("score evidence unavailable" in warning for warning in warnings)


def test_builder_filters_signal_records_by_source_date(tmp_path: Path):
    latest = _ledger_row()
    earlier = _ledger_row(ticker="000002")
    earlier["basDd"] = "2026-09-16"
    earlier["signal_date"] = "2026-09-16"
    root = _ready_root(tmp_path, [latest, earlier])

    records, _ = build_expanded_signal_records(root, basdd=SOURCE_DATE)

    assert [record.ticker for record in records] == ["000001"]


def test_builder_does_not_modify_expanded_or_production_artifacts(tmp_path: Path):
    root = _ready_root(tmp_path, [_ledger_row()])
    _write_csv(root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, [])
    protected = [
        root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv",
        root / "output/expanded_shadow/expanded_shadow_run.json",
        root / "data/expanded_shadow/company_profiles/expanded_company_profiles.json",
        root / "data/expanded_shadow/market" / SOURCE_DATE / "000001.csv",
    ]
    protected.extend(
        [
            root / "output/shadow_signal_records.csv",
            root / "output/dual_shadow_signal_ledger.csv",
            root / "output/daily_run_registry.jsonl",
        ]
    )
    for path in protected[-3:]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("protected", encoding="utf-8")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}

    build_expanded_signal_records(root)

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    assert before == after
from __future__ import annotations

import csv
import json
from pathlib import Path

from dashboard.api import route_dashboard_request
from dashboard.expanded_signal_board import EXPANDED_BOARD_ENDPOINT, build_expanded_signal_board
from src.expanded_candidate_performance import PERFORMANCE_FIELDS
from src.expanded_shadow_ledger import LEDGER_FIELDS


SOURCE_DATE = "2026-09-17"


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(root: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "run_id": "run-1",
        "basDd": SOURCE_DATE,
        "status": "SUCCESS",
        "canonical_universe_count": 574,
        "attempted_ticker_count": 574,
        "ready_count": 574,
        "signal_count": 26,
        "new_candidate_count": 14,
        "started_at": "2026-09-17T09:00:00+00:00",
        "finished_at": "2026-09-17T09:06:15+00:00",
        "runtime_seconds": 375.0,
    }
    payload.update(overrides)
    path = root / "output/expanded_shadow/expanded_shadow_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _signal(ticker: str, *, basdd: str = SOURCE_DATE, decision: str = "CANDIDATE") -> dict[str, object]:
    row = {field: "" for field in LEDGER_FIELDS}
    row.update(
        {
            "basDd": basdd,
            "stock_code": ticker,
            "stock_name": f"Stock {ticker}",
            "market": "KOSPI",
            "signal_date": basdd,
            "signal_price": 100.0,
            "raw_score": 52,
            "signal_score": 80.0,
            "signal_type": "BUY_WATCH",
            "foreign_5d_ratio": 0.2,
            "foreign_status": "POSITIVE",
            "decision": decision,
            "engine_version": "v0.1",
            "source_commit": "abc123",
            "run_id": "run-1",
            "created_at": "2026-09-17T09:00:00+00:00",
        }
    )
    return row


def _performance(ticker: str, status: str = "OPEN", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {field: "" for field in PERFORMANCE_FIELDS}
    row.update(
        {
            "source_basDd": SOURCE_DATE,
            "ticker": ticker,
            "stock_name": f"Stock {ticker}",
            "market": "KOSPI",
            "signal_date": SOURCE_DATE,
            "entry_price": 100.0,
            "signal_score": 80.0,
            "foreign_status": "POSITIVE",
            "engine_version": "v0.1",
            "source_run_id": "run-1",
            "source_commit": "abc123",
            "source_created_at": "2026-09-17T09:00:00+00:00",
            "registered_at": "2026-09-18T00:00:00+00:00",
            "tracking_status": status,
        }
    )
    row.update(overrides)
    return row


def _ready_root(tmp_path: Path) -> Path:
    _write_manifest(tmp_path)
    rows = [_signal(f"{index:06d}") for index in range(1, 15)]
    rows.extend([_signal(f"{index:06d}", decision="EXCLUDED") for index in range(101, 113)])
    _write_csv(tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv", LEDGER_FIELDS, rows)
    return tmp_path


def test_run_summary_uses_canonical_manifest_counts(tmp_path: Path):
    payload = build_expanded_signal_board(_ready_root(tmp_path))

    assert payload["run_summary"] == {
        "status": "READY",
        "source": str(tmp_path / "output/expanded_shadow/expanded_shadow_run.json"),
        "run_id": "run-1",
        "source_date": SOURCE_DATE,
        "run_status": "SUCCESS",
        "universe": 574,
        "attempted": 574,
        "ready": 574,
        "failure": 0,
        "signals": 26,
        "candidate": 14,
        "excluded": 12,
        "no_signal": 548,
        "started_at": "2026-09-17T09:00:00+00:00",
        "finished_at": "2026-09-17T09:06:15+00:00",
        "runtime_seconds": 375.0,
    }


def test_latest_cohort_only_and_leading_zero_preserved(tmp_path: Path):
    root = _ready_root(tmp_path)
    path = root / "output/expanded_shadow/expanded_shadow_signal_ledger.csv"
    rows = [_signal("000777", basdd="2026-09-16")] + [_signal(f"{index:06d}") for index in range(1, 15)]
    rows.extend([_signal(f"{index:06d}", decision="EXCLUDED") for index in range(101, 113)])
    _write_csv(path, LEDGER_FIELDS, rows)

    section = build_expanded_signal_board(root)["new_candidates"]

    assert section["status"] == "READY"
    assert section["count"] == 14
    assert "000777" not in {row["ticker"] for row in section["records"]}
    assert section["records"][0]["ticker"] == "000001"
    assert section["records"][0]["entry_price"] == 100


def test_performance_records_nullable_values_and_status_summary(tmp_path: Path):
    root = _ready_root(tmp_path)
    rows = [
        _performance("000001", "OPEN"),
        _performance("000002", "5D", return_5d=5.0, excess_5d=2.0),
        _performance("000003", "10D", return_5d=5.0, excess_5d=2.0, return_10d=7.0, excess_10d=3.0),
        _performance("000004", "20D", return_20d=8.0, excess_20d=4.0),
        _performance("000005", "COMPLETE", return_20d=9.0, excess_20d=5.0),
    ]
    _write_csv(root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, rows)

    payload = build_expanded_signal_board(root)

    assert payload["performance"]["status"] == "READY"
    assert payload["status_summary"] == {"OPEN": 1, "5D": 1, "10D": 1, "20D": 1, "COMPLETE": 1}
    assert payload["performance"]["records"][0]["return_5d"] is None
    assert payload["performance"]["records"][1]["return_5d"] == 5


def test_missing_and_empty_performance_are_normal_states(tmp_path: Path):
    root = _ready_root(tmp_path)
    missing = build_expanded_signal_board(root)
    assert missing["status"] == "READY"
    assert missing["performance"]["status"] == "MISSING"
    assert missing["performance"]["empty_message"] == "성과 추적 데이터가 아직 생성되지 않았습니다."
    assert missing["status_summary"] is None

    _write_csv(root / "output/expanded_shadow/expanded_candidate_performance_ledger.csv", PERFORMANCE_FIELDS, [])
    empty = build_expanded_signal_board(root)
    assert empty["performance"]["status"] == "EMPTY"
    assert empty["performance"]["records"] == []


def test_missing_run_and_zero_signal_run_states(tmp_path: Path):
    missing = build_expanded_signal_board(tmp_path)
    assert missing["status"] == "MISSING"
    assert missing["run_summary"]["status"] == "MISSING"

    _write_manifest(tmp_path, signal_count=0, new_candidate_count=0)
    empty = build_expanded_signal_board(tmp_path)
    assert empty["status"] == "EMPTY"
    assert empty["new_candidates"]["status"] == "EMPTY"


def test_stale_signal_ledger_does_not_show_past_candidates_as_new(tmp_path: Path):
    _write_manifest(tmp_path, basDd="2026-09-18")
    _write_csv(
        tmp_path / "output/expanded_shadow/expanded_shadow_signal_ledger.csv",
        LEDGER_FIELDS,
        [_signal("000001", basdd=SOURCE_DATE)],
    )

    payload = build_expanded_signal_board(tmp_path)

    assert payload["status"] == "STALE"
    assert payload["new_candidates"]["status"] == "STALE"
    assert payload["new_candidates"]["records"] == []


def test_malformed_artifacts_are_isolated_in_payload(tmp_path: Path):
    run_path = tmp_path / "output/expanded_shadow/expanded_shadow_run.json"
    run_path.parent.mkdir(parents=True)
    run_path.write_text("{bad", encoding="utf-8")

    payload = build_expanded_signal_board(tmp_path)

    assert payload["status"] == "MALFORMED"
    assert payload["run_summary"]["status"] == "MALFORMED"
    assert payload["warnings"]


def test_expanded_endpoint_is_get_only_and_does_not_touch_production(tmp_path: Path):
    root = _ready_root(tmp_path)
    protected = {
        root / "output/shadow_signal_records.csv": b"production-shadow",
        root / "output/daily_run_registry.jsonl": b"production-registry",
        root / "output/dual_shadow_signal_ledger.csv": b"dual",
    }
    for path, content in protected.items():
        path.write_bytes(content)
    before = {path: path.read_bytes() for path in protected}

    status, headers, body = route_dashboard_request("GET", EXPANDED_BOARD_ENDPOINT, root)
    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert headers["Allow"] == "GET"
    assert payload["mode"] == "EXPANDED_SHADOW"
    assert payload["read_only"] is True

    post_status, post_headers, _ = route_dashboard_request("POST", EXPANDED_BOARD_ENDPOINT, root, b"{}")
    assert post_status == 405
    assert post_headers["Allow"] == "GET"
    assert {path: path.read_bytes() for path in protected} == before
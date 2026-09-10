from __future__ import annotations

import csv
import json
from pathlib import Path

from dashboard.api import route_dashboard_request
from dashboard.dual_shadow import READ_ONLY_ENDPOINTS


LEDGER_HEADER = [
    "trade_date",
    "stock_code",
    "stock_name",
    "evaluation_close",
    "baseline_raw_score",
    "baseline_score",
    "baseline_signal_type",
    "baseline_signal_present",
    "challenger_raw_score",
    "challenger_score",
    "challenger_signal_type",
    "challenger_signal_present",
    "challenger_volume_penalty",
    "challenger_pre_return_penalty",
    "challenger_rsi_penalty",
    "challenger_total_penalty",
    "comparison_group",
    "evaluation_status",
    "baseline_engine_version",
    "challenger_engine_version",
    "source_commit",
    "created_at",
]

FORWARD_HEADER = [
    "trade_date",
    "stock_code",
    "stock_name",
    "evaluation_close",
    "baseline_signal_present",
    "challenger_signal_present",
    "comparison_group",
    "horizon",
    "target_date",
    "target_close",
    "forward_return",
    "return_status",
    "baseline_engine_version",
    "challenger_engine_version",
    "source_commit",
    "created_at",
]


def _root(tmp_path: Path) -> Path:
    (tmp_path / "output").mkdir()
    return tmp_path


def _write_csv(path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _ledger_row(
    stock_code: str = "005930",
    stock_name: str = "Samsung",
    trade_date: str = "2026-09-10",
    baseline_score: float | None = 81.5,
    baseline_signal: str | None = "BUY_WATCH",
    challenger_score: float | None = 66.2,
    challenger_signal: str | None = "WAIT",
    group: str = "BASELINE_ONLY",
    evaluation_status: str = "OK",
    pre_return_penalty: int = 10,
) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "evaluation_close": 100.0,
        "baseline_raw_score": 5,
        "baseline_score": "" if baseline_score is None else baseline_score,
        "baseline_signal_type": "" if baseline_signal is None else baseline_signal,
        "baseline_signal_present": group in {"BOTH_YES", "BASELINE_ONLY"},
        "challenger_raw_score": 5,
        "challenger_score": "" if challenger_score is None else challenger_score,
        "challenger_signal_type": "" if challenger_signal is None else challenger_signal,
        "challenger_signal_present": group in {"BOTH_YES", "CHALLENGER_ONLY"},
        "challenger_volume_penalty": 0,
        "challenger_pre_return_penalty": pre_return_penalty,
        "challenger_rsi_penalty": 0,
        "challenger_total_penalty": pre_return_penalty,
        "comparison_group": group,
        "evaluation_status": evaluation_status,
        "baseline_engine_version": "v0.1",
        "challenger_engine_version": "v0.2",
        "source_commit": "abc123",
        "created_at": "2026-09-10T00:00:00+00:00",
    }


def _forward_row(stock_code: str = "005930", horizon: int = 5, trade_date: str = "2026-09-10") -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "stock_code": stock_code,
        "stock_name": "Samsung",
        "evaluation_close": 100.0,
        "baseline_signal_present": True,
        "challenger_signal_present": False,
        "comparison_group": "BASELINE_ONLY",
        "horizon": horizon,
        "target_date": "2026-09-17",
        "target_close": 103.0,
        "forward_return": 3.0,
        "return_status": "AVAILABLE",
        "baseline_engine_version": "v0.1",
        "challenger_engine_version": "v0.2",
        "source_commit": "abc123",
        "created_at": "2026-09-17T00:00:00+00:00",
    }


def _write_registry(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _registry_row(trade_date: str = "2026-09-10", status: str = "SUCCESS_NO_NEW_EVIDENCE") -> dict[str, object]:
    return {
        "run_id": "run-1",
        "trade_date": trade_date,
        "started_at": "2026-09-10T09:00:00+00:00",
        "finished_at": "2026-09-10T09:00:03+00:00",
        "status": status,
        "source_commit": "abc123",
        "ledger_saved": 20,
        "forward_return_saved": 0,
        "performance_status": "NO_AVAILABLE_EVIDENCE",
        "error_code": None,
        "error_message": None,
    }


def _write_summary(path: Path, status: str = "NO_AVAILABLE_EVIDENCE") -> None:
    empty_stats = {"signal_count": 0, "avg_return": None, "median_return": None, "win_rate": None, "best_return": None, "worst_return": None}
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-10T09:00:03+00:00",
                "status": status,
                "win_definition": "forward_return > 0",
                "engine_versions": {"baseline": "v0.1", "challenger": "v0.2"},
                "total_evidence_rows": 0,
                "horizons": {str(h): {"baseline": empty_stats, "challenger": empty_stats, "delta": {"avg_return_delta": None, "median_return_delta": None, "win_rate_delta": None, "signal_count_delta": 0}} for h in (5, 10, 20)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _payload(method: str, path: str, root: Path) -> dict[str, object]:
    status, _, body = route_dashboard_request(method, path, root)
    assert status == 200
    return json.loads(body.decode("utf-8"))


def test_a_status_normal(tmp_path):
    root = _root(tmp_path)
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, [_ledger_row()])
    _write_summary(root / "output/dual_shadow_performance_summary.json")
    _write_registry(root / "output/dual_shadow_run_registry.jsonl", [_registry_row()])

    payload = _payload("GET", "/api/dual-shadow/status", root)

    assert payload["mode"] == "DUAL_SHADOW"
    assert payload["read_only"] is True
    assert payload["latest_trade_date"] == "2026-09-10"
    assert payload["pipeline_status"] == "SUCCESS_NO_NEW_EVIDENCE"
    assert payload["ledger_row_count"] == 1
    assert payload["performance_status"] == "NO_AVAILABLE_EVIDENCE"


def test_b_latest_comparison_normal_and_penalties(tmp_path):
    root = _root(tmp_path)
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, [_ledger_row()])

    payload = _payload("GET", "/api/dual-shadow/latest", root)

    assert payload["trade_date"] == "2026-09-10"
    assert payload["counts"]["BASELINE_ONLY"] == 1
    record = payload["records"][0]
    assert record["stock_name"] == "Samsung"
    assert record["baseline_score"] == 81.5
    assert record["challenger_signal"] == "WAIT"
    assert record["challenger_pre_return_penalty"] == 10


def test_c_performance_normal(tmp_path):
    root = _root(tmp_path)
    _write_summary(root / "output/dual_shadow_performance_summary.json", status="OK")

    payload = _payload("GET", "/api/dual-shadow/performance", root)

    assert payload["status"] == "OK"
    assert set(payload["horizons"]) == {"5D", "10D", "20D"}


def test_d_runs_normal(tmp_path):
    root = _root(tmp_path)
    _write_registry(root / "output/dual_shadow_run_registry.jsonl", [_registry_row(), _registry_row(trade_date="2026-09-11", status="FAILED")])

    payload = _payload("GET", "/api/dual-shadow/runs", root)

    assert payload["status"] == "AVAILABLE"
    assert payload["items"][0]["trade_date"] == "2026-09-11"
    assert payload["items"][0]["status"] == "FAILED"


def test_e_missing_ledger_is_no_data(tmp_path):
    root = _root(tmp_path)
    payload = _payload("GET", "/api/dual-shadow/latest", root)
    assert payload["status"] == "NO_DATA"
    assert payload["records"] == []


def test_f_missing_forward_returns_is_no_available_evidence(tmp_path):
    root = _root(tmp_path)
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, [_ledger_row()])
    payload = _payload("GET", "/api/dual-shadow/latest", root)
    assert payload["evidence_maturity"]["5D"]["status"] == "NO_AVAILABLE_EVIDENCE"
    assert payload["evidence_maturity"]["5D"]["pending"] == 1


def test_g_missing_summary_is_no_summary(tmp_path):
    root = _root(tmp_path)
    payload = _payload("GET", "/api/dual-shadow/performance", root)
    assert payload["status"] == "NO_SUMMARY"
    assert payload["horizons"]["5D"]["baseline"]["avg_return"] is None


def test_h_missing_registry_is_no_run(tmp_path):
    root = _root(tmp_path)
    payload = _payload("GET", "/api/dual-shadow/runs", root)
    assert payload["status"] == "NO_RUN"
    assert payload["items"] == []


def test_i_malformed_ledger_is_error(tmp_path):
    root = _root(tmp_path)
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", ["trade_date", "stock_code"], [{"trade_date": "2026-09-10", "stock_code": "005930"}])
    payload = _payload("GET", "/api/dual-shadow/latest", root)
    assert payload["status"] == "ERROR"
    assert "missing required columns" in payload["warnings"][0]


def test_j_malformed_summary_is_error(tmp_path):
    root = _root(tmp_path)
    (root / "output/dual_shadow_performance_summary.json").write_text("{bad-json", encoding="utf-8")
    payload = _payload("GET", "/api/dual-shadow/performance", root)
    assert payload["status"] == "ERROR"
    assert "malformed" in payload["warnings"][0]


def test_k_malformed_registry_is_error(tmp_path):
    root = _root(tmp_path)
    (root / "output/dual_shadow_run_registry.jsonl").write_text("not-json\n", encoding="utf-8")
    payload = _payload("GET", "/api/dual-shadow/runs", root)
    assert payload["status"] == "ERROR"
    assert "malformed JSON" in payload["warnings"][0]


def test_l_latest_trade_date_selection_is_exact(tmp_path):
    root = _root(tmp_path)
    _write_csv(
        root / "output/dual_shadow_signal_ledger.csv",
        LEDGER_HEADER,
        [_ledger_row(stock_code="000001", trade_date="2026-09-09"), _ledger_row(stock_code="000002", trade_date="2026-09-10")],
    )
    payload = _payload("GET", "/api/dual-shadow/latest", root)
    assert payload["trade_date"] == "2026-09-10"
    assert [record["stock_code"] for record in payload["records"]] == ["000002"]


def test_m_20_stocks_display(tmp_path):
    root = _root(tmp_path)
    rows = [_ledger_row(stock_code=f"{idx:06d}", group="BOTH_NO", baseline_signal="WAIT", challenger_signal="WAIT") for idx in range(20)]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, rows)
    payload = _payload("GET", "/api/dual-shadow/latest", root)
    assert payload["total_stocks"] == 20
    assert len(payload["records"]) == 20


def test_n_comparison_group_count_accuracy(tmp_path):
    root = _root(tmp_path)
    rows = [
        _ledger_row(stock_code="000001", group="BOTH_YES"),
        _ledger_row(stock_code="000002", group="BASELINE_ONLY"),
        _ledger_row(stock_code="000003", group="CHALLENGER_ONLY"),
        _ledger_row(stock_code="000004", group="BOTH_NO"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, rows)
    counts = _payload("GET", "/api/dual-shadow/latest", root)["counts"]
    assert counts["BOTH_YES"] == 1
    assert counts["BASELINE_ONLY"] == 1
    assert counts["CHALLENGER_ONLY"] == 1
    assert counts["BOTH_NO"] == 1


def test_o_not_evaluable_is_separate_from_both_no(tmp_path):
    root = _root(tmp_path)
    rows = [
        _ledger_row(stock_code="000001", group="BOTH_NO", baseline_signal="WAIT", challenger_signal="WAIT"),
        _ledger_row(stock_code="000002", group="NOT_EVALUABLE", evaluation_status="NOT_EVALUABLE", baseline_score=None, challenger_score=None),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, rows)
    counts = _payload("GET", "/api/dual-shadow/latest", root)["counts"]
    assert counts["BOTH_NO"] == 1
    assert counts["NOT_EVALUABLE"] == 1


def test_p_dual_endpoints_are_get_only(tmp_path):
    root = _root(tmp_path)
    assert READ_ONLY_ENDPOINTS == {"/api/dual-shadow/status", "/api/dual-shadow/latest", "/api/dual-shadow/performance", "/api/dual-shadow/runs"}
    for endpoint in READ_ONLY_ENDPOINTS:
        assert route_dashboard_request("GET", endpoint, root)[0] == 200
        status, headers, body = route_dashboard_request("POST", endpoint, root, b"{}")
        assert status == 405
        assert headers["Allow"] == "GET"
        assert json.loads(body.decode("utf-8"))["allowed_methods"] == ["GET"]


def test_q_production_output_bytes_are_unchanged_by_dual_api(tmp_path):
    root = _root(tmp_path)
    production_ledger = root / "output/shadow_signal_records.csv"
    production_registry = root / "output/daily_run_registry.jsonl"
    production_ledger.write_text("production-ledger\n", encoding="utf-8")
    production_registry.write_text("production-registry\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (production_ledger, production_registry)}

    for endpoint in READ_ONLY_ENDPOINTS:
        route_dashboard_request("GET", endpoint, root)

    assert {path: path.read_bytes() for path in before} == before


def test_evidence_maturity_available_and_pending_by_horizon(tmp_path):
    root = _root(tmp_path)
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", LEDGER_HEADER, [_ledger_row(stock_code="000001"), _ledger_row(stock_code="000002")])
    _write_csv(root / "output/dual_shadow_forward_returns.csv", FORWARD_HEADER, [_forward_row(stock_code="000001", horizon=5)])

    maturity = _payload("GET", "/api/dual-shadow/latest", root)["evidence_maturity"]

    assert maturity["5D"] == {"available": 1, "pending": 1, "status": "AVAILABLE"}
    assert maturity["10D"] == {"available": 0, "pending": 2, "status": "AVAILABLE"}
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from dashboard.daily_signal_board import build_daily_signal_board

LEDGER_HEADER = [
    "stock_code",
    "stock_name",
    "market",
    "signal_date",
    "signal_price",
    "signal_score",
    "foreign_status",
    "decision",
    "exclusion_reason",
    "created_at",
    "status",
    "return_5d",
    "return_10d",
    "return_20d",
    "benchmark_return_5d",
    "benchmark_return_10d",
    "benchmark_return_20d",
    "excess_5d",
    "excess_10d",
    "excess_20d",
]

DUAL_LEDGER_HEADER = [
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


def _root(tmp_path: Path) -> Path:
    (tmp_path / "output").mkdir()
    return tmp_path


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_metadata(root: Path, **overrides: object) -> None:
    payload = {
        "schema_version": "1.0",
        "mode": "SHADOW",
        "read_only": True,
        "pipeline_status": "SUCCESS",
        "started_at": "2026-09-14T00:00:00+00:00",
        "finished_at": "2026-09-14T00:00:03+00:00",
        "duration_seconds": 3.0,
        "signal_base_date": "2026-09-14",
        "market_data_max_date": "2026-09-14",
        "investor_data_max_date": "2026-09-14",
        "input_data_freshness": "CURRENT",
        "input_data_freshness_policy": "policy",
        "input_data_stale_after_days": 5,
        "ledger_status": "AVAILABLE",
        "ledger_path": "output/shadow_signal_records.csv",
        "record_count": 1,
        "error": None,
        "source_commit": "cb6522623807108ffdbde21cc75966d5747cc665",
        "runner_version": "1.0",
    }
    payload.update(overrides)
    (root / "output/shadow_dashboard_run_metadata.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_manifest(root: Path) -> None:
    payload = {
        "overall_status": "SUCCESS_WITH_WARNING",
        "phases": [
            {"name": "PRECHECK", "metrics": {"ticker_count": 20}},
            {"name": "INPUT_GATE", "metrics": {
                "market_coverage": {"expected": 20, "found": 20, "missing": []},
                "investor_coverage": {"expected": 20, "found": 20, "missing": []},
            }},
        ],
    }
    (root / "output/daily_operational_run.json").write_text(json.dumps(payload), encoding="utf-8")


def _dual_row(
    trade_date: str,
    stock_code: str,
    stock_name: str,
    evaluation_close: float,
    baseline_score: float,
    baseline_signal_type: str,
    comparison_group: str = "BOTH_NO",
) -> list[object]:
    return [
        trade_date, stock_code, stock_name, evaluation_close,
        0, baseline_score, baseline_signal_type, False,
        0, baseline_score, baseline_signal_type, False,
        0, 0, 0, 0,
        comparison_group, "OK", "v0.1", "v0.2", "commit123", f"{trade_date}T00:00:00+00:00",
    ]


def test_new_signals_empty_when_no_todays_records(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [
            ["006400", "삼성SDI", "KOSPI", "2026-09-09", 574000, 80, "NEGATIVE", "EXCLUDED", "FOREIGN_NEGATIVE", "2026-09-09T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""],
            ["096770", "SK이노베이션", "KOSPI", "2026-09-10", 153100, 81.5, "POSITIVE", "CANDIDATE", "", "2026-09-10T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""],
        ],
    )
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, [])

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["new_signals"]["count"] == 0
    assert board["new_signals"]["records"] == []
    assert board["new_signals"]["empty_message"] == "신규 매수 후보 없음"


def test_new_signals_lists_todays_records(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [["005930", "Samsung", "KOSPI", "2026-09-14", 70000, 88.0, "POSITIVE", "CANDIDATE", "", "2026-09-14T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, [])

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["new_signals"]["count"] == 1
    assert board["new_signals"]["empty_message"] is None
    record = board["new_signals"]["records"][0]
    assert record["stock_name"] == "Samsung"
    assert record["signal_score"] == 88.0
    assert record["decision"] == "CANDIDATE"


def test_watch_list_sorted_descending_and_limited_to_five(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-14", "000001", "A", 100.0, 30.0, "WATCH"),
        _dual_row("2026-09-14", "000002", "B", 200.0, 55.0, "WATCH"),
        _dual_row("2026-09-14", "000003", "C", 300.0, 45.0, "WATCH"),
        _dual_row("2026-09-14", "000004", "D", 400.0, 60.0, "WATCH"),
        _dual_row("2026-09-14", "000005", "E", 500.0, 50.0, "WATCH"),
        _dual_row("2026-09-14", "000006", "F", 600.0, 38.0, "WATCH"),
        _dual_row("2026-09-14", "000007", "G", 700.0, 10.0, "RISK"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    watch = board["watch_list"]

    assert watch["trade_date"] == "2026-09-14"
    assert watch["total_watch_count"] == 6
    assert len(watch["records"]) == 5
    scores = [r["baseline_score"] for r in watch["records"]]
    assert scores == sorted(scores, reverse=True)
    assert watch["records"][0]["stock_name"] == "D"  # 60.0, highest


def test_wait_list_sorted_descending_and_limited_to_five(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-14", "000001", "A", 100.0, 65.0, "WAIT"),
        _dual_row("2026-09-14", "000002", "B", 200.0, 74.9, "WAIT"),
        _dual_row("2026-09-14", "000003", "C", 300.0, 70.0, "WAIT"),
        _dual_row("2026-09-14", "000004", "D", 400.0, 68.0, "WAIT"),
        _dual_row("2026-09-14", "000005", "E", 500.0, 66.0, "WAIT"),
        _dual_row("2026-09-14", "000006", "F", 600.0, 72.0, "WAIT"),
        _dual_row("2026-09-14", "000007", "G", 700.0, 55.0, "WATCH"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    wait = board["wait_list"]

    assert wait["trade_date"] == "2026-09-14"
    assert wait["total_wait_count"] == 6
    assert len(wait["records"]) == 5
    scores = [r["baseline_score"] for r in wait["records"]]
    assert scores == sorted(scores, reverse=True)
    assert wait["records"][0]["stock_name"] == "B"  # 74.9, highest
    assert board["summary"]["wait_count"] == 6


def test_wait_list_gap_to_75_and_score_change(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-13", "005930", "삼성전자", 68000.0, 68.2, "WAIT"),
        _dual_row("2026-09-14", "005930", "삼성전자", 71000.0, 72.3, "WAIT"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    wait = board["wait_list"]

    assert len(wait["records"]) == 1
    record = wait["records"][0]
    assert record["baseline_score"] == 72.3
    assert record["previous_score"] == 68.2
    assert record["score_change"] == 4.1
    assert record["gap_to_75"] == 2.7


def test_wait_list_empty_when_no_wait_signals(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-14", "000001", "A", 100.0, 55.0, "WATCH"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    wait = board["wait_list"]

    assert wait["total_wait_count"] == 0
    assert wait["records"] == []
    assert wait["empty_message"] == "신호 임박 종목 없음"
    assert board["summary"]["wait_count"] == 0


def test_watch_list_unaffected_by_wait_addition(tmp_path):
    """WAIT \uc139\uc158 \ucd94\uac00 \ud6c4\uc5d0\ub3c4 \uae30\uc874 WATCH \uc9d1\uacc4/\ub85c\uc9c1\uc740 \ub3d9\uc77c\ud574\uc57c \ud568."""
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-14", "000001", "A", 100.0, 55.0, "WATCH"),
        _dual_row("2026-09-14", "000002", "B", 200.0, 70.0, "WAIT"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["watch_list"]["total_watch_count"] == 1
    assert board["watch_list"]["records"][0]["stock_name"] == "A"
    assert board["summary"]["watch_count"] == 1


def test_candidate_tracking_join_and_price_change(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [["096770", "SK이노베이션", "KOSPI", "2026-09-10", 153100, 81.5, "POSITIVE", "CANDIDATE", "", "2026-09-10T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    _write_csv(
        root / "output/dual_shadow_signal_ledger.csv",
        DUAL_LEDGER_HEADER,
        [_dual_row("2026-09-14", "096770", "SK이노베이션", 133800.0, 50.8, "WATCH")],
    )

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    tracking = board["candidate_tracking"]

    assert tracking["as_of"] == "2026-09-14"
    assert len(tracking["records"]) == 1
    rec = tracking["records"][0]
    assert rec["signal_date"] == "2026-09-10"
    assert rec["signal_price"] == 153100.0
    assert rec["signal_score"] == 81.5
    assert rec["current_evaluation_close"] == 133800.0
    assert rec["current_baseline_score"] == 50.8
    assert rec["current_baseline_signal_type"] == "WATCH"
    assert rec["dual_match_found"] is True
    expected_change = round((133800.0 - 153100.0) / 153100.0 * 100, 2)
    assert rec["price_change_pct"] == expected_change


def test_candidate_tracking_without_dual_match(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [["005930", "Samsung", "KOSPI", "2026-09-10", 70000, 80.0, "POSITIVE", "CANDIDATE", "", "2026-09-10T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, [])

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    rec = board["candidate_tracking"]["records"][0]

    assert rec["dual_match_found"] is False
    assert rec["current_evaluation_close"] is None
    assert rec["price_change_pct"] is None


def test_dual_comparison_counts_include_zero_groups(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    rows = [
        _dual_row("2026-09-14", "000001", "A", 100.0, 80.0, "BUY_WATCH", comparison_group="BASELINE_ONLY"),
        _dual_row("2026-09-14", "000002", "B", 100.0, 10.0, "RISK", comparison_group="BOTH_NO"),
    ]
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, rows)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    counts = board["dual_comparison"]["counts"]

    assert counts["BASELINE_ONLY"] == 1
    assert counts["BOTH_NO"] == 1
    assert counts["BOTH_YES"] == 0
    assert counts["CHALLENGER_ONLY"] == 0
    assert counts["NOT_EVALUABLE"] == 0


def test_stale_status_keeps_last_valid_data_visible(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root, signal_base_date="2026-09-10", market_data_max_date="2026-09-10", investor_data_max_date="2026-09-10")
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [["096770", "SK이노베이션", "KOSPI", "2026-09-10", 153100, 81.5, "POSITIVE", "CANDIDATE", "", "2026-09-10T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    _write_csv(
        root / "output/dual_shadow_signal_ledger.csv",
        DUAL_LEDGER_HEADER,
        [_dual_row("2026-09-10", "096770", "SK이노베이션", 153100.0, 81.5, "BUY_WATCH")],
    )

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["status"]["analysis_date"] == "2026-09-10"
    assert board["status"]["is_today"] is False
    assert board["status"]["waiting_for_today"] is True
    # 화면이 비지 않고 마지막 유효 CANDIDATE 추적 데이터가 유지된다
    assert len(board["candidate_tracking"]["records"]) == 1
    assert board["candidate_tracking"]["records"][0]["current_baseline_score"] == 81.5


def test_evaluation_close_included_in_watch_and_candidate_sections(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(
        root / "output/shadow_signal_records.csv",
        LEDGER_HEADER,
        [["096770", "SK이노베이션", "KOSPI", "2026-09-10", 153100, 81.5, "POSITIVE", "CANDIDATE", "", "2026-09-10T00:00:00Z", "OPEN", "", "", "", "", "", "", "", "", ""]],
    )
    _write_csv(
        root / "output/dual_shadow_signal_ledger.csv",
        DUAL_LEDGER_HEADER,
        [
            _dual_row("2026-09-14", "096770", "SK이노베이션", 133800.0, 50.8, "WATCH"),
        ],
    )

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["watch_list"]["records"][0]["evaluation_close"] == 133800.0
    assert board["candidate_tracking"]["records"][0]["current_evaluation_close"] == 133800.0


def test_coverage_field_present_when_manifest_available(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, [])
    _write_manifest(root)

    board = build_daily_signal_board(root, today=date(2026, 9, 14))
    coverage = board["status"]["coverage"]

    assert coverage["status"] == "AVAILABLE"
    assert coverage["ticker_count"] == 20
    assert coverage["market"] == {"expected": 20, "found": 20, "missing": []}
    assert coverage["investor"] == {"expected": 20, "found": 20, "missing": []}
    assert board["status"]["production_status"] is not None


def test_coverage_field_unavailable_when_manifest_missing(tmp_path):
    root = _root(tmp_path)
    _write_metadata(root)
    _write_csv(root / "output/shadow_signal_records.csv", LEDGER_HEADER, [])
    _write_csv(root / "output/dual_shadow_signal_ledger.csv", DUAL_LEDGER_HEADER, [])

    board = build_daily_signal_board(root, today=date(2026, 9, 14))

    assert board["status"]["coverage"]["status"] == "UNAVAILABLE"

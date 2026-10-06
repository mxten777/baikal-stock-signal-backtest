"""
Unit tests for Operational Input Integrity Gate (scripts/input_integrity_gate.py).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.input_integrity_gate import (
    GateResult,
    RECOVERABLE_SOURCE_LAG,
    run_input_integrity_gate,
)

TEST_TICKERS = {"005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차"}


@pytest.mark.parametrize("lag_days", [1, 2, 3, 4])
def test_scheduled_uniform_lag_blocks_pipeline(setup_valid_env, lag_days):
    raw_dir, investor_dir = setup_valid_env
    target = pd.Timestamp("2026-10-06")
    investor_date = (target - pd.Timedelta(days=lag_days)).strftime("%Y-%m-%d")
    for ticker in TEST_TICKERS:
        _make_market_df(ticker, ["2026-10-06"]).to_csv(raw_dir / f"{ticker}.csv", index=False)
        _make_investor_df(ticker, [investor_date]).to_csv(investor_dir / f"{ticker}_investor.csv", index=False)
    result = run_input_integrity_gate(
        raw_dir, investor_dir, TEST_TICKERS, allow_source_lag=True, target_trade_date="2026-10-06",
    )
    assert result.status == "FAIL"
    assert result.pipeline_allowed is False
    assert result.error_code == RECOVERABLE_SOURCE_LAG
    assert result.to_dict()["error_code"] == RECOVERABLE_SOURCE_LAG


@pytest.mark.parametrize("problem", [
    "missing", "empty", "parse", "schema", "null_date", "invalid_date", "duplicate",
    "order", "future", "ahead", "mixed", "numeric", "ohlc", "empty_universe",
    "market_lag", "market_future", "stale",
])
def test_scheduled_lag_with_integrity_problem_is_not_recoverable(setup_valid_env, problem):
    raw_dir, investor_dir = setup_valid_env
    for ticker in TEST_TICKERS:
        _make_market_df(ticker, ["2026-10-06"]).to_csv(raw_dir / f"{ticker}.csv", index=False)
        _make_investor_df(ticker, ["2026-10-02"]).to_csv(investor_dir / f"{ticker}_investor.csv", index=False)
    ticker = next(iter(TEST_TICKERS))
    path = investor_dir / f"{ticker}_investor.csv"
    frame = pd.read_csv(path)
    if problem == "missing":
        path.unlink()
    elif problem == "empty":
        path.write_text("", encoding="utf-8")
    elif problem == "parse":
        path.write_text('"unclosed', encoding="utf-8")
    elif problem == "schema":
        frame.drop(columns=["foreign_net_buy"]).to_csv(path, index=False)
    elif problem in {"null_date", "invalid_date", "future", "mixed"}:
        frame.loc[0, "date"] = {
            "null_date": None, "invalid_date": "not-a-date",
            "future": "2026-10-07", "mixed": "2026-10-01",
        }[problem]
        frame.to_csv(path, index=False)
    elif problem == "duplicate":
        pd.concat([frame, frame]).to_csv(path, index=False)
    elif problem == "order":
        _make_investor_df(ticker, ["2026-10-02", "2026-10-01"]).to_csv(path, index=False)
    elif problem == "numeric":
        frame.loc[0, "foreign_net_buy"] = None
        frame.to_csv(path, index=False)
    elif problem == "ohlc":
        market_path = raw_dir / f"{ticker}.csv"
        market = pd.read_csv(market_path)
        market.loc[0, "high"] = 1
        market.to_csv(market_path, index=False)
    elif problem == "ahead":
        for code in TEST_TICKERS:
            _make_investor_df(code, ["2026-10-07"]).to_csv(investor_dir / f"{code}_investor.csv", index=False)
    elif problem in {"market_lag", "market_future", "stale"}:
        latest = {"market_lag": "2026-10-05", "market_future": "2026-10-07", "stale": "2026-09-20"}[problem]
        for code in TEST_TICKERS:
            _make_market_df(code, [latest]).to_csv(raw_dir / f"{code}.csv", index=False)
    result = run_input_integrity_gate(
        raw_dir, investor_dir, {} if problem == "empty_universe" else TEST_TICKERS,
        allow_source_lag=True, target_trade_date="2026-10-06",
    )
    assert result.status == "FAIL"
    assert result.pipeline_allowed is False
    assert result.error_code is None
    assert result.errors


def test_scheduled_current_input_passes(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    result = run_input_integrity_gate(raw_dir, investor_dir, TEST_TICKERS, target_trade_date="2026-09-03")
    assert result.status == "PASS"
    assert result.pipeline_allowed is True
    assert result.error_code is None


def test_legacy_allowed_lag_preserved(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    for ticker in TEST_TICKERS:
        _make_investor_df(ticker, ["2026-09-01"]).to_csv(investor_dir / f"{ticker}_investor.csv", index=False)
    result = run_input_integrity_gate(
        raw_dir, investor_dir, TEST_TICKERS, allow_source_lag=True, today_date="2026-09-03",
    )
    assert result.status == "PASS_WITH_WARNING"
    assert result.pipeline_allowed is True
    assert result.error_code is None


def _make_market_df(ticker: str, dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        rows.append(
            {
                "date": d,
                "open": 50000.0,
                "high": 51000.0,
                "low": 49500.0,
                "close": 50500.0,
                "volume": 100000.0,
            }
        )
    return pd.DataFrame(rows)


def _make_investor_df(ticker: str, dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        rows.append(
            {
                "date": d,
                "ticker": ticker,
                "foreign_net_buy": 1000,
                "institution_net_buy": 2000,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def setup_valid_env(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    investor_dir = tmp_path / "investor"
    raw_dir.mkdir()
    investor_dir.mkdir()

    dates = ["2026-09-01", "2026-09-02", "2026-09-03"]
    for t in TEST_TICKERS:
        df_m = _make_market_df(t, dates)
        df_m.to_csv(raw_dir / f"{t}.csv", index=False)

        df_i = _make_investor_df(t, dates)
        df_i.to_csv(investor_dir / f"{t}_investor.csv", index=False)

    return raw_dir, investor_dir


def test_1_fully_valid_current_input(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status in ("PASS", "PASS_WITH_WARNING")
    assert res.pipeline_allowed is True
    assert res.alignment_status == "CURRENT"
    assert res.market_latest_date == "2026-09-03"
    assert res.investor_latest_date == "2026-09-03"
    assert len(res.errors) == 0


def test_2_market_file_missing(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    (raw_dir / "005930.csv").unlink()

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("MARKET_FILE_MISSING" in e for e in res.errors)


def test_3_investor_file_missing(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    (investor_dir / "005930_investor.csv").unlink()

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("INVESTOR_FILE_MISSING" in e for e in res.errors)


def test_4_market_duplicate_date(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_m = _make_market_df("005930", ["2026-09-01", "2026-09-02", "2026-09-02"])
    df_m.to_csv(raw_dir / "005930.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("DUPLICATE_DATE" in e for e in res.errors)


def test_5_investor_duplicate_date(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_i = _make_investor_df("005930", ["2026-09-01", "2026-09-02", "2026-09-02"])
    df_i.to_csv(investor_dir / "005930_investor.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("DUPLICATE_DATE" in e for e in res.errors)


def test_6_invalid_schema(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_m = _make_market_df("005930", ["2026-09-01", "2026-09-02"]).drop(columns=["close"])
    df_m.to_csv(raw_dir / "005930.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("MARKET_SCHEMA_INVALID" in e for e in res.errors)


def test_7_null_or_invalid_date(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_i = _make_investor_df("005930", ["2026-09-01", "INVALID_DATE"])
    df_i.to_csv(investor_dir / "005930_investor.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("INVALID_DATE" in e for e in res.errors)


def test_8_future_date(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_m = _make_market_df("005930", ["2026-09-01", "2026-09-10"])
    df_m.to_csv(raw_dir / "005930.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("FUTURE_DATE" in e for e in res.errors)


def test_9_market_ticker_latest_mismatch(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_m = _make_market_df("005930", ["2026-09-01", "2026-09-02"])
    df_m.to_csv(raw_dir / "005930.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("MARKET_PARTIAL_DATE" in e for e in res.errors)


def test_10_investor_ticker_latest_mismatch(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_i = _make_investor_df("005930", ["2026-09-01", "2026-09-02"])
    df_i.to_csv(investor_dir / "005930_investor.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("INVESTOR_PARTIAL_DATE" in e for e in res.errors)


def test_11_market_investor_stale_mismatch(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    dates = ["2026-08-01", "2026-08-02"]
    for t in TEST_TICKERS:
        df_m = _make_market_df(t, dates)
        df_m.to_csv(raw_dir / f"{t}.csv", index=False)
        df_i = _make_investor_df(t, dates)
        df_i.to_csv(investor_dir / f"{t}_investor.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        max_stale_days=7,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert res.alignment_status == "STALE"
    assert any("STALE_INPUT" in e for e in res.errors)


def test_12_valid_uniform_source_lag(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    # Market at 2026-09-03, Investor at 2026-09-01
    i_dates = ["2026-09-01"]
    for t in TEST_TICKERS:
        df_i = _make_investor_df(t, i_dates)
        df_i.to_csv(investor_dir / f"{t}_investor.csv", index=False)

    # Disallow source lag (default policy)
    res_disallowed = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        allow_source_lag=False,
        today_date="2026-09-04",
    )
    assert res_disallowed.status == "FAIL"
    assert res_disallowed.pipeline_allowed is False
    assert any("MARKET_INVESTOR_MISALIGNED" in e for e in res_disallowed.errors)

    # Allow source lag
    res_allowed = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        allow_source_lag=True,
        max_source_lag_days=3,
        today_date="2026-09-04",
    )
    assert res_allowed.alignment_status == "SOURCE_LAG"
    assert res_allowed.pipeline_allowed is True
    assert res_allowed.status == "PASS_WITH_WARNING"


def test_13_empty_file(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    (raw_dir / "005930.csv").write_text("", encoding="utf-8")

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("EMPTY_FILE" in e for e in res.errors)


def test_14_numeric_invalid(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    df_m = _make_market_df("005930", ["2026-09-01"])
    df_m.loc[0, "high"] = 10.0
    df_m.loc[0, "low"] = 200.0  # severe OHLC violation
    df_m.to_csv(raw_dir / "005930.csv", index=False)

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.status == "FAIL"
    assert res.pipeline_allowed is False
    assert any("NUMERIC_INVALID" in e for e in res.errors)


def test_15_zero_mutation(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    files_before = {}
    for f in list(raw_dir.glob("*.csv")) + list(investor_dir.glob("*.csv")):
        files_before[f] = f.read_bytes()

    _ = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )

    for f, content in files_before.items():
        assert f.read_bytes() == content, f"File {f} was mutated!"


def test_16_machine_readable_json_contract(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    res_dict = res.to_dict()
    json_str = json.dumps(res_dict)
    loaded = json.loads(json_str)

    required_keys = [
        "status",
        "pipeline_allowed",
        "checked_at",
        "expected_ticker_count",
        "market_file_count",
        "investor_file_count",
        "market_latest_date",
        "investor_latest_date",
        "alignment_status",
        "market_coverage",
        "investor_coverage",
        "errors",
        "warnings",
    ]
    for key in required_keys:
        assert key in loaded


def test_17_expected_ticker_coverage(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    (raw_dir / "005930.csv").unlink()

    res = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res.market_coverage["expected"] == 3
    assert res.market_coverage["found"] == 2
    assert res.market_coverage["missing"] == ["005930"]


def test_18_rerun_deterministic(setup_valid_env):
    raw_dir, investor_dir = setup_valid_env
    res1 = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    res2 = run_input_integrity_gate(
        raw_dir=raw_dir,
        investor_dir=investor_dir,
        tickers=TEST_TICKERS,
        today_date="2026-09-04",
    )
    assert res1.status == res2.status
    assert res1.pipeline_allowed == res2.pipeline_allowed
    assert res1.alignment_status == res2.alignment_status
    assert res1.errors == res2.errors
    assert res1.warnings == res2.warnings

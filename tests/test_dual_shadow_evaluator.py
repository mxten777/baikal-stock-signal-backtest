"""
tests/test_dual_shadow_evaluator.py
===================================
Unit & Parity Tests for DUAL Shadow STEP 2 Latest-Day Evaluation Adapter.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import config
from src.dual_shadow_evaluator import (
    BaselineEvaluation,
    ChallengerEvaluation,
    DualShadowEvaluation,
    evaluate_latest_day,
)
from src.indicators import add_all_indicators
from src.signal_engine import (
    generate_signals,
    generate_signals_v2,
)

DATA_RAW_DIR = Path(__file__).parent.parent / "data" / "raw"


def _format_date(val: object) -> str:
    """날짜 필드를 YYYY-MM-DD 문자열로 통일 변환."""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    return s[:10] if len(s) >= 10 else s


def _build_indicator_df(rows: list[dict]) -> pd.DataFrame:
    """테스트용 가상 지표 DataFrame 생성."""
    default_row = {
        "date": "2026-09-10",
        "close": 100000.0,
        "ma5": 102000.0,
        "ma20": 100000.0,
        "ma60": 98000.0,
        "volume": 2000000.0,
        "volume_ma20": 1000000.0,
        "rsi": 55.0,
        "macd": 500.0,
        "macd_signal": 300.0,
        "return_5d_pct": 2.0,
    }
    expanded = []
    for r in rows:
        merged = dict(default_row)
        merged.update(r)
        expanded.append(merged)
    df = pd.DataFrame(expanded)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


class TestBaselineParity:
    """기존 generate_signals()와의 100% parity 검증."""

    def test_baseline_signal_parity_on_real_data(self):
        """실제 종목 데이터에서 최신 거래일 Baseline 평가 parity 검증."""
        sk_path = DATA_RAW_DIR / "096770.csv"
        if not sk_path.exists():
            pytest.skip("096770.csv not found")

        df = pd.read_csv(sk_path)
        df_ind = add_all_indicators(df)

        res = evaluate_latest_day(df_ind, "096770", "SK이노베이션")
        signals_v1 = generate_signals(df_ind, "096770", "SK이노베이션")

        latest_date = _format_date(df_ind["date"].iloc[-1])
        sig_v1_latest = signals_v1[signals_v1["signal_date"] == latest_date]

        assert len(sig_v1_latest) == 1
        sig_row = sig_v1_latest.iloc[0]

        assert res.baseline.signal_present is True
        assert res.baseline.raw_score == int(sig_row["raw_score"])
        assert res.baseline.score == float(sig_row["score"])
        assert res.baseline.signal_type == str(sig_row["signal_type"])

    def test_baseline_no_signal_parity_on_real_data(self):
        """실제 종목 데이터에서 신호가 없는 경우 Baseline parity 검증."""
        samsung_path = DATA_RAW_DIR / "005930.csv"
        if not samsung_path.exists():
            pytest.skip("005930.csv not found")

        df = pd.read_csv(samsung_path)
        df_ind = add_all_indicators(df)

        res = evaluate_latest_day(df_ind, "005930", "삼성전자")
        signals_v1 = generate_signals(df_ind, "005930", "삼성전자")

        latest_date = _format_date(df_ind["date"].iloc[-1])
        sig_v1_latest = signals_v1[signals_v1["signal_date"] == latest_date]

        assert sig_v1_latest.empty
        assert res.baseline.signal_present is False

    def test_all_raw_tickers_baseline_parity(self):
        """data/raw/의 모든 종목에 대해 Baseline 최신일 판정 parity 검증."""
        csv_files = list(DATA_RAW_DIR.glob("*.csv"))
        if not csv_files:
            pytest.skip("No csv files in data/raw")

        for f in csv_files:
            ticker = f.stem
            name = config.TICKERS.get(ticker, ticker)
            df = pd.read_csv(f)
            df_ind = add_all_indicators(df)

            res = evaluate_latest_day(df_ind, ticker, name)
            signals_v1 = generate_signals(df_ind, ticker, name)

            latest_date = _format_date(df_ind["date"].iloc[-1])
            has_sig = not signals_v1.empty and len(signals_v1[signals_v1["signal_date"] == latest_date]) > 0

            assert res.baseline.signal_present == has_sig
            if has_sig:
                row = signals_v1[signals_v1["signal_date"] == latest_date].iloc[0]
                assert res.baseline.raw_score == int(row["raw_score"])
                assert res.baseline.score == float(row["score"])
                assert res.baseline.signal_type == str(row["signal_type"])


class TestChallengerParity:
    """기존 generate_signals_v2()와의 parity 및 no-signal 정보 보존 검증."""

    def test_challenger_signal_parity_when_fired(self):
        """Challenger 신호 발생 시 generate_signals_v2()와 완전 일치 검증."""
        # 신호가 발생하도록 설계된 과거 시점 슬라이스 탐색
        sk_path = DATA_RAW_DIR / "096770.csv"
        if not sk_path.exists():
            pytest.skip("096770.csv not found")

        df = pd.read_csv(sk_path)
        df_ind = add_all_indicators(df)
        signals_v2 = generate_signals_v2(df_ind, "096770", "SK이노베이션")

        assert not signals_v2.empty
        target_date = signals_v2.iloc[0]["signal_date"]

        # target_date 시점까지의 데이터만 잘라서 평가
        sub_df = df_ind[df_ind["date"] <= target_date].copy()
        res = evaluate_latest_day(sub_df, "096770", "SK이노베이션")

        v2_row = signals_v2.iloc[0]
        assert res.challenger.signal_present is True
        assert res.challenger.raw_score == int(v2_row["raw_score"])
        assert res.challenger.score == float(v2_row["score"])
        assert res.challenger.signal_type == str(v2_row["signal_type"])
        assert res.challenger.volume_penalty == int(v2_row["v2_volume_penalty"])
        assert res.challenger.pre_return_penalty == int(v2_row["v2_pre_return_penalty"])
        assert res.challenger.rsi_penalty == int(v2_row["v2_rsi_penalty"])

    def test_all_raw_tickers_challenger_parity(self):
        """data/raw/의 모든 종목에 대해 Challenger 최신일 판정 parity 검증."""
        csv_files = list(DATA_RAW_DIR.glob("*.csv"))
        if not csv_files:
            pytest.skip("No csv files in data/raw")

        for f in csv_files:
            ticker = f.stem
            name = config.TICKERS.get(ticker, ticker)
            df = pd.read_csv(f)
            df_ind = add_all_indicators(df)

            res = evaluate_latest_day(df_ind, ticker, name)
            signals_v2 = generate_signals_v2(df_ind, ticker, name)

            latest_date = _format_date(df_ind["date"].iloc[-1])
            has_sig = not signals_v2.empty and len(signals_v2[signals_v2["signal_date"] == latest_date]) > 0

            assert res.challenger.signal_present == has_sig
            if has_sig:
                row = signals_v2[signals_v2["signal_date"] == latest_date].iloc[0]
                assert res.challenger.raw_score == int(row["raw_score"])
                assert res.challenger.score == float(row["score"])
                assert res.challenger.signal_type == str(row["signal_type"])


class TestSKInnovationFixture:
    """SK이노베이션 2026-09-10 실제 사례 정밀 검증."""

    def test_sk_innovation_2026_09_10_values(self):
        sk_path = DATA_RAW_DIR / "096770.csv"
        if not sk_path.exists():
            pytest.skip("096770.csv not found")

        df = pd.read_csv(sk_path)
        df_ind = add_all_indicators(df)

        res = evaluate_latest_day(df_ind, "096770", "SK이노베이션")

        assert res.ticker == "096770"
        assert res.name == "SK이노베이션"
        assert res.trade_date == "2026-09-10"
        assert res.close == 153100.0
        assert res.evaluation_status == "OK"

        # Baseline 검증
        assert res.baseline.raw_score == 53
        assert res.baseline.score == 81.5
        assert res.baseline.signal_present is True
        assert res.baseline.signal_type == "BUY_WATCH"

        # Challenger 검증 (신호 미발생에도 점수/타입/페널티 완벽 보존)
        assert res.challenger.raw_score == 43
        assert res.challenger.score == 66.2
        assert res.challenger.signal_present is False
        assert res.challenger.signal_type == "WAIT"
        assert res.challenger.volume_penalty == 0
        assert res.challenger.pre_return_penalty == 10
        assert res.challenger.rsi_penalty == 0
        assert res.challenger.total_penalty == 10

        # 비교군 검증
        assert res.comparison_group == "BASELINE_ONLY"


class TestComparisonGroups:
    """4가지 comparison group (BOTH_YES, BASELINE_ONLY, CHALLENGER_ONLY, BOTH_NO) 검증."""

    def test_both_yes_fixture(self):
        """Baseline & Challenger 모두 신호가 발생하는 경우."""
        # Day 1: 낮은 점수 (prev_score < 75)
        # Day 2: 높은 점수 (raw=55, score=84.6), 페널티 0 -> 둘 다 신호 발생
        rows = [
            {
                "date": "2026-09-08",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                "date": "2026-09-09",
                "close": 100000.0,
                # Trend=25 (ma5>ma20 +7, ma20>ma60 +7, close>ma20 +5, ma20>prev_ma20 +6)
                "ma5": 102000.0,
                "ma20": 95000.0,
                "ma60": 92000.0,
                # Volume=20 (ratio>=2.0 +15, close>prev & vol>vma +5)
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                # Momentum=10 (rsi 55 +5, return>0 +3, macd 유지 +3 -> max 20)
                "rsi": 55.0,
                "macd": 100.0,
                "macd_signal": 50.0,
                "return_5d_pct": 5.0,  # < 12% threshold -> penalty 0
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999001", "BOTH_YES_TEST")

        assert res.evaluation_status == "OK"
        assert res.baseline.signal_present is True
        assert res.challenger.signal_present is True
        assert res.comparison_group == "BOTH_YES"

    def test_baseline_only_fixture(self):
        """Baseline만 신호 발생하고 Challenger는 페널티로 신호 미발생."""
        rows = [
            {
                "date": "2026-09-08",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                "date": "2026-09-09",
                "close": 100000.0,
                # Trend=25
                "ma5": 102000.0,
                "ma20": 95000.0,
                "ma60": 92000.0,
                # Volume=20
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                # Momentum=5 (RSI 55 +5)
                "rsi": 55.0,
                "macd": -10.0,
                "macd_signal": -5.0,
                "return_5d_pct": 15.0,  # >= 12% -> penalty 10
            },
        ]
        # Baseline raw = 50 -> score = 76.9 (>=75) -> signal_present = True
        # Challenger raw = 50 - 10 = 40 -> score = 61.5 (<75) -> signal_present = False
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999002", "BASELINE_ONLY_TEST")

        assert res.evaluation_status == "OK"
        assert res.baseline.signal_present is True
        assert res.challenger.signal_present is False
        assert res.comparison_group == "BASELINE_ONLY"
        assert res.challenger.pre_return_penalty == 10
        assert res.challenger.total_penalty == 10

    def test_both_no_low_score_fixture(self):
        """점수가 낮아 둘 다 신호 미발생."""
        rows = [
            {
                "date": "2026-09-08",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 40.0,
                "macd": -50.0,
                "macd_signal": 0.0,
                "return_5d_pct": -2.0,
            },
            {
                "date": "2026-09-09",
                "close": 91000.0,
                "ma5": 90500.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 42.0,
                "macd": -40.0,
                "macd_signal": -20.0,
                "return_5d_pct": 1.0,
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999003", "BOTH_NO_LOW_SCORE")

        assert res.evaluation_status == "OK"
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is False
        assert res.comparison_group == "BOTH_NO"

    def test_both_no_consecutive_high_score_fixture(self):
        """전일 이미 점수가 75점 이상이어서 신규 신호가 발생하지 않는 경우 (prev_score >= 75)."""
        rows = [
            {
                "date": "2026-09-07",
                "close": 80000.0,
                "ma5": 80000.0,
                "ma20": 80000.0,
                "ma60": 80000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                # Day 1: 고득점 -> 신호 발생 (prev_score=0 -> score=84.6)
                "date": "2026-09-08",
                "close": 100000.0,
                "ma5": 102000.0,
                "ma20": 95000.0,
                "ma60": 92000.0,
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                "rsi": 55.0,
                "macd": 100.0,
                "macd_signal": 50.0,
                "return_5d_pct": 5.0,
            },
            {
                # Day 2: 여전히 고득점이나 전일 prev_score(84.6) >= 75 이므로 신규 신호 False
                "date": "2026-09-09",
                "close": 101000.0,
                "ma5": 103000.0,
                "ma20": 96000.0,
                "ma60": 92500.0,
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                "rsi": 58.0,
                "macd": 110.0,
                "macd_signal": 60.0,
                "return_5d_pct": 6.0,
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999004", "BOTH_NO_CONSECUTIVE")

        assert res.evaluation_status == "OK"
        assert res.baseline.score >= 75.0
        assert res.challenger.score >= 75.0
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is False
        assert res.comparison_group == "BOTH_NO"

    def test_challenger_only_fixture(self):
        """
        CHALLENGER_ONLY 가능성 검증:
        - Day t-1: Baseline raw 50 (score 76.9 >= 75, prev_score=76.9).
                   Challenger는 return_5d >= 12% 페널티 10 적용되어 raw 40 (score 61.5 < 75, prev_score=61.5).
        - Day t: return_5d가 5%로 내려가 페널티 해제 (0), 둘 다 raw 50 (score 76.9 >= 75).
        - Baseline: prev_score(76.9) < 75 가 False -> signal_present = False.
        - Challenger: prev_score(61.5) < 75 가 True -> signal_present = True.
        - 결과: CHALLENGER_ONLY!
        """
        rows = [
            {
                "date": "2026-09-07",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                # Day t-1: raw 50 -> score 76.9
                # Challenger has pre_return 15% -> penalty 10 -> raw 40 -> score 61.5
                "date": "2026-09-08",
                "close": 100000.0,
                "ma5": 102000.0,
                "ma20": 95000.0,
                "ma60": 92000.0,
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                "rsi": 55.0,
                "macd": -10.0,
                "macd_signal": -5.0,
                "return_5d_pct": 15.0,  # penalty 10 for v2
            },
            {
                # Day t: raw 50 -> score 76.9
                # Challenger penalty 0 -> raw 50 -> score 76.9
                "date": "2026-09-09",
                "close": 101000.0,
                "ma5": 103000.0,
                "ma20": 96000.0,
                "ma60": 92500.0,
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                "rsi": 55.0,
                "macd": -10.0,
                "macd_signal": -5.0,
                "return_5d_pct": 5.0,  # penalty 0
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999005", "CHALLENGER_ONLY_TEST")

        assert res.evaluation_status == "OK"
        assert res.baseline.score >= 75.0
        assert res.challenger.score >= 75.0
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is True
        assert res.comparison_group == "CHALLENGER_ONLY"
        assert res.challenger.score >= 75.0
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is True
        assert res.comparison_group == "CHALLENGER_ONLY"


class TestOverheatingClassification:
    """과열 필터 적용 시 signal_type OVERHEATED 정상 분류 검증."""

    def test_overheated_rsi(self):
        rows = [
            {
                "date": "2026-09-08",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                "date": "2026-09-09",
                "close": 100000.0,
                "ma5": 102000.0,
                "ma20": 95000.0,
                "ma60": 92000.0,
                "volume": 2500000.0,
                "volume_ma20": 1000000.0,
                "rsi": 80.0,  # > OVERHEATED_RSI (75.0)
                "macd": 100.0,
                "macd_signal": 50.0,
                "return_5d_pct": 5.0,
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "999006", "OVERHEATED_TEST")

        assert res.evaluation_status == "OK"
        assert res.baseline.signal_type == "OVERHEATED"
        assert res.challenger.signal_type == "OVERHEATED"


class TestNotEvaluable:
    """NOT_EVALUABLE 케이스 철저 검증."""

    def test_none_dataframe(self):
        res = evaluate_latest_day(None, "005930", "삼성전자")
        assert res.evaluation_status == "NOT_EVALUABLE"
        assert res.comparison_group == "BOTH_NO"
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is False
        assert res.baseline.score is None
        assert res.challenger.score is None
        assert res.close is None

    def test_empty_dataframe(self):
        res = evaluate_latest_day(pd.DataFrame(), "005930", "삼성전자")
        assert res.evaluation_status == "NOT_EVALUABLE"
        assert res.comparison_group == "BOTH_NO"
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is False

    def test_missing_required_column(self):
        rows = [{"date": "2026-09-10", "close": 70000.0, "ma5": 70000.0}]
        df = pd.DataFrame(rows)
        res = evaluate_latest_day(df, "005930", "삼성전자")
        assert res.evaluation_status == "NOT_EVALUABLE"
        assert res.trade_date == "2026-09-10"
        assert res.close == 70000.0

    def test_nan_in_latest_row_required_column(self):
        rows = [
            {
                "date": "2026-09-08",
                "close": 90000.0,
                "ma5": 90000.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
            {
                "date": "2026-09-09",
                "close": 91000.0,
                "ma5": 90500.0,
                "ma20": np.nan,  # NaN in required column
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "005930", "삼성전자")
        assert res.evaluation_status == "NOT_EVALUABLE"
        assert res.baseline.signal_present is False
        assert res.challenger.signal_present is False

    def test_nan_in_latest_close(self):
        rows = [
            {
                "date": "2026-09-09",
                "close": np.nan,
                "ma5": 90500.0,
                "ma20": 90000.0,
                "ma60": 90000.0,
                "volume": 1000000.0,
                "volume_ma20": 1000000.0,
                "rsi": 50.0,
                "macd": 0.0,
                "macd_signal": 0.0,
                "return_5d_pct": 0.0,
            },
        ]
        df = _build_indicator_df(rows)
        res = evaluate_latest_day(df, "005930", "삼성전자")
        assert res.evaluation_status == "NOT_EVALUABLE"
        assert res.close is None


class TestSerialization:
    """to_dict() serialization schema 검증."""

    def test_to_dict_structure(self):
        sk_path = DATA_RAW_DIR / "096770.csv"
        if not sk_path.exists():
            pytest.skip("096770.csv not found")

        df = pd.read_csv(sk_path)
        df_ind = add_all_indicators(df)
        res = evaluate_latest_day(df_ind, "096770", "SK이노베이션")
        d = res.to_dict()

        # Top-level keys
        assert "trade_date" in d
        assert "ticker" in d
        assert "name" in d
        assert "close" in d
        assert "baseline" in d
        assert "challenger" in d
        assert "comparison_group" in d
        assert "evaluation_status" in d

        # Baseline sub-keys
        assert set(d["baseline"].keys()) == {
            "raw_score",
            "score",
            "signal_type",
            "signal_present",
        }

        # Challenger sub-keys
        assert set(d["challenger"].keys()) == {
            "raw_score",
            "score",
            "signal_type",
            "signal_present",
            "volume_penalty",
            "pre_return_penalty",
            "rsi_penalty",
            "total_penalty",
        }

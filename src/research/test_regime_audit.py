"""Focused audit tests kept entirely inside the Research namespace."""

import numpy as np
import pandas as pd

from src.research.regime_audit import daily_regimes, join_events, write_audit


def fixture(days=100):
    dates = pd.bdate_range("2024-01-02", periods=days)
    frames = []
    for ticker, sign in (("000001", -1), ("000002", 1)):
        frame = pd.DataFrame({
            "ticker": ticker, "trading_date": dates, "tradable_bar": True, "feature_ready": True,
            "close": 110, "ma20": 100, "ma5": 105, "return_1d_pct": sign * (1 + np.arange(days) / 100),
            "return_5d_pct": sign * 2, "return_20d_pct": sign * 4,
            "volume_ratio": 1.2, "volume_ratio_5d_20d": 1.1,
        })
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def test_regime_truncated_prefix_and_future_perturbation():
    panel = fixture()
    before = daily_regimes(panel)
    dates = before["trading_date"]
    truncated = daily_regimes(panel.loc[panel["trading_date"].le(dates.iloc[79])])
    pd.testing.assert_frame_equal(before.iloc[:80], truncated)
    changed = panel.copy()
    changed.loc[changed["trading_date"].gt(dates.iloc[79]), ["return_1d_pct", "return_20d_pct"]] *= 100
    after = daily_regimes(changed)
    pd.testing.assert_frame_equal(before.iloc[:80], after.iloc[:80])
    assert before.loc[:59, "volatility_regime"].eq("UNKNOWN").all()
    assert before.loc[60, "volatility_regime"] == "HIGH"
    assert before.loc[60, "volatility_past_median_pct"] == before.loc[:59, "cross_sectional_volatility_1d_pct"].median()


def test_denominators_do_not_treat_missing_or_untradable_as_declines():
    panel = fixture(2)
    panel.loc[0, "return_20d_pct"] = np.nan
    panel.loc[1, "tradable_bar"] = False
    daily = daily_regimes(panel)
    assert daily.loc[0, "return_20d_count"] == 1
    assert daily.loc[0, "positive_20d_pct"] == 100
    assert daily.loc[0, "direction"] == "BULL"
    assert daily.loc[1, "tradable_count"] == 1


def test_direction_boundaries_are_predeclared_and_not_target_driven():
    panel = pd.concat([fixture(1).iloc[[1]]] * 10, ignore_index=True)
    for positive, expected in ((3, "BEAR"), (4, "NEUTRAL"), (6, "NEUTRAL"), (7, "BULL")):
        panel["return_20d_pct"] = -1.0
        panel.loc[:positive - 1, "return_20d_pct"] = 1.0
        panel["future_return_20d"] = -999.0
        assert daily_regimes(panel).loc[0, "direction"] == expected
        panel["future_return_20d"] = 999.0
        assert daily_regimes(panel).loc[0, "direction"] == expected


def test_join_is_same_day_and_not_future_day():
    daily = daily_regimes(fixture(3))
    events = pd.DataFrame({
        "strategy": ["A"], "ticker": ["000001"], "trading_date": [daily.loc[1, "trading_date"]],
        "cohort": ["crossing"], "split": ["2024"],
    })
    joined = join_events(events, daily)
    assert joined.loc[0, "cross_sectional_volatility_1d_pct"] == daily.loc[1, "cross_sectional_volatility_1d_pct"]


def test_output_cannot_overwrite_35c_and_rerun_must_match(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        write_audit(tmp_path, "..\\fixed574_2026-10-07\\summary.json", b"forbidden")
    write_audit(tmp_path, "test.json", b"same")
    write_audit(tmp_path, "test.json", b"same")
    with pytest.raises(RuntimeError):
        write_audit(tmp_path, "test.json", b"different")

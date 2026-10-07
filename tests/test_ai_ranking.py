from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.research import ai_ranking as ai
from src.research.pipeline import operational_inventory, verify_inventory


def fixture(days: int = 50, tickers: int = 10) -> ai.Inputs:
    calendar = pd.bdate_range("2024-01-02", periods=days)
    rows, events = [], []
    for position, date in enumerate(calendar):
        for number in range(tickers):
            ticker = f"{number:06d}"
            raw = float(number - tickers / 2)
            row = {
                "trading_date": date, "ticker": ticker, "feature_ready": True,
                "overheated": False, "return_20d_pct": raw + position / 100,
                "rsi": 45 + number, "volume_ratio_5d_20d": 0.8 + number / 10,
                "volatility_20d_pct": 1 + number / 10,
                "future_return_10d": raw if position + 10 < days else np.nan,
                "target_date_10d": calendar[position + 10] if position + 10 < days else pd.NaT,
                "target_status_10d": "AVAILABLE" if position + 10 < days else "IMMATURE",
                "evaluation_ready_10d": position + 10 < days,
            }
            for strategy in ai.STRATEGIES:
                row[f"signal_{strategy}"] = True
                row[f"score_{strategy}"] = 80 + number
                events.append({"trading_date": date, "ticker": ticker, "strategy": strategy,
                               "cohort": "crossing", "overheated": False, "score": 80 + number})
            rows.append(row)
    panel = pd.DataFrame(rows)
    daily = pd.DataFrame({"trading_date": calendar, "positive_20d_pct": 50.0,
                          "direction": "NEUTRAL", "volatility_regime": "LOW"})
    basket = pd.DataFrame({"trading_date": calendar,
                           "basket_return_10d": [0.0] * (days - 10) + [np.nan] * 10})
    return ai.Inputs(panel, pd.DataFrame(events), daily, basket, calendar, {})


def labelled(inputs: ai.Inputs) -> pd.DataFrame:
    return ai.attach_labels(ai.build_candidates(inputs), inputs.basket, inputs.calendar, inputs.calendar[-1])


def scored(days: int = 50, tickers: int = 10) -> tuple[pd.DataFrame, ai.Inputs]:
    inputs = fixture(days, tickers)
    rows = labelled(inputs)
    rows["probability"] = rows["ticker"].astype(int) / tickers
    rows["score_A"] = -rows["ticker"].astype(int)
    return rows, inputs


def test_membership_union_unique_no_future_filter():
    inputs = fixture()
    rows = ai.build_candidates(inputs)
    assert len(rows) == 500
    assert not rows.duplicated(ai.KEY).any()
    assert rows[["member_A", "member_B", "member_C"]].all().all()
    assert rows["future_return_10d"].isna().sum() == 100


@pytest.mark.parametrize("kind", ["crossing_duplicate", "panel_duplicate", "missing_panel",
                                 "heat_conflict", "score_conflict", "missing_event", "regime_duplicate"])
def test_invalid_candidate_join_rejected(kind):
    inputs = fixture()
    if kind == "crossing_duplicate":
        inputs.events.loc[len(inputs.events)] = inputs.events.iloc[0]
    elif kind == "panel_duplicate":
        inputs.panel.loc[len(inputs.panel)] = inputs.panel.iloc[0]
    elif kind == "missing_panel":
        inputs.panel.drop(index=0, inplace=True)
    elif kind == "heat_conflict":
        inputs.events.loc[0, "overheated"] = True
    elif kind == "score_conflict":
        inputs.events.loc[0, "score"] = 99
    elif kind == "missing_event":
        inputs.events.drop(index=0, inplace=True)
    else:
        inputs.daily.loc[len(inputs.daily)] = inputs.daily.iloc[0]
    with pytest.raises(ValueError):
        ai.build_candidates(inputs)


def test_duplicate_scored_candidate_rejected():
    rows, _ = scored()
    with pytest.raises(ValueError, match="duplicate"):
        ai.selections(pd.concat([rows, rows.iloc[:1]]))


@pytest.mark.parametrize("extra", ["future_return_10d", "target_status_10d", "evaluation_ready_10d",
                                  "score_A", "ticker"])
def test_exact_feature_allowlist(extra):
    rows = ai.build_candidates(fixture())
    with pytest.raises(ValueError, match="allowlist"):
        ai.feature_matrix(rows, (*ai.FEATURES, extra))
    with pytest.raises(ValueError, match="allowlist"):
        ai.feature_matrix(rows, ai.FEATURES[:-1])
    assert tuple(ai.feature_matrix(rows).columns) == ai.FEATURES


def test_nonfinite_feature_is_error_not_imputation():
    inputs = fixture()
    inputs.panel.loc[0, "rsi"] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        ai.build_candidates(inputs)


def test_purge_boundary_and_maturity_preserves_candidates():
    calendar = pd.bdate_range("2024-12-02", periods=40)
    inputs = fixture(40)
    mapping = dict(zip(inputs.calendar, calendar))
    candidates = ai.build_candidates(inputs)
    candidates["trading_date"] = candidates["trading_date"].map(mapping)
    candidates["target_date_10d"] = candidates["target_date_10d"].map(mapping)
    basket = inputs.basket.copy()
    basket["trading_date"] = basket["trading_date"].map(mapping)
    result = ai.attach_labels(candidates, basket, calendar, calendar[-1])
    crossing = result["trading_date"].dt.year.ne(result["target_date_10d"].dt.year)
    assert not result.loc[crossing, "label_ready"].any()
    assert result.loc[result["trading_date"].dt.year.eq(2025), "split_role"].eq("validation").all()
    early = ai.attach_labels(candidates, basket, calendar, calendar[15])
    assert early.loc[early["target_date_10d"].gt(calendar[15]), "y"].isna().all()
    assert len(early) == len(candidates)


def test_wrong_horizon_rejected():
    inputs = fixture()
    rows = ai.build_candidates(inputs)
    rows.loc[0, "target_date_10d"] = inputs.calendar[11]
    with pytest.raises(ValueError, match="ten"):
        ai.attach_labels(rows, inputs.basket, inputs.calendar, inputs.calendar[-1])


def test_train_only_scaler_and_prediction_does_not_refit():
    rows = labelled(fixture())
    train = rows.loc[rows["label_ready"]]
    with pytest.warns(FutureWarning, match="penalty"):
        model = ai.fit_train(train)
    assert model.warning_records
    np.testing.assert_allclose(model.pipeline.named_steps["scaler"].mean_,
                               ai.feature_matrix(train).mean().to_numpy())
    mean = model.pipeline.named_steps["scaler"].mean_.copy()
    validation = rows.copy()
    validation["split_role"] = "validation"
    validation["rsi"] += 1000
    ai.predict(model, validation)
    np.testing.assert_array_equal(mean, model.pipeline.named_steps["scaler"].mean_)
    with pytest.raises(ValueError, match="Train"):
        ai.fit_train(validation)
    with pytest.raises(ValueError, match="Train"):
        ai.fit_train(rows)
    assert model.pipeline.named_steps["model"].get_params()["penalty"] == "l2"


def test_date_weights_have_equal_date_totals_and_mean_one():
    rows = pd.DataFrame({"trading_date": pd.to_datetime(["2024-01-02"] * 2 + ["2024-01-03"] * 5),
                         "ticker": [f"{n:06d}" for n in range(7)]})
    weights = ai.date_weights(rows)
    totals = pd.Series(weights).groupby(rows["trading_date"]).sum()
    np.testing.assert_allclose(totals, [3.5, 3.5])
    assert weights.mean() == pytest.approx(1)


def test_prefix_future_and_target_perturbation_do_not_change_candidates_or_rank():
    inputs = fixture()
    before = ai.build_candidates(inputs)
    original, _ = scored()
    cutoff = inputs.calendar[25]
    prefix = ai.Inputs(inputs.panel.loc[inputs.panel["trading_date"].le(cutoff)].copy(),
                       inputs.events.loc[inputs.events["trading_date"].le(cutoff)].copy(),
                       inputs.daily.loc[inputs.daily["trading_date"].le(cutoff)].copy(),
                       inputs.basket, inputs.calendar[:26], {})
    prefix_rows = ai.build_candidates(prefix)
    pd.testing.assert_frame_equal(before.loc[before["trading_date"].le(cutoff)].reset_index(drop=True),
                                  prefix_rows)
    inputs.panel.loc[inputs.panel["trading_date"].gt(cutoff), "rsi"] = 99
    inputs.panel["future_return_10d"] = -999
    after = ai.build_candidates(inputs)
    pd.testing.assert_frame_equal(ai.feature_matrix(before.loc[before["trading_date"].le(cutoff)]),
                                  ai.feature_matrix(after.loc[after["trading_date"].le(cutoff)]))
    first, decisions = ai.selections(original)
    changed = original.copy()
    changed["Z"] = np.nan
    changed["label_ready"] = False
    second, new_decisions = ai.selections(changed)
    pd.testing.assert_frame_equal(first[[*ai.KEY, "method"]], second[[*ai.KEY, "method"]])
    pd.testing.assert_frame_equal(decisions, new_decisions)


@pytest.mark.parametrize("n,k", [(1, 0), (4, 0), (5, 1), (6, 2), (10, 2), (21, 5)])
def test_top20_and_ticker_tie_break(n, k):
    rows, _ = scored(15, n)
    rows["probability"] = 0.5
    rows[["score_A", "score_B", "score_C"]] = 80
    selected, decisions = ai.selections(rows.sample(frac=1, random_state=37))
    assert decisions["k"].eq(k).all()
    assert decisions["status"].eq("SELECTED" if k else "INSUFFICIENT_CANDIDATES").all()
    for _, group in selected.groupby(["trading_date", "method"]):
        assert list(group["ticker"]) == [f"{i:06d}" for i in range(k)]


def test_missing_label_no_replacement_and_positive_delta_sign():
    rows, inputs = scored()
    selected, decisions = ai.selections(rows)
    report = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    assert report["delta_pp"] == pytest.approx(8)
    assert report["relative_win_rate_difference"] == pytest.approx(1)
    first_date = inputs.calendar[0]
    selected.loc[selected["trading_date"].eq(first_date) & selected["method"].eq("AI"),
                 ["Z", "label_ready"]] = [np.nan, False]
    missing = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    assert missing["paired_dates"] == report["paired_dates"] - 1
    assert first_date.strftime("%Y-%m-%d") not in [
        p["trading_date"] for p in missing["paired_daily"] if p["baseline"] == "A"]
    assert len(selected) == 400


def test_negative_delta_sign():
    rows, inputs = scored()
    rows["probability"] = 1 - rows["probability"]
    rows["score_A"] = -rows["score_A"]
    selected, decisions = ai.selections(rows)
    report = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    assert report["delta_pp"] == pytest.approx(-8)


def test_bootstrap_reproducible_full_calendar_missing_dates():
    calendar = pd.bdate_range("2025-01-02", periods=80)
    values = pd.Series(np.sin(np.arange(40)), index=calendar[::2])
    first = ai.bootstrap(values, calendar)
    assert first == ai.bootstrap(values, calendar)
    assert first["valid_repeats"] == 2000
    assert first["seed"] == 37
    assert first["block_sessions"] == 20
    assert ai.bootstrap(values.iloc[:0], calendar)["status"] == "INSUFFICIENT_DATA"


def test_empty_candidate_evaluation():
    inputs = fixture()
    rows = labelled(inputs).iloc[:0].assign(probability=pd.Series(dtype=float))
    selected, decisions = ai.selections(rows)
    report = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    assert report["delta_pp"] is None
    assert report["zero_candidate_dates"] == 50


def test_selection_counts_cannot_be_changed_after_targets():
    rows, inputs = scored()
    selected, decisions = ai.selections(rows)
    with pytest.raises(ValueError, match="counts"):
        ai.evaluate(selected.iloc[1:], decisions, inputs.calendar, inputs.daily)


def test_isolated_write_no_overwrite_and_operational_scope_guard(tmp_path):
    production = tmp_path / "src" / "signal_engine.py"
    production.parent.mkdir()
    production.write_text("protected")
    frozen = tmp_path / ai.OUTPUT_RELATIVE / "panel.csv.gz"
    frozen.parent.mkdir(parents=True)
    frozen.write_bytes(b"frozen")
    protected = operational_inventory(tmp_path)
    old_hash = ai.file_hash(frozen)
    target = ai.write_new_json(tmp_path, "provenance.json", {"contract": ai.CONTRACT_ID})
    assert target.parent == tmp_path / ai.OUTPUT
    with pytest.raises(FileExistsError):
        ai.write_new_json(tmp_path, "provenance.json", {})
    with pytest.raises(ValueError):
        ai.write_new_json(tmp_path, "..\\fixed574_2026-10-07\\panel.json", {})
    verify_inventory(tmp_path, protected)
    assert ai.file_hash(frozen) == old_hash
    production.write_text("unexpected")
    with pytest.raises(RuntimeError, match="Operational"):
        verify_inventory(tmp_path, protected)


def test_real_frozen_artifact_hashes_only_no_training():
    root = Path(__file__).resolve().parents[1]
    if not (root / ai.OUTPUT_RELATIVE / "panel.csv.gz").exists():
        pytest.skip("Local STEP35 large artifacts not present in checkout")
    before = ai.verify_frozen(root)
    assert before == ai.verify_frozen(root)
    import json
    expected = json.loads((root / ai.AUDIT_OUTPUT / "audit_manifest.json").read_text())
    assert before == expected["frozen_35c_hashes"]


def test_corrupt_frozen_artifact_rejected(tmp_path):
    import json
    source = tmp_path / ai.OUTPUT_RELATIVE
    source.mkdir(parents=True)
    panel = source / "panel.csv.gz"
    panel.write_bytes(b"original")
    (source / "artifact_hashes.json").write_text(json.dumps({"panel.csv.gz": ai.file_hash(panel)}))
    ai.verify_frozen(tmp_path)
    panel.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        ai.verify_frozen(tmp_path)


@pytest.mark.parametrize("change", ["production", "artifact", "outside_output"])
def test_research_scope_detects_mutation(tmp_path, change):
    production = tmp_path / "src" / "signal_engine.py"
    production.parent.mkdir()
    production.write_text("protected")
    artifact = tmp_path / ai.OUTPUT_RELATIVE / "existing.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}")
    with pytest.raises(RuntimeError):
        with ai.research_scope(tmp_path):
            if change == "production":
                production.write_text("changed")
            elif change == "artifact":
                artifact.write_text("changed")
            else:
                (artifact.parent / "unexpected.json").write_text("{}")


def test_research_scope_allows_only_new_contract_outputs(tmp_path):
    with ai.research_scope(tmp_path):
        ai.write_new_json(tmp_path, "probe.json", {"synthetic": True})


def test_serializable_synthetic_pipeline_provenance_report(tmp_path):
    inputs = fixture()
    candidates = labelled(inputs)
    train = candidates.loc[candidates["label_ready"]]
    with pytest.warns(FutureWarning, match="penalty"):
        model = ai.fit_train(train)
    selected, decisions = ai.selections(ai.predict(model, candidates))
    report = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    root = Path(__file__).resolve().parents[1]
    record = ai.provenance(root, inputs, model)
    assert record["model_parameters"] == ai.MODEL_PARAMETERS
    assert record["features"] == list(ai.FEATURES)
    assert record["warnings"]
    ai.write_new_json(tmp_path, "synthetic_provenance.json", record)
    ai.write_new_json(tmp_path, "synthetic_metrics.json", report)
    assert report["selection_coverage"]["AI"]["label_availability"] == 1
    assert report["selection_coverage"]["AI"]["unmatured_or_boundary_selected"] == 20
    assert report["pair_availability"] == 1


def test_zero_is_negative_binary_target_and_single_class_rejected():
    inputs = fixture()
    rows = labelled(inputs)
    assert rows.loc[rows["Z"].eq(0), "y"].eq(0).all()
    single = rows.loc[rows["label_ready"] & rows["y"].eq(0)]
    with pytest.raises(ValueError, match="both binary"):
        ai.fit_train(single)


def test_forged_train_boundary_and_target_rejected():
    train = labelled(fixture()).query("label_ready").copy()
    train.loc[train.index[0], "target_date_10d"] = pd.Timestamp("2025-01-02")
    with pytest.raises(ValueError, match="boundary"):
        ai.fit_train(train)
    train = labelled(fixture()).query("label_ready").copy()
    train.loc[train.index[0], "y"] = 1 - train.loc[train.index[0], "y"]
    with pytest.raises(ValueError, match="conflicts"):
        ai.fit_train(train)


def test_convergence_warning_is_failure_not_deprecation(monkeypatch):
    from sklearn.exceptions import ConvergenceWarning
    import warnings
    train = labelled(fixture()).query("label_ready")
    def fake_fit(self, *_args, **_kwargs):
        warnings.warn("not converged", ConvergenceWarning)
        return self
    monkeypatch.setattr(ai.Pipeline, "fit", fake_fit)
    with pytest.warns(ConvergenceWarning):
        with pytest.raises(RuntimeError, match="converge"):
            ai.fit_train(train)


def test_statistics_half_regime_best5_and_negative_pair():
    rows, inputs = scored(60)
    selected, decisions = ai.selections(rows)
    report = ai.evaluate(selected, decisions, inputs.calendar, inputs.daily)
    assert report["halves"] == {"first": 8.0, "second": 8.0}
    assert report["best5_removed_delta_pp"] == 8
    assert report["bootstrap"]["ci95_pp"] == [8, 8]
    assert report["regimes"][0]["paired_dates"] == 50
    assert report["regimes"][0]["status"] == "OK"


def test_prediction_prefix_and_future_target_invariance():
    rows = labelled(fixture())
    with pytest.warns(FutureWarning, match="penalty"):
        model = ai.fit_train(rows.loc[rows["label_ready"]])
    before = ai.predict(model, rows)
    cutoff = rows["trading_date"].iloc[len(rows) // 2]
    prefix = rows.loc[rows["trading_date"].le(cutoff)].copy()
    prefix["future_return_10d"] = 999
    prefix["Z"] = np.nan
    prefix["label_ready"] = False
    prefix["y"] = pd.NA
    after = ai.predict(model, prefix)
    np.testing.assert_array_equal(before.loc[before["trading_date"].le(cutoff), "probability"],
                                  after["probability"])
    mutated = rows.copy()
    mutated.loc[mutated["trading_date"].gt(cutoff), list(ai.FEATURES)] += 100
    changed = ai.predict(model, mutated)
    np.testing.assert_array_equal(before.loc[before["trading_date"].le(cutoff), "probability"],
                                  changed.loc[changed["trading_date"].le(cutoff), "probability"])


def test_alphanumeric_universe_ticker_uses_existing_pattern():
    inputs = fixture()
    inputs.panel["ticker"] = inputs.panel["ticker"].replace({"000000": "0007C0"})
    inputs.events["ticker"] = inputs.events["ticker"].replace({"000000": "0007C0"})
    assert ai.build_candidates(inputs)["ticker"].eq("0007C0").any()

"""V03-LR-RANK-10D-v1 infrastructure; no automatic training or operational entry point."""

from __future__ import annotations

import json
import math
import platform
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from collections.abc import Generator

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.expanded_shadow_universe import TICKER_PATTERN, load_expanded_universe
from src.research.pipeline import (
    OUTPUT_RELATIVE, file_hash, json_bytes, operational_inventory, verify_inventory,
)
from src.research.regime_audit import AUDIT_OUTPUT, daily_regimes, verify_frozen

CONTRACT_ID = "V03-LR-RANK-10D-v1"
FEATURES = (
    "return_20d_pct", "rsi", "volume_ratio_5d_20d", "volatility_20d_pct",
    "positive_20d_pct",
)
KEY = ["trading_date", "ticker"]
STRATEGIES = ("A", "B", "C")
METHODS = ("AI", *STRATEGIES)
MODEL_PARAMETERS = {
    "penalty": "l2", "C": 1.0, "solver": "lbfgs", "tol": 1e-6,
    "max_iter": 2000, "class_weight": None,
}
SPLITS = {"2024": "train", "2025": "validation", "2026": "retrospective_diagnostic"}
SEED = 37
BLOCK = 20
REPEATS = 2000
OUTPUT = Path("output") / "research" / CONTRACT_ID


@contextmanager
def research_scope(root: Path) -> Generator[None, None, None]:
    """Detect protected changes, including on failure; never roll back shared files."""
    root = root.resolve()
    protected = operational_inventory(root)
    research = root / "output" / "research"
    before = {path: file_hash(path) for path in research.rglob("*") if path.is_file()}
    try:
        yield
    finally:
        verify_inventory(root, protected)
        after = {path: file_hash(path) for path in research.rglob("*") if path.is_file()}
        if any(after.get(path) != digest for path, digest in before.items()):
            raise RuntimeError("Existing Research artifact changed")
        if any(path.parent != root / OUTPUT for path in after.keys() - before.keys()):
            raise RuntimeError("New Research artifact outside contract output")


def unique(frame: pd.DataFrame, keys: list[str], label: str) -> None:
    if frame[keys].isna().any().any() or frame.duplicated(keys).any():
        raise ValueError(f"Null or duplicate {label} key")
    if "ticker" in keys and not frame["ticker"].str.fullmatch(TICKER_PATTERN).all():
        raise ValueError(f"Invalid {label} ticker")


def calendar_checked(calendar: pd.DatetimeIndex) -> None:
    if calendar.empty or calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("Calendar must be nonempty, unique and ascending")
    if calendar.tz is not None or not calendar.equals(calendar.normalize()):
        raise ValueError("Calendar must contain timezone-free daily sessions")


@dataclass(frozen=True)
class Inputs:
    panel: pd.DataFrame
    events: pd.DataFrame
    daily: pd.DataFrame
    basket: pd.DataFrame
    calendar: pd.DatetimeIndex
    provenance: dict[str, object]


def load_inputs(root: Path) -> Inputs:
    """Read and validate frozen inputs only; never fit, rank or write artifacts."""
    root = root.resolve()
    protected = operational_inventory(root)
    hashes = verify_frozen(root)
    source = root / OUTPUT_RELATIVE
    audit_paths = [AUDIT_OUTPUT / name for name in (
        "audit_manifest.json", "daily_regimes.csv", "basket_daily.csv",
    )]
    audit_hashes = {str(path): file_hash(root / path) for path in audit_paths}
    manifest = json.loads((source / "input_freeze_manifest.json").read_text())
    audit = json.loads((root / AUDIT_OUTPUT / "audit_manifest.json").read_text())
    if hashes != audit["frozen_35c_hashes"]:
        raise ValueError("STEP35D frozen input provenance mismatch")
    if file_hash(root / "src" / "research" / "regime_audit.py") != audit["audit_source_sha256"]:
        raise ValueError("Frozen Regime audit source changed")
    universe_path = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    universe = load_expanded_universe(universe_path)
    if universe.sha256 != manifest["universe_hash"] or universe.row_count != 574:
        raise ValueError("Frozen Universe mismatch")
    for name, expected in manifest["source_code_hashes"].items():
        if file_hash(root / name) != expected:
            raise ValueError(f"Frozen STEP35 source changed: {name}")
    for entry in manifest["input_files"]:
        if entry["status"] != "AVAILABLE" or file_hash(root / entry["path"]) != entry["sha256"]:
            raise ValueError(f"Frozen raw input changed/missing: {entry['ticker']}")
    panel = pd.read_csv(source / "panel.csv.gz", dtype={"ticker": str, "split": str},
                        parse_dates=["trading_date", "target_date_10d"])
    events = pd.read_csv(source / "events.csv.gz", dtype={"ticker": str, "split": str},
                         parse_dates=["trading_date"])
    daily = pd.read_csv(root / AUDIT_OUTPUT / "daily_regimes.csv", dtype={"split": str},
                        parse_dates=["trading_date"])
    basket = pd.read_csv(root / AUDIT_OUTPUT / "basket_daily.csv", dtype={"split": str},
                         parse_dates=["trading_date"])
    calendar = pd.DatetimeIndex(pd.to_datetime(manifest["calendar"]))
    calendar_checked(calendar)
    unique(panel, KEY, "Panel")
    unique(daily, ["trading_date"], "Regime")
    unique(basket, ["trading_date"], "Basket")
    if len(panel) != len(calendar) * 574 or set(panel["ticker"]) != {
        stock.ticker for stock in universe.tickers
    } or not panel["trading_date"].isin(calendar).all():
        raise ValueError("Panel does not match frozen Universe/calendar grid")
    if not pd.DatetimeIndex(daily["trading_date"]).equals(calendar):
        raise ValueError("Regime calendar mismatch")
    expected_daily = daily_regimes(panel)
    pd.testing.assert_frame_equal(daily, expected_daily, check_dtype=False, rtol=1e-12, atol=1e-12)
    eligible = panel.loc[panel["evaluation_ready_10d"]]
    expected_basket = eligible.groupby("trading_date")["future_return_10d"].agg(["mean", "count"])
    expected_values = basket["trading_date"].map(expected_basket["mean"])
    expected_counts = basket["trading_date"].map(expected_basket["count"]).fillna(0).astype(int)
    np.testing.assert_allclose(basket["basket_return_10d"], expected_values,
                               rtol=1e-12, atol=1e-12, equal_nan=True)
    np.testing.assert_array_equal(basket["basket_count_10d"], expected_counts)
    if not pd.DatetimeIndex(basket["trading_date"]).equals(calendar):
        raise ValueError("Basket calendar mismatch")
    all_hashes = {str(OUTPUT_RELATIVE / name): value for name, value in hashes.items()}
    for path in audit_paths:
        if file_hash(root / path) != audit_hashes[str(path)]:
            raise RuntimeError("Regime/Basket inputs changed during loading")
    all_hashes.update(audit_hashes)
    if verify_frozen(root) != hashes:
        raise RuntimeError("Frozen inputs changed during loading")
    verify_inventory(root, protected)
    return Inputs(panel, events, daily, basket, calendar, {
        "input_hashes": all_hashes, "raw_input_hashes": {
            entry["path"]: entry["sha256"] for entry in manifest["input_files"]
        }, "step35_source_hashes": manifest["source_code_hashes"],
        "universe_hash": universe.sha256,
        "historical_pit": "UNVERIFIED", "basket": "Fixed-574; not official index excess",
    })


def build_candidates(inputs: Inputs) -> pd.DataFrame:
    panel, events, daily = inputs.panel, inputs.events, inputs.daily
    unique(panel, KEY, "Panel")
    unique(daily, ["trading_date"], "Regime")
    crossings = events.loc[events["cohort"].eq("crossing")]
    unique(crossings, [*KEY, "strategy"], "crossing")
    if not crossings["strategy"].isin(STRATEGIES).all():
        raise ValueError("Unknown crossing strategy")
    expected = {
        (row.trading_date, row.ticker, strategy)
        for strategy in STRATEGIES
        for row in panel.loc[panel[f"signal_{strategy}"]].itertuples()
    }
    actual = set(crossings[[*KEY, "strategy"]].itertuples(index=False, name=None))
    if actual != expected:
        raise ValueError("Crossing events do not match Panel signals")
    crossing_heat = crossings.merge(panel[[*KEY, "overheated"]], on=KEY,
                                     validate="many_to_one", suffixes=("", "_panel"))
    if not crossing_heat["overheated"].eq(crossing_heat["overheated_panel"]).all():
        raise ValueError("Crossing heat conflicts with Panel")
    crossing_scores = crossings.merge(
        panel[[*KEY, *[f"score_{s}" for s in STRATEGIES]]], on=KEY, validate="many_to_one",
    )
    for strategy in STRATEGIES:
        rows = crossing_scores.loc[crossing_scores["strategy"].eq(strategy)]
        if not np.allclose(rows["score"], rows[f"score_{strategy}"], rtol=0, atol=0):
            raise ValueError("Crossing score conflicts with Panel")
    chosen = crossings.loc[~crossings["overheated"]]
    members = chosen.assign(member=True).pivot(index=KEY, columns="strategy", values="member")
    members = members.reindex(columns=list(STRATEGIES)).fillna(False).astype(bool)
    members.columns = [f"member_{s}" for s in STRATEGIES]
    members = members.reset_index()
    columns = [
        *KEY, *FEATURES[:-1], *[f"score_{s}" for s in STRATEGIES],
        "future_return_10d", "target_date_10d", "target_status_10d",
        "evaluation_ready_10d", "feature_ready",
    ]
    result = members.merge(panel[columns], on=KEY, how="left", validate="one_to_one", indicator=True)
    if not result["_merge"].eq("both").all() or not result["feature_ready"].all():
        raise ValueError("Candidate missing/invalid Panel row")
    result = result.drop(columns="_merge").merge(
        daily[["trading_date", "positive_20d_pct", "direction", "volatility_regime"]],
        on="trading_date", how="left", validate="many_to_one",
    )
    unique(result, KEY, "candidate")
    feature_matrix(result)
    if not np.isfinite(result[[f"score_{s}" for s in STRATEGIES]].to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite Rule score")
    return result.sort_values(KEY).reset_index(drop=True)


def feature_matrix(frame: pd.DataFrame, columns: tuple[str, ...] = FEATURES) -> pd.DataFrame:
    if columns != FEATURES:
        raise ValueError("Feature allowlist must match exactly; extra/future/target/evaluation inputs forbidden")
    values = frame.loc[:, list(columns)].astype(float)
    if not np.isfinite(values.to_numpy()).all():
        raise ValueError("Missing or nonfinite allowed Feature")
    return values


def attach_labels(candidates: pd.DataFrame, basket: pd.DataFrame,
                  calendar: pd.DatetimeIndex, available_through: pd.Timestamp) -> pd.DataFrame:
    unique(candidates, KEY, "candidate")
    unique(basket, ["trading_date"], "Basket")
    calendar_checked(calendar)
    if not candidates["trading_date"].isin(calendar).all():
        raise ValueError("Candidate date absent from calendar")
    exits = pd.Series(calendar, index=calendar).shift(-10)
    expected = candidates["trading_date"].map(exits)
    if not (candidates["target_date_10d"].eq(expected) |
            (candidates["target_date_10d"].isna() & expected.isna())).all():
        raise ValueError("Target is not exactly ten calendar sessions ahead")
    if available_through not in calendar:
        raise ValueError("available_through must be an observed calendar session")
    result = candidates.merge(basket[["trading_date", "basket_return_10d"]],
                              on="trading_date", how="left", validate="many_to_one")
    result["split_role"] = result["trading_date"].dt.strftime("%Y").map(SPLITS)
    if result["split_role"].isna().any():
        raise ValueError("Only frozen 2024/2025/2026 retrospective splits authorized")
    result["label_ready"] = (
        result["target_status_10d"].eq("AVAILABLE") & result["evaluation_ready_10d"]
        & expected.notna().to_numpy() & result["target_date_10d"].le(available_through)
        & result["target_date_10d"].dt.year.eq(result["trading_date"].dt.year)
        & np.isfinite(result["future_return_10d"]) & np.isfinite(result["basket_return_10d"])
    )
    result["Z"] = (result["future_return_10d"] - result["basket_return_10d"]).where(result["label_ready"])
    result["y"] = result["Z"].gt(0).astype("Int64").where(result["label_ready"])
    return result


def date_weights(rows: pd.DataFrame) -> np.ndarray:
    if rows.empty:
        raise ValueError("Empty Train")
    counts = rows.groupby("trading_date")["ticker"].transform("size")
    weights = 1 / counts.to_numpy(dtype=float)
    return weights / weights.mean()


@dataclass
class FittedModel:
    pipeline: Pipeline
    warning_records: list[str]
    train_rows: int
    train_dates: int


def fit_train(rows: pd.DataFrame) -> FittedModel:
    unique(rows, KEY, "training candidate")
    if not rows["split_role"].eq("train").all() or not rows["label_ready"].all():
        raise ValueError("Fit accepts mature, purged 2024 Train only")
    if not rows["trading_date"].dt.year.eq(2024).all():
        raise ValueError("Non-2024 Train date")
    if not rows["target_date_10d"].dt.year.eq(2024).all():
        raise ValueError("Unpurged Train target boundary")
    if rows["y"].isna().any() or set(rows["y"].unique()) != {0, 1}:
        raise ValueError("Train requires both binary classes")
    if not np.isfinite(rows["Z"]).all() or not rows["y"].eq(rows["Z"].gt(0).astype(int)).all():
        raise ValueError("Train binary target conflicts with Z")
    matrix = feature_matrix(rows)
    pipeline = Pipeline([("scaler", StandardScaler()),
                         ("model", LogisticRegression(**MODEL_PARAMETERS))])
    captured: list[str] = []
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        pipeline.fit(matrix, rows["y"].astype(int), model__sample_weight=date_weights(rows))
    for record in records:
        captured.append(f"{record.category.__name__}: {record.message}")
        warnings.warn(str(record.message), record.category, stacklevel=2)
    if any(issubclass(record.category, ConvergenceWarning) for record in records):
        raise RuntimeError("Logistic Regression did not converge")
    return FittedModel(pipeline, captured, len(rows), rows["trading_date"].nunique())


def predict(model: FittedModel, candidates: pd.DataFrame) -> pd.DataFrame:
    unique(candidates, KEY, "candidate")
    result = candidates.copy()
    result["probability"] = model.pipeline.predict_proba(feature_matrix(result))[:, 1]
    if not np.isfinite(result["probability"]).all() or not result["probability"].between(0, 1).all():
        raise ValueError("Invalid AI probabilities")
    return result


def selections(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    unique(scored, KEY, "candidate")
    output, decisions = [], []
    for date, rows in scored.groupby("trading_date", sort=True):
        n = len(rows)
        k = math.ceil(n * 0.20) if n >= 5 else 0
        decisions.append({"trading_date": date, "candidate_count": n, "k": k,
                          "status": "SELECTED" if k else "INSUFFICIENT_CANDIDATES"})
        if not k:
            continue
        for method in METHODS:
            column = "probability" if method == "AI" else f"score_{method}"
            if not np.isfinite(rows[column].to_numpy(dtype=float)).all():
                raise ValueError(f"Invalid ranking value: {column}")
            selected = rows.sort_values([column, "ticker"], ascending=[False, True],
                                        kind="stable").head(k).copy()
            selected["method"] = method
            output.append(selected)
    selected_frame = pd.concat(output, ignore_index=True) if output else scored.iloc[:0].copy().assign(
        method=pd.Series(dtype=str))
    return selected_frame, pd.DataFrame(decisions, columns=["trading_date", "candidate_count", "k", "status"])


def bootstrap(differences: pd.Series, calendar: pd.DatetimeIndex) -> dict[str, object]:
    calendar_checked(calendar)
    if differences.index.has_duplicates or not differences.index.isin(calendar).all():
        raise ValueError("Invalid paired date index")
    if np.isinf(differences.to_numpy(dtype=float)).any():
        raise ValueError("Infinite paired difference")
    values = differences.reindex(calendar).to_numpy(dtype=float)
    if len(calendar) < BLOCK or not np.isfinite(values).any():
        return {"status": "INSUFFICIENT_DATA", "ci95_pp": None, "valid_repeats": 0}
    rng = np.random.default_rng(SEED)
    estimates = []
    for _ in range(REPEATS):
        starts = rng.integers(0, len(calendar) - BLOCK + 1, size=math.ceil(len(calendar) / BLOCK))
        positions = (starts[:, None] + np.arange(BLOCK)).ravel()[:len(calendar)]
        sampled = values[positions]
        finite = sampled[np.isfinite(sampled)]
        if len(finite):
            estimates.append(float(finite.mean()))
    if len(estimates) != REPEATS:
        return {"status": "INSUFFICIENT_BOOTSTRAP_COVERAGE", "ci95_pp": None,
                "valid_repeats": len(estimates)}
    return {"status": "OK", "ci95_pp": np.quantile(estimates, [0.025, 0.975]).tolist(),
            "valid_repeats": REPEATS, "seed": SEED, "block_sessions": BLOCK}


def evaluate(selected: pd.DataFrame, decisions: pd.DataFrame,
             calendar: pd.DatetimeIndex, daily: pd.DataFrame) -> dict[str, object]:
    """Evaluate one split; missing selections are never replaced or partially averaged."""
    calendar_checked(calendar)
    unique(decisions, ["trading_date"], "decision")
    unique(selected, [*KEY, "method"], "selection")
    unique(daily, ["trading_date"], "Regime")
    if not decisions["trading_date"].isin(calendar).all() or not selected["trading_date"].isin(calendar).all():
        raise ValueError("Evaluation must use the split's calendar")
    if len(set(calendar.year)) != 1:
        raise ValueError("Evaluate each retrospective year separately")
    if not selected["method"].isin(METHODS).all():
        raise ValueError("Unknown selection method")
    positions = pd.Series(np.arange(len(calendar)), index=calendar)
    mature_dates = decisions.loc[
        decisions["k"].gt(0) & decisions["trading_date"].map(positions).lt(len(calendar) - 10),
        "trading_date",
    ]
    if selected.loc[~selected["trading_date"].isin(mature_dates), "label_ready"].any():
        raise ValueError("Label outside period maturity boundary")
    daily_metrics = []
    coverage = {}
    decision_counts = decisions.set_index("trading_date")["k"]
    for method in METHODS:
        rows = selected.loc[selected["method"].eq(method)]
        counts = rows.groupby("trading_date").size().reindex(decision_counts.index, fill_value=0)
        if not counts.eq(decision_counts).all():
            raise ValueError("Selection counts must equal pre-target decision counts")
        finite = rows["label_ready"] & np.isfinite(rows["Z"])
        mature_rows = rows.loc[rows["trading_date"].isin(mature_dates)]
        mature_finite = mature_rows["label_ready"] & np.isfinite(mature_rows["Z"])
        coverage[method] = {
            "selected": len(rows), "labelled": int(finite.sum()),
            "mature_selected": len(mature_rows), "mature_labelled": int(mature_finite.sum()),
            "label_availability": float(mature_finite.mean()) if len(mature_rows) else None,
            "unmatured_or_boundary_selected": len(rows) - len(mature_rows),
            "missing_label_dates": sorted(
                rows.loc[~finite, "trading_date"].dt.strftime("%Y-%m-%d").unique().tolist()),
        }
        for date, group in rows.groupby("trading_date"):
            complete = bool(group["label_ready"].all() and np.isfinite(group["Z"]).all())
            daily_metrics.append({"trading_date": date, "method": method, "complete": complete,
                                  "mean_Z": group["Z"].mean() if complete else np.nan,
                                  "win": group["Z"].gt(0).mean() if complete else np.nan})
    pair_records = []
    metrics = pd.DataFrame(daily_metrics, columns=["trading_date", "method", "complete", "mean_Z", "win"])
    for date, groups in metrics.groupby("trading_date"):
        by_method = groups.set_index("method")
        for baseline in STRATEGIES:
            if bool(by_method.loc["AI", "complete"]) and bool(by_method.loc[baseline, "complete"]):
                pair_records.append({
                    "trading_date": date, "baseline": baseline,
                    "delta_t": float(by_method.loc["AI", "mean_Z"] - by_method.loc[baseline, "mean_Z"]),
                    "win_delta": float(by_method.loc["AI", "win"] - by_method.loc[baseline, "win"]),
                })
    pairs = pd.DataFrame(pair_records, columns=["trading_date", "baseline", "delta_t", "win_delta"])
    primary = pairs.loc[pairs["baseline"].eq("A")].set_index("trading_date")
    difference = primary["delta_t"]
    midpoint = calendar[len(calendar) // 2]
    halves = {name: float(values.mean()) if len(values) else None for name, values in (
        ("first", difference.loc[difference.index < midpoint]),
        ("second", difference.loc[difference.index >= midpoint]),
    )}
    removed = difference.sort_values(ascending=False, kind="stable").iloc[5:]
    regimes = primary.reset_index().merge(
        daily[["trading_date", "direction", "volatility_regime"]], on="trading_date",
        how="left", validate="one_to_one",
    )
    if regimes[["direction", "volatility_regime"]].isna().any().any():
        raise ValueError("Paired date missing Regime")
    regime_records = []
    for dimension in ("direction", "volatility_regime"):
        for label in sorted(daily[dimension].unique()):
            values = regimes.loc[regimes[dimension].eq(label), "delta_t"]
            regime_records.append({"dimension": dimension, "regime": label, "paired_dates": len(values),
                                   "status": "OK" if len(values) >= 30 else "SPARSE",
                                   "delta_pp": float(values.mean()) if len(values) else None})
    primary_mature = difference.index.intersection(pd.DatetimeIndex(mature_dates))
    pairs["trading_date"] = pd.to_datetime(pairs["trading_date"]).dt.strftime("%Y-%m-%d")
    return {
        "primary_metric": "mean_t(mean_Z_AI - mean_Z_score_A)", "unit": "pp",
        "paired_dates": len(difference), "delta_pp": float(difference.mean()) if len(difference) else None,
        "median_delta_pp": float(difference.median()) if len(difference) else None,
        "relative_win_rate_difference": float(primary["win_delta"].mean()) if len(primary) else None,
        "secondary_baseline_delta_pp": {
            s: float(pairs.loc[pairs["baseline"].eq(s), "delta_t"].mean())
            if pairs["baseline"].eq(s).any() else None for s in ("B", "C")
        },
        "bootstrap": bootstrap(difference, calendar), "halves": halves,
        "best5_removed_delta_pp": float(removed.mean()) if len(removed) else None,
        "regimes": regime_records, "selection_coverage": coverage,
        "mature_decision_dates": len(mature_dates),
        "pair_availability": len(primary_mature) / len(mature_dates) if len(mature_dates) else None,
        "insufficient_candidate_dates": int(decisions["k"].eq(0).sum()),
        "zero_candidate_dates": len(calendar.difference(pd.DatetimeIndex(decisions["trading_date"]))),
        "paired_daily": pairs.to_dict("records"),
        "no_ai_superiority_decision": True,
    }


def provenance(root: Path, inputs: Inputs, model: FittedModel | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "contract_id": CONTRACT_ID, **inputs.provenance, "features": list(FEATURES),
        "splits": SPLITS, "horizon_sessions": 10, "target": "y=1[raw_10d-basket_10d>0]",
        "model_parameters": MODEL_PARAMETERS, "scaler": "Train-only StandardScaler",
        "sample_weight": "inverse Train date count; mean normalized to 1",
        "selection": {"fraction": 0.20, "rounding": "ceil", "minimum_candidates": 5,
                      "tie_break": "ticker ascending"},
        "bootstrap": {"seed": SEED, "block": BLOCK, "repeats": REPEATS},
        "python_version": platform.python_version(),
        "source_hash": file_hash(Path(__file__)),
        "research_requirements_hash": file_hash(root / "requirements-research.txt"),
        "runtime": {name: version(name) for name in (
            "numpy", "pandas", "scikit-learn", "scipy", "joblib", "threadpoolctl", "cloudpickle",
        )},
        "no_unseen_test": True,
        "purge": "exact calendar +10; exit within entry year and <=available_through",
        "primary_metric": "mean_t(mean_Z_AI - mean_Z_score_A); pp",
    }
    if model is not None:
        result.update({"warnings": model.warning_records, "train_rows": model.train_rows,
                       "train_dates": model.train_dates,
                       "scaler_mean": model.pipeline.named_steps["scaler"].mean_.tolist(),
                       "scaler_scale": model.pipeline.named_steps["scaler"].scale_.tolist(),
                       "model_coef": model.pipeline.named_steps["model"].coef_.tolist(),
                       "model_intercept": model.pipeline.named_steps["model"].intercept_.tolist()})
    return result


def write_new_json(root: Path, name: str, value: object) -> Path:
    expected = root.resolve() / OUTPUT
    target = (expected / name).resolve()
    if expected.resolve() != expected or target.parent != expected or target.suffix != ".json":
        raise ValueError("Output must be a direct JSON child of the isolated contract directory")
    if target.exists():
        raise FileExistsError(f"Research output overwrite forbidden: {target}")
    payload = json_bytes(value)
    expected.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(payload)
    return target

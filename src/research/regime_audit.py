"""STEP 35-D: descriptive regime audit of immutable STEP 35-C artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.research.panel import HORIZONS, STRATEGIES
from src.research.pipeline import (
    OUTPUT_RELATIVE, csv_bytes, file_hash, json_bytes, operational_inventory, verify_inventory,
)
from src.research.report import metrics

AUDIT_OUTPUT = Path("output") / "research" / "fixed574_regime_audit_35d"
PANEL_COLUMNS = [
    "ticker", "trading_date", "split", "tradable_bar", "feature_ready", "close", "ma5", "ma20",
    "return_1d_pct", "return_5d_pct", "return_20d_pct", "volume_ratio", "volume_ratio_5d_20d",
    *[f"future_return_{h}d" for h in HORIZONS],
    *[f"evaluation_ready_{h}d" for h in HORIZONS],
]
REGIME_FEATURES = [
    "return_1d_mean_pct", "return_1d_median_pct", "return_5d_mean_pct", "return_5d_median_pct",
    "return_20d_mean_pct", "return_20d_median_pct", "positive_1d_pct", "positive_5d_pct",
    "positive_20d_pct", "close_above_ma20_pct", "ma5_above_ma20_pct",
    "cross_sectional_volatility_1d_pct", "volume_above_ma20_pct", "volume_ma5_above_ma20_pct",
]
RULES = {
    "direction": "positive_20d_pct <40 BEAR; >60 BULL; otherwise NEUTRAL; missing UNKNOWN",
    "volatility": "1D cross-sectional std(ddof=1) > lagged expanding median HIGH; <= LOW; 60 past days required",
    "coverage": "feature-specific finite tradable bars; denominator and percentage of fixed 574 reported",
    "primary": "unchanged non-overheated STEP35C crossings; unchanged per-horizon evaluation flags",
    "bootstrap": "2000 paired 20-session moving blocks over all calendar dates; seed 35; date-weighted cell mean CI",
    "no_trade": "descriptive comparison with zero gross raw return, not an implemented filter or executable portfolio",
}


def daily_regimes(panel: pd.DataFrame) -> pd.DataFrame:
    """No targets enter this calculation; every threshold uses current/past inputs."""
    rows: list[dict[str, object]] = []
    for date, frame in panel.groupby("trading_date", sort=True):
        tradable = frame.loc[frame["tradable_bar"]]
        row: dict[str, object] = {
            "trading_date": date, "split": str(date.year), "universe_rows": len(frame),
            "tradable_count": len(tradable), "feature_ready_count": int(frame["feature_ready"].sum()),
        }
        for horizon in (1, 5, 20):
            values = tradable[f"return_{horizon}d_pct"].dropna()
            row.update({
                f"return_{horizon}d_count": len(values),
                f"return_{horizon}d_coverage_pct": len(values) / len(frame) * 100,
                f"return_{horizon}d_mean_pct": float(values.mean()) if len(values) else None,
                f"return_{horizon}d_median_pct": float(values.median()) if len(values) else None,
                f"positive_{horizon}d_pct": float(values.gt(0).mean() * 100) if len(values) else None,
            })
        values = tradable["return_1d_pct"].dropna()
        row["cross_sectional_volatility_1d_pct"] = float(values.std(ddof=1)) if len(values) > 1 else None
        for name, columns, left, right in (
            ("close_above_ma20", ("close", "ma20"), "close", "ma20"),
            ("ma5_above_ma20", ("ma5", "ma20"), "ma5", "ma20"),
        ):
            eligible = tradable.dropna(subset=list(columns))
            row[f"{name}_count"] = len(eligible)
            row[f"{name}_pct"] = float(eligible[left].gt(eligible[right]).mean() * 100) if len(eligible) else None
        for name, column in (
            ("volume_above_ma20", "volume_ratio"),
            ("volume_ma5_above_ma20", "volume_ratio_5d_20d"),
        ):
            values = tradable[column].dropna()
            row[f"{name}_count"] = len(values)
            row[f"{name}_pct"] = float(values.gt(1).mean() * 100) if len(values) else None
        rows.append(row)
    daily = pd.DataFrame(rows)
    breadth = daily["positive_20d_pct"]
    daily["direction"] = np.select(
        [breadth.isna(), breadth.lt(40), breadth.gt(60)],
        ["UNKNOWN", "BEAR", "BULL"], default="NEUTRAL",
    )
    volatility = daily["cross_sectional_volatility_1d_pct"]
    baseline = volatility.shift(1).expanding(min_periods=60).median()
    daily["volatility_past_median_pct"] = baseline
    daily["volatility_regime"] = np.select(
        [baseline.isna() | volatility.isna(), volatility.gt(baseline)],
        ["UNKNOWN", "HIGH"], default="LOW",
    )
    daily["joint_regime"] = daily["direction"] + "_" + daily["volatility_regime"]
    return daily


def year_market_state(daily: pd.DataFrame) -> pd.DataFrame:
    results: list[dict[str, object]] = []
    for year, rows in daily.groupby("split", sort=True):
        record: dict[str, object] = {"year": year, "sessions": len(rows)}
        for feature in REGIME_FEATURES:
            record[f"{feature}_mean"] = rows[feature].mean()
            record[f"{feature}_median"] = rows[feature].median()
        record["mean_return20_coverage_pct"] = rows["return_20d_coverage_pct"].mean()
        for column in ("direction", "volatility_regime"):
            for label in sorted(daily[column].unique()):
                record[f"{column}_{label}_days"] = int(rows[column].eq(label).sum())
        results.append(record)
    return pd.DataFrame(results)


def join_events(events: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    crossing = events.loc[events["cohort"].eq("crossing")].copy()
    if crossing.duplicated(["strategy", "ticker", "trading_date"]).any():
        raise ValueError("Duplicate frozen crossing event")
    joined = crossing.merge(daily.drop(columns="split"), on="trading_date", how="left", validate="many_to_one")
    if joined["direction"].isna().any():
        raise ValueError("Event date missing from regime calendar")
    return joined


def regime_comparisons(events: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for heat in ("non_overheated", "overheated", "all"):
        heated = events if heat == "all" else events.loc[events["overheated"].eq(heat == "overheated")]
        for strategy in STRATEGIES:
            selected = heated.loc[heated["strategy"].eq(strategy)]
            for year in ("ALL", "2024", "2025", "2026"):
                subset = selected if year == "ALL" else selected.loc[selected["split"].eq(year)]
                calendar = daily if year == "ALL" else daily.loc[daily["split"].eq(year)]
                for dimension in ("direction", "volatility_regime", "joint_regime"):
                    for label in sorted(daily[dimension].unique()):
                        rows = subset.loc[subset[dimension].eq(label)]
                        for horizon in HORIZONS:
                            records.append({
                                "heat": heat, "strategy": strategy, "year": year,
                                "dimension": dimension, "regime": label, "horizon": horizon,
                                "calendar_days": int(calendar[dimension].eq(label).sum()),
                                **metrics(rows, horizon),
                            })
    return pd.DataFrame(records)


def basket_diagnostics(panel: pd.DataFrame, daily: pd.DataFrame, events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    basket = daily[["trading_date", "split", "direction", "volatility_regime"]].copy()
    for horizon in HORIZONS:
        eligible = panel.loc[panel[f"evaluation_ready_{horizon}d"]]
        values = eligible.groupby("trading_date")[f"future_return_{horizon}d"].agg(["mean", "count"])
        basket[f"basket_return_{horizon}d"] = basket["trading_date"].map(values["mean"])
        basket[f"basket_count_{horizon}d"] = basket["trading_date"].map(values["count"]).fillna(0).astype(int)
    result = events.merge(
        basket[["trading_date", *[f"basket_return_{h}d" for h in HORIZONS]]],
        on="trading_date", how="left", validate="many_to_one",
    )
    records = []
    for strategy in STRATEGIES:
        for year in ("ALL", "2024", "2025", "2026"):
            subset = result.loc[result["strategy"].eq(strategy) & ~result["overheated"]]
            if year != "ALL":
                subset = subset.loc[subset["split"].eq(year)]
            for dimension in ("direction", "volatility_regime"):
                for label in sorted(daily[dimension].unique()):
                    rows = subset.loc[subset[dimension].eq(label)]
                    for horizon in HORIZONS:
                        evaluated = rows.loc[rows[f"evaluation_ready_{horizon}d"]].copy()
                        evaluated["difference"] = evaluated[f"future_return_{horizon}d"] - evaluated[f"basket_return_{horizon}d"]
                        date_diff = evaluated.groupby("trading_date")["difference"].mean()
                        records.append({
                            "strategy": strategy, "year": year, "dimension": dimension,
                            "regime": label, "horizon": horizon, "evaluated_count": len(evaluated),
                            "date_weighted_basket_difference_pct": date_diff.mean(),
                        })
    return basket, pd.DataFrame(records)


def continuous_relationships(events: pd.DataFrame) -> pd.DataFrame:
    records = []
    for strategy in STRATEGIES:
        for year in ("ALL", "2024", "2025", "2026"):
            rows = events.loc[events["strategy"].eq(strategy) & ~events["overheated"]]
            if year != "ALL":
                rows = rows.loc[rows["split"].eq(year)]
            for horizon in HORIZONS:
                evaluated = rows.loc[rows[f"evaluation_ready_{horizon}d"]]
                grouped = evaluated.groupby("trading_date")
                target = grouped[f"future_return_{horizon}d"].mean()
                for feature in REGIME_FEATURES:
                    values = grouped[feature].first()
                    paired = pd.concat([values.rename("feature"), target.rename("target")], axis=1).dropna()
                    correlation = None
                    if len(paired) >= 3 and paired["feature"].nunique() > 1 and paired["target"].nunique() > 1:
                        correlation = paired["feature"].rank().corr(paired["target"].rank())
                    records.append({
                        "strategy": strategy, "year": year, "horizon": horizon, "feature": feature,
                        "signal_dates": len(paired), "spearman_date_mean": correlation,
                    })
    return pd.DataFrame(records)


def robustness(events: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    records = []
    midpoint = daily["trading_date"].iloc[len(daily) // 2]
    for strategy in STRATEGIES:
        subset = events.loc[events["strategy"].eq(strategy) & ~events["overheated"]]
        for dimension in ("direction", "volatility_regime"):
            for label in sorted(daily[dimension].unique()):
                rows = subset.loc[subset[dimension].eq(label)]
                for horizon in HORIZONS:
                    eligible = rows.loc[rows[f"evaluation_ready_{horizon}d"]]
                    date_returns = eligible.groupby("trading_date")[f"future_return_{horizon}d"].mean()
                    busy = eligible.groupby("trading_date").size().sort_values(ascending=False, kind="stable")
                    best = date_returns.sort_values(ascending=False, kind="stable").head(5).index
                    scenarios = {
                        "BASE": eligible,
                        "REMOVE_BEST_5_DATES": eligible.loc[~eligible["trading_date"].isin(best)],
                        "REMOVE_BUSIEST_5_DATES": eligible.loc[~eligible["trading_date"].isin(busy.head(5).index)],
                        "FIRST_CALENDAR_HALF": eligible.loc[eligible["trading_date"].lt(midpoint)],
                        "SECOND_CALENDAR_HALF": eligible.loc[eligible["trading_date"].ge(midpoint)],
                        **{f"WITHOUT_{year}": eligible.loc[eligible["split"].ne(year)] for year in ("2024", "2025", "2026")},
                    }
                    for scenario, selected in scenarios.items():
                        dates = selected.groupby("trading_date")[f"future_return_{horizon}d"].mean()
                        records.append({
                            "strategy": strategy, "dimension": dimension, "regime": label,
                            "horizon": horizon, "scenario": scenario, "events": len(selected),
                            "signal_dates": len(dates), "mean_return_pct": selected[f"future_return_{horizon}d"].mean(),
                            "date_weighted_mean_return_pct": dates.mean(),
                            "largest_date_event_share": float(busy.max() / busy.sum()) if len(busy) else None,
                            "top5_dates_event_share": float(busy.head(5).sum() / busy.sum()) if len(busy) else None,
                        })
    return pd.DataFrame(records)


def cell_bootstrap(events: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    n = len(daily)
    if n < 20:
        raise ValueError("At least 20 sessions required for block bootstrap")
    rng = np.random.default_rng(35)
    starts = rng.integers(0, n - 19, size=(2000, math.ceil(n / 20)))
    draws = (starts[:, :, None] + np.arange(20)).reshape(2000, -1)[:, :n]
    calendar = pd.DatetimeIndex(daily["trading_date"])
    records = []
    for strategy in STRATEGIES:
        subset = events.loc[events["strategy"].eq(strategy) & ~events["overheated"]]
        for dimension in ("direction", "volatility_regime"):
            for label in sorted(daily[dimension].unique()):
                rows = subset.loc[subset[dimension].eq(label)]
                for horizon in HORIZONS:
                    selected = rows.loc[rows[f"evaluation_ready_{horizon}d"]]
                    returns = selected.groupby("trading_date")[f"future_return_{horizon}d"].mean()
                    values = returns.reindex(calendar).to_numpy()[draws]
                    counts = np.isfinite(values).sum(axis=1)
                    averages = np.divide(np.nansum(values, axis=1), counts, out=np.full(2000, np.nan), where=counts > 0)
                    finite = averages[np.isfinite(averages)]
                    records.append({
                        "strategy": strategy, "dimension": dimension, "regime": label, "horizon": horizon,
                        "signal_dates": len(returns), "valid_replicates": len(finite),
                        "lower_95_pct": float(np.quantile(finite, .025)) if len(finite) else None,
                        "upper_95_pct": float(np.quantile(finite, .975)) if len(finite) else None,
                        "status": "DESCRIPTIVE" if len(returns) >= 40 else "LOW_EFFECTIVE_SAMPLE",
                    })
    return pd.DataFrame(records)


def verify_frozen(root: Path) -> dict[str, str]:
    source = root / OUTPUT_RELATIVE
    hashes = {path.name: file_hash(path) for path in sorted(source.iterdir()) if path.is_file()}
    approved = json.loads((source / "artifact_hashes.json").read_text(encoding="utf-8"))
    for name, digest in approved.items():
        if hashes.get(name) != digest:
            raise RuntimeError(f"STEP35C artifact hash mismatch: {name}")
    return hashes


def table_markdown(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for values in frame.itertuples(index=False, name=None):
        cells = ["n/a" if pd.isna(value) else f"{value:.3f}" if isinstance(value, float) else str(value) for value in values]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_audit(root: Path, name: str, payload: bytes) -> None:
    expected = root.resolve() / AUDIT_OUTPUT
    output = expected.resolve()
    target = (output / name).resolve()
    if output != expected or target.parent != output:
        raise ValueError("Audit output must stay inside its own Research directory")
    output.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise RuntimeError(f"Audit rerun differs: {name}")
    else:
        with target.open("xb") as handle:
            handle.write(payload)


def run_audit(root: Path) -> dict[str, object]:
    root = root.resolve()
    protected = operational_inventory(root)
    frozen = verify_frozen(root)
    source = root / OUTPUT_RELATIVE
    panel = pd.read_csv(source / "panel.csv.gz", usecols=PANEL_COLUMNS, dtype={"ticker": str, "split": str}, parse_dates=["trading_date"])
    events = pd.read_csv(source / "events.csv.gz", dtype={"ticker": str, "split": str}, parse_dates=["trading_date"])
    daily = daily_regimes(panel)
    joined = join_events(events, daily)
    comparisons = regime_comparisons(joined, daily)
    years = year_market_state(daily)
    basket, differences = basket_diagnostics(panel, daily, joined)
    correlations = continuous_relationships(joined)
    sensitivity = robustness(joined, daily)
    intervals = cell_bootstrap(joined, daily)
    primary = comparisons.loc[
        comparisons["heat"].eq("non_overheated") & comparisons["year"].eq("ALL")
        & comparisons["dimension"].isin(["direction", "volatility_regime"])
    ]
    report = [
        "# STEP 35-D: Fixed-574 Market Regime Audit", "",
        "Analysis only: STEP 35-C artifacts and A/B/C rules unchanged. No new strategy, fitting or external fetch.",
        "", "## Predeclared regime rules", *["- " + key + ": " + value for key, value in RULES.items()],
        "", "Feature-specific finite/tradable denominators are explicit; missing values are not counted as down stocks.",
        "Labels use all available contemporaneous members, not only future-target survivors.",
        "", "## Yearly market state", table_markdown(years), "",
        "## Non-overheated crossing comparisons", table_markdown(primary[[
            "dimension", "regime", "strategy", "horizon", "calendar_days", "signal_count",
            "evaluated_count", "evaluated_dates", "mean_return_pct", "median_return_pct",
            "win_rate_pct", "date_weighted_mean_return_pct",
        ]]), "",
        "## Outputs",
        "- daily_regimes.csv: causal contemporaneous features, eligibility counts and fixed labels.",
        "- regime_comparisons.csv: all/2024/2025/2026, direction/volatility/joint, heat separation.",
        "- correlations.csv: date-mean Spearman diagnostics, not fitted coefficients or causal estimates.",
        "- robustness.csv: fixed top5-date removal, chronological halves and leave-one-year-out diagnostics.",
        "- basket_diagnostics.csv: same-date equal-weight Fixed-574 future-return difference; not benchmark excess or PIT correction.",
        "- cell_bootstrap.csv: descriptive 95% date-weighted mean intervals; sparse cells flagged.",
        "", "## Interpretation limits",
        "- Frozen 2026-09-17 universe retains survivorship and selection bias.",
        "- Historical PIT and adjusted-price status remain UNVERIFIED.",
        "- Contemporary breadth includes the signal stock itself; shared market exposure creates association, not causation.",
        "- Daily samples have overlapping forward horizons and nonstationary years; block CI is descriptive.",
        "- Regime-specific zero return comparisons do not establish a deployable No-Trade rule.",
        "- Gross raw returns omit opportunity cost, cash yield, fees and execution timing.",
        "- Many correlated comparisons are exploratory; no significance search, threshold optimization or ML fitting.",
        "- Strong gross returns can reflect the whole basket rather than stock-selection edge.",
        "",
    ]
    manifest = {
        "step": "35-D", "rules": RULES, "frozen_35c_hashes": frozen,
        "audit_source_sha256": file_hash(Path(__file__)),
        "operational_files_verified": len(protected), "status": "READ_ONLY_INPUTS_UNCHANGED",
    }
    artifacts = {
        "audit_manifest.json": json_bytes(manifest),
        "daily_regimes.csv": csv_bytes(daily), "year_market_state.csv": csv_bytes(years),
        "regime_comparisons.csv": csv_bytes(comparisons), "basket_daily.csv": csv_bytes(basket),
        "basket_diagnostics.csv": csv_bytes(differences), "correlations.csv": csv_bytes(correlations),
        "robustness.csv": csv_bytes(sensitivity), "cell_bootstrap.csv": csv_bytes(intervals),
        "report.md": "\n".join(report).encode("utf-8"),
    }
    if verify_frozen(root) != frozen:
        raise RuntimeError("STEP35C inputs changed during audit")
    verify_inventory(root, protected)
    for name, payload in artifacts.items():
        write_audit(root, name, payload)
    if verify_frozen(root) != frozen:
        raise RuntimeError("STEP35C inputs changed during output write")
    verify_inventory(root, protected)
    return {"sessions": len(daily), "frozen_artifacts_verified": len(frozen), "operational_files_verified": len(protected)}


if __name__ == "__main__":
    print(json.dumps(run_audit(Path(__file__).resolve().parents[2]), indent=2))

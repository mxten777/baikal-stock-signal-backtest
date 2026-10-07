"""Descriptive comparisons; no fitting, rule search or benchmark fetch."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.research.panel import HORIZONS, STRATEGIES

BOOTSTRAP_SEED = 35
BOOTSTRAP_REPEATS = 2000
BLOCK_SESSIONS = 20


def strategy_events(panel: pd.DataFrame) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    base = [
        "ticker", "trading_date", "session_index", "market", "split", "evaluation_month",
        "overheated", "evaluation_ready_common_20d",
    ]
    for horizon in HORIZONS:
        base.extend([
            f"future_return_{horizon}d", f"target_status_{horizon}d",
            f"split_purged_{horizon}d", f"evaluation_ready_{horizon}d",
        ])
    for strategy in STRATEGIES:
        for cohort, mask in (
            ("crossing", panel[f"signal_{strategy}"]),
            ("common20", panel[f"signal_{strategy}"] & panel["evaluation_ready_common_20d"]),
            ("cooldown20", panel[f"cooldown_{strategy}"]),
            ("warmup120", panel[f"signal_120_{strategy}"]),
        ):
            rows = panel.loc[mask, base].copy()
            rows["strategy"] = strategy
            rows["cohort"] = cohort
            rows["score"] = panel.loc[mask, f"score_{strategy}"]
            records.append(rows)
        ranked = panel.loc[panel["feature_ready"]].sort_values(
            ["trading_date", f"score_{strategy}", "ticker"], ascending=[True, False, True],
            kind="stable",
        )
        counts = ranked.groupby("trading_date")["ticker"].transform("size")
        positions = ranked.groupby("trading_date").cumcount()
        k = np.ceil(counts * 0.10).astype(int)
        for cohort, mask in (
            ("top10_daily", positions.lt(k)),
            ("bottom10_daily", positions.ge(counts - k)),
        ):
            rows = ranked.loc[mask, base].copy()
            rows["strategy"] = strategy
            rows["cohort"] = cohort
            rows["score"] = ranked.loc[mask, f"score_{strategy}"]
            records.append(rows)
    return pd.concat(records, ignore_index=True).sort_values(
        ["cohort", "strategy", "trading_date", "ticker"], kind="stable",
    ).reset_index(drop=True)


def metrics(rows: pd.DataFrame, horizon: int) -> dict[str, object]:
    values = rows.loc[rows[f"evaluation_ready_{horizon}d"], f"future_return_{horizon}d"]
    evaluated = rows.loc[values.index]
    dates = evaluated.groupby("trading_date")[f"future_return_{horizon}d"].mean()
    daily_win = evaluated.assign(win=values.gt(0).astype(float)).groupby("trading_date")["win"].mean()
    missing_counts = rows[f"target_status_{horizon}d"].value_counts().to_dict()
    result: dict[str, object] = {
        "signal_count": len(rows),
        "evaluated_count": len(values),
        "signal_dates": int(rows["trading_date"].nunique()),
        "evaluated_dates": len(dates),
        "immature_count": int(missing_counts.get("IMMATURE", 0)),
        "missing_path_count": int(missing_counts.get("MISSING_PATH", 0)),
        "invalid_price_count": int(missing_counts.get("INVALID_PRICE", 0)),
        "split_purged_count": int((
            rows[f"split_purged_{horizon}d"] & rows[f"target_status_{horizon}d"].eq("AVAILABLE")
        ).sum()),
        "mean_return_pct": None if values.empty else float(values.mean()),
        "median_return_pct": None if values.empty else float(values.median()),
        "win_rate_pct": None if values.empty else float(values.gt(0).mean() * 100),
        "date_weighted_mean_return_pct": None if dates.empty else float(dates.mean()),
        "date_weighted_win_rate_pct": None if daily_win.empty else float(daily_win.mean() * 100),
        "p10_return_pct": None if values.empty else float(values.quantile(0.10)),
        "p25_return_pct": None if values.empty else float(values.quantile(0.25)),
        "p75_return_pct": None if values.empty else float(values.quantile(0.75)),
        "p90_return_pct": None if values.empty else float(values.quantile(0.90)),
    }
    return result


def comparison_table(events: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    results: list[dict[str, object]] = []
    months = sorted(panel["evaluation_month"].unique())
    for cohort in ("crossing", "common20", "cooldown20", "warmup120", "top10_daily", "bottom10_daily"):
        for strategy in STRATEGIES:
            rows = events.loc[events["cohort"].eq(cohort) & events["strategy"].eq(strategy)]
            for heat in ("all", "non_overheated", "overheated"):
                subset = rows if heat == "all" else rows.loc[rows["overheated"].eq(heat == "overheated")]
                scopes: list[tuple[str, str, pd.DataFrame]] = [("all", "ALL", subset)]
                scopes.extend(("year", year, subset.loc[subset["split"].eq(year)]) for year in ("2024", "2025", "2026"))
                scopes.extend(("market", market, subset.loc[subset["market"].eq(market)]) for market in ("KOSPI", "KOSDAQ"))
                scopes.extend(("month", month, subset.loc[subset["evaluation_month"].eq(month)]) for month in months)
                for scope, value, scoped_rows in scopes:
                    for horizon in HORIZONS:
                        results.append({
                            "cohort": cohort, "strategy": strategy, "heat": heat,
                            "scope": scope, "scope_value": value, "horizon": horizon,
                            **metrics(scoped_rows, horizon),
                        })
    return pd.DataFrame(results)


def score_quantiles(events: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {}
    for strategy in STRATEGIES:
        rows = events.loc[
            events["cohort"].eq("crossing") & events["strategy"].eq(strategy) & ~events["overheated"]
        ].copy()
        if rows.empty:
            result[strategy] = {"status": "NO_SIGNALS", "bins": [], "rows": []}
            continue
        bins, edges = pd.qcut(rows["score"], 5, duplicates="drop", retbins=True)
        groups = []
        for interval, group in rows.groupby(bins, observed=True, sort=True):
            for horizon in HORIZONS:
                groups.append({"bin": str(interval), "horizon": horizon, **metrics(group, horizon)})
        result[strategy] = {
            "status": "DESCRIPTIVE_ONLY_TIES_NOT_SPLIT",
            "requested_bins": 5, "actual_bins": len(edges) - 1,
            "bins": edges.tolist(), "rows": groups,
        }
    return result


def overlap_report(panel: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {}
    for heat in ("all", "non_overheated", "overheated"):
        flags = panel[[f"signal_{s}" for s in STRATEGIES]].copy()
        if heat != "all":
            flags &= panel["overheated"].eq(heat == "overheated").to_numpy()[:, None]
        counts: dict[str, int] = {}
        for pattern in ("100", "010", "001", "110", "101", "011", "111"):
            mask = pd.Series(True, index=panel.index)
            for index, strategy in enumerate(STRATEGIES):
                mask &= flags[f"signal_{strategy}"].eq(pattern[index] == "1")
            counts[pattern] = int(mask.sum())
        pairs = {}
        for left, right in (("A", "B"), ("A", "C"), ("B", "C")):
            a, b = flags[f"signal_{left}"], flags[f"signal_{right}"]
            intersection, union = int((a & b).sum()), int((a | b).sum())
            pairs[f"{left}_{right}"] = {
                "intersection": intersection, "union": union,
                "jaccard": intersection / union if union else None,
            }
        result[heat] = {"exclusive_patterns_ABC": counts, "pairs": pairs}
    return result


def bootstrap_report(
    events: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    *,
    repeats: int = BOOTSTRAP_REPEATS,
) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("Bootstrap repeats must be positive")
    n = len(calendar)
    if n < BLOCK_SESSIONS:
        return {"status": "INSUFFICIENT_CALENDAR", "intervals": [], "paired_differences": []}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    starts = rng.integers(0, n - BLOCK_SESSIONS + 1, size=(repeats, math.ceil(n / BLOCK_SESSIONS)))
    draws = (starts[:, :, None] + np.arange(BLOCK_SESSIONS)).reshape(repeats, -1)[:, :n]
    intervals: list[dict[str, object]] = []
    paired: list[dict[str, object]] = []
    for horizon in HORIZONS:
        samples: dict[str, np.ndarray] = {}
        for strategy in STRATEGIES:
            rows = events.loc[
                events["cohort"].eq("crossing") & events["strategy"].eq(strategy)
                & ~events["overheated"] & events[f"evaluation_ready_{horizon}d"]
            ]
            daily = rows.groupby("trading_date")[f"future_return_{horizon}d"].agg(["sum", "count", "mean"])
            daily = daily.reindex(calendar)
            sums = daily["sum"].fillna(0).to_numpy()[draws].sum(axis=1)
            counts = daily["count"].fillna(0).to_numpy()[draws].sum(axis=1)
            means = np.divide(sums, counts, out=np.full(repeats, np.nan), where=counts > 0)
            date_means = daily["mean"].to_numpy()[draws]
            date_counts = np.isfinite(date_means).sum(axis=1)
            weighted = np.divide(
                np.nansum(date_means, axis=1), date_counts,
                out=np.full(repeats, np.nan), where=date_counts > 0,
            )
            samples[strategy] = means
            for metric, values in (("mean_return_pct", means), ("date_weighted_mean_return_pct", weighted)):
                finite = values[np.isfinite(values)]
                intervals.append({
                    "strategy": strategy, "horizon": horizon, "metric": metric,
                    "lower_95": float(np.quantile(finite, 0.025)) if len(finite) else None,
                    "upper_95": float(np.quantile(finite, 0.975)) if len(finite) else None,
                    "valid_replicates": len(finite), "signal_dates": len(rows.groupby("trading_date")),
                    "status": "OK" if len(rows.groupby("trading_date")) >= 40 and n // 20 >= 20 else "LOW_EFFECTIVE_SAMPLE",
                })
        for left, right in (("A", "B"), ("A", "C"), ("B", "C")):
            difference = samples[left] - samples[right]
            finite = difference[np.isfinite(difference)]
            paired.append({
                "contrast": f"{left}-{right}", "horizon": horizon,
                "lower_95": float(np.quantile(finite, 0.025)) if len(finite) else None,
                "upper_95": float(np.quantile(finite, 0.975)) if len(finite) else None,
                "valid_replicates": len(finite),
            })
    return {
        "status": "DESCRIPTIVE_FIXED_SELECTION",
        "method": "paired 20-session moving-block resampling of entire dates including zero-signal dates",
        "seed": BOOTSTRAP_SEED, "repeats": repeats, "calendar_sessions": n,
        "intervals": intervals, "paired_differences": paired,
    }


def summarize(panel: pd.DataFrame, events: pd.DataFrame, comparisons: pd.DataFrame) -> dict[str, object]:
    calendar = pd.DatetimeIndex(sorted(panel["trading_date"].unique()))
    crossing = events.loc[events["cohort"].eq("crossing")]
    primary = comparisons.loc[
        comparisons["cohort"].eq("crossing") & comparisons["heat"].eq("non_overheated")
        & comparisons["scope"].ne("month")
    ]
    monthly = comparisons.loc[
        comparisons["cohort"].eq("crossing") & comparisons["heat"].eq("non_overheated")
        & comparisons["scope"].eq("month")
    ]
    stability = []
    for (strategy, horizon), rows in monthly.groupby(["strategy", "horizon"], sort=True):
        observed = rows.loc[rows["evaluated_count"].gt(0)]
        stability.append({
            "strategy": strategy, "horizon": int(horizon),
            "months_with_targets": len(observed), "months_without_targets": len(rows) - len(observed),
            "positive_mean_months": int(observed["mean_return_pct"].gt(0).sum()),
            "positive_month_fraction": float(observed["mean_return_pct"].gt(0).mean()) if len(observed) else None,
            "worst_month_mean_pct": float(observed["mean_return_pct"].min()) if len(observed) else None,
            "best_month_mean_pct": float(observed["mean_return_pct"].max()) if len(observed) else None,
        })
    counts: dict[str, object] = {
        "panel_rows": len(panel), "observed_rows": int(panel["bar_present"].sum()),
        "calendar_sessions": len(calendar), "feature_ready_rows": int(panel["feature_ready"].sum()),
        "feature_ready_120_rows": int(panel["feature_ready_120"].sum()),
        "crossing_ready_rows": int((
            panel["feature_ready"] & panel.groupby("ticker")["feature_ready"].shift(1, fill_value=False)
        ).sum()),
        "gap_counts": {str(k): int(v) for k, v in panel["gap_reason"].value_counts().items()},
        "common20_evaluation_rows": int(panel["evaluation_ready_common_20d"].sum()),
        "horizons": {},
        "signals": {},
    }
    horizon_counts = {}
    for horizon in HORIZONS:
        horizon_counts[str(horizon)] = {
            "feature_ready_mature_valid_rows": int((
                panel["feature_ready"] & panel[f"target_status_{horizon}d"].eq("AVAILABLE")
            ).sum()),
            "evaluation_rows_after_split_purge": int(panel[f"evaluation_ready_{horizon}d"].sum()),
            "status_counts": {str(k): int(v) for k, v in panel[f"target_status_{horizon}d"].value_counts().items()},
        }
    counts["horizons"] = horizon_counts
    counts["signals"] = {
        strategy: {
            "all": int(crossing["strategy"].eq(strategy).sum()),
            "non_overheated": int((crossing["strategy"].eq(strategy) & ~crossing["overheated"]).sum()),
            "overheated": int((crossing["strategy"].eq(strategy) & crossing["overheated"]).sum()),
        }
        for strategy in STRATEGIES
    }
    concentration = []
    for strategy in STRATEGIES:
        sizes = crossing.loc[
            crossing["strategy"].eq(strategy) & ~crossing["overheated"]
        ].groupby("trading_date").size().sort_values(ascending=False)
        concentration.append({
            "strategy": strategy, "signal_dates": len(sizes),
            "max_signals_one_date": int(sizes.max()) if len(sizes) else 0,
            "top10_dates_signal_share": float(sizes.head(10).sum() / sizes.sum()) if len(sizes) else None,
        })
    return {
        "counts": counts,
        "primary_comparisons": primary.astype(object).where(primary.notna(), None).to_dict("records"),
        "monthly_stability": stability, "date_concentration": concentration,
        "overlap": overlap_report(panel), "score_quantiles": score_quantiles(events),
    }

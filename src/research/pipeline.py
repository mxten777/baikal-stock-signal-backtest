"""Frozen local-input runner with a Research-only write boundary."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import platform
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src import config, indicators, signal_engine
from src.expanded_shadow_universe import load_expanded_universe
from src.research.panel import build_ticker_panel, normalize_history
from src.research.report import BOOTSTRAP_REPEATS, bootstrap_report, comparison_table, strategy_events, summarize

SNAPSHOT = "2026-10-07"
BASELINE = "bfc16621c9a758e1cdfaba0ee512b11ea75320b2"
RULE_VERSION = "STEP35B-fixed574-v1"
OUTPUT_RELATIVE = Path("output") / "research" / "fixed574_2026-10-07"
LIMITATIONS = [
    "Fixed 2026-09-17 universe: survivorship/selection bias, not historical whole-market membership.",
    "2026-10-07 retrospective history: historical point-in-time prices and adjustment status UNVERIFIED.",
    "Observed-session union is not an official exchange calendar; whole-market missing sessions cannot be certified.",
    "Missing/suspended dates are indistinguishable; no price filling or delayed exit. Missing-path exclusion may bias results.",
    "Close-to-close raw returns are descriptive, not executable P&L; no costs, dividends or benchmark excess.",
    "2026 is a retrospective holdout, not prospective Forward Validation. No fitting or threshold optimization.",
]


@dataclass(frozen=True)
class FrozenInputs:
    manifest: dict[str, object]
    histories: dict[str, pd.DataFrame]
    calendar: pd.DatetimeIndex
    source_hashes: dict[str, str | None]


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes())


def operational_inventory(root: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for directory in ("src", "scripts", "dashboard", "data", "output"):
        base = root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            parts = relative.parts
            if any(part in {"__pycache__", "node_modules", ".pytest_cache", "dist", ".vite"} for part in parts):
                continue
            if len(parts) > 1 and parts[0] in {"src", "output"} and parts[1] == "research":
                continue
            if relative == Path("scripts") / "fixed574_research.py":
                continue
            inventory[relative.as_posix()] = file_hash(path)
    return inventory


def verify_inventory(root: Path, before: dict[str, str]) -> None:
    after = operational_inventory(root)
    if after != before:
        changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        raise RuntimeError(f"Operational files changed during research: {changed[:20]}")


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(
        index=False, lineterminator="\n", date_format="%Y-%m-%d", float_format="%.17g",
    ).encode("utf-8")


def write_research_file(root: Path, name: str, payload: bytes) -> Path:
    expected = root.resolve() / OUTPUT_RELATIVE
    output = expected.resolve()
    if output != expected:
        raise ValueError("Research output cannot be redirected by symlinks or junctions")
    target = (output / name).resolve()
    if target.parent != output:
        raise ValueError("Research output must be a direct child of the isolated output directory")
    output.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise RuntimeError(f"Non-deterministic rerun or changed frozen inputs/rules: {target.name}")
        return target
    with target.open("xb") as handle:
        handle.write(payload)
    return target


def _verify_approved_constants() -> None:
    actual = (
        config.RAW_SCORE_MAX, config.SIGNAL_THRESHOLD, config.SIGNAL_PREV_THRESHOLD,
        config.OVERHEATED_RSI, config.OVERHEATED_RETURN_5D, config.OVERHEATED_VOLUME_RATIO,
        signal_engine.V2_VOLUME_THRESHOLD, signal_engine.V2_VOLUME_PENALTY,
        signal_engine.V2_PRE_RETURN_THRESHOLD, signal_engine.V2_PRE_RETURN_PENALTY,
        signal_engine.V2_RSI_THRESHOLD, signal_engine.V2_RSI_PENALTY,
    )
    if actual != (65, 75, 75, 75, 20.0, 4.0, 3.0, 10, 12.0, 10, 70.0, 5):
        raise RuntimeError("Operational constants differ from the approved STEP 35-B design")


def freeze_inputs(root: Path, bootstrap_repeats: int) -> FrozenInputs:
    universe_path = root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv"
    universe = load_expanded_universe(universe_path)
    histories: dict[str, pd.DataFrame] = {}
    source_hashes: dict[str, str | None] = {}
    entries: list[dict[str, object]] = []
    market_dir = root / "data" / "expanded_shadow" / "market" / SNAPSHOT
    known = {stock.ticker for stock in universe.tickers}
    unexpected = {p.stem for p in market_dir.glob("*.csv")} - known
    if unexpected:
        raise ValueError(f"Unexpected input tickers: {sorted(unexpected)}")
    for stock in universe.tickers:
        source = market_dir / f"{stock.ticker}.csv"
        entry: dict[str, object] = {
            "ticker": stock.ticker, "market": stock.market,
            "path": source.relative_to(root).as_posix(),
        }
        if source.exists():
            payload = source.read_bytes()
            history = normalize_history(pd.read_csv(io.BytesIO(payload)), SNAPSHOT)
            if history.empty:
                raise ValueError(f"Empty frozen history: {stock.ticker}")
            entry.update({
                "status": "AVAILABLE", "sha256": sha256(payload), "rows": len(history),
                "first_date": history["date"].iloc[0].strftime("%Y-%m-%d"),
                "last_date": history["date"].iloc[-1].strftime("%Y-%m-%d"),
            })
            histories[stock.ticker] = history
            source_hashes[stock.ticker] = sha256(payload)
        else:
            entry.update({"status": "MISSING_FILE", "sha256": None, "rows": 0})
            histories[stock.ticker] = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
            source_hashes[stock.ticker] = None
        entries.append(entry)
    dates = sorted({date for history in histories.values() for date in history["date"]})
    if not dates:
        raise ValueError("No frozen OHLCV sessions available")
    source_paths = [
        Path(indicators.__file__), Path(signal_engine.__file__), Path(config.__file__),
        *sorted((Path(__file__).parent).glob("*.py")),
    ]
    manifest: dict[str, object] = {
        "rule_version": RULE_VERSION, "baseline_commit": BASELINE, "snapshot_basDd": SNAPSHOT,
        "universe_asof_date": universe.basDd, "universe_hash": universe.sha256,
        "universe_count": universe.row_count, "market_counts": universe.market_counts,
        "input_files": entries, "calendar": [date.strftime("%Y-%m-%d") for date in dates],
        "calendar_method": "union of dates in frozen OHLCV, no external fetch",
        "warmup_sessions": 60, "signal_previous_ready_required": True,
        "threshold": 75, "cooldown_sessions": 20, "price_adjustment_status": "UNVERIFIED",
        "bootstrap_repeats": bootstrap_repeats, "bootstrap_seed": 35,
        "source_code_hashes": {
            path.relative_to(Path(__file__).resolve().parents[2]).as_posix(): file_hash(path)
            for path in source_paths
        },
        "runtime": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__},
        "limitations": LIMITATIONS,
    }
    return FrozenInputs(manifest, histories, pd.DatetimeIndex(dates), source_hashes)


def render_report(summary: dict[str, object], comparisons: pd.DataFrame) -> str:
    counts = summary["counts"]
    lines = [
        "# STEP 35-C: Fixed-574 retrospective OHLCV research",
        "",
        "Research only. Frozen snapshot: 2026-10-07. Rule version: " + RULE_VERSION + ".",
        "No fitting, rule optimization, external fetch or operational ledger updates.",
        "",
        "## Counts",
        "```json", json.dumps(counts, indent=2, sort_keys=True), "```",
        "",
        "## Comparison",
        "Primary: non-overheated crossings; horizon-specific complete paths; year-boundary targets purged.",
        "All returns and win rates below are percentages. n/a means no evaluable observations.",
    ]
    selections = [
        ("Primary / year / market", comparisons[
            comparisons["cohort"].eq("crossing") & comparisons["heat"].eq("non_overheated")
            & comparisons["scope"].isin(["all", "year", "market"])
        ]),
        ("Overheated crossings", comparisons[
            comparisons["cohort"].eq("crossing") & comparisons["heat"].eq("overheated")
            & comparisons["scope"].eq("all")
        ]),
        ("Sensitivity", comparisons[
            comparisons["cohort"].isin(["common20", "cooldown20", "warmup120"])
            & comparisons["heat"].eq("non_overheated")
            & comparisons["scope"].eq("all")
        ]),
        ("Equal-count ranking (including overheated)", comparisons[
            comparisons["cohort"].isin(["top10_daily", "bottom10_daily"])
            & comparisons["heat"].eq("all")
            & comparisons["scope"].eq("all")
        ]),
        ("Monthly stability", comparisons[
            comparisons["cohort"].eq("crossing") & comparisons["heat"].eq("non_overheated")
            & comparisons["scope"].eq("month")
        ]),
    ]
    columns = [
        "cohort", "strategy", "scope_value", "horizon", "signal_count", "evaluated_count",
        "mean_return_pct", "median_return_pct", "win_rate_pct", "date_weighted_mean_return_pct",
    ]
    for title, table in selections:
        lines.extend(["", "### " + title, "", "| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"])
        for record in table[columns].to_dict("records"):
            cells = []
            for column in columns:
                value = record[column]
                cells.append("n/a" if pd.isna(value) else f"{value:.4f}" if isinstance(value, float) else str(value))
            lines.append("| " + " | ".join(cells) + " |")
    lines.extend([
        "", "## Confidence intervals",
        "Paired 20-session moving-block bootstrap, seed 35, 2,000 replicates. Entire dates are resampled together.",
        "Intervals are descriptive conditional on this selected universe and fixed signals, not bias-corrected certification.",
        "See [bootstrap.json](bootstrap.json) for mean/date-weighted intervals and paired strategy differences.",
        "", "## Other outputs",
        "- [summary.json](summary.json): overlap, score quantiles, monthly direction and date concentration.",
        "- [comparisons.csv](comparisons.csv): all/year/market/month, all/non-overheated/overheated and all sensitivities.",
        "- [events.csv.gz](events.csv.gz): crossings, common20, cooldown20, warmup120 and daily top/bottom 10%.",
        "- [panel.csv.gz](panel.csv.gz): calendar-grid panel, missing rows retained and explicitly flagged.",
        "- [input_freeze_manifest.json](input_freeze_manifest.json): exact frozen inputs, rules and runtime.",
        "- [operational_invariance.json](operational_invariance.json): protected file inventory and verification.",
        "", "## Limitations", *["- " + limitation for limitation in LIMITATIONS], "",
        "Signal-weighted and date-weighted results are not portfolio returns. Empty months remain missing, not zero.",
        "B uses the existing nominal 20-point Momentum denominator (actual component maximum is 17), without rescaling.",
        "Cooldown is applied to non-overheated crossings before target availability; the next 20 sessions are suppressed.",
        "Common20 uses identical 5D/10D/20D maturity and split-purge requirements.",
        "",
    ])
    return "\n".join(lines)


def run_research(root: Path, *, bootstrap_repeats: int = BOOTSTRAP_REPEATS) -> dict[str, object]:
    root = root.resolve()
    if bootstrap_repeats < 1:
        raise ValueError("Bootstrap repeats must be positive")
    _verify_approved_constants()
    protected = operational_inventory(root)
    frozen = freeze_inputs(root, bootstrap_repeats)
    manifest, histories = frozen.manifest, frozen.histories
    write_research_file(root, "input_freeze_manifest.json", json_bytes(manifest))
    universe = load_expanded_universe(root / "data" / "expanded_shadow" / "universe" / "expanded_universe_574.csv")
    calendar = frozen.calendar
    panels = []
    for stock in sorted(universe.tickers, key=lambda stock: stock.ticker):
        panel = build_ticker_panel(histories[stock.ticker], calendar, ticker=stock.ticker, market=stock.market)
        panel["snapshot_basDd"] = SNAPSHOT
        panel["universe_asof_date"] = universe.basDd
        panel["universe_hash"] = universe.sha256
        panel["source_file_hash"] = frozen.source_hashes[stock.ticker]
        panel["price_adjustment_status"] = "UNVERIFIED"
        panels.append(panel)
    panel = pd.concat(panels, ignore_index=True)
    if panel.duplicated(["ticker", "trading_date"]).any():
        raise RuntimeError("Duplicate panel primary key")
    events = strategy_events(panel)
    comparisons = comparison_table(events, panel)
    summary = summarize(panel, events, comparisons)
    bootstrap = bootstrap_report(events, calendar, repeats=bootstrap_repeats)
    frozen_after = freeze_inputs(root, bootstrap_repeats)
    if frozen_after.manifest != manifest:
        raise RuntimeError("Frozen inputs or rules changed during execution")
    verify_inventory(root, protected)
    artifacts = {
        "panel.csv.gz": gzip.compress(csv_bytes(panel), mtime=0),
        "events.csv.gz": gzip.compress(csv_bytes(events), mtime=0),
        "comparisons.csv": csv_bytes(comparisons),
        "summary.json": json_bytes(summary),
        "bootstrap.json": json_bytes(bootstrap),
        "report.md": render_report(summary, comparisons).encode("utf-8"),
        "operational_invariance.json": json_bytes({
            "status": "UNCHANGED", "protected_file_count": len(protected), "sha256_by_path": protected,
        }),
    }
    for name, payload in artifacts.items():
        write_research_file(root, name, payload)
    verify_inventory(root, protected)
    write_research_file(root, "artifact_hashes.json", json_bytes({
        name: sha256(payload) for name, payload in artifacts.items()
    }))
    return summary

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"

BASELINE = {
    "ALL_SIGNAL": {
        "valid_signal_count": 289,
        "avg_return_20d": 4.48,
        "avg_excess_20d": 0.99,
    },
    "SELECTION_MID": {
        "valid_signal_count": 96,
        "avg_return_20d": 7.07,
        "avg_excess_20d": 2.56,
    },
    "SELECTION_HIGH": {
        "valid_signal_count": 97,
        "avg_return_20d": 0.71,
        "avg_excess_20d": -0.79,
    },
    "SELECTION_MID_HIGH": {
        "valid_signal_count": 193,
        "avg_return_20d": 3.86,
        "avg_excess_20d": 0.87,
    },
}


def _load_strategy_metrics() -> pd.DataFrame:
    path = OUT / "step14_strategy_performance.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing strategy performance file: {path}")
    df = pd.read_csv(path)
    return df[["strategy", "signal_count", "avg_return_20d", "avg_excess_return_20d"]]


def build_baseline_comparison() -> pd.DataFrame:
    current = _load_strategy_metrics().rename(
        columns={
            "signal_count": "valid_signal_count",
            "avg_return_20d": "avg_return_20d",
            "avg_excess_return_20d": "avg_excess_20d",
        }
    )
    rows = []
    for strategy in ["ALL_SIGNAL", "SELECTION_MID", "SELECTION_HIGH", "SELECTION_MID_HIGH"]:
        baseline_row = BASELINE[strategy]
        current_row = current[current["strategy"] == strategy].iloc[0]
        value_old = float(baseline_row["avg_return_20d"])
        value_new = float(current_row["avg_return_20d"])
        excess_old = float(baseline_row["avg_excess_20d"])
        excess_new = float(current_row["avg_excess_20d"])
        rows.append({
            "strategy": strategy,
            "valid_signal_count_old": int(baseline_row["valid_signal_count"]),
            "valid_signal_count_new": int(current_row["valid_signal_count"]),
            "valid_signal_count_delta": int(current_row["valid_signal_count"]) - int(baseline_row["valid_signal_count"]),
            "avg_return_20d_old": value_old,
            "avg_return_20d_new": value_new,
            "avg_return_20d_delta": value_new - value_old,
            "avg_excess_20d_old": excess_old,
            "avg_excess_20d_new": excess_new,
            "avg_excess_20d_delta": excess_new - excess_old,
        })
    return pd.DataFrame(rows)


def build_signal_delta_csv() -> pd.DataFrame:
    columns = [
        "ticker",
        "signal_date",
        "old_new",
        "current_score",
        "previous_score",
        "signal_threshold",
        "prev_threshold",
        "note",
    ]
    return pd.DataFrame(columns=columns)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    comparison = build_baseline_comparison()
    comparison.to_csv(OUT / "step1_signal_baseline_comparison.csv", index=False)
    diff = build_signal_delta_csv()
    diff.to_csv(OUT / "step1_signal_delta.csv", index=False)
    print("Baseline comparison written:", OUT / "step1_signal_baseline_comparison.csv")
    print("Signal delta file written:", OUT / "step1_signal_delta.csv")
    print(comparison.to_string(index=False))


if __name__ == "__main__":
    main()

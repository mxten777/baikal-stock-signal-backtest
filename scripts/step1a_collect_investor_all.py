"""STEP 1-A: collect and audit Naver investor-flow data for all backtest tickers.

Run with: python -m scripts.step1a_collect_investor_all
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.config import DATA_RAW_DIR, OUTPUT_DIR, TICKERS
from src.data_provider.naver_investor_provider import fetch_investor_flow, save_investor_flow

INVESTOR_DIR = ROOT / "data" / "investor"
TARGET_START = pd.Timestamp("2023-11-01")
TARGET_END = pd.Timestamp("2026-07-31")
COVERAGE_OUTPUT = OUTPUT_DIR / "v02_step1a_investor_coverage.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "v02_step1a_investor_collection_summary.csv"
COMPARISON_OUTPUT = OUTPUT_DIR / "v02_step1a_existing_investor_comparison.csv"

COVERAGE_COLUMNS = [
    "ticker", "name", "start_date", "end_date", "expected_trading_days",
    "available_investor_days", "foreign_coverage_pct", "institution_coverage_pct",
    "coverage_pct", "missing_days", "missing_dates", "duplicate_rows", "invalid_rows",
    "non_trading_day_rows", "date_order_violations", "longest_missing_gap_days", "status",
]
SUMMARY_COLUMNS = [
    "ticker", "name", "collection_status", "saved_path", "rows_collected", "status",
    "foreign_coverage_pct", "institution_coverage_pct", "error",
]
COMPARISON_COLUMNS = [
    "ticker", "name", "previous_rows", "new_rows", "previous_start_date",
    "previous_end_date", "new_start_date", "new_end_date", "overlap_days",
    "foreign_value_differences", "institution_value_differences", "comparison_result",
]


def _load_expected_dates(ticker: str) -> pd.DatetimeIndex:
    raw_path = DATA_RAW_DIR / f"{ticker}.csv"
    raw = pd.read_csv(raw_path, parse_dates=["date"])
    dates = raw.loc[
        raw["date"].between(TARGET_START, TARGET_END), "date"
    ].dropna().drop_duplicates().sort_values()
    return pd.DatetimeIndex(dates)


def _date_text(value: pd.Timestamp | None) -> str:
    return value.strftime("%Y-%m-%d") if value is not None and not pd.isna(value) else ""


def _longest_missing_streak(expected: pd.DatetimeIndex, available: set[pd.Timestamp]) -> int:
    longest = 0
    current = 0
    for date in expected:
        if date in available:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def audit_investor_data(ticker: str, name: str, data: pd.DataFrame) -> dict[str, object]:
    expected = _load_expected_dates(ticker)
    required = ["date", "foreign_net_buy", "institution_net_buy"]
    missing_columns = set(required) - set(data.columns)
    if missing_columns:
        raise ValueError(f"필수 컬럼 누락: {sorted(missing_columns)}")

    frame = data.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    foreign = pd.to_numeric(frame["foreign_net_buy"], errors="coerce")
    institution = pd.to_numeric(frame["institution_net_buy"], errors="coerce")
    invalid_rows = int((frame["date"].isna() | foreign.isna() | institution.isna()).sum())
    duplicate_rows = int(frame.duplicated(subset="date", keep=False).sum() - frame.duplicated(subset="date", keep="first").sum())
    date_order_violations = int((frame["date"].diff().dt.days < 0).sum())

    valid = frame.loc[frame["date"].notna()].copy()
    valid["foreign_net_buy"] = foreign.loc[valid.index]
    valid["institution_net_buy"] = institution.loc[valid.index]
    expected_set = set(expected)
    non_trading_day_rows = int((~valid["date"].isin(expected_set)).sum())
    valid = valid[valid["date"].isin(expected_set)].drop_duplicates(subset="date", keep="first")

    foreign_days = set(valid.loc[valid["foreign_net_buy"].notna(), "date"])
    institution_days = set(valid.loc[valid["institution_net_buy"].notna(), "date"])
    complete_days = foreign_days & institution_days
    missing = expected_set - complete_days
    expected_count = len(expected)
    foreign_coverage = len(foreign_days) / expected_count * 100 if expected_count else 0.0
    institution_coverage = len(institution_days) / expected_count * 100 if expected_count else 0.0
    coverage = len(complete_days) / expected_count * 100 if expected_count else 0.0
    status = "PASS" if coverage == 100 else "PARTIAL" if complete_days else "FAIL"

    return {
        "ticker": ticker,
        "name": name,
        "start_date": _date_text(valid["date"].min() if not valid.empty else None),
        "end_date": _date_text(valid["date"].max() if not valid.empty else None),
        "expected_trading_days": expected_count,
        "available_investor_days": len(complete_days),
        "foreign_coverage_pct": round(foreign_coverage, 2),
        "institution_coverage_pct": round(institution_coverage, 2),
        "coverage_pct": round(coverage, 2),
        "missing_days": len(missing),
        "missing_dates": ";".join(date.strftime("%Y-%m-%d") for date in sorted(missing)),
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,
        "non_trading_day_rows": non_trading_day_rows,
        "date_order_violations": date_order_violations,
        "longest_missing_gap_days": _longest_missing_streak(expected, complete_days),
        "status": status,
    }


def compare_existing_data(ticker: str, name: str, previous: pd.DataFrame | None, current: pd.DataFrame) -> dict[str, object]:
    def normalize(data: pd.DataFrame) -> pd.DataFrame:
        frame = data[["date", "foreign_net_buy", "institution_net_buy"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["foreign_net_buy", "institution_net_buy"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.dropna(subset=["date"]).drop_duplicates("date").set_index("date").sort_index()

    current_normalized = normalize(current)
    if previous is None:
        return {
            "ticker": ticker, "name": name, "previous_rows": 0, "new_rows": len(current),
            "previous_start_date": "", "previous_end_date": "",
            "new_start_date": _date_text(current_normalized.index.min() if not current_normalized.empty else None),
            "new_end_date": _date_text(current_normalized.index.max() if not current_normalized.empty else None),
            "overlap_days": 0, "foreign_value_differences": 0,
            "institution_value_differences": 0, "comparison_result": "no_existing_baseline",
        }

    previous_normalized = normalize(previous)
    overlap = previous_normalized.index.intersection(current_normalized.index)
    foreign_differences = int((previous_normalized.loc[overlap, "foreign_net_buy"] != current_normalized.loc[overlap, "foreign_net_buy"]).sum())
    institution_differences = int((previous_normalized.loc[overlap, "institution_net_buy"] != current_normalized.loc[overlap, "institution_net_buy"]).sum())
    result = "identical_on_overlap" if not foreign_differences and not institution_differences else "source_snapshot_difference"
    return {
        "ticker": ticker, "name": name, "previous_rows": len(previous), "new_rows": len(current),
        "previous_start_date": _date_text(previous_normalized.index.min() if not previous_normalized.empty else None),
        "previous_end_date": _date_text(previous_normalized.index.max() if not previous_normalized.empty else None),
        "new_start_date": _date_text(current_normalized.index.min() if not current_normalized.empty else None),
        "new_end_date": _date_text(current_normalized.index.max() if not current_normalized.empty else None),
        "overlap_days": len(overlap), "foreign_value_differences": foreign_differences,
        "institution_value_differences": institution_differences, "comparison_result": result,
    }


def main() -> None:
    coverage_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    INVESTOR_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for ticker, name in TICKERS.items():
        destination = INVESTOR_DIR / f"{ticker}_investor.csv"
        previous = pd.read_csv(destination) if destination.exists() else None
        try:
            current = fetch_investor_flow(ticker, str(TARGET_START.date()), str(TARGET_END.date()))
            if current.empty:
                raise RuntimeError("Naver Finance returned no rows for the target period")
            audit = audit_investor_data(ticker, name, current)
            saved_path = save_investor_flow(current, INVESTOR_DIR)
            comparison_rows.append(compare_existing_data(ticker, name, previous, current))
            coverage_rows.append(audit)
            summary_rows.append({
                "ticker": ticker, "name": name, "collection_status": "COLLECTED",
                "saved_path": str(saved_path.relative_to(ROOT)), "rows_collected": len(current),
                "status": audit["status"], "foreign_coverage_pct": audit["foreign_coverage_pct"],
                "institution_coverage_pct": audit["institution_coverage_pct"], "error": "",
            })
            print(f"{ticker} {name}: {audit['status']} ({audit['coverage_pct']}%)")
        except Exception as exc:
            expected_count = len(_load_expected_dates(ticker))
            coverage_rows.append({
                "ticker": ticker, "name": name, "start_date": "", "end_date": "",
                "expected_trading_days": expected_count, "available_investor_days": 0,
                "foreign_coverage_pct": 0.0, "institution_coverage_pct": 0.0,
                "coverage_pct": 0.0, "missing_days": expected_count, "missing_dates": "",
                "duplicate_rows": 0, "invalid_rows": 0, "non_trading_day_rows": 0,
                "date_order_violations": 0, "longest_missing_gap_days": expected_count, "status": "FAIL",
            })
            summary_rows.append({
                "ticker": ticker, "name": name, "collection_status": "FAILED", "saved_path": "",
                "rows_collected": 0, "status": "FAIL", "foreign_coverage_pct": 0.0,
                "institution_coverage_pct": 0.0, "error": str(exc),
            })
            print(f"{ticker} {name}: FAIL ({exc})")

    pd.DataFrame(coverage_rows, columns=COVERAGE_COLUMNS).to_csv(COVERAGE_OUTPUT, index=False, encoding="utf-8-sig")
    pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS).to_csv(SUMMARY_OUTPUT, index=False, encoding="utf-8-sig")
    pd.DataFrame(comparison_rows, columns=COMPARISON_COLUMNS).to_csv(COMPARISON_OUTPUT, index=False, encoding="utf-8-sig")
    print(f"Wrote {COVERAGE_OUTPUT.relative_to(ROOT)}")
    print(f"Wrote {SUMMARY_OUTPUT.relative_to(ROOT)}")
    print(f"Wrote {COMPARISON_OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
"""
백테스트 결과 요약 및 콘솔 출력
"""

from __future__ import annotations

import pandas as pd
import numpy as np


def _win_rate(series: pd.Series) -> float:
    valid = series.dropna()
    if len(valid) == 0:
        return float("nan")
    return round((valid > 0).sum() / len(valid) * 100, 1)


def _avg(series: pd.Series) -> float:
    valid = series.dropna()
    if len(valid) == 0:
        return float("nan")
    return round(valid.mean(), 2)


def _median(series: pd.Series) -> float:
    valid = series.dropna()
    if len(valid) == 0:
        return float("nan")
    return round(valid.median(), 2)


def _has_excess(df: pd.DataFrame) -> bool:
    return "excess_return_20d" in df.columns


def build_summary(signals_df: pd.DataFrame) -> pd.DataFrame:
    """종목별 요약 DataFrame 생성"""
    rows = []
    has_excess = _has_excess(signals_df)

    for ticker, grp in signals_df.groupby("ticker"):
        name = grp["name"].iloc[0]
        valid = grp[grp["signal_type"] != "OVERHEATED"]
        overheated_count = len(grp) - len(valid)

        row_data: dict = {
            "ticker": ticker,
            "name": name,
            "signal_count": len(grp),
            "valid_signal_count": len(valid),
            "overheated_count": overheated_count,
            "win_rate_5d": _win_rate(valid["return_5d"]),
            "win_rate_10d": _win_rate(valid["return_10d"]),
            "win_rate_20d": _win_rate(valid["return_20d"]),
            "avg_return_5d": _avg(valid["return_5d"]),
            "avg_return_10d": _avg(valid["return_10d"]),
            "avg_return_20d": _avg(valid["return_20d"]),
            "median_return_5d": _median(valid["return_5d"]),
            "median_return_10d": _median(valid["return_10d"]),
            "median_return_20d": _median(valid["return_20d"]),
            "best_return_20d": round(valid["return_20d"].dropna().max(), 2) if not valid["return_20d"].dropna().empty else float("nan"),
            "worst_return_20d": round(valid["return_20d"].dropna().min(), 2) if not valid["return_20d"].dropna().empty else float("nan"),
            "avg_max_drawdown_20d": _avg(valid["max_drawdown_20d"]),
            "worst_max_drawdown_20d": round(valid["max_drawdown_20d"].dropna().min(), 2) if not valid["max_drawdown_20d"].dropna().empty else float("nan"),
        }

        if has_excess:
            row_data.update({
                "benchmark": valid["benchmark"].iloc[0] if not valid.empty else "",
                "avg_excess_5d": _avg(valid["excess_return_5d"]),
                "avg_excess_10d": _avg(valid["excess_return_10d"]),
                "avg_excess_20d": _avg(valid["excess_return_20d"]),
                "median_excess_20d": _median(valid["excess_return_20d"]),
                "excess_win_rate_5d": _win_rate(valid["excess_return_5d"]),
                "excess_win_rate_10d": _win_rate(valid["excess_return_10d"]),
                "excess_win_rate_20d": _win_rate(valid["excess_return_20d"]),
            })

        rows.append(row_data)

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 콘솔 출력
# ──────────────────────────────────────────────

def print_console_report(signals_df: pd.DataFrame, summary_df: pd.DataFrame) -> None:
    """콘솔 전체 보고"""
    has_excess = _has_excess(signals_df)

    print("=" * 60)
    print("BAIKAL Signal Score v0.1 — Backtest Result")
    print("=" * 60)

    for _, row in summary_df.iterrows():
        print(f"\n[{row['ticker']}] {row['name']}")
        print(f"  Signal Count     : {row['signal_count']} (valid: {row['valid_signal_count']}, overheated: {row['overheated_count']})")
        print(f"  5D  Win Rate     : {row['win_rate_5d']}%")
        print(f"  10D Win Rate     : {row['win_rate_10d']}%")
        print(f"  20D Win Rate     : {row['win_rate_20d']}%")
        print(f"  Avg 5D  Return   : {row['avg_return_5d']}%")
        print(f"  Avg 10D Return   : {row['avg_return_10d']}%")
        print(f"  Avg 20D Return   : {row['avg_return_20d']}%")
        print(f"  Med 5D  Return   : {row['median_return_5d']}%")
        print(f"  Med 10D Return   : {row['median_return_10d']}%")
        print(f"  Med 20D Return   : {row['median_return_20d']}%")
        print(f"  Best  20D Return : {row['best_return_20d']}%")
        print(f"  Worst 20D Return : {row['worst_return_20d']}%")
        print(f"  Avg  Drawdown 20D: {row['avg_max_drawdown_20d']}%")
        print(f"  Worst Drawdown 20D: {row['worst_max_drawdown_20d']}%")
        if has_excess and "avg_excess_20d" in row:
            print(f"  Benchmark        : {row.get('benchmark', '')}")
            print(f"  Avg Excess  5D   : {row['avg_excess_5d']}%")
            print(f"  Avg Excess  10D  : {row['avg_excess_10d']}%")
            print(f"  Avg Excess  20D  : {row['avg_excess_20d']}%")
            print(f"  Med Excess  20D  : {row['median_excess_20d']}%")
            print(f"  Excess WR   20D  : {row['excess_win_rate_20d']}%")

    # 전체 합산
    valid_all = signals_df[signals_df["signal_type"] != "OVERHEATED"]
    overheated_all = len(signals_df) - len(valid_all)
    print("\n" + "=" * 60)
    print("TOTAL")
    print("=" * 60)
    print(f"  TOTAL SIGNALS     : {len(signals_df)} (valid: {len(valid_all)}, overheated: {overheated_all})")
    print(f"  5D  WIN RATE      : {_win_rate(valid_all['return_5d'])}%")
    print(f"  10D WIN RATE      : {_win_rate(valid_all['return_10d'])}%")
    print(f"  20D WIN RATE      : {_win_rate(valid_all['return_20d'])}%")
    print(f"  AVG  5D  RETURN   : {_avg(valid_all['return_5d'])}%")
    print(f"  AVG  10D RETURN   : {_avg(valid_all['return_10d'])}%")
    print(f"  AVG  20D RETURN   : {_avg(valid_all['return_20d'])}%")
    print(f"  MED  5D  RETURN   : {_median(valid_all['return_5d'])}%")
    print(f"  MED  10D RETURN   : {_median(valid_all['return_10d'])}%")
    print(f"  MED  20D RETURN   : {_median(valid_all['return_20d'])}%")
    worst = valid_all["max_drawdown_20d"].dropna()
    print(f"  WORST 20D DRAWDOWN: {round(worst.min(), 2) if not worst.empty else 'N/A'}%")

    if has_excess:
        print()
        _print_excess_report(valid_all)

    print("=" * 60)


def _print_excess_report(valid_all: pd.DataFrame) -> None:
    """초과수익 분석 섹션 (Section 4–7)"""
    print("─" * 60)
    print("BENCHMARK EXCESS RETURN ANALYSIS")
    print("─" * 60)

    # ── Section 4: 전체 초과수익 ──────────────────────────────────────
    print(f"  Valid Signals          : {len(valid_all['excess_return_20d'].dropna())}")
    print(f"  Avg   Excess  5D       : {_avg(valid_all['excess_return_5d'])}%")
    print(f"  Avg   Excess  10D      : {_avg(valid_all['excess_return_10d'])}%")
    print(f"  Avg   Excess  20D      : {_avg(valid_all['excess_return_20d'])}%")
    print(f"  Median Excess 5D       : {_median(valid_all['excess_return_5d'])}%")
    print(f"  Median Excess 10D      : {_median(valid_all['excess_return_10d'])}%")
    print(f"  Median Excess 20D      : {_median(valid_all['excess_return_20d'])}%")
    print(f"  Excess Win Rate 5D     : {_win_rate(valid_all['excess_return_5d'])}%")
    print(f"  Excess Win Rate 10D    : {_win_rate(valid_all['excess_return_10d'])}%")
    print(f"  Excess Win Rate 20D    : {_win_rate(valid_all['excess_return_20d'])}%")

    # ── Section 5: 종목별 초과수익 ────────────────────────────────────
    print()
    print("─" * 60)
    print("PER-TICKER EXCESS RETURN")
    print("─" * 60)
    print(f"  {'Ticker':<8} {'Name':<16} {'Valid':>5} {'AvgEx5':>7} {'AvgEx10':>7} {'AvgEx20':>7} {'MedEx20':>7} {'ExWR20':>7}")
    print("  " + "-" * 68)
    for ticker, grp in valid_all.groupby("ticker"):
        name = grp["name"].iloc[0][:14]
        n = len(grp["excess_return_20d"].dropna())
        ae5  = _avg(grp["excess_return_5d"])
        ae10 = _avg(grp["excess_return_10d"])
        ae20 = _avg(grp["excess_return_20d"])
        me20 = _median(grp["excess_return_20d"])
        ew20 = _win_rate(grp["excess_return_20d"])
        print(f"  {ticker:<8} {name:<16} {n:>5} {ae5:>7} {ae10:>7} {ae20:>7} {me20:>7} {ew20:>7}")

    # ── Section 6: 음수 성과 종목 ─────────────────────────────────────
    print()
    print("─" * 60)
    print("FOCUS: NAVER(035420) / 카카오(035720) / SK이노베이션(096770)")
    print("─" * 60)
    print(f"  {'Ticker':<8} {'Name':<16} {'Ret20':>7} {'BmRet20':>7} {'ExRet20':>7}")
    print("  " + "-" * 50)
    for ticker in ["035420", "035720", "096770"]:
        grp = valid_all[valid_all["ticker"] == ticker]
        if grp.empty:
            print(f"  {ticker:<8} {'(데이터없음)':<16}")
            continue
        name = grp["name"].iloc[0][:14]
        r20  = _avg(grp["return_20d"])
        br20 = _avg(grp["benchmark_return_20d"])
        ex20 = _avg(grp["excess_return_20d"])
        print(f"  {ticker:<8} {name:<16} {r20:>7} {br20:>7} {ex20:>7}")

    # ── Section 7: Outlier 확인 ───────────────────────────────────────
    print()
    print("─" * 60)
    print("OUTLIER ANALYSIS (Return 20D)")
    print("─" * 60)
    col = "return_20d"
    ex_col = "excess_return_20d"
    valid_20d = valid_all.dropna(subset=[col]).copy()
    valid_20d = valid_20d.sort_values(col, ascending=False)

    top5 = valid_20d.head(5)
    bot5 = valid_20d.tail(5)

    print("  Top 5 Signals (20D Return):")
    for _, r in top5.iterrows():
        ex = r.get(ex_col, float("nan"))
        print(f"    [{r['ticker']}] {str(r['signal_date'])[:10]}  Ret={r[col]:+.2f}%  Excess={ex:+.2f}%")

    print("  Bottom 5 Signals (20D Return):")
    for _, r in bot5.iterrows():
        ex = r.get(ex_col, float("nan"))
        print(f"    [{r['ticker']}] {str(r['signal_date'])[:10]}  Ret={r[col]:+.2f}%  Excess={ex:+.2f}%")

    # 상위 5 제외 후 통계
    ex_top5_idx = top5.index
    trimmed = valid_20d[~valid_20d.index.isin(ex_top5_idx)]
    print()
    print("  상위 5개 Signal 제외 후:")
    print(f"    Avg    Return 20D : {_avg(trimmed[col])}%")
    print(f"    Median Return 20D : {_median(trimmed[col])}%")
    print(f"    Avg    Excess 20D : {_avg(trimmed[ex_col])}%")
    print(f"    Median Excess 20D : {_median(trimmed[ex_col])}%")


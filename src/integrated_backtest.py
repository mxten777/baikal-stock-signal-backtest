"""
STEP 14 — 통합 백테스트

기존 Signal(STEP 1~9)과 Stock Selection v0.1(STEP 13) 점수를 결합하여
전략별(A/B/C/D) 성과를 비교하는 순수 계산 로직.

전략 정의:
  A. ALL_SIGNAL         : 기존 Valid Signal 전체 (OVERHEATED 제외, STEP 13과 동일 모집단)
  B. SELECTION_MID      : Stock Selection Score 그룹 MID
  C. SELECTION_HIGH     : Stock Selection Score 그룹 HIGH
  D. SELECTION_MID_HIGH : MID + HIGH

주의:
- STEP 13 점수/가중치/그룹 기준(score_group)을 그대로 사용한다. 새 필터/튜닝 금지.
- 입력은 output/step13_stock_selection_score.csv (이미 look-ahead bias 방지 적용됨)를 그대로 사용한다.
"""

from __future__ import annotations

import pandas as pd

STRATEGIES = ("ALL_SIGNAL", "SELECTION_MID", "SELECTION_HIGH", "SELECTION_MID_HIGH")


def build_strategies(scored: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """STEP 13 score_group 컬럼을 기준으로 전략별 부분집합을 만든다."""
    return {
        "ALL_SIGNAL": scored,
        "SELECTION_MID": scored[scored["score_group"] == "MID"],
        "SELECTION_HIGH": scored[scored["score_group"] == "HIGH"],
        "SELECTION_MID_HIGH": scored[scored["score_group"].isin(["MID", "HIGH"])],
    }


def _avg(series: pd.Series) -> float:
    s = series.dropna()
    return round(float(s.mean()), 2) if len(s) else float("nan")


def _win_rate(series: pd.Series) -> float:
    s = series.dropna()
    return round(float((s > 0).sum() / len(s) * 100), 1) if len(s) else float("nan")


def compute_metrics(df: pd.DataFrame) -> dict:
    """단일 그룹(전략/종목/연도 등)에 대한 성과 지표 딕셔너리."""
    n = len(df)
    if n == 0:
        nan = float("nan")
        return {
            "signal_count": 0,
            "avg_return_5d": nan, "avg_return_10d": nan, "avg_return_20d": nan,
            "win_rate_5d": nan, "win_rate_10d": nan, "win_rate_20d": nan,
            "avg_excess_return_20d": nan,
            "avg_max_drawdown_20d": nan, "worst_max_drawdown_20d": nan,
        }
    dd = df["max_drawdown_20d"].dropna()
    return {
        "signal_count": n,
        "avg_return_5d": _avg(df["return_5d"]),
        "avg_return_10d": _avg(df["return_10d"]),
        "avg_return_20d": _avg(df["return_20d"]),
        "win_rate_5d": _win_rate(df["return_5d"]),
        "win_rate_10d": _win_rate(df["return_10d"]),
        "win_rate_20d": _win_rate(df["return_20d"]),
        "avg_excess_return_20d": _avg(df["excess_return_20d"]) if "excess_return_20d" in df.columns else float("nan"),
        "avg_max_drawdown_20d": _avg(df["max_drawdown_20d"]),
        "worst_max_drawdown_20d": round(float(dd.min()), 2) if len(dd) else float("nan"),
    }


def build_strategy_table(scored: pd.DataFrame) -> pd.DataFrame:
    """전략 A/B/C/D별 성과 테이블."""
    strategies = build_strategies(scored)
    rows = [{"strategy": name, **compute_metrics(df)} for name, df in strategies.items()]
    return pd.DataFrame(rows)


def build_ticker_table(scored: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    """단일 전략 내 종목별 성과 테이블 (Signal 수 내림차순)."""
    df = build_strategies(scored)[strategy_name]
    rows = []
    for ticker, gdf in df.groupby("ticker"):
        name = gdf["name"].iloc[0] if "name" in gdf.columns and len(gdf) else ""
        rows.append({"ticker": ticker, "name": name, **compute_metrics(gdf)})
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    return table.sort_values("signal_count", ascending=False).reset_index(drop=True)


def add_year_column(scored: pd.DataFrame) -> pd.DataFrame:
    result = scored.copy()
    result["year"] = pd.to_datetime(result["signal_date"]).dt.year
    return result


def build_yearly_table(scored: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    """단일 전략 내 연도별 성과 테이블."""
    df = build_strategies(add_year_column(scored))[strategy_name]
    rows = [{"year": int(year), **compute_metrics(gdf)} for year, gdf in sorted(df.groupby("year"))]
    return pd.DataFrame(rows)


def ticker_concentration(scored: pd.DataFrame, strategy_name: str) -> dict:
    """특정 종목 의존 여부: 최다 비중 종목과 비중(%), 해당 종목 제외 시 20D 평균수익률."""
    df = build_strategies(scored)[strategy_name]
    if df.empty:
        return {"top_ticker": None, "top_ticker_share_pct": float("nan"),
                "avg_return_20d_excl_top": float("nan")}
    counts = df["ticker"].value_counts()
    top_ticker = counts.index[0]
    share_pct = round(float(counts.iloc[0] / len(df) * 100), 1)
    excl = df[df["ticker"] != top_ticker]
    return {
        "top_ticker": top_ticker,
        "top_ticker_share_pct": share_pct,
        "avg_return_20d_excl_top": _avg(excl["return_20d"]),
    }


def year_concentration(scored: pd.DataFrame, strategy_name: str) -> dict:
    """특정 연도 의존 여부: 최다 비중 연도와 비중(%), 해당 연도 제외 시 20D 평균수익률."""
    df = build_strategies(add_year_column(scored))[strategy_name]
    if df.empty:
        return {"top_year": None, "top_year_share_pct": float("nan"),
                "avg_return_20d_excl_top_year": float("nan")}
    counts = df["year"].value_counts()
    top_year = int(counts.index[0])
    share_pct = round(float(counts.iloc[0] / len(df) * 100), 1)
    excl = df[df["year"] != top_year]
    return {
        "top_year": top_year,
        "top_year_share_pct": share_pct,
        "avg_return_20d_excl_top_year": _avg(excl["return_20d"]),
    }

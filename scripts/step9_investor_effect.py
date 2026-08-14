"""
STEP 9 — 외국인·기관 수급 효과 검증

v0.2 Signal에 외국인·기관 수급 데이터를 결합하여
수급이 좋은 Signal과 나쁜 Signal을 구분하는 데 실제 도움이 되는지 검증한다.

기존 코드(v0.1, v0.2, penalty, threshold, benchmark, overheated filter) 수정 없음.
수급을 Score에 추가하지 않음.

실행: python -m scripts.step9_investor_effect
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
TARGET_TICKERS = ["005930", "000660", "035720"]
TICKER_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"}
INVESTOR_DIR = ROOT / "data" / "investor"
SIGNALS_PATH = ROOT / "output" / "signals.csv"
RAW_DIR = ROOT / "data" / "raw"

LOW_SAMPLE_THRESHOLD = 5


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
def load_signals() -> pd.DataFrame:
    df = pd.read_csv(SIGNALS_PATH)
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    return df[df["ticker"].isin(TARGET_TICKERS)].copy().reset_index(drop=True)


def load_investor_data() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for ticker in TARGET_TICKERS:
        path = INVESTOR_DIR / f"{ticker}_investor.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            df["ticker"] = df["ticker"].astype(str).str.zfill(6)
            df = df.sort_values("date").reset_index(drop=True)
            result[ticker] = df
        else:
            print(f"  !! 수급 데이터 없음: {path}")
    return result


def load_raw_data() -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    for ticker in TARGET_TICKERS:
        path = RAW_DIR / f"{ticker}.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            df = df.sort_values("date").reset_index(drop=True)
            result[ticker] = df
    return result


# ─────────────────────────────────────────────
# 수급 Feature 계산
# ─────────────────────────────────────────────
def compute_investor_features(
    signals: pd.DataFrame,
    investor_map: dict[str, pd.DataFrame],
    raw_map: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Signal 발생일 기준 수급 Feature를 추가한다."""
    feature_rows: list[dict] = []

    for _, sig in signals.iterrows():
        ticker = sig["ticker"]
        signal_date = sig["signal_date"]
        row: dict = {}

        inv_df = investor_map.get(ticker)
        if inv_df is not None:
            past = inv_df[inv_df["date"] <= signal_date]
            for window, suffix in [(1, "1d"), (3, "3d"), (5, "5d")]:
                chunk = past.tail(window)
                row[f"foreign_net_{suffix}"] = (
                    chunk["foreign_net_buy"].sum() if not chunk.empty else np.nan
                )
                row[f"institution_net_{suffix}"] = (
                    chunk["institution_net_buy"].sum() if not chunk.empty else np.nan
                )
        else:
            for suffix in ("1d", "3d", "5d"):
                row[f"foreign_net_{suffix}"] = np.nan
                row[f"institution_net_{suffix}"] = np.nan

        raw_df = raw_map.get(ticker)
        if raw_df is not None:
            past_raw = raw_df[raw_df["date"] <= signal_date]
            chunk_20 = past_raw.tail(20)
            row["avg_volume_20d"] = (
                chunk_20["volume"].mean() if not chunk_20.empty else np.nan
            )
        else:
            row["avg_volume_20d"] = np.nan

        avg_vol = row["avg_volume_20d"]
        if not pd.isna(avg_vol) and avg_vol > 0:
            f5d = row["foreign_net_5d"]
            i5d = row["institution_net_5d"]
            row["foreign_5d_ratio"] = f5d / avg_vol if not pd.isna(f5d) else np.nan
            row["institution_5d_ratio"] = i5d / avg_vol if not pd.isna(i5d) else np.nan
        else:
            row["foreign_5d_ratio"] = np.nan
            row["institution_5d_ratio"] = np.nan

        feature_rows.append(row)

    features = pd.DataFrame(feature_rows)
    return pd.concat(
        [signals.reset_index(drop=True), features.reset_index(drop=True)], axis=1
    )


# ─────────────────────────────────────────────
# 통계 헬퍼
# ─────────────────────────────────────────────
def _low_sample_flag(n: int) -> str:
    return " [LOW SAMPLE]" if n < LOW_SAMPLE_THRESHOLD else ""


def _group_stats(label: str, df: pd.DataFrame) -> dict:
    n = len(df)
    flag = _low_sample_flag(n)
    valid_20d = df["excess_return_20d"].dropna() if n > 0 else pd.Series(dtype=float)
    return {
        "Group": f"{label}{flag}",
        "Count": n,
        "Avg Excess 5D": round(df["excess_return_5d"].mean(), 2) if n > 0 else np.nan,
        "Avg Excess 10D": round(df["excess_return_10d"].mean(), 2) if n > 0 else np.nan,
        "Avg Excess 20D": round(valid_20d.mean(), 2) if len(valid_20d) > 0 else np.nan,
        "Median Excess 20D": round(valid_20d.median(), 2) if len(valid_20d) > 0 else np.nan,
        "Win Rate 20D (%)": round((valid_20d > 0).mean() * 100, 1) if len(valid_20d) > 0 else np.nan,
    }


def _print_table(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()


# ─────────────────────────────────────────────
# 섹션 4 — 외국인 5일 누적 성과
# ─────────────────────────────────────────────
def print_foreign_5d_analysis(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 4 — 외국인 5일 누적 순매수 기준 성과")
    print("=" * 70)
    pos = df[df["foreign_net_5d"] > 0]
    neg = df[df["foreign_net_5d"] <= 0]
    _print_table([
        _group_stats("POSITIVE (foreign_net_5d > 0)", pos),
        _group_stats("NEGATIVE (foreign_net_5d <= 0)", neg),
    ])


# ─────────────────────────────────────────────
# 섹션 5 — 기관 5일 누적 성과
# ─────────────────────────────────────────────
def print_institution_5d_analysis(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 5 — 기관 5일 누적 순매수 기준 성과")
    print("=" * 70)
    pos = df[df["institution_net_5d"] > 0]
    neg = df[df["institution_net_5d"] <= 0]
    _print_table([
        _group_stats("POSITIVE (institution_net_5d > 0)", pos),
        _group_stats("NEGATIVE (institution_net_5d <= 0)", neg),
    ])


# ─────────────────────────────────────────────
# 섹션 6 — 동시 수급 조합별 성과
# ─────────────────────────────────────────────
def print_combined_5d_analysis(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 6 — 외국인·기관 동시 수급 조합별 성과")
    print("=" * 70)
    f_pos = df["foreign_net_5d"] > 0
    i_pos = df["institution_net_5d"] > 0
    _print_table([
        _group_stats("A: Foreign + / Institution +", df[f_pos & i_pos]),
        _group_stats("B: Foreign + / Institution -", df[f_pos & ~i_pos]),
        _group_stats("C: Foreign - / Institution +", df[~f_pos & i_pos]),
        _group_stats("D: Foreign - / Institution -", df[~f_pos & ~i_pos]),
    ])


# ─────────────────────────────────────────────
# 섹션 7 — 수급 강도 구간별 성과
# ─────────────────────────────────────────────
def print_ratio_buckets(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 7 — 수급 강도 구간별 성과 (ratio = net_5d / avg_volume_20d)")
    print("=" * 70)

    for col, label in [
        ("foreign_5d_ratio", "외국인"),
        ("institution_5d_ratio", "기관"),
    ]:
        valid = df[df[col].notna()].copy()
        print(f"  [{label}] 수급 강도 구간")
        buckets = [
            ("ratio <= -0.20", valid[valid[col] <= -0.20]),
            ("-0.20 < ratio <= 0", valid[(valid[col] > -0.20) & (valid[col] <= 0)]),
            ("0 < ratio < 0.20", valid[(valid[col] > 0) & (valid[col] < 0.20)]),
            ("ratio >= 0.20", valid[valid[col] >= 0.20]),
        ]
        rows = []
        for bucket_label, bucket_df in buckets:
            n = len(bucket_df)
            flag = _low_sample_flag(n)
            valid_20d = bucket_df["excess_return_20d"].dropna() if n > 0 else pd.Series(dtype=float)
            rows.append({
                "Bucket": f"{bucket_label}{flag}",
                "Count": n,
                "Avg Excess 20D": round(valid_20d.mean(), 2) if len(valid_20d) > 0 else np.nan,
                "Median Excess 20D": round(valid_20d.median(), 2) if len(valid_20d) > 0 else np.nan,
                "Win Rate 20D (%)": round((valid_20d > 0).mean() * 100, 1) if len(valid_20d) > 0 else np.nan,
            })
        _print_table(rows)


# ─────────────────────────────────────────────
# 섹션 8 — 종목별 분석
# ─────────────────────────────────────────────
def print_per_ticker_analysis(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 8 — 종목별 수급 성과")
    print("=" * 70)

    for ticker, name in TICKER_NAMES.items():
        sub = df[df["ticker"] == ticker].copy()
        if sub.empty:
            print(f"  [{name}] Signal 없음\n")
            continue

        print(f"  [{name}] ({ticker})")
        f_pos = sub["foreign_net_5d"] > 0
        i_pos = sub["institution_net_5d"] > 0
        _print_table([
            _group_stats("외국인 net_5d > 0", sub[f_pos]),
            _group_stats("외국인 net_5d <= 0", sub[~f_pos]),
            _group_stats("기관 net_5d > 0", sub[i_pos]),
            _group_stats("기관 net_5d <= 0", sub[~i_pos]),
            _group_stats("외국인 + 기관 동시 순매수", sub[f_pos & i_pos]),
        ])


# ─────────────────────────────────────────────
# 섹션 9 — 카카오 특별 분석
# ─────────────────────────────────────────────
def print_kakao_special(df: pd.DataFrame) -> None:
    print("=" * 70)
    print("섹션 9 — 카카오 특별 분석 (Foreign + / Institution +)")
    print("=" * 70)

    kakao = df[df["ticker"] == "035720"].copy()
    if kakao.empty:
        print("  카카오 Signal 없음\n")
        return

    f_pos = kakao["foreign_net_5d"] > 0
    i_pos = kakao["institution_net_5d"] > 0
    both_pos = f_pos & i_pos

    _print_table([
        _group_stats("Foreign + / Institution +", kakao[both_pos]),
        _group_stats("나머지 (위 조건 미충족)", kakao[~both_pos]),
        _group_stats("전체 카카오", kakao),
    ])


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main() -> None:
    print("=" * 70)
    print("STEP 9 — 외국인·기관 수급 효과 검증")
    print("=" * 70)
    print()

    signals = load_signals()
    investor_map = load_investor_data()
    raw_map = load_raw_data()

    print(f"대상 종목  : {TARGET_TICKERS}")
    print(f"Signal 수  : {len(signals)}")
    print(f"수급 데이터: {list(investor_map.keys())}")
    print()

    df = compute_investor_features(signals, investor_map, raw_map)

    valid_mask = df["foreign_net_5d"].notna() & df["institution_net_5d"].notna()
    print(f"수급 Feature 생성 완료: {valid_mask.sum()} / {len(df)} Signal에 수급 데이터 있음")
    print()

    df_valid = df[valid_mask].copy()
    print_foreign_5d_analysis(df_valid)
    print_institution_5d_analysis(df_valid)
    print_combined_5d_analysis(df_valid)
    print_ratio_buckets(df_valid)
    print_per_ticker_analysis(df_valid)
    print_kakao_special(df_valid)


if __name__ == "__main__":
    main()

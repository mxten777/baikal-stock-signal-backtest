"""
Operational Input Integrity Gate — Shadow Pipeline 사전 입출력 검증 Module.

핵심 원칙:
    INVALID OR INCOMPLETE INPUT = PIPELINE MUST NOT START

이 모듈은 데이터 읽기(Read-only)만 수행하며, Production CSV 또는 환경 데이터를 절대 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import pandas as pd

# Default locations and tickers
ROOT_DIR = Path(__file__).parent.parent
DEFAULT_RAW_DIR = ROOT_DIR / "data" / "raw"
DEFAULT_INVESTOR_DIR = ROOT_DIR / "data" / "investor"

MARKET_REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
MARKET_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume"]

INVESTOR_REQUIRED_COLUMNS = ["date", "ticker", "foreign_net_buy", "institution_net_buy"]
INVESTOR_NUMERIC_COLUMNS = ["foreign_net_buy", "institution_net_buy"]


def get_default_tickers() -> Dict[str, str]:
    """기존 repository (src.config)의 TICKERS를 가져온다."""
    try:
        from src.config import TICKERS
        return dict(TICKERS)
    except Exception:
        # Fallback if src.config is not importable
        return {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "005380": "현대차",
            "000270": "기아",
            "035420": "NAVER",
            "035720": "카카오",
            "207940": "삼성바이오로직스",
            "068270": "셀트리온",
            "012450": "한화에어로스페이스",
            "034020": "두산에너빌리티",
            "080220": "제주반도체",
            "105560": "KB금융",
            "055550": "신한지주",
            "006400": "삼성SDI",
            "051910": "LG화학",
            "373220": "LG에너지솔루션",
            "028260": "삼성물산",
            "096770": "SK이노베이션",
            "009540": "HD한국조선해양",
            "042660": "한화오션",
        }


@dataclass
class GateResult:
    status: str  # "PASS" | "PASS_WITH_WARNING" | "FAIL"
    pipeline_allowed: bool
    checked_at: str  # ISO timestamp
    expected_ticker_count: int
    market_file_count: int
    investor_file_count: int
    market_latest_date: Optional[str]
    investor_latest_date: Optional[str]
    alignment_status: str  # "CURRENT" | "SOURCE_LAG" | "STALE" | "INVALID"
    market_coverage: Dict[str, Union[int, List[str]]]
    investor_coverage: Dict[str, Union[int, List[str]]]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Union[str, int, bool, Dict[str, str]]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def run_input_integrity_gate(
    raw_dir: Union[str, Path] = DEFAULT_RAW_DIR,
    investor_dir: Union[str, Path] = DEFAULT_INVESTOR_DIR,
    tickers: Optional[Union[Dict[str, str], Sequence[str]]] = None,
    allow_source_lag: bool = False,
    max_source_lag_days: int = 3,
    max_stale_days: int = 7,
    today_date: Optional[str] = None,
) -> GateResult:
    """
    Shadow Pipeline 실행 전 Operational Input Integrity Gate 검증.
    
    Zero Mutation Guarantee: Read-only check.
    """
    raw_path = Path(raw_dir)
    investor_path = Path(investor_dir)

    if tickers is None:
        ticker_map = get_default_tickers()
    elif isinstance(tickers, dict):
        ticker_map = tickers
    else:
        ticker_map = {t: t for t in tickers}

    expected_ticker_codes = list(ticker_map.keys())
    expected_count = len(expected_ticker_codes)

    checked_at = datetime.now(timezone.utc).isoformat()
    if today_date is None:
        today_str = date.today().isoformat()
    else:
        today_str = today_date

    today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
    is_weekend = today_dt.weekday() >= 5  # 5=Sat, 6=Sun

    errors: List[str] = []
    warnings: List[str] = []

    # 1. Coverage tracking
    market_missing: List[str] = []
    investor_missing: List[str] = []
    market_latest_per_ticker: Dict[str, str] = {}
    investor_latest_per_ticker: Dict[str, str] = {}

    # 2. Inspect Market CSVs
    for ticker in expected_ticker_codes:
        m_file = raw_path / f"{ticker}.csv"
        if not m_file.exists():
            market_missing.append(ticker)
            errors.append(f"MARKET_FILE_MISSING: market file missing for ticker {ticker}")
            continue

        if m_file.stat().st_size == 0:
            errors.append(f"EMPTY_FILE: market file empty for ticker {ticker}")
            continue

        try:
            df_m = pd.read_csv(m_file)
        except Exception as e:
            errors.append(f"MARKET_SCHEMA_INVALID: cannot parse market CSV for ticker {ticker} ({e})")
            continue

        if len(df_m) == 0:
            errors.append(f"EMPTY_FILE: market file has 0 rows for ticker {ticker}")
            continue

        missing_cols = [c for c in MARKET_REQUIRED_COLUMNS if c not in df_m.columns]
        if missing_cols:
            errors.append(
                f"MARKET_SCHEMA_INVALID: market file for ticker {ticker} missing columns {missing_cols}"
            )
            continue

        # Date validation
        dates_ser = df_m["date"]
        if dates_ser.isna().any():
            errors.append(f"INVALID_DATE: market file for ticker {ticker} contains null date")

        # Parse date format YYYY-MM-DD
        parsed_dates = pd.to_datetime(dates_ser, format="%Y-%m-%d", errors="coerce")
        if parsed_dates.isna().any():
            errors.append(f"INVALID_DATE: market file for ticker {ticker} contains invalid date format")

        if dates_ser.duplicated().any():
            errors.append(f"DUPLICATE_DATE: market file for ticker {ticker} contains duplicate dates")

        if not parsed_dates.is_monotonic_increasing:
            errors.append(f"DATE_ORDER_INVALID: market file for ticker {ticker} dates not strictly ascending")

        # Future date check
        future_dates = parsed_dates[parsed_dates > pd.to_datetime(today_str)]
        if not future_dates.empty:
            fd_str = future_dates.iloc[0].strftime("%Y-%m-%d")
            errors.append(f"FUTURE_DATE: market file for ticker {ticker} contains future date {fd_str}")

        # Numeric OHLCV validation
        num_err = False
        for num_col in MARKET_NUMERIC_COLUMNS:
            num_ser = pd.to_numeric(df_m[num_col], errors="coerce")
            if num_ser.isna().any():
                errors.append(
                    f"NUMERIC_INVALID: market file for ticker {ticker} col {num_col} contains non-numeric/null"
                )
                num_err = True

        if not num_err:
            open_ser = pd.to_numeric(df_m["open"])
            high_ser = pd.to_numeric(df_m["high"])
            low_ser = pd.to_numeric(df_m["low"])
            close_ser = pd.to_numeric(df_m["close"])
            vol_ser = pd.to_numeric(df_m["volume"])

            if (close_ser <= 0).any() or (open_ser <= 0).any():
                errors.append(f"NUMERIC_INVALID: market file for ticker {ticker} contains non-positive price")
            if (vol_ser < 0).any():
                errors.append(f"NUMERIC_INVALID: market file for ticker {ticker} contains negative volume")

            # Severe OHLC violations (high < low, low > open, low > close)
            severe_ohlc = (high_ser < low_ser) | (low_ser > open_ser) | (low_ser > close_ser)
            if severe_ohlc.any():
                errors.append(f"NUMERIC_INVALID: market file for ticker {ticker} fails severe OHLC relationship")
            else:
                # High vs Open/Close relationship with rounding tolerance
                # If high < close or high < open by more than 0.5% -> ERROR (NUMERIC_INVALID)
                # If high < close or high < open by <= 0.5% (adjusted price rounding artifact) -> WARNING
                high_diff_close = close_ser - high_ser
                high_diff_open = open_ser - high_ser

                major_high_invalid = (high_diff_close > close_ser * 0.005) | (high_diff_open > open_ser * 0.005)
                minor_high_invalid = (high_diff_close > 0) | (high_diff_open > 0)

                if major_high_invalid.any():
                    errors.append(f"NUMERIC_INVALID: market file for ticker {ticker} high price significantly below open/close")
                elif minor_high_invalid.any():
                    bad_dates = df_m.loc[minor_high_invalid, "date"].tolist()
                    warnings.append(
                        f"NUMERIC_WARNING: market file for ticker {ticker} has minor adjusted-price rounding artifact on {bad_dates}"
                    )

        # Latest date
        latest_d = str(df_m["date"].iloc[-1])
        market_latest_per_ticker[ticker] = latest_d

    # 3. Inspect Investor CSVs
    for ticker in expected_ticker_codes:
        i_file = investor_path / f"{ticker}_investor.csv"
        if not i_file.exists():
            investor_missing.append(ticker)
            errors.append(f"INVESTOR_FILE_MISSING: investor file missing for ticker {ticker}")
            continue

        if i_file.stat().st_size == 0:
            errors.append(f"EMPTY_FILE: investor file empty for ticker {ticker}")
            continue

        try:
            df_i = pd.read_csv(i_file)
        except Exception as e:
            errors.append(f"INVESTOR_SCHEMA_INVALID: cannot parse investor CSV for ticker {ticker} ({e})")
            continue

        if len(df_i) == 0:
            errors.append(f"EMPTY_FILE: investor file has 0 rows for ticker {ticker}")
            continue

        missing_cols = [c for c in INVESTOR_REQUIRED_COLUMNS if c not in df_i.columns]
        if missing_cols:
            errors.append(
                f"INVESTOR_SCHEMA_INVALID: investor file for ticker {ticker} missing columns {missing_cols}"
            )
            continue

        # Date validation
        dates_ser = df_i["date"]
        if dates_ser.isna().any():
            errors.append(f"INVALID_DATE: investor file for ticker {ticker} contains null date")

        parsed_dates = pd.to_datetime(dates_ser, format="%Y-%m-%d", errors="coerce")
        if parsed_dates.isna().any():
            errors.append(f"INVALID_DATE: investor file for ticker {ticker} contains invalid date format")

        if dates_ser.duplicated().any():
            errors.append(f"DUPLICATE_DATE: investor file for ticker {ticker} contains duplicate dates")

        if not parsed_dates.is_monotonic_increasing:
            errors.append(f"DATE_ORDER_INVALID: investor file for ticker {ticker} dates not strictly ascending")

        future_dates = parsed_dates[parsed_dates > pd.to_datetime(today_str)]
        if not future_dates.empty:
            fd_str = future_dates.iloc[0].strftime("%Y-%m-%d")
            errors.append(f"FUTURE_DATE: investor file for ticker {ticker} contains future date {fd_str}")

        # Numeric validation
        for num_col in INVESTOR_NUMERIC_COLUMNS:
            num_ser = pd.to_numeric(df_i[num_col], errors="coerce")
            if num_ser.isna().any():
                errors.append(
                    f"NUMERIC_INVALID: investor file for ticker {ticker} col {num_col} contains non-numeric/null"
                )

        latest_d = str(df_i["date"].iloc[-1])
        investor_latest_per_ticker[ticker] = latest_d

    market_file_count = expected_count - len(market_missing)
    investor_file_count = expected_count - len(investor_missing)

    market_coverage = {
        "expected": expected_count,
        "found": market_file_count,
        "missing": market_missing,
    }
    investor_coverage = {
        "expected": expected_count,
        "found": investor_file_count,
        "missing": investor_missing,
    }

    # 4. Cross-Ticker Consistency
    market_latest_date: Optional[str] = None
    if market_latest_per_ticker:
        unique_m_dates = set(market_latest_per_ticker.values())
        if len(unique_m_dates) > 1:
            errors.append(
                f"MARKET_PARTIAL_DATE: market ticker latest dates are non-uniform ({sorted(unique_m_dates)})"
            )
        else:
            market_latest_date = next(iter(unique_m_dates))

    investor_latest_date: Optional[str] = None
    if investor_latest_per_ticker:
        unique_i_dates = set(investor_latest_per_ticker.values())
        if len(unique_i_dates) > 1:
            errors.append(
                f"INVESTOR_PARTIAL_DATE: investor ticker latest dates are non-uniform ({sorted(unique_i_dates)})"
            )
        else:
            investor_latest_date = next(iter(unique_i_dates))

    # 5. Market / Investor Alignment & Freshness
    alignment_status = "INVALID"
    if market_latest_date and investor_latest_date:
        m_dt = datetime.strptime(market_latest_date, "%Y-%m-%d").date()
        i_dt = datetime.strptime(investor_latest_date, "%Y-%m-%d").date()
        cal_gap = (m_dt - i_dt).days

        if market_latest_date == investor_latest_date:
            alignment_status = "CURRENT"
        elif i_dt < m_dt:
            if cal_gap <= max_source_lag_days and allow_source_lag:
                alignment_status = "SOURCE_LAG"
                warnings.append(
                    f"SOURCE_LAG: investor date ({investor_latest_date}) lags market date ({market_latest_date}) by {cal_gap} days (allowed)"
                )
            else:
                alignment_status = "SOURCE_LAG"
                errors.append(
                    f"MARKET_INVESTOR_MISALIGNED: investor date ({investor_latest_date}) lags market date ({market_latest_date}) by {cal_gap} days"
                )
        else:
            alignment_status = "INVALID"
            errors.append(
                f"MARKET_INVESTOR_MISALIGNED: investor date ({investor_latest_date}) is ahead of market date ({market_latest_date})"
            )
    else:
        alignment_status = "INVALID"

    # Freshness Check against today_date
    if market_latest_date:
        m_dt = datetime.strptime(market_latest_date, "%Y-%m-%d").date()
        stale_days = (today_dt - m_dt).days
        if stale_days > max_stale_days:
            alignment_status = "STALE"
            errors.append(
                f"STALE_INPUT: market latest date ({market_latest_date}) is older than max stale threshold ({max_stale_days} days) relative to today ({today_str})"
            )
        elif stale_days > 3 and not is_weekend:
            warnings.append(
                f"FRESHNESS_WARNING: market latest date ({market_latest_date}) is {stale_days} calendar days behind today ({today_str})"
            )

    # 6. Final Status & pipeline_allowed determination
    if errors:
        status = "FAIL"
        pipeline_allowed = False
    elif warnings:
        status = "PASS_WITH_WARNING"
        pipeline_allowed = True
    else:
        status = "PASS"
        pipeline_allowed = True

    details = {
        "today_date": today_str,
        "is_weekend": is_weekend,
        "allow_source_lag": allow_source_lag,
        "max_source_lag_days": max_source_lag_days,
        "max_stale_days": max_stale_days,
        "market_latest_dates_sample": market_latest_per_ticker,
        "investor_latest_dates_sample": investor_latest_per_ticker,
    }

    return GateResult(
        status=status,
        pipeline_allowed=pipeline_allowed,
        checked_at=checked_at,
        expected_ticker_count=expected_count,
        market_file_count=market_file_count,
        investor_file_count=investor_file_count,
        market_latest_date=market_latest_date,
        investor_latest_date=investor_latest_date,
        alignment_status=alignment_status,
        market_coverage=market_coverage,
        investor_coverage=investor_coverage,
        errors=errors,
        warnings=warnings,
        details=details,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Operational Input Integrity Gate")
    parser.add_argument("--raw-dir", type=str, default=str(DEFAULT_RAW_DIR), help="Path to raw market CSVs")
    parser.add_argument("--investor-dir", type=str, default=str(DEFAULT_INVESTOR_DIR), help="Path to investor CSVs")
    parser.add_argument("--allow-source-lag", action="store_true", help="Allow investor source lag within policy threshold")
    parser.add_argument("--max-source-lag-days", type=int, default=3, help="Max allowed investor source lag in calendar days")
    parser.add_argument("--max-stale-days", type=int, default=7, help="Max allowed data stale days")
    parser.add_argument("--today-date", type=str, default=None, help="Override today date (YYYY-MM-DD)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args()

    result = run_input_integrity_gate(
        raw_dir=args.raw_dir,
        investor_dir=args.investor_dir,
        allow_source_lag=args.allow_source_lag,
        max_source_lag_days=args.max_source_lag_days,
        max_stale_days=args.max_stale_days,
        today_date=args.today_date,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("=== INPUT INTEGRITY GATE REPORT ===")
        print(f"Status: {result.status}")
        print(f"Pipeline Allowed: {result.pipeline_allowed}")
        print(f"Checked At: {result.checked_at}")
        print(f"Expected Ticker Count: {result.expected_ticker_count}")
        print(f"Market File Count: {result.market_file_count}/{result.expected_ticker_count}")
        print(f"Investor File Count: {result.investor_file_count}/{result.expected_ticker_count}")
        print(f"Market Latest Date: {result.market_latest_date}")
        print(f"Investor Latest Date: {result.investor_latest_date}")
        print(f"Alignment Status: {result.alignment_status}")
        if result.warnings:
            print("\nWarnings:")
            for w in result.warnings:
                print(f"  - {w}")
        if result.errors:
            print("\nErrors:")
            for e in result.errors:
                print(f"  - {e}")

    sys.exit(0 if result.pipeline_allowed else 1)


if __name__ == "__main__":
    main()

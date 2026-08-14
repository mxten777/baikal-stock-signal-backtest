"""
Naver Finance 외국인·기관 수급 데이터 Provider (STEP 8 검증용)

데이터 소스: https://finance.naver.com/item/frgn.naver
단위: 거래량 기준 (주, shares)
항목: 외국인 순매매량, 기관 순매매량
제한: 개인 순매매량 미제공, 거래대금 기준 데이터 미제공

Signal Engine과 연결하지 않는 독립 Provider.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests
from io import StringIO

_NAVER_URL = "https://finance.naver.com/item/frgn.naver"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_REQUEST_DELAY = 0.5  # 서버 부하 방지


def _find_investor_table(tables: list) -> pd.DataFrame | None:
    """테이블 목록에서 날짜/기관/외국인 컬럼을 가진 수급 테이블을 탐색한다."""
    for t in tables:
        # 9컬럼 + 날짜 패턴 존재 여부로 식별
        if t.shape[1] != 9:
            continue
        # 컬럼 이름(MultiIndex 포함)을 flatten해서 검사
        flat_cols = [str(c).lower() for c in t.columns]
        joined = " ".join(flat_cols)
        if "날짜" in joined and ("외국인" in joined or "foreign" in joined):
            return t
    return None


def _fetch_page(ticker: str, page: int) -> pd.DataFrame:
    """Naver Finance frgn.naver 한 페이지 파싱."""
    resp = requests.get(
        _NAVER_URL,
        headers=_HEADERS,
        params={"code": ticker, "page": page},
        timeout=15,
    )
    resp.raise_for_status()

    tables = pd.read_html(StringIO(resp.text))
    raw = _find_investor_table(tables)
    if raw is None:
        return pd.DataFrame()
    raw = raw.copy()
    raw.columns = [
        "date", "close", "change", "change_pct",
        "volume", "institution_net", "foreign_net",
        "foreign_hold", "foreign_hold_pct",
    ]
    # 날짜 형식 행만 유지
    raw = raw.dropna(subset=["date"])
    raw = raw[raw["date"].str.match(r"\d{4}\.\d{2}\.\d{2}", na=False)]
    return raw.reset_index(drop=True)


def fetch_investor_flow(
    ticker: str,
    start_date: str,
    end_date: str,
    max_pages: int = 50,
) -> pd.DataFrame:
    """
    Naver Finance에서 외국인·기관 수급 데이터를 수집한다.

    Args:
        ticker:     종목코드 (예: '005930')
        start_date: 조회 시작일 'YYYY-MM-DD'
        end_date:   조회 종료일 'YYYY-MM-DD'
        max_pages:  최대 페이지 수 (안전 상한)

    Returns:
        DataFrame columns:
            date (datetime64), ticker (str),
            foreign_net_buy (int), institution_net_buy (int)
        날짜 오름차순 정렬.
    """
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    rows: list[pd.DataFrame] = []
    for page in range(1, max_pages + 1):
        df = _fetch_page(ticker, page)
        if df.empty:
            break

        df["_dt"] = pd.to_datetime(df["date"], format="%Y.%m.%d")
        # 조회 범위 이전 데이터가 나타나면 조기 종료
        if df["_dt"].max() < start:
            break

        in_range = df[(df["_dt"] >= start) & (df["_dt"] <= end)]
        if not in_range.empty:
            rows.append(in_range)

        time.sleep(_REQUEST_DELAY)

    if not rows:
        return pd.DataFrame(columns=["date", "ticker", "foreign_net_buy", "institution_net_buy"])

    combined = pd.concat(rows, ignore_index=True)

    result = pd.DataFrame({
        "date": combined["_dt"],
        "ticker": ticker,
        "foreign_net_buy": pd.to_numeric(combined["foreign_net"], errors="coerce").astype("Int64"),
        "institution_net_buy": pd.to_numeric(combined["institution_net"], errors="coerce").astype("Int64"),
    })

    result = result.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    return result


def save_investor_flow(df: pd.DataFrame, output_dir: Path) -> Path:
    """수급 데이터를 CSV로 저장한다."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if df.empty or "ticker" not in df.columns:
        raise ValueError("저장할 데이터가 없거나 ticker 컬럼이 없습니다.")

    ticker = df["ticker"].iloc[0]
    path = output_dir / f"{ticker}_investor.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path

"""
Naver Stock 외국인·기관 수급 데이터 Provider (STEP 8 검증용)

데이터 소스: https://m.stock.naver.com/api/stock/{ticker}/integration
단위: 거래량 기준 (주, shares)
항목: 외국인 순매매량, 기관 순매매량
제한: 개인 순매매량 미제공, 거래대금 기준 데이터 미제공

Signal Engine과 연결하지 않는 독립 Provider.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

_NAVER_URL = "https://m.stock.naver.com/api/stock/{ticker}/integration"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.Series(dtype="datetime64[ns]"),
        "ticker": pd.Series(dtype="object"),
        "foreign_net_buy": pd.Series(dtype="Int64"),
        "institution_net_buy": pd.Series(dtype="Int64"),
    })


def _parse_quantity(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_page(ticker: str, page: int) -> pd.DataFrame:
    """Naver Stock integration API의 수급 데이터를 파싱한다."""
    del page  # 기존 private interface 호환용
    try:
        resp = requests.get(
            _NAVER_URL.format(ticker=ticker),
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (requests.RequestException, TypeError, ValueError):
        return pd.DataFrame(columns=["date", "ticker", "institution_net", "foreign_net"])

    if not isinstance(payload, dict):
        return pd.DataFrame(columns=["date", "ticker", "institution_net", "foreign_net"])
    records = payload.get("dealTrendInfos")
    if not isinstance(records, list):
        return pd.DataFrame(columns=["date", "ticker", "institution_net", "foreign_net"])

    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        rows.append({
            "date": record.get("bizdate"),
            "ticker": record.get("itemCode") or ticker,
            "institution_net": _parse_quantity(record.get("organPureBuyQuant")),
            "foreign_net": _parse_quantity(record.get("foreignerPureBuyQuant")),
        })
    return pd.DataFrame(rows, columns=["date", "ticker", "institution_net", "foreign_net"])


def fetch_investor_flow(
    ticker: str,
    start_date: str,
    end_date: str,
    max_pages: int = 100,
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

    del max_pages  # 신규 API는 수급 목록을 단일 응답으로 제공한다.
    combined = _fetch_page(ticker, 1)
    if combined.empty:
        return _empty_result()

    combined["_dt"] = pd.to_datetime(combined["date"], format="%Y%m%d", errors="coerce")
    combined = combined[(combined["_dt"] >= start) & (combined["_dt"] <= end)]
    if combined.empty:
        return _empty_result()

    result = pd.DataFrame({
        "date": combined["_dt"],
        "ticker": combined["ticker"].astype(str),
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

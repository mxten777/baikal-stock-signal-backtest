"""
STEP 8 — 수급 데이터 Provider 단위 테스트

실제 네트워크 요청 없이 동작하도록 모든 HTTP 호출은 mock 처리.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_provider.naver_investor_provider import (
    _fetch_page,
    fetch_investor_flow,
    save_investor_flow,
)

# ─────────────────────────────────────────────
# 헬퍼 — Mock HTML 생성
# ─────────────────────────────────────────────
_MOCK_HTML_TEMPLATE = """
<html><body>
<table><tr><td>dummy0</td></tr></table>
<table><tr><td>dummy1</td></tr></table>
<table><tr><td>dummy2</td></tr></table>
<table>
  <thead>
    <tr>
      <th colspan="1">날짜</th><th colspan="1">종가</th><th colspan="1">전일비</th>
      <th colspan="1">등락률</th><th colspan="1">거래량</th>
      <th colspan="1">기관</th><th colspan="2">외국인</th><th colspan="1"></th>
    </tr>
    <tr>
      <th>날짜</th><th>종가</th><th>전일비</th><th>등락률</th>
      <th>거래량</th><th>순매매량</th><th>순매매량</th><th>보유주수</th><th>보유율</th>
    </tr>
  </thead>
  <tbody>
    <tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
    <tr><td>2024.01.15</td><td>72000</td><td>상승 500</td><td>+0.70%</td>
        <td>10000000</td><td>500000</td><td>-300000</td><td>2900000000</td><td>48.50%</td></tr>
    <tr><td>2024.01.12</td><td>71500</td><td>하락 200</td><td>-0.28%</td>
        <td>8000000</td><td>-200000</td><td>150000</td><td>2901000000</td><td>48.52%</td></tr>
    <tr><td>2024.01.11</td><td>71700</td><td>상승 100</td><td>+0.14%</td>
        <td>9000000</td><td>100000</td><td>200000</td><td>2900500000</td><td>48.51%</td></tr>
  </tbody>
</table>
</body></html>
"""


def _make_mock_response(html: str = _MOCK_HTML_TEMPLATE) -> MagicMock:
    mock = MagicMock()
    mock.text = html
    mock.raise_for_status = MagicMock()
    return mock


# ─────────────────────────────────────────────
# _fetch_page 단위 테스트
# ─────────────────────────────────────────────
class TestFetchPage:
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_returns_dataframe(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        assert isinstance(df, pd.DataFrame)

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_required_columns_present(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        for col in ["date", "institution_net", "foreign_net"]:
            assert col in df.columns, f"컬럼 누락: {col}"

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_date_format_filter(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        # NaN 행 제거 확인
        assert df["date"].notna().all()
        # 날짜 형식 확인 YYYY.MM.DD
        assert df["date"].str.match(r"\d{4}\.\d{2}\.\d{2}").all()

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_row_count(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        assert len(df) == 3  # Mock HTML에 유효 날짜 행 3개


# ─────────────────────────────────────────────
# fetch_investor_flow 통합 테스트
# ─────────────────────────────────────────────
class TestFetchInvestorFlow:
    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_output_columns(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        for col in ["date", "ticker", "foreign_net_buy", "institution_net_buy"]:
            assert col in df.columns, f"컬럼 누락: {col}"

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_ticker_column_value(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert (df["ticker"] == "005930").all()

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_date_is_datetime(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_date_range_filter(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-12", "2024-01-15")
        assert df["date"].min() >= pd.Timestamp("2024-01-12")
        assert df["date"].max() <= pd.Timestamp("2024-01-15")

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_no_duplicate_dates(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert not df.duplicated(subset="date").any()

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_sorted_ascending(self, mock_get, mock_sleep):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert df["date"].is_monotonic_increasing

    @patch("src.data_provider.naver_investor_provider.time.sleep")
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_empty_on_out_of_range(self, mock_get, mock_sleep):
        """조회 기간에 데이터가 없으면 빈 DataFrame 반환."""
        mock_get.return_value = _make_mock_response()
        # 2024-01-15, 2024-01-12, 2024-01-11 데이터이므로 2020년 범위는 빈 결과
        df = fetch_investor_flow("005930", "2020-01-01", "2020-12-31")
        assert df.empty or len(df) == 0


# ─────────────────────────────────────────────
# save_investor_flow 테스트
# ─────────────────────────────────────────────
class TestSaveInvestorFlow:
    def test_saves_csv(self, tmp_path):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-15", "2024-01-12"]),
            "ticker": "005930",
            "foreign_net_buy": pd.array([-300000, 150000], dtype="Int64"),
            "institution_net_buy": pd.array([500000, -200000], dtype="Int64"),
        })
        path = save_investor_flow(df, tmp_path)
        assert path.exists()
        assert path.name == "005930_investor.csv"

    def test_csv_readable(self, tmp_path):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-15"]),
            "ticker": "005930",
            "foreign_net_buy": pd.array([-300000], dtype="Int64"),
            "institution_net_buy": pd.array([500000], dtype="Int64"),
        })
        path = save_investor_flow(df, tmp_path)
        loaded = pd.read_csv(path)
        assert "foreign_net_buy" in loaded.columns
        assert "institution_net_buy" in loaded.columns

    def test_raises_on_empty(self, tmp_path):
        with pytest.raises(ValueError):
            save_investor_flow(pd.DataFrame(), tmp_path)

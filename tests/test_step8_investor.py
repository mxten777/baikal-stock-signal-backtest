"""
STEP 8 — 수급 데이터 Provider 단위 테스트

실제 네트워크 요청 없이 동작하도록 모든 HTTP 호출은 mock 처리.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from src.data_provider.naver_investor_provider import (
    _fetch_page,
    fetch_investor_flow,
    save_investor_flow,
)

# ─────────────────────────────────────────────
# 헬퍼 — Mock API 응답 생성
# ─────────────────────────────────────────────
_MOCK_PAYLOAD = {
        "dealTrendInfos": [
                {
                        "bizdate": "20240115",
                        "itemCode": "005930",
                        "foreignerPureBuyQuant": "-300,000",
                        "organPureBuyQuant": "+500,000",
                },
                {
                        "bizdate": "20240112",
                        "itemCode": "005930",
                        "foreignerPureBuyQuant": "+150,000",
                        "organPureBuyQuant": "-200,000",
                },
                {
                        "bizdate": "20240111",
                        "itemCode": "005930",
                        "foreignerPureBuyQuant": "+200,000",
                        "organPureBuyQuant": "+100,000",
                },
        ]
}


def _make_mock_response(payload: object = _MOCK_PAYLOAD) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = payload
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
        for col in ["date", "ticker", "institution_net", "foreign_net"]:
            assert col in df.columns, f"컬럼 누락: {col}"

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_date_format_filter(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        assert df["date"].str.fullmatch(r"\d{8}").all()

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_row_count(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = _fetch_page("005930", 1)
        assert len(df) == 3

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_empty_on_http_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("network error")
        assert _fetch_page("005930", 1).empty

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_empty_on_missing_deal_trends(self, mock_get):
        mock_get.return_value = _make_mock_response({})
        assert _fetch_page("005930", 1).empty


# ─────────────────────────────────────────────
# fetch_investor_flow 통합 테스트
# ─────────────────────────────────────────────
class TestFetchInvestorFlow:
    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_output_columns(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert list(df.columns) == [
            "date", "ticker", "foreign_net_buy", "institution_net_buy",
        ]

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_ticker_column_value(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert (df["ticker"] == "005930").all()

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_schema_dtypes(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df["ticker"].map(type).eq(str).all()
        assert str(df["foreign_net_buy"].dtype) == "Int64"
        assert str(df["institution_net_buy"].dtype) == "Int64"

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_date_range_filter(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-12", "2024-01-15")
        assert df["date"].min() >= pd.Timestamp("2024-01-12")
        assert df["date"].max() <= pd.Timestamp("2024-01-15")

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_no_duplicate_dates(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert not df.duplicated(subset="date").any()

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_sorted_ascending(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-01", "2024-01-31")
        assert df["date"].is_monotonic_increasing

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_signed_comma_quantities(self, mock_get):
        mock_get.return_value = _make_mock_response()
        df = fetch_investor_flow("005930", "2024-01-15", "2024-01-15")
        assert df.iloc[0]["foreign_net_buy"] == -300000
        assert df.iloc[0]["institution_net_buy"] == 500000

    @patch("src.data_provider.naver_investor_provider.requests.get")
    def test_empty_on_out_of_range(self, mock_get):
        """조회 기간에 데이터가 없으면 빈 DataFrame 반환."""
        mock_get.return_value = _make_mock_response()
        # 2024-01-15, 2024-01-12, 2024-01-11 데이터이므로 2020년 범위는 빈 결과
        df = fetch_investor_flow("005930", "2020-01-01", "2020-12-31")
        assert df.empty
        assert str(df["date"].dtype) == "datetime64[ns]"
        assert str(df["foreign_net_buy"].dtype) == "Int64"
        assert str(df["institution_net_buy"].dtype) == "Int64"


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

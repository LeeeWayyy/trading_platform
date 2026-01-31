"""Tests for execution quality page in apps/web_console_ng/pages/execution_quality.py.

Tests the TCA dashboard page including data fetching, filtering,
and chart rendering.
"""

from __future__ import annotations

import os

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.web_console_ng.pages.execution_quality import (
    DEFAULT_RANGE_DAYS,
    _fetch_tca_data,
)


class TestFetchTCAData:
    """Tests for _fetch_tca_data function."""

    @pytest.fixture
    def mock_response_data(self) -> dict[str, Any]:
        """Sample TCA API response."""
        return {
            "summary": {
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "total_orders": 50,
                "total_fills": 120,
                "total_notional": 2500000.0,
                "total_shares": 25000,
                "avg_fill_rate": 0.96,
                "avg_implementation_shortfall_bps": 1.8,
                "avg_price_shortfall_bps": 1.2,
                "avg_vwap_slippage_bps": 0.8,
                "avg_fee_cost_bps": 0.4,
                "avg_opportunity_cost_bps": 0.3,
                "avg_market_impact_bps": 0.9,
                "avg_timing_cost_bps": 0.4,
                "warnings": [],
            },
            "orders": [
                {
                    "client_order_id": "order-1",
                    "symbol": "AAPL",
                    "side": "buy",
                    "execution_date": "2024-01-15",
                    "implementation_shortfall_bps": 2.0,
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_response_data: dict[str, Any]) -> None:
        """Successful fetch returns TCA data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_response_data

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

        assert result is not None
        assert "summary" in result
        assert "orders" in result
        assert result["summary"]["total_orders"] == 50

    @pytest.mark.asyncio
    async def test_fetch_with_filters(self, mock_response_data: dict[str, Any]) -> None:
        """Fetch with symbol and side filters."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_response_data

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol="AAPL",
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

            # Check that filters were passed in query params
            call_args = mock_instance.get.call_args
            params = call_args.kwargs.get("params", {})
            assert params.get("symbol") == "AAPL"

        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_api_error(self) -> None:
        """API error returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_connection_error(self) -> None:
        """Connection error returns None."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError("Connection refused")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_sends_auth_headers(
        self, mock_response_data: dict[str, Any]
    ) -> None:
        """Fetch sends correct authentication headers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_response_data

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="admin",
                strategies=["strat1", "strat2"],
            )

            call_args = mock_instance.get.call_args
            headers = call_args.kwargs.get("headers", {})
            assert headers["X-User-ID"] == "test_user"
            assert headers["X-User-Role"] == "admin"
            assert headers["X-User-Strategies"] == "strat1,strat2"


class TestDefaultDateRange:
    """Tests for default date range constant."""

    def test_default_range_is_reasonable(self) -> None:
        """Default date range is reasonable for TCA analysis."""
        assert DEFAULT_RANGE_DAYS >= 7  # At least a week
        assert DEFAULT_RANGE_DAYS <= 90  # Within API max


class TestExecutionQualityPageIntegration:
    """Integration tests for the execution quality page."""

    @pytest.mark.asyncio
    async def test_page_handles_no_data(self) -> None:
        """Page handles case when no TCA data is available."""
        # This would be a full page render test with mocked dependencies
        # For now, verify the data fetching handles empty response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "summary": {
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "total_orders": 0,
                "total_fills": 0,
                "total_notional": 0,
                "total_shares": 0,
                "avg_fill_rate": 0,
                "avg_implementation_shortfall_bps": 0,
                "avg_price_shortfall_bps": 0,
                "avg_vwap_slippage_bps": 0,
                "avg_fee_cost_bps": 0,
                "avg_opportunity_cost_bps": 0,
                "avg_market_impact_bps": 0,
                "avg_timing_cost_bps": 0,
                "warnings": ["No orders in selected date range"],
            },
            "orders": [],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is not None
        assert result["summary"]["total_orders"] == 0
        assert len(result["orders"]) == 0

    @pytest.mark.asyncio
    async def test_page_handles_strategy_filter(self) -> None:
        """Page correctly passes strategy filter to API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"summary": {}, "orders": []}

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id="alpha_baseline",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

            call_args = mock_instance.get.call_args
            params = call_args.kwargs.get("params", {})
            assert params.get("strategy_id") == "alpha_baseline"

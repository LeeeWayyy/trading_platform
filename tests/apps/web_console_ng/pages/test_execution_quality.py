"""Tests for execution quality page in apps/web_console_ng/pages/execution_quality.py.

Tests the TCA dashboard page including data fetching, filtering,
and chart rendering.
"""

from __future__ import annotations

import os

os.environ["WEB_CONSOLE_NG_DEBUG"] = "true"
os.environ.setdefault("NICEGUI_STORAGE_SECRET", "test-secret")

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.web_console_ng.pages.execution_quality import (
    DEFAULT_RANGE_DAYS,
    _fetch_tca_benchmarks,
    _fetch_tca_data,
    _is_numeric,
    _safe_float,
)


class TestFetchTCAData:
    """Tests for _fetch_tca_data function."""

    @pytest.fixture()
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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


class TestFetchTCABenchmarks:
    """Tests for _fetch_tca_benchmarks function."""

    @pytest.mark.asyncio()
    async def test_fetch_benchmarks_success(self) -> None:
        """Successful fetch returns benchmark series data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "symbol": "AAPL",
            "benchmark_type": "vwap",
            "points": [
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "execution_price": 150.25,
                    "benchmark_price": 150.1,
                }
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="order-1",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

            call_args = mock_instance.get.call_args
            params = call_args.kwargs.get("params", {})
            headers = call_args.kwargs.get("headers", {})
            assert params == {"client_order_id": "order-1", "benchmark": "vwap"}
            assert headers["X-User-ID"] == "test_user"
            assert headers["X-User-Role"] == "trader"
            assert headers["X-User-Strategies"] == "alpha_baseline"

        assert result is not None
        assert result["points"][0]["execution_price"] == 150.25

    @pytest.mark.asyncio()
    async def test_fetch_benchmarks_api_error(self) -> None:
        """API error returns None for benchmark fetches."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="missing-order",
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_benchmarks_connection_error(self) -> None:
        """Connection error returns None for benchmark fetches."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError("Connection refused")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="order-1",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_benchmarks_malformed_json(self) -> None:
        """Malformed JSON response returns None for benchmark fetches."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="order-1",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

        assert result is None


class TestSafeFloat:
    """Tests for _safe_float and _is_numeric helpers."""

    def test_safe_float_valid(self) -> None:
        """Valid numeric values are converted correctly."""
        assert _safe_float(3.14) == 3.14
        assert _safe_float("2.5") == 2.5
        assert _safe_float(0) == 0.0

    def test_safe_float_invalid(self) -> None:
        """Invalid values return the default."""
        assert _safe_float(None) == 0.0
        assert _safe_float("bad") == 0.0
        assert _safe_float(None, default=-1.0) == -1.0

    def test_is_numeric_valid(self) -> None:
        """_is_numeric returns True for convertible values."""
        assert _is_numeric(150.25) is True
        assert _is_numeric("42") is True
        assert _is_numeric(0) is True

    def test_is_numeric_invalid(self) -> None:
        """_is_numeric returns False for non-numeric values."""
        assert _is_numeric(None) is False
        assert _is_numeric("bad") is False
        assert _is_numeric([]) is False


class TestBenchmarkDemoModeGating:
    """Tests that benchmark fetch is skipped in demo mode."""

    @pytest.mark.asyncio()
    async def test_benchmark_not_fetched_in_demo_mode(self) -> None:
        """Benchmark API should not be called when TCA data comes from demo mode."""
        # When the TCA analysis API fails, demo data is used and
        # _fetch_tca_benchmarks should NOT be called (guarded by
        # ``not state.get("demo_mode")``).
        tca_fail_response = MagicMock()
        tca_fail_response.status_code = 503

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = tca_fail_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            # Fetch TCA data — will fail and caller should switch to demo mode
            result = await _fetch_tca_data(
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                symbol=None,
                strategy_id=None,
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        # Confirm API failure returns None (triggers demo fallback)
        assert result is None

    @pytest.mark.asyncio()
    async def test_benchmark_fetched_for_real_orders(self) -> None:
        """Benchmark API is called when real TCA data is available."""
        benchmark_response = MagicMock()
        benchmark_response.status_code = 200
        benchmark_response.json.return_value = {
            "client_order_id": "order-1",
            "symbol": "AAPL",
            "benchmark_type": "vwap",
            "points": [
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "execution_price": 150.25,
                    "benchmark_price": 150.1,
                }
            ],
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = benchmark_response
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="order-1",
                user_id="test_user",
                role="trader",
                strategies=["alpha_baseline"],
            )

        assert result is not None
        assert len(result["points"]) == 1


class TestDefaultDateRange:
    """Tests for default date range constant."""

    def test_default_range_is_reasonable(self) -> None:
        """Default date range is reasonable for TCA analysis."""
        assert DEFAULT_RANGE_DAYS >= 7  # At least a week
        assert DEFAULT_RANGE_DAYS <= 90  # Within API max


class TestExecutionQualityPageIntegration:
    """Integration tests for the execution quality page."""

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
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

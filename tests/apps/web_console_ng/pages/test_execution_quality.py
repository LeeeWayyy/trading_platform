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
    _format_benchmark_timestamp,
    _is_valid_price,
    _is_valid_timestamp,
    _parse_utc,
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
    async def test_fetch_success(self) -> None:
        """Successful fetch returns benchmark data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "symbol": "AAPL",
            "benchmark_type": "vwap",
            "points": [
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "execution_price": 150.1,
                    "benchmark_price": 150.0,
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
        assert result["client_order_id"] == "order-1"

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_when_no_points(self) -> None:
        """Empty benchmark series returns None so UI hides chart."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "points": [],
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
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_on_error(self) -> None:
        """API error returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 500

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
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_on_request_error(self) -> None:
        """Network-level error returns None (httpx.RequestError)."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.ConnectError("connection refused")
            mock_instance.__aenter__.return_value = mock_instance
            mock_instance.__aexit__.return_value = None
            mock_client.return_value = mock_instance

            result = await _fetch_tca_benchmarks(
                client_order_id="order-1",
                user_id="test_user",
                role="trader",
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_on_malformed_json(self) -> None:
        """Malformed JSON body on 200 returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("invalid json")

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
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_filters_non_dict_points(self) -> None:
        """Non-dict entries in points are filtered out (schema drift guard)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "points": [
                "not-a-dict",
                {
                    "timestamp": "2024-01-15T10:00:00Z",
                    "execution_price": 150.1,
                    "benchmark_price": 150.0,
                },
                42,
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
                strategies=[],
            )

        assert result is not None
        # Only the valid dict entry should remain
        assert len(result["points"]) == 1
        assert result["points"][0]["execution_price"] == 150.1

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_when_all_points_non_dict(self) -> None:
        """Returns None when all points entries are non-dict."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "points": ["bad", 123, None],
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
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_when_json_is_not_dict(self) -> None:
        """Non-dict top-level JSON (e.g. list) returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = ["unexpected", "list"]

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
                strategies=[],
            )

        assert result is None

    @pytest.mark.asyncio()
    async def test_fetch_returns_none_when_points_not_list(self) -> None:
        """Non-list 'points' field returns None and logs warning."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "client_order_id": "order-1",
            "points": "not-a-list",
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
                strategies=[],
            )

        assert result is None


class TestBenchmarkHelpers:
    """Tests for module-level benchmark transformation helpers."""

    def test_parse_utc_zulu(self) -> None:
        """Zulu suffix is parsed as UTC."""
        dt = _parse_utc("2024-01-15T10:30:00Z")
        assert dt.strftime("%H:%M") == "10:30"
        assert dt.tzinfo is not None

    def test_parse_utc_offset(self) -> None:
        """Explicit offset is normalized to UTC."""
        dt = _parse_utc("2024-01-15T15:30:00-05:00")
        assert dt.strftime("%H:%M") == "20:30"

    def test_parse_utc_naive(self) -> None:
        """Naive datetime is assumed UTC (no local-timezone shift)."""
        dt = _parse_utc("2024-01-15T10:30:00")
        assert dt.strftime("%H:%M") == "10:30"

    def test_parse_utc_invalid(self) -> None:
        """Invalid string returns datetime.min (UTC)."""
        dt = _parse_utc("not-a-date")
        assert dt.year == 1
        assert dt.month == 1
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_format_benchmark_timestamp_zulu(self) -> None:
        """Zulu timestamp formatted as HH:MM:SS UTC."""
        assert _format_benchmark_timestamp("2024-01-15T10:30:00Z") == "10:30:00 UTC"

    def test_format_benchmark_timestamp_invalid(self) -> None:
        """Invalid timestamp returned as-is."""
        assert _format_benchmark_timestamp("bad") == "bad"

    def test_format_benchmark_timestamp_with_offset(self) -> None:
        """Offset timestamp converted to UTC before formatting."""
        assert _format_benchmark_timestamp("2024-01-15T15:30:00-05:00") == "20:30:00 UTC"

    def test_format_benchmark_timestamp_include_date(self) -> None:
        """Include date when requested for multi-day fills."""
        result = _format_benchmark_timestamp(
            "2024-01-15T10:30:00Z", include_date=True
        )
        assert result == "2024-01-15 10:30:00 UTC"

    def test_is_valid_price_positive(self) -> None:
        """Positive float is valid."""
        assert _is_valid_price(150.5) is True

    def test_is_valid_price_zero(self) -> None:
        """Zero is not valid (used to detect missing data)."""
        assert _is_valid_price(0.0) is False
        assert _is_valid_price(0) is False

    def test_is_valid_price_none(self) -> None:
        """None is not valid."""
        assert _is_valid_price(None) is False

    def test_is_valid_price_string(self) -> None:
        """Non-numeric string is not valid."""
        assert _is_valid_price("abc") is False

    def test_is_valid_price_numeric_string(self) -> None:
        """Numeric string is valid."""
        assert _is_valid_price("150.5") is True

    def test_is_valid_price_negative(self) -> None:
        """Negative price is not valid."""
        assert _is_valid_price(-10.0) is False

    def test_is_valid_price_boolean(self) -> None:
        """Booleans are not valid prices (float(True) == 1.0)."""
        assert _is_valid_price(True) is False
        assert _is_valid_price(False) is False

    def test_is_valid_price_nan(self) -> None:
        """NaN is not valid."""
        assert _is_valid_price(float("nan")) is False

    def test_is_valid_price_inf(self) -> None:
        """Infinity is not valid."""
        assert _is_valid_price(float("inf")) is False
        assert _is_valid_price(float("-inf")) is False

    def test_is_valid_timestamp_valid(self) -> None:
        """Valid ISO timestamp returns True."""
        assert _is_valid_timestamp("2024-01-15T10:00:00Z") is True

    def test_is_valid_timestamp_invalid(self) -> None:
        """Non-parseable string returns False."""
        assert _is_valid_timestamp("not-a-date") is False

    def test_is_valid_timestamp_none(self) -> None:
        """None returns False."""
        assert _is_valid_timestamp(None) is False

    def test_sort_with_mixed_offsets(self) -> None:
        """Points with different timezone offsets sort chronologically."""
        points = [
            {"timestamp": "2024-01-15T15:30:00-05:00", "val": "later"},  # 20:30 UTC
            {"timestamp": "2024-01-15T10:00:00Z", "val": "earlier"},  # 10:00 UTC
        ]
        points.sort(key=lambda p: _parse_utc(p["timestamp"]))
        assert points[0]["val"] == "earlier"
        assert points[1]["val"] == "later"


class TestDefaultDateRange:
    """Tests for default date range constant."""

    def test_default_range_is_reasonable(self) -> None:
        """Default date range is reasonable for TCA analysis."""
        assert DEFAULT_RANGE_DAYS >= 7  # At least a week
        assert DEFAULT_RANGE_DAYS <= 90  # Within API max


class TestExecutionQualityPageIntegration:
    """Integration tests for the execution quality page.

    NOTE: Full render-path tests (benchmark chart shown/hidden/demo,
    stale-load discard via ``_load_version``) require a running NiceGUI
    server and are not feasible in unit-test scope.  The helpers that
    drive those render branches (``_parse_utc``, ``_is_valid_price``,
    ``_is_valid_timestamp``, ``_format_benchmark_timestamp``,
    ``_fetch_tca_benchmarks``) are thoroughly covered above.
    """

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

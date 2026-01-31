"""Tests for TCA routes in apps/execution_gateway/routes/tca.py.

Tests the TCA analysis endpoints including parameter validation
and response format using the demo data generators.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.dependencies import get_context
from apps.execution_gateway.routes import tca
from apps.execution_gateway.services.auth_helpers import build_user_context
from libs.core.common.api_auth_dependency import AuthContext


def create_mock_user_context() -> dict[str, Any]:
    """Create mock user context for testing."""
    mock_user = MagicMock()
    mock_user.role = "trader"
    return {
        "user_id": "test_user",
        "user": mock_user,
        "role": "trader",
    }


def create_mock_app_context() -> AppContext:
    """Create mock app context for testing."""
    ctx = MagicMock(spec=AppContext)
    return ctx


def create_mock_auth_context() -> AuthContext:
    """Create mock auth context."""
    ctx = MagicMock(spec=AuthContext)
    ctx.user_id = "test_user"
    ctx.user = MagicMock()
    ctx.user.role = "trader"
    return ctx


@pytest.fixture
def test_client() -> TestClient:
    """Create test client with mocked dependencies."""
    app = FastAPI()
    app.include_router(tca.router)

    # Override all dependencies
    app.dependency_overrides[tca.tca_auth] = create_mock_auth_context
    app.dependency_overrides[get_context] = create_mock_app_context
    app.dependency_overrides[build_user_context] = create_mock_user_context

    return TestClient(app)


class TestGetTCAAnalysis:
    """Tests for GET /api/v1/tca/analysis endpoint."""

    def test_analysis_valid_request(self, test_client: TestClient) -> None:
        """Valid analysis request returns summary and orders."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "orders" in data
        assert data["summary"]["start_date"] == str(start)
        assert data["summary"]["end_date"] == str(today)

    def test_analysis_with_symbol_filter(self, test_client: TestClient) -> None:
        """Analysis with symbol filter."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
                "symbol": "AAPL",
            },
        )

        assert response.status_code == 200

    def test_analysis_with_side_filter(self, test_client: TestClient) -> None:
        """Analysis with side filter."""
        today = date.today()
        start = today - timedelta(days=7)

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(start),
                "end_date": str(today),
                "side": "buy",
            },
        )

        assert response.status_code == 200

    def test_analysis_invalid_date_range(self, test_client: TestClient) -> None:
        """End date before start date returns 400."""
        today = date.today()

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(today),
                "end_date": str(today - timedelta(days=7)),
            },
        )

        assert response.status_code == 400
        assert "end_date must be >= start_date" in response.json()["detail"]

    def test_analysis_date_range_exceeds_max(self, test_client: TestClient) -> None:
        """Date range > 90 days returns 400."""
        today = date.today()

        response = test_client.get(
            "/api/v1/tca/analysis",
            params={
                "start_date": str(today - timedelta(days=100)),
                "end_date": str(today),
            },
        )

        assert response.status_code == 400
        assert "cannot exceed" in response.json()["detail"]


class TestGetOrderTCA:
    """Tests for GET /api/v1/tca/analysis/{client_order_id} endpoint."""

    def test_order_tca_valid_request(self, test_client: TestClient) -> None:
        """Valid order TCA request returns order detail."""
        response = test_client.get("/api/v1/tca/analysis/order-123")

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "order-123"
        assert "implementation_shortfall_bps" in data
        assert "vwap_slippage_bps" in data


class TestGetBenchmarks:
    """Tests for GET /api/v1/tca/benchmarks endpoint."""

    def test_benchmarks_vwap(self, test_client: TestClient) -> None:
        """Get VWAP benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "vwap",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["client_order_id"] == "order-123"
        assert data["benchmark_type"] == "vwap"
        assert "points" in data

    def test_benchmarks_twap(self, test_client: TestClient) -> None:
        """Get TWAP benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "twap",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "twap"

    def test_benchmarks_arrival(self, test_client: TestClient) -> None:
        """Get arrival price benchmark comparison."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={
                "client_order_id": "order-123",
                "benchmark": "arrival",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "arrival"

    def test_benchmarks_default_vwap(self, test_client: TestClient) -> None:
        """Default benchmark type is VWAP."""
        response = test_client.get(
            "/api/v1/tca/benchmarks",
            params={"client_order_id": "order-123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["benchmark_type"] == "vwap"


class TestDemoDataGenerators:
    """Tests for internal demo data generator functions."""

    def test_generate_demo_summary(self) -> None:
        """Demo summary generator produces valid data."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        summary = tca._generate_demo_summary(start, end)

        assert summary.start_date == start
        assert summary.end_date == end
        assert summary.total_orders > 0
        assert 0 <= summary.avg_fill_rate <= 1
        assert summary.computation_timestamp is not None

    def test_generate_demo_summary_with_filters(self) -> None:
        """Demo summary generator with symbol and strategy filters."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        summary = tca._generate_demo_summary(
            start, end, symbol="AAPL", strategy_id="alpha_baseline"
        )

        assert summary.start_date == start
        assert summary.total_orders > 0

    def test_generate_demo_orders(self) -> None:
        """Demo orders generator produces valid order details."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        orders = tca._generate_demo_orders(start, end, 10)

        assert len(orders) <= 10
        for order in orders:
            assert order.symbol in ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
            assert order.side in ["buy", "sell"]
            assert 0 <= order.fill_rate <= 1
            assert order.num_fills >= 1

    def test_generate_demo_orders_with_symbol(self) -> None:
        """Demo orders generator with symbol filter."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        orders = tca._generate_demo_orders(start, end, 10, symbol="AAPL")

        for order in orders:
            assert order.symbol == "AAPL"

    def test_generate_demo_orders_capped_at_50(self) -> None:
        """Demo orders generator caps at 50 orders."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)

        orders = tca._generate_demo_orders(start, end, 100)

        assert len(orders) <= 50

    def test_generate_demo_benchmarks(self) -> None:
        """Demo benchmarks generator produces valid time series."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        orders = tca._generate_demo_orders(start, end, 1)

        if orders:
            order = orders[0]
            points = tca._generate_demo_benchmarks(order, "vwap")

            assert len(points) > 0
            for point in points:
                assert point.benchmark_type == "vwap"
                assert point.cumulative_qty >= 0

    def test_generate_demo_benchmarks_twap(self) -> None:
        """Demo benchmarks for TWAP type."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        orders = tca._generate_demo_orders(start, end, 1)

        if orders:
            order = orders[0]
            points = tca._generate_demo_benchmarks(order, "twap")

            for point in points:
                assert point.benchmark_type == "twap"

    def test_generate_demo_benchmarks_arrival(self) -> None:
        """Demo benchmarks for arrival type."""
        start = date(2024, 1, 1)
        end = date(2024, 1, 31)
        orders = tca._generate_demo_orders(start, end, 1)

        if orders:
            order = orders[0]
            points = tca._generate_demo_benchmarks(order, "arrival")

            for point in points:
                assert point.benchmark_type == "arrival"

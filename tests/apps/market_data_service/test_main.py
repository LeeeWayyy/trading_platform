"""
Unit tests for Market Data Service FastAPI endpoints.

Tests cover:
- Health check endpoint (healthy/degraded/unavailable states)
- Subscribe endpoint (success/errors/validation)
- Unsubscribe endpoint (success/errors)
- Get subscriptions endpoint
- Get subscription stats endpoint
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from libs.market_data import SubscriptionError


@pytest.fixture
def test_client(monkeypatch):
    """Create FastAPI test client with mocked lifespan to avoid external dependencies."""
    # Set required environment variables before importing (using monkeypatch to avoid test pollution)
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

    # Create a mock lifespan that doesn't connect to external services
    @asynccontextmanager
    async def mock_lifespan(app):
        """Mock lifespan that skips Redis and Alpaca connections."""
        yield

    # Patch the lifespan before importing the app
    with patch("apps.market_data_service.main.lifespan", mock_lifespan):
        from apps.market_data_service.main import app

        return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_stream():
    """Create mock AlpacaMarketDataStream."""
    mock = Mock()
    # Mock async methods
    mock.subscribe_symbols = AsyncMock()
    mock.unsubscribe_symbols = AsyncMock()
    # Mock sync methods
    mock.get_connection_stats = Mock()
    mock.get_subscribed_symbols = Mock()
    return mock


@pytest.fixture
def mock_subscription_manager():
    """Create mock PositionBasedSubscription."""
    mock = Mock()
    mock.get_stats = Mock()
    return mock


class TestHealthCheckEndpoint:
    """Tests for health check endpoint."""

    def test_health_check_stream_not_initialized(self, test_client):
        """Test health check returns 503 when stream not initialized."""
        with patch("apps.market_data_service.main.stream", None):
            response = test_client.get("/health")

        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]

    def test_health_check_healthy_when_connected(self, test_client, mock_stream):
        """Test health check returns healthy when WebSocket connected."""
        mock_stream.get_connection_stats.return_value = {
            "is_connected": True,
            "subscribed_symbols": 5,
            "reconnect_attempts": 0,
            "max_reconnect_attempts": 3,
        }

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["websocket_connected"] is True
        assert data["subscribed_symbols"] == 5
        assert data["reconnect_attempts"] == 0
        assert data["max_reconnect_attempts"] == 3

    def test_health_check_degraded_when_disconnected(self, test_client, mock_stream):
        """Test health check returns degraded when WebSocket disconnected."""
        mock_stream.get_connection_stats.return_value = {
            "is_connected": False,
            "subscribed_symbols": 0,
            "reconnect_attempts": 2,
            "max_reconnect_attempts": 3,
        }

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["websocket_connected"] is False
        assert data["reconnect_attempts"] == 2


class TestSubscribeEndpoint:
    """Tests for subscribe endpoint."""

    def test_subscribe_stream_not_initialized(self, test_client):
        """Test subscribe returns 503 when stream not initialized."""
        with patch("apps.market_data_service.main.stream", None):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]

    def test_subscribe_no_symbols_provided(self, test_client, mock_stream):
        """Test subscribe returns 400 when no symbols provided."""
        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": []},
            )

        assert response.status_code == 400
        assert "No symbols provided" in response.json()["detail"]

    def test_subscribe_success(self, test_client, mock_stream):
        """Test successful subscription to symbols."""
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL", "MSFT"]},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Successfully subscribed to 2 symbols"
        assert data["subscribed_symbols"] == ["AAPL", "MSFT"]
        assert data["total_subscriptions"] == 3

        # Verify subscribe_symbols was called
        mock_stream.subscribe_symbols.assert_called_once_with(["AAPL", "MSFT"])

    def test_subscribe_subscription_error(self, test_client, mock_stream):
        """Test subscribe handles SubscriptionError."""
        mock_stream.subscribe_symbols.side_effect = SubscriptionError("WebSocket not connected")

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 500
        assert "Subscription failed" in response.json()["detail"]
        assert "WebSocket not connected" in response.json()["detail"]


class TestUnsubscribeEndpoint:
    """Tests for unsubscribe endpoint."""

    def test_unsubscribe_stream_not_initialized(self, test_client):
        """Test unsubscribe returns 503 when stream not initialized."""
        with patch("apps.market_data_service.main.stream", None):
            response = test_client.delete("/api/v1/subscribe/AAPL")

        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]

    def test_unsubscribe_success(self, test_client, mock_stream):
        """Test successful unsubscription from symbol."""
        mock_stream.get_subscribed_symbols.return_value = ["MSFT", "GOOGL"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.delete("/api/v1/subscribe/AAPL")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Successfully unsubscribed from AAPL"
        assert data["remaining_subscriptions"] == 2

        # Verify unsubscribe_symbols was called
        mock_stream.unsubscribe_symbols.assert_called_once_with(["AAPL"])

    def test_unsubscribe_subscription_error(self, test_client, mock_stream):
        """Test unsubscribe handles SubscriptionError."""
        mock_stream.unsubscribe_symbols.side_effect = SubscriptionError("Symbol not subscribed")

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.delete("/api/v1/subscribe/AAPL")

        assert response.status_code == 500
        assert "Unsubscription failed" in response.json()["detail"]
        assert "Symbol not subscribed" in response.json()["detail"]


class TestGetSubscriptionsEndpoint:
    """Tests for get subscriptions endpoint."""

    def test_get_subscriptions_stream_not_initialized(self, test_client):
        """Test get subscriptions returns 503 when stream not initialized."""
        with patch("apps.market_data_service.main.stream", None):
            response = test_client.get("/api/v1/subscriptions")

        assert response.status_code == 503
        assert "not initialized" in response.json()["detail"]

    def test_get_subscriptions_success(self, test_client, mock_stream):
        """Test getting list of subscribed symbols."""
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/api/v1/subscriptions")

        assert response.status_code == 200
        data = response.json()
        assert data["symbols"] == ["AAPL", "MSFT", "GOOGL"]
        assert data["count"] == 3

    def test_get_subscriptions_empty(self, test_client, mock_stream):
        """Test getting subscriptions when no symbols subscribed."""
        mock_stream.get_subscribed_symbols.return_value = []

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/api/v1/subscriptions")

        assert response.status_code == 200
        data = response.json()
        assert data["symbols"] == []
        assert data["count"] == 0


class TestGetSubscriptionStatsEndpoint:
    """Tests for get subscription stats endpoint."""

    def test_get_stats_subscription_manager_not_initialized(self, test_client):
        """Test get stats when subscription manager not initialized."""
        with patch("apps.market_data_service.main.subscription_manager", None):
            response = test_client.get("/api/v1/subscriptions/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["auto_subscription_enabled"] is False
        assert "not configured" in data["message"]

    def test_get_stats_success(self, test_client, mock_subscription_manager):
        """Test getting subscription manager stats."""
        mock_subscription_manager.get_stats.return_value = {
            "execution_gateway_url": "http://localhost:8002",
            "sync_interval": 60,
            "last_sync_at": "2024-10-19T10:00:00Z",
            "position_count": 5,
            "position_symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            "subscribed_symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
        }

        with patch("apps.market_data_service.main.subscription_manager", mock_subscription_manager):
            response = test_client.get("/api/v1/subscriptions/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["auto_subscription_enabled"] is True
        assert data["execution_gateway_url"] == "http://localhost:8002"
        assert data["sync_interval"] == 60
        assert data["position_count"] == 5
        assert len(data["position_symbols"]) == 5

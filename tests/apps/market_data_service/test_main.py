"""
Unit tests for Market Data Service FastAPI endpoints.

Tests cover:
- Lifespan startup/shutdown (success and various error scenarios)
- Health check endpoint (healthy/degraded/unavailable states)
- Subscribe endpoint (success/errors/validation)
- Unsubscribe endpoint (success/errors)
- Get subscriptions endpoint
- Get subscription stats endpoint
- Prometheus metrics endpoint
- Exception handling in endpoints
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
import redis.exceptions
from fastapi.testclient import TestClient

from libs.data.market_data import SubscriptionError


@pytest.fixture()
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


@pytest.fixture()
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


@pytest.fixture()
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


class TestLifespanStartup:
    """Tests for lifespan startup logic."""

    @pytest.mark.asyncio()
    async def test_lifespan_successful_startup_and_shutdown(self, monkeypatch):
        """Test successful lifespan startup and shutdown."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        # Mock dependencies
        mock_redis_client = Mock()
        mock_event_publisher = Mock()
        mock_stream = Mock()
        mock_stream.start = AsyncMock()
        mock_stream.stop = AsyncMock()

        mock_subscription_manager = Mock()
        mock_subscription_manager.shutdown = AsyncMock()
        mock_sync_task = Mock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream", return_value=mock_stream
            ),
            patch(
                "apps.market_data_service.main.PositionBasedSubscription",
                return_value=mock_subscription_manager,
            ),
            patch("apps.market_data_service.main.asyncio.create_task", return_value=mock_sync_task),
        ):
            from apps.market_data_service.main import lifespan

            # Create a mock app
            mock_app = Mock()

            # Test lifespan
            async with lifespan(mock_app):
                # Verify startup was successful
                mock_stream.start.assert_called_once()
                mock_subscription_manager.set_task.assert_called_once_with(mock_sync_task)

            # Verify shutdown was called
            mock_subscription_manager.shutdown.assert_called_once()
            mock_stream.stop.assert_called_once()

    @pytest.mark.asyncio()
    async def test_lifespan_redis_connection_error(self, monkeypatch):
        """Test lifespan handles Redis connection errors."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "invalid_host")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        with patch(
            "apps.market_data_service.main.RedisClient",
            side_effect=redis.exceptions.ConnectionError("Connection refused"),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            with pytest.raises(redis.exceptions.ConnectionError):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio()
    async def test_lifespan_http_status_error(self, monkeypatch):
        """Test lifespan handles HTTP status errors from Alpaca."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()

        # Create a mock HTTP error
        mock_request = httpx.Request("GET", "https://api.alpaca.markets")
        mock_response = httpx.Response(401, request=mock_request)
        http_error = httpx.HTTPStatusError(
            "Unauthorized", request=mock_request, response=mock_response
        )

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream", side_effect=http_error
            ),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            with pytest.raises(httpx.HTTPStatusError):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio()
    async def test_lifespan_network_error(self, monkeypatch):
        """Test lifespan handles network errors."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream",
                side_effect=httpx.NetworkError("Network unreachable"),
            ),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            with pytest.raises(httpx.NetworkError):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio()
    async def test_lifespan_connect_timeout(self, monkeypatch):
        """Test lifespan handles connection timeouts."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream",
                side_effect=httpx.ConnectTimeout("Connection timed out"),
            ),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            with pytest.raises(httpx.ConnectTimeout):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio()
    async def test_lifespan_unexpected_error(self, monkeypatch):
        """Test lifespan handles unexpected errors."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream",
                side_effect=ValueError("Unexpected error"),
            ),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            with pytest.raises(ValueError, match="Unexpected error"):
                async with lifespan(mock_app):
                    pass

    @pytest.mark.asyncio()
    async def test_lifespan_shutdown_handles_stream_stop_error(self, monkeypatch):
        """Test lifespan shutdown handles errors when stopping stream."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()
        mock_stream = Mock()
        mock_stream.start = AsyncMock()
        mock_stream.stop = AsyncMock(side_effect=RuntimeError("Stop failed"))

        mock_subscription_manager = Mock()
        mock_subscription_manager.shutdown = AsyncMock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream", return_value=mock_stream
            ),
            patch(
                "apps.market_data_service.main.PositionBasedSubscription",
                return_value=mock_subscription_manager,
            ),
            patch("apps.market_data_service.main.asyncio.create_task"),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            # Should not raise, just log the error
            async with lifespan(mock_app):
                pass

            # Verify shutdown was still attempted
            mock_subscription_manager.shutdown.assert_called_once()
            mock_stream.stop.assert_called_once()

    @pytest.mark.asyncio()
    async def test_lifespan_shutdown_handles_cancelled_error(self, monkeypatch):
        """Test lifespan shutdown handles CancelledError during stream stop."""
        monkeypatch.setenv("ALPACA_API_KEY", "test_key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
        monkeypatch.setenv("REDIS_HOST", "localhost")
        monkeypatch.setenv("REDIS_PORT", "6379")
        monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

        mock_redis_client = Mock()
        mock_event_publisher = Mock()
        mock_stream = Mock()
        mock_stream.start = AsyncMock()
        mock_stream.stop = AsyncMock(side_effect=asyncio.CancelledError())

        mock_subscription_manager = Mock()
        mock_subscription_manager.shutdown = AsyncMock()

        with (
            patch("apps.market_data_service.main.RedisClient", return_value=mock_redis_client),
            patch(
                "apps.market_data_service.main.EventPublisher", return_value=mock_event_publisher
            ),
            patch(
                "apps.market_data_service.main.AlpacaMarketDataStream", return_value=mock_stream
            ),
            patch(
                "apps.market_data_service.main.PositionBasedSubscription",
                return_value=mock_subscription_manager,
            ),
            patch("apps.market_data_service.main.asyncio.create_task"),
        ):
            from apps.market_data_service.main import lifespan

            mock_app = Mock()

            # CancelledError should be re-raised
            with pytest.raises(asyncio.CancelledError):
                async with lifespan(mock_app):
                    pass


class TestMetricsEndpoint:
    """Tests for Prometheus metrics endpoint."""

    def test_metrics_endpoint_accessible(self, test_client):
        """Test that metrics endpoint is accessible."""
        response = test_client.get("/metrics")

        assert response.status_code == 200
        # Check for prometheus format
        assert response.headers["content-type"].startswith("text/plain")

    def test_metrics_contain_expected_metrics(self, test_client):
        """Test that metrics endpoint contains expected metric names."""
        response = test_client.get("/metrics")

        assert response.status_code == 200
        content = response.text

        # Check for business metrics
        assert "market_data_subscription_requests_total" in content
        assert "market_data_subscription_duration_seconds" in content
        assert "market_data_subscribed_symbols_current" in content
        assert "market_data_websocket_messages_received_total" in content
        assert "market_data_position_syncs_total" in content

        # Check for health metrics
        assert "market_data_websocket_connection_status" in content
        assert "market_data_redis_connection_status" in content
        assert "market_data_reconnect_attempts_total" in content

        # Check for shared latency metric (no service prefix)
        assert "market_data_processing_duration_seconds" in content


class TestSubscribeEndpointEdgeCases:
    """Additional edge case tests for subscribe endpoint."""

    def test_subscribe_unexpected_exception(self, test_client, mock_stream):
        """Test subscribe handles unexpected exceptions."""
        mock_stream.subscribe_symbols.side_effect = RuntimeError("Unexpected error")

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL"]},
            )

        # Should still return an error (500)
        assert response.status_code == 500

    def test_subscribe_with_multiple_symbols(self, test_client, mock_stream):
        """Test subscribing to multiple symbols at once."""
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT", "GOOGL", "AMZN"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL", "MSFT", "GOOGL", "AMZN"]},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["message"] == "Successfully subscribed to 4 symbols"
        assert len(data["subscribed_symbols"]) == 4
        assert data["total_subscriptions"] == 4

    def test_subscribe_invalid_json(self, test_client):
        """Test subscribe with invalid JSON returns 422."""
        response = test_client.post(
            "/api/v1/subscribe",
            content="invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422


class TestUnsubscribeEndpointEdgeCases:
    """Additional edge case tests for unsubscribe endpoint."""

    def test_unsubscribe_unexpected_exception(self, test_client, mock_stream):
        """Test unsubscribe handles unexpected exceptions."""
        mock_stream.unsubscribe_symbols.side_effect = RuntimeError("Unexpected error")

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.delete("/api/v1/subscribe/AAPL")

        # Should still return an error (500)
        assert response.status_code == 500

    def test_unsubscribe_last_symbol(self, test_client, mock_stream):
        """Test unsubscribing the last symbol leaves empty subscriptions."""
        mock_stream.get_subscribed_symbols.return_value = []

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.delete("/api/v1/subscribe/AAPL")

        assert response.status_code == 200
        data = response.json()
        assert data["remaining_subscriptions"] == 0


class TestHealthCheckEdgeCases:
    """Additional edge case tests for health check endpoint."""

    def test_health_check_with_reconnect_attempts(self, test_client, mock_stream):
        """Test health check with ongoing reconnection attempts."""
        mock_stream.get_connection_stats.return_value = {
            "is_connected": False,
            "subscribed_symbols": 3,
            "reconnect_attempts": 5,
            "max_reconnect_attempts": 10,
        }

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        assert data["reconnect_attempts"] == 5
        assert data["max_reconnect_attempts"] == 10

    def test_health_check_updates_metrics(self, test_client, mock_stream):
        """Test health check updates Prometheus metrics."""
        mock_stream.get_connection_stats.return_value = {
            "is_connected": True,
            "subscribed_symbols": 10,
            "reconnect_attempts": 0,
            "max_reconnect_attempts": 3,
        }

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.get("/health")

        assert response.status_code == 200

        # Verify metrics endpoint reflects updated values
        metrics_response = test_client.get("/metrics")
        assert "market_data_websocket_connection_status 1.0" in metrics_response.text
        assert "market_data_subscribed_symbols_current 10.0" in metrics_response.text


class TestAppInitialization:
    """Tests for FastAPI app initialization."""

    def test_app_metadata(self, test_client):
        """Test app metadata is correctly configured."""
        from apps.market_data_service.main import app

        assert app.title == "Market Data Service"
        assert app.description == "Real-time market data streaming from Alpaca"
        assert app.version == "1.0.0"

    def test_app_has_lifespan(self, test_client):
        """Test app has lifespan configured."""
        from apps.market_data_service.main import app

        assert app.router.lifespan_context is not None


class TestResponseModels:
    """Tests for response model validation."""

    def test_subscribe_response_model_valid(self, test_client, mock_stream):
        """Test subscribe response matches SubscribeResponse model."""
        mock_stream.get_subscribed_symbols.return_value = ["AAPL"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 201
        data = response.json()

        # Verify all required fields are present
        assert "message" in data
        assert "subscribed_symbols" in data
        assert "total_subscriptions" in data
        assert isinstance(data["subscribed_symbols"], list)
        assert isinstance(data["total_subscriptions"], int)

    def test_health_response_model_valid(self, test_client, mock_stream):
        """Test health response matches HealthResponse model."""
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

        # Verify all required fields are present
        assert "status" in data
        assert "service" in data
        assert "websocket_connected" in data
        assert "subscribed_symbols" in data
        assert "reconnect_attempts" in data
        assert "max_reconnect_attempts" in data
        assert isinstance(data["websocket_connected"], bool)
        assert isinstance(data["subscribed_symbols"], int)


class TestMetricsUpdates:
    """Tests that endpoints properly update Prometheus metrics."""

    def test_subscribe_updates_metrics(self, test_client, mock_stream):
        """Test subscribe endpoint updates metrics."""
        mock_stream.get_subscribed_symbols.return_value = ["AAPL", "MSFT"]

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL", "MSFT"]},
            )

        assert response.status_code == 201

        # Check metrics endpoint for updated counters
        metrics_response = test_client.get("/metrics")
        content = metrics_response.text

        # Subscription requests counter should be incremented
        assert "market_data_subscription_requests_total" in content
        # Subscribed symbols gauge should be updated
        assert "market_data_subscribed_symbols_current 2.0" in content

    def test_subscribe_error_updates_error_metrics(self, test_client, mock_stream):
        """Test subscribe errors update error metrics."""
        mock_stream.subscribe_symbols.side_effect = SubscriptionError("Test error")

        with patch("apps.market_data_service.main.stream", mock_stream):
            response = test_client.post(
                "/api/v1/subscribe",
                json={"symbols": ["AAPL"]},
            )

        assert response.status_code == 500

        # Check metrics for error status
        metrics_response = test_client.get("/metrics")
        content = metrics_response.text

        # Should have error status in metrics
        assert 'market_data_subscription_requests_total{operation="subscribe",status="error"}' in content

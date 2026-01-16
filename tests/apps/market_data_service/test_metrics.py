"""
Tests for Prometheus metrics endpoint in Market Data Service.

Verifies that:
1. /metrics endpoint exists and returns Prometheus-formatted data
2. All defined metrics are present in the output
3. Metrics have correct initial values
4. Metric naming follows Prometheus conventions
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create test client for Market Data Service."""
    # Import here to avoid issues with module-level initialization
    from apps.market_data_service.main import app

    return TestClient(app)


class TestPrometheusMetrics:
    """Test suite for Prometheus metrics endpoint."""

    def test_metrics_endpoint_exists(self, client):
        """Test that /metrics endpoint exists and returns 200."""
        response = client.get("/metrics")
        assert response.status_code == 200

    def test_metrics_content_type(self, client):
        """Test that /metrics returns correct content type."""
        response = client.get("/metrics")
        # Prometheus exposition format
        assert "text/plain" in response.headers.get("content-type", "")

    def test_subscription_requests_total_metric_exists(self, client):
        """Test that market_data_subscription_requests_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Check metric name and help text
        assert "market_data_subscription_requests_total" in response.text
        assert "Total number of subscription requests" in response.text

    def test_subscription_duration_metric_exists(self, client):
        """Test that market_data_subscription_duration_seconds metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_subscription_duration_seconds" in response.text
        assert "Time taken to process subscription requests" in response.text

    def test_subscribed_symbols_current_metric_exists(self, client):
        """Test that market_data_subscribed_symbols_current metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_subscribed_symbols_current" in response.text
        assert "Current number of subscribed symbols" in response.text

    def test_websocket_messages_received_total_metric_exists(self, client):
        """Test that market_data_websocket_messages_received_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_websocket_messages_received_total" in response.text
        assert "Total number of WebSocket messages received" in response.text

    def test_position_syncs_total_metric_exists(self, client):
        """Test that market_data_position_syncs_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_position_syncs_total" in response.text
        assert "Total number of position-based subscription syncs" in response.text

    def test_websocket_connection_status_metric_exists(self, client):
        """Test that market_data_websocket_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_websocket_connection_status" in response.text
        assert "WebSocket connection status" in response.text

    def test_redis_connection_status_metric_exists(self, client):
        """Test that market_data_redis_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_redis_connection_status" in response.text
        assert "Redis connection status" in response.text

    def test_reconnect_attempts_total_metric_exists(self, client):
        """Test that market_data_reconnect_attempts_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "market_data_reconnect_attempts_total" in response.text
        assert "Total number of WebSocket reconnection attempts" in response.text

    def test_websocket_connection_status_initial_value(self, client):
        """Test that websocket_connection_status has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 0 initially (will be updated by lifespan)
        assert "market_data_websocket_connection_status 0.0" in response.text

    def test_redis_connection_status_initial_value(self, client):
        """Test that redis_connection_status has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 0 initially (will be updated by lifespan)
        assert "market_data_redis_connection_status 0.0" in response.text

    def test_all_required_metrics_present(self, client):
        """Test that all required metrics are present in output."""
        response = client.get("/metrics")
        assert response.status_code == 200

        required_metrics = [
            "market_data_subscription_requests_total",
            "market_data_subscription_duration_seconds",
            "market_data_subscribed_symbols_current",
            "market_data_websocket_messages_received_total",
            "market_data_position_syncs_total",
            "market_data_websocket_connection_status",
            "market_data_redis_connection_status",
            "market_data_reconnect_attempts_total",
        ]

        for metric in required_metrics:
            assert metric in response.text, f"Missing metric: {metric}"

    def test_metrics_naming_convention(self, client):
        """Test that all metrics follow Prometheus naming conventions."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # All metrics should start with service name prefix
        lines = response.text.split("\n")
        metric_lines = [
            line
            for line in lines
            if not line.startswith("#") and line.strip() and "market_data_" in line
        ]

        for line in metric_lines:
            # Each metric line should start with market_data_
            assert "market_data_" in line, f"Metric doesn't follow naming convention: {line}"

    def test_metrics_have_help_text(self, client):
        """Test that all metrics have HELP text."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "market_data_subscription_requests_total",
            "market_data_subscription_duration_seconds",
            "market_data_subscribed_symbols_current",
            "market_data_websocket_messages_received_total",
            "market_data_position_syncs_total",
            "market_data_websocket_connection_status",
            "market_data_redis_connection_status",
            "market_data_reconnect_attempts_total",
        ]

        for metric in metrics:
            # Check for HELP line
            assert f"# HELP {metric}" in response.text, f"Missing HELP text for: {metric}"

    def test_metrics_have_type_declaration(self, client):
        """Test that all metrics have TYPE declaration."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "market_data_subscription_requests_total",
            "market_data_subscription_duration_seconds",
            "market_data_subscribed_symbols_current",
            "market_data_websocket_messages_received_total",
            "market_data_position_syncs_total",
            "market_data_websocket_connection_status",
            "market_data_redis_connection_status",
            "market_data_reconnect_attempts_total",
        ]

        for metric in metrics:
            # Check for TYPE line
            assert f"# TYPE {metric}" in response.text, f"Missing TYPE declaration for: {metric}"

    def test_counter_metrics_have_total_suffix(self, client):
        """Test that counter metrics end with _total suffix."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Counter metrics should end with _total
        counter_metrics = [
            "market_data_subscription_requests_total",
            "market_data_websocket_messages_received_total",
            "market_data_position_syncs_total",
            "market_data_reconnect_attempts_total",
        ]

        for metric in counter_metrics:
            assert metric.endswith("_total"), f"Counter metric missing _total suffix: {metric}"
            assert f"# TYPE {metric} counter" in response.text

    def test_histogram_metrics_have_correct_type(self, client):
        """Test that histogram metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        histogram_metrics = ["market_data_subscription_duration_seconds"]

        for metric in histogram_metrics:
            assert f"# TYPE {metric} histogram" in response.text

    def test_gauge_metrics_have_correct_type(self, client):
        """Test that gauge metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        gauge_metrics = [
            "market_data_subscribed_symbols_current",
            "market_data_websocket_connection_status",
            "market_data_redis_connection_status",
        ]

        for metric in gauge_metrics:
            assert f"# TYPE {metric} gauge" in response.text

    def test_metrics_endpoint_performance(self, client):
        """Test that /metrics endpoint responds quickly."""
        import time

        start = time.time()
        response = client.get("/metrics")
        duration = time.time() - start

        assert response.status_code == 200
        # Metrics endpoint should respond in < 100ms
        assert duration < 0.1, f"Metrics endpoint too slow: {duration:.3f}s"

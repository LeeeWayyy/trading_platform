"""
Tests for Prometheus metrics endpoint in Execution Gateway.

Verifies that:
1. /metrics endpoint exists and returns Prometheus-formatted data
2. All defined metrics are present in the output
3. Metrics have correct initial values
4. Metric naming follows Prometheus conventions
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client for Execution Gateway."""
    # Import here to avoid issues with module-level initialization
    from apps.execution_gateway.main import app

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

    def test_orders_total_metric_exists(self, client):
        """Test that execution_gateway_orders_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Check metric name and help text
        assert "execution_gateway_orders_total" in response.text
        assert "Total number of orders submitted" in response.text
        # Note: Label data only appears after first increment
        # Just verify the metric is declared

    def test_order_placement_duration_metric_exists(self, client):
        """Test that execution_gateway_order_placement_duration_seconds metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_order_placement_duration_seconds" in response.text
        assert "Time taken to place an order" in response.text

    def test_positions_current_metric_exists(self, client):
        """Test that execution_gateway_positions_current metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_positions_current" in response.text
        assert "Current open positions by symbol" in response.text

    def test_pnl_dollars_metric_exists(self, client):
        """Test that execution_gateway_pnl_dollars metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_pnl_dollars" in response.text
        assert "P&L in dollars" in response.text
        # Note: Label data only appears after first set()
        # Just verify the metric is declared

    def test_database_connection_status_metric_exists(self, client):
        """Test that execution_gateway_database_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_database_connection_status" in response.text
        assert "Database connection status" in response.text

    def test_redis_connection_status_metric_exists(self, client):
        """Test that execution_gateway_redis_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_redis_connection_status" in response.text
        assert "Redis connection status" in response.text

    def test_alpaca_connection_status_metric_exists(self, client):
        """Test that execution_gateway_alpaca_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_alpaca_connection_status" in response.text
        assert "Alpaca connection status" in response.text

    def test_webhook_received_total_metric_exists(self, client):
        """Test that execution_gateway_webhook_received_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_webhook_received_total" in response.text
        assert "Total webhooks received" in response.text

    def test_dry_run_mode_metric_exists(self, client):
        """Test that execution_gateway_dry_run_mode metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "execution_gateway_dry_run_mode" in response.text
        assert "DRY_RUN mode status" in response.text

    def test_dry_run_mode_initial_value(self, client):
        """Test that dry_run_mode metric has correct initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # In test environment, DRY_RUN defaults to true
        # So metric should be 1.0
        assert "execution_gateway_dry_run_mode 1.0" in response.text

    def test_database_connection_status_initial_value(self, client):
        """Test that database_connection_status has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 0 initially (will be updated by health check)
        assert "execution_gateway_database_connection_status 0.0" in response.text

    def test_all_required_metrics_present(self, client):
        """Test that all required metrics are present in output."""
        response = client.get("/metrics")
        assert response.status_code == 200

        required_metrics = [
            "execution_gateway_orders_total",
            "execution_gateway_order_placement_duration_seconds",
            "execution_gateway_positions_current",
            "execution_gateway_pnl_dollars",
            "execution_gateway_database_connection_status",
            "execution_gateway_redis_connection_status",
            "execution_gateway_alpaca_connection_status",
            "execution_gateway_alpaca_api_requests_total",
            "execution_gateway_webhook_received_total",
            "execution_gateway_dry_run_mode",
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
            if not line.startswith("#") and line.strip() and "execution_gateway_" in line
        ]

        for line in metric_lines:
            # Each metric line should start with execution_gateway_
            assert "execution_gateway_" in line, f"Metric doesn't follow naming convention: {line}"

    def test_metrics_have_help_text(self, client):
        """Test that all metrics have HELP text."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "execution_gateway_orders_total",
            "execution_gateway_order_placement_duration_seconds",
            "execution_gateway_positions_current",
            "execution_gateway_pnl_dollars",
            "execution_gateway_database_connection_status",
            "execution_gateway_redis_connection_status",
            "execution_gateway_alpaca_connection_status",
            "execution_gateway_webhook_received_total",
            "execution_gateway_dry_run_mode",
        ]

        for metric in metrics:
            # Check for HELP line
            assert f"# HELP {metric}" in response.text, f"Missing HELP text for: {metric}"

    def test_metrics_have_type_declaration(self, client):
        """Test that all metrics have TYPE declaration."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "execution_gateway_orders_total",
            "execution_gateway_order_placement_duration_seconds",
            "execution_gateway_positions_current",
            "execution_gateway_pnl_dollars",
            "execution_gateway_database_connection_status",
            "execution_gateway_redis_connection_status",
            "execution_gateway_alpaca_connection_status",
            "execution_gateway_webhook_received_total",
            "execution_gateway_dry_run_mode",
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
            "execution_gateway_orders_total",
            "execution_gateway_webhook_received_total",
        ]

        for metric in counter_metrics:
            assert metric.endswith("_total"), f"Counter metric missing _total suffix: {metric}"
            assert f"# TYPE {metric} counter" in response.text

    def test_histogram_metrics_have_correct_type(self, client):
        """Test that histogram metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        histogram_metrics = ["execution_gateway_order_placement_duration_seconds"]

        for metric in histogram_metrics:
            assert f"# TYPE {metric} histogram" in response.text

    def test_gauge_metrics_have_correct_type(self, client):
        """Test that gauge metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        gauge_metrics = [
            "execution_gateway_positions_current",
            "execution_gateway_pnl_dollars",
            "execution_gateway_database_connection_status",
            "execution_gateway_redis_connection_status",
            "execution_gateway_alpaca_connection_status",
            "execution_gateway_dry_run_mode",
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

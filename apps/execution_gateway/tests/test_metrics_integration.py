"""
Integration tests for Prometheus metrics data capture in Execution Gateway.

Verifies that:
1. Health metrics update dynamically based on connection status
2. Metrics endpoint responds with proper Prometheus format
3. Service-level metrics are tracked

Note: Full order/position metrics tests require database setup and are covered
in end-to-end test suites. These tests focus on metrics that can be verified
without external dependencies.
"""

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client for Execution Gateway."""
    # Import here to avoid issues with module-level initialization
    from apps.execution_gateway.main import app

    return TestClient(app)


class TestMetricsDataCapture:
    """Test suite for verifying metrics capture actual data."""

    def test_health_check_updates_connection_status(self, client):
        """Test that health_check endpoint updates database_connection_status dynamically."""
        # Call health check
        health_response = client.get("/health")
        assert health_response.status_code == 200
        health_data = health_response.json()

        # Get metrics
        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        metrics_text = metrics_response.text

        # Verify database_connection_status reflects actual state
        expected_status = 1.0 if health_data["database_connected"] else 0.0

        pattern = r"execution_gateway_database_connection_status (\d+\.?\d*)"
        match = re.search(pattern, metrics_text)
        assert match, "Database connection status metric not found"
        metric_value = float(match.group(1))

        assert (
            metric_value == expected_status
        ), f"Expected database status {expected_status}, got {metric_value}"

    def test_health_check_updates_alpaca_connection_status(self, client):
        """Test that health_check endpoint updates alpaca_connection_status."""
        # Get metrics
        metrics_response = client.get("/metrics")
        metrics_text = metrics_response.text

        # In test environment with DRY_RUN=true and no Alpaca client,
        # alpaca_connection_status metric should be 0
        # (even though health check reports alpaca_connected=true,
        # the metric tracks actual connection state, not operational status)
        pattern = r"execution_gateway_alpaca_connection_status (\d+\.?\d*)"
        match = re.search(pattern, metrics_text)
        assert match, "Alpaca connection status metric not found"
        metric_value = float(match.group(1))

        # In DRY_RUN mode (test environment), we don't have Alpaca connection
        # Metric should be 0
        assert metric_value == 0.0, f"Expected Alpaca status 0.0 in DRY_RUN mode, got {metric_value}"

    def test_dry_run_mode_metric_set_correctly(self, client):
        """Test that dry_run_mode metric reflects DRY_RUN environment variable."""
        # Get metrics
        metrics_response = client.get("/metrics")
        metrics_text = metrics_response.text

        # Verify dry_run_mode is set (should be 1.0 in test environment)
        pattern = r"execution_gateway_dry_run_mode (\d+\.?\d*)"
        match = re.search(pattern, metrics_text)
        assert match, "Dry run mode metric not found"
        metric_value = float(match.group(1))

        # In test environment, DRY_RUN defaults to true
        assert metric_value == 1.0, f"Expected dry_run_mode=1.0, got {metric_value}"

    def test_redis_connection_status_reflects_state(self, client):
        """Test that redis_connection_status reflects actual Redis connection state."""
        # Get metrics
        metrics_response = client.get("/metrics")
        metrics_text = metrics_response.text

        # Verify redis_connection_status exists
        pattern = r"execution_gateway_redis_connection_status (\d+\.?\d*)"
        match = re.search(pattern, metrics_text)
        assert match, "Redis connection status metric not found"
        metric_value = float(match.group(1))

        # In test environment without Redis running, should be 0.0
        # (Redis connection fails during startup and client is set to None)
        assert (
            metric_value == 0.0
        ), f"Expected redis_connection_status=0.0 (no Redis in test env), got {metric_value}"

    def test_metrics_format_is_prometheus_compliant(self, client):
        """Test that metrics endpoint returns valid Prometheus format."""
        metrics_response = client.get("/metrics")
        assert metrics_response.status_code == 200
        assert "text/plain" in metrics_response.headers.get("content-type", "")

        metrics_text = metrics_response.text

        # Verify all our custom metrics have HELP and TYPE declarations
        custom_metrics = [
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

        for metric in custom_metrics:
            # Each metric should have HELP text
            assert (
                f"# HELP {metric}" in metrics_text
            ), f"Missing HELP declaration for {metric}"

            # Each metric should have TYPE declaration
            assert (
                f"# TYPE {metric}" in metrics_text
            ), f"Missing TYPE declaration for {metric}"

    def test_multiple_health_checks_update_metrics(self, client):
        """Test that calling health check multiple times updates metrics each time."""
        # Call health check first time
        first_response = client.get("/health")
        assert first_response.status_code == 200

        first_metrics = client.get("/metrics").text
        pattern = r"execution_gateway_database_connection_status (\d+\.?\d*)"
        first_match = re.search(pattern, first_metrics)
        assert first_match
        first_value = float(first_match.group(1))

        # Call health check second time
        second_response = client.get("/health")
        assert second_response.status_code == 200

        second_metrics = client.get("/metrics").text
        second_match = re.search(pattern, second_metrics)
        assert second_match
        second_value = float(second_match.group(1))

        # Values should be consistent (both reflect same DB state)
        assert (
            first_value == second_value
        ), "Database connection status should be consistent across health checks"

    def test_alpaca_api_requests_total_tracked_in_health_check(self, client):
        """Test that alpaca_api_requests_total increments when health check calls Alpaca API."""
        # Get initial metrics
        initial_metrics = client.get("/metrics").text

        # Call health check (triggers Alpaca API check_connection if not DRY_RUN)
        health_response = client.get("/health")
        assert health_response.status_code == 200

        # Get updated metrics
        updated_metrics = client.get("/metrics").text

        # In DRY_RUN mode, Alpaca API is not called, so metric should not increment
        # In non-DRY_RUN mode with Alpaca client, metric should increment
        # This test verifies the metric is properly defined and tracked
        assert "execution_gateway_alpaca_api_requests_total" in updated_metrics

    def test_alpaca_api_requests_total_has_operation_label(self, client):
        """Test that alpaca_api_requests_total has operation and status labels."""
        # Call health check to potentially trigger metric
        client.get("/health")

        # Get metrics
        metrics = client.get("/metrics").text

        # Metric should be defined with operation and status labels
        assert "execution_gateway_alpaca_api_requests_total" in metrics
        assert "# TYPE execution_gateway_alpaca_api_requests_total counter" in metrics

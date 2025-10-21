"""
Tests for Prometheus metrics endpoint in Orchestrator.

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
    """Create test client for Orchestrator."""
    # Import here to avoid issues with module-level initialization
    from apps.orchestrator.main import app

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

    def test_orchestration_runs_total_metric_exists(self, client):
        """Test that orchestrator_runs_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Check metric name and help text
        assert "orchestrator_runs_total" in response.text
        assert "Total number of orchestration runs" in response.text

    def test_orchestration_duration_metric_exists(self, client):
        """Test that orchestrator_orchestration_duration_seconds metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_orchestration_duration_seconds" in response.text
        assert "Time taken to complete orchestration workflow" in response.text

    def test_signals_received_total_metric_exists(self, client):
        """Test that orchestrator_signals_received_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_signals_received_total" in response.text
        assert "Total number of signals received from Signal Service" in response.text

    def test_orders_submitted_total_metric_exists(self, client):
        """Test that orchestrator_orders_submitted_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_orders_submitted_total" in response.text
        assert "Total number of orders submitted to Execution Gateway" in response.text

    def test_positions_adjusted_total_metric_exists(self, client):
        """Test that orchestrator_positions_adjusted_total metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_positions_adjusted_total" in response.text
        assert "Total number of position adjustments made" in response.text

    def test_database_connection_status_metric_exists(self, client):
        """Test that orchestrator_database_connection_status metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_database_connection_status" in response.text
        assert "Database connection status" in response.text

    def test_signal_service_available_metric_exists(self, client):
        """Test that orchestrator_signal_service_available metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_signal_service_available" in response.text
        assert "Signal Service availability" in response.text

    def test_execution_gateway_available_metric_exists(self, client):
        """Test that orchestrator_execution_gateway_available metric exists."""
        response = client.get("/metrics")
        assert response.status_code == 200

        assert "orchestrator_execution_gateway_available" in response.text
        assert "Execution Gateway availability" in response.text

    def test_database_connection_status_initial_value(self, client):
        """Test that database_connection_status has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 1 initially (assuming DB is up in tests)
        assert "orchestrator_database_connection_status 1.0" in response.text

    def test_signal_service_available_initial_value(self, client):
        """Test that signal_service_available has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 0 initially (will be updated by health check)
        assert "orchestrator_signal_service_available 0.0" in response.text

    def test_execution_gateway_available_initial_value(self, client):
        """Test that execution_gateway_available has initial value."""
        response = client.get("/metrics")
        assert response.status_code == 200

        # Should be set to 0 initially (will be updated by health check)
        assert "orchestrator_execution_gateway_available 0.0" in response.text

    def test_all_required_metrics_present(self, client):
        """Test that all required metrics are present in output."""
        response = client.get("/metrics")
        assert response.status_code == 200

        required_metrics = [
            "orchestrator_runs_total",
            "orchestrator_orchestration_duration_seconds",
            "orchestrator_signals_received_total",
            "orchestrator_orders_submitted_total",
            "orchestrator_positions_adjusted_total",
            "orchestrator_database_connection_status",
            "orchestrator_signal_service_available",
            "orchestrator_execution_gateway_available",
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
            if not line.startswith("#") and line.strip() and "orchestrator_" in line
        ]

        for line in metric_lines:
            # Each metric line should start with orchestrator_
            assert "orchestrator_" in line, f"Metric doesn't follow naming convention: {line}"

    def test_metrics_have_help_text(self, client):
        """Test that all metrics have HELP text."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "orchestrator_runs_total",
            "orchestrator_orchestration_duration_seconds",
            "orchestrator_signals_received_total",
            "orchestrator_orders_submitted_total",
            "orchestrator_positions_adjusted_total",
            "orchestrator_database_connection_status",
            "orchestrator_signal_service_available",
            "orchestrator_execution_gateway_available",
        ]

        for metric in metrics:
            # Check for HELP line
            assert f"# HELP {metric}" in response.text, f"Missing HELP text for: {metric}"

    def test_metrics_have_type_declaration(self, client):
        """Test that all metrics have TYPE declaration."""
        response = client.get("/metrics")
        assert response.status_code == 200

        metrics = [
            "orchestrator_runs_total",
            "orchestrator_orchestration_duration_seconds",
            "orchestrator_signals_received_total",
            "orchestrator_orders_submitted_total",
            "orchestrator_positions_adjusted_total",
            "orchestrator_database_connection_status",
            "orchestrator_signal_service_available",
            "orchestrator_execution_gateway_available",
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
            "orchestrator_runs_total",
            "orchestrator_signals_received_total",
            "orchestrator_orders_submitted_total",
            "orchestrator_positions_adjusted_total",
        ]

        for metric in counter_metrics:
            assert metric.endswith("_total"), f"Counter metric missing _total suffix: {metric}"
            assert f"# TYPE {metric} counter" in response.text

    def test_histogram_metrics_have_correct_type(self, client):
        """Test that histogram metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        histogram_metrics = ["orchestrator_orchestration_duration_seconds"]

        for metric in histogram_metrics:
            assert f"# TYPE {metric} histogram" in response.text

    def test_gauge_metrics_have_correct_type(self, client):
        """Test that gauge metrics are declared correctly."""
        response = client.get("/metrics")
        assert response.status_code == 200

        gauge_metrics = [
            "orchestrator_database_connection_status",
            "orchestrator_signal_service_available",
            "orchestrator_execution_gateway_available",
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

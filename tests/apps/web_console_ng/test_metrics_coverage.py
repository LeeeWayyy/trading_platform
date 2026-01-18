"""Comprehensive test coverage for apps/web_console_ng/core/metrics.py.

Target: 85%+ branch coverage for MEDIUM RISK observability module.
Tests cover: metrics registration, endpoint security, decorators, sanitization, edge cases.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from nicegui import app
from prometheus_client import REGISTRY

from apps.web_console_ng import config
from apps.web_console_ng.core import metrics as metrics_module

client = TestClient(app)


# =============================================================================
# Metrics Registration Tests
# =============================================================================


def test_all_metrics_registered_in_prometheus():
    """All exported metrics should be registered in Prometheus registry."""
    metric_names = [
        "nicegui_ws_connections",
        "nicegui_ws_connects_total",
        "nicegui_ws_disconnects_total",
        "nicegui_connections_rejected_total",
        "nicegui_auth_failures_total",
        "nicegui_sessions_created_total",
        "nicegui_api_latency_seconds",
        "nicegui_redis_latency_seconds",
        "nicegui_state_save_errors_total",
        "nicegui_circuit_breaker_state",
        "nicegui_audit_flush_errors_total",
    ]

    registered_metrics = [collector.name for collector in REGISTRY.collect()]

    for metric_name in metric_names:
        assert metric_name in registered_metrics, f"{metric_name} not registered"


def test_metrics_have_pod_label():
    """All metrics should include 'pod' label for multi-pod deployments."""
    metrics_with_labels = [
        metrics_module.ws_connections,
        metrics_module.ws_connects_total,
        metrics_module.ws_disconnects_total,
        metrics_module.connections_rejected_total,
        metrics_module.auth_failures_total,
        metrics_module.sessions_created_total,
        metrics_module.api_latency_seconds,
        metrics_module.redis_latency_seconds,
        metrics_module.state_save_errors_total,
        metrics_module.circuit_breaker_state,
        metrics_module.audit_flush_errors_total,
    ]

    for metric in metrics_with_labels:
        assert "pod" in metric._labelnames, f"{metric._name} missing 'pod' label"


def test_histogram_buckets_configured():
    """Histograms should have appropriate buckets for latency ranges."""
    # API latency: 10ms to 10s
    api_buckets = metrics_module.api_latency_seconds._upper_bounds
    assert 0.01 in api_buckets  # 10ms
    assert 0.1 in api_buckets  # 100ms
    assert 1.0 in api_buckets  # 1s
    assert 10.0 in api_buckets  # 10s

    # Redis latency: 500µs to 1s
    redis_buckets = metrics_module.redis_latency_seconds._upper_bounds
    assert 0.0005 in redis_buckets  # 500µs
    assert 0.001 in redis_buckets  # 1ms
    assert 0.01 in redis_buckets  # 10ms
    assert 1.0 in redis_buckets  # 1s


# =============================================================================
# Metrics Endpoint Security Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_metrics_endpoint_protected_from_external():
    """Metrics endpoint should reject external requests when protection is enabled."""
    with (
        patch("apps.web_console_ng.config.METRICS_INGRESS_PROTECTED", False),
        patch("apps.web_console_ng.core.metrics.is_internal_request", return_value=False),
    ):
        response = client.get("/metrics")
        assert response.status_code == 403
        assert response.text == "Forbidden"


@pytest.mark.asyncio()
async def test_metrics_endpoint_allows_internal():
    """Metrics endpoint should allow internal requests."""
    with (
        patch("apps.web_console_ng.config.METRICS_INGRESS_PROTECTED", False),
        patch("apps.web_console_ng.core.metrics.is_internal_request", return_value=True),
    ):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        # Should contain prometheus metrics
        assert b"nicegui_ws_connections" in response.content


@pytest.mark.asyncio()
async def test_metrics_endpoint_public_when_ingress_protected():
    """When ingress protection is enabled, endpoint should be public."""
    with patch("apps.web_console_ng.config.METRICS_INGRESS_PROTECTED", True):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert b"nicegui_ws_connections" in response.content


# =============================================================================
# Label Sanitization Tests
# =============================================================================


def test_sanitize_label_value_removes_special_chars():
    """Sanitization should replace special characters with underscores."""
    result = metrics_module._sanitize_label_value("Redis timeout: connection reset")
    assert ":" not in result
    assert " " not in result
    assert result == "redis_timeout_connection_reset"


def test_sanitize_label_value_handles_empty_string():
    """Empty strings should return 'unknown' fallback."""
    assert metrics_module._sanitize_label_value("") == "unknown"
    assert metrics_module._sanitize_label_value("   ") == "unknown"


def test_sanitize_label_value_custom_fallback():
    """Sanitization should support custom fallback values."""
    result = metrics_module._sanitize_label_value("", fallback="error")
    assert result == "error"


def test_sanitize_label_value_lowercases():
    """Sanitization should lowercase input."""
    result = metrics_module._sanitize_label_value("ConnectionError")
    assert result == "connectionerror"


def test_sanitize_label_value_strips_underscores():
    """Leading/trailing underscores should be stripped."""
    result = metrics_module._sanitize_label_value("___error___")
    assert result == "error"


def test_sanitize_label_value_preserves_alphanumeric():
    """Alphanumeric, hyphens, and underscores should be preserved."""
    result = metrics_module._sanitize_label_value("error-code_123")
    assert result == "error-code_123"


def test_sanitize_label_value_handles_only_special_chars():
    """Strings with only special chars should return fallback."""
    result = metrics_module._sanitize_label_value("@#$%^&*()")
    assert result == "unknown"


# =============================================================================
# Helper Function Tests
# =============================================================================


def test_record_state_save_error():
    """Recording state save errors should increment counter with sanitized label."""
    initial_count = sum(
        sample.value
        for sample in metrics_module.state_save_errors_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
    )

    metrics_module.record_state_save_error("Connection timeout: network error")

    final_count = sum(
        sample.value
        for sample in metrics_module.state_save_errors_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
    )

    assert final_count > initial_count

    # Verify label was sanitized
    samples = metrics_module.state_save_errors_total.collect()[0].samples
    reasons = [s.labels.get("reason", "") for s in samples if s.labels.get("pod") == config.POD_NAME]
    assert any("connection_timeout" in r for r in reasons)


def test_record_auth_failure():
    """Recording auth failures should increment counter with sanitized label."""
    initial_count = sum(
        sample.value
        for sample in metrics_module.auth_failures_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("auth_type") == "password"
    )

    metrics_module.record_auth_failure("password", "Invalid credentials!")

    final_count = sum(
        sample.value
        for sample in metrics_module.auth_failures_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("auth_type") == "password"
    )

    assert final_count > initial_count


# =============================================================================
# Circuit Breaker State Tests
# =============================================================================


def test_set_circuit_breaker_state_bool_true():
    """Boolean True should set circuit breaker to 1 (tripped)."""
    metrics_module.set_circuit_breaker_state(True)
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_bool_false():
    """Boolean False should set circuit breaker to 0 (normal)."""
    metrics_module.set_circuit_breaker_state(False)
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


def test_set_circuit_breaker_state_int_nonzero():
    """Non-zero integer should set circuit breaker to 1 (tripped)."""
    metrics_module.set_circuit_breaker_state(42)
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_int_zero():
    """Zero integer should set circuit breaker to 0 (normal)."""
    metrics_module.set_circuit_breaker_state(0)
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


def test_set_circuit_breaker_state_string_tripped():
    """String 'TRIPPED' should set circuit breaker to 1."""
    metrics_module.set_circuit_breaker_state("TRIPPED")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_string_open():
    """String 'OPEN' should set circuit breaker to 1."""
    metrics_module.set_circuit_breaker_state("OPEN")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_string_engaged():
    """String 'ENGAGED' should set circuit breaker to 1."""
    metrics_module.set_circuit_breaker_state("ENGAGED")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_string_on():
    """String 'ON' should set circuit breaker to 1."""
    metrics_module.set_circuit_breaker_state("ON")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


def test_set_circuit_breaker_state_string_normal():
    """String 'NORMAL' should set circuit breaker to 0."""
    metrics_module.set_circuit_breaker_state("NORMAL")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


def test_set_circuit_breaker_state_string_closed():
    """String 'CLOSED' should set circuit breaker to 0."""
    metrics_module.set_circuit_breaker_state("CLOSED")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


def test_set_circuit_breaker_state_string_case_insensitive():
    """Circuit breaker state strings should be case insensitive."""
    metrics_module.set_circuit_breaker_state("tripped")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1

    metrics_module.set_circuit_breaker_state("Normal")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


def test_set_circuit_breaker_state_string_whitespace():
    """Circuit breaker state strings should handle whitespace."""
    metrics_module.set_circuit_breaker_state("  TRIPPED  ")
    value = metrics_module.circuit_breaker_state.labels(pod=config.POD_NAME)._value.get()
    assert value == 1


# =============================================================================
# Decorator Tests: time_redis_operation
# =============================================================================


@pytest.mark.asyncio()
async def test_time_redis_operation_decorator_success():
    """Redis timing decorator should record successful operation latency."""

    @metrics_module.time_redis_operation("test_op")
    async def mock_redis_call():
        await asyncio_sleep(0.01)  # 10ms delay
        return "success"

    # Import asyncio for sleep
    import asyncio

    # Temporarily replace sleep
    async def asyncio_sleep(duration):
        await asyncio.sleep(duration)

    initial_count = sum(
        sample.value
        for sample in metrics_module.redis_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("operation") == "test_op"
        and sample.name.endswith("_count")
    )

    result = await mock_redis_call()

    assert result == "success"

    final_count = sum(
        sample.value
        for sample in metrics_module.redis_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("operation") == "test_op"
        and sample.name.endswith("_count")
    )

    assert final_count > initial_count


@pytest.mark.asyncio()
async def test_time_redis_operation_decorator_exception():
    """Redis timing decorator should record latency even on exception."""

    @metrics_module.time_redis_operation("failing_op")
    async def mock_failing_redis_call():
        await asyncio_sleep(0.005)
        raise ValueError("Redis connection failed")

    import asyncio

    async def asyncio_sleep(duration):
        await asyncio.sleep(duration)

    initial_count = sum(
        sample.value
        for sample in metrics_module.redis_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("operation") == "failing_op"
        and sample.name.endswith("_count")
    )

    with pytest.raises(ValueError, match="Redis connection failed"):
        await mock_failing_redis_call()

    final_count = sum(
        sample.value
        for sample in metrics_module.redis_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("operation") == "failing_op"
        and sample.name.endswith("_count")
    )

    # Should still record latency despite exception
    assert final_count > initial_count


@pytest.mark.asyncio()
async def test_time_redis_operation_preserves_function_metadata():
    """Decorator should preserve original function name and docstring."""

    @metrics_module.time_redis_operation("meta_test")
    async def test_function():
        """Test docstring."""
        return 42

    assert test_function.__name__ == "test_function"
    assert test_function.__doc__ == "Test docstring."


# =============================================================================
# Decorator Tests: time_api_call
# =============================================================================


@pytest.mark.asyncio()
async def test_time_api_call_decorator_success():
    """API timing decorator should record successful call latency."""

    @metrics_module.time_api_call("test_endpoint")
    async def mock_api_call():
        await asyncio_sleep(0.02)  # 20ms delay
        return {"status": "ok"}

    import asyncio

    async def asyncio_sleep(duration):
        await asyncio.sleep(duration)

    initial_count = sum(
        sample.value
        for sample in metrics_module.api_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("endpoint") == "test_endpoint"
        and sample.name.endswith("_count")
    )

    result = await mock_api_call()

    assert result == {"status": "ok"}

    final_count = sum(
        sample.value
        for sample in metrics_module.api_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("endpoint") == "test_endpoint"
        and sample.name.endswith("_count")
    )

    assert final_count > initial_count


@pytest.mark.asyncio()
async def test_time_api_call_decorator_exception():
    """API timing decorator should record latency even on exception."""

    @metrics_module.time_api_call("failing_endpoint")
    async def mock_failing_api_call():
        await asyncio_sleep(0.01)
        raise ConnectionError("Backend unavailable")

    import asyncio

    async def asyncio_sleep(duration):
        await asyncio.sleep(duration)

    initial_count = sum(
        sample.value
        for sample in metrics_module.api_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("endpoint") == "failing_endpoint"
        and sample.name.endswith("_count")
    )

    with pytest.raises(ConnectionError, match="Backend unavailable"):
        await mock_failing_api_call()

    final_count = sum(
        sample.value
        for sample in metrics_module.api_latency_seconds.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
        and sample.labels.get("endpoint") == "failing_endpoint"
        and sample.name.endswith("_count")
    )

    # Should still record latency despite exception
    assert final_count > initial_count


@pytest.mark.asyncio()
async def test_time_api_call_preserves_function_metadata():
    """Decorator should preserve original function name and docstring."""

    @metrics_module.time_api_call("meta_test")
    async def test_api_function():
        """Test API docstring."""
        return "result"

    assert test_api_function.__name__ == "test_api_function"
    assert test_api_function.__doc__ == "Test API docstring."


@pytest.mark.asyncio()
async def test_time_api_call_with_args_and_kwargs():
    """Decorator should work with functions that have arguments."""

    @metrics_module.time_api_call("parameterized_endpoint")
    async def mock_api_with_params(user_id: str, role: str, timeout: float = 1.0):
        await asyncio_sleep(0.005)
        return f"user={user_id}, role={role}, timeout={timeout}"

    import asyncio

    async def asyncio_sleep(duration):
        await asyncio.sleep(duration)

    result = await mock_api_with_params("user123", "admin", timeout=2.0)
    assert result == "user=user123, role=admin, timeout=2.0"


# =============================================================================
# Update Resource Metrics Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_update_resource_metrics_syncs_connection_counter():
    """update_resource_metrics should sync ws_connections gauge from connection_counter."""
    from apps.web_console_ng.core.health import connection_counter

    # Set connection counter to known value
    connection_counter._count = 5

    # Update metrics
    await metrics_module.update_resource_metrics()

    # Verify gauge was updated
    value = metrics_module.ws_connections.labels(pod=config.POD_NAME)._value.get()
    assert value == 5


@pytest.mark.asyncio()
async def test_update_resource_metrics_handles_zero_connections():
    """update_resource_metrics should handle zero connections correctly."""
    from apps.web_console_ng.core.health import connection_counter

    connection_counter._count = 0

    await metrics_module.update_resource_metrics()

    value = metrics_module.ws_connections.labels(pod=config.POD_NAME)._value.get()
    assert value == 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


def test_multiple_record_state_save_errors():
    """Multiple error recordings should accumulate correctly."""
    initial_count = sum(
        sample.value
        for sample in metrics_module.state_save_errors_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
    )

    for i in range(5):
        metrics_module.record_state_save_error(f"error_{i}")

    final_count = sum(
        sample.value
        for sample in metrics_module.state_save_errors_total.collect()[0].samples
        if sample.labels.get("pod") == config.POD_NAME
    )

    assert final_count >= initial_count + 5


def test_record_auth_failure_different_types():
    """Auth failures should be tracked separately by auth_type."""
    metrics_module.record_auth_failure("password", "invalid")
    metrics_module.record_auth_failure("mfa", "timeout")
    metrics_module.record_auth_failure("sso", "redirect_error")

    samples = metrics_module.auth_failures_total.collect()[0].samples
    auth_types = {s.labels.get("auth_type", "") for s in samples if s.labels.get("pod") == config.POD_NAME}

    assert "password" in auth_types
    assert "mfa" in auth_types
    assert "sso" in auth_types


def test_ws_disconnects_total_has_reason_label():
    """WebSocket disconnect counter should have reason label."""
    assert "reason" in metrics_module.ws_disconnects_total._labelnames


def test_connections_rejected_total_has_reason_label():
    """Connection rejection counter should have reason label."""
    assert "reason" in metrics_module.connections_rejected_total._labelnames


def test_sessions_created_total_has_auth_type_label():
    """Session creation counter should have auth_type label."""
    assert "auth_type" in metrics_module.sessions_created_total._labelnames


# =============================================================================
# Module Exports Tests
# =============================================================================


def test_all_exports_are_defined():
    """All items in __all__ should be defined in the module."""
    for name in metrics_module.__all__:
        assert hasattr(metrics_module, name), f"{name} not defined but listed in __all__"


def test_metrics_objects_exported():
    """Key metrics objects should be in __all__."""
    expected_metrics = [
        "ws_connections",
        "ws_connects_total",
        "ws_disconnects_total",
        "api_latency_seconds",
        "redis_latency_seconds",
        "circuit_breaker_state",
    ]
    for metric in expected_metrics:
        assert metric in metrics_module.__all__


def test_helper_functions_exported():
    """Helper functions should be in __all__."""
    expected_functions = [
        "record_state_save_error",
        "record_auth_failure",
        "set_circuit_breaker_state",
        "time_redis_operation",
        "time_api_call",
        "update_resource_metrics",
    ]
    for func in expected_functions:
        assert func in metrics_module.__all__

"""Prometheus metrics for NiceGUI web console."""

from __future__ import annotations

import functools
import re
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from nicegui import app
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

from apps.web_console_ng import config
from apps.web_console_ng.core.health import is_internal_request

P = ParamSpec("P")
T = TypeVar("T")

POD_NAME = config.POD_NAME

ws_connections = Gauge(
    "nicegui_ws_connections",
    "Current WebSocket connections",
    ["pod"],
)

ws_connects_total = Counter(
    "nicegui_ws_connects_total",
    "Total WebSocket connects",
    ["pod"],
)

ws_disconnects_total = Counter(
    "nicegui_ws_disconnects_total",
    "Total WebSocket disconnects",
    ["pod", "reason"],
)

connections_rejected_total = Counter(
    "nicegui_connections_rejected_total",
    "Connections rejected by admission control",
    ["pod", "reason"],
)

auth_failures_total = Counter(
    "nicegui_auth_failures_total",
    "Authentication failures",
    ["pod", "auth_type", "reason"],
)

sessions_created_total = Counter(
    "nicegui_sessions_created_total",
    "Sessions created",
    ["pod", "auth_type"],
)

api_latency_seconds = Histogram(
    "nicegui_api_latency_seconds",
    "Backend API latency",
    ["pod", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

redis_latency_seconds = Histogram(
    "nicegui_redis_latency_seconds",
    "Redis operation latency",
    ["pod", "operation"],
    # Include 0.0005 (500µs) bucket for high-performance networks or localhost
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

state_save_errors_total = Counter(
    "nicegui_state_save_errors_total",
    "Errors while saving user state",
    ["pod", "reason"],
)

circuit_breaker_state = Gauge(
    "nicegui_circuit_breaker_state",
    "Circuit breaker state (1=tripped, 0=normal)",
    ["pod"],
)

audit_flush_errors_total = Counter(
    "nicegui_audit_flush_errors_total",
    "Errors while flushing audit logs to database",
    ["pod"],
)


@app.get("/metrics")
async def metrics_endpoint(request: Request) -> Response:
    """Expose Prometheus metrics, guarded for internal access."""
    if not config.METRICS_INGRESS_PROTECTED and not is_internal_request(request):
        return Response(status_code=403, content="Forbidden", media_type="text/plain")

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _sanitize_label_value(value: str, *, fallback: str = "unknown") -> str:
    """Normalize label values to avoid raw exception messages."""
    if not value:
        return fallback
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "_", normalized)
    normalized = normalized.strip("_")
    return normalized or fallback


def record_state_save_error(reason: str) -> None:
    """Increment state save error counter with sanitized reason."""
    safe_reason = _sanitize_label_value(reason)
    state_save_errors_total.labels(pod=POD_NAME, reason=safe_reason).inc()


def record_auth_failure(auth_type: str, reason: str) -> None:
    """Increment auth failure counter with sanitized reason."""
    safe_reason = _sanitize_label_value(reason)
    auth_failures_total.labels(pod=POD_NAME, auth_type=auth_type, reason=safe_reason).inc()


def set_circuit_breaker_state(state: str | int | bool) -> None:
    """Set circuit breaker gauge (1=tripped, 0=normal)."""
    if isinstance(state, bool):
        value = 1 if state else 0
    elif isinstance(state, int):
        value = 1 if state else 0
    else:
        normalized = state.strip().upper()
        value = 1 if normalized in {"TRIPPED", "OPEN", "ENGAGED", "ON"} else 0
    circuit_breaker_state.labels(pod=POD_NAME).set(value)


def time_redis_operation(
    operation: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator to time Redis operations."""

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                redis_latency_seconds.labels(pod=POD_NAME, operation=operation).observe(duration)

        return wrapper

    return decorator


def time_api_call(
    endpoint: str,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator to time API calls."""

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                api_latency_seconds.labels(pod=POD_NAME, endpoint=endpoint).observe(duration)

        return wrapper

    return decorator


async def update_resource_metrics() -> None:
    """Sync gauges that rely on in-memory counters."""
    from apps.web_console_ng.core.health import connection_counter

    ws_connections.labels(pod=POD_NAME).set(connection_counter.value)


__all__ = [
    "ws_connections",
    "ws_connects_total",
    "ws_disconnects_total",
    "connections_rejected_total",
    "auth_failures_total",
    "sessions_created_total",
    "api_latency_seconds",
    "redis_latency_seconds",
    "state_save_errors_total",
    "circuit_breaker_state",
    "audit_flush_errors_total",
    "record_state_save_error",
    "record_auth_failure",
    "set_circuit_breaker_state",
    "time_redis_operation",
    "time_api_call",
    "update_resource_metrics",
]

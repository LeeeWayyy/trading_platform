"""System Health Monitor page (T7.2).

Provides real-time monitoring of microservices, infrastructure connectivity,
and latency metrics with graceful degradation and auto-refresh.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh  # type: ignore[import-untyped]

from apps.web_console.auth.operations_auth import operations_requires_auth
from apps.web_console.config import (
    AUTO_REFRESH_INTERVAL,
    FEATURE_HEALTH_MONITOR,
    PROMETHEUS_URL,
    SERVICE_URLS,
)
from apps.web_console.services.health_service import (
    ConnectivityStatus,
    HealthMonitorService,
)
from libs.health.health_client import HealthClient, ServiceHealthResponse
from libs.health.prometheus_client import LatencyMetrics, PrometheusClient
from libs.redis_client import RedisClient
from libs.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


def _get_redis_client() -> RedisClient:
    """Get or create Redis client (cached in Streamlit session)."""

    if "health_redis_client" not in st.session_state:
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD")
        st.session_state["health_redis_client"] = RedisClient(
            host=host, port=port, db=db, password=password
        )
    return cast(RedisClient, st.session_state["health_redis_client"])


def _get_db_pool() -> Any:
    """Get database connection pool for Postgres connectivity checks."""

    try:
        from apps.web_console.utils.sync_db_pool import get_sync_db_pool

        return get_sync_db_pool()
    except Exception as exc:  # Narrowed by caller warning/logging
        logger.warning("Failed to get DB pool for health check: %s", exc)
        return None


def _get_health_service(db_pool: Any = None) -> HealthMonitorService:
    """Get or create health monitor service (cached in Streamlit session).

    Args:
        db_pool: Optional database pool to use. If provided, will be used
                 instead of creating a new one. Allows callers to reuse
                 existing pools.

    Note:
        If the cached service has db_pool=None but a pool is now available,
        the service is recreated to pick up the new pool. This handles the
        case where Postgres was unavailable during initial page load.
    """
    # Check if we need to recreate service due to db_pool becoming available
    if "health_service" in st.session_state:
        cached_service = cast(HealthMonitorService, st.session_state["health_service"])
        effective_db_pool = db_pool if db_pool is not None else _get_db_pool()
        # Recreate if cached has no pool but one is now available
        if cached_service.db_pool is None and effective_db_pool is not None:
            del st.session_state["health_service"]

    if "health_service" not in st.session_state:
        health_client = HealthClient(SERVICE_URLS)
        prometheus_client = PrometheusClient(PROMETHEUS_URL)
        redis_client = _get_redis_client()
        # Use provided db_pool if available, otherwise create new one
        effective_db_pool = db_pool if db_pool is not None else _get_db_pool()

        st.session_state["health_service"] = HealthMonitorService(
            health_client=health_client,
            prometheus_client=prometheus_client,
            redis_client=redis_client,
            db_pool=effective_db_pool,
        )

    return cast(HealthMonitorService, st.session_state["health_service"])


def _status_color(status: str) -> str:
    """Map status to color name for badges."""

    return {
        "healthy": "green",
        "degraded": "orange",
        "unhealthy": "red",
        "stale": "yellow",
        "unreachable": "gray",
        "unknown": "gray",
    }.get(status.lower(), "gray")


def _format_relative_time(timestamp: datetime | None) -> str:
    """Format timestamp as human-readable relative time."""

    if not timestamp:
        return "unknown"
    now = datetime.now(UTC)
    delta = now - timestamp
    seconds = delta.total_seconds()
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    return f"{seconds / 3600:.1f}h ago"


def _staleness_color(age_seconds: float | None) -> str:
    """Get color based on data staleness age."""

    if age_seconds is None:
        return "gray"
    if age_seconds < 30:
        return "green"
    if age_seconds < 120:
        return "orange"
    return "red"


def _render_service_grid(statuses: dict[str, ServiceHealthResponse]) -> None:
    """Render service status grid with staleness indicators."""

    st.subheader("Service Status")
    cols = st.columns(3)

    for idx, (service, health) in enumerate(statuses.items()):
        col = cols[idx % 3]
        with col:
            status_emoji = {
                "healthy": ":white_check_mark:",
                "degraded": ":warning:",
                "unhealthy": ":x:",
                "stale": ":hourglass:",
                "unreachable": ":no_entry:",
            }.get(health.status, ":question:")

            st.markdown(f"### {status_emoji} {service}")
            st.markdown(f"**Status:** {health.status.upper()}")
            st.caption(f"Response: {health.response_time_ms:.1f}ms")

            if health.is_stale:
                age_str = (
                    f"{health.stale_age_seconds:.0f}s"
                    if health.stale_age_seconds is not None
                    else "unknown"
                )
                st.warning(f":hourglass: **STALE DATA** ({age_str} old)", icon="⚠️")
                st.caption("Using cached response - service may be unreachable")

            if health.last_operation_timestamp:
                last_op = _format_relative_time(health.last_operation_timestamp)
                st.caption(f"Last operation: {last_op}")

            if health.error and not health.is_stale:
                st.error(health.error)

            if health.details:
                with st.expander("Details"):
                    for key, value in health.details.items():
                        if key not in {"status", "service", "timestamp", "cached_at"}:
                            st.text(f"{key}: {value}")


def _render_connectivity(connectivity: ConnectivityStatus) -> None:
    """Render infrastructure connectivity indicators."""

    st.subheader("Infrastructure")
    col1, col2 = st.columns(2)

    with col1:
        redis_status = (
            ":white_check_mark: Connected" if connectivity.redis_connected else ":x: Disconnected"
        )
        st.markdown(f"**Redis:** {redis_status}")
        if connectivity.redis_info:
            st.caption(f"Version: {connectivity.redis_info.get('redis_version', 'unknown')}")
            st.caption(f"Memory: {connectivity.redis_info.get('used_memory_human', 'unknown')}")
        if connectivity.redis_error:
            st.caption(f"Error: {connectivity.redis_error}")

    with col2:
        pg_status = (
            ":white_check_mark: Connected"
            if connectivity.postgres_connected
            else ":x: Disconnected"
        )
        st.markdown(f"**PostgreSQL:** {pg_status}")
        if connectivity.postgres_latency_ms:
            st.caption(f"Latency: {connectivity.postgres_latency_ms:.1f}ms")
        if connectivity.postgres_error:
            st.caption(f"Error: {connectivity.postgres_error}")

    st.caption(f"Last checked: {connectivity.checked_at.isoformat()}")


def _render_queue_depth() -> None:
    """Render queue depth placeholder (feature deferred to C2.1)."""

    st.subheader("Signal Queue Depth")
    st.info("Queue depth metrics pending infrastructure approval")
    st.caption("Enable after ADR-012 approval and Redis Streams deployment (C2.1)")


def _render_latency_charts(latencies: dict[str, LatencyMetrics]) -> None:
    """Render latency metrics with multi-series charts for P50/P95/P99."""

    st.subheader("Latency Metrics (P50/P95/P99)")

    if not latencies:
        st.info("No latency data available")
        return

    data: list[dict[str, Any]] = []
    for service, metrics in latencies.items():
        if metrics.p50_ms is not None:
            data.append(
                {
                    "Service": service,
                    "Operation": metrics.operation,
                    "P50 (ms)": metrics.p50_ms,
                    "P95 (ms)": metrics.p95_ms or 0,
                    "P99 (ms)": metrics.p99_ms or 0,
                }
            )
        elif metrics.error:
            data.append(
                {
                    "Service": service,
                    "Operation": metrics.operation,
                    "P50 (ms)": None,
                    "P95 (ms)": None,
                    "P99 (ms)": None,
                    "Error": metrics.error,
                }
            )

    if not data:
        st.warning("Latency metrics unavailable - Prometheus may be unreachable")
        return

    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)

    chart_data = df[df["P50 (ms)"].notna()].set_index("Service")[
        ["P50 (ms)", "P95 (ms)", "P99 (ms)"]
    ]
    if not chart_data.empty:
        st.bar_chart(chart_data)
        st.caption("Latency in milliseconds - lower is better")
    else:
        st.warning("No numeric latency data available for chart")


@dataclass
class HealthData:
    """Container for all health data with staleness tracking."""

    statuses: dict[str, ServiceHealthResponse]
    connectivity: ConnectivityStatus
    latencies: dict[str, LatencyMetrics]
    latencies_stale: bool
    latencies_age: float | None


async def _fetch_all_health_data(health_service: HealthMonitorService) -> HealthData:
    """Fetch all health data concurrently using asyncio.gather.

    Note: Concurrency is managed within individual clients (HealthClient, PrometheusClient)
    rather than at this orchestration level to avoid event loop issues with Streamlit's
    asyncio.run() pattern.
    """
    statuses, connectivity, latency_result = await asyncio.gather(
        health_service.get_all_services_status(),
        health_service.get_connectivity(),
        health_service.get_latency_metrics(),
        return_exceptions=True,
    )

    # Handle exceptions from gather(return_exceptions=True)
    # Note: We check for Exception (not BaseException) to avoid masking
    # critical signals like SystemExit or KeyboardInterrupt
    statuses_result: dict[str, ServiceHealthResponse]
    if isinstance(statuses, Exception):
        logger.warning("Failed to fetch service statuses: %s", statuses)
        statuses_result = {}
    else:
        statuses_result = cast(dict[str, ServiceHealthResponse], statuses)

    connectivity_result: ConnectivityStatus
    if isinstance(connectivity, Exception):
        logger.warning("Failed to fetch connectivity: %s", connectivity)
        connectivity_result = ConnectivityStatus(
            redis_connected=False,
            redis_info=None,
            postgres_connected=False,
            postgres_latency_ms=None,
            checked_at=datetime.now(UTC),
        )
    else:
        connectivity_result = cast(ConnectivityStatus, connectivity)

    latencies_stale = False
    latencies_age: float | None = None
    latencies: dict[str, LatencyMetrics] = {}
    if isinstance(latency_result, Exception):
        logger.warning("Failed to fetch latencies: %s", latency_result)
    elif isinstance(latency_result, tuple):
        latencies, latencies_stale, latencies_age = latency_result

    return HealthData(
        statuses=statuses_result,
        connectivity=connectivity_result,
        latencies=latencies,
        latencies_stale=latencies_stale,
        latencies_age=latencies_age,
    )


@operations_requires_auth
def render_health_monitor(user: dict[str, Any], db_pool: Any) -> None:
    """Render the System Health Monitor page."""

    if not FEATURE_HEALTH_MONITOR:
        st.info("System Health Monitor feature is disabled.")
        st.caption("Set FEATURE_HEALTH_MONITOR=true to enable.")
        return

    if not has_permission(user, Permission.VIEW_CIRCUIT_BREAKER):
        st.error("Permission denied: VIEW_CIRCUIT_BREAKER required")
        st.stop()

    st.title("System Health Monitor")

    # Auto-refresh using configured interval (default 10s)
    st_autorefresh(interval=AUTO_REFRESH_INTERVAL * 1000, key="health_autorefresh")

    health_service = _get_health_service(db_pool=db_pool)

    async def _fetch_and_close() -> HealthData:
        """Fetch health data and close HTTP clients to prevent event loop issues."""
        try:
            return await _fetch_all_health_data(health_service)
        finally:
            # Close HTTP clients after each fetch to prevent lifecycle issues
            # with Streamlit's asyncio.run() creating new event loops
            await health_service.close()

    try:
        health_data = asyncio.run(_fetch_and_close())
    except Exception as exc:  # pragma: no cover - Streamlit runtime guard
        st.error(f"Error fetching health data: {exc}")
        return

    _render_service_grid(health_data.statuses)
    st.divider()
    _render_connectivity(health_data.connectivity)
    st.divider()
    _render_queue_depth()
    st.divider()
    _render_latency_charts(health_data.latencies)

    if health_data.latencies_stale and health_data.latencies_age:
        st.caption(
            f":hourglass: Latency data is {health_data.latencies_age:.0f}s old (Prometheus unavailable)"
        )


def main() -> None:
    """Entry point for direct page access."""

    user = dict(st.session_state)
    render_health_monitor(user=user, db_pool=None)


__all__ = ["render_health_monitor", "main"]

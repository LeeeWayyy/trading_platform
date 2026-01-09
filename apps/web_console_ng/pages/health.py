"""System Health Monitor page for NiceGUI web console (P5T7).

Provides real-time monitoring of microservices, infrastructure connectivity,
and latency metrics with graceful degradation and auto-refresh.

Features:
    - Service status grid (3 columns) with staleness indicators
    - Infrastructure connectivity status (Redis, PostgreSQL)
    - Latency metrics table with P50/P95/P99
    - Auto-refresh with timer lifecycle cleanup

PARITY: Mirrors apps/web_console/pages/health.py functionality
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from nicegui import app, ui

from apps.web_console_ng import config
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from apps.web_console.services.health_service import HealthMonitorService

logger = logging.getLogger(__name__)


def _get_health_service() -> HealthMonitorService:
    """Get HealthMonitorService with async dependencies (global cache).

    ⚠️ Uses app.storage (global) for service singleton.
    ⚠️ HealthMonitorService is ASYNC - call methods directly (NOT run.io_bound).
    See P5T7_TASK.md Note #31.
    """
    if not hasattr(app.storage, "_health_service"):
        from apps.web_console.services.health_service import HealthMonitorService
        from apps.web_console_ng.config import PROMETHEUS_URL, REDIS_URL, SERVICE_URLS
        from libs.health.health_client import HealthClient
        from libs.health.prometheus_client import PrometheusClient
        from libs.redis_client import RedisClient

        # Get async DB pool for async health checks
        async_db_pool = get_db_pool()

        # Create RedisClient for health monitoring (HealthMonitorService expects RedisClient, not redis.Redis)
        # Parse REDIS_URL to get connection params, or use None for graceful degradation
        redis_client = None
        if REDIS_URL:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(REDIS_URL)
                redis_client = RedisClient(
                    host=parsed.hostname or "localhost",
                    port=parsed.port or 6379,
                    db=int(parsed.path.lstrip("/") or "0"),
                    password=parsed.password,
                )
            except Exception as e:
                logger.warning("redis_client_init_failed", extra={"error": str(e)})

        health_client = HealthClient(SERVICE_URLS)
        prometheus_client = PrometheusClient(PROMETHEUS_URL)

        setattr(  # noqa: B010
            app.storage,
            "_health_service",
            HealthMonitorService(
                health_client=health_client,
                prometheus_client=prometheus_client,
                redis_client=redis_client,
                db_pool=async_db_pool,
            ),
        )

    service: HealthMonitorService = getattr(app.storage, "_health_service")  # noqa: B009
    return service


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


@ui.page("/health")
@requires_auth
@main_layout
async def health_page() -> None:
    """System Health Monitor page."""
    user = get_current_user()

    # Feature flag check
    if not config.FEATURE_HEALTH_MONITOR:
        ui.label("System Health Monitor feature is disabled.").classes("text-lg")
        ui.label("Set FEATURE_HEALTH_MONITOR=true to enable.").classes("text-gray-500")
        return

    # Permission check (VIEW_REPORTS covers health monitoring visibility)
    if not has_permission(user, Permission.VIEW_REPORTS):
        ui.label("Permission denied: VIEW_REPORTS required").classes("text-red-500 text-lg")
        return

    # Get service (ASYNC - call methods directly, NOT run.io_bound)
    health_service = _get_health_service()

    # State for UI
    service_statuses: dict[str, Any] = {}
    connectivity: Any = None
    latency_data: dict[str, Any] = {}
    latency_stale: bool = False

    fetch_lock = asyncio.Lock()

    async def fetch_health_data() -> None:
        nonlocal service_statuses, connectivity, latency_data, latency_stale
        if fetch_lock.locked():
            return
        async with fetch_lock:
            try:
                # ⚠️ HealthMonitorService is ASYNC - call methods directly (see Note #31)
                # DO NOT use run.io_bound - it returns un-awaited coroutine
                statuses, conn, latencies = await asyncio.gather(
                    health_service.get_all_services_status(),
                    health_service.get_connectivity(),
                    health_service.get_latency_metrics(),
                    return_exceptions=True,
                )

                if isinstance(statuses, BaseException):
                    logger.warning("service_status_fetch_failed", extra={"error": str(statuses)})
                    service_statuses = {}
                else:
                    service_statuses = dict(statuses)

                if isinstance(conn, BaseException):
                    logger.warning("connectivity_fetch_failed", extra={"error": str(conn)})
                    connectivity = None
                else:
                    connectivity = conn

                if isinstance(latencies, BaseException):
                    logger.warning("latency_fetch_failed", extra={"error": str(latencies)})
                    latency_data = {}
                    latency_stale = True
                else:
                    latency_tuple = latencies  # type: tuple[dict[str, Any], bool, Any]
                    latency_data, latency_stale, _ = latency_tuple

            except Exception as e:
                logger.exception("health_data_fetch_failed", extra={"error": str(e)})

    # Initial fetch
    await fetch_health_data()

    # Page title
    ui.label("System Health Monitor").classes("text-2xl font-bold mb-4")

    # Service status section
    ui.label("Service Status").classes("text-xl font-bold mb-2")

    @ui.refreshable
    def service_grid() -> None:
        if not service_statuses:
            ui.label("No service status available").classes("text-gray-500")
            return

        with ui.row().classes("w-full gap-4 flex-wrap"):
            for service_name, health in service_statuses.items():
                status = getattr(health, "status", "unknown")
                status_colors = {
                    "healthy": "bg-green-100 border-green-500 text-green-700",
                    "degraded": "bg-yellow-100 border-yellow-500 text-yellow-700",
                    "unhealthy": "bg-red-100 border-red-500 text-red-700",
                    "stale": "bg-gray-100 border-gray-500 text-gray-700",
                    "unreachable": "bg-red-100 border-red-500 text-red-700",
                }
                color_class = status_colors.get(status, "bg-gray-100 border-gray-500")

                status_icons = {
                    "healthy": "check_circle",
                    "degraded": "warning",
                    "unhealthy": "cancel",
                    "stale": "hourglass_empty",
                    "unreachable": "block",
                }
                icon = status_icons.get(status, "help")

                with ui.card().classes(f"p-4 border-2 {color_class} min-w-48"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon(icon).classes("text-2xl")
                        ui.label(service_name).classes("font-bold")
                    ui.label(status.upper()).classes("text-sm font-medium")

                    # Show details if available
                    checked_at = getattr(health, "checked_at", None)
                    if checked_at:
                        ui.label(f"Checked: {_format_relative_time(checked_at)}").classes(
                            "text-xs text-gray-500"
                        )

                    # Show stale indicator
                    is_stale = getattr(health, "is_stale", False)
                    if is_stale:
                        ui.label("(stale)").classes("text-xs text-yellow-600")

    service_grid()

    ui.separator().classes("my-4")

    # Infrastructure connectivity section
    ui.label("Infrastructure Connectivity").classes("text-xl font-bold mb-2")

    @ui.refreshable
    def connectivity_section() -> None:
        if connectivity is None:
            ui.label("Connectivity status unavailable").classes("text-gray-500")
            return

        with ui.row().classes("w-full gap-4"):
            # Redis status
            with ui.card().classes("p-4 flex-1"):
                redis_ok = getattr(connectivity, "redis_connected", False)
                redis_color = "text-green-600" if redis_ok else "text-red-600"
                redis_icon = "check_circle" if redis_ok else "cancel"

                with ui.row().classes("items-center gap-2"):
                    ui.icon(redis_icon).classes(f"text-2xl {redis_color}")
                    ui.label("Redis").classes("font-bold text-lg")

                ui.label("Connected" if redis_ok else "Disconnected").classes(redis_color)

                redis_error = getattr(connectivity, "redis_error", None)
                if redis_error:
                    ui.label(f"Error: {redis_error}").classes("text-sm text-red-500")

            # PostgreSQL status
            with ui.card().classes("p-4 flex-1"):
                pg_ok = getattr(connectivity, "postgres_connected", False)
                pg_color = "text-green-600" if pg_ok else "text-red-600"
                pg_icon = "check_circle" if pg_ok else "cancel"

                with ui.row().classes("items-center gap-2"):
                    ui.icon(pg_icon).classes(f"text-2xl {pg_color}")
                    ui.label("PostgreSQL").classes("font-bold text-lg")

                ui.label("Connected" if pg_ok else "Disconnected").classes(pg_color)

                pg_latency = getattr(connectivity, "postgres_latency_ms", None)
                if pg_latency:
                    ui.label(f"Latency: {pg_latency:.1f}ms").classes("text-sm text-gray-500")

                pg_error = getattr(connectivity, "postgres_error", None)
                if pg_error:
                    ui.label(f"Error: {pg_error}").classes("text-sm text-red-500")

        # Staleness indicator
        conn_stale = getattr(connectivity, "is_stale", False)
        if conn_stale:
            stale_age = getattr(connectivity, "stale_age_seconds", None)
            stale_text = f"Data is stale ({stale_age:.0f}s old)" if stale_age else "Data is stale"
            ui.label(stale_text).classes("text-sm text-yellow-600 mt-2")

    connectivity_section()

    ui.separator().classes("my-4")

    # Latency metrics section
    ui.label("Service Latency Metrics").classes("text-xl font-bold mb-2")

    @ui.refreshable
    def latency_section() -> None:
        if not latency_data:
            ui.label("No latency data available").classes("text-gray-500")
            if latency_stale:
                ui.label("(data may be stale)").classes("text-sm text-yellow-600")
            return

        has_latency = False
        for metrics in latency_data.values():
            if any(
                getattr(metrics, name, None) is not None for name in ("p50_ms", "p95_ms", "p99_ms")
            ):
                has_latency = True
                break

        if not has_latency:
            ui.label("Latency metrics pending (no data yet)").classes("text-gray-500")
            return

        # Build table data
        columns = [
            {"name": "service", "label": "Service", "field": "service", "sortable": True},
            {"name": "p50", "label": "P50 (ms)", "field": "p50", "sortable": True},
            {"name": "p95", "label": "P95 (ms)", "field": "p95", "sortable": True},
            {"name": "p99", "label": "P99 (ms)", "field": "p99", "sortable": True},
        ]

        rows = []
        for service_name, metrics in latency_data.items():
            p50 = getattr(metrics, "p50_ms", None)
            p95 = getattr(metrics, "p95_ms", None)
            p99 = getattr(metrics, "p99_ms", None)

            rows.append({
                "service": service_name,
                "p50": f"{p50:.1f}" if p50 else "-",
                "p95": f"{p95:.1f}" if p95 else "-",
                "p99": f"{p99:.1f}" if p99 else "-",
            })

        ui.table(columns=columns, rows=rows).classes("w-full")

        if latency_stale:
            ui.label("(latency data may be stale)").classes("text-sm text-yellow-600 mt-2")

    latency_section()

    # Auto-refresh
    async def auto_refresh() -> None:
        await fetch_health_data()
        service_grid.refresh()
        connectivity_section.refresh()
        latency_section.refresh()

    # ⚠️ Rev 19: Timer lifecycle cleanup (see Note #29)
    timer = ui.timer(config.AUTO_REFRESH_INTERVAL, auto_refresh)

    # Register cleanup on client disconnect to prevent timer leaks
    client_id = ui.context.client.storage.get("client_id")
    if client_id:
        lifecycle_mgr = ClientLifecycleManager.get()
        await lifecycle_mgr.register_cleanup_callback(client_id, lambda: timer.cancel())


__all__ = ["health_page"]

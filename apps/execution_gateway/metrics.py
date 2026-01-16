"""Prometheus metrics definitions for Execution Gateway.

This module centralizes all Prometheus metric definitions, replacing scattered
metric definitions in main.py with a single source of truth.

Design Rationale:
    - Single source of truth for metrics
    - Prevents metric name/label drift
    - Easier to maintain and audit
    - Enables metrics contract testing

Usage:
    from apps.execution_gateway.metrics import orders_total, database_connection_status

    orders_total.labels(symbol="AAPL", side="buy", status="success").inc()
    database_connection_status.set(1)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ============================================================================
# Business Metrics
# ============================================================================

orders_total = Counter(
    "execution_gateway_orders_total",
    "Total number of orders submitted",
    ["symbol", "side", "status"],  # status: success, failed, rejected, blocked
)

order_placement_duration = Histogram(
    "execution_gateway_order_placement_duration_seconds",
    "Time taken to place an order",
    ["symbol", "side"],
)

# Latency histogram for shared health dashboard (no service prefix)
order_placement_duration_seconds = Histogram(
    "order_placement_duration_seconds",
    "Time taken to place an order",
)

fat_finger_warnings_total = Counter(
    "execution_gateway_fat_finger_warnings_total",
    "Total fat-finger threshold warnings",
    ["threshold_type"],
)

fat_finger_rejections_total = Counter(
    "execution_gateway_fat_finger_rejections_total",
    "Total fat-finger threshold rejections",
    ["threshold_type"],
)

positions_current = Gauge(
    "execution_gateway_positions_current",
    "Current open positions by symbol",
    ["symbol"],
)

pnl_dollars = Gauge(
    "execution_gateway_pnl_dollars",
    "P&L in dollars",
    ["type"],  # Label values: realized, unrealized, total
)

# ============================================================================
# Service Health Metrics
# ============================================================================

database_connection_status = Gauge(
    "execution_gateway_database_connection_status",
    "Database connection status (1=up, 0=down)",
)

redis_connection_status = Gauge(
    "execution_gateway_redis_connection_status",
    "Redis connection status (1=up, 0=down)",
)

alpaca_connection_status = Gauge(
    "execution_gateway_alpaca_connection_status",
    "Alpaca connection status (1=up, 0=down)",
)

alpaca_api_requests_total = Counter(
    "execution_gateway_alpaca_api_requests_total",
    "Total Alpaca API requests",
    ["operation", "status"],  # operation: submit_order, check_connection; status: success, error
)

webhook_received_total = Counter(
    "execution_gateway_webhook_received_total",
    "Total webhooks received",
    ["event_type"],
)

dry_run_mode = Gauge(
    "execution_gateway_dry_run_mode",
    "DRY_RUN mode status (1=enabled, 0=disabled)",
)

# ============================================================================
# Metrics Initialization
# ============================================================================


def initialize_metrics(dry_run: bool) -> None:
    """Initialize metric default values.

    This function sets initial values for gauges that track service status.
    Should be called during application startup after configuration is loaded.

    Args:
        dry_run: Whether the application is running in dry-run mode

    Note:
        Connection status metrics default to 0 (down) and will be updated
        by health checks and lifespan events.
    """
    dry_run_mode.set(1 if dry_run else 0)
    database_connection_status.set(0)  # Will be updated by health check
    redis_connection_status.set(0)  # Will be updated by health check
    alpaca_connection_status.set(0)  # Will be updated by health check


# ============================================================================
# Metric Names Registry (for contract testing)
# ============================================================================

# This list enables contract testing to verify that metric names remain
# stable across refactorings. Changing metric names breaks dashboards and
# alerts, so we explicitly track them here.
METRIC_NAMES = [
    "execution_gateway_orders_total",
    "execution_gateway_order_placement_duration_seconds",
    "order_placement_duration_seconds",
    "execution_gateway_fat_finger_warnings_total",
    "execution_gateway_fat_finger_rejections_total",
    "execution_gateway_positions_current",
    "execution_gateway_pnl_dollars",
    "execution_gateway_database_connection_status",
    "execution_gateway_redis_connection_status",
    "execution_gateway_alpaca_connection_status",
    "execution_gateway_alpaca_api_requests_total",
    "execution_gateway_webhook_received_total",
    "execution_gateway_dry_run_mode",
]

# Metric labels registry (for contract testing)
METRIC_LABELS = {
    "execution_gateway_orders_total": ["symbol", "side", "status"],
    "execution_gateway_order_placement_duration_seconds": ["symbol", "side"],
    "order_placement_duration_seconds": [],
    "execution_gateway_fat_finger_warnings_total": ["threshold_type"],
    "execution_gateway_fat_finger_rejections_total": ["threshold_type"],
    "execution_gateway_positions_current": ["symbol"],
    "execution_gateway_pnl_dollars": ["type"],
    "execution_gateway_database_connection_status": [],
    "execution_gateway_redis_connection_status": [],
    "execution_gateway_alpaca_connection_status": [],
    "execution_gateway_alpaca_api_requests_total": ["operation", "status"],
    "execution_gateway_webhook_received_total": ["event_type"],
    "execution_gateway_dry_run_mode": [],
}

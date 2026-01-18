"""Orphan order detection and handling.

This module handles detection and quarantine of orphan orders - orders
that exist at the broker but are not tracked in the local database.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import redis
from prometheus_client import Counter

from apps.execution_gateway.database import TERMINAL_STATUSES
from apps.execution_gateway.reconciliation.helpers import estimate_notional
from libs.core.redis_client.keys import RedisKeys

if TYPE_CHECKING:
    from apps.execution_gateway.database import DatabaseClient
    from libs.core.redis_client import RedisClient

logger = logging.getLogger(__name__)

# Pod label for Prometheus metrics
POD_LABEL = os.getenv("POD_NAME") or os.getenv("HOSTNAME") or "unknown"

# Prometheus metric (re-exported from main metrics module for compatibility)
symbols_quarantined_total = Counter(
    "execution_gateway_symbols_quarantined_total",
    "Total symbols quarantined due to orphan orders",
    ["pod", "symbol"],
)

# Strategy sentinel for external/unknown origin orders
QUARANTINE_STRATEGY_SENTINEL = "external"


def handle_orphan_order(
    broker_order: dict[str, Any],
    db_client: DatabaseClient,
    redis_client: RedisClient | None,
    resolve_terminal: bool = False,
) -> bool:
    """Handle an orphan order detected at the broker.

    Orphan orders are orders that exist at the broker but are not tracked
    in our database. This function:
    1. Creates an orphan order record in the database
    2. Sets quarantine for the symbol to block new trading
    3. Syncs orphan exposure to Redis

    Args:
        broker_order: Broker order data dict.
        db_client: Database client for orphan order operations.
        redis_client: Redis client for quarantine state. May be None.
        resolve_terminal: If True and order is terminal, mark as resolved.

    Returns:
        True if orphan was handled, False if skipped (missing required fields).

    Example:
        >>> handled = handle_orphan_order(
        ...     {"id": "abc", "symbol": "AAPL", "side": "buy", "qty": "100"},
        ...     db_client,
        ...     redis_client,
        ... )
        >>> print(f"Orphan handled: {handled}")
    """
    symbol = broker_order.get("symbol")
    if not symbol:
        return False

    broker_order_id = broker_order.get("id")
    if not broker_order_id:
        return False

    side = broker_order.get("side") or "unknown"
    qty = int(Decimal(str(broker_order.get("qty") or 0)))
    estimated_notional = estimate_notional(broker_order)

    status = broker_order.get("status") or "untracked"

    # Create orphan order record
    db_client.create_orphan_order(
        broker_order_id=str(broker_order_id),
        client_order_id=broker_order.get("client_order_id"),
        symbol=symbol,
        strategy_id=QUARANTINE_STRATEGY_SENTINEL,
        side=side,
        qty=qty,
        estimated_notional=estimated_notional,
        status=status,
    )

    # Update status and potentially resolve if terminal
    resolved_at = None
    if resolve_terminal and status in TERMINAL_STATUSES:
        resolved_at = datetime.now(UTC)
    db_client.update_orphan_order_status(
        broker_order_id=str(broker_order_id),
        status=status,
        resolved_at=resolved_at,
    )

    # Fail-closed quarantine for unknown strategy
    set_quarantine(symbol=symbol, strategy_id="*", redis_client=redis_client)

    # Update exposure cache for external sentinel
    sync_orphan_exposure(
        symbol=symbol,
        strategy_id=QUARANTINE_STRATEGY_SENTINEL,
        db_client=db_client,
        redis_client=redis_client,
    )

    logger.warning(
        "Orphan order detected and quarantined",
        extra={
            "broker_order_id": broker_order_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "status": status,
            "resolved": resolved_at is not None,
        },
    )

    return True


def set_quarantine(
    symbol: str,
    strategy_id: str,
    redis_client: RedisClient | None,
) -> bool:
    """Set quarantine flag for a symbol.

    Quarantine blocks new trading on the symbol until the orphan order
    is resolved.

    FAIL-CLOSED SAFETY: If this Redis WRITE fails, trading is still blocked
    because:
    1. The orphan order is persisted to the database (before this call)
    2. Order submission's quarantine CHECK (`_check_quarantine` in routes/orders.py)
       implements fail-closed semantics: if Redis is unavailable during the CHECK,
       trading is blocked with HTTP 503
    3. Therefore, a failed WRITE here doesn't create a safety gap - the CHECK
       path independently enforces fail-closed behavior

    Args:
        symbol: Symbol to quarantine.
        strategy_id: Strategy ID or "*" for all strategies.
        redis_client: Redis client. May be None.

    Returns:
        True if quarantine was set, False if skipped/failed.
    """
    if not redis_client:
        return False

    try:
        key = RedisKeys.quarantine(strategy_id=strategy_id, symbol=symbol)
        redis_client.set(key, "orphan_order_detected")
        symbols_quarantined_total.labels(pod=POD_LABEL, symbol=symbol).inc()
        return True
    except redis.RedisError as exc:
        logger.warning(
            "Failed to set quarantine key: Redis error",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "error": str(exc),
                "error_type": "redis",
            },
        )
        return False
    except ValueError as exc:
        logger.warning(
            "Failed to set quarantine key: Validation error",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "error": str(exc),
                "error_type": "validation",
            },
        )
        return False


def sync_orphan_exposure(
    symbol: str,
    strategy_id: str,
    db_client: DatabaseClient,
    redis_client: RedisClient | None,
) -> bool:
    """Sync orphan exposure to Redis cache.

    Fetches the current orphan exposure from the database and updates
    Redis for fast lookup during order validation.

    Args:
        symbol: Symbol to sync exposure for.
        strategy_id: Strategy ID (usually QUARANTINE_STRATEGY_SENTINEL).
        db_client: Database client for exposure query.
        redis_client: Redis client for cache update. May be None.

    Returns:
        True if exposure was synced, False if skipped/failed.
    """
    if not redis_client:
        return False

    try:
        import psycopg

        exposure = db_client.get_orphan_exposure(symbol, strategy_id)
        key = RedisKeys.orphan_exposure(strategy_id=strategy_id, symbol=symbol)
        redis_client.set(key, str(exposure))
        return True
    except (psycopg.OperationalError, psycopg.IntegrityError) as exc:
        logger.warning(
            "Failed to sync orphan exposure: Database error",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "error": str(exc),
                "error_type": "database",
            },
        )
        return False
    except redis.RedisError as exc:
        logger.warning(
            "Failed to sync orphan exposure: Redis error",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "error": str(exc),
                "error_type": "redis",
            },
        )
        return False
    except ValueError as exc:
        logger.warning(
            "Failed to sync orphan exposure: Validation error",
            extra={
                "symbol": symbol,
                "strategy_id": strategy_id,
                "error": str(exc),
                "error_type": "validation",
            },
        )
        return False


def detect_orphans(
    open_orders: list[dict[str, Any]],
    recent_orders: list[dict[str, Any]],
    db_known_ids: set[str],
    db_client: DatabaseClient,
    redis_client: RedisClient | None,
) -> int:
    """Detect and handle orphan orders.

    Scans open and recent orders from broker, identifies any not in DB,
    and handles them as orphans.

    Args:
        open_orders: List of open orders from broker.
        recent_orders: List of recently updated orders from broker.
        db_known_ids: Set of client_order_ids known to DB.
        db_client: Database client for orphan operations.
        redis_client: Redis client for quarantine. May be None.

    Returns:
        Count of orphans detected and handled.
    """
    orphan_count = 0

    # Check open orders (don't auto-resolve)
    for order in open_orders:
        client_id = order.get("client_order_id")
        if client_id and client_id in db_known_ids:
            continue
        if handle_orphan_order(order, db_client, redis_client, resolve_terminal=False):
            orphan_count += 1

    # Check recent orders (auto-resolve if terminal)
    for order in recent_orders:
        client_id = order.get("client_order_id")
        if client_id and client_id in db_known_ids:
            continue
        if handle_orphan_order(order, db_client, redis_client, resolve_terminal=True):
            orphan_count += 1

    return orphan_count

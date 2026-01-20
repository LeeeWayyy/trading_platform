"""Pure helper functions for reconciliation.

This module contains pure functions with no side effects. All functions are
stateless and depend only on their input parameters, making them trivially
testable with table-driven tests.

Target coverage: 95%+
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Any


def calculate_synthetic_fill(
    client_order_id: str,
    filled_qty: Decimal,
    filled_avg_price: Decimal,
    timestamp: datetime,
    existing_fills: list[dict[str, Any]],
    source: str,
) -> dict[str, Any] | None:
    """Calculate synthetic fill data if there's a quantity gap.

    This pure function determines if a synthetic fill is needed by comparing
    the broker's filled_qty against existing fills in the database. It calculates
    the gap and returns fill data if a synthetic fill should be injected.

    Args:
        client_order_id: Unique client order identifier.
        filled_qty: Total quantity filled according to broker.
        filled_avg_price: Average fill price from broker.
        timestamp: Timestamp for the synthetic fill.
        existing_fills: List of existing fill records from order metadata.
        source: Source identifier for fill_id generation (e.g., "recon", "recon_db").

    Returns:
        Fill data dict if a synthetic fill is needed, None otherwise.
        The returned dict includes a "_missing_qty" field for logging purposes
        that should be removed before storage.

    Example:
        >>> fill = calculate_synthetic_fill(
        ...     "order_123",
        ...     Decimal("100"),
        ...     Decimal("50.00"),
        ...     datetime.now(UTC),
        ...     [],
        ...     "recon"
        ... )
        >>> fill["fill_qty"]
        100
        >>> fill["synthetic"]
        True
    """
    # Count real (non-synthetic) fills separately to avoid double-counting
    real_fill_qty = Decimal("0")
    synthetic_fill_qty = Decimal("0")
    for fill in existing_fills:
        try:
            # Skip superseded fills - they were replaced by real fills
            if fill.get("superseded"):
                continue
            qty = Decimal(str(fill.get("fill_qty", 0)))
            if fill.get("synthetic"):
                synthetic_fill_qty += qty
            else:
                real_fill_qty += qty
        except (TypeError, ValueError, ArithmeticError):
            # ArithmeticError catches decimal.InvalidOperation for invalid strings
            continue

    filled_qty_dec = Decimal(str(filled_qty))

    # If real fills cover the broker's filled_qty, no need for synthetic
    if filled_qty_dec <= real_fill_qty:
        return None

    # Calculate missing based on real fills only (synthetic may be stale/duplicated)
    missing_qty = filled_qty_dec - real_fill_qty - synthetic_fill_qty

    # If total (real + synthetic) already covers broker qty, skip
    if missing_qty <= Decimal("0"):
        return None

    # For fractional shares, store as string to preserve precision
    qty_value: int | str = str(missing_qty) if missing_qty % 1 != 0 else int(missing_qty)

    # Use both filled_qty and missing_qty in fill_id to ensure uniqueness
    fill_id_filled = str(filled_qty_dec).replace(".", "_")
    fill_id_missing = str(missing_qty).replace(".", "_")

    return {
        "fill_id": f"{client_order_id}_{source}_{fill_id_filled}_{fill_id_missing}",
        "fill_qty": qty_value,
        "fill_price": str(filled_avg_price),
        "realized_pl": "0",  # Synthetic: actual P&L unknown
        "timestamp": timestamp.isoformat(),
        "synthetic": True,  # AUDIT: Mark as reconciliation-generated
        "source": source,
        "_missing_qty": missing_qty,  # For logging, stripped before storage
    }


def estimate_notional(broker_order: dict[str, Any]) -> Decimal:
    """Estimate order notional value from broker order data.

    Attempts to calculate notional using the following priority:
    1. Direct notional field if present
    2. qty * limit_price if limit order
    3. qty * filled_avg_price if partially/fully filled
    4. Returns 0 as fallback (quarantine will still block trading)

    Args:
        broker_order: Broker order data dict containing qty, limit_price,
            filled_avg_price, or notional fields.

    Returns:
        Estimated notional value as Decimal.

    Example:
        >>> estimate_notional({"qty": "100", "limit_price": "50.00"})
        Decimal('5000.00')
    """
    notional = broker_order.get("notional")
    if notional is not None:
        return Decimal(str(notional))

    qty = Decimal(str(broker_order.get("qty") or 0))
    limit_price = broker_order.get("limit_price")
    if limit_price is not None:
        return qty * Decimal(str(limit_price))

    filled_avg_price = broker_order.get("filled_avg_price")
    if filled_avg_price is not None:
        return qty * Decimal(str(filled_avg_price))

    # As a last resort, return 0 (quarantine will still block trading)
    return Decimal("0")


def generate_fill_id_from_activity(fill: dict[str, Any]) -> str:
    """Generate a deterministic fill ID from activity data.

    When the activity API doesn't provide an ID, generate one using
    multiple fields to avoid collisions.

    Args:
        fill: Fill activity data from Alpaca API.

    Returns:
        32-character deterministic ID based on fill data.

    Example:
        >>> generate_fill_id_from_activity({
        ...     "order_id": "abc123",
        ...     "symbol": "AAPL",
        ...     "qty": "10",
        ...     "price": "150.00"
        ... })
        '7f83b1657ff1fc53b92dc18148a1d65d'  # Example hash
    """
    fallback_parts = {
        "broker_order_id": str(fill.get("order_id") or ""),
        "symbol": str(fill.get("symbol") or ""),
        "side": str(fill.get("side") or ""),
        "qty": str(fill.get("qty") or ""),
        "price": str(fill.get("price") or ""),
        "transaction_time": str(fill.get("transaction_time") or ""),
        "activity_time": str(fill.get("activity_time") or ""),
        "id_hint": str(fill.get("id") or ""),
    }
    fallback_payload = "|".join(f"{key}={value}" for key, value in sorted(fallback_parts.items()))
    return hashlib.sha256(fallback_payload.encode()).hexdigest()[:32]


def merge_broker_orders(
    open_orders: list[dict[str, Any]],
    recent_orders: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Merge open and recent orders by client_order_id, preferring newest.

    When the same order appears in both lists, keep the one with the most
    recent updated_at timestamp.

    Args:
        open_orders: List of open orders from broker.
        recent_orders: List of recently updated orders from broker.

    Returns:
        Dict mapping client_order_id to the most recent order data.

    Example:
        >>> merge_broker_orders(
        ...     [{"client_order_id": "abc", "updated_at": "2024-01-01T10:00:00Z"}],
        ...     [{"client_order_id": "abc", "updated_at": "2024-01-01T11:00:00Z"}]
        ... )
        {'abc': {'client_order_id': 'abc', 'updated_at': '2024-01-01T11:00:00Z'}}
    """
    orders_by_client: dict[str, dict[str, Any]] = {}
    for order in open_orders + recent_orders:
        client_id = order.get("client_order_id")
        if not client_id:
            continue
        existing = orders_by_client.get(client_id)
        if not existing:
            orders_by_client[client_id] = order
            continue
        existing_updated = existing.get("updated_at") or existing.get("created_at")
        current_updated = order.get("updated_at") or order.get("created_at")
        # Prefer order with timestamp over one without, or newer timestamp if both have
        if current_updated:
            if not existing_updated or current_updated > existing_updated:
                orders_by_client[client_id] = order

    return orders_by_client


def extract_broker_client_ids(orders: list[dict[str, Any]]) -> list[str]:
    """Extract client_order_ids from broker orders.

    Args:
        orders: List of broker order dicts.

    Returns:
        List of non-None client_order_id values.
    """
    return [cid for order in orders if (cid := order.get("client_order_id")) is not None]

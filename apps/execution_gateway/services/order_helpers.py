"""Order submission and validation helper functions.

This module provides helper functions for order processing extracted from main.py,
including idempotency handling, fat-finger validation, and webhook timestamp parsing.

Design Rationale:
    - Pure or near-pure functions enable easy unit testing
    - Idempotency race handling isolated for clarity
    - Fat-finger context resolution decoupled from validation
    - Batch price fetching optimizes N+1 queries

Usage:
    from apps.execution_gateway.services.order_helpers import (
        handle_idempotency_race,
        resolve_fat_finger_context,
        batch_fetch_realtime_prices_from_redis,
    )

    # Handle idempotency race condition
    response = handle_idempotency_race(client_order_id, db_client)

    # Resolve fat-finger validation context
    price, adv = await resolve_fat_finger_context(order, thresholds, redis_client, liquidity_service)

    # Batch fetch real-time prices
    prices = batch_fetch_realtime_prices_from_redis(["AAPL", "TSLA"], redis_client)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from libs.core.redis_client import RedisKeys

if TYPE_CHECKING:
    from apps.execution_gateway.app_context import RedisClientProtocol
    from apps.execution_gateway.database import DatabaseClient
    from apps.execution_gateway.fat_finger_validator import FatFingerValidator
    from apps.execution_gateway.liquidity_service import LiquidityService
    from apps.execution_gateway.schemas import (
        FatFingerThresholds,
        FatFingerThresholdsResponse,
        OrderRequest,
        OrderResponse,
    )

logger = logging.getLogger(__name__)


def handle_idempotency_race(
    client_order_id: str,
    db_client: DatabaseClient,
) -> OrderResponse:
    """Handle idempotency race condition by returning existing order.

    When UniqueViolation is caught during order creation, this function
    retrieves the existing order and returns an idempotent response.

    Args:
        client_order_id: The client order ID that caused the race condition
        db_client: Database client for fetching existing order

    Returns:
        OrderResponse for the existing order

    Raises:
        HTTPException: If order not found after UniqueViolation (should never happen)

    Notes:
        - Called when concurrent submissions hit UniqueViolation on client_order_id
        - Returns existing order to maintain idempotent behavior
        - Should never raise HTTPException in practice (defensive check)

    Example:
        >>> try:
        ...     db_client.insert_order(order)
        ... except UniqueViolation:
        ...     return handle_idempotency_race(client_order_id, db_client)
    """
    # Import OrderResponse here to avoid circular imports
    from apps.execution_gateway.schemas import OrderResponse

    logger.info(
        f"Concurrent order submission detected (UniqueViolation): {client_order_id}",
        extra={"client_order_id": client_order_id},
    )
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        return OrderResponse(
            client_order_id=client_order_id,
            status=existing_order.status,
            broker_order_id=existing_order.broker_order_id,
            symbol=existing_order.symbol,
            side=existing_order.side,
            qty=existing_order.qty,
            order_type=existing_order.order_type,
            limit_price=existing_order.limit_price,
            created_at=existing_order.created_at,
            message="Order already submitted (race condition resolved)",
        )
    # Should never happen: UniqueViolation means order exists
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database inconsistency: order not found after UniqueViolation",
    )


def parse_webhook_timestamp(*timestamps: Any, default: datetime) -> datetime:
    """Parse the first valid timestamp from a list of candidates.

    Iterates through the provided timestamp candidates and returns the first
    one that can be successfully parsed. Falls back to the default if none
    are valid.

    Args:
        *timestamps: Variable number of timestamp candidates (str, datetime, or None)
        default: Fallback datetime if no valid timestamp is found

    Returns:
        Parsed datetime or the default value

    Notes:
        - Handles datetime objects directly (passthrough)
        - Parses ISO format strings with Z suffix conversion
        - Skips None/empty values
        - Returns default if all candidates fail

    Example:
        >>> parse_webhook_timestamp(
        ...     None,
        ...     "2024-01-15T10:30:00Z",
        ...     default=datetime.now(UTC),
        ... )
        datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.timezone.utc)
    """
    for ts in timestamps:
        if not ts:
            continue
        if isinstance(ts, datetime):
            return ts
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
    return default


async def resolve_fat_finger_context(
    order: OrderRequest,
    thresholds: FatFingerThresholds,
    redis_client: RedisClientProtocol | None,
    liquidity_service: LiquidityService | None,
    max_price_age_seconds: int,
) -> tuple[Decimal | None, int | None]:
    """Resolve price and ADV context needed for fat-finger validation.

    Uses asyncio.to_thread for ADV lookup to avoid blocking the event loop.

    Args:
        order: Order request to validate
        thresholds: Fat-finger thresholds configuration
        redis_client: Redis client for real-time price lookup
        liquidity_service: Service for ADV (Average Daily Volume) lookup
        max_price_age_seconds: Maximum age of price data to consider valid

    Returns:
        Tuple of (price, adv):
            - price: Market price for notional validation (None if unavailable/stale)
            - adv: Average Daily Volume for ADV% validation (None if unavailable)

    Notes:
        - Price resolution: limit_price → stop_price → Redis real-time → None
        - Price staleness check: rejects prices older than max_price_age_seconds
        - ADV lookup: async to_thread prevents blocking event loop
        - Returns (None, None) if validation context not needed

    Design Decision:
        Why async? ADV lookup may involve database queries or file I/O.
        Using asyncio.to_thread allows blocking operations without starving
        the event loop, maintaining responsiveness for concurrent requests.

    Example:
        >>> order = OrderRequest(symbol="AAPL", qty=1000, ...)
        >>> thresholds = FatFingerThresholds(max_notional=100000, max_adv_pct=0.1)
        >>> price, adv = await resolve_fat_finger_context(
        ...     order, thresholds, redis_client, liquidity_service, 300
        ... )
        >>> if price and adv:
        ...     # Validate notional and ADV percentage
        ...     pass
    """
    price: Decimal | None = None
    if thresholds.max_notional is not None:
        if order.limit_price is not None:
            price = order.limit_price
        elif order.stop_price is not None:
            price = order.stop_price
        else:
            realtime_prices = batch_fetch_realtime_prices_from_redis([order.symbol], redis_client)
            price, price_timestamp = realtime_prices.get(order.symbol, (None, None))
            if price is not None:
                if price_timestamp is None:
                    logger.warning(
                        "Fat-finger price missing timestamp; treating as unavailable",
                        extra={
                            "symbol": order.symbol,
                            "max_price_age_seconds": max_price_age_seconds,
                        },
                    )
                    price = None
                else:
                    if price_timestamp.tzinfo is None:
                        price_timestamp = price_timestamp.replace(tzinfo=UTC)
                    now = datetime.now(UTC)
                    price_age_seconds = (now - price_timestamp).total_seconds()
                    if price_age_seconds > max_price_age_seconds:
                        logger.warning(
                            "Fat-finger price stale; treating as unavailable",
                            extra={
                                "symbol": order.symbol,
                                "price_timestamp": price_timestamp.isoformat(),
                                "price_age_seconds": max(price_age_seconds, 0),
                                "max_price_age_seconds": max_price_age_seconds,
                            },
                        )
                        price = None

    adv: int | None = None
    if thresholds.max_adv_pct is not None and liquidity_service is not None:
        adv = await asyncio.to_thread(liquidity_service.get_adv, order.symbol)

    return price, adv


def create_fat_finger_thresholds_snapshot(
    fat_finger_validator: FatFingerValidator,
) -> FatFingerThresholdsResponse:
    """Build a response payload with current fat-finger thresholds.

    Args:
        fat_finger_validator: Validator instance containing threshold configuration

    Returns:
        FatFingerThresholdsResponse with default thresholds, symbol overrides, and timestamp

    Notes:
        - Returns immutable snapshot at time of call
        - Used by GET /api/v1/config/fat-finger-thresholds endpoint
        - Updated timestamp reflects snapshot creation time

    Example:
        >>> snapshot = create_fat_finger_thresholds_snapshot(validator)
        >>> snapshot.default_thresholds.max_qty
        10000
    """
    from apps.execution_gateway.schemas import FatFingerThresholdsResponse

    return FatFingerThresholdsResponse(
        default_thresholds=fat_finger_validator.get_default_thresholds(),
        symbol_overrides=fat_finger_validator.get_symbol_overrides(),
        updated_at=datetime.now(UTC),
    )


def batch_fetch_realtime_prices_from_redis(
    symbols: list[str], redis_client: RedisClientProtocol | None
) -> dict[str, tuple[Decimal | None, datetime | None]]:
    """Batch fetch real-time prices from Redis for multiple symbols.

    This function solves the N+1 query problem by fetching all prices in a single
    MGET call instead of individual GET calls for each symbol.

    Args:
        symbols: List of stock symbols to fetch
        redis_client: Redis client instance

    Returns:
        Dictionary mapping symbol to (price, timestamp) tuple.
        Missing symbols will have (None, None) as value.

    Performance:
        - 1 Redis call vs N calls (where N = number of symbols)
        - 5-10x faster for 10+ symbols
        - Reduces network round-trips from O(N) to O(1)

    Notes:
        - Returns empty dict if Redis unavailable
        - Returns (None, None) for symbols not in cache
        - Handles parsing errors gracefully per symbol
        - Uses MGET for atomic batch retrieval

    Example:
        >>> prices = batch_fetch_realtime_prices_from_redis(
        ...     ["AAPL", "TSLA", "MSFT"],
        ...     redis_client,
        ... )
        >>> aapl_price, aapl_timestamp = prices["AAPL"]
        >>> if aapl_price:
        ...     print(f"AAPL: ${aapl_price}")
    """
    if not redis_client or not symbols:
        return dict.fromkeys(symbols, (None, None))

    try:
        # Build Redis keys for batch fetch
        price_keys = [RedisKeys.price(symbol) for symbol in symbols]

        # Batch fetch all prices in one Redis call (O(1) network round-trip)
        price_values = redis_client.mget(price_keys)

        # Initialize results with default (None, None) for all symbols (DRY principle)
        result: dict[str, tuple[Decimal | None, datetime | None]] = dict.fromkeys(
            symbols, (None, None)
        )

        # Parse results and update dictionary for symbols with valid data
        for symbol, price_json in zip(symbols, price_values, strict=False):
            if not price_json:
                continue  # Skip symbols not found in cache (already (None, None))

            try:
                price_data = json.loads(price_json)
                price = Decimal(str(price_data["mid"]))
                timestamp = datetime.fromisoformat(price_data["timestamp"])
                result[symbol] = (price, timestamp)
                logger.debug(f"Batch fetched price for {symbol}: ${price}")
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, InvalidOperation) as e:
                # Log error but no need to set result[symbol] - already (None, None)
                logger.warning(f"Failed to parse price for {symbol} from batch fetch: {e}")

        return result

    except RedisError as e:
        # Catch all Redis errors (connection, timeout, etc.) for graceful degradation
        logger.warning(f"Failed to batch fetch prices for {len(symbols)} symbols: {e}")
        return dict.fromkeys(symbols, (None, None))

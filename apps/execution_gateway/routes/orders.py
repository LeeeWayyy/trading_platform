"""
Order management endpoints for Execution Gateway.

This module contains the core order submission, cancellation, and query endpoints.
These endpoints are extracted from main.py to improve modularity while preserving
exact safety gate ordering and business logic.

Key endpoints:
- POST /api/v1/orders - Submit orders with idempotency
- POST /api/v1/orders/{client_order_id}/cancel - Cancel orders
- GET /api/v1/orders/{client_order_id} - Query order status

Safety gates are preserved in exact order per REFACTOR_EXECUTION_GATEWAY_TASK.md:
1. Auth (as Depends() parameter)
2. Rate limiting (as Depends() parameter)
3. Kill-switch unavailable check (fail-closed)
4. Circuit breaker unavailable check (fail-closed)
5. Position reservation unavailable check (fail-closed)
6. Kill-switch engaged check
7. Circuit breaker tripped check
8. Quarantine check (Redis-based, fail-closed)
9. Reconciliation gate check (reduce-only during startup)
10. Position reservation (BEFORE idempotency per task doc)
11. Idempotency check (AFTER reservation)
12. Fat-finger validation
13. Order submission

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

See ADR-0014 for architecture decisions.
See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for design decisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg.errors
import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.errors import LockNotAvailable, UniqueViolation
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.api.dependencies import build_gateway_authenticator
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.database import (
    TERMINAL_STATUSES,
    status_rank_for,
)
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.fat_finger_validator import iter_breach_types
from apps.execution_gateway.order_id_generator import generate_client_order_id
from apps.execution_gateway.reconciliation import (
    SOURCE_PRIORITY_MANUAL,
)
from apps.execution_gateway.schemas import (
    TWAP_MIN_SLICE_NOTIONAL,
    TWAP_MIN_SLICE_QTY,
    TWAP_MIN_SLICES,
    OrderDetail,
    OrderModificationRecord,
    OrderModifyRequest,
    OrderModifyResponse,
    OrderRequest,
    OrderResponse,
    TWAPPreviewRequest,
    TWAPPreviewResponse,
    TWAPValidationException,
)
from apps.execution_gateway.services.order_helpers import resolve_fat_finger_context
from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.redis_client import RedisKeys
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    is_admin,
)

logger = logging.getLogger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter(prefix="/api/v1", tags=["Orders"])

# =============================================================================
# Auth and Rate Limiting Dependencies (Module Level)
# =============================================================================

order_submit_auth = api_auth(
    APIAuthConfig(
        action="order_submit",
        require_role=None,
        require_permission=Permission.SUBMIT_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_cancel_auth = api_auth(
    APIAuthConfig(
        action="order_cancel",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_read_auth = api_auth(
    APIAuthConfig(
        action="order_read",
        require_role=None,
        require_permission=Permission.VIEW_POSITIONS,
    ),
    authenticator_getter=build_gateway_authenticator,
)

# Order modify auth + rate limiting
order_modify_auth = api_auth(
    APIAuthConfig(
        action="order_modify",
        require_role=None,
        require_permission=Permission.MODIFY_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

# TWAP preview auth + rate limiting
order_preview_auth = api_auth(
    APIAuthConfig(
        action="order_preview",
        require_role=None,
        require_permission=Permission.SUBMIT_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

# Rate limiting dependencies
order_submit_rl = rate_limit(
    RateLimitConfig(
        action="order_submit",
        max_requests=40,
        window_seconds=60,
        burst_buffer=10,
        fallback_mode="deny",
        global_limit=80,
    )
)

order_cancel_rl = rate_limit(
    RateLimitConfig(
        action="order_cancel",
        max_requests=100,
        window_seconds=60,
        burst_buffer=20,
        fallback_mode="allow",
        global_limit=200,
    )
)

order_preview_rl = rate_limit(
    RateLimitConfig(
        action="order_preview",
        max_requests=100,
        window_seconds=60,
        burst_buffer=10,
        fallback_mode="allow",
        global_limit=200,
    )
)

order_modify_rl = rate_limit(
    RateLimitConfig(
        action="order_modify",
        max_requests=20,
        window_seconds=60,
        burst_buffer=5,
        fallback_mode="deny",
        global_limit=40,
    )
)


# =============================================================================
# Safety Gate Helpers (Redis-based, fail-closed)
# =============================================================================


async def _check_quarantine(
    symbol: str, strategy_id: str, ctx: AppContext, config: ExecutionGatewayConfig
) -> None:
    """Block trading when symbol is quarantined (Redis-based, fail-closed).

    Per REFACTOR_EXECUTION_GATEWAY_TASK.md, quarantine check uses Redis keys
    to determine if a symbol is blocked due to orphan order issues.
    """
    if config.dry_run:
        return
    if not ctx.redis:
        logger.error(
            "Redis unavailable for quarantine check; failing closed",
            extra={"symbol": symbol},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis unavailable for quarantine enforcement (fail-closed).",
            },
        )

    try:
        symbol = symbol.upper()
        strategy_key = RedisKeys.quarantine(strategy_id=strategy_id, symbol=symbol)
        wildcard_key = RedisKeys.quarantine(strategy_id="*", symbol=symbol)
        values = await asyncio.to_thread(ctx.redis.mget, [strategy_key, wildcard_key])
        strategy_value, wildcard_value = (values + [None, None])[:2] if values else (None, None)
        if strategy_value or wildcard_value:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Symbol quarantined",
                    "message": f"Trading blocked for {symbol} due to orphan order quarantine",
                    "symbol": symbol,
                },
            )
    except HTTPException:
        raise
    except RedisError as exc:
        logger.error(
            "Quarantine check failed - Redis error",
            extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis unavailable for quarantine enforcement (fail-closed).",
            },
        ) from exc
    except redis.exceptions.ConnectionError as exc:
        logger.error(
            "Quarantine check failed - Redis connection error",
            extra={"symbol": symbol, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis connection unavailable for quarantine enforcement (fail-closed).",
            },
        ) from exc
    except (TypeError, KeyError, AttributeError) as exc:
        logger.error(
            "Quarantine check failed - data access error",
            extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Data structure error during quarantine check (fail-closed).",
            },
        ) from exc


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _get_market_hours_warning(start: datetime, end: datetime) -> str | None:
    """Return warning if start/end are outside regular market hours (NYSE)."""
    market_tz = ZoneInfo("America/New_York")
    start_local = start.astimezone(market_tz)
    end_local = end.astimezone(market_tz)
    session_date = start_local.date()
    market_open = datetime.combine(
        session_date, datetime.strptime("09:30", "%H:%M").time(), market_tz
    )
    market_close = datetime.combine(
        session_date, datetime.strptime("16:00", "%H:%M").time(), market_tz
    )

    if start_local < market_open or end_local > market_close:
        return (
            "Schedule extends outside regular market hours. "
            "Execution will be constrained to market sessions at submission."
        )
    return None


async def _get_side_aware_quote(
    symbol: str,
    side: Literal["buy", "sell"],
    ctx: AppContext,
) -> Decimal | None:
    if not ctx.alpaca:
        return None
    try:
        quotes = await asyncio.to_thread(ctx.alpaca.get_latest_quotes, [symbol])
    except AlpacaConnectionError:
        return None
    quote = quotes.get(symbol, {})
    ask = quote.get("ask_price")
    bid = quote.get("bid_price")
    if side == "buy":
        return ask or bid
    return bid or ask


def _is_reconciliation_ready(ctx: AppContext, config: ExecutionGatewayConfig) -> bool:
    """Return True when startup reconciliation gate is open."""
    if config.dry_run:
        return True
    if ctx.reconciliation_service is None:
        return False
    return ctx.reconciliation_service.is_startup_complete()


def _calculate_pending_order_qty(open_orders: list[dict[str, Any]], order_side: str) -> Decimal:
    """Calculate total pending quantity for orders of a given side.

    Args:
        open_orders: List of open orders from Alpaca
        order_side: "buy" or "sell" to filter orders

    Returns:
        Total pending quantity for the specified side
    """
    total = Decimal("0")
    for order in open_orders:
        side = str(order.get("side", "")).lower()
        if side == order_side:
            # Use remaining qty if available, otherwise use qty
            qty = order.get("qty", 0)
            filled_qty = order.get("filled_qty", 0)
            remaining = Decimal(str(qty)) - Decimal(str(filled_qty))
            if remaining > 0:
                total += remaining
    return total


def _is_reduce_only_order(
    order: OrderRequest,
    broker_position: dict[str, Any] | None,
    open_orders: list[dict[str, Any]] | None = None,
) -> bool:
    """Check if an order would strictly reduce the current position without flipping.

    Per ADR-0020, during startup gating we allow risk-reducing orders computed
    from LIVE Alpaca data (not stale DB). An order is reduce-only if:
    1. The side is correct (sell for long, buy for short)
    2. The quantity doesn't exceed available position after accounting for pending orders

    Args:
        order: The order request
        broker_position: Live position from Alpaca, or None if flat
        open_orders: List of open orders from Alpaca (used to compute effective exposure)

    Returns:
        True if order strictly reduces position, False otherwise
    """
    if broker_position is None:
        # No position means any order would increase exposure
        return False

    current_qty = Decimal(str(broker_position.get("qty", 0)))
    if current_qty == 0:
        # Flat position, any order increases exposure
        return False

    order_side = order.side.lower()
    order_qty = Decimal(str(order.qty))

    # Calculate pending orders that affect available position
    pending_sells = Decimal("0")
    pending_buys = Decimal("0")
    if open_orders:
        pending_sells = _calculate_pending_order_qty(open_orders, "sell")
        pending_buys = _calculate_pending_order_qty(open_orders, "buy")

    # Long position (positive qty): only sell that doesn't exceed available position
    if current_qty > 0:
        if order_side != "sell":
            return False
        # Available to sell = current position minus already-committed pending sells.
        # (pending buys don't affect what we can sell now)
        available_to_sell = current_qty - pending_sells
        # Sell qty must not exceed available position (would flip to short)
        return order_qty <= available_to_sell

    # Short position (negative qty): only buy that doesn't exceed available position
    else:
        if order_side != "buy":
            return False
        # Available to cover = absolute short size minus already-committed pending buys.
        # (pending sells don't affect what we can cover now)
        available_to_cover = abs(current_qty) - pending_buys
        # Buy qty must not exceed available position (would flip to long)
        return order_qty <= available_to_cover


async def _require_reconciliation_ready_or_reduce_only(
    order: OrderRequest,
    ctx: AppContext,
    config: ExecutionGatewayConfig,
    client_order_id: str,
) -> None:
    """Gate order submissions during startup reconciliation, allowing reduce-only orders.

    Per ADR-0020 (Startup Gating with Reduce-Only Mode):
    - During gating, compute effective_position from LIVE Alpaca data (not stale DB)
    - Allow risk-reducing orders (orders that decrease position exposure)
    - Reject all orders if broker API unavailable (fail closed)

    Allowed paths:
    - Override active (operator manually unlocked)
    - Reconciliation complete
    - Dry-run mode
    - Reduce-only orders (computed from live broker position)
    """
    recon_service = ctx.reconciliation_service
    if recon_service and recon_service.override_active():
        logger.warning(
            "Reconciliation override active; allowing order",
            extra={
                "client_order_id": client_order_id,
                "override": recon_service.override_context(),
            },
        )
        return

    if _is_reconciliation_ready(ctx, config):
        return

    if recon_service and recon_service.startup_timed_out():
        logger.critical(
            "Startup reconciliation timed out; remaining in gated mode",
            extra={"elapsed_seconds": recon_service.startup_elapsed_seconds()},
        )

    # Per ADR-0020: Allow reduce-only orders during gating
    # Fetch LIVE position from broker (fail closed if unavailable)
    if ctx.alpaca is None:
        logger.error(
            "Alpaca client unavailable during reconciliation gate; blocking order (fail-closed)",
            extra={"client_order_id": client_order_id, "symbol": order.symbol},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Broker unavailable during reconciliation",
                "message": "Cannot verify position for reduce-only check; order blocked (fail-closed)",
            },
        )

    try:
        broker_position = ctx.alpaca.get_open_position(order.symbol)
    except Exception as exc:
        # Fail closed: if we can't determine position, block the order
        logger.error(
            "Failed to fetch broker position during reconciliation gate; blocking order (fail-closed)",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Broker unavailable during reconciliation",
                "message": "Cannot verify position for reduce-only check; order blocked (fail-closed)",
            },
        ) from exc

    # Fetch open orders for this symbol to account for pending exposure
    # This prevents allowing multiple reduce-only orders that would collectively flip the position
    open_orders: list[dict[str, Any]] = []
    try:
        open_orders = ctx.alpaca.get_orders(status="open", symbols=[order.symbol.upper()])
    except Exception as exc:
        # Log warning but continue with position-only check (fail-open for open orders)
        # The position check is still valid, just less precise
        logger.warning(
            "Failed to fetch open orders during reconciliation gate; proceeding with position-only check",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "error": str(exc),
            },
        )

    if _is_reduce_only_order(order, broker_position, open_orders):
        current_qty = broker_position.get("qty", 0) if broker_position else 0
        pending_info = {}
        if open_orders:
            pending_sells = _calculate_pending_order_qty(open_orders, "sell")
            pending_buys = _calculate_pending_order_qty(open_orders, "buy")
            pending_info = {
                "pending_sells": str(pending_sells),
                "pending_buys": str(pending_buys),
            }
        logger.info(
            "Allowing reduce-only order during reconciliation gating",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_qty": str(order.qty),
                "current_position_qty": str(current_qty),
                **pending_info,
            },
        )
        return

    # Block position-increasing orders during reconciliation
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "Reconciliation in progress",
            "message": "Position-increasing orders blocked until startup reconciliation completes. "
            "Reduce-only orders are allowed.",
        },
    )


# =============================================================================
# Order Modification Helpers
# =============================================================================

MODIFIABLE_STATUSES = {"pending_new", "new", "accepted", "partially_filled"}


def _generate_replacement_order_id(original_client_order_id: str, modification_seq: int) -> str:
    content = f"{original_client_order_id}:mod:{modification_seq}"
    return hashlib.sha256(content.encode()).hexdigest()[:24]


def _serialize_change_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _compute_modification_changes(
    order: OrderDetail,
    payload: OrderModifyRequest,
) -> dict[str, tuple[Any, Any]]:
    changes: dict[str, tuple[Any, Any]] = {}
    if payload.qty is not None and payload.qty != order.qty:
        changes["qty"] = (order.qty, payload.qty)
    if payload.limit_price is not None and payload.limit_price != order.limit_price:
        changes["limit_price"] = (
            _serialize_change_value(order.limit_price),
            _serialize_change_value(payload.limit_price),
        )
    if payload.stop_price is not None and payload.stop_price != order.stop_price:
        changes["stop_price"] = (
            _serialize_change_value(order.stop_price),
            _serialize_change_value(payload.stop_price),
        )
    if payload.time_in_force is not None and payload.time_in_force != order.time_in_force:
        changes["time_in_force"] = (order.time_in_force, payload.time_in_force)
    return changes


def _validate_modify_fields(order: OrderDetail, payload: OrderModifyRequest) -> None:
    errors: list[str] = []

    if payload.limit_price is not None and order.order_type not in ("limit", "stop_limit"):
        errors.append(f"limit_price not applicable for {order.order_type} orders")

    if payload.stop_price is not None and order.order_type not in ("stop", "stop_limit"):
        errors.append(f"stop_price not applicable for {order.order_type} orders")

    if order.order_type == "stop_limit":
        effective_stop = payload.stop_price if payload.stop_price is not None else order.stop_price
        effective_limit = (
            payload.limit_price if payload.limit_price is not None else order.limit_price
        )
        if effective_stop is not None and effective_limit is not None:
            if order.side == "buy" and effective_limit < effective_stop:
                errors.append(
                    f"Buy stop-limit requires limit_price >= stop_price "
                    f"(got limit={effective_limit}, stop={effective_stop})"
                )
            if order.side == "sell" and effective_limit > effective_stop:
                errors.append(
                    f"Sell stop-limit requires limit_price <= stop_price "
                    f"(got limit={effective_limit}, stop={effective_stop})"
                )

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="; ".join(errors)
        )


# Valid modification statuses for idempotent response handling
_MODIFICATION_STATUSES: set[str] = {"pending", "completed", "failed", "submitted_unconfirmed"}


def _handle_idempotent_modification_response(
    existing: dict[str, Any] | None,
    response: Response | None,
) -> OrderModifyResponse | None:
    """Handle idempotent responses for existing modifications.

    Returns OrderModifyResponse if existing record found:
    - completed: 200 OK
    - pending: 202 Accepted
    - submitted_unconfirmed: 202 Accepted (broker accepted, DB finalize pending)

    Raises HTTPException for failed records, or returns None if no existing record.
    """
    if not existing:
        return None

    status_value = str(existing.get("status") or "pending")
    # Validate status against known values and provide type-safe default
    _VALID_STATUSES: set[Literal["pending", "completed", "failed", "submitted_unconfirmed"]] = {
        "pending",
        "completed",
        "failed",
        "submitted_unconfirmed",
    }
    status_literal: Literal["pending", "completed", "failed", "submitted_unconfirmed"] = (
        status_value if status_value in _VALID_STATUSES else "pending"  # type: ignore[assignment]
    )
    idempotent_response = OrderModifyResponse(
        original_client_order_id=existing["original_client_order_id"],
        new_client_order_id=existing["new_client_order_id"],
        modification_id=str(existing["modification_id"]),
        modified_at=existing["modified_at"],
        status=status_literal,
        changes=existing.get("changes") or {},
    )
    if status_value == "completed":
        return idempotent_response
    if status_value in ("pending", "submitted_unconfirmed"):
        # Both pending and submitted_unconfirmed return 202:
        # - pending: modification in progress
        # - submitted_unconfirmed: broker accepted but DB finalize failed
        if response is not None:
            response.status_code = status.HTTP_202_ACCEPTED
        return idempotent_response
    # status == "failed"
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=existing.get("error_message") or "Previous modification failed",
    )


async def _is_strictly_risk_reducing(
    order: OrderDetail,
    payload: OrderModifyRequest,
    ctx: AppContext,
) -> bool:
    if order.execution_style == "twap" or order.parent_order_id is not None:
        return False

    if payload.qty is None:
        return False

    if payload.limit_price is not None and payload.limit_price != order.limit_price:
        return False
    if payload.stop_price is not None and payload.stop_price != order.stop_price:
        return False
    if payload.time_in_force is not None and payload.time_in_force != order.time_in_force:
        return False

    try:
        current_qty = ctx.db.get_position_by_symbol(order.symbol)
    except Exception as exc:
        logger.warning(
            "risk_reducing_check_position_fetch_failed",
            extra={"symbol": order.symbol, "error": str(exc), "error_type": type(exc).__name__},
        )
        return False

    old_pending = order.qty if order.side == "buy" else -order.qty
    new_pending = payload.qty if order.side == "buy" else -payload.qty
    old_projected = current_qty + old_pending
    new_projected = current_qty + new_pending

    return abs(new_projected) < abs(old_projected)


async def _check_modify_safety_gates(
    ctx: AppContext,
    order: OrderDetail,
    payload: OrderModifyRequest,
) -> None:
    kill_switch = ctx.recovery_manager.kill_switch
    circuit_breaker = ctx.recovery_manager.circuit_breaker

    if ctx.recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch state unavailable (fail-closed)",
        )
    if ctx.recovery_manager.is_circuit_breaker_unavailable() or circuit_breaker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Circuit-breaker state unavailable (fail-closed)",
        )

    safety_event = kill_switch.is_engaged() or circuit_breaker.is_tripped()
    if safety_event and not await _is_strictly_risk_reducing(order, payload, ctx):
        event_type = "kill switch" if kill_switch.is_engaged() else "circuit breaker"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Order modifications blocked: {event_type} engaged. "
                "Only strictly risk-reducing qty decreases allowed."
            ),
        )


def _validate_modify_position_limits(
    order: OrderDetail,
    payload: OrderModifyRequest,
    ctx: AppContext,
) -> None:
    if payload.qty is None or payload.qty <= order.qty:
        return

    max_position_size = ctx.risk_config.position_limits.max_position_size
    current_position = ctx.db.get_position_by_symbol(order.symbol)

    # Use remaining open qty (order.qty - filled_qty) instead of full order.qty
    # because current_position already reflects the filled quantity.
    # For partially filled orders, using full order.qty would double-count fills.
    filled_qty = int(order.filled_qty or 0)
    existing_open_qty = order.qty - filled_qty
    proposed_open_qty = payload.qty - filled_qty

    existing_projected = current_position + (
        existing_open_qty if order.side == "buy" else -existing_open_qty
    )
    proposed_projected = current_position + (
        proposed_open_qty if order.side == "buy" else -proposed_open_qty
    )

    if abs(proposed_projected) > max_position_size:
        delta = abs(proposed_projected) - abs(existing_projected)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Qty increase would exceed position limits. "
                f"Additional exposure: {int(delta)} shares."
            ),
        )


def _reserve_modify_delta(
    order: OrderDetail,
    payload: OrderModifyRequest,
    ctx: AppContext,
) -> str | None:
    """Reserve additional exposure for qty increases during modification."""
    if payload.qty is None or payload.qty <= order.qty:
        return None

    position_reservation = ctx.recovery_manager.position_reservation
    if ctx.recovery_manager.is_position_reservation_unavailable() or position_reservation is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Position-reservation state unavailable (fail-closed)",
        )

    try:
        current_position = ctx.db.get_position_by_symbol(order.symbol)
    except Exception as exc:
        logger.error(
            "Position lookup unavailable during modification reservation (fail-closed)",
            extra={
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Position lookup unavailable for reservation (fail-closed)",
        ) from exc

    delta_qty = payload.qty - order.qty
    max_position_size = ctx.risk_config.position_limits.max_position_size
    reservation_result = position_reservation.reserve(
        symbol=order.symbol,
        side=order.side,
        qty=delta_qty,
        max_limit=max_position_size,
        current_position=current_position,
    )

    if not reservation_result.success or reservation_result.token is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Position limit exceeded",
                "message": reservation_result.reason or "Position limit exceeded",
                "symbol": order.symbol,
                "max_position_size": max_position_size,
            },
        )

    return reservation_result.token


async def _validate_modify_fat_finger(
    order: OrderDetail,
    payload: OrderModifyRequest,
    ctx: AppContext,
    max_price_age_seconds: int,
) -> None:
    stop_price_increases_risk = False
    if payload.stop_price is not None and payload.stop_price != order.stop_price:
        if order.side == "sell":
            # Sell stop: lower stop triggers sooner (more risk), higher stop is less risk.
            if order.stop_price is None or payload.stop_price < order.stop_price:
                stop_price_increases_risk = True
        else:
            # Buy stop: higher stop triggers at higher price (more risk), lower stop is less risk.
            if order.stop_price is None or payload.stop_price > order.stop_price:
                stop_price_increases_risk = True

    exposure_increasing = (
        (payload.qty is not None and payload.qty > order.qty)
        or (
            payload.limit_price is not None
            and order.limit_price is not None
            and (
                (order.side == "buy" and payload.limit_price > order.limit_price)
                or (order.side == "sell" and payload.limit_price < order.limit_price)
            )
        )
        or stop_price_increases_risk
    )

    if not exposure_increasing:
        return

    effective_qty = payload.qty if payload.qty is not None else order.qty
    effective_limit = payload.limit_price if payload.limit_price is not None else order.limit_price
    effective_stop = payload.stop_price if payload.stop_price is not None else order.stop_price
    effective_tif = (
        payload.time_in_force if payload.time_in_force is not None else order.time_in_force
    )

    replacement_request = OrderRequest(
        symbol=order.symbol,
        side=order.side,
        qty=effective_qty,
        order_type=order.order_type,
        limit_price=effective_limit,
        stop_price=effective_stop,
        time_in_force=effective_tif,
        execution_style=order.execution_style or "instant",
    )

    thresholds = ctx.fat_finger_validator.get_effective_thresholds(order.symbol)
    price, adv = await resolve_fat_finger_context(
        replacement_request,
        thresholds,
        ctx.redis,
        ctx.liquidity_service,
        max_price_age_seconds,
    )
    result = ctx.fat_finger_validator.validate(
        symbol=order.symbol,
        qty=replacement_request.qty,
        price=price,
        adv=adv,
        thresholds=thresholds,
    )
    if result.breached:
        breach_list = ", ".join(iter_breach_types(result.breaches))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Modification would breach fat-finger limits: {breach_list}"
                if breach_list
                else "Modification would breach fat-finger limits"
            ),
        )


def _extract_user_id_from_auth(user: Any) -> str:
    """Extract user_id from auth context (handles both dataclass and dict).

    Args:
        user: AuthenticatedUser dataclass or dict from tests.

    Returns:
        User ID string, or "unknown" if not extractable.
    """
    if user is None:
        return "unknown"
    if hasattr(user, "user_id"):
        return str(user.user_id)
    if isinstance(user, dict):
        return str(user.get("user_id") or "unknown")
    return "unknown"


def _check_order_modification_eligibility(
    order: OrderDetail,
) -> None:
    """Validate that an order is eligible for modification.

    Checks:
    - Order status is modifiable (pending_new, new, accepted, partially_filled)
    - Order has broker_order_id (acknowledged by broker)
    - Order is not TWAP (TWAP orders cannot be modified)

    Args:
        order: The order to check.

    Raises:
        HTTPException: If order cannot be modified.
    """
    if order.status not in MODIFIABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Order cannot be modified in status '{order.status}'. "
                f"Only orders in {sorted(MODIFIABLE_STATUSES)} can be modified."
            ),
        )

    if order.broker_order_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order cannot be modified yet - awaiting broker acknowledgment.",
        )

    if (
        order.execution_style == "twap"
        or order.parent_order_id is not None
        or order.total_slices is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TWAP orders cannot be modified. Cancel and resubmit instead.",
        )


def _acquire_modification_lock(
    ctx: AppContext,
    client_order_id: str,
    order: OrderDetail,
    payload: OrderModifyRequest,
    changes: dict[str, tuple[Any, Any]],
    user_id: str,
    response: Response | None,
) -> tuple[str, str]:
    """Acquire modification lock and insert pending modification record (Phase 1).

    Uses advisory locking to ensure only one modification at a time per order.
    Handles idempotency via idempotency_key lookup.

    Args:
        ctx: Application context.
        client_order_id: Original order's client_order_id.
        order: The order being modified.
        payload: Modification request payload.
        changes: Computed changes dict.
        user_id: User initiating the modification.
        response: FastAPI response for setting status codes.

    Returns:
        Tuple of (modification_id, new_client_order_id).

    Raises:
        HTTPException: On lock contention or DB errors.
    """
    with ctx.db.transaction() as conn:
        # Double-check idempotency within transaction
        existing = ctx.db.get_modification_by_idempotency_key(
            client_order_id, payload.idempotency_key, conn=conn
        )
        idempotent_resp = _handle_idempotent_modification_response(existing, response)
        if idempotent_resp:
            # Return sentinel values to indicate idempotent response
            raise _IdempotentModificationException(idempotent_resp)

        try:
            modification_seq = ctx.db.get_next_modification_seq(client_order_id, conn)
        except LockNotAvailable:
            # Another modification in progress - check if it's same idempotency_key
            existing = ctx.db.get_modification_by_idempotency_key(
                client_order_id, payload.idempotency_key, conn=conn
            )
            idempotent_resp = _handle_idempotent_modification_response(existing, response)
            if idempotent_resp:
                raise _IdempotentModificationException(idempotent_resp) from None
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Order modification in progress. Retry with same idempotency_key.",
            ) from None

        new_client_order_id = _generate_replacement_order_id(client_order_id, modification_seq)
        modification_id = ctx.db.insert_pending_modification(
            original_client_order_id=client_order_id,
            new_client_order_id=new_client_order_id,
            original_broker_order_id=order.broker_order_id,
            modification_seq=modification_seq,
            idempotency_key=payload.idempotency_key,
            status="pending",
            modified_by=user_id,
            reason=payload.reason,
            changes=changes,
            conn=conn,
        )

    return modification_id, new_client_order_id


class _IdempotentModificationException(Exception):
    """Internal exception for idempotent modification responses."""

    def __init__(self, response: OrderModifyResponse) -> None:
        self.response = response
        super().__init__("Idempotent modification response")


def _call_broker_replace(
    ctx: AppContext,
    order: OrderDetail,
    payload: OrderModifyRequest,
    new_client_order_id: str,
    modification_id: str,
    reservation_token: str | None,
) -> tuple[str | None, str, dict[str, Any]]:
    """Execute broker replace call (Phase 2).

    Args:
        ctx: Application context.
        order: Original order being modified.
        payload: Modification request payload.
        new_client_order_id: Generated client_order_id for replacement.
        modification_id: DB modification record ID.
        reservation_token: Position reservation token (if qty increased).

    Returns:
        Tuple of (broker_order_id, broker_client_order_id, broker_response).

    Raises:
        HTTPException: On broker errors.

    Note:
        Caller must verify ctx.alpaca is not None and order.broker_order_id
        is not None before calling this function.
    """
    # These assertions should never fail - caller validates before calling
    assert ctx.alpaca is not None, "Alpaca client must be initialized"
    assert order.broker_order_id is not None, "Order must have broker_order_id"

    try:
        broker_response = ctx.alpaca.replace_order(
            order.broker_order_id,
            qty=payload.qty,
            limit_price=payload.limit_price,
            stop_price=payload.stop_price,
            time_in_force=payload.time_in_force,
            new_client_order_id=new_client_order_id,
        )
    except (AlpacaValidationError, AlpacaRejectionError, AlpacaConnectionError) as exc:
        # Release reservation on failure
        if reservation_token and ctx.recovery_manager.position_reservation:
            ctx.recovery_manager.position_reservation.release(order.symbol, reservation_token)
        ctx.db.update_modification_status(modification_id, status="failed", error_message=str(exc))
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
                if isinstance(exc, AlpacaRejectionError)
                else (
                    status.HTTP_400_BAD_REQUEST
                    if isinstance(exc, AlpacaValidationError)
                    else status.HTTP_503_SERVICE_UNAVAILABLE
                )
            ),
            detail=str(exc),
        ) from exc

    broker_order_id = broker_response.get("id")
    broker_client_order_id = broker_response.get("client_order_id") or new_client_order_id

    # Confirm reservation on success
    if reservation_token and ctx.recovery_manager.position_reservation:
        ctx.recovery_manager.position_reservation.confirm(order.symbol, reservation_token)

    if broker_client_order_id != new_client_order_id:
        logger.warning(
            "alpaca_replace_client_order_id_mismatch",
            extra={
                "client_order_id": order.client_order_id,
                "expected": new_client_order_id,
                "actual": broker_client_order_id,
            },
        )

    return broker_order_id, broker_client_order_id, broker_response


def _finalize_modification_in_db(
    ctx: AppContext,
    client_order_id: str,
    order: OrderDetail,
    modification_id: str,
    broker_order_id: str | None,
    broker_client_order_id: str,
    broker_response: dict[str, Any],
    replacement_request: OrderRequest,
) -> None:
    """Finalize modification records in database (Phase 3).

    Updates modification record, marks original order as replaced,
    and inserts the replacement order record.

    Args:
        ctx: Application context.
        client_order_id: Original order's client_order_id.
        order: Original order being modified.
        modification_id: DB modification record ID.
        broker_order_id: Broker's order ID for replacement.
        broker_client_order_id: Broker's client_order_id for replacement.
        broker_response: Full broker response dict.
        replacement_request: OrderRequest for the replacement.

    Raises:
        HTTPException: On DB errors (order is already live at broker).
    """
    try:
        with ctx.db.transaction() as conn:
            ctx.db.finalize_modification(
                modification_id,
                new_broker_order_id=broker_order_id,
                status="completed",
                new_client_order_id=broker_client_order_id,
                conn=conn,
            )
            ctx.db.update_order_status_simple_with_conn(client_order_id, "replaced", conn=conn)
            ctx.db.insert_replacement_order(
                client_order_id=broker_client_order_id,
                replaced_order_id=client_order_id,
                strategy_id=order.strategy_id,
                order_request=replacement_request,
                status=broker_response.get("status") or "pending_new",
                broker_order_id=broker_order_id,
                conn=conn,
            )
    except Exception as exc:
        logger.error(
            "Modification finalize failed after broker replacement",
            extra={
                "client_order_id": client_order_id,
                "modification_id": str(modification_id),
                "broker_order_id": broker_order_id,
                "broker_client_order_id": broker_client_order_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        try:
            ctx.db.update_modification_status(
                modification_id,
                status="submitted_unconfirmed",
                error_message=f"db_finalize_failed: {exc}",
            )
        except Exception as status_exc:
            logger.error(
                "Failed to mark modification as submitted_unconfirmed",
                extra={
                    "client_order_id": client_order_id,
                    "modification_id": str(modification_id),
                    "error": str(status_exc),
                    "error_type": type(status_exc).__name__,
                },
                exc_info=True,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Order replacement accepted by broker but failed to persist. "
                "Reconciliation required."
            ),
        ) from exc


# =============================================================================
# POST /api/v1/orders/twap-preview - Preview TWAP Plan
# =============================================================================


@router.post("/orders/twap-preview", response_model=TWAPPreviewResponse)
async def twap_preview(
    payload: TWAPPreviewRequest,
    _auth_context: AuthContext = Depends(order_preview_auth),
    _rate_limit_remaining: int = Depends(order_preview_rl),
    ctx: AppContext = Depends(get_context),
) -> TWAPPreviewResponse:
    """Preview TWAP slicing without creating orders."""
    authorized_strategies = get_authorized_strategies(_auth_context.user)
    if not authorized_strategies or payload.strategy_id not in authorized_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    try:
        slicing_plan = ctx.twap_slicer.plan(
            symbol=payload.symbol,
            side=payload.side,
            qty=payload.qty,
            duration_minutes=payload.duration_minutes,
            interval_seconds=payload.interval_seconds,
            order_type=payload.order_type,
            limit_price=payload.limit_price,
            time_in_force=payload.time_in_force,
        )
    except ValueError as exc:
        raise TWAPValidationException([str(exc)]) from exc

    slice_count = slicing_plan.total_slices
    base_slice_qty = payload.qty // slice_count if slice_count > 0 else 0
    if slice_count < TWAP_MIN_SLICES:
        raise TWAPValidationException(
            [f"TWAP requires at least {TWAP_MIN_SLICES} slices (got {slice_count})"]
        )
    if base_slice_qty < TWAP_MIN_SLICE_QTY:
        raise TWAPValidationException(
            [
                f"TWAP minimum slice size is {TWAP_MIN_SLICE_QTY} shares "
                f"(got {base_slice_qty} shares per slice)"
            ]
        )

    remainder_distribution = [
        slice_detail.slice_num
        for slice_detail in slicing_plan.slices
        if slice_detail.qty > base_slice_qty
    ]

    start_time = _normalize_utc(payload.start_time or datetime.now(UTC))
    scheduled_times_full = [
        start_time + timedelta(seconds=i * payload.interval_seconds) for i in range(slice_count)
    ]

    # Avoid importing timedelta at top-level to keep ordering stable
    if scheduled_times_full:
        first_slice_at = scheduled_times_full[0]
        last_slice_at = scheduled_times_full[-1]
    else:
        first_slice_at = start_time
        last_slice_at = start_time

    try:
        tzinfo = ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError:
        tzinfo = ZoneInfo("UTC")

    market_hours_warning = _get_market_hours_warning(first_slice_at, last_slice_at)

    scheduled_times = scheduled_times_full[:100]
    display_times = [ts.astimezone(tzinfo).isoformat() for ts in scheduled_times]

    estimated_duration_minutes = int(
        math.ceil((last_slice_at - first_slice_at).total_seconds() / 60)
    )

    slice_notional: Decimal | None = None
    notional_warning: str | None = None
    if payload.order_type == "limit":
        if payload.limit_price is None:
            raise TWAPValidationException(["limit_price required for limit orders"])
        slice_notional = payload.limit_price * base_slice_qty
        if slice_notional < TWAP_MIN_SLICE_NOTIONAL:
            raise TWAPValidationException(
                [
                    f"Slice notional ${slice_notional:.2f} below minimum "
                    f"${TWAP_MIN_SLICE_NOTIONAL}."
                ]
            )
    else:
        price = await _get_side_aware_quote(payload.symbol, payload.side, ctx)
        if price is None:
            # Design Decision #32: allow TWAP preview without quote during extended hours,
            # but require explicit user acknowledgement before submission.
            notional_warning = (
                "Market quote unavailable; slice notional cannot be validated. "
                "Explicit acknowledgement is required before submitting this TWAP. "
                "Order submission may fail if slice notional is below $500."
            )
        else:
            slice_notional = price * base_slice_qty
            if slice_notional < TWAP_MIN_SLICE_NOTIONAL:
                raise TWAPValidationException(
                    [
                        f"Slice notional ${slice_notional:.2f} below minimum "
                        f"${TWAP_MIN_SLICE_NOTIONAL}."
                    ]
                )

    return TWAPPreviewResponse(
        slice_count=slice_count,
        base_slice_qty=base_slice_qty,
        remainder_distribution=remainder_distribution,
        scheduled_times=scheduled_times,
        display_times=display_times,
        first_slice_at=first_slice_at,
        last_slice_at=last_slice_at,
        estimated_duration_minutes=estimated_duration_minutes,
        market_hours_warning=market_hours_warning,
        notional_warning=notional_warning,
        slice_notional=slice_notional,
        validation_errors=[],
    )


# =============================================================================
# POST /api/v1/orders - Submit Order
# =============================================================================


@router.post("/orders", response_model=OrderResponse)
async def submit_order(
    order: OrderRequest,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    _auth_context: AuthContext = Depends(order_submit_auth),
    _rate_limit_remaining: int = Depends(order_submit_rl),
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> OrderResponse:
    """
    Submit order with idempotent retry semantics.

    The order is assigned a deterministic client_order_id based on the order
    parameters and current date. This ensures that the same order submitted
    multiple times will have the same ID and won't create duplicates.

    In DRY_RUN mode (default), orders are logged to database but NOT submitted
    to Alpaca. Set DRY_RUN=false to enable actual paper trading.

    Safety Gate Order (per REFACTOR_EXECUTION_GATEWAY_TASK.md):
    1. Kill-switch unavailable (fail-closed)
    2. Circuit breaker unavailable (fail-closed)
    3. Position reservation unavailable (fail-closed)
    4. Kill-switch engaged
    5. Circuit breaker tripped
    6. Quarantine check (Redis-based)
    7. Reconciliation gate (reduce-only during startup)
    8. Position reservation (BEFORE idempotency)
    9. Idempotency check (AFTER reservation)
    10. Fat-finger validation
    11. Order submission

    Args:
        order: Order request (symbol, side, qty, order_type, etc.)
        response: FastAPI response object
        _auth_context: Authentication context (injected)
        _rate_limit_remaining: Rate limit remaining (injected)
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        OrderResponse with client_order_id, status, and broker_order_id

    Raises:
        HTTPException 400: Invalid order parameters
        HTTPException 422: Order rejected by broker
        HTTPException 503: Broker connection error
    """
    # Safety gating uses RecoveryManager (thread-safe, fail-closed)
    start_time = time.time()

    # Generate deterministic client_order_id
    client_order_id = generate_client_order_id(order, config.strategy_id)

    logger.info(
        f"Order request received: {order.symbol} {order.side} {order.qty}",
        extra={
            "client_order_id": client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "order_type": order.order_type,
        },
    )

    kill_switch = ctx.recovery_manager.kill_switch
    circuit_breaker = ctx.recovery_manager.circuit_breaker
    position_reservation = ctx.recovery_manager.position_reservation

    # =========================================================================
    # GATE 1: Kill-switch unavailable check (fail-closed for safety)
    # =========================================================================
    if ctx.recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
        logger.error(
            f"Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_unavailable": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state unknown (fail-closed for safety)",
                "fail_closed": True,
            },
        )

    # =========================================================================
    # GATE 2: Circuit breaker unavailable check (fail-closed)
    # =========================================================================
    if ctx.recovery_manager.is_circuit_breaker_unavailable() or circuit_breaker is None:
        logger.error(
            f"Order blocked by unavailable circuit-breaker (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "circuit_breaker_unavailable": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Circuit-breaker state unavailable (fail-closed for safety)",
        )

    # =========================================================================
    # GATE 3: Position reservation unavailable check (fail-closed)
    # =========================================================================
    if ctx.recovery_manager.is_position_reservation_unavailable() or position_reservation is None:
        logger.error(
            f"Order blocked by unavailable position-reservation (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "position_reservation_unavailable": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Position-reservation state unavailable (fail-closed for safety)",
        )

    # =========================================================================
    # GATE 4: Kill-switch engaged check
    # =========================================================================
    if kill_switch.is_engaged():
        logger.warning(
            f"Order blocked by kill-switch: {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_engaged": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch engaged - new orders blocked",
        )

    # =========================================================================
    # GATE 5: Circuit breaker tripped check
    # =========================================================================
    if circuit_breaker.is_tripped():
        logger.warning(
            f"Order blocked by circuit breaker: {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "circuit_breaker_tripped": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Circuit breaker tripped - trading paused",
        )

    # =========================================================================
    # TWAP orders are not supported on this endpoint
    # =========================================================================
    if order.execution_style == "twap":
        logger.warning(
            "TWAP order rejected on /api/v1/orders",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "twap_not_supported",
                "message": "TWAP orders must be submitted via /api/v1/manual/orders. "
                "The /api/v1/orders endpoint supports instant execution only.",
            },
        )

    # =========================================================================
    # GATE 6: Quarantine check (Redis-based, fail-closed)
    # =========================================================================
    await _check_quarantine(order.symbol, config.strategy_id, ctx, config)

    # =========================================================================
    # GATE 7: Reconciliation gate check (reduce-only during startup)
    # =========================================================================
    await _require_reconciliation_ready_or_reduce_only(order, ctx, config, client_order_id)

    # =========================================================================
    # GATE 8: Position reservation (BEFORE idempotency per task doc)
    # This ensures position limits are checked atomically even for concurrent
    # duplicate submissions. If duplicate found later, reservation is released.
    # =========================================================================
    # Get current position from DB for reservation fallback (handles Redis restart)
    # CRITICAL: Fail closed on DB error to prevent over-positioning
    try:
        current_position = ctx.db.get_position_by_symbol(order.symbol)
    except Exception as e:
        logger.error(
            f"DB position lookup failed, failing closed: {client_order_id}",
            extra={"client_order_id": client_order_id, "symbol": order.symbol, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Position lookup unavailable for reservation (fail-closed)",
        ) from e

    max_position_size = ctx.risk_config.position_limits.max_position_size
    reservation_result = position_reservation.reserve(
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        max_limit=max_position_size,
        current_position=current_position,
    )

    if not reservation_result.success:
        logger.warning(
            f"Order blocked by position limits: {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "reason": reservation_result.reason,
                "previous_position": reservation_result.previous_position,
                "max_limit": max_position_size,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Position limit exceeded",
                "message": reservation_result.reason,
                "symbol": order.symbol,
                "max_position_size": max_position_size,
            },
        )

    # Store token for release on error paths
    reservation_token = reservation_result.token
    if reservation_token is None:
        logger.error(
            "Position reservation missing token after successful reservation",
            extra={"client_order_id": client_order_id, "symbol": order.symbol},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Position reservation failed to return token",
        )
    logger.debug(
        f"Position reserved: {order.symbol} {order.side} {order.qty}",
        extra={
            "client_order_id": client_order_id,
            "reservation_token": reservation_token,
            "new_position": reservation_result.new_position,
        },
    )

    # =========================================================================
    # GATE 9: Idempotency check (AFTER reservation per task doc)
    # If duplicate found, release reservation and return existing order.
    # =========================================================================
    try:
        existing_order = ctx.db.get_order_by_client_id(client_order_id)
    except Exception as e:
        # Release reservation on DB error during idempotency check
        position_reservation.release(order.symbol, reservation_token)
        logger.error(
            f"DB error during idempotency check, releasing reservation: {client_order_id}",
            extra={"client_order_id": client_order_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable during idempotency check",
        ) from e

    if existing_order:
        # Release reservation for duplicate order
        position_reservation.release(order.symbol, reservation_token)
        logger.info(
            f"Order already exists (idempotent): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "status": existing_order.status,
                "broker_order_id": existing_order.broker_order_id,
            },
        )
        return OrderResponse(
            client_order_id=client_order_id,
            status=existing_order.status,
            broker_order_id=existing_order.broker_order_id,
            symbol=existing_order.symbol,
            side=existing_order.side,
            qty=existing_order.qty,
            order_type=existing_order.order_type,
            limit_price=existing_order.limit_price,
            stop_price=existing_order.stop_price,
            created_at=existing_order.created_at,
            message="Order already exists (idempotent retry)",
        )

    # =========================================================================
    # GATE 10: Fat-finger validation
    # =========================================================================
    thresholds = ctx.fat_finger_validator.get_effective_thresholds(order.symbol)
    price, adv = await resolve_fat_finger_context(
        order,
        thresholds,
        ctx.redis,
        ctx.liquidity_service,
        config.fat_finger_max_price_age_seconds,
    )
    fat_finger_result = ctx.fat_finger_validator.validate(
        symbol=order.symbol,
        qty=order.qty,
        price=price,
        adv=adv,
        thresholds=thresholds,
    )
    if fat_finger_result.breached:
        # Release reservation on fat-finger rejection
        position_reservation.release(order.symbol, reservation_token)
        breach_list = ", ".join(iter_breach_types(fat_finger_result.breaches))
        logger.warning(
            f"Order blocked by fat-finger validation: {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "symbol": order.symbol,
                "qty": order.qty,
                "breaches": breach_list,
                "fat_finger_blocked": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Order rejected by fat-finger checks: {breach_list}"
                if breach_list
                else "Order rejected by fat-finger checks"
            ),
        )

    # Insert order into database
    try:
        ctx.db.create_order(
            client_order_id=client_order_id,
            strategy_id=config.strategy_id,
            order_request=order,
            status="dry_run" if config.dry_run else "pending_new",
        )
    except UniqueViolation:
        # Race condition: another request inserted same order - release reservation
        position_reservation.release(order.symbol, reservation_token)
        logger.info(
            f"Order already exists (race condition): {client_order_id}",
            extra={"client_order_id": client_order_id},
        )
        order_detail = ctx.db.get_order_by_client_id(client_order_id)
        if order_detail:
            return OrderResponse(
                client_order_id=client_order_id,
                status=order_detail.status,
                broker_order_id=order_detail.broker_order_id,
                symbol=order_detail.symbol,
                side=order_detail.side,
                qty=order_detail.qty,
                order_type=order_detail.order_type,
                limit_price=order_detail.limit_price,
                stop_price=order_detail.stop_price,
                created_at=order_detail.created_at,
                message="Order already exists (concurrent retry)",
            )
        raise
    except Exception as e:
        # Release reservation on any DB insert failure
        position_reservation.release(order.symbol, reservation_token)
        logger.error(
            f"DB insert failed, releasing reservation: {client_order_id}",
            extra={"client_order_id": client_order_id, "error": str(e)},
        )
        raise

    # =========================================================================
    # Phase 2: Submit to broker (OUTSIDE DB transaction per task doc)
    # Broker call protected by idempotent client_order_id
    # CRITICAL: Once broker accepts, NEVER release reservation (order is live)
    # =========================================================================
    broker_order_id = None
    broker_accepted = False  # Track if broker accepted to prevent unsafe release

    if not config.dry_run:
        if not ctx.alpaca:
            # Release reservation on error (broker not reached)
            position_reservation.release(order.symbol, reservation_token)
            logger.error(
                f"Alpaca client not initialized: {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials.",
            )

        try:
            # Submit order to broker using OrderRequest object
            broker_response = ctx.alpaca.submit_order(order, client_order_id)
            broker_order_id = broker_response.get("id")
            broker_accepted = True  # Mark accepted IMMEDIATELY after broker returns
            logger.info(
                f"Order submitted to broker: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "broker_order_id": broker_order_id,
                },
            )

            # CRITICAL: Confirm reservation IMMEDIATELY after broker acceptance
            # This MUST happen before DB update to minimize the crash window where
            # a live order could lose its reservation due to TTL expiry.
            position_reservation.confirm(order.symbol, reservation_token)

            # Update order with broker_order_id (best effort - reconciliation catches failures)
            try:
                ctx.db.update_order_status(
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    status="pending_new",
                )
            except Exception as db_error:
                # DB update failed but broker accepted - log for reconciliation
                # Reservation already confirmed above, order is live
                logger.error(
                    f"DB update failed after broker acceptance, order requires reconciliation: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "broker_order_id": broker_order_id,
                        "error": str(db_error),
                    },
                )

        except ValueError as e:
            # Release reservation on local validation error (broker not reached)
            position_reservation.release(order.symbol, reservation_token)
            logger.warning(
                f"Order validation error: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "error": str(e),
                },
            )
            ctx.db.update_order_status_cas(
                client_order_id=client_order_id,
                status="rejected",
                broker_updated_at=datetime.now(UTC),
                status_rank=status_rank_for("rejected"),
                source_priority=SOURCE_PRIORITY_MANUAL,
                filled_qty=Decimal("0"),
                filled_avg_price=None,
                filled_at=None,
                broker_order_id=None,
                error_message=f"Order validation failed: {e}",
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Order validation failed: {e}",
            ) from e

        except AlpacaValidationError as e:
            # Release reservation on broker validation error (broker rejected)
            position_reservation.release(order.symbol, reservation_token)
            logger.warning(
                f"Order validation error: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "error": str(e),
                },
            )
            ctx.db.update_order_status_cas(
                client_order_id=client_order_id,
                status="rejected",
                broker_updated_at=datetime.now(UTC),
                status_rank=status_rank_for("rejected"),
                source_priority=SOURCE_PRIORITY_MANUAL,
                filled_qty=Decimal("0"),
                filled_avg_price=None,
                filled_at=None,
                broker_order_id=None,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order validation failed: {e}",
            ) from e

        except AlpacaRejectionError as e:
            # Release reservation on broker rejection (broker rejected)
            position_reservation.release(order.symbol, reservation_token)
            logger.warning(
                f"Order rejected by broker: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "error": str(e),
                },
            )
            ctx.db.update_order_status_cas(
                client_order_id=client_order_id,
                status="rejected",
                broker_updated_at=datetime.now(UTC),
                status_rank=status_rank_for("rejected"),
                source_priority=SOURCE_PRIORITY_MANUAL,
                filled_qty=Decimal("0"),
                filled_avg_price=None,
                filled_at=None,
                broker_order_id=None,
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Order rejected by broker: {e}",
            ) from e

        except AlpacaConnectionError as e:
            # Release reservation on connection error (broker not reached)
            position_reservation.release(order.symbol, reservation_token)
            logger.error(
                f"Broker connection error: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "error": str(e),
                },
            )
            # Update order status to "failed" to prevent stuck orders in pending_new
            ctx.db.update_order_status_cas(
                client_order_id=client_order_id,
                status="failed",
                broker_updated_at=datetime.now(UTC),
                status_rank=status_rank_for("failed"),
                source_priority=SOURCE_PRIORITY_MANUAL,
                filled_qty=Decimal("0"),
                filled_avg_price=None,
                filled_at=None,
                broker_order_id=None,
                error_message=f"Broker connection error: {e}",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Broker connection error: {e}",
            ) from e

        except Exception as e:
            # Catch-all: ONLY release if broker did NOT accept
            if not broker_accepted:
                position_reservation.release(order.symbol, reservation_token)
                logger.error(
                    f"Unexpected broker error, releasing reservation: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
            else:
                # Broker accepted - reservation already confirmed immediately after acceptance
                # Order is live, reservation is permanent, just log the post-broker error
                logger.error(
                    f"Post-broker error, order is live, reservation already confirmed: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "broker_order_id": broker_order_id,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Unexpected error during order submission: {type(e).__name__}",
            ) from e
    else:
        # DRY_RUN mode: release reservation immediately (no broker submission)
        position_reservation.release(order.symbol, reservation_token)
        logger.debug(
            f"DRY_RUN mode: reservation released: {client_order_id}",
            extra={"client_order_id": client_order_id},
        )

    # Success response
    order_detail = ctx.db.get_order_by_client_id(client_order_id)
    if not order_detail:
        logger.error(
            f"Order not found after insertion: {client_order_id}",
            extra={"client_order_id": client_order_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Order inserted but not found in database",
        )

    duration = time.time() - start_time
    logger.info(
        f"Order submitted successfully: {client_order_id} ({duration:.2f}s)",
        extra={
            "client_order_id": client_order_id,
            "broker_order_id": broker_order_id,
            "duration_seconds": duration,
        },
    )

    return OrderResponse(
        client_order_id=client_order_id,
        status=order_detail.status,
        broker_order_id=broker_order_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        created_at=order_detail.created_at,
        message="Order logged (DRY_RUN mode)" if config.dry_run else "Order submitted",
    )


# =============================================================================
# PATCH /api/v1/orders/{client_order_id} - Modify Order
# =============================================================================


@router.patch("/orders/{client_order_id}", response_model=OrderModifyResponse)
async def modify_order(
    client_order_id: str,
    payload: OrderModifyRequest,
    _auth_context: AuthContext = Depends(order_modify_auth),
    _rate_limit_remaining: int = Depends(order_modify_rl),
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
    response: Response = None,  # type: ignore[assignment]
) -> OrderModifyResponse:
    """Modify a working order via Alpaca's atomic replace API.

    This endpoint implements a three-phase modification protocol:
    1. Acquire lock + insert pending modification record
    2. Call broker's atomic replace API
    3. Finalize records (mark original replaced, insert replacement order)

    The protocol ensures:
    - Idempotency via idempotency_key
    - No duplicate modifications via advisory locking
    - Consistent state even if finalization fails (reconciliation can recover)
    """
    # --- Validation Phase ---
    order = ctx.db.get_order_by_client_id(client_order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    authorized_strategies = get_authorized_strategies(_auth_context.user)
    if not authorized_strategies or order.strategy_id not in authorized_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Idempotency check BEFORE status check to allow retries of completed modifications
    existing = ctx.db.get_modification_by_idempotency_key(client_order_id, payload.idempotency_key)
    idempotent_resp = _handle_idempotent_modification_response(existing, response)
    if idempotent_resp:
        return idempotent_resp

    await _check_modify_safety_gates(ctx, order, payload)
    _check_order_modification_eligibility(order)
    _validate_modify_fields(order, payload)

    if payload.qty is not None and Decimal(str(order.filled_qty or 0)) > Decimal(str(payload.qty)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot reduce qty below filled quantity ({order.filled_qty})",
        )

    changes = _compute_modification_changes(order, payload)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No changes detected for modification",
        )

    _validate_modify_position_limits(order, payload, ctx)
    await _validate_modify_fat_finger(order, payload, ctx, config.fat_finger_max_price_age_seconds)

    # --- Phase 1: Acquire lock + insert pending modification ---
    user_id = _extract_user_id_from_auth(_auth_context.user)
    try:
        modification_id, new_client_order_id = _acquire_modification_lock(
            ctx, client_order_id, order, payload, changes, user_id, response
        )
    except _IdempotentModificationException as exc:
        return exc.response
    except HTTPException:
        raise

    # Check broker availability
    if not ctx.alpaca:
        ctx.db.update_modification_status(
            modification_id, status="failed", error_message="broker_unavailable"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Alpaca client not initialized. Check credentials.",
        )

    # Reserve additional exposure for qty increases
    reservation_token: str | None = None
    try:
        reservation_token = _reserve_modify_delta(order, payload, ctx)
    except HTTPException as exc:
        ctx.db.update_modification_status(
            modification_id, status="failed", error_message=str(exc.detail)
        )
        raise

    # Build replacement request for DB record
    replacement_request = _build_replacement_request(order, payload)

    # --- Phase 2: Call broker replace ---
    broker_order_id, broker_client_order_id, broker_response = _call_broker_replace(
        ctx, order, payload, new_client_order_id, modification_id, reservation_token
    )

    # --- Phase 3: Finalize records ---
    _finalize_modification_in_db(
        ctx,
        client_order_id,
        order,
        modification_id,
        broker_order_id,
        broker_client_order_id,
        broker_response,
        replacement_request,
    )

    return OrderModifyResponse(
        original_client_order_id=client_order_id,
        new_client_order_id=broker_client_order_id,
        modification_id=str(modification_id),
        modified_at=datetime.now(UTC),
        status="completed",
        changes=changes,
    )


def _build_replacement_request(order: OrderDetail, payload: OrderModifyRequest) -> OrderRequest:
    """Build OrderRequest for the replacement order.

    Args:
        order: Original order being modified.
        payload: Modification request payload.

    Returns:
        OrderRequest with effective values after modification.
    """
    effective_qty = payload.qty if payload.qty is not None else order.qty
    effective_limit = payload.limit_price if payload.limit_price is not None else order.limit_price
    effective_stop = payload.stop_price if payload.stop_price is not None else order.stop_price
    effective_tif = (
        payload.time_in_force if payload.time_in_force is not None else order.time_in_force
    )

    return OrderRequest(
        symbol=order.symbol,
        side=order.side,
        qty=effective_qty,
        order_type=order.order_type,
        limit_price=effective_limit,
        stop_price=effective_stop,
        time_in_force=effective_tif,
        execution_style=order.execution_style or "instant",
    )


# =============================================================================
# GET /api/v1/orders/{client_order_id}/modifications - Modification History
# =============================================================================


@router.get("/orders/{client_order_id}/modifications", response_model=list[OrderModificationRecord])
async def get_modification_history(
    client_order_id: str,
    _auth_context: AuthContext = Depends(order_read_auth),
    ctx: AppContext = Depends(get_context),
) -> list[OrderModificationRecord]:
    order = ctx.db.get_order_by_client_id(client_order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    authorized_strategies = get_authorized_strategies(_auth_context.user)
    if not authorized_strategies or order.strategy_id not in authorized_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    records = ctx.db.get_modifications_for_order(client_order_id)
    return [
        OrderModificationRecord(
            modification_id=str(row["modification_id"]),
            original_client_order_id=row["original_client_order_id"],
            new_client_order_id=row["new_client_order_id"],
            modified_at=row["modified_at"],
            modified_by=row["modified_by"],
            changes=row.get("changes") or {},
            reason=row.get("reason"),
        )
        for row in records
    ]


# =============================================================================
# POST /api/v1/orders/{client_order_id}/cancel - Cancel Order
# =============================================================================


@router.post("/orders/{client_order_id}/cancel")
async def cancel_order(
    client_order_id: str,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    _auth_context: AuthContext = Depends(order_cancel_auth),
    _rate_limit_remaining: int = Depends(order_cancel_rl),
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> dict[str, Any]:
    """Cancel a single order by client_order_id.

    Args:
        client_order_id: The client order ID to cancel
        response: FastAPI response object
        _auth_context: Authentication context (injected)
        _rate_limit_remaining: Rate limit remaining (injected)
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        Dict with cancellation status
    """
    order = ctx.db.get_order_by_client_id(client_order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found: {client_order_id}",
        )

    if order.status in TERMINAL_STATUSES:
        return {
            "client_order_id": client_order_id,
            "status": order.status,
            "message": "Order already in terminal state",
        }

    if not config.dry_run:
        if not ctx.alpaca:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials.",
            )
        if order.broker_order_id:
            ctx.alpaca.cancel_order(order.broker_order_id)

    updated = ctx.db.update_order_status_cas(
        client_order_id=client_order_id,
        status="canceled",
        broker_updated_at=datetime.now(UTC),
        status_rank=status_rank_for("canceled"),
        source_priority=SOURCE_PRIORITY_MANUAL,
        filled_qty=order.filled_qty,
        filled_avg_price=order.filled_avg_price,
        filled_at=order.filled_at,
        broker_order_id=order.broker_order_id,
    )

    # Note: Position reservation was already "confirmed" when order was submitted.
    # Cancelled orders have their positions adjusted via webhook/reconciliation,
    # not via the reservation system (token is deleted after confirm).

    return {
        "client_order_id": client_order_id,
        "status": updated.status if updated else "canceled",
        "message": "Order canceled",
    }


# =============================================================================
# GET /api/v1/orders/{client_order_id} - Get Order Details
# =============================================================================


@router.get("/orders/{client_order_id}", response_model=OrderDetail)
async def get_order(
    client_order_id: str,
    _auth_context: AuthContext = Depends(order_read_auth),
    ctx: AppContext = Depends(get_context),
) -> OrderDetail:
    """
    Get order details by client_order_id.

    Args:
        client_order_id: Deterministic client order ID
        _auth_context: Authentication context (injected)
        ctx: Application context with all dependencies (injected)

    Returns:
        OrderDetail with full order information

    Raises:
        HTTPException 404: Order not found
    """
    order = ctx.db.get_order_by_client_id(client_order_id)

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found: {client_order_id}",
        )

    return order


# =============================================================================
# GET /api/v1/orders/{client_order_id}/audit - Order Audit Trail (P6T8)
# =============================================================================


class AuditEntryResponse(BaseModel):
    """Response model for a single audit entry."""

    id: int = Field(..., description="Audit log entry ID")
    timestamp: datetime = Field(..., description="When the action occurred")
    user_id: str | None = Field(None, description="User who performed the action")
    action: str = Field(..., description="Action type (submit, cancel, modify, etc.)")
    outcome: str = Field(..., description="Action outcome (success, failure)")
    details: dict[str, Any] = Field(default_factory=dict, description="Action details")
    ip_address: str | None = Field(None, description="Client IP address")
    session_id: str | None = Field(None, description="Session ID")


class OrderAuditResponse(BaseModel):
    """Response model for order audit trail."""

    client_order_id: str = Field(..., description="Order client ID")
    entries: list[AuditEntryResponse] = Field(
        default_factory=list, description="Audit trail entries in chronological order"
    )
    total_count: int = Field(..., description="Total number of entries", ge=0)


@router.get("/orders/{client_order_id}/audit", response_model=OrderAuditResponse)
async def get_order_audit_trail(
    client_order_id: str,
    limit: int = 100,
    _auth_context: AuthContext = Depends(order_read_auth),
    ctx: AppContext = Depends(get_context),
) -> OrderAuditResponse:
    """
    Get audit trail for an order.

    Returns chronological list of all actions taken on this order including:
    - Order submission
    - Modifications (price, qty changes)
    - Cancellation attempts
    - Status transitions

    Each entry includes IP address and session ID for compliance tracking.

    Args:
        client_order_id: Deterministic client order ID
        limit: Maximum entries to return (default 100)

    Returns:
        OrderAuditResponse with audit entries

    Raises:
        HTTPException 404: Order not found
        HTTPException 403: Not authorized for this order's strategy
    """
    # Verify order exists and user has access
    order = ctx.db.get_order_by_client_id(client_order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found: {client_order_id}",
        )

    # Verify strategy authorization (fail-closed: empty list = deny)
    authorized_strategies = get_authorized_strategies(_auth_context.user)
    if not authorized_strategies:
        # No strategies assigned - deny access (fail-closed security)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No strategy access - cannot view audit trail",
        )
    if order.strategy_id not in authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this order's audit trail",
        )

    # Check if user is admin (for PII visibility)
    user_is_admin = is_admin(_auth_context.user)

    # Query audit_log for this order
    # Uses idx_audit_log_resource index created in migration 0027
    entries: list[AuditEntryResponse] = []
    try:
        with ctx.db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, timestamp, user_id, action, outcome, details,
                           ip_address, session_id
                    FROM audit_log
                    WHERE resource_type = 'order' AND resource_id = %s
                    ORDER BY timestamp ASC, id ASC
                    LIMIT %s
                    """,
                    (client_order_id, limit),
                )
                rows = cur.fetchall()

                for row in rows:
                    # Parse details JSON (json imported at module top)
                    details_raw = row[5]
                    if isinstance(details_raw, str):
                        details = json.loads(details_raw)
                    elif isinstance(details_raw, dict):
                        details = details_raw
                    else:
                        details = {}

                    # Redact PII (IP, session_id, user_id) for non-admin users
                    entries.append(
                        AuditEntryResponse(
                            id=row[0],
                            timestamp=row[1],
                            user_id=row[2] if user_is_admin else None,
                            action=row[3],
                            outcome=row[4],
                            details=details,
                            ip_address=row[6] if user_is_admin else None,
                            session_id=row[7] if user_is_admin else None,
                        )
                    )
    except (psycopg.errors.Error, json.JSONDecodeError) as e:
        # Catch specific database and JSON parsing errors
        # Audit is supplementary - return empty rather than fail
        logger.warning(
            "Failed to query audit log",
            extra={
                "client_order_id": client_order_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        entries = []

    return OrderAuditResponse(
        client_order_id=client_order_id,
        entries=entries,
        total_count=len(entries),
    )


# =============================================================================
# Legacy Factory Function (Deprecated)
# =============================================================================
# The create_orders_router factory function has been deprecated in favor of
# module-level router definition with Depends() pattern.
# This factory is kept temporarily for backward compatibility during Phase 2B
# transition. It will be removed after all routes are migrated.
#
# See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for migration details.

"""Manual control endpoints for the Execution Gateway."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, TypeVar

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg import DatabaseError, IntegrityError, OperationalError
from pydantic import ValidationError
from redis.exceptions import RedisError

from apps.execution_gateway.alpaca_client import AlpacaClientError, AlpacaExecutor
from apps.execution_gateway.api.dependencies import (
    DRY_RUN,
    TwoFaResult,
    TwoFaValidator,
    check_rate_limit_with_fallback,
    error_detail,
    get_2fa_validator,
    get_alpaca_executor,
    get_async_redis,
    get_audit_logger,
    get_authenticated_user,
    get_db_client,
    get_rate_limiter,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import OrderRequest, OrderResponse
from apps.execution_gateway.schemas_manual_controls import (
    AdjustPositionRequest,
    AdjustPositionResponse,
    CancelAllOrdersRequest,
    CancelAllOrdersResponse,
    CancelOrderRequest,
    CancelOrderResponse,
    ClosePositionRequest,
    ClosePositionResponse,
    FlattenAllRequest,
    FlattenAllResponse,
    ManualOrderRequest,
    PendingOrdersResponse,
    RecentFillEvent,
    RecentFillsResponse,
)
from libs.platform.web_console_auth.audit_logger import AuditLogger
from libs.platform.web_console_auth.gateway_auth import AuthenticatedUser
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.platform.web_console_auth.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()

# Rate limit configuration (requests per window)
RATE_LIMITS = {
    "cancel_order": (10, 60),
    "cancel_all": (5, 60),
    "close_position": (10, 60),
    "adjust_position": (10, 60),
    "flatten_all": (1, 300),
    "manual_order": (10, 60),
    "pending_orders": (10, 60),
    "recent_fills": (5, 60),  # Tighter limit: fills are higher-cardinality than orders
}


# Manual control operations use synthetic strategy IDs for audit trail.
# Operators who can perform manual controls should be able to see/cancel these orders.
MANUAL_CONTROL_STRATEGY_PREFIX = "manual_controls_"
MANUAL_CONTROL_STRATEGIES = (
    f"{MANUAL_CONTROL_STRATEGY_PREFIX}close_position",
    f"{MANUAL_CONTROL_STRATEGY_PREFIX}adjust_position",
    f"{MANUAL_CONTROL_STRATEGY_PREFIX}flatten_all",
    f"{MANUAL_CONTROL_STRATEGY_PREFIX}trade",
)
MANUAL_CONTROL_PERMISSIONS = (
    Permission.CANCEL_ORDER,
    Permission.CLOSE_POSITION,
    Permission.ADJUST_POSITION,
    Permission.FLATTEN_ALL,
    Permission.SUBMIT_ORDER,
)

# Default MFA error mapping fallback (avoids per-request tuple creation).
MFA_DEFAULT_ERROR: tuple[int, str, str] = (
    status.HTTP_403_FORBIDDEN,
    "mfa_required",
    "MFA verification required. Please re-authenticate.",
)

# Circuit breaker Redis key (consistent with libs/risk_management/breaker.py)
CIRCUIT_BREAKER_STATE_KEY = "circuit_breaker:state"
REASON_MAX_LEN = 512


def _sanitize_reason(reason: str) -> str:
    """Sanitize reason strings for audit logs (truncate + strip newlines)."""
    # Collapse all whitespace (including newlines) into single spaces.
    cleaned = " ".join(reason.split()).strip()
    if len(cleaned) > REASON_MAX_LEN:
        return cleaned[:REASON_MAX_LEN].rstrip()
    return cleaned


async def _check_circuit_breaker(
    redis_client: redis_async.Redis | None,
    *,
    user: AuthenticatedUser,
    action: str,
    reason: str,
    audit_logger: AuditLogger,
) -> None:
    """Fail-closed if circuit breaker is tripped or state unavailable."""
    if redis_client is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type="circuit_breaker",
            resource_id="state",
            outcome="failed",
            details={"reason": reason, "error": "redis_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("circuit_breaker_unavailable", "Circuit breaker state unavailable"),
        )

    try:
        state = await redis_client.get(CIRCUIT_BREAKER_STATE_KEY)
    except RedisError as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type="circuit_breaker",
            resource_id="state",
            outcome="failed",
            details={"reason": reason, "error": f"redis_error: {exc}"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("circuit_breaker_unavailable", "Circuit breaker state unavailable"),
        ) from exc

    if not state:
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type="circuit_breaker",
            resource_id="state",
            outcome="failed",
            details={"reason": reason, "error": "state_missing"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("circuit_breaker_unavailable", "Circuit breaker state unavailable"),
        )

    state_value = _parse_circuit_breaker_state(state)
    if state_value == "TRIPPED":
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type="circuit_breaker",
            resource_id="state",
            outcome="denied",
            details={"reason": reason, "state": state_value},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail(
                "circuit_breaker_tripped", "Trading blocked: circuit breaker active"
            ),
        )


def _parse_circuit_breaker_state(state: bytes | str | None) -> str:
    """
    Parse circuit breaker state from Redis value.

    Args:
        state: Raw Redis value (bytes, str, or None)

    Returns:
        Uppercase state string (e.g., "TRIPPED", "OPEN", "QUIET_PERIOD")
        or empty string if state is None/empty or unparseable.

    Notes:
        Circuit breaker stores JSON like {"state": "TRIPPED", ...}.
        Falls back to treating raw value as state string for legacy compatibility.
    """
    if not state:
        return ""
    state_str = state.decode() if isinstance(state, bytes | bytearray) else str(state)
    try:
        state_data = json.loads(state_str)
        raw_state = state_data.get("state", "") if isinstance(state_data, dict) else ""
        return str(raw_state).upper()
    except (json.JSONDecodeError, TypeError):
        # Fallback: treat raw value as state string (legacy compatibility)
        return state_str.upper()


def _strategy_allowed(user: AuthenticatedUser, strategy_id: str | None) -> bool:
    """Check if user is authorized for the given strategy."""
    # Admins with VIEW_ALL_STRATEGIES can access any strategy
    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        return True
    # For non-admins, require explicit strategy assignment
    if strategy_id is None:
        return False
    # Allow access to manual-control orders for operators who can act on them.
    # This enables users to view/cancel manual control orders without exposing
    # unrelated strategy orders.
    if strategy_id.startswith(MANUAL_CONTROL_STRATEGY_PREFIX):
        return _manual_controls_allowed(user)
    return strategy_id in get_authorized_strategies(user)


T = TypeVar("T")


async def _db_call(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run synchronous database calls in a thread to avoid blocking the event loop."""

    return await asyncio.to_thread(func, *args, **kwargs)


def _require_integral_qty(qty: Decimal, field_name: str) -> int:
    """Ensure quantity is a whole number and return its int value."""

    if qty != qty.to_integral_value():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("invalid_request", f"{field_name} must be a whole number"),
        )
    qty_int = int(qty)
    if qty_int <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("invalid_request", f"{field_name} must be positive"),
        )
    return qty_int


def _apply_strategy_scope(
    user: AuthenticatedUser, strategies: Iterable[str] | None
) -> list[str] | None:
    """Return strategy scope for queries (None = all)."""

    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        return None
    scope = list(strategies) if strategies else []
    if _manual_controls_allowed(user):
        scope.extend(MANUAL_CONTROL_STRATEGIES)
    return sorted(set(scope))


def _manual_controls_allowed(user: AuthenticatedUser) -> bool:
    """Return True if the user can access manual-control orders."""

    return any(has_permission(user, permission) for permission in MANUAL_CONTROL_PERMISSIONS)


async def _require_non_empty_strategy_scope(
    user: AuthenticatedUser,
    *,
    action: str,
    audit_logger: AuditLogger,
    resource_type: str,
    resource_id: str,
) -> list[str] | None:
    """Return strategy scope or raise 403 when user has no authorized strategies."""
    scope = _apply_strategy_scope(user, get_authorized_strategies(user))
    if scope is not None and len(scope) == 0:
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="failed",
            details={"error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )
    return scope


def _generate_manual_order_id(
    action: str,
    symbol: str,
    side: str,
    qty: Decimal | int,
    user_id: str,
    as_of_datetime: datetime | None = None,
    *,
    order_type: str | None = None,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    time_in_force: str | None = None,
) -> str:
    """Generate deterministic order ID for manual control operations.

    Ensures idempotency for manual operations by creating reproducible IDs
    based on operation parameters and timestamp (minute precision).

    Using minute precision allows:
    - Same user to execute identical trades at different times
    - Protection against rapid duplicate submissions (within same minute)

    Args:
        action: Operation type (close_position, adjust_position, flatten_all)
        symbol: Trading symbol
        side: Order side (buy/sell)
        qty: Order quantity
        user_id: User initiating the operation
        as_of_datetime: Datetime for ID generation (defaults to now, truncated to minute)
        order_type: Order type (market, limit, stop, stop_limit)
        limit_price: Limit price for limit/stop_limit orders
        stop_price: Stop price for stop/stop_limit orders
        time_in_force: Time in force (day, gtc, ioc, fok)

    Returns:
        24-character alphanumeric ID compatible with Alpaca
    """

    # Use UTC with minute precision - allows same trade at different times
    # while protecting against rapid duplicate submissions
    target_dt = as_of_datetime or datetime.now(UTC)
    minute_key = target_dt.strftime("%Y%m%d%H%M")  # Minute precision
    qty_int = int(qty)

    # Include all economically relevant fields to prevent collisions
    # between different order types with same symbol/side/qty
    components = f"{action}:{symbol}:{side}:{qty_int}:{user_id}:{minute_key}"
    if order_type:
        components += f":{order_type}"
    if limit_price is not None:
        components += f":lp{limit_price}"
    if stop_price is not None:
        components += f":sp{stop_price}"
    if time_in_force:
        components += f":{time_in_force}"

    digest = hashlib.sha256(components.encode()).hexdigest()
    return digest[:24]


async def _enforce_rate_limit(
    rate_limiter: RateLimiter,
    user_id: str,
    action: str,
    audit_logger: AuditLogger | None = None,
) -> None:
    max_requests, window = RATE_LIMITS[action]
    allowed, _, is_fallback = await check_rate_limit_with_fallback(
        rate_limiter, user_id, action, max_requests, window
    )
    if not allowed:
        if audit_logger:
            await audit_logger.log_action(
                user_id=user_id,
                action=action,
                resource_type="rate_limit",
                resource_id=action,
                outcome="rate_limited",
                details={
                    "window_seconds": window,
                    "fallback_mode": is_fallback,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_detail("rate_limited", "Rate limit exceeded", retry_after=window),
            headers={"Retry-After": str(window)},
        )


async def _ensure_permission_with_audit(
    user: AuthenticatedUser,
    permission: Permission,
    action: str,
    audit_logger: AuditLogger,
    resource_type: str = "endpoint",
    resource_id: str = "*",
) -> None:
    """Check permission and audit denial if not granted."""
    if not has_permission(user, permission):
        await audit_logger.log_action(
            user_id=user.user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="denied",
            details={"required_permission": permission.name},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail(
                "permission_denied",
                f"Permission {permission.name} required",
            ),
        )


@router.post("/orders/{order_id}/cancel", response_model=CancelOrderResponse)
async def cancel_order(
    order_id: str,
    request: CancelOrderRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
) -> CancelOrderResponse:
    """Cancel a single order with permission, strategy, and rate-limit enforcement."""
    sanitized_reason = _sanitize_reason(request.reason)

    await _ensure_permission_with_audit(user, Permission.CANCEL_ORDER, "cancel_order", audit_logger)
    await _enforce_rate_limit(rate_limiter, user.user_id, "cancel_order", audit_logger)

    # Centralized strategy scope guard for consistency
    await _require_non_empty_strategy_scope(
        user,
        action="cancel_order",
        audit_logger=audit_logger,
        resource_type="order",
        resource_id=order_id,
    )

    order = await _db_call(db_client.get_order_by_client_id, order_id)
    # Combine order existence and strategy authorization check to prevent
    # information leakage (403 vs 404 would reveal order existence across strategies)
    if not order or not _strategy_allowed(user, order.strategy_id):
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="denied",
            details={
                "reason": sanitized_reason,
                "error": "not_found_or_unauthorized",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail("not_found", f"Order {order_id} not found"),
        )

    previous_status = order.status

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": "broker_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    await audit_logger.log_action(
        user_id=user.user_id,
        action="cancel_order",
        resource_type="order",
        resource_id=order_id,
        outcome="pending",
        details={"reason": sanitized_reason, "strategy_id": order.strategy_id},
    )

    try:
        # Mark as pending_cancel BEFORE broker call to avoid stale active state.
        await _db_call(db_client.update_order_status, order_id, "pending_cancel")
    except (OperationalError, DatabaseError) as exc:
        # Database errors: connection failures, query errors, transaction issues
        logger.error(
            "Database error updating order status before cancel",
            extra={
                "client_order_id": order_id,
                "action": "cancel_order",
                "user_id": user.user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": f"db_update_failed: {exc}"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("db_error", "Failed to update order status before cancel"),
        ) from exc
    except ValidationError as exc:
        # Pydantic validation errors when constructing OrderDetail from DB row
        logger.error(
            "Validation error updating order status before cancel",
            extra={
                "client_order_id": order_id,
                "action": "cancel_order",
                "user_id": user.user_id,
                "error": str(exc),
                "validation_errors": exc.errors(),
            },
        )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": f"validation_failed: {exc}"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("validation_error", "Invalid order data after status update"),
        ) from exc

    # If no broker_order_id, the order was never submitted to broker (dry_run or pre-submit)
    # Update directly to canceled status instead of waiting for webhook
    if not order.broker_order_id:
        await _db_call(db_client.update_order_status, order_id, "canceled")
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="success",
            details={
                "reason": sanitized_reason,
                "strategy_id": order.strategy_id,
                "note": "no_broker_order_id",
            },
        )
        return CancelOrderResponse(
            status="cancelled",
            order_id=order_id,
            cancelled_at=datetime.now(UTC),
        )

    try:
        if alpaca_executor:
            alpaca_executor.cancel_order(order.broker_order_id)
    except TimeoutError as exc:
        try:
            await _db_call(
                db_client.update_order_status,
                order_id,
                previous_status,
                error_message=f"cancel_failed_timeout: {exc}",
            )
        except (OperationalError, DatabaseError):
            logger.error(
                "Failed to revert order status after cancel timeout",
                extra={"client_order_id": order_id, "error": str(exc)},
                exc_info=True,
            )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="timeout",
            details={"reason": sanitized_reason},
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=error_detail(
                "broker_timeout", "Broker timeout - order may or may not be cancelled"
            ),
        ) from exc
    except AlpacaClientError as exc:
        try:
            await _db_call(
                db_client.update_order_status,
                order_id,
                previous_status,
                error_message=f"cancel_failed_broker: {exc}",
            )
        except (OperationalError, DatabaseError):
            logger.error(
                "Failed to revert order status after broker cancel error",
                extra={"client_order_id": order_id, "error": str(exc)},
                exc_info=True,
            )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail("broker_error", f"Broker error: {exc}"),
        ) from exc

    # Broker cancel succeeded; keep status as pending_cancel until webhook confirmation.
    await audit_logger.log_action(
        user_id=user.user_id,
        action="cancel_order",
        resource_type="order",
        resource_id=order_id,
        outcome="success",
        details={"reason": sanitized_reason, "strategy_id": order.strategy_id},
    )

    return CancelOrderResponse(
        status="cancelled",
        order_id=order_id,
        cancelled_at=datetime.now(UTC),
    )


@router.post("/orders/cancel-all", response_model=CancelAllOrdersResponse)
async def cancel_all_orders(
    request: CancelAllOrdersRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
) -> CancelAllOrdersResponse:
    """Cancel all pending orders for a symbol within authorized strategies."""
    sanitized_reason = _sanitize_reason(request.reason)

    await _ensure_permission_with_audit(
        user, Permission.CANCEL_ORDER, "cancel_all_orders", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "cancel_all", audit_logger)

    strategy_scope = await _require_non_empty_strategy_scope(
        user,
        action="cancel_all_orders",
        audit_logger=audit_logger,
        resource_type="symbol",
        resource_id=request.symbol,
    )
    orders, _ = await _db_call(
        db_client.get_pending_orders, symbol=request.symbol, strategy_ids=strategy_scope
    )

    if not orders:
        # Audit no-op case before raising 404
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_all_orders",
            resource_type="symbol",
            resource_id=request.symbol,
            outcome="no_op",
            details={"reason": sanitized_reason, "message": "no_pending_orders_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail(
                "not_found",
                f"No pending orders found for {request.symbol} in authorized strategies",
            ),
        )

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_all_orders",
            resource_type="symbol",
            resource_id=request.symbol,
            outcome="failed",
            details={"reason": sanitized_reason, "error": "broker_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    cancelled_ids: list[str] = []
    failed_ids: list[str] = []
    skipped_unauthorized: list[str] = []
    strategies_affected: set[str] = set()

    for order in orders:
        if not _strategy_allowed(user, order.strategy_id):
            # Log unauthorized orders as safety net (DB query should already be scoped)
            skipped_unauthorized.append(order.client_order_id)
            await audit_logger.log_action(
                user_id=user.user_id,
                action="cancel_order",
                resource_type="order",
                resource_id=order.client_order_id,
                outcome="denied",
                details={"reason": sanitized_reason, "strategy_id": order.strategy_id},
            )
            continue
        strategies_affected.add(order.strategy_id or "")
        previous_status = order.status
        try:
            await _db_call(db_client.update_order_status, order.client_order_id, "pending_cancel")
        except (OperationalError, DatabaseError) as exc:
            # Database errors: connection failures, query errors, transaction issues
            logger.error(
                "Database error updating order status in cancel_all",
                extra={
                    "client_order_id": order.client_order_id,
                    "action": "cancel_all_orders",
                    "user_id": user.user_id,
                    "symbol": request.symbol,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            failed_ids.append(order.client_order_id)
            await audit_logger.log_action(
                user_id=user.user_id,
                action="cancel_order",
                resource_type="order",
                resource_id=order.client_order_id,
                outcome="failed",
                details={"reason": sanitized_reason, "error": f"db_update_failed: {exc}"},
            )
            continue
        except ValidationError as exc:
            # Pydantic validation errors when constructing OrderDetail from DB row
            logger.error(
                "Validation error updating order status in cancel_all",
                extra={
                    "client_order_id": order.client_order_id,
                    "action": "cancel_all_orders",
                    "user_id": user.user_id,
                    "symbol": request.symbol,
                    "error": str(exc),
                    "validation_errors": exc.errors(),
                },
            )
            failed_ids.append(order.client_order_id)
            await audit_logger.log_action(
                user_id=user.user_id,
                action="cancel_order",
                resource_type="order",
                resource_id=order.client_order_id,
                outcome="failed",
                details={"reason": sanitized_reason, "error": f"validation_failed: {exc}"},
            )
            continue

        # If no broker_order_id, update directly to canceled status
        if not order.broker_order_id:
            await _db_call(db_client.update_order_status, order.client_order_id, "canceled")
            cancelled_ids.append(order.client_order_id)
            continue

        try:
            alpaca_executor.cancel_order(order.broker_order_id)
            cancelled_ids.append(order.client_order_id)
        except AlpacaClientError as exc:
            try:
                await _db_call(
                    db_client.update_order_status,
                    order.client_order_id,
                    previous_status,
                    error_message=f"cancel_failed_broker: {exc}",
                )
            except (OperationalError, DatabaseError):
                logger.error(
                    "Failed to revert order status after broker cancel error",
                    extra={"client_order_id": order.client_order_id, "error": str(exc)},
                    exc_info=True,
                )
            failed_ids.append(order.client_order_id)
            await audit_logger.log_action(
                user_id=user.user_id,
                action="cancel_order",
                resource_type="order",
                resource_id=order.client_order_id,
                outcome="failed",
                details={"reason": sanitized_reason, "error": str(exc)},
            )

    # Determine overall outcome based on successes and failures
    if failed_ids and not cancelled_ids:
        outcome = "failed"
    elif failed_ids:
        outcome = "partial"
    else:
        outcome = "success"

    await audit_logger.log_action(
        user_id=user.user_id,
        action="cancel_all_orders",
        resource_type="symbol",
        resource_id=request.symbol,
        outcome=outcome,
        details={
            "reason": sanitized_reason,
            "cancelled": cancelled_ids,
            "failed": failed_ids,
            "skipped_unauthorized": skipped_unauthorized,
        },
    )

    # Return 502 if all cancellations failed
    if failed_ids and not cancelled_ids:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail(
                "broker_error",
                f"All {len(failed_ids)} cancellation(s) failed",
            ),
        )

    return CancelAllOrdersResponse(
        status="cancelled",
        symbol=request.symbol,
        cancelled_count=len(cancelled_ids),
        order_ids=cancelled_ids,
        strategies_affected=sorted(s for s in strategies_affected if s),
    )


@router.post("/positions/{symbol}/close", response_model=ClosePositionResponse)
async def close_position(
    symbol: str,
    request: ClosePositionRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
) -> ClosePositionResponse:
    """Close (fully or partially) a position."""
    sanitized_reason = _sanitize_reason(request.reason)

    await _ensure_permission_with_audit(
        user, Permission.CLOSE_POSITION, "close_position", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "close_position", audit_logger)

    strategies = get_authorized_strategies(user)
    # Fail-closed: user with no authorized strategies cannot close positions
    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES) and not strategies:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )

    await _require_non_empty_strategy_scope(
        user,
        action="close_position",
        audit_logger=audit_logger,
        resource_type="position",
        resource_id=symbol.upper(),
    )

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = await _db_call(db_client.get_positions_for_strategies, strategies)
    else:
        positions = await _db_call(db_client.get_all_positions)

    position = next((p for p in positions if p.symbol == symbol.upper()), None)
    if not position:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "position_not_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail("not_found", f"Position for {symbol} not found"),
        )

    if position.qty == 0:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="no_op",
            details={"reason": sanitized_reason, "message": "position_already_flat"},
        )
        return ClosePositionResponse(
            status="already_flat",
            symbol=symbol.upper(),
            order_id=None,
            qty_to_close=Decimal(0),
        )

    position_qty = Decimal(position.qty)
    if position_qty != position_qty.to_integral_value():
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={
                "reason": sanitized_reason,
                "error": "fractional_position_unsupported",
                "position_qty": str(position_qty),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_detail(
                "fractional_position_unsupported",
                "Fractional positions cannot be closed via manual controls. "
                "Use broker tools or reconcile fractional positions before retrying.",
            ),
        )

    # Determine qty to close: use request.qty (always positive) or full position (abs value)
    qty_to_close = Decimal(request.qty) if request.qty is not None else abs(position_qty)

    # Safety: prevent over-close that would flip the position
    max_closeable = abs(position_qty)
    if qty_to_close > max_closeable:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="rejected",
            details={
                "reason": sanitized_reason,
                "error": "qty_exceeds_position",
                "requested_qty": float(qty_to_close),
                "position_qty": float(max_closeable),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_detail(
                "qty_exceeds_position",
                f"Requested qty ({qty_to_close}) exceeds position size ({max_closeable}). "
                "This would flip the position, which is not allowed via close endpoint.",
            ),
        )

    qty_int = _require_integral_qty(qty_to_close, "qty")

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "broker_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    # Side based on position sign: long (qty>0) -> sell, short (qty<0) -> buy to cover
    side: Literal["buy", "sell"] = "sell" if position.qty > 0 else "buy"
    order_req = OrderRequest(
        symbol=symbol.upper(),
        side=side,
        qty=qty_int,
        order_type="market",
    )

    # Generate deterministic order ID for idempotency
    order_id = _generate_manual_order_id(
        action="close_position",
        symbol=symbol.upper(),
        side=side,
        qty=Decimal(qty_int),
        user_id=user.user_id,
    )
    try:
        # Persist order in DB BEFORE broker submission to ensure webhooks can find it
        # Strategy ID uses manual_controls prefix to identify operator-initiated orders
        try:
            await _db_call(
                db_client.create_order,
                client_order_id=order_id,
                strategy_id="manual_controls_close_position",
                order_request=order_req,
                status="pending_new",
                broker_order_id=None,
            )
        except IntegrityError:
            # Idempotency: order already exists (e.g., retry within same minute)
            existing = await _db_call(db_client.get_order_by_client_id, order_id)
            if existing:
                await audit_logger.log_action(
                    user_id=user.user_id,
                    action="close_position",
                    resource_type="position",
                    resource_id=symbol.upper(),
                    outcome="success",
                    details={
                        "reason": sanitized_reason,
                        "qty": float(qty_to_close),
                        "order_id": order_id,
                        "idempotent": True,
                    },
                )
                return ClosePositionResponse(
                    status="closing",
                    symbol=symbol.upper(),
                    order_id=order_id,
                    qty_to_close=qty_to_close,
                    message="Order already submitted (idempotent retry)",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_detail("db_error", "Order creation conflict"),
            ) from None

        # Submit to broker after DB persistence
        alpaca_executor.submit_order(order_req, order_id)

        # Log success after broker submission
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="success",
            details={"reason": sanitized_reason, "qty": float(qty_to_close), "order_id": order_id},
        )
    except AlpacaClientError as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "qty": float(qty_to_close), "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail("broker_error", f"Broker error: {exc}"),
        ) from exc

    return ClosePositionResponse(
        status="closing",
        symbol=symbol.upper(),
        order_id=order_id,
        qty_to_close=qty_to_close,
    )


@router.post("/positions/{symbol}/adjust", response_model=AdjustPositionResponse)
async def adjust_position(
    symbol: str,
    request: AdjustPositionRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
    redis_client: redis_async.Redis | None = Depends(get_async_redis),
) -> AdjustPositionResponse:
    """Force position adjustment to a target quantity."""
    sanitized_reason = _sanitize_reason(request.reason)

    await _ensure_permission_with_audit(
        user, Permission.ADJUST_POSITION, "adjust_position", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "adjust_position", audit_logger)

    strategies = get_authorized_strategies(user)
    # Fail-closed: user with no authorized strategies cannot adjust positions
    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES) and not strategies:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )

    await _require_non_empty_strategy_scope(
        user,
        action="adjust_position",
        audit_logger=audit_logger,
        resource_type="position",
        resource_id=symbol.upper(),
    )

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = await _db_call(db_client.get_positions_for_strategies, strategies)
    else:
        positions = await _db_call(db_client.get_all_positions)

    position = next((p for p in positions if p.symbol == symbol.upper()), None)
    if not position:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "position_not_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail("not_found", f"Position for {symbol} not found"),
        )
    current_qty = Decimal(position.qty)
    if current_qty != current_qty.to_integral_value():
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={
                "reason": sanitized_reason,
                "error": "fractional_position_unsupported",
                "current_qty": str(current_qty),
                "target_qty": str(request.target_qty),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=error_detail(
                "fractional_position_unsupported",
                "Fractional positions cannot be adjusted via manual controls. "
                "Use broker tools or reconcile fractional positions before retrying.",
            ),
        )
    delta = Decimal(request.target_qty) - current_qty

    # Circuit breaker check: only allow risk-reducing adjustments during breaker trips
    # Risk-reducing = abs(target_qty) < abs(current_qty)
    is_risk_reducing = abs(request.target_qty) < abs(current_qty)
    if not is_risk_reducing and delta != 0:
        # Check circuit breaker for exposure-increasing adjustments
        try:
            if redis_client:
                state = await redis_client.get(CIRCUIT_BREAKER_STATE_KEY)
                cb_state = _parse_circuit_breaker_state(state)
                # Fail-closed: missing state blocks exposure-increasing adjustments
                if not cb_state:
                    await audit_logger.log_action(
                        user_id=user.user_id,
                        action="adjust_position",
                        resource_type="circuit_breaker",
                        resource_id=symbol.upper(),
                        outcome="blocked",
                        details={
                            "reason": sanitized_reason,
                            "error": "circuit_breaker_state_missing",
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=error_detail(
                            "circuit_breaker_unavailable",
                            "Circuit breaker state unavailable. Only risk-reducing adjustments allowed.",
                        ),
                    )
                if cb_state == "TRIPPED":
                    await audit_logger.log_action(
                        user_id=user.user_id,
                        action="adjust_position",
                        resource_type="circuit_breaker",
                        resource_id=symbol.upper(),
                        outcome="blocked",
                        details={
                            "reason": sanitized_reason,
                            "error": "circuit_breaker_tripped",
                            "current_qty": float(current_qty),
                            "target_qty": float(request.target_qty),
                            "delta": float(delta),
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=error_detail(
                            "circuit_breaker_tripped",
                            "Trading blocked: circuit breaker active. Only risk-reducing adjustments allowed.",
                        ),
                    )
            else:
                # Fail-closed: no Redis connection, block exposure-increasing adjustments
                await audit_logger.log_action(
                    user_id=user.user_id,
                    action="adjust_position",
                    resource_type="circuit_breaker",
                    resource_id=symbol.upper(),
                    outcome="blocked",
                    details={
                        "reason": sanitized_reason,
                        "error": "circuit_breaker_unavailable",
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=error_detail(
                        "circuit_breaker_unavailable",
                        "Circuit breaker state unavailable. Only risk-reducing adjustments allowed.",
                    ),
                )
        except RedisError as exc:
            # Fail-closed: Redis error, block exposure-increasing adjustments
            await audit_logger.log_action(
                user_id=user.user_id,
                action="adjust_position",
                resource_type="circuit_breaker",
                resource_id=symbol.upper(),
                outcome="blocked",
                details={
                    "reason": sanitized_reason,
                    "error": f"redis_error: {exc}",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=error_detail(
                    "circuit_breaker_unavailable",
                    "Circuit breaker state unavailable. Only risk-reducing adjustments allowed.",
                ),
            ) from exc

    if delta == 0:
        # Audit no-op case for completeness
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="no_op",
            details={
                "reason": sanitized_reason,
                "current_qty": float(current_qty),
                "target_qty": float(request.target_qty),
                "message": "position_already_at_target",
            },
        )
        return AdjustPositionResponse(
            status="adjusting",
            symbol=symbol.upper(),
            current_qty=current_qty,
            target_qty=request.target_qty,
            order_id=None,
        )

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": sanitized_reason, "error": "broker_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    side: Literal["buy", "sell"] = "buy" if delta > 0 else "sell"
    qty_int = _require_integral_qty(abs(delta), "target_qty")
    order_req = OrderRequest(
        symbol=symbol.upper(),
        side=side,
        qty=qty_int,
        order_type=request.order_type,
        limit_price=request.limit_price,
    )

    # Generate deterministic order ID for idempotency
    order_id = _generate_manual_order_id(
        action="adjust_position",
        symbol=symbol.upper(),
        side=side,
        qty=Decimal(qty_int),
        user_id=user.user_id,
        order_type=request.order_type,
        limit_price=request.limit_price,
    )
    try:
        # Persist order in DB BEFORE broker submission to ensure webhooks can find it
        # Strategy ID uses manual_controls prefix to identify operator-initiated orders
        try:
            await _db_call(
                db_client.create_order,
                client_order_id=order_id,
                strategy_id="manual_controls_adjust_position",
                order_request=order_req,
                status="pending_new",
                broker_order_id=None,
            )
        except IntegrityError:
            # Idempotency: order already exists (e.g., retry within same minute)
            existing = await _db_call(db_client.get_order_by_client_id, order_id)
            if existing:
                await audit_logger.log_action(
                    user_id=user.user_id,
                    action="adjust_position",
                    resource_type="position",
                    resource_id=symbol.upper(),
                    outcome="success",
                    details={
                        "reason": sanitized_reason,
                        "current_qty": float(current_qty),
                        "target_qty": float(request.target_qty),
                        "order_id": order_id,
                        "idempotent": True,
                    },
                )
                return AdjustPositionResponse(
                    status="adjusting",
                    symbol=symbol.upper(),
                    current_qty=current_qty,
                    target_qty=request.target_qty,
                    order_id=order_id,
                    message="Order already submitted (idempotent retry)",
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_detail("db_error", "Order creation conflict"),
            ) from None

        # Submit to broker after DB persistence
        alpaca_executor.submit_order(order_req, order_id)

        # Log success after broker submission
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="success",
            details={
                "reason": sanitized_reason,
                "current_qty": float(current_qty),
                "target_qty": float(request.target_qty),
                "order_id": order_id,
            },
        )
    except AlpacaClientError as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={
                "reason": sanitized_reason,
                "current_qty": float(current_qty),
                "target_qty": float(request.target_qty),
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail("broker_error", f"Broker error: {exc}"),
        ) from exc

    return AdjustPositionResponse(
        status="adjusting",
        symbol=symbol.upper(),
        current_qty=current_qty,
        target_qty=request.target_qty,
        order_id=order_id,
    )


@router.post("/positions/flatten-all", response_model=FlattenAllResponse)
async def flatten_all_positions(
    request: FlattenAllRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
    two_fa_validator: TwoFaValidator = Depends(get_2fa_validator),
) -> FlattenAllResponse:
    """Flatten all positions across authorized strategies with MFA."""
    sanitized_reason = _sanitize_reason(request.reason)

    await _ensure_permission_with_audit(user, Permission.FLATTEN_ALL, "flatten_all", audit_logger)
    await _enforce_rate_limit(rate_limiter, user.user_id, "flatten_all", audit_logger)

    mfa_result: TwoFaResult = await two_fa_validator(request.id_token, user.user_id)
    valid_mfa, mfa_error, amr_method = mfa_result
    if not valid_mfa:
        # Error messages are specific enough to aid debugging but generic enough to avoid
        # leaking sensitive authentication details (e.g., token structure, validation internals)
        error_map = {
            "token_expired": (
                status.HTTP_403_FORBIDDEN,
                "mfa_expired",
                "MFA verification expired. Please re-authenticate.",
            ),
            "invalid_issuer": (
                status.HTTP_403_FORBIDDEN,
                "mfa_invalid",
                "MFA verification failed: invalid provider.",
            ),
            "invalid_audience": (
                status.HTTP_403_FORBIDDEN,
                "mfa_invalid",
                "MFA verification failed: invalid audience.",
            ),
            "token_not_yet_valid": (
                status.HTTP_403_FORBIDDEN,
                "mfa_required",
                "MFA verification failed: token not yet valid.",
            ),
            "token_mismatch": (
                status.HTTP_403_FORBIDDEN,
                "token_mismatch",
                "MFA verification failed: user mismatch.",
            ),
            "mfa_required": (
                status.HTTP_403_FORBIDDEN,
                "mfa_required",
                "MFA verification required. Please authenticate with MFA.",
            ),
            "mfa_expired": (
                status.HTTP_403_FORBIDDEN,
                "mfa_expired",
                "MFA verification expired. Please re-authenticate.",
            ),
            "invalid_jwt": (
                status.HTTP_403_FORBIDDEN,
                "mfa_invalid",
                "MFA verification failed: invalid token.",
            ),
            "mfa_misconfigured": (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "mfa_unavailable",
                "MFA service is not configured.",
            ),
            "mfa_unavailable": (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "mfa_unavailable",
                "MFA service temporarily unavailable. Please retry.",
            ),
        }
        status_code, code, message = error_map.get(mfa_error or "mfa_required", MFA_DEFAULT_ERROR)
        # Audit log MFA denial before raising
        await audit_logger.log_action(
            user_id=user.user_id,
            action="flatten_all",
            resource_type="positions",
            resource_id="*",
            outcome="failed",
            details={"reason": sanitized_reason, "mfa_error": mfa_error or "mfa_required"},
        )
        raise HTTPException(
            status_code=status_code,
            detail=error_detail(code, message),
        )

    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = await _db_call(db_client.get_all_positions)
    else:
        authorized_strategies = get_authorized_strategies(user)
        # Fail-closed: user with no authorized strategies cannot flatten
        if not authorized_strategies:
            await audit_logger.log_action(
                user_id=user.user_id,
                action="flatten_all",
                resource_type="positions",
                resource_id="*",
                outcome="failed",
                details={"reason": sanitized_reason, "error": "no_authorized_strategies"},
                amr_method=amr_method,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
            )
        positions = await _db_call(db_client.get_positions_for_strategies, authorized_strategies)

    if not positions:
        # Audit no-op case for completeness
        await audit_logger.log_action(
            user_id=user.user_id,
            action="flatten_all",
            resource_type="positions",
            resource_id="*",
            outcome="no_op",
            details={"reason": sanitized_reason, "message": "no_positions_to_flatten"},
            amr_method=amr_method,
        )
        return FlattenAllResponse(
            status="flattening",
            positions_closed=0,
            orders_created=[],
            strategies_affected=[],
        )

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="flatten_all",
            resource_type="positions",
            resource_id="*",
            outcome="failed",
            details={"reason": sanitized_reason, "error": "broker_unavailable"},
            amr_method=amr_method,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    orders_created: list[str] = []
    strategies_affected: set[str] = set()
    strategy_map = await _db_call(
        db_client.get_strategy_map_for_symbols, [position.symbol for position in positions]
    )

    for position in positions:
        if position.qty == 0:
            continue
        side: Literal["buy", "sell"] = "sell" if position.qty > 0 else "buy"
        qty = int(abs(position.qty))
        order_req = OrderRequest(
            symbol=position.symbol,
            side=side,
            qty=qty,
            order_type="market",
        )
        # Generate deterministic order ID for idempotency
        order_id = _generate_manual_order_id(
            action="flatten_all",
            symbol=position.symbol,
            side=side,
            qty=Decimal(qty),
            user_id=user.user_id,
        )
        try:
            # Persist order in DB BEFORE broker submission to ensure webhooks can find it
            # Strategy ID uses manual_controls prefix to identify operator-initiated orders
            order_already_exists = False
            try:
                await _db_call(
                    db_client.create_order,
                    client_order_id=order_id,
                    strategy_id="manual_controls_flatten_all",
                    order_request=order_req,
                    status="pending_new",
                    broker_order_id=None,
                )
            except IntegrityError:
                # Idempotency: order already exists (e.g., retry within same minute)
                # Continue to add to orders_created since broker may already have the order
                order_already_exists = True

            if not order_already_exists:
                # Submit to broker only if this is a new order
                alpaca_executor.submit_order(order_req, order_id)
            orders_created.append(order_id)
            strategy_id = strategy_map.get(position.symbol)
            strategies_affected.add(strategy_id or "manual_controls_flatten_all")
        except AlpacaClientError as exc:
            # Log failure with partial progress
            await audit_logger.log_action(
                user_id=user.user_id,
                action="flatten_all",
                resource_type="positions",
                resource_id="*",
                outcome="failed",
                details={
                    "reason": sanitized_reason,
                    "orders_created_before_failure": orders_created,
                    "failed_position": position.symbol,
                    "error": str(exc),
                },
                amr_method=amr_method,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=error_detail("broker_error", f"Broker error: {exc}"),
            ) from exc

    # Log success after all orders submitted
    await audit_logger.log_action(
        user_id=user.user_id,
        action="flatten_all",
        resource_type="positions",
        resource_id="*",
        outcome="success",
        details={"reason": sanitized_reason, "orders": orders_created},
        amr_method=amr_method,
    )

    return FlattenAllResponse(
        status="flattening",
        positions_closed=len(orders_created),
        orders_created=orders_created,
        strategies_affected=sorted(s for s in strategies_affected if s),
    )


@router.post("/manual/orders", response_model=OrderResponse)
async def submit_manual_order(
    request: ManualOrderRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    alpaca_executor: AlpacaExecutor | None = Depends(get_alpaca_executor),
    redis_client: redis_async.Redis | None = Depends(get_async_redis),
) -> OrderResponse:
    """Submit a manual order with manual_controls strategy attribution."""
    await _ensure_permission_with_audit(user, Permission.SUBMIT_ORDER, "manual_order", audit_logger)
    await _enforce_rate_limit(rate_limiter, user.user_id, "manual_order", audit_logger)
    sanitized_reason = _sanitize_reason(request.reason)

    await _require_non_empty_strategy_scope(
        user,
        action="manual_order",
        audit_logger=audit_logger,
        resource_type="order",
        resource_id="*",
    )

    qty_int = _require_integral_qty(request.qty, "qty")

    order_req = OrderRequest(
        symbol=request.symbol.upper(),
        side=request.side,
        qty=qty_int,
        order_type=request.order_type,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        time_in_force=request.time_in_force,
    )

    order_id = _generate_manual_order_id(
        action="manual_trade",
        symbol=order_req.symbol,
        side=order_req.side,
        qty=qty_int,
        user_id=user.user_id,
        order_type=request.order_type,
        limit_price=request.limit_price,
        stop_price=request.stop_price,
        time_in_force=request.time_in_force,
    )

    await _check_circuit_breaker(
        redis_client,
        user=user,
        action="manual_order",
        reason=sanitized_reason,
        audit_logger=audit_logger,
    )

    if alpaca_executor is None:
        # Fail-closed: if DRY_RUN=false but broker unavailable, reject the order
        if not DRY_RUN:
            await audit_logger.log_action(
                user_id=user.user_id,
                action="manual_order",
                resource_type="order",
                resource_id=order_id,
                outcome="failed",
                details={
                    "symbol": order_req.symbol,
                    "side": order_req.side,
                    "qty": qty_int,
                    "order_type": order_req.order_type,
                    "reason": sanitized_reason,
                    "error": "broker_unavailable",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=error_detail(
                    "broker_unavailable",
                    "Broker connection unavailable. DRY_RUN is disabled, cannot proceed.",
                ),
            )

        # DRY_RUN=true: allow dry_run mode
        try:
            await _db_call(
                db_client.create_order,
                client_order_id=order_id,
                strategy_id=f"{MANUAL_CONTROL_STRATEGY_PREFIX}trade",
                order_request=order_req,
                status="dry_run",
                broker_order_id=None,
            )
        except IntegrityError:
            existing = await _db_call(db_client.get_order_by_client_id, order_id)
            if existing:
                return OrderResponse(
                    client_order_id=order_id,
                    status=existing.status,
                    broker_order_id=existing.broker_order_id,
                    symbol=existing.symbol,
                    side=existing.side,
                    qty=existing.qty,
                    order_type=existing.order_type,
                    limit_price=existing.limit_price,
                    created_at=existing.created_at,
                    message="Order already submitted (idempotent retry)",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail("duplicate_order", "Order already submitted"),
            ) from None
        await audit_logger.log_action(
            user_id=user.user_id,
            action="manual_order",
            resource_type="order",
            resource_id=order_id,
            outcome="success",
            details={
                "symbol": order_req.symbol,
                "side": order_req.side,
                "qty": qty_int,
                "order_type": order_req.order_type,
                "reason": sanitized_reason,
                "mode": "dry_run",
            },
        )
        return OrderResponse(
            client_order_id=order_id,
            status="dry_run",
            broker_order_id=None,
            symbol=order_req.symbol,
            side=order_req.side,
            qty=qty_int,
            order_type=order_req.order_type,
            limit_price=order_req.limit_price,
            created_at=datetime.now(UTC),
            message="Order logged (manual DRY_RUN)",
        )

    try:
        # Create DB record first for idempotency and webhook safety
        try:
            order_detail = await _db_call(
                db_client.create_order,
                client_order_id=order_id,
                strategy_id=f"{MANUAL_CONTROL_STRATEGY_PREFIX}trade",
                order_request=order_req,
                status="pending_new",
                broker_order_id=None,
            )
        except IntegrityError:
            existing = await _db_call(db_client.get_order_by_client_id, order_id)
            if existing:
                return OrderResponse(
                    client_order_id=order_id,
                    status=existing.status,
                    broker_order_id=existing.broker_order_id,
                    symbol=existing.symbol,
                    side=existing.side,
                    qty=existing.qty,
                    order_type=existing.order_type,
                    limit_price=existing.limit_price,
                    created_at=existing.created_at,
                    message="Order already submitted (idempotent retry)",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error_detail("duplicate_order", "Order already submitted"),
            ) from None

        try:
            await _check_circuit_breaker(
                redis_client,
                user=user,
                action="manual_order",
                reason=sanitized_reason,
                audit_logger=audit_logger,
            )
        except HTTPException as exc:
            try:
                await _db_call(
                    db_client.update_order_status,
                    order_id,
                    "failed",
                    error_message="circuit_breaker_tripped_before_submit",
                )
            except (OperationalError, DatabaseError):
                logger.error(
                    "Failed to update manual order after circuit breaker trip",
                    extra={"client_order_id": order_id},
                    exc_info=True,
                )
            raise exc

        alpaca_response = alpaca_executor.submit_order(order_req, order_id)
        updated = await _db_call(
            db_client.update_order_status,
            order_id,
            alpaca_response["status"],
            broker_order_id=alpaca_response["id"],
            error_message=None,
        )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="manual_order",
            resource_type="order",
            resource_id=order_id,
            outcome="success",
            details={
                "symbol": order_req.symbol,
                "side": order_req.side,
                "qty": qty_int,
                "order_type": order_req.order_type,
                "reason": sanitized_reason,
            },
        )
        return OrderResponse(
            client_order_id=order_id,
            status=alpaca_response["status"],
            broker_order_id=alpaca_response["id"],
            symbol=order_req.symbol,
            side=order_req.side,
            qty=qty_int,
            order_type=order_req.order_type,
            limit_price=order_req.limit_price,
            created_at=(updated.created_at if updated else order_detail.created_at),
            message="Order submitted (manual)",
        )
    except AlpacaClientError as exc:
        try:
            await _db_call(
                db_client.update_order_status,
                order_id,
                "failed",
                error_message=str(exc),
            )
        except (OperationalError, DatabaseError):
            logger.error(
                "Failed to update manual order after broker error",
                extra={"client_order_id": order_id, "error": str(exc)},
                exc_info=True,
            )
        await audit_logger.log_action(
            user_id=user.user_id,
            action="manual_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={
                "symbol": order_req.symbol,
                "side": order_req.side,
                "qty": qty_int,
                "order_type": order_req.order_type,
                "reason": sanitized_reason,
                "error": str(exc),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail("broker_error", f"Broker error: {exc}"),
        ) from exc
    except (OperationalError, DatabaseError) as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="manual_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": f"db_error: {exc}"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("db_error", "Failed to persist manual order"),
        ) from exc
    except ValidationError as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="manual_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": sanitized_reason, "error": f"validation_failed: {exc}"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail("validation_error", "Invalid order data after status update"),
        ) from exc


@router.get("/orders/pending", response_model=PendingOrdersResponse)
async def list_pending_orders(
    strategy_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    parent_order_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000, ge=1),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> PendingOrdersResponse:
    """List pending orders scoped to authorized strategies."""

    await _ensure_permission_with_audit(
        user, Permission.VIEW_TRADES, "list_pending_orders", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "pending_orders", audit_logger)

    scope = await _require_non_empty_strategy_scope(
        user,
        action="list_pending_orders",
        audit_logger=audit_logger,
        resource_type="orders",
        resource_id="*",
    )

    if sort_by not in {"created_at", "updated_at", "symbol", "strategy_id", "status"}:
        sort_by = "created_at"

    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"

    scope_strategies = get_authorized_strategies(user)
    filtered_by_strategy = True

    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        filtered_by_strategy = strategy_id is not None
        scope = [strategy_id] if strategy_id else None
    else:
        # Fail-closed: user with no authorized strategies cannot list orders
        if not scope:
            await audit_logger.log_action(
                user_id=user.user_id,
                action="list_pending_orders",
                resource_type="orders",
                resource_id="*",
                outcome="denied",
                details={"error": "no_authorized_strategies"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
            )
        if strategy_id and strategy_id not in scope:
            await audit_logger.log_action(
                user_id=user.user_id,
                action="list_pending_orders",
                resource_type="strategy",
                resource_id=strategy_id,
                outcome="denied",
                details={"requested_strategy": strategy_id},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail(
                    "strategy_unauthorized",
                    f"User not authorized for strategy {strategy_id}",
                ),
            )
        scope = [strategy_id] if strategy_id else scope

    orders, total = await _db_call(
        db_client.get_pending_orders,
        symbol=symbol.upper() if symbol else None,
        strategy_ids=scope,
        parent_order_id=parent_order_id,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    # Audit successful read for traceability
    await audit_logger.log_action(
        user_id=user.user_id,
        action="list_pending_orders",
        resource_type="orders",
        resource_id="*",
        outcome="success",
        details={
            "strategy_filter": strategy_id,
            "symbol_filter": symbol,
            "parent_order_id_filter": parent_order_id,
            "results_count": len(orders),
            "total_matching": total,
        },
    )

    return PendingOrdersResponse(
        orders=orders,
        total=total,
        limit=limit,
        offset=offset,
        filtered_by_strategy=filtered_by_strategy,
        user_strategies=scope_strategies,
    )


@router.get("/orders/recent-fills", response_model=RecentFillsResponse)
async def list_recent_fills(
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_authenticated_user),
    db_client: DatabaseClient = Depends(get_db_client),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> RecentFillsResponse:
    """List recent fill events for activity feed."""

    await _ensure_permission_with_audit(
        user, Permission.VIEW_TRADES, "list_recent_fills", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "recent_fills", audit_logger)

    scope_strategies = get_authorized_strategies(user)
    filtered_by_strategy = not has_permission(user, Permission.VIEW_ALL_STRATEGIES)

    # Fail-closed: user with no authorized strategies cannot view fills
    if filtered_by_strategy and not scope_strategies:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="list_recent_fills",
            resource_type="orders",
            resource_id="*",
            outcome="denied",
            details={"error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )

    rows = await _db_call(
        db_client.get_recent_fills,
        strategy_ids=scope_strategies if filtered_by_strategy else None,
        limit=limit,
    )

    await audit_logger.log_action(
        user_id=user.user_id,
        action="list_recent_fills",
        resource_type="orders",
        resource_id="*",
        outcome="success",
        details={"results_count": len(rows), "limit": limit, "filtered": filtered_by_strategy},
    )

    # Convert dict rows to Pydantic models for type safety
    events = [RecentFillEvent(**row) for row in rows]
    return RecentFillsResponse(
        events=events,
        total=len(events),
        limit=limit,
        filtered_by_strategy=filtered_by_strategy,
        user_strategies=scope_strategies,
    )


__all__ = ["router"]

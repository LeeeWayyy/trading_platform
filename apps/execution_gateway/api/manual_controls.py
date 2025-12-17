"""Manual control endpoints for the Execution Gateway."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.execution_gateway.alpaca_client import AlpacaExecutor
from apps.execution_gateway.api.dependencies import (
    TwoFaResult,
    TwoFaValidator,
    check_rate_limit_with_fallback,
    error_detail,
    get_2fa_validator,
    get_alpaca_executor,
    get_audit_logger,
    get_authenticated_user,
    get_db_client,
    get_rate_limiter,
)
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.schemas import OrderRequest
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
    PendingOrdersResponse,
)
from libs.web_console_auth.audit_logger import AuditLogger
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
)
from libs.web_console_auth.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

router = APIRouter()

# Rate limit configuration (requests per window)
RATE_LIMITS = {
    "cancel_order": (10, 60),
    "cancel_all": (5, 60),
    "close_position": (10, 60),
    "adjust_position": (10, 60),
    "flatten_all": (1, 300),
    "pending_orders": (10, 60),
}


def _strategy_allowed(user: AuthenticatedUser, strategy_id: str | None) -> bool:
    """Check if user is authorized for the given strategy."""
    # Admins with VIEW_ALL_STRATEGIES can access any strategy
    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        return True
    # For non-admins, require explicit strategy assignment
    if strategy_id is None:
        return False
    return strategy_id in get_authorized_strategies(user)


def _apply_strategy_scope(
    user: AuthenticatedUser, strategies: Iterable[str] | None
) -> list[str] | None:
    """Return strategy scope for queries (None = all)."""

    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        return None
    if strategies is None:
        return []
    return list(strategies)


def _generate_manual_order_id(
    action: str,
    symbol: str,
    side: str,
    qty: Decimal,
    user_id: str,
    as_of_date: date | None = None,
) -> str:
    """Generate deterministic order ID for manual control operations.

    Ensures idempotency for manual operations by creating reproducible IDs
    based on operation parameters and date.

    Args:
        action: Operation type (close_position, adjust_position, flatten_all)
        symbol: Trading symbol
        side: Order side (buy/sell)
        qty: Order quantity
        user_id: User initiating the operation
        as_of_date: Date for ID generation (defaults to today)

    Returns:
        24-character alphanumeric ID compatible with Alpaca
    """

    target_date = as_of_date or date.today()
    components = f"{action}:{symbol}:{side}:{qty}:{user_id}:{target_date}"
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

    await _ensure_permission_with_audit(user, Permission.CANCEL_ORDER, "cancel_order", audit_logger)
    await _enforce_rate_limit(rate_limiter, user.user_id, "cancel_order", audit_logger)

    order = db_client.get_order_by_client_id(order_id)
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
                "reason": request.reason,
                "error": "not_found_or_unauthorized",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail("not_found", f"Order {order_id} not found"),
        )

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": request.reason, "error": "broker_unavailable"},
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
        details={"reason": request.reason, "strategy_id": order.strategy_id},
    )

    try:
        # DESIGN TRADEOFF: Broker cancellation before DB update
        # This approach is acceptable because:
        # 1. Broker cancellation is idempotent (safe to retry)
        # 2. DB failure is logged and can be reconciled later
        # 3. Broker state is source of truth - DB is eventually consistent
        # Alternative (DB first) risks orphaned pending orders if broker call fails
        if alpaca_executor and order.broker_order_id:
            alpaca_executor.cancel_order(order.broker_order_id)
        db_client.update_order_status(order_id, "canceled")
    except TimeoutError as exc:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="timeout",
            details={"reason": request.reason},
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=error_detail(
                "broker_timeout", "Broker timeout - order may or may not be cancelled"
            ),
        ) from exc
    except Exception as exc:  # pragma: no cover - safety net
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": request.reason, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=error_detail("broker_error", f"Broker error: {exc}"),
        ) from exc

    await audit_logger.log_action(
        user_id=user.user_id,
        action="cancel_order",
        resource_type="order",
        resource_id=order_id,
        outcome="success",
        details={"reason": request.reason, "strategy_id": order.strategy_id},
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

    await _ensure_permission_with_audit(
        user, Permission.CANCEL_ORDER, "cancel_all_orders", audit_logger
    )
    await _enforce_rate_limit(rate_limiter, user.user_id, "cancel_all", audit_logger)

    strategy_scope = _apply_strategy_scope(user, get_authorized_strategies(user))
    # Fail fast if user has no authorized strategies (non-admins with empty list)
    if strategy_scope is not None and len(strategy_scope) == 0:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_all_orders",
            resource_type="symbol",
            resource_id=request.symbol,
            outcome="failed",
            details={"reason": request.reason, "error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail(
                "strategy_unauthorized",
                "User has no authorized strategies",
            ),
        )
    orders, _ = db_client.get_pending_orders(symbol=request.symbol, strategy_ids=strategy_scope)

    if not orders:
        # Audit no-op case before raising 404
        await audit_logger.log_action(
            user_id=user.user_id,
            action="cancel_all_orders",
            resource_type="symbol",
            resource_id=request.symbol,
            outcome="no_op",
            details={"reason": request.reason, "message": "no_pending_orders_found"},
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
            details={"reason": request.reason, "error": "broker_unavailable"},
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
                details={"reason": request.reason, "strategy_id": order.strategy_id},
            )
            continue
        strategies_affected.add(order.strategy_id or "")
        try:
            if order.broker_order_id:
                alpaca_executor.cancel_order(order.broker_order_id)
            db_client.update_order_status(order.client_order_id, "canceled")
            cancelled_ids.append(order.client_order_id)
        except Exception as exc:  # pragma: no cover - defensive
            failed_ids.append(order.client_order_id)
            await audit_logger.log_action(
                user_id=user.user_id,
                action="cancel_order",
                resource_type="order",
                resource_id=order.client_order_id,
                outcome="failed",
                details={"reason": request.reason, "error": str(exc)},
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
            "reason": request.reason,
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
        strategies_affected=sorted(strategies_affected),
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
            details={"reason": request.reason, "error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = db_client.get_positions_for_strategies(strategies)
    else:
        positions = db_client.get_all_positions()

    position = next((p for p in positions if p.symbol == symbol.upper()), None)
    if not position:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": request.reason, "error": "position_not_found"},
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
            details={"reason": request.reason, "message": "position_already_flat"},
        )
        return ClosePositionResponse(
            status="already_flat",
            symbol=symbol.upper(),
            order_id=None,
            qty_to_close=Decimal(0),
        )

    # Determine qty to close: use request.qty (always positive) or full position (abs value)
    qty_to_close = Decimal(request.qty) if request.qty is not None else abs(Decimal(position.qty))
    if qty_to_close <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_detail("invalid_request", "qty must be positive to close position"),
        )

    # Fail-closed: require broker executor for destructive operations
    if alpaca_executor is None:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": request.reason, "error": "broker_unavailable"},
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
        qty=int(qty_to_close),
        order_type="market",
    )

    # Generate deterministic order ID for idempotency
    order_id = _generate_manual_order_id(
        action="close_position",
        symbol=symbol.upper(),
        side=side,
        qty=qty_to_close,
        user_id=user.user_id,
    )
    try:
        # Persist order in DB BEFORE broker submission to ensure webhooks can find it
        # Strategy ID uses manual_controls prefix to identify operator-initiated orders
        db_client.create_order(
            client_order_id=order_id,
            strategy_id="manual_controls_close_position",
            order_request=order_req,
            status="pending_new",
            broker_order_id=None,
        )

        # Submit to broker after DB persistence
        alpaca_executor.submit_order(order_req, order_id)

        # Log success after broker submission
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="success",
            details={"reason": request.reason, "qty": float(qty_to_close), "order_id": order_id},
        )
    except Exception as exc:  # pragma: no cover - safety net
        await audit_logger.log_action(
            user_id=user.user_id,
            action="close_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": request.reason, "qty": float(qty_to_close), "error": str(exc)},
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
) -> AdjustPositionResponse:
    """Force position adjustment to a target quantity."""

    await _ensure_permission_with_audit(
        user, Permission.CLOSE_POSITION, "adjust_position", audit_logger
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
            details={"reason": request.reason, "error": "no_authorized_strategies"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
        )

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = db_client.get_positions_for_strategies(strategies)
    else:
        positions = db_client.get_all_positions()

    position = next((p for p in positions if p.symbol == symbol.upper()), None)
    if not position:
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={"reason": request.reason, "error": "position_not_found"},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_detail("not_found", f"Position for {symbol} not found"),
        )
    current_qty = Decimal(position.qty)
    delta = Decimal(request.target_qty) - current_qty

    if delta == 0:
        # Audit no-op case for completeness
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="no_op",
            details={
                "reason": request.reason,
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
            details={"reason": request.reason, "error": "broker_unavailable"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    side: Literal["buy", "sell"] = "buy" if delta > 0 else "sell"
    order_req = OrderRequest(
        symbol=symbol.upper(),
        side=side,
        qty=int(abs(delta)),
        order_type=request.order_type,
        limit_price=request.limit_price,
    )

    # Generate deterministic order ID for idempotency
    order_id = _generate_manual_order_id(
        action="adjust_position",
        symbol=symbol.upper(),
        side=side,
        qty=abs(delta),
        user_id=user.user_id,
    )
    try:
        # Persist order in DB BEFORE broker submission to ensure webhooks can find it
        # Strategy ID uses manual_controls prefix to identify operator-initiated orders
        db_client.create_order(
            client_order_id=order_id,
            strategy_id="manual_controls_adjust_position",
            order_request=order_req,
            status="pending_new",
            broker_order_id=None,
        )

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
                "reason": request.reason,
                "current_qty": float(current_qty),
                "target_qty": float(request.target_qty),
                "order_id": order_id,
            },
        )
    except Exception as exc:  # pragma: no cover
        await audit_logger.log_action(
            user_id=user.user_id,
            action="adjust_position",
            resource_type="position",
            resource_id=symbol.upper(),
            outcome="failed",
            details={
                "reason": request.reason,
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
        }
        status_code, code, message = error_map.get(
            mfa_error or "mfa_required",
            (
                status.HTTP_403_FORBIDDEN,
                "mfa_required",
                "MFA verification required. Please re-authenticate.",
            ),
        )
        # Audit log MFA denial before raising
        await audit_logger.log_action(
            user_id=user.user_id,
            action="flatten_all",
            resource_type="positions",
            resource_id="*",
            outcome="failed",
            details={"reason": request.reason, "mfa_error": mfa_error or "mfa_required"},
        )
        raise HTTPException(
            status_code=status_code,
            detail=error_detail(code, message),
        )

    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        positions = db_client.get_all_positions()
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
                details={"reason": request.reason, "error": "no_authorized_strategies"},
                amr_method=amr_method,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=error_detail("strategy_unauthorized", "User has no authorized strategies"),
            )
        positions = db_client.get_positions_for_strategies(authorized_strategies)

    if not positions:
        # Audit no-op case for completeness
        await audit_logger.log_action(
            user_id=user.user_id,
            action="flatten_all",
            resource_type="positions",
            resource_id="*",
            outcome="no_op",
            details={"reason": request.reason, "message": "no_positions_to_flatten"},
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
            details={"reason": request.reason, "error": "broker_unavailable"},
            amr_method=amr_method,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=error_detail("broker_unavailable", "Broker connection unavailable"),
        )

    orders_created: list[str] = []
    strategies_affected: set[str] = set()

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
            db_client.create_order(
                client_order_id=order_id,
                strategy_id="manual_controls_flatten_all",
                order_request=order_req,
                status="pending_new",
                broker_order_id=None,
            )

            # Submit to broker after DB persistence
            alpaca_executor.submit_order(order_req, order_id)
            orders_created.append(order_id)
            strategies_affected.add(getattr(position, "strategy_id", "") or "")
        except Exception as exc:  # pragma: no cover
            # Log failure with partial progress
            await audit_logger.log_action(
                user_id=user.user_id,
                action="flatten_all",
                resource_type="positions",
                resource_id="*",
                outcome="failed",
                details={
                    "reason": request.reason,
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
        details={"reason": request.reason, "orders": orders_created},
        amr_method=amr_method,
    )

    return FlattenAllResponse(
        status="flattening",
        positions_closed=len(orders_created),
        orders_created=orders_created,
        strategies_affected=sorted(s for s in strategies_affected if s),
    )


@router.get("/orders/pending", response_model=PendingOrdersResponse)
async def list_pending_orders(
    strategy_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
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
        if not scope_strategies:
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
        if strategy_id and strategy_id not in scope_strategies:
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
        scope = [strategy_id] if strategy_id else scope_strategies

    orders, total = db_client.get_pending_orders(
        symbol=symbol.upper() if symbol else None,
        strategy_ids=scope,
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


__all__ = ["router"]

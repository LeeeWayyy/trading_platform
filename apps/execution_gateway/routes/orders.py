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
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import redis.exceptions
from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.errors import UniqueViolation
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
    OrderDetail,
    OrderRequest,
    OrderResponse,
)
from apps.execution_gateway.services.order_helpers import resolve_fat_finger_context
from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.redis_client import RedisKeys
from libs.platform.web_console_auth.permissions import Permission

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


def _is_reconciliation_ready(ctx: AppContext, config: ExecutionGatewayConfig) -> bool:
    """Return True when startup reconciliation gate is open."""
    if config.dry_run:
        return True
    if ctx.reconciliation_service is None:
        return False
    return ctx.reconciliation_service.is_startup_complete()


async def _require_reconciliation_ready_or_reduce_only(
    order: OrderRequest,
    ctx: AppContext,
    config: ExecutionGatewayConfig,
    client_order_id: str,
) -> None:
    """Gate ALL order submissions until reconciliation completes.

    IMPORTANT: Despite the function name, this gate blocks ALL orders during
    startup reconciliation, not just position-increasing orders. The reduce-only
    logic was intentionally omitted for simplicity since it would require
    broker position lookups which add complexity and latency.

    This conservative approach ensures no orders can slip through during the
    critical startup window when position state may be inconsistent.

    Allowed paths:
    - Override active (operator manually unlocked)
    - Reconciliation complete
    - Dry-run mode
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

    # Block non-reduce-only orders during reconciliation gating
    # For simplicity, block all orders during reconciliation (reduce-only logic
    # requires broker position lookup which is complex)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "Reconciliation in progress",
            "message": "Order submission blocked until startup reconciliation completes",
        },
    )


# =============================================================================
# POST /api/v1/orders - Submit Order
# =============================================================================


@router.post("/orders", response_model=OrderResponse)
async def submit_order(
    order: OrderRequest,
    response: Response,
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
        created_at=order_detail.created_at,
        message="Order logged (DRY_RUN mode)" if config.dry_run else "Order submitted",
    )


# =============================================================================
# POST /api/v1/orders/{client_order_id}/cancel - Cancel Order
# =============================================================================


@router.post("/orders/{client_order_id}/cancel")
async def cancel_order(
    client_order_id: str,
    response: Response,
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
# Legacy Factory Function (Deprecated)
# =============================================================================
# The create_orders_router factory function has been deprecated in favor of
# module-level router definition with Depends() pattern.
# This factory is kept temporarily for backward compatibility during Phase 2B
# transition. It will be removed after all routes are migrated.
#
# See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for migration details.

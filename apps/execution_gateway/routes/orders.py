"""
Order management endpoints for Execution Gateway.

This module contains the core order submission, cancellation, and query endpoints.
These endpoints are extracted from main.py to improve modularity while preserving
exact safety gate ordering and business logic.

Key endpoints:
- POST /api/v1/orders - Submit orders with idempotency
- POST /api/v1/orders/{client_order_id}/cancel - Cancel orders
- GET /api/v1/orders/{client_order_id} - Query order status

Safety gates are preserved in exact order:
1. Auth (as Depends() parameter)
2. Rate limiting (as Depends() parameter)
3. Kill-switch check (inside handler)
4. Circuit breaker check (inside handler)
5. Quarantine check (inside handler)
6. Reconciliation lock (inside handler)
7. Business logic validations (inside handler)

See ADR-0014 for architecture decisions.
See REFACTOR_EXECUTION_GATEWAY_TASK.md for extraction strategy.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.errors import UniqueViolation

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.database import (
    TERMINAL_STATUSES,
    DatabaseClient,
    status_rank_for,
)
from apps.execution_gateway.fat_finger_validator import (
    FatFingerValidator,
    iter_breach_types,
)
from apps.execution_gateway.order_id_generator import generate_client_order_id
from apps.execution_gateway.reconciliation import (
    SOURCE_PRIORITY_MANUAL,
    SOURCE_PRIORITY_WEBHOOK,
)
from apps.execution_gateway.recovery_manager import RecoveryManager
from apps.execution_gateway.schemas import (
    OrderDetail,
    OrderRequest,
    OrderResponse,
)
from libs.core.common.api_auth_dependency import AuthContext

logger = logging.getLogger(__name__)


def create_orders_router(
    *,
    db_client: DatabaseClient,
    alpaca_client: AlpacaExecutor | None,
    recovery_manager: RecoveryManager,
    fat_finger_validator: FatFingerValidator,
    order_submit_auth: Any,
    order_submit_rl: Any,
    order_cancel_auth: Any,
    order_cancel_rl: Any,
    order_read_auth: Any,
    strategy_id: str,
    dry_run: bool,
) -> APIRouter:
    """
    Create orders router with injected dependencies.

    This factory function creates an APIRouter with all order endpoints.
    Dependencies are injected via closure to avoid global state.

    Args:
        db_client: Database client for order persistence
        alpaca_client: Alpaca API client (None if not initialized)
        recovery_manager: Safety gate coordinator (kill-switch, circuit breaker, etc.)
        fat_finger_validator: Order size validation
        order_submit_auth: Auth dependency for order submission
        order_submit_rl: Rate limit dependency for order submission
        order_cancel_auth: Auth dependency for order cancellation
        order_cancel_rl: Rate limit dependency for order cancellation
        order_read_auth: Auth dependency for order queries
        strategy_id: Strategy identifier for client_order_id generation
        dry_run: Whether to run in dry-run mode (no broker submission)

    Returns:
        Configured APIRouter with order endpoints
    """
    router = APIRouter(prefix="/api/v1", tags=["Orders"])

    # ============================================================================
    # POST /api/v1/orders - Submit Order
    # ============================================================================

    @router.post("/orders", response_model=OrderResponse)
    async def submit_order(
        order: OrderRequest,
        response: Response,
        # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
        # This allows rate limiter to bucket by user/service instead of anonymous IP
        _auth_context: AuthContext = Depends(order_submit_auth),
        _rate_limit_remaining: int = Depends(order_submit_rl),
    ) -> OrderResponse:
        """
        Submit order with idempotent retry semantics.

        The order is assigned a deterministic client_order_id based on the order
        parameters and current date. This ensures that the same order submitted
        multiple times will have the same ID and won't create duplicates.

        In DRY_RUN mode (default), orders are logged to database but NOT submitted
        to Alpaca. Set DRY_RUN=false to enable actual paper trading.

        Args:
            order: Order request (symbol, side, qty, order_type, etc.)

        Returns:
            OrderResponse with client_order_id, status, and broker_order_id

        Raises:
            HTTPException 400: Invalid order parameters
            HTTPException 422: Order rejected by broker
            HTTPException 503: Broker connection error

        Examples:
            Market buy order:
            >>> import requests
            >>> response = requests.post(
            ...     "http://localhost:8002/api/v1/orders",
            ...     json={
            ...         "symbol": "AAPL",
            ...         "side": "buy",
            ...         "qty": 10,
            ...         "order_type": "market"
            ...     }
            ... )
            >>> response.json()
            {
                "client_order_id": "a1b2c3d4e5f6...",
                "status": "dry_run",  # or "pending_new" if DRY_RUN=false
                "broker_order_id": null,
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "limit_price": null,
                "created_at": "2024-10-17T16:30:00Z",
                "message": "Order logged (DRY_RUN mode)"
            }

            Limit sell order:
            >>> response = requests.post(
            ...     "http://localhost:8002/api/v1/orders",
            ...     json={
            ...         "symbol": "MSFT",
            ...         "side": "sell",
            ...         "qty": 5,
            ...         "order_type": "limit",
            ...         "limit_price": "300.50"
            ...     }
            ... )
        """
        # Safety gating uses RecoveryManager (thread-safe, fail-closed)
        # Start timing for metrics
        start_time = time.time()

        # Generate deterministic client_order_id
        client_order_id = generate_client_order_id(order, strategy_id)

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

        kill_switch = recovery_manager.kill_switch
        circuit_breaker = recovery_manager.circuit_breaker
        position_reservation = recovery_manager.position_reservation

        # Check kill-switch unavailable (fail closed for safety)
        if recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
            logger.error(
                f"🔴 Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "kill_switch_unavailable": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kill-switch state unavailable (fail-closed for safety)",
            )

        # Check kill-switch engaged
        if kill_switch.is_engaged():
            logger.warning(
                f"🔴 Order blocked by kill-switch: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "kill_switch_engaged": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Kill-switch engaged - new orders blocked",
            )

        # Check circuit breaker unavailable (fail closed)
        if recovery_manager.is_circuit_breaker_unavailable() or circuit_breaker is None:
            logger.error(
                f"🔴 Order blocked by unavailable circuit-breaker (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "circuit_breaker_unavailable": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Circuit-breaker state unavailable (fail-closed for safety)",
            )

        # Check circuit breaker tripped
        if circuit_breaker.is_tripped():
            logger.warning(
                f"🔴 Order blocked by circuit breaker: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "circuit_breaker_tripped": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Circuit breaker tripped - trading paused",
            )

        # Check quarantine status
        if db_client.is_symbol_quarantined(order.symbol):
            logger.warning(
                f"🚨 Order blocked by quarantine: {order.symbol}",
                extra={
                    "client_order_id": client_order_id,
                    "symbol": order.symbol,
                    "quarantine_blocked": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Symbol {order.symbol} is quarantined due to repeated order failures",
            )

        # Check reconciliation lock
        if recovery_manager.reconciliation_lock_enabled():
            logger.warning(
                f"🔒 Order blocked by reconciliation lock: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "reconciliation_locked": True,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reconciliation in progress - orders temporarily blocked",
            )

        # Check for existing order (idempotency)
        existing_order = db_client.get_order_by_client_id(client_order_id)
        if existing_order:
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

        # Fat-finger validation
        fat_finger_result = fat_finger_validator.validate_order(order)
        if not fat_finger_result.approved:
            breach_list = ", ".join(iter_breach_types(fat_finger_result))
            logger.warning(
                f"🚨 Order blocked by fat-finger validation: {client_order_id}",
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
                detail=f"Order rejected by fat-finger checks: {fat_finger_result.reason}",
            )

        # Position reservation
        if position_reservation is None:
            logger.error(
                f"🔴 Position reservation unavailable: {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Position reservation service unavailable",
            )

        reservation_result = position_reservation.reserve_position(
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            client_order_id=client_order_id,
        )

        if not reservation_result.approved:
            logger.warning(
                f"🚨 Order blocked by position limits: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "reason": reservation_result.reason,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order blocked by position limits: {reservation_result.reason}",
            )

        # Insert order into database
        try:
            db_client.insert_order(
                client_order_id=client_order_id,
                strategy_id=strategy_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                order_type=order.order_type,
                limit_price=order.limit_price,
                status="dry_run" if dry_run else "pending_new",
                time_in_force=order.time_in_force or "day",
            )
        except UniqueViolation:
            # Race condition: another request inserted same order
            logger.info(
                f"Order already exists (race condition): {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            order_detail = db_client.get_order_by_client_id(client_order_id)
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

        # Submit to broker if not dry-run
        broker_order_id = None
        if not dry_run:
            if not alpaca_client:
                logger.error(
                    f"🔴 Alpaca client not initialized: {client_order_id}",
                    extra={"client_order_id": client_order_id},
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Alpaca client not initialized. Check credentials.",
                )

            try:
                broker_order_id = alpaca_client.submit_order(
                    symbol=order.symbol,
                    qty=order.qty,
                    side=order.side,
                    order_type=order.order_type,
                    limit_price=order.limit_price,
                    client_order_id=client_order_id,
                    time_in_force=order.time_in_force or "day",
                )
                logger.info(
                    f"✅ Order submitted to broker: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "broker_order_id": broker_order_id,
                    },
                )

                # Update order with broker_order_id
                db_client.update_order_broker_id(
                    client_order_id=client_order_id,
                    broker_order_id=broker_order_id,
                    status="pending_new",
                )

            except AlpacaValidationError as e:
                logger.warning(
                    f"🚨 Order validation error: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "error": str(e),
                    },
                )
                db_client.update_order_status_cas(
                    client_order_id=client_order_id,
                    status="rejected",
                    broker_updated_at=datetime.now(UTC),
                    status_rank=status_rank_for("rejected"),
                    source_priority=SOURCE_PRIORITY_MANUAL,
                    filled_qty=0,
                    filled_avg_price=None,
                    filled_at=None,
                    broker_order_id=None,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Order validation failed: {e}",
                ) from e

            except AlpacaRejectionError as e:
                logger.warning(
                    f"🚨 Order rejected by broker: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "error": str(e),
                    },
                )
                db_client.update_order_status_cas(
                    client_order_id=client_order_id,
                    status="rejected",
                    broker_updated_at=datetime.now(UTC),
                    status_rank=status_rank_for("rejected"),
                    source_priority=SOURCE_PRIORITY_MANUAL,
                    filled_qty=0,
                    filled_avg_price=None,
                    filled_at=None,
                    broker_order_id=None,
                )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Order rejected by broker: {e}",
                ) from e

            except AlpacaConnectionError as e:
                logger.error(
                    f"🔴 Broker connection error: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "error": str(e),
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Broker connection error: {e}",
                ) from e

        # Success response
        order_detail = db_client.get_order_by_client_id(client_order_id)
        if not order_detail:
            logger.error(
                f"🔴 Order not found after insertion: {client_order_id}",
                extra={"client_order_id": client_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Order inserted but not found in database",
            )

        duration = time.time() - start_time
        logger.info(
            f"✅ Order submitted successfully: {client_order_id} ({duration:.2f}s)",
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
            message="Order logged (DRY_RUN mode)" if dry_run else "Order submitted",
        )

    # ============================================================================
    # POST /api/v1/orders/{client_order_id}/cancel - Cancel Order
    # ============================================================================

    @router.post("/orders/{client_order_id}/cancel")
    async def cancel_order(
        client_order_id: str,
        response: Response,
        # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
        # This allows rate limiter to bucket by user/service instead of anonymous IP
        _auth_context: AuthContext = Depends(order_cancel_auth),
        _rate_limit_remaining: int = Depends(order_cancel_rl),
    ) -> dict[str, Any]:
        """Cancel a single order by client_order_id."""
        order = db_client.get_order_by_client_id(client_order_id)
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

        if not dry_run:
            if not alpaca_client:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Alpaca client not initialized. Check credentials.",
                )
            if order.broker_order_id:
                alpaca_client.cancel_order(order.broker_order_id)

        updated = db_client.update_order_status_cas(
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

        return {
            "client_order_id": client_order_id,
            "status": updated.status if updated else "canceled",
            "message": "Order canceled",
        }

    # ============================================================================
    # GET /api/v1/orders/{client_order_id} - Get Order Details
    # ============================================================================

    @router.get("/orders/{client_order_id}", response_model=OrderDetail)
    async def get_order(
        client_order_id: str,
        _auth_context: AuthContext = Depends(order_read_auth),
    ) -> OrderDetail:
        """
        Get order details by client_order_id.

        Args:
            client_order_id: Deterministic client order ID

        Returns:
            OrderDetail with full order information

        Raises:
            HTTPException 404: Order not found

        Examples:
            >>> import requests
            >>> response = requests.get(
            ...     "http://localhost:8002/api/v1/orders/a1b2c3d4e5f6..."
            ... )
            >>> response.json()
            {
                "client_order_id": "a1b2c3d4e5f6...",
                "strategy_id": "alpha_baseline",
                "symbol": "AAPL",
                "side": "buy",
                "qty": 10,
                "order_type": "market",
                "status": "filled",
                "broker_order_id": "broker123...",
                "filled_qty": "10",
                "filled_avg_price": "150.25",
                "created_at": "2024-10-17T16:30:00Z",
                "filled_at": "2024-10-17T16:30:05Z"
            }
        """
        order = db_client.get_order_by_client_id(client_order_id)

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order not found: {client_order_id}",
            )

        return order

    return router

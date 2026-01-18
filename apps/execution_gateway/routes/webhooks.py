"""
Webhook routes for Alpaca order updates.

CRITICAL SECURITY NOTE:
- Webhooks use SIGNATURE authentication ONLY (X-Alpaca-Signature header)
- Webhooks are NOT gated by kill-switch or circuit-breaker
- Webhooks MUST always process even when trading is halted
- This allows position tracking to remain accurate during system outages

Signature verification uses HMAC-SHA256 with WEBHOOK_SECRET.
See apps/execution_gateway/webhook_security.py for implementation.

This module uses FastAPI's native dependency injection pattern (Depends())
instead of factory functions for cleaner, more testable code.

Design Pattern:
    - Router defined at module level (not inside factory function)
    - Dependencies injected via Depends() in route handlers
    - Dependencies retrieved from app.state via dependency providers
    - No closure over dependencies (cleaner, more testable)

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for design decisions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import ExecutionGatewayConfig
from apps.execution_gateway.database import status_rank_for
from apps.execution_gateway.dependencies import get_config, get_context
from apps.execution_gateway.metrics import webhook_received_total
from apps.execution_gateway.reconciliation import SOURCE_PRIORITY_WEBHOOK
from apps.execution_gateway.services.order_helpers import parse_webhook_timestamp
from apps.execution_gateway.services.performance_cache import invalidate_performance_cache
from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

# Router defined at module level (Phase 2B refactoring)
router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])

# Metrics are defined in apps.execution_gateway.metrics
# status_rank_for imported from database.py (DRY - single source of truth)


# =============================================================================
# Webhook Endpoints
# =============================================================================


@router.post("/orders")
async def handle_order_webhook(
    request: Request,
    ctx: AppContext = Depends(get_context),
    config: ExecutionGatewayConfig = Depends(get_config),
) -> dict[str, str]:
    """
    Webhook for Alpaca order updates with per-fill P&L and row locking.

    AUTHENTICATION:
    - Uses signature verification (X-Alpaca-Signature header)
    - NO bearer token required
    - NO trading gates (kill-switch, circuit-breaker)

    PROCESSING:
    - Verifies HMAC-SHA256 signature
    - Updates order status in database
    - Processes fills with position updates
    - Tracks realized P&L per fill
    - Invalidates performance cache

    Args:
        request: FastAPI request object
        ctx: Application context with all dependencies (injected)
        config: Application configuration (injected)

    Returns:
        dict: Status response with client_order_id
    """
    try:
        # Read raw body for signature verification (before parsing JSON)
        body = await request.body()

        # Verify webhook signature BEFORE parsing JSON (security requirement)
        webhook_secret = ctx.webhook_secret
        if webhook_secret:
            signature_header = request.headers.get("X-Alpaca-Signature")
            signature = extract_signature_from_header(signature_header)

            if not signature:
                logger.warning("Webhook received without signature")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing webhook signature",
                )

            if not verify_webhook_signature(body, signature, webhook_secret):
                logger.error("Webhook signature verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid webhook signature",
                )

            logger.debug("Webhook signature verified successfully")
        else:
            # SECURITY: In production, missing webhook secret is a configuration error
            environment = config.environment
            if environment not in ("dev", "test"):
                logger.error(
                    "Webhook rejected: WEBHOOK_SECRET not configured in production",
                    extra={"environment": environment},
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Webhook verification unavailable - secret not configured",
                )
            logger.warning(
                "Webhook signature verification disabled (WEBHOOK_SECRET not set, dev/test only)"
            )

        # Parse JSON only AFTER signature verification passes
        payload = await request.json()

        # Validate payload is a dict (not a JSON array or primitive)
        if not isinstance(payload, dict):
            logger.warning(
                "Webhook payload is not a JSON object",
                extra={"payload_type": type(payload).__name__},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook payload must be a JSON object",
            )

        logger.info(
            f"Webhook received: {payload.get('event', 'unknown')}", extra={"payload": payload}
        )

        # Extract order information
        event_type = payload.get("event")

        # Track webhook metrics
        webhook_received_total.labels(event_type=event_type or "unknown").inc()
        order_data = payload.get("order", {})

        client_order_id = order_data.get("client_order_id")
        broker_order_id = order_data.get("id")
        order_status = order_data.get("status")
        filled_qty = order_data.get("filled_qty")
        filled_avg_price = order_data.get("filled_avg_price")

        if not client_order_id:
            logger.warning("Webhook missing client_order_id")
            return {"status": "ignored", "reason": "missing_client_order_id"}

        # Fast path: if no fill info, just update status and return
        # Use explicit None checks to avoid skipping valid 0/0.0 values
        if (
            event_type not in ("fill", "partial_fill")
            or filled_qty is None
            or filled_avg_price is None
        ):
            broker_updated_at = parse_webhook_timestamp(
                order_data.get("updated_at"),
                payload.get("timestamp"),
                order_data.get("created_at"),
                default=datetime.now(UTC),
            )

            updated_order = ctx.db.update_order_status_cas(
                client_order_id=client_order_id,
                status=order_status,
                broker_updated_at=broker_updated_at,
                status_rank=status_rank_for(order_status or ""),
                source_priority=SOURCE_PRIORITY_WEBHOOK,
                filled_qty=Decimal(str(filled_qty)) if filled_qty else Decimal("0"),
                filled_avg_price=Decimal(str(filled_avg_price)) if filled_avg_price else None,
                filled_at=None,
                broker_order_id=broker_order_id,
                broker_event_id=payload.get("execution_id"),
            )
            if not updated_order:
                logger.warning(f"Order not found for webhook or CAS skipped: {client_order_id}")
                return {"status": "ignored", "reason": "order_not_found"}
            return {"status": "ok", "client_order_id": client_order_id}

        # Fill processing: transactional with row locks
        filled_qty_dec = Decimal(str(filled_qty))
        filled_avg_price_dec = Decimal(str(filled_avg_price))

        # Handle null/blank/invalid price gracefully (use filled_avg_price as fallback)
        price_value = payload.get("price")
        try:
            # Treat None, empty string, and whitespace as missing
            if price_value is None or (isinstance(price_value, str) and not price_value.strip()):
                per_fill_price = filled_avg_price_dec
            else:
                per_fill_price = Decimal(str(price_value))
        except InvalidOperation:
            # Non-numeric price string - fallback to filled_avg_price
            logger.warning(
                f"Invalid per-fill price value, using filled_avg_price: {price_value}",
                extra={"client_order_id": client_order_id, "price_value": price_value},
            )
            per_fill_price = filled_avg_price_dec

        # Parse fill and broker timestamps using helper
        server_now = datetime.now(UTC)
        fill_timestamp = parse_webhook_timestamp(
            payload.get("timestamp"),
            payload.get("filled_at"),
            order_data.get("filled_at"),
            default=server_now,
        )

        broker_updated_at = parse_webhook_timestamp(
            order_data.get("updated_at"),
            payload.get("timestamp"),
            order_data.get("filled_at"),
            default=fill_timestamp,
        )

        with ctx.db.transaction() as conn:
            order = ctx.db.get_order_for_update(client_order_id, conn)
            if not order:
                logger.warning(f"Order not found for webhook: {client_order_id}")
                return {"status": "ignored", "reason": "order_not_found"}

            # Use Decimal values but compute integer delta from cumulative quantities
            # This ensures fractional fills accumulate at integer boundaries
            # e.g., 0.3 + 0.4 + 0.3 = 1.0 triggers a position update when crossing 1
            prev_filled_qty_dec = order.filled_qty or Decimal("0")
            incremental_fill_qty_int = int(filled_qty_dec) - int(prev_filled_qty_dec)

            # Log fractional remainder for observability (positions table uses integers)
            fractional_current = filled_qty_dec % 1
            fractional_prev = prev_filled_qty_dec % 1
            if fractional_current != 0 or fractional_prev != 0:
                logger.info(
                    "Fractional fill quantities detected; position updates at integer boundaries",
                    extra={
                        "client_order_id": client_order_id,
                        "filled_qty_decimal": str(filled_qty_dec),
                        "prev_filled_qty_decimal": str(prev_filled_qty_dec),
                        "incremental_fill_int": incremental_fill_qty_int,
                        "fractional_current": str(fractional_current),
                        "fractional_prev": str(fractional_prev),
                    },
                )

            # Only update position and append fill metadata if there's an incremental fill
            if incremental_fill_qty_int > 0:
                position_locked = ctx.db.get_position_for_update(order.symbol, conn)
                old_realized = position_locked.realized_pl if position_locked else Decimal("0")

                position = ctx.db.update_position_on_fill_with_conn(
                    symbol=order.symbol,
                    fill_qty=incremental_fill_qty_int,
                    fill_price=per_fill_price,
                    side=order.side,
                    conn=conn,
                )

                realized_delta = position.realized_pl - old_realized

                ctx.db.append_fill_to_order_metadata(
                    client_order_id=client_order_id,
                    fill_data={
                        "fill_id": f"{client_order_id}_{int(filled_qty_dec)}",
                        "fill_qty": incremental_fill_qty_int,
                        "fill_price": str(per_fill_price),
                        "realized_pl": str(realized_delta),
                        "timestamp": fill_timestamp.isoformat(),
                    },
                    conn=conn,
                )
            else:
                logger.info(
                    "No incremental fill; skipping position update but still updating order status",
                    extra={
                        "client_order_id": client_order_id,
                        "prev_filled_qty": str(prev_filled_qty_dec),
                        "current_filled_qty": str(filled_qty_dec),
                        "order_status": order_status,
                    },
                )

            # Always update order status/avg_price (even with no incremental fill)
            # This ensures status-only updates and price corrections are captured
            ctx.db.update_order_status_with_conn(
                client_order_id=client_order_id,
                status=order_status,
                filled_qty=filled_qty_dec,
                filled_avg_price=filled_avg_price_dec,
                filled_at=fill_timestamp if order_status == "filled" else None,
                conn=conn,
                broker_order_id=broker_order_id,
                broker_updated_at=broker_updated_at,
                status_rank=status_rank_for(order_status or ""),
                source_priority=SOURCE_PRIORITY_WEBHOOK,
                broker_event_id=payload.get("execution_id"),
            )

        # Invalidate performance cache after successful fill
        invalidate_performance_cache(ctx.redis, trade_date=fill_timestamp.date())

        return {"status": "ok", "client_order_id": client_order_id}

    except HTTPException:
        # Re-raise HTTPException with its original status code (e.g., 401 from signature validation)
        raise
    except ValidationError as e:
        logger.error(
            "Webhook processing error - validation error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook validation failed: {str(e)}",
        ) from e
    except psycopg.OperationalError as e:
        logger.error(
            "Webhook processing error - database connection error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook processing failed: database error",
        ) from e
    except (KeyError, TypeError, ValueError, InvalidOperation) as e:
        logger.error(
            "Webhook processing error - data parsing error",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Webhook processing failed: {str(e)}",
        ) from e


# =============================================================================
# Legacy Factory Function (Deprecated)
# =============================================================================
# The create_webhooks_router factory function has been deprecated in favor of
# module-level router definition with Depends() pattern.
# This factory is kept temporarily for backward compatibility during Phase 2B
# transition. It will be removed after all routes are migrated.
#
# See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 2B for migration details.

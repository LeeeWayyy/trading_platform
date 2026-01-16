"""
Webhook routes for Alpaca order updates.

CRITICAL SECURITY NOTE:
- Webhooks use SIGNATURE authentication ONLY (X-Alpaca-Signature header)
- Webhooks are NOT gated by kill-switch or circuit-breaker
- Webhooks MUST always process even when trading is halted
- This allows position tracking to remain accurate during system outages

Signature verification uses HMAC-SHA256 with WEBHOOK_SECRET.
See apps/execution_gateway/webhook_security.py for implementation.
"""

import logging
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import psycopg
from fastapi import APIRouter, HTTPException, Request, status
from prometheus_client import Counter
from pydantic import ValidationError

from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.reconciliation import SOURCE_PRIORITY_WEBHOOK
from apps.execution_gateway.services.order_helpers import parse_webhook_timestamp
from apps.execution_gateway.services.performance_cache import invalidate_performance_cache
from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

# Environment configuration
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")

# Webhook secret (loaded during app lifespan)
WEBHOOK_SECRET: str = ""

# Metrics
webhook_received_total = Counter(
    "execution_gateway_webhook_received_total",
    "Total webhook events received by type",
    ["event_type"],
)


def status_rank_for(status: str) -> int:
    """
    Return numeric rank for order status for CAS comparison.
    Higher rank = more authoritative.
    """
    rank_map = {
        "pending_new": 1,
        "accepted": 2,
        "new": 3,
        "pending_cancel": 4,
        "pending_replace": 5,
        "partially_filled": 6,
        "filled": 7,
        "canceled": 8,
        "expired": 8,
        "replaced": 8,
        "rejected": 9,
    }
    return rank_map.get(status, 0)


def create_webhooks_router(
    db_client: DatabaseClient,
    webhook_secret: str,
) -> APIRouter:
    """
    Create webhooks router with dependencies.

    Args:
        db_client: Database client for order/position updates
        webhook_secret: HMAC secret for signature verification

    Returns:
        Configured APIRouter instance
    """
    global WEBHOOK_SECRET
    WEBHOOK_SECRET = webhook_secret

    router = APIRouter(prefix="/api/v1/webhooks", tags=["Webhooks"])

    @router.post("/orders")
    async def handle_order_webhook(request: Request) -> dict[str, str]:
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

        Returns:
            dict: Status response with client_order_id
        """
        try:
            # Parse webhook payload
            body = await request.body()
            payload = await request.json()

            # Verify webhook signature (required when secret configured)
            if WEBHOOK_SECRET:
                signature_header = request.headers.get("X-Alpaca-Signature")
                signature = extract_signature_from_header(signature_header)

                if not signature:
                    logger.warning("Webhook received without signature")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Missing webhook signature",
                    )

                if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
                    logger.error("Webhook signature verification failed")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid webhook signature",
                    )

                logger.debug("Webhook signature verified successfully")
            else:
                # SECURITY: In production, missing webhook secret is a configuration error
                # We must reject webhooks rather than accepting unsigned ones
                if ENVIRONMENT not in ("dev", "test"):
                    logger.error(
                        "Webhook rejected: WEBHOOK_SECRET not configured in production",
                        extra={"environment": ENVIRONMENT},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Webhook verification unavailable - secret not configured",
                    )
                logger.warning(
                    "Webhook signature verification disabled (WEBHOOK_SECRET not set, dev/test only)"
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
            if (
                event_type not in ("fill", "partial_fill")
                or not filled_qty
                or not filled_avg_price
            ):
                broker_updated_at = parse_webhook_timestamp(
                    order_data.get("updated_at"),
                    payload.get("timestamp"),
                    order_data.get("created_at"),
                    default=datetime.now(UTC),
                )

                updated_order = db_client.update_order_status_cas(
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
                    logger.warning(
                        f"Order not found for webhook or CAS skipped: {client_order_id}"
                    )
                    return {"status": "ignored", "reason": "order_not_found"}
                return {"status": "ok", "client_order_id": client_order_id}

            # Fill processing: transactional with row locks
            filled_qty_dec = Decimal(str(filled_qty))
            filled_avg_price_dec = Decimal(str(filled_avg_price))

            per_fill_price = Decimal(str(payload.get("price", filled_avg_price_dec)))

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

            with db_client.transaction() as conn:
                order = db_client.get_order_for_update(client_order_id, conn)
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
                    position_locked = db_client.get_position_for_update(order.symbol, conn)
                    old_realized = position_locked.realized_pl if position_locked else Decimal("0")

                    position = db_client.update_position_on_fill_with_conn(
                        symbol=order.symbol,
                        fill_qty=incremental_fill_qty_int,
                        fill_price=per_fill_price,
                        side=order.side,
                        conn=conn,
                    )

                    realized_delta = position.realized_pl - old_realized

                    db_client.append_fill_to_order_metadata(
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
                db_client.update_order_status_with_conn(
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
            invalidate_performance_cache(trade_date=fill_timestamp.date())

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

    return router

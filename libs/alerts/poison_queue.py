"""Postgres-backed poison queue for failed alert deliveries."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from libs.alerts.metrics import alert_poison_queue_size
from libs.alerts.models import AlertDelivery, DeliveryStatus

logger = logging.getLogger(__name__)

# Patterns to sanitize PII from error messages before logging
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_PATTERN = re.compile(r"\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\+?[0-9]{10,15}")
_URL_PATTERN = re.compile(r"https?://[^\s]+")


def _sanitize_error_for_log(error: str) -> str:
    """Remove PII (emails, phone numbers) from error messages for safe logging."""
    sanitized = _EMAIL_PATTERN.sub("[EMAIL]", error)
    sanitized = _PHONE_PATTERN.sub("[PHONE]", sanitized)
    sanitized = _URL_PATTERN.sub("[URL]", sanitized)
    return sanitized


class PoisonQueue:
    """Handle failed deliveries stored in Postgres alert_deliveries table."""

    def __init__(self, db_pool: AsyncConnectionPool) -> None:
        self.db_pool = db_pool

    async def add(self, delivery_id: str, error: str) -> None:
        """Mark delivery as poisoned in Postgres."""
        now = datetime.now(UTC)
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET status = %s,
                        poison_at = %s,
                        error_message = %s,
                        last_attempt_at = %s
                    WHERE id = %s
                    """,
                    (
                        DeliveryStatus.POISON.value,
                        now,
                        error,
                        now,
                        delivery_id,
                    ),
                )
            await conn.commit()
        # Log sanitized error to prevent PII leakage; full error is in DB
        sanitized_error = _sanitize_error_for_log(error)
        logger.error("delivery_moved_to_poison", extra={
            "delivery_id": delivery_id,
            "error": sanitized_error,
        })
        alert_poison_queue_size.inc()
        # Refresh gauge from authoritative DB to avoid drift on duplicate inserts
        await self.sync_gauge_from_db()

    async def get_pending(self, limit: int = 100) -> list[AlertDelivery]:
        """Get unresolved poison queue items from Postgres."""
        async with self.db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT id,
                           alert_id,
                           channel,
                           recipient,
                           dedup_key,
                           status,
                           attempts,
                           last_attempt_at,
                           delivered_at,
                           poison_at,
                           error_message,
                           created_at
                    FROM alert_deliveries
                    WHERE status = %s
                    ORDER BY poison_at ASC NULLS LAST, created_at ASC
                    LIMIT %s
                    """,
                    (DeliveryStatus.POISON.value, limit),
                )
                rows: Iterable[dict[str, Any]] = await cur.fetchall()
        return [AlertDelivery(**row) for row in rows]

    async def sync_gauge_from_db(self) -> int:
        """Sync the Prometheus gauge with actual poison queue size from DB.

        Call this at startup to initialize the gauge correctly.
        """
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM alert_deliveries
                    WHERE status = %s
                    """,
                    (DeliveryStatus.POISON.value,),
                )
                row = await cur.fetchone()
        count = int(row[0]) if row is not None else 0
        alert_poison_queue_size.set(count)
        return count

    async def resolve(self, delivery_id: str, resolution: str) -> None:
        """Mark poison queue item as resolved."""
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET status = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        DeliveryStatus.FAILED.value,
                        resolution,
                        delivery_id,
                    ),
                )
            await conn.commit()
        logger.info("poison_delivery_resolved", extra={"delivery_id": delivery_id})
        # Reset gauge from authoritative count to avoid drift/negative values
        await self.sync_gauge_from_db()


__all__ = ["PoisonQueue"]

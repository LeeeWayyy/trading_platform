"""Alert submission layer for creating events and enqueuing deliveries."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import redis.asyncio as redis_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis import Redis
from rq import Queue

from apps.alert_worker.entrypoint import execute_delivery_job
from libs.alerts.dedup import compute_dedup_key, get_recipient_hash_secret
from libs.alerts.delivery_service import QueueDepthManager, QueueFullError
from libs.alerts.metrics import alert_dropped_total, alert_queue_full_total
from libs.alerts.models import AlertEvent, ChannelConfig, DeliveryStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _RuleChannels:
    name: str
    channels: list[ChannelConfig]


class AlertManager:
    """Coordinate alert creation and delivery submission via RQ."""

    def __init__(
        self,
        *,
        db_pool: AsyncConnectionPool,
        redis_client: redis_async.Redis,
        rq_queue: Queue | None = None,
        queue_depth_manager: QueueDepthManager | None = None,
        redis_sync: Redis | None = None,
    ) -> None:
        self.db_pool = db_pool
        self.redis = redis_client
        sync_redis = redis_sync or self._derive_sync_redis(redis_client)
        self.queue = rq_queue or Queue("alerts", connection=sync_redis)
        self.queue_depth_manager = queue_depth_manager or QueueDepthManager(redis_client)
        self._recipient_hash_secret = self._load_recipient_hash_secret()

    def _derive_sync_redis(self, async_client: redis_async.Redis) -> Redis:
        """Construct a sync Redis client sharing the async client's connection params."""
        params = dict(async_client.connection_pool.connection_kwargs)
        return Redis(**params)

    def _load_recipient_hash_secret(self) -> str:
        try:
            return get_recipient_hash_secret()
        except ValueError:
            logger.error("alert_recipient_hash_secret_missing")
            raise

    async def trigger_alert(
        self,
        rule_id: str,
        trigger_value: Decimal | None,
        triggered_at: datetime,
    ) -> AlertEvent:
        """Create alert event, insert delivery rows, and enqueue RQ jobs."""
        if not await self.queue_depth_manager.is_accepting():
            alert_queue_full_total.inc()
            alert_dropped_total.labels(channel="all", reason="queue_full").inc()
            raise QueueFullError()

        rule = await self._fetch_rule(rule_id)
        if not rule.channels:
            raise ValueError(f"Alert rule {rule_id} has no enabled channels")

        trigger_ts = (
            triggered_at if triggered_at.tzinfo is not None else triggered_at.replace(tzinfo=UTC)
        )

        async with self.db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    INSERT INTO alert_events (
                        rule_id,
                        trigger_value,
                        triggered_at,
                        routed_channels
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, rule_id, triggered_at, trigger_value, acknowledged_at,
                              acknowledged_by, acknowledged_note, routed_channels, created_at
                    """,
                    (
                        rule_id,
                        trigger_value,
                        trigger_ts,
                        [channel.type.value for channel in rule.channels],
                    ),
                )
                event_row = await cur.fetchone()
                if not event_row:
                    raise RuntimeError("Failed to insert alert_event")
                alert_event = AlertEvent(**event_row)

                deliveries = await self._create_deliveries(
                    cur=cur,
                    alert_event=alert_event,
                    rule_channels=rule.channels,
                    triggered_at=trigger_ts,
                )

            await conn.commit()

        await self._enqueue_deliveries(deliveries, rule.name, trigger_value, trigger_ts)
        return alert_event

    async def acknowledge_alert(self, alert_id: str, user_id: str, note: str | None = None) -> None:
        """Mark an alert event as acknowledged."""
        now = datetime.now(UTC)
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_events
                    SET acknowledged_at = %s,
                        acknowledged_by = %s,
                        acknowledged_note = %s
                    WHERE id = %s
                    """,
                    (
                        now,
                        user_id,
                        note,
                        alert_id,
                    ),
                )
            await conn.commit()

    async def _fetch_rule(self, rule_id: str) -> _RuleChannels:
        async with self.db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT name, channels
                    FROM alert_rules
                    WHERE id = %s AND enabled = TRUE
                    """,
                    (rule_id,),
                )
                row = await cur.fetchone()
        if not row:
            raise ValueError(f"Alert rule {rule_id} not found or disabled")

        channels_raw = row["channels"] or []
        channels = [
            ChannelConfig(**channel)
            for channel in channels_raw
            if channel.get("enabled", True)
        ]
        return _RuleChannels(name=row["name"], channels=channels)

    async def _create_deliveries(
        self,
        *,
        cur: Any,
        alert_event: AlertEvent,
        rule_channels: Sequence[ChannelConfig],
        triggered_at: datetime,
    ) -> list[tuple[str, ChannelConfig]]:
        deliveries: list[tuple[str, ChannelConfig]] = []
        for channel in rule_channels:
            dedup_key = compute_dedup_key(
                str(alert_event.rule_id),
                channel.type.value,
                channel.recipient,
                triggered_at,
                self._recipient_hash_secret,
            )
            await cur.execute(
                """
                INSERT INTO alert_deliveries (
                    alert_id, channel, recipient, dedup_key, status, attempts, created_at
                )
                VALUES (%s, %s, %s, %s, 'pending', 0, NOW())
                ON CONFLICT (dedup_key) DO NOTHING
                RETURNING id
                """,
                (
                    str(alert_event.id),
                    channel.type.value,
                    channel.recipient,
                    dedup_key,
                ),
            )
            delivery_row = await cur.fetchone()
            if delivery_row:
                deliveries.append((str(delivery_row["id"]), channel))
        return deliveries

    async def _mark_delivery_failed(self, delivery_id: str, error: str) -> None:
        """Mark a delivery as failed when enqueue fails."""
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET status = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (DeliveryStatus.FAILED.value, error, delivery_id),
                )
            await conn.commit()

    async def _enqueue_deliveries(
        self,
        deliveries: Sequence[tuple[str, ChannelConfig]],
        rule_name: str,
        trigger_value: Decimal | None,
        triggered_at: datetime,
    ) -> None:
        subject = f"[Alert] {rule_name} triggered"
        value_str = f"value={trigger_value}" if trigger_value is not None else "value=N/A"
        body = (
            f"Alert '{rule_name}' triggered at {triggered_at.isoformat()} "
            f"({value_str}). Alert ID reserved for tracking."
        )

        async def enqueue_one(delivery_id: str, channel: ChannelConfig) -> tuple[str, Exception | None]:
            """Enqueue a single delivery, returning (delivery_id, error or None)."""
            try:
                await asyncio.to_thread(
                    self.queue.enqueue,
                    execute_delivery_job,
                    delivery_id,
                    channel.type.value,
                    channel.recipient,
                    subject,
                    body,
                    0,
                )
                return (delivery_id, None)
            except Exception as exc:
                return (delivery_id, exc)

        tasks = []
        for delivery_id, channel in deliveries:
            await self.queue_depth_manager.increment()
            tasks.append(enqueue_one(delivery_id, channel))

        if tasks:
            results = await asyncio.gather(*tasks)
            for idx, (delivery_id, error) in enumerate(results):
                if error is not None:
                    await self.queue_depth_manager.decrement()
                    channel = deliveries[idx][1]
                    alert_dropped_total.labels(channel=channel.type.value, reason="enqueue_failed").inc()
                    logger.error("enqueue_failed", extra={"delivery_id": delivery_id}, exc_info=error)
                    # Mark delivery as failed so it's observable and can be retried
                    await self._mark_delivery_failed(delivery_id, f"Enqueue failed: {error}")

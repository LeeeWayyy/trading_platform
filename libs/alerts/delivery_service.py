"""Delivery execution and queue depth management for alerting."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

import redis.asyncio as redis_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from libs.alerts.channels import BaseChannel
from libs.alerts.dedup import compute_recipient_hash, get_recipient_hash_secret
from libs.alerts.metrics import (
    alert_delivery_attempts_total,
    alert_delivery_latency_seconds,
    alert_queue_depth,
    alert_retry_total,
    alert_throttle_total,
)
from libs.alerts.models import (
    AlertDelivery,
    ChannelType,
    DeliveryResult,
    DeliveryStatus,
)
from libs.alerts.poison_queue import PoisonQueue

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """Raised by submission layer when queue is full."""

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(f"Queue full. Retry after {retry_after}s")


class QueueDepthManager:
    """Manage queue depth with Redis atomic counters and hysteresis.

    A periodic background reconciliation task should call ``sync_depth_from_db``
    to realign the Redis counter with the authoritative database count of
    pending/in-progress deliveries.
    """

    MAX_QUEUE_DEPTH = 10000
    QUEUE_RESUME_THRESHOLD = 8000
    REDIS_KEY = "alert:queue:depth"

    def __init__(self, redis_client: redis_async.Redis) -> None:
        self.redis = redis_client
        self._accepting = True

    async def increment(self) -> int:
        """Increment queue depth atomically.

        Note: Called by the submission layer (AlertManager) to reserve a slot.
        The executor only decrements on exit.
        """
        result: int = await self.redis.incr(self.REDIS_KEY)
        depth = int(result)
        alert_queue_depth.set(depth)
        return depth

    async def decrement(self) -> int:
        """Decrement queue depth atomically. Returns new depth, clamped at 0."""
        # Use Lua script to atomically decrement but clamp at 0
        # This prevents underflow if increment/decrement get out of sync
        lua_script = """
        local current = redis.call('GET', KEYS[1])
        if current == false or tonumber(current) <= 0 then
            redis.call('SET', KEYS[1], 0)
            return 0
        else
            return redis.call('DECR', KEYS[1])
        end
        """
        result: int | None = await self.redis.eval(lua_script, 1, self.REDIS_KEY)  # type: ignore[misc]
        depth = int(result) if result is not None else 0
        if depth < 0:
            # Safety: reset to 0 if somehow negative
            await self.redis.set(self.REDIS_KEY, 0)
            logger.warning("queue_depth_underflow_corrected", extra={"was": depth})
            alert_queue_depth.set(0)
            return 0
        alert_queue_depth.set(depth)
        return depth

    async def get_depth(self) -> int:
        """Get current queue depth."""
        val = await self.redis.get(self.REDIS_KEY)
        depth = int(val) if val is not None else 0
        return depth

    async def is_accepting(self) -> bool:
        """Check if accepting new deliveries (with hysteresis)."""
        depth = await self.get_depth()
        if self._accepting and depth >= self.MAX_QUEUE_DEPTH:
            self._accepting = False
        elif not self._accepting and depth < self.QUEUE_RESUME_THRESHOLD:
            self._accepting = True
        return self._accepting

    async def sync_depth_from_db(self, db_pool: AsyncConnectionPool) -> int:
        """Recompute and reset the Redis queue depth from authoritative DB state."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM alert_deliveries
                    WHERE status IN (%s, %s)
                    """,
                    (DeliveryStatus.PENDING.value, DeliveryStatus.IN_PROGRESS.value),
                )
                row = await cur.fetchone()
        depth = int(row[0]) if row is not None else 0
        await self.redis.set(self.REDIS_KEY, depth)
        alert_queue_depth.set(depth)
        return depth


def is_transient_error(result: DeliveryResult) -> bool:
    """Determine if error is transient (should retry)."""
    if result.success:
        return False
    return result.retryable


class AlertRateLimiter(Protocol):
    """Protocol for alert rate limiter dependency."""

    async def check_channel_rate_limit(self, channel: str) -> bool: ...

    async def check_recipient_rate_limit(self, recipient_hash: str, channel: str) -> bool: ...

    async def check_global_rate_limit(self) -> bool: ...


RetryScheduler = Callable[[int, int], Awaitable[None]]


class DeliveryExecutor:
    """Execution layer: runs in RQ worker, handles delivery with retry."""

    RETRY_DELAYS = [1, 2, 4]  # seconds between retries
    MAX_ATTEMPTS = 3
    LONG_RETRY_THRESHOLD = 5  # Re-enqueue if Retry-After > 5s
    STUCK_TASK_THRESHOLD_MINUTES = 15
    MAX_IN_MEMORY_SLEEP_SECONDS = 240

    def __init__(
        self,
        *,
        channels: dict[ChannelType, BaseChannel],
        db_pool: AsyncConnectionPool,
        redis_client: redis_async.Redis,
        poison_queue: PoisonQueue,
        rate_limiter: AlertRateLimiter | None = None,
        retry_scheduler: RetryScheduler | None = None,
        recipient_hash_secret: str | None = None,
    ) -> None:
        self.channels = channels
        self.db_pool = db_pool
        self.redis = redis_client
        self.poison_queue = poison_queue
        self.rate_limiter = rate_limiter
        self.retry_scheduler = retry_scheduler
        self.queue_depth_manager = QueueDepthManager(redis_client)

        # Limit how long a worker will sleep on rate-limit backoff when no scheduler is available
        self.MAX_RATE_LIMIT_WAITS = 3

        self._recipient_hash_secret: str | None
        try:
            self._recipient_hash_secret = recipient_hash_secret or get_recipient_hash_secret()
        except ValueError:
            logger.warning("alert_recipient_hash_secret_missing")
            self._recipient_hash_secret = None

    RATE_LIMIT_RETRY_DELAY = 60  # Seconds to wait on rate limit before re-enqueue

    async def execute(
        self,
        delivery_id: str,
        channel: ChannelType,
        recipient: str,
        subject: str,
        body: str,
        attempt: int = 0,
    ) -> DeliveryResult:
        """
        Execute delivery with immediate attempt + retry backoff.

        Attempt 0: Immediate
        Attempt 1: After RETRY_DELAYS[0] = 1s
        Attempt 2: After RETRY_DELAYS[1] = 2s
        After MAX_ATTEMPTS: Move to poison queue

        Uses optimistic locking to prevent duplicate deliveries across workers.
        """
        last_result: DeliveryResult | None = None
        handoff_to_scheduler = False
        # Queue slot is reserved by submission layer; we should release it unless we hand off to scheduler
        queue_slot_reserved = True
        claimed = False
        rate_limit_waits = 0
        start_time = time.monotonic()

        try:
            # Atomic claim - prevents duplicate delivery by concurrent workers
            try:
                delivery = await self._claim_delivery(delivery_id)
            except Exception:
                # Release reserved queue slot if claim fails (e.g., DB outage)
                await self.queue_depth_manager.decrement()
                raise
            if delivery is None:
                # Already claimed, completed, or doesn't exist
                await self.queue_depth_manager.decrement()
                return DeliveryResult(
                    success=True,
                    message_id=delivery_id,
                    metadata={"skipped": "already_claimed_or_completed"},
                )

            claimed = True
            current_attempt = max(attempt, delivery.attempts)

            while current_attempt < self.MAX_ATTEMPTS:
                if current_attempt > attempt:
                    delay = self._delay_for_attempt(current_attempt)
                    if delay:
                        await asyncio.sleep(min(delay, self.MAX_IN_MEMORY_SLEEP_SECONDS))

                # Check rate limits - use retry_scheduler for rate-limited to avoid
                # consuming retry attempts on rate limits
                try:
                    rate_limit_result = await self._check_rate_limits(delivery, channel)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "rate_limit_check_error",
                        extra={"delivery_id": delivery_id, "channel": channel.value},
                    )
                    rate_limit_result = DeliveryResult(
                        success=False,
                        error="Rate limiter error",
                        retryable=True,
                        metadata={"limit": "rate_limiter_exception"},
                    )
                if rate_limit_result is not None:
                    last_result = rate_limit_result
                    limit_type = (
                        rate_limit_result.metadata.get("limit", "unknown")
                        if rate_limit_result.metadata
                        else "unknown"
                    )
                    alert_throttle_total.labels(channel=channel.value, limit_type=limit_type).inc()
                    retry_delay = self._rate_limit_delay(rate_limit_result)
                    # Don't consume retry attempts for rate limits - re-enqueue with delay
                    if self.retry_scheduler is not None:
                        await self._record_attempt_failure(
                            delivery_id=delivery_id,
                            attempts=current_attempt,
                            error=rate_limit_result.error,
                            status=DeliveryStatus.PENDING,
                        )
                        try:
                            await self.retry_scheduler(retry_delay, current_attempt)
                        except Exception:
                            logger.exception(
                                "retry_scheduler_enqueue_failed",
                                extra={
                                    "delivery_id": delivery_id,
                                    "channel": channel.value,
                                    "delay": retry_delay,
                                    "attempt": current_attempt,
                                },
                            )
                            last_result = DeliveryResult(
                                success=False,
                                error="scheduler_enqueue_failed",
                                retryable=True,
                                metadata={"limit": limit_type, "scheduler_failed": "true"},
                            )
                            attempt_number = current_attempt + 1
                            if attempt_number >= self.MAX_ATTEMPTS:
                                await self._record_attempt(
                                    delivery_id=delivery_id,
                                    attempts=attempt_number,
                                    status=DeliveryStatus.FAILED,
                                    error=last_result.error,
                                    delivered=False,
                                )
                                await self.poison_queue.add(
                                    delivery_id=delivery_id,
                                    error=last_result.error or "delivery failed",
                                )
                                return last_result
                            await self._record_attempt(
                                delivery_id=delivery_id,
                                attempts=attempt_number,
                                status=DeliveryStatus.IN_PROGRESS,
                                error=last_result.error,
                                delivered=False,
                            )
                            current_attempt = attempt_number
                            continue
                        alert_retry_total.labels(channel=channel.value).inc()
                        handoff_to_scheduler = True
                        return rate_limit_result
                    # No scheduler - wait without consuming a retry attempt
                    rate_limit_waits += 1
                    if rate_limit_waits >= self.MAX_RATE_LIMIT_WAITS:
                        attempt_number = current_attempt + 1
                        last_result = DeliveryResult(
                            success=False,
                            error="rate_limit_wait_exhausted",
                            retryable=False,
                            metadata={
                                "limit": (
                                    str(rate_limit_result.metadata.get("limit"))
                                    if rate_limit_result.metadata
                                    else ""
                                ),
                                "waits": str(rate_limit_waits),
                            },
                        )
                        await self._record_attempt_failure(
                            delivery_id=delivery_id,
                            attempts=attempt_number,
                            error=last_result.error,
                            status=DeliveryStatus.FAILED,
                        )
                        await self.poison_queue.add(
                            delivery_id=delivery_id,
                            error=last_result.error or "delivery failed",
                        )
                        return last_result
                    await self._record_attempt_failure(
                        delivery_id=delivery_id,
                        attempts=current_attempt,
                        error=rate_limit_result.error,
                        status=DeliveryStatus.IN_PROGRESS,
                    )
                    await asyncio.sleep(min(retry_delay, self.MAX_IN_MEMORY_SLEEP_SECONDS))
                    continue

                try:
                    channel_handler = self._get_channel(channel)
                except ValueError:
                    last_result = DeliveryResult(
                        success=False,
                        error="Unknown channel type",
                        retryable=False,
                    )
                    attempt_number = current_attempt + 1
                    await self._record_attempt(
                        delivery_id=delivery_id,
                        attempts=attempt_number,
                        status=DeliveryStatus.FAILED,
                        error=last_result.error,
                        delivered=False,
                    )
                    alert_delivery_attempts_total.labels(
                        channel=channel.value, status="failure"
                    ).inc()
                    await self.poison_queue.add(
                        delivery_id=delivery_id,
                        error=last_result.error or "delivery failed",
                    )
                    return last_result
                try:
                    last_result = await channel_handler.send(
                        recipient, subject, body, metadata=None
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "channel_send_unhandled_exception",
                        extra={"delivery_id": delivery_id, "channel": channel.value},
                    )
                    last_result = DeliveryResult(
                        success=False,
                        error=str(exc),
                        retryable=True,
                        metadata={"raised_exception": "true"},
                    )
                status = "success" if last_result.success else "failure"
                alert_delivery_attempts_total.labels(channel=channel.value, status=status).inc()

                attempt_number = current_attempt + 1
                is_terminal_failure = False
                if not last_result.success:
                    is_terminal_failure = (
                        attempt_number >= self.MAX_ATTEMPTS or not is_transient_error(last_result)
                    )

                should_reenqueue = False
                delay = 0
                if not last_result.success and not is_terminal_failure:
                    should_reenqueue, delay = self._should_reenqueue(last_result)

                will_reenqueue_with_scheduler = (
                    should_reenqueue and self.retry_scheduler is not None
                )

                if last_result.success:
                    await self._record_attempt(
                        delivery_id=delivery_id,
                        attempts=attempt_number,
                        status=DeliveryStatus.DELIVERED,
                        error=None,
                        delivered=True,
                    )
                    elapsed = time.monotonic() - start_time
                    alert_delivery_latency_seconds.labels(channel=channel.value).observe(elapsed)
                    return last_result

                if is_terminal_failure:
                    await self._record_attempt(
                        delivery_id=delivery_id,
                        attempts=attempt_number,
                        status=DeliveryStatus.FAILED,
                        error=last_result.error,
                        delivered=False,
                    )
                    await self.poison_queue.add(
                        delivery_id=delivery_id,
                        error=last_result.error or "delivery failed",
                    )
                    return last_result

                status_for_db = (
                    DeliveryStatus.PENDING
                    if will_reenqueue_with_scheduler
                    else DeliveryStatus.IN_PROGRESS
                )

                await self._record_attempt(
                    delivery_id=delivery_id,
                    attempts=attempt_number,
                    status=status_for_db,
                    error=last_result.error,
                    delivered=False,
                )

                if should_reenqueue:
                    if self.retry_scheduler is not None:
                        try:
                            await self.retry_scheduler(delay, attempt_number)
                            alert_retry_total.labels(channel=channel.value).inc()
                        except Exception:
                            # Keep status unchanged; allow finally to decrement depth
                            raise
                        handoff_to_scheduler = True
                        return last_result
                    await asyncio.sleep(min(delay, self.MAX_IN_MEMORY_SLEEP_SECONDS))
                    current_attempt = attempt_number
                    continue
                current_attempt = attempt_number

            if last_result is None:
                last_result = DeliveryResult(
                    success=False,
                    error="attempt_limit_reached",
                    retryable=False,
                )
                await self._record_attempt_failure(
                    delivery_id=delivery_id,
                    attempts=max(current_attempt, self.MAX_ATTEMPTS),
                    error="Attempt limit reached",
                    status=DeliveryStatus.FAILED,
                )
            await self.poison_queue.add(
                delivery_id=delivery_id, error=last_result.error or "delivery failed"
            )
            # Poison queue is a terminal, persisted state; mark persistence as successful
            return last_result
        finally:
            # Do not decrement queue depth when handing off to retry scheduler;
            # the delivery is still pending and will not re-increment on re-enqueue.
            if claimed and queue_slot_reserved and not handoff_to_scheduler:
                await self.queue_depth_manager.decrement()

    def _delay_for_attempt(self, attempt_index: int) -> int:
        """Return delay for attempt index (1-based retries)."""
        if attempt_index <= 0:
            return 0
        idx = min(attempt_index - 1, len(self.RETRY_DELAYS) - 1)
        return self.RETRY_DELAYS[idx]

    def _should_reenqueue(self, result: DeliveryResult) -> tuple[bool, int]:
        """Check if should re-enqueue with RQ delay instead of in-memory retry."""
        retry_after = result.metadata.get("retry_after") if result.metadata else None
        if retry_after is not None:
            try:
                retry_after_int = int(retry_after)
            except (TypeError, ValueError):
                return False, 0
            if retry_after_int > self.LONG_RETRY_THRESHOLD:
                return True, retry_after_int
        return False, 0

    def _rate_limit_delay(self, result: DeliveryResult) -> int:
        """Derive delay from rate-limit metadata, fall back to default."""
        retry_after = result.metadata.get("retry_after") if result.metadata else None
        if retry_after is None:
            return self.RATE_LIMIT_RETRY_DELAY
        try:
            retry_after_int = int(retry_after)
        except (TypeError, ValueError):
            return self.RATE_LIMIT_RETRY_DELAY
        if retry_after_int <= 0:
            return self.RATE_LIMIT_RETRY_DELAY
        return retry_after_int

    def _get_channel(self, channel: ChannelType) -> BaseChannel:
        if channel not in self.channels:
            raise ValueError(f"No channel handler configured for {channel}")
        return self.channels[channel]

    async def _claim_delivery(self, delivery_id: str) -> AlertDelivery | None:
        """Atomically claim a delivery for processing.

        Uses optimistic locking: UPDATE ... SET status='in_progress' WHERE status='pending'
        Only one worker can claim because we change status atomically.
        Returns None if already claimed/completed by another worker.
        """
        async with self.db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Atomic claim: set to IN_PROGRESS so only one worker proceeds
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET status = %s,
                        last_attempt_at = %s
                    WHERE id = %s AND (
                        status = %s OR (
                            status = %s AND last_attempt_at < (NOW() - (%s * INTERVAL '1 minute'))
                        )
                    )
                    RETURNING id, alert_id, channel, recipient, dedup_key, status, attempts,
                              last_attempt_at, delivered_at, poison_at, error_message, created_at
                    """,
                    (
                        DeliveryStatus.IN_PROGRESS.value,  # Claim exclusively
                        datetime.now(UTC),
                        delivery_id,
                        DeliveryStatus.PENDING.value,
                        DeliveryStatus.IN_PROGRESS.value,
                        self.STUCK_TASK_THRESHOLD_MINUTES,
                    ),
                )
                row = await cur.fetchone()
            await conn.commit()

            if not row:
                # Check if already delivered, claimed, or doesn't exist
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT id, status FROM alert_deliveries WHERE id = %s
                        """,
                        (delivery_id,),
                    )
                    check_row = await cur.fetchone()
                    if check_row and check_row["status"] == DeliveryStatus.DELIVERED.value:
                        logger.info(
                            "delivery_already_completed", extra={"delivery_id": delivery_id}
                        )
                    elif check_row and check_row["status"] == DeliveryStatus.IN_PROGRESS.value:
                        logger.info(
                            "delivery_claimed_by_another",
                            extra={
                                "delivery_id": delivery_id,
                            },
                        )
                    elif check_row:
                        logger.info(
                            "delivery_in_terminal_state",
                            extra={
                                "delivery_id": delivery_id,
                                "status": check_row["status"],
                            },
                        )
                    else:
                        logger.warning("delivery_not_found", extra={"delivery_id": delivery_id})
                return None

            return AlertDelivery(**row)

    async def _record_attempt(
        self,
        *,
        delivery_id: str,
        attempts: int,
        status: DeliveryStatus,
        error: str | None,
        delivered: bool,
    ) -> None:
        """Persist attempt metadata to Postgres."""
        now = datetime.now(UTC)
        delivered_at = now if delivered else None
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET attempts = %s,
                        status = %s,
                        last_attempt_at = %s,
                        delivered_at = COALESCE(delivered_at, %s),
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        attempts,
                        status.value,
                        now,
                        delivered_at,
                        error,
                        delivery_id,
                    ),
                )
            await conn.commit()

    async def _record_attempt_failure(
        self,
        *,
        delivery_id: str,
        attempts: int,
        error: str | None,
        status: DeliveryStatus,
    ) -> None:
        """Persist failed attempt without marking delivered."""
        now = datetime.now(UTC)
        async with self.db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE alert_deliveries
                    SET attempts = %s,
                        status = %s,
                        last_attempt_at = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        attempts,
                        status.value,
                        now,
                        error,
                        delivery_id,
                    ),
                )
            await conn.commit()

    async def _check_rate_limits(
        self,
        delivery: AlertDelivery,
        channel: ChannelType,
    ) -> DeliveryResult | None:
        """Return DeliveryResult on rate limit, otherwise None."""
        if self.rate_limiter is None:
            return None

        channel_allowed = await self.rate_limiter.check_channel_rate_limit(channel.value)
        if not channel_allowed:
            return DeliveryResult(
                success=False,
                error="channel_rate_limited",
                retryable=True,
                metadata={"limit": "channel"},
            )

        # Use delivery.recipient (stored value) for consistent rate limiting
        recipient_hash = self._extract_recipient_hash(delivery, channel.value)
        if recipient_hash:
            allowed = await self.rate_limiter.check_recipient_rate_limit(
                recipient_hash, channel.value
            )
            if not allowed:
                return DeliveryResult(
                    success=False,
                    error="recipient_rate_limited",
                    retryable=True,
                    metadata={"limit": "recipient"},
                )

        global_allowed = await self.rate_limiter.check_global_rate_limit()
        if not global_allowed:
            return DeliveryResult(
                success=False,
                error="global_rate_limited",
                retryable=True,
                metadata={"limit": "global"},
            )
        return None

    def _extract_recipient_hash(self, delivery: AlertDelivery, channel: str) -> str | None:
        """Extract recipient hash for rate limiting.

        Priority: compute from delivery.recipient first (supports secret rotation),
        then fall back to dedup_key hash if recipient unavailable.
        """
        # Prefer computing hash from stored recipient with current secret
        # This ensures consistent rate limiting across secret rotations
        if self._recipient_hash_secret and delivery.recipient:
            try:
                return compute_recipient_hash(
                    delivery.recipient, channel, self._recipient_hash_secret
                )
            except Exception:
                logger.warning("recipient_hash_compute_failed", exc_info=False)

        # Fall back to hash from dedup_key if recipient unavailable
        if delivery.dedup_key:
            parts = delivery.dedup_key.split(":")
            if len(parts) >= 3:
                return parts[2]

        return None


__all__ = [
    "AlertRateLimiter",
    "DeliveryExecutor",
    "QueueDepthManager",
    "QueueFullError",
    "RetryScheduler",
    "is_transient_error",
]

"""RQ worker entrypoint for alert delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import psycopg
import redis
import redis.asyncio as redis_async
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from redis import Redis
from rq import Queue, Worker

from libs.core.common.exceptions import ConfigurationError
from libs.platform.alerts.channels import BaseChannel, EmailChannel, SlackChannel, SMSChannel
from libs.platform.alerts.delivery_service import DeliveryExecutor, QueueDepthManager
from libs.platform.alerts.models import ChannelType, DeliveryResult
from libs.platform.alerts.poison_queue import PoisonQueue
from libs.platform.web_console_auth.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Sync singletons are safe to share across jobs because RQ invokes the job
# function in the worker process without reusing asyncio event loops.
_CHANNELS: dict[ChannelType, BaseChannel] | None = None
_RQ_QUEUE: Queue | None = None


@dataclass
class AsyncResources:
    """Per-job async resources bound to the job's event loop."""

    db_pool: AsyncConnectionPool
    redis_client: redis_async.Redis
    poison_queue: PoisonQueue
    rate_limiter: RateLimiter


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("worker_startup_failed", extra={"reason": f"{name} not set"})
        sys.exit(1)
    return value


def _get_rq_queue() -> Queue:
    global _RQ_QUEUE
    if _RQ_QUEUE is None:
        redis_url = _require_env("REDIS_URL")
        redis_sync = Redis.from_url(redis_url)
        _RQ_QUEUE = Queue("alerts", connection=redis_sync)
    return _RQ_QUEUE


async def _create_async_resources() -> AsyncResources:
    """Instantiate async resources inside the job's event loop."""
    redis_url = _require_env("REDIS_URL")
    db_url = _require_env("DATABASE_URL")
    redis_client = cast(redis_async.Redis, redis_async.from_url(redis_url))  # type: ignore[no-untyped-call]
    db_pool = AsyncConnectionPool(conninfo=db_url, min_size=1, max_size=5)
    await db_pool.open()
    poison_queue = PoisonQueue(db_pool)
    rate_limiter = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    return AsyncResources(
        db_pool=db_pool,
        redis_client=redis_client,
        poison_queue=poison_queue,
        rate_limiter=rate_limiter,
    )


async def _close_async_resources(resources: AsyncResources) -> None:
    """Tear down per-job async resources safely.

    Ensures both resources are closed even if one raises an exception.
    Uses ExceptionGroup to preserve all exceptions if multiple occur.
    """
    exceptions: list[Exception] = []
    try:
        await resources.db_pool.close()
    except psycopg.Error as exc:
        logger.warning(
            "db_pool_close_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        exceptions.append(exc)
    try:
        await resources.redis_client.close()
    except redis.exceptions.RedisError as exc:
        logger.warning(
            "redis_client_close_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        exceptions.append(exc)
    if exceptions:
        if len(exceptions) == 1:
            raise exceptions[0]
        raise ExceptionGroup("resource cleanup failed", exceptions)


def _get_channels() -> dict[ChannelType, BaseChannel]:
    """Build channel handlers, lazily skipping unconfigured channels.

    SMS channel requires Twilio credentials. If not configured, SMS is
    skipped and a warning is logged. Email and Slack are always enabled.
    """
    global _CHANNELS
    if _CHANNELS is None:
        _CHANNELS = {
            ChannelType.EMAIL: EmailChannel(),
            ChannelType.SLACK: SlackChannel(),
        }
        # SMS requires Twilio credentials - skip if not configured
        try:
            _CHANNELS[ChannelType.SMS] = SMSChannel()
        except ConfigurationError as exc:
            logger.warning(
                "sms_channel_disabled",
                extra={
                    "reason": str(exc),
                    "hint": "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER",
                },
            )
    return _CHANNELS


async def _build_executor(
    resources: AsyncResources,
    delivery_id: str,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
) -> DeliveryExecutor:
    queue = _get_rq_queue()
    channels = _get_channels()

    async def schedule_retry(delay: int, next_attempt: int) -> None:
        await asyncio.to_thread(
            queue.enqueue_in,
            timedelta(seconds=delay),
            execute_delivery_job,
            delivery_id,
            channel,
            recipient,
            subject,
            body,
            next_attempt,
        )

    return DeliveryExecutor(
        channels=channels,
        db_pool=resources.db_pool,
        redis_client=resources.redis_client,
        poison_queue=resources.poison_queue,
        rate_limiter=resources.rate_limiter,
        retry_scheduler=schedule_retry,
    )


async def _execute_delivery_job(
    delivery_id: str,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    attempt: int = 0,
) -> dict[str, str | bool | None]:
    channel_enum = ChannelType(channel)
    resources = await _create_async_resources()
    try:
        executor = await _build_executor(resources, delivery_id, channel, recipient, subject, body)
        result: DeliveryResult = await executor.execute(
            delivery_id=delivery_id,
            channel=channel_enum,
            recipient=recipient,
            subject=subject,
            body=body,
            attempt=attempt,
        )
        return result.dict()
    finally:
        await _close_async_resources(resources)


def execute_delivery_job(
    delivery_id: str,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    attempt: int = 0,
) -> dict[str, str | bool | None]:
    """RQ job wrapper to run DeliveryExecutor within asyncio loop."""
    return asyncio.run(
        _execute_delivery_job(
            delivery_id=delivery_id,
            channel=channel,
            recipient=recipient,
            subject=subject,
            body=body,
            attempt=attempt,
        )
    )


def main() -> None:
    """Validate environment and start RQ worker."""
    redis_url = _require_env("REDIS_URL")
    db_url = _require_env("DATABASE_URL")

    redis_client = Redis.from_url(redis_url)

    # Fail fast if DB is unreachable
    try:
        db_pool = ConnectionPool(conninfo=db_url, min_size=1, max_size=1)
        with db_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        db_pool.close()
    except psycopg.Error as exc:  # pragma: no cover - startup guard
        logger.error("db_connection_failed", extra={"error": str(exc)})
        sys.exit(1)

    try:
        redis_client.ping()
    except redis.exceptions.RedisError as exc:
        logger.error("redis_connection_failed", extra={"error": str(exc)})
        sys.exit(1)

    async def _sync_startup_metrics() -> None:
        resources = await _create_async_resources()
        try:
            poison_count = await resources.poison_queue.sync_gauge_from_db()
            qdm = QueueDepthManager(resources.redis_client)
            depth = await qdm.sync_depth_from_db(resources.db_pool)
            logger.info(
                "startup_metrics_synced",
                extra={"poison_queue_count": poison_count, "queue_depth": depth},
            )
        finally:
            await _close_async_resources(resources)

    asyncio.run(_sync_startup_metrics())

    queues_env = os.getenv("RQ_QUEUES")
    queues = [q.strip() for q in queues_env.split(",") if q.strip()] if queues_env else ["alerts"]

    worker = Worker(queues, connection=redis_client)
    logger.info("alert_worker_starting", extra={"queues": queues, "pid": os.getpid()})
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()

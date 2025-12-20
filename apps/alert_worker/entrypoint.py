"""RQ worker entrypoint for alert delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import timedelta
from typing import cast

import redis
import redis.asyncio as redis_async
from psycopg_pool import AsyncConnectionPool, ConnectionPool
from redis import Redis
from rq import Queue, Worker

from libs.alerts.channels import BaseChannel, EmailChannel, SlackChannel, SMSChannel
from libs.alerts.delivery_service import DeliveryExecutor, QueueDepthManager
from libs.alerts.models import ChannelType, DeliveryResult
from libs.alerts.poison_queue import PoisonQueue
from libs.web_console_auth.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_ASYNC_DB_POOL: AsyncConnectionPool | None = None
_ASYNC_REDIS: redis_async.Redis | None = None
_POISON_QUEUE: PoisonQueue | None = None
_CHANNELS: dict[ChannelType, BaseChannel] | None = None
_RQ_QUEUE: Queue | None = None
_RATE_LIMITER: RateLimiter | None = None


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


async def _get_async_redis() -> redis_async.Redis:
    global _ASYNC_REDIS
    if _ASYNC_REDIS is None:
        redis_url = _require_env("REDIS_URL")
        _ASYNC_REDIS = cast(redis_async.Redis, redis_async.from_url(redis_url))  # type: ignore[no-untyped-call]
    return _ASYNC_REDIS


async def _get_db_pool() -> AsyncConnectionPool:
    global _ASYNC_DB_POOL
    if _ASYNC_DB_POOL is None:
        db_url = _require_env("DATABASE_URL")
        _ASYNC_DB_POOL = AsyncConnectionPool(conninfo=db_url, min_size=1, max_size=5)
        await _ASYNC_DB_POOL.open()
    return _ASYNC_DB_POOL


async def _get_poison_queue() -> PoisonQueue:
    global _POISON_QUEUE
    if _POISON_QUEUE is None:
        pool = await _get_db_pool()
        _POISON_QUEUE = PoisonQueue(pool)
    return _POISON_QUEUE


async def _get_rate_limiter() -> RateLimiter:
    """Build a shared alert rate limiter backed by Redis."""
    global _RATE_LIMITER
    if _RATE_LIMITER is None:
        redis_client = await _get_async_redis()
        _RATE_LIMITER = RateLimiter(redis_client=redis_client, fallback_mode="deny")
    return _RATE_LIMITER


def _get_channels() -> dict[ChannelType, BaseChannel]:
    global _CHANNELS
    if _CHANNELS is None:
        _CHANNELS = {
            ChannelType.EMAIL: EmailChannel(),
            ChannelType.SLACK: SlackChannel(),
            ChannelType.SMS: SMSChannel(),
        }
    return _CHANNELS


async def _build_executor(
    delivery_id: str,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
) -> DeliveryExecutor:
    queue = _get_rq_queue()
    redis_client = await _get_async_redis()
    db_pool = await _get_db_pool()
    poison_queue = await _get_poison_queue()
    rate_limiter = await _get_rate_limiter()
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
        db_pool=db_pool,
        redis_client=redis_client,
        poison_queue=poison_queue,
        rate_limiter=rate_limiter,
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
    executor = await _build_executor(delivery_id, channel, recipient, subject, body)
    result: DeliveryResult = await executor.execute(
        delivery_id=delivery_id,
        channel=channel_enum,
        recipient=recipient,
        subject=subject,
        body=body,
        attempt=attempt,
    )
    return result.dict()


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
    except Exception as exc:  # pragma: no cover - startup guard
        logger.error("db_connection_failed", extra={"error": str(exc)})
        sys.exit(1)

    try:
        redis_client.ping()
    except redis.exceptions.RedisError as exc:
        logger.error("redis_connection_failed", extra={"error": str(exc)})
        sys.exit(1)

    # Initialize poison queue gauge from DB at startup
    async def _sync_poison_queue_gauge() -> None:
        poison_queue = await _get_poison_queue()
        count = await poison_queue.sync_gauge_from_db()
        logger.info("poison_queue_gauge_synced", extra={"count": count})

    async def _sync_queue_depth() -> None:
        redis_client = await _get_async_redis()
        db_pool = await _get_db_pool()
        qdm = QueueDepthManager(redis_client)
        depth = await qdm.sync_depth_from_db(db_pool)
        logger.info("queue_depth_synced", extra={"depth": depth})

    async def _sync_startup_metrics() -> None:
        await asyncio.gather(
            _sync_poison_queue_gauge(),
            _sync_queue_depth(),
        )

    asyncio.run(_sync_startup_metrics())

    queues_env = os.getenv("RQ_QUEUES")
    queues = [q.strip() for q in queues_env.split(",") if q.strip()] if queues_env else ["alerts"]

    worker = Worker(queues, connection=redis_client)
    logger.info("alert_worker_starting", extra={"queues": queues, "pid": os.getpid()})
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()

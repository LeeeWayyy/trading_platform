"""
RQ backtest worker entrypoint.

Validates environment, verifies Redis connectivity, registers the retry handler,
and starts the RQ worker processing the three priority queues.
"""

from __future__ import annotations

import os
import sys

import redis
import structlog  # type: ignore[import-not-found]
from redis import Redis
from rq import Worker

from libs.backtest.worker import record_retry

logger = structlog.get_logger(__name__)


def _require_env(name: str) -> str:
    """Fetch required environment variable or exit with error."""
    value = os.getenv(name)
    if not value:
        logger.error("worker_startup_failed", reason=f"{name} not set")
        sys.exit(1)
    return value


def main() -> None:
    """Worker entrypoint - validates env and starts RQ worker loop."""
    redis_url = _require_env("REDIS_URL")
    _require_env("DATABASE_URL")  # needed by retry hook; value checked for presence

    redis_client = Redis.from_url(redis_url)

    # Verify Redis connectivity before starting worker loop
    try:
        redis_client.ping()
    except redis.exceptions.RedisError as exc:
        logger.error("redis_connection_failed", error=str(exc))
        sys.exit(1)

    # RQ_QUEUES env var allows specifying which queues to process (comma-separated)
    # If not set, process all priority queues
    rq_queues_env = os.getenv("RQ_QUEUES")
    if rq_queues_env:
        queues = [q.strip() for q in rq_queues_env.split(",") if q.strip()]
    else:
        queues = ["backtest_high", "backtest_normal", "backtest_low"]
    worker = Worker(queues, connection=redis_client)

    # Register retry handler to track automated retries in DB
    worker.push_exc_handler(record_retry)  # type: ignore[no-untyped-call]

    logger.info("worker_starting", queues=queues, pid=os.getpid())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()

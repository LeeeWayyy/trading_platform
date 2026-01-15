"""Redis client helpers shared across services.

Centralizes Redis configuration parsing and client construction so that
web_console and execution_gateway use consistent settings and error handling.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import redis
import redis.asyncio as redis_async

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RedisConfig:
    """Parsed Redis connection configuration."""

    host: str
    port: int
    db: int


def load_redis_config() -> RedisConfig:
    """Load Redis config from environment with safe fallbacks."""

    host = os.getenv("REDIS_HOST", "localhost")

    try:
        port = int(os.getenv("REDIS_PORT", "6379"))
    except ValueError:
        logger.warning("invalid_redis_port_env", extra={"value": os.getenv("REDIS_PORT")})
        port = 6379

    try:
        db = int(os.getenv("REDIS_DB", "0"))
    except ValueError:
        logger.warning("invalid_redis_db_env", extra={"value": os.getenv("REDIS_DB")})
        db = 0

    return RedisConfig(host=host, port=port, db=db)


def create_sync_redis(config: RedisConfig, *, decode_responses: bool = True) -> redis.Redis:
    """Create a synchronous Redis client from parsed config."""

    return redis.Redis(
        host=config.host,
        port=config.port,
        db=config.db,
        decode_responses=decode_responses,
    )


def create_async_redis(config: RedisConfig, *, decode_responses: bool = True) -> redis_async.Redis:
    """Create an async Redis client from parsed config."""

    return redis_async.Redis(
        host=config.host,
        port=config.port,
        db=config.db,
        decode_responses=decode_responses,
    )


__all__ = ["RedisConfig", "create_async_redis", "create_sync_redis", "load_redis_config"]

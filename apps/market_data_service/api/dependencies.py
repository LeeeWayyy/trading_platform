"""Shared FastAPI dependencies for Market Data Service auth."""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as redis_async
from psycopg_pool import AsyncConnectionPool

from libs.core.common.secrets import get_required_secret
from libs.platform.web_console_auth.config import AuthConfig
from libs.platform.web_console_auth.gateway_auth import GatewayAuthenticator
from libs.platform.web_console_auth.jwt_manager import JWTManager
from libs.platform.web_console_auth.redis_client import (
    create_async_redis,
    create_sync_redis,
    load_redis_config,
)

# Environment configuration (same pattern as Execution Gateway)
REDIS_CONFIG = load_redis_config()

_gateway_authenticator: GatewayAuthenticator | None = None


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_database_url() -> str:
    """Return database URL from secrets (lazy loading)."""

    return get_required_secret("database/url")


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_db_pool() -> AsyncConnectionPool:
    """Return async connection pool for auth/session validation."""

    return AsyncConnectionPool(get_database_url(), open=False)


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_async_redis() -> redis_async.Redis:
    """Return shared async Redis client (decode responses for string keys)."""

    return create_async_redis(REDIS_CONFIG, decode_responses=True)


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def get_sync_redis():
    """Return sync Redis client (used by JWTManager blacklist)."""

    return create_sync_redis(REDIS_CONFIG, decode_responses=True)


def build_market_data_authenticator() -> GatewayAuthenticator:
    """Build GatewayAuthenticator without FastAPI DI (for api_auth dependency)."""

    global _gateway_authenticator
    if _gateway_authenticator is None:
        config = AuthConfig.from_env()
        jwt_manager = JWTManager(config=config, redis_client=get_sync_redis())
        _gateway_authenticator = GatewayAuthenticator(
            jwt_manager=jwt_manager,
            db_pool=get_db_pool(),
            redis_client=get_async_redis(),
        )
    return _gateway_authenticator

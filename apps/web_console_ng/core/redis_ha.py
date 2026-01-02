# apps/web_console_ng/core/redis_ha.py
# IMPORTANT: Use redis.asyncio.sentinel for async Sentinel support
# Requires redis-py >= 5.0
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel
from redis.exceptions import RedisError

from apps.web_console_ng import config

logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext | None:
    need_ssl_context = config.REDIS_SSL_ENABLED or config.REDIS_SENTINEL_SSL_ENABLED
    if not need_ssl_context:
        return None

    ssl_context = ssl.create_default_context(cafile=config.REDIS_SSL_CA_CERTS or None)
    cert_reqs_map = {
        "required": ssl.CERT_REQUIRED,
        "optional": ssl.CERT_OPTIONAL,
        "none": ssl.CERT_NONE,
    }
    ssl_context.verify_mode = cert_reqs_map.get(
        config.REDIS_SSL_CERT_REQS.lower(), ssl.CERT_REQUIRED
    )
    ssl_context.check_hostname = ssl_context.verify_mode == ssl.CERT_REQUIRED

    if config.REDIS_SSL_CERTFILE and config.REDIS_SSL_KEYFILE:
        ssl_context.load_cert_chain(
            certfile=config.REDIS_SSL_CERTFILE,
            keyfile=config.REDIS_SSL_KEYFILE,
        )

    return ssl_context


class HARedisStore:
    """
    High-availability Redis store with async Sentinel support.

    Provides automatic failover and read replica support.
    Requires redis-py >= 5.0 for redis.asyncio.sentinel module.
    """

    _instance: HARedisStore | None = None

    # Configurable pool size: default 200 for 1000 WS connections (20% ratio)
    POOL_MAX_CONNECTIONS = getattr(config, "REDIS_POOL_MAX_CONNECTIONS", 200)

    def __init__(self) -> None:
        ssl_context = _build_ssl_context()

        # Build sentinel_kwargs with TLS if enabled
        sentinel_kwargs: dict[str, Any] = {"password": config.REDIS_SENTINEL_PASSWORD}
        if config.REDIS_SENTINEL_SSL_ENABLED and ssl_context:
            sentinel_kwargs["ssl"] = True
            sentinel_kwargs["ssl_context"] = ssl_context

        # Use async Sentinel from redis.asyncio.sentinel
        self.sentinel = Sentinel(
            config.REDIS_SENTINEL_HOSTS,  # [('sentinel-1', 26379), ...]
            socket_timeout=0.5,
            password=config.REDIS_PASSWORD,
            sentinel_kwargs=sentinel_kwargs,  # Sentinel auth + TLS
            ssl=config.REDIS_SSL_ENABLED,  # Enable TLS for data connections
            ssl_context=ssl_context if config.REDIS_SSL_ENABLED else None,
        )
        self.master_name = config.REDIS_MASTER_NAME  # "nicegui-sessions"
        self._master_text: Redis | None = None
        self._master_binary: Redis | None = None
        self._slave_text: Redis | None = None
        self._slave_binary: Redis | None = None
        self._ssl_context: ssl.SSLContext | None = ssl_context if config.REDIS_SSL_ENABLED else None

    @classmethod
    def get(cls) -> HARedisStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _build_master_client(self, *, decode_responses: bool) -> Redis:
        return self.sentinel.master_for(  # type: ignore[no-any-return]
            self.master_name,
            socket_timeout=0.5,
            decode_responses=decode_responses,
            ssl=True if self._ssl_context else False,
            ssl_context=self._ssl_context,
            connection_pool_class_kwargs={
                "max_connections": self.POOL_MAX_CONNECTIONS,
            },
        )

    def _build_slave_client(self, *, decode_responses: bool) -> Redis:
        return self.sentinel.slave_for(  # type: ignore[no-any-return]
            self.master_name,
            socket_timeout=0.5,
            decode_responses=decode_responses,
            ssl=True if self._ssl_context else False,
            ssl_context=self._ssl_context,
            connection_pool_class_kwargs={
                "max_connections": self.POOL_MAX_CONNECTIONS,
            },
        )

    def get_master_client(self, *, decode_responses: bool = True) -> Redis:
        """Return master client without connectivity checks (sync)."""
        if decode_responses:
            if self._master_text is None:
                self._master_text = self._build_master_client(decode_responses=True)
            return self._master_text

        if self._master_binary is None:
            self._master_binary = self._build_master_client(decode_responses=False)
        return self._master_binary

    def get_slave_client(self, *, decode_responses: bool = True) -> Redis:
        """Return slave client without connectivity checks (sync)."""
        if decode_responses:
            if self._slave_text is None:
                self._slave_text = self._build_slave_client(decode_responses=True)
            return self._slave_text

        if self._slave_binary is None:
            self._slave_binary = self._build_slave_client(decode_responses=False)
        return self._slave_binary

    async def get_master(self) -> Redis:
        """Get master connection for writes with connection pooling.

        NOTE: This method is async because it performs a connection health check
        via _is_connected() which pings Redis. The sync get_master_client() returns
        the client without verification.

        Returns client with decode_responses=True (strings, not bytes).
        For binary data (encryption), use get_master_client(decode_responses=False).
        """
        master = self.get_master_client(decode_responses=True)
        if not await self._is_connected(master):
            master = self._build_master_client(decode_responses=True)
            self._master_text = master
        return master

    async def get_slave(self) -> Redis:
        """Get slave connection for reads (fallback to master) with connection pooling.

        NOTE: Returns client with decode_responses=True (strings, not bytes).
        For binary data (encryption), use get_slave_client(decode_responses=False).
        """
        try:
            slave = self.get_slave_client(decode_responses=True)
        except Exception:
            master = await self.get_master()
            self._slave_text = master
            return master

        if not await self._is_connected(slave):
            try:
                slave = self._build_slave_client(decode_responses=True)
                self._slave_text = slave
            except Exception:
                slave = await self.get_master()
                self._slave_text = slave
        return slave

    async def _is_connected(self, conn: Redis) -> bool:
        """Check if connection is alive."""
        try:
            await asyncio.wait_for(conn.ping(), timeout=0.5)
            return True
        except Exception:
            return False

    async def ping(self) -> bool:
        """Health check - verify Redis is reachable."""
        master = await self.get_master()
        return await master.ping()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Explicitly close all Redis connection pools.

        Call this during application shutdown (in lifespan handler) to prevent
        'Unclosed connection' warnings in logs. While asyncio loop termination
        handles cleanup eventually, explicit closure is cleaner.
        """
        clients = [
            self._master_text,
            self._master_binary,
            self._slave_text,
            self._slave_binary,
        ]
        for client in clients:
            if client is not None:
                try:
                    await client.aclose()
                except (RedisError, OSError, ConnectionError) as e:
                    logger.warning("Failed to close Redis client during shutdown: %s", e)
        self._master_text = None
        self._master_binary = None
        self._slave_text = None
        self._slave_binary = None


# Fallback for non-Sentinel environments (dev/test)
class SimpleRedisStore:
    """Simple Redis store for development (no Sentinel).

    Supports TLS when REDIS_SSL_ENABLED=true for secure dev/test environments.
    """

    _instance: SimpleRedisStore | None = None

    def __init__(self) -> None:
        # Build connection options
        connection_kwargs: dict[str, Any] = {"decode_responses": True}
        connection_kwargs_binary: dict[str, Any] = {"decode_responses": False}

        # Add TLS if enabled (mirrors HARedisStore config exactly)
        if config.REDIS_SSL_ENABLED:
            ssl_context = ssl.create_default_context()

            # Use same mapping as HARedisStore for consistency
            cert_reqs_map = {
                "required": ssl.CERT_REQUIRED,
                "optional": ssl.CERT_OPTIONAL,
                "none": ssl.CERT_NONE,
            }
            ssl_context.verify_mode = cert_reqs_map.get(
                config.REDIS_SSL_CERT_REQS.lower(), ssl.CERT_REQUIRED
            )
            ssl_context.check_hostname = ssl_context.verify_mode == ssl.CERT_REQUIRED

            # Load CA cert if provided
            if config.REDIS_SSL_CA_CERTS:
                ssl_context.load_verify_locations(config.REDIS_SSL_CA_CERTS)

            # Load client cert for mTLS if provided
            if config.REDIS_SSL_CERTFILE and config.REDIS_SSL_KEYFILE:
                ssl_context.load_cert_chain(
                    certfile=config.REDIS_SSL_CERTFILE,
                    keyfile=config.REDIS_SSL_KEYFILE,
                )

            connection_kwargs["ssl"] = True
            connection_kwargs["ssl_context"] = ssl_context
            connection_kwargs_binary["ssl"] = True
            connection_kwargs_binary["ssl_context"] = ssl_context

        self.redis: Redis = Redis.from_url(config.REDIS_URL, **connection_kwargs)
        self.redis_binary: Redis = Redis.from_url(config.REDIS_URL, **connection_kwargs_binary)

    @classmethod
    def get(cls) -> SimpleRedisStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_master(self) -> Redis:
        return self.redis

    async def get_slave(self) -> Redis:
        return self.redis

    def get_master_client(self, *, decode_responses: bool = True) -> Redis:
        return self.redis if decode_responses else self.redis_binary

    def get_slave_client(self, *, decode_responses: bool = True) -> Redis:
        return self.redis if decode_responses else self.redis_binary

    async def ping(self) -> bool:
        return await self.redis.ping()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Explicitly close Redis connection pools."""
        try:
            await self.redis.aclose()
        except (RedisError, OSError, ConnectionError) as e:
            logger.warning("Failed to close Redis client during shutdown: %s", e)
        try:
            await self.redis_binary.aclose()
        except (RedisError, OSError, ConnectionError) as e:
            logger.warning("Failed to close Redis binary client during shutdown: %s", e)


def get_redis_store() -> HARedisStore | SimpleRedisStore:
    """Factory function - returns appropriate store based on config."""
    if config.REDIS_USE_SENTINEL:
        return HARedisStore.get()
    return SimpleRedisStore.get()

"""Strategy-scoped data access with server-side filtering and caching."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import logging
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from apps.web_console.auth.permissions import get_authorized_strategies

logger = logging.getLogger(__name__)

STRATEGY_CACHE_DB_ENV = "REDIS_STRATEGY_CACHE_DB"
DEFAULT_STRATEGY_CACHE_DB = 3
STRATEGY_CACHE_KEY_ENV = "STRATEGY_CACHE_ENCRYPTION_KEY"


def _get_cache_encryption_key() -> bytes | None:
    """Get the 32-byte encryption key for strategy cache.

    Returns None if not configured (cache will be disabled for security).
    """
    key_b64 = os.getenv(STRATEGY_CACHE_KEY_ENV)
    if not key_b64:
        return None
    try:
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            logger.warning("strategy_cache_key_invalid_length", extra={"length": len(key)})
            return None
        return key
    except Exception:
        logger.warning("strategy_cache_key_decode_failed", exc_info=True)
        return None


def _build_cache_client(redis_client: Any) -> Any:
    """Return Redis client backed by an isolated DB for strategy caches.

    Falls back to the provided client when cloning is not possible (e.g., tests).
    """

    if not redis_client:
        return None

    cache_db = int(os.getenv(STRATEGY_CACHE_DB_ENV, str(DEFAULT_STRATEGY_CACHE_DB)))

    try:
        import redis.asyncio as redis_asyncio

        if isinstance(redis_client, redis_asyncio.Redis):
            conn_kwargs = dict(redis_client.connection_pool.connection_kwargs)
            conn_kwargs["db"] = cache_db
            return redis_asyncio.Redis(**conn_kwargs)
    except Exception:  # pragma: no cover - defensive; falls back to provided client
        logger.debug("strategy_cache_client_fallback", exc_info=True)

    return redis_client


@asynccontextmanager
async def _conn(db_pool: Any) -> AsyncIterator[Any]:
    if hasattr(db_pool, "acquire"):
        candidate = db_pool.acquire()
        if hasattr(candidate, "__aenter__"):
            async with candidate as conn:
                yield conn
        else:
            conn = await candidate if inspect.isawaitable(candidate) else candidate
            try:
                yield conn
            finally:
                releaser = getattr(db_pool, "release", None)
                if releaser:
                    maybe = releaser(conn)
                    if inspect.isawaitable(maybe):
                        await maybe
        return
    if hasattr(db_pool, "connection"):
        candidate = db_pool.connection()
        conn = await candidate if inspect.isawaitable(candidate) else candidate
        async with conn:
            yield conn
        return
    raise RuntimeError("Unsupported db_pool interface")


class StrategyScopedDataAccess:
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 1000
    CACHE_TTL_SECONDS = 300

    def __init__(self, db_pool: Any, redis_client: Any, user: dict[str, Any]):
        self.db_pool = db_pool
        self.redis = _build_cache_client(redis_client)
        self.user = user
        self.user_id = user.get("user_id") or user.get("sub")
        self.authorized_strategies = get_authorized_strategies(user)
        strategy_hash = hashlib.sha256(
            ",".join(sorted(self.authorized_strategies)).encode()
        ).hexdigest()[:12]
        session_version = user.get("session_version", 1)
        self._cache_prefix = f"scoped_data:{self.user_id}:{strategy_hash}:v{session_version}"

        # Initialize encryption for cache data
        encryption_key = _get_cache_encryption_key()
        self._cipher = AESGCM(encryption_key) if encryption_key else None
        if not self._cipher:
            logger.info("strategy_cache_encryption_disabled", extra={
                "reason": "STRATEGY_CACHE_ENCRYPTION_KEY not configured",
                "cache_enabled": self.redis is not None,
            })

    def _encrypt_cache_data(self, data: str) -> str:
        """Encrypt cache data with AES-256-GCM."""
        if not self._cipher:
            return data
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, data.encode(), None)
        return base64.b64encode(nonce + ciphertext).decode()

    def _decrypt_cache_data(self, encrypted: str) -> str:
        """Decrypt cache data with AES-256-GCM."""
        if not self._cipher:
            return encrypted
        try:
            blob = base64.b64decode(encrypted)
            nonce = blob[:12]
            ciphertext = blob[12:]
            return self._cipher.decrypt(nonce, ciphertext, None).decode()
        except Exception:
            logger.debug("cache_decrypt_failed", exc_info=True)
            raise

    @staticmethod
    def _build_filter_clauses(
        filters: dict[str, Any], allowed: dict[str, str], start_index: int
    ) -> tuple[list[str], list[Any], int]:
        """Translate allowed filters into SQL clauses and params.

        Unknown filters are ignored to preserve backward compatibility.
        Lists/sets are treated as ANY() equality checks.
        Returns (clauses, params, last_index).
        """

        clauses: list[str] = []
        params: list[Any] = []
        idx = start_index

        for key, column in allowed.items():
            if key not in filters:
                continue

            value = filters[key]
            if value is None:
                continue

            idx += 1
            if isinstance(value, list | tuple | set):
                clauses.append(f"{column} = ANY(${idx})")
                params.append(list(value))
            else:
                clauses.append(f"{column} = ${idx}")
                params.append(value)

        return clauses, params, idx

    @staticmethod
    def _filters_cache_token(filters: dict[str, Any], allowed: dict[str, str]) -> str:
        if not filters:
            return ""

        normalized: dict[str, Any] = {}
        for key in sorted(allowed):
            if key in filters and filters[key] is not None:
                value = filters[key]
                if isinstance(value, set | tuple):
                    value = sorted(value)
                normalized[key] = value

        if not normalized:
            return ""

        digest_input = json.dumps(normalized, sort_keys=True, default=str)
        digest = hashlib.sha256(digest_input.encode()).hexdigest()[:12]
        return f":{digest}"

    def _get_strategy_filter(self) -> list[str]:
        if not self.authorized_strategies:
            raise PermissionError("No strategy access")
        return self.authorized_strategies

    async def _get_cached(self, key: str) -> list[dict[str, Any]] | None:
        if not self.redis:
            return None
        # Skip cache if encryption not configured (security requirement)
        if not self._cipher:
            return None
        try:
            data = await self.redis.get(key)
            if data:
                decrypted = self._decrypt_cache_data(data)
                return cast(list[dict[str, Any]], json.loads(decrypted))
        except Exception:  # pragma: no cover
            logger.debug("cache_read_failed", exc_info=True)
        return None

    async def _set_cached(self, key: str, value: list[dict[str, Any]]) -> None:
        if not self.redis:
            return
        # Skip cache if encryption not configured (security requirement)
        if not self._cipher:
            return
        try:
            json_data = json.dumps(value)
            encrypted = self._encrypt_cache_data(json_data)
            await self.redis.setex(key, self.CACHE_TTL_SECONDS, encrypted)
        except Exception:  # pragma: no cover
            logger.debug("cache_write_failed", exc_info=True)

    def _limit(self, value: int | None) -> int:
        if value is None:
            return self.DEFAULT_LIMIT
        # Clamp to valid range [1, MAX_LIMIT] to prevent DoS via 0/negative limits
        return max(1, min(int(value), self.MAX_LIMIT))

    async def get_positions(
        self, limit: int = DEFAULT_LIMIT, offset: int = 0, use_cache: bool = True, **filters: Any
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        allowed_filters = {"symbol": "symbol"}
        filter_token = self._filters_cache_token(filters, allowed_filters)
        cache_key = f"{self._cache_prefix}:positions:{offset}:{limit}{filter_token}"
        if use_cache:
            cached = await self._get_cached(cache_key)
            if cached is not None:
                return cached

        strategies = self._get_strategy_filter()
        clauses, params, last_idx = self._build_filter_clauses(filters, allowed_filters, start_index=1)
        params = [strategies, *params, limit, offset]
        limit_idx = last_idx + 1
        offset_idx = last_idx + 2
        async with _conn(self.db_pool) as conn:
            query = f"""
                SELECT * FROM positions
                WHERE strategy_id = ANY($1)
                {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
                ORDER BY updated_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}
            """
            rows = await conn.fetch(query, *params)
            data = [dict(row) for row in rows]

        await self._set_cached(cache_key, data)
        return data

    async def get_orders(
        self, limit: int = DEFAULT_LIMIT, offset: int = 0, **filters: Any
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side", "status": "status"}
        clauses, params, last_idx = self._build_filter_clauses(filters, allowed_filters, start_index=1)
        params = [strategies, *params, limit, offset]
        limit_idx = last_idx + 1
        offset_idx = last_idx + 2
        async with _conn(self.db_pool) as conn:
            query = f"""
                SELECT * FROM orders
                WHERE strategy_id = ANY($1)
                {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
                ORDER BY created_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}
            """
            rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]

    async def get_pnl_summary(
        self,
        date_from: Any,
        date_to: Any,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        strategies = self._get_strategy_filter()
        async with _conn(self.db_pool) as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM pnl_daily
                WHERE strategy_id = ANY($1)
                  AND trade_date BETWEEN $2 AND $3
                ORDER BY trade_date DESC
                LIMIT $4 OFFSET $5
                """,
                strategies,
                date_from,
                date_to,
                limit,
                offset,
            )
        return [dict(row) for row in rows]

    async def get_trades(
        self, limit: int = DEFAULT_LIMIT, offset: int = 0, **filters: Any
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side"}
        clauses, params, last_idx = self._build_filter_clauses(filters, allowed_filters, start_index=1)
        params = [strategies, *params, limit, offset]
        limit_idx = last_idx + 1
        offset_idx = last_idx + 2
        async with _conn(self.db_pool) as conn:
            query = f"""
                SELECT * FROM trades
                WHERE strategy_id = ANY($1)
                {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
                ORDER BY executed_at DESC
                LIMIT ${limit_idx} OFFSET ${offset_idx}
            """
            rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]

    async def stream_trades_for_export(self, **filters: Any) -> AsyncGenerator[dict[str, Any], None]:
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side"}
        clauses, params, _ = self._build_filter_clauses(filters, allowed_filters, start_index=1)
        params = [strategies, *params]
        async with _conn(self.db_pool) as conn:
            async with conn.transaction():
                query = f"""
                    SELECT * FROM trades
                    WHERE strategy_id = ANY($1)
                    {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
                    ORDER BY executed_at DESC
                """
                async for row in conn.cursor(query, *params):
                    yield dict(row)


def get_scoped_data_access(db_pool: Any, redis_client: Any, user: dict[str, Any]) -> StrategyScopedDataAccess:
    return StrategyScopedDataAccess(db_pool, redis_client, user)


__all__ = ["StrategyScopedDataAccess", "get_scoped_data_access"]

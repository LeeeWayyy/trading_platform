"""Strategy-scoped data access with server-side filtering and caching."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row

from libs.core.common.db import acquire_connection
from libs.platform.web_console_auth.permissions import get_authorized_strategies

logger = logging.getLogger(__name__)

STRATEGY_CACHE_DB_ENV = "REDIS_STRATEGY_CACHE_DB"
DEFAULT_STRATEGY_CACHE_DB = 3
STRATEGY_CACHE_KEY_ENV = "STRATEGY_CACHE_ENCRYPTION_KEY"
BREAK_EVEN_EPSILON = Decimal("0.01")


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
    except (ValueError, TypeError) as e:
        # JUSTIFIED: Fail-open defensive handler for invalid base64 or type mismatches
        # during cache key decoding. Cache will be disabled if key invalid (security-first).
        # ValueError: Invalid base64 encoding
        # TypeError: Non-string input to base64.b64decode
        logger.warning(
            "strategy_cache_key_decode_failed",
            extra={"error": str(e), "key_type": type(key_b64).__name__},
            exc_info=True,
        )
        return None


def _build_cache_client(redis_client: Any) -> Any:
    """Return Redis client backed by an isolated DB for strategy caches.

    If cloning fails or the client type is unsupported, caching is disabled to
    avoid mixing strategy data with session storage.
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
        logger.warning(
            "strategy_cache_disabled_incompatible_client",
            extra={"client_type": type(redis_client).__name__},
        )
    except (ImportError, AttributeError, TypeError) as e:  # pragma: no cover - defensive
        # JUSTIFIED: Defensive handler for Redis client cloning failures
        # ImportError: redis.asyncio not installed
        # AttributeError: connection_pool not available on client
        # TypeError: connection_kwargs incompatible with Redis constructor
        logger.warning(
            "strategy_cache_client_fallback_disabled",
            extra={"error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )

    # If the client is a lightweight fake without connection metadata, we assume
    # it's already isolated (e.g., unit tests) and allow caching to proceed.
    if not hasattr(redis_client, "connection_pool"):
        return redis_client

    return None


def _date_to_utc_datetime(d: date) -> datetime:
    """Convert date to UTC-aware datetime for timestamptz comparisons."""

    return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=UTC)


class StrategyScopedDataAccess:
    DEFAULT_LIMIT = 100
    MAX_LIMIT = 5000  # Raised from 1000 to support multi-year comparisons
    CACHE_TTL_SECONDS = 300

    def __init__(self, db_pool: Any, redis_client: Any, user: dict[str, Any]):
        self.db_pool = db_pool
        self.redis = _build_cache_client(redis_client)
        self.user = user
        self.user_id = user.get("user_id") or user.get("sub")

        # Primary source of truth: RBAC helper.
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
            logger.info(
                "strategy_cache_encryption_disabled",
                extra={
                    "reason": "STRATEGY_CACHE_ENCRYPTION_KEY not configured",
                    "cache_enabled": self.redis is not None,
                },
            )

    def _to_decimal_or_none(self, value: Any) -> Decimal | None:
        """Convert value to Decimal when present, otherwise return None."""

        if value is None:
            return None
        return Decimal(str(value))

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
        except (ValueError, TypeError) as e:
            # Crypto errors: Invalid base64, wrong key, corrupted ciphertext, decoding failure
            # ValueError: Invalid base64 or decryption failure (wrong key/corrupted data)
            # TypeError: Type mismatch in crypto operations
            logger.debug(
                "cache_decrypt_failed",
                extra={"error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            raise

    @staticmethod
    def _build_filter_clauses(
        filters: dict[str, Any], allowed: dict[str, str]
    ) -> tuple[list[str], list[Any]]:
        """Translate allowed filters into SQL clauses and params.

        Unknown filters are ignored to preserve backward compatibility.
        Lists/sets are treated as ANY() equality checks.
        Returns (clauses, params).
        """

        clauses: list[str] = []
        params: list[Any] = []

        for key, column in allowed.items():
            if key not in filters:
                continue

            value = filters[key]
            if value is None:
                continue

            if isinstance(value, (list, tuple, set)):  # noqa: UP038
                clauses.append(f"{column} = ANY(%s)")
                params.append(list(value))
            else:
                clauses.append(f"{column} = %s")
                params.append(value)

        return clauses, params

    @staticmethod
    def _filters_cache_token(filters: dict[str, Any], allowed: dict[str, str]) -> str:
        if not filters:
            return ""

        normalized: dict[str, Any] = {}
        for key in sorted(allowed):
            if key in filters and filters[key] is not None:
                value = filters[key]
                if isinstance(value, (set, tuple)):  # noqa: UP038
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
        except (ValueError, TypeError, json.JSONDecodeError) as e:  # pragma: no cover
            # JUSTIFIED: Fail-open cache read for corrupted/invalid cache entries
            # ValueError: Decryption failed (wrong key/corrupted ciphertext)
            # TypeError: Type mismatch in Redis get or crypto operations
            # json.JSONDecodeError: Corrupted JSON after decryption
            logger.debug(
                "cache_read_failed",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "cache_key": key,
                },
                exc_info=True,
            )
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
        except (ValueError, TypeError, OSError) as e:  # pragma: no cover
            # JUSTIFIED: Fail-open cache write for transient Redis errors
            # ValueError: JSON serialization or encryption error
            # TypeError: Type mismatch in setex arguments
            # OSError: Redis connection errors (ConnectionError inherits from OSError)
            logger.debug(
                "cache_write_failed",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "cache_key": key,
                },
                exc_info=True,
            )

    def _limit(self, value: int | None) -> int:
        if value is None:
            return self.DEFAULT_LIMIT
        # Clamp to valid range [1, MAX_LIMIT] to prevent DoS via 0/negative limits
        return max(1, min(int(value), self.MAX_LIMIT))

    @staticmethod
    async def _execute_fetchall(conn: Any, query: str, params: tuple[Any, ...]) -> list[Any]:
        """Execute query and return rows using psycopg-style interfaces."""
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
        return cast(list[Any], rows)

    @staticmethod
    def _add_date_filters(
        clauses: list[str],
        params: list[Any],
        date_from: date | None,
        date_to: date | None,
    ) -> None:
        """Append executed_at date filters to clauses/params in-place."""
        if date_from:
            clauses.append("executed_at >= %s")
            params.append(_date_to_utc_datetime(date_from))
        if date_to:
            clauses.append("executed_at < %s")
            params.append(_date_to_utc_datetime(date_to))

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
        clauses, params = self._build_filter_clauses(filters, allowed_filters)
        query = f"""
            SELECT * FROM positions
            WHERE strategy_id = ANY(%s)
            {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY updated_at DESC
            LIMIT %s OFFSET %s
        """
        exec_params = [strategies, *params, limit, offset]
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, tuple(exec_params))
            data = [dict(row) for row in rows]

        await self._set_cached(cache_key, data)
        return data

    async def get_orders(
        self, limit: int = DEFAULT_LIMIT, offset: int = 0, **filters: Any
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side", "status": "status"}
        clauses, params = self._build_filter_clauses(filters, allowed_filters)
        query = f"""
            SELECT * FROM orders
            WHERE strategy_id = ANY(%s)
            {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """
        exec_params = [strategies, *params, limit, offset]
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, tuple(exec_params))
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
        query = """
            SELECT * FROM pnl_daily
            WHERE strategy_id = ANY(%s)
              AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date DESC
            LIMIT %s OFFSET %s
        """
        params = (strategies, date_from, date_to, limit, offset)
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, params)
        return [dict(row) for row in rows]

    async def get_trades(
        self,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        date_from: date | None = None,
        date_to: date | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        limit = self._limit(limit)
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side"}
        clauses, params = self._build_filter_clauses(filters, allowed_filters)

        self._add_date_filters(clauses, params, date_from, date_to)

        query = f"""
            SELECT * FROM trades
            WHERE strategy_id = ANY(%s)
              AND COALESCE(superseded, FALSE) = FALSE
            {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY executed_at DESC
            LIMIT %s OFFSET %s
        """
        exec_params = [strategies, *params, limit, offset]
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, tuple(exec_params))
        return [dict(row) for row in rows]

    async def get_trade_stats(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        **filters: Any,
    ) -> dict[str, Any]:
        """Aggregate trade statistics using SQL for accuracy."""

        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side"}
        clauses, params = self._build_filter_clauses(filters, allowed_filters)

        self._add_date_filters(clauses, params, date_from, date_to)

        # Trade counts use BREAK_EVEN_EPSILON to ignore micro-pnl noise when
        # classifying wins/losses/break-evens and computing avg_win/avg_loss.
        # Gross profit/loss stay strict (> 0 / < 0) so financial totals
        # reconcile exactly: total_realized_pnl = gross_profit - gross_loss.
        # This hybrid keeps profit_factor accurate while filtering tiny fills
        # from the win-rate calculation.
        query = f"""
            SELECT
                COUNT(*) AS total_trades,
                COUNT(*) FILTER (WHERE realized_pnl > {BREAK_EVEN_EPSILON}) AS winning_trades,
                COUNT(*) FILTER (WHERE realized_pnl < -{BREAK_EVEN_EPSILON}) AS losing_trades,
                COUNT(*) FILTER (WHERE realized_pnl BETWEEN -{BREAK_EVEN_EPSILON} AND {BREAK_EVEN_EPSILON}) AS break_even_trades,
                COALESCE(SUM(realized_pnl), 0) AS total_realized_pnl,
                COALESCE(SUM(realized_pnl) FILTER (WHERE realized_pnl > 0), 0) AS gross_profit,
                COALESCE(ABS(SUM(realized_pnl) FILTER (WHERE realized_pnl < 0)), 0) AS gross_loss,
                AVG(realized_pnl) FILTER (WHERE realized_pnl > {BREAK_EVEN_EPSILON}) AS avg_win,
                AVG(realized_pnl) FILTER (WHERE realized_pnl < -{BREAK_EVEN_EPSILON}) AS avg_loss,
                MAX(realized_pnl) AS largest_win,
                MIN(realized_pnl) AS largest_loss
            FROM trades
            WHERE strategy_id = ANY(%s)
              AND COALESCE(superseded, FALSE) = FALSE
            {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
        """
        exec_params = [strategies, *params]
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, tuple(exec_params))
            # Convert Row to dict to support .get() access (psycopg3 Row doesn't have .get)
            row: dict[str, Any] = dict(rows[0]) if rows else {}

        return {
            "total_trades": int(row.get("total_trades", 0)),
            "winning_trades": int(row.get("winning_trades", 0)),
            "losing_trades": int(row.get("losing_trades", 0)),
            "break_even_trades": int(row.get("break_even_trades", 0)),
            "total_realized_pnl": Decimal(str(row.get("total_realized_pnl", 0))),
            "gross_profit": Decimal(str(row.get("gross_profit", 0))),
            "gross_loss": Decimal(str(row.get("gross_loss", 0))),
            "avg_win": self._to_decimal_or_none(row.get("avg_win")),
            "avg_loss": self._to_decimal_or_none(row.get("avg_loss")),
            "largest_win": self._to_decimal_or_none(row.get("largest_win")),
            "largest_loss": self._to_decimal_or_none(row.get("largest_loss")),
        }

    async def stream_trades_for_export(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        **filters: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        strategies = self._get_strategy_filter()
        allowed_filters = {"symbol": "symbol", "side": "side"}
        clauses, params = self._build_filter_clauses(filters, allowed_filters)

        self._add_date_filters(clauses, params, date_from, date_to)

        query = f"""
            SELECT * FROM trades
            WHERE strategy_id = ANY(%s)
              AND COALESCE(superseded, FALSE) = FALSE
            {(' AND ' + ' AND '.join(clauses)) if clauses else ''}
            ORDER BY executed_at DESC
        """
        exec_params = [strategies, *params]
        async with acquire_connection(self.db_pool) as conn:
            async with conn.transaction():
                cursor = await conn.execute(query, tuple(exec_params))
                async for row in cursor:
                    yield dict(row)

    # P6T10: Attribution and Quantile Analysis
    async def get_portfolio_returns(
        self,
        strategy_id: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Get portfolio returns for attribution analysis.

        Returns list of {date, daily_return} dicts.
        Computes returns from pnl_daily using daily_pnl / (nav - daily_pnl).
        """
        # Verify strategy authorization
        if strategy_id not in self.authorized_strategies:
            raise PermissionError(f"Not authorized for strategy: {strategy_id}")

        # Query daily P&L and NAV to compute returns
        # daily_return = daily_pnl / (nav - daily_pnl) where (nav - daily_pnl) is prior NAV
        query = """
            SELECT
                trade_date as date,
                CASE
                    WHEN (nav - daily_pnl) > 0 THEN daily_pnl / (nav - daily_pnl)
                    ELSE 0.0
                END as daily_return
            FROM pnl_daily
            WHERE strategy_id = %s
              AND trade_date BETWEEN %s AND %s
            ORDER BY trade_date ASC
        """
        params = (strategy_id, start_date, end_date)
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, params)
        return [dict(row) for row in rows]

    async def verify_job_ownership(self, job_id: str) -> None:
        """Verify the current user owns the specified backtest job.

        Raises PermissionError if user does not own the job.
        Checks that the job's strategy_id is in user's authorized strategies.
        """
        query = """
            SELECT strategy_id FROM backtest_jobs WHERE job_id = %s
        """
        async with acquire_connection(self.db_pool) as conn:
            rows = await self._execute_fetchall(conn, query, (job_id,))

        if not rows:
            raise PermissionError(f"Backtest job not found: {job_id}")

        job_strategy = rows[0].get("strategy_id")
        if job_strategy not in self.authorized_strategies:
            raise PermissionError(f"Not authorized for backtest job: {job_id}")


def get_scoped_data_access(
    db_pool: Any, redis_client: Any, user: dict[str, Any]
) -> StrategyScopedDataAccess:
    return StrategyScopedDataAccess(db_pool, redis_client, user)


__all__ = ["StrategyScopedDataAccess", "get_scoped_data_access"]

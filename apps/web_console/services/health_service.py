"""Health monitor service for web console.

Aggregates service health, infrastructure connectivity, and latency metrics
for the System Health Monitor page. Queue depth is deferred to C2.1.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import redis
from pydantic import BaseModel, ConfigDict

from libs.health.health_client import HealthClient, ServiceHealthResponse
from libs.health.prometheus_client import LatencyMetrics, PrometheusClient
from libs.redis_client import RedisClient

logger = logging.getLogger(__name__)


class ConnectivityStatus(BaseModel):
    """Connectivity status for infrastructure components."""

    model_config = ConfigDict(extra="allow")  # Forward compatibility

    redis_connected: bool
    redis_info: dict[str, Any] | None
    redis_error: str | None = None
    postgres_connected: bool
    postgres_latency_ms: float | None
    postgres_error: str | None = None
    checked_at: datetime
    # Staleness tracking
    is_stale: bool = False
    stale_age_seconds: float | None = None


class HealthMonitorService:
    """Service for aggregating health data from all sources."""

    def __init__(
        self,
        health_client: HealthClient,
        prometheus_client: PrometheusClient,
        redis_client: RedisClient | None,
        db_pool: Any = None,
        connectivity_cache_ttl_seconds: int = 30,
    ) -> None:
        self.health = health_client
        self.prometheus = prometheus_client
        self.redis = redis_client
        self.db_pool = db_pool
        self._connectivity_cache: tuple[ConnectivityStatus, datetime] | None = None
        self._connectivity_cache_ttl = timedelta(seconds=connectivity_cache_ttl_seconds)

    def _get_stale_connectivity_or_none(self, now: datetime) -> ConnectivityStatus | None:
        """Return stale cached connectivity status if within 2x TTL, else None.

        Used for graceful degradation when connectivity checks fail.
        """
        if not self._connectivity_cache:
            return None
        cached, cached_at = self._connectivity_cache
        stale_age = (now - cached_at).total_seconds()
        if stale_age < self._connectivity_cache_ttl.total_seconds() * 2:
            return cached.model_copy(update={"is_stale": True, "stale_age_seconds": stale_age})
        return None

    async def get_all_services_status(self) -> dict[str, ServiceHealthResponse]:
        """Get health status for all services."""

        return await self.health.check_all()

    async def get_connectivity(self) -> ConnectivityStatus:
        """Check Redis and Postgres connectivity with caching and staleness."""

        now = datetime.now(UTC)

        # Return fresh cached result if available (avoid redundant checks)
        if self._connectivity_cache:
            cached, cached_at = self._connectivity_cache
            cache_age = now - cached_at
            if cache_age < self._connectivity_cache_ttl:
                return cached

        def _redact_redis_info(info: dict[str, Any]) -> dict[str, Any]:
            """Redact sensitive fields from Redis INFO output.

            Removes auth credentials, config paths, and topology/replication
            details to avoid leaking infrastructure details to console viewers.
            """
            sensitive_fields = {
                # Auth/config
                "requirepass",
                "masterauth",
                "client_info",
                "config_file",
                "aclfile",
                "logfile",
                "pidfile",
                # Replication/topology (avoid leaking infra details)
                "role",
                "connected_slaves",
                "master_replid",
                "master_replid2",
                "master_repl_offset",
                "second_repl_offset",
                "repl_backlog_active",
                "repl_backlog_size",
            }
            # Filter by prefixes to catch dynamic fields like slave<N>, master_*, cluster_*
            sensitive_prefixes = ("slave", "master_", "cluster_")
            return {
                k: v
                for k, v in info.items()
                if k.lower() not in sensitive_fields
                and not any(k.lower().startswith(p) for p in sensitive_prefixes)
            }

        def _check_redis() -> tuple[bool, dict[str, Any] | None, str | None]:
            if self.redis is None:
                return False, None, "Redis client unavailable"
            try:
                connected = self.redis.health_check()
                info = self.redis.get_info() if connected else None
                if info:
                    info = _redact_redis_info(info)
                return connected, info, None
            except (redis.RedisError, ConnectionError, TimeoutError) as exc:
                return False, None, str(exc)

        async def _check_postgres_async() -> tuple[bool, float | None, str | None]:
            """Async version of postgres health check for AsyncConnectionAdapter."""
            if not self.db_pool:
                return False, None, "No database pool configured"
            start = datetime.now(UTC)
            try:
                async with self.db_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute("SELECT 1")
                        await cur.fetchone()
                latency_ms = (datetime.now(UTC) - start).total_seconds() * 1000
                return True, latency_ms, None
            except Exception as exc:
                logger.warning("Postgres health check failed: %s", exc)
                return False, None, str(exc)

        def _check_postgres() -> tuple[bool, float | None, str | None]:
            """Sync wrapper that runs the async check in a new event loop."""
            if not self.db_pool:
                return False, None, "No database pool configured"
            try:
                import asyncio
                # Create a new event loop for this thread
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(_check_postgres_async())
                finally:
                    loop.close()
            except Exception as exc:
                # Broad exception handling to catch all DB errors (psycopg, pg8000, etc.)
                # and let the stale-cache path execute instead of blanking the panel
                logger.warning("Postgres health check failed: %s", exc)
                return False, None, str(exc)

        try:
            # Use get_running_loop() (not deprecated get_event_loop())
            # Use default executor (None) to avoid ThreadPool churn
            loop = asyncio.get_running_loop()
            redis_future = loop.run_in_executor(None, _check_redis)
            postgres_future = loop.run_in_executor(None, _check_postgres)

            # Use gather with return_exceptions to handle failures independently
            # This ensures one service's failure doesn't mask the other's status
            results = await asyncio.gather(
                asyncio.wait_for(redis_future, timeout=5.0),
                asyncio.wait_for(postgres_future, timeout=5.0),
                return_exceptions=True,
            )
            redis_res, pg_res = results

            # Check if both checks failed - use stale cache if available
            both_failed = isinstance(redis_res, Exception) and isinstance(pg_res, Exception)
            if both_failed:
                logger.warning(
                    "Both connectivity checks failed. Redis: %s, PG: %s",
                    redis_res,
                    pg_res,
                )
                stale_result = self._get_stale_connectivity_or_none(now)
                if stale_result:
                    return stale_result

            # Extract Redis result (may be exception or tuple)
            if isinstance(redis_res, Exception):
                redis_connected, redis_info, redis_error = False, None, str(redis_res)
            else:
                redis_connected, redis_info, redis_error = redis_res

            # Extract Postgres result (may be exception or tuple)
            if isinstance(pg_res, Exception):
                postgres_connected, postgres_latency_ms, postgres_error = (
                    False,
                    None,
                    str(pg_res),
                )
            else:
                postgres_connected, postgres_latency_ms, postgres_error = pg_res

            result = ConnectivityStatus(
                redis_connected=redis_connected,
                redis_info=redis_info,
                redis_error=redis_error,
                postgres_connected=postgres_connected,
                postgres_latency_ms=postgres_latency_ms,
                postgres_error=postgres_error,
                checked_at=now,
            )
            self._connectivity_cache = (result, now)
            return result

        except Exception as exc:
            # Safeguard for unexpected errors (should rarely be reached with gather)
            logger.warning("Connectivity check failed unexpectedly: %s", exc)

            stale_result = self._get_stale_connectivity_or_none(now)
            if stale_result:
                return stale_result

            return ConnectivityStatus(
                redis_connected=False,
                redis_info=None,
                redis_error=str(exc),
                postgres_connected=False,
                postgres_latency_ms=None,
                postgres_error=str(exc),
                checked_at=now,
            )

    async def get_latency_metrics(self) -> tuple[dict[str, LatencyMetrics], bool, float | None]:
        """Get latency metrics from Prometheus with staleness tracking."""

        return await self.prometheus.get_service_latencies()

    async def close(self) -> None:
        """Close all HTTP clients to release resources.

        Should be called after each fetch cycle to prevent event loop
        lifecycle issues with Streamlit's asyncio.run() pattern.
        """
        await self.health.close()
        await self.prometheus.close()


__all__ = ["ConnectivityStatus", "HealthMonitorService"]

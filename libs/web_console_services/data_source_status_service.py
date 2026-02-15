"""Service layer for data source health/status visibility.

This service currently returns mock data for UI integration and permission validation.
It is designed to be upgraded to real provider health checks in a future task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import redis.asyncio as redis_asyncio
import redis.exceptions

from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)

from .schemas.data_management import DataSourceStatusDTO

logger = logging.getLogger(__name__)

_REFRESH_LOCK_TTL_SECONDS = 60
_REFRESH_TIMEOUT_SECONDS = 45
_REFRESH_LOCK_KEY_PREFIX = "data_source_status:refresh"
_VALID_DATA_MODES: tuple[Literal["mock", "real"], ...] = ("mock", "real")

_DATA_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "name": "crsp",
        "display_name": "CRSP Daily",
        "provider_type": "academic",
        "is_production_ready": True,
        "dataset_key": "crsp",
        "tables": ["crsp_daily", "crsp_monthly"],
        "status": "ok",
        "minutes_ago": 5,
        "row_count": 52_103_487,
        "error_rate_pct": 0.1,
        "error_message": None,
    },
    {
        "name": "yfinance",
        "display_name": "Yahoo Finance",
        "provider_type": "free",
        "is_production_ready": False,
        "dataset_key": None,
        "tables": ["yfinance"],
        "status": "stale",
        "minutes_ago": 47,
        "row_count": 2_412_008,
        "error_rate_pct": 2.6,
        "error_message": None,
    },
    {
        "name": "compustat",
        "display_name": "Compustat",
        "provider_type": "academic",
        "is_production_ready": True,
        "dataset_key": "compustat",
        "tables": ["compustat_annual", "compustat_quarterly"],
        "status": "ok",
        "minutes_ago": 12,
        "row_count": 9_104_223,
        "error_rate_pct": 0.0,
        "error_message": None,
    },
    {
        "name": "fama_french",
        "display_name": "Fama-French Factors",
        "provider_type": "academic",
        "is_production_ready": True,
        "dataset_key": "fama_french",
        "tables": ["ff_factors_daily", "ff_factors_monthly"],
        "status": "ok",
        "minutes_ago": 8,
        "row_count": 143_220,
        "error_rate_pct": 0.0,
        "error_message": None,
    },
    {
        "name": "taq",
        "display_name": "TAQ (Trade & Quote)",
        "provider_type": "commercial",
        "is_production_ready": True,
        "dataset_key": "taq",
        "tables": ["taq_trades", "taq_quotes"],
        "status": "error",
        "minutes_ago": 96,
        "row_count": 402_800_112,
        "error_rate_pct": 6.8,
        "error_message": "Provider timeout on incremental ingest",
    },
)

_KNOWN_SOURCES = frozenset(source["name"] for source in _DATA_SOURCES)
_COMPARE_AND_DELETE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] "
    "then return redis.call('del', KEYS[1]) else return 0 end"
)


class DataSourceStatusService:
    """Service for source-level status and manual refresh actions."""

    def __init__(
        self,
        redis_client_factory: Callable[[], Awaitable[redis_asyncio.Redis]] | None = None,
        data_mode: Literal["mock", "real"] = "mock",
    ) -> None:
        if data_mode not in _VALID_DATA_MODES:
            raise ValueError(f"Invalid data_mode: {data_mode}")
        self._redis_client_factory = redis_client_factory
        self._data_mode: Literal["mock", "real"] = data_mode
        self._last_refresh_results: dict[str, DataSourceStatusDTO] = {}

    async def get_all_sources(self, user: Any) -> list[DataSourceStatusDTO]:
        """Return visible data sources for the current user."""
        if not has_permission(user, Permission.VIEW_DATA_SYNC):
            raise PermissionError("Permission 'view_data_sync' required")

        all_sources = self._get_mock_sources()
        filtered = [
            source
            for source in all_sources
            if source.dataset_key is None or has_dataset_permission(user, source.dataset_key)
        ]
        # Merge refreshed sources so callers see updated timestamps after manual refresh
        merged = [
            self._last_refresh_results.get(source.name, source)
            for source in filtered
        ]
        for source in merged:
            self._last_refresh_results.setdefault(source.name, source)
        return merged

    async def refresh_source(self, user: Any, source_name: str) -> DataSourceStatusDTO:
        """Trigger manual source refresh with per-source lock semantics."""
        if not has_permission(user, Permission.TRIGGER_DATA_SYNC):
            raise PermissionError("Permission 'trigger_data_sync' required")

        try:
            _validate_source_name(source_name)
            source = self._get_source_by_name(source_name)
        except (ValueError, KeyError, LookupError):
            raise PermissionError("Source not available") from None

        if source.dataset_key is not None and not has_dataset_permission(user, source.dataset_key):
            raise PermissionError("Source not available")

        self._last_refresh_results.setdefault(source.name, source)

        if self._redis_client_factory is None:
            if self._data_mode == "real":
                raise RuntimeError(
                    "Redis client factory required for refresh in real data mode"
                )
            logger.warning(
                "redis_lock_disabled_no_factory",
                extra={"source_name": source.name, "data_mode": self._data_mode},
            )
            return await asyncio.wait_for(
                self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS
            )

        lock_key = f"{_REFRESH_LOCK_KEY_PREFIX}:{source.name}"
        lock_token = str(uuid4())

        try:
            redis_client = await self._redis_client_factory()
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            return await self._handle_runtime_redis_fallback(source, exc)

        acquired: bool
        try:
            acquired = bool(
                await redis_client.set(lock_key, lock_token, ex=_REFRESH_LOCK_TTL_SECONDS, nx=True)
            )
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            return await self._handle_acquire_failure(source, exc)

        if not acquired:
            return self._last_refresh_results.get(source.name, source)

        refreshed: DataSourceStatusDTO | None = None
        try:
            refreshed = await asyncio.wait_for(
                self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS
            )
            self._last_refresh_results[source.name] = refreshed
            return refreshed
        finally:
            await self._release_refresh_lock(redis_client, lock_key, lock_token)

    @staticmethod
    async def _release_refresh_lock(
        redis_client: redis_asyncio.Redis,
        lock_key: str,
        lock_token: str,
    ) -> None:
        try:
            released = await redis_client.eval(  # type: ignore[misc]
                _COMPARE_AND_DELETE_LUA, 1, lock_key, lock_token,
            )
            if int(released) == 0:
                logger.warning("redis_lock_release_noop", extra={"lock_key": lock_key})
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            logger.warning(
                "redis_lock_release_failed",
                extra={"lock_key": lock_key, "error": str(exc)},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "redis_lock_release_unexpected_error",
                extra={"lock_key": lock_key, "error": str(exc)},
            )

    async def _handle_acquire_failure(
        self,
        source: DataSourceStatusDTO,
        exc: BaseException,
    ) -> DataSourceStatusDTO:
        if self._data_mode == "real":
            raise RuntimeError("Redis required for real-data refresh") from exc
        logger.warning(
            "redis_lock_acquire_failed_fallback",
            extra={"source_name": source.name, "error": str(exc)},
        )
        return await asyncio.wait_for(self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS)

    async def _handle_runtime_redis_fallback(
        self,
        source: DataSourceStatusDTO,
        exc: BaseException,
    ) -> DataSourceStatusDTO:
        if self._data_mode == "real":
            raise RuntimeError("Redis required for real-data refresh") from exc
        logger.warning(
            "redis_lock_fallback_runtime_error",
            extra={"source_name": source.name, "error": str(exc)},
        )
        return await asyncio.wait_for(self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS)

    async def _perform_refresh(self, source: DataSourceStatusDTO) -> DataSourceStatusDTO:
        """Mock refresh implementation (deterministic, idempotent)."""
        await asyncio.sleep(0)
        now = datetime.now(UTC)
        refreshed = source.model_copy(deep=True)
        refreshed.last_update = now
        refreshed.age_seconds = 0.0
        if refreshed.status == "unknown":
            refreshed.status = "ok"
        self._last_refresh_results[source.name] = refreshed
        return refreshed

    def _get_source_by_name(self, source_name: str) -> DataSourceStatusDTO:
        normalized = source_name.strip().lower()
        if not normalized:
            raise ValueError("Unknown data source")
        all_sources = {source.name: source for source in self._get_mock_sources()}
        source = all_sources.get(normalized)
        if source is None:
            raise LookupError(f"Unknown data source: {source_name}")
        return source

    def _get_mock_sources(self) -> list[DataSourceStatusDTO]:
        now = datetime.now(UTC)
        sources: list[DataSourceStatusDTO] = []
        for spec in _DATA_SOURCES:
            minutes_ago = int(spec["minutes_ago"])
            last_update: datetime | None = now - timedelta(minutes=minutes_ago)
            age_seconds: float | None = float(minutes_ago * 60)
            if spec["status"] == "unknown":
                last_update = None
                age_seconds = None
            sources.append(
                DataSourceStatusDTO(
                    name=str(spec["name"]),
                    display_name=str(spec["display_name"]),
                    provider_type=str(spec["provider_type"]),
                    dataset_key=spec["dataset_key"],
                    status=str(spec["status"]),
                    last_update=last_update,
                    age_seconds=age_seconds,
                    row_count=int(spec["row_count"]),
                    error_rate_pct=float(spec["error_rate_pct"]),
                    error_message=spec["error_message"],
                    is_production_ready=bool(spec["is_production_ready"]),
                    tables=[str(name) for name in spec["tables"]],
                )
            )
        return sources


def _validate_source_name(name: str) -> None:
    """Reject unknown source names to prevent source enumeration."""
    normalized = name.strip().lower()
    if not normalized or normalized not in _KNOWN_SOURCES:
        raise ValueError(f"Unknown data source: {name}")


__all__ = [
    "DataSourceStatusService",
    "_REFRESH_LOCK_TTL_SECONDS",
    "_REFRESH_TIMEOUT_SECONDS",
    "_validate_source_name",
]

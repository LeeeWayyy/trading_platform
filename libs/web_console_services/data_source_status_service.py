"""Service layer for data source health/status visibility.

This service currently returns mock data for UI integration and permission validation.
It is designed to be upgraded to real provider health checks in a future task.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import redis.asyncio as redis_asyncio
import redis.exceptions

from libs.data.data_quality.manifest import ManifestManager, SyncManifest
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
_ALPACA_SIP_MANIFEST_DATASETS = ("alpaca_sip_daily", "alpaca_sip_corp_actions")

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
        "name": "alpaca_sip",
        "display_name": "Alpaca SIP Daily",
        "provider_type": "commercial",
        "is_production_ready": False,
        "dataset_key": "alpaca_sip",
        "tables": ["alpaca_sip_daily", "alpaca_sip_corp_actions"],
        "status": "unknown",
        "minutes_ago": 0,
        "row_count": 0,
        "error_rate_pct": 0.0,
        "error_message": "Local SIP sync status unavailable until manifest-backed status lands",
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
        now = datetime.now(UTC)
        merged: list[DataSourceStatusDTO] = []
        for source in filtered:
            if source.name == "alpaca_sip":
                # Alpaca SIP readiness is manifest-backed; do not let an older
                # cached unknown/manual-refresh DTO mask newly written manifests.
                self._last_refresh_results[source.name] = source
                merged.append(source)
                continue
            merged.append(self._last_refresh_results.get(source.name, source))
        # Recompute age_seconds from last_update so cached DTOs stay accurate
        for source in merged:
            self._last_refresh_results.setdefault(source.name, source)
            if source.last_update is not None:
                source.age_seconds = (now - source.last_update).total_seconds()
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
                raise RuntimeError("Redis client factory required for refresh in real data mode")
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
                _COMPARE_AND_DELETE_LUA,
                1,
                lock_key,
                lock_token,
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
        return await asyncio.wait_for(
            self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS
        )

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
        return await asyncio.wait_for(
            self._perform_refresh(source), timeout=_REFRESH_TIMEOUT_SECONDS
        )

    async def _perform_refresh(self, source: DataSourceStatusDTO) -> DataSourceStatusDTO:
        """Mock refresh implementation (deterministic, idempotent)."""
        await asyncio.sleep(0)
        if source.name == "alpaca_sip":
            # Re-read manifests on every manual refresh so a previously cached
            # unknown status can move to ok/error after sync artifacts appear.
            source = self._get_source_by_name(source.name)
            if source.status == "unknown":
                self._last_refresh_results[source.name] = source
                return source

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
            if spec["name"] == "alpaca_sip":
                spec = self._alpaca_sip_spec_from_manifests(spec)
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

    def _alpaca_sip_spec_from_manifests(self, spec: dict[str, Any]) -> dict[str, Any]:
        manifests = self._load_alpaca_sip_manifests()
        if not manifests:
            return spec

        latest = max(manifest.sync_timestamp for manifest in manifests)
        now = datetime.now(UTC)
        age_seconds = max(0.0, (now - latest).total_seconds())
        validation_statuses = {manifest.validation_status for manifest in manifests}
        status = "ok" if validation_statuses == {"passed"} else "error"
        error_message = (
            None
            if status == "ok"
            else f"SIP manifest validation statuses: {', '.join(sorted(validation_statuses))}"
        )

        updated = dict(spec)
        updated["status"] = status
        updated["minutes_ago"] = max(0, int(age_seconds // 60))
        updated["row_count"] = sum(manifest.row_count for manifest in manifests)
        updated["error_rate_pct"] = 0.0 if status == "ok" else 100.0
        updated["error_message"] = error_message
        return updated

    def _load_alpaca_sip_manifests(self) -> list[SyncManifest]:
        data_root = Path(os.getenv("DATA_ROOT", "data")).resolve()
        manager = ManifestManager(
            storage_path=data_root / "manifests",
            lock_dir=data_root / "locks",
            data_root=data_root,
        )
        manifests: list[SyncManifest] = []
        for dataset in _ALPACA_SIP_MANIFEST_DATASETS:
            manifest = manager.load_manifest(dataset)
            if manifest is not None:
                manifests.append(manifest)
        return manifests


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

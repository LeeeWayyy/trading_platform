"""Data health monitoring for pipeline activity tracking (P6T12.4).

Provides a ``HealthMonitor`` singleton that aggregates freshness status
across multiple data sources.  Each source registers an async check
function that returns its last-update timestamp.  Results are cached
per-source with configurable TTLs.

**Threshold rationale:** Defaults are set to ~1.5x the expected
ETL/pipeline cadence to avoid false STALE alerts.  The global
``data_freshness_minutes`` in ``config/settings.py`` (default: 30 min)
is for circuit-breaker-level staleness and is intentionally separate.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------
class HealthStatus(Enum):
    """Health status of a single data source."""

    OK = "ok"
    STALE = "stale"
    ERROR = "error"


@dataclass
class DataSourceHealth:
    """Result of checking a single data source."""

    name: str
    category: str  # "price", "volume", "signal", "fundamental"
    last_update: datetime | None
    age_seconds: float | None
    status: HealthStatus
    message: str
    last_checked: datetime


# ---------------------------------------------------------------------------
# Thresholds (Pydantic config)
# ---------------------------------------------------------------------------
class HealthThresholds(BaseModel):
    """Configurable staleness thresholds per data category.

    Defaults are ~1.5x the expected pipeline cadence.
    """

    price_stale_seconds: int = 900  # 15 min
    volume_stale_seconds: int = 900  # 15 min
    signal_stale_seconds: int = 600  # 10 min
    fundamental_stale_seconds: int = 86400  # 24 hr

    def get_threshold(self, category: str) -> int:
        """Return the staleness threshold for a category."""
        mapping = {
            "price": self.price_stale_seconds,
            "volume": self.volume_stale_seconds,
            "signal": self.signal_stale_seconds,
            "fundamental": self.fundamental_stale_seconds,
        }
        return mapping.get(category, self.price_stale_seconds)


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------
@dataclass
class _CacheEntry:
    result: DataSourceHealth
    expires_at: datetime


# TTL by category
_CATEGORY_TTL: dict[str, int] = {
    "price": 10,
    "volume": 10,
    "signal": 10,
    "fundamental": 60,
}


# ---------------------------------------------------------------------------
# Registered source
# ---------------------------------------------------------------------------
@dataclass
class _RegisteredSource:
    name: str
    category: str
    check_fn: Callable[[], Awaitable[datetime | None]]


# ---------------------------------------------------------------------------
# HealthMonitor
# ---------------------------------------------------------------------------
class HealthMonitor:
    """Aggregates freshness across registered data sources.

    Supports async check functions and caches results per-source.
    """

    def __init__(self, thresholds: HealthThresholds | None = None) -> None:
        self._thresholds = thresholds or HealthThresholds()
        self._sources: list[_RegisteredSource] = []
        self._source_names: set[str] = set()
        self._cache: dict[str, _CacheEntry] = {}

    def has_source(self, name: str) -> bool:
        """Check whether a source with the given name is already registered."""
        return name in self._source_names

    def register_source(
        self,
        name: str,
        category: str,
        check_fn: Callable[[], Awaitable[datetime | None]],
    ) -> None:
        """Register a data source with an async check function.

        Idempotent: sources with duplicate names are silently skipped.

        Args:
            name: Display name (e.g. "Price Data").
            category: One of "price", "volume", "signal", "fundamental".
            check_fn: Async callable returning last-update ``datetime`` or
                ``None`` if unavailable.
        """
        if self.has_source(name):
            return
        self._sources.append(_RegisteredSource(name=name, category=category, check_fn=check_fn))
        self._source_names.add(name)

    async def check_all(self) -> list[DataSourceHealth]:
        """Check all registered sources, returning cached results when fresh."""
        now = datetime.now(UTC)
        results: list[DataSourceHealth] = []

        for src in self._sources:
            cached = self._cache.get(src.name)
            if cached is not None and cached.expires_at > now:
                results.append(cached.result)
                continue

            try:
                last_update = await src.check_fn()
            except Exception as exc:
                logger.warning(
                    "health_check_failed",
                    extra={"source": src.name, "error": str(exc)},
                )
                health = DataSourceHealth(
                    name=src.name,
                    category=src.category,
                    last_update=None,
                    age_seconds=None,
                    status=HealthStatus.ERROR,
                    message=f"Check failed: {exc}",
                    last_checked=now,
                )
                self._cache_result(src, health, now)
                results.append(health)
                continue

            health = self._evaluate(src, last_update, now)
            self._cache_result(src, health, now)
            results.append(health)

        return results

    def _evaluate(
        self,
        src: _RegisteredSource,
        last_update: datetime | None,
        now: datetime,
    ) -> DataSourceHealth:
        """Evaluate health status for a single source."""
        if last_update is None:
            return DataSourceHealth(
                name=src.name,
                category=src.category,
                last_update=None,
                age_seconds=None,
                status=HealthStatus.ERROR,
                message="No heartbeat recorded",
                last_checked=now,
            )

        age = (now - last_update).total_seconds()
        threshold = self._thresholds.get_threshold(src.category)

        if age <= threshold:
            status = HealthStatus.OK
            message = "OK"
        else:
            status = HealthStatus.STALE
            message = f"Stale: last update {format_age(age)} ago (threshold: {threshold}s)"

        return DataSourceHealth(
            name=src.name,
            category=src.category,
            last_update=last_update,
            age_seconds=age,
            status=status,
            message=message,
            last_checked=now,
        )

    def _cache_result(
        self,
        src: _RegisteredSource,
        health: DataSourceHealth,
        now: datetime,
    ) -> None:
        ttl = _CATEGORY_TTL.get(src.category, 10)
        self._cache[src.name] = _CacheEntry(
            result=health,
            expires_at=now + timedelta(seconds=ttl),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: HealthMonitor | None = None


def get_health_monitor(thresholds: HealthThresholds | None = None) -> HealthMonitor:
    """Return the module-level ``HealthMonitor`` singleton."""
    global _instance  # noqa: PLW0603
    if _instance is None:
        _instance = HealthMonitor(thresholds)
    return _instance


# ---------------------------------------------------------------------------
# Human-readable age formatting
# ---------------------------------------------------------------------------
def format_age(seconds: float | None) -> str:
    """Format age in seconds to human-readable string.

    Examples: "2s ago", "5m 32s ago", "2h 15m ago", "3d 4h ago".
    """
    if seconds is None:
        return "unknown"
    s = int(seconds)
    if s < 0:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        m, remainder = divmod(s, 60)
        return f"{m}m {remainder}s ago"
    if s < 86400:
        h, remainder = divmod(s, 3600)
        m = remainder // 60
        return f"{h}h {m}m ago"
    d, remainder = divmod(s, 86400)
    h = remainder // 3600
    return f"{d}d {h}h ago"


__all__ = [
    "DataSourceHealth",
    "HealthMonitor",
    "HealthStatus",
    "HealthThresholds",
    "format_age",
    "get_health_monitor",
]

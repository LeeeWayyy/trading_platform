"""Performance monitoring and metrics for high-frequency grid updates.

Note: This module MONITORS update rates and triggers degradation mode.
Actual batching is handled by AG Grid's asyncTransactionWaitMillis config.

Architecture Note: The module-level registries (_monitor_registry, _monitor_by_grid_and_session,
_grid_to_monitor) store state in-memory. This implementation assumes a single-process deployment.
If the application is run with multiple workers, state will be inconsistent across workers.
For multi-worker deployments, consider using Redis or another shared state store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

from nicegui import ui

from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.utils.session import get_or_create_client_id

logger = logging.getLogger(__name__)


@dataclass
class UpdateMetrics:
    """Track grid update performance metrics."""

    updates_total: int = 0
    updates_dropped: int = 0
    batches_sent: int = 0
    last_batch_time_ms: float = 0.0
    degradation_events: int = 0
    _window_start: float = field(default_factory=time.time)
    _window_updates: int = 0

    def record_update(self, row_count: int) -> None:
        """Record an update batch."""
        self.updates_total += row_count
        self._window_updates += row_count

    def record_batch(self, batch_time_ms: float) -> None:
        """Record batch send timing."""
        self.batches_sent += 1
        self.last_batch_time_ms = batch_time_ms

    def record_dropped(self, count: int) -> None:
        """Record dropped updates (backpressure)."""
        self.updates_dropped += count

    def get_rate(self) -> float:
        """Get current update rate (updates/sec)."""
        elapsed = time.time() - self._window_start
        if elapsed < 1.0:
            return 0.0
        rate = self._window_updates / elapsed
        # Reset window every 5 seconds
        if elapsed >= 5.0:
            self._window_start = time.time()
            self._window_updates = 0
        return rate

    def to_dict(self) -> dict[str, Any]:
        """Export metrics for logging/monitoring."""
        return {
            "updates_total": self.updates_total,
            "updates_dropped": self.updates_dropped,
            "batches_sent": self.batches_sent,
            "last_batch_time_ms": round(self.last_batch_time_ms, 2),
            "degradation_events": self.degradation_events,
            "current_rate": round(self.get_rate(), 1),
        }


class GridPerformanceMonitor:
    """Monitors grid update rates and triggers degradation mode.

    Note: This class does NOT batch updates. AG Grid handles batching via
    asyncTransactionWaitMillis. This class monitors update rates and toggles
    degradation mode (disabling animations) when updates exceed threshold.
    """

    # Configurable via environment variables
    MAX_BATCH_SIZE = int(os.environ.get("GRID_MAX_BATCH_SIZE", "500"))
    DEGRADE_THRESHOLD = int(os.environ.get("GRID_DEGRADE_THRESHOLD", "120"))

    def __init__(self, grid_id: str) -> None:
        self.grid_id = grid_id
        self.metrics = UpdateMetrics()
        self._degraded = False

    def should_degrade(self) -> bool:
        """Check if degradation mode should be active and update state.

        IMPORTANT: This method has side effects (updates _degraded, logs, increments counter).
        Call ONCE per update cycle and cache the result.
        """
        rate = self.metrics.get_rate()
        should = rate > self.DEGRADE_THRESHOLD
        if should != self._degraded:
            self._degraded = should
            self.metrics.degradation_events += 1
            logger.info(
                "grid_degradation_mode_change",
                extra={
                    "grid_id": self.grid_id,
                    "degraded": should,
                    "rate": round(rate, 1),
                },
            )
        return should

    def is_degraded(self) -> bool:
        """Check current degradation state WITHOUT recalculating.

        Use this to read state without side effects. Call should_degrade() first
        to update state, then use is_degraded() for subsequent checks.
        """
        return self._degraded

    def log_metrics(self) -> None:
        """Log current metrics for observability."""
        logger.info(
            "grid_update_metrics",
            extra={"grid_id": self.grid_id, **self.metrics.to_dict()},
        )

    def attach_to_grid(self, grid: ui.aggrid) -> None:
        """Attach this monitor to a NiceGUI AG Grid instance and register globally.

        Uses WeakKeyDictionary to avoid monkey-patching the grid instance directly.
        """
        _grid_to_monitor[grid] = self
        session_id = get_or_create_client_id()
        if not session_id:
            logger.debug(
                "grid_performance_missing_session_id",
                extra={"grid_id": self.grid_id},
            )
            return
        _monitor_registry[(self.grid_id, session_id)] = self
        _monitor_by_grid_and_session[(self.grid_id, session_id)] = self
        _schedule_monitor_cleanup(self.grid_id, session_id, self)


# Module-level registry for periodic metrics logging
_monitor_registry: dict[tuple[str, str], GridPerformanceMonitor] = {}
_monitor_by_grid_and_session: dict[tuple[str, str], GridPerformanceMonitor] = {}

# WeakKeyDictionary to map grid instances to monitors without monkey-patching
# When grid is garbage collected, the entry is automatically removed
_grid_to_monitor: WeakKeyDictionary[ui.aggrid, GridPerformanceMonitor] = WeakKeyDictionary()


def get_monitor(grid: ui.aggrid) -> GridPerformanceMonitor | None:
    """Retrieve attached monitor from grid instance via WeakKeyDictionary."""
    return _grid_to_monitor.get(grid)


def get_all_monitors() -> dict[tuple[str, str], GridPerformanceMonitor]:
    """Get all registered monitors for periodic metrics logging."""
    return _monitor_registry.copy()


def get_monitor_by_grid_id(grid_id: str, session_id: str) -> GridPerformanceMonitor | None:
    """Retrieve monitor by grid_id + session_id (for use in realtime.py backpressure logging).

    This is useful when you have identifiers but not the grid instance,
    such as in the realtime update path where backpressure drops occur.
    Passing session_id avoids misattribution across multiple clients.
    """
    return _monitor_by_grid_and_session.get((grid_id, session_id))


def _schedule_monitor_cleanup(
    grid_id: str,
    session_id: str,
    monitor: GridPerformanceMonitor,
) -> None:
    """Register cleanup callback to remove monitor entries on disconnect."""

    async def _register_cleanup() -> None:
        lifecycle = ClientLifecycleManager.get()
        await lifecycle.register_cleanup_callback(
            session_id,
            lambda: _remove_monitor(grid_id, session_id, monitor),
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "grid_performance_cleanup_no_event_loop",
            extra={"grid_id": grid_id, "session_id": session_id},
        )
        return
    loop.create_task(_register_cleanup())


def _remove_monitor(grid_id: str, session_id: str, monitor: GridPerformanceMonitor) -> None:
    """Remove registry entries for a monitor, guarding against newer replacements."""
    key = (grid_id, session_id)
    current = _monitor_registry.get(key)
    if current is not monitor:
        return
    _monitor_registry.pop(key, None)
    _monitor_by_grid_and_session.pop(key, None)


__all__ = [
    "GridPerformanceMonitor",
    "UpdateMetrics",
    "get_all_monitors",
    "get_monitor",
    "get_monitor_by_grid_id",
]

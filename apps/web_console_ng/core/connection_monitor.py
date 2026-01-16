"""Connection monitoring with read-only gating and backoff.

Tracks connection health, latency degradation, staleness, and reconnection
attempts with exponential backoff. Exposes read-only mode when disconnected
or reconnecting.
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)

# Defaults
STALE_THRESHOLD_SECONDS = 30.0
DEGRADED_LATENCY_MS = 300.0
DEGRADED_REQUIRED_COUNT = 3
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
MAX_RECONNECT_ATTEMPTS = 6


class ConnectionState(Enum):
    """Connection state for API/data access."""

    CONNECTED = "connected"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class ConnectionMonitor:
    """Track connection state and read-only status.

    Usage:
        monitor = ConnectionMonitor()
        monitor.record_success(latency_ms)
        monitor.record_failure()
        if monitor.is_read_only(): ...
    """

    def __init__(
        self,
        *,
        stale_threshold_seconds: float = STALE_THRESHOLD_SECONDS,
        degraded_latency_ms: float = DEGRADED_LATENCY_MS,
        degraded_required_count: int = DEGRADED_REQUIRED_COUNT,
        backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = BACKOFF_MAX_SECONDS,
        max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS,
    ) -> None:
        self._state = ConnectionState.CONNECTED
        self._stale_threshold_seconds = stale_threshold_seconds
        self._degraded_latency_ms = degraded_latency_ms
        self._degraded_required_count = degraded_required_count
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._max_reconnect_attempts = max_reconnect_attempts

        self._last_success_time: float | None = None
        self._last_failure_time: float | None = None
        self._consecutive_degraded = 0
        self._reconnect_attempts = 0
        self._next_retry_at: float | None = None

    def get_connection_state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    def is_read_only(self) -> bool:
        """Return True when UI should be read-only."""
        return self._state in {ConnectionState.DISCONNECTED, ConnectionState.RECONNECTING}

    def is_stale(self, now: float | None = None) -> bool:
        """Return True if last successful update exceeded the stale threshold."""
        if self._last_success_time is None:
            return False
        current = now if now is not None else time.monotonic()
        return (current - self._last_success_time) > self._stale_threshold_seconds

    def record_success(self, latency_ms: float | None = None) -> None:
        """Record a successful connection check.

        Note: Does NOT reset _consecutive_degraded - that counter is managed
        exclusively by record_latency() to properly track sustained high latency.
        """
        now = time.monotonic()
        self._last_success_time = now
        self._last_failure_time = None
        self._reconnect_attempts = 0
        self._next_retry_at = None
        # Only set to CONNECTED if not already DEGRADED; latency determines degraded state
        if self._state not in {ConnectionState.CONNECTED, ConnectionState.DEGRADED}:
            self._state = ConnectionState.CONNECTED

        if latency_ms is not None:
            self.record_latency(latency_ms)

    def record_latency(self, latency_ms: float) -> None:
        """Update degraded state based on latency measurements."""
        if latency_ms >= self._degraded_latency_ms:
            self._consecutive_degraded += 1
        else:
            self._consecutive_degraded = 0

        if self._state in {ConnectionState.CONNECTED, ConnectionState.DEGRADED}:
            if self._consecutive_degraded >= self._degraded_required_count:
                self._state = ConnectionState.DEGRADED
            elif self._state == ConnectionState.DEGRADED and self._consecutive_degraded == 0:
                self._state = ConnectionState.CONNECTED

    def record_failure(self) -> None:
        """Record a failed connection check and schedule reconnect.

        After max_reconnect_attempts, continues retrying with capped backoff
        to allow recovery from extended outages without requiring page refresh.
        """
        now = time.monotonic()
        self._last_failure_time = now
        self._consecutive_degraded = 0
        self._state = ConnectionState.DISCONNECTED

        self._reconnect_attempts += 1

        if self._reconnect_attempts > self._max_reconnect_attempts:
            # Continue retrying with max backoff - don't stop permanently
            logger.warning(
                "connection_reconnect_extended",
                extra={"attempts": self._reconnect_attempts},
            )
            self._next_retry_at = now + self._backoff_max_seconds
        else:
            backoff = min(
                self._backoff_base_seconds * (2 ** (self._reconnect_attempts - 1)),
                self._backoff_max_seconds,
            )
            self._next_retry_at = now + backoff

    def start_reconnect(self) -> None:
        """Transition from DISCONNECTED to RECONNECTING if a retry is scheduled."""
        if self._state == ConnectionState.DISCONNECTED and self._next_retry_at is not None:
            self._state = ConnectionState.RECONNECTING

    def should_attempt(self, now: float | None = None) -> bool:
        """Return True if a connection attempt should be made now."""
        current = now if now is not None else time.monotonic()
        if self._state in {ConnectionState.CONNECTED, ConnectionState.DEGRADED}:
            return True
        if self._next_retry_at is None:
            return False
        return current >= self._next_retry_at

    def get_reconnect_countdown(self, now: float | None = None) -> float | None:
        """Return seconds until next reconnect attempt, if scheduled."""
        if self._next_retry_at is None:
            return None
        current = now if now is not None else time.monotonic()
        return max(0.0, self._next_retry_at - current)

    def get_badge_text(self, now: float | None = None) -> str:
        """Return display text for the connection badge."""
        current = now if now is not None else time.monotonic()
        if self.is_stale(current) and self._state in {
            ConnectionState.CONNECTED,
            ConnectionState.DEGRADED,
        }:
            return "Stale data"

        if self._state == ConnectionState.RECONNECTING:
            countdown = self.get_reconnect_countdown(current)
            if countdown is not None and countdown > 0:
                return f"Reconnecting in {int(round(countdown))}s"
            return "Reconnecting"
        if self._state == ConnectionState.CONNECTED:
            return "Connected"
        if self._state == ConnectionState.DEGRADED:
            return "Degraded"
        return "Disconnected"

    def get_badge_class(self, now: float | None = None) -> str:
        """Return CSS classes for connection badge based on state."""
        current = now if now is not None else time.monotonic()
        if self.is_stale(current) and self._state in {
            ConnectionState.CONNECTED,
            ConnectionState.DEGRADED,
        }:
            return "bg-yellow-500 text-black"

        if self._state == ConnectionState.CONNECTED:
            return "bg-green-500 text-white"
        if self._state == ConnectionState.DEGRADED:
            return "bg-yellow-500 text-black"
        if self._state == ConnectionState.RECONNECTING:
            return "bg-gray-500 text-white"
        return "bg-red-500 text-white"


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MAX_SECONDS",
    "ConnectionMonitor",
    "ConnectionState",
    "DEGRADED_LATENCY_MS",
    "DEGRADED_REQUIRED_COUNT",
    "MAX_RECONNECT_ATTEMPTS",
    "STALE_THRESHOLD_SECONDS",
]

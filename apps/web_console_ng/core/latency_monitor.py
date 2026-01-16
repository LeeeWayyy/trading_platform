"""Latency monitor for API connection quality tracking.

This module provides a singleton LatencyMonitor that measures HTTP round-trip
latency to the backend API health endpoint. It tracks rolling averages and
historical data for debugging purposes.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from enum import Enum

import httpx

from apps.web_console_ng import config

logger = logging.getLogger(__name__)

# Latency thresholds (milliseconds)
LATENCY_GREEN_MAX = 100  # < 100ms = green
LATENCY_ORANGE_MAX = 300  # 100-300ms = orange, > 300ms = red

# Rolling average configuration
ROLLING_WINDOW_SIZE = 10  # Number of measurements for rolling average
HISTORY_SIZE = 100  # Number of historical measurements to keep

# Measurement timeout
PING_TIMEOUT_SECONDS = 5.0


class LatencyStatus(Enum):
    """Connection latency status levels."""

    GOOD = "good"  # Green: < 100ms
    DEGRADED = "degraded"  # Orange: 100-300ms
    POOR = "poor"  # Red: > 300ms
    DISCONNECTED = "disconnected"  # Gray: No connection


class LatencyMonitor:
    """Monitor and track API connection latency.

    This class measures HTTP round-trip time to the backend API health endpoint
    and provides current latency, rolling average, and connection status.

    Usage:
        monitor = LatencyMonitor()
        await monitor.measure()  # Call periodically (every 5s)
        latency = monitor.get_current_latency()  # Returns ms or None
        status = monitor.get_latency_status()  # Returns LatencyStatus enum
    """

    def __init__(self) -> None:
        """Initialize the latency monitor."""
        self._current_latency: float | None = None
        self._measurements: deque[float] = deque(maxlen=ROLLING_WINDOW_SIZE)
        self._history: deque[tuple[float, float]] = deque(maxlen=HISTORY_SIZE)
        self._last_measurement_time: float | None = None
        self._consecutive_failures = 0
        # Persistent HTTP client for latency measurements
        self._http_client: httpx.AsyncClient | None = None

    def get_current_latency(self) -> float | None:
        """Get the most recent latency measurement in milliseconds.

        Returns:
            Latency in ms, or None if no successful measurement
        """
        return self._current_latency

    def get_rolling_average(self) -> float | None:
        """Get the rolling average latency in milliseconds.

        Returns:
            Rolling average in ms, or None if no measurements
        """
        if not self._measurements:
            return None
        return sum(self._measurements) / len(self._measurements)

    def get_latency_status(self) -> LatencyStatus:
        """Get the current connection status based on latency.

        Returns:
            LatencyStatus enum value
        """
        if self._current_latency is None or self._consecutive_failures >= 3:
            return LatencyStatus.DISCONNECTED
        if self._current_latency < LATENCY_GREEN_MAX:
            return LatencyStatus.GOOD
        if self._current_latency < LATENCY_ORANGE_MAX:
            return LatencyStatus.DEGRADED
        return LatencyStatus.POOR

    def get_status_color_class(self) -> str:
        """Get the CSS color class for current latency status.

        Returns:
            Tailwind CSS class string for badge background
        """
        status = self.get_latency_status()
        if status == LatencyStatus.GOOD:
            return "bg-green-600 text-white"
        if status == LatencyStatus.DEGRADED:
            return "bg-orange-500 text-white"
        if status == LatencyStatus.POOR:
            return "bg-red-600 text-white"
        return "bg-gray-500 text-white"  # DISCONNECTED

    def get_history(self) -> list[tuple[float, float]]:
        """Get historical latency measurements for debugging.

        Returns:
            List of (timestamp, latency_ms) tuples, most recent last
        """
        return list(self._history)

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the persistent HTTP client for latency measurements."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=PING_TIMEOUT_SECONDS)
        return self._http_client

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _handle_failure(self) -> None:
        """Handle a failed latency measurement.

        Increments consecutive failure count and clears latency after 3 failures.
        """
        self._consecutive_failures += 1
        if self._consecutive_failures >= 3:
            self._current_latency = None

    async def measure(self) -> float | None:
        """Measure round-trip latency to the API health endpoint.

        This method should be called periodically (every 5 seconds).

        Returns:
            Latency in milliseconds, or None if measurement failed
        """
        start_time = time.perf_counter()

        try:
            # Use persistent client for efficiency
            http_client = self._get_http_client()
            base_url = config.EXECUTION_GATEWAY_URL.rstrip("/")
            response = await http_client.get(f"{base_url}/healthz")
            response.raise_for_status()

            # Calculate latency
            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000

            # Update state
            self._current_latency = latency_ms
            self._measurements.append(latency_ms)
            self._history.append((time.time(), latency_ms))
            self._last_measurement_time = time.monotonic()
            self._consecutive_failures = 0

            logger.debug(
                "Latency measurement",
                extra={
                    "latency_ms": round(latency_ms, 1),
                    "rolling_avg": round(self.get_rolling_average() or 0, 1),
                    "status": self.get_latency_status().value,
                },
            )

            return latency_ms

        except httpx.TimeoutException:
            logger.warning("Latency measurement timed out")
            self._handle_failure()
            return None

        except httpx.HTTPStatusError as e:
            logger.warning(
                "Latency measurement failed with HTTP error",
                extra={"status_code": e.response.status_code},
            )
            self._handle_failure()
            return None

        except Exception as e:
            logger.warning(
                "Latency measurement failed",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
            self._handle_failure()
            return None

    def format_display(self) -> str:
        """Format latency for header display.

        Returns:
            Formatted string like "24ms" or "--" if disconnected
        """
        if self._current_latency is None:
            return "--"
        return f"{int(self._current_latency)}ms"

    def format_tooltip(self) -> str:
        """Format tooltip with current and average latency.

        Returns:
            Tooltip string like "API Latency: 24ms (avg: 28ms)"
        """
        current = self.format_display()
        avg = self.get_rolling_average()
        if avg is not None:
            return f"API Latency: {current} (avg: {int(avg)}ms)"
        return f"API Latency: {current}"


__all__ = ["LatencyMonitor", "LatencyStatus"]

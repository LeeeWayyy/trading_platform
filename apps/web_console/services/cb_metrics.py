"""Prometheus metrics for circuit breaker operations.

This module defines Prometheus counters for monitoring circuit breaker
status checks, trips, and resets. These metrics are used by the
CircuitBreakerService and exposed via the web console's metrics endpoint.

Metrics:
    cb_status_checks_total: Total circuit breaker status checks
    cb_trip_total: Total circuit breaker trips (manual and automatic)
    cb_reset_total: Total circuit breaker resets
"""

from __future__ import annotations

from prometheus_client import Counter

# No labels - simple counters avoid label mismatch errors
# Labels would require matching inc() calls to always include them

CB_STATUS_CHECKS = Counter(
    "cb_status_checks_total",
    "Total circuit breaker status checks",
)

CB_TRIP_TOTAL = Counter(
    "cb_trip_total",
    "Total circuit breaker trips",
)

CB_RESET_TOTAL = Counter(
    "cb_reset_total",
    "Total circuit breaker resets",
)

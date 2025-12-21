"""Prometheus metrics for circuit breaker operations.

This module defines Prometheus counters for monitoring circuit breaker
status checks, trips, and resets. These metrics are used by the
CircuitBreakerService and exposed via the web console's metrics endpoint.

Metrics:
    cb_status_checks_total: Total circuit breaker status checks
    cb_trip_total: Total circuit breaker trips (manual and automatic)
    cb_reset_total: Total circuit breaker resets
    cb_staleness_seconds: CB verification status (0=verified, sentinel=failed)
        - Binary semantics: 0 on success, 999999 (sentinel) on failure
        - Triggers CBVerificationFailed alert when sentinel is detected
        - Note: Does not track actual staleness time; multiprocess_mode="min"
          ensures Prometheus sees 0 if ANY worker succeeds
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from prometheus_client import Counter, Gauge

if TYPE_CHECKING:
    from libs.redis_client import RedisClient

logger = logging.getLogger(__name__)

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

# CB staleness gauge with multiprocess support
# Use "min" mode: if ANY worker successfully verifies (sets 0), that's reported
# This prevents false alerts when one worker fails but others succeed
_multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
_gauge_kwargs: dict[str, Any] = {}
if _multiproc_dir:
    _gauge_kwargs["multiprocess_mode"] = "min"

cb_staleness_seconds = Gauge(
    "cb_staleness_seconds",
    "CB verification status (0 = just verified, sentinel = failed)",
    **_gauge_kwargs,
)

# Sentinel value indicating CB verification failed (triggers critical alert)
CB_VERIFICATION_FAILED_SENTINEL = 999999.0  # ~11.5 days - clearly abnormal


def update_cb_staleness_metric(redis_client: RedisClient) -> None:
    """Update CB staleness metric based on Redis accessibility.

    SIMPLIFIED SEMANTICS (multiprocess-safe):
    - On successful Redis read: set to 0 (just verified)
    - On any failure: set to sentinel (999999)
    - With multiprocess_mode="min", if ANY worker succeeds, Prometheus sees 0
    - Alert fires only when ALL workers fail to verify

    This avoids per-process state that causes false alerts in multi-worker setups.
    """
    try:
        state_json = redis_client.get("circuit_breaker:state")
        if state_json is None:
            logger.error("cb_state_missing")
            cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)
            return

        # Verify it's valid JSON
        try:
            json.loads(state_json)
        except json.JSONDecodeError:
            logger.error("cb_state_malformed_json")
            cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)
            return

        # Success! Set to 0
        cb_staleness_seconds.set(0)

    except Exception as exc:
        logger.exception("cb_verification_failed", extra={"error": str(exc)})
        cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)

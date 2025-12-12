"""Scheduled audit log cleanup task."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

_audit_cleanup_deleted_count = Counter(
    "audit_cleanup_deleted_count", "Rows deleted by audit cleanup"
)
_audit_cleanup_last_run_timestamp = Gauge(
    "audit_cleanup_last_run_timestamp", "Last audit cleanup run timestamp (unix)"
)
_audit_cleanup_duration_seconds = Histogram(
    "audit_cleanup_duration_seconds", "Duration of audit cleanup execution"
)


async def run_audit_cleanup(audit_logger: Any) -> int:
    """Execute cleanup and emit metrics."""

    start = time.time()
    deleted = int(await audit_logger.cleanup_old_events())
    duration = time.time() - start

    _audit_cleanup_deleted_count.inc(deleted)
    _audit_cleanup_last_run_timestamp.set(datetime.now(UTC).timestamp())
    _audit_cleanup_duration_seconds.observe(duration)

    logger.info(
        "audit_cleanup_completed",
        extra={"deleted": deleted, "duration_seconds": duration},
    )
    return deleted


__all__ = ["run_audit_cleanup"]

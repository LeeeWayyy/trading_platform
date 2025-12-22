"""Audit logging helpers shared across services."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter, Histogram

from libs.web_console_auth.db import acquire_connection

logger = logging.getLogger(__name__)

audit_events_total = Counter(
    "audit_log_events_total",
    "Total audit log events written",
    ["event_type", "outcome"],
)
audit_write_failures_total = Counter(
    "audit_log_write_failures_total",
    "Audit log write failures by reason",
    ["reason"],
)
audit_cleanup_duration_seconds = Histogram(
    "audit_log_cleanup_duration_seconds",
    "Duration of audit log cleanup runs",
)

# Track 7 SLA metrics - exported for use by web_console
admin_action_total = Counter(
    "admin_action_total",
    "Counter of admin actions",
    ["action"],
)

audit_write_latency_seconds = Histogram(
    "audit_write_latency_seconds",
    "Audit log write latency",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0],
)


@asynccontextmanager
async def _maybe_transaction(conn: Any) -> AsyncIterator[None]:
    """Use conn.transaction() when available; otherwise no-op context."""

    txn_factory = getattr(conn, "transaction", None)
    if txn_factory and callable(txn_factory):
        async with txn_factory():
            yield
        return

    yield


class AuditLogger:
    """Audit logging utility with graceful degradation."""

    def __init__(self, db_pool: Any, retention_days: int | None = None) -> None:
        self.db_pool = db_pool
        self.retention_days = retention_days or int(os.getenv("AUDIT_RETENTION_DAYS", "90"))

    async def _write(
        self,
        *,
        user_id: str | None,
        action: str,
        event_type: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        outcome: str = "success",
        details: dict[str, Any] | None = None,
        amr_method: str | None = None,
    ) -> None:
        details = details or {}

        # Track admin actions for SLA metrics (only 'admin' event type)
        if event_type == "admin":
            admin_action_total.labels(action=action).inc()

        if not self.db_pool:
            logger.info("audit_log_fallback", extra={"event_type": event_type, "action": action})
            return

        start = time.monotonic()
        try:
            async with acquire_connection(self.db_pool) as conn:
                async with _maybe_transaction(conn):
                    await conn.execute(
                        """
                        INSERT INTO audit_log (
                            timestamp,
                            user_id,
                            action,
                            details,
                            event_type,
                            resource_type,
                            resource_id,
                            outcome,
                            amr_method
                        )
                        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            action,
                            json.dumps(details, default=str),
                            event_type,
                            resource_type,
                            resource_id,
                            outcome,
                            amr_method,
                        ),
                    )
            audit_events_total.labels(event_type=event_type, outcome=outcome).inc()
        except Exception as exc:  # pragma: no cover
            audit_write_failures_total.labels(reason=exc.__class__.__name__).inc()
            logger.exception(
                "audit_log_write_failed",
                extra={
                    "event_type": event_type,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "outcome": outcome,
                },
            )
        finally:
            audit_write_latency_seconds.observe(time.monotonic() - start)

    async def log_access(
        self,
        *,
        user_id: str | None,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._write(
            user_id=user_id,
            action="access",
            event_type="access",
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
        )

    async def log_action(
        self,
        *,
        user_id: str | None,
        action: str,
        resource_type: str | None,
        resource_id: str | None,
        outcome: str,
        details: dict[str, Any] | None = None,
        amr_method: str | None = None,
    ) -> None:
        await self._write(
            user_id=user_id,
            action=action,
            event_type="action",
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
            amr_method=amr_method,
        )

    async def log_auth_event(
        self,
        *,
        user_id: str | None,
        action: str,
        outcome: str,
        details: dict[str, Any] | None = None,
        amr_method: str | None = None,
    ) -> None:
        await self._write(
            user_id=user_id,
            action=action,
            event_type="auth",
            resource_type="user",
            resource_id=user_id,
            outcome=outcome,
            details=details,
            amr_method=amr_method,
        )

    async def log_admin_change(
        self,
        *,
        admin_user_id: str,
        action: str,
        target_user_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._write(
            user_id=admin_user_id,
            action=action,
            event_type="admin",
            resource_type="user",
            resource_id=target_user_id,
            outcome="success",
            details=details,
        )

    async def log_export(
        self,
        *,
        user_id: str,
        export_type: str,
        resource_type: str,
        row_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"row_count": row_count}
        if metadata:
            details["metadata"] = metadata
        await self._write(
            user_id=user_id,
            action=f"export_{export_type}",
            event_type="export",
            resource_type=resource_type,
            resource_id=None,
            outcome="success",
            details=details,
        )

    async def cleanup_old_events(self) -> int:
        """Delete audit rows older than retention window."""
        if not self.db_pool:
            return 0

        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        start = datetime.now(UTC)
        try:
            async with acquire_connection(self.db_pool) as conn:
                async with _maybe_transaction(conn):
                    result = await conn.execute(
                        "DELETE FROM audit_log WHERE timestamp < %s",
                        (cutoff,),
                    )
            duration = (datetime.now(UTC) - start).total_seconds()
            audit_cleanup_duration_seconds.observe(duration)

            if hasattr(result, "rowcount"):
                return int(result.rowcount or 0)

            logger.warning(
                "audit_log_cleanup_missing_rowcount",
                extra={"result_type": type(result).__name__},
            )
            return 0
        except Exception as exc:  # pragma: no cover
            logger.exception("audit_log_cleanup_failed", extra={"error": str(exc)})
            return 0


__all__ = [
    "AuditLogger",
    "admin_action_total",
    "audit_write_latency_seconds",
    "audit_cleanup_duration_seconds",
    "audit_events_total",
    "audit_write_failures_total",
]

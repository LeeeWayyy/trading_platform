"""Acknowledgment store for data quality alerts.

The Data Page plan (Phase 5 AC) requires acknowledgments to be persisted
server-side with actor, time, source, and issue scope before they can affect
operational state; if persistence is unavailable, acknowledgment controls
render unavailable.

This module defines the storage contract and ships two implementations:

* :class:`InMemoryAlertAcknowledgmentStore` — used when no durable backend is
  configured. ``is_persistent`` is False so callers can render the UI as
  unavailable.
* :class:`PostgresAlertAcknowledgmentStore` — backed by the
  ``data_quality_alert_acknowledgments`` table (see migrations 0017 + 0034).
  Uses ``INSERT ... ON CONFLICT (alert_id) DO NOTHING RETURNING`` to keep
  acknowledgments idempotent on first-write-wins semantics.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from .schemas.data_management import AlertAcknowledgmentDTO

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class AlertAcknowledgmentStore(Protocol):
    """Read/write contract for alert acknowledgments."""

    @property
    def is_persistent(self) -> bool:
        """True iff acknowledgments survive process restarts."""

    def get(self, alert_id: str) -> AlertAcknowledgmentDTO | None:
        """Return an existing acknowledgment for ``alert_id`` or None."""

    def acknowledge(
        self,
        *,
        alert_id: str,
        dataset: str,
        metric: str,
        severity: str,
        acknowledged_by: str,
        reason: str,
        source: str,
        issue_scope: dict[str, Any],
        original_alert: dict[str, Any] | None = None,
    ) -> AlertAcknowledgmentDTO:
        """Record an acknowledgment idempotently and return the durable record."""


class InMemoryAlertAcknowledgmentStore:
    """In-memory fallback used when no durable backend is available.

    ``DataQualityService`` runs ``acknowledge`` inside ``asyncio.to_thread``,
    so two concurrent callers can race on the same ``alert_id``. The
    read-check-write block is guarded by a ``threading.Lock`` to preserve
    first-write-wins idempotency. ``Postgres`` does not need this guard
    because ``INSERT ... ON CONFLICT DO NOTHING RETURNING`` is the atomic
    idempotency primitive.
    """

    is_persistent = False

    def __init__(self) -> None:
        self._records: dict[str, AlertAcknowledgmentDTO] = {}
        self._lock = threading.Lock()

    def get(self, alert_id: str) -> AlertAcknowledgmentDTO | None:
        return self._records.get(alert_id)

    def acknowledge(
        self,
        *,
        alert_id: str,
        dataset: str,
        metric: str,
        severity: str,
        acknowledged_by: str,
        reason: str,
        source: str,
        issue_scope: dict[str, Any],
        original_alert: dict[str, Any] | None = None,
    ) -> AlertAcknowledgmentDTO:
        with self._lock:
            existing = self._records.get(alert_id)
            if existing is not None:
                return existing
            record = AlertAcknowledgmentDTO(
                id=str(uuid4()),
                alert_id=alert_id,
                dataset=dataset,
                metric=metric,
                severity=severity,
                acknowledged_by=acknowledged_by,
                acknowledged_at=datetime.now(UTC),
                reason=reason,
                source=source,
                issue_scope=dict(issue_scope),
            )
            self._records[alert_id] = record
            return record


class PostgresAlertAcknowledgmentStore:
    """Durable acknowledgment store backed by Postgres.

    The store keeps the synchronous shape of the rest of ``web_console_services``
    (services run blocking work in ``asyncio.to_thread``), so all DB access
    here is sync and uses ``psycopg_pool.ConnectionPool``.
    """

    is_persistent = True

    _TABLE = "data_quality_alert_acknowledgments"

    def __init__(self, db_pool: ConnectionPool) -> None:
        self._db_pool = db_pool

    def get(self, alert_id: str) -> AlertAcknowledgmentDTO | None:
        sql = f"""
            SELECT id, alert_id, dataset, metric, severity,
                   acknowledged_by, acknowledged_at, reason,
                   source, issue_scope
            FROM {self._TABLE}
            WHERE alert_id = %s
        """
        with self._db_pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (alert_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_dto(row)

    def acknowledge(
        self,
        *,
        alert_id: str,
        dataset: str,
        metric: str,
        severity: str,
        acknowledged_by: str,
        reason: str,
        source: str,
        issue_scope: dict[str, Any],
        original_alert: dict[str, Any] | None = None,
    ) -> AlertAcknowledgmentDTO:
        scope_payload = json.dumps(dict(issue_scope))
        alert_payload = json.dumps(original_alert or {})
        insert_sql = f"""
            INSERT INTO {self._TABLE} (
                alert_id, dataset, metric, severity,
                acknowledged_by, reason, source, issue_scope, original_alert
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
            ON CONFLICT (alert_id) DO NOTHING
            RETURNING id, alert_id, dataset, metric, severity,
                      acknowledged_by, acknowledged_at, reason,
                      source, issue_scope
        """
        select_sql = f"""
            SELECT id, alert_id, dataset, metric, severity,
                   acknowledged_by, acknowledged_at, reason,
                   source, issue_scope
            FROM {self._TABLE}
            WHERE alert_id = %s
        """
        with self._db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    insert_sql,
                    (
                        alert_id,
                        dataset,
                        metric,
                        severity,
                        acknowledged_by,
                        reason,
                        source,
                        scope_payload,
                        alert_payload,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    # ON CONFLICT path: another writer already recorded it.
                    cur.execute(select_sql, (alert_id,))
                    row = cur.fetchone()
            conn.commit()
        if row is None:
            raise RuntimeError(
                f"Failed to read acknowledgment for alert_id={alert_id} after insert"
            )
        return self._row_to_dto(row)

    @staticmethod
    def _row_to_dto(row: Any) -> AlertAcknowledgmentDTO:
        (
            ack_id,
            alert_id,
            dataset,
            metric,
            severity,
            acknowledged_by,
            acknowledged_at,
            reason,
            source,
            issue_scope,
        ) = row
        if isinstance(issue_scope, str):
            try:
                parsed = json.loads(issue_scope)
            except json.JSONDecodeError:
                parsed = None
            issue_scope_dict = parsed if isinstance(parsed, dict) else {}
        elif isinstance(issue_scope, dict):
            issue_scope_dict = issue_scope
        else:
            # JSONB columns may also arrive as a list/None depending on
            # adapter — Pydantic requires a dict, so coerce defensively.
            issue_scope_dict = {}
        return AlertAcknowledgmentDTO(
            id=str(ack_id),
            alert_id=str(alert_id),
            dataset=str(dataset),
            metric=str(metric),
            severity=str(severity),
            acknowledged_by=str(acknowledged_by),
            acknowledged_at=acknowledged_at,
            reason=reason if reason is not None else None,
            source=str(source) if source is not None else "unknown",
            issue_scope=issue_scope_dict,
        )


__all__ = [
    "AlertAcknowledgmentStore",
    "InMemoryAlertAcknowledgmentStore",
    "PostgresAlertAcknowledgmentStore",
]

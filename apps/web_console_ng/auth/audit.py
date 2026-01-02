"""Authentication audit logging (JSON logs + Postgres queue)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from apps.web_console_ng import config
from libs.web_console_auth.db import acquire_connection

logger = logging.getLogger("audit.auth")


class AuthAuditLogger:
    """Dual-sink audit logging: JSON logs + Postgres database."""

    _instance: AuthAuditLogger | None = None

    def __init__(self, db_enabled: bool = True, db_pool: Any | None = None) -> None:
        self._queue: deque[tuple[tuple[Any, ...], int]] = deque(maxlen=1000)
        self._dead_letter: deque[tuple[Any, ...]] = deque(maxlen=1000)
        self._db_enabled = db_enabled
        self._db_pool = db_pool
        self._flush_interval = 0.1
        self._batch_size = 10
        self._flush_task: asyncio.Task[None] | None = None
        self._dropped_count = 0
        self._dead_letter_count = 0
        self._max_retries = 3

        sink = config.AUDIT_LOG_SINK
        self._log_sink = sink in {"log", "both"}
        self._db_sink = sink in {"db", "both"}

    @classmethod
    def get(cls, db_enabled: bool = True, db_pool: Any | None = None) -> AuthAuditLogger:
        if cls._instance is None:
            cls._instance = cls(db_enabled=db_enabled, db_pool=db_pool)
        else:
            if db_pool is not None:
                cls._instance.set_db_pool(db_pool)
            if db_enabled and not cls._instance._db_enabled:
                cls._instance._db_enabled = True
        return cls._instance

    def set_db_pool(self, db_pool: Any | None) -> None:
        self._db_pool = db_pool
        if db_pool is not None and self._db_sink:
            self._db_enabled = True

    async def start(self) -> None:
        if self._db_enabled and self._db_sink and self._flush_task is None:
            if self._db_pool is None:
                logger.warning("audit_db_pool_unavailable_startup")
                self._db_enabled = False
                return
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._flush_task:
            task = self._flush_task
            self._flush_task = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await self._flush_to_db()

        # Log dead-letter contents on shutdown to prevent silent data loss
        if self._dead_letter:
            logger.error(
                "Dumping %d audit events from dead-letter queue on shutdown.",
                len(self._dead_letter),
                extra={"dead_letter_events": json.dumps(list(self._dead_letter), default=str)},
            )

    def log_event(
        self,
        *,
        event_type: str,
        user_id: str | None,
        session_id: str | None,
        client_ip: str,
        user_agent: str,
        auth_type: str,
        outcome: str,
        failure_reason: str | None = None,
        request_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        req_id = request_id or str(uuid.uuid4())
        request_id_raw: str | None = None
        try:
            req_uuid = uuid.UUID(req_id)
        except ValueError:
            request_id_raw = req_id
            req_uuid = uuid.uuid4()
            req_id = str(req_uuid)
            if extra_data is None:
                extra_data = {}
            else:
                extra_data = dict(extra_data)
            extra_data.setdefault("request_id_raw", request_id_raw)

        event = {
            "timestamp": now.isoformat(),
            "event_type": event_type,
            "user_id": user_id or "anonymous",
            "session_id": session_id[:8] if session_id else None,
            "client_ip": client_ip,
            "user_agent": (user_agent or "")[:256] if user_agent is not None else None,
            "auth_type": auth_type,
            "outcome": outcome,
            "failure_reason": failure_reason,
            "request_id": req_id,
            "extra_data": extra_data,
        }

        if self._log_sink:
            level = logging.INFO if outcome == "success" else logging.WARNING
            logger.log(level, json.dumps(event, default=str))

        if self._db_enabled and self._db_sink:
            if self._queue.maxlen is not None and len(self._queue) >= self._queue.maxlen:
                self._dropped_count += 1
                logger.warning(
                    "audit_queue_overflow dropped=%s maxlen=%s",
                    self._dropped_count,
                    self._queue.maxlen,
                )
            payload = (
                now,
                event_type,
                user_id or "anonymous",
                session_id[:8] if session_id else None,
                client_ip,
                (user_agent or "")[:256] if user_agent is not None else None,
                auth_type,
                outcome,
                failure_reason,
                req_uuid,
                json.dumps(extra_data, default=str) if extra_data is not None else None,
            )
            self._queue.append((payload, 0))

    async def _flush_loop(self) -> None:
        while self._db_enabled and self._db_sink:
            await asyncio.sleep(self._flush_interval)
            if self._queue:
                await self._flush_to_db()

    async def _flush_to_db(self) -> None:
        if not self._queue or not self._db_enabled or not self._db_sink:
            return
        if self._db_pool is None:
            logger.debug("audit_db_pool_unavailable")
            return

        batch_payloads: list[tuple[Any, ...]] = []
        batch_items: list[tuple[tuple[Any, ...], int]] = []
        while self._queue and len(batch_payloads) < self._batch_size:
            payload, attempts = self._queue.popleft()
            batch_payloads.append(payload)
            batch_items.append((payload, attempts))

        try:
            async with acquire_connection(self._db_pool) as conn:
                await conn.executemany(
                    """
                    INSERT INTO auth_audit_log
                        (timestamp, event_type, user_id, session_id, client_ip,
                         user_agent, auth_type, outcome, failure_reason, request_id, extra_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    batch_payloads,
                )
        except asyncio.CancelledError:
            logger.warning("Audit DB flush cancelled. Re-queueing %d items.", len(batch_items))
            for payload, attempts in reversed(batch_items):
                self._queue.appendleft((payload, attempts))
            raise
        except Exception as exc:
            logger.error("Audit DB write failed: %s", exc)
            # Expose metric for monitoring audit flush failures
            try:
                from apps.web_console_ng.core.metrics import audit_flush_errors_total

                audit_flush_errors_total.labels(pod=config.POD_NAME).inc()
            except ImportError:
                logger.warning("Metrics module not available for audit flush error tracking")

            for payload, attempts in reversed(batch_items):
                next_attempt = attempts + 1
                if next_attempt >= self._max_retries:
                    self._dead_letter.append(payload)
                    self._dead_letter_count += 1
                    logger.error(
                        "audit_dead_letter_enqueue attempts=%s total=%s",
                        next_attempt,
                        self._dead_letter_count,
                    )
                else:
                    self._queue.appendleft((payload, next_attempt))


__all__ = ["AuthAuditLogger"]

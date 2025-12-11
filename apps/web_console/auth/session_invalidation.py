"""Session invalidation helpers using session_version increments."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from apps.web_console.utils.db import acquire_connection

logger = logging.getLogger(__name__)


class SessionInvalidationError(Exception):
    """Raised when a session invalidation operation cannot be completed."""


@asynccontextmanager
async def _maybe_transaction(conn: Any) -> AsyncIterator[None]:
    """Use conn.transaction() when available, otherwise yield without transaction."""

    if hasattr(conn, "transaction") and callable(conn.transaction):
        txn_cm = conn.transaction()
        if hasattr(txn_cm, "__aenter__"):
            async with txn_cm:
                yield
            return
    yield


async def invalidate_user_sessions(
    user_id: str,
    db_pool: Any,
    audit_logger: Any | None = None,
    admin_user_id: str | None = None,
) -> int:
    """Increment session_version to invalidate active sessions.

    Returns the new session_version (defaults to 1 if row missing).
    """

    try:
        async with acquire_connection(db_pool) as conn:
            async with _maybe_transaction(conn):
                # [v1.5] Use psycopg3-style %s placeholders consistently
                cursor = await conn.execute(
                    """
                    UPDATE user_roles
                    SET session_version = session_version + 1,
                        updated_at = NOW(),
                        updated_by = COALESCE(%s, updated_by)
                    WHERE user_id = %s
                    RETURNING session_version
                    """,
                    (admin_user_id, user_id),
                )
                row = await cursor.fetchone()
    except Exception as exc:  # pragma: no cover
        logger.exception("session_invalidation_failed", extra={"user_id": user_id, "error": str(exc)})
        raise

    if not row:
        logger.warning("session_invalidation_no_rows_updated", extra={"user_id": user_id})
        raise SessionInvalidationError(f"No session row found for user_id={user_id}")

    # psycopg3: row is a tuple, access by index
    new_version = int(row["session_version"] if isinstance(row, dict) else row[0])

    if audit_logger and admin_user_id:
        try:
            await audit_logger.log_admin_change(
                admin_user_id=admin_user_id,
                action="invalidate_sessions",
                target_user_id=user_id,
                details={"new_session_version": new_version},
            )
        except Exception:  # pragma: no cover
            logger.warning("audit_log_failure_during_invalidation", exc_info=True)

    return new_version


async def validate_session_version(user_id: str, session_version: int, db_pool: Any) -> bool:
    """Return True if provided session_version matches DB."""

    try:
        async with acquire_connection(db_pool) as conn:
            async with _maybe_transaction(conn):
                # [v1.5] Use psycopg3-style %s placeholders consistently
                cursor = await conn.execute(
                    "SELECT session_version FROM user_roles WHERE user_id = %s",
                    (user_id,),
                )
                row = await cursor.fetchone()
    except Exception as exc:  # pragma: no cover
        logger.exception("session_version_validation_failed", extra={"user_id": user_id, "error": str(exc)})
        return False

    if not row:
        return False

    db_version = row["session_version"] if isinstance(row, dict) else row[0]

    return int(db_version) == int(session_version)


__all__ = ["invalidate_user_sessions", "validate_session_version", "SessionInvalidationError"]

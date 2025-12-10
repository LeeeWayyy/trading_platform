"""Session invalidation helpers using session_version increments."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)


class SessionInvalidationError(Exception):
    """Raised when a session invalidation operation cannot be completed."""


@asynccontextmanager
async def _conn(db_pool: Any) -> AsyncIterator[Any]:
    if hasattr(db_pool, "acquire"):
        candidate = db_pool.acquire()
        if hasattr(candidate, "__aenter__"):
            async with candidate as conn:
                yield conn
        else:
            conn = await candidate if inspect.isawaitable(candidate) else candidate
            try:
                yield conn
            finally:
                releaser = getattr(db_pool, "release", None)
                if releaser:
                    maybe = releaser(conn)
                    if inspect.isawaitable(maybe):
                        await maybe
        return
    if hasattr(db_pool, "connection"):
        candidate = db_pool.connection()
        conn = await candidate if inspect.isawaitable(candidate) else candidate
        async with conn:
            yield conn
        return
    raise RuntimeError("Unsupported db_pool interface")


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
        async with _conn(db_pool) as conn:
            row = await conn.fetchrow(
                """
                UPDATE user_roles
                SET session_version = session_version + 1,
                    updated_at = NOW(),
                    updated_by = COALESCE($2, updated_by)
                WHERE user_id = $1
                RETURNING session_version
                """,
                user_id,
                admin_user_id,
            )
    except Exception as exc:  # pragma: no cover
        logger.exception("session_invalidation_failed", extra={"user_id": user_id, "error": str(exc)})
        raise

    if not row:
        logger.warning("session_invalidation_no_rows_updated", extra={"user_id": user_id})
        raise SessionInvalidationError(f"No session row found for user_id={user_id}")

    # mypy: row values are Any from asyncpg fetchrow; coerce to int for return type
    new_version = int(row["session_version"])

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
        async with _conn(db_pool) as conn:
            row = await conn.fetchrow(
                "SELECT session_version FROM user_roles WHERE user_id = $1",
                user_id,
            )
    except Exception as exc:  # pragma: no cover
        logger.exception("session_version_validation_failed", extra={"user_id": user_id, "error": str(exc)})
        return False

    if not row:
        return False

    return int(row["session_version"]) == int(session_version)


__all__ = ["invalidate_user_sessions", "validate_session_version", "SessionInvalidationError"]

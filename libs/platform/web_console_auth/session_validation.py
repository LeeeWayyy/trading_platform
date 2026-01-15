"""Session invalidation and validation helpers shared across services."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg import DatabaseError, OperationalError

from libs.platform.web_console_auth.db import acquire_connection

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
    """Increment session_version to invalidate active sessions."""

    try:
        async with acquire_connection(db_pool) as conn:
            async with _maybe_transaction(conn):
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
    except OperationalError as exc:
        # Database connection/operational errors (network, timeout, etc.)
        logger.exception(
            "session_invalidation_failed_db_operational_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise
    except DatabaseError as exc:
        # Database errors (constraint violations, query errors, etc.)
        logger.exception(
            "session_invalidation_failed_db_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise
    except Exception as exc:  # Generic catch justified - must raise but log unexpected errors
        # Unexpected errors (should be rare, but defensive logging)
        logger.exception(
            "session_invalidation_failed_unexpected_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise

    if not row:
        logger.warning("session_invalidation_no_rows_updated", extra={"user_id": user_id})
        raise SessionInvalidationError(f"No session row found for user_id={user_id}")

    new_version = int(row["session_version"] if isinstance(row, dict) else row[0])

    if audit_logger and admin_user_id:
        try:
            await audit_logger.log_admin_change(
                admin_user_id=admin_user_id,
                action="invalidate_sessions",
                target_user_id=user_id,
                details={"new_session_version": new_version},
            )
        except (OperationalError, DatabaseError) as exc:
            # Database errors in audit logging - log warning but don't fail invalidation
            logger.warning(
                "audit_log_db_error_during_invalidation",
                extra={
                    "user_id": user_id,
                    "admin_user_id": admin_user_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception as exc:  # Generic catch justified - audit log failures should not block invalidation
            # Unexpected errors in audit logging - log warning but don't fail invalidation
            logger.warning(
                "audit_log_unexpected_error_during_invalidation",
                extra={
                    "user_id": user_id,
                    "admin_user_id": admin_user_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )

    return new_version


async def validate_session_version(user_id: str, session_version: int, db_pool: Any) -> bool:
    """Return True if provided session_version matches DB.

    Returns False on any error (fail closed for security).
    """

    try:
        async with acquire_connection(db_pool) as conn:
            async with _maybe_transaction(conn):
                cursor = await conn.execute(
                    "SELECT session_version FROM user_roles WHERE user_id = %s",
                    (user_id,),
                )
                row = await cursor.fetchone()
    except OperationalError as exc:
        # Database connection/operational errors - fail closed (deny access)
        logger.warning(
            "session_version_validation_failed_db_operational_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return False
    except DatabaseError as exc:
        # Database errors - fail closed (deny access)
        logger.warning(
            "session_version_validation_failed_db_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return False
    except Exception as exc:  # Generic catch justified - validation must fail closed on unexpected errors
        # Unexpected errors - fail closed (deny access)
        logger.warning(
            "session_version_validation_failed_unexpected_error",
            extra={
                "user_id": user_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        return False

    if not row:
        return False

    db_version = row["session_version"] if isinstance(row, dict) else row[0]

    return int(db_version) == int(session_version)


__all__ = ["invalidate_user_sessions", "validate_session_version", "SessionInvalidationError"]

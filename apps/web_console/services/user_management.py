"""Database operations for user management.

[v1.1] All failure paths now emit audit log entries for denied attempts.
[v1.1] Uses per-request DB pool pattern for thread safety.
[v1.3] All SQL uses %s placeholders (psycopg3 style).
[v1.4] All mutating operations use explicit transactions for atomicity.

SECURITY NOTE: Authorization (MANAGE_USERS permission) is enforced at the
page/component layer (admin_users.py, bulk_operations.py, strategy_assignment.py).
Service functions assume the caller has verified permissions and log all actions
to the audit trail for post-facto security review. This is a defense-in-depth
approach where UI enforces access control and service layer provides audit trail.

Provides async functions for:
- Listing users with strategy counts
- Changing user roles (with denied-attempt logging)
- Granting/revoking strategy access (with denied-attempt logging)
- Listing available strategies
- Bulk operations
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.auth.permissions import Role
from apps.web_console.utils.db import acquire_connection

logger = logging.getLogger(__name__)


@dataclass
class UserInfo:
    """User info with role and strategy count."""

    user_id: str
    role: str
    session_version: int
    updated_at: str
    updated_by: str | None
    strategy_count: int


@dataclass
class StrategyInfo:
    """Strategy info for assignment UI."""

    strategy_id: str
    name: str
    description: str | None


async def list_users(db_pool: Any) -> list[UserInfo]:
    """List all users with their strategy counts."""

    async with acquire_connection(db_pool) as conn:
        cursor = await conn.execute(
            """
            SELECT
                ur.user_id,
                ur.role,
                ur.session_version,
                ur.updated_at,
                ur.updated_by,
                COUNT(usa.strategy_id) as strategy_count
            FROM user_roles ur
            LEFT JOIN user_strategy_access usa ON ur.user_id = usa.user_id
            GROUP BY ur.user_id, ur.role, ur.session_version, ur.updated_at, ur.updated_by
            ORDER BY ur.role, ur.user_id
        """
        )
        rows = await cursor.fetchall()
        return [_row_to_user_info(row) for row in rows]


def _row_to_user_info(row: Any) -> UserInfo:
    """Convert tuple/dict row to UserInfo (supports dict_row connections)."""
    if isinstance(row, Mapping):
        return UserInfo(
            user_id=str(row.get("user_id", "")),
            role=str(row.get("role", "")),
            session_version=int(row.get("session_version", 0)),
            updated_at=str(row.get("updated_at", "")),
            updated_by=row.get("updated_by"),
            strategy_count=int(row.get("strategy_count", 0)),
        )

    return UserInfo(
        user_id=str(row[0]),
        role=str(row[1]),
        session_version=int(row[2]),
        updated_at=str(row[3]),
        updated_by=row[4],
        strategy_count=int(row[5]),
    )


async def change_user_role(
    db_pool: Any,
    user_id: str,
    new_role: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
    reason: str,
) -> tuple[bool, str]:
    """Change user role with session invalidation and audit logging.

    [v1.1] Now logs DENIED attempts to audit trail, not just successes.
    Returns (success, message).
    """

    valid_roles = {r.value for r in Role}
    if new_role not in valid_roles:
        # [v1.1] Log denied attempt
        await audit_logger.log_action(
            user_id=admin_user_id,
            action="role_change_denied",
            resource_type="user",
            resource_id=user_id,
            outcome="denied",
            details={"reason": "invalid_role", "attempted_role": new_role},
        )
        return False, f"Invalid role: {new_role}"

    try:
        async with acquire_connection(db_pool) as conn:
            # [v1.4] Use explicit transaction for atomicity
            async with conn.transaction():
                # Get old role for audit
                cursor = await conn.execute(
                    "SELECT role FROM user_roles WHERE user_id = %s FOR UPDATE",
                    (user_id,),
                )
                old_row = await cursor.fetchone()
                if not old_row:
                    # [v1.1] Log denied attempt
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="role_change_denied",
                        resource_type="user",
                        resource_id=user_id,
                        outcome="denied",
                        details={"reason": "user_not_found"},
                    )
                    return False, f"User not found: {user_id}"

                old_role = old_row[0]
                if old_role == new_role:
                    # [v1.1] Log denied (no-op)
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="role_change_denied",
                        resource_type="user",
                        resource_id=user_id,
                        outcome="denied",
                        details={"reason": "no_change", "current_role": old_role},
                    )
                    return False, f"User already has role: {new_role}"

                # Update role and increment session_version
                await conn.execute(
                    """
                    UPDATE user_roles
                    SET role = %s,
                        updated_by = %s,
                        updated_at = NOW(),
                        session_version = session_version + 1
                    WHERE user_id = %s
                """,
                    (new_role, admin_user_id, user_id),
                )

        # Audit log success
        await audit_logger.log_admin_change(
            admin_user_id=admin_user_id,
            action="role_change",
            target_user_id=user_id,
            details={
                "old_role": old_role,
                "new_role": new_role,
                "reason": reason,
            },
        )

        logger.info(
            "role_changed",
            extra={
                "user_id": user_id,
                "old_role": old_role,
                "new_role": new_role,
                "by": admin_user_id,
            },
        )

        return True, f"Role changed from {old_role} to {new_role}"

    except psycopg.Error as e:  # pragma: no cover - defensive logging
        # [v1.1] Log failed attempt
        await audit_logger.log_action(
            user_id=admin_user_id,
            action="role_change_failed",
            resource_type="user",
            resource_id=user_id,
            outcome="failed",
            details={"reason": "db_error", "error": str(e)},
        )
        logger.exception("role_change_failed", extra={"user_id": user_id, "error": str(e)})
        return False, f"Database error: {str(e)}"


async def list_strategies(db_pool: Any) -> list[StrategyInfo]:
    """List all available strategies."""

    async with acquire_connection(db_pool) as conn:
        cursor = await conn.execute(
            "SELECT strategy_id, name, description FROM strategies ORDER BY strategy_id"
        )
        rows = await cursor.fetchall()
        return [
            StrategyInfo(
                strategy_id=r[0],
                name=r[1],
                description=r[2],
            )
            for r in rows
        ]


async def get_user_strategies(db_pool: Any, user_id: str) -> list[str]:
    """Get list of strategy IDs assigned to user."""

    async with acquire_connection(db_pool) as conn:
        cursor = await conn.execute(
            "SELECT strategy_id FROM user_strategy_access WHERE user_id = %s",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def grant_strategy(
    db_pool: Any,
    user_id: str,
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> tuple[bool, str]:
    """Grant strategy access to user.

    Session invalidation is handled by DB trigger (0007_strategy_session_version_triggers.sql)
    which automatically increments session_version on INSERT to user_strategy_access.
    This ensures session invalidation cannot be bypassed by any code path.

    Logs DENIED attempts to audit trail.
    """

    try:
        async with acquire_connection(db_pool) as conn:
            # [v1.4] Use explicit transaction for atomicity (INSERT + UPDATE)
            async with conn.transaction():
                # [v1.3] Verify strategy exists before granting
                cursor = await conn.execute(
                    "SELECT 1 FROM strategies WHERE strategy_id = %s",
                    (strategy_id,),
                )
                strategy_exists = await cursor.fetchone()
                if not strategy_exists:
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="strategy_grant_denied",
                        resource_type="user_strategy",
                        resource_id=f"{user_id}:{strategy_id}",
                        outcome="denied",
                        details={"reason": "strategy_not_found"},
                    )
                    return False, f"Strategy {strategy_id} does not exist"

                insert_cursor = await conn.execute(
                    """
                    INSERT INTO user_strategy_access (user_id, strategy_id, granted_by)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, strategy_id) DO NOTHING
                """,
                    (user_id, strategy_id, admin_user_id),
                )

                if insert_cursor.rowcount == 0:
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="strategy_grant_denied",
                        resource_type="user_strategy",
                        resource_id=f"{user_id}:{strategy_id}",
                        outcome="denied",
                        details={"reason": "already_granted"},
                    )
                    return False, f"Strategy {strategy_id} already granted"

        await audit_logger.log_admin_change(
            admin_user_id=admin_user_id,
            action="strategy_grant",
            target_user_id=user_id,
            details={"strategy_id": strategy_id},
        )

        return True, f"Granted {strategy_id}"

    except Exception as e:  # pragma: no cover - defensive logging
        await audit_logger.log_action(
            user_id=admin_user_id,
            action="strategy_grant_failed",
            resource_type="user_strategy",
            resource_id=f"{user_id}:{strategy_id}",
            outcome="failed",
            details={"reason": "db_error", "error": str(e)},
        )
        logger.exception("grant_strategy_failed", extra={"error": str(e)})
        return False, f"Error: {str(e)}"


async def revoke_strategy(
    db_pool: Any,
    user_id: str,
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> tuple[bool, str]:
    """Revoke strategy access from user.

    Session invalidation is handled by DB trigger (0007_strategy_session_version_triggers.sql)
    which automatically increments session_version on DELETE from user_strategy_access.
    This ensures session invalidation cannot be bypassed by any code path.

    Logs DENIED attempts to audit trail.
    """

    try:
        async with acquire_connection(db_pool) as conn:
            # [v1.4] Use explicit transaction for atomicity (DELETE + UPDATE)
            async with conn.transaction():
                # [v1.3] Verify strategy exists before revoking (for clearer error message)
                cursor = await conn.execute(
                    "SELECT 1 FROM strategies WHERE strategy_id = %s",
                    (strategy_id,),
                )
                strategy_exists = await cursor.fetchone()
                if not strategy_exists:
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="strategy_revoke_denied",
                        resource_type="user_strategy",
                        resource_id=f"{user_id}:{strategy_id}",
                        outcome="denied",
                        details={"reason": "strategy_not_found"},
                    )
                    return False, f"Strategy {strategy_id} does not exist"

                cursor = await conn.execute(
                    """
                    DELETE FROM user_strategy_access
                    WHERE user_id = %s AND strategy_id = %s
                """,
                    (user_id, strategy_id),
                )

                # Check if any rows deleted (psycopg3: cursor.rowcount)
                if cursor.rowcount == 0:
                    await audit_logger.log_action(
                        user_id=admin_user_id,
                        action="strategy_revoke_denied",
                        resource_type="user_strategy",
                        resource_id=f"{user_id}:{strategy_id}",
                        outcome="denied",
                        details={"reason": "not_assigned"},
                    )
                    return False, f"Strategy {strategy_id} not assigned"

        await audit_logger.log_admin_change(
            admin_user_id=admin_user_id,
            action="strategy_revoke",
            target_user_id=user_id,
            details={"strategy_id": strategy_id},
        )

        return True, f"Revoked {strategy_id}"

    except Exception as e:  # pragma: no cover - defensive logging
        await audit_logger.log_action(
            user_id=admin_user_id,
            action="strategy_revoke_failed",
            resource_type="user_strategy",
            resource_id=f"{user_id}:{strategy_id}",
            outcome="failed",
            details={"reason": "db_error", "error": str(e)},
        )
        logger.exception("revoke_strategy_failed", extra={"error": str(e)})
        return False, f"Error: {str(e)}"


# [v1.2] Bulk operations - supports role changes, strategy grants AND revokes
async def bulk_change_roles(
    db_pool: Any,
    user_ids: list[str],
    new_role: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
    reason: str,
) -> dict[str, tuple[bool, str]]:
    """Change roles for multiple users.

    Returns dict mapping user_id -> (success, message).
    Each operation is independent; failures don't affect other users.
    """

    results: dict[str, tuple[bool, str]] = {}
    for user_id in user_ids:
        success, msg = await change_user_role(
            db_pool, user_id, new_role, admin_user_id, audit_logger, reason
        )
        results[user_id] = (success, msg)
    return results


async def bulk_grant_strategy(
    db_pool: Any,
    user_ids: list[str],
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> dict[str, tuple[bool, str]]:
    """Grant strategy to multiple users.

    Returns dict mapping user_id -> (success, message).
    """

    results: dict[str, tuple[bool, str]] = {}
    for user_id in user_ids:
        success, msg = await grant_strategy(
            db_pool, user_id, strategy_id, admin_user_id, audit_logger
        )
        results[user_id] = (success, msg)
    return results


# [v1.2 NEW] Bulk strategy revoke
async def bulk_revoke_strategy(
    db_pool: Any,
    user_ids: list[str],
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> dict[str, tuple[bool, str]]:
    """Revoke strategy from multiple users.

    Returns dict mapping user_id -> (success, message).
    """

    results: dict[str, tuple[bool, str]] = {}
    for user_id in user_ids:
        success, msg = await revoke_strategy(
            db_pool, user_id, strategy_id, admin_user_id, audit_logger
        )
        results[user_id] = (success, msg)
    return results


__all__ = [
    "UserInfo",
    "StrategyInfo",
    "list_users",
    "change_user_role",
    "list_strategies",
    "get_user_strategies",
    "grant_strategy",
    "revoke_strategy",
    "bulk_change_roles",
    "bulk_grant_strategy",
    "bulk_revoke_strategy",
]

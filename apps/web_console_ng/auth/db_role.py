"""Fresh DB role verification for mutation callbacks.

WebSocket sessions (NiceGUI ``/_nicegui``, ``/socket.io``) bypass the auth
middleware role override, so ``app.storage.user`` may contain a stale role.
This helper queries the DB directly before every mutation, providing
defence-in-depth against privilege escalation via stale WebSocket sessions.

Fail-closed: returns ``False`` on any DB error (unlike middleware's
fail-open policy for read-only page loads).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import psycopg

from libs.platform.web_console_auth.permissions import Permission, has_permission

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# Timeout for DB role verification queries (seconds).
# Prevents mutation callbacks from hanging indefinitely during DB degradation.
_DB_ROLE_VERIFY_TIMEOUT_S = 5


async def verify_db_role(
    db_pool: AsyncConnectionPool, user_id: str, required_permission: Permission
) -> bool:
    """Check the user's *current* DB role for *required_permission*.

    Returns True if the user's current DB role has the required permission.
    Returns False on DB error or timeout (fail-closed for mutations, unlike
    middleware fail-open).
    """
    try:
        async with asyncio.timeout(_DB_ROLE_VERIFY_TIMEOUT_S):
            async with db_pool.connection() as conn:
                cursor = await conn.execute(
                    "SELECT role FROM user_roles WHERE user_id = %s", (user_id,)
                )
                row = await cursor.fetchone()
                if not row:
                    return False
                mock_user: dict[str, Any] = {"role": row[0]}
                return has_permission(mock_user, required_permission)
    except (TimeoutError, psycopg.Error, OSError) as exc:
        logger.warning("db_role_verify_failed", extra={"user_id": user_id, "error": str(exc)})
        return False  # Fail-closed for mutations

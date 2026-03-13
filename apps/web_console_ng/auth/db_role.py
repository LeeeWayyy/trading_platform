"""Fresh DB role verification for mutation callbacks.

WebSocket sessions (NiceGUI ``/_nicegui``, ``/socket.io``) bypass the auth
middleware role override, so ``app.storage.user`` may contain a stale role.
This helper queries the DB directly before every mutation, providing
defence-in-depth against privilege escalation via stale WebSocket sessions.

Fail-closed: returns ``False`` on any DB error (unlike middleware's
fail-open policy for read-only page loads).
"""

from __future__ import annotations

import logging
from typing import Any

from libs.platform.web_console_auth.permissions import Permission, has_permission

logger = logging.getLogger(__name__)


async def verify_db_role(
    db_pool: Any, user_id: str, required_permission: Permission
) -> bool:
    """Check the user's *current* DB role for *required_permission*.

    Returns True if the user's current DB role has the required permission.
    Returns False on DB error (fail-closed for mutations, unlike middleware
    fail-open).
    """
    try:
        async with db_pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT role FROM user_roles WHERE user_id = %s", (user_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return False
            mock_user: dict[str, Any] = {"role": row[0]}
            return has_permission(mock_user, required_permission)
    except Exception:
        logger.warning("db_role_verify_failed", extra={"user_id": user_id})
        return False  # Fail-closed for mutations

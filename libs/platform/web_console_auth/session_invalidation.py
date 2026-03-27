"""Session invalidation helpers.

P6T19: Simplified — session_version mechanism removed (single-admin model).
Functions kept for backward compatibility but are no-ops.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SessionInvalidationError(Exception):
    """Raised when a session invalidation operation cannot be completed."""


async def invalidate_user_sessions(
    user_id: str,
    db_pool: Any,
    audit_logger: Any | None = None,
    admin_user_id: str | None = None,
) -> int:
    """P6T19: No-op — session versioning removed. Returns 1 for compatibility."""

    logger.debug("invalidate_user_sessions called (no-op in single-admin model)", extra={"user_id": user_id})
    return 1


async def validate_session_version(user_id: str, session_version: int, db_pool: Any) -> bool:
    """P6T19: Always returns True — session versioning removed."""

    return True


__all__ = ["invalidate_user_sessions", "validate_session_version", "SessionInvalidationError"]

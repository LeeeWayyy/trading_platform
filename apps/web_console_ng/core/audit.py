"""Trading action audit logger wrapper.

NOTE: Backend already logs actions via AuditLogger in manual_controls.py.
This wrapper is for CLIENT-SIDE audit logging to complement backend logs.
For most actions, backend logging is sufficient.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


async def audit_log(
    action: str,
    user_id: str,
    details: dict[str, Any],
) -> None:
    """Log trading action for audit trail.

    This logs to the application logger. Backend already handles
    persistent audit logging to the database.

    Args:
        action: Action name (e.g., "order_submitted", "position_closed").
        user_id: User performing the action.
        details: Action-specific details.
    """
    logger.info(
        "trading_audit",
        extra={
            "action": action,
            "user_id": user_id,
            "details": details,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


__all__ = ["audit_log"]

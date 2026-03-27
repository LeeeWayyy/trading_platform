"""Unit tests for libs.platform.web_console_auth.session_invalidation.

P6T19: Rewritten for single-admin model — functions are no-ops.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from libs.platform.web_console_auth.session_invalidation import (
    SessionInvalidationError,
    invalidate_user_sessions,
    validate_session_version,
)


@pytest.mark.asyncio()
async def test_validate_session_version_always_returns_true() -> None:
    """validate_session_version() always returns True (no DB query)."""
    assert await validate_session_version("user-1", 2, db_pool=Mock()) is True
    assert await validate_session_version("user-1", 999, db_pool=None) is True


@pytest.mark.asyncio()
async def test_invalidate_user_sessions_returns_one() -> None:
    """invalidate_user_sessions() always returns 1 (no DB query)."""
    result = await invalidate_user_sessions("user-1", db_pool=Mock())
    assert result == 1


@pytest.mark.asyncio()
async def test_invalidate_user_sessions_with_audit_logger() -> None:
    """invalidate_user_sessions() returns 1 even with audit_logger (no-op)."""
    result = await invalidate_user_sessions(
        user_id="user-1",
        db_pool=Mock(),
        audit_logger=Mock(),
        admin_user_id="admin-1",
    )
    assert result == 1


def test_session_invalidation_error_exists() -> None:
    """SessionInvalidationError can still be imported and raised."""
    with pytest.raises(SessionInvalidationError):
        raise SessionInvalidationError("test")

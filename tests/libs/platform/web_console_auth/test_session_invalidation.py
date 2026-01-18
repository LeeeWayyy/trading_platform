"""Unit tests for libs.platform.web_console_auth.session_invalidation."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.platform.web_console_auth.session_invalidation import (
    SessionInvalidationError,
    _maybe_transaction,
    invalidate_user_sessions,
    validate_session_version,
)


@pytest.mark.asyncio
async def test_maybe_transaction_uses_transaction_when_available() -> None:
    mock_conn = Mock()
    txn_cm = AsyncMock()
    txn_cm.__aenter__ = AsyncMock()
    txn_cm.__aexit__ = AsyncMock()
    mock_conn.transaction = Mock(return_value=txn_cm)

    async with _maybe_transaction(mock_conn):
        pass

    mock_conn.transaction.assert_called_once()
    txn_cm.__aenter__.assert_called_once()
    txn_cm.__aexit__.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_transaction_noop_without_transaction_method() -> None:
    mock_conn = Mock(spec=[])

    async with _maybe_transaction(mock_conn):
        pass


@pytest.mark.asyncio
@patch("libs.platform.web_console_auth.session_invalidation.acquire_connection")
async def test_invalidate_user_sessions_success_with_audit_log(mock_acquire: Mock) -> None:
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=(7,))
    mock_conn.execute = AsyncMock(return_value=mock_cursor)
    mock_conn.transaction = Mock(return_value=AsyncMock())

    mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.return_value.__aexit__ = AsyncMock()

    audit_logger = AsyncMock()

    new_version = await invalidate_user_sessions(
        user_id="user-1",
        db_pool=Mock(),
        audit_logger=audit_logger,
        admin_user_id="admin-1",
    )

    assert new_version == 7
    audit_logger.log_admin_change.assert_awaited_once()


@pytest.mark.asyncio
@patch("libs.platform.web_console_auth.session_invalidation.acquire_connection")
async def test_invalidate_user_sessions_missing_row_raises(mock_acquire: Mock) -> None:
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.return_value.__aexit__ = AsyncMock()

    with pytest.raises(SessionInvalidationError):
        await invalidate_user_sessions("user-1", db_pool=Mock())


@pytest.mark.asyncio
@patch("libs.platform.web_console_auth.session_invalidation.acquire_connection")
async def test_validate_session_version_matches_tuple(mock_acquire: Mock) -> None:
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=(2,))
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.return_value.__aexit__ = AsyncMock()

    assert await validate_session_version("user-1", 2, db_pool=Mock()) is True


@pytest.mark.asyncio
@patch("libs.platform.web_console_auth.session_invalidation.acquire_connection")
async def test_validate_session_version_no_row_returns_false(mock_acquire: Mock) -> None:
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value=mock_cursor)

    mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.return_value.__aexit__ = AsyncMock()

    assert await validate_session_version("user-1", 2, db_pool=Mock()) is False

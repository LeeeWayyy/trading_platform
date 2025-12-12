"""Tests for user management service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


class MockAsyncCursor:
    """Helper to mock async cursor returned by conn.execute()."""

    def __init__(self, rows=None, single_row=None, rowcount=0):
        self._rows = rows or []
        self._single_row = single_row
        self.rowcount = rowcount

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._single_row


class MockAsyncContextManager:
    """Helper to mock async context manager returned by pool.connection()."""

    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, *_args):
        return None


class MockTransaction:
    """Helper to mock async transaction context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


from apps.web_console.services.user_management import (
    UserInfo,
    change_user_role,
    list_users,
)


class TestListUsers:
    """Tests for list_users function."""

    @pytest.mark.asyncio
    async def test_list_users_returns_user_info(self):
        """Test list_users returns UserInfo objects."""

        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)

        # psycopg3 pattern: execute returns cursor, cursor.fetchall returns tuples
        mock_cursor = MockAsyncCursor(
            rows=[
                ("user1", "admin", 1, datetime.now(UTC), "bootstrap", 2),
            ]
        )
        mock_conn.execute.return_value = mock_cursor

        users = await list_users(mock_pool)

        assert len(users) == 1
        assert users[0].user_id == "user1"
        assert users[0].role == "admin"
        assert users[0].strategy_count == 2


class TestChangeUserRole:
    """Tests for change_user_role function."""

    @pytest.mark.asyncio
    async def test_change_role_success(self):
        """Test successful role change."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: execute returns cursor, cursor.fetchone returns tuple
        mock_cursor = MockAsyncCursor(single_row=("viewer",))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        mock_audit = AsyncMock()

        success, msg = await change_user_role(
            db_pool=mock_pool,
            user_id="user1",
            new_role="operator",
            admin_user_id="admin1",
            audit_logger=mock_audit,
            reason="Promotion",
        )

        assert success is True
        assert "operator" in msg
        mock_audit.log_admin_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_change_role_invalid_role(self):
        """Test invalid role rejected."""

        mock_pool = MagicMock()
        mock_audit = AsyncMock()

        success, msg = await change_user_role(
            db_pool=mock_pool,
            user_id="user1",
            new_role="superadmin",  # Invalid
            admin_user_id="admin1",
            audit_logger=mock_audit,
            reason="Test",
        )

        assert success is False
        assert "Invalid role" in msg

    @pytest.mark.asyncio
    async def test_change_role_user_not_found(self):
        """Test user not found error."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: cursor.fetchone returns None for no rows
        mock_cursor = MockAsyncCursor(single_row=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        mock_audit = AsyncMock()

        success, msg = await change_user_role(
            db_pool=mock_pool,
            user_id="nonexistent",
            new_role="admin",
            admin_user_id="admin1",
            audit_logger=mock_audit,
            reason="Test",
        )

        assert success is False
        assert "not found" in msg

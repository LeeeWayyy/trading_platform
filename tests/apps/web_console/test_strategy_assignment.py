"""Tests for strategy assignment component and service functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.web_console.services.user_management import (
    grant_strategy,
    revoke_strategy,
)


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


class TestGrantStrategy:
    """Tests for grant_strategy function."""

    @pytest.mark.asyncio
    async def test_grant_strategy_success(self):
        """Test successful strategy grant."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: execute returns cursor
        # 1) SELECT strategy exists
        # 2) INSERT ... ON CONFLICT DO NOTHING (rowcount=1 when inserted)
        mock_cursor_exists = MockAsyncCursor(single_row=(1,))  # strategy exists
        mock_cursor_inserted = MockAsyncCursor(rowcount=1)  # insert succeeded
        mock_conn.execute = AsyncMock(side_effect=[
            mock_cursor_exists,   # SELECT strategy exists
            mock_cursor_inserted, # INSERT grant
        ])

        mock_audit = AsyncMock()

        success, msg = await grant_strategy(
            db_pool=mock_pool,
            user_id="user1",
            strategy_id="alpha_baseline",
            admin_user_id="admin1",
            audit_logger=mock_audit,
        )

        assert success is True
        assert "alpha_baseline" in msg
        mock_audit.log_admin_change.assert_called_once()
        assert mock_conn.execute.call_count == 2  # SELECT exists + INSERT

    @pytest.mark.asyncio
    async def test_grant_strategy_already_granted_denied(self):
        """[v1.2] Test already granted logs denied attempt."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: first call strategy exists, second call insert with rowcount=0
        mock_cursor_exists = MockAsyncCursor(single_row=(1,))  # strategy exists
        mock_cursor_conflict = MockAsyncCursor(rowcount=0)  # already granted (ON CONFLICT)
        mock_conn.execute = AsyncMock(side_effect=[mock_cursor_exists, mock_cursor_conflict])

        mock_audit = AsyncMock()

        success, msg = await grant_strategy(
            db_pool=mock_pool,
            user_id="user1",
            strategy_id="alpha_baseline",
            admin_user_id="admin1",
            audit_logger=mock_audit,
        )

        assert success is False
        assert "already granted" in msg
        mock_audit.log_action.assert_called_once()
        call_kwargs = mock_audit.log_action.call_args[1]
        assert call_kwargs["outcome"] == "denied"
        assert call_kwargs["action"] == "strategy_grant_denied"
        assert mock_conn.execute.call_count == 2  # SELECT exists + INSERT (conflict)


class TestRevokeStrategy:
    """Tests for revoke_strategy function."""

    @pytest.mark.asyncio
    async def test_revoke_strategy_success(self):
        """Test successful strategy revoke."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: execute returns cursor
        # First call: strategy exists check
        # Second call: DELETE with rowcount=1
        mock_cursor_exists = MockAsyncCursor(single_row=(1,))  # strategy exists
        mock_cursor_deleted = MockAsyncCursor(rowcount=1)  # row deleted
        mock_conn.execute = AsyncMock(side_effect=[
            mock_cursor_exists,  # SELECT strategy exists
            mock_cursor_deleted, # DELETE
        ])

        mock_audit = AsyncMock()

        success, msg = await revoke_strategy(
            db_pool=mock_pool,
            user_id="user1",
            strategy_id="alpha_baseline",
            admin_user_id="admin1",
            audit_logger=mock_audit,
        )

        assert success is True
        mock_audit.log_admin_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_strategy_not_assigned_denied(self):
        """[v1.2] Test revoke of non-assigned strategy logs denied."""

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        # Use connection() interface (psycopg_pool pattern)
        mock_pool.connection.return_value = MockAsyncContextManager(mock_conn)
        # Mock transaction() - it returns an async context manager
        mock_conn.transaction = MagicMock(return_value=MockTransaction())

        # psycopg3 pattern: execute returns cursor
        # First call: strategy exists check
        # Second call: DELETE with rowcount=0 (not assigned)
        mock_cursor_exists = MockAsyncCursor(single_row=(1,))  # strategy exists
        mock_cursor_not_deleted = MockAsyncCursor(rowcount=0)  # no rows deleted
        mock_conn.execute = AsyncMock(side_effect=[
            mock_cursor_exists,      # SELECT strategy exists
            mock_cursor_not_deleted, # DELETE (0 rows)
        ])

        mock_audit = AsyncMock()

        success, msg = await revoke_strategy(
            db_pool=mock_pool,
            user_id="user1",
            strategy_id="alpha_baseline",
            admin_user_id="admin1",
            audit_logger=mock_audit,
        )

        assert success is False
        mock_audit.log_action.assert_called_once()
        call_kwargs = mock_audit.log_action.call_args[1]
        assert call_kwargs["outcome"] == "denied"

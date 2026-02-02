"""
Unit tests for libs.web_console_services.user_management.

This test suite validates user management operations including:
- User listing and data conversion
- Role changes with validation and audit logging
- Strategy access grants and revokes
- Bulk operations
- Error handling and edge cases
- Database transaction behavior
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import psycopg
import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services import user_management
from libs.web_console_services.user_management import UserInfo


@pytest.fixture()
def mock_db_pool() -> Mock:
    return Mock()


@pytest.fixture()
def audit_logger() -> Mock:
    logger = Mock()
    logger.log_action = AsyncMock()
    logger.log_admin_change = AsyncMock()
    return logger


def _mock_acquire_connection(mock_conn: AsyncMock) -> AsyncMock:
    """Create a properly configured async context manager for connections."""
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None
    return mock_cm


def _setup_transaction_mock(mock_conn: AsyncMock) -> None:
    """Set up transaction context manager on mock connection."""
    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=None)
    mock_txn.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = Mock(return_value=mock_txn)


class TestRowToUserInfo:
    def test_row_mapping_dict(self) -> None:
        row = {
            "user_id": "u1",
            "role": "admin",
            "session_version": 3,
            "updated_at": "2025-01-01",
            "updated_by": "admin",
            "strategy_count": 2,
        }
        info = user_management._row_to_user_info(row)
        assert info == UserInfo(
            user_id="u1",
            role="admin",
            session_version=3,
            updated_at="2025-01-01",
            updated_by="admin",
            strategy_count=2,
        )

    def test_row_mapping_tuple(self) -> None:
        row = ("u2", "viewer", 1, "2025-01-02", None, 0)
        info = user_management._row_to_user_info(row)
        assert info.user_id == "u2"
        assert info.role == "viewer"
        assert info.session_version == 1
        assert info.updated_by is None
        assert info.strategy_count == 0


class TestListUsers:
    @pytest.mark.asyncio()
    async def test_list_users_maps_rows(self, mock_db_pool: Mock) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                ("u1", "admin", 1, "2025-01-01", "admin", 2),
                {
                    "user_id": "u2",
                    "role": "viewer",
                    "session_version": 2,
                    "updated_at": "2025-01-02",
                    "updated_by": None,
                    "strategy_count": 0,
                },
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            users = await user_management.list_users(mock_db_pool)

        assert [u.user_id for u in users] == ["u1", "u2"]
        assert users[0].strategy_count == 2
        assert users[1].role == "viewer"


class TestChangeUserRole:
    @pytest.mark.asyncio()
    async def test_change_user_role_invalid_role_logs_denied(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        success, msg = await user_management.change_user_role(
            mock_db_pool,
            user_id="user-1",
            new_role="invalid",
            admin_user_id="admin-1",
            audit_logger=audit_logger,
            reason="testing",
        )

        assert not success
        assert "Invalid role" in msg
        audit_logger.log_action.assert_awaited_once()
        call = audit_logger.log_action.await_args.kwargs
        assert call["action"] == "role_change_denied"
        assert call["details"]["reason"] == "invalid_role"

    @pytest.mark.asyncio()
    async def test_change_user_role_user_not_found(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.change_user_role(
                mock_db_pool,
                user_id="user-1",
                new_role=Role.ADMIN.value,
                admin_user_id="admin-1",
                audit_logger=audit_logger,
                reason="testing",
            )

        assert not success
        assert "User not found" in msg
        audit_logger.log_action.assert_awaited()
        assert audit_logger.log_admin_change.await_count == 0

    @pytest.mark.asyncio()
    async def test_change_user_role_no_change(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(Role.ADMIN.value,))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.change_user_role(
                mock_db_pool,
                user_id="user-1",
                new_role=Role.ADMIN.value,
                admin_user_id="admin-1",
                audit_logger=audit_logger,
                reason="testing",
            )

        assert not success
        assert "already has role" in msg
        audit_logger.log_action.assert_awaited()
        audit_logger.log_admin_change.assert_not_called()

    @pytest.mark.asyncio()
    async def test_change_user_role_success(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_cursor_select = AsyncMock()
        mock_cursor_select.fetchone = AsyncMock(return_value=(Role.VIEWER.value,))
        mock_cursor_update = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[mock_cursor_select, mock_cursor_update])

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.change_user_role(
                mock_db_pool,
                user_id="user-1",
                new_role=Role.ADMIN.value,
                admin_user_id="admin-1",
                audit_logger=audit_logger,
                reason="promotion",
            )

        assert success
        assert "Role changed" in msg
        audit_logger.log_admin_change.assert_awaited_once()


class TestStrategyGrants:
    @pytest.mark.asyncio()
    async def test_grant_strategy_missing_strategy(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.grant_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "does not exist" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_grant_strategy_already_granted(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)

        select_cursor = AsyncMock()
        select_cursor.fetchone = AsyncMock(return_value=(1,))
        insert_cursor = AsyncMock()
        insert_cursor.rowcount = 0

        mock_conn.execute = AsyncMock(side_effect=[select_cursor, insert_cursor])

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.grant_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "already granted" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_grant_strategy_success(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)

        select_cursor = AsyncMock()
        select_cursor.fetchone = AsyncMock(return_value=(1,))
        insert_cursor = AsyncMock()
        insert_cursor.rowcount = 1

        mock_conn.execute = AsyncMock(side_effect=[select_cursor, insert_cursor])

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.grant_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert success
        assert "Granted" in msg
        audit_logger.log_admin_change.assert_awaited_once()


class TestStrategyRevokes:
    @pytest.mark.asyncio()
    async def test_revoke_strategy_missing_strategy(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.revoke_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "does not exist" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_revoke_strategy_not_assigned(
        self, mock_db_pool: Mock, audit_logger: Mock
    ) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)

        select_cursor = AsyncMock()
        select_cursor.fetchone = AsyncMock(return_value=(1,))
        delete_cursor = AsyncMock()
        delete_cursor.rowcount = 0

        mock_conn.execute = AsyncMock(side_effect=[select_cursor, delete_cursor])

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.revoke_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "not assigned" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_revoke_strategy_success(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)

        select_cursor = AsyncMock()
        select_cursor.fetchone = AsyncMock(return_value=(1,))
        delete_cursor = AsyncMock()
        delete_cursor.rowcount = 1

        mock_conn.execute = AsyncMock(side_effect=[select_cursor, delete_cursor])

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.revoke_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="strat-1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert success
        assert "Revoked" in msg
        audit_logger.log_admin_change.assert_awaited_once()


class TestBulkOperations:
    @pytest.mark.asyncio()
    async def test_bulk_change_roles(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        with patch(
            "libs.web_console_services.user_management.change_user_role",
            new=AsyncMock(side_effect=[(True, "ok"), (False, "bad")]),
        ):
            results = await user_management.bulk_change_roles(
                mock_db_pool,
                user_ids=["u1", "u2"],
                new_role=Role.ADMIN.value,
                admin_user_id="admin-1",
                audit_logger=audit_logger,
                reason="bulk",
            )

        assert results == {"u1": (True, "ok"), "u2": (False, "bad")}

    @pytest.mark.asyncio()
    async def test_bulk_grant_strategy(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        with patch(
            "libs.web_console_services.user_management.grant_strategy",
            new=AsyncMock(side_effect=[(True, "ok")]),
        ):
            results = await user_management.bulk_grant_strategy(
                mock_db_pool,
                user_ids=["u1"],
                strategy_id="s1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert results == {"u1": (True, "ok")}

    @pytest.mark.asyncio()
    async def test_bulk_revoke_strategy(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        with patch(
            "libs.web_console_services.user_management.revoke_strategy",
            new=AsyncMock(side_effect=[(True, "ok")]),
        ):
            results = await user_management.bulk_revoke_strategy(
                mock_db_pool,
                user_ids=["u1"],
                strategy_id="s1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert results == {"u1": (True, "ok")}


class TestListStrategies:
    """Test list_strategies function."""

    @pytest.mark.asyncio()
    async def test_list_strategies_empty(self, mock_db_pool: Mock) -> None:
        """Test listing strategies when none exist."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            strategies = await user_management.list_strategies(mock_db_pool)

        assert strategies == []

    @pytest.mark.asyncio()
    async def test_list_strategies_with_data(self, mock_db_pool: Mock) -> None:
        """Test listing multiple strategies."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                ("s1", "Strategy 1", "Description 1"),
                ("s2", "Strategy 2", None),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            strategies = await user_management.list_strategies(mock_db_pool)

        assert len(strategies) == 2
        assert strategies[0].strategy_id == "s1"
        assert strategies[1].description is None


class TestGetUserStrategies:
    """Test get_user_strategies function."""

    @pytest.mark.asyncio()
    async def test_get_user_strategies_empty(self, mock_db_pool: Mock) -> None:
        """Test getting user strategies when none assigned."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            strategies = await user_management.get_user_strategies(mock_db_pool, "user-1")

        assert strategies == []

    @pytest.mark.asyncio()
    async def test_get_user_strategies_with_data(self, mock_db_pool: Mock) -> None:
        """Test getting user strategies with assignments."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[("s1",), ("s2",)])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            strategies = await user_management.get_user_strategies(mock_db_pool, "user-1")

        assert strategies == ["s1", "s2"]


class TestDatabaseErrors:
    """Test database error handling."""

    @pytest.mark.asyncio()
    async def test_change_role_database_error(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        """Test database error handling in change_user_role."""
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_conn.execute = AsyncMock(side_effect=psycopg.Error("DB error"))

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.change_user_role(
                mock_db_pool,
                user_id="user-1",
                new_role=Role.ADMIN.value,
                admin_user_id="admin-1",
                audit_logger=audit_logger,
                reason="testing",
            )

        assert not success
        assert "Database error" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_grant_strategy_exception(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        """Test exception handling in grant_strategy."""
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_conn.execute = AsyncMock(side_effect=Exception("Test error"))

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.grant_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="s1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "Error:" in msg
        audit_logger.log_action.assert_awaited()

    @pytest.mark.asyncio()
    async def test_revoke_strategy_exception(self, mock_db_pool: Mock, audit_logger: Mock) -> None:
        """Test exception handling in revoke_strategy."""
        mock_conn = AsyncMock()
        _setup_transaction_mock(mock_conn)
        mock_conn.execute = AsyncMock(side_effect=Exception("Test error"))

        with patch(
            "libs.web_console_services.user_management.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            success, msg = await user_management.revoke_strategy(
                mock_db_pool,
                user_id="user-1",
                strategy_id="s1",
                admin_user_id="admin-1",
                audit_logger=audit_logger,
            )

        assert not success
        assert "Error:" in msg
        audit_logger.log_action.assert_awaited()

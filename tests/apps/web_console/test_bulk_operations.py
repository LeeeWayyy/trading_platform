"""Tests for bulk operations service functions."""

from unittest.mock import AsyncMock, patch

import pytest

from apps.web_console.services.user_management import (
    bulk_change_roles,
    bulk_grant_strategy,
    bulk_revoke_strategy,
)


class TestBulkChangeRoles:
    """Tests for bulk_change_roles function."""

    @pytest.mark.asyncio()
    async def test_bulk_change_roles_multiple_users(self):
        """Test bulk role change for multiple users."""

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        with patch("apps.web_console.services.user_management.change_user_role") as mock_change:
            mock_change.return_value = (True, "Role changed")

            results = await bulk_change_roles(
                db_pool=mock_pool,
                user_ids=["user1", "user2", "user3"],
                new_role="operator",
                admin_user_id="admin1",
                audit_logger=mock_audit,
                reason="Bulk promotion",
            )

            assert len(results) == 3
            assert all(success for success, _ in results.values())
            assert mock_change.call_count == 3

    @pytest.mark.asyncio()
    async def test_bulk_change_roles_partial_failure(self):
        """Test bulk role change with some failures."""

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        with patch("apps.web_console.services.user_management.change_user_role") as mock_change:
            mock_change.side_effect = [
                (True, "Success"),
                (False, "User not found"),
                (True, "Success"),
            ]

            results = await bulk_change_roles(
                db_pool=mock_pool,
                user_ids=["user1", "user2", "user3"],
                new_role="operator",
                admin_user_id="admin1",
                audit_logger=mock_audit,
                reason="Test",
            )

            success_count = sum(1 for success, _ in results.values() if success)
            fail_count = len(results) - success_count

            assert success_count == 2
            assert fail_count == 1


class TestBulkStrategyOperations:
    """Tests for bulk strategy grant/revoke functions."""

    @pytest.mark.asyncio()
    async def test_bulk_grant_strategy(self):
        """Test bulk strategy grant."""

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        with patch("apps.web_console.services.user_management.grant_strategy") as mock_grant:
            mock_grant.return_value = (True, "Granted")

            results = await bulk_grant_strategy(
                db_pool=mock_pool,
                user_ids=["user1", "user2"],
                strategy_id="alpha_baseline",
                admin_user_id="admin1",
                audit_logger=mock_audit,
            )

            assert len(results) == 2
            assert all(success for success, _ in results.values())

    @pytest.mark.asyncio()
    async def test_bulk_revoke_strategy(self):
        """[v1.2] Test bulk strategy revoke."""

        mock_pool = AsyncMock()
        mock_audit = AsyncMock()

        with patch("apps.web_console.services.user_management.revoke_strategy") as mock_revoke:
            mock_revoke.return_value = (True, "Revoked")

            results = await bulk_revoke_strategy(
                db_pool=mock_pool,
                user_ids=["user1", "user2"],
                strategy_id="alpha_baseline",
                admin_user_id="admin1",
                audit_logger=mock_audit,
            )

            assert len(results) == 2
            assert all(success for success, _ in results.values())

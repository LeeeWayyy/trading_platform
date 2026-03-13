"""Unit tests for libs.web_console_services.strategy_service.

Tests cover:
- StrategyService initialization
- get_strategies with RBAC scoping (authorized strategies + VIEW_ALL_STRATEGIES)
- get_open_exposure admin-only gate
- toggle_strategy with admin-only gate and audit logging
- Activity status derivation (active/idle/unknown)
- Error handling paths
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.web_console_services.strategy_service import StrategyService


@pytest.fixture()
def mock_db_pool() -> Mock:
    """Create mock database pool."""
    return Mock()


@pytest.fixture()
def mock_audit_logger() -> Mock:
    """Create mock audit logger."""
    logger = Mock()
    logger.log_action = AsyncMock()
    return logger


@pytest.fixture()
def strategy_service(mock_db_pool: Mock, mock_audit_logger: Mock) -> StrategyService:
    """Create StrategyService instance."""
    return StrategyService(db_pool=mock_db_pool, audit_logger=mock_audit_logger)


@pytest.fixture()
def admin_user() -> dict[str, Any]:
    """Admin user with VIEW_ALL_STRATEGIES."""
    return {"user_id": "admin-123", "role": "admin", "strategies": []}


@pytest.fixture()
def operator_user() -> dict[str, Any]:
    """Operator user with specific strategy access."""
    return {
        "user_id": "operator-456",
        "role": "operator",
        "strategies": ["alpha_baseline", "alpha_v2"],
    }


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    """Viewer user with no strategy management permissions."""
    return {"user_id": "viewer-789", "role": "viewer"}


class TestStrategyServiceInit:
    """Tests for StrategyService initialization."""

    def test_init(self, mock_db_pool: Mock, mock_audit_logger: Mock) -> None:
        service = StrategyService(db_pool=mock_db_pool, audit_logger=mock_audit_logger)
        assert service.db_pool is mock_db_pool
        assert service.audit_logger is mock_audit_logger


class TestGetStrategies:
    """Tests for get_strategies with RBAC scoping."""

    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, strategy_service: StrategyService, viewer_user: dict[str, Any]
    ) -> None:
        """Viewer lacks MANAGE_STRATEGIES permission."""
        with pytest.raises(PermissionError, match="MANAGE_STRATEGIES"):
            await strategy_service.get_strategies(viewer_user)

    @pytest.mark.asyncio()
    async def test_operator_gets_scoped_strategies(
        self, strategy_service: StrategyService, operator_user: dict[str, Any]
    ) -> None:
        """Operator gets only their authorized strategies."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        # 7 columns: strategy_id, name, description, active, updated_at, updated_by, last_order_at
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                ("alpha_baseline", "Alpha Baseline", "Default strategy", True, None, None, None),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.strategy_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await strategy_service.get_strategies(operator_user)

        assert len(result) == 1
        assert result[0]["strategy_id"] == "alpha_baseline"
        assert result[0]["activity_status"] == "unknown"

    @pytest.mark.asyncio()
    async def test_admin_gets_all_strategies(
        self, strategy_service: StrategyService, admin_user: dict[str, Any]
    ) -> None:
        """Admin with VIEW_ALL_STRATEGIES gets all strategies."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        # 7 columns: strategy_id, name, description, active, updated_at, updated_by, last_order_at
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                ("alpha_baseline", "Alpha Baseline", "Default", True, None, None, None),
                ("alpha_v2", "Alpha V2", "Experimental", False, None, None, None),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.strategy_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await strategy_service.get_strategies(admin_user)

        assert len(result) == 2

    @pytest.mark.asyncio()
    async def test_no_authorized_strategies_returns_empty(
        self, strategy_service: StrategyService
    ) -> None:
        """Operator with no authorized strategies and no VIEW_ALL returns empty."""
        user = {"user_id": "op-1", "role": "operator", "strategies": []}
        # Operator has MANAGE_STRATEGIES but no strategies assigned and no VIEW_ALL
        with patch("libs.web_console_services.strategy_service.acquire_connection"):
            # Should not even reach DB
            result = await strategy_service.get_strategies(user)
        assert result == []


class TestToggleStrategy:
    """Tests for toggle_strategy with admin-only gate."""

    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, strategy_service: StrategyService, viewer_user: dict[str, Any]
    ) -> None:
        """Viewer lacks admin role."""
        with pytest.raises(PermissionError, match="Admin role required"):
            await strategy_service.toggle_strategy("alpha_baseline", active=False, user=viewer_user)

    @pytest.mark.asyncio()
    async def test_operator_denied(
        self, strategy_service: StrategyService, operator_user: dict[str, Any]
    ) -> None:
        """Operator lacks admin role — toggle is admin-only per ADR."""
        with pytest.raises(PermissionError, match="Admin role required"):
            await strategy_service.toggle_strategy(
                "alpha_baseline", active=False, user=operator_user
            )

    @pytest.mark.asyncio()
    async def test_toggle_deactivate_success(
        self,
        strategy_service: StrategyService,
        admin_user: dict[str, Any],
        mock_audit_logger: Mock,
    ) -> None:
        """Admin can deactivate a strategy."""
        mock_conn = AsyncMock()

        # First call: get current state
        mock_current = AsyncMock()
        mock_current.fetchone = AsyncMock(return_value=(True,))

        # Second call: update
        mock_update = AsyncMock()

        # Third call: get updated row
        mock_updated = AsyncMock()
        mock_updated.fetchone = AsyncMock(
            return_value=(
                "alpha_baseline",
                "Alpha Baseline",
                "Default",
                False,
                datetime.now(UTC),
                "admin-123",
            )
        )

        call_count = 0

        async def execute_side_effect(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_current
            if call_count == 2:
                return mock_update
            return mock_updated

        mock_conn.execute = AsyncMock(side_effect=execute_side_effect)

        with patch("libs.web_console_services.strategy_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await strategy_service.toggle_strategy(
                "alpha_baseline", active=False, user=admin_user
            )

        assert result["active"] is False
        assert result["strategy_id"] == "alpha_baseline"
        mock_audit_logger.log_action.assert_called_once()
        call_kwargs = mock_audit_logger.log_action.call_args.kwargs
        assert call_kwargs["action"] == "STRATEGY_TOGGLED"
        assert call_kwargs["details"]["previous_active"] is True
        assert call_kwargs["details"]["new_active"] is False

    @pytest.mark.asyncio()
    async def test_toggle_strategy_not_found(
        self, strategy_service: StrategyService, admin_user: dict[str, Any]
    ) -> None:
        """Toggle raises ValueError for missing strategy."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.strategy_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="not found"):
                await strategy_service.toggle_strategy("nonexistent", active=False, user=admin_user)


class TestGetOpenExposure:
    """Tests for get_open_exposure (admin-only)."""

    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, strategy_service: StrategyService, viewer_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="Admin role required"):
            await strategy_service.get_open_exposure("alpha_baseline", viewer_user)

    @pytest.mark.asyncio()
    async def test_operator_denied(
        self, strategy_service: StrategyService, operator_user: dict[str, Any]
    ) -> None:
        """Operator lacks admin role — exposure check is admin-only."""
        with pytest.raises(PermissionError, match="Admin role required"):
            await strategy_service.get_open_exposure("alpha_baseline", operator_user)

    @pytest.mark.asyncio()
    async def test_returns_exposure_data(
        self, strategy_service: StrategyService, admin_user: dict[str, Any]
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(3, 5))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch("libs.web_console_services.strategy_service.acquire_connection") as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await strategy_service.get_open_exposure("alpha_baseline", admin_user)

        assert result["positions_count"] == 3
        assert result["open_orders_count"] == 5

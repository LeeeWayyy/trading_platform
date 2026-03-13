"""Unit tests for libs.web_console_services.model_registry_browser_service.

Tests cover:
- ModelRegistryBrowserService initialization
- list_strategies_with_models with RBAC scoping
- get_models_for_strategy with RBAC
- activate_model admin-only gate + precheck + audit
- deactivate_model admin-only gate + audit
- validate_model graceful degradation
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.web_console_services.model_registry_browser_service import (
    ModelRegistryBrowserService,
)


@pytest.fixture()
def mock_db_pool() -> Mock:
    return Mock()


@pytest.fixture()
def mock_audit_logger() -> Mock:
    logger = Mock()
    logger.log_action = AsyncMock()
    return logger


@pytest.fixture()
def service(mock_db_pool: Mock, mock_audit_logger: Mock) -> ModelRegistryBrowserService:
    return ModelRegistryBrowserService(
        db_pool=mock_db_pool,
        audit_logger=mock_audit_logger,
        model_registry_url="http://localhost:8005",
        validate_token="test-token",
    )


@pytest.fixture()
def service_no_api(mock_db_pool: Mock, mock_audit_logger: Mock) -> ModelRegistryBrowserService:
    """Service without API credentials."""
    return ModelRegistryBrowserService(db_pool=mock_db_pool, audit_logger=mock_audit_logger)


@pytest.fixture()
def admin_user() -> dict[str, Any]:
    return {"user_id": "admin-1", "role": "admin", "strategies": []}


@pytest.fixture()
def operator_user() -> dict[str, Any]:
    return {
        "user_id": "op-1",
        "role": "operator",
        "strategies": ["alpha_baseline"],
    }


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    return {"user_id": "viewer-1", "role": "viewer"}


class TestListStrategiesWithModels:
    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, service: ModelRegistryBrowserService, viewer_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="VIEW_MODELS"):
            await service.list_strategies_with_models(viewer_user)

    @pytest.mark.asyncio()
    async def test_admin_gets_all(
        self, service: ModelRegistryBrowserService, admin_user: dict[str, Any]
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[("alpha_baseline",), ("alpha_v2",)])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.list_strategies_with_models(admin_user)

        assert len(result) == 2
        assert result[0]["strategy_name"] == "alpha_baseline"

    @pytest.mark.asyncio()
    async def test_operator_scoped(
        self, service: ModelRegistryBrowserService, operator_user: dict[str, Any]
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[("alpha_baseline",)])
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.list_strategies_with_models(operator_user)

        assert len(result) == 1


class TestGetModelsForStrategy:
    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, service: ModelRegistryBrowserService, viewer_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="VIEW_MODELS"):
            await service.get_models_for_strategy("alpha_baseline", viewer_user)

    @pytest.mark.asyncio()
    async def test_operator_unauthorized_strategy(
        self, service: ModelRegistryBrowserService, operator_user: dict[str, Any]
    ) -> None:
        """Operator cannot access strategies they're not authorized for."""
        with pytest.raises(PermissionError, match="Access denied"):
            await service.get_models_for_strategy("alpha_v2", operator_user)

    @pytest.mark.asyncio()
    async def test_returns_models(
        self, service: ModelRegistryBrowserService, admin_user: dict[str, Any]
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(
            return_value=[
                (
                    1,
                    "alpha_baseline",
                    "v1.0.0",
                    "/path/model.txt",
                    "active",
                    {"ic": 0.08},
                    {"lr": 0.05},
                    None,
                    None,
                    None,
                    "system",
                    "notes",
                ),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await service.get_models_for_strategy("alpha_baseline", admin_user)

        assert len(result) == 1
        assert result[0]["version"] == "v1.0.0"
        assert result[0]["status"] == "active"


class TestActivateModel:
    @pytest.mark.asyncio()
    async def test_non_admin_denied(
        self, service: ModelRegistryBrowserService, operator_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="Admin role required"):
            await service.activate_model("alpha_baseline", "v1.0.0", operator_user)

    @pytest.mark.asyncio()
    async def test_already_active_raises(
        self,
        service: ModelRegistryBrowserService,
        admin_user: dict[str, Any],
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=("active",))
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="already active"):
                await service.activate_model("alpha_baseline", "v1.0.0", admin_user)

    @pytest.mark.asyncio()
    async def test_not_found_raises(
        self, service: ModelRegistryBrowserService, admin_user: dict[str, Any]
    ) -> None:
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="not found"):
                await service.activate_model("alpha_baseline", "v999", admin_user)

    @pytest.mark.asyncio()
    async def test_activate_success(
        self,
        service: ModelRegistryBrowserService,
        admin_user: dict[str, Any],
        mock_audit_logger: Mock,
    ) -> None:
        mock_conn = AsyncMock()

        call_count = 0

        async def execute_side_effect(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            mock_cursor = AsyncMock()
            if call_count == 1:
                # Strategy-level lock (SELECT ... FOR UPDATE)
                mock_cursor.fetchall = AsyncMock(return_value=[])
            elif call_count == 2:
                # Status check for target version
                mock_cursor.fetchone = AsyncMock(return_value=("inactive",))
            else:
                # activate_model() DB function call
                mock_cursor.fetchone = AsyncMock(return_value=None)
            return mock_cursor

        mock_conn.execute = AsyncMock(side_effect=execute_side_effect)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await service.activate_model("alpha_baseline", "v2.0.0", admin_user)

        mock_audit_logger.log_action.assert_called_once()
        call_kwargs = mock_audit_logger.log_action.call_args.kwargs
        assert call_kwargs["action"] == "MODEL_ACTIVATED"
        assert call_kwargs["resource_id"] == "alpha_baseline/v2.0.0"


class TestDeactivateModel:
    @pytest.mark.asyncio()
    async def test_non_admin_denied(
        self, service: ModelRegistryBrowserService, operator_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="Admin role required"):
            await service.deactivate_model("alpha_baseline", "v1.0.0", operator_user)

    @pytest.mark.asyncio()
    async def test_deactivate_success(
        self,
        service: ModelRegistryBrowserService,
        admin_user: dict[str, Any],
        mock_audit_logger: Mock,
    ) -> None:
        mock_conn = AsyncMock()

        call_count = 0

        async def execute_side_effect(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            mock_cursor = AsyncMock()
            if call_count == 1:
                mock_cursor.fetchone = AsyncMock(return_value=("active",))
            return mock_cursor

        mock_conn.execute = AsyncMock(side_effect=execute_side_effect)

        with patch(
            "libs.web_console_services.model_registry_browser_service.acquire_connection"
        ) as mock_acquire:
            mock_acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            await service.deactivate_model("alpha_baseline", "v1.0.0", admin_user)

        mock_audit_logger.log_action.assert_called_once()
        assert mock_audit_logger.log_action.call_args.kwargs["action"] == "MODEL_DEACTIVATED"


class TestValidateModel:
    @pytest.mark.asyncio()
    async def test_no_api_returns_none(
        self, service_no_api: ModelRegistryBrowserService, admin_user: dict[str, Any]
    ) -> None:
        """Without API credentials, returns None (graceful degradation)."""
        result = await service_no_api.validate_model("alpha_baseline", "v1.0.0", admin_user)
        assert result is None

    @pytest.mark.asyncio()
    async def test_viewer_denied(
        self, service: ModelRegistryBrowserService, viewer_user: dict[str, Any]
    ) -> None:
        with pytest.raises(PermissionError, match="Admin role required"):
            await service.validate_model("alpha_baseline", "v1.0.0", viewer_user)

    @pytest.mark.asyncio()
    async def test_api_unavailable_returns_none(
        self, service: ModelRegistryBrowserService, admin_user: dict[str, Any]
    ) -> None:
        """API timeout returns None instead of raising."""
        import httpx

        with patch("libs.web_console_services.model_registry_browser_service.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.RequestError = httpx.RequestError

            result = await service.validate_model("alpha_baseline", "v1.0.0", admin_user)

        assert result is None
        # Verify per-send client lifecycle (context manager was used)
        mock_client.__aenter__.assert_called_once()
        mock_client.__aexit__.assert_called_once()

"""Tests for data sync service (non-rate-limit paths)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.data_sync_service import DataSyncService
from libs.web_console_services.schemas.data_management import SyncScheduleUpdateDTO


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def rate_limiter() -> AsyncMock:
    """Async rate limiter stub."""
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=(True, 1))
    return limiter


@pytest.fixture()
async def service(rate_limiter: AsyncMock) -> DataSyncService:
    """DataSyncService with mocked rate limiter."""
    return DataSyncService(rate_limiter=rate_limiter)


@pytest.fixture()
async def operator_user() -> DummyUser:
    """Operator user with partial dataset access."""
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def admin_user() -> DummyUser:
    """Admin user with full permissions."""
    return DummyUser(user_id="user-admin", role=Role.ADMIN)


@pytest.fixture()
async def viewer_user() -> DummyUser:
    """Viewer user with minimal permissions."""
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.mark.asyncio()
async def test_get_sync_status_filters_datasets(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    """Operator should only see datasets they can access."""
    statuses = await service.get_sync_status(operator_user)

    datasets = {status.dataset for status in statuses}
    # Operators have access to crsp, compustat, fama_french, and taq (P6T8: TCA)
    assert datasets == {"crsp", "compustat", "fama_french", "taq"}


@pytest.mark.asyncio()
async def test_update_sync_schedule_happy_path(
    service: DataSyncService, admin_user: DummyUser
) -> None:
    """Admin can update sync schedule for any dataset."""
    update = SyncScheduleUpdateDTO(enabled=False, cron_expression="0 3 * * *")

    result = await service.update_sync_schedule(admin_user, dataset="taq", schedule=update)

    assert result.dataset == "taq"
    assert result.enabled is False
    assert result.cron_expression == "0 3 * * *"


@pytest.mark.asyncio()
async def test_update_sync_schedule_denied_without_permission(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """Viewer lacks MANAGE_SYNC_SCHEDULE permission."""
    update = SyncScheduleUpdateDTO(enabled=True, cron_expression="0 1 * * *")

    with pytest.raises(PermissionError):
        await service.update_sync_schedule(viewer_user, dataset="fama_french", schedule=update)


@pytest.mark.asyncio()
async def test_update_sync_schedule_denied_without_dataset_access(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    """Operator lacks access to unlicensed datasets (default-deny)."""
    update = SyncScheduleUpdateDTO(enabled=True, cron_expression="0 1 * * *")

    with pytest.raises(PermissionError):
        # Use a dataset not in ROLE_DATASET_PERMISSIONS
        await service.update_sync_schedule(operator_user, dataset="proprietary_internal", schedule=update)

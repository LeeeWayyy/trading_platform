"""
Unit tests for libs.web_console_services.data_sync_service.

Coverage focus:
- Permission checks and dataset-level access filtering
- Rate limit enforcement for manual sync triggers
- Schedule update validation
- Delegation to rate limiter and helper utilities
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.data_sync_service import DataSyncService, RateLimitExceeded
from libs.web_console_services.schemas.data_management import SyncScheduleUpdateDTO


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.fixture()
def admin_user() -> DummyUser:
    return DummyUser(user_id="user-admin", role=Role.ADMIN)


@pytest.fixture()
def rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    return limiter


@pytest.fixture()
def service(rate_limiter: AsyncMock) -> DataSyncService:
    return DataSyncService(rate_limiter=rate_limiter)


@pytest.mark.asyncio()
async def test_get_sync_status_filters_by_dataset_permission(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    results = await service.get_sync_status(operator_user)

    datasets = {item.dataset for item in results}
    # Operators have access to crsp, compustat, fama_french, and taq (P6T8: TCA)
    assert datasets == {"crsp", "compustat", "fama_french", "taq"}


@pytest.mark.asyncio()
async def test_get_sync_status_researcher_allowed_single_admin(service: DataSyncService) -> None:
    """P6T19: Researcher can view sync status — single-admin model."""
    user = DummyUser(user_id="researcher-1", role=Role.RESEARCHER)

    result = await service.get_sync_status(user)
    assert result is not None


@pytest.mark.asyncio()
async def test_get_sync_logs_viewer_sees_all_datasets_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer sees all dataset logs — single-admin model."""
    results = await service.get_sync_logs(viewer_user, dataset=None, level=None, limit=100)

    datasets = {item.dataset for item in results}
    # Single-admin: has_dataset_permission always True, all datasets visible
    assert len(datasets) >= 1


@pytest.mark.asyncio()
async def test_get_sync_logs_viewer_can_access_any_dataset_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer can access any dataset logs — single-admin model."""
    results = await service.get_sync_logs(viewer_user, dataset="crsp", level=None, limit=100)
    assert isinstance(results, list)


@pytest.mark.asyncio()
async def test_get_sync_logs_limit_applied(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    results = await service.get_sync_logs(operator_user, dataset=None, level=None, limit=1)

    assert len(results) == 1


@pytest.mark.asyncio()
async def test_get_sync_schedule_viewer_sees_all_datasets_single_admin(
    service: DataSyncService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer sees all dataset schedules — single-admin model."""
    results = await service.get_sync_schedule(viewer_user)

    datasets = {item.dataset for item in results}
    # Single-admin: all datasets visible
    assert len(datasets) >= 1


@pytest.mark.asyncio()
async def test_update_sync_schedule_operator_can_update_any_single_admin(
    service: DataSyncService, operator_user: DummyUser
) -> None:
    """P6T19: Operator can update any dataset schedule — single-admin model."""
    schedule = SyncScheduleUpdateDTO(enabled=False, cron_expression="0 3 * * *")

    result = await service.update_sync_schedule(operator_user, dataset="crsp", schedule=schedule)
    assert result is not None


@pytest.mark.asyncio()
async def test_update_sync_schedule_success(
    service: DataSyncService, admin_user: DummyUser
) -> None:
    schedule = SyncScheduleUpdateDTO(enabled=False, cron_expression="15 5 * * *")

    result = await service.update_sync_schedule(admin_user, dataset="taq", schedule=schedule)

    assert result.dataset == "taq"
    assert result.enabled is False
    assert result.cron_expression == "15 5 * * *"


@pytest.mark.asyncio()
async def test_trigger_sync_success_calls_rate_limiter(
    rate_limiter: AsyncMock, operator_user: DummyUser
) -> None:
    service = DataSyncService(rate_limiter=rate_limiter)

    job = await service.trigger_sync(operator_user, dataset="crsp", reason="manual")

    assert job.dataset == "crsp"
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_trigger_sync_rate_limited_raises(
    operator_user: DummyUser,
) -> None:
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataSyncService(rate_limiter=rate_limiter)

    with pytest.raises(RateLimitExceeded):
        await service.trigger_sync(operator_user, dataset="crsp", reason="manual")


@pytest.mark.asyncio()
async def test_rate_limit_check_delegates(
    rate_limiter: AsyncMock, operator_user: DummyUser
) -> None:
    service = DataSyncService(rate_limiter=rate_limiter)

    allowed, remaining = await service._rate_limit_check(
        operator_user.user_id,
        action="trigger_data_sync",
        max_requests=2,
        window=60,
    )

    assert allowed is True
    assert remaining == 0
    rate_limiter.check_rate_limit.assert_awaited_once_with(
        user_id=operator_user.user_id,
        action="trigger_data_sync",
        max_requests=2,
        window_seconds=60,
    )


@pytest.mark.asyncio()
async def test_enforce_rate_limit_uses_user_id_lookup(
    rate_limiter: AsyncMock,
) -> None:
    service = DataSyncService(rate_limiter=rate_limiter)

    class UserObj:
        def __init__(self) -> None:
            self.user_id = "user-obj"
            self.role = Role.OPERATOR

    await service._enforce_rate_limit(
        UserObj(), action="trigger_data_sync", max_requests=1, window=60
    )

    rate_limiter.check_rate_limit.assert_awaited_once_with(
        user_id="user-obj",
        action="trigger_data_sync",
        max_requests=1,
        window_seconds=60,
    )

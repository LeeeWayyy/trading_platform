"""Tests for data sync rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from apps.web_console.services.data_sync_service import DataSyncService, RateLimitExceeded
from libs.platform.web_console_auth.permissions import Role


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.mark.asyncio()
async def test_trigger_sync_rate_limit_allows(operator_user: DummyUser) -> None:
    """Rate limiter allow path should enqueue sync job."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataSyncService(rate_limiter=rate_limiter)

    job = await service.trigger_sync(operator_user, dataset="crsp", reason="manual")

    assert job.dataset == "crsp"
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_trigger_sync_rate_limit_blocks(operator_user: DummyUser) -> None:
    """Rate limiter block path should raise RateLimitExceeded."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataSyncService(rate_limiter=rate_limiter)

    with pytest.raises(RateLimitExceeded):
        await service.trigger_sync(operator_user, dataset="crsp", reason="manual")


@pytest.mark.asyncio()
async def test_trigger_sync_permission_denied(viewer_user: DummyUser) -> None:
    """Viewer lacks TRIGGER_DATA_SYNC permission."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataSyncService(rate_limiter=rate_limiter)

    with pytest.raises(PermissionError):
        await service.trigger_sync(viewer_user, dataset="fama_french", reason="manual")


@pytest.mark.asyncio()
async def test_trigger_sync_dataset_access_denied(operator_user: DummyUser) -> None:
    """Operator lacks TAQ dataset access."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataSyncService(rate_limiter=rate_limiter)

    with pytest.raises(PermissionError):
        await service.trigger_sync(operator_user, dataset="taq", reason="manual")

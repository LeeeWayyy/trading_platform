"""Tests for data explorer service (non-rate-limit paths)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.data_explorer_service import DataExplorerService


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=(True, 1))
    return limiter


@pytest.fixture()
async def service(rate_limiter: AsyncMock) -> DataExplorerService:
    return DataExplorerService(rate_limiter=rate_limiter)


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.mark.asyncio()
async def test_list_datasets_filters_by_permissions(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """Operator should only see datasets they can access."""
    datasets = await service.list_datasets(operator_user)

    names = {item.name for item in datasets}
    # Operators have access to crsp, compustat, fama_french, and taq (P6T8: TCA)
    assert names == {"crsp", "compustat", "fama_french", "taq"}


@pytest.mark.asyncio()
async def test_execute_query_viewer_allowed_single_admin(
    service: DataExplorerService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer can execute queries — single-admin model."""
    result = await service.execute_query(viewer_user, dataset="fama_french", query="select 1")
    assert result is not None


@pytest.mark.asyncio()
async def test_export_data_any_dataset_allowed_single_admin(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """P6T19: All datasets accessible — single-admin model."""
    # Use crsp dataset with crsp_daily table (valid combination)
    result = await service.export_data(
        operator_user,
        dataset="crsp",
        query="select * from crsp_daily",
        format="csv",
    )
    assert result is not None


@pytest.mark.asyncio()
async def test_get_dataset_preview_limit_guard(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """Preview limit above 1000 rows should raise ValueError."""
    with pytest.raises(ValueError, match="limit.*1000"):
        await service.get_dataset_preview(operator_user, dataset="crsp", limit=1001)

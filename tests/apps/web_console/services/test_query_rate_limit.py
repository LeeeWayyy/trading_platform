"""Tests for dataset explorer rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services.data_explorer_service import DataExplorerService, RateLimitExceeded


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.mark.asyncio()
async def test_execute_query_rate_limit_allows(operator_user: DummyUser) -> None:
    """Allowed rate limit should return query results."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataExplorerService(rate_limiter=rate_limiter)

    result = await service.execute_query(operator_user, dataset="crsp", query="select 1")

    assert result.total_count == 0
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_execute_query_rate_limit_blocks(operator_user: DummyUser) -> None:
    """Blocked rate limit should raise RateLimitExceeded."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataExplorerService(rate_limiter=rate_limiter)

    with pytest.raises(RateLimitExceeded):
        await service.execute_query(operator_user, dataset="crsp", query="select 1")


@pytest.mark.asyncio()
async def test_export_data_rate_limit_allows(operator_user: DummyUser) -> None:
    """Allowed export rate limit should return export job."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataExplorerService(rate_limiter=rate_limiter)

    job = await service.export_data(
        operator_user,
        dataset="crsp",
        query="select * from crsp_daily",
        format="csv",
    )

    assert job.format == "csv"
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_export_data_rate_limit_blocks(operator_user: DummyUser) -> None:
    """Blocked export rate limit should raise RateLimitExceeded."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataExplorerService(rate_limiter=rate_limiter)

    with pytest.raises(RateLimitExceeded):
        await service.export_data(
            operator_user,
            dataset="crsp",
            query="select * from crsp_daily",
            format="parquet",
        )

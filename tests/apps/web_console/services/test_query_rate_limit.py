"""Tests for dataset explorer rate limiting."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services import sql_explorer_service as sql_module
from libs.web_console_services.data_explorer_service import DataExplorerService, RateLimitExceeded
from libs.web_console_services.sql_explorer_service import TablePathSpec


@dataclass(frozen=True)
class DummyUser:
    """Simple user stub for permission checks."""

    user_id: str
    role: Role


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def table_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, TablePathSpec]:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    return {"crsp_daily": str(partition)}


@pytest.fixture(autouse=True)
def no_sql_explorer_sandbox_probe() -> Generator[None, None, None]:
    with patch(
        "libs.web_console_services.data_explorer_service.ensure_sql_explorer_execution_allowed"
    ):
        yield


@pytest.mark.asyncio()
async def test_execute_query_rate_limit_allows(
    operator_user: DummyUser,
    table_paths: dict[str, TablePathSpec],
) -> None:
    """Allowed rate limit should return query results."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths=table_paths)

    result = await service.execute_query(
        operator_user,
        dataset="crsp",
        query="select * from crsp_daily",
    )

    assert result.total_count == 1
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_execute_query_rate_limit_blocks(
    operator_user: DummyUser,
    table_paths: dict[str, TablePathSpec],
) -> None:
    """Blocked rate limit should raise RateLimitExceeded."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths=table_paths)

    with pytest.raises(RateLimitExceeded):
        await service.execute_query(
            operator_user,
            dataset="crsp",
            query="select * from crsp_daily",
        )


@pytest.mark.asyncio()
async def test_export_data_rate_limit_allows(
    operator_user: DummyUser,
    table_paths: dict[str, TablePathSpec],
) -> None:
    """Allowed export rate limit should return export job."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(True, 0))
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths=table_paths)

    job = await service.export_data(
        operator_user,
        dataset="crsp",
        query="select * from crsp_daily",
        format="csv",
    )

    assert job.format == "csv"
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_export_data_rate_limit_blocks(
    operator_user: DummyUser,
    table_paths: dict[str, TablePathSpec],
) -> None:
    """Blocked export rate limit should raise RateLimitExceeded."""
    rate_limiter = AsyncMock()
    rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths=table_paths)

    with pytest.raises(RateLimitExceeded):
        await service.export_data(
            operator_user,
            dataset="crsp",
            query="select * from crsp_daily",
            format="parquet",
        )

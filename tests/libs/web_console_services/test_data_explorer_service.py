"""Unit tests for libs.web_console_services.data_explorer_service."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.web_console_services.data_explorer_service import DataExplorerService, RateLimitExceeded


@dataclass(frozen=True)
class DummyUser:
    user_id: str


@pytest.fixture()
def rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_rate_limit = AsyncMock(return_value=(True, 5))
    return limiter


@pytest.fixture()
def sql_validator() -> MagicMock:
    validator = MagicMock()
    validator.validate.return_value = (True, None)
    validator.enforce_row_limit.side_effect = lambda query, max_rows: f"{query} LIMIT {max_rows}"
    return validator


@pytest.fixture()
def service(rate_limiter: AsyncMock, sql_validator: MagicMock) -> DataExplorerService:
    return DataExplorerService(rate_limiter=rate_limiter, sql_validator=sql_validator)


@pytest.mark.asyncio()
async def test_list_datasets_filters_by_dataset_permission(
    service: DataExplorerService,
) -> None:
    def dataset_access(_user: DummyUser, dataset: str) -> bool:
        return dataset in {"crsp", "fama_french"}

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            side_effect=dataset_access,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    names = {item.name for item in datasets}
    assert names == {"crsp", "fama_french"}


@pytest.mark.asyncio()
async def test_get_dataset_preview_limit_guard(service: DataExplorerService) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="1000"):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=1001)


@pytest.mark.asyncio()
async def test_execute_query_invalid_sql_fails_before_rate_limit(
    rate_limiter: AsyncMock, sql_validator: MagicMock
) -> None:
    sql_validator.validate.return_value = (False, "bad sql")
    service = DataExplorerService(rate_limiter=rate_limiter, sql_validator=sql_validator)

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="Invalid query"):
            await service.execute_query(DummyUser(user_id="user-1"), "crsp", "select 1")

    rate_limiter.check_rate_limit.assert_not_awaited()


@pytest.mark.asyncio()
async def test_execute_query_rate_limit_exceeded(service: DataExplorerService) -> None:
    service._rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(RateLimitExceeded):
            await service.execute_query(DummyUser(user_id="user-1"), "crsp", "select 1")


@pytest.mark.asyncio()
async def test_execute_query_enforces_row_limit_and_calls_rate_limiter(
    service: DataExplorerService, sql_validator: MagicMock
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(DummyUser(user_id="user-1"), "crsp", "select 1")

    assert result.total_count == 0
    sql_validator.enforce_row_limit.assert_called_once_with("select 1", max_rows=10000)
    service._rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_export_data_returns_job(
    service: DataExplorerService, sql_validator: MagicMock
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        job = await service.export_data(
            DummyUser(user_id="user-1"),
            dataset="crsp",
            query="select * from crsp_daily",
            format="csv",
        )

    assert job.status == "queued"
    assert job.format == "csv"
    sql_validator.enforce_row_limit.assert_called_once_with(
        "select * from crsp_daily", max_rows=100000
    )
    service._rate_limiter.check_rate_limit.assert_awaited_once()

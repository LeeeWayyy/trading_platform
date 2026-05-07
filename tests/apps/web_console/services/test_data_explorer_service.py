"""Tests for data explorer service (non-rate-limit paths)."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from libs.platform.web_console_auth.permissions import Role
from libs.web_console_services import sql_explorer_service as sql_module
from libs.web_console_services.data_explorer_service import DataExplorerService
from libs.web_console_services.sql_explorer_service import TablePathSpec


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
async def table_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, TablePathSpec]:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    return {"crsp_daily": str(partition)}


@pytest.fixture()
async def service(
    rate_limiter: AsyncMock,
    table_paths: dict[str, TablePathSpec],
) -> DataExplorerService:
    return DataExplorerService(rate_limiter=rate_limiter, table_paths=table_paths)


@pytest.fixture()
async def operator_user() -> DummyUser:
    return DummyUser(user_id="user-operator", role=Role.OPERATOR)


@pytest.fixture()
async def viewer_user() -> DummyUser:
    return DummyUser(user_id="user-viewer", role=Role.VIEWER)


@pytest.fixture(autouse=True)
def no_sql_explorer_sandbox_probe() -> Generator[None, None, None]:
    with patch(
        "libs.web_console_services.data_explorer_service.ensure_sql_explorer_execution_allowed"
    ):
        yield


@pytest.mark.asyncio()
async def test_list_datasets_filters_by_permissions(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """Operator should only see datasets they can access."""
    datasets = await service.list_datasets(operator_user)

    names = {item.name for item in datasets}
    assert names == {"crsp", "compustat", "fama_french", "taq", "alpaca_sip"}


@pytest.mark.asyncio()
async def test_execute_query_viewer_allowed_single_admin(
    service: DataExplorerService, viewer_user: DummyUser
) -> None:
    """P6T19: Viewer can execute queries — single-admin model."""
    result = await service.execute_query(
        viewer_user,
        dataset="crsp",
        query="select * from crsp_daily",
    )
    assert result is not None


@pytest.mark.asyncio()
async def test_execute_query_rejects_tableless_table_function(
    rate_limiter: AsyncMock,
    viewer_user: DummyUser,
) -> None:
    """Table functions cannot use the tableless smoke-query path."""
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths={})

    with pytest.raises(ValueError, match="Tableless queries"):
        await service.execute_query(
            viewer_user,
            dataset="crsp",
            query="select count(*) from range(1000000)",
        )

    rate_limiter.check_rate_limit.assert_not_awaited()


@pytest.mark.asyncio()
async def test_export_data_any_dataset_allowed_single_admin(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """P6T19: All datasets accessible — single-admin model."""
    result = await service.export_data(
        operator_user,
        dataset="crsp",
        query="select * from crsp_daily",
        format="csv",
    )
    assert result is not None


@pytest.mark.asyncio()
async def test_execute_query_alpaca_sip_table_requires_trusted_manifest(
    tmp_path: Path,
    rate_limiter: AsyncMock,
    operator_user: DummyUser,
) -> None:
    partition = (
        tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "fallback" / "2026.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"alpaca_sip_daily": str(partition)},
    )

    with pytest.raises(ValueError, match="local data|trusted manifest"):
        await service.execute_query(
            operator_user,
            dataset="alpaca_sip",
            query="select * from alpaca_sip_daily",
        )


@pytest.mark.asyncio()
async def test_get_dataset_preview_limit_guard(
    service: DataExplorerService, operator_user: DummyUser
) -> None:
    """Preview limit above 1000 rows should raise ValueError."""
    with pytest.raises(ValueError, match="limit.*1000"):
        await service.get_dataset_preview(operator_user, dataset="crsp", limit=1001)

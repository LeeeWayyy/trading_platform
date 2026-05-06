"""Unit tests for libs.web_console_services.data_explorer_service."""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from libs.web_console_services import data_explorer_service as data_explorer_module
from libs.web_console_services import sql_explorer_service as sql_module
from libs.web_console_services.data_explorer_service import DataExplorerService, RateLimitExceeded
from libs.web_console_services.provider_signature import ProviderSignatureDTO
from libs.web_console_services.sql_validator import SQLValidator


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
    validator.extract_tables.return_value = ["crsp_daily"]
    validator.enforce_row_limit.side_effect = lambda query, max_rows: f"{query} LIMIT {max_rows}"
    return validator


@pytest.fixture()
def service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
    sql_validator: MagicMock,
) -> DataExplorerService:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    return DataExplorerService(
        rate_limiter=rate_limiter,
        sql_validator=sql_validator,
        table_paths={"crsp_daily": str(partition)},
    )


@pytest.fixture(autouse=True)
def no_sql_explorer_sandbox_probe() -> Generator[None, None, None]:
    with patch(
        "libs.web_console_services.data_explorer_service.ensure_sql_explorer_execution_allowed"
    ):
        yield


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


@pytest.mark.parametrize(
    ("limit", "message"),
    [
        (1001, "1000"),
        (0, "positive"),
    ],
)
@pytest.mark.asyncio()
async def test_get_dataset_preview_limit_guard(
    service: DataExplorerService,
    limit: int,
    message: str,
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match=message):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=limit)


@pytest.mark.asyncio()
async def test_get_dataset_preview_audits_rate_limit_failure(
    service: DataExplorerService,
) -> None:
    service._rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        with pytest.raises(RateLimitExceeded):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=5)

    assert audit.call_args.args[6] == "rate_limited"


@pytest.mark.asyncio()
async def test_get_dataset_preview_audits_permission_failure(
    service: DataExplorerService,
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=False),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        with pytest.raises(PermissionError):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=5)

    assert audit.call_args.args[6] == "authorization_denied"


@pytest.mark.asyncio()
async def test_get_dataset_preview_audits_table_selection_failure(
    rate_limiter: AsyncMock,
) -> None:
    service = DataExplorerService(rate_limiter=rate_limiter, table_paths={})

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        with pytest.raises(ValueError, match="No trusted local data"):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=5)

    assert audit.call_args.args[6] == "validation_error"


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
async def test_execute_query_tableless_literal_smoke_query_runs(
    rate_limiter: AsyncMock,
) -> None:
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        sql_validator=SQLValidator(),
        table_paths={},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "SELECT 1 AS ok",
        )

    assert result.rows == [{"ok": 1}]
    assert result.has_more is False
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_execute_query_max_rows_guard_fails_before_rate_limit(
    rate_limiter: AsyncMock,
    sql_validator: MagicMock,
) -> None:
    service = DataExplorerService(rate_limiter=rate_limiter, sql_validator=sql_validator)

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="row limit must be positive"):
            await service.execute_query(
                DummyUser(user_id="user-1"),
                "crsp",
                "select * from crsp_daily",
                max_rows=0,
            )

    rate_limiter.check_rate_limit.assert_not_awaited()
    sql_validator.validate.assert_not_called()


@pytest.mark.asyncio()
async def test_export_data_table_trust_fails_before_rate_limit(
    service: DataExplorerService,
) -> None:
    service._rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service._resolve_table_availability = AsyncMock(return_value=({}, []))  # type: ignore[method-assign]

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="not available|Trusted local data"):
            await service.export_data(
                DummyUser(user_id="user-1"),
                dataset="crsp",
                query="select * from crsp_daily",
                format="csv",
            )

    service._rate_limiter.check_rate_limit.assert_not_awaited()
    service._resolve_table_availability.assert_awaited_once()


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
async def test_execute_query_table_trust_fails_before_rate_limit(
    service: DataExplorerService,
) -> None:
    service._rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service._resolve_table_availability = AsyncMock(return_value=({}, []))  # type: ignore[method-assign]

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="not available|Trusted local data"):
            await service.execute_query(
                DummyUser(user_id="user-1"),
                "crsp",
                "select * from crsp_daily",
            )

    service._rate_limiter.check_rate_limit.assert_not_awaited()
    service._resolve_table_availability.assert_awaited_once()


@pytest.mark.asyncio()
async def test_get_dataset_preview_table_trust_fails_before_rate_limit(
    service: DataExplorerService,
) -> None:
    service._rate_limiter.check_rate_limit = AsyncMock(return_value=(False, 0))
    service._resolve_table_availability = AsyncMock(return_value=({}, []))  # type: ignore[method-assign]

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="No trusted local data"):
            await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=5)

    service._rate_limiter.check_rate_limit.assert_not_awaited()
    service._resolve_table_availability.assert_awaited_once()


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
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "select * from crsp_daily",
        )

    assert result.total_count == 1
    sql_validator.enforce_row_limit.assert_called_once_with(
        "select * from crsp_daily",
        max_rows=10001,
    )
    service._rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_execute_query_accepts_smaller_page_limit(
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
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "select * from crsp_daily",
            max_rows=500,
        )

    assert result.total_count == 1
    sql_validator.enforce_row_limit.assert_called_once_with(
        "select * from crsp_daily",
        max_rows=501,
    )


@pytest.mark.asyncio()
async def test_execute_query_allows_tableless_smoke_query(
    rate_limiter: AsyncMock,
    sql_validator: MagicMock,
) -> None:
    sql_validator.extract_tables.return_value = []
    sql_validator.enforce_row_limit.side_effect = lambda query, max_rows: query
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        sql_validator=sql_validator,
        table_paths={},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(DummyUser(user_id="user-1"), "crsp", "select 1")

    assert result.total_count == 1
    assert result.has_more is False
    rate_limiter.check_rate_limit.assert_awaited_once()


@pytest.mark.asyncio()
async def test_execute_query_rejects_tableless_table_function(
    rate_limiter: AsyncMock,
) -> None:
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        sql_validator=SQLValidator(),
        table_paths={},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="Tableless queries"):
            await service.execute_query(
                DummyUser(user_id="user-1"),
                "crsp",
                "select count(*) from range(1000000)",
            )

    rate_limiter.check_rate_limit.assert_not_awaited()


@pytest.mark.asyncio()
async def test_execute_query_rejects_cross_dataset_table_after_trust_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
    sql_validator: MagicMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "taq" / "trades" / "taq.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    sql_validator.extract_tables.return_value = ["taq_trades"]
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        sql_validator=sql_validator,
        table_paths={"taq_trades": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="taq_trades.*crsp"):
            await service.execute_query(
                DummyUser(user_id="user-1"),
                "crsp",
                "select * from taq_trades",
            )


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
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
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
    assert audit.call_args.args[6] == "queued"


@pytest.mark.asyncio()
async def test_export_data_audits_sensitive_table_block(
    service: DataExplorerService,
    sql_validator: MagicMock,
) -> None:
    sql_validator.extract_tables.return_value = ["users"]

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        with pytest.raises(sql_module.SensitiveTableAccessError):
            await service.export_data(
                DummyUser(user_id="user-1"),
                dataset="crsp",
                query="select * from users",
                format="csv",
            )

    assert audit.call_args.args[6] == "security_blocked"


@pytest.mark.asyncio()
async def test_export_data_blocks_alpaca_sip_fallback_without_manifest(
    tmp_path: Path,
    rate_limiter: AsyncMock,
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

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="fallback only"):
            await service.export_data(
                DummyUser(user_id="user-1"),
                dataset="alpaca_sip",
                query="SELECT * FROM alpaca_sip_daily",
                format="csv",
            )

    rate_limiter.check_rate_limit.assert_not_awaited()


@pytest.mark.asyncio()
async def test_get_dataset_preview_reads_manifest_pinned_alpaca_sip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "date": ["2026-01-02"],
            "symbol": ["AAPL"],
            "close": [187.25],
        }
    ).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"alpaca_sip_daily": (str(partition),)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            limit=1,
            table="alpaca_sip_daily",
        )

    assert preview.table == "alpaca_sip_daily"
    assert preview.trusted_manifest_backed is True
    assert preview.rows == [{"date": "2026-01-02", "symbol": "AAPL", "close": 187.25}]
    assert preview.sql_handoff_url is not None


@pytest.mark.asyncio()
async def test_get_dataset_preview_blocks_alpaca_sip_fallback_without_manifest(
    tmp_path: Path,
    rate_limiter: AsyncMock,
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

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="fallback only"):
            await service.get_dataset_preview(
                DummyUser(user_id="user-1"),
                "alpaca_sip",
                table="alpaca_sip_daily",
            )


@pytest.mark.asyncio()
async def test_get_dataset_preview_skips_fallback_when_trusted_table_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    fallback_partition = (
        data_root / "alpaca" / "sip" / "daily" / "snapshots" / "fallback" / "2026.parquet"
    )
    fallback_partition.parent.mkdir(parents=True)
    fallback_partition.write_bytes(b"PAR1")
    corp_partition = (
        data_root / "alpaca" / "sip" / "corp_actions" / "snapshots" / "sync-1" / "actions.parquet"
    )
    corp_partition.parent.mkdir(parents=True)
    pl.DataFrame({"ex_date": ["2026-01-02"], "symbol": ["AAPL"]}).write_parquet(corp_partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={
            "alpaca_sip_daily": str(fallback_partition),
            "alpaca_sip_corp_actions": (str(corp_partition),),
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            limit=1,
        )

    assert preview.table == "alpaca_sip_corp_actions"
    assert preview.trusted_manifest_backed is True


@pytest.mark.asyncio()
async def test_list_datasets_labels_alpaca_sip_fallback_only(
    tmp_path: Path,
    rate_limiter: AsyncMock,
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

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    alpaca = next(item for item in datasets if item.name == "alpaca_sip")
    assert alpaca.queryable_state == "queryable_fallback_only"
    assert alpaca.sql_handoff_url is None
    assert alpaca.availability_reason is not None


@pytest.mark.asyncio()
async def test_list_datasets_hides_untrusted_alpaca_manifest_summary(
    tmp_path: Path,
    rate_limiter: AsyncMock,
) -> None:
    partition = (
        tmp_path / "data" / "alpaca" / "sip" / "daily" / "snapshots" / "fallback" / "2026.parquet"
    )
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = SimpleNamespace(
        has_any_manifest=True,
        row_count=123,
        latest_sync=datetime(2026, 1, 3, tzinfo=UTC),
        manifests=[
            SimpleNamespace(
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 2),
            )
        ],
    )
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        manifest_service=manifest_service,
        table_paths={"alpaca_sip_daily": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    alpaca = next(item for item in datasets if item.name == "alpaca_sip")
    assert alpaca.queryable_state == "queryable_fallback_only"
    assert alpaca.row_count is None
    assert alpaca.date_range is None
    assert alpaca.last_sync is None


@pytest.mark.asyncio()
async def test_list_datasets_exposes_trusted_alpaca_manifest_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["AAPL"]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    latest_sync = datetime(2026, 1, 3, tzinfo=UTC)
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = SimpleNamespace(
        has_any_manifest=True,
        row_count=1,
        latest_sync=latest_sync,
        manifests=[
            SimpleNamespace(
                dataset="alpaca_sip_daily",
                validation_status="passed",
                sync_timestamp=latest_sync,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 2),
                row_count=1,
            )
        ],
    )
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        manifest_service=manifest_service,
        table_paths={"alpaca_sip_daily": (str(partition),)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    alpaca = next(item for item in datasets if item.name == "alpaca_sip")
    assert alpaca.queryable_state == "trusted_manifest_backed"
    assert alpaca.tables == ["alpaca_sip_daily"]
    assert alpaca.row_count == 1
    assert alpaca.date_range == {"start": "2026-01-02", "end": "2026-01-02"}
    assert alpaca.last_sync == latest_sync
    assert alpaca.sql_handoff_url is not None
    assert alpaca.query_templates


@pytest.mark.asyncio()
async def test_list_datasets_does_not_mark_invalid_manifest_path_queryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    failed_sync = datetime(2026, 1, 10, tzinfo=UTC)
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = SimpleNamespace(
        has_any_manifest=True,
        row_count=100,
        latest_sync=failed_sync,
        manifests=[
            SimpleNamespace(
                dataset="alpaca_sip_daily",
                validation_status="failed",
                sync_timestamp=failed_sync,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 10),
                row_count=100,
            )
        ],
    )
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        manifest_service=manifest_service,
        table_paths={
            "alpaca_sip_daily": sql_module.ResolvedTablePathSpec(
                path_spec=(str(partition),),
                manifest_backed=True,
                manifest_invalid=True,
            )
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    alpaca = next(item for item in datasets if item.name == "alpaca_sip")
    assert alpaca.queryable_state == "missing"
    assert alpaca.trusted_manifest_backed is False
    assert alpaca.tables == []
    assert alpaca.sql_handoff_url is None
    assert alpaca.row_count is None
    assert alpaca.availability_reason is not None
    assert "Trusted manifest is invalid" in alpaca.availability_reason


@pytest.mark.asyncio()
async def test_list_datasets_filters_alpaca_summary_to_trusted_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    corp_partition = (
        data_root / "alpaca" / "sip" / "corp_actions" / "snapshots" / "sync-1" / "actions.parquet"
    )
    corp_partition.parent.mkdir(parents=True)
    pl.DataFrame({"ex_date": ["2026-01-02"], "symbol": ["AAPL"]}).write_parquet(corp_partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    trusted_sync = datetime(2026, 1, 3, tzinfo=UTC)
    failed_sync = datetime(2026, 1, 10, tzinfo=UTC)
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.return_value = SimpleNamespace(
        has_any_manifest=True,
        row_count=105,
        latest_sync=failed_sync,
        manifests=[
            SimpleNamespace(
                dataset="alpaca_sip_daily",
                validation_status="failed",
                sync_timestamp=failed_sync,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 10),
                row_count=100,
            ),
            SimpleNamespace(
                dataset="alpaca_sip_corp_actions",
                validation_status="passed",
                sync_timestamp=trusted_sync,
                start_date=date(2026, 1, 2),
                end_date=date(2026, 1, 2),
                row_count=5,
            ),
        ],
    )
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        manifest_service=manifest_service,
        table_paths={
            "alpaca_sip_daily": sql_module.ResolvedTablePathSpec(
                path_spec=(),
                manifest_invalid=True,
            ),
            "alpaca_sip_corp_actions": sql_module.ResolvedTablePathSpec(
                path_spec=(str(corp_partition),),
                manifest_backed=True,
            ),
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    alpaca = next(item for item in datasets if item.name == "alpaca_sip")
    assert alpaca.tables == ["alpaca_sip_corp_actions"]
    assert alpaca.row_count == 5
    assert alpaca.date_range == {"start": "2026-01-02", "end": "2026-01-02"}
    assert alpaca.last_sync == trusted_sync
    assert alpaca.sql_handoff_url is not None
    assert "alpaca_sip_corp_actions" in alpaca.sql_handoff_url
    assert "alpaca_sip_daily" not in alpaca.sql_handoff_url
    assert [template.table for template in alpaca.query_templates] == [
        "alpaca_sip_corp_actions"
    ]


@pytest.mark.asyncio()
async def test_list_datasets_isolates_alpaca_manifest_summary_failure(
    rate_limiter: AsyncMock,
) -> None:
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.side_effect = RuntimeError("bad manifest")
    service = DataExplorerService(rate_limiter=rate_limiter, manifest_service=manifest_service)

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        datasets = await service.list_datasets(DummyUser(user_id="user-1"))

    assert {dataset.name for dataset in datasets} == {
        "crsp",
        "compustat",
        "taq",
        "fama_french",
        "alpaca_sip",
    }
    alpaca = next(dataset for dataset in datasets if dataset.name == "alpaca_sip")
    assert alpaca.availability_reason == (
        "Manifest summary temporarily unavailable; row count and date range may be incomplete"
    )


@pytest.mark.asyncio()
async def test_alpaca_manifest_summary_timeout_failure_cache_uses_fresh_timestamp(
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    manifest_service = MagicMock()
    manifest_service.get_alpaca_sip_summary.side_effect = (
        lambda: data_explorer_module.time.sleep(0.12)
    )
    service = DataExplorerService(rate_limiter=rate_limiter, manifest_service=manifest_service)
    monkeypatch.setattr(data_explorer_module, "_MANIFEST_SUMMARY_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(data_explorer_module, "_MANIFEST_SUMMARY_FAILURE_CACHE_TTL_SECONDS", 5.0)

    assert await service._get_alpaca_summary() == (None, True)
    assert await service._get_alpaca_summary() == (None, True)
    manifest_service.get_alpaca_sip_summary.assert_called_once_with()


@pytest.mark.asyncio()
async def test_get_dataset_preview_rejects_requested_table_outside_dataset(
    service: DataExplorerService,
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="taq_trades.*crsp"):
            await service.get_dataset_preview(
                DummyUser(user_id="user-1"),
                "crsp",
                table="taq_trades",
            )


@pytest.mark.asyncio()
async def test_get_dataset_preview_rejects_requested_table_without_local_data(
    service: DataExplorerService,
) -> None:
    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        with pytest.raises(ValueError, match="No local data available for table crsp_monthly"):
            await service.get_dataset_preview(
                DummyUser(user_id="user-1"),
                "crsp",
                table="crsp_monthly",
            )


@pytest.mark.asyncio()
async def test_execute_query_reads_trusted_local_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "date": ["2026-01-02"],
            "symbol": ["AAPL"],
            "close": [187.25],
        }
    ).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"alpaca_sip_daily": (str(partition),)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            "SELECT symbol, close FROM alpaca_sip_daily",
        )

    assert result.rows == [{"symbol": "AAPL", "close": 187.25}]
    assert result.execution_ms is not None
    assert result.fingerprint is not None


@pytest.mark.asyncio()
async def test_execute_query_only_registers_referenced_trusted_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    daily = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    monthly = data_root / "wrds" / "crsp" / "monthly" / "crsp.parquet"
    daily.parent.mkdir(parents=True)
    monthly.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(daily)
    monthly.write_text("not parquet", encoding="utf-8")
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={
            "crsp_daily": str(daily),
            "crsp_monthly": str(monthly),
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "SELECT * FROM crsp_daily",
        )

    assert result.rows == [{"id": 1}]


@pytest.mark.asyncio()
async def test_get_dataset_preview_only_registers_selected_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    daily = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    monthly = data_root / "wrds" / "crsp" / "monthly" / "crsp.parquet"
    daily.parent.mkdir(parents=True)
    monthly.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(daily)
    monthly.write_text("not parquet", encoding="utf-8")
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={
            "crsp_daily": str(daily),
            "crsp_monthly": str(monthly),
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        preview = await service.get_dataset_preview(DummyUser(user_id="user-1"), "crsp", limit=1)

    assert preview.table == "crsp_daily"
    assert preview.rows == [{"id": 1}]


@pytest.mark.asyncio()
async def test_preview_reuses_cached_trusted_manifest_paths_after_manifest_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    project_root = tmp_path
    data_root = project_root / "data"
    trusted_partition = (
        data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "trusted.parquet"
    )
    fallback_partition = (
        data_root / "alpaca" / "sip" / "daily" / "snapshots" / "fallback" / "fallback.parquet"
    )
    trusted_partition.parent.mkdir(parents=True)
    fallback_partition.parent.mkdir(parents=True)
    pl.DataFrame({"source": ["trusted"]}).write_parquet(trusted_partition)
    pl.DataFrame({"source": ["fallback"]}).write_parquet(fallback_partition)
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "alpaca_sip_daily.json"
    manifest_path.write_text(
        json.dumps({"file_paths": [str(trusted_partition)], "validation_status": "passed"}),
        encoding="utf-8",
    )
    sql_module._ALPACA_SIP_MANIFEST_PATH_CACHE.clear()
    monkeypatch.setattr(sql_module, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(rate_limiter=rate_limiter)

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
    ):
        first_preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            limit=10,
            table="alpaca_sip_daily",
        )
        manifest_path.unlink()
        second_preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            limit=10,
            table="alpaca_sip_daily",
        )

    assert first_preview.rows == [{"source": "trusted"}]
    assert second_preview.rows == [{"source": "trusted"}]


@pytest.mark.asyncio()
async def test_execute_query_trims_fetch_limit_and_sets_has_more(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": list(range(10001))}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"crsp_daily": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "SELECT * FROM crsp_daily",
        )

    assert len(result.rows) == 10000
    assert result.total_count == 10001
    assert result.has_more is True


@pytest.mark.asyncio()
async def test_execute_query_uses_sql_explorer_guard_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"crsp_daily": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch(
            "libs.web_console_services.data_explorer_service.ensure_sql_explorer_execution_allowed"
        ) as guard,
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        result = await service.execute_query(
            DummyUser(user_id="user-1"),
            "crsp",
            "SELECT * FROM crsp_daily",
        )

    assert result.total_count == 1
    guard.assert_called_once_with()
    audit.assert_called_once()


@pytest.mark.asyncio()
async def test_get_dataset_preview_uses_sql_explorer_guard_and_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"crsp_daily": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch(
            "libs.web_console_services.data_explorer_service.ensure_sql_explorer_execution_allowed"
        ) as guard,
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "crsp",
            limit=1,
        )

    assert preview.total_count == 1
    assert preview.has_more is False
    guard.assert_called_once_with()
    audit.assert_called_once()


@pytest.mark.asyncio()
async def test_get_dataset_preview_fetches_sentinel_and_sets_has_more(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "wrds" / "crsp" / "daily" / "crsp.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame({"id": [1, 2, 3]}).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"crsp_daily": str(partition)},
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
        patch("libs.web_console_services.data_explorer_service.log_sql_query_audit") as audit,
    ):
        preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "crsp",
            limit=2,
        )

    assert preview.rows == [{"id": 1}, {"id": 2}]
    assert preview.total_count == 3
    assert preview.has_more is True
    assert audit.call_args.args[4] == 2


@pytest.mark.asyncio()
async def test_get_dataset_preview_returns_alpaca_manifest_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["AAPL"],
            "date": [date(2026, 1, 2)],
            "close": [100.0],
            "adj_close": [None],
            "ret": [None],
        }
    ).write_parquet(partition)
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={"alpaca_sip_daily": (str(partition),)},
    )
    provider_signature = ProviderSignatureDTO(
        provider_id="alpaca_sip",
        source_feed="sip",
        adjustment_mode="raw",
        canonical_storage_mode="raw",
        read_time_adjustment_mode="unavailable",
        manifest_id="alpaca_sip_daily@v1:abc",
        manifest_reference="manifests://alpaca_sip_daily.json",
        manifest_checksum="abc",
    )
    service._get_alpaca_summary = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            SimpleNamespace(
                manifests=[
                    SimpleNamespace(
                        dataset="alpaca_sip_daily",
                        manifest_id="alpaca_sip_daily@v1:abc",
                        manifest_reference="manifests://alpaca_sip_daily.json",
                        manifest_checksum="abc",
                        manifest_version=1,
                        provider_id="alpaca_sip",
                        provider_version="1.0",
                        source_feed="sip",
                        adjustment_mode="raw",
                        canonical_storage_mode="raw",
                        read_time_adjustment_mode="unavailable",
                        provider_signature=provider_signature,
                    )
                ]
            ),
            False,
        )
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        preview = await service.get_dataset_preview(
            DummyUser(user_id="user-1"),
            "alpaca_sip",
            limit=5,
            table="alpaca_sip_daily",
        )

    assert preview.provider_id == "alpaca_sip"
    assert preview.provider_version == "1.0"
    assert preview.source_feed == "sip"
    assert preview.adjustment_mode == "raw"
    assert preview.canonical_storage_mode == "raw"
    assert preview.read_time_adjustment_mode == "unavailable"
    assert preview.manifest_id == "alpaca_sip_daily@v1:abc"
    assert preview.manifest_reference == "manifests://alpaca_sip_daily.json"
    assert preview.manifest_checksum == "abc"
    assert preview.provider_signature == provider_signature
    assert preview.null_column_reasons == {
        "adj_close": "raw_sip_returns_unavailable",
        "ret": "raw_sip_returns_unavailable",
    }
    assert preview.warnings == ["raw_sip_returns_unavailable"]


@pytest.mark.asyncio()
async def test_get_dataset_preview_requested_table_reports_invalid_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
) -> None:
    data_root = tmp_path / "data"
    partition = data_root / "alpaca" / "sip" / "daily" / "snapshots" / "sync-1" / "2026.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"PAR1")
    monkeypatch.setattr(sql_module, "_ALLOWED_DATA_ROOTS", [data_root.resolve()])
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={
            "alpaca_sip_daily": sql_module.ResolvedTablePathSpec(
                path_spec=(str(partition),),
                manifest_backed=True,
                manifest_invalid=True,
            )
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="Trusted manifest is invalid"):
            await service.get_dataset_preview(
                DummyUser(user_id="user-1"),
                "alpaca_sip",
                limit=5,
                table="alpaca_sip_daily",
            )


@pytest.mark.asyncio()
async def test_get_dataset_preview_requested_table_reports_unavailable_invalid_manifest(
    rate_limiter: AsyncMock,
) -> None:
    service = DataExplorerService(
        rate_limiter=rate_limiter,
        table_paths={
            "alpaca_sip_daily": sql_module.ResolvedTablePathSpec(
                path_spec=(),
                manifest_invalid=True,
            )
        },
    )

    with (
        patch("libs.web_console_services.data_explorer_service.has_permission", return_value=True),
        patch(
            "libs.web_console_services.data_explorer_service.has_dataset_permission",
            return_value=True,
        ),
        patch("libs.web_console_services.data_explorer_service.get_user_id", return_value="user-1"),
    ):
        with pytest.raises(ValueError, match="Trusted manifest is invalid"):
            await service.get_dataset_preview(
                DummyUser(user_id="user-1"),
                "alpaca_sip",
                limit=5,
                table="alpaca_sip_daily",
            )


def test_handoff_query_uses_explicit_dataset_default() -> None:
    query = data_explorer_module._handoff_query_for_dataset(
        "crsp",
        ["crsp_monthly", "crsp_daily"],
    )

    assert query == "SELECT * FROM crsp_daily LIMIT 100"

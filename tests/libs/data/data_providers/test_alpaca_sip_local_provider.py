"""Tests for Alpaca SIP local data provider and adapter."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.alpaca_sip_local_provider import (
    ALPACA_SIP_COLUMNS,
    ALPACA_SIP_SCHEMA,
    AlpacaSIPLocalProvider,
)
from libs.data.data_providers.protocols import (
    UNIFIED_COLUMNS,
    AlpacaSIPDataProviderAdapter,
    DataProvider,
    ProviderNotSupportedError,
)
from libs.data.data_quality.exceptions import DataNotFoundError
from libs.data.data_quality.manifest import ManifestManager


@pytest.fixture()
def mock_alpaca_sip_data(tmp_path: Path) -> tuple[Path, ManifestManager, list[Path]]:
    """Create local Alpaca SIP parquet files and manifest for tests."""
    data_root = tmp_path / "data"
    sip_dir = data_root / "alpaca" / "sip" / "daily"
    sip_dir.mkdir(parents=True)

    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True)
    lock_dir = data_root / "locks"
    lock_dir.mkdir(parents=True)

    file_paths: list[Path] = []
    data_2023 = pl.DataFrame(
        {
            "date": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 3)],
            "symbol": ["AAPL", "AAPL", "MSFT"],
            "open": [100.0, 102.0, 200.0],
            "high": [101.0, 103.0, 202.0],
            "low": [99.0, 101.0, 198.0],
            "close": [100.0, 101.0, 201.0],
            "volume": [1_000_000.0, 1_100_000.0, 900_000.0],
            "trade_count": [10_000.0, 11_000.0, 9_000.0],
            "vwap": [100.1, 101.2, 200.5],
            "adj_close": [100.0, 102.0, 201.0],
            "ret": [None, None, None],
        },
        schema={column: ALPACA_SIP_SCHEMA[column] for column in ALPACA_SIP_COLUMNS},
    )
    path_2023 = sip_dir / "2023.parquet"
    data_2023.write_parquet(path_2023)
    file_paths.append(path_2023)

    data_2024 = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)],
            "symbol": ["AAPL"],
            "open": [150.0],
            "high": [151.0],
            "low": [149.0],
            "close": [150.5],
            "volume": [1_500_000.0],
            "trade_count": [15_000.0],
            "vwap": [150.25],
            "adj_close": [150.5],
            "ret": [0.01],
        },
        schema={column: ALPACA_SIP_SCHEMA[column] for column in ALPACA_SIP_COLUMNS},
    )
    path_2024 = sip_dir / "2024.parquet"
    data_2024.write_parquet(path_2024)
    file_paths.append(path_2024)

    manifest_manager = ManifestManager(
        storage_path=manifest_dir,
        lock_dir=lock_dir,
        data_root=data_root,
    )
    manifest_data = {
        "dataset": "alpaca_sip_daily",
        "sync_timestamp": datetime.now(UTC).isoformat(),
        "start_date": "2023-01-03",
        "end_date": "2024-01-02",
        "row_count": 4,
        "checksum": "abc123",
        "checksum_algorithm": "sha256",
        "schema_version": "v1.0.0",
        "wrds_query_hash": "alpaca-sip-local-test",
        "file_paths": [str(path) for path in file_paths],
        "validation_status": "passed",
        "manifest_version": 1,
    }
    with open(manifest_dir / "alpaca_sip_daily.json", "w") as f:
        json.dump(manifest_data, f)

    return data_root, manifest_manager, file_paths


class TestAlpacaSIPLocalProvider:
    """Tests for the local DuckDB-backed provider."""

    def test_init_valid_path(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        storage_path = data_root / "alpaca" / "sip" / "daily"

        provider = AlpacaSIPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        assert provider.storage_path == storage_path.resolve()

    def test_duckdb_connection_settings_are_configurable(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data

        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
            duckdb_memory_limit="512MB",
            duckdb_threads=2,
        )

        assert provider._duckdb_memory_limit == "512MB"
        assert provider._duckdb_threads == 2
        provider.close()

    def test_stale_thread_connection_closed_on_generation_bump(
        self,
        mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )
        stale_conn = MagicMock()
        fresh_conn = MagicMock()
        connections = [stale_conn, fresh_conn]

        def new_connection() -> Any:
            return connections.pop(0)

        monkeypatch.setattr(provider, "_new_connection", new_connection)

        assert provider._connection_for_current_thread() is stale_conn
        provider._connection_generation += 1
        assert provider._connection_for_current_thread() is fresh_conn
        stale_conn.close.assert_called_once()

    def test_invalid_duckdb_connection_settings_raise(
        self,
        mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        monkeypatch.setenv("ALPACA_SIP_DUCKDB_MEMORY_LIMIT", "2GB'; drop table x; --")

        with pytest.raises(ValueError, match="duckdb_memory_limit"):
            AlpacaSIPLocalProvider(
                storage_path=data_root / "alpaca" / "sip" / "daily",
                manifest_manager=manifest_manager,
                data_root=data_root,
            )

        with pytest.raises(ValueError, match="duckdb_threads"):
            AlpacaSIPLocalProvider(
                storage_path=data_root / "alpaca" / "sip" / "daily",
                manifest_manager=manifest_manager,
                data_root=data_root,
                duckdb_memory_limit="2GB",
                duckdb_threads=0,
            )

    def test_path_outside_data_root_rejected(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        data_root.mkdir()
        outside_path = tmp_path / "outside"
        outside_path.mkdir()

        with pytest.raises(ValueError, match="must be within data_root"):
            AlpacaSIPLocalProvider(
                storage_path=outside_path,
                manifest_manager=MagicMock(spec=ManifestManager),
                data_root=data_root,
            )

    def test_get_daily_prices_filters_by_symbol_and_date(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        df = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            symbols=["aapl"],
        )

        assert df["symbol"].to_list() == ["AAPL", "AAPL"]
        assert df["date"].to_list() == [date(2023, 1, 3), date(2023, 1, 4)]

    def test_get_daily_prices_column_projection(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        df = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            columns=["date", "symbol", "close"],
        )

        assert df.columns == ["date", "symbol", "close"]

    def test_relative_manifest_path_resolved_against_storage_path(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        manifest_path = data_root / "manifests" / "alpaca_sip_daily.json"
        manifest_data = json.loads(manifest_path.read_text())
        manifest_data["file_paths"] = ["2023.parquet"]
        manifest_path.write_text(json.dumps(manifest_data))

        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        df = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            symbols=["AAPL"],
        )

        assert df.height == 2

    def test_data_root_relative_manifest_path_resolved(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        manifest_path = data_root / "manifests" / "alpaca_sip_daily.json"
        manifest_data = json.loads(manifest_path.read_text())
        manifest_data["file_paths"] = ["alpaca/sip/daily/2023.parquet"]
        manifest_path.write_text(json.dumps(manifest_data))

        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        df = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            symbols=["AAPL"],
        )

        assert df.height == 2

    def test_pinned_manifest_used_even_when_current_manifest_changes(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        pinned_manifest = manifest_manager.load_manifest("alpaca_sip_daily")
        assert pinned_manifest is not None
        manifest_path = data_root / "manifests" / "alpaca_sip_daily.json"
        manifest_data = json.loads(manifest_path.read_text())
        manifest_data["manifest_version"] = 2
        manifest_data["file_paths"] = [str(data_root / "alpaca" / "sip" / "daily" / "2099.parquet")]
        manifest_path.write_text(json.dumps(manifest_data))

        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
            pinned_manifest=pinned_manifest,
        )

        df = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2023, 1, 4),
            symbols=["AAPL"],
        )

        assert df.height == 2

    def test_invalid_column_raises(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        with pytest.raises(ValueError, match="Invalid columns"):
            provider.get_daily_prices(
                start_date=date(2023, 1, 3),
                end_date=date(2023, 1, 4),
                columns=["date", "not_a_column"],
            )

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        storage_path = data_root / "alpaca" / "sip" / "daily"
        storage_path.mkdir(parents=True)
        manifest_manager = ManifestManager(
            storage_path=data_root / "manifests",
            lock_dir=data_root / "locks",
            data_root=data_root,
        )
        provider = AlpacaSIPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        with pytest.raises(DataNotFoundError, match="alpaca_sip_daily"):
            provider.get_daily_prices(date(2023, 1, 3), date(2023, 1, 4))

    def test_manifest_path_outside_storage_path_skipped(self, tmp_path: Path) -> None:
        data_root = tmp_path / "data"
        storage_path = data_root / "alpaca" / "sip" / "daily"
        storage_path.mkdir(parents=True)
        manifest_dir = data_root / "manifests"
        manifest_dir.mkdir(parents=True)
        lock_dir = data_root / "locks"
        lock_dir.mkdir(parents=True)

        outside_storage = data_root / "other" / "2023.parquet"
        outside_storage.parent.mkdir(parents=True)
        pl.DataFrame(
            {
                "date": [date(2023, 1, 3)],
                "symbol": ["AAPL"],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.0],
                "volume": [1_000_000.0],
            }
        ).write_parquet(outside_storage)

        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )
        manifest_data = {
            "dataset": "alpaca_sip_daily",
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "start_date": "2023-01-03",
            "end_date": "2023-01-03",
            "row_count": 1,
            "checksum": "abc123",
            "checksum_algorithm": "sha256",
            "schema_version": "v1.0.0",
            "wrds_query_hash": "alpaca-sip-local-test",
            "file_paths": [str(outside_storage)],
            "validation_status": "passed",
            "manifest_version": 1,
        }
        with open(manifest_dir / "alpaca_sip_daily.json", "w") as f:
            json.dump(manifest_data, f)

        provider = AlpacaSIPLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        result = provider.get_daily_prices(date(2023, 1, 3), date(2023, 1, 3))

        assert result.is_empty()

    def test_close_invalidates_cached_connection(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        first_result = provider.get_daily_prices(date(2023, 1, 3), date(2023, 1, 4))
        first_connection = provider._connection_for_current_thread()
        provider.close()
        second_result = provider.get_daily_prices(date(2023, 1, 3), date(2023, 1, 4))
        second_connection = provider._connection_for_current_thread()

        assert first_result.height == 3
        assert second_result.height == 3
        assert first_connection is not second_connection

    def test_close_allows_queries_from_multiple_threads(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        def query_symbol(symbol: str) -> tuple[str, int]:
            df = provider.get_daily_prices(
                start_date=date(2023, 1, 3),
                end_date=date(2024, 1, 2),
                symbols=[symbol],
            )
            return symbol, df.height

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = dict(executor.map(query_symbol, ["AAPL", "MSFT"]))

        provider.close()
        after_close = provider.get_daily_prices(
            start_date=date(2023, 1, 3),
            end_date=date(2024, 1, 2),
            symbols=["AAPL"],
        )

        assert results == {"AAPL": 3, "MSFT": 1}
        assert after_close.height == 3


class TestAlpacaSIPDataProviderAdapter:
    """Tests for unified-schema adapter behavior."""

    def test_adapter_is_data_provider(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )

        adapter = AlpacaSIPDataProviderAdapter(provider)

        assert isinstance(adapter, DataProvider)
        assert adapter.name == "alpaca_sip"
        assert adapter.supports_universe is False
        assert adapter.is_production_ready is False

    def test_adapter_returns_unified_schema_and_adjusted_returns(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )
        adapter = AlpacaSIPDataProviderAdapter(provider)

        df = adapter.get_daily_prices(["AAPL"], date(2023, 1, 3), date(2023, 1, 4))

        assert df.columns == UNIFIED_COLUMNS
        assert df["symbol"].to_list() == ["AAPL", "AAPL"]
        assert df["ret"].to_list()[0] is None
        assert df["ret"].to_list()[1] == pytest.approx(0.02)
        assert df["close"].to_list() == [100.0, 101.0]
        assert df["adj_close"].to_list() == [100.0, 102.0]

    def test_adapter_keeps_raw_snapshot_returns_null(self) -> None:
        provider = MagicMock()
        provider.get_daily_prices.return_value = pl.DataFrame(
            {
                "date": [date(2023, 1, 3), date(2023, 1, 4)],
                "symbol": ["AAPL", "AAPL"],
                "open": [100.0, 102.0],
                "high": [101.0, 103.0],
                "low": [99.0, 101.0],
                "close": [100.0, 101.0],
                "volume": [1_000_000.0, 1_100_000.0],
                "adj_close": [None, None],
                "ret": [None, None],
            }
        )
        adapter = AlpacaSIPDataProviderAdapter(provider)

        df = adapter.get_daily_prices(["AAPL"], date(2023, 1, 3), date(2023, 1, 4))

        assert df["ret"].to_list() == [None, None]

    def test_adapter_derives_split_adjusted_returns_from_corp_actions_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        data_root = tmp_path / "data"
        daily_dir = data_root / "alpaca" / "sip" / "daily"
        corp_dir = data_root / "alpaca" / "sip" / "corp_actions"
        manifest_dir = data_root / "manifests"
        lock_dir = data_root / "locks"
        daily_dir.mkdir(parents=True)
        corp_dir.mkdir(parents=True)
        manifest_dir.mkdir(parents=True)
        lock_dir.mkdir(parents=True)
        daily_path = daily_dir / "2020.parquet"
        corp_path = corp_dir / "actions.parquet"
        pl.DataFrame(
            {
                "date": [date(2020, 8, 28), date(2020, 8, 31), date(2020, 9, 1)],
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "open": [500.0, 125.0, 130.0],
                "high": [504.0, 126.0, 132.0],
                "low": [496.0, 124.0, 129.0],
                "close": [500.0, 125.0, 130.0],
                "volume": [1_000_000.0, 4_000_000.0, 3_800_000.0],
                "trade_count": [10_000.0, 11_000.0, 12_000.0],
                "vwap": [501.0, 125.5, 130.5],
                "adj_close": [None, None, None],
                "ret": [None, None, None],
            },
            schema={column: ALPACA_SIP_SCHEMA[column] for column in ALPACA_SIP_COLUMNS},
        ).write_parquet(daily_path)
        pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "ca_type": ["stock_split"],
                "process_date": [date(2020, 8, 30)],
                "ex_date": [date(2020, 8, 31)],
                "old_rate": [1.0],
                "new_rate": [4.0],
            }
        ).write_parquet(corp_path)
        manifest_base = {
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "checksum": "abc123",
            "checksum_algorithm": "sha256",
            "schema_version": "v1.0.0",
            "wrds_query_hash": "alpaca-sip-local-test",
            "validation_status": "passed",
            "manifest_version": 1,
        }
        (manifest_dir / "alpaca_sip_daily.json").write_text(
            json.dumps(
                {
                    **manifest_base,
                    "dataset": "alpaca_sip_daily",
                    "start_date": "2020-08-28",
                    "end_date": "2020-09-01",
                    "row_count": 3,
                    "file_paths": [str(daily_path)],
                }
            ),
            encoding="utf-8",
        )
        (manifest_dir / "alpaca_sip_corp_actions.json").write_text(
            json.dumps(
                {
                    **manifest_base,
                    "dataset": "alpaca_sip_corp_actions",
                    "start_date": "2020-08-28",
                    "end_date": "2020-09-01",
                    "row_count": 1,
                    "file_paths": [str(corp_path)],
                }
            ),
            encoding="utf-8",
        )
        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=lock_dir,
            data_root=data_root,
        )
        provider = AlpacaSIPLocalProvider(
            storage_path=daily_dir,
            manifest_manager=manifest_manager,
            data_root=data_root,
        )
        adapter = AlpacaSIPDataProviderAdapter(provider)

        df = adapter.get_daily_prices(["AAPL"], date(2020, 8, 28), date(2020, 9, 1))

        assert df["close"].to_list() == [500.0, 125.0, 130.0]
        assert df["adj_close"].to_list() == [125.0, 125.0, 130.0]
        assert df["ret"].to_list()[0] is None
        assert df["ret"].to_list()[1:] == pytest.approx([0.0, 0.04])

    def test_adapter_rejects_empty_symbols(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )
        adapter = AlpacaSIPDataProviderAdapter(provider)

        with pytest.raises(ValueError, match="symbols list cannot be empty"):
            adapter.get_daily_prices([], date(2023, 1, 3), date(2023, 1, 4))

    def test_adapter_get_universe_raises(
        self, mock_alpaca_sip_data: tuple[Path, ManifestManager, list[Path]]
    ) -> None:
        data_root, manifest_manager, _ = mock_alpaca_sip_data
        provider = AlpacaSIPLocalProvider(
            storage_path=data_root / "alpaca" / "sip" / "daily",
            manifest_manager=manifest_manager,
            data_root=data_root,
        )
        adapter = AlpacaSIPDataProviderAdapter(provider)

        with pytest.raises(ProviderNotSupportedError) as exc_info:
            adapter.get_universe(date(2023, 1, 3))

        assert exc_info.value.provider_name == "alpaca_sip"
        assert exc_info.value.operation == "get_universe"

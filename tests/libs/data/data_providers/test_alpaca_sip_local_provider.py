"""Tests for Alpaca SIP local data provider and adapter."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
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
            "symbol": ["aapl", "AAPL", "MSFT"],
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

        assert df["symbol"].to_list() == ["aapl", "AAPL"]
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

    def test_adapter_returns_unified_schema_and_derived_returns(
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

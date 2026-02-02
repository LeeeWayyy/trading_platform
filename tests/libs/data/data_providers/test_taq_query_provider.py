"""Unit tests for :class:`libs.data_providers.taq_query_provider.TAQLocalProvider`."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.taq_query_provider import TAQLocalProvider
from libs.data.data_quality.exceptions import DataNotFoundError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.data.data_quality.validation import DataValidator
from libs.data.data_quality.versioning import (
    DatasetSnapshot,
    DatasetVersionManager,
    FileStorageInfo,
    SnapshotManifest,
)


def _write_manifest(
    manifest_manager: ManifestManager,
    dataset: str,
    file_paths: list[Path],
    start_date: date,
    end_date: date,
    row_count: int,
) -> None:
    """Helper to persist a manifest JSON for a dataset under the manifest root."""

    manifest = SyncManifest(
        dataset=dataset,
        sync_timestamp=datetime.now(UTC),
        start_date=start_date,
        end_date=end_date,
        row_count=row_count,
        checksum="abc123",
        schema_version="v1.0.0",
        wrds_query_hash="hash",
        file_paths=[str(p) for p in file_paths],
        validation_status="passed",
    )

    path = manifest_manager.storage_path / f"{dataset}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2))


@pytest.fixture()
def taq_env(tmp_path: Path) -> dict[str, Any]:
    """Create mock TAQ datasets, manifests, and providers in isolated dirs."""

    data_root = tmp_path / "data"
    storage_path = data_root / "taq"
    manifest_dir = data_root / "manifests" / "taq"
    lock_dir = data_root / "locks"

    manifest_manager = ManifestManager(
        storage_path=manifest_dir,
        lock_dir=lock_dir,
        data_root=data_root,
    )
    version_manager = DatasetVersionManager(
        manifest_manager=manifest_manager,
        validator=None,
        snapshots_dir=data_root / "snapshots" / "taq",
        cas_dir=data_root / "cas",
        diffs_dir=data_root / "diffs",
        locks_dir=lock_dir,
        data_root=data_root,
    )

    # Aggregates - 1min bars
    bars_dir = storage_path / "aggregates" / "1min_bars"
    bars_dir.mkdir(parents=True, exist_ok=True)
    bars_jan = pl.DataFrame(
        {
            "ts": [
                datetime(2024, 1, 2, 9, 30),
                datetime(2024, 1, 2, 9, 31),
            ],
            "symbol": ["AAPL", "MSFT"],
            "open": [100.0, 200.0],
            "high": [101.0, 201.0],
            "low": [99.5, 199.0],
            "close": [100.5, 200.5],
            "volume": [1_000, 1_500],
            "vwap": [100.2, 200.2],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
        }
    )
    bars_feb = pl.DataFrame(
        {
            "ts": [datetime(2024, 2, 1, 9, 30)],
            "symbol": ["AAPL"],
            "open": [110.0],
            "high": [111.0],
            "low": [109.5],
            "close": [110.5],
            "volume": [900],
            "vwap": [110.1],
            "date": [date(2024, 2, 1)],
        }
    )
    bars_jan_path = bars_dir / "202401.parquet"
    bars_feb_path = bars_dir / "202402.parquet"
    bars_jan.write_parquet(bars_jan_path)
    bars_feb.write_parquet(bars_feb_path)

    _write_manifest(
        manifest_manager,
        dataset="taq_1min_bars",
        file_paths=[bars_jan_path, bars_feb_path],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 1),
        row_count=bars_jan.height + bars_feb.height,
    )

    # Realized volatility
    rv_dir = storage_path / "aggregates" / "daily_rv"
    rv_dir.mkdir(parents=True, exist_ok=True)
    rv_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 2, 1)],
            "symbol": ["AAPL", "MSFT"],
            "rv_5m": [0.10, 0.20],
            "rv_30m": [0.30, 0.40],
            "obs": [390, 395],
        }
    )
    rv_path = rv_dir / "202401.parquet"
    rv_df.write_parquet(rv_path)
    _write_manifest(
        manifest_manager,
        dataset="taq_daily_rv",
        file_paths=[rv_path],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 1),
        row_count=rv_df.height,
    )

    # Spread metrics
    spreads_dir = storage_path / "aggregates" / "spread_stats"
    spreads_dir.mkdir(parents=True, exist_ok=True)
    spreads_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "symbol": ["AAPL", "MSFT"],
            "qwap_spread": [0.01, 0.015],
            "ewas": [0.005, 0.006],
            "quotes": [100, 120],
            "trades": [50, 60],
        }
    )
    spreads_path = spreads_dir / "202401.parquet"
    spreads_df.write_parquet(spreads_path)
    _write_manifest(
        manifest_manager,
        dataset="taq_spread_stats",
        file_paths=[spreads_path],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
        row_count=spreads_df.height,
    )

    # Tick samples for 2024-01-02
    samples_dir = storage_path / "samples" / "2024-01-02"
    samples_dir.mkdir(parents=True, exist_ok=True)
    ticks_df = pl.DataFrame(
        {
            "ts": [datetime(2024, 1, 2, 9, 30), datetime(2024, 1, 2, 9, 31)],
            "symbol": ["AAPL", "MSFT"],
            "bid": [99.9, 199.5],
            "ask": [100.1, 200.5],
            "bid_size": [100, 200],
            "ask_size": [150, 250],
            "trade_px": [100.0, 200.0],
            "trade_size": [10, 12],
            "cond": ["", ""],
        }
    )
    aapl_ticks = samples_dir / "AAPL.parquet"
    msft_ticks = samples_dir / "MSFT.parquet"
    ticks_df.filter(pl.col("symbol") == "AAPL").write_parquet(aapl_ticks)
    ticks_df.filter(pl.col("symbol") == "MSFT").write_parquet(msft_ticks)
    _write_manifest(
        manifest_manager,
        dataset="taq_samples_20240102",
        file_paths=[aapl_ticks, msft_ticks],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        row_count=2,
    )

    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )
    polars_provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        engine="polars",
        data_root=data_root,
    )

    return {
        "provider": provider,
        "polars_provider": polars_provider,
        "manifest_manager": manifest_manager,
        "version_manager": version_manager,
        "data_root": data_root,
        "storage_path": storage_path,
        "bars_jan_path": bars_jan_path,
        "bars_jan_rows": bars_jan.height,
    }


def test_fetch_minute_bars_filters_and_sorts(taq_env: dict[str, Any]) -> None:
    """fetch_minute_bars returns upper-cased symbols sorted by date/ts."""

    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_minute_bars(
        symbols=["aapl"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 1),
    )

    assert list(df["symbol"].unique()) == ["AAPL"]
    assert df.height == 2  # Jan + Feb rows
    assert df["ts"].is_sorted()


def test_fetch_realized_volatility_window_selection(taq_env: dict[str, Any]) -> None:
    """Window selection adds rv alias and preserves original columns."""

    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_realized_volatility(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 1),
        window=30,
    )

    assert "rv" in df.columns
    assert df.filter(pl.col("symbol") == "AAPL")["rv"].to_list() == [0.30]

    with pytest.raises(ValueError, match="window must be 5 or 30"):
        provider.fetch_realized_volatility(
            symbols=["AAPL"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
            window=10,
        )


def test_fetch_spread_metrics_returns_data(taq_env: dict[str, Any]) -> None:
    """Spread metrics are filtered by symbol list and date range."""

    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_spread_metrics(
        symbols=["MSFT"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )

    assert df.height == 1
    assert df["symbol"].to_list() == ["MSFT"]
    assert df["qwap_spread"].to_list() == [0.015]


def test_fetch_ticks_filters_symbols(taq_env: dict[str, Any]) -> None:
    """Tick retrieval filters file list to requested symbols only."""

    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_ticks(sample_date=date(2024, 1, 2), symbols=["MSFT"])

    assert df.height == 1
    assert df["symbol"].to_list() == ["MSFT"]


def test_empty_results_preserve_schema(taq_env: dict[str, Any]) -> None:
    """Empty ranges return typed empty frames rather than raising."""

    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_minute_bars(
        symbols=["AAPL"],
        start_date=date(2024, 2, 2),
        end_date=date(2024, 1, 2),
    )

    assert df.is_empty()
    expected_schema = {
        col: DataValidator.DTYPE_MAP[dtype.lower()]
        for col, dtype in provider.SCHEMAS[provider.DATASET_1MIN].items()
    }
    assert df.schema == expected_schema


def test_pit_query_uses_snapshot(monkeypatch: pytest.MonkeyPatch, taq_env: dict[str, Any]) -> None:
    """PIT queries source files from DatasetVersionManager snapshots."""

    manifest_manager: ManifestManager = taq_env["manifest_manager"]
    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    file_info = FileStorageInfo(
        path="taq/aggregates/1min_bars/202401.parquet",
        original_path="taq/aggregates/1min_bars/202401.parquet",
        storage_mode="copy",
        target="taq/aggregates/1min_bars/202401.parquet",
        size_bytes=taq_env["bars_jan_rows"],
        checksum="abc",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_1min_bars",
        sync_manifest_version=1,
        files=[file_info],
        row_count=taq_env["bars_jan_rows"],
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 1, 31),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"taq_1min_bars": dataset_snapshot},
        total_size_bytes=taq_env["bars_jan_rows"],
        aggregate_checksum="snap",
    )

    version_manager.query_as_of.return_value = (data_root, snapshot)

    df = provider.fetch_minute_bars(
        symbols=["AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 1),
        as_of=date(2024, 2, 15),
    )

    version_manager.query_as_of.assert_called_once()
    assert df["date"].to_list() == [date(2024, 1, 2)]

    version_manager.query_as_of.side_effect = DataNotFoundError("missing")
    with pytest.raises(DataNotFoundError):
        provider.fetch_minute_bars(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            as_of=date(2024, 1, 31),
        )


def test_duckdb_and_polars_engine_parity(taq_env: dict[str, Any]) -> None:
    """DuckDB and Polars engines return identical results."""

    duck = taq_env["provider"]
    polars_provider = taq_env["polars_provider"]

    df_duck = duck.fetch_spread_metrics(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )
    df_polars = polars_provider.fetch_spread_metrics(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 3),
    )

    # Compare DataFrames - both engines should return same data
    assert df_duck.shape == df_polars.shape
    assert df_duck.columns == df_polars.columns
    assert df_duck.to_dicts() == df_polars.to_dicts()


def test_thread_local_connection_isolated_per_thread(taq_env: dict[str, Any]) -> None:
    """Each thread gets its own DuckDB connection, main thread is reused."""

    provider: TAQLocalProvider = taq_env["provider"]

    main_conn = provider._ensure_connection()

    def _get_conn_id() -> int:
        return id(provider._ensure_connection())

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        other_conn_id = executor.submit(_get_conn_id).result()

    assert id(main_conn) != other_conn_id
    assert provider._ensure_connection() is main_conn


# =====================================================================
# Additional tests for improved coverage
# =====================================================================


def test_invalid_engine_raises_value_error(taq_env: dict[str, Any]) -> None:
    """Invalid engine parameter raises ValueError."""
    with pytest.raises(ValueError, match="engine must be 'duckdb' or 'polars'"):
        TAQLocalProvider(
            storage_path=taq_env["storage_path"],
            manifest_manager=taq_env["manifest_manager"],
            engine="invalid",  # type: ignore[arg-type]
            data_root=taq_env["data_root"],
        )


def test_storage_path_outside_data_root_raises(tmp_path: Path) -> None:
    """Storage path must be within data_root or ValueError is raised."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    storage_outside = tmp_path / "other" / "taq"
    storage_outside.mkdir(parents=True)

    manifest_dir = data_root / "manifests" / "taq"
    manifest_manager = ManifestManager(
        storage_path=manifest_dir,
        lock_dir=data_root / "locks",
        data_root=data_root,
    )

    with pytest.raises(ValueError, match="must be within data_root"):
        TAQLocalProvider(
            storage_path=storage_outside,
            manifest_manager=manifest_manager,
            engine="duckdb",
            data_root=data_root,
        )


def test_empty_symbols_raises_value_error(taq_env: dict[str, Any]) -> None:
    """Empty symbols list raises ValueError for all fetch methods."""
    provider: TAQLocalProvider = taq_env["provider"]

    with pytest.raises(ValueError, match="symbols list cannot be empty"):
        provider.fetch_minute_bars(
            symbols=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
        )

    with pytest.raises(ValueError, match="symbols list cannot be empty"):
        provider.fetch_realized_volatility(
            symbols=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 2, 1),
        )

    with pytest.raises(ValueError, match="symbols list cannot be empty"):
        provider.fetch_spread_metrics(
            symbols=[],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
        )

    with pytest.raises(ValueError, match="symbols list cannot be empty"):
        provider.fetch_ticks(sample_date=date(2024, 1, 2), symbols=[])


def test_fetch_realized_volatility_empty_result_on_inverted_dates(
    taq_env: dict[str, Any],
) -> None:
    """RV returns empty typed DataFrame when start_date > end_date."""
    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_realized_volatility(
        symbols=["AAPL"],
        start_date=date(2024, 3, 1),
        end_date=date(2024, 1, 1),
    )
    assert df.is_empty()
    assert "rv_5m" in df.columns


def test_fetch_spread_metrics_empty_result_on_inverted_dates(
    taq_env: dict[str, Any],
) -> None:
    """Spread metrics returns empty typed DataFrame when start_date > end_date."""
    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_spread_metrics(
        symbols=["AAPL"],
        start_date=date(2024, 3, 1),
        end_date=date(2024, 1, 1),
    )
    assert df.is_empty()
    assert "qwap_spread" in df.columns


def test_fetch_minute_bars_no_matching_paths_returns_empty(
    taq_env: dict[str, Any],
) -> None:
    """When no manifest paths match needed months, return empty typed frame."""
    provider: TAQLocalProvider = taq_env["provider"]

    # Query for months outside available data (2023 instead of 2024)
    df = provider.fetch_minute_bars(
        symbols=["AAPL"],
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 31),
    )
    assert df.is_empty()


def test_fetch_realized_volatility_no_matching_paths_returns_empty(
    taq_env: dict[str, Any],
) -> None:
    """When no manifest paths match needed months for RV, return empty typed frame."""
    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_realized_volatility(
        symbols=["AAPL"],
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 31),
    )
    assert df.is_empty()


def test_fetch_spread_metrics_no_matching_paths_returns_empty(
    taq_env: dict[str, Any],
) -> None:
    """When no manifest paths match needed months for spreads, return empty typed frame."""
    provider: TAQLocalProvider = taq_env["provider"]

    df = provider.fetch_spread_metrics(
        symbols=["AAPL"],
        start_date=date(2023, 1, 2),
        end_date=date(2023, 1, 31),
    )
    assert df.is_empty()


def test_fetch_ticks_empty_result_when_no_matching_files(
    taq_env: dict[str, Any],
) -> None:
    """Ticks fetch returns empty typed frame when no symbol files match."""
    provider: TAQLocalProvider = taq_env["provider"]

    # Request symbol that doesn't exist in the tick samples
    df = provider.fetch_ticks(sample_date=date(2024, 1, 2), symbols=["GOOG"])
    assert df.is_empty()
    assert "trade_px" in df.columns


def test_fetch_ticks_with_as_of_requires_version_manager(
    taq_env: dict[str, Any],
) -> None:
    """PIT tick queries require version_manager or raise ValueError."""
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=None,
        engine="duckdb",
        data_root=taq_env["data_root"],
    )

    with pytest.raises(ValueError, match="version_manager is required"):
        provider.fetch_ticks(
            sample_date=date(2024, 1, 2),
            symbols=["AAPL"],
            as_of=date(2024, 2, 1),
        )


def test_pit_query_minute_bars_requires_version_manager(
    taq_env: dict[str, Any],
) -> None:
    """PIT minute bars queries require version_manager or raise ValueError."""
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=None,
        engine="duckdb",
        data_root=taq_env["data_root"],
    )

    with pytest.raises(ValueError, match="version_manager is required"):
        provider.fetch_minute_bars(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            as_of=date(2024, 2, 1),
        )


def test_polars_engine_fetch_minute_bars(taq_env: dict[str, Any]) -> None:
    """Polars engine execution path for minute bars."""
    provider: TAQLocalProvider = taq_env["polars_provider"]

    df = provider.fetch_minute_bars(
        symbols=["AAPL"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 2, 1),
    )

    assert list(df["symbol"].unique()) == ["AAPL"]
    assert df.height == 2


def test_polars_engine_fetch_ticks(taq_env: dict[str, Any]) -> None:
    """Polars engine execution path for tick samples."""
    provider: TAQLocalProvider = taq_env["polars_provider"]

    df = provider.fetch_ticks(sample_date=date(2024, 1, 2), symbols=["AAPL"])

    assert df.height == 1
    assert df["symbol"].to_list() == ["AAPL"]


def test_tick_paths_from_snapshot(taq_env: dict[str, Any]) -> None:
    """PIT tick queries resolve paths from DatasetVersionManager snapshots."""
    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Build snapshot with tick sample files
    sample_date = date(2024, 1, 2)
    file_info_aapl = FileStorageInfo(
        path="taq/samples/2024-01-02/AAPL.parquet",
        original_path="taq/samples/2024-01-02/AAPL.parquet",
        storage_mode="copy",
        target="taq/samples/2024-01-02/AAPL.parquet",
        size_bytes=100,
        checksum="abc",
    )
    file_info_msft = FileStorageInfo(
        path="taq/samples/2024-01-02/MSFT.parquet",
        original_path="taq/samples/2024-01-02/MSFT.parquet",
        storage_mode="copy",
        target="taq/samples/2024-01-02/MSFT.parquet",
        size_bytes=100,
        checksum="def",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_samples_20240102",
        sync_manifest_version=1,
        files=[file_info_aapl, file_info_msft],
        row_count=2,
        date_range_start=date(2024, 1, 2),
        date_range_end=date(2024, 1, 2),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"taq_samples_20240102": dataset_snapshot},
        total_size_bytes=200,
        aggregate_checksum="snap",
    )

    version_manager.query_as_of.return_value = (data_root, snapshot)

    df = provider.fetch_ticks(
        sample_date=sample_date,
        symbols=["AAPL"],
        as_of=date(2024, 2, 15),
    )

    version_manager.query_as_of.assert_called_once_with("taq_samples_20240102", date(2024, 2, 15))
    assert df.height == 1
    assert df["symbol"].to_list() == ["AAPL"]


def test_tick_paths_from_snapshot_raises_on_missing_dataset(
    taq_env: dict[str, Any],
) -> None:
    """PIT tick queries raise DataNotFoundError when snapshot missing dataset."""
    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Snapshot exists but doesn't have the requested dataset
    empty_snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={},
        total_size_bytes=0,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, empty_snapshot)

    with pytest.raises(DataNotFoundError, match="Snapshot missing dataset"):
        provider.fetch_ticks(
            sample_date=date(2024, 1, 2),
            symbols=["AAPL"],
            as_of=date(2024, 2, 15),
        )


def test_tick_paths_from_snapshot_raises_on_no_snapshot(
    taq_env: dict[str, Any],
) -> None:
    """PIT tick queries raise DataNotFoundError when no snapshot exists."""
    from libs.data.data_quality.exceptions import SnapshotNotFoundError

    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    version_manager.query_as_of.side_effect = SnapshotNotFoundError("No snapshot")

    with pytest.raises(DataNotFoundError, match="No snapshot available"):
        provider.fetch_ticks(
            sample_date=date(2024, 1, 2),
            symbols=["AAPL"],
            as_of=date(2024, 2, 15),
        )


def test_tick_paths_from_snapshot_skips_paths_outside_data_root(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PIT tick paths outside data_root are skipped with warning."""
    import logging

    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # File path that resolves outside data_root
    file_info_outside = FileStorageInfo(
        path="../../../outside/AAPL.parquet",
        original_path="AAPL.parquet",
        storage_mode="copy",
        target="../../../outside/AAPL.parquet",
        size_bytes=100,
        checksum="abc",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_samples_20240102",
        sync_manifest_version=1,
        files=[file_info_outside],
        row_count=1,
        date_range_start=date(2024, 1, 2),
        date_range_end=date(2024, 1, 2),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"taq_samples_20240102": dataset_snapshot},
        total_size_bytes=100,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, snapshot)

    with caplog.at_level(logging.WARNING):
        df = provider.fetch_ticks(
            sample_date=date(2024, 1, 2),
            symbols=["AAPL"],
            as_of=date(2024, 2, 15),
        )

    assert df.is_empty()
    assert "outside data_root" in caplog.text


def test_tick_paths_from_snapshot_skips_unexpected_sample_path(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PIT tick paths not under expected date directory are skipped."""
    import logging

    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # File path that is within data_root but wrong date directory
    wrong_date_dir = storage_path / "samples" / "2024-01-03"
    wrong_date_dir.mkdir(parents=True, exist_ok=True)
    wrong_file = wrong_date_dir / "AAPL.parquet"
    wrong_file.touch()

    file_info_wrong_dir = FileStorageInfo(
        path="taq/samples/2024-01-03/AAPL.parquet",
        original_path="taq/samples/2024-01-03/AAPL.parquet",
        storage_mode="copy",
        target="taq/samples/2024-01-03/AAPL.parquet",
        size_bytes=100,
        checksum="abc",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_samples_20240102",
        sync_manifest_version=1,
        files=[file_info_wrong_dir],
        row_count=1,
        date_range_start=date(2024, 1, 2),
        date_range_end=date(2024, 1, 2),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"taq_samples_20240102": dataset_snapshot},
        total_size_bytes=100,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, snapshot)

    with caplog.at_level(logging.WARNING):
        df = provider.fetch_ticks(
            sample_date=date(2024, 1, 2),  # Expecting 2024-01-02 but file is in 2024-01-03
            symbols=["AAPL"],
            as_of=date(2024, 2, 15),
        )

    assert df.is_empty()
    assert "unexpected sample path" in caplog.text


def test_filter_month_partitions_skips_paths_outside_data_root(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_filter_month_partitions skips paths that resolve outside data_root."""
    import logging

    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]
    manifest_manager: ManifestManager = taq_env["manifest_manager"]

    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Test the internal method directly with a path outside data_root
    paths = [
        "../../../outside/202401.parquet",
        str(storage_path / "aggregates/1min_bars/202401.parquet"),
    ]
    needed_months = {"202401"}

    with caplog.at_level(logging.WARNING):
        result = provider._filter_month_partitions(paths, needed_months)

    # Should only include the valid path
    assert len(result) == 1
    assert "202401.parquet" in str(result[0])
    assert "outside data_root" in caplog.text


def test_filter_symbol_paths_skips_paths_outside_data_root(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_filter_symbol_paths skips paths that resolve outside data_root."""
    import logging

    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]
    manifest_manager: ManifestManager = taq_env["manifest_manager"]

    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        engine="duckdb",
        data_root=data_root,
    )

    base_dir = storage_path / "samples" / "2024-01-02"
    paths = ["../../../outside/AAPL.parquet", str(base_dir / "AAPL.parquet")]
    symbols = {"AAPL"}

    with caplog.at_level(logging.WARNING):
        result = provider._filter_symbol_paths(paths, symbols, base_dir)

    # Should only include the valid path
    assert len(result) == 1
    assert "AAPL.parquet" in str(result[0])
    assert "outside data_root" in caplog.text


def test_filter_symbol_paths_skips_unexpected_base_directory(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_filter_symbol_paths skips paths not under expected base directory."""
    import logging

    data_root: Path = taq_env["data_root"]
    storage_path: Path = taq_env["storage_path"]
    manifest_manager: ManifestManager = taq_env["manifest_manager"]

    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Create a file in a different date directory
    wrong_dir = storage_path / "samples" / "2024-01-03"
    wrong_dir.mkdir(parents=True, exist_ok=True)
    wrong_file = wrong_dir / "AAPL.parquet"
    wrong_file.touch()

    base_dir = storage_path / "samples" / "2024-01-02"
    paths = [str(wrong_file)]
    symbols = {"AAPL"}

    with caplog.at_level(logging.WARNING):
        result = provider._filter_symbol_paths(paths, symbols, base_dir)

    assert len(result) == 0
    assert "unexpected sample path" in caplog.text


def test_paths_from_snapshot_missing_dataset_raises(
    taq_env: dict[str, Any],
) -> None:
    """_paths_from_snapshot raises when snapshot doesn't contain requested dataset."""
    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Snapshot exists but for a different dataset
    other_dataset_snapshot = DatasetSnapshot(
        dataset="other_dataset",
        sync_manifest_version=1,
        files=[],
        row_count=0,
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 1, 31),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"other_dataset": other_dataset_snapshot},
        total_size_bytes=0,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, snapshot)

    with pytest.raises(DataNotFoundError, match="Snapshot missing dataset"):
        provider.fetch_minute_bars(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            as_of=date(2024, 2, 15),
        )


def test_paths_from_snapshot_skips_paths_outside_data_root(
    taq_env: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_paths_from_snapshot skips paths that resolve outside data_root."""
    import logging

    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # File path that resolves outside data_root
    file_info_outside = FileStorageInfo(
        path="../../../outside/202401.parquet",
        original_path="202401.parquet",
        storage_mode="copy",
        target="../../../outside/202401.parquet",
        size_bytes=100,
        checksum="abc",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_1min_bars",
        sync_manifest_version=1,
        files=[file_info_outside],
        row_count=1,
        date_range_start=date(2024, 1, 1),
        date_range_end=date(2024, 1, 31),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-02-01",
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        datasets={"taq_1min_bars": dataset_snapshot},
        total_size_bytes=100,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, snapshot)

    with caplog.at_level(logging.WARNING):
        df = provider.fetch_minute_bars(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            as_of=date(2024, 2, 15),
        )

    assert df.is_empty()
    assert "outside data_root" in caplog.text


def test_months_between_spans_year_boundary() -> None:
    """_months_between correctly handles year boundary."""
    # Create a minimal provider to test the method
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir) / "data"
        storage_path = data_root / "taq"
        storage_path.mkdir(parents=True)
        manifest_dir = data_root / "manifests" / "taq"
        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=data_root / "locks",
            data_root=data_root,
        )
        provider = TAQLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            engine="duckdb",
            data_root=data_root,
        )

        months = provider._months_between(date(2023, 11, 15), date(2024, 2, 10))

        assert months == {"202311", "202312", "202401", "202402"}


def test_extract_month_key_non_standard_filename() -> None:
    """_extract_month_key returns None for non-YYYYMM filenames."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir) / "data"
        storage_path = data_root / "taq"
        storage_path.mkdir(parents=True)
        manifest_dir = data_root / "manifests" / "taq"
        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=data_root / "locks",
            data_root=data_root,
        )
        provider = TAQLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            engine="duckdb",
            data_root=data_root,
        )

        # Standard filename
        assert provider._extract_month_key(Path("202401.parquet")) == "202401"

        # Non-standard filenames
        assert provider._extract_month_key(Path("AAPL.parquet")) is None
        assert provider._extract_month_key(Path("2024.parquet")) is None
        assert provider._extract_month_key(Path("20241.parquet")) is None
        assert provider._extract_month_key(Path("data_202401.parquet")) is None


def test_get_manifest_raises_when_not_found(taq_env: dict[str, Any]) -> None:
    """_get_manifest raises DataNotFoundError when manifest doesn't exist."""
    provider: TAQLocalProvider = taq_env["provider"]

    with pytest.raises(DataNotFoundError, match="No manifest found"):
        provider._get_manifest("nonexistent_dataset")


def test_empty_result_unknown_dataset() -> None:
    """_empty_result returns empty DataFrame for unknown dataset."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        data_root = Path(tmpdir) / "data"
        storage_path = data_root / "taq"
        storage_path.mkdir(parents=True)
        manifest_dir = data_root / "manifests" / "taq"
        manifest_manager = ManifestManager(
            storage_path=manifest_dir,
            lock_dir=data_root / "locks",
            data_root=data_root,
        )
        provider = TAQLocalProvider(
            storage_path=storage_path,
            manifest_manager=manifest_manager,
            engine="duckdb",
            data_root=data_root,
        )

        df = provider._empty_result("unknown_dataset")

        assert df.is_empty()
        assert df.columns == []


def test_close_clears_connection(taq_env: dict[str, Any]) -> None:
    """close() removes thread-local connection."""
    provider: TAQLocalProvider = taq_env["provider"]

    # Ensure a connection exists
    _ = provider._ensure_connection()
    assert getattr(provider._thread_local, "conn", None) is not None

    provider.close()

    assert getattr(provider._thread_local, "conn", None) is None


def test_context_manager(taq_env: dict[str, Any]) -> None:
    """Provider works as context manager and closes on exit."""
    storage_path: Path = taq_env["storage_path"]
    manifest_manager: ManifestManager = taq_env["manifest_manager"]
    data_root: Path = taq_env["data_root"]

    with TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        engine="duckdb",
        data_root=data_root,
    ) as provider:
        _ = provider._ensure_connection()
        assert getattr(provider._thread_local, "conn", None) is not None

    # Connection should be closed after exiting context
    assert getattr(provider._thread_local, "conn", None) is None


def test_invalidate_cache_is_noop(taq_env: dict[str, Any]) -> None:
    """invalidate_cache is a placeholder and doesn't raise."""
    provider: TAQLocalProvider = taq_env["provider"]

    # Should not raise
    provider.invalidate_cache()


def test_paths_from_snapshot_exception_on_query_as_of(
    taq_env: dict[str, Any],
) -> None:
    """_paths_from_snapshot re-raises as DataNotFoundError when query_as_of fails."""
    from libs.data.data_quality.exceptions import SnapshotNotFoundError

    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Trigger exception in query_as_of for _paths_from_snapshot path
    version_manager.query_as_of.side_effect = SnapshotNotFoundError("No snapshot")

    with pytest.raises(DataNotFoundError, match="No snapshot available"):
        provider.fetch_minute_bars(
            symbols=["AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            as_of=date(2024, 2, 15),
        )


def test_paths_from_snapshot_filters_non_matching_months(
    taq_env: dict[str, Any],
) -> None:
    """_paths_from_snapshot filters out files that don't match needed months."""
    data_root: Path = taq_env["data_root"]

    version_manager = MagicMock(spec=DatasetVersionManager)
    provider = TAQLocalProvider(
        storage_path=taq_env["storage_path"],
        manifest_manager=taq_env["manifest_manager"],
        version_manager=version_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # Create snapshot with file for a different month (202403) than queried (202401)
    file_info_march = FileStorageInfo(
        path="taq/aggregates/1min_bars/202403.parquet",
        original_path="taq/aggregates/1min_bars/202403.parquet",
        storage_mode="copy",
        target="taq/aggregates/1min_bars/202403.parquet",
        size_bytes=100,
        checksum="abc",
    )
    dataset_snapshot = DatasetSnapshot(
        dataset="taq_1min_bars",
        sync_manifest_version=1,
        files=[file_info_march],  # Only March, but we query January
        row_count=1,
        date_range_start=date(2024, 3, 1),
        date_range_end=date(2024, 3, 31),
    )
    snapshot = SnapshotManifest(
        version_tag="2024-04-01",
        created_at=datetime(2024, 4, 1, tzinfo=UTC),
        datasets={"taq_1min_bars": dataset_snapshot},
        total_size_bytes=100,
        aggregate_checksum="snap",
    )
    version_manager.query_as_of.return_value = (data_root, snapshot)

    # Query for January but snapshot only has March data
    df = provider.fetch_minute_bars(
        symbols=["AAPL"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        as_of=date(2024, 4, 15),
    )

    # Should get empty result because months don't match
    assert df.is_empty()


def test_close_no_connection_does_not_fail(taq_env: dict[str, Any]) -> None:
    """close() when no connection exists completes without error."""
    storage_path: Path = taq_env["storage_path"]
    manifest_manager: ManifestManager = taq_env["manifest_manager"]
    data_root: Path = taq_env["data_root"]

    provider = TAQLocalProvider(
        storage_path=storage_path,
        manifest_manager=manifest_manager,
        engine="duckdb",
        data_root=data_root,
    )

    # close() without ever creating a connection
    # No connection exists yet
    assert getattr(provider._thread_local, "conn", None) is None
    provider.close()  # Should not fail
    assert getattr(provider._thread_local, "conn", None) is None

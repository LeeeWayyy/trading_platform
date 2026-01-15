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

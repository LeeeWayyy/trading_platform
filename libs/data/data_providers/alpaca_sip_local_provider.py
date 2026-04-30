"""Alpaca SIP local historical data provider.

Read-only provider for querying normalized Alpaca SIP daily bars stored in
Parquet files. This mirrors the CRSP local-provider pattern: callers query a
manifest-pinned local snapshot through DuckDB instead of calling Alpaca live
during training or backtesting.
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from libs.data.data_quality.exceptions import DataNotFoundError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest

logger = logging.getLogger(__name__)


class AlpacaSIPManifestVersionChangedError(Exception):
    """Raised when a manifest changes during a query."""


ALPACA_SIP_COLUMNS = (
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "vwap",
    "adj_close",
    "ret",
)

ALPACA_SIP_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "trade_count": pl.Float64,
    "vwap": pl.Float64,
    "adj_close": pl.Float64,
    "ret": pl.Float64,
}

VALID_COLUMNS = set(ALPACA_SIP_COLUMNS)


class AlpacaSIPLocalProvider:
    """Read-only provider for normalized local Alpaca SIP daily bars.

    Expected storage layout:

        data/alpaca/sip/daily/
        ├── 2023.parquet
        └── 2024.parquet

    Required parquet columns are `date`, `symbol`, `open`, `high`, `low`,
    `close`, and `volume`. Optional columns include `trade_count`, `vwap`,
    `adj_close`, and `ret`.
    """

    DATASET_NAME = "alpaca_sip_daily"
    DATA_ROOT = Path("data")

    def __init__(
        self,
        storage_path: Path,
        manifest_manager: ManifestManager,
        data_root: Path | None = None,
        pinned_manifest: SyncManifest | None = None,
    ) -> None:
        """Initialize the local Alpaca SIP provider.

        Args:
            storage_path: Path to daily SIP parquet files.
            manifest_manager: Manager for manifest operations.
            data_root: Root directory for path validation.
            pinned_manifest: Optional immutable manifest to use for all reads.

        Raises:
            ValueError: If storage_path is outside data_root.
        """
        self.storage_path = Path(storage_path).resolve()
        self.manifest_manager = manifest_manager
        self.data_root = (data_root or self.DATA_ROOT).resolve()
        self._pinned_manifest = pinned_manifest

        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{storage_path}' must be within data_root '{self.data_root}'"
            )

        self._thread_local: threading.local = threading.local()

    def get_daily_prices(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Get local Alpaca SIP daily bars for a date range.

        Args:
            start_date: Start date, inclusive.
            end_date: End date, inclusive.
            symbols: Optional ticker-symbol filter.
            columns: Optional column projection.

        Returns:
            Polars DataFrame with requested local SIP columns.

        Raises:
            ValueError: If invalid columns are requested.
            DataNotFoundError: If no manifest exists.
            AlpacaSIPManifestVersionChangedError: If manifest changes mid-query.
        """
        if columns is not None:
            invalid = set(columns) - VALID_COLUMNS
            if invalid:
                raise ValueError(f"Invalid columns: {invalid}. Valid: {VALID_COLUMNS}")

        if start_date > end_date:
            return self._empty_result(columns)

        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version
        partition_paths = self._get_partition_paths_from_manifest(manifest, start_date, end_date)

        if not partition_paths:
            return self._empty_result(columns)

        result = self._execute_query(
            partition_paths=partition_paths,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            columns=columns,
        )

        if self._pinned_manifest is None:
            current_manifest = self._get_manifest()
            if current_manifest.manifest_version != pinned_version:
                raise AlpacaSIPManifestVersionChangedError(
                    f"Manifest version changed from {pinned_version} to "
                    f"{current_manifest.manifest_version} during query"
                )

        return result

    def _get_manifest(self) -> SyncManifest:
        """Load the Alpaca SIP daily manifest."""
        if self._pinned_manifest is not None:
            return self._pinned_manifest

        manifest = self.manifest_manager.load_manifest(self.DATASET_NAME)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for '{self.DATASET_NAME}'. Run Alpaca SIP sync first."
            )
        return manifest

    def _get_partition_paths_from_manifest(
        self,
        manifest: SyncManifest,
        start_date: date,
        end_date: date,
    ) -> list[Path]:
        """Return manifest paths for year partitions overlapping the query range."""
        needed_years = set(range(start_date.year, end_date.year + 1))
        paths: list[Path] = []

        for path_str in manifest.file_paths:
            path = Path(path_str)
            try:
                year = int(path.stem)
            except ValueError:
                continue

            if year not in needed_years:
                continue

            resolved = self._resolve_manifest_path(path)
            if resolved.is_relative_to(self.storage_path):
                paths.append(resolved)
            else:
                logger.warning("Skipping path outside Alpaca SIP storage_path: %s", path)

        return paths

    def _resolve_manifest_path(self, path: Path) -> Path:
        """Resolve manifest paths without depending on process working directory."""
        if path.is_absolute():
            return path.resolve()

        if len(path.parts) == 1:
            return (self.storage_path / path).resolve()

        if path.parts[0] == self.data_root.name:
            return (self.data_root.parent / path).resolve()

        return (self.data_root / path).resolve()

    def _execute_query(
        self,
        partition_paths: list[Path],
        start_date: date,
        end_date: date,
        symbols: list[str] | None,
        columns: list[str] | None,
    ) -> pl.DataFrame:
        """Execute a parameterized DuckDB query over selected partitions."""
        conn = self._ensure_connection()
        col_expr = "*" if columns is None else ", ".join(columns)

        params: dict[str, Any] = {
            "paths": [str(p) for p in partition_paths],
            "start_date": start_date,
            "end_date": end_date,
        }
        where_clauses = ["date >= $start_date", "date <= $end_date"]

        if symbols is not None:
            params["symbols"] = [s.upper() for s in symbols]
            where_clauses.append("UPPER(symbol) = ANY($symbols)")

        query = f"""
            SELECT {col_expr}
            FROM read_parquet($paths)
            WHERE {" AND ".join(where_clauses)}
            ORDER BY date, symbol
        """

        return conn.execute(query, params).pl()

    def _ensure_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create a thread-local DuckDB connection."""
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = duckdb.connect(":memory:", read_only=False)
            conn.execute("PRAGMA disable_object_cache")
            conn.execute("PRAGMA memory_limit='2GB'")
            conn.execute("PRAGMA threads=4")
            self._thread_local.conn = conn
        return conn

    def _empty_result(self, columns: list[str] | None) -> pl.DataFrame:
        """Return an empty DataFrame with the requested local schema."""
        if columns is None:
            schema = ALPACA_SIP_SCHEMA
        else:
            schema = {column: ALPACA_SIP_SCHEMA[column] for column in columns}
        return pl.DataFrame(schema=schema)

    def close(self) -> None:
        """Close the thread-local DuckDB connection for the current thread."""
        conn = getattr(self._thread_local, "conn", None)
        if conn is not None:
            conn.close()
            self._thread_local.conn = None
            logger.debug("DuckDB connection closed for Alpaca SIP provider")

    def __enter__(self) -> AlpacaSIPLocalProvider:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit."""
        self.close()

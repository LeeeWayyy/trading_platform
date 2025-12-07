"""CRSP Local Data Provider.

Read-only provider for querying CRSP daily data stored in Parquet files.
Implements manifest-aware snapshot consistency and point-in-time filtering.

This module provides:
- CRSPLocalProvider: Read-only CRSP data access with DuckDB
- AmbiguousTickerError: Raised when ticker maps to multiple PERMNOs
- ManifestVersionChangedError: Raised when manifest changes during query
"""

from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl

from libs.data_quality.exceptions import DataNotFoundError
from libs.data_quality.manifest import ManifestManager, SyncManifest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AmbiguousTickerError(Exception):
    """Raised when a ticker maps to multiple PERMNOs on the same date.

    This indicates a data quality issue or requires disambiguation
    by the caller using PERMNO directly.

    Attributes:
        ticker: The ambiguous ticker symbol.
        as_of_date: The date of the lookup.
        permnos: List of PERMNOs the ticker maps to.
    """

    def __init__(self, ticker: str, as_of_date: date, permnos: list[int]) -> None:
        self.ticker = ticker
        self.as_of_date = as_of_date
        self.permnos = permnos
        super().__init__(
            f"Ticker '{ticker}' is ambiguous on {as_of_date}: "
            f"maps to PERMNOs {permnos}"
        )


class ManifestVersionChangedError(Exception):
    """Raised when manifest version changes during query execution.

    This indicates a sync occurred while the query was running.
    The caller should retry the query to get consistent data.
    """

    pass


# Schema definition for validation (ordered for deterministic column selection)
CRSP_COLUMNS = ("date", "permno", "cusip", "ticker", "ret", "prc", "vol", "shrout")

CRSP_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "permno": pl.Int64,
    "cusip": pl.Utf8,
    "ticker": pl.Utf8,
    "ret": pl.Float64,
    "prc": pl.Float64,  # Note: Negative = bid/ask average
    "vol": pl.Float64,
    "shrout": pl.Float64,
}

VALID_COLUMNS = set(CRSP_COLUMNS)  # For O(1) validation lookups


class CRSPLocalProvider:
    """Read-only provider for CRSP daily data.

    Uses DuckDB to query Parquet files with manifest-aware partition pruning.
    Implements reader snapshot consistency by pinning manifest version.

    Storage Layout (per P4T1_TASK.md):
        data/wrds/crsp/daily/
        ├── 2020.parquet
        ├── 2021.parquet
        └── 2024.parquet

    Each parquet file contains:
        - date: Date of trading day
        - permno: CRSP permanent identifier
        - cusip: CUSIP identifier
        - ticker: Stock ticker symbol
        - ret: Holding period return
        - prc: Closing price (NEGATIVE = bid/ask average, use adjust_prices=True)
        - vol: Trading volume
        - shrout: Shares outstanding

    Thread Safety:
        This provider is thread-safe for concurrent read operations.
        Each thread gets its own DuckDB connection via thread-local storage.
        Each query pins the manifest version at start for consistency.

    Example:
        provider = CRSPLocalProvider(
            storage_path=Path("data/wrds/crsp/daily"),
            manifest_manager=manifest_mgr,
        )

        # Get prices with manifest consistency
        df = provider.get_daily_prices(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
        )
    """

    DATASET_NAME = "crsp_daily"
    DATA_ROOT = Path("data")  # Permitted root for security validation

    def __init__(
        self,
        storage_path: Path,
        manifest_manager: ManifestManager,
        data_root: Path | None = None,
    ) -> None:
        """Initialize CRSP provider.

        Args:
            storage_path: Path to CRSP data directory (e.g., data/wrds/crsp/daily).
            manifest_manager: Manager for manifest operations (required for consistency).
            data_root: Root directory for path validation (default: data/).

        Raises:
            ValueError: If storage_path is outside data_root (security).
        """
        self.storage_path = Path(storage_path).resolve()
        self.manifest_manager = manifest_manager
        self.data_root = (data_root or self.DATA_ROOT).resolve()

        # Security: Validate storage_path is within data_root
        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{storage_path}' must be within data_root '{self.data_root}'"
            )

        # Thread-local storage for DuckDB connections.
        # DuckDB connections are NOT thread-safe, so each thread needs its own connection.
        self._thread_local: threading.local = threading.local()
        self._security_metadata: pl.DataFrame | None = None
        self._security_metadata_version: int | None = None  # Manifest version when cache was built

    def get_daily_prices(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        permnos: list[int] | None = None,
        as_of_date: date | None = None,
        columns: list[str] | None = None,
        adjust_prices: bool = False,
    ) -> pl.DataFrame:
        """Get daily price data for securities in date range.

        Implements Reader Snapshot Consistency:
        1. Read manifest version at query start
        2. Execute query
        3. Verify manifest version unchanged (retry if changed)

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            symbols: Filter by ticker symbols (None = all).
            permnos: Filter by PERMNOs (None = all).
            as_of_date: Point-in-time filter - exclude securities IPO'd after this date.
            columns: Columns to return (None = all). Validated against schema.
            adjust_prices: If True, return abs(prc) instead of raw (negative = bid/ask).

        Returns:
            DataFrame with requested columns.

        Raises:
            ValueError: If invalid columns requested.
            DataNotFoundError: If no manifest found (run sync first).
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        # Validate columns
        if columns is not None:
            invalid = set(columns) - VALID_COLUMNS
            if invalid:
                raise ValueError(f"Invalid columns: {invalid}. Valid: {VALID_COLUMNS}")

        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version

        # Get partition paths from manifest (not filesystem)
        partition_paths = self._get_partition_paths_from_manifest(
            manifest, start_date, end_date
        )

        if not partition_paths:
            return self._empty_result(columns)

        # Build and execute query with parameterization
        result = self._execute_query(
            partition_paths=partition_paths,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            permnos=permnos,
            as_of_date=as_of_date,
            columns=columns,
            adjust_prices=adjust_prices,
        )

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest()
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        return result

    def get_universe(
        self,
        as_of_date: date,
        include_delisted: bool = True,
    ) -> pl.DataFrame:
        """Get universe of securities as of given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Point-in-time logic:
        - IPO date = first trade date in data (security's min date)
        - Delist date = last trade date in data (security's max date)
        - Security included if: first_date <= as_of_date
        - If include_delisted=False: also require last_date >= as_of_date

        Returns the ticker/cusip that was valid ON as_of_date (not future values).
        For delisted securities (where as_of_date > last_date), returns the
        final ticker/cusip the security had.

        Args:
            as_of_date: Reference date for universe construction.
            include_delisted: If True (default), include stocks delisted before as_of_date.
                            If False, only include stocks actively trading on as_of_date.

        Returns:
            DataFrame with: permno, ticker, cusip, first_date, last_date
            where ticker/cusip are point-in-time values (as of as_of_date).

        Raises:
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version

        # Get base metadata for first_date/last_date filtering (uses pinned manifest)
        metadata = self._get_security_metadata(manifest)

        # Filter: security must have IPO'd by as_of_date
        filtered = metadata.filter(pl.col("first_date") <= as_of_date)

        if not include_delisted:
            # Also require: security still trading on as_of_date
            filtered = filtered.filter(pl.col("last_date") >= as_of_date)

        if filtered.is_empty():
            return filtered

        # Get point-in-time ticker/cusip for the filtered securities
        # Query daily data for the most recent date <= as_of_date for each permno
        all_paths = self._get_validated_paths_from_manifest(manifest)

        if not all_paths:
            return filtered

        conn = self._ensure_connection()
        filtered_permnos = filtered["permno"].to_list()

        # Get the ticker/cusip as of as_of_date (most recent row <= as_of_date)
        query = """
            WITH latest_rows AS (
                SELECT
                    permno,
                    ticker,
                    cusip,
                    date,
                    ROW_NUMBER() OVER (PARTITION BY permno ORDER BY date DESC) as rn
                FROM read_parquet($paths)
                WHERE permno = ANY($permnos) AND date <= $as_of_date
            )
            SELECT permno, ticker, cusip
            FROM latest_rows
            WHERE rn = 1
        """
        pit_data = conn.execute(
            query,
            {
                "paths": [str(p) for p in all_paths],
                "permnos": filtered_permnos,
                "as_of_date": as_of_date,
            },
        ).pl()

        # Join point-in-time ticker/cusip with first_date/last_date
        result = filtered.select(["permno", "first_date", "last_date"]).join(
            pit_data, on="permno", how="left"
        )

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest()
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        # Reorder columns to match expected schema
        return result.select(["permno", "ticker", "cusip", "first_date", "last_date"])

    def ticker_to_permno(
        self,
        ticker: str,
        as_of_date: date,
    ) -> int:
        """Map ticker symbol to PERMNO at given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Queries the actual daily data to find the PERMNO that had this ticker
        on the specified date. This correctly handles historical ticker lookups
        even after ticker changes (e.g., looking up "FB" before Meta renamed).

        Args:
            ticker: Ticker symbol to look up.
            as_of_date: Date for the lookup.

        Returns:
            PERMNO that the ticker referred to on as_of_date.

        Raises:
            DataNotFoundError: If ticker not found on as_of_date.
            AmbiguousTickerError: If ticker maps to multiple PERMNOs on as_of_date.
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version

        # Query actual daily data for point-in-time correctness
        paths = self._get_partition_paths_from_manifest(manifest, as_of_date, as_of_date)

        if not paths:
            raise DataNotFoundError(f"No data available for {as_of_date}")

        conn = self._ensure_connection()

        # Use parameterized query to find PERMNOs with this ticker on as_of_date
        query = """
            SELECT DISTINCT permno
            FROM read_parquet($paths)
            WHERE ticker = $ticker AND date = $as_of_date
        """
        result = conn.execute(
            query,
            {
                "paths": [str(p) for p in paths],
                "ticker": ticker.upper(),
                "as_of_date": as_of_date,
            },
        ).pl()

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest()
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        if result.is_empty():
            raise DataNotFoundError(
                f"Ticker '{ticker}' not found or not trading on {as_of_date}"
            )

        permnos = result["permno"].to_list()
        if len(permnos) > 1:
            raise AmbiguousTickerError(ticker, as_of_date, permnos)

        return int(permnos[0])

    def permno_to_ticker(
        self,
        permno: int,
        as_of_date: date,
    ) -> str:
        """Map PERMNO to ticker symbol at given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Args:
            permno: PERMNO to look up.
            as_of_date: Date for the lookup.

        Returns:
            Ticker symbol that was valid for the PERMNO on as_of_date.

        Raises:
            DataNotFoundError: If PERMNO not found or not trading on as_of_date.
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version

        # Query the actual data to get ticker on specific date
        paths = self._get_partition_paths_from_manifest(manifest, as_of_date, as_of_date)

        if not paths:
            raise DataNotFoundError(f"No data available for {as_of_date}")

        conn = self._ensure_connection()

        # Use parameterized query
        query = """
            SELECT DISTINCT ticker
            FROM read_parquet($paths)
            WHERE permno = $permno AND date = $as_of_date
        """
        result = conn.execute(
            query,
            {
                "paths": [str(p) for p in paths],
                "permno": permno,
                "as_of_date": as_of_date,
            },
        ).pl()

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest()
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        if result.is_empty():
            raise DataNotFoundError(
                f"PERMNO {permno} not found or not trading on {as_of_date}"
            )

        return str(result["ticker"][0])

    def get_security_timeline(
        self,
        permno: int,
    ) -> pl.DataFrame:
        """Get complete trading history for a security.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Useful for tracking ticker changes over time.

        Args:
            permno: PERMNO to look up.

        Returns:
            DataFrame with: date, ticker, prc, ret, vol (ordered by date).

        Raises:
            DataNotFoundError: If PERMNO has no trading history.
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version

        # Security: Use validated paths to prevent path traversal
        paths = self._get_validated_paths_from_manifest(manifest)

        if not paths:
            raise DataNotFoundError("No CRSP data available")

        conn = self._ensure_connection()

        query = """
            SELECT date, ticker, prc, ret, vol
            FROM read_parquet($paths)
            WHERE permno = $permno
            ORDER BY date
        """
        result = conn.execute(
            query, {"paths": [str(p) for p in paths], "permno": permno}
        ).pl()

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest()
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        if result.is_empty():
            raise DataNotFoundError(f"PERMNO {permno} has no trading history")

        return result

    def _get_manifest(self) -> SyncManifest:
        """Load manifest for CRSP daily data.

        Raises:
            DataNotFoundError: If no manifest (run sync first).
        """
        manifest = self.manifest_manager.load_manifest(self.DATASET_NAME)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for '{self.DATASET_NAME}'. Run full_sync first."
            )
        return manifest

    def _get_partition_paths_from_manifest(
        self,
        manifest: SyncManifest,
        start_date: date,
        end_date: date,
    ) -> list[Path]:
        """Get partition paths from manifest for date range.

        Uses manifest file_paths (not filesystem) for consistency.
        Only returns paths for years overlapping with date range.

        Args:
            manifest: Current manifest.
            start_date: Start of range.
            end_date: End of range.

        Returns:
            List of parquet file paths needed for query.
        """
        needed_years = set(range(start_date.year, end_date.year + 1))
        paths = []

        for path_str in manifest.file_paths:
            path = Path(path_str)
            # Extract year from filename (e.g., "2024.parquet" -> 2024)
            try:
                year = int(path.stem)
                if year in needed_years:
                    # Verify path is within data_root (security)
                    resolved = path.resolve()
                    if resolved.is_relative_to(self.data_root):
                        paths.append(path)
                    else:
                        logger.warning(
                            "Skipping path outside data_root: %s",
                            path,
                        )
            except ValueError:
                # Filename doesn't match year pattern
                continue

        return paths

    def _get_validated_paths_from_manifest(
        self,
        manifest: SyncManifest,
    ) -> list[Path]:
        """Get all validated paths from manifest.

        Security: Validates all paths are within data_root.
        Use this for operations that need all data (metadata, timeline).

        Args:
            manifest: Current manifest.

        Returns:
            List of parquet file paths that pass security validation.
        """
        paths = []

        for path_str in manifest.file_paths:
            path = Path(path_str)
            # Verify path is within data_root (security)
            resolved = path.resolve()
            if resolved.is_relative_to(self.data_root):
                paths.append(path)
            else:
                logger.warning(
                    "Skipping path outside data_root: %s",
                    path,
                )

        return paths

    def _execute_query(
        self,
        partition_paths: list[Path],
        start_date: date,
        end_date: date,
        symbols: list[str] | None,
        permnos: list[int] | None,
        as_of_date: date | None,
        columns: list[str] | None,
        adjust_prices: bool,
    ) -> pl.DataFrame:
        """Execute parameterized DuckDB query.

        All filtering uses parameterized queries to prevent SQL injection.
        """
        conn = self._ensure_connection()

        # Build column list
        # When adjust_prices=True, we must use explicit columns (not *)
        # to apply ABS(prc) transformation
        if columns is None:
            if adjust_prices:
                # Use ordered CRSP_COLUMNS tuple for deterministic column order
                cols = [c if c != "prc" else "ABS(prc) AS prc" for c in CRSP_COLUMNS]
                col_expr = ", ".join(cols)
            else:
                col_expr = "*"
        else:
            # Add prc handling for adjust_prices
            if adjust_prices and "prc" in columns:
                cols = [c if c != "prc" else "ABS(prc) AS prc" for c in columns]
                col_expr = ", ".join(cols)
            else:
                col_expr = ", ".join(columns)

        # Build WHERE clause with parameters
        params: dict[str, Any] = {
            "paths": [str(p) for p in partition_paths],
            "start_date": start_date,
            "end_date": end_date,
        }

        where_clauses = ["date >= $start_date", "date <= $end_date"]

        if symbols is not None:
            # Normalize to uppercase
            params["symbols"] = [s.upper() for s in symbols]
            where_clauses.append("ticker = ANY($symbols)")

        if permnos is not None:
            params["permnos"] = permnos
            where_clauses.append("permno = ANY($permnos)")

        if as_of_date is not None:
            # Point-in-time: exclude securities that IPO'd after as_of_date
            # This requires knowing each security's first trade date
            metadata = self._get_security_metadata()
            valid_permnos = metadata.filter(pl.col("first_date") <= as_of_date)[
                "permno"
            ].to_list()
            params["valid_permnos"] = valid_permnos
            where_clauses.append("permno = ANY($valid_permnos)")

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT {col_expr}
            FROM read_parquet($paths)
            WHERE {where_sql}
            ORDER BY date, permno
        """

        return conn.execute(query, params).pl()

    def _get_security_metadata(
        self, manifest: SyncManifest | None = None
    ) -> pl.DataFrame:
        """Get or compute security metadata (first/last trade dates).

        Caches the result for efficiency. Cache is tied to manifest version
        and automatically rebuilds when manifest version changes.

        Note: The ticker/cusip returned here is the FINAL (latest) value,
        which is used for first_date/last_date filtering. For point-in-time
        ticker/cusip lookups, use ticker_to_permno() or get_universe() which
        query the actual daily data for the specific date.

        Args:
            manifest: Optional manifest to use. If None, loads current manifest.
                      Cache is invalidated if manifest version differs from cached.

        Returns:
            DataFrame with: permno, ticker, cusip, first_date, last_date
        """
        if manifest is None:
            manifest = self._get_manifest()

        # Check if cache is valid (matches current manifest version)
        if (
            self._security_metadata is not None
            and self._security_metadata_version == manifest.manifest_version
        ):
            return self._security_metadata

        # Cache is stale or missing - rebuild
        logger.debug(
            "Rebuilding security metadata cache (version %s -> %s)",
            self._security_metadata_version,
            manifest.manifest_version,
        )

        # Security: Use validated paths to prevent path traversal
        paths = self._get_validated_paths_from_manifest(manifest)

        if not paths:
            self._security_metadata = pl.DataFrame(
                schema={
                    "permno": pl.Int64,
                    "ticker": pl.Utf8,
                    "cusip": pl.Utf8,
                    "first_date": pl.Date,
                    "last_date": pl.Date,
                }
            )
            self._security_metadata_version = manifest.manifest_version
            return self._security_metadata

        conn = self._ensure_connection()

        # Compute first/last dates per PERMNO, get latest ticker/cusip
        query = """
            SELECT
                permno,
                LAST(ticker ORDER BY date) AS ticker,
                LAST(cusip ORDER BY date) AS cusip,
                MIN(date) AS first_date,
                MAX(date) AS last_date
            FROM read_parquet($paths)
            GROUP BY permno
        """
        self._security_metadata = conn.execute(
            query, {"paths": [str(p) for p in paths]}
        ).pl()
        self._security_metadata_version = manifest.manifest_version

        return self._security_metadata

    def _ensure_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create thread-local DuckDB in-memory connection.

        Thread Safety:
            DuckDB connections are NOT thread-safe. This method uses thread-local
            storage to provide each thread with its own connection, preventing
            data corruption when multiple threads access the provider concurrently.

        Note:
            Uses in-memory connection with read_only=False because DuckDB requires
            write access for some in-memory operations (PRAGMA settings, temp tables).
            The provider is logically read-only (never modifies Parquet files).

        Uses PRAGMA disable_object_cache to ensure fresh data
        after syncs (per P4T1 DuckDB Operational Safety).
        """
        # Use thread-local storage for thread-safe connection management
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = duckdb.connect(":memory:", read_only=False)
            # Disable object cache for long-lived sessions
            conn.execute("PRAGMA disable_object_cache")
            # Set reasonable memory limit
            conn.execute("PRAGMA memory_limit='2GB'")
            # Limit threads for reader
            conn.execute("PRAGMA threads=4")
            self._thread_local.conn = conn

        return conn

    def _empty_result(self, columns: list[str] | None) -> pl.DataFrame:
        """Return empty DataFrame with correct schema."""
        if columns is None:
            schema = CRSP_SCHEMA
        else:
            schema = {c: CRSP_SCHEMA[c] for c in columns}
        return pl.DataFrame(schema=schema)

    def invalidate_cache(self) -> None:
        """Invalidate cached security metadata.

        Call this after a sync completes to refresh metadata.
        The next query will recompute first/last trade dates.

        Note: The cache is also automatically invalidated when the manifest
        version changes, so explicit invalidation is only needed for edge cases.
        """
        self._security_metadata = None
        self._security_metadata_version = None
        logger.debug("Security metadata cache invalidated")

    def close(self) -> None:
        """Close thread-local DuckDB connection.

        Note: This closes only the connection for the calling thread.
        Other threads' connections remain open until those threads call close()
        or the provider is garbage collected.
        """
        conn = getattr(self._thread_local, "conn", None)
        if conn is not None:
            conn.close()
            self._thread_local.conn = None
            logger.debug("DuckDB connection closed for current thread")

    def __enter__(self) -> CRSPLocalProvider:
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

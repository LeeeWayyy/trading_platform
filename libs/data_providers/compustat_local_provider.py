"""Compustat Local Data Provider.

Read-only provider for querying Compustat fundamental data stored in Parquet files.
Implements manifest-aware snapshot consistency and point-in-time filtering with filing lags.

This module provides:
- CompustatLocalProvider: Read-only Compustat data access with DuckDB
- AmbiguousGVKEYError: Raised when ticker maps to multiple GVKEYs
- ManifestVersionChangedError: Raised when manifest changes during query

Point-in-Time (PIT) Correctness:
    Compustat data requires special handling to avoid look-ahead bias.
    A record with datadate=2023-12-31 (fiscal year end) isn't publicly
    available until the 10-K filing date (~90 days later for annual,
    ~45 days for quarterly).

    PIT Rule: datadate + filing_lag <= as_of_date
    A record is AVAILABLE only after the filing lag has elapsed.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import duckdb
import polars as pl

from libs.data_quality.exceptions import DataNotFoundError
from libs.data_quality.manifest import ManifestManager, SyncManifest

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AmbiguousGVKEYError(Exception):
    """Raised when a ticker maps to multiple GVKEYs on the same date.

    This indicates either multiple companies with the same ticker
    or a data quality issue requiring disambiguation by GVKEY.

    Attributes:
        ticker: The ambiguous ticker symbol.
        as_of_date: The date of the lookup.
        gvkeys: List of GVKEYs the ticker maps to.
    """

    def __init__(self, ticker: str, as_of_date: date, gvkeys: list[str]) -> None:
        self.ticker = ticker
        self.as_of_date = as_of_date
        self.gvkeys = gvkeys
        super().__init__(
            f"Ticker '{ticker}' is ambiguous on {as_of_date}: "
            f"maps to GVKEYs {gvkeys}"
        )


class ManifestVersionChangedError(Exception):
    """Raised when manifest version changes during query execution.

    This indicates a sync occurred while the query was running.
    The caller should retry the query to get consistent data.
    """

    pass


# Schema definitions for validation (ordered for deterministic column selection)
COMPUSTAT_ANNUAL_COLUMNS = (
    "datadate",
    "gvkey",
    "tic",
    "conm",
    "at",
    "lt",
    "sale",
    "ni",
    "ceq",
)

COMPUSTAT_QUARTERLY_COLUMNS = (
    "datadate",
    "gvkey",
    "tic",
    "conm",
    "atq",
    "ltq",
    "saleq",
    "niq",
)

COMPUSTAT_ANNUAL_SCHEMA: dict[str, type[pl.DataType]] = {
    "datadate": pl.Date,  # Fiscal period end date
    "gvkey": pl.Utf8,  # GVKEY identifier (string in Compustat)
    "tic": pl.Utf8,  # Ticker symbol
    "conm": pl.Utf8,  # Company name
    "at": pl.Float64,  # Total Assets
    "lt": pl.Float64,  # Total Liabilities
    "sale": pl.Float64,  # Net Sales/Revenue
    "ni": pl.Float64,  # Net Income
    "ceq": pl.Float64,  # Common Equity - Total
}

COMPUSTAT_QUARTERLY_SCHEMA: dict[str, type[pl.DataType]] = {
    "datadate": pl.Date,
    "gvkey": pl.Utf8,
    "tic": pl.Utf8,
    "conm": pl.Utf8,
    "atq": pl.Float64,  # Total Assets - Quarterly
    "ltq": pl.Float64,  # Total Liabilities - Quarterly
    "saleq": pl.Float64,  # Net Sales - Quarterly
    "niq": pl.Float64,  # Net Income - Quarterly
}

VALID_ANNUAL_COLUMNS = set(COMPUSTAT_ANNUAL_COLUMNS)
VALID_QUARTERLY_COLUMNS = set(COMPUSTAT_QUARTERLY_COLUMNS)


class CompustatLocalProvider:
    """Read-only provider for Compustat fundamental data.

    Uses DuckDB to query Parquet files with manifest-aware partition pruning.
    Implements reader snapshot consistency by pinning manifest version.
    Supports both annual (funda) and quarterly (fundq) datasets.

    Storage Layout (per P4T1_TASK.md):
        data/wrds/compustat_annual/
        ├── 2020.parquet
        └── 2024.parquet

        data/wrds/compustat_quarterly/
        ├── 2020.parquet
        └── 2024.parquet

    Each parquet file contains:
        Annual: datadate, gvkey, tic, conm, at, lt, sale, ni, ceq
        Quarterly: datadate, gvkey, tic, conm, atq, ltq, saleq, niq

    Point-in-Time (PIT) Correctness:
        Filing lags prevent look-ahead bias:
        - Annual (10-K): 90 days default
        - Quarterly (10-Q): 45 days default

        A record with datadate=2023-12-31 is only available when:
        as_of_date >= datadate + filing_lag_days

    Thread Safety:
        This provider is thread-safe for concurrent read operations.
        Each thread gets its own DuckDB connection via thread-local storage.
        Each query pins the manifest version at start for consistency.

    Example:
        provider = CompustatLocalProvider(
            storage_path=Path("data/wrds"),
            manifest_manager=manifest_mgr,
        )

        # Get annual fundamentals with 90-day filing lag
        df = provider.get_annual_fundamentals(
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            as_of_date=date(2024, 4, 1),  # Data available as of this date
        )
    """

    DATASET_ANNUAL = "compustat_annual"
    DATASET_QUARTERLY = "compustat_quarterly"
    DATA_ROOT = Path("data")  # Permitted root for security validation

    # Default filing lags (can be overridden per query)
    DEFAULT_ANNUAL_FILING_LAG_DAYS = 90  # 10-K typically filed within 90 days
    DEFAULT_QUARTERLY_FILING_LAG_DAYS = 45  # 10-Q typically filed within 45 days

    def __init__(
        self,
        storage_path: Path,
        manifest_manager: ManifestManager,
        data_root: Path | None = None,
    ) -> None:
        """Initialize Compustat provider.

        Args:
            storage_path: Path to WRDS data directory (e.g., data/wrds).
                         Annual data is expected in {storage_path}/compustat_annual/
                         Quarterly data in {storage_path}/compustat_quarterly/
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

        # Separate caches for annual and quarterly metadata
        self._annual_metadata: pl.DataFrame | None = None
        self._annual_metadata_version: int | None = None
        self._quarterly_metadata: pl.DataFrame | None = None
        self._quarterly_metadata_version: int | None = None

    def get_annual_fundamentals(
        self,
        start_date: date,
        end_date: date,
        gvkeys: list[str] | None = None,
        *,
        as_of_date: date,
        filing_lag_days: int | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Get annual fundamental data for securities in date range.

        Implements Reader Snapshot Consistency:
        1. Read manifest version at query start
        2. Execute query
        3. Verify manifest version unchanged (retry if changed)

        Args:
            start_date: Start of datadate range (inclusive).
            end_date: End of datadate range (inclusive).
            gvkeys: Filter by GVKEYs (None = all).
            as_of_date: REQUIRED Point-in-time filter - only return data AVAILABLE
                       by this date. A record with datadate is available when:
                       as_of_date >= datadate + filing_lag_days
                       This parameter is required to prevent look-ahead bias.
                       For backtests, pass the simulation date.
                       For live trading, pass date.today().
            filing_lag_days: Override default 90-day filing lag.
            columns: Columns to return (None = all). Validated against schema.

        Returns:
            DataFrame with requested columns.

        Raises:
            ValueError: If invalid columns requested.
            DataNotFoundError: If no manifest found (run sync first).
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        return self._get_fundamentals(
            dataset=self.DATASET_ANNUAL,
            start_date=start_date,
            end_date=end_date,
            gvkeys=gvkeys,
            as_of_date=as_of_date,
            filing_lag_days=filing_lag_days or self.DEFAULT_ANNUAL_FILING_LAG_DAYS,
            columns=columns,
            valid_columns=VALID_ANNUAL_COLUMNS,
            schema=COMPUSTAT_ANNUAL_SCHEMA,
            column_order=COMPUSTAT_ANNUAL_COLUMNS,
        )

    def get_quarterly_fundamentals(
        self,
        start_date: date,
        end_date: date,
        gvkeys: list[str] | None = None,
        *,
        as_of_date: date,
        filing_lag_days: int | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Get quarterly fundamental data for securities in date range.

        Implements Reader Snapshot Consistency:
        1. Read manifest version at query start
        2. Execute query
        3. Verify manifest version unchanged (retry if changed)

        Args:
            start_date: Start of datadate range (inclusive).
            end_date: End of datadate range (inclusive).
            gvkeys: Filter by GVKEYs (None = all).
            as_of_date: REQUIRED Point-in-time filter - only return data AVAILABLE
                       by this date. A record with datadate is available when:
                       as_of_date >= datadate + filing_lag_days
                       This parameter is required to prevent look-ahead bias.
                       For backtests, pass the simulation date.
                       For live trading, pass date.today().
            filing_lag_days: Override default 45-day filing lag.
            columns: Columns to return (None = all). Validated against schema.

        Returns:
            DataFrame with requested columns.

        Raises:
            ValueError: If invalid columns requested.
            DataNotFoundError: If no manifest found (run sync first).
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        return self._get_fundamentals(
            dataset=self.DATASET_QUARTERLY,
            start_date=start_date,
            end_date=end_date,
            gvkeys=gvkeys,
            as_of_date=as_of_date,
            filing_lag_days=filing_lag_days or self.DEFAULT_QUARTERLY_FILING_LAG_DAYS,
            columns=columns,
            valid_columns=VALID_QUARTERLY_COLUMNS,
            schema=COMPUSTAT_QUARTERLY_SCHEMA,
            column_order=COMPUSTAT_QUARTERLY_COLUMNS,
        )

    def _get_fundamentals(
        self,
        dataset: str,
        start_date: date,
        end_date: date,
        gvkeys: list[str] | None,
        as_of_date: date,
        filing_lag_days: int,
        columns: list[str] | None,
        valid_columns: set[str],
        schema: dict[str, type[pl.DataType]],
        column_order: tuple[str, ...],
    ) -> pl.DataFrame:
        """Internal method for fundamentals queries.

        Handles both annual and quarterly with parameterized dataset.

        Args:
            as_of_date: REQUIRED point-in-time filter. This prevents look-ahead bias
                       by only returning data that was available on the given date.
        """
        # Validate columns
        if columns is not None:
            invalid = set(columns) - valid_columns
            if invalid:
                raise ValueError(f"Invalid columns: {invalid}. Valid: {valid_columns}")

        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest(dataset)
        pinned_version = manifest.manifest_version

        # Get partition paths from manifest (not filesystem)
        partition_paths = self._get_partition_paths_from_manifest(
            manifest, start_date, end_date
        )

        if not partition_paths:
            return self._empty_result(columns, schema)

        # Build and execute query with parameterization
        result = self._execute_fundamentals_query(
            partition_paths=partition_paths,
            start_date=start_date,
            end_date=end_date,
            gvkeys=gvkeys,
            as_of_date=as_of_date,
            filing_lag_days=filing_lag_days,
            columns=columns,
            column_order=column_order,
        )

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest(dataset)
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        return result

    def gvkey_to_ticker(
        self,
        gvkey: str,
        as_of_date: date,
        dataset: Literal["annual", "quarterly"] = "quarterly",
    ) -> str:
        """Map GVKEY to ticker symbol at given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Queries the fundamentals data to find the ticker from the most recent
        AVAILABLE filing for this GVKEY as of as_of_date.

        Args:
            gvkey: GVKEY to look up.
            as_of_date: Date for the lookup (REQUIRED).
            dataset: Which dataset to query - determines filing lag.
                    "quarterly" uses 45-day lag (higher resolution).
                    "annual" uses 90-day lag.

        Returns:
            Ticker symbol from the most recent available filing.

        Raises:
            DataNotFoundError: If GVKEY not found or no available filings.
            ManifestVersionChangedError: If manifest changes during query (retry).

        Note:
            Ticker resolution is limited to filing frequency (quarterly/annual).
            Ticker changes between filings will be delayed until next filing.
            For higher resolution, consider using comp.names table (future enhancement).
        """
        dataset_name = (
            self.DATASET_QUARTERLY if dataset == "quarterly" else self.DATASET_ANNUAL
        )
        filing_lag = (
            self.DEFAULT_QUARTERLY_FILING_LAG_DAYS
            if dataset == "quarterly"
            else self.DEFAULT_ANNUAL_FILING_LAG_DAYS
        )

        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest(dataset_name)
        pinned_version = manifest.manifest_version

        # Get all paths to search for most recent available record
        paths = self._get_validated_paths_from_manifest(manifest)

        if not paths:
            raise DataNotFoundError(f"No {dataset} data available")

        conn = self._ensure_connection()

        # Compute the latest datadate that would be available as of as_of_date
        # PIT rule: datadate + filing_lag <= as_of_date
        # So: datadate <= as_of_date - filing_lag
        latest_available_datadate = as_of_date - timedelta(days=filing_lag)

        # Query for most recent available record for this GVKEY
        query = """
            SELECT tic
            FROM read_parquet($paths)
            WHERE gvkey = $gvkey AND datadate <= $latest_datadate
            ORDER BY datadate DESC
            LIMIT 1
        """
        result = conn.execute(
            query,
            {
                "paths": [str(p) for p in paths],
                "gvkey": gvkey,
                "latest_datadate": latest_available_datadate,
            },
        ).pl()

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest(dataset_name)
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        if result.is_empty():
            raise DataNotFoundError(
                f"GVKEY '{gvkey}' not found or no available filings as of {as_of_date} "
                f"(with {filing_lag}-day lag)"
            )

        ticker = result["tic"][0]
        if ticker is None:
            raise DataNotFoundError(
                f"GVKEY '{gvkey}' has no ticker in available filings as of {as_of_date}"
            )

        return str(ticker)

    def ticker_to_gvkey(
        self,
        ticker: str,
        as_of_date: date,
        dataset: Literal["annual", "quarterly"] = "quarterly",
    ) -> str:
        """Map ticker symbol to GVKEY at given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Queries the fundamentals data to find the GVKEY that had this ticker
        in the most recent AVAILABLE filing as of as_of_date.

        Args:
            ticker: Ticker symbol to look up.
            as_of_date: Date for the lookup (REQUIRED).
            dataset: Which dataset to query - determines filing lag.
                    "quarterly" uses 45-day lag (higher resolution).
                    "annual" uses 90-day lag.

        Returns:
            GVKEY that the ticker referred to in available filings.

        Raises:
            DataNotFoundError: If ticker not found in available filings.
            AmbiguousGVKEYError: If ticker maps to multiple GVKEYs.
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        dataset_name = (
            self.DATASET_QUARTERLY if dataset == "quarterly" else self.DATASET_ANNUAL
        )
        filing_lag = (
            self.DEFAULT_QUARTERLY_FILING_LAG_DAYS
            if dataset == "quarterly"
            else self.DEFAULT_ANNUAL_FILING_LAG_DAYS
        )

        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest(dataset_name)
        pinned_version = manifest.manifest_version

        paths = self._get_validated_paths_from_manifest(manifest)

        if not paths:
            raise DataNotFoundError(f"No {dataset} data available")

        conn = self._ensure_connection()

        # Compute the latest datadate that would be available
        latest_available_datadate = as_of_date - timedelta(days=filing_lag)

        # Find all GVKEYs with this ticker in AVAILABLE records
        # Get the most recent available record per GVKEY, then filter by ticker
        query = """
            WITH ranked AS (
                SELECT
                    gvkey,
                    tic,
                    datadate,
                    ROW_NUMBER() OVER (PARTITION BY gvkey ORDER BY datadate DESC) as rn
                FROM read_parquet($paths)
                WHERE datadate <= $latest_datadate
            )
            SELECT DISTINCT gvkey
            FROM ranked
            WHERE rn = 1 AND UPPER(tic) = $ticker
        """
        result = conn.execute(
            query,
            {
                "paths": [str(p) for p in paths],
                "ticker": ticker.upper(),
                "latest_datadate": latest_available_datadate,
            },
        ).pl()

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest(dataset_name)
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        if result.is_empty():
            raise DataNotFoundError(
                f"Ticker '{ticker}' not found in available filings as of {as_of_date} "
                f"(with {filing_lag}-day lag)"
            )

        gvkeys = result["gvkey"].to_list()
        if len(gvkeys) > 1:
            raise AmbiguousGVKEYError(ticker, as_of_date, gvkeys)

        return str(gvkeys[0])

    def get_security_universe(
        self,
        as_of_date: date,
        include_inactive: bool = True,
        dataset: Literal["annual", "quarterly"] = "annual",
    ) -> pl.DataFrame:
        """Get universe of GVKEYs as of given date.

        Implements Reader Snapshot Consistency: pins manifest version at start
        and verifies it hasn't changed after query execution.

        Point-in-time logic (LAG-ADJUSTED):
        1. Compute first_datadate/last_datadate from fundamentals
        2. Apply filing lag: first_available = first_datadate + lag
        3. GVKEY included if: first_available <= as_of_date
        4. If include_inactive=False: also require last_available >= as_of_date
        5. Return point-in-time ticker/conm from most recent AVAILABLE filing

        Args:
            as_of_date: Reference date for universe construction (REQUIRED).
            include_inactive: If True (default), include GVKEYs with stale filings.
                            If False, only include GVKEYs with recent filings.
            dataset: Which dataset to use ("annual" or "quarterly").
                    Determines filing lag (90 vs 45 days).

        Returns:
            DataFrame with: gvkey, tic, conm, first_available, last_available
            where tic/conm are point-in-time values from most recent available filing.

        Raises:
            ManifestVersionChangedError: If manifest changes during query (retry).
        """
        dataset_name = (
            self.DATASET_QUARTERLY if dataset == "quarterly" else self.DATASET_ANNUAL
        )
        filing_lag = (
            self.DEFAULT_QUARTERLY_FILING_LAG_DAYS
            if dataset == "quarterly"
            else self.DEFAULT_ANNUAL_FILING_LAG_DAYS
        )

        # Pin manifest version for snapshot consistency
        manifest = self._get_manifest(dataset_name)
        pinned_version = manifest.manifest_version

        # Get metadata with first/last dates
        metadata = self._get_security_metadata(manifest, dataset_name)

        if metadata.is_empty():
            # Return empty DataFrame with expected schema
            return pl.DataFrame(
                schema={
                    "gvkey": pl.Utf8,
                    "tic": pl.Utf8,
                    "conm": pl.Utf8,
                    "first_available": pl.Date,
                    "last_available": pl.Date,
                }
            )

        # Compute lag-adjusted availability dates
        metadata = metadata.with_columns(
            [
                (pl.col("first_datadate") + timedelta(days=filing_lag)).alias(
                    "first_available"
                ),
                (pl.col("last_datadate") + timedelta(days=filing_lag)).alias(
                    "last_available"
                ),
            ]
        )

        # Filter: GVKEY must have first_available <= as_of_date
        # (i.e., at least one filing became public by as_of_date)
        filtered = metadata.filter(pl.col("first_available") <= as_of_date)

        if not include_inactive:
            # Also require: last_available >= as_of_date
            # (i.e., most recent filing is still "recent" as of as_of_date)
            filtered = filtered.filter(pl.col("last_available") >= as_of_date)

        if filtered.is_empty():
            # Verify manifest and return empty result
            current_manifest = self._get_manifest(dataset_name)
            if current_manifest.manifest_version != pinned_version:
                raise ManifestVersionChangedError(
                    f"Manifest version changed from {pinned_version} to "
                    f"{current_manifest.manifest_version} during query"
                )
            return pl.DataFrame(
                schema={
                    "gvkey": pl.Utf8,
                    "tic": pl.Utf8,
                    "conm": pl.Utf8,
                    "first_available": pl.Date,
                    "last_available": pl.Date,
                }
            )

        # Get point-in-time ticker/conm for the filtered GVKEYs
        # Query data for most recent AVAILABLE record per GVKEY
        all_paths = self._get_validated_paths_from_manifest(manifest)

        if not all_paths:
            # Verify manifest and return filtered result without PIT ticker
            current_manifest = self._get_manifest(dataset_name)
            if current_manifest.manifest_version != pinned_version:
                raise ManifestVersionChangedError(
                    f"Manifest version changed from {pinned_version} to "
                    f"{current_manifest.manifest_version} during query"
                )
            return filtered.select(
                ["gvkey", "tic", "conm", "first_available", "last_available"]
            )

        conn = self._ensure_connection()
        filtered_gvkeys = filtered["gvkey"].to_list()

        # Compute the latest datadate that would be available
        latest_available_datadate = as_of_date - timedelta(days=filing_lag)

        # Get the ticker/conm from most recent available record per GVKEY
        query = """
            WITH ranked AS (
                SELECT
                    gvkey,
                    tic,
                    conm,
                    datadate,
                    ROW_NUMBER() OVER (PARTITION BY gvkey ORDER BY datadate DESC) as rn
                FROM read_parquet($paths)
                WHERE gvkey = ANY($gvkeys) AND datadate <= $latest_datadate
            )
            SELECT gvkey, tic, conm
            FROM ranked
            WHERE rn = 1
        """
        pit_data = conn.execute(
            query,
            {
                "paths": [str(p) for p in all_paths],
                "gvkeys": filtered_gvkeys,
                "latest_datadate": latest_available_datadate,
            },
        ).pl()

        # Join point-in-time ticker/conm with availability dates
        result = filtered.select(
            ["gvkey", "first_available", "last_available"]
        ).join(pit_data, on="gvkey", how="left")

        # Verify manifest version unchanged (snapshot consistency)
        current_manifest = self._get_manifest(dataset_name)
        if current_manifest.manifest_version != pinned_version:
            raise ManifestVersionChangedError(
                f"Manifest version changed from {pinned_version} to "
                f"{current_manifest.manifest_version} during query"
            )

        # Reorder columns to match expected schema
        return result.select(
            ["gvkey", "tic", "conm", "first_available", "last_available"]
        )

    def _get_manifest(self, dataset: str) -> SyncManifest:
        """Load manifest for specified dataset.

        Args:
            dataset: Dataset name (compustat_annual or compustat_quarterly).

        Raises:
            DataNotFoundError: If no manifest (run sync first).
        """
        manifest = self.manifest_manager.load_manifest(dataset)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for '{dataset}'. Run full_sync first."
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
        Use this for operations that need all data (metadata, mappings).

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

    def _execute_fundamentals_query(
        self,
        partition_paths: list[Path],
        start_date: date,
        end_date: date,
        gvkeys: list[str] | None,
        as_of_date: date | None,
        filing_lag_days: int,
        columns: list[str] | None,
        column_order: tuple[str, ...],
    ) -> pl.DataFrame:
        """Execute parameterized DuckDB query for fundamentals.

        All filtering uses parameterized queries to prevent SQL injection.
        """
        conn = self._ensure_connection()

        # Build column list
        if columns is None:
            col_expr = ", ".join(column_order)
        else:
            col_expr = ", ".join(columns)

        # Build WHERE clause with parameters
        params: dict[str, Any] = {
            "paths": [str(p) for p in partition_paths],
            "start_date": start_date,
            "end_date": end_date,
        }

        where_clauses = ["datadate >= $start_date", "datadate <= $end_date"]

        if gvkeys is not None:
            if len(gvkeys) == 0:
                # Empty gvkeys list - return empty result
                return pl.DataFrame(
                    schema={
                        c: COMPUSTAT_ANNUAL_SCHEMA.get(c, COMPUSTAT_QUARTERLY_SCHEMA.get(c, pl.Utf8))
                        for c in (columns or column_order)
                    }
                )
            params["gvkeys"] = gvkeys
            where_clauses.append("gvkey = ANY($gvkeys)")

        if as_of_date is not None:
            # Point-in-time: only include records that are AVAILABLE
            # PIT rule: datadate + filing_lag <= as_of_date
            # Equivalent: datadate <= as_of_date - filing_lag
            latest_available_datadate = as_of_date - timedelta(days=filing_lag_days)
            params["pit_cutoff"] = latest_available_datadate
            where_clauses.append("datadate <= $pit_cutoff")

        where_sql = " AND ".join(where_clauses)

        query = f"""
            SELECT {col_expr}
            FROM read_parquet($paths)
            WHERE {where_sql}
            ORDER BY datadate, gvkey
        """

        return conn.execute(query, params).pl()

    def _get_security_metadata(
        self, manifest: SyncManifest, dataset: str
    ) -> pl.DataFrame:
        """Get or compute security metadata (first/last datadate).

        Caches the result for efficiency. Cache is tied to manifest version
        and automatically rebuilds when manifest version changes.

        Args:
            manifest: Manifest to use.
            dataset: Dataset name (for cache selection).

        Returns:
            DataFrame with: gvkey, tic, conm, first_datadate, last_datadate
        """
        # Select appropriate cache
        if dataset == self.DATASET_ANNUAL:
            cache = self._annual_metadata
            cache_version = self._annual_metadata_version
        else:
            cache = self._quarterly_metadata
            cache_version = self._quarterly_metadata_version

        # Check if cache is valid (matches current manifest version)
        if cache is not None and cache_version == manifest.manifest_version:
            return cache

        # Cache is stale or missing - rebuild
        logger.debug(
            "Rebuilding security metadata cache (version %s -> %s)",
            cache_version,
            manifest.manifest_version,
        )

        # Security: Use validated paths to prevent path traversal
        paths = self._get_validated_paths_from_manifest(manifest)

        if not paths:
            empty_result = pl.DataFrame(
                schema={
                    "gvkey": pl.Utf8,
                    "tic": pl.Utf8,
                    "conm": pl.Utf8,
                    "first_datadate": pl.Date,
                    "last_datadate": pl.Date,
                }
            )
            # Update cache
            if dataset == self.DATASET_ANNUAL:
                self._annual_metadata = empty_result
                self._annual_metadata_version = manifest.manifest_version
            else:
                self._quarterly_metadata = empty_result
                self._quarterly_metadata_version = manifest.manifest_version
            return empty_result

        conn = self._ensure_connection()

        # Compute first/last dates per GVKEY, get latest ticker/conm
        query = """
            SELECT
                gvkey,
                LAST(tic ORDER BY datadate) AS tic,
                LAST(conm ORDER BY datadate) AS conm,
                MIN(datadate) AS first_datadate,
                MAX(datadate) AS last_datadate
            FROM read_parquet($paths)
            GROUP BY gvkey
        """
        result = conn.execute(query, {"paths": [str(p) for p in paths]}).pl()

        # Update cache
        if dataset == self.DATASET_ANNUAL:
            self._annual_metadata = result
            self._annual_metadata_version = manifest.manifest_version
        else:
            self._quarterly_metadata = result
            self._quarterly_metadata_version = manifest.manifest_version

        return result

    def _ensure_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create thread-local DuckDB connection.

        Thread Safety:
            DuckDB connections are NOT thread-safe. This method uses thread-local
            storage to provide each thread with its own connection, preventing
            data corruption when multiple threads access the provider concurrently.

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

    def _empty_result(
        self,
        columns: list[str] | None,
        schema: dict[str, type[pl.DataType]],
    ) -> pl.DataFrame:
        """Return empty DataFrame with correct schema."""
        if columns is None:
            return pl.DataFrame(schema=schema)
        else:
            return pl.DataFrame(schema={c: schema[c] for c in columns})

    def invalidate_cache(self) -> None:
        """Invalidate cached security metadata.

        Call this after a sync completes to refresh metadata.
        The next query will recompute first/last dates.

        Note: The cache is also automatically invalidated when the manifest
        version changes, so explicit invalidation is only needed for edge cases.
        """
        self._annual_metadata = None
        self._annual_metadata_version = None
        self._quarterly_metadata = None
        self._quarterly_metadata_version = None
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

    def __enter__(self) -> CompustatLocalProvider:
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

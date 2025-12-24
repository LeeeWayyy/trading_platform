"""
TAQ Data Storage and Sync Module.

This module provides:
- TAQ data storage classes for aggregates and samples
- Sync functions for WRDS TAQ data ingestion
- Integration with SyncManager patterns from P4T1

Storage Layout:
    data/taq/
        aggregates/
            1min_bars/YYYYMM.parquet
            daily_rv/YYYYMM.parquet
            spread_stats/YYYYMM.parquet
        samples/YYYY-MM-DD/<SYMBOL>.parquet
        tmp/                         # staging for atomic writes
        quarantine/                  # failed writes

Schemas (registered with SchemaRegistry):
    - taq_1min_bars: ts, symbol, open, high, low, close, volume, vwap, date
    - taq_daily_rv: date, symbol, rv_5m, rv_30m, obs
    - taq_spread_stats: date, symbol, qwap_spread, ewas, quotes, trades
    - taq_ticks: ts, symbol, bid, ask, bid_size, ask_size, trade_px, trade_size, cond
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import polars as pl
from pydantic import BaseModel

from libs.data_quality.exceptions import DiskSpaceError, SchemaError
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.schema import SchemaRegistry
from libs.data_quality.types import DiskSpaceStatus
from libs.data_quality.validation import DataValidator
from libs.data_quality.versioning import DatasetVersionManager

if TYPE_CHECKING:
    from libs.data_providers.wrds_client import WRDSClient
    from libs.data_quality.types import LockToken

logger = logging.getLogger(__name__)


# =============================================================================
# TAQ Schema Definitions
# =============================================================================

TAQ_1MIN_BARS_SCHEMA: dict[str, str] = {
    "ts": "datetime[ns]",
    "symbol": "utf8",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "int64",
    "vwap": "float64",
    "date": "date",
}

TAQ_DAILY_RV_SCHEMA: dict[str, str] = {
    "date": "date",
    "symbol": "utf8",
    "rv_5m": "float64",
    "rv_30m": "float64",
    "obs": "int64",
}

TAQ_SPREAD_STATS_SCHEMA: dict[str, str] = {
    "date": "date",
    "symbol": "utf8",
    "qwap_spread": "float64",
    "ewas": "float64",
    "quotes": "int64",
    "trades": "int64",
}

TAQ_TICKS_SCHEMA: dict[str, str] = {
    "ts": "datetime[ns]",
    "symbol": "utf8",
    "bid": "float64",
    "ask": "float64",
    "bid_size": "int64",
    "ask_size": "int64",
    "trade_px": "float64",
    "trade_size": "int64",
    "cond": "utf8",
}

# Map dataset names to schemas
# NOTE: Keep these definitions in sync with the schemas persisted via
# libs.data_quality.schema.SchemaRegistry. The registration helper below
# registers the canonical schemas used by data quality checks.
TAQ_SCHEMAS: dict[str, dict[str, str]] = {
    "taq_1min_bars": TAQ_1MIN_BARS_SCHEMA,
    "taq_daily_rv": TAQ_DAILY_RV_SCHEMA,
    "taq_spread_stats": TAQ_SPREAD_STATS_SCHEMA,
    "taq_ticks": TAQ_TICKS_SCHEMA,
}

# Primary keys for deduplication
TAQ_PRIMARY_KEYS: dict[str, list[str]] = {
    "taq_1min_bars": ["ts", "symbol"],
    "taq_daily_rv": ["date", "symbol"],
    "taq_spread_stats": ["date", "symbol"],
    "taq_ticks": ["ts", "symbol"],
}


def register_taq_schemas(registry: SchemaRegistry) -> None:
    """Register TAQ schemas with the schema registry.

    Args:
        registry: SchemaRegistry instance to register schemas with.
    """
    for dataset, schema in TAQ_SCHEMAS.items():
        existing = registry.get_expected_schema(dataset)
        if existing is None:
            registry.register_schema(
                dataset=dataset,
                schema=schema,
                description=f"TAQ {dataset.replace('taq_', '')} schema",
            )
            logger.info("Registered schema: %s", dataset)


# =============================================================================
# Sync Progress Tracking
# =============================================================================


class TAQSyncProgress(BaseModel):
    """Tracks TAQ sync progress for resume capability.

    Attributes:
        dataset: Dataset being synced (e.g., "taq_1min_bars").
        tier: Tier being synced ("aggregates" or "samples").
        started_at: UTC timestamp when sync started.
        last_checkpoint: UTC timestamp of last successful partition.
        partitions_completed: Partitions that have been successfully synced.
        partitions_remaining: Partitions still to be synced.
        total_rows_synced: Cumulative row count.
        status: Current sync status.
    """

    dataset: str
    tier: Literal["aggregates", "samples"]
    started_at: datetime.datetime
    last_checkpoint: datetime.datetime
    partitions_completed: list[str]
    partitions_remaining: list[str]
    total_rows_synced: int
    status: Literal["running", "paused", "completed", "failed"]


# =============================================================================
# TAQ Storage Manager
# =============================================================================


class TAQStorageManager:
    """Manages TAQ data storage and synchronization.

    This manager:
    - Syncs TAQ data from WRDS to local Parquet files
    - Supports two tiers: aggregates (1min bars, RV, spreads) and samples (ticks)
    - Uses atomic writes with disk guards
    - Integrates with ManifestManager and DatasetVersionManager for PIT queries

    Example:
        manager = TAQStorageManager(
            wrds_client=client,
            storage_path=Path("data/taq"),
            manifest_manager=manifest_mgr,
            version_manager=version_mgr,
        )
        manifest = manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL", "MSFT"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
    """

    # Storage layout paths
    AGGREGATES_DIR = "aggregates"
    SAMPLES_DIR = "samples"
    TMP_DIR = "tmp"
    QUARANTINE_DIR = "quarantine"

    # Disk space thresholds
    DISK_WARNING_PCT = 0.80
    DISK_CRITICAL_PCT = 0.90
    DISK_BLOCKED_PCT = 0.95
    DISK_SAFETY_MULTIPLIER = 2.0  # 2x for temp + final file

    # SLO thresholds
    SYNC_SLO_MINUTES = 30  # SP500 x 1 month should complete in 30 min

    # Bytes per row estimate for disk space calculation
    BYTES_PER_ROW_ESTIMATE = 150

    # Symbol validation pattern: alphanumeric, dots, and hyphens only (1-10 chars)
    # Examples: AAPL, BRK.B, BRK-A, META
    SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

    def __init__(
        self,
        wrds_client: WRDSClient | None,
        storage_path: Path,
        lock_dir: Path,
        manifest_manager: ManifestManager,
        version_manager: DatasetVersionManager,
        validator: DataValidator,
        schema_registry: SchemaRegistry,
    ) -> None:
        """Initialize TAQ storage manager.

        Args:
            wrds_client: Connected WRDS client (optional for read-only operations).
            storage_path: Root path for TAQ data storage.
            lock_dir: Directory for lock files.
            manifest_manager: For manifest operations.
            version_manager: For snapshot/versioning operations.
            validator: For data validation.
            schema_registry: For schema drift detection.
        """
        self.wrds_client = wrds_client
        self.storage_path = Path(storage_path)
        self.lock_dir = Path(lock_dir)
        self.manifest_manager = manifest_manager
        self.version_manager = version_manager
        self.validator = validator
        self.schema_registry = schema_registry

        # Ensure directories exist
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_path / self.TMP_DIR).mkdir(parents=True, exist_ok=True)
        (self.storage_path / self.QUARANTINE_DIR).mkdir(parents=True, exist_ok=True)

        # Create tier directories
        for tier in [self.AGGREGATES_DIR, self.SAMPLES_DIR]:
            (self.storage_path / tier).mkdir(parents=True, exist_ok=True)

        # Create aggregate dataset directories
        for dataset in ["1min_bars", "daily_rv", "spread_stats"]:
            (self.storage_path / self.AGGREGATES_DIR / dataset).mkdir(parents=True, exist_ok=True)

    def _sanitize_symbol(self, symbol: str) -> str:
        """Sanitize and validate symbol to prevent path traversal attacks.

        Args:
            symbol: Raw symbol string.

        Returns:
            Upper-cased, validated symbol.

        Raises:
            ValueError: If symbol contains invalid characters or patterns.
        """
        # Upper-case and strip whitespace
        sanitized = symbol.upper().strip()

        # Check for empty or path traversal patterns
        if not sanitized or sanitized in (".", ".."):
            raise ValueError(f"Invalid symbol: {symbol!r}")

        # Validate against allowed pattern
        if not self.SYMBOL_PATTERN.match(sanitized):
            raise ValueError(
                f"Invalid symbol '{symbol}': must be 1-10 chars, "
                f"alphanumeric, dots, or hyphens only (e.g., AAPL, BRK.B)"
            )

        return sanitized

    def _sanitize_symbols(self, symbols: list[str]) -> list[str]:
        """Sanitize and validate a list of symbols.

        Args:
            symbols: Raw symbol list.

        Returns:
            List of validated, upper-cased symbols.

        Raises:
            ValueError: If any symbol is invalid.
        """
        return [self._sanitize_symbol(s) for s in symbols]

    def sync_aggregates(
        self,
        dataset: Literal["1min_bars", "daily_rv", "spread_stats"],
        symbols: list[str],
        start_date: datetime.date,
        end_date: datetime.date,
        incremental: bool = True,
        create_snapshot: bool = False,
    ) -> SyncManifest:
        """Sync TAQ aggregate data from WRDS.

        Args:
            dataset: Which aggregate dataset to sync.
            symbols: List of symbols to sync.
            start_date: Start of date range.
            end_date: End of date range.
            incremental: If True, only sync new data since last manifest.
            create_snapshot: If True, create a versioned snapshot after sync.

        Returns:
            SyncManifest for the completed sync.

        Raises:
            DiskSpaceError: If insufficient disk space.
            SchemaError: If breaking schema drift detected.
            ValueError: If WRDS client not configured.
        """
        if self.wrds_client is None:
            raise ValueError("WRDS client required for sync operations")

        # Sanitize symbols to prevent path traversal
        symbols = self._sanitize_symbols(symbols)

        manifest_dataset = f"taq_{dataset}"
        sync_start = datetime.datetime.now(datetime.UTC)

        logger.info(
            "Starting TAQ aggregates sync",
            extra={
                "component": "taq_sync",
                "event": "sync.aggregates.start",
                "dataset": dataset,
                "symbols_count": len(symbols),
                "start_date": str(start_date),
                "end_date": str(end_date),
                "incremental": incremental,
            },
        )

        # Use ManifestManager's lock to ensure lock_token.lock_path matches
        # what save_manifest() expects (manifest_manager.lock_dir / f"{dataset}.lock")
        with self.manifest_manager.acquire_lock(
            dataset=manifest_dataset,
            writer_id=f"taq_sync_{dataset}",
            timeout_seconds=60.0,
        ) as lock_token:
            # Estimate disk space needed
            estimated_rows = self._estimate_rows(dataset, symbols, start_date, end_date)
            self._check_disk_space(estimated_rows)

            # Build partition list (month-based)
            partitions = self._build_month_partitions(start_date, end_date)

            # Check for incremental resume
            current_manifest = self.manifest_manager.load_manifest(manifest_dataset)
            if incremental and current_manifest:
                # Filter partitions to only include new data
                partitions = self._filter_new_partitions(partitions, current_manifest.end_date)

            file_paths: list[str] = []
            total_rows = 0

            # Carry forward existing files if incremental
            if incremental and current_manifest:
                file_paths = list(current_manifest.file_paths)
                total_rows = current_manifest.row_count

            for partition in partitions:
                path, rows = self._sync_aggregate_partition(
                    dataset=dataset,
                    symbols=symbols,
                    partition=partition,
                    lock_token=lock_token,
                )
                file_paths.append(str(path))
                total_rows += rows

            # Create manifest
            manifest = self._create_manifest(
                dataset=manifest_dataset,
                file_paths=file_paths,
                row_count=total_rows,
                start_date=start_date,
                end_date=end_date,
            )

            # Save manifest
            self.manifest_manager.save_manifest(manifest, lock_token)

            # Check SLO
            duration = datetime.datetime.now(datetime.UTC) - sync_start
            if duration.total_seconds() > self.SYNC_SLO_MINUTES * 60:
                logger.warning(
                    "TAQ sync exceeded SLO",
                    extra={
                        "component": "taq_sync",
                        "event": "sync.slo_breach",
                        "dataset": dataset,
                        "duration_minutes": duration.total_seconds() / 60,
                        "slo_minutes": self.SYNC_SLO_MINUTES,
                    },
                )

            # Create snapshot if requested
            if create_snapshot:
                snapshot_tag = f"taq_{dataset}_{end_date.strftime('%Y%m%d')}"
                self.version_manager.create_snapshot(
                    version_tag=snapshot_tag,
                    datasets=[manifest_dataset],
                )
                logger.info("Created snapshot: %s", snapshot_tag)

            logger.info(
                "TAQ aggregates sync completed",
                extra={
                    "component": "taq_sync",
                    "event": "sync.aggregates.complete",
                    "dataset": dataset,
                    "total_rows": total_rows,
                    "file_count": len(file_paths),
                    "duration_seconds": duration.total_seconds(),
                },
            )

            return manifest

    def sync_samples(
        self,
        sample_date: datetime.date,
        symbols: list[str],
        create_snapshot: bool = False,
    ) -> SyncManifest:
        """Sync TAQ tick samples for a specific date.

        Args:
            sample_date: Date to sync tick data for.
            symbols: List of symbols to sync.
            create_snapshot: If True, create a versioned snapshot after sync.

        Returns:
            SyncManifest for the completed sync.

        Raises:
            DiskSpaceError: If insufficient disk space.
            ValueError: If WRDS client not configured.
        """
        if self.wrds_client is None:
            raise ValueError("WRDS client required for sync operations")

        # Sanitize symbols to prevent path traversal
        symbols = self._sanitize_symbols(symbols)

        manifest_dataset = f"taq_samples_{sample_date.strftime('%Y%m%d')}"

        logger.info(
            "Starting TAQ samples sync",
            extra={
                "component": "taq_sync",
                "event": "sync.samples.start",
                "sample_date": str(sample_date),
                "symbols_count": len(symbols),
            },
        )

        # Use ManifestManager's lock to ensure lock_token.lock_path matches
        # what save_manifest() expects
        with self.manifest_manager.acquire_lock(
            dataset=manifest_dataset,
            writer_id=f"taq_sync_samples_{sample_date.strftime('%Y%m%d')}",
            timeout_seconds=60.0,
        ) as lock_token:
            # Estimate disk space (tick data is larger)
            estimated_rows = len(symbols) * 50_000  # ~50k ticks per symbol per day
            self._check_disk_space(estimated_rows)

            # Create date directory
            date_dir = self.storage_path / self.SAMPLES_DIR / sample_date.strftime("%Y-%m-%d")
            date_dir.mkdir(parents=True, exist_ok=True)

            file_paths: list[str] = []
            total_rows = 0

            for symbol in symbols:
                path, rows = self._sync_sample_symbol(
                    sample_date=sample_date,
                    symbol=symbol,
                    date_dir=date_dir,
                    lock_token=lock_token,
                )
                file_paths.append(str(path))
                total_rows += rows

            # Create manifest
            manifest = self._create_manifest(
                dataset=manifest_dataset,
                file_paths=file_paths,
                row_count=total_rows,
                start_date=sample_date,
                end_date=sample_date,
            )

            # Save manifest
            self.manifest_manager.save_manifest(manifest, lock_token)

            # Create snapshot if requested
            if create_snapshot:
                snapshot_tag = f"taq_samples_{sample_date.strftime('%Y%m%d')}"
                self.version_manager.create_snapshot(
                    version_tag=snapshot_tag,
                    datasets=[manifest_dataset],
                )
                logger.info("Created snapshot: %s", snapshot_tag)

            logger.info(
                "TAQ samples sync completed",
                extra={
                    "component": "taq_sync",
                    "event": "sync.samples.complete",
                    "sample_date": str(sample_date),
                    "total_rows": total_rows,
                    "symbol_count": len(symbols),
                },
            )

            return manifest

    def cleanup(self, retention_days: int = 365) -> int:
        """Clean up old TAQ data beyond retention period.

        Args:
            retention_days: Days to retain data.

        Returns:
            Number of files deleted.
        """
        cutoff = datetime.date.today() - datetime.timedelta(days=retention_days)
        deleted_count = 0

        # Clean up samples (date-based directories)
        samples_dir = self.storage_path / self.SAMPLES_DIR
        if samples_dir.exists():
            for date_dir in samples_dir.iterdir():
                if date_dir.is_dir():
                    try:
                        dir_date = datetime.datetime.strptime(date_dir.name, "%Y-%m-%d").date()
                        if dir_date < cutoff:
                            shutil.rmtree(date_dir)
                            deleted_count += 1
                            logger.info("Deleted old samples: %s", date_dir.name)
                    except ValueError:
                        continue

        # Clean up quarantine
        quarantine_dir = self.storage_path / self.QUARANTINE_DIR
        if quarantine_dir.exists():
            for item in quarantine_dir.iterdir():
                if item.is_dir():
                    try:
                        # Parse timestamp from directory name (YYYYMMDD_HHMMSS_*)
                        ts_str = item.name[:15]
                        item_date = datetime.datetime.strptime(ts_str, "%Y%m%d_%H%M%S").date()
                        if item_date < cutoff:
                            shutil.rmtree(item)
                            deleted_count += 1
                            logger.info("Deleted quarantined: %s", item.name)
                    except ValueError:
                        continue

        return deleted_count

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _sync_aggregate_partition(
        self,
        dataset: str,
        symbols: list[str],
        partition: str,
        lock_token: LockToken,
    ) -> tuple[Path, int]:
        """Sync a single month partition for aggregates.

        Args:
            dataset: Aggregate dataset name.
            symbols: Symbols to sync.
            partition: Partition identifier (YYYYMM).
            lock_token: Active lock token.

        Returns:
            Tuple of (file_path, row_count).
        """
        schema_name = f"taq_{dataset}"
        output_dir = self.storage_path / self.AGGREGATES_DIR / dataset
        output_path = output_dir / f"{partition}.parquet"

        # Build and execute query (wrds_client checked non-None by caller)
        assert self.wrds_client is not None
        query, params = self._build_aggregates_query(dataset, symbols, partition)
        df = self.wrds_client.execute_query(query, params)

        if df.is_empty():
            logger.warning(
                "No data for partition",
                extra={"dataset": dataset, "partition": partition},
            )
            # Create empty file with correct schema
            df = self._create_empty_df(schema_name)

        # Validate schema
        current_schema = {col: str(df.schema[col]) for col in df.columns}
        drift = self.schema_registry.detect_drift(schema_name, current_schema)

        if drift.is_breaking:
            logger.error(
                "Breaking schema drift in TAQ data",
                extra={
                    "dataset": dataset,
                    "partition": partition,
                    "removed": drift.removed_columns,
                    "changed": drift.changed_columns,
                },
            )
            raise SchemaError(drift, f"Breaking schema drift for {schema_name}")

        if drift.has_additions:
            logger.warning(
                "New columns detected in TAQ data",
                extra={"dataset": dataset, "new_columns": drift.added_columns},
            )
            self.schema_registry.apply_drift_policy(schema_name, drift, current_schema)

        # Write atomically
        checksum = self._atomic_write_parquet(df, output_path)

        logger.info(
            "Synced aggregate partition",
            extra={
                "dataset": dataset,
                "partition": partition,
                "rows": df.height,
                "checksum": checksum[:16],
            },
        )

        return output_path, df.height

    def _sync_sample_symbol(
        self,
        sample_date: datetime.date,
        symbol: str,
        date_dir: Path,
        lock_token: LockToken,
    ) -> tuple[Path, int]:
        """Sync tick data for a single symbol on a single date.

        Args:
            sample_date: Date to sync.
            symbol: Symbol to sync.
            date_dir: Directory for this date's samples.
            lock_token: Active lock token.

        Returns:
            Tuple of (file_path, row_count).
        """
        output_path = date_dir / f"{symbol}.parquet"
        schema_name = "taq_ticks"

        # Build and execute query (wrds_client checked non-None by caller)
        assert self.wrds_client is not None
        query, params = self._build_ticks_query(sample_date, symbol)
        df = self.wrds_client.execute_query(query, params)

        if df.is_empty():
            logger.warning(
                "No tick data for symbol",
                extra={"symbol": symbol, "date": str(sample_date)},
            )
            df = self._create_empty_df(schema_name)

        # Validate schema (same as aggregates - detect breaking drift)
        if not df.is_empty():
            current_schema = {col: str(df.schema[col]) for col in df.columns}
            drift = self.schema_registry.detect_drift(schema_name, current_schema)

            if drift.is_breaking:
                logger.error(
                    "Breaking schema drift in TAQ tick data",
                    extra={
                        "symbol": symbol,
                        "date": str(sample_date),
                        "removed": drift.removed_columns,
                        "changed": drift.changed_columns,
                    },
                )
                raise SchemaError(drift, f"Breaking schema drift for {schema_name}")

            if drift.has_additions:
                logger.warning(
                    "New columns detected in TAQ tick data",
                    extra={"symbol": symbol, "new_columns": drift.added_columns},
                )
                self.schema_registry.apply_drift_policy(schema_name, drift, current_schema)

        # Write atomically
        self._atomic_write_parquet(df, output_path)

        logger.debug(
            "Synced sample symbol",
            extra={
                "symbol": symbol,
                "date": str(sample_date),
                "rows": df.height,
            },
        )

        return output_path, df.height

    def _build_aggregates_query(
        self,
        dataset: str,
        symbols: list[str],
        partition: str,
    ) -> tuple[str, dict[str, Any]]:
        """Build WRDS query for aggregate data.

        Args:
            dataset: Aggregate dataset (1min_bars, daily_rv, spread_stats).
            symbols: Symbols to query.
            partition: Month partition (YYYYMM).

        Returns:
            Tuple of (SQL query, params dict).
        """
        # Parse partition to get date range
        year = int(partition[:4])
        month = int(partition[4:6])
        start_date = datetime.date(year, month, 1)

        # End date is last day of month
        if month == 12:
            end_date = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end_date = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

        params = {
            "symbols": symbols,
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
        }

        if dataset == "1min_bars":
            query = """
                SELECT
                    datetime_m AS ts,
                    sym_root AS symbol,
                    open_m AS open,
                    high_m AS high,
                    low_m AS low,
                    close_m AS close,
                    vol_m AS volume,
                    vwap_m AS vwap,
                    DATE(datetime_m) AS date
                FROM taq.msec_1min
                WHERE sym_root = ANY(:symbols)
                AND DATE(datetime_m) >= :start_date
                AND DATE(datetime_m) <= :end_date
                ORDER BY datetime_m, sym_root
            """
        elif dataset == "daily_rv":
            query = """
                SELECT
                    date,
                    sym_root AS symbol,
                    rv_5m,
                    rv_30m,
                    obs_5m AS obs
                FROM taq.rv_daily
                WHERE sym_root = ANY(:symbols)
                AND date >= :start_date
                AND date <= :end_date
                ORDER BY date, sym_root
            """
        elif dataset == "spread_stats":
            query = """
                SELECT
                    date,
                    sym_root AS symbol,
                    qwap_spread,
                    ewas,
                    quote_cnt AS quotes,
                    trade_cnt AS trades
                FROM taq.spread_daily
                WHERE sym_root = ANY(:symbols)
                AND date >= :start_date
                AND date <= :end_date
                ORDER BY date, sym_root
            """
        else:
            raise ValueError(f"Unknown aggregate dataset: {dataset}")

        return query, params

    def _build_ticks_query(
        self,
        sample_date: datetime.date,
        symbol: str,
    ) -> tuple[str, dict[str, Any]]:
        """Build WRDS query for tick data.

        Args:
            sample_date: Date to query.
            symbol: Symbol to query.

        Returns:
            Tuple of (SQL query, params dict).
        """
        params = {
            "symbol": symbol,
            "date": sample_date.strftime("%Y-%m-%d"),
        }

        query = """
            SELECT
                datetime AS ts,
                sym_root AS symbol,
                bid,
                ask,
                bidsiz AS bid_size,
                asksiz AS ask_size,
                price AS trade_px,
                size AS trade_size,
                cond
            FROM taq.ctm_{}
            WHERE sym_root = :symbol
            AND DATE(datetime) = :date
            ORDER BY datetime
        """.format(sample_date.strftime("%Y%m%d"))

        return query, params

    def _build_month_partitions(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> list[str]:
        """Build list of month partitions between dates.

        Args:
            start_date: Start date.
            end_date: End date.

        Returns:
            List of partition identifiers (YYYYMM).
        """
        partitions = []
        current = start_date.replace(day=1)

        while current <= end_date:
            partitions.append(current.strftime("%Y%m"))
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return partitions

    def _filter_new_partitions(
        self,
        partitions: list[str],
        last_synced_date: datetime.date,
    ) -> list[str]:
        """Filter partitions to only include those strictly after last sync.

        Uses > (not >=) to avoid duplicating the last synced partition when
        incremental sync carries forward existing file_paths from the manifest.

        Args:
            partitions: All partitions in range.
            last_synced_date: Last date that was synced.

        Returns:
            Filtered list of partitions (excluding already-synced month).
        """
        last_partition = last_synced_date.strftime("%Y%m")
        return [p for p in partitions if p > last_partition]

    def _estimate_rows(
        self,
        dataset: str,
        symbols: list[str],
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> int:
        """Estimate number of rows for disk space calculation.

        Args:
            dataset: Dataset being synced.
            symbols: Symbols being synced.
            start_date: Start date.
            end_date: End date.

        Returns:
            Estimated row count.
        """
        days = (end_date - start_date).days + 1

        if dataset == "1min_bars":
            # ~390 bars per day per symbol (trading hours)
            return len(symbols) * days * 390
        elif dataset in ("daily_rv", "spread_stats"):
            # 1 row per day per symbol
            return len(symbols) * days
        else:
            # Tick data: ~50k ticks per symbol per day
            return len(symbols) * days * 50_000

    def _check_disk_space(self, estimated_rows: int) -> DiskSpaceStatus:
        """Check disk space with safety multiplier.

        Args:
            estimated_rows: Estimated rows to sync.

        Returns:
            DiskSpaceStatus.

        Raises:
            DiskSpaceError: If insufficient space.
        """
        required_bytes = int(
            estimated_rows * self.BYTES_PER_ROW_ESTIMATE * self.DISK_SAFETY_MULTIPLIER
        )

        stat = shutil.disk_usage(self.storage_path)
        used_pct = (stat.total - stat.free) / stat.total

        # Check if we have enough free space
        if stat.free < required_bytes:
            logger.error(
                "Insufficient disk space for TAQ sync",
                extra={
                    "component": "taq_sync",
                    "event": "sync.disk.blocked",
                    "free_bytes": stat.free,
                    "required_bytes": required_bytes,
                },
            )
            raise DiskSpaceError(
                f"Insufficient disk space: {stat.free / 1e9:.1f} GB free, "
                f"{required_bytes / 1e9:.1f} GB required"
            )

        if used_pct >= self.DISK_BLOCKED_PCT:
            logger.error(
                "Disk space blocked",
                extra={
                    "component": "taq_sync",
                    "event": "sync.disk.blocked",
                    "used_pct": used_pct,
                },
            )
            raise DiskSpaceError(
                f"Disk usage at {used_pct:.1%}, blocked at {self.DISK_BLOCKED_PCT:.0%}"
            )

        if used_pct >= self.DISK_CRITICAL_PCT:
            logger.critical(
                "Disk space critical",
                extra={
                    "component": "taq_sync",
                    "event": "sync.disk.critical",
                    "used_pct": used_pct,
                },
            )
            level: Literal["ok", "warning", "critical"] = "critical"
        elif used_pct >= self.DISK_WARNING_PCT:
            logger.warning(
                "Disk space warning",
                extra={
                    "component": "taq_sync",
                    "event": "sync.disk.warning",
                    "used_pct": used_pct,
                },
            )
            level = "warning"
        else:
            level = "ok"

        return DiskSpaceStatus(
            level=level,
            free_bytes=stat.free,
            total_bytes=stat.total,
            used_pct=used_pct,
            message=f"Disk at {used_pct:.1%}",
        )

    def _atomic_write_parquet(self, df: pl.DataFrame, target_path: Path) -> str:
        """Write Parquet file atomically.

        Pattern:
        1. Write to temp file (with UUID to prevent collisions)
        2. Compute checksum
        3. fsync temp file
        4. Atomic rename to target
        5. fsync parent directory

        Args:
            df: DataFrame to write.
            target_path: Final destination path.

        Returns:
            SHA-256 checksum of written file.
        """
        import uuid

        temp_dir = self.storage_path / self.TMP_DIR
        # Include UUID to prevent collision across datasets/concurrent syncs
        # e.g., "202401-a1b2c3d4.parquet.tmp" instead of "202401.parquet.tmp"
        unique_id = uuid.uuid4().hex[:8]
        temp_path = temp_dir / f"{target_path.stem}-{unique_id}.parquet.tmp"

        try:
            # Write to temp
            df.write_parquet(temp_path)

            # Compute checksum and fsync
            checksum = self._compute_checksum_and_fsync(temp_path)

            # Ensure target directory exists
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic rename
            temp_path.replace(target_path)

            # fsync parent directory
            self._fsync_directory(target_path.parent)

            return checksum

        except OSError as e:
            if e.errno == 28:  # ENOSPC
                logger.error(
                    "Disk full during write",
                    extra={
                        "component": "taq_sync",
                        "event": "sync.disk.blocked",
                        "target": str(target_path),
                    },
                )
                self._quarantine_failed(temp_path, "Disk full")
                raise DiskSpaceError(f"Disk full writing {target_path}") from e
            raise
        finally:
            # Clean up temp file on failure
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def _compute_checksum_and_fsync(self, path: Path) -> str:
        """Compute SHA-256 checksum and fsync in single operation."""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
            os.fsync(f.fileno())
        return hasher.hexdigest()

    def _fsync_directory(self, dir_path: Path) -> None:
        """Sync directory for crash safety."""
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            logger.warning("Failed to fsync directory", extra={"path": str(dir_path)})

    def _quarantine_failed(self, temp_path: Path, reason: str) -> None:
        """Move failed temp file to quarantine."""
        if not temp_path.exists():
            return

        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        quarantine_dir = self.storage_path / self.QUARANTINE_DIR / f"{timestamp}_{temp_path.stem}"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        dest = quarantine_dir / temp_path.name
        shutil.move(str(temp_path), str(dest))

        reason_file = quarantine_dir / "reason.txt"
        with open(reason_file, "w") as f:
            f.write(f"Quarantined at: {timestamp}\n")
            f.write(f"Reason: {reason}\n")
            f.write(f"Original path: {temp_path}\n")

        logger.warning(
            "File quarantined",
            extra={
                "component": "taq_sync",
                "path": str(temp_path),
                "quarantine": str(quarantine_dir),
                "reason": reason,
            },
        )

    def _create_manifest(
        self,
        dataset: str,
        file_paths: list[str],
        row_count: int,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> SyncManifest:
        """Create a SyncManifest for completed sync."""
        # Compute combined checksum
        combined_checksum = self._compute_combined_checksum(file_paths)

        # Get schema version - for sample manifests (taq_samples_YYYYMMDD),
        # look up taq_ticks schema since that's the actual data schema
        schema_lookup = "taq_ticks" if dataset.startswith("taq_samples_") else dataset
        schema = self.schema_registry.get_expected_schema(schema_lookup)
        schema_version = schema.version if schema else "v1.0.0"

        # Compute query hash
        query_hash = hashlib.sha256(f"{dataset}:{start_date}:{end_date}".encode()).hexdigest()

        return SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.datetime.now(datetime.UTC),
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            checksum=combined_checksum,
            schema_version=schema_version,
            wrds_query_hash=query_hash,
            file_paths=file_paths,
            validation_status="passed",
        )

    def _compute_combined_checksum(self, file_paths: list[str]) -> str:
        """Compute combined checksum for multiple files."""
        hasher = hashlib.sha256()
        for path_str in sorted(file_paths):
            path = Path(path_str)
            if path.exists():
                file_checksum = self.validator.compute_checksum(path)
                hasher.update(file_checksum.encode())
        return hasher.hexdigest()

    def _create_empty_df(self, schema_name: str) -> pl.DataFrame:
        """Create empty DataFrame with correct schema."""
        schema = TAQ_SCHEMAS.get(schema_name, {})
        polars_schema: dict[str, Any] = {}

        for col, dtype_str in schema.items():
            dtype_str_lower = dtype_str.lower()
            if dtype_str_lower == "datetime[ns]":
                polars_schema[col] = pl.Datetime("ns")
            elif dtype_str_lower == "utf8":
                polars_schema[col] = pl.Utf8
            elif dtype_str_lower == "float64":
                polars_schema[col] = pl.Float64
            elif dtype_str_lower == "int64":
                polars_schema[col] = pl.Int64
            elif dtype_str_lower == "date":
                polars_schema[col] = pl.Date
            else:
                polars_schema[col] = pl.Utf8

        return pl.DataFrame(schema=polars_schema)

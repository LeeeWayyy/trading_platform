"""Historical ETL Pipeline for daily price data.

This module provides:
- HistoricalETL: Orchestrates historical data fetching via UnifiedDataFetcher
- ETLProgressManifest: Tracks ETL progress for resume capability
- Year-partitioned Parquet storage with atomic writes
- DuckDB catalog for SQL queries

Example:
    config = FetcherConfig(environment="development")
    fetcher = UnifiedDataFetcher(config, yfinance_provider=yf_provider)
    etl = HistoricalETL(fetcher)

    result = etl.run_full_etl(
        symbols=["AAPL", "MSFT"],
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
    )

See Also:
    docs/CONCEPTS/historical-etl-pipeline.md
    docs/ADRs/ADR-017-historical-etl-pipeline.md
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import duckdb
import polars as pl
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from libs.data_providers.unified_fetcher import UnifiedDataFetcher

from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.validation import DataValidator

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class ETLError(Exception):
    """Base exception for ETL errors."""

    pass


class DataQualityError(ETLError):
    """Raised when data validation fails."""

    pass


class ChecksumMismatchError(ETLError):
    """Raised when checksum verification fails after write."""

    pass


class DiskSpaceError(ETLError):
    """Raised when insufficient disk space."""

    pass


class ETLProgressError(ETLError):
    """Raised when ETL progress operations fail."""

    pass


# =============================================================================
# Models
# =============================================================================


class ETLProgressManifest(BaseModel):
    """ETL-specific progress tracking (separate from SyncManifest).

    Stored at: data/sync_progress/historical_daily_progress.json
    SyncManifest stored at: data/manifests/historical_daily.json (standard)

    Attributes:
        dataset: Dataset identifier.
        last_updated: Last progress update timestamp.
        symbol_last_dates: Per-symbol last sync date for incremental updates.
        years_completed: List of years fully processed.
        years_remaining: List of years still to process.
        status: Current ETL status.
    """

    dataset: str
    last_updated: datetime
    symbol_last_dates: dict[str, str]  # symbol -> date ISO string
    years_completed: list[int]
    years_remaining: list[int]
    status: Literal["running", "paused", "completed", "failed"]


@dataclass
class ETLResult:
    """Result of an ETL operation.

    Attributes:
        total_rows: Total rows written across all partitions.
        partitions_written: List of partition file paths.
        symbols_processed: List of symbols processed.
        start_date: Start of date range.
        end_date: End of date range.
        duration_seconds: Total execution time.
        manifest_checksum: Combined checksum of all partition files.
    """

    total_rows: int
    partitions_written: list[str]
    symbols_processed: list[str]
    start_date: date
    end_date: date
    duration_seconds: float
    manifest_checksum: str


# =============================================================================
# HistoricalETL
# =============================================================================


class HistoricalETL:
    """Historical ETL pipeline with atomic writes and manifest tracking.

    Uses ManifestManager for single-writer locking and manifest coupling.
    Supports both full ETL and incremental updates with deduplication.

    Storage Layout:
        data/historical/daily/{year}.parquet  - Year-partitioned data
        data/manifests/historical_daily.json  - SyncManifest
        data/sync_progress/historical_daily_progress.json - ETL progress
        data/duckdb/historical_catalog.duckdb - DuckDB catalog

    Thread Safety:
        Single-writer via ManifestManager.acquire_lock()
        Multiple readers via DuckDB read-only connections
    """

    DATASET_ID = "historical_daily"
    PRIMARY_KEYS = ["date", "symbol"]
    DEFAULT_START_DATE = date(2000, 1, 1)
    PROGRESS_DIR = Path("data/sync_progress")

    def __init__(
        self,
        fetcher: UnifiedDataFetcher,
        storage_path: Path = Path("data/historical"),
        catalog_path: Path = Path("data/duckdb/historical_catalog.duckdb"),
        manifest_manager: ManifestManager | None = None,
        validator: DataValidator | None = None,
    ) -> None:
        """Initialize HistoricalETL.

        Args:
            fetcher: UnifiedDataFetcher instance for data access.
            storage_path: Directory for Parquet partition files.
            catalog_path: Path to DuckDB catalog file.
            manifest_manager: Optional ManifestManager (uses default if None).
            validator: Optional DataValidator (uses default if None).
        """
        self.fetcher = fetcher
        self.storage_path = storage_path
        self.catalog_path = catalog_path
        self.manifest_manager = manifest_manager or ManifestManager()
        self.validator = validator or DataValidator()

        # Ensure directories exist
        self.storage_path.mkdir(parents=True, exist_ok=True)
        (self.storage_path / "daily").mkdir(parents=True, exist_ok=True)
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Full ETL
    # =========================================================================

    def run_full_etl(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        resume: bool = True,
    ) -> ETLResult:
        """Run full ETL with lock + manifest coupling.

        Args:
            symbols: List of ticker symbols to fetch.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            resume: Whether to resume from previous progress.

        Returns:
            ETLResult with operation details.

        Raises:
            ETLError: If ETL operation fails.
            LockNotHeldError: If lock cannot be acquired.
        """
        start_time = time.monotonic()
        # CRITICAL: Clamp end_date to today to prevent future cursor advancement
        today = datetime.now(UTC).date()
        end_date = min(end_date, today)

        with self.manifest_manager.acquire_lock(
            dataset=self.DATASET_ID,
            writer_id=f"etl_{os.getpid()}",
        ) as lock_token:
            # Check for resume
            etl_progress = self._load_etl_progress() if resume else None
            if etl_progress and etl_progress.status in ("running", "paused"):
                years = etl_progress.years_remaining
                logger.info(
                    "Resuming ETL from previous progress",
                    extra={
                        "event": "etl.resume",
                        "years_remaining": years,
                        "years_completed": etl_progress.years_completed,
                    },
                )
            else:
                years = list(range(start_date.year, end_date.year + 1))
                etl_progress = ETLProgressManifest(
                    dataset=self.DATASET_ID,
                    last_updated=datetime.now(UTC),
                    symbol_last_dates={},
                    years_completed=[],
                    years_remaining=years.copy(),
                    status="running",
                )

            # Disk space check
            estimated_bytes = self._estimate_total_size(symbols, start_date, end_date)
            self.manifest_manager.check_disk_space(int(estimated_bytes * 2))

            written_paths: list[Path] = []
            total_rows = 0

            for year in years.copy():
                # Fetch data for year
                year_start = max(date(year, 1, 1), start_date)
                year_end = min(date(year, 12, 31), end_date)

                logger.info(
                    "Fetching data for year",
                    extra={
                        "event": "etl.fetch.year",
                        "year": year,
                        "start": str(year_start),
                        "end": str(year_end),
                        "symbols_count": len(symbols),
                    },
                )

                df = self.fetcher.get_daily_prices(
                    symbols=symbols,
                    start_date=year_start,
                    end_date=year_end,
                )

                # CRITICAL: Filter out future dates (provider may return T+1 or beyond)
                df = df.filter(pl.col("date") <= today)

                if df.is_empty():
                    logger.info("No data for year %d, skipping", year)
                    etl_progress.years_remaining.remove(year)
                    etl_progress.years_completed.append(year)
                    continue

                # Sort for deterministic output
                df = df.sort(self.PRIMARY_KEYS)

                # Validate + atomic write
                partition_path = self.storage_path / "daily" / f"{year}.parquet"
                self._atomic_write_with_quarantine(df, partition_path)
                written_paths.append(partition_path)
                total_rows += df.height

                # Update progress checkpoint
                etl_progress.years_completed.append(year)
                etl_progress.years_remaining.remove(year)
                etl_progress.last_updated = datetime.now(UTC)
                self._save_etl_progress(etl_progress)

                logger.info(
                    "Completed partition",
                    extra={
                        "event": "etl.partition.complete",
                        "year": year,
                        "rows": df.height,
                    },
                )

            # Create and save manifest with ALL existing partitions (not just this run)
            # CRITICAL: On resume, we must include previously written partitions
            # to avoid dropping them from the manifest
            all_partition_paths = sorted((self.storage_path / "daily").glob("*.parquet"))
            manifest = self._create_sync_manifest(
                file_paths=all_partition_paths,
                start_date=start_date,
                end_date=end_date,
            )
            self.manifest_manager.save_manifest(manifest, lock_token)

            # Update DuckDB catalog
            self._update_catalog()

            # Compute and store symbol_last_dates for incremental to use
            # CRITICAL: Without this, incremental ETL would refetch from DEFAULT_START_DATE
            if all_partition_paths:
                all_data = pl.scan_parquet(all_partition_paths)
                symbol_max_dates = (
                    all_data.group_by("symbol")
                    .agg(pl.col("date").max().alias("max_date"))
                    .collect()
                )
                # Convert dates to ISO format strings for Pydantic serialization
                # CRITICAL: Clamp to today to prevent future cursor advancement
                etl_progress.symbol_last_dates = {
                    row["symbol"]: min(row["max_date"], today).isoformat()
                    for row in symbol_max_dates.iter_rows(named=True)
                }

            # Mark complete
            etl_progress.status = "completed"
            self._save_etl_progress(etl_progress)

            duration = time.monotonic() - start_time

            logger.info(
                "ETL completed successfully",
                extra={
                    "event": "etl.complete",
                    "total_rows": total_rows,
                    "partitions": len(written_paths),
                    "duration_seconds": duration,
                },
            )

            return ETLResult(
                total_rows=total_rows,
                partitions_written=[str(p) for p in written_paths],
                symbols_processed=symbols,
                start_date=start_date,
                end_date=end_date,
                duration_seconds=duration,
                manifest_checksum=manifest.checksum,
            )

    # =========================================================================
    # Incremental ETL
    # =========================================================================

    def run_incremental_etl(self, symbols: list[str]) -> ETLResult:
        """Incremental ETL with batched deduplication.

        Performance optimization:
        1. Group symbols by last_updated date for batched fetching
        2. Collect ALL new data in memory
        3. Group by year
        4. Single read-merge-write per affected year partition

        Processing Guarantee: AT-LEAST-ONCE
        Progress (symbol_last_dates) is saved only after all partitions are written.
        If the process crashes mid-loop, work will be redone on restart.
        This is safe because the deterministic merge (_merge_partition_deterministic)
        ensures identical output regardless of how many times data is processed.

        Args:
            symbols: List of ticker symbols to update.

        Returns:
            ETLResult with operation details.
        """
        start_time = time.monotonic()
        # CRITICAL: Use UTC to avoid day boundary issues on non-UTC hosts
        today = datetime.now(UTC).date()

        with self.manifest_manager.acquire_lock(
            dataset=self.DATASET_ID,
            writer_id=f"etl_incr_{os.getpid()}",
        ) as lock_token:
            etl_progress = self._load_etl_progress()
            symbol_last_dates = self._parse_symbol_dates(etl_progress)

            # Step 1: Group symbols by last_updated for batched fetching
            date_to_symbols: dict[date, list[str]] = {}
            for symbol in symbols:
                last = symbol_last_dates.get(symbol, self.DEFAULT_START_DATE)
                if last >= today:
                    continue
                fetch_start = last + timedelta(days=1)
                date_to_symbols.setdefault(fetch_start, []).append(symbol)

            if not date_to_symbols:
                logger.info("All symbols up to date, nothing to fetch")
                return ETLResult(
                    total_rows=0,
                    partitions_written=[],
                    symbols_processed=[],
                    start_date=today,
                    end_date=today,
                    duration_seconds=time.monotonic() - start_time,
                    manifest_checksum="",
                )

            # Step 2: Fetch in batches, collect ALL new data
            all_new_data: list[pl.DataFrame] = []
            for fetch_start, batch_symbols in date_to_symbols.items():
                logger.info(
                    "Fetching batch",
                    extra={
                        "event": "etl.incremental.batch",
                        "start_date": str(fetch_start),
                        "symbols_count": len(batch_symbols),
                    },
                )
                batch_df = self.fetcher.get_daily_prices(
                    symbols=batch_symbols,
                    start_date=fetch_start,
                    end_date=today,
                )
                if not batch_df.is_empty():
                    all_new_data.append(batch_df)

            if not all_new_data:
                logger.info("No new data fetched")
                return ETLResult(
                    total_rows=0,
                    partitions_written=[],
                    symbols_processed=[],
                    start_date=today,
                    end_date=today,
                    duration_seconds=time.monotonic() - start_time,
                    manifest_checksum="",
                )

            # Step 3: Concat all new data
            combined_df = pl.concat(all_new_data)

            # CRITICAL: Filter out future dates (provider may return T+1 or beyond)
            # This prevents cursor from advancing past today
            combined_df = combined_df.filter(pl.col("date") <= today)

            if combined_df.is_empty():
                logger.info("All fetched data was filtered (future dates)")
                return ETLResult(
                    total_rows=0,
                    partitions_written=[],
                    symbols_processed=[],
                    start_date=today,
                    end_date=today,
                    duration_seconds=time.monotonic() - start_time,
                    manifest_checksum="",
                )

            # Step 4: Group by year, single merge per partition
            affected_years = combined_df["date"].dt.year().unique().to_list()

            # Disk space check for merge
            self._check_merge_disk_space(affected_years, combined_df)

            written_paths: list[Path] = []
            total_rows = 0

            for year in affected_years:
                year_df = combined_df.filter(pl.col("date").dt.year() == year)
                partition_path = self._merge_partition_deterministic(year, year_df)
                written_paths.append(partition_path)
                total_rows += year_df.height

            # Update symbol_last_dates based on ACTUAL max date from fetched data
            # CRITICAL: Don't use 'today' blindly - data may lag (early morning, provider delay)
            processed_symbols = set()
            for batch in date_to_symbols.values():
                processed_symbols.update(batch)

            # Compute actual max date per symbol from combined data
            # CRITICAL: Clamp to today in case future dates slipped through
            symbol_max_dates = (
                combined_df
                .group_by("symbol")
                .agg(pl.col("date").max().alias("max_date"))
            )
            actual_max_dates = {
                row["symbol"]: min(row["max_date"], today)
                for row in symbol_max_dates.iter_rows(named=True)
            }

            for symbol in processed_symbols:
                # Use actual max date if available
                if symbol in actual_max_dates:
                    symbol_last_dates[symbol] = actual_max_dates[symbol]
                else:
                    # CONSERVATIVE DESIGN: Do NOT advance cursor if no data returned.
                    # Rationale: We cannot distinguish between:
                    #   a) Legitimate "no data" (delisted, holiday)
                    #   b) Transient provider failure returning empty instead of exception
                    # Advancing on (b) creates permanent data gaps.
                    #
                    # Trade-off: Delisted symbols will be re-queried each run until
                    # their cursor catches up naturally via date advancement.
                    # This is acceptable because:
                    #   1. Most symbols DO return data (low overhead)
                    #   2. Data gaps are worse than extra queries
                    #   3. Monitoring can identify persistently empty symbols
                    logger.warning(
                        "Symbol returned no data - cursor NOT advanced (prevents data gaps)",
                        extra={
                            "event": "etl.incremental.no_data",
                            "symbol": symbol,
                            "requested_start": str(symbol_last_dates.get(symbol, self.DEFAULT_START_DATE) + timedelta(days=1)),
                            "action": "cursor_unchanged",
                        },
                    )

            # Update manifest
            all_partition_paths = list((self.storage_path / "daily").glob("*.parquet"))
            manifest = self._create_sync_manifest(
                file_paths=all_partition_paths,
                start_date=self.DEFAULT_START_DATE,
                end_date=today,
            )
            self.manifest_manager.save_manifest(manifest, lock_token)

            # Update catalog
            self._update_catalog()

            # Update progress
            if etl_progress is None:
                etl_progress = ETLProgressManifest(
                    dataset=self.DATASET_ID,
                    last_updated=datetime.now(UTC),
                    symbol_last_dates={},
                    years_completed=[],
                    years_remaining=[],
                    status="completed",
                )
            etl_progress.symbol_last_dates = {
                s: d.isoformat() for s, d in symbol_last_dates.items()
            }
            etl_progress.last_updated = datetime.now(UTC)
            # CRITICAL: Always set status to completed to prevent stale "running" state
            etl_progress.status = "completed"
            self._save_etl_progress(etl_progress)

            duration = time.monotonic() - start_time

            logger.info(
                "Incremental ETL completed",
                extra={
                    "event": "etl.incremental.complete",
                    "total_rows": total_rows,
                    "partitions_updated": len(written_paths),
                    "symbols_updated": len(processed_symbols),
                    "duration_seconds": duration,
                },
            )

            return ETLResult(
                total_rows=total_rows,
                partitions_written=[str(p) for p in written_paths],
                symbols_processed=list(processed_symbols),
                start_date=today,
                end_date=today,
                duration_seconds=duration,
                manifest_checksum=manifest.checksum,
            )

    def _parse_symbol_dates(
        self, progress: ETLProgressManifest | None
    ) -> dict[str, date]:
        """Parse symbol_last_dates from progress manifest.

        CRITICAL: Clamps all parsed dates to min(parsed_date, today) to prevent
        future dates in a corrupted/edited progress file from blocking updates.
        """
        if progress is None:
            return {}
        today = datetime.now(UTC).date()
        result = {}
        for symbol, date_str in progress.symbol_last_dates.items():
            try:
                parsed = date.fromisoformat(date_str)
                # Clamp to today to prevent future dates from blocking updates
                result[symbol] = min(parsed, today)
            except ValueError:
                logger.warning("Invalid date for symbol %s: %s", symbol, date_str)
        return result

    def _merge_partition_deterministic(
        self,
        year: int,
        new_df: pl.DataFrame,
    ) -> Path:
        """Deterministic merge: read → concat → sort → dedup → atomic rewrite.

        Guarantees:
        - Reruns produce IDENTICAL checksums (deterministic sort order)
        - No duplicates across partition (PRIMARY_KEYS uniqueness)
        - Atomic rewrite with quarantine on failure

        Args:
            year: Year of the partition.
            new_df: New data to merge.

        Returns:
            Path to written partition file.
        """
        partition_path = self.storage_path / "daily" / f"{year}.parquet"

        # Step 1: Read existing partition (if exists)
        # CRITICAL: Halt on corruption - silent data loss is unacceptable for trading
        # MEMORY NOTE: This reads the full partition into memory. For daily price data
        # with yearly partitions (~252 trading days * N symbols), this is typically
        # manageable (tens of MB). If partitions grow to GBs, consider:
        # - Using pl.scan_parquet() with streaming
        # - Splitting into monthly partitions
        # - Using sink_parquet() for streaming writes
        if partition_path.exists():
            try:
                existing_df = pl.read_parquet(partition_path)
                combined = pl.concat([existing_df, new_df])
            except Exception as e:
                # Corrupt partition - quarantine it but HALT the pipeline
                # Proceeding with only new_df would silently drop years of history
                # Manual intervention required: restore from backup or re-fetch
                logger.error(
                    "Corrupt partition detected - halting pipeline",
                    extra={
                        "event": "etl.partition.corrupt",
                        "partition": str(partition_path),
                        "error": str(e),
                    },
                )
                self._quarantine_temp_file(partition_path, f"Corrupt partition read: {e}")
                raise DataQualityError(
                    f"Corrupt partition {partition_path}: {e}. "
                    f"Partition quarantined. Manual intervention required: "
                    f"restore from backup or re-fetch entire year."
                ) from e
        else:
            combined = new_df

        # Step 2: Deduplicate BEFORE sorting to preserve concat order (new data last)
        # CRITICAL: Using keep="last" on unsorted data ensures new data wins
        # because new_df is concatenated AFTER existing_df
        combined = combined.unique(subset=self.PRIMARY_KEYS, keep="last")

        # Step 3: Sort for deterministic output (maintain_order not needed after dedup)
        combined = combined.sort(self.PRIMARY_KEYS)

        # Step 5: Validate before write
        validation_errors = self._validate_partition(combined, year)
        if validation_errors:
            self._handle_validation_failure(partition_path, combined, validation_errors)
            raise DataQualityError(f"Validation failed: {validation_errors}")

        # Step 6: Atomic rewrite with quarantine on failure
        self._atomic_write_with_quarantine(combined, partition_path)

        return partition_path

    # =========================================================================
    # Progress Manifest
    # =========================================================================

    def _load_etl_progress(self) -> ETLProgressManifest | None:
        """Load ETL progress with corruption recovery.

        Recovery behavior:
        - Missing file: Return None (fresh start)
        - Corrupted file: Log warning, backup corrupted, return None
        """
        path = self.PROGRESS_DIR / f"{self.DATASET_ID}_progress.json"
        if not path.exists():
            return None

        try:
            content = path.read_text()
            return ETLProgressManifest.model_validate_json(content)
        except (json.JSONDecodeError, ValidationError) as e:
            # Corrupted progress file - backup, delete original, and start fresh
            backup_path = path.with_suffix(f".json.corrupted.{int(time.time())}")
            shutil.copy2(path, backup_path)
            # Delete the corrupted original to prevent repeated warnings on each run
            path.unlink()
            logger.warning(
                "Corrupted ETL progress file backed up and removed, starting fresh",
                extra={
                    "event": "etl.progress.corrupted",
                    "original": str(path),
                    "backup": str(backup_path),
                    "error": str(e),
                },
            )
            return None

    def _save_etl_progress(self, progress: ETLProgressManifest) -> None:
        """Atomic save of ETL progress with fsync and backup.

        Durability guarantees:
        1. Backup previous progress before overwrite
        2. Write to temp file
        3. fsync temp file
        4. Atomic rename
        5. fsync directory

        On failure: previous progress file remains intact.
        """
        path = self.PROGRESS_DIR / f"{self.DATASET_ID}_progress.json"
        temp_path = path.with_suffix(".json.tmp")
        backup_path = path.with_suffix(".json.backup")

        # Step 1: Backup previous progress (if exists)
        if path.exists():
            shutil.copy2(path, backup_path)

        try:
            # Step 2: Write to temp
            content = progress.model_dump_json(indent=2)
            temp_path.write_text(content)

            # Step 3: fsync temp file
            with open(temp_path, "rb") as f:
                os.fsync(f.fileno())

            # Step 4: Atomic rename
            temp_path.rename(path)

            # Step 5: fsync directory
            dir_fd = os.open(self.PROGRESS_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        except OSError as e:
            # Clean up temp on failure
            if temp_path.exists():
                temp_path.unlink()
            raise ETLProgressError(f"Failed to save ETL progress: {e}") from e

    # =========================================================================
    # Validation
    # =========================================================================

    def _validate_partition(self, df: pl.DataFrame, year: int) -> list[str]:
        """Validate partition data quality before writing.

        Checks:
        1. Primary key uniqueness (no duplicates)
        2. No nulls in primary key columns
        3. Schema matches expected (required columns exist)

        Args:
            df: DataFrame to validate.
            year: Year of the partition (for error messages).

        Returns:
            List of validation errors (empty = passed).
        """
        errors: list[str] = []

        # 1. Primary key uniqueness
        pk_cols = self.PRIMARY_KEYS
        dup_count = df.height - df.unique(subset=pk_cols).height
        if dup_count > 0:
            errors.append(f"Duplicate primary keys: {dup_count} rows")

        # 2. No nulls in primary keys
        for col in pk_cols:
            if col in df.columns:
                null_count = df.filter(pl.col(col).is_null()).height
                if null_count > 0:
                    errors.append(f"Null values in {col}: {null_count} rows")

        # 3. Schema validation (required columns exist)
        required_cols = ["date", "symbol", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            errors.append(f"Missing required columns: {missing}")

        return errors

    def _handle_validation_failure(
        self, partition_path: Path, df: pl.DataFrame, errors: list[str]
    ) -> None:
        """Handle validation failure with quarantine."""
        # Write failed data to quarantine (use configured storage path to stay on same volume)
        quarantine_dir = self.storage_path.parent / "quarantine" / self.DATASET_ID
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        quarantine_path = quarantine_dir / f"{partition_path.stem}_{timestamp}.parquet"

        df.write_parquet(quarantine_path)

        # Write reason file
        reason_path = quarantine_path.with_suffix(".reason.txt")
        reason_path.write_text("Validation errors:\n" + "\n".join(errors))

        logger.error(
            "Partition quarantined due to validation failure",
            extra={
                "event": "etl.quarantine",
                "partition": str(partition_path),
                "quarantine_path": str(quarantine_path),
                "errors": errors,
            },
        )

    # =========================================================================
    # Atomic Write
    # =========================================================================

    def _atomic_write_with_quarantine(
        self, df: pl.DataFrame, target_path: Path
    ) -> str:
        """Atomic write with complete validation + quarantine coupling.

        CRITICAL: Never quarantine an existing good partition on validation failure.
        Only quarantine temp files that were created during this operation.

        Failure Path:
        1. Validation fails → raise (no file created yet, nothing to quarantine)
        2. ENOSPC during write → quarantine temp → raise appropriate error
        3. Checksum mismatch after write → quarantine temp (not target!) → raise

        Args:
            df: DataFrame to write.
            target_path: Target Parquet file path.

        Returns:
            Checksum of written file.
        """
        temp_path = target_path.with_suffix(".parquet.tmp")
        backup_path = target_path.with_suffix(".parquet.bak")
        year = int(target_path.stem)

        try:
            # 1. Validate BEFORE writing - no file created yet, nothing to quarantine
            validation_errors = self._validate_partition(df, year)
            if validation_errors:
                logger.error(
                    "Partition validation failed",
                    extra={
                        "event": "etl.validation.failed",
                        "year": year,
                        "errors": validation_errors,
                    },
                )
                # CRITICAL: Do NOT quarantine existing target_path here!
                # The existing partition is valid; only the new data failed validation.
                raise DataQualityError(f"Validation failed: {validation_errors}")

            # 2. Disk space check on DATA volume
            estimated_size = self._estimate_parquet_size(df)
            self._check_disk_space_on_path(target_path.parent, estimated_size * 2)

            # 3. Write to temp
            df.write_parquet(temp_path)

            # 4. Compute checksum
            checksum = self.validator.compute_checksum(temp_path)

            # 5. fsync temp file
            with open(temp_path, "rb") as f:
                os.fsync(f.fileno())

            # 6. Backup existing file before atomic rename (if exists)
            # CRITICAL: Preserve last good copy to prevent data loss on checksum failure
            existing_file_backed_up = False
            if target_path.exists():
                shutil.copy2(str(target_path), str(backup_path))
                existing_file_backed_up = True

            # 7. Atomic rename
            temp_path.rename(target_path)

            # 8. fsync directory
            dir_fd = os.open(target_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

            # 9. Verify checksum after rename
            verify_checksum = self.validator.compute_checksum(target_path)
            if verify_checksum != checksum:
                # CRITICAL: Handle corrupt file - restore backup or quarantine new file
                if existing_file_backed_up and backup_path.exists():
                    # Move corrupt file to quarantine first
                    self._quarantine_temp_file(target_path, "Checksum mismatch - restoring backup")
                    # Restore the backup
                    shutil.move(str(backup_path), str(target_path))
                    logger.warning(
                        "Restored backup after checksum mismatch",
                        extra={
                            "event": "etl.backup.restored",
                            "target": str(target_path),
                        },
                    )
                else:
                    # New file (no backup) - quarantine the corrupt file
                    self._quarantine_temp_file(target_path, "Checksum mismatch - new file")
                raise ChecksumMismatchError(
                    f"Checksum mismatch after write: {checksum} vs {verify_checksum}"
                )

            # 10. Success - remove backup if we created one
            if existing_file_backed_up and backup_path.exists():
                backup_path.unlink()

            return checksum

        except OSError as e:
            # Quarantine temp file if it exists
            if temp_path.exists():
                self._quarantine_temp_file(temp_path, f"Write failed: {e}")
            # Clean up backup if created during this operation
            if backup_path.exists():
                backup_path.unlink()

            # Categorize error properly for operator diagnosis
            if e.errno == errno.ENOSPC:
                raise DiskSpaceError(f"Disk full during write: {e}") from e
            elif e.errno == errno.EACCES or e.errno == errno.EPERM:
                raise ETLError(f"Permission denied during write: {e}") from e
            else:
                raise ETLError(f"I/O error during write: {e}") from e

        except ChecksumMismatchError:
            # Backup restoration is handled in the try block above
            # Just re-raise - no additional cleanup needed
            raise

    def _quarantine_temp_file(self, source_path: Path, reason: str) -> None:
        """Move a temp file to quarantine directory.

        Uses configured storage_path parent to ensure quarantine is on the same
        volume, avoiding cross-device move failures.
        """
        quarantine_dir = self.storage_path.parent / "quarantine" / self.DATASET_ID
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        dest_path = quarantine_dir / f"{source_path.name}_{timestamp}"

        try:
            shutil.move(str(source_path), str(dest_path))
            reason_path = dest_path.with_suffix(dest_path.suffix + ".reason.txt")
            reason_path.write_text(f"Quarantined at: {timestamp}\nReason: {reason}\n")
            logger.warning(
                "File quarantined",
                extra={
                    "event": "etl.quarantine.temp",
                    "source": str(source_path),
                    "dest": str(dest_path),
                    "reason": reason,
                },
            )
        except OSError as e:
            logger.error("Failed to quarantine file %s: %s", source_path, e)

    # =========================================================================
    # Disk Space
    # =========================================================================

    def _estimate_parquet_size(self, df: pl.DataFrame) -> int:
        """Estimate Parquet file size from DataFrame.

        Uses Polars estimated_size() with conservative factors.

        Returns:
            Estimated bytes (conservative: 0.75x estimated_size).
        """
        memory_size = df.estimated_size()
        # Parquet is typically 2-5x smaller than in-memory
        # Use conservative 0.5x factor (memory → parquet)
        parquet_estimate = int(memory_size * 0.5)
        # Add 50% safety margin
        return int(parquet_estimate * 1.5)

    def _estimate_total_size(
        self, symbols: list[str], start_date: date, end_date: date
    ) -> int:
        """Estimate total size for full ETL.

        Rough estimate: ~200 bytes per row per symbol per day.
        """
        days = (end_date - start_date).days + 1
        # Assume ~252 trading days per year
        trading_days = int(days * 252 / 365)
        rows = len(symbols) * trading_days
        return rows * 200  # ~200 bytes per row

    def _check_disk_space_on_path(self, path: Path, required_bytes: int) -> None:
        """Check disk space on the SPECIFIC volume containing path."""
        stat = os.statvfs(path)
        available = stat.f_bavail * stat.f_frsize
        if available < required_bytes:
            raise DiskSpaceError(
                f"Insufficient disk space on {path}: "
                f"need {required_bytes:,}, have {available:,}"
            )

    def _check_merge_disk_space(
        self, affected_years: list[int], new_df: pl.DataFrame
    ) -> None:
        """Check disk space for merge operations.

        Formula:
        required = existing_partitions + new_data + temp_files + manifest_overhead
        """
        existing_size = 0
        for year in affected_years:
            partition_path = self.storage_path / "daily" / f"{year}.parquet"
            if partition_path.exists():
                existing_size += partition_path.stat().st_size

        new_data_size = self._estimate_parquet_size(new_df)
        merged_estimate = existing_size + new_data_size
        temp_buffer = merged_estimate  # 1x for temp during atomic write
        manifest_overhead = 10 * 1024  # 10KB

        required = merged_estimate + temp_buffer + manifest_overhead
        required_with_margin = int(required * 1.2)

        self._check_disk_space_on_path(self.storage_path, required_with_margin)

        logger.debug(
            "Disk space check passed for merge",
            extra={
                "event": "etl.disk_check",
                "existing_size": existing_size,
                "new_data_size": new_data_size,
                "required_with_margin": required_with_margin,
            },
        )

    # =========================================================================
    # Manifest
    # =========================================================================

    def _create_sync_manifest(
        self,
        file_paths: list[Path],
        start_date: date,
        end_date: date,
    ) -> SyncManifest:
        """Create SyncManifest with all required fields.

        Computes:
        - row_count: Sum of rows across all partitions
        - checksum: Combined SHA-256 of all partition files
        - file_paths: List of partition paths
        - schema_version: Default v1.0.0
        """
        # Compute combined row count
        total_rows = 0
        for path in file_paths:
            df = pl.scan_parquet(path).select(pl.len()).collect()
            total_rows += df.item()

        # Compute combined checksum (sorted order for determinism)
        hasher = hashlib.sha256()
        for path in sorted(file_paths):
            file_checksum = self.validator.compute_checksum(path)
            hasher.update(file_checksum.encode())
        combined_checksum = hasher.hexdigest()

        # Compute query hash (for reproducibility)
        query_hash = hashlib.sha256(
            f"{self.DATASET_ID}:{start_date}:{end_date}".encode()
        ).hexdigest()

        return SyncManifest(
            dataset=self.DATASET_ID,
            sync_timestamp=datetime.now(UTC),
            start_date=start_date,
            end_date=end_date,
            row_count=total_rows,
            checksum=combined_checksum,
            checksum_algorithm="sha256",
            schema_version="v1.0.0",
            wrds_query_hash=query_hash,
            file_paths=[str(p) for p in file_paths],
            validation_status="passed",
        )

    # =========================================================================
    # DuckDB Catalog
    # =========================================================================

    def _get_writer_connection(self) -> duckdb.DuckDBPyConnection:
        """Writer-only connection (called under exclusive lock)."""
        conn = duckdb.connect(str(self.catalog_path))
        conn.execute("PRAGMA threads=4")
        conn.execute("PRAGMA memory_limit='4GB'")
        conn.execute("PRAGMA disable_object_cache")
        return conn

    def _get_reader_connection(self) -> duckdb.DuckDBPyConnection:
        """Read-only connection for queries."""
        conn = duckdb.connect(str(self.catalog_path), read_only=True)
        conn.execute("PRAGMA disable_object_cache")
        return conn

    def _update_catalog(self) -> None:
        """Update catalog views after successful write (under lock).

        Uses CREATE OR REPLACE to avoid reader failures.
        """
        conn = self._get_writer_connection()
        try:
            daily_path = str(self.storage_path / "daily" / "*.parquet")
            conn.execute(f"""
                CREATE OR REPLACE VIEW daily_prices AS
                SELECT * FROM read_parquet('{daily_path}')
            """)
            logger.debug("Updated DuckDB catalog view: daily_prices")
        finally:
            conn.close()

    def query_sql(self, sql: str) -> pl.DataFrame:
        """Query via DuckDB with read-only connection.

        Args:
            sql: SQL query to execute.

        Returns:
            Query results as Polars DataFrame.
        """
        conn = self._get_reader_connection()
        try:
            return conn.execute(sql).pl()
        finally:
            conn.close()

    # =========================================================================
    # Utilities
    # =========================================================================

    def get_partition_checksum(self, year: int) -> str | None:
        """Get checksum for a specific partition.

        Args:
            year: Year of the partition.

        Returns:
            SHA-256 checksum or None if partition doesn't exist.
        """
        partition_path = self.storage_path / "daily" / f"{year}.parquet"
        if not partition_path.exists():
            return None
        return self.validator.compute_checksum(partition_path)

    def list_partitions(self) -> list[int]:
        """List all available partition years.

        Returns:
            Sorted list of years with data.
        """
        years = []
        for path in (self.storage_path / "daily").glob("*.parquet"):
            try:
                years.append(int(path.stem))
            except ValueError:
                pass
        return sorted(years)

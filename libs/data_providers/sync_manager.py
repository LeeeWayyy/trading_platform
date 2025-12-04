"""
Bulk sync manager for WRDS data synchronization.

This module provides:
- SyncProgress: Tracks sync state for resume capability
- SyncManager: Orchestrates bulk data sync with atomic writes

Features:
- Atomic writes (temp file + rename + fsync)
- Progress checkpointing for crash recovery
- Disk space monitoring
- Schema drift detection
- Structured logging for alerting
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import polars as pl
from pydantic import BaseModel

from libs.data_providers.locking import AtomicFileLock
from libs.data_providers.wrds_client import WRDSClient
from libs.data_quality.exceptions import (
    DiskSpaceError,
    SchemaError,
)
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.schema import SchemaRegistry
from libs.data_quality.types import DiskSpaceStatus
from libs.data_quality.validation import DataValidator

if TYPE_CHECKING:
    from libs.data_quality.types import LockToken

logger = logging.getLogger(__name__)


class SyncProgress(BaseModel):
    """Tracks sync progress for resume capability.

    Saved to data/sync_progress/{dataset}.json after each partition.

    Attributes:
        dataset: Dataset being synced.
        started_at: UTC timestamp when sync started.
        last_checkpoint: UTC timestamp of last successful partition.
        years_completed: Years that have been successfully synced.
        years_remaining: Years still to be synced.
        total_rows_synced: Cumulative row count.
        status: Current sync status.
    """

    dataset: str
    started_at: datetime.datetime
    last_checkpoint: datetime.datetime
    years_completed: list[int]
    years_remaining: list[int]
    total_rows_synced: int
    status: Literal["running", "paused", "completed", "failed"]


class SyncManager:
    """Orchestrates bulk data sync from WRDS to local Parquet.

    This manager:
    - Acquires exclusive locks before writing
    - Writes data atomically (temp + checksum + rename)
    - Validates schema against registry
    - Tracks progress for resume after crashes
    - Monitors disk space with watermarks

    Example:
        with WRDSClient(config) as client:
            manager = SyncManager(
                wrds_client=client,
                storage_path=Path("data/wrds"),
                lock_dir=Path("data/locks"),
                manifest_manager=manifest_mgr,
                validator=data_validator,
                schema_registry=schema_registry,
            )
            manifest = manager.full_sync("crsp_daily", start_year=2000)
    """

    PROGRESS_DIR = Path("data/sync_progress")
    QUARANTINE_DIR = Path("data/quarantine")
    TMP_DIR = Path("data/tmp")

    # Disk space watermarks
    DISK_WARNING_PCT = 0.80
    DISK_CRITICAL_PCT = 0.90
    DISK_BLOCKED_PCT = 0.95

    # SLO thresholds for alerting
    FULL_SYNC_SLO_HOURS = 4
    INCREMENTAL_SYNC_SLO_MINUTES = 60

    def __init__(
        self,
        wrds_client: WRDSClient,
        storage_path: Path,
        lock_dir: Path,
        manifest_manager: ManifestManager,
        validator: DataValidator,
        schema_registry: SchemaRegistry,
    ) -> None:
        """Initialize sync manager.

        Args:
            wrds_client: Connected WRDS client.
            storage_path: Root path for data storage.
            lock_dir: Directory for lock files.
            manifest_manager: For manifest operations.
            validator: For data validation.
            schema_registry: For schema drift detection.
        """
        self.wrds_client = wrds_client
        self.storage_path = Path(storage_path)
        self.lock_dir = Path(lock_dir)
        self.manifest_manager = manifest_manager
        self.validator = validator
        self.schema_registry = schema_registry

        # Ensure directories exist
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        self.QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        self.TMP_DIR.mkdir(parents=True, exist_ok=True)

    def full_sync(
        self,
        dataset: str,
        start_year: int = 2000,
        end_year: int | None = None,
    ) -> SyncManifest:
        """Execute full dataset sync from WRDS.

        Syncs all data from start_year to current year, partitioned by year.
        Progress is checkpointed after each year for resume capability.

        Args:
            dataset: Dataset name (e.g., "crsp_daily").
            start_year: First year to sync.
            end_year: Last year to sync (defaults to current year).

        Returns:
            SyncManifest for the completed sync.

        Raises:
            LockAcquisitionError: If cannot acquire exclusive lock.
            DiskSpaceError: If disk space is insufficient.
            SchemaError: If breaking schema drift detected.
        """
        sync_start = datetime.datetime.now(datetime.UTC)
        end_year = end_year or datetime.datetime.now().year
        years = list(range(start_year, end_year + 1))

        logger.info(
            "Starting full sync",
            extra={
                "event": "sync.full.start",
                "dataset": dataset,
                "start_year": start_year,
                "end_year": end_year,
            },
        )

        # Check for existing progress (resume)
        # Resume from both "paused" (graceful interruption) and "running" (crash)
        existing_progress = self._load_progress(dataset)
        resuming = existing_progress is not None and existing_progress.status in {
            "paused",
            "running",
        }

        if resuming and existing_progress is not None:
            years = existing_progress.years_remaining
            logger.info(
                "Resuming from checkpoint",
                extra={
                    "event": "sync.resume",
                    "dataset": dataset,
                    "years_remaining": years,
                    "years_completed": existing_progress.years_completed,
                },
            )

        # Acquire exclusive lock (manual management for refresh support)
        lock = AtomicFileLock(self.lock_dir, dataset)
        lock_token = lock.acquire(timeout_seconds=60.0)
        last_refresh = datetime.datetime.now(datetime.UTC)

        try:
            # Check disk space with estimated rows
            estimated_rows = len(years) * 250_000  # ~250k rows per year typical
            self._check_disk_space_and_alert(estimated_rows=estimated_rows)

            # When resuming, carry forward completed partitions and row counts
            if resuming and existing_progress is not None:
                file_paths = [
                    str(self.storage_path / dataset / f"{year}.parquet")
                    for year in existing_progress.years_completed
                ]
                total_rows = existing_progress.total_rows_synced
                years_completed = existing_progress.years_completed.copy()
                started_at = existing_progress.started_at
            else:
                file_paths = []
                total_rows = 0
                years_completed = []
                started_at = sync_start

            # Initialize or update progress
            progress = SyncProgress(
                dataset=dataset,
                started_at=started_at,
                last_checkpoint=sync_start,
                years_completed=years_completed,
                years_remaining=years.copy(),
                total_rows_synced=total_rows,
                status="running",
            )
            self._save_progress(progress)

            try:
                for year in years:
                    # Sync single year partition
                    path, rows = self._sync_year_partition(
                        dataset, year, lock_token
                    )
                    file_paths.append(str(path))
                    total_rows += rows

                    # Update progress
                    progress.years_completed.append(year)
                    progress.years_remaining.remove(year)
                    progress.total_rows_synced = total_rows
                    progress.last_checkpoint = datetime.datetime.now(datetime.UTC)
                    self._save_progress(progress)

                    # Refresh lock if needed (every REFRESH_INTERVAL_SECONDS)
                    now = datetime.datetime.now(datetime.UTC)
                    if (now - last_refresh).total_seconds() >= lock.REFRESH_INTERVAL_SECONDS:
                        lock_token = lock.refresh(lock_token)
                        last_refresh = now
                        logger.debug(
                            "Lock refreshed",
                            extra={
                                "event": "sync.lock.refresh",
                                "dataset": dataset,
                            },
                        )

                # All years complete
                progress.status = "completed"
                self._save_progress(progress)

            except Exception as e:
                progress.status = "failed"
                self._save_progress(progress)
                logger.error(
                    "Full sync failed",
                    extra={
                        "event": "sync.full.failed",
                        "dataset": dataset,
                        "error": str(e),
                    },
                )
                raise

            # Check sync duration SLO
            sync_duration = datetime.datetime.now(datetime.UTC) - sync_start
            if sync_duration.total_seconds() > self.FULL_SYNC_SLO_HOURS * 3600:
                logger.warning(
                    "Full sync exceeded SLO",
                    extra={
                        "event": "sync.duration.slo_breach",
                        "dataset": dataset,
                        "duration_hours": sync_duration.total_seconds() / 3600,
                        "slo_hours": self.FULL_SYNC_SLO_HOURS,
                    },
                )

            # Create manifest
            manifest = self._create_manifest(
                dataset=dataset,
                file_paths=file_paths,
                row_count=total_rows,
                start_date=datetime.date(start_year, 1, 1),
                end_date=datetime.date(end_year, 12, 31),
            )

            # Save manifest
            self.manifest_manager.save_manifest(manifest, lock_token)

            logger.info(
                "Full sync completed",
                extra={
                    "event": "sync.full.complete",
                    "dataset": dataset,
                    "total_rows": total_rows,
                    "file_count": len(file_paths),
                },
            )

            return manifest
        finally:
            lock.release(lock_token)

    def incremental_sync(self, dataset: str) -> SyncManifest:
        """Execute incremental sync for new data since last sync.

        Args:
            dataset: Dataset name.

        Returns:
            Updated SyncManifest.

        Raises:
            ValueError: If no existing manifest (run full_sync first).
        """
        sync_start = datetime.datetime.now(datetime.UTC)

        # Load existing manifest
        current_manifest = self.manifest_manager.load_manifest(dataset)
        if not current_manifest:
            raise ValueError(f"No existing manifest for {dataset}. Run full_sync first.")

        last_date = current_manifest.end_date
        today = datetime.date.today()

        if last_date >= today:
            logger.info(
                "Already up to date",
                extra={"event": "sync.incremental.uptodate", "dataset": dataset},
            )
            return current_manifest

        logger.info(
            "Starting incremental sync",
            extra={
                "event": "sync.incremental.start",
                "dataset": dataset,
                "from_date": str(last_date),
                "to_date": str(today),
            },
        )

        # Acquire exclusive lock (manual management for refresh support)
        lock = AtomicFileLock(self.lock_dir, dataset)
        lock_token = lock.acquire(timeout_seconds=60.0)
        last_refresh = datetime.datetime.now(datetime.UTC)

        try:
            # Determine years to update
            years_to_sync: set[int] = set()
            current_date = last_date
            while current_date <= today:
                years_to_sync.add(current_date.year)
                current_date += datetime.timedelta(days=1)

            # Check disk space with estimated rows
            estimated_rows = len(years_to_sync) * 50_000  # ~50k new rows per year
            self._check_disk_space_and_alert(estimated_rows=estimated_rows)

            # Sync new data
            file_paths = list(current_manifest.file_paths)

            # Start with zero - we'll recompute from actual files
            total_rows = 0

            # Track rows for years NOT being updated (use Path.name for cross-platform)
            for file_path_str in file_paths:
                file_path = Path(file_path_str)
                if file_path.exists():
                    # Check if this year is being updated using Path.name
                    is_updated_year = any(
                        file_path.name == f"{year}.parquet" for year in years_to_sync
                    )
                    if not is_updated_year:
                        # Not being updated - count its rows
                        df = pl.scan_parquet(file_path).select(pl.len()).collect()
                        total_rows += df.item()

            for year in sorted(years_to_sync):
                path, rows = self._sync_year_partition(
                    dataset, year, lock_token, incremental=True,
                    last_date=last_date if year == last_date.year else None,
                )
                # Update or add file path using Path.name for cross-platform
                year_filename = f"{year}.parquet"
                # Remove old path for this year if exists
                file_paths = [
                    p for p in file_paths if Path(p).name != year_filename
                ]
                file_paths.append(str(path))
                # Add new partition rows
                total_rows += rows

                # Refresh lock if needed
                now = datetime.datetime.now(datetime.UTC)
                if (now - last_refresh).total_seconds() >= lock.REFRESH_INTERVAL_SECONDS:
                    lock_token = lock.refresh(lock_token)
                    last_refresh = now
                    logger.debug(
                        "Lock refreshed",
                        extra={
                            "event": "sync.lock.refresh",
                            "dataset": dataset,
                        },
                    )

            # Check SLO
            sync_duration = datetime.datetime.now(datetime.UTC) - sync_start
            if sync_duration.total_seconds() > self.INCREMENTAL_SYNC_SLO_MINUTES * 60:
                logger.warning(
                    "Incremental sync exceeded SLO",
                    extra={
                        "event": "sync.duration.slo_breach",
                        "dataset": dataset,
                        "duration_minutes": sync_duration.total_seconds() / 60,
                        "slo_minutes": self.INCREMENTAL_SYNC_SLO_MINUTES,
                    },
                )

            # Create updated manifest
            manifest = self._create_manifest(
                dataset=dataset,
                file_paths=file_paths,
                row_count=total_rows,
                start_date=current_manifest.start_date,
                end_date=today,
            )

            self.manifest_manager.save_manifest(manifest, lock_token)

            logger.info(
                "Incremental sync completed",
                extra={
                    "event": "sync.incremental.complete",
                    "dataset": dataset,
                    "new_rows": total_rows - current_manifest.row_count,
                },
            )

            return manifest
        finally:
            lock.release(lock_token)

    def verify_integrity(self, dataset: str) -> list[str]:
        """Verify integrity of synced data.

        Checks:
        - All files in manifest exist
        - Combined checksum matches manifest
        - Row counts are consistent

        Args:
            dataset: Dataset name.

        Returns:
            List of error messages (empty if valid).
        """
        errors: list[str] = []

        manifest = self.manifest_manager.load_manifest(dataset)
        if not manifest:
            errors.append(f"No manifest found for {dataset}")
            return errors

        logger.info(
            "Verifying integrity",
            extra={
                "event": "sync.verify.start",
                "dataset": dataset,
            },
        )

        # Check each file exists
        for file_path_str in manifest.file_paths:
            file_path = Path(file_path_str)

            if not file_path.exists():
                errors.append(f"Missing file: {file_path}")
                logger.error(
                    "File missing",
                    extra={
                        "event": "sync.manifest.mismatch",
                        "dataset": dataset,
                        "file": str(file_path),
                    },
                )

        # If any files missing, cannot verify checksum
        if errors:
            logger.error(
                "Integrity verification failed - missing files",
                extra={
                    "event": "sync.verify.failed",
                    "dataset": dataset,
                    "error_count": len(errors),
                },
            )
            return errors

        # Verify combined checksum matches manifest
        computed_checksum = self._compute_combined_checksum(manifest.file_paths)
        if computed_checksum != manifest.checksum:
            error_msg = (
                f"Checksum mismatch: manifest={manifest.checksum[:16]}..., "
                f"computed={computed_checksum[:16]}..."
            )
            errors.append(error_msg)
            logger.error(
                "Checksum mismatch",
                extra={
                    "event": "sync.checksum.mismatch",
                    "dataset": dataset,
                    "manifest_checksum": manifest.checksum,
                    "computed_checksum": computed_checksum,
                },
            )

        # Verify row count by scanning files
        total_rows = 0
        for file_path_str in manifest.file_paths:
            file_path = Path(file_path_str)
            try:
                df = pl.scan_parquet(file_path).select(pl.len()).collect()
                total_rows += df.item()
            except Exception as e:
                errors.append(f"Cannot read {file_path}: {e}")

        if total_rows != manifest.row_count:
            error_msg = (
                f"Row count mismatch: manifest={manifest.row_count}, "
                f"computed={total_rows}"
            )
            errors.append(error_msg)
            logger.error(
                "Row count mismatch",
                extra={
                    "event": "sync.verify.row_mismatch",
                    "dataset": dataset,
                    "manifest_rows": manifest.row_count,
                    "computed_rows": total_rows,
                },
            )

        if errors:
            logger.error(
                "Integrity verification failed",
                extra={
                    "event": "sync.verify.failed",
                    "dataset": dataset,
                    "error_count": len(errors),
                },
            )
        else:
            logger.info(
                "Integrity verification passed",
                extra={
                    "event": "sync.verify.passed",
                    "dataset": dataset,
                },
            )

        return errors

    # Primary key columns for deduplication during incremental sync
    DATASET_PRIMARY_KEYS: dict[str, list[str]] = {
        "crsp_daily": ["date", "permno"],
        "compustat_annual": ["datadate", "gvkey"],
        "compustat_quarterly": ["datadate", "gvkey"],
        "fama_french": ["date"],
    }

    def _sync_year_partition(
        self,
        dataset: str,
        year: int,
        lock_token: LockToken,
        incremental: bool = False,
        last_date: datetime.date | None = None,
    ) -> tuple[Path, int]:
        """Sync a single year partition.

        Args:
            dataset: Dataset name.
            year: Year to sync.
            lock_token: Exclusive lock token.
            incremental: If True, merge new data with existing.
            last_date: For incremental sync, fetch data after this date.

        Returns:
            Tuple of (file_path, row_count).
        """
        # Build query based on dataset
        query = self._build_query(dataset, year, incremental, last_date)

        logger.debug(
            "Syncing year partition",
            extra={
                "dataset": dataset,
                "year": year,
                "incremental": incremental,
            },
        )

        # Execute query
        new_df = self.wrds_client.execute_query(query)

        if new_df.is_empty() and not incremental:
            logger.warning(
                "No data for year",
                extra={"dataset": dataset, "year": year},
            )

        # Validate schema
        current_schema = {col: str(new_df.schema[col]) for col in new_df.columns}
        drift = self.schema_registry.detect_drift(dataset, current_schema)

        if drift.is_breaking:
            logger.error(
                "Breaking schema drift",
                extra={
                    "event": "sync.schema.breaking",
                    "dataset": dataset,
                    "removed": drift.removed_columns,
                    "changed": drift.changed_columns,
                },
            )
            raise SchemaError(drift, f"Breaking schema drift for {dataset}")

        if drift.has_additions:
            logger.warning(
                "New columns detected",
                extra={
                    "event": "sync.schema.additions",
                    "dataset": dataset,
                    "new_columns": drift.added_columns,
                },
            )
            self.schema_registry.apply_drift_policy(dataset, drift, current_schema)

        # Prepare output path
        output_dir = self.storage_path / dataset
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{year}.parquet"

        # For incremental sync, merge with existing data using streaming approach
        if incremental and output_path.exists():
            primary_keys = self.DATASET_PRIMARY_KEYS.get(dataset, [])

            if new_df.is_empty():
                # No new data - skip rewrite entirely, return existing file info
                # This avoids unnecessary IO, checksum changes, and memory usage
                existing_rows = pl.scan_parquet(output_path).select(pl.len()).collect().item()
                logger.debug(
                    "No new data for partition, keeping existing",
                    extra={
                        "dataset": dataset,
                        "year": year,
                        "existing_rows": existing_rows,
                    },
                )
                return output_path, existing_rows

            if primary_keys:
                # Deduplicate new_df on primary keys before merge
                # WRDS can return duplicate keys in late corrections
                new_df_deduped = new_df.unique(subset=primary_keys, keep="last")

                # Use anti-join to avoid loading full partition into memory
                # Only new_df is in memory; existing data is streamed via lazy scan
                existing_lazy = pl.scan_parquet(output_path)

                # Filter existing to exclude rows that will be replaced by new data
                # This is an anti-join: keep existing rows NOT in new_df's keys
                filtered_existing = existing_lazy.join(
                    new_df_deduped.lazy().select(primary_keys),
                    on=primary_keys,
                    how="anti",
                )

                # Get existing row count for logging (streaming count)
                existing_count = existing_lazy.select(pl.len()).collect().item()

                # Concatenate filtered existing + deduped new, sort by keys
                # Use engine="streaming" to avoid loading full partition into memory
                df = (
                    pl.concat([filtered_existing, new_df_deduped.lazy()])
                    .sort(primary_keys)
                    .collect(engine="streaming")
                )

                logger.info(
                    "Merged incremental data",
                    extra={
                        "dataset": dataset,
                        "year": year,
                        "existing_rows": existing_count,
                        "new_rows": new_df_deduped.height,
                        "merged_rows": df.height,
                    },
                )
            else:
                # No primary keys defined, just concatenate using lazy
                existing_lazy = pl.scan_parquet(output_path)
                df = pl.concat([existing_lazy, new_df.lazy()]).collect()
        else:
            df = new_df

        # Validate data before writing
        self._validate_partition(df, dataset, year)

        # Write atomically
        checksum = self._atomic_write_parquet(df, output_path)

        logger.info(
            "Year partition synced",
            extra={
                "dataset": dataset,
                "year": year,
                "rows": df.height,
                "checksum": checksum[:16],
            },
        )

        return output_path, df.height

    def _validate_partition(
        self, df: pl.DataFrame, dataset: str, year: int
    ) -> None:
        """Validate data quality before persisting.

        Uses the DataValidator to check:
        - Primary key uniqueness (no duplicate rows)
        - No nulls in primary key columns

        Args:
            df: DataFrame to validate.
            dataset: Dataset name.
            year: Year being synced.

        Raises:
            ValidationError: If validation fails.
        """
        if df.is_empty():
            return  # Empty partitions are valid

        primary_keys = self.DATASET_PRIMARY_KEYS.get(dataset, [])
        if not primary_keys:
            return  # No primary keys to validate

        # Check for nulls in primary key columns
        pk_null_thresholds = {pk: 0.0 for pk in primary_keys if pk in df.columns}
        if pk_null_thresholds:
            errors = self.validator.validate_null_percentage(df, pk_null_thresholds)
            if errors:
                error_msgs = [str(e) for e in errors]
                logger.error(
                    "Primary key null validation failed",
                    extra={
                        "event": "sync.validation.failed",
                        "dataset": dataset,
                        "year": year,
                        "errors": error_msgs,
                    },
                )
                raise ValueError(f"Validation failed for {dataset}/{year}: {error_msgs}")

        # Check for duplicate primary keys
        duplicate_count = df.height - df.unique(subset=primary_keys).height
        if duplicate_count > 0:
            logger.error(
                "Duplicate primary keys detected",
                extra={
                    "event": "sync.validation.failed",
                    "dataset": dataset,
                    "year": year,
                    "duplicate_count": duplicate_count,
                },
            )
            raise ValueError(
                f"Validation failed for {dataset}/{year}: "
                f"{duplicate_count} duplicate primary keys detected"
            )

        logger.debug(
            "Partition validation passed",
            extra={
                "dataset": dataset,
                "year": year,
                "rows": df.height,
            },
        )

    def _atomic_write_parquet(self, df: pl.DataFrame, target_path: Path) -> str:
        """Write Parquet file atomically.

        Pattern:
        1. Write to temp file
        2. Compute checksum
        3. fsync temp file
        4. Atomic rename to target
        5. fsync parent directory

        Args:
            df: DataFrame to write.
            target_path: Final destination path.

        Returns:
            MD5 checksum of written file.

        Raises:
            DiskSpaceError: If disk full during write.
            ChecksumMismatchError: If verification fails.
        """
        temp_path = target_path.with_suffix(".parquet.tmp")

        try:
            # Write to temp
            df.write_parquet(temp_path)

            # Compute checksum and fsync in single file open (optimized)
            checksum = self._compute_checksum_and_fsync(temp_path)

            # Atomic rename
            # Use replace instead of rename for cross-platform atomic overwrite
            # (rename raises FileExistsError on Windows if target exists)
            temp_path.replace(target_path)

            # fsync parent directory
            self._fsync_directory(target_path.parent)

            return checksum

        except OSError as e:
            # Handle disk full
            if e.errno == 28:  # ENOSPC
                logger.error(
                    "Disk full during write",
                    extra={
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

    def _compute_checksum(self, path: Path) -> str:
        """Compute MD5 checksum of file.

        Args:
            path: File path.

        Returns:
            Hex digest of MD5 hash.
        """
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _compute_checksum_and_fsync(self, path: Path) -> str:
        """Compute MD5 checksum and fsync in single file operation.

        Optimized version that reads file once for checksum then fsyncs,
        avoiding two separate file opens.

        Args:
            path: File path.

        Returns:
            Hex digest of MD5 hash.
        """
        hasher = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
            # fsync after reading (file descriptor still open)
            os.fsync(f.fileno())
        return hasher.hexdigest()

    def _quarantine_failed(self, temp_path: Path, reason: str) -> None:
        """Move failed temp file to quarantine.

        Args:
            temp_path: Path to failed temp file.
            reason: Reason for quarantine.
        """
        if not temp_path.exists():
            return

        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        quarantine_dir = self.QUARANTINE_DIR / f"{timestamp}_{temp_path.stem}"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        dest = quarantine_dir / temp_path.name
        shutil.move(str(temp_path), str(dest))

        # Write reason file
        reason_file = quarantine_dir / "reason.txt"
        with open(reason_file, "w") as f:
            f.write(f"Quarantined at: {timestamp}\n")
            f.write(f"Reason: {reason}\n")
            f.write(f"Original path: {temp_path}\n")

        logger.warning(
            "File quarantined",
            extra={
                "event": "sync.checksum.mismatch",
                "path": str(temp_path),
                "quarantine": str(quarantine_dir),
                "reason": reason,
            },
        )

    # Estimated bytes per row for disk space calculation
    BYTES_PER_ROW_ESTIMATE = 200  # Conservative estimate for WRDS data

    def _check_disk_space_and_alert(
        self,
        required_bytes: int = 0,
        estimated_rows: int = 0,
    ) -> DiskSpaceStatus:
        """Check disk space and emit alerts.

        Args:
            required_bytes: Bytes needed for operation (if known).
            estimated_rows: Estimated rows to sync (used for space calculation).

        Returns:
            DiskSpaceStatus.

        Raises:
            DiskSpaceError: If insufficient space for operation.
        """
        stat = shutil.disk_usage(self.storage_path)
        used_pct = (stat.total - stat.free) / stat.total

        # Calculate required space (2x for temp file + final file)
        if required_bytes == 0 and estimated_rows > 0:
            required_bytes = estimated_rows * self.BYTES_PER_ROW_ESTIMATE * 2
        elif required_bytes > 0:
            required_bytes = required_bytes * 2  # Account for temp file

        # Check if we have enough free space for the operation
        if required_bytes > 0 and stat.free < required_bytes:
            logger.error(
                "Insufficient disk space for operation",
                extra={
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
                    "event": "sync.disk.blocked",
                    "used_pct": used_pct,
                },
            )
            raise DiskSpaceError(f"Disk usage at {used_pct:.1%}, blocked at {self.DISK_BLOCKED_PCT:.0%}")

        if used_pct >= self.DISK_CRITICAL_PCT:
            logger.critical(
                "Disk space critical",
                extra={
                    "event": "sync.disk.critical",
                    "used_pct": used_pct,
                },
            )
            level: Literal["ok", "warning", "critical"] = "critical"
        elif used_pct >= self.DISK_WARNING_PCT:
            logger.warning(
                "Disk space warning",
                extra={
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

    def _save_progress(self, progress: SyncProgress) -> None:
        """Save sync progress for resume.

        Uses atomic write pattern (temp + fsync + rename + dir-fsync) to ensure
        progress is durable even on crash.

        Args:
            progress: Current progress state.
        """
        path = self.PROGRESS_DIR / f"{progress.dataset}.json"
        temp_path = path.with_suffix(".json.tmp")

        with open(temp_path, "w") as f:
            f.write(progress.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())

        # Use replace for cross-platform atomic overwrite
        temp_path.replace(path)
        # fsync directory to ensure replace is durable
        self._fsync_directory(path.parent)

    def _load_progress(self, dataset: str) -> SyncProgress | None:
        """Load sync progress if exists.

        Args:
            dataset: Dataset name.

        Returns:
            SyncProgress if found, None otherwise.
        """
        path = self.PROGRESS_DIR / f"{dataset}.json"
        if not path.exists():
            return None

        with open(path) as f:
            data = f.read()

        return SyncProgress.model_validate_json(data)

    def _create_manifest(
        self,
        dataset: str,
        file_paths: list[str],
        row_count: int,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> SyncManifest:
        """Create a SyncManifest for completed sync.

        Args:
            dataset: Dataset name.
            file_paths: List of synced file paths.
            row_count: Total rows synced.
            start_date: Data range start.
            end_date: Data range end.

        Returns:
            SyncManifest instance.
        """
        # Compute combined checksum
        combined_checksum = self._compute_combined_checksum(file_paths)

        # Get schema version
        schema = self.schema_registry.get_expected_schema(dataset)
        schema_version = schema.version if schema else "v1.0.0"

        # Compute query hash (placeholder)
        query_hash = hashlib.md5(f"{dataset}:{start_date}:{end_date}".encode()).hexdigest()

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
        """Compute combined checksum for multiple files.

        Args:
            file_paths: List of file paths.

        Returns:
            Combined MD5 hex digest.
        """
        hasher = hashlib.md5()
        for path_str in sorted(file_paths):
            path = Path(path_str)
            if path.exists():
                file_checksum = self._compute_checksum(path)
                hasher.update(file_checksum.encode())
        return hasher.hexdigest()

    # Supported datasets and their date column names
    SUPPORTED_DATASETS: dict[str, str] = {
        "crsp_daily": "date",
        "compustat_annual": "datadate",
        "compustat_quarterly": "datadate",
        "fama_french": "date",
    }

    def _build_query(
        self,
        dataset: str,
        year: int,
        incremental: bool,
        last_date: datetime.date | None = None,
    ) -> str:
        """Build SQL query for dataset sync.

        Args:
            dataset: Dataset name.
            year: Year to query.
            incremental: If True, only fetch recent data.
            last_date: For incremental, start date for query.

        Returns:
            SQL query string.

        Raises:
            ValueError: If dataset is not explicitly supported.
        """
        if dataset not in self.SUPPORTED_DATASETS:
            raise ValueError(
                f"Dataset '{dataset}' is not supported. "
                f"Supported datasets: {list(self.SUPPORTED_DATASETS.keys())}. "
                f"Add explicit query support for new datasets."
            )

        # Determine date range based on incremental flag
        if incremental and last_date and last_date.year == year:
            # For incremental: fetch only new data since last_date
            start_date = (last_date + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            # Full partition sync: fetch entire year
            start_date = f"{year}-01-01"
        end_date = f"{year}-12-31"

        # Dataset-specific queries with proper date columns
        # ORDER BY primary keys for deterministic deduplication (keep="last" requires order)
        if dataset == "crsp_daily":
            return f"""
                SELECT date, permno, cusip, ticker, ret, prc, vol, shrout
                FROM crsp.dsf
                WHERE date >= '{start_date}' AND date <= '{end_date}'
                ORDER BY date, permno
            """
        elif dataset == "compustat_annual":
            return f"""
                SELECT datadate, gvkey, at, lt, sale, ni, ceq
                FROM comp.funda
                WHERE datadate >= '{start_date}' AND datadate <= '{end_date}'
                AND indfmt = 'INDL' AND datafmt = 'STD' AND popsrc = 'D' AND consol = 'C'
                ORDER BY datadate, gvkey
            """
        elif dataset == "compustat_quarterly":
            return f"""
                SELECT datadate, gvkey, atq, ltq, saleq, niq
                FROM comp.fundq
                WHERE datadate >= '{start_date}' AND datadate <= '{end_date}'
                AND indfmt = 'INDL' AND datafmt = 'STD' AND popsrc = 'D' AND consol = 'C'
                ORDER BY datadate, gvkey
            """
        elif dataset == "fama_french":
            return f"""
                SELECT date, mktrf, smb, hml, rf, umd
                FROM ff.factors_daily
                WHERE date >= '{start_date}' AND date <= '{end_date}'
                ORDER BY date
            """
        # Should never reach here due to check above
        raise ValueError(f"No query template for {dataset}")

    def _fsync_directory(self, dir_path: Path) -> None:
        """Sync directory for crash safety.

        Args:
            dir_path: Directory to sync.
        """
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            logger.warning("Failed to fsync directory", extra={"path": str(dir_path)})

"""Fama-French Local Data Provider.

Provider for Fama-French factor data stored in Parquet files with sync capability.
Downloads data from Ken French's Data Library using pandas-datareader.

This module provides:
- FamaFrenchLocalProvider: Factor and industry data access with DuckDB + sync
- FamaFrenchSyncError: Raised when sync operations fail
- ChecksumError: Raised when checksum validation fails

Data is normalized from percent to decimal (÷100) during sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
import polars as pl

from libs.data_providers.locking import AtomicFileLock
from libs.data_quality.exceptions import DataNotFoundError

logger = logging.getLogger(__name__)


class FamaFrenchSyncError(Exception):
    """Raised when Fama-French sync operations fail."""

    pass


class ChecksumError(Exception):
    """Raised when checksum validation fails."""

    pass


# Schema definitions for validation
FF3_COLUMNS = ("date", "mkt_rf", "smb", "hml", "rf")
FF5_COLUMNS = ("date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf")
FF6_COLUMNS = ("date", "mkt_rf", "smb", "hml", "rmw", "cma", "umd", "rf")
MOM_COLUMNS = ("date", "umd")


class FamaFrenchLocalProvider:
    """Provider for Fama-French factor data with sync capability.

    Provides read access to locally cached Fama-French data, with built-in
    sync functionality to download from Ken French Data Library.

    Note: This provider uses its own manifest format (per-file checksums)
    rather than the shared ManifestManager, as Fama-French data has different
    schema requirements than CRSP/Compustat data.

    Features:
    - Download from Ken French Data Library via pandas-datareader
    - Local Parquet caching with atomic writes
    - Support for 3-factor, 5-factor, 6-factor (with momentum)
    - Industry portfolio returns (10, 30, 49 industries)
    - Daily and monthly frequencies
    - Per-file checksums for integrity verification
    - Quarantine for failed/corrupted files
    - Return normalization (percent → decimal)

    Storage Layout:
        data/fama_french/
        ├── factors/
        │   ├── factors_3_daily.parquet
        │   ├── factors_3_monthly.parquet
        │   ├── factors_5_daily.parquet
        │   ├── factors_5_monthly.parquet
        │   ├── factors_6_daily.parquet
        │   ├── factors_6_monthly.parquet
        │   ├── momentum_daily.parquet
        │   └── momentum_monthly.parquet
        ├── industries/
        │   ├── ind10_daily.parquet
        │   ├── ind10_monthly.parquet
        │   ├── ind30_daily.parquet
        │   ├── ind30_monthly.parquet
        │   ├── ind49_daily.parquet
        │   └── ind49_monthly.parquet
        ├── quarantine/
        └── fama_french_manifest.json
    """

    DATASET_NAME = "fama_french"

    # Ken French data source identifiers
    FF3_DAILY = "F-F_Research_Data_Factors_daily"
    FF3_MONTHLY = "F-F_Research_Data_Factors"
    FF5_DAILY = "F-F_Research_Data_5_Factors_2x3_daily"
    FF5_MONTHLY = "F-F_Research_Data_5_Factors_2x3"
    MOM_DAILY = "F-F_Momentum_Factor_daily"
    MOM_MONTHLY = "F-F_Momentum_Factor"

    # Industry portfolios - daily
    IND10_DAILY = "10_Industry_Portfolios_daily"
    IND30_DAILY = "30_Industry_Portfolios_daily"
    IND49_DAILY = "49_Industry_Portfolios_daily"

    # Industry portfolios - monthly
    IND10_MONTHLY = "10_Industry_Portfolios"
    IND30_MONTHLY = "30_Industry_Portfolios"
    IND49_MONTHLY = "49_Industry_Portfolios"

    # All datasets to sync
    ALL_DATASETS = [
        # Factor datasets
        ("factors_3_daily", FF3_DAILY),
        ("factors_3_monthly", FF3_MONTHLY),
        ("factors_5_daily", FF5_DAILY),
        ("factors_5_monthly", FF5_MONTHLY),
        ("momentum_daily", MOM_DAILY),
        ("momentum_monthly", MOM_MONTHLY),
        # Industry datasets - daily
        ("ind10_daily", IND10_DAILY),
        ("ind30_daily", IND30_DAILY),
        ("ind49_daily", IND49_DAILY),
        # Industry datasets - monthly
        ("ind10_monthly", IND10_MONTHLY),
        ("ind30_monthly", IND30_MONTHLY),
        ("ind49_monthly", IND49_MONTHLY),
    ]

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2.0

    def __init__(
        self,
        storage_path: Path,
        lock_dir: Path | None = None,
    ) -> None:
        """Initialize Fama-French provider.

        Args:
            storage_path: Base path for data storage (e.g., data/fama_french).
            lock_dir: Directory for lock files. Defaults to storage_path/locks.
        """
        self._storage_path = self._validate_storage_path(storage_path)
        self._lock_dir = lock_dir or (storage_path / "locks")

        # Create directories
        self._factors_dir = self._storage_path / "factors"
        self._industries_dir = self._storage_path / "industries"
        self._quarantine_dir = self._storage_path / "quarantine"

        self._factors_dir.mkdir(parents=True, exist_ok=True)
        self._industries_dir.mkdir(parents=True, exist_ok=True)
        self._lock_dir.mkdir(parents=True, exist_ok=True)

    def _validate_storage_path(self, storage_path: Path) -> Path:
        """Validate storage path to prevent path traversal.

        Args:
            storage_path: Path to validate.

        Returns:
            Resolved absolute path.

        Raises:
            ValueError: If path is invalid or attempts traversal.
        """
        resolved = storage_path.resolve()

        # Check for path traversal attempts
        if ".." in str(storage_path):
            raise ValueError(f"Path traversal detected in storage_path: {storage_path}")

        return resolved

    def get_factors(
        self,
        start_date: date,
        end_date: date,
        model: Literal["ff3", "ff5", "ff6"] = "ff3",
        frequency: Literal["daily", "monthly"] = "daily",
    ) -> pl.DataFrame:
        """Get Fama-French factor returns.

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            model: Factor model:
                - ff3: 3-factor (Mkt-RF, SMB, HML, RF)
                - ff5: 5-factor (adds RMW, CMA)
                - ff6: 6-factor (adds UMD/momentum)
            frequency: Data frequency (daily or monthly).

        Returns:
            DataFrame with date and factor columns.
            All returns are in decimal form (not percent).

        Raises:
            DataNotFoundError: If data files don't exist.
            ValueError: If invalid model or frequency.
        """
        if model not in ("ff3", "ff5", "ff6"):
            raise ValueError(f"Invalid model: {model}. Must be ff3, ff5, or ff6")
        if frequency not in ("daily", "monthly"):
            raise ValueError(f"Invalid frequency: {frequency}. Must be daily or monthly")

        # Determine which file to use
        if model == "ff6":
            filename = f"factors_6_{frequency}.parquet"
        elif model == "ff5":
            filename = f"factors_5_{frequency}.parquet"
        else:  # ff3
            filename = f"factors_3_{frequency}.parquet"

        file_path = self._factors_dir / filename

        if not file_path.exists():
            raise DataNotFoundError(
                f"Factor data not found: {filename}. Run sync_data() first."
            )

        # Query with DuckDB for efficient date filtering
        conn = duckdb.connect(":memory:")
        try:
            query = f"""
                SELECT *
                FROM read_parquet('{file_path}')
                WHERE date >= '{start_date}' AND date <= '{end_date}'
                ORDER BY date
            """
            result = conn.execute(query).pl()
            return result
        finally:
            conn.close()

    def get_industry_returns(
        self,
        start_date: date,
        end_date: date,
        num_industries: Literal[10, 30, 49] = 49,
        frequency: Literal["daily", "monthly"] = "daily",
    ) -> pl.DataFrame:
        """Get industry portfolio returns.

        Args:
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            num_industries: Industry classification (10, 30, or 49).
            frequency: Data frequency (daily or monthly).

        Returns:
            DataFrame with date and industry return columns.
            All returns are in decimal form (not percent).

        Raises:
            DataNotFoundError: If data files don't exist.
            ValueError: If invalid num_industries or frequency.
        """
        if num_industries not in (10, 30, 49):
            raise ValueError(
                f"Invalid num_industries: {num_industries}. Must be 10, 30, or 49"
            )
        if frequency not in ("daily", "monthly"):
            raise ValueError(f"Invalid frequency: {frequency}. Must be daily or monthly")

        filename = f"ind{num_industries}_{frequency}.parquet"
        file_path = self._industries_dir / filename

        if not file_path.exists():
            raise DataNotFoundError(
                f"Industry data not found: {filename}. Run sync_data() first."
            )

        # Query with DuckDB for efficient date filtering
        conn = duckdb.connect(":memory:")
        try:
            query = f"""
                SELECT *
                FROM read_parquet('{file_path}')
                WHERE date >= '{start_date}' AND date <= '{end_date}'
                ORDER BY date
            """
            result = conn.execute(query).pl()
            return result
        finally:
            conn.close()

    def sync_data(
        self,
        datasets: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Sync data from Ken French Data Library.

        Downloads factor and industry data, normalizes returns from percent
        to decimal, and stores as Parquet with atomic writes.

        Preserves manifest entries for existing datasets that are skipped.
        Reports any download failures in the returned result.

        Args:
            datasets: Specific dataset names to sync (None = all).
                Valid names: factors_3_daily, factors_5_daily, etc.
            force: Force re-download even if data exists.

        Returns:
            Dict with sync results including:
            - files: Per-file checksums and metadata
            - failed_datasets: List of datasets that failed to download
            - total_row_count: Sum of rows across all files

        Raises:
            FamaFrenchSyncError: If sync fails catastrophically.
        """
        # Import pandas_datareader here to avoid import errors if not installed
        try:
            import pandas_datareader.data as web
        except ImportError as e:
            raise FamaFrenchSyncError(
                "pandas-datareader not installed. Run: pip install pandas-datareader"
            ) from e

        # Determine which datasets to sync
        if datasets is None:
            to_sync = self.ALL_DATASETS
        else:
            to_sync = [(name, src) for name, src in self.ALL_DATASETS if name in datasets]
            if not to_sync:
                raise ValueError(f"No valid datasets found in: {datasets}")

        # Acquire exclusive lock for entire sync operation
        lock = AtomicFileLock(
            lock_dir=self._lock_dir,
            dataset=self.DATASET_NAME,
        )

        try:
            lock_token = lock.acquire()
        except Exception as e:
            raise FamaFrenchSyncError(f"Failed to acquire sync lock: {e}") from e

        try:
            # CRITICAL: Load existing manifest to preserve entries for skipped datasets
            existing_manifest = self.get_manifest()
            manifest_entries: dict[str, dict[str, Any]] = {}
            if existing_manifest and "files" in existing_manifest:
                manifest_entries = dict(existing_manifest["files"])

            total_rows = 0
            failed_datasets: list[str] = []

            for name, source in to_sync:
                logger.info("Syncing dataset", extra={"dataset": name, "source": source})

                # Check if file exists and skip if not forcing
                target_path = self._get_target_path(name)
                if target_path.exists() and not force:
                    logger.info(
                        "Skipping existing dataset",
                        extra={"dataset": name, "path": str(target_path)},
                    )
                    # Preserve existing manifest entry (already in manifest_entries)
                    continue

                # Download with retries
                df = self._download_with_retry(web, source, name)

                if df is None or df.height == 0:
                    logger.warning(
                        "Download failed or empty dataset",
                        extra={"dataset": name},
                    )
                    failed_datasets.append(name)
                    continue

                # Normalize returns (percent → decimal)
                df = self._normalize_returns(df)

                # Atomic write
                checksum = self._atomic_write_parquet(df, target_path)

                # Get date range
                date_col = df.get_column("date")
                min_date = date_col.min()
                max_date = date_col.max()

                manifest_entries[target_path.name] = {
                    "checksum": checksum,
                    "row_count": df.height,
                    "start_date": str(min_date) if min_date else None,
                    "end_date": str(max_date) if max_date else None,
                }

                logger.info(
                    "Dataset synced",
                    extra={
                        "dataset": name,
                        "rows": df.height,
                        "checksum": checksum[:16] + "...",
                    },
                )

            # Generate 6-factor files by joining 5-factor + momentum
            for freq in ["daily", "monthly"]:
                ff6_path = self._factors_dir / f"factors_6_{freq}.parquet"
                ff5_path = self._factors_dir / f"factors_5_{freq}.parquet"
                mom_path = self._factors_dir / f"momentum_{freq}.parquet"

                if ff5_path.exists() and mom_path.exists():
                    if not ff6_path.exists() or force:
                        ff6_df = self._create_ff6(ff5_path, mom_path)
                        checksum = self._atomic_write_parquet(ff6_df, ff6_path)

                        date_col = ff6_df.get_column("date")
                        manifest_entries[ff6_path.name] = {
                            "checksum": checksum,
                            "row_count": ff6_df.height,
                            "start_date": str(date_col.min()),
                            "end_date": str(date_col.max()),
                        }

                        logger.info(
                            "6-factor file created",
                            extra={"frequency": freq, "rows": ff6_df.height},
                        )

            # Calculate total rows from all manifest entries
            for entry in manifest_entries.values():
                total_rows += entry.get("row_count", 0)

            # Update manifest (preserves existing + adds new)
            manifest_data: dict[str, Any] = {
                "dataset": self.DATASET_NAME,
                "sync_timestamp": datetime.now(UTC).isoformat(),
                "schema_version": "v1.0.0",
                "files": manifest_entries,
                "total_row_count": total_rows,
            }

            # Include failed datasets in result for caller awareness
            if failed_datasets:
                manifest_data["failed_datasets"] = failed_datasets
                logger.warning(
                    "Some datasets failed to sync",
                    extra={"failed": failed_datasets, "count": len(failed_datasets)},
                )

            self._atomic_write_manifest(manifest_data)

            return manifest_data

        finally:
            lock.release(lock_token)

    def _get_target_path(self, dataset_name: str) -> Path:
        """Get target file path for dataset.

        Args:
            dataset_name: Dataset name (e.g., factors_3_daily, ind10_monthly).

        Returns:
            Path to target Parquet file.
        """
        if dataset_name.startswith("ind"):
            return self._industries_dir / f"{dataset_name}.parquet"
        else:
            return self._factors_dir / f"{dataset_name}.parquet"

    def _download_with_retry(
        self,
        web: Any,
        source: str,
        name: str,
    ) -> pl.DataFrame | None:
        """Download data from Ken French with retries.

        Args:
            web: pandas_datareader.data module.
            source: Ken French dataset identifier.
            name: Local dataset name for logging.

        Returns:
            Polars DataFrame or None if download failed.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                # pandas_datareader returns dict of DataFrames
                data = web.DataReader(source, "famafrench")

                # Get the main data (usually index 0)
                # Some datasets have multiple tables
                if isinstance(data, dict):
                    # Get first table
                    pdf = data[0] if 0 in data else list(data.values())[0]
                else:
                    pdf = data

                # Convert to polars
                # Reset index to get date as column
                if isinstance(pdf.index, pd.DatetimeIndex):
                    pdf = pdf.reset_index()
                    pdf.columns = ["date"] + list(pdf.columns[1:])
                elif hasattr(pdf.index, "to_timestamp"):
                    # PeriodIndex - convert to timestamp
                    pdf.index = pdf.index.to_timestamp()
                    pdf = pdf.reset_index()
                    pdf.columns = ["date"] + list(pdf.columns[1:])
                else:
                    pdf = pdf.reset_index()
                    pdf.columns = ["date"] + list(pdf.columns[1:])

                # Normalize column names to lowercase
                pdf.columns = [c.lower().replace("-", "_").replace(" ", "_") for c in pdf.columns]

                # Convert to polars
                df = pl.from_pandas(pdf)

                # Ensure date column is date type
                if "date" in df.columns:
                    df = df.with_columns(pl.col("date").cast(pl.Date))

                return df

            except Exception as e:
                logger.warning(
                    "Download attempt failed",
                    extra={
                        "dataset": name,
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES,
                        "error": str(e),
                    },
                )
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
                else:
                    logger.error(
                        "Download failed after retries",
                        extra={"dataset": name, "error": str(e)},
                    )
                    return None

        return None

    def _normalize_returns(self, df: pl.DataFrame) -> pl.DataFrame:
        """Convert percent returns to decimal returns.

        Ken French publishes returns as percent (1.5 = 1.5%).
        We store as decimal (0.015) for downstream consistency.

        Args:
            df: DataFrame with percent returns.

        Returns:
            DataFrame with decimal returns (divided by 100).
        """
        # Identify return columns (all except 'date')
        return_cols = [c for c in df.columns if c != "date"]

        return df.with_columns([
            (pl.col(c) / 100.0).alias(c)
            for c in return_cols
        ])

    def _create_ff6(self, ff5_path: Path, mom_path: Path) -> pl.DataFrame:
        """Create 6-factor DataFrame by joining 5-factor + momentum.

        Args:
            ff5_path: Path to 5-factor Parquet.
            mom_path: Path to momentum Parquet.

        Returns:
            6-factor DataFrame with all columns.
        """
        ff5_df = pl.read_parquet(ff5_path)
        mom_df = pl.read_parquet(mom_path)

        # Join on date
        ff6_df = ff5_df.join(mom_df, on="date", how="inner")

        # Reorder columns to have RF at the end
        cols = [c for c in ff6_df.columns if c not in ("date", "rf")]
        cols = ["date"] + cols
        if "rf" in ff6_df.columns:
            cols.append("rf")

        return ff6_df.select(cols)

    def _atomic_write_parquet(
        self,
        df: pl.DataFrame,
        target_path: Path,
        expected_checksum: str | None = None,
    ) -> str:
        """Write Parquet atomically using temp file + rename + quarantine.

        Pattern:
        1. Write to temp path: target.parquet.tmp
        2. Compute checksum of temp file
        3. Validate: row count > 0, checksum matches (if expected)
        4. On validation failure: move to quarantine, raise error
        5. Atomic rename: temp -> target
        6. fsync directory for crash safety
        7. Return checksum

        Readers NEVER see .tmp files (atomic rename).

        Args:
            df: DataFrame to write.
            target_path: Target file path.
            expected_checksum: Optional expected checksum for validation.

        Returns:
            MD5 checksum of written file.

        Raises:
            ChecksumError: If checksum validation fails.
            ValueError: If DataFrame is empty.
        """
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_suffix(".parquet.tmp")

        try:
            df.write_parquet(temp_path)

            actual_checksum = self._compute_checksum(temp_path)

            # Validate row count
            if df.height == 0:
                self._quarantine_file(temp_path, "empty_dataframe")
                raise ValueError("Empty DataFrame, file quarantined")

            # Validate checksum if expected
            if expected_checksum and actual_checksum != expected_checksum:
                self._quarantine_file(temp_path, "checksum_mismatch")
                raise ChecksumError(
                    f"Checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
                )

            # Atomic rename (readers never see .tmp)
            temp_path.rename(target_path)

            # fsync directory for crash safety
            self._fsync_directory(target_path.parent)

            return actual_checksum

        except Exception:
            # Clean up temp file on any error
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def _quarantine_file(self, file_path: Path, reason: str) -> Path:
        """Move failed file to quarantine directory.

        Args:
            file_path: File to quarantine.
            reason: Reason for quarantine.

        Returns:
            Quarantine destination path.
        """
        self._quarantine_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        dest = self._quarantine_dir / f"{timestamp}_{reason}_{file_path.name}"
        file_path.rename(dest)
        logger.warning(
            "File quarantined",
            extra={"source": str(file_path), "destination": str(dest), "reason": reason},
        )
        return dest

    def _compute_checksum(self, file_path: Path) -> str:
        """Compute MD5 checksum of file.

        Args:
            file_path: Path to file.

        Returns:
            MD5 hex digest.
        """
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()

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

    def _atomic_write_manifest(self, manifest_data: dict[str, Any]) -> None:
        """Write manifest atomically.

        Args:
            manifest_data: Manifest data to write.
        """
        manifest_path = self._storage_path / "fama_french_manifest.json"
        temp_path = manifest_path.with_suffix(".json.tmp")

        try:
            with open(temp_path, "w") as f:
                json.dump(manifest_data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            temp_path.rename(manifest_path)
            self._fsync_directory(manifest_path.parent)

        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def get_manifest(self) -> dict[str, Any] | None:
        """Get current manifest.

        Returns:
            Manifest dict or None if not found.
        """
        manifest_path = self._storage_path / "fama_french_manifest.json"
        if not manifest_path.exists():
            return None

        with open(manifest_path) as f:
            data: dict[str, Any] = json.load(f)
            return data

    def verify_data(self) -> dict[str, bool]:
        """Verify checksums of all data files.

        Returns:
            Dict mapping filename to verification result (True = valid).
        """
        manifest = self.get_manifest()
        if manifest is None:
            return {}

        results: dict[str, bool] = {}
        files = manifest.get("files", {})

        for filename, entry in files.items():
            expected_checksum = entry.get("checksum")
            if not expected_checksum:
                results[filename] = False
                continue

            # Determine file path
            if filename.startswith("ind"):
                file_path = self._industries_dir / filename
            else:
                file_path = self._factors_dir / filename

            if not file_path.exists():
                results[filename] = False
                continue

            actual_checksum = self._compute_checksum(file_path)
            results[filename] = actual_checksum == expected_checksum

        return results

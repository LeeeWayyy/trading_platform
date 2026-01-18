"""
DiskExpressionCache for computed factor caching with PIT safety.

This module provides:
- DiskExpressionCache: Disk-based cache for computed factor expressions
- 5-component key format for PIT safety
- TTL-based expiration
- Atomic writes to prevent corruption

Key Format (per spec ~2390):
    {factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}

Where dataset_version_id is derived deterministically from version_ids dict:
    {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'} -> 'compustat-v1.0.1_crsp-v1.2.3'

PIT Safety:
- Cache key includes snapshot_id (prevents stale data)
- Cache key includes config_hash (prevents stale computation)
- Cache miss on ANY component mismatch
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)


class CacheError(Exception):
    """Base exception for cache errors."""

    pass


class CacheCorruptionError(CacheError):
    """Raised when cached data is corrupted."""

    pass


class DiskExpressionCache:
    """Disk-based cache for computed factor expressions.

    Features:
    - TTL-based expiration (7 days default, configurable)
    - Atomic writes using temp file + rename
    - Snapshot-aware cache invalidation
    - Config-hash aware invalidation
    - Thread-safe concurrent access

    Key Format:
        {factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}

    All 5 components are required for PIT safety.

    Example:
        cache = DiskExpressionCache(Path("data/cache/factors"))

        # Get or compute factor
        df, was_cached = cache.get_or_compute(
            factor_name="momentum_12m",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_abc123",
            version_ids={"crsp": "v1.2.3", "compustat": "v1.0.1"},
            config_hash="def456",
            compute_fn=lambda: expensive_factor_computation(),
        )
    """

    DEFAULT_TTL_DAYS = 7
    FILE_EXTENSION = ".parquet"

    def __init__(self, cache_dir: Path, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        """Initialize cache.

        Args:
            cache_dir: Directory for cached files.
            ttl_days: Time-to-live in days (default 7).
        """
        self.cache_dir = Path(cache_dir)
        self.ttl_days = ttl_days
        self._lock = threading.RLock()
        self._index_db_path = self.cache_dir / ".cache_index.sqlite"

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_index()

    # =========================================================================
    # Key Building
    # =========================================================================

    def _build_version_id_string(self, version_ids: dict[str, str]) -> str:
        """Build deterministic dataset_version_id from dict.

        Sorts keys alphabetically to ensure deterministic output.

        Args:
            version_ids: Dict of dataset name to version.
                Example: {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}

        Returns:
            Deterministic string: 'compustat-v1.0.1_crsp-v1.2.3'
        """
        return "_".join(f"{k}-{v}" for k, v in sorted(version_ids.items()))

    def _build_key(
        self,
        factor_name: str,
        as_of_date: date,
        version_ids: dict[str, str],
        snapshot_id: str,
        config_hash: str,
    ) -> str:
        """Build cache key per spec format.

        Key format: {factor_name}:{as_of_date}:{dataset_version_id}:{snapshot_id}:{config_hash}

        Args:
            factor_name: Name of the factor.
            as_of_date: As-of date for the computation.
            version_ids: Dataset version IDs.
            snapshot_id: Snapshot identifier.
            config_hash: Hash of configuration.

        Returns:
            Cache key string.
        """
        version_id_str = self._build_version_id_string(version_ids)
        return f"{factor_name}:{as_of_date}:{version_id_str}:{snapshot_id}:{config_hash}"

    def _key_to_filename(self, key: str) -> str:
        """Convert key to safe filename.

        Uses hash to avoid filesystem issues with special characters.

        Args:
            key: Cache key.

        Returns:
            Safe filename.
        """
        # Hash the key for a safe filename
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:32]
        # Keep a readable prefix for debugging
        parts = key.split(":")
        prefix = f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else key[:20]
        # Sanitize prefix
        prefix = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix)
        return f"{prefix}_{key_hash}{self.FILE_EXTENSION}"

    def _get_cache_path(self, key: str) -> Path:
        """Get full path for cache file.

        Args:
            key: Cache key.

        Returns:
            Full path to cache file.
        """
        return self.cache_dir / self._key_to_filename(key)

    # =========================================================================
    # Core Operations
    # =========================================================================

    def get_or_compute(
        self,
        factor_name: str,
        as_of_date: date,
        snapshot_id: str,
        version_ids: dict[str, str],
        config_hash: str,
        compute_fn: Callable[[], pl.DataFrame],
    ) -> tuple[pl.DataFrame, bool]:
        """Get cached value or compute and cache.

        Args:
            factor_name: Name of the factor.
            as_of_date: As-of date for computation.
            snapshot_id: Snapshot ID for PIT safety.
            version_ids: Dataset version IDs.
            config_hash: Configuration hash.
            compute_fn: Function to compute if cache miss.

        Returns:
            Tuple of (DataFrame, was_cached).
        """
        key = self._build_key(factor_name, as_of_date, version_ids, snapshot_id, config_hash)
        cache_path = self._get_cache_path(key)

        # Try to get from cache
        with self._lock:
            if cache_path.exists():
                # Check TTL
                mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=UTC)
                age = datetime.now(UTC) - mtime
                if age < timedelta(days=self.ttl_days):
                    try:
                        df = pl.read_parquet(cache_path)
                        logger.debug(
                            "Cache hit",
                            extra={
                                "factor": factor_name,
                                "as_of_date": str(as_of_date),
                                "age_hours": age.total_seconds() / 3600,
                            },
                        )
                        return df, True
                    except OSError as e:
                        logger.warning(
                            "Factor cache read failed - file I/O error, will recalculate",
                            extra={
                                "path": str(cache_path),
                                "factor": factor_name,
                                "as_of_date": str(as_of_date),
                                "error": str(e),
                            },
                            exc_info=True,
                        )
                    except (pl.exceptions.ComputeError, ValueError) as e:
                        logger.error(
                            "Factor cache corruption - invalid Parquet format, clearing corrupted entry",
                            extra={
                                "path": str(cache_path),
                                "factor": factor_name,
                                "as_of_date": str(as_of_date),
                                "error": str(e),
                            },
                            exc_info=True,
                        )
                        # Clear corrupted cache entry
                        try:
                            cache_path.unlink(missing_ok=True)
                        except OSError as cleanup_err:
                            logger.debug("Failed to remove corrupted cache file: %s", cleanup_err)

        # Cache miss - compute
        logger.debug(
            "Cache miss, computing",
            extra={"factor": factor_name, "as_of_date": str(as_of_date)},
        )
        df = compute_fn()

        # Store in cache with rollback on index failure to prevent orphaned files
        self._atomic_write_parquet(cache_path, df)

        try:
            # Update metadata index for invalidation support
            self._update_index(
                filename=cache_path.name,
                snapshot_id=snapshot_id,
                version_ids=version_ids,
                config_hash=config_hash,
            )
        except (sqlite3.Error, OSError) as idx_err:
            # Rollback: delete orphaned parquet file if index update fails
            logger.error(
                "Index update failed, rolling back cache file",
                extra={
                    "path": str(cache_path),
                    "factor": factor_name,
                    "as_of_date": str(as_of_date),
                    "error": str(idx_err),
                },
                exc_info=True,
            )
            try:
                cache_path.unlink(missing_ok=True)
            except OSError as del_err:
                logger.error(
                    "Failed to rollback cache file after index failure",
                    extra={"path": str(cache_path), "error": str(del_err)},
                )
            raise  # Re-raise original error

        logger.info(
            "Cached computed factor",
            extra={
                "factor": factor_name,
                "as_of_date": str(as_of_date),
                "rows": len(df),
                "path": str(cache_path),
            },
        )

        return df, False

    def get(
        self,
        factor_name: str,
        as_of_date: date,
        snapshot_id: str,
        version_ids: dict[str, str],
        config_hash: str,
    ) -> pl.DataFrame | None:
        """Get cached value if exists and not expired.

        Args:
            factor_name: Name of the factor.
            as_of_date: As-of date.
            snapshot_id: Snapshot ID.
            version_ids: Dataset version IDs.
            config_hash: Configuration hash.

        Returns:
            DataFrame or None if not cached/expired.
        """
        key = self._build_key(factor_name, as_of_date, version_ids, snapshot_id, config_hash)
        cache_path = self._get_cache_path(key)

        with self._lock:
            if not cache_path.exists():
                return None

            # Check TTL
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=UTC)
            age = datetime.now(UTC) - mtime
            if age >= timedelta(days=self.ttl_days):
                return None

            try:
                return pl.read_parquet(cache_path)
            except OSError as e:
                logger.warning(
                    "Factor cache read failed - file I/O error, returning None",
                    extra={
                        "path": str(cache_path),
                        "factor": factor_name,
                        "as_of_date": str(as_of_date),
                        "error": str(e),
                    },
                    exc_info=True,
                )
                return None
            except (pl.exceptions.ComputeError, ValueError) as e:
                logger.error(
                    "Factor cache corruption - invalid Parquet format, clearing corrupted entry",
                    extra={
                        "path": str(cache_path),
                        "factor": factor_name,
                        "as_of_date": str(as_of_date),
                        "error": str(e),
                    },
                    exc_info=True,
                )
                # Clear corrupted cache entry
                try:
                    cache_path.unlink(missing_ok=True)
                except OSError as cleanup_err:
                    logger.debug("Failed to cleanup corrupted cache: %s", cleanup_err)
                return None

    def set(
        self,
        factor_name: str,
        as_of_date: date,
        snapshot_id: str,
        version_ids: dict[str, str],
        config_hash: str,
        data: pl.DataFrame,
    ) -> None:
        """Store value in cache.

        Args:
            factor_name: Name of the factor.
            as_of_date: As-of date.
            snapshot_id: Snapshot ID.
            version_ids: Dataset version IDs.
            config_hash: Configuration hash.
            data: DataFrame to cache.
        """
        key = self._build_key(factor_name, as_of_date, version_ids, snapshot_id, config_hash)
        cache_path = self._get_cache_path(key)
        self._atomic_write_parquet(cache_path, data)

        # Update metadata index for invalidation support
        self._update_index(
            filename=cache_path.name,
            snapshot_id=snapshot_id,
            version_ids=version_ids,
            config_hash=config_hash,
        )

    # =========================================================================
    # Invalidation
    # =========================================================================

    def invalidate_by_snapshot(self, snapshot_id: str) -> int:
        """Invalidate all entries for a snapshot (thread-safe).

        Maintains a metadata index to track snapshot_id per cache file.
        Since snapshot_id is embedded in the cache key, we scan all entries
        and match against the index.

        Args:
            snapshot_id: Snapshot ID to invalidate.

        Returns:
            Number of entries invalidated.
        """
        count = 0
        with self._lock:
            with self._index_connection() as conn:
                rows = conn.execute(
                    "SELECT filename FROM cache_index WHERE snapshot_id = ?", (snapshot_id,)
                ).fetchall()
                filenames = [row[0] for row in rows]

            for filename in filenames:
                path = self.cache_dir / filename
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete {path}: {e}")

            self._delete_index_entries(filenames)

        logger.info(f"Invalidated {count} entries for snapshot {snapshot_id}")
        return count

    def invalidate_by_dataset_update(self, dataset: str, new_version: str) -> int:
        """Invalidate entries affected by dataset version change (thread-safe).

        Scans the metadata index and removes entries that used a different
        version of the specified dataset.

        Args:
            dataset: Dataset name that was updated.
            new_version: New version of the dataset.

        Returns:
            Number of entries invalidated.
        """
        count = 0

        with self._lock:
            with self._index_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT ci.filename, cv.version FROM cache_index ci
                    JOIN cache_versions cv ON ci.filename = cv.filename
                    WHERE cv.dataset = ? AND cv.version != ?
                    """,
                    (dataset, new_version),
                ).fetchall()
                filenames = [row[0] for row in rows]

            for filename in filenames:
                path = self.cache_dir / filename
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete {path}: {e}")

            self._delete_index_entries(filenames)

        logger.info(f"Invalidated {count} entries for dataset {dataset}={new_version}")
        return count

    def invalidate_by_config_change(self, factor_name: str) -> int:
        """Invalidate all entries for a factor (on config change).

        Args:
            factor_name: Factor name to invalidate.

        Returns:
            Number of entries invalidated.
        """
        count = 0
        with self._lock:
            # Match files starting with factor name
            pattern = f"{factor_name}_*{self.FILE_EXTENSION}"
            for path in self.cache_dir.glob(pattern):
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete {path}: {e}")
        logger.info(f"Invalidated {count} entries for factor {factor_name}")
        return count

    def invalidate_all(self, factor_name: str | None = None) -> int:
        """Invalidate all cache entries.

        Args:
            factor_name: Optional factor to limit invalidation.

        Returns:
            Number of entries invalidated.
        """
        count = 0
        with self._lock:
            query = "SELECT filename FROM cache_index"
            params: tuple[str, ...] = ()

            if factor_name:
                query += " WHERE filename LIKE ?"
                params = (f"{factor_name}_%{self.FILE_EXTENSION}",)

            with self._index_connection() as conn:
                filenames = [row[0] for row in conn.execute(query, params).fetchall()]

            for filename in filenames:
                path = self.cache_dir / filename
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete {path}: {e}")

            self._delete_index_entries(filenames)
        logger.info(f"Invalidated {count} cache entries")
        return count

    def cleanup_expired(self, ttl_days: int | None = None) -> int:
        """Remove expired cache entries and orphaned files.

        This method handles both:
        1. Expired entries (based on TTL)
        2. Orphaned files (files on disk not in index, e.g., from failed index updates)

        Args:
            ttl_days: Override TTL for cleanup (default: self.ttl_days).

        Returns:
            Number of entries removed (includes orphans).
        """
        ttl = ttl_days if ttl_days is not None else self.ttl_days
        cutoff = datetime.now(UTC) - timedelta(days=ttl)
        count = 0

        with self._lock:
            cutoff_ts = int(cutoff.timestamp())
            with self._index_connection() as conn:
                # Use <= to ensure entries at the cutoff boundary (e.g., TTL=0)
                # are treated as expired rather than lingering.
                filenames = [
                    row[0]
                    for row in conn.execute(
                        "SELECT filename FROM cache_index WHERE created_at <= ?", (cutoff_ts,)
                    ).fetchall()
                ]

                # Get all indexed filenames for orphan detection
                indexed_files = {
                    row[0] for row in conn.execute("SELECT filename FROM cache_index").fetchall()
                }

            # Remove expired entries from disk
            for filename in filenames:
                path = self.cache_dir / filename
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to check/delete {path}: {e}")

            self._delete_index_entries(filenames)

            # Clean up orphaned files (files on disk but not in index)
            orphan_count = 0
            for path in self.cache_dir.glob(f"*{self.FILE_EXTENSION}"):
                if path.name not in indexed_files:
                    try:
                        path.unlink()
                        orphan_count += 1
                        logger.debug(f"Removed orphaned cache file: {path.name}")
                    except OSError as e:
                        logger.warning(f"Failed to delete orphan {path}: {e}")

            if orphan_count > 0:
                logger.info(f"Cleaned up {orphan_count} orphaned cache files")

        logger.info(f"Cleaned up {count} expired cache entries (TTL: {ttl} days)")
        return count + orphan_count

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, int | str | None]:
        """Get cache statistics.

        Returns:
            Dict with entry_count, total_size_bytes, oldest_entry, newest_entry.
        """
        with self._lock:
            files = list(self.cache_dir.glob(f"*{self.FILE_EXTENSION}"))
            if not files:
                return {
                    "entry_count": 0,
                    "total_size_bytes": 0,
                    "oldest_entry": None,
                    "newest_entry": None,
                }

            total_size = 0
            oldest_mtime = datetime.now(UTC)
            newest_mtime = datetime.min.replace(tzinfo=UTC)

            for path in files:
                stat = path.stat()
                total_size += stat.st_size
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                if mtime < oldest_mtime:
                    oldest_mtime = mtime
                if mtime > newest_mtime:
                    newest_mtime = mtime

            return {
                "entry_count": len(files),
                "total_size_bytes": total_size,
                "oldest_entry": oldest_mtime.isoformat(),
                "newest_entry": newest_mtime.isoformat(),
            }

    # =========================================================================
    # Atomic Writes
    # =========================================================================

    def _atomic_write_parquet(self, path: Path, df: pl.DataFrame) -> None:
        """Atomically write DataFrame to parquet file.

        Uses temp file + rename for atomicity.

        Args:
            path: Target path.
            df: DataFrame to write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent, prefix=".tmp_", suffix=self.FILE_EXTENSION
        )
        try:
            os.close(fd)
            df.write_parquet(temp_path)
            # Atomic rename
            shutil.move(temp_path, path)
        except OSError as e:
            logger.error(
                "Factor cache atomic write failed - file I/O error",
                extra={
                    "path": str(path),
                    "temp_path": str(temp_path),
                    "error": str(e),
                },
                exc_info=True,
            )
            # Clean up temp file on failure
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError as cleanup_err:
                logger.debug("Failed to cleanup temp file: %s", cleanup_err)
            raise
        except (pl.exceptions.ComputeError, ValueError) as e:
            logger.error(
                "Factor cache atomic write failed - Parquet write error",
                extra={
                    "path": str(path),
                    "temp_path": str(temp_path),
                    "error": str(e),
                },
                exc_info=True,
            )
            # Clean up temp file on failure
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError as cleanup_err:
                logger.debug("Failed to cleanup temp file: %s", cleanup_err)
            raise

    # =========================================================================
    # Metadata Index
    # =========================================================================

    # SQLite-backed metadata index

    def _ensure_index(self) -> None:
        """Initialize SQLite index if missing and migrate legacy JSON index."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._index_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_index (
                    filename TEXT PRIMARY KEY,
                    snapshot_id TEXT,
                    config_hash TEXT,
                    created_at INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_versions (
                    filename TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    version TEXT NOT NULL,
                    PRIMARY KEY (filename, dataset),
                    FOREIGN KEY (filename) REFERENCES cache_index(filename) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_versions_dataset ON cache_versions(dataset);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cache_index_snapshot ON cache_index(snapshot_id);"
            )

        self._migrate_legacy_index()

    def _index_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._index_db_path)
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _write_index_entry(
        self, filename: str, snapshot_id: str, version_ids: dict[str, str], config_hash: str
    ) -> None:
        """Upsert metadata entry inside locked section."""

        now_ts = int(datetime.now(UTC).timestamp())
        with self._index_connection() as conn:
            conn.execute(
                "REPLACE INTO cache_index(filename, snapshot_id, config_hash, created_at) VALUES (?, ?, ?, ?)",
                (filename, snapshot_id, config_hash, now_ts),
            )
            conn.execute("DELETE FROM cache_versions WHERE filename = ?", (filename,))
            if version_ids:
                conn.executemany(
                    "INSERT INTO cache_versions(filename, dataset, version) VALUES (?, ?, ?)",
                    [(filename, ds, ver) for ds, ver in version_ids.items()],
                )

    def _delete_index_entries(self, filenames: list[str]) -> None:
        if not filenames:
            return
        with self._index_connection() as conn:
            conn.executemany(
                "DELETE FROM cache_index WHERE filename = ?", [(f,) for f in filenames]
            )

    def _migrate_legacy_index(self) -> None:
        """One-time migration from JSON index to SQLite for backward compatibility."""

        legacy_path = self.cache_dir / ".cache_index.json"
        if not legacy_path.exists():
            return

        try:
            import json

            with open(legacy_path) as f:
                data = json.load(f)
        except OSError as e:
            logger.warning(
                "Failed to read legacy cache index JSON - file I/O error, skipping migration",
                extra={"path": str(legacy_path), "error": str(e)},
            )
            return
        except json.JSONDecodeError as e:
            logger.warning(
                "Failed to parse legacy cache index JSON - corrupted format, skipping migration",
                extra={"path": str(legacy_path), "error": str(e), "position": e.pos},
                exc_info=True,
            )
            return
        except ValueError as e:
            logger.warning(
                "Failed to load legacy cache index - invalid data format, skipping migration",
                extra={"path": str(legacy_path), "error": str(e)},
                exc_info=True,
            )
            return

        # Validate data is a dict (e.g., JSON "null" would result in None)
        if not isinstance(data, dict):
            logger.warning(
                "Legacy cache index has invalid structure (expected dict), skipping migration",
                extra={"path": str(legacy_path), "data_type": type(data).__name__},
            )
            return

        with self._index_connection() as conn:
            now_ts = int(datetime.now(UTC).timestamp())
            for filename, meta in data.items():
                snapshot_id = meta.get("snapshot_id", "unknown")
                config_hash = meta.get("config_hash", "")
                conn.execute(
                    "REPLACE INTO cache_index(filename, snapshot_id, config_hash, created_at) VALUES (?, ?, ?, ?)",
                    (filename, snapshot_id, config_hash, now_ts),
                )

                version_ids = meta.get("version_ids", {}) or {}
                if isinstance(version_ids, dict) and version_ids:
                    conn.executemany(
                        "INSERT OR REPLACE INTO cache_versions(filename, dataset, version) VALUES (?, ?, ?)",
                        [(filename, ds, ver) for ds, ver in version_ids.items()],
                    )

        # Cleanup legacy file after successful migration
        try:
            legacy_path.unlink()
        except OSError as e:
            logger.debug("Failed to cleanup legacy cache index: %s", e)

    def _update_index(
        self,
        filename: str,
        snapshot_id: str,
        version_ids: dict[str, str],
        config_hash: str,
    ) -> None:
        """Update metadata index with cache entry info (thread-safe)."""

        with self._lock:
            self._write_index_entry(filename, snapshot_id, version_ids, config_hash)

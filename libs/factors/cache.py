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

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
        key = self._build_key(
            factor_name, as_of_date, version_ids, snapshot_id, config_hash
        )
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
                    except Exception as e:
                        logger.warning(
                            f"Failed to read cached file, recomputing: {e}",
                            extra={"path": str(cache_path)},
                        )

        # Cache miss - compute
        logger.debug(
            "Cache miss, computing",
            extra={"factor": factor_name, "as_of_date": str(as_of_date)},
        )
        df = compute_fn()

        # Store in cache
        self._atomic_write_parquet(cache_path, df)

        # Update metadata index for invalidation support
        self._update_index(
            filename=cache_path.name,
            snapshot_id=snapshot_id,
            version_ids=version_ids,
            config_hash=config_hash,
        )

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
        key = self._build_key(
            factor_name, as_of_date, version_ids, snapshot_id, config_hash
        )
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
            except Exception:
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
        key = self._build_key(
            factor_name, as_of_date, version_ids, snapshot_id, config_hash
        )
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
            # Load index inside lock for atomic read-modify-write
            index = self._load_metadata_index()

            for path in self.cache_dir.glob(f"*{self.FILE_EXTENSION}"):
                filename = path.name
                if filename in index:
                    entry = index[filename]
                    if entry.get("snapshot_id") == snapshot_id:
                        try:
                            path.unlink()
                            del index[filename]
                            count += 1
                        except OSError as e:
                            logger.warning(f"Failed to delete {path}: {e}")

            self._save_metadata_index(index)

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
            # Load index inside lock for atomic read-modify-write
            index = self._load_metadata_index()

            for path in self.cache_dir.glob(f"*{self.FILE_EXTENSION}"):
                filename = path.name
                if filename in index:
                    entry = index[filename]
                    version_ids_raw = entry.get("version_ids", {})
                    # version_ids is always a dict if present
                    version_ids = version_ids_raw if isinstance(version_ids_raw, dict) else {}
                    if dataset in version_ids and version_ids.get(dataset) != new_version:
                        try:
                            path.unlink()
                            del index[filename]
                            count += 1
                        except OSError as e:
                            logger.warning(f"Failed to delete {path}: {e}")

            self._save_metadata_index(index)

        logger.info(
            f"Invalidated {count} entries for dataset {dataset}={new_version}"
        )
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
            if factor_name:
                pattern = f"{factor_name}_*{self.FILE_EXTENSION}"
            else:
                pattern = f"*{self.FILE_EXTENSION}"

            for path in self.cache_dir.glob(pattern):
                try:
                    path.unlink()
                    count += 1
                except OSError as e:
                    logger.warning(f"Failed to delete {path}: {e}")
        logger.info(f"Invalidated {count} cache entries")
        return count

    def cleanup_expired(self, ttl_days: int | None = None) -> int:
        """Remove expired cache entries.

        Args:
            ttl_days: Override TTL for cleanup (default: self.ttl_days).

        Returns:
            Number of entries removed.
        """
        ttl = ttl_days if ttl_days is not None else self.ttl_days
        cutoff = datetime.now(UTC) - timedelta(days=ttl)
        count = 0

        with self._lock:
            for path in self.cache_dir.glob(f"*{self.FILE_EXTENSION}"):
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                    if mtime < cutoff:
                        path.unlink()
                        count += 1
                except OSError as e:
                    logger.warning(f"Failed to check/delete {path}: {e}")

        logger.info(f"Cleaned up {count} expired cache entries (TTL: {ttl} days)")
        return count

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
        except Exception:
            # Clean up temp file on failure
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
            raise

    # =========================================================================
    # Metadata Index
    # =========================================================================

    def _get_index_path(self) -> Path:
        """Get path to metadata index file."""
        return self.cache_dir / ".cache_index.json"

    def _load_metadata_index(self) -> dict[str, dict[str, str | dict[str, str]]]:
        """Load metadata index from disk.

        Returns:
            Dict mapping filename -> {snapshot_id, version_ids, config_hash}.
        """
        import json

        index_path = self._get_index_path()
        if not index_path.exists():
            return {}
        try:
            with open(index_path) as f:
                data: dict[str, dict[str, str | dict[str, str]]] = json.load(f)
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_metadata_index(self, index: dict[str, dict[str, str | dict[str, str]]]) -> None:
        """Save metadata index to disk atomically.

        Args:
            index: Dict mapping filename -> metadata.

        Note: Caller must hold self._lock.
        """
        import json
        import tempfile
        import shutil

        index_path = self._get_index_path()
        # Atomic write: temp file + rename
        fd, temp_path = tempfile.mkstemp(dir=self.cache_dir, prefix=".index_tmp_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(index, f)
            shutil.move(temp_path, index_path)
        except Exception:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _update_index(
        self,
        filename: str,
        snapshot_id: str,
        version_ids: dict[str, str],
        config_hash: str,
    ) -> None:
        """Update metadata index with cache entry info (thread-safe).

        Args:
            filename: Cache filename.
            snapshot_id: Snapshot ID.
            version_ids: Dataset version IDs.
            config_hash: Config hash.
        """
        with self._lock:
            index = self._load_metadata_index()
            index[filename] = {
                "snapshot_id": snapshot_id,
                "version_ids": version_ids,
                "config_hash": config_hash,
            }
            self._save_metadata_index(index)

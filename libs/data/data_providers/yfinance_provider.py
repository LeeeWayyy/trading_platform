"""yfinance Local Data Provider.

Free market data provider for development and testing only.
Downloads data from Yahoo Finance using the yfinance library.

WARNING: yfinance lacks survivorship handling and corporate actions.
NOT suitable for production backtests - use CRSP instead.

This module provides:
- YFinanceProvider: Rate-limited data fetcher with local caching
- YFinanceError: Base exception for yfinance operations
- ProductionGateError: Raised when yfinance blocked in production

Features:
- Rate-limited downloads with retries and jitter
- Local Parquet caching with atomic writes
- Production gating (blocks yfinance when CRSP available in prod)
- Drift detection against baseline data
- Quarantine for failed/corrupted files
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from libs.data.data_providers.locking import AtomicFileLock

logger = logging.getLogger(__name__)

# Valid symbol pattern: alphanumeric, dots, hyphens only (e.g., BRK.B, BRK-B)
VALID_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")


class YFinanceError(Exception):
    """Base exception for yfinance operations."""

    pass


class ProductionGateError(RuntimeError):
    """Raised when yfinance is blocked in production environment."""

    pass


class DriftDetectedError(Exception):
    """Raised when price drift exceeds tolerance."""

    def __init__(self, symbol: str, max_drift: float, tolerance: float) -> None:
        self.symbol = symbol
        self.max_drift = max_drift
        self.tolerance = tolerance
        super().__init__(f"Price drift detected for {symbol}: {max_drift:.4f} > {tolerance:.4f}")


# Schema definition for yfinance data
YFINANCE_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume", "adj_close")

# Baseline file suffix (e.g., spy_60d.parquet)
BASELINE_FILE_SUFFIX = "_60d.parquet"

# Baseline manifest filename
BASELINE_MANIFEST_FILE = "baseline_manifest.json"

YFINANCE_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "adj_close": pl.Float64,
}


class YFinanceProvider:
    """Free data provider for development/testing only.

    WARNING: yfinance lacks survivorship handling and corporate actions.
    NOT suitable for production backtests - use CRSP instead.

    Features:
    - Rate-limited downloads with retries
    - Local Parquet caching with atomic writes
    - Production gating (blocks yfinance when CRSP available in prod)
    - Drift detection against baseline data
    - Quarantine for failed/corrupted files

    Storage Layout:
        data/yfinance/
        ├── daily/
        │   ├── SPY.parquet
        │   ├── AAPL.parquet
        │   └── ...
        ├── quarantine/
        ├── locks/
        └── yfinance_manifest.json

    Example:
        provider = YFinanceProvider(
            storage_path=Path("data/yfinance"),
            environment="development",
        )

        # Step 1: Prime cache with atomic writes (recommended)
        result = provider.fetch_and_cache(
            symbols=["SPY", "AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        # Step 2: Read from cache (fast, no network)
        df = provider.get_daily_prices(
            symbols=["SPY", "AAPL"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
    """

    DATASET_NAME = "yfinance"

    # Rate limiting
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2.0
    REQUEST_DELAY_SECONDS = 0.5  # Between symbols
    JITTER_MAX_SECONDS = 0.5  # Random jitter for rate limiting

    # Drift tolerance
    DEFAULT_DRIFT_TOLERANCE = 0.01  # 1%

    # Disk space check (10 MB minimum)
    MIN_DISK_SPACE_BYTES = 10 * 1024 * 1024

    def __init__(
        self,
        storage_path: Path,
        baseline_path: Path | None = None,
        lock_dir: Path | None = None,
        environment: str = "development",
        use_yfinance_in_prod: bool = False,
        crsp_available: bool = False,
    ) -> None:
        """Initialize provider with production gating.

        Args:
            storage_path: Base path for cache storage (e.g., data/yfinance).
            baseline_path: Path to baseline data for drift detection (e.g., data/baseline).
            lock_dir: Directory for lock files. Defaults to storage_path/locks.
            environment: Current environment (development/test/staging/production).
            use_yfinance_in_prod: Override to allow yfinance in production (NOT recommended).
            crsp_available: Whether CRSP data is available (blocks yfinance in prod if True).

        Raises:
            ValueError: If storage_path contains path traversal.
        """
        self._storage_path = self._validate_storage_path(storage_path)
        self._baseline_path = baseline_path.resolve() if baseline_path else None
        self._lock_dir = lock_dir or (storage_path / "locks")

        # Normalize environment to lowercase for case-insensitive matching
        self._environment = environment.lower()
        self._use_yfinance_in_prod = use_yfinance_in_prod
        self._crsp_available = crsp_available

        # Create directories
        self._daily_dir = self._storage_path / "daily"
        self._quarantine_dir = self._storage_path / "quarantine"

        self._daily_dir.mkdir(parents=True, exist_ok=True)
        self._quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._lock_dir.mkdir(parents=True, exist_ok=True)

        # Log dev-only warning in non-dev environments (using normalized env)
        if self._environment not in ("development", "test"):
            logger.warning(
                "yfinance provider initialized in non-development environment. "
                "yfinance lacks survivorship handling and corporate actions - "
                "use CRSP for production backtests.",
                extra={"environment": self._environment},
            )

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

        # Check for path traversal attempts using is_relative_to (Python 3.9+)
        # For storage_path validation, we just ensure no ".." in components
        # and that it resolves to an absolute path
        if ".." in storage_path.parts:
            raise ValueError(f"Path traversal detected in storage_path: {storage_path}")

        return resolved

    def _validate_symbol(self, symbol: str) -> str:
        """Validate and normalize symbol to prevent path traversal.

        Args:
            symbol: Ticker symbol to validate.

        Returns:
            Normalized uppercase symbol.

        Raises:
            ValueError: If symbol contains invalid characters or path components.
        """
        # Normalize to uppercase
        normalized = symbol.upper()

        # Check for path separators or traversal attempts
        if "/" in symbol or "\\" in symbol or ".." in symbol:
            raise ValueError(f"Invalid symbol (path traversal attempt): {symbol}")

        # Validate against whitelist pattern
        if not VALID_SYMBOL_PATTERN.match(normalized):
            raise ValueError(
                f"Invalid symbol format: {symbol}. "
                "Symbols must be 1-15 alphanumeric characters, dots, or hyphens."
            )

        return normalized

    def _safe_cache_path(self, symbol: str) -> Path:
        """Get safe cache file path for a symbol.

        Args:
            symbol: Already-validated symbol.

        Returns:
            Absolute path within cache directory.

        Raises:
            ValueError: If resolved path escapes cache directory.
        """
        target_path = (self._daily_dir / f"{symbol}.parquet").resolve()

        # Defense in depth: verify path stays within cache directory
        if not target_path.is_relative_to(self._daily_dir.resolve()):
            raise ValueError(f"Cache path escape attempt for symbol: {symbol}")

        return target_path

    def _check_production_gate(self) -> None:
        """Block yfinance in production when CRSP is available.

        Matrix of conditions:
        - env=production, CRSP=available → BLOCK (always)
        - env=production, CRSP=unavailable, flag=False → BLOCK
        - env=production, CRSP=unavailable, flag=True → WARN + ALLOW
        - env=development/test → ALLOW (always)
        - env=staging → WARN + ALLOW

        Raises:
            ProductionGateError: If yfinance blocked in current environment.
        """
        if self._environment in ("development", "test"):
            return  # Always allowed

        if self._environment == "production":
            # CRSP available always blocks yfinance in production
            if self._crsp_available:
                raise ProductionGateError(
                    "yfinance blocked: CRSP data is available. "
                    "Use CRSP for production backtests."
                )

            # Flag check when CRSP not available
            if not self._use_yfinance_in_prod:
                raise ProductionGateError(
                    "yfinance blocked in production. "
                    "Set use_yfinance_in_prod=True to override (NOT recommended)."
                )

            logger.warning(
                "yfinance used in production without CRSP",
                extra={
                    "use_yfinance_in_prod": True,
                    "crsp_available": False,
                },
            )
        else:
            # staging or other environments
            logger.warning(
                "yfinance used in non-development environment",
                extra={"environment": self._environment},
            )

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        use_cache: bool = True,
    ) -> pl.DataFrame:
        """Fetch daily OHLCV data, reading from cache if available.

        This method reads from existing cache but does NOT write to cache.
        For populating the cache with atomic writes and manifest updates,
        use fetch_and_cache() instead.

        Args:
            symbols: List of ticker symbols.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).
            use_cache: Whether to read from cached data if available (default True).

        Returns:
            DataFrame with columns: date, symbol, open, high, low, close, volume, adj_close

        Raises:
            ProductionGateError: If yfinance blocked in current environment.
            ValueError: If symbols list is empty.

        Note:
            Cache is only used if it FULLY covers the requested date range.
            Partial cache coverage triggers a fresh fetch from yfinance.

        Warning:
            This method may return partial data if some symbols fail to download.
            A warning is logged when this occurs. For workflows requiring ALL
            symbols (e.g., portfolio calculations), use fetch_and_cache() which
            returns a structured result with explicit failed_symbols list.
        """
        # Check production gate
        self._check_production_gate()

        if not symbols:
            raise ValueError("symbols list cannot be empty")

        # Validate and normalize symbols
        symbols = [self._validate_symbol(s) for s in symbols]

        all_data: list[pl.DataFrame] = []
        symbols_from_cache: set[str] = set()
        symbols_to_fetch: list[str] = []

        for symbol in symbols:
            if use_cache:
                cached = self._read_from_cache(symbol, start_date, end_date)
                if cached is not None:
                    all_data.append(cached)
                    symbols_from_cache.add(symbol)
                    continue

            symbols_to_fetch.append(symbol)

        # Fetch missing symbols
        symbols_fetched: set[str] = set()
        if symbols_to_fetch:
            # PERFORMANCE WARNING: Fetching uncached symbols hits yfinance API.
            # This data is NOT cached by this method. For workflows that repeatedly
            # need the same data, call fetch_and_cache() first to populate the cache.
            # Repeated uncached fetches may trigger yfinance rate limits.
            if len(symbols_to_fetch) > 10:
                logger.warning(
                    "Fetching many uncached symbols from yfinance API - consider using "
                    "fetch_and_cache() to populate cache first",
                    extra={
                        "event": "yfinance.uncached_fetch",
                        "uncached_count": len(symbols_to_fetch),
                        "total_symbols": len(symbols),
                        "recommendation": "Call fetch_and_cache() before get_daily_prices()",
                    },
                )
            fetched = self._fetch_symbols(symbols_to_fetch, start_date, end_date)
            for df in fetched:
                if not df.is_empty() and "symbol" in df.columns:
                    symbols_fetched.add(df["symbol"][0])
            all_data.extend(fetched)

        # Check for missing symbols and warn
        all_returned_symbols = symbols_from_cache | symbols_fetched
        failed_symbols = [s for s in symbols if s not in all_returned_symbols]
        if failed_symbols:
            logger.warning(
                "Some symbols failed to fetch - returning partial data",
                extra={
                    "requested": len(symbols),
                    "returned": len(all_returned_symbols),
                    "failed_symbols": failed_symbols,
                },
            )

        if not all_data:
            return self._empty_result()

        # Combine and sort
        result = pl.concat(all_data)
        return result.sort(["date", "symbol"])

    def fetch_and_cache(
        self,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
        run_drift_check: bool = True,
    ) -> dict[str, Any]:
        """Download from yfinance and cache locally.

        Drift detection runs automatically after download (per plan).

        Args:
            symbols: List of ticker symbols to fetch.
            start_date: Start of date range (default: 5 years ago).
            end_date: End of date range (default: today).
            run_drift_check: Whether to run drift detection (default True).

        Returns:
            Dict with sync results:
            - files: Per-file metadata (checksum, row_count, date_range)
            - failed_symbols: List of symbols that failed to download
            - drift_warnings: Dict of symbol -> max_drift for symbols exceeding tolerance

        Raises:
            ProductionGateError: If yfinance blocked in current environment.
        """
        # Check production gate
        self._check_production_gate()

        if not symbols:
            return {"files": {}, "failed_symbols": [], "drift_warnings": {}}

        # Default date range: 5 years
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            # Use timedelta to avoid leap year ValueError (Feb 29 -> Feb 28)
            # 5 years ≈ 1826 days (365.25 * 5)
            start_date = end_date - timedelta(days=1826)

        # Validate and normalize symbols
        symbols = [self._validate_symbol(s) for s in symbols]

        # Acquire lock for cache writes
        lock = AtomicFileLock(
            lock_dir=self._lock_dir,
            dataset=self.DATASET_NAME,
        )

        try:
            lock_token = lock.acquire()
        except TimeoutError as e:
            logger.error(
                "Lock acquisition timeout - cache locked by another process",
                extra={"provider": "yfinance", "dataset": self.DATASET_NAME, "error": str(e)},
                exc_info=True,
            )
            raise YFinanceError(f"Failed to acquire cache lock: {e}") from e
        except OSError as e:
            logger.error(
                "Lock acquisition failed - filesystem error",
                extra={"provider": "yfinance", "lock_dir": str(self._lock_dir), "error": str(e)},
                exc_info=True,
            )
            raise YFinanceError(f"Failed to acquire cache lock: {e}") from e

        try:
            manifest_entries: dict[str, dict[str, Any]] = {}
            failed_symbols: list[str] = []
            drift_warnings: dict[str, float] = {}

            for idx, symbol in enumerate(symbols):
                # Rate limiting: sleep BEFORE each request (except first)
                # This ensures rate limiting runs even when errors/continues occur
                if idx > 0:
                    delay = self.REQUEST_DELAY_SECONDS + random.uniform(0, self.JITTER_MAX_SECONDS)
                    time.sleep(delay)

                logger.info(
                    "Fetching symbol",
                    extra={
                        "symbol": symbol,
                        "start_date": str(start_date),
                        "end_date": str(end_date),
                    },
                )

                df = self._download_with_retry(symbol, start_date, end_date)

                if df is None or df.is_empty():
                    logger.warning("Download failed or empty", extra={"symbol": symbol})
                    failed_symbols.append(symbol)
                    continue

                # Run drift check before caching (per plan)
                if run_drift_check:
                    passed, max_drift = self.check_drift(symbol, df)
                    # Block caching if drift check fails for ANY reason:
                    # - max_drift > tolerance (drift detected)
                    # - baseline checksum invalid (can't verify drift)
                    # - any other validation failure
                    if not passed:
                        if max_drift is not None:
                            drift_warnings[symbol] = max_drift
                        # Quarantine/skip bad data - don't cache unreliable data
                        logger.warning(
                            "Skipping cache due to drift check failure",
                            extra={
                                "symbol": symbol,
                                "max_drift": (
                                    f"{max_drift:.4f}" if max_drift else "N/A (validation failed)"
                                ),
                            },
                        )
                        # Invalidate any existing stale cache for this symbol
                        existing_cache = self._safe_cache_path(symbol)
                        if existing_cache.exists():
                            self._quarantine_file(existing_cache, "drift_detected")
                            # Remove from manifest to prevent serving stale data
                            existing_manifest = self.get_manifest()
                            if existing_manifest:
                                files = existing_manifest.get("files", {})
                                files.pop(f"{symbol}.parquet", None)
                                existing_manifest["files"] = files
                                self._atomic_write_manifest(existing_manifest)
                        failed_symbols.append(symbol)
                        continue

                # Atomic write to cache (using safe path)
                target_path = self._safe_cache_path(symbol)
                checksum = self._atomic_write_parquet(df, target_path)

                # Get date range
                date_col = df.get_column("date")
                min_date = date_col.min()
                max_date = date_col.max()

                file_entry = {
                    "symbol": symbol,
                    "checksum": checksum,
                    "row_count": df.height,
                    "start_date": str(min_date) if min_date else None,
                    "end_date": str(max_date) if max_date else None,
                    "last_updated": datetime.now(UTC).isoformat(),
                }
                manifest_entries[f"{symbol}.parquet"] = file_entry

                # Update manifest IMMEDIATELY after each file write to prevent
                # race condition where concurrent readers see file but not manifest
                existing_manifest = self.get_manifest() or {}
                existing_files = existing_manifest.get("files", {})
                existing_files[f"{symbol}.parquet"] = file_entry

                manifest_data: dict[str, Any] = {
                    "dataset": self.DATASET_NAME,
                    "sync_timestamp": datetime.now(UTC).isoformat(),
                    "schema_version": "v1.0.0",
                    "files": existing_files,
                }
                self._atomic_write_manifest(manifest_data)

                logger.info(
                    "Symbol cached",
                    extra={
                        "symbol": symbol,
                        "rows": df.height,
                        "checksum": checksum[:16] + "...",
                    },
                )

            result: dict[str, Any] = {
                "files": manifest_entries,
                "failed_symbols": failed_symbols,
                "drift_warnings": drift_warnings,
            }

            if failed_symbols:
                logger.warning(
                    "Some symbols failed to fetch",
                    extra={"failed": failed_symbols, "count": len(failed_symbols)},
                )

            if drift_warnings:
                logger.warning(
                    "Drift detected for some symbols",
                    extra={"drift_warnings": drift_warnings},
                )

            return result

        finally:
            lock.release(lock_token)

    def _download_with_retry(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame | None:
        """Download data from yfinance with retries and jitter.

        Args:
            symbol: Ticker symbol.
            start_date: Start of date range.
            end_date: End of date range.

        Returns:
            Polars DataFrame or None if download failed.
        """
        # Import yfinance lazily to avoid import errors if not installed
        try:
            import yfinance as yf
        except ImportError as e:
            raise YFinanceError("yfinance not installed. Run: pip install yfinance") from e

        for attempt in range(self.MAX_RETRIES):
            try:
                # Download using yfinance
                ticker = yf.Ticker(symbol)
                pdf = ticker.history(
                    start=start_date.isoformat(),
                    end=(end_date + timedelta(days=1)).isoformat(),
                    auto_adjust=False,
                )

                if pdf.empty:
                    logger.warning(
                        "Empty response from yfinance",
                        extra={"symbol": symbol, "attempt": attempt + 1},
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self.RETRY_DELAY_SECONDS * (attempt + 1)
                        delay += random.uniform(0, self.JITTER_MAX_SECONDS)
                        time.sleep(delay)
                        continue
                    return None

                # Convert to polars
                pdf = pdf.reset_index()

                # Normalize column names
                pdf.columns = [c.lower().replace(" ", "_") for c in pdf.columns]

                # Rename columns to match schema
                rename_map = {
                    "adj_close": "adj_close",
                    "adj close": "adj_close",
                }
                for old, new in rename_map.items():
                    if old in pdf.columns and old != new:
                        pdf = pdf.rename(columns={old: new})

                df = pl.from_pandas(pdf)

                # Ensure date column is date type
                if "date" in df.columns:
                    df = df.with_columns(pl.col("date").cast(pl.Date))

                # Add symbol column
                df = df.with_columns(pl.lit(symbol).alias("symbol"))

                # Select and order columns
                available_cols = [c for c in YFINANCE_COLUMNS if c in df.columns]
                result: pl.DataFrame = df.select(available_cols)

                return result

            except Exception as e:
                logger.warning(
                    "Download attempt failed",
                    extra={
                        "symbol": symbol,
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES,
                        "error": str(e),
                    },
                )
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAY_SECONDS * (attempt + 1)
                    delay += random.uniform(0, self.JITTER_MAX_SECONDS)
                    time.sleep(delay)
                else:
                    logger.error(
                        "Download failed after retries",
                        extra={"symbol": symbol, "error": str(e)},
                    )
                    return None

        return None

    def _fetch_symbols(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> list[pl.DataFrame]:
        """Fetch multiple symbols with rate limiting.

        Args:
            symbols: List of symbols to fetch.
            start_date: Start of date range.
            end_date: End of date range.

        Returns:
            List of DataFrames (one per symbol that succeeded).
        """
        results: list[pl.DataFrame] = []

        for idx, symbol in enumerate(symbols):
            # Rate limiting: sleep BEFORE each request (except first)
            if idx > 0:
                delay = self.REQUEST_DELAY_SECONDS + random.uniform(0, self.JITTER_MAX_SECONDS)
                time.sleep(delay)

            df = self._download_with_retry(symbol, start_date, end_date)
            if df is not None and not df.is_empty():
                results.append(df)
                # Note: We do NOT cache here to avoid:
                # 1. Manifest inconsistency (M1 review feedback)
                # 2. Lock bypass race condition (M2 review feedback)
                # Use fetch_and_cache() for writes with proper locking and manifest updates.

        return results

    def _verify_cache_integrity(self, symbol: str, cache_path: Path) -> bool:
        """Verify cache file integrity against manifest checksum.

        Args:
            symbol: Symbol being verified.
            cache_path: Path to cache file.

        Returns:
            True if valid (or no manifest entry), False if corrupted.
        """
        manifest = self.get_manifest()
        if manifest is None:
            return True  # No manifest = skip verification

        files = manifest.get("files", {})
        entry = files.get(f"{symbol}.parquet")

        if entry is None:
            # File exists but not in manifest - could be a race condition where
            # writer wrote the file but hasn't updated manifest yet.
            # Return True (skip verification) instead of quarantining to avoid
            # deleting freshly written valid data during a write race.
            logger.debug(
                "Cache file not in manifest - may be in-progress write, skipping verification",
                extra={"symbol": symbol},
            )
            return True  # Don't quarantine - might be race with writer

        expected_checksum = entry.get("checksum")
        if not expected_checksum:
            return True  # No checksum = skip verification

        actual_checksum = self._compute_checksum(cache_path)
        if actual_checksum != expected_checksum:
            logger.warning(
                "Cache checksum mismatch - file corrupted or tampered",
                extra={
                    "symbol": symbol,
                    "expected": expected_checksum,
                    "actual": actual_checksum,
                },
            )
            return False

        return True

    def _read_from_cache(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame | None:
        """Read cached data for a symbol.

        Only returns data if the cache fully covers the requested date range
        AND passes integrity verification against the manifest checksum.
        This prevents silent data loss from partial coverage or corruption.

        Args:
            symbol: Ticker symbol.
            start_date: Start of date range.
            end_date: End of date range.

        Returns:
            DataFrame if cache hit, valid, and FULLY covers date range, None otherwise.
        """
        try:
            cache_path = self._safe_cache_path(symbol)
        except ValueError:
            # Invalid symbol - no cache possible
            return None

        if not cache_path.exists():
            return None

        # Verify checksum against manifest before reading
        if not self._verify_cache_integrity(symbol, cache_path):
            # Quarantine corrupted file
            try:
                self._quarantine_file(cache_path, "checksum_mismatch")
            except Exception as e:
                logger.warning(
                    "Failed to quarantine corrupted cache",
                    extra={"symbol": symbol, "error": str(e)},
                )
            return None

        try:
            df = pl.read_parquet(cache_path)

            if df.is_empty():
                return None

            # Check if cache covers the requested date range
            # Get the actual date range in the cache
            date_col = df.get_column("date")
            cache_start_raw = date_col.min()
            cache_end_raw = date_col.max()

            if cache_start_raw is None or cache_end_raw is None:
                return None

            # Cast to date for comparison (polars returns union type)
            if not isinstance(cache_start_raw, date) or not isinstance(cache_end_raw, date):
                return None

            cache_start: date = cache_start_raw
            cache_end: date = cache_end_raw

            # Verify full coverage: cache must span the entire requested range
            if cache_start > start_date or cache_end < end_date:
                logger.debug(
                    "Cache partial coverage - will refetch",
                    extra={
                        "symbol": symbol,
                        "requested": f"{start_date} to {end_date}",
                        "cached": f"{cache_start} to {cache_end}",
                    },
                )
                return None

            # Check for potential gaps in data (heuristic: ~252 trading days/year)
            # This catches files with missing interior dates
            calendar_days = (cache_end - cache_start).days + 1
            actual_rows = df.height
            # Expect roughly 70% of calendar days to be trading days (252/365 ≈ 0.69)
            # Use 50% as lower bound to account for holidays, weekends
            min_expected_rows = max(1, int(calendar_days * 0.50))
            if actual_rows < min_expected_rows:
                logger.warning(
                    "Cache may have gaps - row count lower than expected",
                    extra={
                        "symbol": symbol,
                        "actual_rows": actual_rows,
                        "min_expected": min_expected_rows,
                        "calendar_days": calendar_days,
                    },
                )
                # Quarantine gapped data - don't serve potentially incomplete data
                try:
                    self._quarantine_file(cache_path, "potential_gaps")
                except Exception as e:
                    logger.warning(
                        "Failed to quarantine gapped cache",
                        extra={"symbol": symbol, "error": str(e)},
                    )
                return None

            # Filter to requested date range
            df = df.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

            if df.is_empty():
                return None

            logger.debug(
                "Cache hit",
                extra={"symbol": symbol, "rows": df.height},
            )

            return df

        except Exception as e:
            logger.warning(
                "Failed to read cache",
                extra={"symbol": symbol, "error": str(e)},
            )
            return None

    def _validate_baseline_file(self, symbol: str, baseline_file: Path) -> bool:
        """Validate baseline file against baseline manifest.

        Args:
            symbol: Already-validated symbol.
            baseline_file: Already-validated path to baseline parquet file.

        Returns:
            True if valid (or no manifest), False if checksum mismatch.
        """
        if self._baseline_path is None:
            return True

        manifest_path = self._baseline_path / BASELINE_MANIFEST_FILE
        if not manifest_path.exists():
            logger.debug(
                "No baseline manifest found - skipping checksum validation",
                extra={"symbol": symbol},
            )
            return True

        try:
            with open(manifest_path) as f:
                manifest: dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                "Failed to read baseline manifest",
                extra={"error": str(e)},
            )
            return True  # Skip validation if manifest unreadable

        # Look for entry matching this symbol (symbol already validated/normalized)
        files = manifest.get("files", {})
        filename = baseline_file.name  # Use actual filename from validated path
        entry = files.get(filename)

        if entry is None:
            logger.debug(
                "No manifest entry for baseline file",
                extra={"symbol": symbol, "filename": filename},
            )
            return True  # No entry = skip validation

        expected_checksum = entry.get("checksum")
        if not expected_checksum:
            return True

        actual_checksum = self._compute_checksum(baseline_file)
        if actual_checksum != expected_checksum:
            logger.warning(
                "Baseline checksum mismatch - file may be corrupted or tampered",
                extra={
                    "symbol": symbol,
                    "expected": expected_checksum,
                    "actual": actual_checksum,
                },
            )
            return False

        return True

    def _safe_baseline_path(self, symbol: str) -> Path | None:
        """Get safe baseline file path for a symbol.

        Args:
            symbol: Symbol to validate and get baseline path for.

        Returns:
            Absolute path within baseline directory, or None if invalid.
        """
        if self._baseline_path is None:
            return None

        # Validate symbol format
        try:
            validated = self._validate_symbol(symbol)
        except ValueError:
            logger.warning(
                "Invalid symbol for baseline path",
                extra={"symbol": symbol},
            )
            return None

        baseline_file = (
            self._baseline_path / f"{validated.lower()}{BASELINE_FILE_SUFFIX}"
        ).resolve()

        # Defense in depth: verify path stays within baseline directory
        if not baseline_file.is_relative_to(self._baseline_path.resolve()):
            logger.warning(
                "Baseline path escape attempt",
                extra={"symbol": symbol},
            )
            return None

        return baseline_file

    def check_drift(
        self,
        symbol: str,
        yfinance_data: pl.DataFrame | None = None,
        tolerance: float = DEFAULT_DRIFT_TOLERANCE,
    ) -> tuple[bool, float | None]:
        """Check price drift against baseline data.

        Compares closing prices between yfinance and baseline data.
        Only compares dates that exist in both datasets.

        Args:
            symbol: Symbol to check (e.g., "SPY").
            yfinance_data: DataFrame from yfinance. If None, reads from cache.
            tolerance: Maximum allowed drift (default 1%).

        Returns:
            Tuple of (passed, max_drift). max_drift is None if baseline missing.
        """
        if self._baseline_path is None:
            logger.debug("No baseline path configured - skipping drift check")
            return True, None

        # Validate symbol and get safe baseline path
        baseline_file = self._safe_baseline_path(symbol)
        if baseline_file is None:
            logger.warning(
                "Invalid symbol for drift check - skipping",
                extra={"symbol": symbol},
            )
            return True, None

        if not baseline_file.exists():
            logger.warning(
                "Baseline missing for drift check - skipping",
                extra={"symbol": symbol, "path": str(baseline_file)},
            )
            return True, None  # Pass with warning (per requirements)

        # Validate baseline against manifest (checksum verification)
        if not self._validate_baseline_file(symbol, baseline_file):
            logger.warning(
                "Baseline checksum validation failed - blocking cache to prevent "
                "ingesting potentially bad data without drift verification",
                extra={"symbol": symbol},
            )
            # Return False to block caching - corrupted/tampered baseline means
            # we can't verify drift, so we must not cache unverified data
            return False, None

        # Load yfinance data if not provided
        if yfinance_data is None:
            try:
                cache_path = self._safe_cache_path(symbol)
            except ValueError:
                logger.warning(
                    "Invalid symbol for drift check",
                    extra={"symbol": symbol},
                )
                return True, None

            if not cache_path.exists():
                logger.warning(
                    "No yfinance cache for drift check",
                    extra={"symbol": symbol},
                )
                return True, None

            yfinance_data = pl.read_parquet(cache_path)

        # Load baseline
        try:
            baseline_df = pl.read_parquet(baseline_file)
        except Exception as e:
            logger.warning(
                "Failed to read baseline for drift check",
                extra={"symbol": symbol, "error": str(e)},
            )
            return True, None

        # Use adj_close for comparison since yfinance downloads with auto_adjust=False
        # and baseline (CRSP) data is typically split/dividend adjusted
        # This avoids false drift on ex-dividend dates
        yf_price_col = "adj_close" if "adj_close" in yfinance_data.columns else "close"
        baseline_price_col = "adj_close" if "adj_close" in baseline_df.columns else "close"

        if yf_price_col not in yfinance_data.columns:
            logger.warning(
                "Missing price column for drift check",
                extra={"symbol": symbol, "expected": yf_price_col},
            )
            return True, None

        if baseline_price_col not in baseline_df.columns:
            logger.warning(
                "Missing price column in baseline for drift check",
                extra={"symbol": symbol, "expected": baseline_price_col},
            )
            return True, None

        # Align data by date (inner join)
        # Use adjusted close for both sides to avoid false drift on dividend days
        yf_for_join = yfinance_data.select(["date", yf_price_col]).rename(
            {yf_price_col: "yf_close"}
        )
        baseline_for_join = baseline_df.select(["date", baseline_price_col]).rename(
            {baseline_price_col: "baseline_close"}
        )

        joined = yf_for_join.join(baseline_for_join, on="date", how="inner")

        if joined.is_empty():
            logger.warning(
                "No overlapping dates for drift check",
                extra={"symbol": symbol},
            )
            return True, None

        # Filter out zero/null baseline values to avoid division by zero
        joined = joined.filter(
            (pl.col("baseline_close").is_not_null()) & (pl.col("baseline_close") != 0)
        )

        if joined.is_empty():
            logger.warning(
                "No valid baseline prices for drift check (all zero or null)",
                extra={"symbol": symbol},
            )
            return True, None

        # Calculate drift: |yfinance - baseline| / baseline
        drift = (joined["yf_close"] - joined["baseline_close"]).abs() / joined["baseline_close"]
        drift_max = drift.max()
        if drift_max is None:
            return True, None
        max_drift = float(drift_max)  # type: ignore[arg-type]

        if max_drift > tolerance:
            logger.warning(
                "Price drift detected",
                extra={
                    "symbol": symbol,
                    "max_drift": f"{max_drift:.4f}",
                    "tolerance": tolerance,
                },
            )
            return False, max_drift

        logger.debug(
            "Drift check passed",
            extra={"symbol": symbol, "max_drift": f"{max_drift:.4f}"},
        )
        return True, max_drift

    def verify_data(self) -> dict[str, bool]:
        """Verify checksums of all cached files.

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

            file_path = self._daily_dir / filename

            if not file_path.exists():
                results[filename] = False
                continue

            actual_checksum = self._compute_checksum(file_path)
            results[filename] = actual_checksum == expected_checksum

        return results

    def invalidate_cache(self, symbols: list[str] | None = None) -> int:
        """Invalidate cache for specified symbols.

        Uses AtomicFileLock to prevent races with concurrent fetch_and_cache.

        Args:
            symbols: List of symbols to invalidate. If None, invalidates all.

        Returns:
            Number of cache files removed.
        """
        # Acquire lock to prevent races with fetch_and_cache
        lock = AtomicFileLock(
            lock_dir=self._lock_dir,
            dataset=self.DATASET_NAME,
        )

        try:
            lock_token = lock.acquire()
        except Exception as e:
            raise YFinanceError(f"Failed to acquire cache lock for invalidation: {e}") from e

        try:
            removed = 0
            removed_symbols: list[str] = []

            if symbols is None:
                # Remove all cache files
                for cache_file in self._daily_dir.glob("*.parquet"):
                    try:
                        cache_file.unlink()
                        removed += 1
                    except Exception as e:
                        logger.warning(
                            "Failed to remove cache file",
                            extra={"file": str(cache_file), "error": str(e)},
                        )

                # Clear manifest
                manifest_path = self._storage_path / "yfinance_manifest.json"
                if manifest_path.exists():
                    manifest_path.unlink()
            else:
                # Remove specific symbols
                for symbol in symbols:
                    normalized = symbol.upper()
                    try:
                        cache_file = self._safe_cache_path(normalized)
                    except ValueError:
                        logger.warning(
                            "Invalid symbol for cache invalidation",
                            extra={"symbol": symbol},
                        )
                        continue

                    if cache_file.exists():
                        try:
                            cache_file.unlink()
                            removed += 1
                            removed_symbols.append(normalized)
                        except Exception as e:
                            logger.warning(
                                "Failed to remove cache file",
                                extra={"symbol": symbol, "error": str(e)},
                            )

                # Update manifest to remove invalidated entries
                if removed_symbols:
                    manifest = self.get_manifest()
                    if manifest is not None:
                        files = manifest.get("files", {})
                        for sym in removed_symbols:
                            files.pop(f"{sym}.parquet", None)

                        manifest["files"] = files
                        manifest["sync_timestamp"] = datetime.now(UTC).isoformat()
                        self._atomic_write_manifest(manifest)

            logger.info(
                "Cache invalidated",
                extra={"removed_count": removed, "symbols": symbols},
            )
            return removed
        finally:
            lock.release(lock_token)

    def get_manifest(self) -> dict[str, Any] | None:
        """Get current manifest.

        Returns:
            Manifest dict or None if not found.
        """
        manifest_path = self._storage_path / "yfinance_manifest.json"
        if not manifest_path.exists():
            return None

        with open(manifest_path) as f:
            data: dict[str, Any] = json.load(f)
            return data

    def _check_disk_space(self, path: Path) -> None:
        """Check if sufficient disk space is available.

        Args:
            path: Path to check disk space for.

        Raises:
            OSError: If insufficient disk space.
        """
        try:
            stat = os.statvfs(path)
            available = stat.f_bavail * stat.f_frsize

            if available < self.MIN_DISK_SPACE_BYTES:
                raise OSError(
                    f"Insufficient disk space: {available} bytes available, "
                    f"need at least {self.MIN_DISK_SPACE_BYTES} bytes"
                )
        except AttributeError:
            # statvfs not available on Windows
            pass

    def _atomic_write_parquet(
        self,
        df: pl.DataFrame,
        target_path: Path,
    ) -> str:
        """Write Parquet atomically using temp file + rename + quarantine.

        Pattern (per repo standards):
        1. Check disk space
        2. Write to temp path: target.parquet.tmp
        3. Compute checksum of temp file
        4. Validate: row count > 0
        5. On validation failure: move to quarantine
        6. Atomic rename: temp -> target
        7. fsync directory for crash safety
        8. Return checksum

        Args:
            df: DataFrame to write.
            target_path: Target file path.

        Returns:
            SHA-256 checksum of written file.

        Raises:
            ValueError: If DataFrame is empty.
            OSError: If disk space insufficient.
        """
        # Check disk space before write
        self._check_disk_space(target_path.parent)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_suffix(".parquet.tmp")

        try:
            df.write_parquet(temp_path)

            # fsync the temp file to ensure data is on disk before rename
            with open(temp_path, "rb") as f:
                os.fsync(f.fileno())

            actual_checksum = self._compute_checksum(temp_path)

            # Validate row count
            if df.height == 0:
                self._quarantine_file(temp_path, "empty_dataframe")
                raise ValueError("Empty DataFrame, file quarantined")

            # Atomic rename (readers never see .tmp)
            temp_path.rename(target_path)

            # fsync directory for crash safety
            self._fsync_directory(target_path.parent)

            return actual_checksum

        except OSError as e:
            logger.error(
                "Atomic write failed - filesystem error",
                extra={
                    "provider": "yfinance",
                    "target": str(target_path),
                    "error": str(e),
                    "errno": e.errno,
                },
                exc_info=True,
            )
            # Clean up temp file on any error
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise
        except ValueError as e:
            logger.error(
                "Atomic write failed - validation error",
                extra={"provider": "yfinance", "target": str(target_path), "error": str(e)},
                exc_info=True,
            )
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
        """Compute SHA-256 checksum of file.

        Uses SHA-256 instead of MD5 for stronger integrity guarantees
        against both accidental corruption and deliberate tampering.

        Args:
            file_path: Path to file.

        Returns:
            SHA-256 hex digest.
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _fsync_directory(self, dir_path: Path) -> None:
        """Sync directory for crash safety.

        Note: O_DIRECTORY is not available on Windows. The try/except block
        handles platforms where directory fsync is not supported, allowing
        the code to degrade gracefully on those systems.

        Args:
            dir_path: Directory to sync.
        """
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            # OSError: fsync failed or O_DIRECTORY not supported
            # AttributeError: O_DIRECTORY doesn't exist on this platform
            logger.warning("Failed to fsync directory", extra={"path": str(dir_path)})

    def _atomic_write_manifest(self, manifest_data: dict[str, Any]) -> None:
        """Write manifest atomically.

        Args:
            manifest_data: Manifest data to write.
        """
        manifest_path = self._storage_path / "yfinance_manifest.json"
        temp_path = manifest_path.with_suffix(".json.tmp")

        try:
            with open(temp_path, "w") as f:
                json.dump(manifest_data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            temp_path.rename(manifest_path)
            self._fsync_directory(manifest_path.parent)

        except OSError as e:
            logger.error(
                "Manifest write failed - filesystem error",
                extra={
                    "provider": "yfinance",
                    "manifest_path": str(manifest_path),
                    "error": str(e),
                    "errno": e.errno,
                },
                exc_info=True,
            )
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise
        except (TypeError, ValueError) as e:
            logger.error(
                "Manifest write failed - serialization error",
                extra={
                    "provider": "yfinance",
                    "manifest_path": str(manifest_path),
                    "error": str(e),
                },
                exc_info=True,
            )
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def _empty_result(self) -> pl.DataFrame:
        """Return empty DataFrame with correct schema."""
        return pl.DataFrame(schema=YFINANCE_SCHEMA)

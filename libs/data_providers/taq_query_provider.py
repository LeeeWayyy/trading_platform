"""TAQ Local Query Provider.

Read-only provider for querying Trade and Quote (TAQ) aggregates and tick
samples stored on disk. Mirrors the CRSPLocalProvider pattern with
manifest-aware partition selection, optional point-in-time (PIT) resolution
via DatasetVersionManager snapshots, and thread-local DuckDB connections for
concurrent reads. Polars is supported as an alternate execution engine.

Storage layout (produced by TAQStorageManager):
    data/taq/
        aggregates/1min_bars/YYYYMM.parquet
        aggregates/daily_rv/YYYYMM.parquet
        aggregates/spread_stats/YYYYMM.parquet
        samples/YYYY-MM-DD/<SYMBOL>.parquet

Datasets and schemas (see taq_storage.py):
    - taq_1min_bars: ts, symbol, open, high, low, close, volume, vwap, date
    - taq_daily_rv:  date, symbol, rv_5m, rv_30m, obs
    - taq_spread_stats: date, symbol, qwap_spread, ewas, quotes, trades
    - taq_ticks: ts, symbol, bid, ask, bid_size, ask_size, trade_px,
      trade_size, cond
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal

import duckdb
import polars as pl

from libs.data_providers.taq_storage import (
    TAQ_1MIN_BARS_SCHEMA,
    TAQ_DAILY_RV_SCHEMA,
    TAQ_SPREAD_STATS_SCHEMA,
    TAQ_TICKS_SCHEMA,
)
from libs.data_quality.exceptions import (
    DataNotFoundError,
    DatasetNotInSnapshotError,
    SnapshotNotFoundError,
)
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.validation import DataValidator
from libs.data_quality.versioning import DatasetVersionManager

logger = logging.getLogger(__name__)


class TAQLocalProvider:
    """Query TAQ aggregates and samples from local Parquet files.

    Features
    --------
    - DuckDB execution (default) with thread-local in-memory connections
    - Optional Polars engine for lightweight scans
    - Point-in-time queries backed by DatasetVersionManager snapshots
    - Manifest-driven partition pruning (no direct filesystem globbing)
    - Security checks to keep all reads within the configured data_root

    Thread safety
    -------------
    DuckDB connections are **not** thread-safe; each thread obtains its own
    connection via thread-local storage. Polars scans are inherently
    thread-safe for read-only access.

    Parameters
    ----------
    storage_path:
        Root of TAQ data (e.g., ``Path("data/taq")``). All manifest paths are
        validated to reside under this directory.
    manifest_manager:
        Manifest manager configured **with storage_path** (e.g.
        ``ManifestManager(storage_path=Path("data/manifests/taq"))``).
    version_manager:
        Dataset version manager initialized with the same manifest manager and
        a TAQ-specific ``snapshots_dir`` (e.g., ``data/snapshots/taq``).
    engine:
        ``"duckdb"`` (default) or ``"polars"``.
    data_root:
        Security boundary for all file paths (default: ``Path("data")``).
    """

    DATA_ROOT = Path("data")
    DATASET_1MIN = "taq_1min_bars"
    DATASET_RV = "taq_daily_rv"
    DATASET_SPREADS = "taq_spread_stats"
    DATASET_SAMPLES_PREFIX = "taq_samples"

    SCHEMAS: dict[str, dict[str, str]] = {
        DATASET_1MIN: TAQ_1MIN_BARS_SCHEMA,
        DATASET_RV: TAQ_DAILY_RV_SCHEMA,
        DATASET_SPREADS: TAQ_SPREAD_STATS_SCHEMA,
        "taq_ticks": TAQ_TICKS_SCHEMA,
    }

    def __init__(
        self,
        storage_path: Path,
        manifest_manager: ManifestManager,
        version_manager: DatasetVersionManager | None = None,
        engine: Literal["duckdb", "polars"] = "duckdb",
        data_root: Path | None = None,
    ) -> None:
        self.storage_path = Path(storage_path).resolve()
        self.data_root = (data_root or self.DATA_ROOT).resolve()
        self.manifest_manager = manifest_manager
        self.version_manager = version_manager
        self.engine = engine

        if engine not in ("duckdb", "polars"):
            raise ValueError("engine must be 'duckdb' or 'polars'")

        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{self.storage_path}' must be within data_root '{self.data_root}'"
            )

        self._thread_local: threading.local = threading.local()

    # ------------------------------------------------------------------
    # Public query surface
    # ------------------------------------------------------------------
    def fetch_minute_bars(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Fetch 1-minute bars for symbols in [start_date, end_date].

        Args:
            symbols: List of ticker symbols (case-insensitive).
            start_date: Inclusive start date.
            end_date: Inclusive end date.
            as_of: Optional PIT date. When provided, resolves paths using the
                latest snapshot on or before ``as_of`` via
                ``DatasetVersionManager.query_as_of``.

        Returns:
            Polars DataFrame sorted by date, symbol, ts. Empty DataFrame when no
            data are available.
        """

        self._validate_symbols(symbols)
        if start_date > end_date:
            return self._empty_result(self.DATASET_1MIN)

        symbols_u = [s.upper() for s in symbols]
        paths = self._resolve_partition_paths(
            dataset=self.DATASET_1MIN,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        if not paths:
            return self._empty_result(self.DATASET_1MIN)

        return self._execute_query(
            paths=paths,
            date_col="date",
            start_date=start_date,
            end_date=end_date,
            symbols=symbols_u,
            order_by=["date", "symbol", "ts"],
        )

    def fetch_realized_volatility(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        window: int = 5,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Fetch daily realized volatility metrics.

        The underlying dataset contains both 5-minute and 30-minute sampled
        realized volatility. The ``window`` parameter selects the desired
        column and adds a convenience alias ``rv`` while preserving the
        original fields.
        """

        self._validate_symbols(symbols)
        if start_date > end_date:
            return self._empty_result(self.DATASET_RV)

        if window not in (5, 30):
            raise ValueError("window must be 5 or 30 minutes")

        symbols_u = [s.upper() for s in symbols]
        paths = self._resolve_partition_paths(
            dataset=self.DATASET_RV,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        if not paths:
            return self._empty_result(self.DATASET_RV)

        df = self._execute_query(
            paths=paths,
            date_col="date",
            start_date=start_date,
            end_date=end_date,
            symbols=symbols_u,
            order_by=["date", "symbol"],
        )

        # Add window-specific alias without dropping source columns
        rv_col = f"rv_{window}m"
        if rv_col in df.columns:
            df = df.with_columns(pl.col(rv_col).alias("rv"))

        return df

    def fetch_spread_metrics(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Fetch daily spread / market quality metrics."""

        self._validate_symbols(symbols)
        if start_date > end_date:
            return self._empty_result(self.DATASET_SPREADS)

        symbols_u = [s.upper() for s in symbols]
        paths = self._resolve_partition_paths(
            dataset=self.DATASET_SPREADS,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        if not paths:
            return self._empty_result(self.DATASET_SPREADS)

        return self._execute_query(
            paths=paths,
            date_col="date",
            start_date=start_date,
            end_date=end_date,
            symbols=symbols_u,
            order_by=["date", "symbol"],
        )

    def fetch_ticks(
        self,
        sample_date: date,
        symbols: list[str],
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Fetch tick samples for a specific date.

        Args:
            sample_date: Trading date of the tick sample directory.
            symbols: List of symbols to load.
            as_of: Optional PIT date. When provided, resolves paths using the
                latest snapshot on or before ``as_of`` via
                ``DatasetVersionManager.query_as_of``. Used for backtesting
                reproducibility and T3.2 Execution Quality analysis.

        Returns:
            DataFrame with tick-level fields sorted by ts and symbol.
        """

        self._validate_symbols(symbols)
        dataset = f"{self.DATASET_SAMPLES_PREFIX}_{sample_date.strftime('%Y%m%d')}"
        symbols_u = {s.upper() for s in symbols}

        if as_of is not None:
            if self.version_manager is None:
                raise ValueError("version_manager is required for PIT queries")
            paths = self._tick_paths_from_snapshot(
                dataset=dataset,
                sample_date=sample_date,
                symbols=symbols_u,
                as_of=as_of,
            )
        else:
            manifest = self._get_manifest(dataset)
            paths = self._filter_symbol_paths(
                manifest.file_paths,
                symbols=symbols_u,
                base_dir=self.storage_path / "samples" / sample_date.strftime("%Y-%m-%d"),
            )

        if not paths:
            return self._empty_result("taq_ticks")

        return self._execute_query(
            paths=paths,
            date_col=None,
            start_date=None,
            end_date=None,
            symbols=list(symbols_u),
            order_by=["ts", "symbol"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _execute_query(
        self,
        paths: list[Path],
        date_col: str | None,
        start_date: date | None,
        end_date: date | None,
        symbols: list[str],
        order_by: list[str],
    ) -> pl.DataFrame:
        """Dispatch to DuckDB or Polars execution."""

        if self.engine == "duckdb":
            return self._execute_duckdb(
                paths=paths,
                date_col=date_col,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
                order_by=order_by,
            )

        return self._execute_polars(
            paths=paths,
            date_col=date_col,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            order_by=order_by,
        )

    def _execute_duckdb(
        self,
        paths: list[Path],
        date_col: str | None,
        start_date: date | None,
        end_date: date | None,
        symbols: list[str],
        order_by: list[str],
    ) -> pl.DataFrame:
        conn = self._ensure_connection()

        params: dict[str, Any] = {"paths": [str(p) for p in paths]}
        where_clauses: list[str] = []

        if date_col is not None and start_date is not None and end_date is not None:
            params["start_date"] = start_date
            params["end_date"] = end_date
            where_clauses.extend([f"{date_col} >= $start_date", f"{date_col} <= $end_date"])

        if symbols:
            params["symbols"] = symbols
            where_clauses.append("symbol = ANY($symbols)")

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"
        order_sql = ", ".join(order_by)

        query = f"""
            SELECT *
            FROM read_parquet($paths)
            WHERE {where_sql}
            ORDER BY {order_sql}
        """

        return conn.execute(query, params).pl()

    def _execute_polars(
        self,
        paths: list[Path],
        date_col: str | None,
        start_date: date | None,
        end_date: date | None,
        symbols: list[str],
        order_by: list[str],
    ) -> pl.DataFrame:
        lf = pl.scan_parquet([str(p) for p in paths])

        if date_col is not None and start_date is not None and end_date is not None:
            lf = lf.filter(
                (pl.col(date_col) >= pl.lit(start_date)) & (pl.col(date_col) <= pl.lit(end_date))
            )

        if symbols:
            lf = lf.filter(pl.col("symbol").is_in(symbols))

        return lf.sort(order_by).collect()

    def _resolve_partition_paths(
        self,
        dataset: str,
        start_date: date,
        end_date: date,
        as_of: date | None,
    ) -> list[Path]:
        needed_months = self._months_between(start_date, end_date)

        if as_of is not None:
            if self.version_manager is None:
                raise ValueError("version_manager is required for PIT queries")
            return self._paths_from_snapshot(dataset, needed_months, as_of)

        manifest = self._get_manifest(dataset)
        return self._filter_month_partitions(manifest.file_paths, needed_months)

    def _paths_from_snapshot(
        self, dataset: str, needed_months: set[str], as_of: date
    ) -> list[Path]:
        # version_manager is checked non-None by callers before using as_of
        assert self.version_manager is not None
        try:
            data_path, snapshot = self.version_manager.query_as_of(dataset, as_of)
        except (SnapshotNotFoundError, DatasetNotInSnapshotError) as exc:
            raise DataNotFoundError(
                f"No snapshot available for dataset '{dataset}' as of {as_of}"
            ) from exc

        if dataset not in snapshot.datasets:
            raise DataNotFoundError(f"Snapshot missing dataset '{dataset}' for as_of {as_of}")

        files = snapshot.datasets[dataset].files
        paths: list[Path] = []

        for file_info in files:
            month_key = self._extract_month_key(Path(file_info.original_path))
            if month_key is None or month_key not in needed_months:
                continue

            candidate = (data_path / file_info.path).resolve()
            if candidate.is_relative_to(self.data_root):
                paths.append(candidate)
            else:
                logger.warning("Skipping snapshot path outside data_root: %s", candidate)

        return paths

    def _tick_paths_from_snapshot(
        self,
        dataset: str,
        sample_date: date,
        symbols: set[str],
        as_of: date,
    ) -> list[Path]:
        """Resolve tick sample paths from a PIT snapshot.

        Unlike _paths_from_snapshot (which filters by month), this method
        filters by symbol for tick samples stored by date.

        Args:
            dataset: Dataset identifier (e.g., taq_samples_20240115).
            sample_date: The trading date for tick samples.
            symbols: Set of uppercase ticker symbols to load.
            as_of: PIT date for snapshot resolution.

        Returns:
            List of resolved Parquet file paths.
        """
        assert self.version_manager is not None  # Caller must check

        try:
            data_path, snapshot = self.version_manager.query_as_of(dataset, as_of)
        except (SnapshotNotFoundError, DatasetNotInSnapshotError) as exc:
            raise DataNotFoundError(
                f"No snapshot available for dataset '{dataset}' as of {as_of}"
            ) from exc

        if dataset not in snapshot.datasets:
            raise DataNotFoundError(f"Snapshot missing dataset '{dataset}' for as_of {as_of}")

        files = snapshot.datasets[dataset].files
        paths: list[Path] = []
        expected_root = (self.storage_path / "samples" / sample_date.strftime("%Y-%m-%d")).resolve()

        for file_info in files:
            # Extract symbol from filename (e.g., AAPL.parquet -> AAPL)
            symbol = Path(file_info.original_path).stem.upper()
            if symbol not in symbols:
                continue

            candidate = (data_path / file_info.path).resolve()

            # Security: ensure path is under data_root
            if not candidate.is_relative_to(self.data_root):
                logger.warning("Skipping snapshot path outside data_root: %s", candidate)
                continue

            # Defense in depth: ensure path is under expected sample date directory
            if not candidate.is_relative_to(expected_root):
                logger.warning("Skipping unexpected sample path: %s", candidate)
                continue

            paths.append(candidate)

        return paths

    def _filter_month_partitions(self, paths: Iterable[str], needed_months: set[str]) -> list[Path]:
        filtered: list[Path] = []
        for path_str in paths:
            path = Path(path_str)
            month_key = self._extract_month_key(path)
            if month_key is None or month_key not in needed_months:
                continue

            resolved = path.resolve()
            if not resolved.is_relative_to(self.data_root):
                logger.warning("Skipping path outside data_root: %s", resolved)
                continue
            filtered.append(resolved)

        return filtered

    def _filter_symbol_paths(
        self,
        paths: Iterable[str],
        symbols: set[str],
        base_dir: Path,
    ) -> list[Path]:
        """Filter sample paths to requested symbols only."""

        filtered: list[Path] = []
        for path_str in paths:
            path = Path(path_str)
            symbol = path.stem.upper()
            if symbol not in symbols:
                continue

            resolved = path.resolve()
            if not resolved.is_relative_to(self.data_root):
                logger.warning("Skipping path outside data_root: %s", resolved)
                continue

            # Ensure path resides under expected date directory for defense in depth
            expected_root = base_dir.resolve()
            if not resolved.is_relative_to(expected_root):
                logger.warning("Skipping unexpected sample path: %s", resolved)
                continue

            filtered.append(resolved)

        return filtered

    def _months_between(self, start_date: date, end_date: date) -> set[str]:
        months: set[str] = set()
        cursor = date(start_date.year, start_date.month, 1)
        end_cursor = date(end_date.year, end_date.month, 1)

        while cursor <= end_cursor:
            months.add(cursor.strftime("%Y%m"))
            # Advance to first day of next month
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

        return months

    def _extract_month_key(self, path: Path) -> str | None:
        """Extract YYYYMM partition key from a parquet filename."""

        stem = path.stem  # e.g., 202411 from 202411.parquet
        if len(stem) == 6 and stem.isdigit():
            return stem
        return None

    def _get_manifest(self, dataset: str) -> SyncManifest:
        manifest = self.manifest_manager.load_manifest(dataset)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for dataset '{dataset}'. Run sync before querying."
            )
        return manifest

    def _empty_result(self, dataset: str) -> pl.DataFrame:
        schema_spec = self.SCHEMAS.get(dataset)
        if schema_spec is None:
            return pl.DataFrame()

        schema = {col: DataValidator.DTYPE_MAP[dtype.lower()] for col, dtype in schema_spec.items()}
        return pl.DataFrame(schema=schema)

    def _validate_symbols(self, symbols: Iterable[str]) -> None:
        if not symbols:
            raise ValueError("symbols list cannot be empty")

    def _ensure_connection(self) -> duckdb.DuckDBPyConnection:
        """Return thread-local DuckDB connection with safe pragmas applied."""

        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = duckdb.connect(":memory:", read_only=False)
            conn.execute("PRAGMA disable_object_cache")
            conn.execute("PRAGMA memory_limit='2GB'")
            conn.execute("PRAGMA threads=4")
            self._thread_local.conn = conn
        return conn

    def invalidate_cache(self) -> None:
        """Placeholder for interface symmetry; no caches yet."""

    def close(self) -> None:
        """Close the DuckDB connection for the current thread."""

        conn = getattr(self._thread_local, "conn", None)
        if conn is not None:
            conn.close()
            self._thread_local.conn = None

    def __enter__(self) -> TAQLocalProvider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.close()

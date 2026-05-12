"""Alpaca SIP local historical data provider.

Read-only provider for querying normalized Alpaca SIP daily bars stored in
Parquet files. This mirrors the CRSP local-provider pattern: callers query a
manifest-pinned local snapshot through DuckDB instead of calling Alpaca live
during training or backtesting.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import weakref
from datetime import date
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl

from libs.data.data_providers.alpaca_corp_actions_sync import (
    ALPACA_CORP_ACTIONS_SCHEMA,
)
from libs.data.data_providers.alpaca_sip_paths import resolve_alpaca_sip_manifest_path
from libs.data.data_quality.exceptions import DataNotFoundError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest

logger = logging.getLogger(__name__)


class AlpacaSIPManifestVersionChangedError(Exception):
    """Raised when a manifest changes during a query."""


ALPACA_SIP_COLUMNS = (
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "vwap",
    "adj_close",
    "ret",
)

ALPACA_SIP_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "symbol": pl.Utf8,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "trade_count": pl.Float64,
    "vwap": pl.Float64,
    "adj_close": pl.Float64,
    "ret": pl.Float64,
}

VALID_COLUMNS = set(ALPACA_SIP_COLUMNS)
_DUCKDB_MEMORY_LIMIT_RE = re.compile(
    r"^[1-9][0-9]*(?:\.[0-9]+)?\s*(?:B|KB|MB|GB|TB|KIB|MIB|GIB|TIB)$",
    re.IGNORECASE,
)


class AlpacaSIPLocalProvider:
    """Read-only provider for normalized local Alpaca SIP daily bars.

    Expected storage layout:

        data/alpaca/sip/daily/
        ├── 2023.parquet
        └── 2024.parquet

    Required parquet columns are `date`, `symbol`, `open`, `high`, `low`,
    `close`, and `volume`. Optional columns include `trade_count`, `vwap`,
    `adj_close`, and `ret`.
    """

    DATASET_NAME = "alpaca_sip_daily"
    CORP_ACTIONS_DATASET_NAME = "alpaca_sip_corp_actions"
    DATA_ROOT = Path("data")

    def __init__(
        self,
        storage_path: Path,
        manifest_manager: ManifestManager,
        data_root: Path | None = None,
        pinned_manifest: SyncManifest | None = None,
        corp_actions_storage_path: Path | None = None,
        duckdb_memory_limit: str | None = None,
        duckdb_threads: int | None = None,
    ) -> None:
        """Initialize the local Alpaca SIP provider.

        Args:
            storage_path: Path to daily SIP parquet files.
            manifest_manager: Manager for manifest operations.
            data_root: Root directory for path validation.
            pinned_manifest: Optional immutable manifest to use for all reads.
            corp_actions_storage_path: Optional path to paired Alpaca SIP
                corporate-action parquet files. Defaults to
                ALPACA_CORP_ACTIONS_STORAGE_PATH or
                ``data_root/alpaca/sip/corp_actions``.
            duckdb_memory_limit: Optional DuckDB memory limit (env fallback:
                ALPACA_SIP_DUCKDB_MEMORY_LIMIT, default: 2GB).
            duckdb_threads: Optional DuckDB worker threads (env fallback:
                ALPACA_SIP_DUCKDB_THREADS, default: 4).

        Raises:
            ValueError: If storage_path is outside data_root.
        """
        self.storage_path = Path(storage_path).resolve()
        self.manifest_manager = manifest_manager
        self.data_root = (data_root or self.DATA_ROOT).resolve()
        raw_corp_actions_storage_path = (
            corp_actions_storage_path
            or os.getenv("ALPACA_CORP_ACTIONS_STORAGE_PATH")
            or self.data_root / "alpaca" / "sip" / "corp_actions"
        )
        self.corp_actions_storage_path = Path(raw_corp_actions_storage_path).resolve()
        self._pinned_manifest = pinned_manifest
        self._duckdb_memory_limit = self._resolve_duckdb_memory_limit(duckdb_memory_limit)
        self._duckdb_threads = self._resolve_duckdb_threads(duckdb_threads)

        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{storage_path}' must be within data_root '{self.data_root}'"
            )
        if not self.corp_actions_storage_path.is_relative_to(self.data_root):
            raise ValueError(
                "corp_actions_storage_path "
                f"'{self.corp_actions_storage_path}' must be within data_root '{self.data_root}'"
            )

        self._thread_local = threading.local()
        self._connection_lock = threading.RLock()
        self._connections: weakref.WeakSet[duckdb.DuckDBPyConnection] = weakref.WeakSet()
        self._connection_generation = 0

    def get_daily_prices(
        self,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Get local Alpaca SIP daily bars for a date range.

        Args:
            start_date: Start date, inclusive.
            end_date: End date, inclusive.
            symbols: Optional ticker-symbol filter.
            columns: Optional column projection.

        Returns:
            Polars DataFrame with requested local SIP columns.

        Raises:
            ValueError: If invalid columns are requested.
            DataNotFoundError: If no manifest exists.
            AlpacaSIPManifestVersionChangedError: If manifest changes mid-query.
        """
        if columns is not None:
            invalid = set(columns) - VALID_COLUMNS
            if invalid:
                raise ValueError(f"Invalid columns: {invalid}. Valid: {VALID_COLUMNS}")

        if start_date > end_date:
            return self._empty_result(columns)

        manifest = self._get_manifest()
        pinned_version = manifest.manifest_version
        partition_paths = self._get_partition_paths_from_manifest(manifest, start_date, end_date)

        if not partition_paths:
            return self._empty_result(columns)

        result = self._execute_query(
            partition_paths=partition_paths,
            start_date=start_date,
            end_date=end_date,
            symbols=symbols,
            columns=columns,
        )

        if self._pinned_manifest is None:
            current_manifest = self._get_manifest()
            if current_manifest.manifest_version != pinned_version:
                raise AlpacaSIPManifestVersionChangedError(
                    f"Manifest version changed from {pinned_version} to "
                    f"{current_manifest.manifest_version} during query"
                )

        return result

    def get_corporate_actions(
        self,
        *,
        start_date: date,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        """Get paired corporate actions from the trusted manifest.

        The read-time split adjustment layer needs actions on or after the
        first requested price date so later splits can adjust earlier raw bars.
        A missing corporate-action manifest fails closed via ``DataNotFoundError``.
        """
        manifest = self.manifest_manager.load_manifest(self.CORP_ACTIONS_DATASET_NAME)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for '{self.CORP_ACTIONS_DATASET_NAME}'. "
                "Run Alpaca corporate-actions sync first."
            )

        partition_paths = self._get_corp_action_paths_from_manifest(manifest)
        if not partition_paths:
            return pl.DataFrame(schema=ALPACA_CORP_ACTIONS_SCHEMA)

        return self._execute_corp_actions_query(
            partition_paths=partition_paths,
            start_date=start_date,
            symbols=symbols,
        )

    def _get_manifest(self) -> SyncManifest:
        """Load the Alpaca SIP daily manifest."""
        if self._pinned_manifest is not None:
            return self._pinned_manifest

        manifest = self.manifest_manager.load_manifest(self.DATASET_NAME)
        if manifest is None:
            raise DataNotFoundError(
                f"No manifest found for '{self.DATASET_NAME}'. Run Alpaca SIP sync first."
            )
        return manifest

    def _get_partition_paths_from_manifest(
        self,
        manifest: SyncManifest,
        start_date: date,
        end_date: date,
    ) -> list[Path]:
        """Return manifest paths for year partitions overlapping the query range."""
        needed_years = set(range(start_date.year, end_date.year + 1))
        paths: list[Path] = []

        for path_str in manifest.file_paths:
            path = Path(path_str)
            try:
                year = int(path.stem)
            except ValueError:
                continue

            if year not in needed_years:
                continue

            resolved = self._resolve_manifest_path(path)
            if resolved.is_relative_to(self.storage_path):
                paths.append(resolved)
            else:
                logger.warning("Skipping path outside Alpaca SIP storage_path: %s", resolved)

        return paths

    def _resolve_manifest_path(self, path: Path) -> Path:
        """Resolve manifest paths without depending on process working directory."""
        return resolve_alpaca_sip_manifest_path(
            path,
            data_root=self.data_root,
            storage_root=self.storage_path,
        )

    def _resolve_corp_action_manifest_path(self, path: Path) -> Path:
        return resolve_alpaca_sip_manifest_path(
            path,
            data_root=self.data_root,
            storage_root=self.corp_actions_storage_path,
        )

    def _get_corp_action_paths_from_manifest(
        self,
        manifest: SyncManifest,
    ) -> list[Path]:
        paths: list[Path] = []
        for path_str in manifest.file_paths:
            resolved = self._resolve_corp_action_manifest_path(Path(path_str))
            if resolved.is_relative_to(self.corp_actions_storage_path):
                paths.append(resolved)
            else:
                logger.warning(
                    "Skipping path outside Alpaca SIP corp-actions storage: %s", resolved
                )
        return paths

    def _execute_query(
        self,
        partition_paths: list[Path],
        start_date: date,
        end_date: date,
        symbols: list[str] | None,
        columns: list[str] | None,
    ) -> pl.DataFrame:
        """Execute a parameterized DuckDB query over selected partitions."""
        col_expr = (
            "*"
            if columns is None
            else ", ".join(self._quote_identifier(column) for column in columns)
        )

        params: dict[str, Any] = {
            "paths": [str(p) for p in partition_paths],
            "start_date": start_date,
            "end_date": end_date,
        }
        where_clauses = ['"date" >= $start_date', '"date" <= $end_date']

        if symbols is not None:
            params["symbols"] = sorted(
                {symbol.upper().strip() for symbol in symbols if symbol.strip()}
            )
            where_clauses.append('"symbol" = ANY($symbols)')

        query = f"""
            SELECT {col_expr}
            FROM read_parquet($paths)
            WHERE {" AND ".join(where_clauses)}
            ORDER BY "date", "symbol"
        """

        with self._connection_lock:
            conn = self._connection_for_current_thread()
            return conn.execute(query, params).pl()

    def _execute_corp_actions_query(
        self,
        partition_paths: list[Path],
        start_date: date,
        symbols: list[str] | None,
    ) -> pl.DataFrame:
        params: dict[str, Any] = {
            "paths": [str(p) for p in partition_paths],
            "start_date": start_date,
        }
        where_clauses = ['COALESCE("ex_date", "process_date") >= $start_date']

        if symbols is not None:
            params["symbols"] = sorted(
                {symbol.upper().strip() for symbol in symbols if symbol.strip()}
            )
            where_clauses.append('"symbol" = ANY($symbols)')

        query = f"""
            SELECT *
            FROM read_parquet($paths)
            WHERE {" AND ".join(where_clauses)}
            ORDER BY "symbol", COALESCE("ex_date", "process_date")
        """

        with self._connection_lock:
            conn = self._connection_for_current_thread()
            return conn.execute(query, params).pl()

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if identifier not in VALID_COLUMNS:
            raise ValueError(f"Invalid column identifier: {identifier}")
        return f'"{identifier}"'

    def _connection_for_current_thread(self) -> duckdb.DuckDBPyConnection:
        """Get or create a cached DuckDB connection scoped to the current thread."""
        with self._connection_lock:
            cached_conn = cast(
                duckdb.DuckDBPyConnection | None,
                getattr(self._thread_local, "connection", None),
            )
            cached_generation = getattr(self._thread_local, "connection_generation", None)
            if cached_conn is not None and cached_generation == self._connection_generation:
                return cached_conn
            if cached_conn is not None:
                self._drop_thread_local_connection(cached_conn)

            generation = self._connection_generation
            conn = self._new_connection()
            self._connections.add(conn)
            self._thread_local.connection = conn
            self._thread_local.connection_generation = generation
            return conn

    def _new_connection(self) -> duckdb.DuckDBPyConnection:
        """Create a DuckDB connection for thread-local caching."""
        conn = duckdb.connect(
            ":memory:",
            read_only=False,
            config={
                "memory_limit": self._duckdb_memory_limit,
                "threads": str(self._duckdb_threads),
            },
        )
        conn.execute("PRAGMA disable_object_cache")
        return conn

    def _drop_thread_local_connection(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Remove and close a stale thread-local connection immediately."""
        with self._connection_lock:
            self._connections.discard(conn)
        self._close_duckdb_connection(conn)
        if hasattr(self._thread_local, "connection"):
            del self._thread_local.connection
        if hasattr(self._thread_local, "connection_generation"):
            del self._thread_local.connection_generation

    @staticmethod
    def _close_duckdb_connection(conn: duckdb.DuckDBPyConnection) -> None:
        try:
            conn.close()
        except Exception as exc:  # pragma: no cover - defensive cleanup path
            logger.debug("Failed to close Alpaca SIP DuckDB connection: %s", exc)

    @staticmethod
    def _resolve_duckdb_memory_limit(value: str | None) -> str:
        memory_limit = (value or os.getenv("ALPACA_SIP_DUCKDB_MEMORY_LIMIT") or "2GB").strip()
        if not _DUCKDB_MEMORY_LIMIT_RE.fullmatch(memory_limit):
            raise ValueError(
                "duckdb_memory_limit must be a positive DuckDB size such as '512MB' or '2GB'"
            )
        return memory_limit

    @staticmethod
    def _resolve_duckdb_threads(value: int | None) -> int:
        raw_value: int | str | None = value
        if raw_value is None:
            raw_value = os.getenv("ALPACA_SIP_DUCKDB_THREADS") or "4"
        try:
            threads = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("duckdb_threads must be a positive integer") from exc
        if threads < 1:
            raise ValueError("duckdb_threads must be a positive integer")
        return threads

    def _empty_result(self, columns: list[str] | None) -> pl.DataFrame:
        """Return an empty DataFrame with the requested local schema."""
        if columns is None:
            schema = ALPACA_SIP_SCHEMA
        else:
            schema = {column: ALPACA_SIP_SCHEMA[column] for column in columns}
        return pl.DataFrame(schema=schema)

    def close(self) -> None:
        """Close all cached DuckDB connections owned by this provider."""
        with self._connection_lock:
            self._connection_generation += 1
            connections = list(self._connections)
            self._connections.clear()
            if hasattr(self._thread_local, "connection"):
                del self._thread_local.connection
            if hasattr(self._thread_local, "connection_generation"):
                del self._thread_local.connection_generation
            for conn in connections:
                self._close_duckdb_connection(conn)
        logger.debug("Closed %d Alpaca SIP DuckDB connection(s)", len(connections))

    def __del__(self) -> None:
        """Best-effort cleanup for providers that outlive their explicit owner."""
        try:
            self.close()
        except Exception as exc:  # pragma: no cover - destructor safety path
            logger.debug("Failed to close Alpaca SIP provider during finalization: %s", exc)

    def __enter__(self) -> AlpacaSIPLocalProvider:
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

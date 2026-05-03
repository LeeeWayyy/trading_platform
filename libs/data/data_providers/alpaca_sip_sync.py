"""Bulk sync manager for Alpaca SIP daily bars.

This module writes normalized Alpaca SIP historical bars to local yearly
Parquet partitions and signs the resulting snapshot with the existing
``SyncManifest`` format. Reads remain local-only through
``AlpacaSIPLocalProvider``.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import polars as pl

from libs.data.data_providers.alpaca_sip_local_provider import (
    ALPACA_SIP_COLUMNS,
    ALPACA_SIP_SCHEMA,
)
from libs.data.data_providers.registry import (
    ProviderType,
    compute_symbol_set_hash,
    get_provider_spec,
)
from libs.data.data_quality.exceptions import DiskSpaceError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest

try:
    from alpaca.common.enums import Sort
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    ALPACA_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency guard
    ALPACA_AVAILABLE = False
    Sort = None  # type: ignore[assignment,misc]
    Adjustment = None  # type: ignore[assignment,misc]
    DataFeed = None  # type: ignore[assignment,misc]
    StockHistoricalDataClient = None  # type: ignore[assignment,misc]
    StockBarsRequest = None  # type: ignore[assignment,misc]
    TimeFrame = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlpacaSIPSyncEstimate:
    """Offline estimate for an Alpaca SIP daily-bar full sync."""

    symbol_count: int
    start_year: int
    end_year: int
    year_count: int
    request_chunk_size: int
    request_count: int
    estimated_rows: int
    estimated_storage_bytes: int
    request_interval_seconds: float
    requests_per_minute: int | None
    estimated_throttle_seconds: float
    estimated_rate_limit_floor_seconds: float | None
    estimated_total_seconds: float
    symbol_set_hash: str

    def to_dict(self) -> dict[str, int | float | str | None]:
        """Serialize estimate to stable JSON-compatible values."""
        return {
            "symbol_count": self.symbol_count,
            "start_year": self.start_year,
            "end_year": self.end_year,
            "year_count": self.year_count,
            "request_chunk_size": self.request_chunk_size,
            "request_count": self.request_count,
            "estimated_rows": self.estimated_rows,
            "estimated_storage_bytes": self.estimated_storage_bytes,
            "request_interval_seconds": self.request_interval_seconds,
            "requests_per_minute": self.requests_per_minute,
            "estimated_throttle_seconds": self.estimated_throttle_seconds,
            "estimated_rate_limit_floor_seconds": self.estimated_rate_limit_floor_seconds,
            "estimated_total_seconds": self.estimated_total_seconds,
            "estimated_total_minutes": round(self.estimated_total_seconds / 60.0, 4),
            "symbol_set_hash": self.symbol_set_hash,
        }


class AlpacaStockBarsClient(Protocol):
    """Minimal client interface needed by ``AlpacaSIPSyncManager``."""

    def get_stock_bars(self, request_params: Any) -> Any:
        """Return bars for a ``StockBarsRequest``."""


class AlpacaSIPSyncManager:
    """Sync normalized Alpaca SIP daily bars to local Parquet.

    The manager is intentionally focused on daily bars for Phase 2. It uses the
    Alpaca SDK's paginated ``StockBarsRequest`` path, writes each full sync into
    an immutable snapshot directory, and updates
    ``data/manifests/alpaca_sip_daily.json`` via ``ManifestManager``.
    """

    DATASET_NAME = "alpaca_sip_daily"
    DEFAULT_DATA_ROOT = Path("data")
    DEFAULT_STORAGE_PATH = Path("data/alpaca/sip/daily")
    BYTES_PER_ROW_ESTIMATE = 160
    MAX_PAGES_PER_REQUEST = 1000

    def __init__(
        self,
        client: AlpacaStockBarsClient,
        storage_path: Path,
        manifest_manager: ManifestManager,
        *,
        data_root: Path | None = None,
        request_chunk_size: int = 200,
        request_interval_seconds: float = 0.0,
        feed: str = "sip",
        adjustment: str = "raw",
    ) -> None:
        """Initialize the sync manager.

        Args:
            client: Alpaca historical data client or compatible test double.
            storage_path: Directory where yearly daily-bar partitions are written.
            manifest_manager: Manifest manager for snapshot signing.
            data_root: Root directory used for path validation.
            request_chunk_size: Number of symbols per Alpaca request.
            request_interval_seconds: Sleep between API requests for throttling.
            feed: Alpaca data feed. Phase 2 defaults to ``sip``.
            adjustment: Alpaca adjustment policy. Canonical SIP syncs currently
                require ``raw`` so the stored OHLC columns remain unadjusted.
        """
        if request_chunk_size < 1:
            raise ValueError("request_chunk_size must be >= 1")
        if request_interval_seconds < 0:
            raise ValueError("request_interval_seconds must be >= 0")

        self.client = client
        self.storage_path = Path(storage_path).resolve()
        self.manifest_manager = manifest_manager
        self.data_root = (data_root or self.DEFAULT_DATA_ROOT).resolve()
        self.request_chunk_size = request_chunk_size
        self.request_interval_seconds = request_interval_seconds
        self.feed = feed.lower().strip()
        self.adjustment = adjustment.lower().strip()
        if self.adjustment != "raw":
            raise ValueError(
                "Alpaca SIP canonical sync currently requires adjustment='raw'. "
                "Use integrity/feed-delta commands for adjusted comparison checks."
            )

        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{storage_path}' must be within data_root '{self.data_root}'"
            )

        self.storage_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(
        cls,
        *,
        storage_path: Path | None = None,
        manifest_manager: ManifestManager | None = None,
        data_root: Path | None = None,
        request_chunk_size: int = 200,
        request_interval_seconds: float = 0.0,
        feed: str | None = None,
        adjustment: str = "raw",
    ) -> AlpacaSIPSyncManager:
        """Build a manager from standard Alpaca environment variables."""
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for Alpaca SIP sync")

        api_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca credentials required: set ALPACA_API_KEY_ID and " "ALPACA_API_SECRET_KEY"
            )

        resolved_data_root = Path(data_root or cls.DEFAULT_DATA_ROOT)
        resolved_storage = Path(
            storage_path or os.getenv("ALPACA_SIP_STORAGE_PATH") or cls.DEFAULT_STORAGE_PATH
        )
        resolved_manifest_manager = manifest_manager or ManifestManager(
            data_root=resolved_data_root
        )
        client = cast(Any, StockHistoricalDataClient)(api_key=api_key, secret_key=secret_key)
        resolved_feed = feed if feed is not None else os.getenv("ALPACA_DATA_FEED") or "sip"

        return cls(
            client=client,
            storage_path=resolved_storage,
            manifest_manager=resolved_manifest_manager,
            data_root=resolved_data_root,
            request_chunk_size=request_chunk_size,
            request_interval_seconds=request_interval_seconds,
            feed=resolved_feed,
            adjustment=adjustment,
        )

    def full_sync(
        self,
        symbols: Sequence[str],
        *,
        start_year: int,
        end_year: int | None = None,
    ) -> SyncManifest:
        """Sync yearly daily-bar partitions for the requested symbols."""
        sync_started_at = datetime.datetime.now(datetime.UTC)
        normalized_symbols = self._normalize_symbols(symbols)
        resolved_end_year = end_year or datetime.datetime.now(datetime.UTC).year
        self._validate_year_range(start_year, resolved_end_year)

        years = list(range(start_year, resolved_end_year + 1))
        logger.info(
            "Starting Alpaca SIP full sync",
            extra={
                "event": "alpaca_sip.sync.full.start",
                "dataset": self.DATASET_NAME,
                "start_year": start_year,
                "end_year": resolved_end_year,
                "symbol_count": len(normalized_symbols),
            },
        )

        sync_id = self._build_sync_id(normalized_symbols, start_year, resolved_end_year)
        output_dir = self.storage_path / "snapshots" / sync_id
        file_paths: list[str] = []
        partition_dates: list[tuple[datetime.date, datetime.date]] = []
        row_count = 0
        with self.manifest_manager.acquire_lock(
            self.DATASET_NAME,
            writer_id=f"alpaca-sip-sync:{os.getpid()}",
            timeout_seconds=60.0,
        ) as lock_token:
            self._check_disk_space(estimated_rows=len(years) * len(normalized_symbols) * 252)
            for year in years:
                partition = self.sync_year_partition(
                    normalized_symbols, year, output_dir=output_dir
                )
                file_paths.append(str(partition.path.relative_to(self.data_root)))
                row_count += partition.row_count
                if partition.start_date is not None and partition.end_date is not None:
                    partition_dates.append((partition.start_date, partition.end_date))

            if row_count == 0 or not partition_dates:
                raise ValueError("No Alpaca SIP rows returned; manifest not updated")

            manifest = self._create_manifest(
                file_paths=file_paths,
                row_count=row_count,
                start_date=min(start for start, _end in partition_dates),
                end_date=max(end for _start, end in partition_dates),
                symbols=normalized_symbols,
                sync_started_at=sync_started_at,
                sync_finished_at=datetime.datetime.now(datetime.UTC),
            )
            self.manifest_manager.save_manifest(manifest, lock_token)

        logger.info(
            "Alpaca SIP full sync completed",
            extra={
                "event": "alpaca_sip.sync.full.complete",
                "dataset": self.DATASET_NAME,
                "row_count": row_count,
                "file_count": len(file_paths),
            },
        )
        return manifest

    @classmethod
    def estimate_full_sync(
        cls,
        symbols: Sequence[str],
        *,
        start_year: int,
        end_year: int | None = None,
        request_chunk_size: int = 200,
        request_interval_seconds: float = 0.0,
        requests_per_minute: int | None = None,
        avg_trading_days_per_year: int = 252,
    ) -> AlpacaSIPSyncEstimate:
        """Estimate request volume, rows, storage, and minimum sync duration.

        This is intentionally offline. It lets operators choose chunking and
        throttling before spending live API quota on a large SIP sync.
        """
        if request_chunk_size < 1:
            raise ValueError("request_chunk_size must be >= 1")
        if request_interval_seconds < 0:
            raise ValueError("request_interval_seconds must be >= 0")
        if requests_per_minute is not None and requests_per_minute < 1:
            raise ValueError("requests_per_minute must be >= 1")
        if avg_trading_days_per_year < 1:
            raise ValueError("avg_trading_days_per_year must be >= 1")

        normalized_symbols = cls._normalize_symbols(symbols)
        resolved_end_year = end_year or datetime.datetime.now(datetime.UTC).year
        cls._validate_year_range(start_year, resolved_end_year)
        year_count = resolved_end_year - start_year + 1
        chunks_per_year = (len(normalized_symbols) + request_chunk_size - 1) // request_chunk_size
        request_count = chunks_per_year * year_count
        estimated_rows = len(normalized_symbols) * year_count * avg_trading_days_per_year
        estimated_storage_bytes = estimated_rows * cls.BYTES_PER_ROW_ESTIMATE
        throttle_seconds = request_count * request_interval_seconds
        rate_limit_floor_seconds = (
            (request_count / requests_per_minute) * 60.0
            if requests_per_minute is not None
            else None
        )
        estimated_total_seconds = max(throttle_seconds, rate_limit_floor_seconds or 0.0)

        return AlpacaSIPSyncEstimate(
            symbol_count=len(normalized_symbols),
            start_year=start_year,
            end_year=resolved_end_year,
            year_count=year_count,
            request_chunk_size=request_chunk_size,
            request_count=request_count,
            estimated_rows=estimated_rows,
            estimated_storage_bytes=estimated_storage_bytes,
            request_interval_seconds=request_interval_seconds,
            requests_per_minute=requests_per_minute,
            estimated_throttle_seconds=round(throttle_seconds, 6),
            estimated_rate_limit_floor_seconds=(
                round(rate_limit_floor_seconds, 6) if rate_limit_floor_seconds is not None else None
            ),
            estimated_total_seconds=round(estimated_total_seconds, 6),
            symbol_set_hash=compute_symbol_set_hash(normalized_symbols),
        )

    def sync_year_partition(
        self,
        symbols: Sequence[str],
        year: int,
        *,
        output_dir: Path | None = None,
    ) -> SyncedPartition:
        """Fetch and write one yearly daily-bar partition."""
        normalized_symbols = self._normalize_symbols(symbols)
        self._validate_year_range(year, year)
        self._check_disk_space(estimated_rows=max(1, len(normalized_symbols) * 252))
        rows: list[dict[str, Any]] = []
        for chunk in self._chunks(normalized_symbols, self.request_chunk_size):
            page_token: str | None = None
            page_count = 0
            while True:
                page_count += 1
                if page_count > self.MAX_PAGES_PER_REQUEST:
                    raise RuntimeError(
                        "Alpaca SIP bars pagination exceeded "
                        f"{self.MAX_PAGES_PER_REQUEST} pages for year={year}"
                    )
                response = self._fetch_bars(chunk, year, page_token=page_token)
                rows.extend(self._rows_from_response(response))
                page_token = self._next_page_token_from_response(response)
                if page_token is None:
                    break
            if self.request_interval_seconds > 0:
                time.sleep(self.request_interval_seconds)

        df = self._rows_to_frame(rows, year)
        partition_dir = (output_dir or self.storage_path).resolve()
        if not partition_dir.is_relative_to(self.storage_path):
            raise ValueError(
                f"output_dir '{partition_dir}' must be within storage_path '{self.storage_path}'"
            )
        self._check_disk_space(estimated_rows=max(1, df.height))
        output_path = partition_dir / f"{year}.parquet"
        checksum = self._atomic_write_parquet(df, output_path)
        start_date, end_date = self._frame_date_bounds(df)

        logger.info(
            "Alpaca SIP partition synced",
            extra={
                "event": "alpaca_sip.sync.partition.complete",
                "dataset": self.DATASET_NAME,
                "year": year,
                "rows": df.height,
                "checksum": checksum[:16],
            },
        )

        return SyncedPartition(
            path=output_path,
            row_count=df.height,
            checksum=checksum,
            start_date=start_date,
            end_date=end_date,
        )

    def verify_integrity(self) -> list[str]:
        """Verify files, row count, and checksum against the current manifest."""
        errors: list[str] = []
        manifest = self.manifest_manager.load_manifest(self.DATASET_NAME)
        if manifest is None:
            return [f"No manifest found for {self.DATASET_NAME}"]

        partition_paths, path_errors = self._manifest_paths_for_verify(manifest)
        if path_errors:
            return path_errors

        total_rows = 0
        for path in partition_paths:
            if not path.exists():
                errors.append(f"Missing file: {path}")
                continue
            try:
                total_rows += int(pl.scan_parquet(path).select(pl.len()).collect().item())
            except (OSError, ValueError) as exc:
                errors.append(f"Cannot read {path}: {exc}")

        if errors:
            return errors

        computed_checksum = self._compute_combined_checksum_for_paths(partition_paths)
        if computed_checksum != manifest.checksum:
            errors.append(
                "Checksum mismatch: "
                f"manifest={manifest.checksum[:16]}..., "
                f"computed={computed_checksum[:16]}..."
            )

        if total_rows != manifest.row_count:
            errors.append(
                f"Row count mismatch: manifest={manifest.row_count}, computed={total_rows}"
            )

        return errors

    def _fetch_bars(
        self,
        symbols: Sequence[str],
        year: int,
        *,
        page_token: str | None = None,
    ) -> Any:
        """Fetch daily bars for one symbol chunk and year."""
        if not ALPACA_AVAILABLE:
            raise ImportError("alpaca-py package is required for Alpaca SIP sync")

        start = datetime.datetime(year, 1, 1, tzinfo=datetime.UTC)
        end = datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=datetime.UTC)
        request_fields: dict[str, Any] = {
            "symbol_or_symbols": list(symbols),
            "timeframe": cast(Any, TimeFrame).Day,
            "start": start,
            "end": end,
            "sort": cast(Any, Sort).ASC,
            "adjustment": cast(Any, Adjustment)(self.adjustment),
            "feed": cast(Any, DataFeed)(self.feed),
        }
        if page_token is not None:
            if not self._stock_bars_request_supports_page_token():
                raise RuntimeError(
                    "Alpaca bars response returned next_page_token, but the installed "
                    "alpaca-py StockBarsRequest cannot carry page_token. This SDK "
                    "version normally paginates inside get_stock_bars(); refusing to "
                    "silently truncate the SIP partition."
                )
            request_fields["page_token"] = page_token

        request = cast(Any, StockBarsRequest)(**request_fields)
        return self.client.get_stock_bars(request)

    @staticmethod
    def _stock_bars_request_supports_page_token() -> bool:
        """Return whether the installed alpaca-py request model accepts page tokens."""
        for field_attr in ("model_fields", "__fields__"):
            fields = getattr(cast(Any, StockBarsRequest), field_attr, None)
            if isinstance(fields, Mapping) and "page_token" in fields:
                return True
        return False

    @staticmethod
    def _next_page_token_from_response(response: Any) -> str | None:
        token = getattr(response, "next_page_token", None)
        if token is None and isinstance(response, Mapping):
            token = response.get("next_page_token")
        if token is None:
            return None
        token_text = str(token).strip()
        return token_text or None

    def _rows_from_response(self, response: Any) -> list[dict[str, Any]]:
        """Normalize SDK response shapes into row dictionaries."""
        data = getattr(response, "data", None)
        if data is None and isinstance(response, dict):
            data = response
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected Alpaca bars response type: {type(response).__name__}")

        rows: list[dict[str, Any]] = []
        for symbol, bars in data.items():
            if bars is None:
                continue
            for bar in bars:
                rows.append(self._row_from_bar(str(symbol), bar))
        return rows

    def _row_from_bar(self, fallback_symbol: str, bar: Any) -> dict[str, Any]:
        """Normalize one Alpaca bar object or raw dictionary."""
        symbol = self._get_bar_field(bar, "symbol", "S") or fallback_symbol
        timestamp = self._normalize_timestamp(self._get_bar_field(bar, "timestamp", "t"))
        close = self._required_float(bar, "close", "c")

        return {
            "date": timestamp.date(),
            "symbol": str(symbol).upper().strip(),
            "open": self._required_float(bar, "open", "o"),
            "high": self._required_float(bar, "high", "h"),
            "low": self._required_float(bar, "low", "l"),
            "close": close,
            "volume": self._required_float(bar, "volume", "v"),
            "trade_count": self._optional_float(bar, "trade_count", "n"),
            "vwap": self._optional_float(bar, "vwap", "vw"),
            "adj_close": None,
            "ret": None,
        }

    def _rows_to_frame(self, rows: list[dict[str, Any]], year: int) -> pl.DataFrame:
        """Convert normalized rows into the canonical local SIP schema."""
        if not rows:
            return pl.DataFrame(schema=ALPACA_SIP_SCHEMA)

        df = pl.DataFrame(rows, schema=ALPACA_SIP_SCHEMA)
        start = datetime.date(year, 1, 1)
        end = datetime.date(year, 12, 31)
        return (
            df.filter((pl.col("date") >= start) & (pl.col("date") <= end))
            .with_columns(pl.col("symbol").str.to_uppercase())
            .unique(subset=["date", "symbol"], keep="last", maintain_order=True)
            .sort(["date", "symbol"])
            .select(list(ALPACA_SIP_COLUMNS))
        )

    @staticmethod
    def _get_bar_field(bar: Any, primary_name: str, fallback_name: str) -> Any:
        if isinstance(bar, dict):
            value = bar.get(primary_name)
            return value if value is not None else bar.get(fallback_name)
        value = getattr(bar, primary_name, None)
        return value if value is not None else getattr(bar, fallback_name, None)

    def _required_float(self, bar: Any, primary_name: str, fallback_name: str) -> float:
        value = self._get_bar_field(bar, primary_name, fallback_name)
        if value is None:
            raise ValueError(f"Alpaca bar missing required field '{primary_name}'")
        return float(value)

    def _optional_float(
        self,
        bar: Any,
        primary_name: str,
        fallback_name: str,
    ) -> float | None:
        value = self._get_bar_field(bar, primary_name, fallback_name)
        return None if value is None else float(value)

    @staticmethod
    def _normalize_timestamp(value: Any) -> datetime.datetime:
        if isinstance(value, datetime.datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=datetime.UTC)
            return value.astimezone(datetime.UTC)
        if isinstance(value, str):
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.UTC)
            return parsed.astimezone(datetime.UTC)
        raise ValueError(f"Unsupported Alpaca timestamp value: {value!r}")

    @staticmethod
    def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            value = symbol.upper().strip()
            if not value:
                continue
            if value not in seen:
                normalized.append(value)
                seen.add(value)

        if not normalized:
            raise ValueError("symbols must contain at least one non-empty symbol")
        return sorted(normalized)

    @staticmethod
    def _validate_year_range(start_year: int, end_year: int) -> None:
        spec = get_provider_spec(ProviderType.ALPACA_SIP)
        history_start = spec.capabilities.history_start
        if history_start is not None and start_year < history_start.year:
            raise ValueError(
                "Alpaca SIP historical coverage starts around " f"{history_start.isoformat()}"
            )
        if end_year < start_year:
            raise ValueError("end_year must be >= start_year")
        current_year = datetime.datetime.now(datetime.UTC).year
        if end_year > current_year:
            raise ValueError("end_year cannot be in the future")

    @staticmethod
    def _chunks(symbols: Sequence[str], chunk_size: int) -> list[list[str]]:
        return [list(symbols[i : i + chunk_size]) for i in range(0, len(symbols), chunk_size)]

    def _build_sync_id(
        self,
        symbols: Sequence[str],
        start_year: int,
        end_year: int,
    ) -> str:
        """Build a deterministic, filesystem-safe snapshot id for one sync input set."""
        params_hash = hashlib.sha256(
            "|".join(
                [
                    ",".join(symbols),
                    str(start_year),
                    str(end_year),
                    self.feed,
                    self.adjustment,
                ]
            ).encode()
        ).hexdigest()[:12]
        return params_hash

    @staticmethod
    def _frame_date_bounds(df: pl.DataFrame) -> tuple[datetime.date | None, datetime.date | None]:
        """Return actual min/max data dates for a partition."""
        if df.is_empty():
            return None, None

        row = df.select(
            [
                pl.col("date").min().alias("start_date"),
                pl.col("date").max().alias("end_date"),
            ]
        ).row(0, named=True)
        return cast(datetime.date, row["start_date"]), cast(datetime.date, row["end_date"])

    def _atomic_write_parquet(self, df: pl.DataFrame, target_path: Path) -> str:
        """Write a Parquet file atomically."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_suffix(".parquet.tmp")
        try:
            df.write_parquet(temp_path)
            checksum = self._compute_checksum_and_fsync(temp_path)
            temp_path.replace(target_path)
            self._fsync_directory(target_path.parent)
            return checksum
        except OSError as exc:
            if exc.errno == 28:
                raise DiskSpaceError(f"Disk full writing {target_path}") from exc
            raise
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _compute_checksum(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _compute_checksum_and_fsync(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "r+b") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                hasher.update(chunk)
            os.fsync(handle.fileno())
        return hasher.hexdigest()

    def _compute_combined_checksum(self, file_paths: Sequence[str]) -> str:
        return self._compute_combined_checksum_for_paths(
            [self._resolve_manifest_path(Path(path)) for path in file_paths]
        )

    def _compute_combined_checksum_for_paths(self, paths: Sequence[Path]) -> str:
        hasher = hashlib.sha256()
        for path in sorted(paths, key=str):
            if path.exists():
                hasher.update(self._compute_checksum(path).encode())
        return hasher.hexdigest()

    def _manifest_paths_for_verify(self, manifest: SyncManifest) -> tuple[list[Path], list[str]]:
        """Resolve manifest file paths and reject anything outside storage_path."""
        paths: list[Path] = []
        errors: list[str] = []
        for path_str in manifest.file_paths:
            resolved = self._resolve_manifest_path(Path(path_str))
            if not resolved.is_relative_to(self.storage_path):
                errors.append(
                    f"Manifest path outside storage_path: {path_str} "
                    f"(resolved: {resolved}, storage_path: {self.storage_path})"
                )
                continue
            paths.append(resolved)
        return paths, errors

    def _resolve_manifest_path(self, path: Path) -> Path:
        """Resolve manifest paths without depending on process working directory."""
        if path.is_absolute():
            return path.resolve()

        if len(path.parts) == 1:
            return (self.storage_path / path).resolve()

        if path.parts[0] == self.data_root.name:
            return (self.data_root.parent / path).resolve()

        if path.parts[0] == "alpaca":
            return (self.data_root / path).resolve()

        return (self.storage_path / path).resolve()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _create_manifest(
        self,
        *,
        file_paths: list[str],
        row_count: int,
        start_date: datetime.date,
        end_date: datetime.date,
        symbols: Sequence[str],
        sync_started_at: datetime.datetime,
        sync_finished_at: datetime.datetime,
    ) -> SyncManifest:
        checksum = self._compute_combined_checksum(file_paths)
        spec = get_provider_spec(ProviderType.ALPACA_SIP)
        query_hash = hashlib.sha256(
            "|".join(
                [
                    self.DATASET_NAME,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    self.feed,
                    self.adjustment,
                    ",".join(symbols),
                ]
            ).encode()
        ).hexdigest()

        return SyncManifest(
            dataset=self.DATASET_NAME,
            sync_timestamp=datetime.datetime.now(datetime.UTC),
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            checksum=checksum,
            schema_version="v1.0.0",
            wrds_query_hash=query_hash,
            file_paths=file_paths,
            validation_status="passed",
            provider_id=spec.provider_id.value,
            provider_version=spec.provider_version,
            source_feed=self.feed,
            adjustment_mode=self.adjustment,
            symbol_set_hash=compute_symbol_set_hash(list(symbols)),
            sync_started_at=sync_started_at,
            sync_finished_at=sync_finished_at,
        )

    def _check_disk_space(self, estimated_rows: int) -> None:
        required_bytes = max(1, estimated_rows) * self.BYTES_PER_ROW_ESTIMATE * 2
        status = self.manifest_manager.check_disk_space(required_bytes)
        if status.level == "warning":
            logger.warning("Alpaca SIP sync disk warning: %s", status.message)
        elif status.level == "critical":
            logger.critical("Alpaca SIP sync disk critical: %s", status.message)
            raise DiskSpaceError(status.message)


class SyncedPartition:
    """Metadata for one synced partition."""

    def __init__(
        self,
        *,
        path: Path,
        row_count: int,
        checksum: str,
        start_date: datetime.date | None,
        end_date: datetime.date | None,
    ) -> None:
        self.path = path
        self.row_count = row_count
        self.checksum = checksum
        self.start_date = start_date
        self.end_date = end_date

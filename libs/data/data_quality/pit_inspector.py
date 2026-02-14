"""Point-in-Time data inspector for look-ahead bias detection.

Queries market data Parquet files (``data/adjusted/YYYY-MM-DD/{SYMBOL}.parquet``)
to show what data was available as of a given "knowledge date". This enables
researchers to validate that backtests do not suffer from look-ahead bias.

Key design:
    - Per-partition DuckDB registration (one table per run date) for reliable
      run_date tagging (no ``filename`` virtual column dependency).
    - Dedup by market date: latest eligible run_date wins (reprocessed snapshots
      supersede older ones).
    - Look-ahead detection: checks both future partitions and contaminated
      historical partitions (future-dated market data in past run-date dirs).
    - Staleness in trading days via ``ExchangeCalendarAdapter("XNYS")``.

TODO(perf): For large datasets (1000+ partitions), consider filtering
    available_partitions to only those within ``lookback_days`` range,
    or using DuckDB glob patterns instead of per-partition registration.
    Contamination check can also be scoped to avoid O(N) overhead.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from libs.data.data_quality.validation import is_valid_date_partition
from libs.duckdb_catalog import DuckDBCatalog

logger = logging.getLogger(__name__)

# Input validation
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9]{1,10}$")
_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_]+$")

MAX_LOOKBACK_DAYS = 3650  # 10 years


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class PITDataPoint:
    """Single data point tagged with its partition run date."""

    market_date: datetime.date
    run_date: datetime.date
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str  # "adjusted" or "quarantine"


@dataclass
class PITLookupResult:
    """Result of a point-in-time lookup for a single ticker."""

    ticker: str
    knowledge_date: datetime.date
    data_available: list[PITDataPoint]
    data_future: list[PITDataPoint]
    has_look_ahead_risk: bool
    has_contaminated_historical: bool
    latest_available_date: datetime.date | None
    days_stale: int | None
    total_rows_available: int
    future_partition_count: int


# ============================================================================
# Helpers
# ============================================================================


def _safe_table_name(prefix: str, *parts: str) -> str:
    """Build a DuckDB table name from components, sanitizing each part.

    Raises ValueError if any part contains non-identifier characters.
    """
    sanitized = [p.replace("-", "_").replace(".", "_") for p in parts]
    for s in sanitized:
        if not _SAFE_IDENT.match(s):
            raise ValueError(f"Unsafe identifier component: {s!r}")
    return f"{prefix}_{'_'.join(sanitized)}"


def _compute_trading_days_stale(
    knowledge_date: datetime.date,
    latest_available_date: datetime.date | None,
) -> int | None:
    """Compute staleness in trading days (XNYS calendar).

    Returns None if latest_available_date is None.
    """
    if latest_available_date is None:
        return None
    if latest_available_date >= knowledge_date:
        return 0

    try:
        # Lazy import: exchange_calendars is optional; fallback to calendar days below.
        from libs.data.data_quality.types import ExchangeCalendarAdapter

        cal = ExchangeCalendarAdapter("XNYS")
        trading_days = cal.trading_days_between(latest_available_date, knowledge_date)
        # Exclude the latest_available_date itself (staleness = gap days)
        return max(0, len(trading_days) - 1)
    except Exception:
        logger.warning(
            "exchange_calendar_unavailable, falling back to calendar days",
            exc_info=True,
        )
        return (knowledge_date - latest_available_date).days


def _extract_rows(
    result_df: object,  # polars DataFrame from DuckDB
) -> list[dict[str, object]]:
    """Extract rows as dicts from a Polars DataFrame, handling API variations."""
    # polars iter_rows(named=True) returns list of dicts
    return list(result_df.iter_rows(named=True))  # type: ignore[attr-defined]


def _safe_float(val: object, default: float = 0.0) -> float:
    """Null-safe float conversion for Parquet column values."""
    return float(val) if val is not None else default  # type: ignore[arg-type]


def _safe_int(val: object, default: int = 0) -> int:
    """Null-safe int conversion for Parquet column values."""
    return int(val) if val is not None else default  # type: ignore[call-overload]


# ============================================================================
# PITInspector
# ============================================================================


class PITInspector:
    """Point-in-Time data inspector.

    Reads ``data/adjusted/YYYY-MM-DD/{SYMBOL}.parquet`` to determine what
    data was available as of a given knowledge date.

    Authorization is enforced at the page level, not here.
    """

    def __init__(self, data_dir: Path = Path("data")) -> None:
        self._data_dir = data_dir
        self._adjusted_dir = data_dir / "adjusted"

    def get_available_tickers(self) -> list[str]:
        """Scan adjusted data directory for all available ticker symbols."""
        if not self._adjusted_dir.exists():
            return []
        tickers: set[str] = set()
        for date_dir in self._adjusted_dir.iterdir():
            if date_dir.is_dir() and is_valid_date_partition(date_dir.name):
                for parquet_file in date_dir.glob("*.parquet"):
                    tickers.add(parquet_file.stem)
        return sorted(tickers)

    def get_date_range(self) -> tuple[datetime.date | None, datetime.date | None]:
        """Get min and max run dates from adjusted data directory."""
        if not self._adjusted_dir.exists():
            return (None, None)
        dates = [
            datetime.date.fromisoformat(d.name)
            for d in self._adjusted_dir.iterdir()
            if d.is_dir() and is_valid_date_partition(d.name)
        ]
        return (min(dates), max(dates)) if dates else (None, None)

    def lookup(
        self,
        ticker: str,
        knowledge_date: datetime.date,
        lookback_days: int = 365,
    ) -> PITLookupResult:
        """Look up what data was available for ticker as-of knowledge_date.

        Args:
            ticker: Stock symbol (alphanumeric, 1-10 chars).
            knowledge_date: The "as of" date (must be <= today).
            lookback_days: Calendar days to look back (1-3650).

        Returns:
            PITLookupResult with available data, future data, and risk flags.

        Raises:
            ValueError: On invalid inputs.
        """
        # Validate inputs
        if not SYMBOL_PATTERN.match(ticker):
            raise ValueError(
                f"Invalid ticker: {ticker!r} (must be 1-10 alphanumeric chars)"
            )
        if knowledge_date > datetime.datetime.now(datetime.UTC).date():
            raise ValueError(
                f"knowledge_date {knowledge_date} is in the future"
            )
        if not (1 <= lookback_days <= MAX_LOOKBACK_DAYS):
            raise ValueError(
                f"lookback_days must be 1-{MAX_LOOKBACK_DAYS}, got {lookback_days}"
            )

        empty_result = PITLookupResult(
            ticker=ticker,
            knowledge_date=knowledge_date,
            data_available=[],
            data_future=[],
            has_look_ahead_risk=False,
            has_contaminated_historical=False,
            latest_available_date=None,
            days_stale=None,
            total_rows_available=0,
            future_partition_count=0,
        )

        if not self._adjusted_dir.exists():
            return empty_result

        # Discover partitions (filesystem, outside DuckDB)
        available_partitions: list[tuple[str, datetime.date]] = []
        future_partitions: list[tuple[str, datetime.date]] = []

        knowledge_iso = knowledge_date.isoformat()
        for d in sorted(self._adjusted_dir.iterdir()):
            if not (d.is_dir() and is_valid_date_partition(d.name)):
                continue
            parquet_path = d / f"{ticker}.parquet"
            if not parquet_path.exists():
                continue
            run_dt = datetime.date.fromisoformat(d.name)
            if d.name <= knowledge_iso:
                available_partitions.append((str(parquet_path), run_dt))
            else:
                future_partitions.append((str(parquet_path), run_dt))

        future_partition_count = len(future_partitions)

        if not available_partitions and not future_partitions:
            return empty_result

        # All DuckDB operations inside a single with block
        earliest_market = (
            knowledge_date - datetime.timedelta(days=lookback_days)
        ).isoformat()

        raw_rows: dict[datetime.date, PITDataPoint] = {}
        has_contaminated_historical = False
        all_future_rows: list[PITDataPoint] = []

        with DuckDBCatalog() as catalog:
            # Query available partitions and check for contamination in one pass
            for path, run_dt in available_partitions:
                table_name = _safe_table_name("avail", run_dt.isoformat())
                catalog.register_table(table_name, path)

                # Fetch available data within lookback window (CTE avoids
                # repeating CAST(date AS DATE) in SELECT and WHERE)
                result_df = catalog.query(
                    f"WITH src AS ("  # noqa: S608
                    f"  SELECT *, CAST(date AS DATE) AS market_date "
                    f"  FROM {table_name}"
                    f") SELECT * FROM src "
                    f"WHERE market_date >= ? AND market_date <= ? "
                    f"ORDER BY market_date DESC",
                    params=[earliest_market, knowledge_iso],
                )
                for row in _extract_rows(result_df):
                    mdate = row["market_date"]
                    if isinstance(mdate, str):
                        mdate = datetime.date.fromisoformat(mdate)
                    market_date: datetime.date = mdate  # type: ignore[assignment]
                    point = PITDataPoint(
                        market_date=market_date,
                        run_date=run_dt,
                        open=_safe_float(row.get("open")),
                        high=_safe_float(row.get("high")),
                        low=_safe_float(row.get("low")),
                        close=_safe_float(row.get("close")),
                        volume=_safe_int(row.get("volume")),
                        source="adjusted",
                    )
                    # Dedup: keep row from LATEST eligible run_date per market date
                    if (
                        market_date not in raw_rows
                        or run_dt > raw_rows[market_date].run_date
                    ):
                        raw_rows[market_date] = point

                # Contamination check: reuse same CTE pattern
                if not has_contaminated_historical:
                    anomaly_df = catalog.query(
                        f"WITH src AS ("  # noqa: S608
                        f"  SELECT CAST(date AS DATE) AS market_date "
                        f"  FROM {table_name}"
                        f") SELECT COUNT(*) AS cnt FROM src "
                        f"WHERE market_date > ?",
                        params=[knowledge_iso],
                    )
                    if anomaly_df.row(0)[0] > 0:  # type: ignore[operator]
                        has_contaminated_historical = True

            # Step 4c: Sample future partitions (up to 5)
            for path, run_dt in future_partitions[:5]:
                table_name = _safe_table_name("future", run_dt.isoformat())
                catalog.register_table(table_name, path)
                result_df = catalog.query(
                    f"WITH src AS ("  # noqa: S608
                    f"  SELECT *, CAST(date AS DATE) AS market_date "
                    f"  FROM {table_name}"
                    f") SELECT * FROM src "
                    f"WHERE market_date > ? "
                    f"ORDER BY market_date ASC LIMIT 20",
                    params=[knowledge_iso],
                )
                for row in _extract_rows(result_df):
                    mdate = row["market_date"]
                    if isinstance(mdate, str):
                        mdate = datetime.date.fromisoformat(mdate)
                    market_date = mdate  # type: ignore[assignment]
                    all_future_rows.append(
                        PITDataPoint(
                            market_date=market_date,
                            run_date=run_dt,
                            open=_safe_float(row.get("open")),
                            high=_safe_float(row.get("high")),
                            low=_safe_float(row.get("low")),
                            close=_safe_float(row.get("close")),
                            volume=_safe_int(row.get("volume")),
                            source="adjusted",
                        )
                    )

        # Step 5: Compute look-ahead risk (after with block)
        has_look_ahead_risk = (
            future_partition_count > 0 or has_contaminated_historical
        )

        # Build result
        all_available = sorted(
            raw_rows.values(), key=lambda p: p.market_date, reverse=True
        )
        latest_available = all_available[0].market_date if all_available else None
        days_stale = _compute_trading_days_stale(knowledge_date, latest_available)

        return PITLookupResult(
            ticker=ticker,
            knowledge_date=knowledge_date,
            data_available=all_available,
            data_future=all_future_rows,
            has_look_ahead_risk=has_look_ahead_risk,
            has_contaminated_historical=has_contaminated_historical,
            latest_available_date=latest_available,
            days_stale=days_stale,
            total_rows_available=len(all_available),
            future_partition_count=future_partition_count,
        )


__all__ = [
    "MAX_LOOKBACK_DAYS",
    "PITDataPoint",
    "PITInspector",
    "PITLookupResult",
    "SYMBOL_PATTERN",
]

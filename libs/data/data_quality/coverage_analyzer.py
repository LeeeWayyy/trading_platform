"""Data coverage analyzer for symbol x date heatmap visualization.

Scans adjusted and quarantine Parquet directories to build a coverage matrix
showing data completeness across the ticker universe. Uses Polars direct reads
for efficient per-file date extraction and NYSE trading calendar to distinguish
missing data from market-closed dates.

Key design:
    - Polars ``read_parquet(columns=["date"])`` for lightweight per-file scanning
      (avoids O(symbols * date_dirs) DuckDB view registration overhead).
    - Symbol derived from ``pq_file.stem`` (filename), not from Parquet column.
    - NYSE (XNYS) calendar classifies each date as trading/non-trading.
    - 200-symbol cap with truncation indicator for UI.
    - 180-day hard cap for daily resolution (auto-upgrades to weekly).
    - Per-file fault tolerance: corrupt files logged and skipped.

TODO(perf): For production-scale datasets (500+ partitions, 200 symbols),
    consider scanning partitions in reverse chronological order with a
    "newest-wins" cache to avoid redundant reads of already-seen (symbol, date)
    pairs. See also: centralize ExchangeCalendarAdapter usage into a shared
    utility in ``libs/data/data_quality/`` to prevent logic drift between
    PITInspector and CoverageAnalyzer.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

import polars as pl

from libs.data.data_quality.validation import is_valid_date_partition

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 200
MAX_DAILY_DAYS = 180

# Symbol validation: alphanumeric, 1-10 chars (matches PIT inspector).
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9]{1,10}$")


# ============================================================================
# Data Classes
# ============================================================================


class CoverageStatus(str, Enum):
    """Status of a single symbol-date cell in the coverage matrix."""

    COMPLETE = "complete"
    MISSING = "missing"
    SUSPICIOUS = "suspicious"
    NO_EXPECTATION = "no_expectation"


@dataclass
class CoverageGap:
    """A contiguous run of missing trading days for a symbol."""

    symbol: str
    start_date: datetime.date
    end_date: datetime.date
    gap_days: int


@dataclass
class CoverageSummary:
    """Aggregate statistics for the coverage matrix."""

    total_expected: int
    total_present: int
    total_missing: int
    total_suspicious: int
    coverage_pct: float
    gaps: list[CoverageGap]


@dataclass
class CoverageMatrix:
    """Full coverage analysis result."""

    symbols: list[str]
    dates: list[datetime.date]
    matrix: list[list[CoverageStatus]]
    summary: CoverageSummary
    truncated: bool
    total_symbol_count: int
    effective_resolution: Literal["daily", "weekly", "monthly"]
    notices: list[str] = field(default_factory=list)
    skipped_file_count: int = 0


# ============================================================================
# Helpers
# ============================================================================


def _coerce_date(raw: object) -> datetime.date | None:
    """Defensively coerce a Parquet date value to ``datetime.date``.

    Handles ``datetime.datetime``, ``datetime.date``, and ISO-format strings.
    Returns ``None`` for unrecognized or ``None`` values.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            return None
    return None


# ============================================================================
# CoverageAnalyzer
# ============================================================================


class CoverageAnalyzer:
    """Analyze data coverage across symbols and dates.

    Authorization is enforced at the page level, not here.
    """

    def __init__(self, data_dir: Path = Path("data")) -> None:
        self._adjusted_dir = data_dir / "adjusted"
        self._quarantine_dir = data_dir / "quarantine"

    def get_available_tickers(self) -> list[str]:
        """Scan adjusted data directory for all available ticker symbols."""
        if not self._adjusted_dir.exists():
            return []
        tickers: set[str] = set()
        for date_dir in self._adjusted_dir.iterdir():
            if date_dir.is_dir():
                for pq in date_dir.glob("*.parquet"):
                    tickers.add(pq.stem)
        return sorted(tickers)

    def _discover_date_range(
        self,
    ) -> tuple[datetime.date | None, datetime.date | None]:
        """Scan partition directory names for min/max run dates."""
        if not self._adjusted_dir.exists():
            return (None, None)
        dates: list[datetime.date] = []
        for d in self._adjusted_dir.iterdir():
            if d.is_dir() and is_valid_date_partition(d.name):
                dates.append(datetime.date.fromisoformat(d.name))
        return (min(dates), max(dates)) if dates else (None, None)

    def analyze(
        self,
        symbols: list[str] | None = None,
        start_date: datetime.date | None = None,
        end_date: datetime.date | None = None,
        resolution: Literal["daily", "weekly", "monthly"] = "monthly",
    ) -> CoverageMatrix:
        """Build a coverage matrix for the given parameters.

        Args:
            symbols: Ticker symbols to include (None = all discovered, capped at 200).
            start_date: Start of date range (None = earliest partition).
            end_date: End of date range (None = latest partition).
            resolution: Time granularity for the matrix.

        Returns:
            CoverageMatrix with status for each (symbol, date) cell.
        """
        empty_summary = CoverageSummary(
            total_expected=0,
            total_present=0,
            total_missing=0,
            total_suspicious=0,
            coverage_pct=0.0,
            gaps=[],
        )

        # Materialize date bounds
        if start_date is None or end_date is None:
            data_min, data_max = self._discover_date_range()
            if data_min is None or data_max is None:
                return CoverageMatrix(
                    symbols=[],
                    dates=[],
                    matrix=[],
                    summary=empty_summary,
                    truncated=False,
                    total_symbol_count=0,
                    effective_resolution=resolution,
                    notices=["No adjusted data directory found"],
                )
            effective_start = start_date or data_min
            effective_end = end_date or data_max
        else:
            effective_start, effective_end = start_date, end_date

        if effective_start > effective_end:
            raise ValueError(
                f"start_date ({effective_start}) must be <= end_date "
                f"({effective_end})"
            )

        # Build target symbol set (validate to prevent path traversal)
        if symbols is None:
            all_symbols = self.get_available_tickers()
        else:
            all_symbols = sorted(
                s for s in symbols if _SYMBOL_PATTERN.match(s)
            )
        total_symbol_count = len(all_symbols)
        target_symbols_list = all_symbols[:MAX_SYMBOLS]
        target_symbols = set(target_symbols_list)
        truncated = total_symbol_count > MAX_SYMBOLS

        notices: list[str] = []
        if truncated:
            notices.append(
                f"Showing first {MAX_SYMBOLS} of {total_symbol_count} symbols "
                f"(alphabetical). Use the symbol filter to narrow results."
            )

        # Determine effective resolution (auto-upgrade daily if range too long)
        effective_resolution = resolution
        range_days = (effective_end - effective_start).days + 1
        if resolution == "daily" and range_days > MAX_DAILY_DAYS:
            effective_resolution = "weekly"
            notices.append(
                f"Date range exceeds {MAX_DAILY_DAYS} days; switched to weekly "
                f"view. Use a shorter range for daily granularity."
            )

        # Build calendar date axis
        trading_days_set: set[datetime.date] = set()
        try:
            from libs.data.data_quality.types import ExchangeCalendarAdapter

            cal = ExchangeCalendarAdapter("XNYS")
            trading_days_list = cal.trading_days_between(
                effective_start, effective_end
            )
            trading_days_set = set(trading_days_list)
        except Exception:
            logger.warning(
                "exchange_calendar_unavailable, treating all weekdays as trading days",
                exc_info=True,
            )
            d = effective_start
            while d <= effective_end:
                if d.weekday() < 5:
                    trading_days_set.add(d)
                d += datetime.timedelta(days=1)

        all_dates = []
        d = effective_start
        while d <= effective_end:
            all_dates.append(d)
            d += datetime.timedelta(days=1)

        # Scan adjusted data
        presence_set: set[tuple[str, datetime.date]] = set()
        skipped_files: list[str] = []

        if self._adjusted_dir.exists():
            for date_dir in sorted(self._adjusted_dir.iterdir()):
                if not date_dir.is_dir() or not is_valid_date_partition(
                    date_dir.name
                ):
                    continue
                # Targeted file checks: iterate over capped symbol set
                # instead of globbing all parquet files (O(200) vs O(10k+))
                for symbol in target_symbols:
                    pq_file = date_dir / f"{symbol}.parquet"
                    if not pq_file.exists():
                        continue
                    try:
                        df = pl.read_parquet(pq_file, columns=["date"])
                    except Exception:
                        logger.warning(
                            "coverage_scan_skip_file",
                            extra={
                                "file": str(pq_file),
                                "symbol": symbol,
                                "partition": date_dir.name,
                            },
                        )
                        skipped_files.append(str(pq_file))
                        continue
                    for raw_date in df["date"].unique().to_list():
                        market_date = _coerce_date(raw_date)
                        if market_date is None:
                            continue
                        if effective_start <= market_date <= effective_end:
                            presence_set.add((symbol, market_date))

        # Scan quarantine data
        quarantine_set: set[tuple[str, datetime.date]] = set()
        if self._quarantine_dir.exists():
            for date_dir in sorted(self._quarantine_dir.iterdir()):
                if not date_dir.is_dir() or not is_valid_date_partition(
                    date_dir.name
                ):
                    continue
                for symbol in target_symbols:
                    pq_file = date_dir / f"{symbol}.parquet"
                    if not pq_file.exists():
                        continue
                    try:
                        df = pl.read_parquet(pq_file, columns=["date"])
                    except Exception:
                        logger.warning(
                            "coverage_scan_skip_quarantine_file",
                            extra={
                                "file": str(pq_file),
                                "symbol": symbol,
                                "partition": date_dir.name,
                            },
                        )
                        skipped_files.append(str(pq_file))
                        continue
                    for raw_date in df["date"].unique().to_list():
                        market_date = _coerce_date(raw_date)
                        if market_date is None:
                            continue
                        if effective_start <= market_date <= effective_end:
                            quarantine_set.add((symbol, market_date))

        # Build daily matrix
        daily_matrix: list[list[CoverageStatus]] = []
        for symbol in target_symbols_list:
            row: list[CoverageStatus] = []
            for dt in all_dates:
                if dt not in trading_days_set:
                    row.append(CoverageStatus.NO_EXPECTATION)
                elif (symbol, dt) in quarantine_set:
                    row.append(CoverageStatus.SUSPICIOUS)
                elif (symbol, dt) in presence_set:
                    row.append(CoverageStatus.COMPLETE)
                else:
                    row.append(CoverageStatus.MISSING)
            daily_matrix.append(row)

        # Aggregate by resolution
        if effective_resolution == "daily":
            final_dates = all_dates
            final_matrix = daily_matrix
        elif effective_resolution == "weekly":
            final_dates, final_matrix = _aggregate_weekly(
                all_dates, daily_matrix
            )
        else:  # monthly
            final_dates, final_matrix = _aggregate_monthly(
                all_dates, daily_matrix
            )

        # Compute summary
        total_expected = 0
        total_present = 0
        total_missing = 0
        total_suspicious = 0
        for row in daily_matrix:
            for cell in row:
                if cell == CoverageStatus.NO_EXPECTATION:
                    continue
                total_expected += 1
                if cell == CoverageStatus.COMPLETE:
                    total_present += 1
                elif cell == CoverageStatus.MISSING:
                    total_missing += 1
                elif cell == CoverageStatus.SUSPICIOUS:
                    total_suspicious += 1

        coverage_pct = (
            (total_present / total_expected * 100.0) if total_expected > 0 else 0.0
        )

        # Identify gaps (contiguous missing trading days per symbol)
        gaps = _find_gaps(target_symbols_list, all_dates, daily_matrix, trading_days_set)

        summary = CoverageSummary(
            total_expected=total_expected,
            total_present=total_present,
            total_missing=total_missing,
            total_suspicious=total_suspicious,
            coverage_pct=coverage_pct,
            gaps=gaps,
        )

        if skipped_files:
            notices.append(
                f"{len(skipped_files)} file(s) could not be read "
                f"and were excluded from analysis"
            )

        return CoverageMatrix(
            symbols=target_symbols_list,
            dates=final_dates,
            matrix=final_matrix,
            summary=summary,
            truncated=truncated,
            total_symbol_count=total_symbol_count,
            effective_resolution=effective_resolution,
            notices=notices,
            skipped_file_count=len(skipped_files),
        )

    def export_coverage_report(
        self,
        matrix: CoverageMatrix,
        fmt: Literal["csv", "json"] = "csv",
    ) -> str:
        """Export coverage matrix as CSV or JSON string.

        Args:
            matrix: The coverage matrix to export.
            fmt: Output format.

        Returns:
            Formatted string (CSV or JSON).
        """
        if fmt == "csv":
            lines = ["symbol,date,status"]
            for sym_idx, symbol in enumerate(matrix.symbols):
                for dt_idx, dt in enumerate(matrix.dates):
                    status = matrix.matrix[sym_idx][dt_idx]
                    lines.append(f"{symbol},{dt.isoformat()},{status.value}")
            return "\n".join(lines)

        # JSON format
        gap_list = [
            {
                "symbol": g.symbol,
                "start_date": g.start_date.isoformat(),
                "end_date": g.end_date.isoformat(),
                "gap_days": g.gap_days,
            }
            for g in matrix.summary.gaps
        ]
        data = {
            "summary": {
                "total_expected": matrix.summary.total_expected,
                "total_present": matrix.summary.total_present,
                "total_missing": matrix.summary.total_missing,
                "total_suspicious": matrix.summary.total_suspicious,
                "coverage_pct": round(matrix.summary.coverage_pct, 2),
            },
            "gaps": gap_list,
            "resolution": matrix.effective_resolution,
            "truncated": matrix.truncated,
            "total_symbol_count": matrix.total_symbol_count,
            "notices": matrix.notices,
        }
        return json.dumps(data, indent=2)


# ============================================================================
# Aggregation Helpers
# ============================================================================


def _aggregate_status(statuses: list[CoverageStatus]) -> CoverageStatus:
    """Aggregate daily statuses into a single status using precedence rules.

    Precedence: MISSING > SUSPICIOUS > COMPLETE > NO_EXPECTATION.
    """
    has_trading = False
    has_missing = False
    has_suspicious = False
    has_complete = False

    for s in statuses:
        if s == CoverageStatus.MISSING:
            has_missing = True
            has_trading = True
        elif s == CoverageStatus.SUSPICIOUS:
            has_suspicious = True
            has_trading = True
        elif s == CoverageStatus.COMPLETE:
            has_complete = True
            has_trading = True

    if not has_trading:
        return CoverageStatus.NO_EXPECTATION
    if has_missing:
        return CoverageStatus.MISSING
    if has_suspicious:
        return CoverageStatus.SUSPICIOUS
    if has_complete:
        return CoverageStatus.COMPLETE
    return CoverageStatus.NO_EXPECTATION


def _aggregate_weekly(
    all_dates: list[datetime.date],
    daily_matrix: list[list[CoverageStatus]],
) -> tuple[list[datetime.date], list[list[CoverageStatus]]]:
    """Group daily data by ISO week (Monday-Sunday)."""
    if not all_dates:
        return [], [[] for _ in daily_matrix]

    # Group date indices by ISO week
    week_groups: list[tuple[datetime.date, list[int]]] = []
    current_week_label: datetime.date | None = None
    current_indices: list[int] = []

    for idx, d in enumerate(all_dates):
        # Monday of this ISO week
        monday = d - datetime.timedelta(days=d.weekday())
        if monday != current_week_label:
            if current_week_label is not None:
                week_groups.append((current_week_label, current_indices))
            current_week_label = monday
            current_indices = [idx]
        else:
            current_indices.append(idx)

    if current_week_label is not None:
        week_groups.append((current_week_label, current_indices))

    week_dates = [wl for wl, _ in week_groups]
    week_matrix: list[list[CoverageStatus]] = []
    for row in daily_matrix:
        week_row = [
            _aggregate_status([row[i] for i in indices])
            for _, indices in week_groups
        ]
        week_matrix.append(week_row)

    return week_dates, week_matrix


def _aggregate_monthly(
    all_dates: list[datetime.date],
    daily_matrix: list[list[CoverageStatus]],
) -> tuple[list[datetime.date], list[list[CoverageStatus]]]:
    """Group daily data by calendar month."""
    if not all_dates:
        return [], [[] for _ in daily_matrix]

    # Group date indices by (year, month)
    month_groups: list[tuple[datetime.date, list[int]]] = []
    current_month: tuple[int, int] | None = None
    current_indices: list[int] = []

    for idx, d in enumerate(all_dates):
        ym = (d.year, d.month)
        if ym != current_month:
            if current_month is not None:
                label = datetime.date(current_month[0], current_month[1], 1)
                month_groups.append((label, current_indices))
            current_month = ym
            current_indices = [idx]
        else:
            current_indices.append(idx)

    if current_month is not None:
        label = datetime.date(current_month[0], current_month[1], 1)
        month_groups.append((label, current_indices))

    month_dates = [ml for ml, _ in month_groups]
    month_matrix: list[list[CoverageStatus]] = []
    for row in daily_matrix:
        month_row = [
            _aggregate_status([row[i] for i in indices])
            for _, indices in month_groups
        ]
        month_matrix.append(month_row)

    return month_dates, month_matrix


# ============================================================================
# Gap Detection
# ============================================================================


def _find_gaps(
    symbols: list[str],
    all_dates: list[datetime.date],
    daily_matrix: list[list[CoverageStatus]],
    trading_days: set[datetime.date],
) -> list[CoverageGap]:
    """Find contiguous runs of missing trading days per symbol.

    Non-trading days between missing trading days do NOT break a gap.
    """
    gaps: list[CoverageGap] = []

    for sym_idx, symbol in enumerate(symbols):
        gap_start: datetime.date | None = None
        last_missing_trading: datetime.date | None = None
        gap_count = 0

        for dt_idx, dt in enumerate(all_dates):
            if dt not in trading_days:
                continue  # Skip non-trading days
            status = daily_matrix[sym_idx][dt_idx]
            if status == CoverageStatus.MISSING:
                if gap_start is None:
                    gap_start = dt
                last_missing_trading = dt
                gap_count += 1
            else:
                if gap_start is not None and gap_count > 0:
                    gaps.append(
                        CoverageGap(
                            symbol=symbol,
                            start_date=gap_start,
                            end_date=last_missing_trading or gap_start,
                            gap_days=gap_count,
                        )
                    )
                gap_start = None
                last_missing_trading = None
                gap_count = 0

        # Close any open gap at end
        if gap_start is not None and gap_count > 0:
            gaps.append(
                CoverageGap(
                    symbol=symbol,
                    start_date=gap_start,
                    end_date=last_missing_trading or gap_start,
                    gap_days=gap_count,
                )
            )

    # Sort by gap_days descending
    gaps.sort(key=lambda g: g.gap_days, reverse=True)
    return gaps


__all__ = [
    "CoverageAnalyzer",
    "CoverageGap",
    "CoverageMatrix",
    "CoverageStatus",
    "CoverageSummary",
    "MAX_DAILY_DAYS",
    "MAX_SYMBOLS",
]

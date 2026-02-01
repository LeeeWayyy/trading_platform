"""Universe and Forward Returns Providers for Quantile Analysis.

P6T10: Track 10 - Quantile & Attribution Analytics

Provides:
- UniverseProvider: PIT-safe index constituent provider
- ForwardReturnsProvider: Forward returns with trading calendar
- CRSPUnavailableError: Exception for missing CRSP data
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from config.settings import get_settings
from libs.data.data_providers.protocols import DataProviderError

if TYPE_CHECKING:
    import exchange_calendars as xcals  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


class CRSPUnavailableError(DataProviderError):
    """Raised when CRSP data is not configured or unavailable.

    Inherits from DataProviderError for consistent global error handling
    (HTTP 503 in web layer).
    """

    pass


class UniverseProvider:
    """PIT-safe index constituent provider.

    Provides survivorship-bias-free universe membership data.
    Uses CRSP constituent history or equivalent PIT-safe source.

    Example:
        provider = UniverseProvider(crsp_data_dir="data/crsp")
        sp500_today = provider.get_constituents("SP500", date.today())
    """

    def __init__(self, crsp_data_dir: str | None = None) -> None:
        """Initialize with CRSP data directory.

        Args:
            crsp_data_dir: Path to CRSP constituent data.
                          Falls back to settings.crsp_data_dir if None.

        Raises:
            CRSPUnavailableError: If CRSP data directory not configured.
        """
        settings = get_settings()
        resolved_dir = crsp_data_dir or settings.crsp_data_dir

        # Require explicit non-empty path to prevent silent CWD fallback
        if not resolved_dir:
            raise CRSPUnavailableError(
                "CRSP data directory not configured. Set CRSP_DATA_DIR in settings."
            )

        self._data_dir = Path(resolved_dir)

        if not self._data_dir.exists():
            raise CRSPUnavailableError(
                f"CRSP data directory not found: {self._data_dir}"
            )

        self._constituents_path = self._data_dir / "index_constituents.parquet"
        if not self._constituents_path.exists():
            raise CRSPUnavailableError(
                f"Index constituents file not found: {self._constituents_path}"
            )

    def get_constituents(
        self,
        universe_id: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Return [permno] for universe at as_of_date.

        Args:
            universe_id: Universe identifier (e.g., "SP500", "R1000").
            as_of_date: Date for which to get constituents.

        Returns:
            DataFrame with single column 'permno' (Int64).

        Raises:
            CRSPUnavailableError: If data cannot be loaded.
        """
        try:
            # Lazy scan with predicate pushdown
            lf = pl.scan_parquet(self._constituents_path)

            # Filter to universe and date
            # Schema: [date, universe_id, permno]
            result = (
                lf.filter(
                    (pl.col("universe_id") == universe_id)
                    & (pl.col("date") == as_of_date)
                )
                .select("permno")
                .collect()
            )

            return result

        except Exception as e:
            logger.error(
                "universe_constituents_load_failed",
                extra={
                    "universe_id": universe_id,
                    "as_of_date": str(as_of_date),
                    "error": str(e),
                },
                exc_info=True,
            )
            raise CRSPUnavailableError(f"Failed to load universe constituents: {e}") from e

    def get_constituents_range(
        self,
        universe_id: str,
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Return [date, permno] for all dates in range.

        Args:
            universe_id: Universe identifier (e.g., "SP500").
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            DataFrame with columns [date, permno].

        Raises:
            CRSPUnavailableError: If data cannot be loaded.
        """
        try:
            lf = pl.scan_parquet(self._constituents_path)

            result = (
                lf.filter(
                    (pl.col("universe_id") == universe_id)
                    & (pl.col("date") >= start_date)
                    & (pl.col("date") <= end_date)
                )
                .select(["date", "permno"])
                .collect()
            )

            return result

        except Exception as e:
            logger.error(
                "universe_constituents_range_failed",
                extra={
                    "universe_id": universe_id,
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "error": str(e),
                },
                exc_info=True,
            )
            raise CRSPUnavailableError(f"Failed to load universe range: {e}") from e


class ForwardReturnsProvider:
    """Provider for forward returns used in quantile analysis.

    CRITICAL: This is the source of truth for forward returns in T10.1.

    Uses CRSP daily stock file (dsf) or equivalent for daily returns,
    compounding over the holding period using trading calendar.

    Example:
        provider = ForwardReturnsProvider(crsp_data_dir="data/crsp")
        fwd_returns = provider.get_forward_returns_lazy(
            signal_dates=[date(2024, 1, 2)],
            permnos=[10001, 10002],
            skip_days=1,
            holding_period=20,
            calendar=calendar,
        ).collect()
    """

    def __init__(self, crsp_data_dir: str | None = None) -> None:
        """Initialize with CRSP data directory.

        Args:
            crsp_data_dir: Path to CRSP daily returns parquet files.
                          Falls back to settings.crsp_data_dir if None.

        Raises:
            CRSPUnavailableError: If CRSP data directory not configured.
        """
        settings = get_settings()
        resolved_dir = crsp_data_dir or settings.crsp_data_dir

        # Require explicit non-empty path to prevent silent CWD fallback
        if not resolved_dir:
            raise CRSPUnavailableError(
                "CRSP data directory not configured. Set CRSP_DATA_DIR in settings."
            )

        self._data_dir = Path(resolved_dir)

        if not self._data_dir.exists():
            raise CRSPUnavailableError(
                f"CRSP data directory not found: {self._data_dir}"
            )

        self._returns_path = self._data_dir / "daily_returns.parquet"
        if not self._returns_path.exists():
            raise CRSPUnavailableError(
                f"CRSP daily returns file not found: {self._returns_path}"
            )

    def get_daily_returns(
        self,
        permnos: list[int],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame:
        """Fetch daily returns for given permnos.

        Args:
            permnos: List of CRSP permno identifiers.
            start_date: Start of date range (inclusive).
            end_date: End of date range (inclusive).

        Returns:
            DataFrame[date, permno, daily_return]

        Notes:
            - Source: CRSP daily stock file (dsf) or equivalent.
            - Uses 'ret' column for total return (including dividends).
            - Missing Data: Returns NaN for delisted/missing days.
        """
        try:
            lf = pl.scan_parquet(self._returns_path)

            # Schema: [date, permno, ret]
            result = (
                lf.filter(
                    (pl.col("permno").is_in(permnos))
                    & (pl.col("date") >= start_date)
                    & (pl.col("date") <= end_date)
                )
                .select([
                    pl.col("date"),
                    pl.col("permno"),
                    pl.col("ret").alias("daily_return"),
                ])
                .collect()
            )

            return result

        except Exception as e:
            logger.error(
                "crsp_daily_returns_failed",
                extra={
                    "n_permnos": len(permnos),
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "error": str(e),
                },
                exc_info=True,
            )
            raise CRSPUnavailableError(f"Failed to load CRSP returns: {e}") from e

    def get_forward_returns(
        self,
        signals_df: pl.DataFrame,
        skip_days: int,
        holding_period: int,
        calendar: xcals.ExchangeCalendar,
    ) -> pl.DataFrame:
        """Compute forward returns for quantile analysis.

        Args:
            signals_df: DataFrame with [signal_date, permno] columns.
            skip_days: Gap between signal and return start (avoid look-ahead).
            holding_period: Number of trading days to compound.
            calendar: Trading calendar for date arithmetic.

        Returns:
            DataFrame[signal_date, permno, forward_return]

        Algorithm:
        1. For each (signal_date, permno):
           - t_start = calendar.add_trading_days(signal_date, skip_days)
           - t_end = calendar.add_trading_days(t_start, holding_period - 1)
           - forward_return = prod(1 + r_i for i in [t_start, t_end]) - 1
        2. Drop rows where ANY day in window is missing

        Notes:
            Rows with missing returns in the window are dropped (no NaN propagation).

        Raises:
            ValueError: If skip_days < 1 or holding_period <= 0 (look-ahead risk).
        """
        # Validate parameters to prevent look-ahead bias
        if skip_days < 1:
            raise ValueError(
                f"skip_days must be >= 1 to avoid look-ahead bias, got {skip_days}"
            )
        if holding_period <= 0:
            raise ValueError(
                f"holding_period must be > 0, got {holding_period}"
            )

        # Schema validation: require signal_date and permno columns
        required_cols = {"signal_date", "permno"}
        missing_cols = required_cols - set(signals_df.columns)
        if missing_cols:
            raise ValueError(
                f"signals_df missing required columns: {missing_cols}. "
                f"Expected columns: {required_cols}, got: {list(signals_df.columns)}"
            )

        # Validate column types for proper join/calendar operations
        if signals_df["signal_date"].dtype not in (pl.Date, pl.Datetime):
            raise ValueError(
                f"signal_date column must be Date or Datetime type, "
                f"got {signals_df['signal_date'].dtype}"
            )

        if signals_df.height == 0:
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        # Filter out null signal_date/permno to prevent min()/max() failures
        signals_df = signals_df.filter(
            pl.col("signal_date").is_not_null() & pl.col("permno").is_not_null()
        )
        if signals_df.height == 0:
            logger.warning("forward_returns_all_null_filtered")
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        # Get unique signal dates and permnos
        signal_dates = signals_df["signal_date"].unique().sort().to_list()
        permnos = signals_df["permno"].unique().to_list()

        min_signal = min(signal_dates)
        max_signal = max(signal_dates)

        # Normalize min_signal to a valid session to prevent sessions_in_range failure
        # when signals include non-trading dates (weekends/holidays)
        # Use "previous" direction to avoid look-ahead bias
        try:
            min_signal = calendar.date_to_session(min_signal, direction="previous").date()
        except Exception:
            # If normalization fails, proceed with original date - sessions_in_range
            # will handle empty results gracefully
            pass

        # Normalize max_signal to a valid session before session_offset to prevent
        # session_offset failures on non-trading days (weekends/holidays)
        # Use "next" to ensure we include the full forward window for the last signal
        try:
            if not calendar.is_session(max_signal):
                max_signal = calendar.date_to_session(max_signal, direction="next").date()
        except Exception:
            # If normalization fails, proceed with original date - fallback will handle
            pass

        # CRITICAL FIX: Extend calendar range beyond max_signal to include forward window
        # Use calendar to compute the actual end date we need
        total_forward_days = skip_days + holding_period
        # Buffer of 5 trading days accounts for potential holiday clusters
        # (e.g., Thanksgiving week, Christmas-New Year period) that could
        # cause the last signal date's forward window to extend further
        CALENDAR_BUFFER_DAYS = 5
        try:
            # Get sessions starting from max_signal and extend forward
            extended_end = calendar.session_offset(max_signal, total_forward_days + CALENDAR_BUFFER_DAYS)
            extended_end_date = extended_end.date()
        except Exception as e:
            # Fallback: estimate calendar days (trading days * 1.5 for weekends)
            from datetime import timedelta
            extended_end_date = max_signal + timedelta(days=int(total_forward_days * 1.5) + 10)
            logger.warning(
                "calendar_session_offset_failed_using_fallback",
                extra={
                    "max_signal": str(max_signal),
                    "total_forward_days": total_forward_days,
                    "fallback_end_date": str(extended_end_date),
                    "error": str(e),
                },
            )

        # Get full session range including forward window
        sessions = calendar.sessions_in_range(min_signal, extended_end_date)
        sessions_list = [s.date() for s in sessions]

        if not sessions_list:
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        # Build date-to-index lookup for O(1) access
        date_to_idx = {d: i for i, d in enumerate(sessions_list)}

        # Load all returns we need (extended range)
        all_returns = self.get_daily_returns(
            permnos=permnos,
            start_date=min_signal,
            end_date=extended_end_date,
        )

        if all_returns.height == 0:
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        # VECTORIZED: Group returns by permno for faster lookup
        # Use partition_by with as_dict=True for O(1) permno lookup
        # This is safer than group_by iteration which has varying key formats

        # De-duplicate (permno, date) pairs by averaging returns to handle revised CRSP rows
        # This prevents silent overwrites when dict(zip(...)) encounters duplicates
        n_before_dedup = all_returns.height
        all_returns = all_returns.group_by(["permno", "date"]).agg(
            pl.col("daily_return").mean()
        )
        n_after_dedup = all_returns.height
        if n_before_dedup > n_after_dedup:
            logger.info(
                "crsp_returns_deduplicated",
                extra={
                    "before": n_before_dedup,
                    "after": n_after_dedup,
                    "duplicates_merged": n_before_dedup - n_after_dedup,
                },
            )

        # ============================================================
        # VECTORIZED FORWARD RETURNS COMPUTATION
        # Replaces iter_rows loop with Polars operations for ~10-100x speedup
        # ============================================================

        # Step 1: Create date-to-index mapping DataFrame for joins
        date_idx_df = pl.DataFrame({
            "date": sessions_list,
            "date_idx": list(range(len(sessions_list))),
        })

        # Step 2: Add date index to returns for window filtering
        all_returns_with_idx = all_returns.join(date_idx_df, on="date", how="inner")

        # Step 3: Normalize signal dates and compute signal index
        # Build mapping: original_date -> (normalized_date, signal_idx)
        signal_date_records: list[dict[str, date | int]] = []
        unique_signal_dates = signals_df["signal_date"].unique().to_list()

        for d in unique_signal_dates:
            if d is None:
                continue
            if d in date_to_idx:
                signal_date_records.append({
                    "orig_signal_date": d,
                    "normalized_signal_date": d,
                    "signal_idx": date_to_idx[d],
                })
            else:
                # Normalize to previous trading session (avoid look-ahead bias)
                try:
                    normalized = calendar.date_to_session(d, direction="previous").date()
                    if normalized in date_to_idx:
                        signal_date_records.append({
                            "orig_signal_date": d,
                            "normalized_signal_date": normalized,
                            "signal_idx": date_to_idx[normalized],
                        })
                except Exception:
                    pass  # Skip dates that can't be normalized

        if not signal_date_records:
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        signal_date_mapping = pl.DataFrame(signal_date_records)

        # Step 4: Join signals with date mapping to get normalized dates and indices
        signals_normalized = signals_df.join(
            signal_date_mapping,
            left_on="signal_date",
            right_on="orig_signal_date",
            how="inner",
        )

        n_skipped_calendar = signals_df.height - signals_normalized.height

        # Step 5: Filter out signals whose forward window extends beyond data
        max_valid_signal_idx = len(sessions_list) - skip_days - holding_period
        signals_valid = signals_normalized.filter(
            pl.col("signal_idx") <= max_valid_signal_idx
        )

        n_skipped_window_overflow = signals_normalized.height - signals_valid.height

        if signals_valid.height == 0:
            total_signals = signals_df.height
            logger.info(
                "forward_returns_skipped",
                extra={
                    "n_skipped_calendar": n_skipped_calendar,
                    "n_skipped_insufficient": n_skipped_window_overflow,
                    "n_computed": 0,
                    "total_signals": total_signals,
                    "drop_rate_pct": 100.0,
                    "survivorship_bias_warning": True,
                },
            )
            return pl.DataFrame({
                "signal_date": pl.Series([], dtype=pl.Date),
                "permno": pl.Series([], dtype=pl.Int64),
                "forward_return": pl.Series([], dtype=pl.Float64),
            })

        # Step 6: Compute window boundaries
        signals_with_bounds = signals_valid.with_columns([
            (pl.col("signal_idx") + skip_days).alias("window_start_idx"),
            (pl.col("signal_idx") + skip_days + holding_period).alias("window_end_idx"),
        ])

        # Step 7: Join signals with returns on permno (creates larger intermediate DataFrame)
        # This is more memory-intensive but much faster than row iteration
        joined = signals_with_bounds.join(
            all_returns_with_idx,
            on="permno",
            how="inner",
        )

        # Step 8: Filter to keep only returns within the forward window
        # date_idx must be in [window_start_idx, window_end_idx)
        joined_filtered = joined.filter(
            (pl.col("date_idx") >= pl.col("window_start_idx"))
            & (pl.col("date_idx") < pl.col("window_end_idx"))
            & pl.col("daily_return").is_not_null()
            & pl.col("daily_return").is_finite()
        )

        # Step 9: Group by (normalized_signal_date, permno) and compute compounded return
        # Use normalized_signal_date to ensure join consistency with downstream analyzers
        # (QuantileAnalyzer normalizes signals to trading sessions before joining)
        # Only keep groups with complete windows (exactly holding_period days)
        result = (
            joined_filtered
            .group_by(["normalized_signal_date", "permno"])
            .agg([
                pl.len().alias("n_days"),
                # Compound return: prod(1 + r_i) - 1
                ((1 + pl.col("daily_return")).product() - 1).alias("forward_return"),
            ])
            .filter(pl.col("n_days") == holding_period)
            # Rename to signal_date for API compatibility
            .rename({"normalized_signal_date": "signal_date"})
            .select(["signal_date", "permno", "forward_return"])
        )

        # Log statistics for survivorship bias awareness
        total_signals = signals_df.height
        n_computed = result.height
        n_skipped_insufficient = signals_valid.height - n_computed + n_skipped_window_overflow
        drop_rate = (total_signals - n_computed) / total_signals if total_signals > 0 else 0.0

        if n_skipped_calendar > 0 or n_skipped_insufficient > 0:
            log_level = logging.INFO if drop_rate > 0.10 else logging.DEBUG
            logger.log(
                log_level,
                "forward_returns_skipped",
                extra={
                    "n_skipped_calendar": n_skipped_calendar,
                    "n_skipped_insufficient": n_skipped_insufficient,
                    "n_computed": n_computed,
                    "total_signals": total_signals,
                    "drop_rate_pct": round(drop_rate * 100, 2),
                    "survivorship_bias_warning": drop_rate > 0.10,
                },
            )

        return result


__all__ = [
    "CRSPUnavailableError",
    "UniverseProvider",
    "ForwardReturnsProvider",
]

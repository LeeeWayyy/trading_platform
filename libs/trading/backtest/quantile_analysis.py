"""Quantile Analysis for Signal Validation.

P6T10: Track 10 - Quantile & Attribution Analytics

Provides:
- QuantileAnalysisConfig: Configuration for analysis
- QuantileResult: Result dataclass with Rank IC and quantile metrics
- QuantileAnalyzer: Main analysis class

Rank IC (Information Coefficient) is the primary metric, measuring
Spearman correlation between raw signal values and forward returns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import polars as pl
from scipy.stats import rankdata, spearmanr  # type: ignore[import-untyped]

if TYPE_CHECKING:
    import exchange_calendars as xcals  # type: ignore[import-not-found]

    from libs.data.data_providers.universe import ForwardReturnsProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuantileAnalysisConfig:
    """Configuration for quantile tear sheet analysis.

    Attributes:
        n_quantiles: Number of quantile buckets (default 5).
        holding_period_days: Forward return horizon in trading days (must be > 0).
        min_observations_per_date: Minimum stocks per date for valid analysis.
        min_total_dates: Minimum number of valid dates for result.
        skip_days: Gap between signal and return (must be >= 1 to avoid look-ahead).
    """

    n_quantiles: int = 5
    holding_period_days: int = 20  # Trading days
    min_observations_per_date: int = 50
    min_total_dates: int = 50
    skip_days: int = 1  # Gap between signal and return

    def __post_init__(self) -> None:
        """Validate configuration to prevent look-ahead bias and invalid quantiles."""
        if self.skip_days < 1:
            raise ValueError(
                f"skip_days must be >= 1 to avoid look-ahead bias, got {self.skip_days}"
            )
        if self.holding_period_days <= 0:
            raise ValueError(
                f"holding_period_days must be > 0, got {self.holding_period_days}"
            )
        if self.n_quantiles < 2:
            raise ValueError(
                f"n_quantiles must be >= 2 for meaningful long/short analysis, got {self.n_quantiles}"
            )
        if self.min_observations_per_date < self.n_quantiles:
            raise ValueError(
                f"min_observations_per_date ({self.min_observations_per_date}) must be >= "
                f"n_quantiles ({self.n_quantiles}) for valid quantile assignment"
            )


@dataclass(frozen=True)
class QuantileResult:
    """Result of quantile analysis.

    Attributes:
        mean_rank_ic: Mean of per-date Spearman(signal, return).
        rank_ic_std: Standard deviation of per-date ICs.
        rank_ic_t_stat: t-statistic for IC != 0.
        rank_ic_positive_pct: Percentage of days with IC > 0.
        quantile_returns: Mean forward return per quantile bucket.
        long_short_spread: Q_high - Q_low spread.
        n_dates: Number of valid dates analyzed.
        n_dates_skipped: Number of dates skipped (low obs, non-trading, NaN IC).
        n_observations_per_quantile: Count per quantile bucket.
        period_start: First date in analysis.
        period_end: Last date in analysis.
        signal_name: Name of signal analyzed.
        universe_name: Universe used (e.g., "SP500").
    """

    # Rank IC (primary metric)
    mean_rank_ic: float
    rank_ic_std: float
    rank_ic_t_stat: float
    rank_ic_positive_pct: float  # % of days with IC > 0

    # Quantile returns (secondary)
    quantile_returns: dict[int, float] = field(default_factory=dict)
    long_short_spread: float = 0.0

    # Metadata
    n_dates: int = 0
    n_dates_skipped: int = 0
    n_observations_per_quantile: dict[int, int] = field(default_factory=dict)
    period_start: date | None = None
    period_end: date | None = None
    signal_name: str = ""
    universe_name: str = ""


class InsufficientDataError(Exception):
    """Raised when there's insufficient data for analysis."""

    pass


class QuantileAnalyzer:
    """Compute Rank IC and quantile tear sheet metrics.

    Rank IC is the primary metric: Spearman correlation between
    raw signal values and forward returns, computed per-date then averaged.

    Example:
        from exchange_calendars import get_calendar

        calendar = get_calendar("XNYS")
        analyzer = QuantileAnalyzer(calendar)

        result = analyzer.analyze(
            signals=signals_df,  # [date, permno, signal_value]
            forward_returns=returns_df,  # [date, permno, forward_return]
            config=QuantileAnalysisConfig(n_quantiles=5),
        )
        print(f"Mean Rank IC: {result.mean_rank_ic:.3f}")
    """

    def __init__(self, calendar: xcals.ExchangeCalendar) -> None:
        """Initialize with trading calendar.

        Args:
            calendar: Exchange calendar for trading day validation.
        """
        self._calendar = calendar
        self._trading_dates: set[date] = set()

    def _load_trading_dates(self, start: date, end: date) -> None:
        """Load trading dates for date range.

        Normalizes non-trading days to nearest sessions to prevent
        sessions_in_range failures on weekend/holiday boundaries.
        """
        # Normalize start to previous session (or same if already a session)
        # Uses "previous" to be consistent with signal normalization, ensuring
        # signals normalized to prior trading days are included in trading_dates
        try:
            if not self._calendar.is_session(start):
                start = self._calendar.date_to_session(start, direction="previous").date()
        except Exception:
            pass  # Keep original if normalization fails

        # Normalize end to previous session (or same if already a session)
        try:
            if not self._calendar.is_session(end):
                end = self._calendar.date_to_session(end, direction="previous").date()
        except Exception:
            pass  # Keep original if normalization fails

        # Guard against inverted range after normalization
        if start > end:
            self._trading_dates = set()
            return

        sessions = self._calendar.sessions_in_range(start, end)
        self._trading_dates = {s.date() for s in sessions}

    def analyze(
        self,
        signals: pl.DataFrame,
        forward_returns: pl.DataFrame,
        config: QuantileAnalysisConfig | None = None,
        signal_name: str = "",
        universe_name: str = "",
    ) -> QuantileResult:
        """Run analysis with Rank IC as primary metric.

        Args:
            signals: DataFrame[date, permno, signal_value].
            forward_returns: DataFrame[signal_date, permno, forward_return].
            config: Analysis configuration (default if None).
            signal_name: Name of signal for metadata.
            universe_name: Universe name for metadata.

        Returns:
            QuantileResult with Rank IC and quantile metrics.

        Raises:
            InsufficientDataError: If not enough data for analysis.

        Algorithm:
        1. Pre-filter: Only include signal dates in trading calendar
        2. For each trading date:
           a. Skip if < min_observations
           b. Compute Rank IC: Spearman(signal_value, forward_return)
           c. Handle NaN IC: Drop dates with constant signals
           d. Assign quantiles and record per-quantile returns
        3. Aggregate across dates
        """
        cfg = config or QuantileAnalysisConfig()

        # Validate input
        if signals.height == 0:
            raise InsufficientDataError("No signals provided")
        if forward_returns.height == 0:
            raise InsufficientDataError("No forward returns provided")

        # Rename columns for consistency
        if "date" in signals.columns and "signal_date" not in signals.columns:
            signals = signals.rename({"date": "signal_date"})

        # Coerce signal_date to pl.Date to prevent join/calendar mismatches
        # (handles Datetime, Utf8, or other input types)
        if signals["signal_date"].dtype != pl.Date:
            try:
                signals = signals.with_columns(pl.col("signal_date").cast(pl.Date))
            except Exception as e:
                raise InsufficientDataError(
                    f"Failed to convert signal_date to Date type: {e}"
                ) from e

        # Filter out null signal_date/permno to prevent min()/max() failures
        signals = signals.filter(
            pl.col("signal_date").is_not_null() & pl.col("permno").is_not_null()
        )
        if signals.height == 0:
            raise InsufficientDataError("No valid signals after null filtering")

        # Normalize non-trading signal_dates to previous session for join consistency
        # This ensures signals join correctly with forward_returns which also normalizes
        unique_dates = signals["signal_date"].unique().to_list()
        date_map = {}
        for d in unique_dates:
            if d is None:
                continue
            try:
                if self._calendar.is_session(d):
                    date_map[d] = d
                else:
                    # Normalize to previous trading session (avoid look-ahead)
                    date_map[d] = self._calendar.date_to_session(d, direction="previous").date()
            except Exception:
                date_map[d] = d  # Keep original if normalization fails

        if date_map:
            n_normalized = sum(1 for orig, norm in date_map.items() if orig != norm)
            if n_normalized > 0:
                logger.debug(
                    "signal_dates_normalized",
                    extra={"n_normalized": n_normalized},
                )
            signals = signals.with_columns(
                pl.col("signal_date").replace(date_map).alias("signal_date")
            )

        # De-duplicate signals: if multiple signals exist for same (signal_date, permno),
        # average them to prevent double-counting in IC and quantile calculations
        n_before_dedup = signals.height
        signals = signals.group_by(["signal_date", "permno"]).agg(
            pl.col("signal_value").mean()
        )
        n_after_dedup = signals.height
        if n_before_dedup > n_after_dedup:
            logger.info(
                "signals_deduplicated",
                extra={
                    "before": n_before_dedup,
                    "after": n_after_dedup,
                    "duplicates_merged": n_before_dedup - n_after_dedup,
                },
            )

        # Get date range
        signal_dates = signals["signal_date"].unique().sort().to_list()
        if not signal_dates:
            raise InsufficientDataError("No signal dates found")

        # Load trading dates
        self._load_trading_dates(min(signal_dates), max(signal_dates))

        # Join signals with forward returns
        joined = signals.join(
            forward_returns,
            on=["signal_date", "permno"],
            how="inner",
        )

        if joined.height == 0:
            raise InsufficientDataError("No overlapping signal/return data")

        # Filter out non-finite values
        joined = joined.filter(
            pl.col("signal_value").is_finite()
            & pl.col("forward_return").is_finite()
        )

        # Per-date analysis
        per_date_ics: list[float] = []
        per_date_quantile_returns: dict[int, list[float]] = {
            q: [] for q in range(1, cfg.n_quantiles + 1)
        }
        n_dates_skipped = 0
        total_obs_per_quantile: dict[int, int] = {
            q: 0 for q in range(1, cfg.n_quantiles + 1)
        }

        valid_dates = []

        # PERFORMANCE: Use partition_by for O(1) per-date access instead of O(N) filter
        date_groups = joined.partition_by("signal_date", as_dict=True)

        for signal_date in signal_dates:
            # Check if trading day
            if signal_date not in self._trading_dates:
                n_dates_skipped += 1
                logger.debug(
                    "skipping_non_trading_date",
                    extra={"date": str(signal_date)},
                )
                continue

            # Get data for this date using O(1) dict lookup
            # Note: partition_by returns tuple keys even for single column in Polars 1.x
            date_data = date_groups.get((signal_date,))
            if date_data is None:
                n_dates_skipped += 1
                continue

            # Check minimum observations
            if date_data.height < cfg.min_observations_per_date:
                n_dates_skipped += 1
                logger.debug(
                    "skipping_low_obs_date",
                    extra={"date": str(signal_date), "obs": date_data.height},
                )
                continue

            # Extract arrays
            signal_vals = date_data["signal_value"].to_numpy()
            return_vals = date_data["forward_return"].to_numpy()

            # Compute Rank IC
            ic = self._compute_rank_ic(signal_vals, return_vals)
            if ic is None or np.isnan(ic):
                n_dates_skipped += 1
                logger.debug(
                    "skipping_nan_ic_date",
                    extra={"date": str(signal_date)},
                )
                continue

            per_date_ics.append(ic)
            valid_dates.append(signal_date)

            # Assign quantiles and record returns
            quantiles = self._assign_quantiles(signal_vals, cfg.n_quantiles)

            for q in range(1, cfg.n_quantiles + 1):
                q_mask = quantiles == q
                q_returns = return_vals[q_mask]
                if len(q_returns) > 0:
                    per_date_quantile_returns[q].append(float(np.mean(q_returns)))
                    total_obs_per_quantile[q] += len(q_returns)

        # Check minimum dates
        if len(per_date_ics) < cfg.min_total_dates:
            raise InsufficientDataError(
                f"Only {len(per_date_ics)} valid dates, "
                f"need {cfg.min_total_dates}"
            )

        # Aggregate Rank IC
        ic_array = np.array(per_date_ics)
        mean_ic = float(np.mean(ic_array))
        # Guard against NaN std when n=1 (ddof=1 requires n>1)
        if len(ic_array) > 1:
            std_ic = float(np.std(ic_array, ddof=1))
            t_stat = mean_ic / (std_ic / np.sqrt(len(ic_array))) if std_ic > 0 else 0.0
        else:
            std_ic = 0.0
            t_stat = 0.0
        positive_pct = float(np.mean(ic_array > 0) * 100)

        # Aggregate quantile returns
        quantile_means: dict[int, float] = {}
        for q in range(1, cfg.n_quantiles + 1):
            q_rets = per_date_quantile_returns[q]
            if q_rets:
                quantile_means[q] = float(np.mean(q_rets))
            else:
                quantile_means[q] = 0.0

        # Long/Short spread
        long_short = quantile_means.get(cfg.n_quantiles, 0.0) - quantile_means.get(1, 0.0)

        return QuantileResult(
            mean_rank_ic=mean_ic,
            rank_ic_std=std_ic,
            rank_ic_t_stat=t_stat,
            rank_ic_positive_pct=positive_pct,
            quantile_returns=quantile_means,
            long_short_spread=long_short,
            n_dates=len(per_date_ics),
            n_dates_skipped=n_dates_skipped,
            n_observations_per_quantile=total_obs_per_quantile,
            period_start=min(valid_dates) if valid_dates else None,
            period_end=max(valid_dates) if valid_dates else None,
            signal_name=signal_name,
            universe_name=universe_name,
        )

    def _compute_rank_ic(
        self,
        signals: npt.NDArray[np.floating[Any]],
        returns: npt.NDArray[np.floating[Any]],
    ) -> float | None:
        """Spearman correlation between raw signal values and returns.

        This is the standard Rank IC used in factor research.

        Args:
            signals: Array of signal values.
            returns: Array of forward returns.

        Returns:
            Spearman correlation coefficient, or None if invalid.
        """
        # Check for constant signals (would give NaN correlation)
        if np.std(signals) == 0 or np.std(returns) == 0:
            return None

        result = spearmanr(signals, returns)
        # Use statistic attr (scipy 1.10+) with fallback to correlation for compatibility
        correlation = getattr(result, "statistic", getattr(result, "correlation", None))
        return float(correlation) if correlation is not None else None

    def _assign_quantiles(
        self,
        signals: npt.NDArray[np.floating[Any]],
        n_quantiles: int,
    ) -> npt.NDArray[np.intp]:
        """Assign quantile labels using scipy.stats.rankdata.

        Args:
            signals: Array of signal values.
            n_quantiles: Number of quantile buckets.

        Returns:
            Array of quantile labels (1 to n_quantiles).
        """
        ranks = rankdata(signals, method="average")
        n = len(ranks)
        # Quantiles: 1 = lowest, n_quantiles = highest
        quantiles: npt.NDArray[np.intp] = np.ceil(ranks / n * n_quantiles).astype(np.intp)
        # Clip to valid range
        quantiles = np.clip(quantiles, 1, n_quantiles)
        return quantiles


def run_quantile_analysis(
    signals: pl.DataFrame,
    forward_returns_provider: ForwardReturnsProvider,
    calendar: xcals.ExchangeCalendar,
    config: QuantileAnalysisConfig | None = None,
    signal_name: str = "",
    universe_name: str = "",
) -> QuantileResult:
    """Convenience function to run full quantile analysis.

    This handles computing forward returns and running the analysis.

    Args:
        signals: DataFrame[date, permno, signal_value].
        forward_returns_provider: Provider for forward returns.
        calendar: Trading calendar.
        config: Analysis configuration.
        signal_name: Name of signal for metadata.
        universe_name: Universe name for metadata.

    Returns:
        QuantileResult with analysis metrics.
    """
    cfg = config or QuantileAnalysisConfig()

    # Rename date column if needed (with guard for existing signal_date)
    if "date" in signals.columns and "signal_date" not in signals.columns:
        signals = signals.rename({"date": "signal_date"})

    # Note: Date normalization is handled internally by QuantileAnalyzer.analyze
    # to avoid duplication and ensure consistent behavior

    # Compute forward returns (dedup signal keys to avoid duplicate forward_return rows)
    forward_returns = forward_returns_provider.get_forward_returns(
        signals_df=signals.select(["signal_date", "permno"]).unique(),
        skip_days=cfg.skip_days,
        holding_period=cfg.holding_period_days,
        calendar=calendar,
    )

    # Run analysis
    analyzer = QuantileAnalyzer(calendar)
    return analyzer.analyze(
        signals=signals,
        forward_returns=forward_returns,
        config=cfg,
        signal_name=signal_name,
        universe_name=universe_name,
    )


__all__ = [
    "QuantileAnalysisConfig",
    "QuantileResult",
    "QuantileAnalyzer",
    "InsufficientDataError",
    "run_quantile_analysis",
]

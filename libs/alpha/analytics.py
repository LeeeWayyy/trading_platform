"""
Alpha analytics extensions.

Provides decay curve analysis and grouped analysis utilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import polars as pl

from libs.alpha.metrics import AlphaMetricsAdapter

logger = logging.getLogger(__name__)


@dataclass
class GroupedICResult:
    """Result of grouped IC analysis."""

    by_group: pl.DataFrame  # [group_name, ic, rank_ic, n_stocks]
    overall_ic: float
    high_ic_groups: list[str]  # Groups with IC > overall
    low_ic_groups: list[str]  # Groups with IC < overall


@dataclass
class DecayAnalysisResult:
    """Extended decay curve analysis result."""

    decay_curve: pl.DataFrame  # [horizon, ic, rank_ic]
    half_life: float | None
    decay_rate: float | None  # Estimated daily IC decay rate
    is_persistent: bool  # True if IC remains positive at longest horizon


class AlphaAnalytics:
    """Extended analytics for alpha signals."""

    def __init__(self, metrics_adapter: AlphaMetricsAdapter | None = None):
        """Initialize analytics.

        Args:
            metrics_adapter: Metrics adapter (created if not provided)
        """
        self._metrics = metrics_adapter or AlphaMetricsAdapter()

    def analyze_by_sector(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        sector_mapping: pl.DataFrame,
    ) -> GroupedICResult:
        """Analyze IC by GICS sector.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            sector_mapping: DataFrame with [permno, date, gics_sector]

        Returns:
            GroupedICResult with per-sector analysis
        """
        grouped_ic = self._metrics.compute_grouped_ic(signal, returns, sector_mapping)

        if grouped_ic.height == 0:
            return GroupedICResult(
                by_group=grouped_ic,
                overall_ic=float("nan"),
                high_ic_groups=[],
                low_ic_groups=[],
            )

        # Overall IC
        overall = self._metrics.compute_ic(signal, returns)

        # Identify high/low IC sectors
        high_ic = (
            grouped_ic.filter(pl.col("rank_ic") > overall.rank_ic)
            .get_column("gics_sector")
            .to_list()
        )

        low_ic = (
            grouped_ic.filter(pl.col("rank_ic") < overall.rank_ic)
            .get_column("gics_sector")
            .to_list()
        )

        return GroupedICResult(
            by_group=grouped_ic,
            overall_ic=overall.rank_ic,
            high_ic_groups=high_ic,
            low_ic_groups=low_ic,
        )

    def analyze_by_market_cap(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        market_caps: pl.DataFrame,
        n_quintiles: int = 5,
    ) -> GroupedICResult:
        """Analyze IC by market cap quintile.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            market_caps: DataFrame with [permno, date, market_cap]
            n_quintiles: Number of quintiles (default 5)

        Returns:
            GroupedICResult with per-quintile analysis
        """
        # Assign market cap quintiles per date
        mc_with_quintile = market_caps.with_columns(
            [
                (
                    pl.col("market_cap").rank(method="ordinal").over("date")
                    / pl.col("market_cap").count().over("date")
                    * n_quintiles
                )
                .ceil()
                .cast(pl.Int64)
                .clip(1, n_quintiles)
                .alias("mc_quintile")
            ]
        )

        # Convert to sector-like mapping
        sector_mapping = mc_with_quintile.with_columns(
            [pl.col("mc_quintile").cast(pl.Utf8).alias("gics_sector")]
        ).select(["permno", "date", "gics_sector"])

        grouped_ic = self._metrics.compute_grouped_ic(signal, returns, sector_mapping)

        if grouped_ic.height == 0:
            return GroupedICResult(
                by_group=grouped_ic,
                overall_ic=float("nan"),
                high_ic_groups=[],
                low_ic_groups=[],
            )

        # Overall IC
        overall = self._metrics.compute_ic(signal, returns)

        # Sort by quintile number
        grouped_ic = grouped_ic.sort("gics_sector")

        high_ic = (
            grouped_ic.filter(pl.col("rank_ic") > overall.rank_ic)
            .get_column("gics_sector")
            .to_list()
        )

        low_ic = (
            grouped_ic.filter(pl.col("rank_ic") < overall.rank_ic)
            .get_column("gics_sector")
            .to_list()
        )

        return GroupedICResult(
            by_group=grouped_ic,
            overall_ic=overall.rank_ic,
            high_ic_groups=high_ic,
            low_ic_groups=low_ic,
        )

    def analyze_decay(
        self,
        signal: pl.DataFrame,
        returns_by_horizon: dict[int, pl.DataFrame],
        horizons: list[int] | None = None,
    ) -> DecayAnalysisResult:
        """Extended decay curve analysis.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns_by_horizon: Dict mapping horizon to returns DataFrame
            horizons: Horizons to analyze (default [1, 2, 5, 10, 20, 60])

        Returns:
            DecayAnalysisResult with decay metrics
        """
        if horizons is None:
            horizons = [1, 2, 5, 10, 20, 60]

        decay_result = self._metrics.compute_decay_curve(signal, returns_by_horizon)

        if decay_result.decay_curve.height < 2:
            return DecayAnalysisResult(
                decay_curve=decay_result.decay_curve,
                half_life=None,
                decay_rate=None,
                is_persistent=False,
            )

        # Estimate decay rate (simple linear regression on log IC)
        decay_rate = self._estimate_decay_rate(decay_result.decay_curve)

        # Check persistence (IC positive at longest horizon)
        sorted_curve = decay_result.decay_curve.sort("horizon", descending=True)
        last_ic_raw = sorted_curve.get_column("rank_ic").first()
        # Cast to float with proper type handling
        last_ic: float | None = None
        if last_ic_raw is not None and isinstance(last_ic_raw, int | float):
            last_ic = float(last_ic_raw)
        is_persistent: bool = last_ic is not None and last_ic > 0

        return DecayAnalysisResult(
            decay_curve=decay_result.decay_curve,
            half_life=decay_result.half_life,
            decay_rate=decay_rate,
            is_persistent=is_persistent,
        )

    def _estimate_decay_rate(self, decay_curve: pl.DataFrame) -> float | None:
        """Estimate daily IC decay rate from decay curve."""
        import math

        horizons = decay_curve.get_column("horizon").to_list()
        ics = decay_curve.get_column("rank_ic").to_list()

        # Filter valid positive ICs for log
        valid_points = [
            (h, ic)
            for h, ic in zip(horizons, ics, strict=True)
            if ic is not None and not math.isnan(ic) and ic > 0
        ]

        if len(valid_points) < 2:
            return None

        # Simple linear regression: log(IC) = a - b * horizon
        # decay_rate = b
        import numpy as np

        x = np.array([p[0] for p in valid_points])
        y = np.array([np.log(p[1]) for p in valid_points])

        # Linear regression
        n = len(x)
        sum_x = np.sum(x)
        sum_y = np.sum(y)
        sum_xy = np.sum(x * y)
        sum_xx = np.sum(x * x)

        denominator = n * sum_xx - sum_x * sum_x
        if abs(denominator) < 1e-10:
            return None

        slope = (n * sum_xy - sum_x * sum_y) / denominator

        # Return positive decay rate (negative slope means decay)
        return -slope if slope < 0 else 0.0

    def compute_quintile_returns(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        n_quintiles: int = 5,
    ) -> pl.DataFrame:
        """Compute average returns by signal quintile.

        Useful for analyzing monotonicity of signal-return relationship.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            n_quintiles: Number of quintiles (default 5)

        Returns:
            DataFrame with [quintile, mean_return, n_stocks]
        """
        # Join and assign quintiles per date
        joined = signal.join(returns, on=["permno", "date"], how="inner")

        if joined.height == 0:
            return pl.DataFrame(
                schema={
                    "quintile": pl.Int64,
                    "mean_return": pl.Float64,
                    "n_stocks": pl.Int64,
                }
            )

        joined = joined.with_columns(
            [
                (
                    pl.col("signal").rank(method="ordinal").over("date")
                    / pl.col("signal").count().over("date")
                    * n_quintiles
                )
                .ceil()
                .cast(pl.Int64)
                .clip(1, n_quintiles)
                .alias("quintile")
            ]
        )

        # Aggregate by quintile
        quintile_returns = (
            joined.group_by("quintile")
            .agg(
                [
                    pl.col("return").mean().alias("mean_return"),
                    pl.col("return").count().alias("n_stocks"),
                ]
            )
            .sort("quintile")
        )

        return quintile_returns

    def check_monotonicity(
        self,
        quintile_returns: pl.DataFrame,
    ) -> tuple[bool, float]:
        """Check if quintile returns are monotonic.

        Args:
            quintile_returns: Output from compute_quintile_returns

        Returns:
            (is_monotonic, correlation) where correlation is rank correlation
            between quintile number and mean return
        """
        if quintile_returns.height < 2:
            return False, float("nan")

        quintiles = quintile_returns.get_column("quintile").to_list()
        returns = quintile_returns.get_column("mean_return").to_list()

        # Check strict monotonicity
        diffs = [returns[i + 1] - returns[i] for i in range(len(returns) - 1)]
        is_monotonic = all(d > 0 for d in diffs) or all(d < 0 for d in diffs)

        # Compute rank correlation
        import numpy as np

        corr = np.corrcoef(quintiles, returns)[0, 1]

        return is_monotonic, corr

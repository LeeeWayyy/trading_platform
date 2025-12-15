"""
Factor Analytics for evaluating factor performance.

This module provides analytics for factor evaluation including:
- Information Coefficient (IC) analysis
- Decay curves
- Factor turnover
- Correlation matrices
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)


@dataclass
class ICAnalysis:
    """Information Coefficient analysis results."""

    factor_name: str
    ic_mean: float  # Average IC
    ic_std: float  # IC standard deviation
    icir: float  # IC Information Ratio (ic_mean / ic_std)
    t_statistic: float  # Statistical significance
    hit_rate: float  # % of periods with positive IC
    n_periods: int  # Number of periods analyzed


class FactorAnalytics:
    """
    Analytics for factor evaluation.

    Provides methods to analyze factor predictive power, decay,
    turnover, and correlations.
    """

    def compute_ic(
        self,
        factor_exposures: pl.DataFrame,
        forward_returns: pl.DataFrame,
        horizons: Sequence[int] = (1, 5, 20),
    ) -> dict[str, dict[int, ICAnalysis]]:
        """
        Compute Information Coefficient for each factor at each horizon.

        IC is the rank correlation between factor exposures and subsequent returns.

        Args:
            factor_exposures: DataFrame with date, permno, factor_name, zscore
            forward_returns: DataFrame with date, permno, and return columns (ret_1d, ret_5d, etc.)
            horizons: List of forward return horizons in days

        Returns:
            Dict mapping factor_name -> horizon -> ICAnalysis
        """
        results: dict[str, dict[int, ICAnalysis]] = {}

        factor_names = factor_exposures["factor_name"].unique().to_list()

        for factor_name in factor_names:
            results[factor_name] = {}

            # Filter to this factor
            factor_df = factor_exposures.filter(pl.col("factor_name") == factor_name)

            for horizon in horizons:
                ret_col = f"ret_{horizon}d"
                if ret_col not in forward_returns.columns:
                    logger.warning(f"Return column {ret_col} not found, skipping horizon {horizon}")
                    continue

                # Join exposures with forward returns
                merged = factor_df.join(
                    forward_returns.select(["date", "permno", ret_col]),
                    on=["date", "permno"],
                    how="inner",
                )

                ic_by_date = (
                    merged.drop_nulls(["zscore", ret_col])
                    .group_by("date")
                    .agg(
                        pl.len().alias("n"),
                        pl.corr("zscore", ret_col, method="spearman").alias("ic"),
                    )
                    .filter(pl.col("n") >= 10)
                    .drop_nulls("ic")
                )

                if ic_by_date.height == 0:
                    logger.warning(f"No valid IC data for {factor_name} at horizon {horizon}")
                    continue

                ic_values = ic_by_date["ic"].to_numpy()
                ic_mean = float(np.nanmean(ic_values))
                ic_std = float(np.nanstd(ic_values, ddof=1))
                n_periods = len(ic_values)

                # ICIR and t-stat
                icir = ic_mean / ic_std if ic_std > 0 else 0.0
                t_stat = ic_mean / (ic_std / np.sqrt(n_periods)) if ic_std > 0 else 0.0

                # Hit rate (% positive IC)
                hit_rate = float(np.mean(ic_values > 0))

                results[factor_name][horizon] = ICAnalysis(
                    factor_name=factor_name,
                    ic_mean=ic_mean,
                    ic_std=ic_std,
                    icir=icir,
                    t_statistic=t_stat,
                    hit_rate=hit_rate,
                    n_periods=n_periods,
                )

        return results

    def analyze_decay(
        self,
        factor_exposures: pl.DataFrame,
        returns: pl.DataFrame,
        max_horizon: int = 60,
    ) -> pl.DataFrame:
        """
        Analyze factor predictiveness over time horizons.

        Computes IC at each horizon to show how factor signal decays.

        Args:
            factor_exposures: DataFrame with date, permno, factor_name, zscore
            returns: DataFrame with date, permno, ret columns
            max_horizon: Maximum horizon in days

        Returns:
            DataFrame with columns: factor_name, horizon, ic_mean, ic_std
        """
        results = []

        factor_names = factor_exposures["factor_name"].unique().to_list()

        # Compute forward returns at each horizon
        for horizon in range(1, max_horizon + 1, 5):  # Sample every 5 days
            # Create forward return column
            forward_returns = self._compute_forward_returns(returns, horizon)

            for factor_name in factor_names:
                factor_df = factor_exposures.filter(pl.col("factor_name") == factor_name)

                merged = factor_df.join(
                    forward_returns.select(["date", "permno", "forward_ret"]),
                    on=["date", "permno"],
                    how="inner",
                )

                if merged.height == 0:
                    continue

                # Compute IC for each date using numpy
                ic_values_list = []
                for dt in merged["date"].unique().to_list():
                    date_df = merged.filter(pl.col("date") == dt)
                    if date_df.height < 10:
                        continue
                    ic = self._compute_rank_corr(
                        date_df["zscore"].to_numpy(),
                        date_df["forward_ret"].to_numpy(),
                    )
                    if not np.isnan(ic):
                        ic_values_list.append(ic)

                if len(ic_values_list) > 0:
                    results.append(
                        {
                            "factor_name": factor_name,
                            "horizon": horizon,
                            "ic_mean": float(np.nanmean(ic_values_list)),
                            "ic_std": float(np.nanstd(ic_values_list)),
                        }
                    )

        return pl.DataFrame(results)

    def compute_turnover(
        self,
        factor_exposures: pl.DataFrame,
    ) -> pl.DataFrame:
        """
        Compute factor turnover (rank correlation between consecutive periods).

        High turnover means the factor changes rapidly, potentially costly to trade.

        Args:
            factor_exposures: DataFrame with date, permno, factor_name, zscore

        Returns:
            DataFrame with columns: factor_name, date, turnover
        """
        results = []

        factor_names = factor_exposures["factor_name"].unique().to_list()
        dates = sorted(factor_exposures["date"].unique().to_list())

        for factor_name in factor_names:
            factor_df = factor_exposures.filter(pl.col("factor_name") == factor_name)

            for i in range(1, len(dates)):
                prev_date = dates[i - 1]
                curr_date = dates[i]

                prev_df = factor_df.filter(pl.col("date") == prev_date).select(
                    ["permno", pl.col("zscore").alias("zscore_prev")]
                )
                curr_df = factor_df.filter(pl.col("date") == curr_date).select(
                    ["permno", pl.col("zscore").alias("zscore_curr")]
                )

                merged = prev_df.join(curr_df, on="permno", how="inner")

                if merged.height < 10:
                    continue

                # Rank correlation as stability measure
                # Turnover = 1 - rank_corr (high correlation = low turnover)
                rank_corr = self._compute_rank_corr(
                    merged["zscore_prev"].to_numpy(),
                    merged["zscore_curr"].to_numpy(),
                )

                turnover = 1.0 - rank_corr

                results.append(
                    {
                        "factor_name": factor_name,
                        "date": curr_date,
                        "turnover": turnover,
                    }
                )

        return pl.DataFrame(results)

    def compute_correlation_matrix(
        self,
        factor_exposures: pl.DataFrame,
        as_of_date: date | None = None,
    ) -> pl.DataFrame:
        """
        Compute pairwise factor correlations.

        Args:
            factor_exposures: DataFrame with date, permno, factor_name, zscore
            as_of_date: Optional date to compute correlation (None = all dates)

        Returns:
            DataFrame with correlation matrix (factor_name x factor_name)
        """
        if as_of_date is not None:
            df = factor_exposures.filter(pl.col("date") == as_of_date)
        else:
            df = factor_exposures

        # Pivot to wide format (permno x factors)
        factor_names = sorted(df["factor_name"].unique().to_list())

        # Aggregate by permno+factor_name first (take mean across dates)
        aggregated = df.group_by(["permno", "factor_name"]).agg(
            pl.col("zscore").mean().alias("zscore")
        )

        wide_df = aggregated.pivot(
            on="factor_name",
            index="permno",
            values="zscore",
        ).drop_nulls()

        if wide_df.height < 10:
            logger.warning("Insufficient data for correlation matrix")
            return pl.DataFrame()

        # Compute correlation matrix
        corr_data = []
        for f1 in factor_names:
            row: dict[str, str | float | None] = {"factor_name": f1}
            for f2 in factor_names:
                if f1 in wide_df.columns and f2 in wide_df.columns:
                    corr = self._compute_rank_corr(
                        wide_df[f1].to_numpy(),
                        wide_df[f2].to_numpy(),
                    )
                    row[f2] = corr
                else:
                    row[f2] = None
            corr_data.append(row)

        return pl.DataFrame(corr_data)

    def _compute_rank_corr(
        self,
        x: "np.ndarray[Any, np.dtype[np.floating[Any]]]",
        y: "np.ndarray[Any, np.dtype[np.floating[Any]]]",
    ) -> float:
        """Compute Spearman rank correlation using numpy."""
        from scipy import stats  # type: ignore[import-untyped]

        # Handle NaNs
        mask = ~(np.isnan(x) | np.isnan(y))
        if mask.sum() < 3:
            return 0.0

        corr, _ = stats.spearmanr(x[mask], y[mask])
        return float(corr) if not np.isnan(corr) else 0.0

    def _compute_forward_returns(self, returns: pl.DataFrame, horizon: int) -> pl.DataFrame:
        """
        Compute forward returns at specified horizon.

        For each date, computes the cumulative return over the next `horizon` days.
        This is PIT-correct: only uses data available after the signal date.

        Args:
            returns: DataFrame with date, permno, ret
            horizon: Number of days forward

        Returns:
            DataFrame with forward_ret column
        """
        # Sort by permno and date
        sorted_df = returns.sort(["permno", "date"])

        # Compute rolling forward returns using lead operations
        # Forward return = prod(1 + ret[t+1:t+horizon+1]) - 1
        result = sorted_df.with_columns(
            # Use rolling_map or shift+cumsum approach for forward returns
            # For each row, we need the product of next `horizon` returns
            pl.col("ret")
            .map_batches(
                lambda s: self._compute_horizon_returns(s, horizon),
                return_dtype=pl.Float64,
            )
            .over("permno")
            .alias("forward_ret")
        )

        # Drop rows where we don't have full forward window (null or NaN)
        result = result.filter(
            pl.col("forward_ret").is_not_null() & ~pl.col("forward_ret").is_nan()
        )

        return result

    def _compute_horizon_returns(self, returns: pl.Series, horizon: int) -> pl.Series:
        """
        Compute forward cumulative returns for a single security.
        Optimized to use vectorized operations via log-sum-exp approach.

        Note: Returns are clamped at -0.9999 to avoid -inf from log(0) when
        a security has a -100% return (total loss). This preserves the
        "extremely bad return" signal while maintaining numerical stability.
        """
        # CRITICAL: Clamp returns at -0.9999 to prevent -inf from log(0)
        # A -100% return (r=-1) would produce log(0) = -inf
        # Clamping at -0.9999 gives log(0.0001) ≈ -9.2, still extremely negative
        clamped_returns = returns.clip(lower_bound=-0.9999)

        # Use rolling_sum on log(1+r) for forward cumulative returns.
        # ret_{t, t+h} = exp( sum( log(1+r_{t+1})...log(1+r_{t+h}) ) ) - 1

        # 1. log(1+r) - now safe from -inf due to clamping
        log_ret = (clamped_returns + 1.0).log()

        # 2. Forward rolling sum using reverse-roll-reverse approach
        # Shift log returns to align t+1 at position t
        next_log_rets = log_ret.shift(-1)

        # Reverse, rolling sum, reverse back (backward rolling on reversed = forward)
        # min_samples=horizon ensures we don't return partial sums
        rev_next = next_log_rets.reverse()
        rev_sum = rev_next.rolling_sum(window_size=horizon, min_samples=horizon)
        fwd_sum = rev_sum.reverse()

        # 3. Convert back to simple return
        forward_rets = fwd_sum.exp() - 1.0

        return forward_rets

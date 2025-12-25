"""
Signal-to-weight conversion and turnover calculation.

Converts raw alpha signals to portfolio weights for turnover analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import polars as pl

logger = logging.getLogger(__name__)


class SignalToWeight:
    """Convert raw alpha signals to portfolio weights.

    Supports multiple weight calculation methods for different use cases.
    All methods produce dollar-neutral portfolios (weights sum to 0).
    """

    def __init__(
        self,
        method: Literal["zscore", "quantile", "rank"] = "zscore",
        long_only: bool = False,
        target_leverage: float = 1.0,
        n_quantiles: int = 5,
    ):
        """Initialize signal-to-weight converter.

        Args:
            method: Weight calculation method
                - zscore: weights = z-score / sum(|z-score|)
                - quantile: top quantile = +1/n, bottom = -1/n, middle = 0
                - rank: weights = (rank - mean_rank) / sum(|rank - mean_rank|)
            long_only: If True, only positive weights (no shorts)
            target_leverage: Sum of absolute weights (default 1.0 = 100%)
            n_quantiles: Number of quantiles for quantile method (default 5)
        """
        self.method = method
        self.long_only = long_only
        self.target_leverage = target_leverage
        self.n_quantiles = n_quantiles

    def convert(self, signals: pl.DataFrame) -> pl.DataFrame:
        """Convert signals to weights.

        Args:
            signals: DataFrame with columns [permno, date, signal]

        Returns:
            DataFrame with columns [permno, date, weight]

        Weight properties:
        - zscore: weights sum to 0 (dollar-neutral), |weights| sum to target_leverage
        - quantile: top quantile long, bottom short, middle neutral
        - rank: rank-based, dollar-neutral
        """
        if signals.height == 0:
            return pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "weight": pl.Float64})

        # Filter valid signals
        valid = signals.filter(pl.col("signal").is_not_null())

        if valid.height == 0:
            logger.warning("SignalToWeight: no valid signals")
            return pl.DataFrame(schema={"permno": pl.Int64, "date": pl.Date, "weight": pl.Float64})

        if self.method == "zscore":
            return self._zscore_weights(valid)
        elif self.method == "quantile":
            return self._quantile_weights(valid)
        elif self.method == "rank":
            return self._rank_weights(valid)
        else:
            raise ValueError(f"Unknown method: {self.method}")

    def _zscore_weights(self, signals: pl.DataFrame) -> pl.DataFrame:
        """Z-score based weights (dollar-neutral)."""
        result = (
            signals
            # Cross-sectional z-score per date
            .with_columns(
                [
                    (
                        (pl.col("signal") - pl.col("signal").mean().over("date"))
                        / pl.col("signal").std().over("date")
                    ).alias("zscore")
                ]
            )
            # Handle zero std case (when all signals identical)
            .with_columns(
                [
                    pl.when(pl.col("zscore").is_null() | pl.col("zscore").is_nan())
                    .then(0.0)
                    .otherwise(pl.col("zscore"))
                    .alias("zscore")
                ]
            )
            # Compute abs sum for normalization (guard against zero)
            .with_columns([pl.col("zscore").abs().sum().over("date").alias("_abs_sum")])
            # Normalize to target leverage per date (guard against zero denominator)
            .with_columns(
                [
                    pl.when(pl.col("_abs_sum") == 0)
                    .then(0.0)
                    .otherwise(pl.col("zscore") / pl.col("_abs_sum") * self.target_leverage)
                    .alias("weight")
                ]
            ).drop("_abs_sum")
        )

        if self.long_only:
            result = result.with_columns(
                [
                    pl.when(pl.col("weight") < 0)
                    .then(0.0)
                    .otherwise(pl.col("weight"))
                    .alias("weight")
                ]
            )
            # Re-normalize to target leverage (guard against zero sum)
            result = (
                result.with_columns([pl.col("weight").sum().over("date").alias("_weight_sum")])
                .with_columns(
                    [
                        pl.when(pl.col("_weight_sum") == 0)
                        .then(0.0)
                        .otherwise(pl.col("weight") / pl.col("_weight_sum") * self.target_leverage)
                        .alias("weight")
                    ]
                )
                .drop("_weight_sum")
            )

        return result.select(["permno", "date", "weight"])

    def _quantile_weights(self, signals: pl.DataFrame) -> pl.DataFrame:
        """Quantile-based weights."""
        # Assign quantile buckets per date
        result = signals.with_columns(
            [
                pl.col("signal").rank(method="ordinal").over("date").alias("_rank"),
                pl.col("signal").count().over("date").alias("_count"),
            ]
        )

        # Compute quantile thresholds
        result = result.with_columns(
            [
                (pl.col("_rank") / pl.col("_count") * self.n_quantiles)
                .ceil()
                .cast(pl.Int64)
                .clip(1, self.n_quantiles)
                .alias("quantile")
            ]
        )

        # Assign weights: top quantile = long, bottom = short
        n_stocks_per_date = result.group_by("date").agg(pl.len().alias("n_total"))

        result = result.join(n_stocks_per_date, on="date")

        # Top quantile: +1/n_top, Bottom quantile: -1/n_bottom, Middle: 0
        result = result.with_columns(
            [
                pl.when(pl.col("quantile") == self.n_quantiles)
                .then(1.0)
                .when(pl.col("quantile") == 1)
                .then(-1.0 if not self.long_only else 0.0)
                .otherwise(0.0)
                .alias("raw_weight")
            ]
        )

        # Normalize per date
        result = result.with_columns(
            [
                (
                    pl.col("raw_weight")
                    / pl.col("raw_weight").abs().sum().over("date")
                    * self.target_leverage
                )
                .fill_nan(0.0)
                .fill_null(0.0)
                .alias("weight")
            ]
        )

        return result.select(["permno", "date", "weight"])

    def _rank_weights(self, signals: pl.DataFrame) -> pl.DataFrame:
        """Rank-based weights (dollar-neutral)."""
        result = (
            signals
            # Rank per date
            .with_columns(
                [
                    pl.col("signal").rank(method="ordinal").over("date").alias("_rank"),
                    pl.col("signal").count().over("date").alias("_count"),
                ]
            )
            # Demean rank
            .with_columns([(pl.col("_rank") - (pl.col("_count") + 1) / 2).alias("demeaned_rank")])
            # Compute abs sum for normalization (guard against zero)
            .with_columns([pl.col("demeaned_rank").abs().sum().over("date").alias("_abs_sum")])
            # Normalize to target leverage (guard against zero denominator)
            .with_columns(
                [
                    pl.when(pl.col("_abs_sum") == 0)
                    .then(0.0)
                    .otherwise(pl.col("demeaned_rank") / pl.col("_abs_sum") * self.target_leverage)
                    .alias("weight")
                ]
            ).drop("_abs_sum")
        )

        if self.long_only:
            result = result.with_columns(
                [
                    pl.when(pl.col("weight") < 0)
                    .then(0.0)
                    .otherwise(pl.col("weight"))
                    .alias("weight")
                ]
            )
            # Re-normalize (guard against zero sum)
            result = (
                result.with_columns([pl.col("weight").sum().over("date").alias("_weight_sum")])
                .with_columns(
                    [
                        pl.when(pl.col("_weight_sum") == 0)
                        .then(0.0)
                        .otherwise(pl.col("weight") / pl.col("_weight_sum") * self.target_leverage)
                        .alias("weight")
                    ]
                )
                .drop("_weight_sum")
            )

        return result.select(["permno", "date", "weight"])


@dataclass
class TurnoverResult:
    """Result of turnover calculation."""

    daily_turnover: pl.DataFrame  # [date, turnover]
    average_turnover: float
    annualized_turnover: float  # average * 252


class TurnoverCalculator:
    """Calculate portfolio turnover from weight time series."""

    def compute_daily_turnover(self, weights: pl.DataFrame) -> pl.DataFrame:
        """Compute daily turnover.

        Formula: turnover_t = sum(|weight_t - weight_{t-1}|) / 2

        Handles exits/re-entries: if a symbol is absent on a date, its weight
        is treated as 0, so exit and re-entry turnover is correctly captured.

        Args:
            weights: DataFrame with columns [permno, date, weight]

        Returns:
            DataFrame with columns [date, turnover]
        """
        if weights.height == 0:
            return pl.DataFrame(schema={"date": pl.Date, "turnover": pl.Float64})

        # Get unique dates and permnos
        dates = weights.select("date").unique().sort("date")
        permnos = weights.select("permno").unique()

        # Create full date × permno grid to capture exits/re-entries
        full_grid = dates.join(permnos, how="cross")

        # Join with actual weights, fill missing with 0 (exited position)
        full_weights = (
            full_grid.join(weights, on=["permno", "date"], how="left")
            .with_columns([pl.col("weight").fill_null(0.0).alias("weight")])
            .sort(["permno", "date"])
        )

        # Compute weight changes with proper handling of exits/re-entries
        weight_changes = full_weights.with_columns(
            [
                (pl.col("weight") - pl.col("weight").shift(1).over("permno"))
                .abs()
                .alias("weight_change")
            ]
        )

        # First day for each stock: no prior weight, assume came from 0
        weight_changes = weight_changes.with_columns(
            [
                pl.when(pl.col("weight_change").is_null())
                .then(pl.col("weight").abs())
                .otherwise(pl.col("weight_change"))
                .alias("weight_change")
            ]
        )

        # Aggregate by date
        # Turnover = sum(|w_t - w_{t-1}|) / 2, consistent for all dates including first
        # First date: w_{t-1} = 0 (cash), so turnover = sum(|w_t|) / 2
        daily_turnover = (
            weight_changes.group_by("date")
            .agg(
                [
                    (pl.col("weight_change").sum() / 2).alias("turnover"),
                ]
            )
            .sort("date")
        )

        return daily_turnover

    def compute_average_turnover(self, weights: pl.DataFrame) -> float:
        """Average daily turnover over the period.

        Args:
            weights: DataFrame with columns [permno, date, weight]

        Returns:
            Average daily turnover (excluding first day)
        """
        daily = self.compute_daily_turnover(weights)
        if daily.height <= 1:
            return 0.0

        # Exclude first day
        dates = daily.select("date").unique().sort("date")
        first_date = dates.select(pl.col("date").min()).item()
        daily_ex_first = daily.filter(pl.col("date") != first_date)

        result = daily_ex_first.select(pl.col("turnover").mean()).item()
        return result if result is not None else 0.0

    def compute_turnover_result(self, weights: pl.DataFrame) -> TurnoverResult:
        """Compute full turnover analysis.

        Args:
            weights: DataFrame with columns [permno, date, weight]

        Returns:
            TurnoverResult with daily, average, and annualized turnover
        """
        daily = self.compute_daily_turnover(weights)
        avg = self.compute_average_turnover(weights)

        return TurnoverResult(
            daily_turnover=daily,
            average_turnover=avg,
            annualized_turnover=avg * 252,
        )

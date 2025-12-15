"""
Canonical alpha signal implementations.

Provides 5 standard alpha signals used in quantitative finance:
- MomentumAlpha: 12-1 month return
- ReversalAlpha: 1-month short-term reversal
- ValueAlpha: Book-to-market or earnings yield
- QualityAlpha: ROE or gross profitability
- VolatilityAlpha: Low volatility premium
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

import polars as pl

from libs.alpha.alpha_definition import BaseAlpha

logger = logging.getLogger(__name__)


class MomentumAlpha(BaseAlpha):
    """12-1 Month Momentum (Jegadeesh-Titman style).

    Computes return from t-12 months to t-1 month (skipping most recent month).
    The skip avoids short-term reversal contamination.
    """

    def __init__(
        self,
        lookback_days: int = 252,
        skip_days: int = 21,
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize momentum alpha.

        Args:
            lookback_days: Total lookback period (default 252 = 12 months)
            skip_days: Recent days to skip (default 21 = 1 month)
            winsorize_pct: Percentile for winsorization
            universe_filter: Universe filter
        """
        super().__init__(winsorize_pct=winsorize_pct, universe_filter=universe_filter)
        self._lookback_days = lookback_days
        self._skip_days = skip_days

    @property
    def name(self) -> str:
        return f"momentum_{self._lookback_days}_{self._skip_days}"

    @property
    def category(self) -> str:
        return "momentum"

    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute 12-1 month momentum."""
        # Date boundaries
        end_date = as_of_date - timedelta(days=self._skip_days)
        start_date = as_of_date - timedelta(days=self._lookback_days)

        # Filter to relevant period
        period_data = prices.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

        if period_data.height == 0:
            logger.warning(f"MomentumAlpha: no data for {start_date} to {end_date}")
            return pl.DataFrame(schema={"permno": pl.Int64, "raw_signal": pl.Float64})

        # Compute cumulative return per stock using geometric compounding
        # (1 + r1) * (1 + r2) * ... * (1 + rn) - 1
        momentum = (
            period_data.group_by("permno")
            .agg(
                [
                    ((pl.col("ret") + 1).product() - 1).alias("cumulative_ret"),
                    pl.col("ret").count().alias("n_days"),
                ]
            )
            # Require minimum observations (half the period)
            .filter(pl.col("n_days") >= self._lookback_days * 0.5)
            .select(
                [
                    pl.col("permno"),
                    pl.col("cumulative_ret").alias("raw_signal"),
                ]
            )
        )

        return momentum


class ReversalAlpha(BaseAlpha):
    """Short-term Reversal (1-month).

    Stocks with poor recent performance tend to rebound (mean reversion).
    Signal is negative of recent return (sell winners, buy losers).
    """

    def __init__(
        self,
        lookback_days: int = 21,
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize reversal alpha.

        Args:
            lookback_days: Lookback period (default 21 = 1 month)
            winsorize_pct: Percentile for winsorization
            universe_filter: Universe filter
        """
        super().__init__(winsorize_pct=winsorize_pct, universe_filter=universe_filter)
        self._lookback_days = lookback_days

    @property
    def name(self) -> str:
        return f"reversal_{self._lookback_days}"

    @property
    def category(self) -> str:
        return "reversal"

    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute short-term reversal (negative of recent return)."""
        start_date = as_of_date - timedelta(days=self._lookback_days)

        period_data = prices.filter((pl.col("date") >= start_date) & (pl.col("date") <= as_of_date))

        if period_data.height == 0:
            return pl.DataFrame(schema={"permno": pl.Int64, "raw_signal": pl.Float64})

        # Negative of recent return (reversal signal) using geometric compounding
        # Reversal = -(cumulative return)
        reversal = (
            period_data.group_by("permno")
            .agg(
                [
                    (-((pl.col("ret") + 1).product() - 1)).alias("raw_signal"),
                    pl.col("ret").count().alias("n_days"),
                ]
            )
            .filter(pl.col("n_days") >= self._lookback_days * 0.5)
            .select(["permno", "raw_signal"])
        )

        return reversal


class ValueAlpha(BaseAlpha):
    """Value Factor (Book-to-Market).

    High B/M stocks (value) tend to outperform low B/M (growth).
    Uses Compustat book equity / market cap.
    """

    def __init__(
        self,
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize value alpha.

        Args:
            winsorize_pct: Percentile for winsorization
            universe_filter: Universe filter
        """
        super().__init__(winsorize_pct=winsorize_pct, universe_filter=universe_filter)

    @property
    def name(self) -> str:
        return "value_bm"

    @property
    def category(self) -> str:
        return "value"

    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute book-to-market ratio."""
        if fundamentals is None or fundamentals.height == 0:
            logger.warning("ValueAlpha: no fundamentals available")
            return pl.DataFrame(schema={"permno": pl.Int64, "raw_signal": pl.Float64})

        # Get most recent price for market cap (use last available <= as_of_date)
        # This handles non-trading days where price exactly on as_of_date may be missing
        latest_prices = (
            prices.filter(pl.col("date") <= as_of_date)
            .sort(["permno", "date"], descending=[False, True])
            .group_by("permno")
            .first()
            .select(
                [
                    pl.col("permno"),
                    (pl.col("prc").abs() * pl.col("shrout")).alias("market_cap"),
                ]
            )
        )

        # Get book equity from fundamentals (ceq = common equity)
        # Use most recent available data (PIT-correct via filing lag)
        book_equity = (
            fundamentals.sort(["permno", "datadate"], descending=[False, True])
            .group_by("permno")
            .first()
            .select(
                [
                    pl.col("permno"),
                    pl.col("ceq").alias("book_equity"),  # Common equity
                ]
            )
        )

        # Join and compute B/M
        bm = (
            latest_prices.join(book_equity, on="permno", how="inner")
            .filter((pl.col("market_cap") > 0) & (pl.col("book_equity") > 0))
            .with_columns([(pl.col("book_equity") / pl.col("market_cap")).alias("raw_signal")])
            .select(["permno", "raw_signal"])
        )

        return bm


class QualityAlpha(BaseAlpha):
    """Quality Factor (ROE - Return on Equity).

    Profitable firms with high ROE tend to outperform.
    """

    def __init__(
        self,
        metric: Literal["roe", "gp"] = "roe",
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize quality alpha.

        Args:
            metric: Quality metric - 'roe' (Return on Equity) or 'gp' (Gross Profit)
            winsorize_pct: Percentile for winsorization
            universe_filter: Universe filter
        """
        super().__init__(winsorize_pct=winsorize_pct, universe_filter=universe_filter)
        self._metric = metric

    @property
    def name(self) -> str:
        return f"quality_{self._metric}"

    @property
    def category(self) -> str:
        return "quality"

    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute quality metric (ROE or Gross Profitability)."""
        if fundamentals is None or fundamentals.height == 0:
            logger.warning("QualityAlpha: no fundamentals available")
            return pl.DataFrame(schema={"permno": pl.Int64, "raw_signal": pl.Float64})

        # Get most recent fundamentals
        latest_fund = (
            fundamentals.sort(["permno", "datadate"], descending=[False, True])
            .group_by("permno")
            .first()
        )

        if self._metric == "roe":
            # ROE = Net Income / Book Equity
            quality = (
                latest_fund.filter(pl.col("ceq") > 0)
                .with_columns([(pl.col("ni") / pl.col("ceq")).alias("raw_signal")])
                .select(["permno", "raw_signal"])
            )
        else:  # gp (Gross Profitability)
            # GP = (Revenue - COGS) / Total Assets
            quality = (
                latest_fund.filter(pl.col("at") > 0)
                .with_columns(
                    [((pl.col("revt") - pl.col("cogs")) / pl.col("at")).alias("raw_signal")]
                )
                .select(["permno", "raw_signal"])
            )

        return quality


class VolatilityAlpha(BaseAlpha):
    """Low Volatility Factor.

    Low volatility stocks tend to outperform on risk-adjusted basis.
    Signal is negative of realized volatility (buy low vol, sell high vol).
    """

    def __init__(
        self,
        lookback_days: int = 252,
        min_observations: int = 126,
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize volatility alpha.

        Args:
            lookback_days: Lookback period for volatility calculation
            min_observations: Minimum days required
            winsorize_pct: Percentile for winsorization
            universe_filter: Universe filter
        """
        super().__init__(winsorize_pct=winsorize_pct, universe_filter=universe_filter)
        self._lookback_days = lookback_days
        self._min_observations = min_observations

    @property
    def name(self) -> str:
        return f"low_vol_{self._lookback_days}"

    @property
    def category(self) -> str:
        return "volatility"

    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute negative realized volatility (low vol premium)."""
        start_date = as_of_date - timedelta(days=self._lookback_days)

        period_data = prices.filter((pl.col("date") >= start_date) & (pl.col("date") <= as_of_date))

        if period_data.height == 0:
            return pl.DataFrame(schema={"permno": pl.Int64, "raw_signal": pl.Float64})

        # Compute realized volatility (std of returns)
        # Negative so high signal = low volatility = buy
        vol = (
            period_data.group_by("permno")
            .agg(
                [
                    (-pl.col("ret").std()).alias("raw_signal"),  # Negative volatility
                    pl.col("ret").count().alias("n_days"),
                ]
            )
            .filter(pl.col("n_days") >= self._min_observations)
            .select(["permno", "raw_signal"])
        )

        return vol


# Registry of canonical alphas
CANONICAL_ALPHAS: dict[str, type[BaseAlpha]] = {
    "momentum": MomentumAlpha,
    "reversal": ReversalAlpha,
    "value": ValueAlpha,
    "quality": QualityAlpha,
    "volatility": VolatilityAlpha,
}


def create_alpha(
    name: str,
    **kwargs: float | str | int,
) -> BaseAlpha:
    """Factory function to create canonical alpha.

    Args:
        name: Alpha name ('momentum', 'reversal', 'value', 'quality', 'volatility')
        **kwargs: Parameters for the alpha (numeric or string values)

    Returns:
        Initialized alpha instance

    Example:
        >>> alpha = create_alpha("momentum", lookback_days=126, winsorize_pct=0.02)
    """
    if name not in CANONICAL_ALPHAS:
        raise ValueError(f"Unknown alpha: {name}. Available: {list(CANONICAL_ALPHAS.keys())}")

    # Factory function - type narrowing happens at runtime
    alpha_class = CANONICAL_ALPHAS[name]
    return alpha_class(**kwargs)  # type: ignore[arg-type]

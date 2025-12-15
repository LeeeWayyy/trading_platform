"""
Factor definitions for multi-factor model construction.

This module defines the FactorDefinition protocol and implements
5 canonical equity factors: value, momentum, quality, size, low-vol.

All factor computations are point-in-time (PIT) correct.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class FactorDefinition(Protocol):
    """
    Protocol for factor computation.

    All factor implementations must provide these properties and methods
    to ensure consistent behavior and enable registration with FactorBuilder.
    """

    @property
    def name(self) -> str:
        """Unique factor name (e.g., 'momentum_12_1')."""
        ...

    @property
    def category(self) -> str:
        """Factor category: 'value', 'momentum', 'quality', 'size', 'low_vol'."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        ...

    @property
    def requires_fundamentals(self) -> bool:
        """Whether factor needs Compustat data."""
        ...

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute factor exposures as of a specific date.

        Args:
            prices: CRSP daily data with columns: date, permno, ret, prc, vol, shrout
            fundamentals: Compustat data (if requires_fundamentals=True)
            as_of_date: Point-in-time date for computation

        Returns:
            DataFrame with columns: permno, factor_value
            Must be point-in-time correct (no look-ahead bias).
        """
        ...


@dataclass
class FactorConfig:
    """Configuration for factor computation."""

    winsorize_pct: float = 0.01  # Winsorize at 1%/99% percentiles
    neutralize_sector: bool = True  # Sector-neutralize factors
    min_stocks_per_sector: int = 5  # Minimum for neutralization
    lookback_days: int = 365  # Calendar days for price data fetch (supports 12-month momentum)
    report_date_column: str | None = None  # Optional actual report/public date for PIT filtering


@dataclass
class FactorResult:
    """
    Result of factor computation with metadata.

    Includes exposures DataFrame and provenance metadata for reproducibility.
    """

    exposures: pl.DataFrame  # permno, date, factor_name, raw_value, zscore, percentile
    as_of_date: date
    dataset_version_ids: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    reproducibility_hash: str = ""

    def to_storage_format(self) -> pl.DataFrame:
        """
        Convert to storage format with single dataset_version_id column.

        Storage contract: The `dataset_version_id` column in parquet encodes
        multiple source versions as a combined string:
        'crsp:v1.2.3|compustat:v1.0.1'

        This matches P4T2_TASK.md schema while preserving full provenance.
        """
        version_str = "|".join(f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items()))
        return self.exposures.with_columns(
            [
                pl.lit(version_str).alias("dataset_version_id"),
                pl.lit(self.computation_timestamp).alias("computation_timestamp"),
            ]
        )

    def validate(self) -> list[str]:
        """
        Check for nulls, NaNs, infs, z-scores within +/- 5 sigma.

        Returns:
            List of validation error messages (empty if valid).

        Raises:
            No exceptions - returns error list for caller to handle.
        """
        errors: list[str] = []

        # Check for nulls
        null_counts = self.exposures.null_count()
        for col in ["raw_value", "zscore", "percentile"]:
            if col in self.exposures.columns:
                col_null = null_counts.select(col).item()
                if col_null > 0:
                    errors.append(f"Column '{col}' has {col_null} null values")

        # Check for NaNs (separate from nulls in Polars)
        for col in ["raw_value", "zscore", "percentile"]:
            if col in self.exposures.columns:
                nan_count = self.exposures.filter(pl.col(col).is_nan()).height
                if nan_count > 0:
                    errors.append(f"Column '{col}' has {nan_count} NaN values")

        # Check for infs
        for col in ["raw_value", "zscore", "percentile"]:
            if col in self.exposures.columns:
                inf_count = self.exposures.filter(pl.col(col).is_infinite()).height
                if inf_count > 0:
                    errors.append(f"Column '{col}' has {inf_count} infinite values")

        # Check z-scores within +/- 5 sigma
        if "zscore" in self.exposures.columns:
            extreme_count = self.exposures.filter(pl.col("zscore").abs() > 5.0).height
            if extreme_count > 0:
                errors.append(f"Found {extreme_count} z-scores exceeding +/- 5 sigma")

        return errors


# =============================================================================
# Canonical Factor Implementations
# =============================================================================


class MomentumFactor:
    """
    12-1 Momentum Factor.

    Computes 12-month cumulative return, skipping the most recent month
    to avoid short-term reversal effects.
    """

    @property
    def name(self) -> str:
        return "momentum_12_1"

    @property
    def category(self) -> str:
        return "momentum"

    @property
    def description(self) -> str:
        return "12-month return excluding most recent month (12-1 momentum)"

    @property
    def requires_fundamentals(self) -> bool:
        return False

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute 12-1 momentum.

        Uses returns from t-365 to t-30 (12 calendar months minus 1 calendar month).
        Note: Uses calendar days, not trading days, to ensure correct 12-month lookback.
        """
        from datetime import timedelta

        # Define lookback windows using calendar days
        # 365 days = 12 months, 30 days = 1 month skip to avoid short-term reversal
        end_date = as_of_date - timedelta(days=30)  # Skip last month (calendar)
        start_date = as_of_date - timedelta(days=365)  # 12 months ago (calendar)

        # Filter to lookback window (PIT correct: only past data)
        window_data = prices.filter((pl.col("date") >= start_date) & (pl.col("date") <= end_date))

        # Compute cumulative return per security
        # Using geometric compounding of simple returns: product(1+r) - 1
        momentum = (
            window_data.group_by("permno")
            .agg(
                [
                    # Cumulative return = product of (1 + ret) - 1
                    ((1 + pl.col("ret")).product() - 1).alias("factor_value"),
                    pl.col("ret").count().alias("n_obs"),
                ]
            )
            .filter(pl.col("n_obs") >= 120)  # Require ~50% of trading days (flexible)
            .select(["permno", "factor_value"])
        )

        return momentum


class BookToMarketFactor:
    """
    Book-to-Market Value Factor.

    Uses book value (common equity) from Compustat divided by
    market capitalization from CRSP.
    """

    @property
    def name(self) -> str:
        return "book_to_market"

    @property
    def category(self) -> str:
        return "value"

    @property
    def description(self) -> str:
        return "Book value to market capitalization ratio"

    @property
    def requires_fundamentals(self) -> bool:
        return True

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute book-to-market ratio.

        Book value from most recent Compustat filing (respecting filing lag).
        Market cap from most recent CRSP price.
        """
        if fundamentals is None:
            raise ValueError("BookToMarketFactor requires fundamentals data")

        from datetime import timedelta

        # Filing lag: fundamentals are not public until ~90 days after fiscal period end
        FILING_LAG_DAYS = 90
        filing_cutoff = as_of_date - timedelta(days=FILING_LAG_DAYS)

        # Get most recent market cap per security
        # Use the latest price before as_of_date
        # CRITICAL: Sort by date before .last() to ensure PIT correctness
        latest_prices = (
            prices.filter(pl.col("date") <= as_of_date)
            .sort(["permno", "date"])
            .group_by("permno")
            .agg(
                [
                    pl.col("prc").last().alias("price"),
                    pl.col("shrout").last().alias("shares_out"),
                ]
            )
        )

        # Compute market cap (price * shares outstanding)
        # shrout is in thousands, so multiply by 1000
        market_cap = latest_prices.with_columns(
            (pl.col("price").abs() * pl.col("shares_out") * 1000).alias("market_cap")
        ).select(["permno", "market_cap"])

        # Get most recent book value (common equity) per security
        # CRITICAL: Apply filing lag to prevent look-ahead bias
        # Only use fundamentals with datadate <= filing_cutoff (90 days before as_of_date)
        latest_fundamentals = (
            fundamentals.filter(pl.col("datadate") <= filing_cutoff)
            .sort(["permno", "datadate"])
            .group_by("permno")
            .agg(
                [
                    pl.col("ceq").last().alias("book_value"),  # Common equity
                ]
            )
            .filter(pl.col("book_value") > 0)  # Positive book value only
        )

        # Join and compute B/M ratio
        bm = (
            latest_fundamentals.join(market_cap, on="permno", how="inner")
            .filter(pl.col("market_cap") > 0)
            .with_columns(
                (pl.col("book_value") * 1_000_000 / pl.col("market_cap")).alias(
                    "factor_value"
                )  # ceq is in millions
            )
            .select(["permno", "factor_value"])
        )

        return bm


class ROEFactor:
    """
    Return on Equity Quality Factor.

    Net income divided by common equity from Compustat.
    """

    @property
    def name(self) -> str:
        return "roe"

    @property
    def category(self) -> str:
        return "quality"

    @property
    def description(self) -> str:
        return "Return on Equity (net income / common equity)"

    @property
    def requires_fundamentals(self) -> bool:
        return True

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute ROE from most recent fundamentals.
        """
        if fundamentals is None:
            raise ValueError("ROEFactor requires fundamentals data")

        from datetime import timedelta

        # Filing lag: fundamentals are not public until ~90 days after fiscal period end
        FILING_LAG_DAYS = 90
        filing_cutoff = as_of_date - timedelta(days=FILING_LAG_DAYS)

        # Get most recent fundamentals per security
        # CRITICAL: Apply filing lag and sort by datadate to ensure PIT correctness
        latest = (
            fundamentals.filter(pl.col("datadate") <= filing_cutoff)
            .sort(["permno", "datadate"])
            .group_by("permno")
            .agg(
                [
                    pl.col("ni").last().alias("net_income"),
                    pl.col("ceq").last().alias("common_equity"),
                ]
            )
            .filter(pl.col("common_equity") > 0)  # Positive equity only
        )

        # Compute ROE
        roe = latest.with_columns(
            (pl.col("net_income") / pl.col("common_equity")).alias("factor_value")
        ).select(["permno", "factor_value"])

        return roe


class SizeFactor:
    """
    Log Market Capitalization Size Factor.

    Log of market cap from CRSP (price * shares outstanding).
    """

    @property
    def name(self) -> str:
        return "log_market_cap"

    @property
    def category(self) -> str:
        return "size"

    @property
    def description(self) -> str:
        return "Log of market capitalization"

    @property
    def requires_fundamentals(self) -> bool:
        return False

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute log market cap.
        """

        # Get most recent price/shares per security
        latest_prices = (
            prices.filter(pl.col("date") <= as_of_date)
            .group_by("permno")
            .agg(
                [
                    pl.col("prc").last().alias("price"),
                    pl.col("shrout").last().alias("shares_out"),
                ]
            )
        )

        # Compute log market cap
        # shrout is in thousands, price may be negative (bid/ask midpoint)
        size = (
            latest_prices.with_columns(
                (pl.col("price").abs() * pl.col("shares_out") * 1000).alias("market_cap")
            )
            .filter(pl.col("market_cap") > 0)
            .with_columns(pl.col("market_cap").log().alias("factor_value"))
            .select(["permno", "factor_value"])
        )

        return size


class RealizedVolFactor:
    """
    60-day Realized Volatility (Low-Vol) Factor.

    Standard deviation of daily returns over past 60 trading days.
    Lower volatility is typically considered more attractive.
    """

    @property
    def name(self) -> str:
        return "realized_vol"

    @property
    def category(self) -> str:
        return "low_vol"

    @property
    def description(self) -> str:
        return "60-day realized volatility of daily returns"

    @property
    def requires_fundamentals(self) -> bool:
        return False

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """
        Compute 60-day realized volatility.
        """
        from datetime import timedelta

        import numpy as np

        # 60 trading days lookback
        start_date = as_of_date - timedelta(days=90)  # ~60 trading days

        # Filter to lookback window
        window_data = prices.filter((pl.col("date") >= start_date) & (pl.col("date") <= as_of_date))

        # Compute volatility per security
        vol = (
            window_data.group_by("permno")
            .agg(
                [
                    pl.col("ret").std().alias("factor_value"),
                    pl.col("ret").count().alias("n_obs"),
                ]
            )
            .filter(pl.col("n_obs") >= 40)  # Require ~2/3 of trading days
            .select(["permno", "factor_value"])
        )

        # Annualize volatility (multiply by sqrt(252))
        vol = vol.with_columns((pl.col("factor_value") * np.sqrt(252)).alias("factor_value"))

        return vol


# Registry of canonical factors
CANONICAL_FACTORS: dict[str, type] = {
    "momentum_12_1": MomentumFactor,
    "book_to_market": BookToMarketFactor,
    "roe": ROEFactor,
    "log_market_cap": SizeFactor,
    "realized_vol": RealizedVolFactor,
}

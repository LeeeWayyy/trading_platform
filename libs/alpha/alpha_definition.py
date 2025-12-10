"""
Alpha signal definition framework.

Provides Protocol for alpha signal computation and base class with common utilities.
All alpha signals must be point-in-time (PIT) correct.
"""

from __future__ import annotations

import hashlib
import logging
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import polars as pl

from libs.alpha.exceptions import AlphaValidationError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@runtime_checkable
class AlphaDefinition(Protocol):
    """Protocol for alpha signal computation.

    All alpha implementations must satisfy this protocol.
    Signals must be point-in-time correct (no look-ahead bias).
    """

    @property
    def name(self) -> str:
        """Unique identifier for the alpha."""
        ...

    @property
    def category(self) -> str:
        """Category: 'momentum', 'value', 'quality', 'reversal', 'volatility'."""
        ...

    @property
    def universe_filter(self) -> str:
        """Universe filter: 'all', 'large_cap', 'mid_cap', 'small_cap'."""
        ...

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute alpha signal as of given date.

        Args:
            prices: CRSP price data with columns [permno, date, ret, prc, vol, shrout]
                    Must be filtered to date <= as_of_date before calling
            fundamentals: Compustat data with PIT-correct filing dates, or None
            as_of_date: Point-in-time date for computation

        Returns:
            DataFrame with columns [permno, date, signal]
            - permno: Stock identifier
            - date: The as_of_date
            - signal: Alpha signal value (higher = more bullish)
        """
        ...


@dataclass(frozen=True)
class AlphaResult:
    """Result of alpha computation with full metadata for reproducibility."""

    alpha_name: str
    as_of_date: date
    signals: pl.DataFrame  # [permno, date, signal]
    dataset_version_ids: dict[str, str]  # {'crsp': 'v1.2.3', 'compustat': 'v1.0.1'}
    computation_timestamp: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
    n_stocks: int = 0
    coverage: float = 0.0  # Fraction of universe with valid signal

    def __post_init__(self) -> None:
        """Compute derived fields."""
        if self.signals is not None and self.signals.height > 0:
            object.__setattr__(self, "n_stocks", self.signals.height)
            valid_signals = self.signals.filter(pl.col("signal").is_not_null())
            if self.signals.height > 0:
                object.__setattr__(
                    self, "coverage", valid_signals.height / self.signals.height
                )

    @property
    def reproducibility_hash(self) -> str:
        """SHA-256 hash of inputs for reproducibility tracking."""
        hash_input = (
            f"{self.alpha_name}|"
            f"{self.as_of_date.isoformat()}|"
            f"{sorted(self.dataset_version_ids.items())}"
        )
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


class BaseAlpha(ABC):
    """Base class for alpha implementations with common utilities.

    Provides:
    - Cross-sectional z-score normalization
    - Winsorization
    - Universe filtering
    - Validation
    """

    def __init__(
        self,
        winsorize_pct: float = 0.01,
        universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = "all",
    ):
        """Initialize base alpha.

        Args:
            winsorize_pct: Percentile for winsorization (0.01 = 1%/99%)
            universe_filter: 'all', 'large_cap', 'mid_cap', 'small_cap'
        """
        self._winsorize_pct = winsorize_pct
        self._universe_filter: Literal["all", "large_cap", "mid_cap", "small_cap"] = (
            universe_filter
        )

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the alpha."""
        ...

    @property
    @abstractmethod
    def category(self) -> str:
        """Category: 'momentum', 'value', 'quality', 'reversal', 'volatility'."""
        ...

    @property
    def universe_filter(self) -> str:
        """Universe filter."""
        return self._universe_filter

    @abstractmethod
    def _compute_raw(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute raw (unnormalized) signal. Subclasses implement this.

        Returns:
            DataFrame with columns [permno, raw_signal]
        """
        ...

    def compute(
        self,
        prices: pl.DataFrame,
        fundamentals: pl.DataFrame | None,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute normalized alpha signal.

        1. Apply universe filter (if not 'all')
        2. Compute raw signal via _compute_raw
        3. Winsorize extreme values
        4. Cross-sectional z-score normalization
        5. Validate output

        Returns:
            DataFrame with columns [permno, date, signal]
        """
        # Apply universe filter if specified
        filtered_prices = prices
        if self._universe_filter != "all" and prices.height > 0:
            # Compute market cap for filtering (use latest available price)
            latest_prices = (
                prices.filter(pl.col("date") <= as_of_date)
                .sort(["permno", "date"], descending=[False, True])
                .group_by("permno")
                .first()
            )
            if "prc" in latest_prices.columns and "shrout" in latest_prices.columns:
                with_mcap = latest_prices.with_columns([
                    (pl.col("prc").abs() * pl.col("shrout")).alias("market_cap")
                ])
                filtered_universe = self.filter_universe(with_mcap, self._universe_filter)
                valid_permnos = filtered_universe.select("permno")
                filtered_prices = prices.join(valid_permnos, on="permno", how="semi")

        # Compute raw signal
        raw = self._compute_raw(filtered_prices, fundamentals, as_of_date)

        if raw.height == 0:
            logger.warning(f"Alpha {self.name}: no stocks returned for {as_of_date}")
            return pl.DataFrame(
                schema={"permno": pl.Int64, "date": pl.Date, "signal": pl.Float64}
            )

        # Winsorize
        winsorized = self._winsorize(raw, "raw_signal")

        # Z-score normalize
        normalized = self._zscore_normalize(winsorized, "raw_signal")

        # Add date and rename
        result = normalized.select([
            pl.col("permno"),
            pl.lit(as_of_date).alias("date"),
            pl.col("zscore").alias("signal"),
        ])

        # Validate
        self._validate(result, as_of_date)

        return result

    def _winsorize(self, df: pl.DataFrame, col: str) -> pl.DataFrame:
        """Winsorize column at specified percentiles."""
        if self._winsorize_pct <= 0:
            return df

        lower = df.select(pl.col(col).quantile(self._winsorize_pct)).item()
        upper = df.select(pl.col(col).quantile(1 - self._winsorize_pct)).item()

        return df.with_columns([
            pl.col(col).clip(lower, upper).alias(col)
        ])

    def _zscore_normalize(self, df: pl.DataFrame, col: str) -> pl.DataFrame:
        """Cross-sectional z-score normalization."""
        mean = df.select(pl.col(col).mean()).item()
        std = df.select(pl.col(col).std()).item()

        if std is None or std == 0 or math.isnan(std):
            logger.warning(f"Alpha {self.name}: zero/nan std, returning zeros")
            return df.with_columns(pl.lit(0.0).alias("zscore"))

        return df.with_columns([
            ((pl.col(col) - mean) / std).alias("zscore")
        ])

    def _validate(self, result: pl.DataFrame, as_of_date: date) -> None:
        """Validate output DataFrame."""
        required_cols = {"permno", "date", "signal"}
        actual_cols = set(result.columns)

        if not required_cols.issubset(actual_cols):
            missing = required_cols - actual_cols
            raise AlphaValidationError(
                f"Alpha {self.name}: missing columns {missing}"
            )

        # Check for inf values
        inf_count = result.filter(
            pl.col("signal").is_infinite()
        ).height

        if inf_count > 0:
            raise AlphaValidationError(
                f"Alpha {self.name}: {inf_count} infinite values on {as_of_date}"
            )

        # Check z-score range (should be within +/- 5 sigma after winsorization)
        max_abs = result.select(pl.col("signal").abs().max()).item()
        if max_abs is not None and max_abs > 5:
            logger.warning(
                f"Alpha {self.name}: max |z-score| = {max_abs:.2f} > 5 on {as_of_date}"
            )

    @staticmethod
    def filter_universe(
        df: pl.DataFrame,
        filter_type: Literal["all", "large_cap", "mid_cap", "small_cap"],
        market_cap_col: str = "market_cap",
    ) -> pl.DataFrame:
        """Filter stocks by market cap quintile.

        Args:
            df: DataFrame with market_cap column
            filter_type: Universe filter type
            market_cap_col: Name of market cap column

        Returns:
            Filtered DataFrame
        """
        if filter_type == "all":
            return df

        if market_cap_col not in df.columns:
            logger.warning(f"No {market_cap_col} column, returning all stocks")
            return df

        # Compute rank-based percentiles for quintiles
        df_ranked = df.with_columns(
            pl.col(market_cap_col)
            .rank(method="ordinal")
            .over(pl.lit(1))
            .alias("_mkt_cap_rank")
        )

        n_stocks = df_ranked.height
        if n_stocks < 5:
            return df

        percentile_col = (pl.col("_mkt_cap_rank") / pl.lit(float(n_stocks))).alias(
            "_mkt_cap_pct"
        )
        df_ranked = df_ranked.with_columns(percentile_col)

        if filter_type == "large_cap":
            # Top quintile (80-100%)
            return df_ranked.filter(pl.col("_mkt_cap_pct") > 0.8).drop(
                ["_mkt_cap_rank", "_mkt_cap_pct"]
            )
        elif filter_type == "mid_cap":
            # Middle 3 quintiles (20-80%)
            return df_ranked.filter(
                (pl.col("_mkt_cap_pct") > 0.2)
                & (pl.col("_mkt_cap_pct") <= 0.8)
            ).drop(["_mkt_cap_rank", "_mkt_cap_pct"])
        elif filter_type == "small_cap":
            # Bottom quintile (0-20%)
            return df_ranked.filter(pl.col("_mkt_cap_pct") <= 0.2).drop(
                ["_mkt_cap_rank", "_mkt_cap_pct"]
            )

        return df

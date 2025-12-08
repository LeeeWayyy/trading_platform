"""
Alpha metrics computation with dual backend support.

Provides AlphaMetricsAdapter that uses Qlib when available, with local Polars fallback.
All metrics are computed with consistent NaN handling.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import polars as pl

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Minimum observations for reliable metric computation
MIN_OBSERVATIONS = 30
LOW_COVERAGE_THRESHOLD = 0.5


def _qlib_available() -> bool:
    """Check if Qlib is available and can be used for metrics."""
    try:
        import qlib
        from qlib.contrib.evaluate import risk_analysis  # noqa: F401

        logger.debug(f"Qlib {qlib.__version__} available")
        return True
    except ImportError as e:
        logger.info(f"Qlib not available ({e}), using local Polars backend")
        return False
    except Exception as e:
        logger.warning(f"Qlib import error: {e}, falling back to local backend")
        return False


QLIB_INSTALLED = _qlib_available()


@dataclass
class ICResult:
    """Result of IC computation."""

    pearson_ic: float
    rank_ic: float
    n_observations: int
    coverage: float


@dataclass
class ICIRResult:
    """Result of ICIR computation."""

    icir: float
    mean_ic: float
    std_ic: float
    n_periods: int


@dataclass
class DecayCurveResult:
    """Result of decay curve computation."""

    decay_curve: pl.DataFrame  # [horizon, ic, rank_ic]
    half_life: float | None  # Estimated half-life in days


class LocalMetrics:
    """Pure Polars implementation of alpha metrics."""

    @staticmethod
    def pearson_ic(signal: pl.Series, returns: pl.Series) -> float:
        """Compute Pearson IC (cross-sectional correlation).

        Args:
            signal: Alpha signal values
            returns: Forward returns

        Returns:
            Pearson correlation coefficient, or NaN if insufficient data
        """
        if signal.len() != returns.len():
            raise ValueError("Signal and returns must have same length")

        # Filter valid pairs
        valid_mask = signal.is_not_null() & returns.is_not_null()
        n_valid = valid_mask.sum()

        if n_valid < MIN_OBSERVATIONS:
            logger.warning(
                "IC computation: insufficient data",
                extra={"n_valid": n_valid, "threshold": MIN_OBSERVATIONS},
            )
            return float("nan")

        sig_valid = signal.filter(valid_mask)
        ret_valid = returns.filter(valid_mask)

        # Log coverage warning
        coverage = n_valid / signal.len()
        if coverage < LOW_COVERAGE_THRESHOLD:
            logger.warning(
                "IC computation: low coverage",
                extra={"coverage": coverage, "threshold": LOW_COVERAGE_THRESHOLD},
            )

        # Pearson correlation using DataFrame approach
        df = pl.DataFrame({"signal": sig_valid, "returns": ret_valid})
        result = df.select(pl.corr("signal", "returns")).item()
        return result if result is not None else float("nan")

    @staticmethod
    def rank_ic(signal: pl.Series, returns: pl.Series) -> float:
        """Compute Rank IC (Spearman correlation).

        More robust than Pearson as it's not affected by outliers.

        Args:
            signal: Alpha signal values
            returns: Forward returns

        Returns:
            Spearman correlation coefficient, or NaN if insufficient data
        """
        if signal.len() != returns.len():
            raise ValueError("Signal and returns must have same length")

        # Filter valid pairs
        valid_mask = signal.is_not_null() & returns.is_not_null()
        n_valid = valid_mask.sum()

        if n_valid < MIN_OBSERVATIONS:
            return float("nan")

        sig_valid = signal.filter(valid_mask)
        ret_valid = returns.filter(valid_mask)

        # Rank both series and compute Pearson correlation of ranks
        sig_rank = sig_valid.rank(method="average")
        ret_rank = ret_valid.rank(method="average")

        df = pl.DataFrame({"sig_rank": sig_rank, "ret_rank": ret_rank})
        result = df.select(pl.corr("sig_rank", "ret_rank")).item()
        return result if result is not None else float("nan")

    @staticmethod
    def compute_ic(
        signal: pl.Series, returns: pl.Series, method: str = "rank"
    ) -> float:
        """Compute IC using specified method.

        Args:
            signal: Alpha signal values
            returns: Forward returns
            method: 'pearson' or 'rank'

        Returns:
            IC value
        """
        if method == "pearson":
            return LocalMetrics.pearson_ic(signal, returns)
        elif method == "rank":
            return LocalMetrics.rank_ic(signal, returns)
        else:
            raise ValueError(f"Unknown method: {method}")

    @staticmethod
    def hit_rate(signal: pl.Series, returns: pl.Series) -> float:
        """Compute hit rate (% of correct direction predictions).

        Args:
            signal: Alpha signal values
            returns: Forward returns

        Returns:
            Hit rate [0, 1], or NaN if insufficient data
        """
        valid_mask = signal.is_not_null() & returns.is_not_null()
        n_valid = valid_mask.sum()

        if n_valid < MIN_OBSERVATIONS:
            return float("nan")

        sig_valid = signal.filter(valid_mask)
        ret_valid = returns.filter(valid_mask)

        # Compare signs (both positive or both negative)
        correct = ((sig_valid > 0) & (ret_valid > 0)) | (
            (sig_valid < 0) & (ret_valid < 0)
        )
        # Exclude zero signals/returns from hit rate
        non_zero = (sig_valid != 0) & (ret_valid != 0)
        n_non_zero = non_zero.sum()

        if n_non_zero == 0:
            return float("nan")

        hit_count = (correct & non_zero).sum()
        return hit_count / n_non_zero

    @staticmethod
    def coverage(signal: pl.Series, universe_size: int) -> float:
        """Compute signal coverage (% of universe with valid signal).

        Args:
            signal: Alpha signal values
            universe_size: Total universe size

        Returns:
            Coverage [0, 1]
        """
        if universe_size <= 0:
            return 0.0

        valid = signal.is_not_null() & (signal != 0)
        return valid.sum() / universe_size

    @staticmethod
    def autocorrelation(signal: pl.Series, lag: int = 1) -> float:
        """Compute signal autocorrelation at specified lag.

        Args:
            signal: Alpha signal time series (sorted by date)
            lag: Number of periods for lag

        Returns:
            Autocorrelation coefficient
        """
        if signal.len() <= lag:
            return float("nan")

        # Use pandas for autocorrelation (more robust)
        try:
            series_pd = signal.drop_nulls().to_pandas()
            if len(series_pd) <= lag:
                return float("nan")
            return series_pd.autocorr(lag=lag)
        except Exception:
            return float("nan")

    @staticmethod
    def long_short_spread(
        signal: pl.Series, returns: pl.Series, n_deciles: int = 10
    ) -> float:
        """Compute long/short spread (top decile - bottom decile returns).

        Args:
            signal: Alpha signal values
            returns: Forward returns
            n_deciles: Number of deciles (default 10)

        Returns:
            Spread in returns
        """
        valid_mask = signal.is_not_null() & returns.is_not_null()
        n_valid = valid_mask.sum()

        if n_valid < MIN_OBSERVATIONS:
            return float("nan")

        # Create DataFrame for quantile computation
        df = pl.DataFrame({
            "signal": signal.filter(valid_mask),
            "returns": returns.filter(valid_mask),
        })

        # Compute quantile ranks
        df = df.with_columns([
            (pl.col("signal").rank(method="ordinal") / pl.col("signal").count() * n_deciles)
            .ceil()
            .cast(pl.Int64)
            .clip(1, n_deciles)
            .alias("decile")
        ])

        # Top and bottom decile returns
        top_return = df.filter(pl.col("decile") == n_deciles).select(
            pl.col("returns").mean()
        ).item()
        bottom_return = df.filter(pl.col("decile") == 1).select(
            pl.col("returns").mean()
        ).item()

        if top_return is None or bottom_return is None:
            return float("nan")

        return float(top_return) - float(bottom_return)


class AlphaMetricsAdapter:
    """Compute alpha metrics with Qlib or local fallback.

    Provides a unified interface for alpha signal analysis metrics.
    Uses Qlib when available for battle-tested implementations,
    falls back to local Polars implementation otherwise.
    """

    def __init__(self, prefer_qlib: bool = True):
        """Initialize metrics adapter.

        Args:
            prefer_qlib: If True, use Qlib when available
        """
        self._use_qlib = prefer_qlib and QLIB_INSTALLED
        self._local = LocalMetrics()

        if self._use_qlib:
            logger.debug("AlphaMetricsAdapter using Qlib backend")
        else:
            logger.debug("AlphaMetricsAdapter using local Polars backend")

    @property
    def backend(self) -> str:
        """Return current backend name."""
        return "qlib" if self._use_qlib else "polars"

    def compute_ic(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        method: Literal["pearson", "rank"] = "rank",
    ) -> ICResult:
        """Compute Information Coefficient.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            method: 'pearson' or 'rank'

        Returns:
            ICResult with both Pearson and Rank IC
        """
        # Join signal and returns
        joined = signal.join(returns, on=["permno", "date"], how="inner")

        if joined.height == 0:
            return ICResult(
                pearson_ic=float("nan"),
                rank_ic=float("nan"),
                n_observations=0,
                coverage=0.0,
            )

        sig_series = joined.get_column("signal")
        ret_series = joined.get_column("return")

        pearson = self._local.pearson_ic(sig_series, ret_series)
        rank = self._local.rank_ic(sig_series, ret_series)

        # Coverage = fraction of joined pairs with BOTH valid signal AND return
        # Use same valid mask as IC calculation to reflect true data quality
        valid_mask = sig_series.is_not_null() & ret_series.is_not_null()
        n_valid = valid_mask.sum()
        coverage = n_valid / joined.height if joined.height > 0 else 0.0

        logger.debug(
            "IC computed",
            extra={
                "pearson_ic": pearson,
                "rank_ic": rank,
                "method": method,
                "coverage": coverage,
            },
        )

        return ICResult(
            pearson_ic=pearson,
            rank_ic=rank,
            n_observations=joined.height,
            coverage=coverage,
        )

    def compute_icir(
        self,
        daily_ic: pl.DataFrame,
        window: int = 20,
    ) -> ICIRResult:
        """Compute ICIR (IC Information Ratio).

        ICIR = mean(IC) / std(IC) over rolling window

        Args:
            daily_ic: DataFrame with [date, ic] or [date, rank_ic]
            window: Rolling window size (default 20 days)

        Returns:
            ICIRResult with ICIR and components
        """
        ic_col = "rank_ic" if "rank_ic" in daily_ic.columns else "ic"

        if daily_ic.height < window:
            return ICIRResult(
                icir=float("nan"),
                mean_ic=float("nan"),
                std_ic=float("nan"),
                n_periods=daily_ic.height,
            )

        ic_series = daily_ic.get_column(ic_col).drop_nulls()
        mean_ic_raw = ic_series.mean()
        std_ic_raw = ic_series.std()

        # Cast to float with proper type handling
        mean_ic_val: float = float("nan")
        std_ic_val: float = float("nan")
        if mean_ic_raw is not None and isinstance(mean_ic_raw, int | float):
            mean_ic_val = float(mean_ic_raw)
        if std_ic_raw is not None and isinstance(std_ic_raw, int | float):
            std_ic_val = float(std_ic_raw)

        if std_ic_val == 0 or math.isnan(std_ic_val):
            icir = float("nan")
        else:
            icir = mean_ic_val / std_ic_val

        return ICIRResult(
            icir=icir,
            mean_ic=mean_ic_val,
            std_ic=std_ic_val,
            n_periods=ic_series.len(),
        )

    def compute_grouped_ic(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        sector_mapping: pl.DataFrame,
    ) -> pl.DataFrame:
        """Compute IC per sector (GICS).

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            sector_mapping: DataFrame with [permno, date, gics_sector]

        Returns:
            DataFrame with [gics_sector, ic, rank_ic, n_stocks]
        """
        # Join all data
        joined = (
            signal.join(returns, on=["permno", "date"], how="inner")
            .join(sector_mapping, on=["permno", "date"], how="inner")
        )

        if joined.height == 0:
            return pl.DataFrame(
                schema={
                    "gics_sector": pl.Utf8,
                    "ic": pl.Float64,
                    "rank_ic": pl.Float64,
                    "n_stocks": pl.Int64,
                }
            )

        # Compute IC per sector
        results = []
        sectors = joined.select("gics_sector").unique().to_series().to_list()

        for sector in sectors:
            sector_data = joined.filter(pl.col("gics_sector") == sector)
            if sector_data.height < MIN_OBSERVATIONS:
                continue

            sig_series = sector_data.get_column("signal")
            ret_series = sector_data.get_column("return")

            pearson = self._local.pearson_ic(sig_series, ret_series)
            rank = self._local.rank_ic(sig_series, ret_series)

            results.append({
                "gics_sector": sector,
                "ic": pearson,
                "rank_ic": rank,
                "n_stocks": sector_data.height,
            })

        if not results:
            return pl.DataFrame(
                schema={
                    "gics_sector": pl.Utf8,
                    "ic": pl.Float64,
                    "rank_ic": pl.Float64,
                    "n_stocks": pl.Int64,
                }
            )

        return pl.DataFrame(results)

    def compute_decay_curve(
        self,
        signal: pl.DataFrame,
        returns_by_horizon: dict[int, pl.DataFrame],
    ) -> DecayCurveResult:
        """Compute IC decay curve across horizons.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns_by_horizon: Dict mapping horizon (days) to returns DataFrame
                Each DataFrame has [permno, date, return]

        Returns:
            DecayCurveResult with decay curve and estimated half-life
        """
        results = []

        for horizon, returns in sorted(returns_by_horizon.items()):
            ic_result = self.compute_ic(signal, returns, method="rank")
            results.append({
                "horizon": horizon,
                "ic": ic_result.pearson_ic,
                "rank_ic": ic_result.rank_ic,
            })

        if not results:
            return DecayCurveResult(
                decay_curve=pl.DataFrame(
                    schema={"horizon": pl.Int64, "ic": pl.Float64, "rank_ic": pl.Float64}
                ),
                half_life=None,
            )

        decay_df = pl.DataFrame(results)

        # Estimate half-life (simple linear interpolation)
        half_life = self._estimate_half_life(decay_df)

        return DecayCurveResult(decay_curve=decay_df, half_life=half_life)

    def _estimate_half_life(self, decay_df: pl.DataFrame) -> float | None:
        """Estimate half-life from decay curve.

        Half-life is the horizon where IC drops to half of initial value.
        """
        if decay_df.height < 2:
            return None

        sorted_df = decay_df.sort("horizon")
        horizons = sorted_df.get_column("horizon").to_list()
        ics = sorted_df.get_column("rank_ic").to_list()

        if not ics or math.isnan(ics[0]) or ics[0] <= 0:
            return None

        half_ic = ics[0] / 2

        # Find where IC drops below half
        for i, ic in enumerate(ics):
            if math.isnan(ic):
                continue
            if ic <= half_ic:
                if i == 0:
                    return float(horizons[0])
                # Linear interpolation
                prev_ic = ics[i - 1]
                if math.isnan(prev_ic):
                    return float(horizons[i])
                slope = (ic - prev_ic) / (horizons[i] - horizons[i - 1])
                if slope == 0:
                    return float(horizons[i])
                half_life = horizons[i - 1] + (half_ic - prev_ic) / slope
                return float(max(0, half_life))

        # IC didn't drop to half, return None
        return None

    def compute_autocorrelation(
        self,
        signal_ts: pl.DataFrame,
        lags: list[int] | None = None,
    ) -> dict[int, float]:
        """Compute signal autocorrelation at multiple lags.

        Args:
            signal_ts: DataFrame with [date, signal] (cross-sectional mean per date)
            lags: List of lags to compute (default [1, 5, 20])

        Returns:
            Dict mapping lag to autocorrelation
        """
        if lags is None:
            lags = [1, 5, 20]

        if signal_ts.height == 0:
            return {lag: float("nan") for lag in lags}

        sorted_ts = signal_ts.sort("date")
        signal_series = sorted_ts.get_column("signal")

        return {
            lag: self._local.autocorrelation(signal_series, lag) for lag in lags
        }

    def compute_hit_rate(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
    ) -> float:
        """Compute hit rate (% correct direction predictions).

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]

        Returns:
            Hit rate [0, 1]
        """
        joined = signal.join(returns, on=["permno", "date"], how="inner")

        if joined.height == 0:
            return float("nan")

        return self._local.hit_rate(
            joined.get_column("signal"),
            joined.get_column("return"),
        )

    def compute_coverage(
        self,
        signal: pl.DataFrame,
        universe_size: int,
    ) -> float:
        """Compute signal coverage.

        Args:
            signal: DataFrame with [permno, date, signal]
            universe_size: Total universe size

        Returns:
            Coverage [0, 1]
        """
        if signal.height == 0:
            return 0.0

        return self._local.coverage(
            signal.get_column("signal"),
            universe_size,
        )

    def compute_long_short_spread(
        self,
        signal: pl.DataFrame,
        returns: pl.DataFrame,
        n_deciles: int = 10,
    ) -> float:
        """Compute long/short spread (PIT-correct per-date deciles).

        Computes decile ranks within each date to avoid look-ahead bias,
        then averages the daily long-short spreads.

        Args:
            signal: DataFrame with [permno, date, signal]
            returns: DataFrame with [permno, date, return]
            n_deciles: Number of deciles (default 10)

        Returns:
            Average of daily (top decile return - bottom decile return)
        """
        joined = signal.join(returns, on=["permno", "date"], how="inner")

        if joined.height == 0:
            return float("nan")

        # Filter to valid observations BEFORE ranking to avoid null-skewed boundaries
        valid_joined = joined.filter(
            pl.col("signal").is_not_null() & pl.col("return").is_not_null()
        )

        if valid_joined.height == 0:
            return float("nan")

        # Compute per-date decile ranks to avoid look-ahead bias
        # Use adaptive bucketing: min(n_deciles, count) to handle small universes
        with_decile = valid_joined.with_columns([
            pl.col("signal").len().over("date").alias("_daily_count"),
            pl.col("signal").rank(method="ordinal").over("date").alias("_rank"),
        ]).with_columns([
            # Effective deciles = min(n_deciles, daily_count)
            pl.when(pl.col("_daily_count") >= n_deciles)
            .then(n_deciles)
            .otherwise(pl.col("_daily_count"))
            .alias("_eff_deciles")
        ]).with_columns([
            # Assign decile using (rank - 1) / count * eff_deciles + 1 formula
            # This ensures all deciles 1..eff_deciles are populated
            pl.when(pl.col("_eff_deciles") < 2)
            .then(pl.lit(None).cast(pl.Int64))  # Skip days with < 2 stocks
            .otherwise(
                ((pl.col("_rank") - 1) / pl.col("_daily_count") * pl.col("_eff_deciles"))
                .floor()
                .cast(pl.Int64)
                + 1
            )
            .alias("decile")
        ])

        # Filter out days with insufficient stocks for meaningful spread
        with_decile = with_decile.filter(pl.col("decile").is_not_null())

        if with_decile.height == 0:
            return float("nan")

        # Compute per-date spread using effective top and bottom deciles
        daily_spreads = (
            with_decile.group_by("date")
            .agg([
                pl.col("_eff_deciles").first().alias("eff_deciles"),
                pl.col("return")
                .filter(pl.col("decile") == pl.col("_eff_deciles"))
                .mean()
                .alias("top_return"),
                pl.col("return")
                .filter(pl.col("decile") == 1)
                .mean()
                .alias("bottom_return"),
            ])
            .with_columns([
                (pl.col("top_return") - pl.col("bottom_return")).alias("spread")
            ])
        )

        # Average daily spreads (drop days with insufficient data)
        valid_spreads = daily_spreads.filter(pl.col("spread").is_not_null())
        if valid_spreads.height == 0:
            return float("nan")

        return valid_spreads.select(pl.col("spread").mean()).item() or float("nan")

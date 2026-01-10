"""
Factor covariance estimation for risk models.

This module implements factor return extraction via cross-sectional regression
and factor covariance matrix estimation with exponential decay weighting,
Newey-West HAC correction, and Ledoit-Wolf shrinkage.

All computations are point-in-time (PIT) correct.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl
import statsmodels.api as sm  # type: ignore[import-untyped]
from numpy.typing import NDArray
from sklearn.covariance import LedoitWolf  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from libs.factors import FactorBuilder

logger = logging.getLogger(__name__)


# Canonical factor ordering - all covariance matrices use this order
CANONICAL_FACTOR_ORDER = [
    "momentum_12_1",
    "book_to_market",
    "roe",
    "log_market_cap",
    "realized_vol",
]


class InsufficientDataError(Exception):
    """Raised when there is insufficient data for estimation."""

    pass


@dataclass
class CovarianceConfig:
    """Configuration for covariance estimation."""

    halflife_days: int = 60  # Exponential decay half-life
    min_observations: int = 126  # Minimum trading days required (~6 months)
    newey_west_lags: int = 5  # HAC lag parameter
    shrinkage_intensity: float | None = None  # None = Ledoit-Wolf optimal
    min_stocks_per_day: int = 100  # Minimum stocks for valid regression
    lookback_days: int = 252  # Calendar days for factor return calculation


@dataclass
class CovarianceResult:
    """
    Result of factor covariance estimation with metadata.

    Includes the covariance matrix and all necessary provenance metadata
    for reproducibility.
    """

    factor_covariance: NDArray[np.floating[Any]]  # K x K covariance matrix (PSD guaranteed)
    factor_names: list[str]  # Factor names in canonical order
    factor_returns: pl.DataFrame  # Daily factor returns
    as_of_date: date
    dataset_version_ids: dict[str, str]
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    shrinkage_intensity: float = 0.0  # Actual shrinkage applied
    effective_observations: float = 0.0  # Effective sample size (n_eff)
    reproducibility_hash: str = ""
    skipped_days: list[date] = field(default_factory=list)
    halflife_days: int = 60  # Actual halflife used (for storage)

    def validate(self) -> list[str]:
        """
        Validate covariance matrix for correctness.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Check for NaN
        if np.any(np.isnan(self.factor_covariance)):
            errors.append("Covariance matrix contains NaN values")

        # Check for Inf
        if np.any(np.isinf(self.factor_covariance)):
            errors.append("Covariance matrix contains infinite values")

        # Check PSD (all eigenvalues >= 0)
        eigenvalues = np.linalg.eigvalsh(self.factor_covariance)
        if np.any(eigenvalues < -1e-10):  # Small tolerance for numerical error
            errors.append(f"Covariance matrix is not PSD: min eigenvalue = {eigenvalues.min():.6e}")

        # Check correlations in [-1, 1]
        diag = np.diag(self.factor_covariance)
        if np.any(diag <= 0):
            errors.append("Diagonal elements must be positive (variances)")
        else:
            # Compute correlation matrix
            std = np.sqrt(diag)
            corr = self.factor_covariance / np.outer(std, std)
            if np.any(np.abs(corr) > 1.0 + 1e-6):  # Small tolerance
                errors.append("Correlation values outside [-1, 1]")

        return errors

    def to_storage_format(self) -> pl.DataFrame:
        """
        Convert to long format for parquet storage.

        Storage contract matches P4T2_TASK.md schema:
        as_of_date, factor_i, factor_j, covariance, correlation,
        halflife_days, shrinkage_intensity, dataset_version_id
        """
        rows = []
        n_factors = len(self.factor_names)

        # Compute correlation matrix
        diag = np.diag(self.factor_covariance)
        std = np.sqrt(np.maximum(diag, 1e-10))
        corr_matrix = self.factor_covariance / np.outer(std, std)

        for i in range(n_factors):
            for j in range(n_factors):
                rows.append(
                    {
                        "as_of_date": self.as_of_date,
                        "factor_i": self.factor_names[i],
                        "factor_j": self.factor_names[j],
                        "covariance": float(self.factor_covariance[i, j]),
                        "correlation": float(corr_matrix[i, j]),
                        "halflife_days": self.halflife_days,
                        "shrinkage_intensity": self.shrinkage_intensity,
                        "dataset_version_id": self._format_version_ids(),
                    }
                )

        return pl.DataFrame(rows)

    def _format_version_ids(self) -> str:
        """Format dataset version IDs as pipe-separated string."""
        return "|".join(f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items()))


class FactorCovarianceEstimator:
    """
    Estimate factor covariance matrix from stock returns.

    Uses cross-sectional regression to extract daily factor returns,
    then estimates the factor covariance matrix with:
    - Exponential decay weighting
    - Newey-West HAC correction for autocorrelation
    - Ledoit-Wolf shrinkage for stability
    - PSD enforcement via eigenvalue clipping
    """

    def __init__(
        self,
        factor_builder: "FactorBuilder",
        config: CovarianceConfig | None = None,
    ):
        """
        Initialize the estimator.

        Args:
            factor_builder: FactorBuilder instance for factor exposure retrieval
            config: Configuration for estimation parameters
        """
        self.factor_builder = factor_builder
        self.config = config or CovarianceConfig()
        self.factor_names = CANONICAL_FACTOR_ORDER.copy()

    def _validate_daily_inputs(
        self,
        exposures: pl.DataFrame,
        returns: pl.DataFrame,
        current_date: date,
    ) -> tuple[pl.DataFrame, pl.DataFrame, list[str]]:
        """
        Validate and clean daily inputs for regression.

        Args:
            exposures: Factor exposures DataFrame (permno, factor columns)
            returns: Stock returns DataFrame (permno, ret)
            current_date: Date for logging context

        Returns:
            Tuple of (clean_exposures, clean_returns, warnings)

        Raises:
            InsufficientDataError: If fewer than min_stocks_per_day remain
        """
        warnings: list[str] = []

        # Filter out NaN/inf in returns
        returns_clean = returns.filter(pl.col("ret").is_not_nan() & pl.col("ret").is_finite())
        n_filtered_ret = returns.height - returns_clean.height
        if n_filtered_ret > 0:
            warnings.append(f"Filtered {n_filtered_ret} stocks with NaN/inf returns")

        # Filter out NaN/inf in exposures (any factor column)
        factor_cols = [c for c in exposures.columns if c in self.factor_names]
        exposure_filter = pl.lit(True)
        for col in factor_cols:
            exposure_filter = exposure_filter & pl.col(col).is_not_nan() & pl.col(col).is_finite()
        exposures_clean = exposures.filter(exposure_filter)
        n_filtered_exp = exposures.height - exposures_clean.height
        if n_filtered_exp > 0:
            warnings.append(f"Filtered {n_filtered_exp} stocks with NaN/inf exposures")

        # Join on permno to get matched pairs
        joined = exposures_clean.join(returns_clean, on="permno", how="inner")

        if joined.height < self.config.min_stocks_per_day:
            raise InsufficientDataError(
                f"Only {joined.height} stocks available on {current_date}, "
                f"minimum required: {self.config.min_stocks_per_day}"
            )

        # Split back into exposures and returns
        clean_exposures = joined.select(["permno"] + factor_cols)
        clean_returns = joined.select(["permno", "ret"])

        return clean_exposures, clean_returns, warnings

    def estimate_factor_returns(
        self,
        start_date: date,
        end_date: date,
    ) -> tuple[pl.DataFrame, dict[str, str], list[date]]:
        """
        Compute daily factor returns via cross-sectional regression.

        For each day t, regresses stock returns at t on lagged factor
        exposures from t-1 using WLS with sqrt(market_cap) weights.

        Model: ret_i,t = alpha_t + sum(beta_k * exposure_i,k,t-1) + epsilon_i,t

        Args:
            start_date: Start of estimation period
            end_date: End of estimation period

        Returns:
            Tuple of:
            - DataFrame with columns: date, factor_name, daily_return, t_statistic, r_squared
            - dict of dataset_version_ids accumulated from factor computations
            - list of skipped trading days (for provenance tracking)
        """
        # Get all trading days in range
        crsp_data = self.factor_builder.crsp.get_daily_prices(
            start_date - timedelta(days=1),  # Need t-1 for lagged exposures
            end_date,
        )
        trading_days = sorted(crsp_data["date"].unique().to_list())
        trading_days = [d for d in trading_days if start_date <= d <= end_date]

        # Compute CRSP data content hash for provenance (Codex MEDIUM fix)
        # Include content digest to detect data revisions, not just shape
        ret_sum = crsp_data["ret"].sum() if "ret" in crsp_data.columns else 0.0
        permno_hash = hash(tuple(sorted(crsp_data["permno"].unique().to_list()[:100])))
        crsp_hash_input = f"{start_date}_{end_date}_{crsp_data.height}_{ret_sum:.8f}_{permno_hash}"
        crsp_version = hashlib.sha256(crsp_hash_input.encode()).hexdigest()[:12]

        factor_return_rows = []
        skipped_days: list[date] = []
        # Track version IDs for reproducibility (Codex MEDIUM fix)
        all_version_ids: dict[str, str] = {"crsp_returns": crsp_version}

        for t in trading_days:
            try:
                # Get lagged exposures from t-1 (PIT correct)
                # Find the most recent trading day before t
                prior_days = [d for d in crsp_data["date"].unique().to_list() if d < t]
                if not prior_days:
                    logger.warning(f"No prior day data for {t}, skipping")
                    skipped_days.append(t)
                    continue
                t_lag = max(prior_days)

                # Get factor exposures at t-1
                exposures_result = self.factor_builder.compute_all_factors(as_of_date=t_lag)
                exposures = self._pivot_exposures(exposures_result.exposures)
                # Track version IDs for full provenance (Codex HIGH fix)
                # Include both factor versions AND CRSP returns version
                day_versions = exposures_result.dataset_version_ids.copy()
                day_versions["crsp_returns"] = crsp_version
                all_version_ids.update(day_versions)

                # Get returns at t
                returns_t = crsp_data.filter(pl.col("date") == t).select(["permno", "ret"])

                # Get market cap for WLS weights
                market_cap = (
                    crsp_data.filter(pl.col("date") == t)
                    .select(["permno", "prc", "shrout"])
                    .with_columns(
                        (pl.col("prc").abs() * pl.col("shrout") * 1000).alias("market_cap")
                    )
                    .select(["permno", "market_cap"])
                )

                # Validate and clean inputs
                exposures_clean, returns_clean, warns = self._validate_daily_inputs(
                    exposures, returns_t, t
                )
                for w in warns:
                    logger.debug(f"{t}: {w}")

                # Merge with market cap weights
                data = exposures_clean.join(returns_clean, on="permno").join(
                    market_cap, on="permno"
                )

                # Run WLS regression with intercept
                factor_returns, t_stats, r_squared = self._run_wls_regression(data)

                # Build version string for this day's factor returns (Codex MEDIUM fix)
                day_version_str = "|".join(f"{k}:{v}" for k, v in sorted(day_versions.items()))
                for i, factor_name in enumerate(self.factor_names):
                    factor_return_rows.append(
                        {
                            "date": t,
                            "factor_name": factor_name,
                            "daily_return": factor_returns[i],
                            "t_statistic": t_stats[i],
                            "r_squared": r_squared,
                            "dataset_version_id": day_version_str,
                        }
                    )

            except InsufficientDataError as e:
                logger.warning(f"Skipping {t}: {e}")
                skipped_days.append(t)
                continue
            except np.linalg.LinAlgError as e:
                logger.error(
                    "WLS regression failed - singular matrix",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "date": str(t),
                    },
                    exc_info=True,
                )
                skipped_days.append(t)
                continue
            except (KeyError, IndexError) as e:
                logger.error(
                    "Data access error during factor return computation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "date": str(t),
                    },
                    exc_info=True,
                )
                skipped_days.append(t)
                continue
            except (ValueError, TypeError) as e:
                logger.error(
                    "Invalid data during factor return computation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "date": str(t),
                    },
                    exc_info=True,
                )
                skipped_days.append(t)
                continue

        if len(factor_return_rows) == 0:
            raise InsufficientDataError(
                f"No valid factor returns computed between {start_date} and {end_date}"
            )

        return pl.DataFrame(factor_return_rows), all_version_ids, skipped_days

    def _pivot_exposures(self, exposures_df: pl.DataFrame) -> pl.DataFrame:
        """
        Pivot factor exposures from long to wide format.

        Input: permno, date, factor_name, zscore
        Output: permno, momentum_12_1, book_to_market, roe, log_market_cap, realized_vol
        """
        return exposures_df.pivot(
            index="permno",
            on="factor_name",
            values="zscore",
        )

    def _run_wls_regression(
        self,
        data: pl.DataFrame,
    ) -> tuple[NDArray[np.floating[Any]], NDArray[np.floating[Any]], float]:
        """
        Run WLS regression of returns on factor exposures.

        Model: ret = alpha + exposures @ betas + epsilon
        Weights: sqrt(market_cap)

        Args:
            data: DataFrame with permno, factor columns, ret, market_cap

        Returns:
            Tuple of (factor_returns, t_statistics, r_squared)
        """
        # Extract arrays
        y = data["ret"].to_numpy()
        X = data.select(self.factor_names).to_numpy()
        weights = np.sqrt(np.maximum(data["market_cap"].to_numpy(), 1e-10))

        # Add intercept
        X_with_const = sm.add_constant(X)

        # Run WLS
        model = sm.WLS(y, X_with_const, weights=weights)
        results = model.fit()

        # Extract factor returns (skip intercept at index 0)
        factor_returns = results.params[1:]
        t_stats = results.tvalues[1:]
        r_squared = results.rsquared

        return factor_returns, t_stats, r_squared

    def estimate_covariance(
        self,
        as_of_date: date,
    ) -> CovarianceResult:
        """
        Estimate factor covariance matrix with full pipeline.

        Pipeline:
        1. Compute raw factor returns from cross-sectional regression
        2. Apply exponential decay weights to raw returns
        3. Compute weighted sample covariance
        4. Apply Newey-West HAC correction
        5. Apply Ledoit-Wolf shrinkage
        6. Ensure PSD via eigenvalue clipping

        Args:
            as_of_date: Date for which to estimate covariance

        Returns:
            CovarianceResult with covariance matrix and metadata
        """
        # Calculate lookback period
        start_date = as_of_date - timedelta(days=self.config.lookback_days)

        # Step 1: Get raw factor returns (with dataset version and skipped days tracking)
        factor_returns_df, version_ids, skipped_days = self.estimate_factor_returns(
            start_date, as_of_date
        )

        # Check minimum observations
        n_days = factor_returns_df["date"].n_unique()
        if n_days < self.config.min_observations:
            raise InsufficientDataError(
                f"Only {n_days} days of factor returns, "
                f"minimum required: {self.config.min_observations}"
            )

        # Pivot to wide format for covariance computation
        factor_returns_wide = factor_returns_df.pivot(
            index="date",
            on="factor_name",
            values="daily_return",
        ).sort("date")

        # Ensure canonical ordering
        returns_matrix = factor_returns_wide.select(self.factor_names).to_numpy()
        dates = factor_returns_wide["date"].to_list()

        # Step 2: Compute exponential decay weights
        weights = self._compute_decay_weights(dates, as_of_date)
        # Compute true effective sample size (Codex LOW fix)
        # n_eff = sum_w^2 / sum_w2, not just sum_w
        sum_w = np.sum(weights)
        sum_w2 = np.sum(weights**2)
        effective_obs = (sum_w**2 / sum_w2) if sum_w2 > 0 else sum_w

        # Step 3: Compute weighted sample covariance
        weighted_cov = self._compute_weighted_covariance(returns_matrix, weights)

        # Step 4: Apply Newey-West HAC correction
        hac_cov = self._apply_newey_west_to_covariance(weighted_cov, returns_matrix, weights)

        # Step 5: Apply Ledoit-Wolf shrinkage
        # Apply decay-weighted centering for consistent shrinkage estimation
        weighted_mean = np.average(returns_matrix, axis=0, weights=weights)
        centered_returns = returns_matrix - weighted_mean
        # Scale returns by sqrt(weights) for LedoitWolf (equivalent to weighted covariance)
        sqrt_weights = np.sqrt(weights / np.sum(weights) * len(weights))
        weighted_centered_returns = centered_returns * sqrt_weights[:, np.newaxis]
        shrunk_cov, shrinkage_intensity = self._apply_ledoit_wolf_shrinkage(
            weighted_centered_returns, hac_cov
        )

        # Step 6: Ensure PSD
        psd_cov = self._ensure_psd(shrunk_cov)

        # Compute reproducibility hash with all config params (Codex MEDIUM fix)
        # Include all config knobs and skipped days for unique hashes per run configuration
        skipped_days_str = ",".join(str(d) for d in sorted(skipped_days))
        hash_input = (
            f"{as_of_date}_{self.config.halflife_days}_{self.config.newey_west_lags}_"
            f"{self.config.min_stocks_per_day}_{self.config.lookback_days}_"
            f"{self.config.min_observations}_{shrinkage_intensity:.6f}_{n_days}_"
            f"{skipped_days_str}_{sorted(version_ids.items())}"
        )
        repro_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        result = CovarianceResult(
            factor_covariance=psd_cov,
            factor_names=self.factor_names.copy(),
            factor_returns=factor_returns_df,
            as_of_date=as_of_date,
            dataset_version_ids=version_ids,
            shrinkage_intensity=shrinkage_intensity,
            effective_observations=effective_obs,
            reproducibility_hash=repro_hash,
            skipped_days=skipped_days,
            halflife_days=self.config.halflife_days,
        )

        # Validate result
        errors = result.validate()
        if errors:
            logger.warning(f"Covariance validation warnings: {errors}")

        return result

    def _compute_decay_weights(
        self,
        dates: list[date],
        as_of_date: date,
    ) -> NDArray[np.floating[Any]]:
        """
        Compute exponential decay weights.

        Weight formula: w_t = exp(-ln(2) * age_t / halflife)

        Args:
            dates: List of dates in the sample
            as_of_date: Reference date for age calculation

        Returns:
            Array of weights normalized to sum to len(dates)
        """
        ages = np.array([(as_of_date - d).days for d in dates])
        weights = np.exp(-np.log(2) * ages / self.config.halflife_days)
        # Normalize to sum to sample size for comparable variance
        weights = weights * len(dates) / np.sum(weights)
        return np.asarray(weights, dtype=np.float64)

    def _compute_weighted_covariance(
        self,
        returns: NDArray[np.floating[Any]],
        weights: NDArray[np.floating[Any]],
    ) -> NDArray[np.floating[Any]]:
        """
        Compute weighted sample covariance matrix.

        Args:
            returns: T x K matrix of factor returns
            weights: T-length array of weights

        Returns:
            K x K weighted covariance matrix
        """
        # Weighted mean
        weighted_mean = np.average(returns, axis=0, weights=weights)

        # Center the data
        centered = returns - weighted_mean

        # Weighted covariance with effective sample size correction (Codex HIGH fix)
        sum_w = np.sum(weights)
        sum_w2 = np.sum(weights**2)
        # Effective sample size: n_eff = sum_w^2 / sum_w2
        # Proper Bessel denominator for weighted samples: sum_w - (sum_w2/sum_w)
        denom = sum_w - (sum_w2 / sum_w) if sum_w > 0 else 1.0
        denom = max(denom, 1e-10)  # Guard against division by zero

        # Weighted outer products
        cov = np.zeros((returns.shape[1], returns.shape[1]))
        for t in range(returns.shape[0]):
            cov += weights[t] * np.outer(centered[t], centered[t])

        # Normalize using effective sample size denominator
        cov = cov / denom

        return np.asarray(cov, dtype=np.float64)

    def _apply_newey_west_to_covariance(
        self,
        weighted_cov: NDArray[np.floating[Any]],
        returns: NDArray[np.floating[Any]],
        weights: NDArray[np.floating[Any]],
    ) -> NDArray[np.floating[Any]]:
        """
        Apply Newey-West HAC adjustment to covariance matrix.

        HAC corrects for autocorrelation in the factor return series.
        Applied after exponential decay weighting.

        Args:
            weighted_cov: Initial weighted covariance
            returns: T x K matrix of factor returns
            weights: T-length array of weights

        Returns:
            HAC-adjusted covariance matrix
        """
        n_lags = self.config.newey_west_lags
        T, K = returns.shape

        # Compute weighted mean and centered returns (consistent with base covariance)
        weighted_mean = np.average(returns, axis=0, weights=weights)
        centered = returns - weighted_mean

        # Compute effective sample size denominator (Codex MEDIUM fix)
        # Must match the denominator used in _compute_weighted_covariance
        sum_w = np.sum(weights)
        sum_w2 = np.sum(weights**2)
        denom = sum_w - (sum_w2 / sum_w) if sum_w > 0 else 1.0
        denom = max(denom, 1e-10)

        # Initialize with sample covariance (Gamma_0)
        hac_cov = weighted_cov.copy()

        # Add autocovariance terms with Bartlett kernel weights
        # Use sqrt(w_t * w_{t-lag}) weighting for lagged terms to match base covariance
        for lag in range(1, n_lags + 1):
            bartlett_weight = 1 - lag / (n_lags + 1)

            # Compute lagged autocovariance with geometric mean of weights
            gamma_lag = np.zeros((K, K))
            for t in range(lag, T):
                # Use geometric mean of weights for lagged products
                w_lag = np.sqrt(weights[t] * weights[t - lag])
                gamma_lag += w_lag * np.outer(centered[t], centered[t - lag])
            gamma_lag /= denom  # Use same effective sample size denominator

            # Add symmetric contribution
            hac_cov += bartlett_weight * (gamma_lag + gamma_lag.T)

        return np.asarray(hac_cov, dtype=np.float64)

    def _apply_ledoit_wolf_shrinkage(
        self,
        centered_returns: NDArray[np.floating[Any]],
        weighted_cov: NDArray[np.floating[Any]],
    ) -> tuple[NDArray[np.floating[Any]], float]:
        """
        Apply Ledoit-Wolf shrinkage to covariance matrix.

        Uses sklearn's LedoitWolf estimator on centered returns to compute
        the optimal shrinkage intensity, then applies it to the HAC-corrected
        covariance matrix (which already has exponential decay weighting).

        Args:
            centered_returns: T x K matrix of demeaned factor returns
            weighted_cov: HAC-corrected weighted covariance matrix to shrink

        Returns:
            Tuple of (shrunk_covariance, shrinkage_intensity)
        """
        if self.config.shrinkage_intensity is not None:
            # Use specified shrinkage intensity
            intensity = self.config.shrinkage_intensity
            target = np.eye(weighted_cov.shape[0]) * np.trace(weighted_cov) / weighted_cov.shape[0]
            shrunk = (1 - intensity) * weighted_cov + intensity * target
            return shrunk, intensity

        # Use Ledoit-Wolf optimal shrinkage
        # Fit on centered returns to get optimal shrinkage intensity
        lw = LedoitWolf(assume_centered=True)
        lw.fit(centered_returns)

        # Get shrinkage intensity from LW estimator
        intensity = lw.shrinkage_

        # Apply shrinkage to our HAC-corrected covariance
        # Target is scaled identity (preserves trace)
        target = np.eye(weighted_cov.shape[0]) * np.trace(weighted_cov) / weighted_cov.shape[0]
        shrunk_cov = (1 - intensity) * weighted_cov + intensity * target

        return shrunk_cov, intensity

    def _ensure_psd(self, cov: NDArray[np.floating[Any]]) -> NDArray[np.floating[Any]]:
        """
        Ensure covariance matrix is positive semi-definite.

        Algorithm:
        1. Eigendecomposition
        2. Clip negative eigenvalues to small positive value
        3. Reconstruct symmetric PSD matrix
        4. Ensure perfect symmetry

        Args:
            cov: Covariance matrix (may have small negative eigenvalues)

        Returns:
            PSD covariance matrix
        """
        # 1. Eigendecomposition
        eigenvalues, eigenvectors = np.linalg.eigh(cov)

        # 2. Clip negative eigenvalues to small positive value
        eigenvalues_clipped = np.maximum(eigenvalues, 1e-10)

        # 3. Reconstruct symmetric PSD matrix
        cov_psd = eigenvectors @ np.diag(eigenvalues_clipped) @ eigenvectors.T

        # 4. Ensure perfect symmetry (numerical precision)
        cov_psd = (cov_psd + cov_psd.T) / 2

        return np.asarray(cov_psd, dtype=np.float64)

"""
Specific (idiosyncratic) risk estimation for risk models.

This module implements stock-level specific risk estimation, computing
the idiosyncratic variance that is not explained by factor exposures.

All computations are point-in-time (PIT) correct.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import numpy as np
import polars as pl
from numpy.typing import NDArray

from libs.trading.risk.factor_covariance import (
    CANONICAL_FACTOR_ORDER,
    CovarianceConfig,
)


class CRSPProviderProtocol(Protocol):
    """Protocol for CRSP data provider."""

    def get_daily_data(self, start_date: date, end_date: date) -> pl.DataFrame:
        """Get daily CRSP data for the given date range."""
        ...


logger = logging.getLogger(__name__)


@dataclass
class SpecificRiskResult:
    """
    Result of specific risk estimation with metadata.

    Includes specific risk per stock and provenance metadata.
    """

    specific_risks: pl.DataFrame  # permno, specific_variance, specific_vol
    as_of_date: date
    dataset_version_ids: dict[str, str]
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    coverage: float = 0.0  # % of universe with valid specific risk
    reproducibility_hash: str = ""
    floored_count: int = 0  # Number of stocks with floored variance

    def validate(self) -> list[str]:
        """
        Validate specific risk estimates.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Check for negative variances (should be floored)
        neg_var = self.specific_risks.filter(pl.col("specific_variance") < 0).height
        if neg_var > 0:
            errors.append(f"{neg_var} stocks have negative specific variance")

        # Check for NaN
        nan_count = self.specific_risks.filter(pl.col("specific_variance").is_nan()).height
        if nan_count > 0:
            errors.append(f"{nan_count} stocks have NaN specific variance")

        # Check for Inf
        inf_count = self.specific_risks.filter(pl.col("specific_variance").is_infinite()).height
        if inf_count > 0:
            errors.append(f"{inf_count} stocks have infinite specific variance")

        # Check reasonable range (annualized vol < 500%)
        extreme_vol = self.specific_risks.filter(pl.col("specific_vol") > 5.0).height
        if extreme_vol > 0:
            errors.append(f"{extreme_vol} stocks have annualized specific vol > 500%")

        return errors

    def to_storage_format(self) -> pl.DataFrame:
        """
        Convert to storage format for parquet.

        Storage contract matches P4T2_TASK.md schema:
        as_of_date, permno, specific_variance, specific_vol, dataset_version_id
        """
        version_str = "|".join(f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items()))

        return self.specific_risks.with_columns(
            [
                pl.lit(self.as_of_date).alias("as_of_date"),
                pl.lit(version_str).alias("dataset_version_id"),
            ]
        ).select(
            [
                "as_of_date",
                "permno",
                "specific_variance",
                "specific_vol",
                "dataset_version_id",
            ]
        )


class SpecificRiskEstimator:
    """
    Estimate stock-level idiosyncratic (specific) risk.

    Specific risk = Total risk - Factor risk
    Where factor risk = b' * Cov_factor * b

    This is the variance not explained by the factor model.
    """

    def __init__(
        self,
        config: CovarianceConfig | None = None,
        crsp_provider: CRSPProviderProtocol | None = None,
    ):
        """
        Initialize the estimator.

        Args:
            config: Configuration for estimation parameters
            crsp_provider: CRSP data provider for historical returns
        """
        self.config = config or CovarianceConfig()
        self.crsp_provider = crsp_provider
        self.factor_names = CANONICAL_FACTOR_ORDER.copy()

    def estimate(
        self,
        as_of_date: date,
        factor_cov: NDArray[np.floating[Any]],
        factor_loadings: pl.DataFrame,
        dataset_version_ids: dict[str, str] | None = None,
    ) -> SpecificRiskResult:
        """
        Estimate specific risk per stock.

        Args:
            as_of_date: Date for which to estimate specific risk
            factor_cov: K x K factor covariance matrix (from FactorCovarianceEstimator)
            factor_loadings: Factor exposures per stock
                DataFrame with columns: permno, factor_name, loading (or zscore)
            dataset_version_ids: Optional version IDs from factor covariance estimation
                (for provenance tracking)

        Returns:
            SpecificRiskResult with specific variance/volatility per stock
        """
        if self.crsp_provider is None:
            raise ValueError("CRSP provider required for specific risk estimation")

        # Get historical returns for total variance calculation
        start_date = as_of_date - timedelta(days=self.config.lookback_days)
        historical_returns = self.crsp_provider.get_daily_data(start_date, as_of_date)

        # Compute CRSP data content hash for provenance (Codex MEDIUM fix)
        # Include content digest to detect data revisions, not just shape
        ret_sum = historical_returns["ret"].sum() if "ret" in historical_returns.columns else 0.0
        permno_hash = hash(tuple(sorted(historical_returns["permno"].unique().to_list()[:100])))
        crsp_hash_input = (
            f"{start_date}_{as_of_date}_{historical_returns.height}_{ret_sum:.8f}_{permno_hash}"
        )
        crsp_version = hashlib.sha256(crsp_hash_input.encode()).hexdigest()[:12]

        # Pivot factor loadings to wide format if needed
        if "factor_name" in factor_loadings.columns:
            loadings_wide = factor_loadings.pivot(
                index="permno",
                on="factor_name",
                values="zscore" if "zscore" in factor_loadings.columns else "loading",
            )
        else:
            loadings_wide = factor_loadings

        # Get list of unique permnos with valid loadings
        permnos_with_loadings = set(loadings_wide["permno"].to_list())

        # Partition returns by permno for O(1) lookup (performance optimization)
        returns_by_permno: dict[int, pl.DataFrame] = {}
        for df in historical_returns.sort("date").partition_by("permno", as_dict=False):
            if df.height > 0:
                permno_val = df["permno"][0]
                returns_by_permno[permno_val] = df

        # Compute specific risk for each stock
        results = []
        floored_count = 0

        for permno in permnos_with_loadings:
            try:
                # Get factor loadings for this stock (in canonical order)
                stock_loadings = loadings_wide.filter(pl.col("permno") == permno)
                if stock_loadings.height == 0:
                    continue

                # Extract loadings as vector in canonical order
                b = np.array(
                    [
                        stock_loadings[factor].item() if factor in stock_loadings.columns else 0.0
                        for factor in self.factor_names
                    ]
                )

                # Check for NaN in loadings
                if np.any(np.isnan(b)):
                    logger.debug(f"Skipping permno {permno}: NaN in factor loadings")
                    continue

                # Compute factor contribution to variance: b' * Cov * b
                factor_variance = b.T @ factor_cov @ b

                # Get historical returns for this stock (O(1) lookup)
                if permno not in returns_by_permno:
                    logger.debug(f"Skipping permno {permno}: no return history")
                    continue
                stock_data = returns_by_permno[permno]
                stock_returns = stock_data["ret"].to_numpy()

                if len(stock_returns) < self.config.min_observations // 2:
                    logger.debug(
                        f"Skipping permno {permno}: insufficient return history "
                        f"({len(stock_returns)} days)"
                    )
                    continue

                # Compute total variance with exponential decay
                dates = stock_data["date"].to_list()
                weights = self._compute_decay_weights(dates, as_of_date)

                # Weighted variance with effective sample size correction (Codex HIGH fix)
                # For exponential decay weights, use neff = 1 / sum(w^2) for proper Bessel correction
                weighted_mean = np.average(stock_returns, weights=weights)
                centered = stock_returns - weighted_mean
                # Effective sample size accounts for weight concentration
                sum_w_sq = np.sum(weights**2)
                n_eff = 1.0 / sum_w_sq if sum_w_sq > 0 else len(dates)
                # Bessel correction using effective sample size
                bessel_correction = n_eff / (n_eff - 1.0) if n_eff > 1 else 1.0
                total_variance = np.average(centered**2, weights=weights) * bessel_correction

                # Specific variance = Total - Factor
                specific_variance = total_variance - factor_variance

                # Floor negative variance (Codex LOW fix: use warning level)
                if specific_variance < 0:
                    logger.warning(
                        f"Flooring negative specific variance for permno {permno}: "
                        f"{specific_variance:.6e}"
                    )
                    specific_variance = 1e-8
                    floored_count += 1

                # Annualize: daily variance * 252
                specific_vol = np.sqrt(specific_variance * 252)

                results.append(
                    {
                        "permno": permno,
                        "specific_variance": specific_variance,
                        "specific_vol": specific_vol,
                    }
                )

            except np.linalg.LinAlgError as e:
                logger.error(
                    "Matrix operation failed during specific risk calculation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "permno": permno,
                        "factor_cov_shape": factor_cov.shape,
                    },
                    exc_info=True,
                )
                continue
            except (ValueError, TypeError) as e:
                logger.error(
                    "Invalid data during specific risk calculation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "permno": permno,
                    },
                    exc_info=True,
                )
                continue
            except (KeyError, IndexError) as e:
                logger.error(
                    "Data access error during specific risk calculation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "permno": permno,
                    },
                    exc_info=True,
                )
                continue
            except ZeroDivisionError as e:
                logger.error(
                    "Division by zero during specific risk calculation",
                    extra={
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "permno": permno,
                    },
                    exc_info=True,
                )
                continue

        if len(results) == 0:
            raise ValueError("No valid specific risk estimates computed")

        specific_risks_df = pl.DataFrame(results)

        # Compute coverage
        total_universe = len(permnos_with_loadings)
        coverage = len(results) / total_universe if total_universe > 0 else 0.0

        # Build version IDs - always include derived CRSP version (Codex HIGH fix)
        version_ids: dict[str, str] = {"crsp_specific_risk": crsp_version}
        if dataset_version_ids:
            version_ids.update(dataset_version_ids)

        # Compute reproducibility hash with all config params (Codex MEDIUM fix)
        hash_input = (
            f"{as_of_date}_{len(results)}_{self.config.halflife_days}_"
            f"{self.config.lookback_days}_{self.config.min_observations}_"
            f"{sorted(version_ids.items())}"
        )
        repro_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        result = SpecificRiskResult(
            specific_risks=specific_risks_df,
            as_of_date=as_of_date,
            dataset_version_ids=version_ids,
            coverage=coverage,
            reproducibility_hash=repro_hash,
            floored_count=floored_count,
        )

        # Validate result
        errors = result.validate()
        if errors:
            logger.warning(f"Specific risk validation warnings: {errors}")

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
            Array of weights normalized to sum to 1
        """
        ages = np.array([(as_of_date - d).days for d in dates])
        weights = np.exp(-np.log(2) * ages / self.config.halflife_days)
        # Normalize to sum to 1 for weighted average
        weights = weights / np.sum(weights)
        return np.asarray(weights, dtype=np.float64)

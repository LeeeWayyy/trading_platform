"""
Barra-style multi-factor risk model.

This module implements a Barra-style risk model that combines factor risk
and specific (idiosyncratic) risk for portfolio risk decomposition.

Portfolio variance formula:
    σ²_p = w' * B * F * B' * w + w' * D * w

Where:
    - w = N×1 portfolio weights vector
    - B = N×K factor loadings matrix
    - F = K×K factor covariance matrix (from FactorCovarianceEstimator)
    - D = N×N diagonal specific variance matrix (from SpecificRiskEstimator)

All computations are point-in-time (PIT) correct.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from libs.risk.factor_covariance import (
    CANONICAL_FACTOR_ORDER,
    CovarianceResult,
)
from libs.risk.specific_risk import SpecificRiskResult

if TYPE_CHECKING:
    from libs.risk.risk_decomposition import PortfolioRiskResult

logger = logging.getLogger(__name__)


class InsufficientCoverageError(Exception):
    """Raised when portfolio coverage is below minimum threshold."""

    pass


@dataclass
class BarraRiskModelConfig:
    """Configuration for Barra risk model."""

    annualization_factor: int = 252  # Trading days per year
    min_coverage: float = 0.8  # Minimum % of portfolio weights with risk data
    var_confidence_95: float = 0.95  # 95% VaR confidence level
    var_confidence_99: float = 0.99  # 99% VaR confidence level


@dataclass
class BarraRiskModel:
    """
    Barra-style risk model combining factor and specific risk.

    This class holds the risk model data (factor covariance, loadings, specific risk)
    and provides methods to compute portfolio risk decomposition.

    Portfolio variance formula:
        σ²_p = f' * F * f + Σ(w_i² * σ²_spec_i)

    Where:
        - f = B' * w is the K×1 portfolio factor exposure vector
        - F = K×K factor covariance matrix
        - w_i = weight of stock i
        - σ²_spec_i = specific variance of stock i

    Factor-level contributions use Euler decomposition:
        - MCTR_k = (F @ f)_k / σ_p  (marginal contribution to risk)
        - CCTR_k = f_k * MCTR_k     (component contribution to risk)
        - %Contrib_k = CCTR_k / σ_p
    """

    factor_covariance: NDArray[np.floating[Any]]  # K×K factor covariance (daily)
    factor_names: list[str]  # Factor names in canonical order
    factor_loadings: pl.DataFrame  # permno, factor columns (z-scores)
    specific_risks: pl.DataFrame  # permno, specific_variance (daily)
    as_of_date: date
    dataset_version_ids: dict[str, str]
    config: BarraRiskModelConfig = field(default_factory=BarraRiskModelConfig)
    model_version: str = "barra_v1.0"

    @classmethod
    def from_t22_results(
        cls,
        covariance_result: CovarianceResult,
        specific_risk_result: SpecificRiskResult,
        factor_loadings: pl.DataFrame,
        config: BarraRiskModelConfig | None = None,
    ) -> "BarraRiskModel":
        """
        Factory method to create from T2.2 outputs.

        Args:
            covariance_result: CovarianceResult from FactorCovarianceEstimator
            specific_risk_result: SpecificRiskResult from SpecificRiskEstimator
            factor_loadings: Factor exposures DataFrame (permno, factor columns as z-scores)
                Can be either long format (permno, factor_name, zscore) or
                wide format (permno, momentum_12_1, book_to_market, ...)
            config: Optional configuration override

        Returns:
            BarraRiskModel instance ready for portfolio risk computation
        """
        config = config or BarraRiskModelConfig()

        # Pivot factor loadings to wide format if needed
        if "factor_name" in factor_loadings.columns:
            loadings_wide = factor_loadings.pivot(
                index="permno",
                on="factor_name",
                values="zscore" if "zscore" in factor_loadings.columns else "loading",
            )
        else:
            loadings_wide = factor_loadings

        # Ensure factor order matches canonical order - FAIL FAST if missing
        available_factors = [f for f in CANONICAL_FACTOR_ORDER if f in loadings_wide.columns]
        if len(available_factors) != len(CANONICAL_FACTOR_ORDER):
            missing = set(CANONICAL_FACTOR_ORDER) - set(available_factors)
            raise ValueError(
                f"Factor loadings missing required columns: {missing}. "
                f"Expected all of: {CANONICAL_FACTOR_ORDER}"
            )

        # Compute factor loadings hash for provenance
        loadings_hash_input = (
            f"{loadings_wide.height}_{loadings_wide.width}_"
            f"{loadings_wide.select(available_factors).sum().to_numpy().sum():.8f}"
        )
        loadings_hash = hashlib.sha256(loadings_hash_input.encode()).hexdigest()[:12]

        # Merge version IDs from all sources
        version_ids = {
            **covariance_result.dataset_version_ids,
            **specific_risk_result.dataset_version_ids,
            "factor_loadings": loadings_hash,
        }

        return cls(
            factor_covariance=covariance_result.factor_covariance,
            factor_names=covariance_result.factor_names.copy(),
            factor_loadings=loadings_wide,
            specific_risks=specific_risk_result.specific_risks,
            as_of_date=covariance_result.as_of_date,
            dataset_version_ids=version_ids,
            config=config,
        )

    def check_coverage(self, portfolio: pl.DataFrame) -> tuple[float, list[int], pl.DataFrame]:
        """
        Check portfolio coverage against risk model data.

        Args:
            portfolio: DataFrame with permno, weight columns

        Returns:
            Tuple of:
            - coverage_ratio: Sum of weights with risk data / total weight
            - missing_permnos: List of permnos without risk data
            - covered_portfolio: Portfolio filtered to covered permnos
        """
        portfolio_permnos = set(portfolio["permno"].to_list())
        loadings_permnos = set(self.factor_loadings["permno"].to_list())
        specific_permnos = set(self.specific_risks["permno"].to_list())

        # Permnos with both factor loadings and specific risk
        covered_permnos = portfolio_permnos & loadings_permnos & specific_permnos
        missing_permnos = list(portfolio_permnos - covered_permnos)

        # Filter portfolio to covered permnos
        covered_portfolio = portfolio.filter(pl.col("permno").is_in(list(covered_permnos)))

        # Compute coverage as sum of ABSOLUTE weights
        # This handles long/short and dollar-neutral portfolios correctly
        total_abs_weight = portfolio["weight"].abs().sum()
        covered_abs_weight = covered_portfolio["weight"].abs().sum()

        # Handle zero-weight (flat/empty) portfolios: treat as fully covered
        # since there's nothing to analyze and zero risk is a valid answer
        if total_abs_weight == 0:
            coverage_ratio = 1.0
        else:
            coverage_ratio = covered_abs_weight / total_abs_weight

        return coverage_ratio, missing_permnos, covered_portfolio

    def compute_portfolio_risk(
        self,
        portfolio: pl.DataFrame,
        portfolio_id: str | None = None,
        expected_return: float = 0.0,
        holding_period_days: int = 1,
        validate_inputs: bool = True,
    ) -> "PortfolioRiskResult":
        """
        Compute total portfolio risk decomposed into factor and specific.

        Args:
            portfolio: DataFrame with permno, weight columns
            portfolio_id: Optional portfolio identifier
            expected_return: Expected daily return (for VaR calculation)
            holding_period_days: Holding period for VaR/CVaR (default 1 day)
            validate_inputs: Whether to validate model inputs first (default True)

        Returns:
            PortfolioRiskResult with total, factor, and specific risk

        Raises:
            InsufficientCoverageError: If coverage < min_coverage
            ValueError: If validate_inputs=True and model inputs are invalid
        """
        from libs.risk.risk_decomposition import (
            PortfolioRiskResult,
            compute_cvar_parametric,
            compute_var_parametric,
        )

        # Validate model inputs if requested (fail fast on bad covariance)
        if validate_inputs:
            errors = self.validate()
            if errors:
                raise ValueError(f"Model validation failed: {errors}")

        # Check coverage
        coverage_ratio, missing_permnos, covered_portfolio = self.check_coverage(portfolio)

        if coverage_ratio < self.config.min_coverage:
            raise InsufficientCoverageError(
                f"Portfolio coverage {coverage_ratio:.1%} is below minimum "
                f"{self.config.min_coverage:.1%}. Missing permnos: {missing_permnos[:10]}..."
            )

        if missing_permnos:
            logger.warning(
                f"Portfolio has {len(missing_permnos)} permnos without risk data "
                f"({1 - coverage_ratio:.1%} of weight)"
            )

        # Short-circuit for empty/flat portfolios: return zero risk
        if covered_portfolio.height == 0 or covered_portfolio["weight"].abs().sum() == 0:
            return PortfolioRiskResult(
                analysis_id=str(uuid.uuid4()),
                portfolio_id=portfolio_id or "default",
                as_of_date=self.as_of_date,
                total_risk=0.0,
                factor_risk=0.0,
                specific_risk=0.0,
                var_95=0.0,
                var_99=0.0,
                cvar_95=0.0,
                model_version=self.model_version,
                dataset_version_ids=self.dataset_version_ids.copy(),
                computation_timestamp=datetime.now(UTC),
                factor_contributions=pl.DataFrame(
                    {
                        "factor_name": self.factor_names,
                        "marginal_contribution": [0.0] * len(self.factor_names),
                        "component_contribution": [0.0] * len(self.factor_names),
                        "percent_contribution": [0.0] * len(self.factor_names),
                    }
                ),
                coverage_ratio=float(coverage_ratio),
            )

        # Renormalize covered portfolio weights to account for missing positions
        # This scales up weights proportionally so risk reflects the full portfolio,
        # assuming missing positions would contribute proportionally to covered ones.
        total_abs_weight = portfolio["weight"].abs().sum()
        covered_abs_weight = covered_portfolio["weight"].abs().sum()
        if coverage_ratio < 1.0 and covered_abs_weight > 0:
            scale_factor = total_abs_weight / covered_abs_weight
            covered_portfolio = covered_portfolio.with_columns(
                (pl.col("weight") * scale_factor).alias("weight")
            )

        # Build aligned arrays
        weights, factor_matrix, specific_variances = self._build_aligned_arrays(covered_portfolio)

        # Compute variances using shared helper (ensures consistent flooring)
        (
            portfolio_factor_exposure,
            factor_variance_daily,
            specific_variance_daily,
            total_variance_daily,
        ) = self._compute_variances(weights, factor_matrix, specific_variances)

        # Annualize risks
        factor_risk_annual = np.sqrt(factor_variance_daily * self.config.annualization_factor)
        specific_risk_annual = np.sqrt(specific_variance_daily * self.config.annualization_factor)
        total_risk_annual = np.sqrt(total_variance_daily * self.config.annualization_factor)

        # Compute daily sigma for VaR/CVaR
        daily_sigma = np.sqrt(total_variance_daily)

        # Compute VaR and CVaR using standalone functions (supports multi-day holding periods)
        var_95 = compute_var_parametric(
            daily_sigma,
            confidence=self.config.var_confidence_95,
            holding_period_days=holding_period_days,
            expected_return=expected_return,
        )
        var_99 = compute_var_parametric(
            daily_sigma,
            confidence=self.config.var_confidence_99,
            holding_period_days=holding_period_days,
            expected_return=expected_return,
        )
        cvar_95 = compute_cvar_parametric(
            daily_sigma,
            confidence=self.config.var_confidence_95,
            holding_period_days=holding_period_days,
            expected_return=expected_return,
        )

        # Compute factor contributions
        factor_contributions = self._compute_factor_contributions_internal(
            portfolio_factor_exposure, total_risk_annual
        )

        # Generate analysis ID
        analysis_id = str(uuid.uuid4())

        return PortfolioRiskResult(
            analysis_id=analysis_id,
            portfolio_id=portfolio_id or "default",
            as_of_date=self.as_of_date,
            total_risk=float(total_risk_annual),
            factor_risk=float(factor_risk_annual),
            specific_risk=float(specific_risk_annual),
            var_95=float(var_95),
            var_99=float(var_99),
            cvar_95=float(cvar_95),
            model_version=self.model_version,
            dataset_version_ids=self.dataset_version_ids.copy(),
            computation_timestamp=datetime.now(UTC),
            factor_contributions=factor_contributions,
            coverage_ratio=float(coverage_ratio),
        )

    def compute_factor_contributions(
        self,
        portfolio: pl.DataFrame,
        validate_inputs: bool = True,
    ) -> pl.DataFrame:
        """
        Compute per-factor risk contributions.

        Factor-level Euler decomposition:
            - Portfolio factor exposure: f = B' @ w (K×1)
            - MCTR_k = (F @ f)_k / σ_p (marginal contribution)
            - CCTR_k = f_k * MCTR_k (component contribution)
            - %Contrib_k = CCTR_k / σ_p

        Args:
            portfolio: DataFrame with permno, weight columns
            validate_inputs: Whether to validate model inputs first (default True)

        Returns:
            DataFrame: factor_name, marginal_contribution (MCTR),
                      component_contribution (CCTR), percent_contribution

        Raises:
            InsufficientCoverageError: If portfolio coverage < min_coverage
            ValueError: If validate_inputs=True and model inputs are invalid
        """
        # Validate model inputs if requested (fail fast on bad covariance)
        if validate_inputs:
            errors = self.validate()
            if errors:
                raise ValueError(f"Model validation failed: {errors}")

        # Check coverage and get aligned data
        coverage_ratio, _, covered_portfolio = self.check_coverage(portfolio)

        if coverage_ratio < self.config.min_coverage:
            raise InsufficientCoverageError(
                f"Portfolio coverage {coverage_ratio:.1%} is below minimum "
                f"{self.config.min_coverage:.1%}"
            )

        # Short-circuit for empty/flat portfolios: return zero contributions
        if covered_portfolio.height == 0 or covered_portfolio["weight"].abs().sum() == 0:
            return pl.DataFrame(
                {
                    "factor_name": self.factor_names,
                    "marginal_contribution": [0.0] * len(self.factor_names),
                    "component_contribution": [0.0] * len(self.factor_names),
                    "percent_contribution": [0.0] * len(self.factor_names),
                }
            )

        # Renormalize covered portfolio weights to account for missing positions
        # This scales up weights proportionally so contributions reflect the full portfolio.
        total_abs_weight = portfolio["weight"].abs().sum()
        covered_abs_weight = covered_portfolio["weight"].abs().sum()
        if coverage_ratio < 1.0 and covered_abs_weight > 0:
            scale_factor = total_abs_weight / covered_abs_weight
            covered_portfolio = covered_portfolio.with_columns(
                (pl.col("weight") * scale_factor).alias("weight")
            )

        # Build aligned arrays
        weights, factor_matrix, specific_variances = self._build_aligned_arrays(covered_portfolio)

        # Compute variances using shared helper (ensures consistent flooring)
        (
            portfolio_factor_exposure,
            _factor_variance_daily,
            _specific_variance_daily,
            total_variance_daily,
        ) = self._compute_variances(weights, factor_matrix, specific_variances)

        total_risk_annual = np.sqrt(total_variance_daily * self.config.annualization_factor)

        return self._compute_factor_contributions_internal(
            portfolio_factor_exposure, total_risk_annual
        )

    def _compute_factor_contributions_internal(
        self,
        portfolio_factor_exposure: NDArray[np.floating[Any]],
        total_risk_annual: float,
    ) -> pl.DataFrame:
        """
        Internal method to compute factor contributions.

        Args:
            portfolio_factor_exposure: K×1 portfolio factor exposure vector (f = B' @ w)
            total_risk_annual: Annualized total portfolio risk

        Returns:
            DataFrame with factor contributions
        """
        # MCTR_k = (F @ f)_k / σ_p (annualized)
        # The factor covariance is daily, so F @ f is daily variance contribution
        # We annualize: sqrt(daily_var * 252) gives annual sigma
        # For marginal: ∂σ_annual/∂f_k = (F @ f)_k * 252 / σ_annual
        F_f = self.factor_covariance @ portfolio_factor_exposure  # K×1, daily variance

        # Annualize the marginal contributions
        if total_risk_annual > 1e-10:
            mctr = (F_f * self.config.annualization_factor) / total_risk_annual
        else:
            mctr = np.zeros_like(F_f)

        # CCTR_k = f_k * MCTR_k
        cctr = portfolio_factor_exposure * mctr

        # Percent contribution = CCTR_k / σ_p
        percent_contrib = (
            cctr / total_risk_annual if total_risk_annual > 1e-10 else np.zeros_like(cctr)
        )

        return pl.DataFrame(
            {
                "factor_name": self.factor_names,
                "marginal_contribution": mctr.tolist(),
                "component_contribution": cctr.tolist(),
                "percent_contribution": percent_contrib.tolist(),
            }
        )

    def _build_aligned_arrays(
        self, portfolio: pl.DataFrame
    ) -> tuple[
        NDArray[np.floating[Any]],
        NDArray[np.floating[Any]],
        NDArray[np.floating[Any]],
    ]:
        """
        Build aligned weight, factor loading, and specific variance arrays.

        Args:
            portfolio: DataFrame with permno, weight (filtered to covered permnos)

        Returns:
            Tuple of:
            - weights: N×1 weight vector
            - factor_matrix: N×K factor loadings matrix
            - specific_variances: N×1 specific variance vector
        """
        # Join portfolio with factor loadings and specific risks
        joined = (
            portfolio.select(["permno", "weight"])
            .join(self.factor_loadings, on="permno", how="inner")
            .join(
                self.specific_risks.select(["permno", "specific_variance"]),
                on="permno",
                how="inner",
            )
        )

        # Extract arrays
        weights = joined["weight"].to_numpy().astype(np.float64)
        factor_matrix = joined.select(self.factor_names).to_numpy().astype(np.float64)
        specific_variances = joined["specific_variance"].to_numpy().astype(np.float64)

        # Ensure no negative specific variances (should be floored by T2.2)
        specific_variances = np.maximum(specific_variances, 1e-10)

        return weights, factor_matrix, specific_variances

    def _compute_variances(
        self,
        weights: NDArray[np.floating[Any]],
        factor_matrix: NDArray[np.floating[Any]],
        specific_variances: NDArray[np.floating[Any]],
    ) -> tuple[
        NDArray[np.floating[Any]],  # portfolio_factor_exposure
        float,  # factor_variance_daily
        float,  # specific_variance_daily
        float,  # total_variance_daily
    ]:
        """
        Compute portfolio variances with consistent flooring.

        This helper ensures identical variance computation in both
        compute_portfolio_risk and compute_factor_contributions.

        Args:
            weights: N×1 weight vector
            factor_matrix: N×K factor loadings matrix
            specific_variances: N×1 specific variance vector

        Returns:
            Tuple of:
            - portfolio_factor_exposure: K×1 factor exposure vector
            - factor_variance_daily: Floored factor variance (daily)
            - specific_variance_daily: Floored specific variance (daily)
            - total_variance_daily: Floored total variance (daily)
        """
        # Compute portfolio factor exposure: f = B' @ w (K×1)
        portfolio_factor_exposure = factor_matrix.T @ weights

        # Compute factor variance (daily): f' @ F @ f
        factor_variance_daily = float(
            portfolio_factor_exposure.T @ self.factor_covariance @ portfolio_factor_exposure
        )

        # Compute specific variance (daily): sum(w_i² * σ²_spec_i)
        specific_variance_daily = float(np.sum(weights**2 * specific_variances))

        # IMPORTANT: Floor components FIRST, then sum to get total
        # This prevents negative factor variance (from non-PSD covariance) from
        # canceling specific risk and producing near-zero total risk
        factor_variance_daily = max(factor_variance_daily, 0.0)
        specific_variance_daily = max(specific_variance_daily, 0.0)

        # Total variance (daily) - recomputed after flooring components
        total_variance_daily = factor_variance_daily + specific_variance_daily

        # Guard against near-zero total variance for numerical stability
        # Only apply floor if there's actual risk; for truly empty/zero portfolios,
        # keep everything at 0 to maintain internal consistency
        if total_variance_daily > 0:
            total_variance_daily = max(total_variance_daily, 1e-16)

        return (
            portfolio_factor_exposure,
            factor_variance_daily,
            specific_variance_daily,
            total_variance_daily,
        )

    def validate(self) -> list[str]:
        """
        Validate model inputs for consistency.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Check factor covariance dimensions
        K = len(self.factor_names)
        if self.factor_covariance.shape != (K, K):
            errors.append(
                f"Factor covariance shape {self.factor_covariance.shape} "
                f"doesn't match {K} factors"
            )

        # Check factor covariance is symmetric
        if not np.allclose(self.factor_covariance, self.factor_covariance.T):
            errors.append("Factor covariance matrix is not symmetric")

        # Check factor covariance is PSD
        eigenvalues = np.linalg.eigvalsh(self.factor_covariance)
        if np.any(eigenvalues < -1e-10):
            errors.append(f"Factor covariance is not PSD: min eigenvalue = {eigenvalues.min():.6e}")

        # Check factor loadings has required columns
        missing_factors = [f for f in self.factor_names if f not in self.factor_loadings.columns]
        if missing_factors:
            errors.append(f"Factor loadings missing columns: {missing_factors}")

        # Check specific risks for NaN/negative
        if self.specific_risks.filter(pl.col("specific_variance").is_nan()).height > 0:
            errors.append("Specific risks contain NaN values")

        if self.specific_risks.filter(pl.col("specific_variance") < 0).height > 0:
            errors.append("Specific risks contain negative values")

        return errors

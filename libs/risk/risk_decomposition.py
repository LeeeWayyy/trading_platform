"""
Portfolio risk decomposition and VaR/CVaR calculation.

This module provides result classes for portfolio risk analysis
and utilities for risk decomposition.

All computations follow the Barra methodology with Euler decomposition
for factor contributions.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from libs.risk.barra_model import BarraRiskModel


@dataclass
class PortfolioRiskResult:
    """
    Result of portfolio risk analysis.

    Matches P4T2_TASK.md schema for data/analytics/portfolio_risk.parquet

    All risk values are annualized unless noted otherwise.
    VaR and CVaR are daily values expressed as positive fractions.
    """

    analysis_id: str  # UUID for unique identification
    portfolio_id: str  # User-provided portfolio identifier
    as_of_date: date  # Date of risk analysis
    total_risk: float  # Annualized portfolio volatility
    factor_risk: float  # Systematic risk (annualized)
    specific_risk: float  # Idiosyncratic risk (annualized)
    var_95: float  # 95% VaR (daily, as positive fraction)
    var_99: float | None  # 99% VaR (daily, as positive fraction)
    cvar_95: float  # Expected shortfall at 95% (daily)
    model_version: str  # Version of the risk model used
    dataset_version_ids: dict[str, str]  # Provenance tracking
    computation_timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Additional analytics
    factor_contributions: pl.DataFrame | None = None  # Per-factor contributions
    coverage_ratio: float = 1.0  # Fraction of portfolio weight with risk data

    def validate(self) -> list[str]:
        """
        Validate risk metrics for internal consistency.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        # Total risk should be approximately sqrt(factor² + specific²)
        expected_total = np.sqrt(self.factor_risk**2 + self.specific_risk**2)
        if not np.isclose(self.total_risk, expected_total, rtol=1e-4):
            errors.append(
                f"Total risk {self.total_risk:.6f} != sqrt(factor² + specific²) "
                f"{expected_total:.6f}"
            )

        # Total risk should be >= max(factor_risk, specific_risk)
        if self.total_risk < self.factor_risk - 1e-6:
            errors.append(
                f"Total risk {self.total_risk:.6f} < factor risk {self.factor_risk:.6f}"
            )
        if self.total_risk < self.specific_risk - 1e-6:
            errors.append(
                f"Total risk {self.total_risk:.6f} < specific risk {self.specific_risk:.6f}"
            )

        # VaR should be positive (it represents potential loss)
        if self.var_95 < 0:
            errors.append(f"VaR_95 {self.var_95:.6f} is negative")

        # CVaR >= VaR (expected shortfall is always >= VaR)
        if self.cvar_95 < self.var_95 - 1e-6:
            errors.append(
                f"CVaR_95 {self.cvar_95:.6f} < VaR_95 {self.var_95:.6f} (impossible)"
            )

        # Risks should be non-negative
        if self.total_risk < 0:
            errors.append(f"Total risk {self.total_risk:.6f} is negative")
        if self.factor_risk < 0:
            errors.append(f"Factor risk {self.factor_risk:.6f} is negative")
        if self.specific_risk < 0:
            errors.append(f"Specific risk {self.specific_risk:.6f} is negative")

        # Note: Factor contributions don't have a simple validation sum check
        # because percent contributions sum to (factor_risk/total_risk)², not 1.0
        # This is expected behavior from Euler decomposition

        return errors

    def to_storage_format(self) -> tuple[pl.DataFrame, pl.DataFrame | None]:
        """
        Convert to storage format matching P4T2_TASK.md schema.

        Returns:
            Tuple of:
            - portfolio_risk DataFrame (single row)
            - factor_contributions DataFrame (or None if not computed)
        """
        version_str = "|".join(
            f"{k}:{v}" for k, v in sorted(self.dataset_version_ids.items())
        )

        portfolio_df = pl.DataFrame(
            {
                "analysis_id": [self.analysis_id],
                "portfolio_id": [self.portfolio_id],
                "as_of_date": [self.as_of_date],
                "total_risk": [self.total_risk],
                "factor_risk": [self.factor_risk],
                "specific_risk": [self.specific_risk],
                "var_95": [self.var_95],
                "var_99": [self.var_99],
                "cvar_95": [self.cvar_95],
                "model_version": [self.model_version],
                "dataset_version_id": [version_str],
                "computation_timestamp": [self.computation_timestamp],
            }
        )

        factor_df = None
        if self.factor_contributions is not None:
            factor_df = self.factor_contributions.with_columns(
                pl.lit(self.analysis_id).alias("analysis_id")
            ).select(
                [
                    "analysis_id",
                    "factor_name",
                    "marginal_contribution",
                    "component_contribution",
                    "percent_contribution",
                ]
            )

        return portfolio_df, factor_df


@dataclass
class FactorContribution:
    """
    Per-factor risk contribution.

    Matches P4T2_TASK.md schema for data/analytics/factor_contributions.parquet
    """

    analysis_id: str  # Links to portfolio_risk
    factor_name: str  # Factor name from canonical order
    marginal_contribution: float  # MCTR: ∂σ/∂exposure_k
    component_contribution: float  # CCTR: exposure_k * MCTR_k
    percent_contribution: float  # % of total risk: CCTR / σ_p


class RiskDecomposer:
    """
    Decompose portfolio risk into factor and specific components.

    This class provides a high-level interface for portfolio risk analysis
    using a Barra-style risk model.

    Provides:
    - Total risk breakdown (factor vs specific)
    - MCTR (Marginal Contribution to Risk): ∂σ/∂exposure
    - CCTR (Component Contribution to Risk): exposure * MCTR
    - VaR and CVaR using parametric normal assumption

    Example:
        risk_model = BarraRiskModel.from_t22_results(cov_result, spec_result, loadings)
        decomposer = RiskDecomposer(risk_model)
        result = decomposer.decompose(portfolio, "my_portfolio")
        print(f"Total Risk: {result.total_risk:.2%}")
        print(f"Factor Risk: {result.factor_risk:.2%}")
        print(f"Specific Risk: {result.specific_risk:.2%}")
        print(f"95% VaR (daily): {result.var_95:.2%}")
    """

    def __init__(self, risk_model: "BarraRiskModel"):
        """
        Initialize the decomposer with a risk model.

        Args:
            risk_model: BarraRiskModel instance with factor covariance,
                       loadings, and specific risks
        """
        self.model = risk_model

    def decompose(
        self,
        portfolio: pl.DataFrame,
        portfolio_id: str,
        expected_return: float = 0.0,
        holding_period_days: int = 1,
        validate_inputs: bool = True,
    ) -> PortfolioRiskResult:
        """
        Full portfolio risk decomposition.

        Args:
            portfolio: DataFrame with permno, weight columns.
                      Supports long-only, long/short, and dollar-neutral portfolios.
                      Coverage is computed using absolute weights to handle shorts.
            portfolio_id: Identifier for the portfolio
            expected_return: Optional expected daily return for VaR calculation
            holding_period_days: Holding period for VaR/CVaR (default 1 day)
            validate_inputs: Whether to validate model inputs first (default True)

        Returns:
            PortfolioRiskResult with full risk decomposition

        Raises:
            InsufficientCoverageError: If portfolio coverage < min_coverage
            ValueError: If validate_inputs=True and model inputs are invalid
        """
        return self.model.compute_portfolio_risk(
            portfolio,
            portfolio_id,
            expected_return,
            holding_period_days,
            validate_inputs,
        )

    def get_factor_contributions(self, portfolio: pl.DataFrame) -> pl.DataFrame:
        """
        Get per-factor risk contributions.

        Args:
            portfolio: DataFrame with permno, weight columns

        Returns:
            DataFrame with factor_name, MCTR, CCTR, percent_contribution
        """
        return self.model.compute_factor_contributions(portfolio)

    def check_portfolio_coverage(
        self, portfolio: pl.DataFrame
    ) -> tuple[float, list[int]]:
        """
        Check what fraction of portfolio has risk data.

        Args:
            portfolio: DataFrame with permno, weight columns

        Returns:
            Tuple of (coverage_ratio, missing_permnos)
        """
        coverage_ratio, missing_permnos, _ = self.model.check_coverage(portfolio)
        return coverage_ratio, missing_permnos


def compute_var_parametric(
    sigma: float,
    confidence: float = 0.95,
    holding_period_days: int = 1,
    expected_return: float = 0.0,
) -> float:
    """
    Compute parametric VaR assuming normal distribution.

    VaR_α = -μ * hp + σ * z_α * √hp

    Where:
    - μ is the expected daily return (scaled linearly by holding period)
    - σ is the daily standard deviation (scaled by √hp per IID assumption)
    - z_α is the standard normal quantile at confidence α
    - hp is the holding period in days

    Args:
        sigma: Daily portfolio standard deviation
        confidence: Confidence level (e.g., 0.95, 0.99)
        holding_period_days: Holding period for VaR (default 1 day)
        expected_return: Expected daily return (default 0)

    Returns:
        VaR as positive fraction representing potential loss
    """
    from scipy import stats  # type: ignore[import-untyped]

    z_alpha = stats.norm.ppf(confidence)
    hp_sqrt = np.sqrt(holding_period_days)
    # μ scales linearly (expected return over period), σ scales by √hp (IID volatility)
    raw_var = -expected_return * holding_period_days + sigma * z_alpha * hp_sqrt
    # Floor to 0: VaR represents potential loss, cannot be negative
    return float(max(raw_var, 0.0))


def compute_cvar_parametric(
    sigma: float,
    confidence: float = 0.95,
    holding_period_days: int = 1,
    expected_return: float = 0.0,
) -> float:
    """
    Compute parametric CVaR (Expected Shortfall) assuming normal distribution.

    CVaR_α = -μ * hp + σ * φ(z_α) / (1-α) * √hp

    Where:
    - μ is the expected daily return (scaled linearly by holding period)
    - σ is the daily standard deviation (scaled by √hp per IID assumption)
    - φ is the standard normal PDF

    CVaR >= VaR always (expected shortfall is the average loss beyond VaR).

    Args:
        sigma: Daily portfolio standard deviation
        confidence: Confidence level (e.g., 0.95)
        holding_period_days: Holding period for CVaR (default 1 day)
        expected_return: Expected daily return (default 0)

    Returns:
        CVaR as positive fraction representing expected loss beyond VaR
    """
    from scipy import stats

    z_alpha = stats.norm.ppf(confidence)
    pdf_at_z = stats.norm.pdf(z_alpha)
    hp_sqrt = np.sqrt(holding_period_days)
    # μ scales linearly (expected return over period), σ scales by √hp (IID volatility)
    raw_cvar = -expected_return * holding_period_days + sigma * pdf_at_z / (1 - confidence) * hp_sqrt
    # Floor to 0: CVaR represents expected loss, cannot be negative
    return float(max(raw_cvar, 0.0))

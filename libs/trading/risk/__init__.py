"""
Risk analytics module for factor covariance, specific risk, and portfolio risk.

This module provides:
- FactorCovarianceEstimator: Estimate factor covariance matrices
- SpecificRiskEstimator: Estimate stock-level idiosyncratic risk
- BarraRiskModel: Barra-style multi-factor risk model
- RiskDecomposer: Portfolio risk decomposition with MCTR/CCTR
- PortfolioOptimizer: Mean-variance optimization with constraints
- StressTester: Historical and hypothetical stress testing

All computations are point-in-time (PIT) correct and include
dataset versioning metadata for reproducibility.
"""

from libs.trading.risk.barra_model import (
    BarraRiskModel,
    BarraRiskModelConfig,
    InsufficientCoverageError,
)
from libs.trading.risk.factor_covariance import (
    CANONICAL_FACTOR_ORDER,
    CovarianceConfig,
    CovarianceResult,
    FactorCovarianceEstimator,
    InsufficientDataError,
)

try:
    from libs.trading.risk.portfolio_optimizer import (  # noqa: F401
        BoxConstraint,
        BudgetConstraint,
        Constraint,
        ConstraintPriority,
        FactorExposureConstraint,
        GrossLeverageConstraint,
        InfeasibleOptimizationError,
        InsufficientUniverseCoverageError,
        OptimizationResult,
        OptimizerConfig,
        PortfolioOptimizer,
        RelaxableConstraint,
        ReturnTargetConstraint,
        SectorConstraint,
        TurnoverConstraint,
    )

    _PORTFOLIO_EXPORTS = [
        "OptimizerConfig",
        "OptimizationResult",
        "Constraint",
        "ConstraintPriority",
        "RelaxableConstraint",
        "BudgetConstraint",
        "GrossLeverageConstraint",
        "BoxConstraint",
        "SectorConstraint",
        "FactorExposureConstraint",
        "TurnoverConstraint",
        "ReturnTargetConstraint",
        "PortfolioOptimizer",
        "InfeasibleOptimizationError",
        "InsufficientUniverseCoverageError",
    ]
except ImportError:  # Optional dependency (cvxpy) may be missing in light-weight envs.
    _PORTFOLIO_EXPORTS = []
from libs.trading.risk.risk_decomposition import (
    FactorContribution,
    PortfolioRiskResult,
    RiskDecomposer,
    compute_cvar_parametric,
    compute_var_parametric,
)
from libs.trading.risk.specific_risk import (
    CRSPProviderProtocol,
    SpecificRiskEstimator,
    SpecificRiskResult,
)
from libs.trading.risk.stress_testing import (
    MissingHistoricalDataError,
    StressScenario,
    StressTester,
    StressTestResult,
)

__all__ = [
    # Factor Covariance
    "CANONICAL_FACTOR_ORDER",
    "CovarianceConfig",
    "CovarianceResult",
    "FactorCovarianceEstimator",
    "InsufficientDataError",
    # Specific Risk
    "CRSPProviderProtocol",
    "SpecificRiskEstimator",
    "SpecificRiskResult",
    # Barra Risk Model
    "BarraRiskModel",
    "BarraRiskModelConfig",
    "InsufficientCoverageError",
    # Risk Decomposition
    "PortfolioRiskResult",
    "FactorContribution",
    "RiskDecomposer",
    "compute_var_parametric",
    "compute_cvar_parametric",
    # Portfolio Optimizer (optional dependency)
    *_PORTFOLIO_EXPORTS,
    # Stress Testing
    "StressScenario",
    "StressTestResult",
    "StressTester",
    "MissingHistoricalDataError",
]

"""
Risk analytics module for factor covariance and specific risk estimation.

This module provides:
- FactorCovarianceEstimator: Estimate factor covariance matrices
- SpecificRiskEstimator: Estimate stock-level idiosyncratic risk

All computations are point-in-time (PIT) correct and include
dataset versioning metadata for reproducibility.
"""

from libs.risk.factor_covariance import (
    CANONICAL_FACTOR_ORDER,
    CovarianceConfig,
    CovarianceResult,
    FactorCovarianceEstimator,
    InsufficientDataError,
)
from libs.risk.specific_risk import (
    CRSPProviderProtocol,
    SpecificRiskEstimator,
    SpecificRiskResult,
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
]

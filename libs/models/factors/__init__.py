"""
Factor construction library for multi-factor model building.

This library provides tools for computing and analyzing equity factors
with full point-in-time (PIT) correctness and reproducibility.

Key Components:
    - FactorBuilder: Main computation engine
    - FactorDefinition: Protocol for custom factors
    - FactorAnalytics: IC analysis, decay curves, correlations
    - Canonical Factors: momentum, value, quality, size, low-vol

Example:
    >>> from libs.models.factors import FactorBuilder, FactorConfig
    >>> from libs.data.data_providers.crsp_local_provider import CRSPLocalProvider
    >>> from libs.data.data_providers.compustat_local_provider import CompustatLocalProvider
    >>> from libs.data.data_quality.manifest import ManifestManager
    >>>
    >>> builder = FactorBuilder(crsp, compustat, manifest)
    >>> result = builder.compute_factor("momentum_12_1", date(2023, 6, 30))
    >>> print(result.exposures.head())
"""

from libs.models.factors.cache import CacheCorruptionError, CacheError, DiskExpressionCache
from libs.models.factors.factor_analytics import FactorAnalytics, ICAnalysis
from libs.models.factors.factor_builder import FactorBuilder
from libs.models.factors.factor_definitions import (
    CANONICAL_FACTORS,
    BookToMarketFactor,
    FactorConfig,
    FactorDefinition,
    FactorResult,
    MomentumFactor,
    RealizedVolFactor,
    ROEFactor,
    SizeFactor,
)

__all__ = [
    # Core classes
    "FactorBuilder",
    "FactorConfig",
    "FactorDefinition",
    "FactorResult",
    # Analytics
    "FactorAnalytics",
    "ICAnalysis",
    # Canonical factors
    "MomentumFactor",
    "BookToMarketFactor",
    "ROEFactor",
    "SizeFactor",
    "RealizedVolFactor",
    "CANONICAL_FACTORS",
    # Cache
    "DiskExpressionCache",
    "CacheError",
    "CacheCorruptionError",
]

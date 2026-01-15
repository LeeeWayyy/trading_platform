"""Analytics library for market microstructure, volatility, event study, and factor attribution.

This module provides tools for analyzing high-frequency TAQ data and corporate events:
- MicrostructureAnalyzer: VPIN, realized volatility, spread/depth statistics
- HARVolatilityModel: Heterogeneous Autoregressive model for volatility forecasting
- EventStudyFramework: Event study analysis (CAR, PEAD, index rebalance)
- FactorAttribution: Fama-French factor attribution with robust standard errors

All outputs include dataset_version_id for reproducibility.
"""

from __future__ import annotations

from libs.platform.analytics.attribution import (
    AttributionResult,
    DataMismatchError,
    FactorAttribution,
    FactorAttributionConfig,
    FactorAttributionError,
    InsufficientObservationsError,
    PITViolationError,
    ReturnDecompositionResult,
    RollingExposureResult,
)
from libs.platform.analytics.event_study import (
    ClusteringMitigation,
    EventStudyAnalysis,
    EventStudyConfig,
    EventStudyFramework,
    EventStudyResult,
    ExpectedReturnModel,
    IndexRebalanceResult,
    MarketModelResult,
    OverlapPolicy,
    PEADAnalysisResult,
    SignificanceTest,
)
from libs.platform.analytics.microstructure import (
    CompositeVersionInfo,
    IntradayPatternResult,
    MicrostructureAnalyzer,
    MicrostructureResult,
    RealizedVolatilityResult,
    SpreadDepthResult,
    VPINResult,
)
from libs.platform.analytics.volatility import (
    HARForecastResult,
    HARModelResult,
    HARVolatilityModel,
)

__all__ = [
    # Base classes
    "MicrostructureResult",
    "CompositeVersionInfo",
    # Microstructure results
    "RealizedVolatilityResult",
    "VPINResult",
    "IntradayPatternResult",
    "SpreadDepthResult",
    # Microstructure analyzer
    "MicrostructureAnalyzer",
    # HAR model
    "HARVolatilityModel",
    "HARModelResult",
    "HARForecastResult",
    # Event Study enums
    "ExpectedReturnModel",
    "SignificanceTest",
    "OverlapPolicy",
    "ClusteringMitigation",
    # Event Study config
    "EventStudyConfig",
    # Event Study results
    "EventStudyResult",
    "MarketModelResult",
    "EventStudyAnalysis",
    "PEADAnalysisResult",
    "IndexRebalanceResult",
    # Event Study framework
    "EventStudyFramework",
    # Factor Attribution
    "FactorAttribution",
    "FactorAttributionConfig",
    "AttributionResult",
    "RollingExposureResult",
    "ReturnDecompositionResult",
    "FactorAttributionError",
    "InsufficientObservationsError",
    "DataMismatchError",
    "PITViolationError",
]

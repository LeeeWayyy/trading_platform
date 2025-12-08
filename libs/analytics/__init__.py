"""Analytics library for market microstructure and volatility analysis.

This module provides tools for analyzing high-frequency TAQ data:
- MicrostructureAnalyzer: VPIN, realized volatility, spread/depth statistics
- HARVolatilityModel: Heterogeneous Autoregressive model for volatility forecasting

All outputs include dataset_version_id for reproducibility.
"""

from __future__ import annotations

from libs.analytics.microstructure import (
    CompositeVersionInfo,
    IntradayPatternResult,
    MicrostructureAnalyzer,
    MicrostructureResult,
    RealizedVolatilityResult,
    SpreadDepthResult,
    VPINResult,
)
from libs.analytics.volatility import (
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
]

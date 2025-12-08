"""
Alpha Research Framework
========================

A comprehensive framework for quantitative alpha signal research with
point-in-time (PIT) correct backtesting and dual-backend metrics.

Key Components:
- AlphaDefinition: Protocol for alpha signal computation
- AlphaMetricsAdapter: Metrics with Qlib/local fallback
- PITBacktester: PIT-correct backtesting engine
- Canonical alphas: Momentum, Value, Quality, Reversal, Volatility

Example Usage:

    from libs.alpha import (
        PITBacktester,
        MomentumAlpha,
        AlphaMetricsAdapter,
    )
    from libs.data_quality.versioning import DatasetVersionManager
    from libs.data_providers.crsp_local_provider import CRSPLocalProvider
    from libs.data_providers.compustat_local_provider import CompustatLocalProvider
    from datetime import date

    # Initialize components
    version_mgr = DatasetVersionManager(...)
    crsp = CRSPLocalProvider(...)
    compustat = CompustatLocalProvider(...)
    metrics = AlphaMetricsAdapter(prefer_qlib=True)

    # Create backtester
    backtester = PITBacktester(version_mgr, crsp, compustat, metrics)

    # Define alpha
    alpha = MomentumAlpha(lookback_days=252, skip_days=21)

    # Run backtest
    result = backtester.run_backtest(
        alpha=alpha,
        start_date=date(2020, 1, 1),
        end_date=date(2022, 12, 31),
        weight_method="zscore",
    )

    # Analyze results
    print(f"Alpha: {result.alpha_name}")
    print(f"Mean IC: {result.mean_ic:.4f}")
    print(f"ICIR: {result.icir:.2f}")
    print(f"Hit Rate: {result.hit_rate:.1%}")
    print(f"Average Turnover: {result.average_turnover:.2%}")

    # Decay analysis
    if result.decay_half_life:
        print(f"Half-life: {result.decay_half_life:.1f} days")

Metric Definitions:
- IC (Information Coefficient): Cross-sectional correlation of signal and returns
- Rank IC: Spearman correlation (more robust to outliers)
- ICIR: IC / std(IC), measures signal consistency
- Hit Rate: % of correct direction predictions
- Coverage: % of universe with valid signal
- Long/Short Spread: Top decile return - Bottom decile return
- Turnover: Daily portfolio weight changes

PIT (Point-in-Time) Safety:
- All data access goes through snapshot-locked paths
- PITViolationError raised on any look-ahead attempt
- MissingForwardReturnError raised when forward data unavailable
- Compustat uses 90-day filing lag for PIT correctness
"""

from libs.alpha.alpha_definition import (
    AlphaDefinition,
    AlphaResult,
    BaseAlpha,
)
from libs.alpha.alpha_library import (
    CANONICAL_ALPHAS,
    MomentumAlpha,
    QualityAlpha,
    ReversalAlpha,
    ValueAlpha,
    VolatilityAlpha,
    create_alpha,
)
from libs.alpha.analytics import (
    AlphaAnalytics,
    DecayAnalysisResult,
    GroupedICResult,
)
from libs.alpha.exceptions import (
    AlphaResearchError,
    AlphaValidationError,
    InsufficientDataError,
    MissingForwardReturnError,
    PITViolationError,
)
from libs.alpha.metrics import (
    AlphaMetricsAdapter,
    DecayCurveResult,
    ICIRResult,
    ICResult,
    LocalMetrics,
)
from libs.alpha.portfolio import (
    SignalToWeight,
    TurnoverCalculator,
    TurnoverResult,
)
from libs.alpha.research_platform import (
    BacktestResult,
    PITBacktester,
)

__all__ = [
    # Core definitions
    "AlphaDefinition",
    "AlphaResult",
    "BaseAlpha",
    # Canonical alphas
    "MomentumAlpha",
    "ReversalAlpha",
    "ValueAlpha",
    "QualityAlpha",
    "VolatilityAlpha",
    "CANONICAL_ALPHAS",
    "create_alpha",
    # Metrics
    "AlphaMetricsAdapter",
    "LocalMetrics",
    "ICResult",
    "ICIRResult",
    "DecayCurveResult",
    # Portfolio
    "SignalToWeight",
    "TurnoverCalculator",
    "TurnoverResult",
    # Backtesting
    "PITBacktester",
    "BacktestResult",
    # Analytics
    "AlphaAnalytics",
    "GroupedICResult",
    "DecayAnalysisResult",
    # Exceptions
    "AlphaResearchError",
    "PITViolationError",
    "MissingForwardReturnError",
    "InsufficientDataError",
    "AlphaValidationError",
]

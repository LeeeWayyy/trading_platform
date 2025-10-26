"""
Configuration for ensemble strategy framework.

This module defines all hyperparameters and settings for the ensemble framework,
including combination methods, strategy weights, and confidence thresholds.

All configuration values can be overridden via YAML configuration files or
environment variables for flexibility across environments (dev, staging, prod).
"""

from dataclasses import dataclass, field
from typing import Literal

from strategies.ensemble.combiner import CombinationMethod


@dataclass
class EnsembleConfig:
    """
    Configuration for ensemble strategy combination.

    The ensemble framework combines signals from multiple strategies to produce
    more robust trading decisions. Configuration controls how strategies are
    weighted and combined.

    Attributes:
        combination_method: Method for combining strategy signals
                          Options: weighted_average, majority_vote, unanimous,
                                  confidence_weighted, max_confidence
                          Default: weighted_average

        strategy_weights: Weights for each strategy (must sum to 1.0)
                         Keys: strategy names (e.g., "mean_reversion", "momentum")
                         Values: Weight between 0.0 and 1.0
                         Default: Equal weights

        min_confidence: Minimum ensemble confidence to act on signal (0.0-1.0)
                       Higher = more conservative, fewer trades
                       Lower = more aggressive, more trades
                       Default: 0.6

        min_strategies: Minimum number of strategies required for signal
                       Prevents trading when too few strategies have data
                       Default: 2

        signal_threshold: Threshold for weighted_average method
                         Raw signal > threshold → BUY (+1)
                         Raw signal < -threshold → SELL (-1)
                         Otherwise → HOLD (0)
                         Default: 0.3

        require_agreement: If True, only trade when strategies don't conflict
                          (e.g., don't buy if any strategy signals sell)
                          More conservative, reduces trades
                          Default: False

    Example:
        >>> config = EnsembleConfig(
        ...     combination_method=CombinationMethod.WEIGHTED_AVERAGE,
        ...     strategy_weights={
        ...         "mean_reversion": 0.4,
        ...         "momentum": 0.6,
        ...     },
        ...     min_confidence=0.7,
        ... )
        >>> print(config.combination_method)
        CombinationMethod.WEIGHTED_AVERAGE

    Notes:
        - All thresholds should be validated via backtest before deployment
        - Different market regimes may require different configurations
        - Monitor individual strategy performance to adjust weights
    """

    # Combination method
    combination_method: CombinationMethod = CombinationMethod.WEIGHTED_AVERAGE

    # Strategy weights (must sum to 1.0)
    strategy_weights: dict[str, float] = field(
        default_factory=lambda: {
            "mean_reversion": 0.5,
            "momentum": 0.5,
        }
    )

    # Confidence thresholds
    min_confidence: float = 0.6  # Minimum confidence to act
    signal_threshold: float = 0.3  # Threshold for continuous → discrete

    # Signal filtering
    min_strategies: int = 2  # Minimum strategies required
    require_agreement: bool = False  # Require no conflicting signals

    # Metadata
    version: str = "0.1.0"

    def validate(self) -> None:
        """
        Validate configuration parameters.

        Raises:
            ValueError: If configuration is invalid

        Example:
            >>> config = EnsembleConfig()
            >>> config.validate()  # Passes
            >>> config.strategy_weights = {"mean_reversion": 0.6, "momentum": 0.6}
            >>> config.validate()  # Raises ValueError
        """
        # Validate weights sum to 1.0
        if self.strategy_weights:
            total_weight = sum(self.strategy_weights.values())
            if abs(total_weight - 1.0) > 1e-6:
                raise ValueError(f"Strategy weights must sum to 1.0, got {total_weight}")

            # Check non-negative
            negative = {k: v for k, v in self.strategy_weights.items() if v < 0}
            if negative:
                raise ValueError(f"Weights must be non-negative: {negative}")

        # Validate thresholds
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(f"min_confidence must be in [0, 1], got {self.min_confidence}")

        if not 0.0 <= self.signal_threshold <= 1.0:
            raise ValueError(f"signal_threshold must be in [0, 1], got {self.signal_threshold}")

        # Validate min_strategies
        if self.min_strategies < 1:
            raise ValueError(f"min_strategies must be >= 1, got {self.min_strategies}")


@dataclass
class AdaptiveWeightConfig:
    """
    Configuration for adaptive strategy weighting.

    Adaptive weighting adjusts strategy weights based on recent performance,
    giving more influence to strategies that are currently performing well.

    Attributes:
        enabled: Whether to use adaptive weighting (default: False)
                If False, uses fixed weights from EnsembleConfig

        lookback_days: Days of historical performance to consider (default: 30)
                      Shorter = more responsive to recent changes
                      Longer = more stable, less reactive

        update_frequency: How often to recompute weights (default: "daily")
                         Options: "intraday", "daily", "weekly"

        min_trades: Minimum trades per strategy to compute weights (default: 10)
                   Prevents unstable weights with insufficient data

        performance_metric: Metric for ranking strategies (default: "sharpe")
                           Options: "sharpe", "returns", "win_rate", "profit_factor"

        smoothing_factor: EMA smoothing when updating weights (default: 0.2)
                         Higher = more responsive, lower = more stable
                         new_weight = old_weight * (1-α) + computed_weight * α

    Example:
        >>> config = AdaptiveWeightConfig(
        ...     enabled=True,
        ...     lookback_days=20,
        ...     performance_metric="sharpe",
        ... )
        >>> print(config.smoothing_factor)
        0.2

    Notes:
        - Adaptive weights can improve performance in changing market regimes
        - Requires sufficient historical data to be effective
        - May underperform fixed weights during transitions
        - Always validate on out-of-sample data before deployment
    """

    enabled: bool = False
    lookback_days: int = 30
    update_frequency: Literal["intraday", "daily", "weekly"] = "daily"
    min_trades: int = 10
    performance_metric: Literal["sharpe", "returns", "win_rate", "profit_factor"] = "sharpe"
    smoothing_factor: float = 0.2

    def validate(self) -> None:
        """
        Validate adaptive weight configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if self.lookback_days < 1:
            raise ValueError(f"lookback_days must be >= 1, got {self.lookback_days}")

        if self.min_trades < 1:
            raise ValueError(f"min_trades must be >= 1, got {self.min_trades}")

        if not 0.0 < self.smoothing_factor <= 1.0:
            raise ValueError(f"smoothing_factor must be in (0, 1], got {self.smoothing_factor}")

        valid_frequencies = {"intraday", "daily", "weekly"}
        if self.update_frequency not in valid_frequencies:
            raise ValueError(
                f"update_frequency must be one of {valid_frequencies}, "
                f"got {self.update_frequency}"
            )

        valid_metrics = {"sharpe", "returns", "win_rate", "profit_factor"}
        if self.performance_metric not in valid_metrics:
            raise ValueError(
                f"performance_metric must be one of {valid_metrics}, "
                f"got {self.performance_metric}"
            )


# Default configuration instances
DEFAULT_ENSEMBLE_CONFIG = EnsembleConfig()
DEFAULT_ADAPTIVE_CONFIG = AdaptiveWeightConfig()

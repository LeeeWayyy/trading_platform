"""
Configuration for momentum trading strategy.

This module defines all hyperparameters and settings for the momentum strategy,
including feature parameters, model configuration, and trading rules.

All configuration values can be overridden via YAML configuration files or
environment variables for flexibility across environments (dev, staging, prod).
"""

from dataclasses import dataclass, field


@dataclass
class MomentumFeatureConfig:
    """
    Feature engineering configuration for momentum indicators.

    Attributes:
        ma_fast_period: Fast moving average period (default: 10 days)
                       Shorter period = more responsive to recent price changes

        ma_slow_period: Slow moving average period (default: 50 days)
                       Longer period = smoother trend identification

        macd_fast: MACD fast EMA period (default: 12 days)
        macd_slow: MACD slow EMA period (default: 26 days)
        macd_signal: MACD signal line smoothing period (default: 9 days)

        roc_period: Rate of Change lookback period (default: 14 days)
                   Measures momentum strength over this period

        adx_period: ADX calculation period (default: 14 days)
                   Measures trend strength

    Example:
        >>> config = MomentumFeatureConfig(
        ...     ma_fast_period=10,
        ...     ma_slow_period=50,
        ...     macd_fast=12
        ... )
        >>> print(config.ma_fast_period)
        10

    Notes:
        - These parameters are tuned based on historical backtests
        - May need adjustment for different market regimes
        - Shorter periods = more signals but higher false positive rate
        - Longer periods = fewer signals but higher confidence
    """

    ma_fast_period: int = 10
    ma_slow_period: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    roc_period: int = 14
    adx_period: int = 14


@dataclass
class MomentumModelConfig:
    """
    LightGBM model configuration for momentum strategy.

    Momentum models predict trend continuation based on price and volume
    momentum indicators. Model is trained to identify strong trends early.

    Attributes:
        objective: LightGBM objective function
                  "regression" for continuous return prediction
                  "binary" for directional (up/down) prediction

        metric: Evaluation metric for model validation
               "rmse" = Root Mean Squared Error (regression)
               "ic" = Information Coefficient (rank correlation)

        num_leaves: Maximum tree leaves for base learner (default: 31)
        learning_rate: Boosting learning rate (default: 0.05)
        feature_fraction: Fraction of features per tree (default: 0.8)
        bagging_fraction: Fraction of data per iteration (default: 0.8)
        bagging_freq: Bagging frequency (default: 5)
        min_data_in_leaf: Minimum samples in leaf (default: 50)
        lambda_l1: L1 regularization (default: 0.1)
        lambda_l2: L2 regularization (default: 0.1)
        max_depth: Maximum tree depth (default: 7)
        num_boost_round: Boosting iterations (default: 100)
        early_stopping_rounds: Early stopping patience (default: 20)

    Example:
        >>> config = MomentumModelConfig(
        ...     objective="regression",
        ...     num_leaves=31,
        ...     learning_rate=0.05
        ... )
        >>> lgb_params = config.to_lightgbm_params()

    Notes:
        - Conservative settings reduce overfitting
        - Always validate on out-of-sample data before deployment
    """

    # Objective and metric
    objective: str = "regression"
    metric: str = "rmse"

    # Tree parameters
    num_leaves: int = 31
    learning_rate: float = 0.05
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5
    min_data_in_leaf: int = 50

    # Regularization
    lambda_l1: float = 0.1
    lambda_l2: float = 0.1
    max_depth: int = 7

    # Training parameters
    num_boost_round: int = 100
    early_stopping_rounds: int = 20

    def to_lightgbm_params(self) -> dict[str, int | float | str]:
        """
        Convert config to LightGBM parameter dictionary.

        Returns:
            Dictionary of LightGBM parameters

        Example:
            >>> config = MomentumModelConfig()
            >>> params = config.to_lightgbm_params()
            >>> import lightgbm as lgb
            >>> model = lgb.train(params, train_data)
        """
        return {
            "objective": self.objective,
            "metric": self.metric,
            "num_leaves": self.num_leaves,
            "learning_rate": self.learning_rate,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "min_data_in_leaf": self.min_data_in_leaf,
            "lambda_l1": self.lambda_l1,
            "lambda_l2": self.lambda_l2,
            "max_depth": self.max_depth,
            "verbosity": -1,
        }


@dataclass
class MomentumTradingConfig:
    """
    Trading rules and risk parameters for momentum strategy.

    Defines entry/exit thresholds, position sizing, and risk limits.

    Attributes:
        adx_threshold: Minimum ADX for trend strength (default: 25.0)
                      Enter trades only when ADX > this value (strong trend)

        roc_entry: Minimum ROC for entry signal (default: 5.0)
                  Require at least 5% positive momentum

        macd_entry: Require MACD bullish cross for entry (default: True)

        ma_cross_required: Require MA golden cross for entry (default: True)

        min_confidence: Minimum model confidence for signal (default: 0.6)
                       Only trade when model prediction confidence > this value

        max_position_size: Maximum position size as fraction of portfolio (default: 0.1)
                          10% = no more than 10% of capital in single position

        stop_loss_pct: Stop loss percentage (default: 0.05)
                      Exit position if loss exceeds 5%

        take_profit_pct: Take profit percentage (default: 0.15)
                        Exit position if gain exceeds 15%

    Example:
        >>> config = MomentumTradingConfig(
        ...     adx_threshold=25,
        ...     roc_entry=5.0,
        ...     stop_loss_pct=0.05
        ... )
        >>> if adx > config.adx_threshold and roc > config.roc_entry:
        ...     # Enter long position
        ...     pass

    Notes:
        - Conservative thresholds reduce false signals
        - All thresholds should be validated via backtest before deployment
        - Market regime may require threshold adjustments
    """

    # Entry thresholds
    adx_threshold: float = 25.0  # Minimum trend strength
    roc_entry: float = 5.0  # Minimum momentum (5% gain)
    macd_entry: bool = True  # Require MACD bullish cross
    ma_cross_required: bool = True  # Require MA golden cross

    # Model confidence
    min_confidence: float = 0.6

    # Risk parameters
    max_position_size: float = 0.1  # 10% of portfolio
    stop_loss_pct: float = 0.05  # 5% stop loss
    take_profit_pct: float = 0.15  # 15% take profit


@dataclass
class MomentumConfig:
    """
    Complete configuration for momentum strategy.

    Combines feature, model, and trading configurations into single config object.

    Attributes:
        features: Feature engineering configuration
        model: LightGBM model configuration
        trading: Trading rules and risk parameters
        strategy_name: Name of strategy (for model registry)
        version: Strategy version for tracking changes

    Example:
        >>> config = MomentumConfig()
        >>> print(config.features.ma_fast_period)
        10
        >>> print(config.model.num_leaves)
        31
        >>> print(config.trading.adx_threshold)
        25.0

    Notes:
        - This is the main configuration object used throughout the strategy
        - Can be serialized to YAML for easy configuration management
        - Version tracking ensures reproducibility
    """

    features: MomentumFeatureConfig = field(default_factory=MomentumFeatureConfig)
    model: MomentumModelConfig = field(default_factory=MomentumModelConfig)
    trading: MomentumTradingConfig = field(default_factory=MomentumTradingConfig)

    # Strategy metadata
    strategy_name: str = "momentum"
    version: str = "0.1.0"


# Default configuration instance
DEFAULT_CONFIG = MomentumConfig()

"""
Configuration for mean reversion strategy.

This module defines all hyperparameters and settings for the mean reversion
strategy, including feature parameters, model configuration, and trading rules.

All configuration values can be overridden via YAML configuration files or
environment variables for flexibility across environments (dev, staging, prod).
"""

from dataclasses import dataclass, field


@dataclass
class MeanReversionFeatureConfig:
    """
    Feature engineering configuration for mean reversion indicators.

    Attributes:
        rsi_period: Lookback period for RSI calculation (default: 14 days)
                   Lower values = more sensitive to recent price changes
                   Higher values = smoother, less noisy signals

        bb_period: Lookback period for Bollinger Bands (default: 20 days)
                  Standard period used in technical analysis

        bb_std: Standard deviation multiplier for Bollinger Bands (default: 2.0)
               Higher values = wider bands (fewer signals, higher confidence)
               Lower values = narrower bands (more signals, lower confidence)

        stoch_k_period: Lookback period for Stochastic %K (default: 14 days)
                       Fast line of stochastic oscillator

        stoch_d_period: Smoothing period for Stochastic %D (default: 3 days)
                       Slow line (signal line) of stochastic oscillator

        zscore_period: Lookback period for Z-Score calculation (default: 20 days)
                      Measures statistical deviation from mean

    Example:
        >>> config = MeanReversionFeatureConfig(
        ...     rsi_period=14,
        ...     bb_period=20,
        ...     bb_std=2.0
        ... )
        >>> print(config.rsi_period)
        14

    Notes:
        - These parameters are tuned based on historical backtests
        - May need adjustment for different market regimes
        - Shorter periods = more signals but higher false positive rate
        - Longer periods = fewer signals but higher confidence
    """

    rsi_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    stoch_k_period: int = 14
    stoch_d_period: int = 3
    zscore_period: int = 20


@dataclass
class MeanReversionModelConfig:
    """
    LightGBM model configuration for mean reversion strategy.

    Mean reversion models predict short-term price reversals based on
    deviation from historical mean. Model is trained to predict 1-5 day
    forward returns when mean reversion conditions are present.

    Attributes:
        objective: LightGBM objective function
                  "regression" for continuous return prediction
                  "binary" for directional (up/down) prediction

        metric: Evaluation metric for model validation
               "rmse" = Root Mean Squared Error (regression)
               "ic" = Information Coefficient (rank correlation)

        num_leaves: Maximum tree leaves for base learner
                   Lower values = less overfitting but may underfit
                   Higher values = more expressive but risk overfitting
                   Recommended: 31-127 for financial data

        learning_rate: Boosting learning rate (default: 0.05)
                      Lower values = more robust but slower convergence
                      Higher values = faster training but risk overfitting

        feature_fraction: Fraction of features to use per tree (default: 0.8)
                         Random feature sampling reduces overfitting
                         0.8 = use 80% of features per tree

        bagging_fraction: Fraction of data to use per iteration (default: 0.8)
                         Row sampling (bagging) reduces overfitting
                         0.8 = use 80% of rows per iteration

        bagging_freq: Frequency of bagging (default: 5)
                     Perform bagging every N iterations

        min_data_in_leaf: Minimum samples required in leaf node (default: 50)
                         Higher values = stronger regularization
                         Prevents overfitting on small data subsets

        lambda_l1: L1 regularization term (default: 0.1)
                  Promotes sparse models (feature selection)

        lambda_l2: L2 regularization term (default: 0.1)
                  Shrinks weights towards zero

        max_depth: Maximum tree depth (default: 7)
                  Controls model complexity
                  Deeper trees = more expressive but risk overfitting

        num_boost_round: Number of boosting iterations (default: 100)
                        More rounds = better fit but slower training

        early_stopping_rounds: Stop if no improvement for N rounds (default: 20)
                              Prevents overfitting and saves training time

    Example:
        >>> config = MeanReversionModelConfig(
        ...     objective="regression",
        ...     num_leaves=31,
        ...     learning_rate=0.05
        ... )
        >>> lgb_params = config.to_lightgbm_params()
        >>> # Use lgb_params with LightGBM train()

    Notes:
        - These hyperparameters are tuned via cross-validation
        - Financial data is noisy - conservative settings reduce overfitting
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
            >>> config = MeanReversionModelConfig()
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
            "verbosity": -1,  # Suppress LightGBM warnings
        }


@dataclass
class MeanReversionTradingConfig:
    """
    Trading rules and risk parameters for mean reversion strategy.

    Defines entry/exit thresholds, position sizing, and risk limits.

    Attributes:
        rsi_oversold: RSI threshold for oversold condition (default: 30)
                     Buy signal when RSI < this value
                     Lower values = stronger oversold signal

        rsi_overbought: RSI threshold for overbought condition (default: 70)
                       Sell signal when RSI > this value
                       Higher values = stronger overbought signal

        bb_entry_threshold: Bollinger Band %B threshold for entry (default: 0.0)
                           Buy when %B < this value (price below lower band)
                           Sell when %B > (1.0 + threshold)

        zscore_entry: Z-Score threshold for entry signals (default: -2.0)
                     Buy when zscore < this value (price significantly below mean)
                     Sell when zscore > abs(this value)

        zscore_exit: Z-Score threshold for profit taking (default: 0.0)
                    Close position when zscore returns to mean

        min_confidence: Minimum model confidence for signal (default: 0.6)
                       Only trade when model prediction confidence > this value
                       Range: 0.0-1.0

        max_position_size: Maximum position size as fraction of portfolio (default: 0.1)
                          10% = no more than 10% of capital in single position

        stop_loss_pct: Stop loss percentage (default: 0.05)
                      Exit position if loss exceeds 5%

        take_profit_pct: Take profit percentage (default: 0.10)
                        Exit position if gain exceeds 10%

    Example:
        >>> config = MeanReversionTradingConfig(
        ...     rsi_oversold=30,
        ...     rsi_overbought=70,
        ...     stop_loss_pct=0.05
        ... )
        >>> if rsi < config.rsi_oversold and zscore < config.zscore_entry:
        ...     # Enter long position (oversold)
        ...     pass

    Notes:
        - Conservative thresholds reduce false signals
        - All thresholds should be validated via backtest before deployment
        - Market regime may require threshold adjustments
    """

    # Entry thresholds
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    bb_entry_threshold: float = 0.0
    zscore_entry: float = -2.0
    zscore_exit: float = 0.0

    # Model confidence
    min_confidence: float = 0.6

    # Risk parameters
    max_position_size: float = 0.1  # 10% of portfolio
    stop_loss_pct: float = 0.05  # 5% stop loss
    take_profit_pct: float = 0.10  # 10% take profit


@dataclass
class MeanReversionConfig:
    """
    Complete configuration for mean reversion strategy.

    Combines feature, model, and trading configurations into single config object.

    Attributes:
        features: Feature engineering configuration
        model: LightGBM model configuration
        trading: Trading rules and risk parameters
        strategy_name: Name of strategy (for model registry)
        version: Strategy version for tracking changes

    Example:
        >>> config = MeanReversionConfig()
        >>> print(config.features.rsi_period)
        14
        >>> print(config.model.num_leaves)
        31
        >>> print(config.trading.rsi_oversold)
        30.0

    Notes:
        - This is the main configuration object used throughout the strategy
        - Can be serialized to YAML for easy configuration management
        - Version tracking ensures reproducibility
    """

    features: MeanReversionFeatureConfig = field(default_factory=MeanReversionFeatureConfig)
    model: MeanReversionModelConfig = field(default_factory=MeanReversionModelConfig)
    trading: MeanReversionTradingConfig = field(default_factory=MeanReversionTradingConfig)

    # Strategy metadata
    strategy_name: str = "mean_reversion"
    version: str = "0.1.0"


# Default configuration instance
DEFAULT_CONFIG = MeanReversionConfig()

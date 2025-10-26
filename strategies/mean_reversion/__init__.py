"""
Mean reversion trading strategy based on price oscillators and statistical indicators.

This module implements a mean reversion strategy that identifies overbought and
oversold conditions using technical indicators like RSI, Bollinger Bands, and
stochastic oscillators.

Mean reversion strategies are based on the assumption that prices tend to revert
to their historical mean over time. When prices deviate significantly from the mean,
they present profitable trading opportunities.

Components:
- features: Mean reversion indicators (RSI, Bollinger Bands, Stochastic, etc.)
- config: Strategy configuration and hyperparameters
- model: LightGBM model configuration for mean reversion signals

Strategy Logic:
- BUY signals: When price is significantly below mean (oversold)
- SELL signals: When price is significantly above mean (overbought)
- Features capture price oscillations, volatility, and momentum

See Also:
    - /docs/CONCEPTS/ for trading concepts and terminology
    - /docs/TASKS/P1T6_PROGRESS.md for implementation progress
"""

__version__ = "0.1.0"

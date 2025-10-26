"""
Momentum trading strategy based on trend-following indicators.

This module implements a momentum strategy that identifies and trades with
established price trends using technical indicators like moving averages,
MACD, and trend strength measures.

Momentum strategies are based on the principle that assets exhibiting strong
price trends will continue moving in the same direction for some period,
creating profitable trading opportunities.

Components:
- features: Momentum indicators (MA crossovers, MACD, ADX, ROC, volume)
- config: Strategy configuration and hyperparameters
- model: LightGBM model configuration for momentum signals

Strategy Logic:
- BUY signals: When strong upward momentum is detected (trend confirmation)
- SELL signals: When momentum weakens or reverses (trend exhaustion)
- Features capture trend direction, strength, and persistence

See Also:
    - /docs/CONCEPTS/ for trading concepts and terminology
    - /docs/TASKS/P1T6_PROGRESS.md for implementation progress
"""

__version__ = "0.1.0"

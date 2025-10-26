"""
Backtesting framework for strategy validation.

This module provides tools for validating trading strategy performance
using historical data. Works with signal-based strategies (mean_reversion,
momentum, ensemble) that produce discrete signals (-1, 0, +1).

Key Features:
- Signal-based backtesting (no complex portfolio logic)
- Performance metrics (Sharpe ratio, returns, win rate, drawdown)
- Works with ensemble and individual strategies
- Educational: clear metrics and simple calculations

Components:
- evaluator: Signal-to-performance evaluation
- metrics: Performance metric calculations
- config: Backtest configuration

Example:
    >>> from strategies.backtest import SignalEvaluator
    >>> evaluator = SignalEvaluator(signals_df, returns_df)
    >>> results = evaluator.evaluate()
    >>> print(f"Sharpe Ratio: {results['sharpe']:.2f}")
"""

__version__ = "0.1.0"

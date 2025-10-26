"""
Signal-based backtest evaluator.

This module evaluates trading strategy performance by mapping signals
to returns and computing performance metrics. Works with discrete
signals (-1, 0, +1) from mean_reversion, momentum, and ensemble strategies.

Educational Note:
This is a simplified backtesting approach that assumes:
- Signals executed at next period's open
- No transaction costs (for educational clarity)
- Equal position sizing
- No slippage

Real trading has additional complexities covered in production backtests.
"""


import polars as pl

from strategies.backtest import metrics


class SignalEvaluator:
    """
    Evaluate strategy performance from signals and returns.

    Takes trading signals (-1/0/+1) and actual returns, computes
    strategy returns by applying signals to next-period returns,
    then calculates performance metrics.

    Attributes:
        signals: DataFrame with symbol, date, signal columns
        returns: DataFrame with symbol, date, return columns
        results: Dict of performance metrics after evaluation

    Example:
        >>> evaluator = SignalEvaluator(signals_df, returns_df)
        >>> results = evaluator.evaluate()
        >>> print(f"Sharpe: {results['sharpe']:.2f}")
        Sharpe: 1.85

    Notes:
        - Signals must be -1 (short), 0 (flat), or +1 (long)
        - Returns assumed to be forward-looking (signal → next return)
        - Missing data handled gracefully (filled with 0)
    """

    def __init__(
        self,
        signals: pl.DataFrame,
        returns: pl.DataFrame,
        signal_column: str = "signal",
        return_column: str = "return",
    ) -> None:
        """
        Initialize signal evaluator.

        Args:
            signals: DataFrame with columns: symbol, date, <signal_column>
            returns: DataFrame with columns: symbol, date, <return_column>
            signal_column: Name of signal column (default: "signal")
            return_column: Name of return column (default: "return")

        Raises:
            ValueError: If required columns missing

        Example:
            >>> signals = pl.DataFrame({
            ...     "symbol": ["AAPL", "AAPL"],
            ...     "date": [date(2024, 1, 1), date(2024, 1, 2)],
            ...     "signal": [1, 0],
            ... })
            >>> returns = pl.DataFrame({
            ...     "symbol": ["AAPL", "AAPL"],
            ...     "date": [date(2024, 1, 1), date(2024, 1, 2)],
            ...     "return": [0.01, -0.005],
            ... })
            >>> evaluator = SignalEvaluator(signals, returns)
        """
        self.signals = signals
        self.returns = returns
        self.signal_column = signal_column
        self.return_column = return_column

        self.results: dict[str, float] = {}
        self._strategy_returns: pl.Series | None = None

        # Validate inputs
        self._validate_inputs()

    def _validate_inputs(self) -> None:
        """Validate input DataFrames have required columns."""
        # Check signals DataFrame
        required_signal_cols = {"symbol", "date", self.signal_column}
        missing_signal = required_signal_cols - set(self.signals.columns)
        if missing_signal:
            raise ValueError(f"Signals DataFrame missing required columns: {missing_signal}")

        # Check returns DataFrame
        required_return_cols = {"symbol", "date", self.return_column}
        missing_return = required_return_cols - set(self.returns.columns)
        if missing_return:
            raise ValueError(f"Returns DataFrame missing required columns: {missing_return}")

    def evaluate(
        self,
        commission: float = 0.0,
        risk_free_rate: float = 0.0,
    ) -> dict[str, float]:
        """
        Evaluate strategy performance.

        Process:
        1. Join signals with next-period returns
        2. Calculate strategy returns (signal * return - commission)
        3. Compute performance metrics

        Args:
            commission: Per-trade commission as decimal (default: 0.0)
                       0.001 = 0.1% per trade
            risk_free_rate: Annual risk-free rate (default: 0.0)

        Returns:
            Dictionary with performance metrics:
            - total_return: Cumulative return
            - annualized_return: Annualized return
            - sharpe_ratio: Risk-adjusted return
            - max_drawdown: Largest peak-to-trough decline
            - win_rate: Percentage of profitable trades
            - profit_factor: Gross profit / gross loss
            - num_trades: Number of non-zero signals

        Example:
            >>> evaluator = SignalEvaluator(signals, returns)
            >>> results = evaluator.evaluate(commission=0.001)
            >>> print(f"Sharpe: {results['sharpe_ratio']:.2f}")
            >>> print(f"Win Rate: {results['win_rate']:.1%}")
            Sharpe: 1.85
            Win Rate: 58.5%
        """
        # Join signals with returns on (symbol, date)
        combined = self.signals.join(
            self.returns,
            on=["symbol", "date"],
            how="inner",
        )

        # Calculate strategy returns: signal * return
        # Commission applied on any non-zero signal (trade)
        strategy_returns = combined.select(
            [
                (
                    pl.col(self.signal_column) * pl.col(self.return_column)
                    - pl.when(pl.col(self.signal_column) != 0).then(commission).otherwise(0.0)
                ).alias("strategy_return")
            ]
        )["strategy_return"]

        self._strategy_returns = strategy_returns

        # Calculate metrics
        self.results = {
            "total_return": metrics.calculate_total_return(strategy_returns),
            "annualized_return": metrics.calculate_annualized_return(strategy_returns),
            "sharpe_ratio": metrics.calculate_sharpe_ratio(strategy_returns, risk_free_rate),
            "max_drawdown": metrics.calculate_max_drawdown((1 + strategy_returns).cum_sum()),
            "win_rate": metrics.calculate_win_rate(strategy_returns),
            "profit_factor": metrics.calculate_profit_factor(strategy_returns),
            "num_trades": int((combined[self.signal_column] != 0).sum()),
        }

        return self.results

    def get_strategy_returns(self) -> pl.Series:
        """
        Get strategy returns series after evaluation.

        Returns:
            Series of daily strategy returns

        Raises:
            RuntimeError: If evaluate() not called yet

        Example:
            >>> evaluator = SignalEvaluator(signals, returns)
            >>> evaluator.evaluate()
            >>> returns = evaluator.get_strategy_returns()
            >>> print(returns.mean())
            0.0012
        """
        if self._strategy_returns is None:
            raise RuntimeError("Must call evaluate() before accessing strategy returns")
        return self._strategy_returns

    def get_cumulative_returns(self) -> pl.Series:
        """
        Get cumulative returns series after evaluation.

        Returns:
            Series of cumulative returns (starts at 1.0)

        Raises:
            RuntimeError: If evaluate() not called yet

        Example:
            >>> evaluator = SignalEvaluator(signals, returns)
            >>> evaluator.evaluate()
            >>> cum_returns = evaluator.get_cumulative_returns()
            >>> print(f"Final value: ${cum_returns[-1]:.2f}")
            Final value: $1.25
        """
        if self._strategy_returns is None:
            raise RuntimeError("Must call evaluate() before accessing cumulative returns")
        return (1 + self._strategy_returns).cum_sum()


def quick_evaluate(
    signals: pl.DataFrame,
    returns: pl.DataFrame,
    signal_column: str = "signal",
    return_column: str = "return",
) -> dict[str, float]:
    """
    Quick one-line evaluation of strategy performance.

    Convenience function for simple backtesting without creating
    an evaluator instance.

    Args:
        signals: DataFrame with symbol, date, signal columns
        returns: DataFrame with symbol, date, return columns
        signal_column: Name of signal column (default: "signal")
        return_column: Name of return column (default: "return")

    Returns:
        Dictionary of performance metrics

    Example:
        >>> results = quick_evaluate(signals_df, returns_df)
        >>> print(f"Sharpe: {results['sharpe_ratio']:.2f}")
        Sharpe: 1.85
    """
    evaluator = SignalEvaluator(
        signals, returns, signal_column=signal_column, return_column=return_column
    )
    return evaluator.evaluate()

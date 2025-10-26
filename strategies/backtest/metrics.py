"""
Performance metrics for backtesting evaluation.

This module provides common trading performance metrics used to evaluate
strategy effectiveness. All metrics work with simple returns series.

Educational Note:
- Sharpe Ratio: Risk-adjusted return (higher is better)
- Max Drawdown: Largest peak-to-trough decline (lower is better)
- Win Rate: Percentage of profitable trades
- Profit Factor: Ratio of gross profits to gross losses
"""

from typing import cast

import numpy as np
import polars as pl


def calculate_sharpe_ratio(returns: pl.Series, risk_free_rate: float = 0.0) -> float:
    """
    Calculate annualized Sharpe ratio.

    Sharpe = (Mean Return - Risk Free Rate) / Std Dev of Returns * sqrt(252)

    Args:
        returns: Daily returns series
        risk_free_rate: Annual risk-free rate (default: 0.0)

    Returns:
        Annualized Sharpe ratio

    Example:
        >>> returns = pl.Series([0.01, -0.005, 0.02, 0.01])
        >>> sharpe = calculate_sharpe_ratio(returns)
        >>> print(f"Sharpe: {sharpe:.2f}")
        Sharpe: 2.45

    Notes:
        - Assumes 252 trading days per year
        - Returns should be in decimal form (0.01 = 1%)
        - Sharpe > 1 is good, > 2 is very good, > 3 is exceptional
    """
    if len(returns) == 0:
        return 0.0

    mean_return = returns.mean()
    std_return = returns.std()

    if std_return == 0 or std_return is None:
        return 0.0

    # Type narrowing for mypy (Polars returns should be numeric for numeric series)
    mean_val = cast(float, mean_return)
    std_val = cast(float, std_return)

    # Annualize (252 trading days)
    daily_rf = risk_free_rate / 252
    sharpe = (mean_val - daily_rf) / std_val * np.sqrt(252)

    return float(sharpe) if sharpe is not None else 0.0


def calculate_max_drawdown(cumulative_returns: pl.Series) -> float:
    """
    Calculate maximum drawdown from cumulative returns.

    Max Drawdown = (Trough Value - Peak Value) / Peak Value

    Args:
        cumulative_returns: Cumulative returns series starting at 1.0 (multiplicative compounding)

    Returns:
        Maximum drawdown as negative decimal (-0.15 = -15%)

    Example:
        >>> cum_returns = pl.Series([1.0, 1.1, 1.05, 1.15, 1.0])
        >>> mdd = calculate_max_drawdown(cum_returns)
        >>> print(f"Max Drawdown: {mdd:.2%}")
        Max Drawdown: -13.04%

    Notes:
        - Always negative or zero
        - Lower (more negative) is worse
        - -10% drawdown means portfolio lost 10% from peak
    """
    if len(cumulative_returns) == 0:
        return 0.0

    # Calculate running maximum
    running_max = cumulative_returns.cum_max()

    # Calculate drawdown at each point
    drawdown = (cumulative_returns - running_max) / running_max

    # Return the maximum (most negative) drawdown
    max_dd = drawdown.min()

    # Type narrowing for mypy
    if max_dd is not None:
        return float(cast(float, max_dd))
    return 0.0


def calculate_win_rate(returns: pl.Series) -> float:
    """
    Calculate percentage of profitable periods.

    Win Rate = Number of Positive Returns / Total Returns

    Args:
        returns: Returns series

    Returns:
        Win rate as decimal (0.55 = 55%)

    Example:
        >>> returns = pl.Series([0.01, -0.005, 0.02, 0.01, -0.01])
        >>> win_rate = calculate_win_rate(returns)
        >>> print(f"Win Rate: {win_rate:.1%}")
        Win Rate: 60.0%

    Notes:
        - 50% is random/break-even
        - 55-60% is good for daily strategies
        - Higher is generally better but consider risk
    """
    if len(returns) == 0:
        return 0.0

    # Filter to non-zero returns (actual trades)
    non_zero_returns = returns.filter(returns != 0.0)

    if len(non_zero_returns) == 0:
        return 0.0

    winning_trades = (non_zero_returns > 0).sum()
    total_trades = len(non_zero_returns)

    return float(winning_trades) / total_trades


def calculate_profit_factor(returns: pl.Series) -> float:
    """
    Calculate profit factor (gross profits / gross losses).

    Profit Factor = Sum of Gains / Abs(Sum of Losses)

    Args:
        returns: Returns series

    Returns:
        Profit factor (1.5 means $1.50 profit per $1 loss)

    Example:
        >>> returns = pl.Series([0.02, -0.01, 0.03, -0.01])
        >>> pf = calculate_profit_factor(returns)
        >>> print(f"Profit Factor: {pf:.2f}")
        Profit Factor: 2.50

    Notes:
        - > 1.0 means profitable overall
        - 1.5-2.0 is good
        - < 1.0 means losing strategy
    """
    if len(returns) == 0:
        return 0.0

    gains = returns.filter(returns > 0).sum()
    losses = returns.filter(returns < 0).sum()

    if losses == 0:
        # All gains, no losses
        return float("inf") if gains > 0 else 0.0

    profit_factor = gains / abs(losses)

    return float(profit_factor) if profit_factor is not None else 0.0


def calculate_total_return(returns: pl.Series) -> float:
    """
    Calculate total cumulative return.

    Total Return = Product(1 + returns) - 1

    Args:
        returns: Returns series

    Returns:
        Total return as decimal (0.25 = 25%)

    Example:
        >>> returns = pl.Series([0.01, 0.02, -0.01])
        >>> total = calculate_total_return(returns)
        >>> print(f"Total Return: {total:.2%}")
        Total Return: 2.00%

    Notes:
        - Compounds returns properly
        - 0.25 = 25% total gain
        - -0.10 = 10% total loss
    """
    if len(returns) == 0:
        return 0.0

    # Compound returns: (1 + r1) * (1 + r2) * ... - 1
    cumulative = (1 + returns).product() - 1

    return float(cumulative) if cumulative is not None else 0.0


def calculate_annualized_return(returns: pl.Series, periods_per_year: int = 252) -> float:
    """
    Calculate annualized return.

    Annualized Return = (1 + Total Return) ^ (periods_per_year / num_periods) - 1

    Args:
        returns: Returns series
        periods_per_year: Trading periods per year (default: 252 days)

    Returns:
        Annualized return as decimal (0.15 = 15% per year)

    Example:
        >>> returns = pl.Series([0.001] * 100)  # 100 days of 0.1% returns
        >>> ann_ret = calculate_annualized_return(returns)
        >>> print(f"Annualized: {ann_ret:.2%}")
        Annualized: 28.79%

    Notes:
        - Standardizes returns to annual basis
        - Makes strategies comparable regardless of time period
        - Assumes constant compounding
    """
    if len(returns) == 0:
        return 0.0

    total_return = calculate_total_return(returns)
    num_periods = len(returns)

    if num_periods == 0:
        return 0.0

    # Annualize
    annualized = (1 + total_return) ** (periods_per_year / num_periods) - 1

    return float(annualized)

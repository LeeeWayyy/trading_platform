"""
Tests for backtest performance metrics.

This module tests all performance metric calculations used in backtesting.
"""

import math

import polars as pl

from strategies.backtest.metrics import (
    calculate_annualized_return,
    calculate_max_drawdown,
    calculate_profit_factor,
    calculate_sharpe_ratio,
    calculate_total_return,
    calculate_win_rate,
)


class TestSharpeRatio:
    """Test Sharpe ratio calculation."""

    def test_positive_sharpe(self) -> None:
        """Test Sharpe ratio with positive returns."""
        returns = pl.Series([0.01, 0.02, 0.015, 0.01, 0.018])
        sharpe = calculate_sharpe_ratio(returns)

        # Should be positive with consistent positive returns
        assert sharpe > 0
        # With low volatility, Sharpe should be high
        assert sharpe > 2.0

    def test_negative_sharpe(self) -> None:
        """Test Sharpe ratio with negative returns."""
        returns = pl.Series([-0.01, -0.02, -0.015, -0.01])
        sharpe = calculate_sharpe_ratio(returns)

        # Should be negative with negative returns
        assert sharpe < 0

    def test_zero_volatility(self) -> None:
        """Test Sharpe ratio with zero volatility."""
        returns = pl.Series([0.01, 0.01, 0.01, 0.01])
        sharpe = calculate_sharpe_ratio(returns)

        # Zero std dev should return 0
        assert sharpe == 0.0

    def test_empty_returns(self) -> None:
        """Test Sharpe ratio with empty series."""
        returns = pl.Series([])
        sharpe = calculate_sharpe_ratio(returns)

        assert sharpe == 0.0

    def test_with_risk_free_rate(self) -> None:
        """Test Sharpe ratio with non-zero risk-free rate."""
        returns = pl.Series([0.01, 0.02, 0.015])
        sharpe_no_rf = calculate_sharpe_ratio(returns, risk_free_rate=0.0)
        sharpe_with_rf = calculate_sharpe_ratio(returns, risk_free_rate=0.02)

        # Higher risk-free rate should lower Sharpe
        assert sharpe_with_rf < sharpe_no_rf


class TestMaxDrawdown:
    """Test maximum drawdown calculation."""

    def test_no_drawdown(self) -> None:
        """Test with monotonically increasing cumulative returns."""
        cum_returns = pl.Series([1.0, 1.1, 1.2, 1.3])
        mdd = calculate_max_drawdown(cum_returns)

        # No drawdown = 0
        assert mdd == 0.0

    def test_simple_drawdown(self) -> None:
        """Test with single drawdown."""
        # Peak at 1.2, trough at 1.0 → -16.67% drawdown
        cum_returns = pl.Series([1.0, 1.2, 1.1, 1.0, 1.15])
        mdd = calculate_max_drawdown(cum_returns)

        # Should be negative
        assert mdd < 0
        # Approximately -16.67%
        assert abs(mdd - (-0.1667)) < 0.01

    def test_multiple_drawdowns(self) -> None:
        """Test with multiple drawdowns, return largest."""
        # Two drawdowns: 1.2→1.0 (-16.67%) and 1.3→1.1 (-15.38%)
        cum_returns = pl.Series([1.0, 1.2, 1.0, 1.3, 1.1])
        mdd = calculate_max_drawdown(cum_returns)

        # Should return the larger (more negative) one
        assert mdd < -0.16

    def test_empty_series(self) -> None:
        """Test with empty cumulative returns."""
        cum_returns = pl.Series([])
        mdd = calculate_max_drawdown(cum_returns)

        assert mdd == 0.0


class TestWinRate:
    """Test win rate calculation."""

    def test_all_wins(self) -> None:
        """Test with all positive returns."""
        returns = pl.Series([0.01, 0.02, 0.015])
        win_rate = calculate_win_rate(returns)

        assert win_rate == 1.0  # 100%

    def test_all_losses(self) -> None:
        """Test with all negative returns."""
        returns = pl.Series([-0.01, -0.02, -0.015])
        win_rate = calculate_win_rate(returns)

        assert win_rate == 0.0  # 0%

    def test_mixed_returns(self) -> None:
        """Test with mix of wins and losses."""
        # 3 wins, 2 losses = 60% win rate
        returns = pl.Series([0.01, -0.01, 0.02, 0.015, -0.005])
        win_rate = calculate_win_rate(returns)

        assert abs(win_rate - 0.6) < 0.01

    def test_with_zeros(self) -> None:
        """Test that zero returns are ignored."""
        # Only count non-zero: 2 wins, 1 loss = 66.67%
        returns = pl.Series([0.01, 0.0, -0.01, 0.0, 0.02])
        win_rate = calculate_win_rate(returns)

        assert abs(win_rate - 0.6667) < 0.01

    def test_empty_returns(self) -> None:
        """Test with empty series."""
        returns = pl.Series([])
        win_rate = calculate_win_rate(returns)

        assert win_rate == 0.0

    def test_only_zeros(self) -> None:
        """Test with only zero returns."""
        returns = pl.Series([0.0, 0.0, 0.0])
        win_rate = calculate_win_rate(returns)

        assert win_rate == 0.0


class TestProfitFactor:
    """Test profit factor calculation."""

    def test_profitable_strategy(self) -> None:
        """Test with overall profitable strategy."""
        # Gains: 0.05, Losses: -0.02, PF = 2.5
        returns = pl.Series([0.02, -0.01, 0.03, -0.01])
        pf = calculate_profit_factor(returns)

        assert abs(pf - 2.5) < 0.01

    def test_losing_strategy(self) -> None:
        """Test with overall losing strategy."""
        # Gains: 0.02, Losses: -0.05, PF = 0.4
        returns = pl.Series([0.01, -0.02, 0.01, -0.03])
        pf = calculate_profit_factor(returns)

        assert pf < 1.0
        assert abs(pf - 0.4) < 0.01

    def test_no_losses(self) -> None:
        """Test with only gains (no losses)."""
        returns = pl.Series([0.01, 0.02, 0.03])
        pf = calculate_profit_factor(returns)

        # Should be infinity
        assert math.isinf(pf)

    def test_no_gains(self) -> None:
        """Test with only losses (no gains)."""
        returns = pl.Series([-0.01, -0.02, -0.03])
        pf = calculate_profit_factor(returns)

        assert pf == 0.0

    def test_empty_returns(self) -> None:
        """Test with empty series."""
        returns = pl.Series([])
        pf = calculate_profit_factor(returns)

        assert pf == 0.0


class TestTotalReturn:
    """Test total return calculation."""

    def test_positive_returns(self) -> None:
        """Test with positive returns."""
        # (1.01 * 1.02 * 1.01) - 1 ≈ 0.0404
        returns = pl.Series([0.01, 0.02, 0.01])
        total = calculate_total_return(returns)

        assert abs(total - 0.0404) < 0.001

    def test_negative_returns(self) -> None:
        """Test with negative returns."""
        returns = pl.Series([-0.01, -0.02])
        total = calculate_total_return(returns)

        # Should be negative
        assert total < 0

    def test_mixed_returns(self) -> None:
        """Test with mix of positive and negative."""
        # (1.02 * 0.99) - 1 = 0.0098
        returns = pl.Series([0.02, -0.01])
        total = calculate_total_return(returns)

        assert abs(total - 0.0098) < 0.001

    def test_empty_returns(self) -> None:
        """Test with empty series."""
        returns = pl.Series([])
        total = calculate_total_return(returns)

        assert total == 0.0


class TestAnnualizedReturn:
    """Test annualized return calculation."""

    def test_one_year_of_data(self) -> None:
        """Test with exactly one year of daily returns."""
        # 252 days of 0.1% returns
        returns = pl.Series([0.001] * 252)
        ann_ret = calculate_annualized_return(returns, periods_per_year=252)

        # Compound 252 days: (1.001^252) - 1 ≈ 0.2879
        assert abs(ann_ret - 0.2879) < 0.01

    def test_less_than_one_year(self) -> None:
        """Test with less than one year of data."""
        # 100 days of 0.5% returns
        returns = pl.Series([0.005] * 100)
        ann_ret = calculate_annualized_return(returns, periods_per_year=252)

        # Should annualize to higher rate
        total = calculate_total_return(returns)
        assert ann_ret > total  # Annualized should be higher

    def test_negative_returns(self) -> None:
        """Test with negative returns."""
        returns = pl.Series([-0.001] * 100)
        ann_ret = calculate_annualized_return(returns)

        # Should be negative
        assert ann_ret < 0

    def test_empty_returns(self) -> None:
        """Test with empty series."""
        returns = pl.Series([])
        ann_ret = calculate_annualized_return(returns)

        assert ann_ret == 0.0


class TestEdgeCases:
    """Test edge cases across all metrics."""

    def test_single_return(self) -> None:
        """Test all metrics with single return."""
        returns = pl.Series([0.01])

        # Most metrics should handle single value
        sharpe = calculate_sharpe_ratio(returns)
        total = calculate_total_return(returns)
        win_rate = calculate_win_rate(returns)

        assert sharpe == 0.0  # No std dev with single value
        assert abs(total - 0.01) < 1e-10  # Floating point tolerance
        assert win_rate == 1.0  # 100% wins

    def test_large_values(self) -> None:
        """Test with large return values."""
        returns = pl.Series([0.5, -0.3, 0.4])  # 50%, -30%, 40% returns

        # Should handle without overflow
        total = calculate_total_return(returns)
        pf = calculate_profit_factor(returns)

        assert not math.isinf(total)
        assert not math.isnan(pf)

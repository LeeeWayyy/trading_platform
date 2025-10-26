"""
Tests for signal-based backtest evaluator.

This module tests the SignalEvaluator class that maps trading signals
to returns and computes performance metrics.
"""

from datetime import date

import polars as pl
import pytest

from strategies.backtest.evaluator import SignalEvaluator, quick_evaluate


class TestSignalEvaluatorInit:
    """Test SignalEvaluator initialization and validation."""

    def test_valid_initialization(self) -> None:
        """Test initialization with valid inputs."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "return": [0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)

        assert evaluator.signal_column == "signal"
        assert evaluator.return_column == "return"
        assert len(evaluator.results) == 0  # Not evaluated yet

    def test_custom_column_names(self) -> None:
        """Test initialization with custom column names."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "my_signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "my_return": [0.01],
            }
        )

        evaluator = SignalEvaluator(
            signals, returns, signal_column="my_signal", return_column="my_return"
        )

        assert evaluator.signal_column == "my_signal"
        assert evaluator.return_column == "my_return"

    def test_missing_signal_columns(self) -> None:
        """Test that missing signal columns raises ValueError."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                # Missing date and signal columns
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "return": [0.01],
            }
        )

        with pytest.raises(ValueError, match="missing required columns"):
            SignalEvaluator(signals, returns)

    def test_missing_return_columns(self) -> None:
        """Test that missing return columns raises ValueError."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                # Missing date and return columns
            }
        )

        with pytest.raises(ValueError, match="missing required columns"):
            SignalEvaluator(signals, returns)


class TestSignalEvaluatorEvaluate:
    """Test evaluation of strategy performance."""

    def test_simple_profitable_strategy(self) -> None:
        """Test with simple profitable signals."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "signal": [1, 1, 1],  # All long
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "return": [0.01, 0.02, 0.015],  # All positive
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Should be profitable
        assert results["total_return"] > 0
        assert results["win_rate"] == 1.0  # 100% wins
        assert results["sharpe_ratio"] > 0
        assert results["num_trades"] == 3

    def test_losing_strategy(self) -> None:
        """Test with losing signals."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [1, 1],  # Long
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [-0.02, -0.01],  # Negative returns
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Should be losing
        assert results["total_return"] < 0
        assert results["win_rate"] == 0.0
        assert results["sharpe_ratio"] < 0

    def test_short_signals(self) -> None:
        """Test with short signals (-1)."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [-1, -1],  # Short
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [-0.01, -0.02],  # Negative market returns
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Short with negative returns = profit
        assert results["total_return"] > 0
        assert results["win_rate"] == 1.0

    def test_neutral_signals(self) -> None:
        """Test with neutral signals (0)."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "signal": [0, 0, 0],  # All neutral
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "return": [0.01, 0.02, -0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Neutral signals = zero returns
        assert results["total_return"] == 0.0
        assert results["num_trades"] == 0

    def test_mixed_signals(self) -> None:
        """Test with mix of long, short, and neutral."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "signal": [1, -1, 0, 1, -1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 5,
                "date": [date(2024, 1, i) for i in range(1, 6)],
                "return": [0.01, 0.01, 0.01, -0.01, 0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # 4 trades (exclude neutral)
        assert results["num_trades"] == 4
        # Mix of wins and losses
        assert 0 < results["win_rate"] < 1

    def test_with_commission(self) -> None:
        """Test that commission reduces returns."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [1, 1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [0.01, 0.01],
            }
        )

        # Evaluate without commission
        evaluator_no_comm = SignalEvaluator(signals, returns)
        results_no_comm = evaluator_no_comm.evaluate(commission=0.0)

        # Evaluate with commission
        evaluator_with_comm = SignalEvaluator(signals, returns)
        results_with_comm = evaluator_with_comm.evaluate(commission=0.001)

        # Commission should reduce returns
        assert results_with_comm["total_return"] < results_no_comm["total_return"]

    def test_multi_symbol(self) -> None:
        """Test with multiple symbols."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "GOOGL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)] * 2,
                "signal": [1, 1, -1, -1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "GOOGL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)] * 2,
                "return": [0.01, 0.02, 0.01, 0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Should have 4 total trades
        assert results["num_trades"] == 4
        # AAPL long profits, GOOGL short losses → net depends on calculation
        assert "total_return" in results


class TestSignalEvaluatorMethods:
    """Test evaluator helper methods."""

    def test_get_strategy_returns(self) -> None:
        """Test getting strategy returns after evaluation."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [1, 1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [0.01, 0.02],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        evaluator.evaluate()

        strategy_returns = evaluator.get_strategy_returns()

        assert len(strategy_returns) == 2
        # Long with positive returns
        assert all(strategy_returns > 0)

    def test_get_strategy_returns_before_evaluate(self) -> None:
        """Test that accessing returns before evaluate raises error."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "return": [0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)

        with pytest.raises(RuntimeError, match="Must call evaluate"):
            evaluator.get_strategy_returns()

    def test_get_cumulative_returns(self) -> None:
        """Test getting cumulative returns after evaluation."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [1, 1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [0.01, 0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        evaluator.evaluate()

        cum_returns = evaluator.get_cumulative_returns()

        assert len(cum_returns) == 2
        # Should be monotonically increasing with positive returns
        assert cum_returns[1] > cum_returns[0]
        # Verify multiplicative compounding: (1 + 0.01) * (1 + 0.01) = 1.0201
        assert abs(cum_returns[1] - 1.0201) < 1e-6

    def test_cumulative_returns_uses_compounding(self) -> None:
        """Test that cumulative returns use multiplicative compounding, not additive."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "signal": [1, 1, 1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                "return": [0.10, 0.05, -0.03],  # 10%, 5%, -3%
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        evaluator.evaluate()

        cum_returns = evaluator.get_cumulative_returns()

        # Correct multiplicative: (1.10) * (1.05) * (0.97) = 1.1203
        expected_final = 1.10 * 1.05 * 0.97
        assert abs(cum_returns[2] - expected_final) < 1e-6

        # Verify it's NOT additive: (1.10) + (1.05) + (0.97) = 3.12
        wrong_additive = 1.10 + 1.05 + 0.97
        assert abs(cum_returns[2] - wrong_additive) > 0.1  # Should be very different

    def test_get_cumulative_returns_before_evaluate(self) -> None:
        """Test that accessing cumulative returns before evaluate raises error."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "return": [0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)

        with pytest.raises(RuntimeError, match="Must call evaluate"):
            evaluator.get_cumulative_returns()


class TestQuickEvaluate:
    """Test quick_evaluate convenience function."""

    def test_quick_evaluate_basic(self) -> None:
        """Test basic quick_evaluate functionality."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "signal": [1, 1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL", "AAPL"],
                "date": [date(2024, 1, 1), date(2024, 1, 2)],
                "return": [0.01, 0.02],
            }
        )

        results = quick_evaluate(signals, returns)

        # Should return metrics dict
        assert "total_return" in results
        assert "sharpe_ratio" in results
        assert "win_rate" in results
        assert results["total_return"] > 0

    def test_quick_evaluate_custom_columns(self) -> None:
        """Test quick_evaluate with custom column names."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "my_sig": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "my_ret": [0.01],
            }
        )

        results = quick_evaluate(signals, returns, signal_column="my_sig", return_column="my_ret")

        assert "total_return" in results
        assert results["num_trades"] == 1


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_no_matching_dates(self) -> None:
        """Test when signals and returns have no matching dates."""
        signals = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "signal": [1],
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 2)],  # Different date
                "return": [0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # No matches = zero returns
        assert results["total_return"] == 0.0
        assert results["num_trades"] == 0

    def test_empty_signals(self) -> None:
        """Test with empty signals DataFrame."""
        signals = pl.DataFrame(
            {
                "symbol": pl.Series([], dtype=pl.Utf8),
                "date": pl.Series([], dtype=pl.Date),
                "signal": pl.Series([], dtype=pl.Int8),
            }
        )
        returns = pl.DataFrame(
            {
                "symbol": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "return": [0.01],
            }
        )

        evaluator = SignalEvaluator(signals, returns)
        results = evaluator.evaluate()

        # Empty signals = zero everything
        assert results["total_return"] == 0.0
        assert results["num_trades"] == 0

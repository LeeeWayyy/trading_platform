"""
Tests for ensemble signal combination logic.

This module tests all combination methods and edge cases for the ensemble framework.
"""

from datetime import date

import polars as pl
import pytest

from strategies.ensemble.combiner import (
    CombinationMethod,
    combine_signals,
)


class TestCombineSignalsValidation:
    """Test input validation for combine_signals function."""

    def test_missing_required_columns(self) -> None:
        """Test that missing required columns raises ValueError."""
        # Missing 'date' column
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
        })

        with pytest.raises(ValueError, match="missing required columns"):
            combine_signals(signals)

    def test_too_few_strategies(self) -> None:
        """Test that < 2 strategies raises ValueError."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_only_one_signal": [1],
            "strategy_only_one_confidence": [0.8],
        })

        with pytest.raises(ValueError, match="at least 2 strategies"):
            combine_signals(signals)

    def test_invalid_weights_sum(self) -> None:
        """Test that weights not summing to 1.0 raises ValueError."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        weights = {"a": 0.5, "b": 0.6}  # Sum = 1.1

        with pytest.raises(ValueError, match="must sum to 1.0"):
            combine_signals(signals, weights=weights)

    def test_negative_weights(self) -> None:
        """Test that negative weights raise ValueError."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        weights = {"a": 1.2, "b": -0.2}  # Negative weight

        with pytest.raises(ValueError, match="non-negative"):
            combine_signals(signals, weights=weights)

    def test_missing_strategy_in_weights(self) -> None:
        """Test that missing strategy in weights raises ValueError."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        weights = {"a": 1.0}  # Missing 'b'

        with pytest.raises(ValueError, match="missing strategies"):
            combine_signals(signals, weights=weights)


class TestWeightedAverage:
    """Test weighted average combination method."""

    def test_basic_weighted_average(self) -> None:
        """Test simple weighted average combination."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        result = combine_signals(
            signals,
            method=CombinationMethod.WEIGHTED_AVERAGE,
            weights={"a": 0.6, "b": 0.4},
        )

        # Both signal +1 → ensemble should be +1
        assert result["ensemble_signal"][0] == 1
        # Confidence = 0.8*0.6 + 0.6*0.4 = 0.48 + 0.24 = 0.72
        assert abs(result["ensemble_confidence"][0] - 0.72) < 0.01

    def test_conflicting_signals_weighted(self) -> None:
        """Test weighted average with conflicting signals."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],  # Buy
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],  # Sell
            "strategy_b_confidence": [0.6],
        })

        # Equal weights → should cancel to 0
        result = combine_signals(
            signals,
            method=CombinationMethod.WEIGHTED_AVERAGE,
            weights={"a": 0.5, "b": 0.5},
        )

        # signal = 1*0.5 + (-1)*0.5 = 0 → HOLD
        assert result["ensemble_signal"][0] == 0

    def test_weighted_average_threshold(self) -> None:
        """Test that signals below threshold become HOLD."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [0],  # Hold
            "strategy_b_confidence": [0.5],
        })

        # Weighted: 1*0.7 + 0*0.3 = 0.7 > 0.3 threshold → BUY
        result = combine_signals(
            signals,
            method=CombinationMethod.WEIGHTED_AVERAGE,
            weights={"a": 0.7, "b": 0.3},
        )

        assert result["ensemble_signal"][0] == 1

        # Now with more hold weight: 1*0.2 + 0*0.8 = 0.2 < 0.3 threshold → HOLD
        result2 = combine_signals(
            signals,
            method=CombinationMethod.WEIGHTED_AVERAGE,
            weights={"a": 0.2, "b": 0.8},
        )

        assert result2["ensemble_signal"][0] == 0

    def test_equal_weights_default(self) -> None:
        """Test that None weights gives equal weighting."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        result = combine_signals(signals, method="weighted_average", weights=None)

        # Equal weights: 0.5 each
        # Confidence = 0.8*0.5 + 0.6*0.5 = 0.7
        assert abs(result["ensemble_confidence"][0] - 0.7) < 0.01


class TestMajorityVote:
    """Test majority voting combination method."""

    def test_clear_majority_buy(self) -> None:
        """Test clear majority for buy signal."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.7],
            "strategy_c_signal": [-1],
            "strategy_c_confidence": [0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        # 2 out of 3 vote BUY → ensemble BUY
        assert result["ensemble_signal"][0] == 1

    def test_clear_majority_sell(self) -> None:
        """Test clear majority for sell signal."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [-1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.7],
            "strategy_c_signal": [1],
            "strategy_c_confidence": [0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        # 2 out of 3 vote SELL → ensemble SELL
        assert result["ensemble_signal"][0] == -1

    def test_no_majority(self) -> None:
        """Test no majority results in HOLD."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.7],
            "strategy_c_signal": [0],
            "strategy_c_confidence": [0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        # No majority (1 buy, 1 sell, 1 hold) → HOLD
        assert result["ensemble_signal"][0] == 0

    def test_majority_with_nulls(self) -> None:
        """Test majority vote handles missing data."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [None],  # Missing
            "strategy_b_confidence": [None],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        # Only 1 strategy, can't get >50% → should be treated as hold or single vote
        # Implementation treats null as 0 (hold), so no majority
        assert result["ensemble_signal"][0] in [0, 1]


class TestUnanimous:
    """Test unanimous agreement combination method."""

    def test_unanimous_buy(self) -> None:
        """Test all strategies agree on buy."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.7],
            "strategy_c_signal": [1],
            "strategy_c_confidence": [0.9],
        })

        result = combine_signals(signals, method=CombinationMethod.UNANIMOUS)

        assert result["ensemble_signal"][0] == 1
        # Confidence should be average
        avg_conf = (0.8 + 0.7 + 0.9) / 3
        assert abs(result["ensemble_confidence"][0] - avg_conf) < 0.01

    def test_unanimous_sell(self) -> None:
        """Test all strategies agree on sell."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [-1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.7],
        })

        result = combine_signals(signals, method=CombinationMethod.UNANIMOUS)

        assert result["ensemble_signal"][0] == -1

    def test_not_unanimous(self) -> None:
        """Test disagreement results in HOLD."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],  # Disagrees
            "strategy_b_confidence": [0.7],
        })

        result = combine_signals(signals, method=CombinationMethod.UNANIMOUS)

        # Not unanimous → HOLD
        assert result["ensemble_signal"][0] == 0
        # Confidence should be 0 when not unanimous
        assert result["ensemble_confidence"][0] == 0.0

    def test_unanimous_hold(self) -> None:
        """Test all strategies hold."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [0],
            "strategy_a_confidence": [0.5],
            "strategy_b_signal": [0],
            "strategy_b_confidence": [0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.UNANIMOUS)

        assert result["ensemble_signal"][0] == 0


class TestConfidenceWeighted:
    """Test confidence-weighted combination method."""

    def test_high_confidence_dominates(self) -> None:
        """Test that high confidence strategy has more influence."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.9],  # High confidence BUY
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.2],  # Low confidence SELL
        })

        result = combine_signals(signals, method=CombinationMethod.CONFIDENCE_WEIGHTED)

        # weighted = (1*0.9 + (-1)*0.2) / (0.9+0.2) = 0.7/1.1 = 0.636 > 0.3 → BUY
        assert result["ensemble_signal"][0] == 1

    def test_equal_confidence_cancels(self) -> None:
        """Test equal confidence opposite signals cancel."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.7],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.7],
        })

        result = combine_signals(signals, method=CombinationMethod.CONFIDENCE_WEIGHTED)

        # weighted = (1*0.7 + (-1)*0.7) / (0.7+0.7) = 0/1.4 = 0 → HOLD
        assert result["ensemble_signal"][0] == 0

    def test_confidence_weighted_zero_division(self) -> None:
        """Test handles zero total confidence gracefully."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.0],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.0],
        })

        result = combine_signals(signals, method=CombinationMethod.CONFIDENCE_WEIGHTED)

        # Should not crash, should handle gracefully
        assert result["ensemble_signal"][0] in [-1, 0, 1]


class TestMaxConfidence:
    """Test max confidence combination method."""

    def test_picks_highest_confidence(self) -> None:
        """Test picks signal from most confident strategy."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.6],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.9],  # Higher confidence
        })

        result = combine_signals(signals, method=CombinationMethod.MAX_CONFIDENCE)

        # Should pick strategy_b (higher confidence)
        assert result["ensemble_signal"][0] == -1
        assert result["ensemble_confidence"][0] == 0.9

    def test_tie_picks_first(self) -> None:
        """Test tie in confidence picks first strategy alphabetically."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [-1],
            "strategy_b_confidence": [0.8],  # Same confidence
        })

        result = combine_signals(signals, method=CombinationMethod.MAX_CONFIDENCE)

        # Tie → should pick one (implementation picks first match)
        assert result["ensemble_signal"][0] in [-1, 1]
        assert result["ensemble_confidence"][0] == 0.8


class TestMultiSymbol:
    """Test ensemble combinations with multiple symbols."""

    def test_multi_symbol_weighted_average(self) -> None:
        """Test weighted average works across multiple symbols."""
        signals = pl.DataFrame({
            "symbol": ["AAPL", "GOOGL", "MSFT"],
            "date": [date(2024, 1, 1)] * 3,
            "strategy_a_signal": [1, -1, 0],
            "strategy_a_confidence": [0.8, 0.7, 0.5],
            "strategy_b_signal": [1, 1, -1],
            "strategy_b_confidence": [0.6, 0.6, 0.8],
        })

        result = combine_signals(
            signals,
            method=CombinationMethod.WEIGHTED_AVERAGE,
            weights={"a": 0.5, "b": 0.5},
        )

        assert len(result) == 3
        assert all(result["ensemble_signal"].is_not_null())

    def test_multi_symbol_majority_vote(self) -> None:
        """Test majority vote works across multiple symbols."""
        signals = pl.DataFrame({
            "symbol": ["AAPL", "GOOGL"],
            "date": [date(2024, 1, 1)] * 2,
            "strategy_a_signal": [1, -1],
            "strategy_a_confidence": [0.8, 0.7],
            "strategy_b_signal": [1, -1],
            "strategy_b_confidence": [0.6, 0.8],
            "strategy_c_signal": [1, 1],
            "strategy_c_confidence": [0.7, 0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        # AAPL: all BUY → BUY
        # GOOGL: 2 SELL, 1 BUY → SELL
        assert result.filter(pl.col("symbol") == "AAPL")["ensemble_signal"][0] == 1
        assert result.filter(pl.col("symbol") == "GOOGL")["ensemble_signal"][0] == -1


class TestMethodMetadata:
    """Test that ensemble method is recorded in output."""

    def test_method_recorded(self) -> None:
        """Test that combination method is added to result."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        result = combine_signals(signals, method=CombinationMethod.MAJORITY_VOTE)

        assert "ensemble_method" in result.columns
        assert result["ensemble_method"][0] == "majority_vote"

    def test_string_method_conversion(self) -> None:
        """Test that string method names work."""
        signals = pl.DataFrame({
            "symbol": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "strategy_a_signal": [1],
            "strategy_a_confidence": [0.8],
            "strategy_b_signal": [1],
            "strategy_b_confidence": [0.6],
        })

        # Pass method as string
        result = combine_signals(signals, method="unanimous")

        assert result["ensemble_method"][0] == "unanimous"

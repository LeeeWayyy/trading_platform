"""
Unit tests for Multi-Alpha Allocator.

Tests cover:
- Initialization validation
- Rank aggregation correctness
- Equal weight allocation
- Edge cases (empty signals, single strategy, no overlap)
- Weight normalization (sum to 1.0)
"""

import polars as pl
import pytest

from libs.allocation.multi_alpha import AllocMethod, MultiAlphaAllocator


class TestMultiAlphaAllocatorInit:
    """Test allocator initialization and validation."""

    def test_init_default_parameters(self):
        """Test initialization with default parameters."""
        allocator = MultiAlphaAllocator()
        assert allocator.method == "rank_aggregation"
        assert allocator.per_strategy_max == 0.40
        assert allocator.correlation_threshold == 0.70

    def test_init_custom_parameters(self):
        """Test initialization with custom parameters."""
        allocator = MultiAlphaAllocator(
            method="equal_weight",
            per_strategy_max=0.50,
            correlation_threshold=0.80,
        )
        assert allocator.method == "equal_weight"
        assert allocator.per_strategy_max == 0.50
        assert allocator.correlation_threshold == 0.80

    def test_init_invalid_per_strategy_max_negative(self):
        """Test initialization fails with negative per_strategy_max."""
        with pytest.raises(ValueError, match="per_strategy_max must be in"):
            MultiAlphaAllocator(per_strategy_max=-0.1)

    def test_init_invalid_per_strategy_max_too_large(self):
        """Test initialization fails with per_strategy_max > 1.0."""
        with pytest.raises(ValueError, match="per_strategy_max must be in"):
            MultiAlphaAllocator(per_strategy_max=1.5)

    def test_init_invalid_correlation_threshold_negative(self):
        """Test initialization fails with negative correlation_threshold."""
        with pytest.raises(ValueError, match="correlation_threshold must be in"):
            MultiAlphaAllocator(correlation_threshold=-0.1)

    def test_init_invalid_correlation_threshold_too_large(self):
        """Test initialization fails with correlation_threshold > 1.0."""
        with pytest.raises(ValueError, match="correlation_threshold must be in"):
            MultiAlphaAllocator(correlation_threshold=1.5)


class TestMultiAlphaAllocatorRankAggregation:
    """Test rank aggregation allocation method."""

    def test_rank_aggregation_two_strategies_no_overlap(self):
        """Test rank aggregation with two strategies that have no overlapping symbols."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["GOOGL", "TSLA"],
                    "score": [0.9, 0.7],
                    "weight": [0.5, 0.5],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have all 4 symbols
        assert len(result) == 4
        assert set(result["symbol"].to_list()) == {"AAPL", "MSFT", "GOOGL", "TSLA"}

        # Weights should sum to 1.0 (within tolerance)
        total_weight = result["final_weight"].sum()
        assert abs(total_weight - 1.0) < 1e-9

        # All weights should be positive
        assert all(result["final_weight"] > 0)

    def test_rank_aggregation_two_strategies_with_overlap(self):
        """Test rank aggregation with overlapping symbols across strategies."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.9, 0.7, 0.5],
                    "weight": [0.5, 0.3, 0.2],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "TSLA", "NVDA"],
                    "score": [0.8, 0.9, 0.7],
                    "weight": [0.4, 0.4, 0.2],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 5 unique symbols
        assert len(result) == 5
        assert set(result["symbol"].to_list()) == {"AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"}

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL should have higher weight (appears in both strategies with high scores)
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()
        assert aapl_weight > msft_weight  # AAPL is in both strategies

        # AAPL should have both strategies in contributing_strategies
        aapl_strategies = result.filter(pl.col("symbol") == "AAPL")[
            "contributing_strategies"
        ].item()
        assert set(aapl_strategies) == {"alpha_baseline", "momentum"}

    def test_rank_aggregation_three_strategies(self):
        """Test rank aggregation with three strategies."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.9, 0.7],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "GOOGL"],
                    "score": [0.8, 0.9],
                    "weight": [0.5, 0.5],
                }
            ),
            "mean_reversion": pl.DataFrame(
                {
                    "symbol": ["AAPL", "TSLA"],
                    "score": [0.7, 0.8],
                    "weight": [0.4, 0.6],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 4 unique symbols
        assert len(result) == 4

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL should have all 3 strategies contributing (most diversified)
        aapl_strategies = result.filter(pl.col("symbol") == "AAPL")[
            "contributing_strategies"
        ].item()
        assert len(aapl_strategies) == 3

        # AAPL appears in all strategies, but with reciprocal rank:
        # - alpha_baseline: rank=1 → 1.0
        # - momentum: rank=2 (GOOGL has higher score) → 0.5
        # - mean_reversion: rank=2 (TSLA has higher score) → 0.5
        # Average: (1.0 + 0.5 + 0.5) / 3 = 0.666...
        #
        # GOOGL and TSLA are rank=1 in their respective strategies (1.0 each)
        # So they get higher final weights than AAPL
        #
        # This is CORRECT: rank aggregation rewards TOP ranks, not just presence
        googl_weight = result.filter(pl.col("symbol") == "GOOGL")["final_weight"].item()
        tsla_weight = result.filter(pl.col("symbol") == "TSLA")["final_weight"].item()
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()

        # GOOGL and TSLA should have highest weights (both rank #1 in their strategies)
        assert googl_weight == max(result["final_weight"])
        assert tsla_weight == max(result["final_weight"])
        # AAPL should have lower weight than GOOGL/TSLA but higher than MSFT
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()
        assert aapl_weight > msft_weight  # AAPL in 3 strategies vs MSFT in 1
        assert aapl_weight < googl_weight  # But AAPL's avg rank (0.666) < GOOGL's (1.0)

    def test_rank_aggregation_single_symbol_per_strategy(self):
        """Test rank aggregation when each strategy has only one symbol."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.9],
                    "weight": [1.0],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["MSFT"],
                    "score": [0.8],
                    "weight": [1.0],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 2 symbols
        assert len(result) == 2

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # Both symbols should have rank=1.0 within their strategy, so equal weights
        weights = result["final_weight"].to_list()
        assert abs(weights[0] - weights[1]) < 1e-9  # Equal weights

    def test_rank_aggregation_tie_breaking(self):
        """Test rank aggregation with tied scores."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.9, 0.9, 0.7],  # AAPL and MSFT tied
                    "weight": [0.4, 0.4, 0.2],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 3 symbols
        assert len(result) == 3

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL and MSFT should have higher weights than GOOGL
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()
        googl_weight = result.filter(pl.col("symbol") == "GOOGL")["final_weight"].item()
        assert aapl_weight > googl_weight
        assert msft_weight > googl_weight


class TestMultiAlphaAllocatorEqualWeight:
    """Test equal weight allocation method."""

    def test_equal_weight_two_strategies_no_overlap(self):
        """Test equal weight with two strategies, no overlap."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["GOOGL", "TSLA"],
                    "score": [0.9, 0.7],
                    "weight": [0.7, 0.3],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="equal_weight")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have all 4 symbols
        assert len(result) == 4

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # All weights should be positive
        assert all(result["final_weight"] > 0)

    def test_equal_weight_two_strategies_with_overlap(self):
        """Test equal weight with overlapping symbols."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "GOOGL"],
                    "score": [0.9, 0.7],
                    "weight": [0.7, 0.3],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="equal_weight")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 3 unique symbols
        assert len(result) == 3

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL weight should be average of 0.6 and 0.7 = 0.65, then normalized
        aapl_row = result.filter(pl.col("symbol") == "AAPL")
        assert len(aapl_row) == 1

        # AAPL should have both strategies contributing
        aapl_strategies = aapl_row["contributing_strategies"].item()
        assert len(aapl_strategies) == 2

    def test_equal_weight_all_same_symbol(self):
        """Test equal weight when all strategies recommend the same symbol."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.9],
                    "weight": [1.0],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.8],
                    "weight": [1.0],
                }
            ),
            "mean_reversion": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.7],
                    "weight": [1.0],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="equal_weight")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have only 1 symbol
        assert len(result) == 1
        assert result["symbol"].item() == "AAPL"

        # Weight should be 1.0
        assert abs(result["final_weight"].item() - 1.0) < 1e-9

        # All 3 strategies should be contributing
        strategies = result["contributing_strategies"].item()
        assert len(strategies) == 3


class TestMultiAlphaAllocatorEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_signals_dict(self):
        """Test that empty signals dict raises ValueError."""
        allocator = MultiAlphaAllocator()
        with pytest.raises(ValueError, match="At least one strategy required"):
            allocator.allocate({}, strategy_stats={})

    def test_single_strategy_bypasses_allocator(self):
        """Test that single strategy bypasses allocator logic."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.9, 0.7, 0.5],
                    "weight": [0.5, 0.3, 0.2],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should have all 3 symbols
        assert len(result) == 3

        # Weights should sum to 1.0 (normalized from original weights)
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # All symbols should have only one strategy contributing
        for row in result.iter_rows(named=True):
            assert len(row["contributing_strategies"]) == 1
            assert row["contributing_strategies"][0] == "alpha_baseline"

    def test_empty_dataframe_in_signals(self):
        """Test handling of empty DataFrame in signals."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "score": pl.Float64,
                    "weight": pl.Float64,
                }
            ),  # Empty DataFrame
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should only have symbols from alpha_baseline
        assert len(result) == 2
        assert set(result["symbol"].to_list()) == {"AAPL", "MSFT"}

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

    def test_all_strategies_have_empty_signals(self):
        """Test when all strategies have empty signals."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "score": pl.Float64,
                    "weight": pl.Float64,
                }
            ),
            "momentum": pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "score": pl.Float64,
                    "weight": pl.Float64,
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        result = allocator.allocate(signals, strategy_stats={})

        # Should return empty DataFrame with correct schema
        assert result.is_empty()
        assert "symbol" in result.columns
        assert "final_weight" in result.columns
        assert "contributing_strategies" in result.columns

    def test_strategy_stats_optional_for_rank_aggregation(self):
        """Test that strategy_stats is optional for rank_aggregation."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.9],
                    "weight": [1.0],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        # Should work with None
        result = allocator.allocate(signals, strategy_stats=None)
        assert len(result) == 1

        # Should work with empty dict
        result = allocator.allocate(signals, strategy_stats={})
        assert len(result) == 1

    def test_strategy_stats_optional_for_equal_weight(self):
        """Test that strategy_stats is optional for equal_weight."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.9],
                    "weight": [1.0],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="equal_weight")
        # Should work with None
        result = allocator.allocate(signals, strategy_stats=None)
        assert len(result) == 1

        # Should work with empty dict
        result = allocator.allocate(signals, strategy_stats={})
        assert len(result) == 1


class TestWeightNormalization:
    """Test that weights always sum to 1.0 (within tolerance)."""

    @pytest.mark.parametrize("method", ["rank_aggregation", "equal_weight"])
    def test_weight_normalization_two_strategies(self, method: AllocMethod):
        """Test weight normalization with two strategies."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.9, 0.7, 0.5],
                    "weight": [0.5, 0.3, 0.2],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "TSLA", "NVDA"],
                    "score": [0.8, 0.9, 0.7],
                    "weight": [0.4, 0.4, 0.2],
                }
            ),
        }

        allocator = MultiAlphaAllocator(method=method)
        result = allocator.allocate(signals, strategy_stats={})

        # Weights should sum to 1.0 (within floating point tolerance)
        total_weight = result["final_weight"].sum()
        assert abs(total_weight - 1.0) < 1e-9

    @pytest.mark.parametrize("method", ["rank_aggregation", "equal_weight"])
    def test_weight_normalization_single_strategy(self, method: AllocMethod):
        """Test weight normalization with single strategy."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [100.0, 50.0],  # Arbitrary weights
                }
            ),
        }

        allocator = MultiAlphaAllocator(method=method)
        result = allocator.allocate(signals, strategy_stats={})

        # Weights should sum to 1.0
        total_weight = result["final_weight"].sum()
        assert abs(total_weight - 1.0) < 1e-9


class TestInverseVolPlaceholder:
    """Test inverse volatility placeholder (Component 2)."""

    def test_inverse_vol_falls_back_to_equal_weight(self):
        """Test that inverse_vol method currently falls back to equal_weight."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["GOOGL"],
                    "score": [0.9],
                    "weight": [1.0],
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.15, "sharpe": 1.2},
            "momentum": {"vol": 0.25, "sharpe": 0.8},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should return result (fallback to equal weight)
        assert len(result) > 0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9


class TestCorrelationMonitoringPlaceholder:
    """Test correlation monitoring placeholder (Component 3)."""

    def test_check_correlation_placeholder(self):
        """Test that check_correlation is a placeholder for Component 3."""
        allocator = MultiAlphaAllocator()
        result = allocator.check_correlation({})

        # Should return empty dict (not implemented yet)
        assert result == {}

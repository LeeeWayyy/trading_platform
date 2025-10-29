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


class TestInverseVolatilityWeighting:
    """Test inverse volatility weighting allocation method (Component 2)."""

    def test_inverse_vol_basic_two_strategies(self):
        """Test basic inverse volatility weighting with two strategies."""
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

        # alpha_baseline has lower vol (0.15) → higher weight
        # momentum has higher vol (0.30) → lower weight
        strategy_stats = {
            "alpha_baseline": {"vol": 0.15, "sharpe": 1.2},
            "momentum": {"vol": 0.30, "sharpe": 0.8},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should have all 4 symbols
        assert len(result) == 4
        assert set(result["symbol"].to_list()) == {"AAPL", "MSFT", "GOOGL", "TSLA"}

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # All weights should be positive
        assert all(result["final_weight"] > 0)

        # Verify strategy weights are correct
        # inv_vol_baseline = 1/0.15 = 6.667
        # inv_vol_momentum = 1/0.30 = 3.333
        # total_inv_vol = 10.0
        # weight_baseline = 6.667/10.0 = 0.6667
        # weight_momentum = 3.333/10.0 = 0.3333
        # AAPL gets 0.6 * 0.6667 = 0.4
        # MSFT gets 0.4 * 0.6667 = 0.2667
        # GOOGL gets 0.7 * 0.3333 = 0.2333
        # TSLA gets 0.3 * 0.3333 = 0.1
        # After normalization to sum=1.0: divide by (0.4 + 0.2667 + 0.2333 + 0.1) = 1.0

        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()

        # AAPL should have highest weight (from lower-vol strategy)
        assert aapl_weight == max(result["final_weight"])

    def test_inverse_vol_three_strategies_different_volatilities(self):
        """Test inverse vol with three strategies having different volatilities."""
        signals = {
            "low_vol": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
            "medium_vol": pl.DataFrame({"symbol": ["MSFT"], "score": [0.8], "weight": [1.0]}),
            "high_vol": pl.DataFrame({"symbol": ["GOOGL"], "score": [0.7], "weight": [1.0]}),
        }

        strategy_stats = {
            "low_vol": {"vol": 0.10},  # Lowest vol → highest weight
            "medium_vol": {"vol": 0.20},
            "high_vol": {"vol": 0.40},  # Highest vol → lowest weight
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should have 3 symbols
        assert len(result) == 3

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # Verify ordering: low_vol strategy should contribute most
        # inv_vol weights: 1/0.10=10, 1/0.20=5, 1/0.40=2.5, total=17.5
        # strat_weight_low = 10/17.5 = 0.571
        # strat_weight_med = 5/17.5 = 0.286
        # strat_weight_high = 2.5/17.5 = 0.143
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()
        googl_weight = result.filter(pl.col("symbol") == "GOOGL")["final_weight"].item()

        assert aapl_weight > msft_weight > googl_weight

    def test_inverse_vol_with_overlapping_symbols(self):
        """Test inverse vol when strategies recommend overlapping symbols."""
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
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.15},
            "momentum": {"vol": 0.30},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should have 3 unique symbols
        assert len(result) == 3
        assert set(result["symbol"].to_list()) == {"AAPL", "MSFT", "GOOGL"}

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL should be in contributing_strategies for both
        aapl_strategies = result.filter(pl.col("symbol") == "AAPL")[
            "contributing_strategies"
        ].item()
        assert len(aapl_strategies) == 2

    def test_inverse_vol_missing_strategy_stats(self):
        """Test that inverse_vol raises error when strategy_stats is None."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="strategy_stats required for inverse_vol method but got None",
        ):
            allocator.allocate(signals, strategy_stats=None)

    def test_inverse_vol_empty_strategy_stats(self):
        """Test that inverse_vol raises error when strategy_stats is empty dict."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="strategy_stats required for inverse_vol method but got empty dict",
        ):
            allocator.allocate(signals, strategy_stats={})

    def test_inverse_vol_missing_strategy_entry(self):
        """Test that inverse_vol raises error when strategy_stats missing a strategy."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
            "momentum": pl.DataFrame({"symbol": ["MSFT"], "score": [0.8], "weight": [1.0]}),
        }

        # Missing "momentum" in strategy_stats
        strategy_stats = {
            "alpha_baseline": {"vol": 0.15},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="strategy_stats missing entry for 'momentum'",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_missing_vol_key(self):
        """Test that inverse_vol raises error when 'vol' key is missing."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        # Missing "vol" key
        strategy_stats = {
            "alpha_baseline": {"sharpe": 1.2},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="strategy_stats\\['alpha_baseline'\\] missing 'vol' key",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_invalid_vol_negative(self):
        """Test that inverse_vol raises error for negative volatility."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": -0.15},  # Negative vol
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="Invalid volatility for 'alpha_baseline': -0.15",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_invalid_vol_zero(self):
        """Test that inverse_vol raises error for zero volatility."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.0},  # Zero vol
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="Invalid volatility for 'alpha_baseline': 0.0",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_invalid_vol_nan(self):
        """Test that inverse_vol raises error for NaN volatility."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": float("nan")},  # NaN vol
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="Invalid volatility for 'alpha_baseline'",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_invalid_vol_inf(self):
        """Test that inverse_vol raises error for infinite volatility."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": float("inf")},  # Inf vol
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="Invalid volatility for 'alpha_baseline'",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_invalid_vol_string(self):
        """Test that inverse_vol raises error for non-numeric volatility."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": "0.15"},  # String instead of number
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")

        with pytest.raises(
            ValueError,
            match="Invalid volatility for 'alpha_baseline': 0.15",
        ):
            allocator.allocate(signals, strategy_stats=strategy_stats)

    def test_inverse_vol_single_strategy(self):
        """Test inverse vol with single strategy (should still work)."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.9, 0.7],
                    "weight": [0.6, 0.4],
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.15},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should have 2 symbols
        assert len(result) == 2

        # Weights should sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # Single strategy gets 100% weight, so final weights match original weights
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()
        assert abs(aapl_weight - 0.6) < 1e-9
        assert abs(msft_weight - 0.4) < 1e-9

    def test_inverse_vol_empty_signals_in_one_strategy(self):
        """Test inverse vol when one strategy has empty signals."""
        signals = {
            "alpha_baseline": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
            "momentum": pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "score": pl.Float64,
                    "weight": pl.Float64,
                }
            ),  # Empty DataFrame
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.15},
            "momentum": {"vol": 0.30},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should only have AAPL from alpha_baseline
        assert len(result) == 1
        assert result["symbol"].item() == "AAPL"

        # Weight should be 1.0
        assert abs(result["final_weight"].item() - 1.0) < 1e-9

    def test_inverse_vol_weight_calculation_correctness(self):
        """Test that inverse vol weight calculation is mathematically correct."""
        signals = {
            "strat_a": pl.DataFrame({"symbol": ["AAPL"], "score": [0.9], "weight": [1.0]}),
            "strat_b": pl.DataFrame({"symbol": ["MSFT"], "score": [0.8], "weight": [1.0]}),
        }

        # vol_a = 0.20, vol_b = 0.40
        # inv_vol_a = 1/0.20 = 5.0
        # inv_vol_b = 1/0.40 = 2.5
        # total_inv_vol = 7.5
        # weight_a = 5.0/7.5 = 0.6667
        # weight_b = 2.5/7.5 = 0.3333
        strategy_stats = {
            "strat_a": {"vol": 0.20},
            "strat_b": {"vol": 0.40},
        }

        allocator = MultiAlphaAllocator(method="inverse_vol")
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()

        # Expected: AAPL gets 0.6667, MSFT gets 0.3333
        assert abs(aapl_weight - 0.6667) < 1e-4
        assert abs(msft_weight - 0.3333) < 1e-4
        assert abs(aapl_weight + msft_weight - 1.0) < 1e-9


class TestCorrelationMonitoringPlaceholder:
    """Test correlation monitoring placeholder (Component 3)."""

    def test_check_correlation_placeholder(self):
        """Test that check_correlation is a placeholder for Component 3."""
        allocator = MultiAlphaAllocator()
        result = allocator.check_correlation({})

        # Should return empty dict (not implemented yet)
        assert result == {}

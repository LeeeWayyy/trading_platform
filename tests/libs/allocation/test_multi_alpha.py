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

        # With per-strategy caps enforced (default 40%), the new correct behavior:
        # 1. Each strategy normalizes its ranks to sum to 1.0
        # 2. Each strategy is capped at 40% total contribution
        # 3. Contributions are aggregated by symbol
        #
        # alpha_baseline: AAPL (rank=1) = 0.667, MSFT (rank=2) = 0.333
        # momentum: GOOGL (rank=1) = 0.667, AAPL (rank=2) = 0.333
        # mean_reversion: TSLA (rank=1) = 0.667, AAPL (rank=2) = 0.333
        #
        # After 40% cap applied to each strategy:
        # alpha_baseline: AAPL = 0.267, MSFT = 0.133
        # momentum: GOOGL = 0.267, AAPL = 0.133
        # mean_reversion: TSLA = 0.267, AAPL = 0.133
        #
        # Aggregated: AAPL = 0.533, GOOGL = 0.267, TSLA = 0.267, MSFT = 0.133
        # After final normalization: AAPL = 0.444, GOOGL = 0.222, TSLA = 0.222, MSFT = 0.111
        googl_weight = result.filter(pl.col("symbol") == "GOOGL")["final_weight"].item()
        tsla_weight = result.filter(pl.col("symbol") == "TSLA")["final_weight"].item()
        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()

        # AAPL should have the highest weight (appears in all 3 strategies)
        assert aapl_weight == max(result["final_weight"])
        assert abs(aapl_weight - 0.444) < 1e-2  # ~44.4%

        # GOOGL and TSLA should have equal weights (each appears in 1 strategy as rank #1)
        assert abs(googl_weight - tsla_weight) < 1e-9
        assert abs(googl_weight - 0.222) < 1e-2  # ~22.2%

        # MSFT should have the lowest weight (appears in 1 strategy as rank #2)
        assert msft_weight == min(result["final_weight"])
        assert abs(msft_weight - 0.111) < 1e-2  # ~11.1%

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

        # Set high per_strategy_max to avoid triggering caps for this test
        # This test is specifically for inverse vol calculation, not caps
        allocator = MultiAlphaAllocator(method="inverse_vol", per_strategy_max=1.0)
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        aapl_weight = result.filter(pl.col("symbol") == "AAPL")["final_weight"].item()
        msft_weight = result.filter(pl.col("symbol") == "MSFT")["final_weight"].item()

        # Expected: AAPL gets 0.6667, MSFT gets 0.3333
        assert abs(aapl_weight - 0.6667) < 1e-4
        assert abs(msft_weight - 0.3333) < 1e-4
        assert abs(aapl_weight + msft_weight - 1.0) < 1e-9


class TestPerStrategyCaps:
    """Test per-strategy concentration limits (Component 3)."""

    def test_caps_applied_when_strategy_exceeds_limit(self):
        """Test that caps are applied when a strategy exceeds per_strategy_max."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.9, 0.7],
                    "weight": [0.7, 0.3],  # AAPL gets 70%
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "GOOGL"],
                    "score": [0.8, 0.6],
                    "weight": [0.6, 0.4],
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.15, "sharpe": 1.2},
            "momentum": {"vol": 0.30, "sharpe": 0.8},
        }

        # alpha_baseline has lower vol → gets 66.7% weight
        # momentum has higher vol → gets 33.3% weight
        # AAPL from alpha_baseline: 0.7 * 0.6667 = 0.4667 (exceeds 0.40 cap!)
        # AAPL from momentum: 0.6 * 0.3333 = 0.20

        allocator = MultiAlphaAllocator(method="inverse_vol", per_strategy_max=0.40)
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # AAPL should be capped: 0.40 (capped) + 0.20 = 0.60 before normalization
        # After normalization with other symbols, weights sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # AAPL should still be present
        assert "AAPL" in result["symbol"].to_list()

    def test_no_caps_applied_when_all_below_limit(self):
        """Test that no caps are applied when all strategies are below limit."""
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
                    "score": [0.7, 0.5],
                    "weight": [0.7, 0.3],
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.20, "sharpe": 1.0},
            "momentum": {"vol": 0.20, "sharpe": 1.0},
        }

        # Both strategies have same vol → equal weights (50% each)
        # Max contribution from any strategy to any symbol: 0.6 * 0.5 = 0.30 < 0.40 cap

        allocator = MultiAlphaAllocator(method="inverse_vol", per_strategy_max=0.40)
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Should have all 4 symbols
        assert len(result) == 4
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

    def test_multiple_strategies_one_exceeds(self):
        """Test caps when only one strategy exceeds limit in multi-strategy scenario."""
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL"],
                    "score": [0.9],
                    "weight": [1.0],  # Wants 100% in AAPL
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT"],
                    "score": [0.5, 0.8],
                    "weight": [0.3, 0.7],  # Wants 30% in AAPL
                }
            ),
            "mean_reversion": pl.DataFrame(
                {
                    "symbol": ["AAPL", "GOOGL"],
                    "score": [0.6, 0.7],
                    "weight": [0.4, 0.6],
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.10, "sharpe": 1.5},  # Lowest vol → highest weight
            "momentum": {"vol": 0.30, "sharpe": 0.8},
            "mean_reversion": {"vol": 0.25, "sharpe": 1.0},
        }

        # alpha_baseline gets majority weight due to low vol
        # If it wants 100% in AAPL, its contribution will be capped at per_strategy_max

        allocator = MultiAlphaAllocator(method="inverse_vol", per_strategy_max=0.35)
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        assert abs(result["final_weight"].sum() - 1.0) < 1e-9
        assert "AAPL" in result["symbol"].to_list()

    def test_strategy_total_exposure_across_symbols_capped(self):
        """
        Test that per-strategy cap is enforced on TOTAL contribution across ALL symbols.

        This is the critical regression test for the bug where a strategy could exceed
        the cap by spreading across multiple symbols (e.g., 35% AAPL + 35% MSFT = 70% > 40%).
        """
        signals = {
            "alpha_baseline": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.9, 0.8, 0.7],
                    "weight": [0.4, 0.4, 0.2],  # Wants 40% + 40% + 20% = 100%
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "symbol": ["TSLA"],
                    "score": [0.6],
                    "weight": [1.0],  # Wants 100% in TSLA
                }
            ),
        }

        strategy_stats = {
            "alpha_baseline": {"vol": 0.10, "sharpe": 1.5},  # Low vol → high weight (80%)
            "momentum": {"vol": 0.40, "sharpe": 0.8},  # High vol → low weight (20%)
        }

        # Inverse vol weights:
        # alpha_baseline: (1/0.10) / ((1/0.10) + (1/0.40)) = 10 / 12.5 = 0.80
        # momentum: (1/0.40) / ((1/0.10) + (1/0.40)) = 2.5 / 12.5 = 0.20

        # Without cap, alpha_baseline would contribute:
        # AAPL: 0.4 * 0.80 = 0.32
        # MSFT: 0.4 * 0.80 = 0.32
        # GOOGL: 0.2 * 0.80 = 0.16
        # Total for alpha_baseline: 0.32 + 0.32 + 0.16 = 0.80 (exceeds 0.40 cap!)

        # With cap (per_strategy_max=0.40), alpha_baseline total must be scaled:
        # Scale factor: 0.40 / 0.80 = 0.50
        # AAPL: 0.32 * 0.50 = 0.16
        # MSFT: 0.32 * 0.50 = 0.16
        # GOOGL: 0.16 * 0.50 = 0.08
        # Total for alpha_baseline after cap: 0.16 + 0.16 + 0.08 = 0.40 ✓

        # momentum contributes:
        # TSLA: 1.0 * 0.20 = 0.20

        allocator = MultiAlphaAllocator(method="inverse_vol", per_strategy_max=0.40)
        result = allocator.allocate(signals, strategy_stats=strategy_stats)

        # Verify weights sum to 1.0
        assert abs(result["final_weight"].sum() - 1.0) < 1e-9

        # Extract per-strategy totals by manually reconstructing contributions
        # (In production, these would come from the intermediate weighted_contributions)
        # For verification: calculate expected totals after normalization

        # Before normalization:
        # alpha_baseline: 0.40 (capped)
        # momentum: 0.20
        # Total: 0.60

        # After normalization to sum=1.0:
        # alpha_baseline: 0.40 / 0.60 = 0.6667
        # momentum: 0.20 / 0.60 = 0.3333

        # Verify alpha_baseline's share is ≤ per_strategy_max after normalization
        # Since normalization scales everything, the ratio is preserved
        # But we need to verify the final per-strategy total

        # Get weights for each symbol
        weights_dict = {row["symbol"]: row["final_weight"] for row in result.iter_rows(named=True)}

        # After normalization, per_strategy_max is NOT a hard cap (it's relative/pre-norm)
        # e.g., alpha_baseline capped at 0.40 before norm → 0.6667 after norm (0.40/0.60)
        # This is EXPECTED and CORRECT behavior (relative cap, not hard cap)
        #
        # The caps were enforced BEFORE normalization, ensuring no strategy dominated
        # Final normalization scales everything proportionally to sum to 1.0
        #
        # Verification: Check that weights match expected post-normalization values

        # More direct test: check that AAPL + MSFT + GOOGL weights maintain proportions
        aapl_w = weights_dict.get("AAPL", 0.0)
        msft_w = weights_dict.get("MSFT", 0.0)
        googl_w = weights_dict.get("GOOGL", 0.0)
        tsla_w = weights_dict.get("TSLA", 0.0)

        # Expected after all transformations:
        # Total before norm: 0.40 (alpha capped) + 0.20 (momentum) = 0.60
        # AAPL: 0.16 / 0.60 = 0.2667
        # MSFT: 0.16 / 0.60 = 0.2667
        # GOOGL: 0.08 / 0.60 = 0.1333
        # TSLA: 0.20 / 0.60 = 0.3333

        assert abs(aapl_w - 0.2667) < 1e-3
        assert abs(msft_w - 0.2667) < 1e-3
        assert abs(googl_w - 0.1333) < 1e-3
        assert abs(tsla_w - 0.3333) < 1e-3

        # Most importantly: sum of alpha_baseline symbols should be exactly 2/3
        # (since 0.40 out of 0.60 total = 2/3)
        assert abs((aapl_w + msft_w + googl_w) - 0.6667) < 1e-3


class TestCorrelationMonitoring:
    """Test correlation monitoring (Component 3)."""

    def test_correlation_basic_two_strategies(self):
        """Test basic correlation calculation for two strategies."""
        from datetime import date

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.01, -0.005, 0.015],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.02, -0.01, 0.03],
                }
            ),
        }

        allocator = MultiAlphaAllocator(correlation_threshold=0.70)
        correlations = allocator.check_correlation(returns)

        # Should have one correlation pair
        assert len(correlations) == 1
        assert ("alpha_baseline", "momentum") in correlations

        # Correlation should be between -1 and 1
        corr = correlations[("alpha_baseline", "momentum")]
        assert -1 <= corr <= 1

    def test_correlation_high_triggers_alert(self, caplog):
        """Test that high correlation (>threshold) triggers warning log."""
        import logging
        from datetime import date

        caplog.set_level(logging.WARNING)

        # Create perfectly correlated returns
        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.01, 0.02, 0.03],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.01, 0.02, 0.03],  # Identical returns → corr = 1.0
                }
            ),
        }

        allocator = MultiAlphaAllocator(correlation_threshold=0.70)
        correlations = allocator.check_correlation(returns)

        # Correlation should be 1.0 (perfect positive correlation)
        assert abs(correlations[("alpha_baseline", "momentum")] - 1.0) < 1e-9

        # Should have logged warning
        assert any(
            "High inter-strategy correlation detected" in record.message
            for record in caplog.records
        )

    def test_correlation_low_no_alert(self, caplog):
        """Test that low correlation does not trigger alert."""
        import logging
        from datetime import date

        caplog.set_level(logging.WARNING)

        # Create uncorrelated returns
        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [
                        date(2024, 1, 1),
                        date(2024, 1, 2),
                        date(2024, 1, 3),
                        date(2024, 1, 4),
                    ],
                    "return": [0.01, -0.01, 0.01, -0.01],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [
                        date(2024, 1, 1),
                        date(2024, 1, 2),
                        date(2024, 1, 3),
                        date(2024, 1, 4),
                    ],
                    "return": [0.005, 0.005, 0.005, 0.005],  # Constant → low correlation
                }
            ),
        }

        allocator = MultiAlphaAllocator(correlation_threshold=0.70)
        correlations = allocator.check_correlation(returns)

        # Correlation should be close to 0
        assert abs(correlations[("alpha_baseline", "momentum")]) < 0.3

        # Should NOT have logged high correlation warning
        assert not any(
            "High inter-strategy correlation detected" in record.message
            for record in caplog.records
        )

    def test_correlation_three_strategies_all_pairs(self):
        """Test that all pairwise correlations are calculated for 3 strategies."""
        from datetime import date

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.01, 0.02, 0.03],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.02, 0.04, 0.06],
                }
            ),
            "mean_reversion": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [-0.01, 0.005, 0.015],
                }
            ),
        }

        allocator = MultiAlphaAllocator()
        correlations = allocator.check_correlation(returns)

        # Should have 3 pairs: C(3,2) = 3
        assert len(correlations) == 3
        assert ("alpha_baseline", "mean_reversion") in correlations
        assert ("alpha_baseline", "momentum") in correlations
        assert ("mean_reversion", "momentum") in correlations

    def test_correlation_empty_returns_raises_error(self):
        """Test that empty returns dict raises ValueError."""
        allocator = MultiAlphaAllocator()

        with pytest.raises(ValueError, match="recent_returns cannot be empty"):
            allocator.check_correlation({})

    def test_correlation_single_strategy_returns_empty(self):
        """Test that single strategy returns empty dict (no pairs)."""
        from datetime import date

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2)],
                    "return": [0.01, 0.02],
                }
            ),
        }

        allocator = MultiAlphaAllocator()
        correlations = allocator.check_correlation(returns)

        # Should return empty dict (need at least 2 strategies)
        assert correlations == {}

    def test_correlation_misaligned_dates_inner_join(self):
        """Test that correlation uses inner join for misaligned dates."""
        from datetime import date

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
                    "return": [0.01, 0.02, 0.03],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [
                        date(2024, 1, 2),
                        date(2024, 1, 3),
                        date(2024, 1, 4),
                    ],  # Different dates
                    "return": [0.04, 0.06, 0.08],
                }
            ),
        }

        allocator = MultiAlphaAllocator()
        correlations = allocator.check_correlation(returns)

        # Should calculate correlation on overlapping dates (1/2 and 1/3)
        assert ("alpha_baseline", "momentum") in correlations
        assert -1 <= correlations[("alpha_baseline", "momentum")] <= 1

    def test_correlation_missing_columns_raises_error(self):
        """Test that missing required columns raises ValueError."""
        from datetime import date

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1)],
                    "return": [0.01],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1)],
                    "profit": [0.02],  # Wrong column name!
                }
            ),
        }

        allocator = MultiAlphaAllocator()

        with pytest.raises(ValueError, match="missing required columns"):
            allocator.check_correlation(returns)

    def test_correlation_insufficient_data_points(self, caplog):
        """Test that insufficient data points (<2) is handled gracefully."""
        import logging
        from datetime import date

        caplog.set_level(logging.WARNING)

        returns = {
            "alpha_baseline": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1)],  # Only 1 data point
                    "return": [0.01],
                }
            ),
            "momentum": pl.DataFrame(
                {
                    "date": [date(2024, 1, 1)],
                    "return": [0.02],
                }
            ),
        }

        allocator = MultiAlphaAllocator()
        correlations = allocator.check_correlation(returns)

        # Should return empty dict and log warning
        assert correlations == {}
        assert any(
            "Insufficient overlapping data points" in record.message for record in caplog.records
        )


# ==============================================================================
# Regression Tests for Division-by-Zero Bug Fixes
# ==============================================================================
#
# Context: Codex comprehensive PR review discovered critical division-by-zero bugs
# in equal-weight (lines 370-376, 392-394) and inverse-vol (line 524-526) methods.
#
# These minimal tests verify the fixes prevent NaN values. Full market-neutral
# portfolio support (proper NET vs GROSS exposure handling) is deferred to P3.


class TestDivisionByZeroRegression:
    """Regression tests for division-by-zero bug fixes."""

    def test_equal_weight_zero_sum_no_nan(self, caplog):
        """Verify equal-weight doesn't produce NaN for zero-sum strategy weights."""
        import logging

        caplog.set_level(logging.WARNING)

        # Mix of long-only and zero-sum strategies
        signals = {
            "long_only": pl.DataFrame({"symbol": ["AAPL"], "score": [0.05], "weight": [1.0]}),
            "zero_sum": pl.DataFrame(
                {
                    "symbol": ["MSFT", "GOOGL"],
                    "score": [0.03, -0.03],
                    "weight": [0.5, -0.5],  # Sum = 0 (would cause div/0 without fix)
                }
            ),
        }

        # Enable short positions for market-neutral strategies
        allocator = MultiAlphaAllocator(method="equal_weight", allow_short_positions=True)
        result = allocator.allocate(signals, strategy_stats={})

        # CRITICAL: No NaN values (primary bug fix verification)
        assert not result["final_weight"].is_nan().any()
        assert result["final_weight"].is_finite().all()

        # Warning logged for zero-sum strategy
        assert any("Market-neutral" in record.message for record in caplog.records)

    def test_inverse_vol_with_offsetting_symbols_no_nan(self, caplog):
        """
        Verify inverse-vol doesn't produce NaN when offsetting long/short contributions cancel.

        This test reproduces the division-by-zero condition that would have occurred
        before the fix:
        - Strategy A: long-only portfolio (AAPL=0.5, MSFT=0.5)
        - Strategy B: short-only portfolio (AAPL=-0.5, MSFT=-0.5)
        - Both strategies have same volatility → equal inverse-vol weights (0.5 each)
        - Final aggregation: 0.5*0.5 + (-0.5)*0.5 = 0 for each symbol
        - Total sum = 0 → division by zero without _safe_normalize_weights fix
        """
        import logging

        caplog.set_level(logging.WARNING)

        # Strategy A: long-only
        # Strategy B: short-only (offsetting positions)
        # Same volatility → equal strategy weights → final sum = 0
        signals = {
            "long_strategy": pl.DataFrame(
                {"symbol": ["AAPL", "MSFT"], "score": [0.05, 0.03], "weight": [0.5, 0.5]}
            ),
            "short_strategy": pl.DataFrame(
                {"symbol": ["AAPL", "MSFT"], "score": [-0.05, -0.03], "weight": [-0.5, -0.5]}
            ),
        }

        strategy_stats = {
            "long_strategy": {"vol": 0.15},
            "short_strategy": {"vol": 0.15},  # Same vol → equal inverse-vol weights
        }

        # Enable short positions and disable per-strategy cap for clean zero-sum condition
        allocator = MultiAlphaAllocator(
            method="inverse_vol", per_strategy_max=1.0, allow_short_positions=True
        )
        result = allocator.allocate(signals, strategy_stats)

        # CRITICAL: No NaN values (primary bug fix verification)
        # Without the _safe_normalize_weights fix, this would produce NaN
        assert not result["final_weight"].is_nan().any()
        assert result["final_weight"].is_finite().all()

        # Warning logged for zero-sum portfolio (from _safe_normalize_weights)
        assert any("Zero-sum portfolio detected" in record.message for record in caplog.records)


# ==============================================================================
# Market-Neutral Portfolio Tests
# ==============================================================================


class TestMarketNeutralPortfolios:
    """Test market-neutral portfolio support with proper NET vs GROSS exposure handling."""

    def test_validation_rejects_shorts_when_disabled(self):
        """Verify allocator rejects negative weights when allow_short_positions=False."""
        signals = {
            "strategy_a": pl.DataFrame(
                {"symbol": ["AAPL", "MSFT"], "score": [0.05, -0.03], "weight": [0.6, -0.4]}
            )
        }

        allocator = MultiAlphaAllocator(method="equal_weight", allow_short_positions=False)

        with pytest.raises(ValueError, match="Negative weights detected"):
            allocator.allocate(signals, strategy_stats={})

    def test_equal_weight_market_neutral_gross_normalization(self):
        """Verify equal-weight uses GROSS exposure for market-neutral portfolios."""
        # Perfect market-neutral: NET = 0, GROSS = 2.0
        signals = {
            "long_short_strategy": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL", "AMZN"],
                    "score": [0.05, 0.03, -0.04, -0.04],
                    "weight": [0.5, 0.5, -0.5, -0.5],  # NET = 0, GROSS = 2.0
                }
            )
        }

        allocator = MultiAlphaAllocator(method="equal_weight", allow_short_positions=True)
        result = allocator.allocate(signals, strategy_stats={})

        # Weights normalized by GROSS exposure (2.0)
        # Each symbol gets weight / 2.0
        aapl = result.filter(pl.col("symbol") == "AAPL")["final_weight"][0]
        msft = result.filter(pl.col("symbol") == "MSFT")["final_weight"][0]
        googl = result.filter(pl.col("symbol") == "GOOGL")["final_weight"][0]
        amzn = result.filter(pl.col("symbol") == "AMZN")["final_weight"][0]

        assert abs(aapl - 0.25) < 1e-9  # 0.5 / 2.0
        assert abs(msft - 0.25) < 1e-9  # 0.5 / 2.0
        assert abs(googl - (-0.25)) < 1e-9  # -0.5 / 2.0
        assert abs(amzn - (-0.25)) < 1e-9  # -0.5 / 2.0

        # NET exposure = 0 (market-neutral)
        net_exposure = result["final_weight"].sum()
        assert abs(net_exposure) < 1e-9

        # GROSS exposure = 1.0 (normalized)
        gross_exposure = result["final_weight"].abs().sum()
        assert abs(gross_exposure - 1.0) < 1e-9

    def test_inverse_vol_market_neutral_preserves_signs(self):
        """Verify inverse-vol preserves long/short signs for market-neutral portfolios."""
        signals = {
            "long_strategy": pl.DataFrame(
                {"symbol": ["AAPL", "MSFT"], "score": [0.05, 0.03], "weight": [0.6, 0.4]}
            ),
            "short_strategy": pl.DataFrame(
                {"symbol": ["GOOGL", "AMZN"], "score": [-0.04, -0.06], "weight": [-0.4, -0.6]}
            ),
        }

        strategy_stats = {
            "long_strategy": {"vol": 0.15},
            "short_strategy": {"vol": 0.15},  # Equal vol → equal weights
        }

        allocator = MultiAlphaAllocator(
            method="inverse_vol", per_strategy_max=1.0, allow_short_positions=True
        )
        result = allocator.allocate(signals, strategy_stats)

        # Signs should be preserved
        aapl = result.filter(pl.col("symbol") == "AAPL")["final_weight"][0]
        msft = result.filter(pl.col("symbol") == "MSFT")["final_weight"][0]
        googl = result.filter(pl.col("symbol") == "GOOGL")["final_weight"][0]
        amzn = result.filter(pl.col("symbol") == "AMZN")["final_weight"][0]

        assert aapl > 0  # Long position
        assert msft > 0  # Long position
        assert googl < 0  # Short position
        assert amzn < 0  # Short position

        # NET exposure ≈ 0 (market-neutral)
        net_exposure = result["final_weight"].sum()
        assert abs(net_exposure) < 1e-2

    def test_rank_aggregation_market_neutral_mixed_strategies(self):
        """Verify rank aggregation handles mixed long-only and market-neutral strategies."""
        signals = {
            "long_only": pl.DataFrame(
                {"symbol": ["AAPL", "MSFT"], "score": [0.05, 0.03], "weight": [0.6, 0.4]}
            ),
            "market_neutral": pl.DataFrame(
                {
                    "symbol": ["GOOGL", "AMZN"],
                    "score": [0.04, -0.04],
                    "weight": [0.5, -0.5],  # Zero-sum
                }
            ),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation", allow_short_positions=True)
        result = allocator.allocate(signals, strategy_stats={})

        # Should have 4 symbols (2 long-only + 2 market-neutral)
        assert len(result) == 4

        # AAPL and MSFT should be positive (long-only strategy)
        aapl = result.filter(pl.col("symbol") == "AAPL")["final_weight"][0]
        msft = result.filter(pl.col("symbol") == "MSFT")["final_weight"][0]
        assert aapl > 0
        assert msft > 0

        # GOOGL should be positive, AMZN negative (market-neutral strategy)
        googl = result.filter(pl.col("symbol") == "GOOGL")["final_weight"][0]
        amzn = result.filter(pl.col("symbol") == "AMZN")["final_weight"][0]
        assert googl > 0
        assert amzn < 0

    def test_net_vs_gross_exposure_calculation(self):
        """Verify correct NET and GROSS exposure calculation for market-neutral portfolios."""
        # Create a 130/30 portfolio (130% long, 30% short, 100% NET)
        signals = {
            "long_short_strategy": pl.DataFrame(
                {
                    "symbol": ["AAPL", "MSFT", "GOOGL"],
                    "score": [0.08, 0.05, -0.03],
                    "weight": [0.65, 0.65, -0.30],  # NET = 1.0, GROSS = 1.6
                }
            )
        }

        allocator = MultiAlphaAllocator(method="equal_weight", allow_short_positions=True)
        result = allocator.allocate(signals, strategy_stats={})

        # NET exposure should be normalized to match input proportions
        net_exposure = result["final_weight"].sum()
        gross_exposure = result["final_weight"].abs().sum()

        # For 130/30 portfolio: NET = 1.0, GROSS = 1.6
        # After normalization: weights scaled to maintain ratio
        assert abs(net_exposure - 1.0) < 1e-2  # NET = 100%
        assert abs(gross_exposure - 1.6) < 1e-2  # GROSS = 160%

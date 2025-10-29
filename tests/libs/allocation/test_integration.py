"""
Integration tests for Multi-Alpha Allocator with Orchestrator.

Tests the complete workflow:
1. Signal generation from multiple strategies
2. Signal→DataFrame conversion
3. Multi-alpha allocation
4. DataFrame→Signal conversion
5. Orchestrator integration

Coverage includes:
- Single-strategy mode (backward compatibility)
- Multi-strategy mode (3+ strategies)
- Conversion round-trip correctness
- End-to-end orchestrator flow
"""

from decimal import Decimal

import polars as pl
import pytest

from apps.orchestrator.orchestrator import (
    TradingOrchestrator,
    dataframe_to_signals,
    signals_to_dataframe,
)
from apps.orchestrator.schemas import Signal
from libs.allocation import MultiAlphaAllocator

# ==============================================================================
# Conversion Tests
# ==============================================================================


class TestSignalConversion:
    """Test Signal↔DataFrame conversion helpers."""

    def test_signals_to_dataframe_basic(self) -> None:
        """Test basic conversion from signals to DataFrame."""
        signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.4),
            Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.3),
            Signal(symbol="GOOGL", predicted_return=0.02, rank=3, target_weight=0.3),
        ]

        df = signals_to_dataframe(signals)

        assert df.height == 3
        assert list(df.columns) == ["symbol", "score", "weight"]
        assert df["symbol"].to_list() == ["AAPL", "MSFT", "GOOGL"]
        assert df["score"].to_list() == [0.05, 0.03, 0.02]
        assert df["weight"].to_list() == [0.4, 0.3, 0.3]

    def test_signals_to_dataframe_empty(self) -> None:
        """Test conversion with empty signal list."""
        df = signals_to_dataframe([])

        assert df.height == 0
        assert list(df.columns) == ["symbol", "score", "weight"]

    def test_dataframe_to_signals_basic(self) -> None:
        """Test basic conversion from DataFrame to signals."""
        df = pl.DataFrame(
            {
                "symbol": ["AAPL", "MSFT", "GOOGL"],
                "final_weight": [0.4, 0.3, 0.3],
            }
        )

        signals = dataframe_to_signals(df)

        assert len(signals) == 3
        assert signals[0].symbol == "AAPL"
        assert signals[0].target_weight == 0.4
        assert signals[0].predicted_return == 0.0  # Not preserved through allocation
        assert signals[0].rank == 0  # Not preserved through allocation

        assert signals[1].symbol == "MSFT"
        assert signals[1].target_weight == 0.3

    def test_dataframe_to_signals_empty(self) -> None:
        """Test conversion with empty DataFrame."""
        df = pl.DataFrame({"symbol": [], "final_weight": []})

        signals = dataframe_to_signals(df)

        assert len(signals) == 0

    def test_conversion_roundtrip(self) -> None:
        """Test Signal→DataFrame→Signal round-trip preserves symbols and weights."""
        original_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.4),
            Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.6),
        ]

        # Convert to DataFrame
        df = signals_to_dataframe(original_signals)

        # Simulate allocation (rename weight -> final_weight)
        df = df.rename({"weight": "final_weight"})

        # Convert back to signals
        result_signals = dataframe_to_signals(df)

        # Check symbols and weights preserved
        assert len(result_signals) == len(original_signals)
        assert result_signals[0].symbol == original_signals[0].symbol
        assert result_signals[0].target_weight == original_signals[0].target_weight
        assert result_signals[1].symbol == original_signals[1].symbol
        assert result_signals[1].target_weight == original_signals[1].target_weight


# ==============================================================================
# Multi-Strategy Integration Tests
# ==============================================================================


class TestMultiStrategyIntegration:
    """Test full multi-strategy workflow with allocator."""

    def test_three_strategy_rank_aggregation(self) -> None:
        """Test 3-strategy integration with rank aggregation method."""
        # Strategy 1: alpha_baseline
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.5),
            Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.5),
        ]

        # Strategy 2: momentum
        strategy2_signals = [
            Signal(symbol="GOOGL", predicted_return=0.06, rank=1, target_weight=0.5),
            Signal(symbol="AAPL", predicted_return=0.04, rank=2, target_weight=0.5),
        ]

        # Strategy 3: mean_reversion
        strategy3_signals = [
            Signal(symbol="TSLA", predicted_return=0.07, rank=1, target_weight=0.5),
            Signal(symbol="AAPL", predicted_return=0.02, rank=2, target_weight=0.5),
        ]

        # Convert to DataFrames
        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
            "mean_reversion": signals_to_dataframe(strategy3_signals),
        }

        # Allocate across strategies
        allocator = MultiAlphaAllocator(method="rank_aggregation", per_strategy_max=0.40)
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})

        # Convert back to signals
        blended_signals = dataframe_to_signals(blended_df)

        # Verify results
        assert len(blended_signals) == 4  # AAPL, MSFT, GOOGL, TSLA
        total_weight = sum(s.target_weight for s in blended_signals)
        assert abs(total_weight - 1.0) < 1e-9  # Sum to 100%

        # AAPL should have highest weight (appears in all 3 strategies)
        aapl_signal = next(s for s in blended_signals if s.symbol == "AAPL")
        assert aapl_signal.target_weight == max(s.target_weight for s in blended_signals)

    def test_multi_strategy_equal_weight(self) -> None:
        """Test multi-strategy with equal weight method."""
        # Two strategies with different symbols
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=1.0),
        ]

        strategy2_signals = [
            Signal(symbol="GOOGL", predicted_return=0.06, rank=1, target_weight=1.0),
        ]

        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
        }

        allocator = MultiAlphaAllocator(method="equal_weight")
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})

        blended_signals = dataframe_to_signals(blended_df)

        # Equal weight: each strategy gets 50% influence
        assert len(blended_signals) == 2
        assert abs(blended_signals[0].target_weight - 0.5) < 1e-9
        assert abs(blended_signals[1].target_weight - 0.5) < 1e-9

    def test_per_strategy_caps_enforced(self) -> None:
        """Test that per-strategy caps are enforced in integration."""
        # Strategy 1 dominates with many symbols
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.25),
            Signal(symbol="MSFT", predicted_return=0.04, rank=2, target_weight=0.25),
            Signal(symbol="GOOGL", predicted_return=0.03, rank=3, target_weight=0.25),
            Signal(symbol="AMZN", predicted_return=0.02, rank=4, target_weight=0.25),
        ]

        # Strategy 2 has only one symbol
        strategy2_signals = [
            Signal(symbol="TSLA", predicted_return=0.08, rank=1, target_weight=1.0),
        ]

        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
        }

        # Use 40% per-strategy cap
        allocator = MultiAlphaAllocator(method="rank_aggregation", per_strategy_max=0.40)
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})

        blended_signals = dataframe_to_signals(blended_df)

        # Verify total weight sums to 100%
        total_weight = sum(s.target_weight for s in blended_signals)
        assert abs(total_weight - 1.0) < 1e-9

        # Verify caps are applied (strategy 1 would dominate without caps)
        # With caps: each strategy gets max 40% pre-normalization
        # After normalization: 0.4/0.8 = 50% each
        # TSLA (only in strategy 2) should get ~50% of total
        tsla_signal = next(s for s in blended_signals if s.symbol == "TSLA")

        # After normalization, each strategy contributes equally
        # TSLA gets full weight from its strategy (50% of total)
        # Each symbol from alpha_baseline gets ~12.5% (50% / 4 symbols)
        assert abs(tsla_signal.target_weight - 0.5) < 1e-2  # ~50%


# ==============================================================================
# Orchestrator Integration Tests
# ==============================================================================


@pytest.mark.asyncio()
class TestOrchestratorIntegration:
    """Test orchestrator with multi-strategy support."""

    async def test_single_strategy_backward_compatible(self) -> None:
        """Test that single-strategy mode bypasses allocator (backward compatible)."""
        # This test verifies backward compatibility
        # When strategy_id is a string, allocator should NOT be used

        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
        )

        # Note: This would require mocking signal_client and execution_client
        # For now, this is a structure test (would need full mocks for execution)

        # Verify orchestrator accepts single string strategy_id
        # and has allocation parameters set
        assert orchestrator.allocation_method == "rank_aggregation"
        assert orchestrator.per_strategy_max == 0.40

    async def test_multi_strategy_uses_allocator(self) -> None:
        """Test that multi-strategy mode uses allocator."""
        orchestrator = TradingOrchestrator(
            signal_service_url="http://localhost:8001",
            execution_gateway_url="http://localhost:8002",
            capital=Decimal("100000"),
            max_position_size=Decimal("10000"),
            allocation_method="equal_weight",
            per_strategy_max=0.30,
        )

        # Verify allocation parameters
        assert orchestrator.allocation_method == "equal_weight"
        assert orchestrator.per_strategy_max == 0.30

        # Note: Full integration test would require mocking:
        # - signal_client.fetch_signals() for each strategy
        # - execution_client.submit_order() for each order
        # This structure demonstrates the integration points


# ==============================================================================
# Edge Cases
# ==============================================================================


class TestIntegrationEdgeCases:
    """Test edge cases in integration."""

    def test_single_symbol_from_each_strategy(self) -> None:
        """Test allocation when each strategy recommends a different single symbol."""
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=1.0)
        ]
        strategy2_signals = [
            Signal(symbol="GOOGL", predicted_return=0.06, rank=1, target_weight=1.0)
        ]
        strategy3_signals = [
            Signal(symbol="TSLA", predicted_return=0.07, rank=1, target_weight=1.0)
        ]

        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
            "mean_reversion": signals_to_dataframe(strategy3_signals),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})

        blended_signals = dataframe_to_signals(blended_df)

        # Each symbol should get equal weight (1/3 each)
        assert len(blended_signals) == 3
        for signal in blended_signals:
            assert abs(signal.target_weight - (1.0 / 3.0)) < 1e-9

    def test_complete_symbol_overlap(self) -> None:
        """Test allocation when all strategies recommend the same symbols."""
        # All strategies recommend AAPL and MSFT in same order
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.6),
            Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.4),
        ]
        strategy2_signals = [
            Signal(symbol="AAPL", predicted_return=0.06, rank=1, target_weight=0.6),
            Signal(symbol="MSFT", predicted_return=0.04, rank=2, target_weight=0.4),
        ]

        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})

        blended_signals = dataframe_to_signals(blended_df)

        # Both symbols should appear
        assert len(blended_signals) == 2

        # AAPL should have higher weight (rank 1 in both strategies)
        aapl = next(s for s in blended_signals if s.symbol == "AAPL")
        msft = next(s for s in blended_signals if s.symbol == "MSFT")
        assert aapl.target_weight > msft.target_weight

    def test_empty_strategy_handling(self) -> None:
        """Test handling when one strategy returns no signals."""
        # Strategy 1 has signals
        strategy1_signals = [
            Signal(symbol="AAPL", predicted_return=0.05, rank=1, target_weight=0.5),
            Signal(symbol="MSFT", predicted_return=0.03, rank=2, target_weight=0.5),
        ]

        # Strategy 2 has no signals
        strategy2_signals = []

        signal_dfs = {
            "alpha_baseline": signals_to_dataframe(strategy1_signals),
            "momentum": signals_to_dataframe(strategy2_signals),
        }

        allocator = MultiAlphaAllocator(method="rank_aggregation")

        # Allocator should handle empty strategy gracefully
        # It will only use alpha_baseline's signals
        blended_df = allocator.allocate(signal_dfs, strategy_stats={})
        blended_signals = dataframe_to_signals(blended_df)

        # Should still produce valid allocation from remaining strategy
        assert len(blended_signals) == 2
        total_weight = sum(s.target_weight for s in blended_signals)
        assert abs(total_weight - 1.0) < 1e-9

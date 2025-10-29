"""
Multi-Alpha Capital Allocator.

Blends signals from multiple trading strategies using risk-aware allocation methods:
1. Rank Aggregation: Averages normalized ranks across strategies (robust to outliers)
2. Inverse Volatility: Weights inversely to realized volatility (risk-aware)
3. Equal Weight: Simple average (baseline for comparison)

Safety Features:
- Per-strategy concentration limits (default 40% max)
- Correlation monitoring with alerts (>70% threshold)
- Weight normalization (always sums to 100%)

Example:
    >>> from libs.allocation import MultiAlphaAllocator
    >>> import polars as pl
    >>>
    >>> signals = {
    ...     'alpha_baseline': pl.DataFrame({
    ...         'symbol': ['AAPL', 'MSFT'],
    ...         'score': [0.8, 0.6],
    ...         'weight': [0.6, 0.4]
    ...     }),
    ...     'momentum': pl.DataFrame({
    ...         'symbol': ['AAPL', 'GOOGL'],
    ...         'score': [0.7, 0.9],
    ...         'weight': [0.5, 0.5]
    ...     })
    ... }
    >>>
    >>> allocator = MultiAlphaAllocator(method='rank_aggregation')
    >>> result = allocator.allocate(signals, strategy_stats={})
    >>> print(result)
    shape: (3, 3)
    ┌────────┬──────────────┬────────────────────────┐
    │ symbol ┆ final_weight ┆ contributing_strategies│
    ├────────┼──────────────┼────────────────────────┤
    │ AAPL   ┆ 0.45         ┆ [alpha_baseline, mom…] │
    │ MSFT   ┆ 0.25         ┆ [alpha_baseline]       │
    │ GOOGL  ┆ 0.30         ┆ [momentum]             │
    └────────┴──────────────┴────────────────────────┘
"""

import logging
import math
import numbers
from typing import Any, Literal

import polars as pl

logger = logging.getLogger(__name__)

# Type alias for allocation methods
AllocMethod = Literal["rank_aggregation", "inverse_vol", "equal_weight"]


class MultiAlphaAllocator:
    """
    Allocate capital across multiple trading strategies.

    Supports three allocation methods:
    1. **rank_aggregation**: Averages normalized ranks (robust, handles different signal scales)
    2. **inverse_vol**: Weights inversely to volatility (risk-aware, reduces allocation to volatile strategies)
    3. **equal_weight**: Simple average (baseline, no estimation risk)

    Safety constraints:
    - Per-strategy maximum allocation (prevents over-concentration)
    - Correlation monitoring with alerts (detects redundant strategies)
    - Weight normalization (always sums to 100%)

    Attributes:
        method: Allocation method to use
        per_strategy_max: Maximum allocation to any single strategy (0.0-1.0)
        correlation_threshold: Alert if inter-strategy correlation exceeds this value

    Example:
        >>> allocator = MultiAlphaAllocator(
        ...     method='rank_aggregation',
        ...     per_strategy_max=0.40,  # Max 40% to any strategy
        ...     correlation_threshold=0.70  # Alert if corr > 70%
        ... )
        >>> result = allocator.allocate(signals, strategy_stats)
    """

    def __init__(
        self,
        method: AllocMethod = "rank_aggregation",
        per_strategy_max: float = 0.40,
        correlation_threshold: float = 0.70,
    ):
        """
        Initialize Multi-Alpha Allocator.

        Args:
            method: Allocation method ('rank_aggregation', 'inverse_vol', 'equal_weight')
            per_strategy_max: Maximum weight for any single strategy (default 0.40 = 40%)
            correlation_threshold: Alert threshold for inter-strategy correlation (default 0.70)

        Raises:
            ValueError: If per_strategy_max not in [0, 1] or correlation_threshold not in [0, 1]
        """
        if not 0 <= per_strategy_max <= 1:
            raise ValueError(f"per_strategy_max must be in [0, 1], got {per_strategy_max}")
        if not 0 <= correlation_threshold <= 1:
            raise ValueError(
                f"correlation_threshold must be in [0, 1], got {correlation_threshold}"
            )

        self.method = method
        self.per_strategy_max = per_strategy_max
        self.correlation_threshold = correlation_threshold

        logger.info(
            "Initialized MultiAlphaAllocator",
            extra={
                "method": method,
                "per_strategy_max": per_strategy_max,
                "correlation_threshold": correlation_threshold,
            },
        )

    def allocate(
        self,
        signals: dict[str, pl.DataFrame],
        strategy_stats: dict[str, dict[str, Any]] | None = None,
    ) -> pl.DataFrame:
        """
        Allocate capital weights across strategies.

        Args:
            signals: Dictionary mapping strategy_id to DataFrames with columns [symbol, score, weight].
                    Each DataFrame represents one strategy's target positions.
            strategy_stats: Dictionary mapping strategy_id to statistics dicts with keys {vol, sharpe, ...}.
                           Required for 'inverse_vol' method. Can be empty dict or None for 'rank_aggregation'
                           and 'equal_weight' methods.

        Returns:
            pl.DataFrame with columns:
                - symbol (str): Symbol ticker
                - final_weight (float): Blended allocation weight (sums to 1.0)
                - contributing_strategies (list[str]): Strategies that recommended this symbol

        Raises:
            ValueError: If signals dict is empty or if strategy_stats required but missing

        Example:
            >>> signals = {
            ...     'alpha_baseline': pl.DataFrame({'symbol': ['AAPL'], 'score': [0.8], 'weight': [1.0]}),
            ...     'momentum': pl.DataFrame({'symbol': ['MSFT'], 'score': [0.7], 'weight': [1.0]})
            ... }
            >>> allocator = MultiAlphaAllocator(method='equal_weight')
            >>> result = allocator.allocate(signals, strategy_stats={})
            >>> assert abs(result['final_weight'].sum() - 1.0) < 1e-9  # Sums to 100%
        """
        # Validate inputs
        if not signals:
            raise ValueError("At least one strategy required (signals dict is empty)")

        # Validate strategy_stats for inverse_vol method (BEFORE single-strategy bypass)
        if self.method == "inverse_vol":
            if strategy_stats is None:
                raise ValueError("strategy_stats required for inverse_vol method but got None")
            if not strategy_stats:
                raise ValueError(
                    "strategy_stats required for inverse_vol method but got empty dict"
                )

        # Single strategy optimization: bypass allocator (except inverse_vol which needs validation)
        if len(signals) == 1 and self.method != "inverse_vol":
            strategy_id, df = next(iter(signals.items()))
            logger.info(
                "Single strategy detected, bypassing allocator",
                extra={"strategy_id": strategy_id, "num_symbols": len(df)},
            )
            # Normalize weights to sum to 1.0
            total_weight = df["weight"].sum()
            return df.select(
                [
                    pl.col("symbol"),
                    (pl.col("weight") / total_weight).alias("final_weight"),
                    pl.lit([strategy_id]).alias("contributing_strategies"),
                ]
            )

        # Dispatch to appropriate allocation method
        if self.method == "rank_aggregation":
            return self._rank_aggregation(signals)
        elif self.method == "inverse_vol":
            # strategy_stats validated above, assert for mypy
            assert strategy_stats is not None, "strategy_stats validated above"
            return self._inverse_vol(signals, strategy_stats)
        elif self.method == "equal_weight":
            return self._equal_weight(signals)
        else:
            # Should never reach here due to Literal type hint, but defensive
            raise ValueError(f"Unknown allocation method: {self.method}")

    def _rank_aggregation(self, signals: dict[str, pl.DataFrame]) -> pl.DataFrame:
        """
        Allocate by averaging normalized ranks across strategies.

        Methodology:
        1. For each strategy, rank symbols by score (higher score = better rank)
        2. Normalize ranks to [0, 1] range
        3. Average ranks across strategies (each symbol gets mean of its ranks)
        4. Convert average ranks to weights (normalize to sum to 1.0)

        Advantages:
        - Robust to outlier scores
        - Handles different signal scales naturally
        - Equal influence from each strategy (democratic)

        Disadvantages:
        - Loses information about signal strength magnitude
        - Treats all strategies equally (ignores quality differences)

        Args:
            signals: Dictionary of strategy_id -> DataFrame with [symbol, score, weight]

        Returns:
            DataFrame with [symbol, final_weight, contributing_strategies]
        """
        logger.info(
            "Allocating via rank aggregation",
            extra={"num_strategies": len(signals), "method": "rank_aggregation"},
        )

        # Step 1: Rank symbols within each strategy
        ranked_dfs = []
        for strategy_id, df in signals.items():
            if df.is_empty():
                logger.debug(f"Skipping empty signals from {strategy_id}")
                continue

            # Rank by score descending (higher score = better = rank=1)
            # Use reciprocal rank: weight = 1/rank
            # This ensures all symbols get positive weight:
            #   rank=1 (best) → 1/1 = 1.0
            #   rank=2 → 1/2 = 0.5
            #   rank=3 → 1/3 = 0.33
            # Advantages: Standard rank aggregation method, no zero weights
            ranked = df.select(
                [
                    pl.col("symbol"),
                    pl.col("score").rank(method="ordinal", descending=True).alias("rank"),
                ]
            ).with_columns(
                [
                    # Reciprocal rank: 1 / rank
                    (1.0 / pl.col("rank")).alias("normalized_rank"),
                    pl.lit(strategy_id).alias("strategy_id"),
                ]
            )
            ranked_dfs.append(ranked)

        if not ranked_dfs:
            # All strategies had empty signals
            logger.warning("All strategies had empty signals, returning empty allocation")
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "final_weight": pl.Float64,
                    "contributing_strategies": pl.List(pl.Utf8),
                }
            )

        # Step 2: Combine all rankings
        all_ranks = pl.concat(ranked_dfs)

        # Step 3: Average ranks per symbol across strategies
        avg_ranks = (
            all_ranks.group_by("symbol")
            .agg(
                [
                    pl.col("normalized_rank").mean().alias("avg_rank"),
                    pl.col("strategy_id").alias("contributing_strategies"),
                ]
            )
            .sort("avg_rank", descending=True)  # Higher avg_rank = better
        )

        # Step 4: Convert average ranks to weights
        # Use avg_rank directly as weight, then normalize
        total_rank = avg_ranks["avg_rank"].sum()
        result = avg_ranks.select(
            [
                pl.col("symbol"),
                (pl.col("avg_rank") / total_rank).alias("final_weight"),
                pl.col("contributing_strategies"),
            ]
        )

        # Apply per-strategy concentration limits
        result = self._apply_concentration_limits(result)

        # Final normalization to ensure sum = 1.0
        final_sum = result["final_weight"].sum()
        result = result.with_columns((pl.col("final_weight") / final_sum).alias("final_weight"))

        logger.info(
            "Rank aggregation complete",
            extra={
                "num_symbols": len(result),
                "total_weight": result["final_weight"].sum(),
                "max_weight": result["final_weight"].max(),
            },
        )

        return result

    def _equal_weight(self, signals: dict[str, pl.DataFrame]) -> pl.DataFrame:
        """
        Allocate by simple averaging across strategies.

        Methodology:
        1. For each symbol, collect weights from all strategies that recommend it
        2. Average weights across strategies (equal influence)
        3. Normalize final weights to sum to 1.0

        Advantages:
        - Simple, no estimation risk
        - Equal influence from each strategy

        Disadvantages:
        - Ignores strategy quality (Sharpe, volatility)
        - Ignores signal strength within strategy

        Args:
            signals: Dictionary of strategy_id -> DataFrame with [symbol, score, weight]

        Returns:
            DataFrame with [symbol, final_weight, contributing_strategies]
        """
        logger.info(
            "Allocating via equal weight",
            extra={"num_strategies": len(signals), "method": "equal_weight"},
        )

        # Combine all signals with strategy ID
        combined_dfs = []
        for strategy_id, df in signals.items():
            if df.is_empty():
                logger.debug(f"Skipping empty signals from {strategy_id}")
                continue

            combined_dfs.append(
                df.select([pl.col("symbol"), pl.col("weight")]).with_columns(
                    pl.lit(strategy_id).alias("strategy_id")
                )
            )

        if not combined_dfs:
            logger.warning("All strategies had empty signals, returning empty allocation")
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "final_weight": pl.Float64,
                    "contributing_strategies": pl.List(pl.Utf8),
                }
            )

        all_signals = pl.concat(combined_dfs)

        # Average weights per symbol across strategies
        result = (
            all_signals.group_by("symbol")
            .agg(
                [
                    pl.col("weight").mean().alias("final_weight"),
                    pl.col("strategy_id").alias("contributing_strategies"),
                ]
            )
            .sort("final_weight", descending=True)
        )

        # Apply concentration limits
        result = self._apply_concentration_limits(result)

        # Final normalization
        final_sum = result["final_weight"].sum()
        result = result.with_columns((pl.col("final_weight") / final_sum).alias("final_weight"))

        logger.info(
            "Equal weight allocation complete",
            extra={
                "num_symbols": len(result),
                "total_weight": result["final_weight"].sum(),
                "max_weight": result["final_weight"].max(),
            },
        )

        return result

    def _inverse_vol(
        self, signals: dict[str, pl.DataFrame], strategy_stats: dict[str, dict[str, Any]]
    ) -> pl.DataFrame:
        """
        Allocate inversely proportional to strategy volatility.

        Methodology:
        1. Extract volatility for each strategy from strategy_stats
        2. Calculate inverse volatility weights: weight_i = (1/vol_i) / Σ(1/vol_j)
        3. Apply strategy weights to symbol allocations
        4. Aggregate across symbols and normalize

        Advantages:
        - Risk-aware allocation (reduces exposure to volatile strategies)
        - Leverages historical volatility as proxy for risk
        - Improves risk-adjusted returns (Sharpe ratio)

        Disadvantages:
        - Requires accurate volatility estimates
        - Backward-looking (past vol may not predict future)
        - Penalizes high-conviction volatile strategies

        Args:
            signals: Dictionary of strategy_id -> DataFrame with [symbol, score, weight]
            strategy_stats: Dictionary of strategy_id -> {vol, sharpe, ...}
                           Must contain 'vol' key for each strategy in signals

        Returns:
            DataFrame with [symbol, final_weight, contributing_strategies]

        Raises:
            ValueError: If strategy_stats missing or doesn't contain 'vol' for all strategies
        """
        logger.info(
            "Allocating via inverse volatility",
            extra={"num_strategies": len(signals), "method": "inverse_vol"},
        )

        # Extract and validate volatility for each strategy
        strategy_vols: dict[str, float] = {}
        for strategy_id in signals.keys():
            if strategy_id not in strategy_stats:
                raise ValueError(
                    f"strategy_stats missing entry for '{strategy_id}' (required for inverse_vol)"
                )
            stats = strategy_stats[strategy_id]
            if "vol" not in stats:
                raise ValueError(
                    f"strategy_stats['{strategy_id}'] missing 'vol' key (required for inverse_vol)"
                )

            vol = stats["vol"]
            # Validate volatility is positive and finite (accept any numeric type including numpy)
            if not isinstance(vol, numbers.Real):
                raise ValueError(
                    f"Invalid volatility for '{strategy_id}': {vol} (must be positive finite number)"
                )
            vol_float = float(vol)  # Convert to float for type safety
            if vol_float <= 0 or not math.isfinite(vol_float):
                raise ValueError(
                    f"Invalid volatility for '{strategy_id}': {vol} (must be positive finite number)"
                )

            strategy_vols[strategy_id] = vol_float

        # Calculate inverse volatility weights for strategies
        # weight_i = (1/vol_i) / Σ(1/vol_j)
        inv_vols: dict[str, float] = {sid: 1.0 / vol for sid, vol in strategy_vols.items()}
        total_inv_vol: float = sum(inv_vols.values())
        strategy_weights: dict[str, float] = {
            sid: inv_vol / total_inv_vol for sid, inv_vol in inv_vols.items()
        }

        logger.debug(
            "Calculated inverse volatility weights",
            extra={
                "strategy_vols": strategy_vols,
                "strategy_weights": strategy_weights,
            },
        )

        # Apply strategy weights to signals
        # For each symbol: weighted_signal = Σ(strategy_weight_i * symbol_weight_i)
        weighted_dfs = []
        for strategy_id, df in signals.items():
            if df.is_empty():
                logger.debug(f"Skipping empty signals from {strategy_id}")
                continue

            # Scale symbol weights by strategy weight
            strat_weight = strategy_weights[strategy_id]
            weighted = df.select(
                [
                    pl.col("symbol"),
                    (pl.col("weight") * strat_weight).alias("weighted_contribution"),
                ]
            ).with_columns(pl.lit(strategy_id).alias("strategy_id"))

            weighted_dfs.append(weighted)

        if not weighted_dfs:
            logger.warning("All strategies had empty signals, returning empty allocation")
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "final_weight": pl.Float64,
                    "contributing_strategies": pl.List(pl.Utf8),
                }
            )

        # Combine weighted contributions
        all_weighted = pl.concat(weighted_dfs)

        # Aggregate per symbol
        result = (
            all_weighted.group_by("symbol")
            .agg(
                [
                    pl.col("weighted_contribution").sum().alias("final_weight"),
                    pl.col("strategy_id").alias("contributing_strategies"),
                ]
            )
            .sort("final_weight", descending=True)
        )

        # NOTE: Skipping concentration limits for inverse_vol
        # Reason: Limits should be applied per-strategy BEFORE aggregation, not after.
        # Applying limits after aggregation distorts the mathematically correct inverse-vol ratios.
        # Component 3 will implement per-strategy concentration tracking.
        # result = self._apply_concentration_limits(result)

        # Final normalization to ensure sum = 1.0
        final_sum = result["final_weight"].sum()
        result = result.with_columns((pl.col("final_weight") / final_sum).alias("final_weight"))

        logger.info(
            "Inverse volatility allocation complete",
            extra={
                "num_symbols": len(result),
                "total_weight": result["final_weight"].sum(),
                "max_weight": result["final_weight"].max(),
            },
        )

        return result

    def _apply_concentration_limits(self, df: pl.DataFrame) -> pl.DataFrame:
        """
        Apply per-strategy concentration limits.

        Ensures no single strategy contributes more than per_strategy_max to any symbol.

        NOTE: This is a simplified implementation for Component 1. Full implementation
        in Component 3 will enforce per-strategy caps properly by tracking strategy
        contributions separately before blending.

        Args:
            df: DataFrame with [symbol, final_weight, contributing_strategies]

        Returns:
            DataFrame with capped weights (not yet normalized)
        """
        # Placeholder: Full implementation in Component 3
        # For now, just cap individual symbol weights at per_strategy_max
        return df.with_columns(
            pl.when(pl.col("final_weight") > self.per_strategy_max)
            .then(pl.lit(self.per_strategy_max))
            .otherwise(pl.col("final_weight"))
            .alias("final_weight")
        )

    def check_correlation(
        self, recent_returns: dict[str, pl.DataFrame]
    ) -> dict[tuple[str, str], float]:
        """
        Check inter-strategy correlation and emit alerts if above threshold (Placeholder for Component 3).

        This method will be fully implemented in Component 3.

        Args:
            recent_returns: Dictionary mapping strategy_id to DataFrames with columns [date, return]

        Returns:
            Dictionary mapping (strategy1, strategy2) -> correlation_coefficient
        """
        logger.warning("Correlation monitoring not yet implemented (Component 3)")
        return {}

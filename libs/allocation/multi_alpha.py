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
        allow_short_positions: bool = False,
    ):
        """
        Initialize Multi-Alpha Allocator.

        Args:
            method: Allocation method ('rank_aggregation', 'inverse_vol', 'equal_weight')
            per_strategy_max: Maximum weight for any single strategy (default 0.40 = 40%).
                              This is a RELATIVE PRE-NORMALIZATION limit, not a hard cap.
                              The cap is enforced on total strategy contribution BEFORE final
                              normalization. After normalization, the final weight may differ
                              (e.g., a strategy capped at 40% pre-norm may end up at 67% post-norm
                              if total pre-norm weights summed to 0.6). This ensures proportional
                              scaling while preventing any single strategy from dominating.
            correlation_threshold: Alert threshold for inter-strategy correlation (default 0.70)
            allow_short_positions: Enable market-neutral portfolios with long/short positions
                                   (default False). When True:
                                   - Negative weights are preserved throughout allocation
                                   - Zero-sum portfolios normalized by GROSS exposure (sum of abs values)
                                   - NET exposure (sum of weights) may be != 1.0
                                   When False:
                                   - Assumes long-only portfolios
                                   - All weights expected to be positive
                                   - Normalizes by NET exposure (sum of weights)

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
        self.allow_short_positions = allow_short_positions

        logger.info(
            "Initialized MultiAlphaAllocator",
            extra={
                "method": method,
                "per_strategy_max": per_strategy_max,
                "correlation_threshold": correlation_threshold,
                "allow_short_positions": allow_short_positions,
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
            # Use safe normalization to handle validation and zero-sum cases
            result = df.select(
                [
                    pl.col("symbol"),
                    pl.col("weight").alias("final_weight"),
                    pl.lit([strategy_id]).alias("contributing_strategies"),
                ]
            )
            # Apply safe normalization (handles validation + division-by-zero)
            result = self._safe_normalize_weights(result, weight_col="final_weight")
            return result

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

            # Rank by |score| descending, preserving direction via weight sign
            # This allows market-neutral strategies (long/short) to work correctly
            #
            # For long-only: weight > 0, sign = +1
            # For shorts: weight < 0, sign = -1
            #
            # Rank by absolute score (magnitude), then apply sign to final weights
            ranked = df.select(
                [
                    pl.col("symbol"),
                    pl.col("score").abs().rank(method="ordinal", descending=True).alias("rank"),
                    # Preserve sign from original weight for market-neutral support
                    pl.when(pl.col("weight") >= 0)
                    .then(pl.lit(1.0))
                    .otherwise(pl.lit(-1.0))
                    .alias("weight_sign"),
                ]
            ).with_columns(
                [
                    # Reciprocal rank: 1 / rank (always positive)
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

        # Step 3: Normalize ranks WITHIN each strategy to get strategy-specific weights
        # This ensures each strategy contributes proportionally based on its internal rankings
        strategy_rank_totals = all_ranks.group_by("strategy_id").agg(
            pl.col("normalized_rank").sum().alias("total_rank")
        )

        weighted_ranks = all_ranks.join(
            strategy_rank_totals, on="strategy_id", how="left"
        ).with_columns((pl.col("normalized_rank") / pl.col("total_rank")).alias("strategy_weight"))

        # Create weighted contributions for caps enforcement
        # Each (symbol, strategy_id) pair has a weight contribution
        # CRITICAL: Apply weight_sign to preserve long/short direction
        weighted_contributions = weighted_ranks.select(
            [
                pl.col("symbol"),
                pl.col("strategy_id"),
                (pl.col("strategy_weight") * pl.col("weight_sign")).alias("weighted_contribution"),
            ]
        )

        # Apply per-strategy caps and aggregate
        # This ensures no single strategy contributes more than per_strategy_max
        result = self._apply_per_strategy_caps_and_aggregate(weighted_contributions)

        # Final normalization to ensure sum = 1.0 (handles market-neutral portfolios safely)
        result = self._safe_normalize_weights(result, weight_col="final_weight")

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

        # Normalize weights WITHIN each strategy to get equal influence
        # For each strategy, ensure its weights sum to 1.0 before combining
        # CRITICAL: Handle market-neutral strategies (zero-sum weights) by using absolute sum
        strategy_totals = all_signals.group_by("strategy_id").agg(
            [
                pl.col("weight").sum().alias("total_weight"),
                pl.col("weight").abs().sum().alias("total_weight_abs"),
            ]
        )

        # For market-neutral strategies (total_weight ≈ 0), use absolute sum
        # For all-zero strategies (both zero), use 1.0 to avoid division by zero
        # Otherwise, use raw sum (preserves long-only behavior)
        strategy_totals = strategy_totals.with_columns(
            pl.when(pl.col("total_weight_abs") < 1e-9)
            .then(pl.lit(1.0))  # All zeros case - prevent division by zero
            .when(pl.col("total_weight").abs() < 1e-9)
            .then(pl.col("total_weight_abs"))  # Market-neutral case
            .otherwise(pl.col("total_weight"))  # Normal case
            .alias("normalizer")
        )

        normalized_signals = all_signals.join(
            strategy_totals.select(["strategy_id", "normalizer"]), on="strategy_id", how="left"
        ).with_columns((pl.col("weight") / pl.col("normalizer")).alias("normalized_weight"))

        # Log market-neutral strategies for visibility
        market_neutral = strategy_totals.filter(pl.col("total_weight").abs() < 1e-9)
        if not market_neutral.is_empty():
            for row in market_neutral.iter_rows(named=True):
                logger.warning(
                    "Market-neutral strategy detected (zero-sum weights), normalizing by absolute weights",
                    extra={
                        "strategy_id": row["strategy_id"],
                        "raw_sum": float(row["total_weight"]),
                        "abs_sum": float(row["total_weight_abs"]),
                    },
                )

        # Now each strategy contributes proportionally to its internal weights
        # Rename for consistency with caps method
        weighted_contributions = normalized_signals.select(
            [
                pl.col("symbol"),
                pl.col("strategy_id"),
                pl.col("normalized_weight").alias("weighted_contribution"),
            ]
        )

        # Apply per-strategy caps and aggregate
        # This ensures no single strategy contributes more than per_strategy_max
        result = self._apply_per_strategy_caps_and_aggregate(weighted_contributions)

        # Final normalization (handles market-neutral portfolios safely)
        result = self._safe_normalize_weights(result, weight_col="final_weight")

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

        # Apply per-strategy caps and aggregate
        # This ensures no single strategy contributes more than per_strategy_max
        result = self._apply_per_strategy_caps_and_aggregate(all_weighted)

        # Final normalization to ensure sum = 1.0 (handles market-neutral portfolios safely)
        result = self._safe_normalize_weights(result, weight_col="final_weight")

        # Drop internal column for consistent output schema
        result = result.select(["symbol", "final_weight", "contributing_strategies"])

        logger.info(
            "Inverse volatility allocation complete",
            extra={
                "num_symbols": len(result),
                "total_weight": result["final_weight"].sum(),
                "max_weight": result["final_weight"].max(),
            },
        )

        return result

    def _safe_normalize_weights(
        self, df: pl.DataFrame, weight_col: str = "final_weight"
    ) -> pl.DataFrame:
        """
        Normalize weights safely, handling zero-sum and market-neutral portfolios.

        This method implements proper NET vs GROSS exposure normalization:
        - NET exposure = sum of weights (can be 0 for market-neutral)
        - GROSS exposure = sum of absolute weights (always >= 0)

        For market-neutral portfolios (where long/short positions cancel to zero sum),
        normalizes by GROSS exposure (sum of absolute weights) instead of NET exposure
        (raw sum) to prevent division by zero.

        Args:
            df: DataFrame containing weights to normalize
            weight_col: Name of the weight column to normalize (default: "final_weight")

        Returns:
            DataFrame with normalized weights

        Example:
            Market-neutral portfolio (zero NET exposure):
            Input:  AAPL +0.5, MSFT -0.5  (NET = 0, GROSS = 1.0)
            Output: AAPL +0.5, MSFT -0.5  (normalized by GROSS = 1.0)

            Long-only portfolio:
            Input:  AAPL 0.6, MSFT 0.4  (NET = 1.0, GROSS = 1.0)
            Output: AAPL 0.6, MSFT 0.4  (normalized by NET = 1.0)

        Note:
            - Uses 1e-9 threshold to detect near-zero NET exposure
            - Returns all zeros if both NET and GROSS exposure are near-zero
            - Preserves sign of original weights (critical for short positions)
            - Validates that short positions are only used when allow_short_positions=True

        Raises:
            ValueError: If negative weights detected when allow_short_positions=False
        """
        total = df[weight_col].sum()  # NET exposure
        total_abs = df[weight_col].abs().sum()  # GROSS exposure

        # Validation: Check for negative weights when short positions not allowed
        if not self.allow_short_positions:
            has_negative = (df[weight_col] < -1e-9).any()
            if has_negative:
                raise ValueError(
                    "Negative weights detected but allow_short_positions=False. "
                    "Set allow_short_positions=True to enable market-neutral portfolios."
                )

        # Case 1: Near-zero NET exposure (market-neutral portfolio)
        # Normalize by GROSS exposure to prevent division by zero
        if abs(total) < 1e-9:
            logger.warning(
                "Zero-sum portfolio detected (market-neutral), normalizing by GROSS exposure",
                extra={
                    "net_exposure": float(total),
                    "gross_exposure": float(total_abs),
                    "normalization_method": "gross",
                    "num_symbols": len(df),
                    "allow_short_positions": self.allow_short_positions,
                },
            )

            # Case 1a: All weights are zero (edge case)
            if total_abs < 1e-9:
                logger.warning(
                    "All weights are zero, returning zero allocation",
                    extra={"num_symbols": len(df)},
                )
                return df.with_columns(pl.lit(0.0).alias(weight_col))

            # Case 1b: Normalize by GROSS exposure (preserves signs)
            return df.with_columns((pl.col(weight_col) / total_abs).alias(weight_col))

        # Case 2: Non-zero NET exposure (long-only, net-long, or net-short portfolio)
        # Normalize by absolute value of NET exposure to preserve sign direction
        # For net-short portfolios, dividing by negative total would flip signs (BUG)
        # Example: [-0.6, -0.4] / -1.0 = [+0.6, +0.4] (WRONG)
        # Fixed: [-0.6, -0.4] / abs(-1.0) = [-0.6, -0.4] (CORRECT)
        return df.with_columns((pl.col(weight_col) / abs(total)).alias(weight_col))

    def _apply_per_strategy_caps_and_aggregate(
        self, weighted_contributions: pl.DataFrame
    ) -> pl.DataFrame:
        """
        Apply per-strategy concentration limits and aggregate to final weights.

        Enforces that no single strategy contributes more than per_strategy_max to the
        TOTAL allocation across all symbols. This preserves the relative proportions from
        the allocation method while preventing over-concentration in any one strategy.

        CRITICAL: The cap is enforced on the total contribution from each strategy, not
        per-symbol. This prevents a strategy from exceeding the limit by spreading across
        multiple symbols (e.g., 35% AAPL + 35% MSFT = 70% total > 40% cap).

        Args:
            weighted_contributions: DataFrame with columns:
                - symbol (str): Symbol ticker
                - strategy_id (str): Strategy identifier
                - weighted_contribution (float): Contribution from this strategy to this symbol

        Returns:
            DataFrame with columns:
                - symbol (str): Symbol ticker
                - final_weight (float): Aggregated weight after applying caps (not normalized)
                - contributing_strategies (list[str]): Strategies that contributed to this symbol

        Example:
            Input:
            symbol  | strategy_id     | weighted_contribution
            --------|-----------------|----------------------
            AAPL    | alpha_baseline  | 0.30
            MSFT    | alpha_baseline  | 0.30  (total for alpha_baseline = 0.60 > 0.40 cap!)
            GOOGL   | momentum        | 0.25

            Output (with per_strategy_max=0.40):
            - alpha_baseline total exceeds cap: 0.60 → scale by 0.40/0.60 = 0.6667
            - AAPL: 0.30 * 0.6667 = 0.20
            - MSFT: 0.30 * 0.6667 = 0.20
            - GOOGL: 0.25 (no cap)
        """
        if weighted_contributions.is_empty():
            return pl.DataFrame(
                schema={
                    "symbol": pl.Utf8,
                    "final_weight": pl.Float64,
                    "contributing_strategies": pl.List(pl.Utf8),
                }
            )

        # Step 1: Calculate total contribution per strategy across ALL symbols
        strategy_totals = weighted_contributions.group_by("strategy_id").agg(
            pl.col("weighted_contribution").sum().alias("total_contribution")
        )

        # Step 2: Identify strategies that exceed the cap and calculate scale factors
        # CRITICAL: Use absolute values for cap check to handle short exposures correctly
        # Example: short strategy with -80% exposure should be capped just like +80%
        strategy_totals = strategy_totals.with_columns(
            pl.when(pl.col("total_contribution").abs() > self.per_strategy_max)
            .then(self.per_strategy_max / pl.col("total_contribution").abs())
            .otherwise(pl.lit(1.0))
            .alias("scale_factor")
        )

        # Log strategies that will be scaled
        capped_strategies = strategy_totals.filter(pl.col("scale_factor") < 1.0)
        if not capped_strategies.is_empty():
            for row in capped_strategies.iter_rows(named=True):
                logger.warning(
                    "Applying per-strategy concentration cap",
                    extra={
                        "strategy_id": row["strategy_id"],
                        "original_total": round(row["total_contribution"], 4),
                        "capped_total": round(self.per_strategy_max, 4),
                        "scale_factor": round(row["scale_factor"], 4),
                        "per_strategy_max": self.per_strategy_max,
                    },
                )

        # Step 3: Join scale factors back to weighted_contributions
        scaled = weighted_contributions.join(
            strategy_totals.select(["strategy_id", "scale_factor"]),
            on="strategy_id",
            how="left",
        )

        # Step 4: Apply scale factor to each contribution
        scaled = scaled.with_columns(
            (pl.col("weighted_contribution") * pl.col("scale_factor")).alias("capped_contribution")
        )

        # Step 5: Aggregate by symbol
        result = (
            scaled.group_by("symbol")
            .agg(
                [
                    pl.col("capped_contribution").sum().alias("final_weight"),
                    pl.col("strategy_id").alias("contributing_strategies"),
                ]
            )
            .sort("final_weight", descending=True)
        )

        return result

    def check_correlation(
        self, recent_returns: dict[str, pl.DataFrame]
    ) -> dict[tuple[str, str], float]:
        """
        Check inter-strategy correlation and emit alerts if above threshold.

        Calculates pairwise Pearson correlations between strategy returns. Emits warning
        logs if any pair exceeds correlation_threshold, indicating potential redundancy
        or lack of diversification benefit.

        Args:
            recent_returns: Dictionary mapping strategy_id to DataFrames with columns:
                - date (date or datetime): Trading date
                - return (float): Strategy return for that date

                Each DataFrame should contain aligned dates for proper correlation calculation.

        Returns:
            Dictionary mapping (strategy1, strategy2) -> correlation_coefficient
            Keys are tuples of strategy IDs (sorted alphabetically for consistency)
            Values are Pearson correlation coefficients in range [-1, 1]

        Raises:
            ValueError: If recent_returns is empty or if DataFrames have incompatible schemas

        Example:
            >>> returns = {
            ...     'alpha_baseline': pl.DataFrame({'date': [...], 'return': [...]}),
            ...     'momentum': pl.DataFrame({'date': [...], 'return': [...]})
            ... }
            >>> correlations = allocator.check_correlation(returns)
            >>> assert ('alpha_baseline', 'momentum') in correlations
        """
        if not recent_returns:
            raise ValueError("recent_returns cannot be empty")

        strategy_ids = sorted(recent_returns.keys())
        if len(strategy_ids) < 2:
            # Need at least 2 strategies for correlation
            logger.debug(
                "Correlation check skipped - need at least 2 strategies",
                extra={"num_strategies": len(strategy_ids)},
            )
            return {}

        # Validate schema for all DataFrames
        for strategy_id, df in recent_returns.items():
            if df.is_empty():
                raise ValueError(f"recent_returns['{strategy_id}'] is empty")
            if "date" not in df.columns or "return" not in df.columns:
                raise ValueError(
                    f"recent_returns['{strategy_id}'] missing required columns "
                    f"(expected: date, return; got: {df.columns})"
                )

        # Merge all returns on date for aligned correlation calculation
        # Start with first strategy
        merged = recent_returns[strategy_ids[0]].select(
            [pl.col("date"), pl.col("return").alias(f"return_{strategy_ids[0]}")]
        )

        # Join remaining strategies
        for strategy_id in strategy_ids[1:]:
            merged = merged.join(
                recent_returns[strategy_id].select(
                    [pl.col("date"), pl.col("return").alias(f"return_{strategy_id}")]
                ),
                on="date",
                how="inner",  # Only use dates present in all strategies
            )

        if merged.is_empty():
            logger.warning(
                "No overlapping dates across strategies - cannot calculate correlation",
                extra={"strategies": strategy_ids},
            )
            return {}

        if len(merged) < 2:
            logger.warning(
                "Insufficient overlapping data points for correlation",
                extra={"num_points": len(merged), "strategies": strategy_ids},
            )
            return {}

        # Calculate pairwise correlations
        correlations: dict[tuple[str, str], float] = {}
        high_correlations: list[tuple[str, str, float]] = []

        for i in range(len(strategy_ids)):
            for j in range(i + 1, len(strategy_ids)):
                strat1 = strategy_ids[i]
                strat2 = strategy_ids[j]

                # Calculate Pearson correlation
                corr_result = merged.select(
                    pl.corr(f"return_{strat1}", f"return_{strat2}").alias("correlation")
                )
                correlation = corr_result["correlation"][0]

                # Handle NaN (can occur if all returns are identical or zero variance)
                if correlation is None or not math.isfinite(correlation):
                    logger.warning(
                        "Invalid correlation (NaN/Inf) - likely zero variance in returns",
                        extra={"strategy1": strat1, "strategy2": strat2},
                    )
                    correlation = 0.0

                correlations[(strat1, strat2)] = correlation

                # Check threshold
                if abs(correlation) > self.correlation_threshold:
                    high_correlations.append((strat1, strat2, correlation))

        # Emit alerts for high correlations
        if high_correlations:
            for strat1, strat2, corr in high_correlations:
                logger.warning(
                    "High inter-strategy correlation detected",
                    extra={
                        "strategy1": strat1,
                        "strategy2": strat2,
                        "correlation": round(corr, 4),
                        "threshold": self.correlation_threshold,
                        "risk": "Strategies may lack diversification - consider reducing allocation to one",
                    },
                )

        logger.info(
            "Correlation check complete",
            extra={
                "num_pairs": len(correlations),
                "num_high_correlations": len(high_correlations),
                "max_correlation": (
                    round(max(abs(c) for c in correlations.values()), 4) if correlations else None
                ),
            },
        )

        return correlations

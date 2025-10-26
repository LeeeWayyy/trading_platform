"""
Core ensemble logic for combining multiple strategy signals.

This module provides the fundamental signal combination algorithms that aggregate
predictions from multiple trading strategies into a single ensemble prediction.

Each combination method has different characteristics suitable for different
market conditions and risk preferences.
"""

from enum import Enum
from typing import Literal, cast

import polars as pl


class CombinationMethod(str, Enum):
    """
    Available methods for combining strategy signals.

    Attributes:
        WEIGHTED_AVERAGE: Linear combination using strategy weights
                         Best for: Balanced approach, gradual transitions
                         Risk: Medium

        MAJORITY_VOTE: Take action when >50% of strategies agree
                      Best for: Reducing false positives
                      Risk: Low-Medium

        UNANIMOUS: Only trade when ALL strategies agree
                  Best for: Very conservative, high-confidence trades
                  Risk: Very Low

        CONFIDENCE_WEIGHTED: Weight by individual strategy confidence
                           Best for: Adaptive to strategy performance
                           Risk: Medium-High

        MAX_CONFIDENCE: Use signal from most confident strategy
                       Best for: Opportunistic, high-conviction trades
                       Risk: High
    """

    WEIGHTED_AVERAGE = "weighted_average"
    MAJORITY_VOTE = "majority_vote"
    UNANIMOUS = "unanimous"
    CONFIDENCE_WEIGHTED = "confidence_weighted"
    MAX_CONFIDENCE = "max_confidence"


def combine_signals(
    signals: pl.DataFrame,
    method: CombinationMethod | Literal[
        "weighted_average", "majority_vote", "unanimous",
        "confidence_weighted", "max_confidence"
    ] = CombinationMethod.WEIGHTED_AVERAGE,
    weights: dict[str, float] | None = None,
    signal_threshold: float = 0.3,
) -> pl.DataFrame:
    """
    Combine signals from multiple strategies into ensemble predictions.

    This is the main entry point for signal combination. Takes a DataFrame with
    predictions from multiple strategies and combines them using the specified method.

    Args:
        signals: DataFrame with columns:
                - symbol: Stock symbol
                - date: Trading date
                - strategy_{name}_signal: Signal from strategy (-1/0/+1)
                - strategy_{name}_confidence: Confidence score (0-1)
                Must have at least 2 strategies to combine.

        method: Combination method to use (default: weighted_average)

        weights: Optional strategy weights for weighted methods.
                Keys: strategy names (e.g., "mean_reversion", "momentum")
                Values: Weight (must sum to 1.0)
                If None, uses equal weights.

        signal_threshold: Threshold for continuous signal → discrete conversion.
                         Raw signal > threshold → BUY (+1)
                         Raw signal < -threshold → SELL (-1)
                         Otherwise → HOLD (0)
                         Default: 0.3

    Returns:
        DataFrame with original columns plus:
        - ensemble_signal: Combined signal (-1/0/+1)
        - ensemble_confidence: Combined confidence (0-1)
        - ensemble_method: Method used for combination

    Raises:
        ValueError: If signals missing required columns or has < 2 strategies
        ValueError: If weights don't sum to 1.0 or missing strategies
        ValueError: If signal_threshold not in [0, 1]
        ValueError: If confidence values not in [0, 1]

    Example:
        >>> import polars as pl
        >>> signals = pl.DataFrame({
        ...     "symbol": ["AAPL", "AAPL"],
        ...     "date": [date(2024, 1, 1), date(2024, 1, 2)],
        ...     "strategy_mean_reversion_signal": [1, -1],
        ...     "strategy_mean_reversion_confidence": [0.8, 0.7],
        ...     "strategy_momentum_signal": [1, 1],
        ...     "strategy_momentum_confidence": [0.6, 0.5],
        ... })
        >>> result = combine_signals(signals, method="weighted_average")
        >>> print(result["ensemble_signal"])
        shape: (2,)
        Series: 'ensemble_signal' [i8]
        [
            1
            0
        ]

    Notes:
        - Signals should be normalized to -1 (sell), 0 (hold), +1 (buy)
        - Confidence scores should be in range [0, 1]
        - Missing signals/confidence are treated as 0 (neutral)
        - Each method has different risk/return characteristics
    """
    # Validate threshold range
    if not 0.0 <= signal_threshold <= 1.0:
        raise ValueError(
            f"signal_threshold must be in [0, 1], got {signal_threshold}"
        )

    # Convert method to enum if string
    if isinstance(method, str):
        method = CombinationMethod(method)

    # Validate input
    _validate_signals_dataframe(signals)

    # Extract strategy names from columns
    strategy_names = _extract_strategy_names(signals)

    if len(strategy_names) < 2:
        raise ValueError(
            f"Need at least 2 strategies to combine, got {len(strategy_names)}"
        )

    # Validate and normalize weights
    if weights is None:
        weights = {name: 1.0 / len(strategy_names) for name in strategy_names}
    else:
        weights = _validate_weights(weights, strategy_names)

    # Apply combination method
    if method == CombinationMethod.WEIGHTED_AVERAGE:
        result = _weighted_average(signals, strategy_names, weights, signal_threshold)
    elif method == CombinationMethod.MAJORITY_VOTE:
        result = _majority_vote(signals, strategy_names)
    elif method == CombinationMethod.UNANIMOUS:
        result = _unanimous(signals, strategy_names)
    elif method == CombinationMethod.CONFIDENCE_WEIGHTED:
        result = _confidence_weighted(signals, strategy_names, signal_threshold)
    elif method == CombinationMethod.MAX_CONFIDENCE:
        result = _max_confidence(signals, strategy_names)
    else:
        raise ValueError(f"Unknown combination method: {method}")

    # Add metadata
    result = result.with_columns(pl.lit(method.value).alias("ensemble_method"))

    return result


def _validate_signals_dataframe(signals: pl.DataFrame) -> None:
    """
    Validate that signals DataFrame has required structure.

    Checks:
    - Required columns (symbol, date) present
    - Each signal column has corresponding confidence column
    - Confidence values are in valid range [0, 1]

    Raises:
        ValueError: If validation fails
    """
    # Check required columns
    required_cols = {"symbol", "date"}
    missing = required_cols - set(signals.columns)
    if missing:
        raise ValueError(f"Signals missing required columns: {missing}")

    # Check that each signal column has corresponding confidence column
    signal_cols = [col for col in signals.columns if col.startswith("strategy_") and col.endswith("_signal")]
    for signal_col in signal_cols:
        # Extract strategy name (strategy_{name}_signal → {name})
        strategy_name = signal_col.replace("strategy_", "").replace("_signal", "")
        conf_col = f"strategy_{strategy_name}_confidence"

        if conf_col not in signals.columns:
            raise ValueError(
                f"Signal column '{signal_col}' found but missing corresponding "
                f"confidence column '{conf_col}'"
            )

        # Validate confidence values are in [0, 1] range
        # Only check non-null values
        conf_values = signals[conf_col].drop_nulls()
        if len(conf_values) > 0:
            min_conf = conf_values.min()
            max_conf = conf_values.max()

            # Type narrowing for mypy (confidence values should be numeric)
            if min_conf is not None and max_conf is not None:
                # Polars min/max return numeric types for numeric columns
                min_val = cast(float, min_conf)
                max_val = cast(float, max_conf)

                if min_val < 0.0 or max_val > 1.0:
                    raise ValueError(
                        f"Confidence column '{conf_col}' has values outside [0, 1] range: "
                        f"min={min_val}, max={max_val}"
                    )


def _extract_strategy_names(signals: pl.DataFrame) -> list[str]:
    """
    Extract strategy names from signal column names.

    Looks for columns matching pattern: strategy_{name}_signal
    """
    strategy_names = []
    for col in signals.columns:
        if col.startswith("strategy_") and col.endswith("_signal"):
            # Extract strategy name from middle
            name = col.replace("strategy_", "").replace("_signal", "")
            strategy_names.append(name)

    return sorted(set(strategy_names))


def _validate_weights(
    weights: dict[str, float], strategy_names: list[str]
) -> dict[str, float]:
    """
    Validate and normalize strategy weights.

    Ensures:
    - All strategies have weights
    - Weights sum to 1.0 (within tolerance)
    - All weights are non-negative
    """
    # Check all strategies present
    missing = set(strategy_names) - set(weights.keys())
    if missing:
        raise ValueError(f"Weights missing strategies: {missing}")

    extra = set(weights.keys()) - set(strategy_names)
    if extra:
        raise ValueError(f"Weights have unknown strategies: {extra}")

    # Check non-negative
    negative = {k: v for k, v in weights.items() if v < 0}
    if negative:
        raise ValueError(f"Weights must be non-negative: {negative}")

    # Check sum to 1.0
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Weights must sum to 1.0, got {total}")

    return weights


def _weighted_average(
    signals: pl.DataFrame,
    strategy_names: list[str],
    weights: dict[str, float],
    threshold: float = 0.3,
) -> pl.DataFrame:
    """
    Combine signals using weighted average of strategy signals.

    Formula:
        ensemble_signal = sum(signal_i * weight_i)
        ensemble_confidence = sum(confidence_i * weight_i)

    Threshold:
        signal > threshold → +1 (buy)
        signal < -threshold → -1 (sell)
        otherwise → 0 (hold)

    Args:
        signals: DataFrame with strategy signals and confidences
        strategy_names: List of strategy names to combine
        weights: Weight for each strategy (must sum to 1.0)
        threshold: Threshold for signal discretization (default: 0.3)

    Example:
        Strategy A: signal=+1, confidence=0.8, weight=0.6
        Strategy B: signal=-1, confidence=0.5, weight=0.4
        Result: signal = 1*0.6 + (-1)*0.4 = 0.2 → 0 (neutral, below threshold)
                confidence = 0.8*0.6 + 0.5*0.4 = 0.68
    """
    # Build weighted sum expressions
    signal_expr = pl.lit(0.0)
    confidence_expr = pl.lit(0.0)

    for name in strategy_names:
        signal_col = f"strategy_{name}_signal"
        conf_col = f"strategy_{name}_confidence"
        weight = weights[name]

        # Add weighted components (fill_null to handle missing)
        signal_expr = signal_expr + (
            pl.col(signal_col).fill_null(0.0) * weight
        )
        confidence_expr = confidence_expr + (
            pl.col(conf_col).fill_null(0.0) * weight
        )

    # Apply threshold to convert continuous signal to discrete
    df = signals.with_columns([
        signal_expr.alias("_raw_signal"),
        confidence_expr.alias("ensemble_confidence"),
    ])

    df = df.with_columns(
        pl.when(pl.col("_raw_signal") > threshold)
        .then(pl.lit(1))
        .when(pl.col("_raw_signal") < -threshold)
        .then(pl.lit(-1))
        .otherwise(pl.lit(0))
        .cast(pl.Int8)
        .alias("ensemble_signal")
    )

    return df.drop("_raw_signal")


def _majority_vote(
    signals: pl.DataFrame, strategy_names: list[str]
) -> pl.DataFrame:
    """
    Combine signals using majority voting.

    Rules:
    - If >50% of strategies signal BUY (+1), ensemble signals BUY
    - If >50% of strategies signal SELL (-1), ensemble signals SELL
    - Otherwise, ensemble signals HOLD (0)

    Confidence:
    - Fraction of strategies that agree with majority decision

    Example:
        3 strategies: [+1, +1, -1]
        Majority: +1 (2 out of 3)
        Result: signal=+1, confidence=0.67
    """
    # Count buy/sell/hold votes
    buy_expr = pl.lit(0)
    sell_expr = pl.lit(0)
    total_expr = pl.lit(0)
    conf_sum = pl.lit(0.0)

    for name in strategy_names:
        signal_col = f"strategy_{name}_signal"
        conf_col = f"strategy_{name}_confidence"

        # Count votes (treat null as 0/hold)
        buy_expr = buy_expr + (pl.col(signal_col).fill_null(0) == 1).cast(pl.Int32)
        sell_expr = sell_expr + (pl.col(signal_col).fill_null(0) == -1).cast(pl.Int32)
        total_expr = total_expr + (pl.col(signal_col).is_not_null()).cast(pl.Int32)

        # Sum confidences for averaging
        conf_sum = conf_sum + pl.col(conf_col).fill_null(0.0)

    df = signals.with_columns([
        buy_expr.alias("_buy_votes"),
        sell_expr.alias("_sell_votes"),
        total_expr.alias("_total_votes"),
        (conf_sum / len(strategy_names)).alias("ensemble_confidence"),
    ])

    # Determine majority (need >50%)
    df = df.with_columns(
        pl.when(pl.col("_buy_votes") > pl.col("_total_votes") / 2)
        .then(pl.lit(1))
        .when(pl.col("_sell_votes") > pl.col("_total_votes") / 2)
        .then(pl.lit(-1))
        .otherwise(pl.lit(0))
        .cast(pl.Int8)
        .alias("ensemble_signal")
    )

    # Adjust confidence based on agreement
    df = df.with_columns(
        pl.when(pl.col("ensemble_signal") == 1)
        .then(pl.col("_buy_votes").cast(pl.Float64) / pl.col("_total_votes"))
        .when(pl.col("ensemble_signal") == -1)
        .then(pl.col("_sell_votes").cast(pl.Float64) / pl.col("_total_votes"))
        .otherwise(pl.lit(0.5))  # Neutral = low confidence
        .alias("_agreement_conf")
    )

    # Combine confidence metrics
    df = df.with_columns(
        ((pl.col("ensemble_confidence") + pl.col("_agreement_conf")) / 2.0)
        .alias("ensemble_confidence")
    )

    return df.drop(["_buy_votes", "_sell_votes", "_total_votes", "_agreement_conf"])


def _unanimous(signals: pl.DataFrame, strategy_names: list[str]) -> pl.DataFrame:
    """
    Combine signals requiring unanimous agreement.

    Rules:
    - Only signal BUY if ALL strategies signal BUY
    - Only signal SELL if ALL strategies signal SELL
    - Otherwise HOLD

    Confidence:
    - Average of all strategy confidences when unanimous
    - 0.0 when not unanimous

    Example:
        3 strategies: [+1, +1, +1] → signal=+1, confidence=avg(conf)
        3 strategies: [+1, +1, 0] → signal=0, confidence=0.0
    """
    # Check if all strategies agree
    first_signal = f"strategy_{strategy_names[0]}_signal"
    all_agree_expr = pl.lit(True)

    for name in strategy_names[1:]:
        signal_col = f"strategy_{name}_signal"
        all_agree_expr = all_agree_expr & (
            pl.col(signal_col).fill_null(0) == pl.col(first_signal).fill_null(0)
        )

    # Calculate average confidence
    conf_sum = pl.lit(0.0)
    for name in strategy_names:
        conf_col = f"strategy_{name}_confidence"
        conf_sum = conf_sum + pl.col(conf_col).fill_null(0.0)

    avg_conf = conf_sum / len(strategy_names)

    # If unanimous, use first strategy's signal; otherwise hold
    df = signals.with_columns([
        all_agree_expr.alias("_unanimous"),
        avg_conf.alias("_avg_conf"),
    ])

    df = df.with_columns(
        pl.when(pl.col("_unanimous"))
        .then(pl.col(first_signal).fill_null(0))
        .otherwise(pl.lit(0))
        .cast(pl.Int8)
        .alias("ensemble_signal")
    )

    df = df.with_columns(
        pl.when(pl.col("_unanimous"))
        .then(pl.col("_avg_conf"))
        .otherwise(pl.lit(0.0))
        .alias("ensemble_confidence")
    )

    return df.drop(["_unanimous", "_avg_conf"])


def _confidence_weighted(
    signals: pl.DataFrame, strategy_names: list[str], threshold: float = 0.3
) -> pl.DataFrame:
    """
    Combine signals weighted by individual strategy confidence scores.

    Formula:
        ensemble_signal = sum(signal_i * confidence_i) / sum(confidence_i)
        ensemble_confidence = sum(confidence_i) / N

    This dynamically weights strategies based on their current confidence,
    giving more influence to strategies with higher conviction.

    Args:
        signals: DataFrame with strategy signals and confidences
        strategy_names: List of strategy names to combine
        threshold: Threshold for signal discretization (default: 0.3)

    Example:
        Strategy A: signal=+1, confidence=0.9
        Strategy B: signal=-1, confidence=0.3
        Result: signal = (1*0.9 + (-1)*0.3) / (0.9+0.3) = 0.6/1.2 = 0.5 → +1
                confidence = (0.9+0.3) / 2 = 0.6
    """
    # Build confidence-weighted expressions
    weighted_signal = pl.lit(0.0)
    total_confidence = pl.lit(0.0)
    conf_sum = pl.lit(0.0)

    for name in strategy_names:
        signal_col = f"strategy_{name}_signal"
        conf_col = f"strategy_{name}_confidence"

        signal = pl.col(signal_col).fill_null(0.0)
        confidence = pl.col(conf_col).fill_null(0.0)

        weighted_signal = weighted_signal + (signal * confidence)
        total_confidence = total_confidence + confidence
        conf_sum = conf_sum + confidence

    # Avoid division by zero
    df = signals.with_columns([
        (weighted_signal / total_confidence.clip(1e-6, None)).alias("_raw_signal"),
        (conf_sum / len(strategy_names)).alias("ensemble_confidence"),
    ])

    # Apply threshold
    df = df.with_columns(
        pl.when(pl.col("_raw_signal") > threshold)
        .then(pl.lit(1))
        .when(pl.col("_raw_signal") < -threshold)
        .then(pl.lit(-1))
        .otherwise(pl.lit(0))
        .cast(pl.Int8)
        .alias("ensemble_signal")
    )

    return df.drop("_raw_signal")


def _max_confidence(
    signals: pl.DataFrame, strategy_names: list[str]
) -> pl.DataFrame:
    """
    Use signal from strategy with highest confidence.

    Rules:
    - For each row, find strategy with maximum confidence
    - Use that strategy's signal and confidence
    - Ties: use first strategy in alphabetical order

    Example:
        Strategy A: signal=+1, confidence=0.6
        Strategy B: signal=-1, confidence=0.9
        Result: signal=-1, confidence=0.9 (use Strategy B)
    """
    # Create list of (signal, confidence) pairs for each strategy
    strategy_cols = []
    for name in strategy_names:
        signal_col = f"strategy_{name}_signal"
        conf_col = f"strategy_{name}_confidence"

        strategy_cols.append((signal_col, conf_col))

    # Find max confidence across all strategies
    max_conf_expr = pl.max_horizontal(
        [pl.col(conf_col).fill_null(0.0) for _, conf_col in strategy_cols]
    )

    df = signals.with_columns(max_conf_expr.alias("_max_conf"))

    # Find which strategy has max confidence (first match wins ties)
    signal_expr = pl.lit(0).cast(pl.Int8)
    for signal_col, conf_col in strategy_cols:
        signal_expr = (
            pl.when(pl.col(conf_col).fill_null(0.0) == pl.col("_max_conf"))
            .then(pl.col(signal_col).fill_null(0))
            .otherwise(signal_expr)
        )

    df = df.with_columns([
        signal_expr.alias("ensemble_signal"),
        pl.col("_max_conf").alias("ensemble_confidence"),
    ])

    return df.drop("_max_conf")

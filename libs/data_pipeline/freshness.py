"""
Freshness checking for market data.

This module validates that data is recent enough to be used for trading decisions.
Stale data can lead to executing trades based on outdated information.

See /docs/CONCEPTS/corporate-actions.md and ADR-0001 for context.
"""

from datetime import UTC, datetime
from typing import Literal

import polars as pl

from libs.common.exceptions import StalenessError

# Type alias for check modes
CheckMode = Literal["latest", "oldest", "median", "per_symbol"]


def check_freshness(
    df: pl.DataFrame,
    max_age_minutes: int = 30,
    check_mode: CheckMode = "latest",
    min_fresh_pct: float = 0.9,
) -> None:
    """
    Validate that data is fresh enough for trading.

    Checks timestamps in the data and ensures they're not older than the
    configured threshold. This prevents trading on stale data that could
    lead to incorrect decisions.

    Args:
        df: DataFrame with 'timestamp' column (must be timezone-aware UTC)
        max_age_minutes: Maximum acceptable age in minutes (default: 30)
        check_mode: How to evaluate freshness (default: "latest")
            - "latest": Check only the most recent timestamp (max) - original behavior
            - "oldest": Check the oldest timestamp (min) - catches any stale data
            - "median": Check the median timestamp - robust to outliers
            - "per_symbol": Check freshness per symbol - requires 'symbol' column
        min_fresh_pct: Minimum percentage of fresh symbols for per_symbol mode (default: 0.9)

    Raises:
        StalenessError: If freshness check fails based on mode
        ValueError: If 'timestamp' column is missing, not timezone-aware, or
                   'symbol' column missing when using per_symbol mode

    Example:
        >>> # Fresh data (within 30 minutes)
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "timestamp": [datetime.now(timezone.utc)]
        ... })
        >>> check_freshness(df, max_age_minutes=30)  # Passes (default mode="latest")

        >>> # Mixed fresh/stale data - mode matters!
        >>> df_mixed = pl.DataFrame({
        ...     "symbol": ["AAPL", "MSFT"],
        ...     "timestamp": [datetime.now(timezone.utc), two_hours_ago]
        ... })
        >>> check_freshness(df_mixed, check_mode="latest")  # Passes (AAPL is fresh)
        >>> check_freshness(df_mixed, check_mode="oldest")  # Fails (MSFT is stale)

    Notes:
        - All timestamps must be UTC (timezone-aware)
        - Default mode "latest" maintains backwards compatibility
        - Use "oldest" or "per_symbol" to catch partially stale datasets
        - For per_symbol mode, min_fresh_pct=0.9 means 90% of symbols must be fresh

    See Also:
        - ADR-0001: Data Pipeline Architecture (freshness threshold rationale)
        - config/settings.py: DATA_FRESHNESS_MINUTES configuration
    """
    # Validate inputs
    if "timestamp" not in df.columns:
        raise ValueError(
            "DataFrame must have 'timestamp' column. " "Available columns: " + ", ".join(df.columns)
        )

    if df.is_empty():
        raise ValueError("Cannot check freshness of empty DataFrame")

    # Validate per_symbol mode requirements
    if check_mode == "per_symbol" and "symbol" not in df.columns:
        raise ValueError(
            "per_symbol mode requires 'symbol' column. "
            "Available columns: " + ", ".join(df.columns)
        )

    # Check if timestamp is timezone-aware
    # Polars stores timezone in dtype: pl.Datetime(time_unit, time_zone)
    timestamp_dtype = df["timestamp"].dtype
    if not hasattr(timestamp_dtype, "time_zone") or timestamp_dtype.time_zone is None:
        raise ValueError(
            "Timestamp column must be timezone-aware (UTC). "
            f"Got dtype: {timestamp_dtype}. "
            "Use: pl.col('timestamp').dt.replace_time_zone('UTC')"
        )

    # Get current time in UTC
    now = datetime.now(UTC)

    # Handle per_symbol mode specially
    if check_mode == "per_symbol":
        _check_freshness_per_symbol(df, max_age_minutes, min_fresh_pct, now)
        return

    # Get timestamp to check based on mode
    if check_mode == "latest":
        check_timestamp = df["timestamp"].max()
        mode_desc = "latest"
    elif check_mode == "oldest":
        check_timestamp = df["timestamp"].min()
        mode_desc = "oldest"
    elif check_mode == "median":
        check_timestamp = df["timestamp"].median()
        mode_desc = "median"
    else:
        raise ValueError(f"Invalid check_mode: {check_mode}")

    # Convert Polars datetime to Python datetime for comparison
    # Polars aggregate functions return scalar values, but mypy types them as Any
    # We need type narrowing to satisfy strict type checking
    if not isinstance(check_timestamp, datetime):
        raise ValueError(
            f"Expected datetime from timestamp.{mode_desc}(), got {type(check_timestamp).__name__}"
        )
    check_dt: datetime = check_timestamp

    # Calculate age in seconds
    age_seconds = (now - check_dt).total_seconds()
    age_minutes = age_seconds / 60

    # Check if data is stale
    if age_minutes > max_age_minutes:
        raise StalenessError(
            f"Data is {age_minutes:.1f} minutes old ({mode_desc} timestamp), "
            f"exceeds threshold of {max_age_minutes} minutes. "
            f"Timestamp: {check_dt.isoformat()}, "
            f"Current time: {now.isoformat()}"
        )


def _check_freshness_per_symbol(
    df: pl.DataFrame,
    max_age_minutes: int,
    min_fresh_pct: float,
    now: datetime,
) -> None:
    """
    Check freshness per symbol and ensure minimum percentage are fresh.

    Args:
        df: DataFrame with 'timestamp' and 'symbol' columns
        max_age_minutes: Maximum acceptable age in minutes
        min_fresh_pct: Minimum percentage of symbols that must be fresh (0-1)
        now: Current UTC time

    Raises:
        StalenessError: If fewer than min_fresh_pct symbols are fresh
    """
    # Get latest timestamp per symbol and calculate age vectorized
    # Gemini LOW fix: Use vectorized Polars operations instead of iter_rows
    symbol_freshness = (
        df.group_by("symbol")
        .agg(pl.col("timestamp").max().alias("latest_timestamp"))
        .with_columns(
            # Calculate age in minutes using vectorized datetime operations
            ((pl.lit(now) - pl.col("latest_timestamp")).dt.total_seconds() / 60.0)
            .alias("age_minutes")
        )
        .with_columns(
            # Determine if each symbol is fresh
            (pl.col("age_minutes") <= max_age_minutes).alias("is_fresh")
        )
    )

    # Calculate totals using vectorized operations
    total_symbols = symbol_freshness.height
    fresh_symbols = symbol_freshness.filter(pl.col("is_fresh")).height

    fresh_pct = fresh_symbols / total_symbols if total_symbols > 0 else 0.0

    if fresh_pct < min_fresh_pct:
        # Only collect stale symbol details when we need to report an error
        stale_df = symbol_freshness.filter(~pl.col("is_fresh")).sort("age_minutes", descending=True)

        # Format stale symbols for error message (limit to first 5)
        stale_symbols_list: list[str] = []
        for row in stale_df.head(5).iter_rows(named=True):
            symbol = row["symbol"]
            age_minutes = row["age_minutes"]
            if age_minutes is not None:
                stale_symbols_list.append(f"{symbol} ({age_minutes:.1f}m old)")
            else:
                stale_symbols_list.append(f"{symbol} (invalid timestamp)")

        remaining_stale = stale_df.height - 5
        if remaining_stale > 0:
            stale_symbols_list.append(f"... and {remaining_stale} more")

        raise StalenessError(
            f"Only {fresh_pct * 100:.1f}% of symbols are fresh "
            f"({fresh_symbols}/{total_symbols}), "
            f"below threshold of {min_fresh_pct * 100:.1f}%. "
            f"Stale symbols: {', '.join(stale_symbols_list)}"
        )


def check_freshness_safe(
    df: pl.DataFrame,
    max_age_minutes: int = 30,
    check_mode: CheckMode = "latest",
    min_fresh_pct: float = 0.9,
    default_to_stale: bool = True,
) -> tuple[bool, str | None]:
    """
    Non-raising version of check_freshness for conditional logic.

    This is useful when you want to check freshness without exception handling,
    such as in conditional pipelines or monitoring.

    Args:
        df: DataFrame with 'timestamp' column
        max_age_minutes: Maximum acceptable age in minutes
        check_mode: How to evaluate freshness (see check_freshness)
        min_fresh_pct: Minimum percentage of fresh symbols for per_symbol mode
        default_to_stale: If True, treat errors as stale (fail-safe)

    Returns:
        Tuple of (is_fresh, error_message)
        - (True, None) if data is fresh
        - (False, "error message") if data is stale or check failed

    Example:
        >>> is_fresh, msg = check_freshness_safe(df)
        >>> if not is_fresh:
        ...     logger.warning(f"Skipping stale data: {msg}")
        ...     return None
    """
    try:
        check_freshness(df, max_age_minutes, check_mode, min_fresh_pct)
        return (True, None)
    except StalenessError as e:
        return (False, str(e))
    except (ValueError, Exception) as e:
        # Unexpected errors: treat as stale if default_to_stale=True
        if default_to_stale:
            return (False, f"Freshness check failed: {e}")
        else:
            raise  # Re-raise for debugging

"""
Freshness checking for market data.

This module validates that data is recent enough to be used for trading decisions.
Stale data can lead to executing trades based on outdated information.

See /docs/CONCEPTS/corporate-actions.md and ADR-0001 for context.
"""

from datetime import UTC, datetime

import polars as pl

from libs.common.exceptions import StalenessError


def check_freshness(df: pl.DataFrame, max_age_minutes: int = 30) -> None:
    """
    Validate that data is fresh enough for trading.

    Checks the most recent timestamp in the data and ensures it's not older
    than the configured threshold. This prevents trading on stale data that
    could lead to incorrect decisions.

    Args:
        df: DataFrame with 'timestamp' column (must be timezone-aware UTC)
        max_age_minutes: Maximum acceptable age in minutes (default: 30)

    Raises:
        StalenessError: If latest timestamp exceeds max_age_minutes
        ValueError: If 'timestamp' column is missing or not timezone-aware

    Example:
        >>> # Fresh data (within 30 minutes)
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "timestamp": [datetime.now(timezone.utc)]
        ... })
        >>> check_freshness(df, max_age_minutes=30)  # Passes

        >>> # Stale data (> 30 minutes old)
        >>> old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        >>> df_stale = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "timestamp": [old_time]
        ... })
        >>> check_freshness(df_stale, max_age_minutes=30)  # Raises StalenessError

    Notes:
        - All timestamps must be UTC (timezone-aware)
        - Uses the LATEST timestamp in the DataFrame (max)
        - Threshold should be much looser than your trading frequency
          (e.g., 30 min threshold for daily strategies)
        - For intraday strategies, tighten to 1-5 minutes

    See Also:
        - ADR-0001: Data Pipeline Architecture (freshness threshold rationale)
        - config/settings.py: DATA_FRESHNESS_MINUTES configuration
    """
    # Validate inputs
    if "timestamp" not in df.columns:
        raise ValueError(
            "DataFrame must have 'timestamp' column. "
            "Available columns: " + ", ".join(df.columns)
        )

    if df.is_empty():
        raise ValueError("Cannot check freshness of empty DataFrame")

    # Get latest timestamp
    latest_timestamp = df["timestamp"].max()

    # Check if timestamp is timezone-aware
    # Polars stores timezone in dtype: pl.Datetime(time_unit, time_zone)
    timestamp_dtype = df["timestamp"].dtype
    if not hasattr(timestamp_dtype, "time_zone") or timestamp_dtype.time_zone is None:
        raise ValueError(
            "Timestamp column must be timezone-aware (UTC). "
            f"Got dtype: {timestamp_dtype}. "
            "Use: pl.col('timestamp').dt.replace_time_zone('UTC')"
        )

    # Convert Polars datetime to Python datetime for comparison
    # Polars .max() returns the scalar value, but mypy types it as Any
    # We need type narrowing to satisfy strict type checking
    if not isinstance(latest_timestamp, datetime):
        raise ValueError(
            f"Expected datetime from timestamp.max(), got {type(latest_timestamp).__name__}"
        )
    latest_dt: datetime = latest_timestamp

    # Get current time in UTC
    now = datetime.now(UTC)

    # Calculate age in seconds
    age_seconds = (now - latest_dt).total_seconds()
    age_minutes = age_seconds / 60

    # Check if data is stale
    if age_minutes > max_age_minutes:
        raise StalenessError(
            f"Data is {age_minutes:.1f} minutes old, exceeds threshold of {max_age_minutes} minutes. "
            f"Latest timestamp: {latest_dt.isoformat()}, "
            f"Current time: {now.isoformat()}"
        )


def check_freshness_safe(
    df: pl.DataFrame, max_age_minutes: int = 30, default_to_stale: bool = True
) -> tuple[bool, str | None]:
    """
    Non-raising version of check_freshness for conditional logic.

    This is useful when you want to check freshness without exception handling,
    such as in conditional pipelines or monitoring.

    Args:
        df: DataFrame with 'timestamp' column
        max_age_minutes: Maximum acceptable age in minutes
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
        check_freshness(df, max_age_minutes)
        return (True, None)
    except StalenessError as e:
        return (False, str(e))
    except (ValueError, Exception) as e:
        # Unexpected errors: treat as stale if default_to_stale=True
        if default_to_stale:
            return (False, f"Freshness check failed: {e}")
        else:
            raise  # Re-raise for debugging

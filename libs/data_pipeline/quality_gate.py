"""
Data quality gate for market data.

This module detects outliers and anomalous price movements that could indicate
data errors, halts, or incorrect adjustments. It separates clean data from
suspicious data that needs human review.

See ADR-0001 for quality gate threshold rationale.
"""

import polars as pl

from libs.common.exceptions import OutlierError


def detect_outliers(
    df: pl.DataFrame, ca_df: pl.DataFrame | None = None, threshold: float = 0.30
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Detect outliers based on daily price changes and corporate actions.

    An outlier is defined as a daily return exceeding the threshold WITHOUT
    a corresponding corporate action (split/dividend) on that date.

    This prevents:
    - Data errors from corrupting models
    - Incorrect corporate action adjustments
    - Trading halts being interpreted as price moves
    - Bad ticks from unreliable data sources

    Args:
        df: DataFrame with columns [symbol, date, close]
        ca_df: Corporate actions DataFrame with [symbol, date] (optional)
               If provided, large moves on CA dates are NOT flagged as outliers
        threshold: Maximum acceptable daily return (default: 0.30 = 30%)

    Returns:
        Tuple of (good_data, quarantine_data):
        - good_data: DataFrame with rows passing quality checks
        - quarantine_data: DataFrame with outlier rows plus 'reason' column

    Example:
        >>> # Normal data
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 4,
        ...     "date": ["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-13"],
        ...     "close": [150.0, 151.5, 152.0, 151.0]
        ... })
        >>> good, bad = detect_outliers(df, threshold=0.30)
        >>> len(good) == 4  # All data passes
        True
        >>> len(bad) == 0
        True

        >>> # Outlier without corporate action
        >>> df_outlier = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 3,
        ...     "date": ["2024-01-10", "2024-01-11", "2024-01-12"],
        ...     "close": [150.0, 225.0, 226.0]  # 50% jump = outlier
        ... })
        >>> good, bad = detect_outliers(df_outlier, threshold=0.30)
        >>> len(good) == 2  # First and last row
        True
        >>> len(bad) == 1   # Middle row quarantined
        True
        >>> bad["reason"][0]
        'outlier_daily_return_0.50'

        >>> # Large move WITH corporate action = OK
        >>> ca = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "date": ["2024-01-11"]
        ... })
        >>> good, bad = detect_outliers(df_outlier, ca_df=ca, threshold=0.30)
        >>> len(good) == 3  # All pass (CA explains the jump)
        True
        >>> len(bad) == 0
        True

    Notes:
        - Requires at least 2 rows per symbol to calculate returns
        - First row per symbol cannot be flagged (no prior close)
        - Threshold of 0.30 (30%) is industry standard for liquid stocks
        - If ca_df is None, ALL large moves are flagged as outliers
        - Quarantined data is preserved for investigation, not deleted

    Implementation Details:
        1. Calculate daily returns within each symbol group
        2. Flag returns exceeding threshold
        3. Remove flags for dates with corporate actions
        4. Split into good vs. quarantine DataFrames
        5. Add 'reason' column to quarantine data for debugging

    See Also:
        - ADR-0001: Data Pipeline Architecture (30% threshold rationale)
        - /docs/CONCEPTS/corporate-actions.md: Legitimate large moves
        - config/settings.py: OUTLIER_THRESHOLD configuration
    """
    # Validate inputs
    required_cols = {"symbol", "date", "close"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")

    if df.is_empty():
        # No data = no outliers
        return (df, df.head(0))

    # Ensure date is proper type
    df = df.with_columns(pl.col("date").cast(pl.Date))

    # Sort by symbol and date for correct daily return calculation
    df_sorted = df.sort(["symbol", "date"])

    # Calculate daily returns (percent change from previous close)
    df_with_returns = df_sorted.with_columns(
        [
            # Previous close within each symbol group
            pl.col("close")
            .shift(1)
            .over("symbol")
            .alias("prev_close"),
        ]
    )

    # Calculate percent change
    df_with_returns = df_with_returns.with_columns(
        [((pl.col("close") - pl.col("prev_close")) / pl.col("prev_close")).alias("daily_return")]
    )

    # Flag potential outliers (abs return > threshold)
    df_with_returns = df_with_returns.with_columns(
        [(pl.col("daily_return").abs() > threshold).alias("is_outlier_candidate")]
    )

    # If corporate actions provided, exclude CA dates from outlier flagging
    if ca_df is not None and not ca_df.is_empty():
        # Ensure CA dates are proper type
        ca_df = ca_df.with_columns(pl.col("date").cast(pl.Date))

        # Mark rows that have a corporate action
        df_with_ca = df_with_returns.join(
            ca_df.select(["symbol", "date"]).with_columns(pl.lit(True).alias("has_ca")),
            on=["symbol", "date"],
            how="left",
            coalesce=True,  # Suppress deprecation warning
        )

        # Fill null has_ca with False
        df_with_ca = df_with_ca.with_columns(pl.col("has_ca").fill_null(False))

        # Outlier = candidate AND no corporate action
        df_with_ca = df_with_ca.with_columns(
            [(pl.col("is_outlier_candidate") & ~pl.col("has_ca")).alias("is_outlier")]
        )
    else:
        # No CA data = all candidates are outliers
        df_with_returns = df_with_returns.with_columns(
            [pl.col("is_outlier_candidate").alias("is_outlier")]
        )
        df_with_ca = df_with_returns

    # First row per symbol can't be outlier (no previous close)
    # Replace null daily_return with False for is_outlier
    df_with_ca = df_with_ca.with_columns(
        [
            pl.when(pl.col("daily_return").is_null())
            .then(False)
            .otherwise(pl.col("is_outlier"))
            .alias("is_outlier")
        ]
    )

    # Split into good vs. quarantine
    good_data = df_with_ca.filter(~pl.col("is_outlier"))
    quarantine_data = df_with_ca.filter(pl.col("is_outlier"))

    # Add reason column to quarantine data
    if not quarantine_data.is_empty():
        quarantine_data = quarantine_data.with_columns(
            [
                (
                    pl.lit("outlier_daily_return_")
                    + pl.col("daily_return").abs().round(2).cast(pl.Utf8)
                ).alias("reason")
            ]
        )

    # Drop temporary columns from both DataFrames
    cols_to_drop = ["prev_close", "daily_return", "is_outlier_candidate", "is_outlier"]
    if ca_df is not None and not ca_df.is_empty():
        cols_to_drop.append("has_ca")

    # Only drop columns that exist
    good_cols_to_drop = [c for c in cols_to_drop if c in good_data.columns]
    quarantine_cols_to_drop = [c for c in cols_to_drop if c in quarantine_data.columns]

    good_data = good_data.drop(good_cols_to_drop)
    if not quarantine_data.is_empty():
        quarantine_data = quarantine_data.drop(quarantine_cols_to_drop)

    return (good_data, quarantine_data)


def check_quality(
    df: pl.DataFrame,
    ca_df: pl.DataFrame | None = None,
    threshold: float = 0.30,
    raise_on_outliers: bool = False,
) -> pl.DataFrame:
    """
    Convenience function that checks quality and optionally raises on outliers.

    This is useful when you want to fail-fast if data quality is poor,
    rather than continuing with partial data.

    Args:
        df: DataFrame with market data
        ca_df: Corporate actions DataFrame (optional)
        threshold: Outlier threshold (default: 0.30)
        raise_on_outliers: If True, raise OutlierError if any outliers found

    Returns:
        DataFrame with only good data (outliers removed)

    Raises:
        OutlierError: If raise_on_outliers=True and outliers detected

    Example:
        >>> # Strict mode: fail if any outliers
        >>> try:
        ...     clean = check_quality(df, raise_on_outliers=True)
        ... except OutlierError as e:
        ...     logger.error(f"Bad data detected: {e}")
        ...     # Handle error (retry, alert, etc.)

        >>> # Lenient mode: just filter out outliers
        >>> clean = check_quality(df, raise_on_outliers=False)
        >>> # Continue with clean data
    """
    good_data, quarantine_data = detect_outliers(df, ca_df, threshold)

    if raise_on_outliers and not quarantine_data.is_empty():
        # Get summary of outliers for error message
        outlier_count = len(quarantine_data)
        outlier_symbols = quarantine_data["symbol"].unique().to_list()

        raise OutlierError(
            f"Detected {outlier_count} outlier(s) across {len(outlier_symbols)} symbol(s): "
            f"{outlier_symbols}. "
            f"Threshold: {threshold:.0%}. "
            f"First outlier: symbol={quarantine_data['symbol'][0]}, "
            f"date={quarantine_data['date'][0]}, "
            f"reason={quarantine_data['reason'][0]}"
        )

    return good_data

"""
Corporate action adjustments for market data.

This module adjusts historical OHLCV data for stock splits and dividends using
the backwards adjustment method (current prices match live market).

See /docs/CONCEPTS/corporate-actions.md for detailed explanation.
See ADR-0001 for architecture decisions.
"""

import polars as pl


def adjust_for_splits(df: pl.DataFrame, ca_df: pl.DataFrame) -> pl.DataFrame:
    """
    Adjust OHLCV data for stock splits using backwards adjustment.

    In a stock split, shares multiply and price divides by the split ratio.
    We adjust historical prices (before the split date) to maintain continuity.

    Example: 4-for-1 split on 2020-08-31
    - All prices BEFORE 2020-08-31: divide by 4
    - All prices AFTER 2020-08-31: unchanged
    - All volumes BEFORE 2020-08-31: multiply by 4

    Args:
        df: DataFrame with columns [symbol, date, open, high, low, close, volume]
        ca_df: Corporate actions DataFrame with [symbol, date, split_ratio]
               split_ratio = new_shares / old_shares (e.g., 4.0 for 4-for-1 split)

    Returns:
        DataFrame with adjusted prices and volumes

    Raises:
        ValueError: If required columns are missing

    Example:
        >>> # Raw data with 4:1 split on 2020-08-31
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 3,
        ...     "date": ["2020-08-28", "2020-08-31", "2020-09-01"],
        ...     "close": [500.0, 125.0, 130.0],
        ...     "volume": [1_000_000, 4_000_000, 3_800_000]
        ... })
        >>> ca = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "date": ["2020-08-31"],
        ...     "split_ratio": [4.0]
        ... })
        >>> adjusted = adjust_for_splits(df, ca)
        >>> adjusted["close"].to_list()
        [125.0, 125.0, 130.0]  # Pre-split price adjusted from 500→125

    Notes:
        - Split ratio of 1.0 = no adjustment (identity operation)
        - Reverse splits: ratio < 1.0 (e.g., 1:4 reverse = 0.25)
        - This is idempotent: adjust(adjust(data)) == adjust(data)
        - Assumes CA data is complete and accurate

    See Also:
        - /docs/CONCEPTS/corporate-actions.md for detailed explanation
        - ADR-0001 for backwards vs forward adjustment rationale
    """
    # Validate input columns
    required_df_cols = {"symbol", "date", "open", "high", "low", "close", "volume"}
    required_ca_cols = {"symbol", "date", "split_ratio"}

    missing_df = required_df_cols - set(df.columns)
    if missing_df:
        raise ValueError(f"DataFrame missing required columns: {missing_df}")

    missing_ca = required_ca_cols - set(ca_df.columns)
    if missing_ca:
        raise ValueError(f"Corporate actions DataFrame missing columns: {missing_ca}")

    # If no corporate actions, return original data unchanged
    if ca_df.is_empty():
        return df

    # Ensure date columns are proper date type
    df = df.with_columns(pl.col("date").cast(pl.Date))
    ca_df = ca_df.with_columns(pl.col("date").cast(pl.Date))

    # Join corporate actions data
    # Left join: keep all data rows, fill with null where no CA
    df_with_ca = df.join(
        ca_df.select(["symbol", "date", "split_ratio"]), on=["symbol", "date"], how="left"
    )

    # Fill null split ratios with 1.0 (no split = no adjustment)
    df_with_ca = df_with_ca.with_columns(pl.col("split_ratio").fill_null(1.0))

    # For backwards adjustment, we need cumulative split ratios
    # Sort by symbol and date to ensure correct order
    df_sorted = df_with_ca.sort(["symbol", "date"])

    # Calculate cumulative split ratio (backwards from most recent)
    # We do this by reverse-cumulative-product per symbol
    # Shift by 1 so split date itself gets 1.0 (only BEFORE split is adjusted)
    df_sorted = df_sorted.with_columns(
        [
            # Reverse cumulative product within each symbol group
            pl.col("split_ratio")
            .reverse()
            .cum_prod()
            .shift(1, fill_value=1.0)  # Shift so split date gets 1.0
            .reverse()
            .over("symbol")
            .alias("cumulative_split")
        ]
    )

    # Adjust prices: divide by cumulative split ratio
    # Adjust volume: multiply by cumulative split ratio
    df_adjusted = df_sorted.with_columns(
        [
            (pl.col("open") / pl.col("cumulative_split")).alias("open"),
            (pl.col("high") / pl.col("cumulative_split")).alias("high"),
            (pl.col("low") / pl.col("cumulative_split")).alias("low"),
            (pl.col("close") / pl.col("cumulative_split")).alias("close"),
            (pl.col("volume") * pl.col("cumulative_split")).alias("volume"),
        ]
    )

    # Drop temporary columns
    df_adjusted = df_adjusted.drop(["split_ratio", "cumulative_split"])

    return df_adjusted


def adjust_for_dividends(df: pl.DataFrame, ca_df: pl.DataFrame) -> pl.DataFrame:
    """
    Adjust close prices for cash dividends using backwards adjustment.

    On ex-dividend date, stock typically drops by the dividend amount.
    We adjust historical closes (before ex-date) to maintain continuity.

    Args:
        df: DataFrame with columns [symbol, date, close]
        ca_df: Corporate actions DataFrame with [symbol, date, dividend]
               dividend = cash amount per share (e.g., 2.0 for $2 dividend)

    Returns:
        DataFrame with adjusted close prices

    Raises:
        ValueError: If required columns are missing

    Example:
        >>> # $2 dividend on 2024-01-15
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 3,
        ...     "date": ["2024-01-12", "2024-01-15", "2024-01-16"],
        ...     "close": [150.0, 148.0, 149.0]
        ... })
        >>> ca = pl.DataFrame({
        ...     "symbol": ["AAPL"],
        ...     "date": ["2024-01-15"],
        ...     "dividend": [2.0]
        ... })
        >>> adjusted = adjust_for_dividends(df, ca)
        >>> adjusted["close"].to_list()
        [148.0, 148.0, 149.0]  # Pre-dividend adjusted from 150→148

    Notes:
        - Only close prices are adjusted (dividends affect close, not OHLC)
        - Cumulative dividends are subtracted from historical closes
        - This prevents fake "drops" in price charts
        - Assumes ex-dividend dates (not payment dates)

    See Also:
        - /docs/CONCEPTS/corporate-actions.md for dividend explanation
    """
    # Validate input columns
    required_df_cols = {"symbol", "date", "close"}
    required_ca_cols = {"symbol", "date", "dividend"}

    missing_df = required_df_cols - set(df.columns)
    if missing_df:
        raise ValueError(f"DataFrame missing required columns: {missing_df}")

    missing_ca = required_ca_cols - set(ca_df.columns)
    if missing_ca:
        raise ValueError(f"Corporate actions DataFrame missing columns: {missing_ca}")

    # If no dividends, return original data unchanged
    if ca_df.is_empty():
        return df

    # Ensure date columns are proper date type
    df = df.with_columns(pl.col("date").cast(pl.Date))
    ca_df = ca_df.with_columns(pl.col("date").cast(pl.Date))

    # Join dividend data
    df_with_div = df.join(
        ca_df.select(["symbol", "date", "dividend"]), on=["symbol", "date"], how="left"
    )

    # Fill null dividends with 0.0 (no dividend = no adjustment)
    df_with_div = df_with_div.with_columns(pl.col("dividend").fill_null(0.0))

    # Sort by symbol and date
    df_sorted = df_with_div.sort(["symbol", "date"])

    # Calculate cumulative dividend (backwards from most recent)
    # Reverse cumulative sum within each symbol group
    # Shift by 1 so ex-date itself gets 0 (only BEFORE ex-date is adjusted)
    df_sorted = df_sorted.with_columns(
        [
            pl.col("dividend")
            .reverse()
            .cum_sum()
            .shift(1, fill_value=0.0)  # Shift so ex-date row gets 0
            .reverse()
            .over("symbol")
            .alias("cumulative_dividend")
        ]
    )

    # Adjust close: subtract cumulative dividend
    df_adjusted = df_sorted.with_columns(
        [(pl.col("close") - pl.col("cumulative_dividend")).alias("close")]
    )

    # Drop temporary columns
    df_adjusted = df_adjusted.drop(["dividend", "cumulative_dividend"])

    return df_adjusted


def adjust_prices(
    df: pl.DataFrame,
    splits_df: pl.DataFrame | None = None,
    dividends_df: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """
    Convenience function to adjust for both splits and dividends.

    Applies adjustments in order: splits first, then dividends.
    This is the standard approach in the industry.

    Args:
        df: DataFrame with OHLCV data
        splits_df: Corporate actions for splits (optional)
        dividends_df: Corporate actions for dividends (optional)

    Returns:
        DataFrame with fully adjusted prices

    Example:
        >>> adjusted = adjust_prices(
        ...     raw_data,
        ...     splits_df=splits,
        ...     dividends_df=dividends
        ... )

    Notes:
        - If both DataFrames are None/empty, returns original data
        - Order matters: splits before dividends
        - Each adjustment is optional
    """
    result = df

    # Apply splits first
    if splits_df is not None and not splits_df.is_empty():
        result = adjust_for_splits(result, splits_df)

    # Then apply dividends
    if dividends_df is not None and not dividends_df.is_empty():
        result = adjust_for_dividends(result, dividends_df)

    return result

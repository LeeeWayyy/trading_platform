"""
Mock data generator for testing the data pipeline.

This module creates realistic but deterministic test data with known
characteristics (splits, dividends, outliers) for validating pipeline behavior.

All data is synthetic and designed to test specific edge cases.
"""

from datetime import UTC, date, datetime, timedelta

import polars as pl


def create_normal_ohlcv(
    symbol: str = "AAPL",
    start_date: date = date(2024, 1, 1),
    num_days: int = 10,
    base_price: float = 150.0,
    volatility: float = 0.02,
) -> pl.DataFrame:
    """
    Create normal OHLCV data with small daily variations.

    This simulates typical market data without any corporate actions
    or outliers. Prices drift randomly within the volatility band.

    Args:
        symbol: Stock symbol
        start_date: First date of data
        num_days: Number of trading days to generate
        base_price: Starting close price
        volatility: Daily volatility (stddev of returns, e.g., 0.02 = 2%)

    Returns:
        DataFrame with columns [symbol, date, open, high, low, close, volume, timestamp]

    Example:
        >>> df = create_normal_ohlcv("AAPL", num_days=5)
        >>> len(df) == 5
        True
        >>> all(df["symbol"] == "AAPL")
        True
    """
    dates = [start_date + timedelta(days=i) for i in range(num_days)]

    # Generate deterministic but realistic price path
    # Using sine wave + small noise for reproducibility
    closes = []
    price = base_price
    for i in range(num_days):
        # Deterministic "random" walk
        daily_return = volatility * (i % 3 - 1) * 0.5  # -1, 0, 1 pattern
        price = price * (1 + daily_return)
        closes.append(price)

    # Generate OHLC from close
    data = []
    for i, (d, close) in enumerate(zip(dates, closes, strict=False)):
        # Intraday range: ±0.5% from close
        high = close * 1.005
        low = close * 0.995
        open_price = close * 0.998 if i % 2 == 0 else close * 1.002

        # Volume: realistic range with deterministic variation
        volume = 1_000_000 + (i % 5) * 200_000

        # Timestamp: market close at 4:00 PM ET
        timestamp = datetime.combine(d, datetime.min.time()).replace(
            hour=20, minute=0, tzinfo=UTC  # 4 PM ET = 8 PM UTC (winter)
        )

        data.append(
            {
                "symbol": symbol,
                "date": d,
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": volume,
                "timestamp": timestamp,
            }
        )

    return pl.DataFrame(data)


def create_data_with_split(
    symbol: str = "AAPL",
    split_date: date = date(2024, 1, 15),
    split_ratio: float = 4.0,
    days_before: int = 10,
    days_after: int = 10,
    pre_split_price: float = 500.0,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Create OHLCV data with a stock split.

    Generates realistic pre-split and post-split data. The post-split data
    will have lower prices and higher volumes, simulating the actual split event.

    Args:
        symbol: Stock symbol
        split_date: Date of the split
        split_ratio: Split ratio (e.g., 4.0 for 4-for-1 split)
        days_before: Trading days before split
        days_after: Trading days after split
        pre_split_price: Close price before split

    Returns:
        Tuple of (raw_data_df, splits_df):
        - raw_data_df: OHLCV with split discontinuity
        - splits_df: Corporate actions with [symbol, date, split_ratio]

    Example:
        >>> raw, splits = create_data_with_split(split_ratio=4.0)
        >>> # Pre-split close: ~$500
        >>> # Post-split close: ~$125
        >>> # After adjustment: all prices continuous
    """
    # Pre-split data (higher prices, lower volume)
    pre_split_start = split_date - timedelta(days=days_before)
    pre_split_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=pre_split_start,
        num_days=days_before,
        base_price=pre_split_price,
        volatility=0.01,
    )

    # Post-split data (lower prices, higher volume)
    post_split_price = pre_split_price / split_ratio
    post_split_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=split_date,
        num_days=days_after,
        base_price=post_split_price,
        volatility=0.01,
    )

    # Adjust post-split volume (multiply by split ratio)
    post_split_df = post_split_df.with_columns(
        (pl.col("volume") * split_ratio).cast(pl.Int64).alias("volume")
    )

    # Combine pre and post split data
    raw_data = pl.concat([pre_split_df, post_split_df]).sort("date")

    # Create splits DataFrame
    splits_df = pl.DataFrame(
        {"symbol": [symbol], "date": [split_date], "split_ratio": [split_ratio]}
    )

    return (raw_data, splits_df)


def create_data_with_dividend(
    symbol: str = "MSFT",
    ex_date: date = date(2024, 1, 15),
    dividend_amount: float = 2.0,
    days_before: int = 10,
    days_after: int = 10,
    base_price: float = 150.0,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Create OHLCV data with a dividend payment.

    On the ex-dividend date, the close price typically drops by approximately
    the dividend amount. This simulates that behavior.

    Args:
        symbol: Stock symbol
        ex_date: Ex-dividend date (first day without dividend)
        dividend_amount: Dividend per share (e.g., 2.0 for $2)
        days_before: Trading days before ex-date
        days_after: Trading days after ex-date
        base_price: Close price before dividend

    Returns:
        Tuple of (raw_data_df, dividends_df):
        - raw_data_df: OHLCV with dividend drop
        - dividends_df: Corporate actions with [symbol, date, dividend]

    Example:
        >>> raw, divs = create_data_with_dividend(dividend_amount=2.0)
        >>> # Pre-dividend: ~$150
        >>> # Ex-date: ~$148 (dropped by $2)
        >>> # After adjustment: smooth transition
    """
    # Pre-dividend data
    pre_div_start = ex_date - timedelta(days=days_before)
    pre_div_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=pre_div_start,
        num_days=days_before,
        base_price=base_price,
        volatility=0.01,
    )

    # Post-dividend data (price drops by dividend amount)
    post_div_price = base_price - dividend_amount
    post_div_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=ex_date,
        num_days=days_after,
        base_price=post_div_price,
        volatility=0.01,
    )

    # Combine
    raw_data = pl.concat([pre_div_df, post_div_df]).sort("date")

    # Create dividends DataFrame
    dividends_df = pl.DataFrame(
        {"symbol": [symbol], "date": [ex_date], "dividend": [dividend_amount]}
    )

    return (raw_data, dividends_df)


def create_data_with_outlier(
    symbol: str = "GOOGL",
    outlier_date: date = date(2024, 1, 15),
    outlier_return: float = 0.50,
    days_before: int = 5,
    days_after: int = 5,
    base_price: float = 100.0,
) -> pl.DataFrame:
    """
    Create OHLCV data with an artificial outlier (data error).

    This simulates bad data that should be caught by the quality gate.
    The outlier is a single-day price spike without any corporate action.

    Args:
        symbol: Stock symbol
        outlier_date: Date of the outlier
        outlier_return: Size of the outlier (e.g., 0.50 = 50% jump)
        days_before: Trading days before outlier
        days_after: Trading days after outlier
        base_price: Normal price level

    Returns:
        DataFrame with OHLCV including one outlier row

    Example:
        >>> df = create_data_with_outlier(outlier_return=0.50)
        >>> # Most days: normal ~2% moves
        >>> # Outlier day: 50% spike (should be quarantined)
    """
    # Before outlier
    pre_outlier_start = outlier_date - timedelta(days=days_before)
    pre_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=pre_outlier_start,
        num_days=days_before,
        base_price=base_price,
        volatility=0.02,
    )

    # Outlier day (huge spike)
    last_close = pre_df.filter(pl.col("date") == pre_df["date"].max())["close"][0]
    outlier_close = last_close * (1 + outlier_return)

    outlier_row = pl.DataFrame(
        {
            "symbol": [symbol],
            "date": [outlier_date],
            "open": [last_close * 1.01],
            "high": [outlier_close * 1.01],
            "low": [last_close * 0.99],
            "close": [outlier_close],
            "volume": [5_000_000],  # Unusually high volume
            "timestamp": [
                datetime.combine(outlier_date, datetime.min.time()).replace(
                    hour=20, minute=0, tzinfo=UTC
                )
            ],
        }
    )

    # After outlier (return to normal)
    post_df = create_normal_ohlcv(
        symbol=symbol,
        start_date=outlier_date + timedelta(days=1),
        num_days=days_after,
        base_price=base_price,  # Revert to normal (proves it was an error)
        volatility=0.02,
    )

    return pl.concat([pre_df, outlier_row, post_df]).sort("date")


def create_stale_data(symbol: str = "AAPL", hours_old: int = 2, num_days: int = 5) -> pl.DataFrame:
    """
    Create OHLCV data with stale timestamps.

    This simulates data that is too old to use for trading decisions,
    which should be caught by the freshness check.

    Args:
        symbol: Stock symbol
        hours_old: How many hours in the past (e.g., 2 = 2 hours ago)
        num_days: Number of days of data

    Returns:
        DataFrame with old timestamps

    Example:
        >>> df = create_stale_data(hours_old=2)
        >>> # Freshness check with 30min threshold should raise StalenessError
    """
    # Create normal data
    df = create_normal_ohlcv(symbol=symbol, num_days=num_days)

    # Make all timestamps old
    now = datetime.now(UTC)
    old_time = now - timedelta(hours=hours_old)

    df = df.with_columns(pl.lit(old_time).alias("timestamp"))

    return df


def create_multi_symbol_data(
    symbols: list[str] = None,
    num_days: int = 10,
    include_split: bool = True,
    include_dividend: bool = True,
    include_outlier: bool = True,
) -> dict[str, pl.DataFrame]:
    """
    Create comprehensive test dataset with multiple symbols and corporate actions.

    This generates a realistic test scenario with:
    - Multiple symbols
    - One symbol with split (AAPL)
    - One symbol with dividend (MSFT)
    - One symbol with outlier (GOOGL)

    Args:
        symbols: List of symbols to generate (default: AAPL, MSFT, GOOGL)
        num_days: Days of data per symbol
        include_split: Add split to first symbol
        include_dividend: Add dividend to second symbol
        include_outlier: Add outlier to third symbol

    Returns:
        Dictionary with:
        - "raw_data": Combined OHLCV for all symbols
        - "splits": Splits DataFrame (if include_split=True)
        - "dividends": Dividends DataFrame (if include_dividend=True)

    Example:
        >>> data = create_multi_symbol_data()
        >>> raw = data["raw_data"]
        >>> raw["symbol"].unique().to_list()
        ['AAPL', 'GOOGL', 'MSFT']
    """
    if symbols is None:
        symbols = ["AAPL", "MSFT", "GOOGL"]
    all_data = []
    splits_data = []
    dividends_data = []

    mid_date = date(2024, 1, 1) + timedelta(days=num_days // 2)

    for i, symbol in enumerate(symbols):
        if i == 0 and include_split:
            # First symbol gets a split
            raw, splits = create_data_with_split(
                symbol=symbol,
                split_date=mid_date,
                days_before=num_days // 2,
                days_after=num_days // 2,
            )
            all_data.append(raw)
            splits_data.append(splits)

        elif i == 1 and include_dividend:
            # Second symbol gets a dividend
            raw, divs = create_data_with_dividend(
                symbol=symbol, ex_date=mid_date, days_before=num_days // 2, days_after=num_days // 2
            )
            all_data.append(raw)
            dividends_data.append(divs)

        elif i == 2 and include_outlier:
            # Third symbol gets an outlier
            raw = create_data_with_outlier(
                symbol=symbol,
                outlier_date=mid_date,
                days_before=num_days // 2,
                days_after=num_days // 2,
            )
            all_data.append(raw)

        else:
            # Normal data
            raw = create_normal_ohlcv(symbol=symbol, num_days=num_days, start_date=date(2024, 1, 1))
            all_data.append(raw)

    # Combine all data
    combined_raw = pl.concat(all_data).sort(["symbol", "date"])

    result = {"raw_data": combined_raw}

    if splits_data:
        result["splits"] = pl.concat(splits_data)

    if dividends_data:
        result["dividends"] = pl.concat(dividends_data)

    return result

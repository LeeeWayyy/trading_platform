"""
Mean reversion feature engineering using technical indicators.

This module provides technical indicators for mean reversion trading strategies.
Mean reversion assumes that prices tend to revert to their historical mean
over time, creating profitable trading opportunities when prices deviate.

Key Indicators:
1. RSI (Relative Strength Index): Measures overbought/oversold conditions
2. Bollinger Bands: Statistical bands around price mean
3. Stochastic Oscillator: Compares closing price to price range
4. Z-Score: Statistical measure of price deviation from mean

Feature parity pattern: Same features used in research and production.

See /docs/CONCEPTS/mean-reversion.md for detailed explanation (will create).
"""

from pathlib import Path

import polars as pl


def compute_rsi(prices: pl.DataFrame, period: int = 14, column: str = "close") -> pl.DataFrame:
    """
    Compute Relative Strength Index (RSI) indicator.

    RSI measures the speed and magnitude of price changes to identify
    overbought (>70) and oversold (<30) conditions.

    Formula:
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss over period

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        period: Lookback period for RSI calculation (default: 14 days)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with original columns plus 'rsi' column

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 20,
        ...     "date": pd.date_range("2024-01-01", periods=20),
        ...     "close": [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
        ...               110, 108, 107, 105, 104, 106, 108, 110, 112, 111]
        ... })
        >>> result = compute_rsi(df, period=14)
        >>> print(result["rsi"].tail(1))  # Recent RSI value

    Notes:
        - RSI > 70: Overbought (potential sell signal)
        - RSI < 30: Oversold (potential buy signal)
        - First `period` rows will have null RSI values
        - Wilders smoothing method used (EMA-based)

    See Also:
        - https://www.investopedia.com/terms/r/rsi.asp
        - /docs/CONCEPTS/technical-indicators.md
    """
    # Calculate price changes
    df = prices.with_columns((pl.col(column) - pl.col(column).shift(1)).alias("price_change"))

    # Separate gains and losses
    df = df.with_columns(
        [
            pl.when(pl.col("price_change") > 0)
            .then(pl.col("price_change"))
            .otherwise(0.0)
            .alias("gain"),
            pl.when(pl.col("price_change") < 0)
            .then(-pl.col("price_change"))
            .otherwise(0.0)
            .alias("loss"),
        ]
    )

    # Calculate average gain and loss using EWM (Exponential Weighted Moving Average)
    # This matches Wilder's smoothing method
    alpha = 1.0 / period

    df = df.with_columns(
        [
            pl.col("gain")
            .ewm_mean(alpha=alpha, adjust=False, min_samples=period)
            .alias("avg_gain"),
            pl.col("loss")
            .ewm_mean(alpha=alpha, adjust=False, min_samples=period)
            .alias("avg_loss"),
        ]
    )

    # Calculate RS (Relative Strength) and RSI
    df = df.with_columns((pl.col("avg_gain") / pl.col("avg_loss")).alias("rs"))

    df = df.with_columns((100.0 - (100.0 / (1.0 + pl.col("rs")))).alias("rsi"))

    # Drop intermediate columns
    return df.drop(["price_change", "gain", "loss", "avg_gain", "avg_loss", "rs"])


def compute_bollinger_bands(
    prices: pl.DataFrame,
    period: int = 20,
    num_std: float = 2.0,
    column: str = "close",
) -> pl.DataFrame:
    """
    Compute Bollinger Bands indicator.

    Bollinger Bands consist of:
    - Middle Band: Simple Moving Average (SMA)
    - Upper Band: SMA + (num_std * standard deviation)
    - Lower Band: SMA - (num_std * standard deviation)

    Bands expand during volatile periods and contract during quiet periods.
    Prices touching upper/lower bands may indicate overbought/oversold conditions.

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        period: Lookback period for SMA calculation (default: 20 days)
        num_std: Number of standard deviations for bands (default: 2.0)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with columns: 'bb_middle', 'bb_upper', 'bb_lower', 'bb_width', 'bb_pct'
        - bb_middle: Simple moving average
        - bb_upper: Upper band (mean + num_std * std)
        - bb_lower: Lower band (mean - num_std * std)
        - bb_width: Distance between upper and lower bands (volatility measure)
        - bb_pct: Percent B - where price falls relative to bands (0-1 scale)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 30,
        ...     "date": pd.date_range("2024-01-01", periods=30),
        ...     "close": np.random.randn(30).cumsum() + 100
        ... })
        >>> result = compute_bollinger_bands(df, period=20)
        >>> # Price at lower band (oversold): bb_pct close to 0
        >>> # Price at upper band (overbought): bb_pct close to 1

    Notes:
        - Price touching upper band: Potential sell signal
        - Price touching lower band: Potential buy signal
        - Bollinger Squeeze (narrow bands): Volatility breakout may be imminent
        - First `period` rows will have null values

    See Also:
        - https://www.investopedia.com/terms/b/bollingerbands.asp
        - /docs/CONCEPTS/technical-indicators.md
    """
    # Calculate rolling mean (middle band)
    df = prices.with_columns(pl.col(column).rolling_mean(window_size=period).alias("bb_middle"))

    # Calculate rolling standard deviation
    df = df.with_columns(pl.col(column).rolling_std(window_size=period).alias("bb_std"))

    # Calculate upper and lower bands
    df = df.with_columns(
        [
            (pl.col("bb_middle") + (num_std * pl.col("bb_std"))).alias("bb_upper"),
            (pl.col("bb_middle") - (num_std * pl.col("bb_std"))).alias("bb_lower"),
        ]
    )

    # Calculate bandwidth (volatility measure)
    df = df.with_columns((pl.col("bb_upper") - pl.col("bb_lower")).alias("bb_width"))

    # Calculate %B (where price falls within bands)
    # %B = (Price - Lower Band) / (Upper Band - Lower Band)
    # %B > 1: Price above upper band
    # %B < 0: Price below lower band
    # %B = 0.5: Price at middle band
    df = df.with_columns(
        ((pl.col(column) - pl.col("bb_lower")) / (pl.col("bb_upper") - pl.col("bb_lower"))).alias(
            "bb_pct"
        )
    )

    # Drop intermediate column
    return df.drop("bb_std")


def compute_stochastic_oscillator(
    prices: pl.DataFrame, k_period: int = 14, d_period: int = 3
) -> pl.DataFrame:
    """
    Compute Stochastic Oscillator indicator.

    Stochastic oscillator compares a security's closing price to its price range
    over a given period. It consists of two lines:
    - %K (fast): Current close relative to period high-low range
    - %D (slow): Moving average of %K

    Formula:
        %K = 100 * (Close - Low_n) / (High_n - Low_n)
        %D = SMA of %K over d_period

    Args:
        prices: DataFrame with 'symbol', 'date', 'high', 'low', 'close' columns
        k_period: Lookback period for %K calculation (default: 14 days)
        d_period: Smoothing period for %D calculation (default: 3 days)

    Returns:
        DataFrame with columns: 'stoch_k', 'stoch_d'
        - stoch_k: %K line (fast oscillator)
        - stoch_d: %D line (slow oscillator, signal line)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 20,
        ...     "date": pd.date_range("2024-01-01", periods=20),
        ...     "high": np.random.randn(20).cumsum() + 105,
        ...     "low": np.random.randn(20).cumsum() + 95,
        ...     "close": np.random.randn(20).cumsum() + 100
        ... })
        >>> result = compute_stochastic_oscillator(df)
        >>> # %K and %D > 80: Overbought
        >>> # %K and %D < 20: Oversold
        >>> # %K crosses above %D: Bullish signal
        >>> # %K crosses below %D: Bearish signal

    Notes:
        - Values range from 0 to 100
        - > 80: Overbought (potential sell signal)
        - < 20: Oversold (potential buy signal)
        - %K crossing above %D: Bullish crossover
        - %K crossing below %D: Bearish crossover

    See Also:
        - https://www.investopedia.com/terms/s/stochasticoscillator.asp
        - /docs/CONCEPTS/technical-indicators.md
    """
    # Calculate rolling high and low
    df = prices.with_columns(
        [
            pl.col("high").rolling_max(window_size=k_period).alias("period_high"),
            pl.col("low").rolling_min(window_size=k_period).alias("period_low"),
        ]
    )

    # Calculate %K (fast stochastic)
    df = df.with_columns(
        (
            100.0
            * (pl.col("close") - pl.col("period_low"))
            / (pl.col("period_high") - pl.col("period_low"))
        ).alias("stoch_k")
    )

    # Calculate %D (slow stochastic - SMA of %K)
    df = df.with_columns(pl.col("stoch_k").rolling_mean(window_size=d_period).alias("stoch_d"))

    # Drop intermediate columns
    return df.drop(["period_high", "period_low"])


def compute_price_zscore(
    prices: pl.DataFrame, period: int = 20, column: str = "close"
) -> pl.DataFrame:
    """
    Compute Z-Score of price relative to rolling mean.

    Z-Score measures how many standard deviations the current price is
    from its rolling mean. Used to identify extreme price deviations.

    Formula:
        Z = (Price - Mean) / StdDev

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        period: Lookback period for mean/std calculation (default: 20 days)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with 'price_zscore' column

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 30,
        ...     "date": pd.date_range("2024-01-01", periods=30),
        ...     "close": np.random.randn(30).cumsum() + 100
        ... })
        >>> result = compute_price_zscore(df, period=20)
        >>> # Z-score > 2: Price significantly above mean (sell signal)
        >>> # Z-score < -2: Price significantly below mean (buy signal)

    Notes:
        - Z-score > 2: Price is 2 std devs above mean (overbought)
        - Z-score < -2: Price is 2 std devs below mean (oversold)
        - Z-score near 0: Price close to mean (no clear signal)
        - First `period` rows will have null values

    See Also:
        - https://www.investopedia.com/terms/z/zscore.asp
        - /docs/CONCEPTS/statistical-indicators.md
    """
    # Calculate rolling mean and std
    df = prices.with_columns(
        [
            pl.col(column).rolling_mean(window_size=period).alias("rolling_mean"),
            pl.col(column).rolling_std(window_size=period).alias("rolling_std"),
        ]
    )

    # Calculate Z-score
    df = df.with_columns(
        ((pl.col(column) - pl.col("rolling_mean")) / pl.col("rolling_std")).alias("price_zscore")
    )

    # Drop intermediate columns
    return df.drop(["rolling_mean", "rolling_std"])


def compute_mean_reversion_features(
    prices: pl.DataFrame,
    rsi_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
    stoch_k_period: int = 14,
    stoch_d_period: int = 3,
    zscore_period: int = 20,
) -> pl.DataFrame:
    """
    Compute all mean reversion features for given price data.

    This is the main function that combines all mean reversion indicators
    into a single feature set. Used for both research and production.

    Args:
        prices: DataFrame with OHLCV columns (open, high, low, close, volume)
                Must have 'symbol' and 'date' columns
        rsi_period: RSI lookback period (default: 14)
        bb_period: Bollinger Bands lookback period (default: 20)
        bb_std: Bollinger Bands standard deviation multiplier (default: 2.0)
        stoch_k_period: Stochastic %K period (default: 14)
        stoch_d_period: Stochastic %D smoothing period (default: 3)
        zscore_period: Z-Score lookback period (default: 20)

    Returns:
        DataFrame with all input columns plus:
        - rsi: Relative Strength Index
        - bb_middle, bb_upper, bb_lower: Bollinger Bands
        - bb_width: Bollinger Band width (volatility)
        - bb_pct: Percent B (price position within bands)
        - stoch_k, stoch_d: Stochastic Oscillator
        - price_zscore: Z-Score of price

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 50,
        ...     "date": pd.date_range("2024-01-01", periods=50),
        ...     "open": np.random.randn(50).cumsum() + 100,
        ...     "high": np.random.randn(50).cumsum() + 105,
        ...     "low": np.random.randn(50).cumsum() + 95,
        ...     "close": np.random.randn(50).cumsum() + 100,
        ...     "volume": np.random.randint(1000000, 5000000, 50)
        ... })
        >>> features = compute_mean_reversion_features(df)
        >>> print(features.columns)
        >>> # Use features for model training or signal generation

    Notes:
        - Requires minimum 50 rows for stable feature calculation
        - First 20-50 rows may have null values (depends on max period)
        - All features are normalized indicators (0-100 or -3 to 3 range)
        - Feature parity: Same code used in research and production

    See Also:
        - /docs/CONCEPTS/mean-reversion.md
        - /docs/IMPLEMENTATION_GUIDES/p1t6-advanced-strategies.md
    """
    # Sort by symbol and date to ensure correct order
    df = prices.sort(["symbol", "date"])

    # Compute RSI
    df = compute_rsi(df, period=rsi_period)

    # Compute Bollinger Bands
    df = compute_bollinger_bands(df, period=bb_period, num_std=bb_std)

    # Compute Stochastic Oscillator
    df = compute_stochastic_oscillator(df, k_period=stoch_k_period, d_period=stoch_d_period)

    # Compute Price Z-Score
    df = compute_price_zscore(df, period=zscore_period)

    return df


def load_and_compute_features(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_dir: Path = Path("data/adjusted"),
    **feature_params: int | float,
) -> pl.DataFrame:
    """
    Load price data from T1 adjusted Parquet files and compute mean reversion features.

    This is the main entry point for feature generation, combining data loading
    and feature computation.

    Args:
        symbols: List of stock symbols (e.g., ["AAPL", "MSFT"])
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        data_dir: Directory containing T1 adjusted Parquet files
        **feature_params: Optional parameters passed to compute_mean_reversion_features()

    Returns:
        DataFrame with price data and all mean reversion features

    Example:
        >>> features = load_and_compute_features(
        ...     symbols=["AAPL", "MSFT"],
        ...     start_date="2024-01-01",
        ...     end_date="2024-12-31",
        ...     rsi_period=14,
        ...     bb_period=20
        ... )
        >>> print(features.shape)
        >>> print(features.columns)

    Notes:
        - Data must exist in data_dir for all symbols and dates
        - Features are computed per symbol independently
        - Missing data will result in null feature values
        - Use this function for both training and inference

    Raises:
        FileNotFoundError: If data files don't exist
        ValueError: If data is malformed or incomplete
    """
    # TODO: Implement data loading from T1 adjusted Parquet files
    # For now, this is a placeholder that will be implemented
    # when integrating with T1 data pipeline

    raise NotImplementedError(
        "Data loading not yet implemented. "
        "Will integrate with T1 data pipeline in next iteration."
    )

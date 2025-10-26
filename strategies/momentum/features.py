"""
Momentum feature engineering using trend-following indicators.

This module provides technical indicators for momentum trading strategies.
Momentum strategies assume that price trends persist - assets moving strongly
in one direction will continue that trend, creating profitable opportunities.

Key Indicators:
1. Moving Averages: Trend direction via SMA/EMA crossovers
2. MACD: Trend momentum and reversals
3. ADX: Trend strength measurement
4. Rate of Change: Price momentum velocity
5. Volume Indicators: Trend confirmation via volume

Feature parity pattern: Same features used in research and production.

See /docs/CONCEPTS/momentum-trading.md for detailed explanation (will create).
"""

import polars as pl


def compute_moving_averages(
    prices: pl.DataFrame,
    fast_period: int = 10,
    slow_period: int = 50,
    column: str = "close",
) -> pl.DataFrame:
    """
    Compute moving average crossover signals.

    Moving averages smooth price data to identify trend direction. When the
    fast MA crosses above the slow MA, it signals bullish momentum. When fast
    crosses below slow, it signals bearish momentum.

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        fast_period: Fast moving average period (default: 10 days)
        slow_period: Slow moving average period (default: 50 days)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with columns:
        - ma_fast: Fast moving average
        - ma_slow: Slow moving average
        - ma_diff: Difference between fast and slow MA (positive = bullish)
        - ma_cross: Crossover signal (1 = bullish cross, -1 = bearish cross, 0 = no cross)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 60,
        ...     "date": pd.date_range("2024-01-01", periods=60),
        ...     "close": np.cumsum(np.random.randn(60)) + 100
        ... })
        >>> result = compute_moving_averages(df, fast_period=10, slow_period=50)
        >>> # ma_cross = 1: Golden cross (buy signal)
        >>> # ma_cross = -1: Death cross (sell signal)

    Notes:
        - Golden Cross: Fast MA crosses above slow MA (bullish)
        - Death Cross: Fast MA crosses below slow MA (bearish)
        - First `slow_period` rows will have null values
        - MA difference magnitude indicates trend strength

    See Also:
        - https://www.investopedia.com/terms/m/movingaverage.asp
        - https://www.investopedia.com/terms/g/goldencross.asp
    """
    # Calculate fast and slow moving averages
    df = prices.with_columns(
        [
            pl.col(column).rolling_mean(window_size=fast_period).alias("ma_fast"),
            pl.col(column).rolling_mean(window_size=slow_period).alias("ma_slow"),
        ]
    )

    # Calculate difference (positive = bullish, negative = bearish)
    df = df.with_columns((pl.col("ma_fast") - pl.col("ma_slow")).alias("ma_diff"))

    # Detect crossovers
    # 1 = fast crosses above slow (bullish/golden cross)
    # -1 = fast crosses below slow (bearish/death cross)
    # 0 = no cross
    df = df.with_columns(
        pl.when((pl.col("ma_diff") > 0) & (pl.col("ma_diff").shift(1) <= 0))
        .then(1)  # Golden cross
        .when((pl.col("ma_diff") < 0) & (pl.col("ma_diff").shift(1) >= 0))
        .then(-1)  # Death cross
        .otherwise(0)
        .alias("ma_cross")
    )

    return df


def compute_macd(
    prices: pl.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    column: str = "close",
) -> pl.DataFrame:
    """
    Compute MACD (Moving Average Convergence Divergence) indicator.

    MACD shows the relationship between two moving averages and is used to
    identify trend changes, momentum, and potential reversal points.

    Formula:
        MACD Line = EMA(12) - EMA(26)
        Signal Line = EMA(9) of MACD Line
        Histogram = MACD Line - Signal Line

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        fast_period: Fast EMA period (default: 12 days)
        slow_period: Slow EMA period (default: 26 days)
        signal_period: Signal line EMA period (default: 9 days)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with columns:
        - macd_line: MACD line (fast EMA - slow EMA)
        - macd_signal: Signal line (EMA of MACD line)
        - macd_hist: Histogram (MACD - signal)
        - macd_cross: Crossover signal (1 = bullish, -1 = bearish, 0 = no cross)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 50,
        ...     "date": pd.date_range("2024-01-01", periods=50),
        ...     "close": np.cumsum(np.random.randn(50)) + 100
        ... })
        >>> result = compute_macd(df)
        >>> # macd_hist > 0: Bullish momentum
        >>> # macd_hist < 0: Bearish momentum
        >>> # macd_cross = 1: MACD crosses above signal (buy)
        >>> # macd_cross = -1: MACD crosses below signal (sell)

    Notes:
        - Positive histogram: MACD above signal (bullish)
        - Negative histogram: MACD below signal (bearish)
        - Histogram divergence from price: Potential reversal signal
        - MACD crossing signal line: Trend change signal

    See Also:
        - https://www.investopedia.com/terms/m/macd.asp
    """
    # Calculate fast and slow EMAs
    # Using span parameter for EMA (span = period)
    df = prices.with_columns(
        [
            pl.col(column).ewm_mean(span=fast_period, adjust=False).alias("ema_fast"),
            pl.col(column).ewm_mean(span=slow_period, adjust=False).alias("ema_slow"),
        ]
    )

    # Calculate MACD line
    df = df.with_columns((pl.col("ema_fast") - pl.col("ema_slow")).alias("macd_line"))

    # Calculate signal line (EMA of MACD)
    df = df.with_columns(
        pl.col("macd_line").ewm_mean(span=signal_period, adjust=False).alias("macd_signal")
    )

    # Calculate histogram
    df = df.with_columns((pl.col("macd_line") - pl.col("macd_signal")).alias("macd_hist"))

    # Detect MACD-signal crossovers
    df = df.with_columns(
        pl.when((pl.col("macd_hist") > 0) & (pl.col("macd_hist").shift(1) <= 0))
        .then(1)  # Bullish cross
        .when((pl.col("macd_hist") < 0) & (pl.col("macd_hist").shift(1) >= 0))
        .then(-1)  # Bearish cross
        .otherwise(0)
        .alias("macd_cross")
    )

    # Drop intermediate columns
    return df.drop(["ema_fast", "ema_slow"])


def compute_rate_of_change(
    prices: pl.DataFrame, period: int = 14, column: str = "close"
) -> pl.DataFrame:
    """
    Compute Rate of Change (ROC) momentum indicator.

    ROC measures the percentage change in price over a given period. It
    oscillates above and below zero, indicating momentum direction and strength.

    Formula:
        ROC = ((Price - Price_n_periods_ago) / Price_n_periods_ago) * 100

    Args:
        prices: DataFrame with at least 'symbol', 'date', and price columns
        period: Lookback period for ROC calculation (default: 14 days)
        column: Price column to use (default: "close")

    Returns:
        DataFrame with 'roc' column (percentage change)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 30,
        ...     "date": pd.date_range("2024-01-01", periods=30),
        ...     "close": [100, 102, 105, 103, 108, ...]
        ... })
        >>> result = compute_rate_of_change(df, period=14)
        >>> # ROC > 0: Positive momentum (price rising)
        >>> # ROC < 0: Negative momentum (price falling)
        >>> # ROC > 10: Strong bullish momentum
        >>> # ROC < -10: Strong bearish momentum

    Notes:
        - ROC > 0: Price higher than n periods ago (bullish)
        - ROC < 0: Price lower than n periods ago (bearish)
        - Magnitude indicates strength of momentum
        - Extreme values may signal overbought/oversold conditions

    See Also:
        - https://www.investopedia.com/terms/r/rateofchange.asp
    """
    # Calculate price n periods ago
    df = prices.with_columns(pl.col(column).shift(period).alias("price_n_ago"))

    # Calculate ROC as percentage change
    df = df.with_columns(
        ((pl.col(column) - pl.col("price_n_ago")) / pl.col("price_n_ago") * 100.0).alias("roc")
    )

    # Drop intermediate column
    return df.drop("price_n_ago")


def compute_adx(prices: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """
    Compute ADX (Average Directional Index) trend strength indicator.

    ADX measures the strength of a trend (regardless of direction). High ADX
    indicates strong trend, low ADX indicates weak/ranging market.

    Formula:
        1. Calculate +DM and -DM (directional movements)
        2. Calculate +DI and -DI (directional indicators)
        3. Calculate DX = |+DI - -DI| / |+DI + -DI| * 100
        4. ADX = SMA of DX over period

    Args:
        prices: DataFrame with 'symbol', 'date', 'high', 'low', 'close' columns
        period: Lookback period for ADX calculation (default: 14 days)

    Returns:
        DataFrame with columns:
        - adx: Average Directional Index (0-100)
        - plus_di: Positive Directional Indicator
        - minus_di: Negative Directional Indicator

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 30,
        ...     "date": pd.date_range("2024-01-01", periods=30),
        ...     "high": [...],
        ...     "low": [...],
        ...     "close": [...]
        ... })
        >>> result = compute_adx(df, period=14)
        >>> # ADX > 25: Strong trend
        >>> # ADX < 20: Weak/ranging market
        >>> # +DI > -DI: Uptrend
        >>> # +DI < -DI: Downtrend

    Notes:
        - ADX > 25: Strong trend (good for trend-following)
        - ADX < 20: Weak trend (avoid trend-following)
        - ADX direction: Rising ADX = strengthening trend
        - +DI/-DI crossover: Potential trend reversal

    See Also:
        - https://www.investopedia.com/terms/a/adx.asp
    """
    # Calculate True Range
    df = prices.with_columns(
        [
            (pl.col("high") - pl.col("low")).alias("hl"),
            (pl.col("high") - pl.col("close").shift(1)).abs().alias("hc"),
            (pl.col("low") - pl.col("close").shift(1)).abs().alias("lc"),
        ]
    )

    df = df.with_columns(pl.max_horizontal("hl", "hc", "lc").alias("tr"))

    # Calculate directional movements
    df = df.with_columns(
        [
            (pl.col("high") - pl.col("high").shift(1)).alias("high_diff"),
            (pl.col("low").shift(1) - pl.col("low")).alias("low_diff"),
        ]
    )

    # +DM = high_diff if high_diff > low_diff and high_diff > 0, else 0
    # -DM = low_diff if low_diff > high_diff and low_diff > 0, else 0
    df = df.with_columns(
        [
            pl.when((pl.col("high_diff") > pl.col("low_diff")) & (pl.col("high_diff") > 0))
            .then(pl.col("high_diff"))
            .otherwise(0.0)
            .alias("plus_dm"),
            pl.when((pl.col("low_diff") > pl.col("high_diff")) & (pl.col("low_diff") > 0))
            .then(pl.col("low_diff"))
            .otherwise(0.0)
            .alias("minus_dm"),
        ]
    )

    # Smooth TR, +DM, -DM using Wilder's smoothing (EMA)
    alpha = 1.0 / period
    df = df.with_columns(
        [
            pl.col("tr").ewm_mean(alpha=alpha, adjust=False, min_samples=period).alias("atr"),
            pl.col("plus_dm")
            .ewm_mean(alpha=alpha, adjust=False, min_samples=period)
            .alias("plus_dm_smooth"),
            pl.col("minus_dm")
            .ewm_mean(alpha=alpha, adjust=False, min_samples=period)
            .alias("minus_dm_smooth"),
        ]
    )

    # Calculate +DI and -DI
    df = df.with_columns(
        [
            ((pl.col("plus_dm_smooth") / pl.col("atr")) * 100.0).alias("plus_di"),
            ((pl.col("minus_dm_smooth") / pl.col("atr")) * 100.0).alias("minus_di"),
        ]
    )

    # Calculate DX
    df = df.with_columns(
        (
            (pl.col("plus_di") - pl.col("minus_di")).abs()
            / (pl.col("plus_di") + pl.col("minus_di"))
            * 100.0
        ).alias("dx")
    )

    # Calculate ADX (smoothed DX)
    df = df.with_columns(pl.col("dx").rolling_mean(window_size=period).alias("adx"))

    # Drop intermediate columns
    return df.drop(
        [
            "hl",
            "hc",
            "lc",
            "tr",
            "high_diff",
            "low_diff",
            "plus_dm",
            "minus_dm",
            "atr",
            "plus_dm_smooth",
            "minus_dm_smooth",
            "dx",
        ]
    )


def compute_obv(prices: pl.DataFrame) -> pl.DataFrame:
    """
    Compute OBV (On-Balance Volume) indicator.

    OBV uses volume flow to predict changes in price. Rising OBV suggests
    accumulation (bullish), falling OBV suggests distribution (bearish).

    Formula:
        If Close > Close_previous: OBV = OBV_previous + Volume
        If Close < Close_previous: OBV = OBV_previous - Volume
        If Close = Close_previous: OBV = OBV_previous

    Args:
        prices: DataFrame with 'symbol', 'date', 'close', 'volume' columns

    Returns:
        DataFrame with 'obv' column (cumulative volume)

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 30,
        ...     "date": pd.date_range("2024-01-01", periods=30),
        ...     "close": [...],
        ...     "volume": [...]
        ... })
        >>> result = compute_obv(df)
        >>> # Rising OBV + rising price: Bullish confirmation
        >>> # Falling OBV + falling price: Bearish confirmation
        >>> # Divergence: Price up but OBV down = potential reversal

    Notes:
        - OBV rising: Accumulation (buying pressure)
        - OBV falling: Distribution (selling pressure)
        - OBV divergence from price: Potential reversal signal
        - Use OBV trend, not absolute values

    See Also:
        - https://www.investopedia.com/terms/o/onbalancevolume.asp
    """
    # Calculate price direction
    df = prices.with_columns(pl.col("close").diff().alias("price_change"))

    # Calculate signed volume (volume * direction)
    df = df.with_columns(
        pl.when(pl.col("price_change") > 0)
        .then(pl.col("volume"))
        .when(pl.col("price_change") < 0)
        .then(-pl.col("volume"))
        .otherwise(0)
        .alias("signed_volume")
    )

    # Calculate cumulative OBV
    df = df.with_columns(pl.col("signed_volume").cum_sum().alias("obv"))

    # Drop intermediate columns
    return df.drop(["price_change", "signed_volume"])


def compute_momentum_features(
    prices: pl.DataFrame,
    ma_fast_period: int = 10,
    ma_slow_period: int = 50,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    roc_period: int = 14,
    adx_period: int = 14,
) -> pl.DataFrame:
    """
    Compute all momentum features for given price data.

    This is the main function that combines all momentum indicators into a
    single feature set. Used for both research and production.

    Args:
        prices: DataFrame with OHLCV columns (open, high, low, close, volume)
                Must have 'symbol' and 'date' columns
        ma_fast_period: Fast MA period (default: 10)
        ma_slow_period: Slow MA period (default: 50)
        macd_fast: MACD fast EMA period (default: 12)
        macd_slow: MACD slow EMA period (default: 26)
        macd_signal: MACD signal line period (default: 9)
        roc_period: ROC lookback period (default: 14)
        adx_period: ADX period (default: 14)

    Returns:
        DataFrame with all input columns plus:
        - ma_fast, ma_slow: Moving averages
        - ma_diff: MA difference (trend direction)
        - ma_cross: MA crossover signal
        - macd_line, macd_signal, macd_hist: MACD components
        - macd_cross: MACD crossover signal
        - roc: Rate of change
        - adx, plus_di, minus_di: ADX trend strength
        - obv: On-balance volume

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL"] * 60,
        ...     "date": pd.date_range("2024-01-01", periods=60),
        ...     "open": [...],
        ...     "high": [...],
        ...     "low": [...],
        ...     "close": [...],
        ...     "volume": [...]
        ... })
        >>> features = compute_momentum_features(df)
        >>> print(features.columns)
        >>> # Use features for model training or signal generation

    Notes:
        - Requires minimum 60 rows for stable feature calculation
        - First 50+ rows may have null values (depends on max period)
        - All features capture different aspects of momentum
        - Feature parity: Same code used in research and production

    See Also:
        - /docs/CONCEPTS/momentum-trading.md
        - /docs/IMPLEMENTATION_GUIDES/p1t6-advanced-strategies.md
    """
    # Sort by symbol and date to ensure correct order
    df = prices.sort(["symbol", "date"])

    # Compute moving averages
    df = compute_moving_averages(df, fast_period=ma_fast_period, slow_period=ma_slow_period)

    # Compute MACD
    df = compute_macd(df, fast_period=macd_fast, slow_period=macd_slow, signal_period=macd_signal)

    # Compute Rate of Change
    df = compute_rate_of_change(df, period=roc_period)

    # Compute ADX
    df = compute_adx(df, period=adx_period)

    # Compute OBV
    df = compute_obv(df)

    return df

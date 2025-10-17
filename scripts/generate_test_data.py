"""
Generate realistic synthetic market data for testing.

This script creates adjusted OHLCV data for testing the baseline strategy.
Data includes:
- Multiple symbols (AAPL, MSFT, GOOGL)
- Historical periods (2020-2024)
- Realistic price movements (random walk with drift)
- Proper OHLCV relationships
"""

from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import polars as pl


def generate_trading_days(start_date: str, end_date: str) -> list:
    """
    Generate trading days (weekdays only, no holidays).

    This is simplified - doesn't account for market holidays.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    days = []
    current = start
    while current <= end:
        # Skip weekends (Saturday=5, Sunday=6)
        if current.weekday() < 5:
            days.append(current.date())
        current += timedelta(days=1)

    return days


def generate_price_series(
    start_price: float,
    n_days: int,
    drift: float = 0.0005,
    volatility: float = 0.02,
    seed: int = 42
) -> np.ndarray:
    """
    Generate realistic price series using geometric Brownian motion.

    Args:
        start_price: Initial price
        n_days: Number of days
        drift: Daily drift (annualized return / 252)
        volatility: Daily volatility (annualized volatility / sqrt(252))
        seed: Random seed

    Returns:
        Array of close prices
    """
    np.random.seed(seed)

    # Generate daily returns
    returns = np.random.normal(drift, volatility, n_days)

    # Convert to prices (geometric Brownian motion)
    price_multipliers = np.exp(returns)
    prices = start_price * np.cumprod(price_multipliers)

    return prices


def generate_ohlcv(close_prices: np.ndarray, base_volume: int = 10_000_000) -> dict:
    """
    Generate OHLCV data from close prices.

    Args:
        close_prices: Array of close prices
        base_volume: Base trading volume

    Returns:
        Dictionary with open, high, low, close, volume
    """
    n = len(close_prices)

    # Generate realistic OHLC
    opens = np.roll(close_prices, 1)  # Previous close
    opens[0] = close_prices[0] * 0.995  # First open slightly below first close

    # High/Low around close (±1-3%)
    highs = close_prices * (1 + np.random.uniform(0.01, 0.03, n))
    lows = close_prices * (1 - np.random.uniform(0.01, 0.03, n))

    # Ensure OHLC relationships are valid
    highs = np.maximum(highs, np.maximum(opens, close_prices))
    lows = np.minimum(lows, np.minimum(opens, close_prices))

    # Volume varies with price movement (higher volume on big moves)
    returns = np.abs(np.diff(close_prices, prepend=close_prices[0]) / close_prices)
    volume = (base_volume * (1 + returns * 5)).astype(int)

    return {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": close_prices,
        "volume": volume,
    }


def generate_symbol_data(
    symbol: str,
    dates: list,
    start_price: float,
    drift: float = 0.0005,
    volatility: float = 0.02,
    seed: int = 42,
) -> pl.DataFrame:
    """
    Generate complete dataset for one symbol.

    Args:
        symbol: Stock symbol
        dates: List of trading dates
        start_price: Initial price
        drift: Daily drift
        volatility: Daily volatility
        seed: Random seed

    Returns:
        Polars DataFrame with OHLCV data
    """
    n_days = len(dates)

    # Generate prices
    close_prices = generate_price_series(
        start_price=start_price,
        n_days=n_days,
        drift=drift,
        volatility=volatility,
        seed=seed,
    )

    # Generate OHLCV
    ohlcv = generate_ohlcv(close_prices)

    # Create DataFrame
    df = pl.DataFrame({
        "symbol": [symbol] * n_days,
        "date": dates,
        "open": ohlcv["open"],
        "high": ohlcv["high"],
        "low": ohlcv["low"],
        "close": ohlcv["close"],
        "volume": ohlcv["volume"],
    })

    return df


def main():
    """Generate test data for multiple symbols and date ranges."""
    print("=" * 60)
    print("Generating Test Data for Baseline Strategy")
    print("=" * 60)

    # Configuration
    symbols = {
        "AAPL": {"start_price": 100.0, "drift": 0.0008, "volatility": 0.022, "seed": 42},
        "MSFT": {"start_price": 200.0, "drift": 0.0010, "volatility": 0.020, "seed": 43},
        "GOOGL": {"start_price": 150.0, "drift": 0.0006, "volatility": 0.024, "seed": 44},
    }

    # Date ranges
    start_date = "2020-01-01"
    end_date = "2024-12-31"

    print(f"\nSymbols: {list(symbols.keys())}")
    print(f"Date range: {start_date} to {end_date}")

    # Generate trading days
    print("\nGenerating trading days...")
    dates = generate_trading_days(start_date, end_date)
    print(f"Generated {len(dates)} trading days")

    # Output directory
    output_dir = Path("data/adjusted")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create date partition (use today's date)
    partition_date = datetime.now().strftime("%Y-%m-%d")
    partition_dir = output_dir / partition_date
    partition_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nOutput directory: {partition_dir}")

    # Generate data for each symbol
    for symbol, params in symbols.items():
        print(f"\nGenerating {symbol}...")

        df = generate_symbol_data(
            symbol=symbol,
            dates=dates,
            start_price=params["start_price"],
            drift=params["drift"],
            volatility=params["volatility"],
            seed=params["seed"],
        )

        # Save to Parquet
        output_path = partition_dir / f"{symbol}.parquet"
        df.write_parquet(output_path)

        print(f"  Rows: {len(df)}")
        print(f"  Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
        print(f"  Saved to: {output_path}")

    print("\n" + "=" * 60)
    print("Test data generation complete!")
    print("=" * 60)
    print(f"\nData location: {partition_dir}")
    print(f"Symbols: {len(symbols)}")
    print(f"Trading days: {len(dates)}")
    print(f"Total size: ~{len(symbols) * len(dates) * 100 / 1024:.1f} KB")

    # Show sample data
    print("\nSample data (AAPL, first 5 days):")
    df = pl.read_parquet(partition_dir / "AAPL.parquet")
    print(df.head())


if __name__ == "__main__":
    main()

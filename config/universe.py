"""
Symbol universe configuration.

Defines which symbols are tradable for the MVP. This keeps the initial
implementation focused on a small, manageable set of liquid stocks.

See Phase 3/Signal Service in the implementation plan.
"""

import polars as pl

# MVP universe: Small set of liquid, well-known stocks
# These were chosen for:
# - High liquidity (tight spreads, easy to trade)
# - Well-documented corporate actions
# - Available in free/paper data sources
TRADABLE_SYMBOLS = ["AAPL", "MSFT", "GOOGL"]


def filter_universe(df: pl.DataFrame) -> pl.DataFrame:
    """
    Filter DataFrame to include only tradable symbols.

    This is applied after signal generation to ensure we only
    trade symbols in our approved universe.

    Args:
        df: DataFrame with 'symbol' column

    Returns:
        DataFrame containing only rows where symbol is in TRADABLE_SYMBOLS

    Raises:
        ValueError: If 'symbol' column is missing

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL", "TSLA", "MSFT", "AMZN"],
        ...     "signal": [0.8, 0.6, 0.7, 0.5]
        ... })
        >>> filtered = filter_universe(df)
        >>> filtered["symbol"].to_list()
        ['AAPL', 'MSFT']  # Only symbols in TRADABLE_SYMBOLS

    Notes:
        - Apply this AFTER signal generation, not before feature computation
        - This allows backtesting full universe while trading subset
        - Expand TRADABLE_SYMBOLS as system proves stable
    """
    if "symbol" not in df.columns:
        raise ValueError("DataFrame must have 'symbol' column")

    return df.filter(pl.col("symbol").is_in(TRADABLE_SYMBOLS))

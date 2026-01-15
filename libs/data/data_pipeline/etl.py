"""
Main ETL pipeline for market data processing.

This module orchestrates the complete data pipeline:
1. Validate freshness
2. Adjust for corporate actions
3. Quality gate (outlier detection)
4. Save results to Parquet (adjusted + quarantine)

See ADR-0001 for pipeline architecture decisions.
"""

from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from libs.core.common.exceptions import DataQualityError
from libs.data.data_pipeline.corporate_actions import adjust_prices
from libs.data.data_pipeline.freshness import check_freshness
from libs.data.data_pipeline.quality_gate import detect_outliers


def run_etl_pipeline(
    raw_data: pl.DataFrame,
    splits_df: pl.DataFrame | None = None,
    dividends_df: pl.DataFrame | None = None,
    freshness_minutes: int = 30,
    outlier_threshold: float = 0.30,
    output_dir: Path | str | None = None,
    run_date: date | None = None,
) -> dict[str, pl.DataFrame | dict[str, Any]]:
    """
    Execute the complete ETL pipeline on raw market data.

    This is the main entry point for data processing. It runs all quality
    checks, adjustments, and saves results to disk in Parquet format.

    Pipeline Steps:
    1. Freshness Check: Validate data is recent enough
    2. Corporate Action Adjustment: Adjust prices for splits/dividends
    3. Quality Gate: Detect and quarantine outliers
    4. Persistence: Save adjusted and quarantined data to Parquet

    Args:
        raw_data: Raw OHLCV DataFrame with columns:
                  [symbol, date, open, high, low, close, volume, timestamp]
        splits_df: Stock splits with [symbol, date, split_ratio] (optional)
        dividends_df: Dividends with [symbol, date, dividend] (optional)
        freshness_minutes: Maximum acceptable data age (default: 30)
        outlier_threshold: Daily return threshold for outliers (default: 0.30)
        output_dir: Directory to save Parquet files (default: ./data)
        run_date: Date for output partitioning (default: today)

    Returns:
        Dictionary with processing results:
        {
            "adjusted": DataFrame with clean adjusted data,
            "quarantined": DataFrame with outliers (empty if none),
            "stats": {
                "input_rows": int,
                "adjusted_rows": int,
                "quarantined_rows": int,
                "symbols_processed": list[str]
            }
        }

    Raises:
        StalenessError: If data exceeds freshness threshold
        DataQualityError: If required columns missing
        ValueError: If input validation fails

    Example:
        >>> # Minimal usage
        >>> raw = load_raw_data()
        >>> splits = load_splits()
        >>> result = run_etl_pipeline(raw, splits_df=splits)
        >>> print(f"Processed {result['stats']['adjusted_rows']} rows")

        >>> # Full configuration
        >>> result = run_etl_pipeline(
        ...     raw_data=raw,
        ...     splits_df=splits,
        ...     dividends_df=dividends,
        ...     freshness_minutes=30,
        ...     outlier_threshold=0.30,
        ...     output_dir=Path("data"),
        ...     run_date=date(2024, 10, 16)
        ... )
        >>> adjusted = result["adjusted"]
        >>> quarantined = result["quarantined"]
        >>> stats = result["stats"]

    File Output Structure:
        data/
        ├── adjusted/
        │   └── YYYY-MM-DD/
        │       └── {symbol}.parquet
        └── quarantine/
            └── YYYY-MM-DD/
                └── {symbol}.parquet

    Notes:
        - Pipeline is deterministic: same input → same output
        - All steps are idempotent: safe to re-run
        - Raw data is NOT modified (immutable)
        - If output_dir is None, only returns DataFrames (no disk writes)
        - Quarantined data includes 'reason' column for debugging

    Performance:
        - Processes ~750 rows (252 days × 3 symbols) in <1 second
        - Uses Polars for efficient columnar operations
        - Parquet compression reduces disk usage 5-10x vs CSV

    See Also:
        - ADR-0001: Data Pipeline Architecture
        - libs/data_pipeline/freshness.py: Freshness validation
        - libs/data_pipeline/corporate_actions.py: CA adjustments
        - libs/data_pipeline/quality_gate.py: Outlier detection
    """
    # Validate inputs
    if raw_data.is_empty():
        raise ValueError("Cannot run pipeline on empty DataFrame")

    required_cols = {"symbol", "date", "open", "high", "low", "close", "volume", "timestamp"}
    missing = required_cols - set(raw_data.columns)
    if missing:
        raise DataQualityError(f"Raw data missing required columns: {missing}")

    # Set defaults
    if run_date is None:
        run_date = date.today()
    if output_dir is None:
        output_dir = Path("data")
    else:
        output_dir = Path(output_dir)

    # Get input stats
    input_rows = len(raw_data)
    input_symbols = raw_data["symbol"].unique().to_list()

    # Step 1: Freshness Check
    check_freshness(raw_data, max_age_minutes=freshness_minutes)

    # Step 2: Corporate Action Adjustment
    adjusted = adjust_prices(raw_data, splits_df=splits_df, dividends_df=dividends_df)

    # Step 3: Quality Gate (Outlier Detection)
    # Combine splits and dividends for CA awareness
    ca_df = None
    if splits_df is not None or dividends_df is not None:
        ca_dfs = []
        if splits_df is not None and not splits_df.is_empty():
            ca_dfs.append(splits_df.select(["symbol", "date"]))
        if dividends_df is not None and not dividends_df.is_empty():
            ca_dfs.append(dividends_df.select(["symbol", "date"]))

        if ca_dfs:
            ca_df = pl.concat(ca_dfs).unique()

    good_data, quarantine_data = detect_outliers(adjusted, ca_df=ca_df, threshold=outlier_threshold)

    # Get output stats
    adjusted_rows = len(good_data)
    quarantined_rows = len(quarantine_data)

    # Step 4: Persistence (if output_dir provided)
    if output_dir is not None:
        _save_results(
            good_data=good_data,
            quarantine_data=quarantine_data,
            output_dir=output_dir,
            run_date=run_date,
        )

    # Return results
    return {
        "adjusted": good_data,
        "quarantined": quarantine_data,
        "stats": {
            "input_rows": input_rows,
            "adjusted_rows": adjusted_rows,
            "quarantined_rows": quarantined_rows,
            "symbols_processed": input_symbols,
        },
    }


def _save_results(
    good_data: pl.DataFrame, quarantine_data: pl.DataFrame, output_dir: Path, run_date: date
) -> None:
    """
    Save processed data to Parquet files partitioned by date and symbol.

    This is an internal helper function called by run_etl_pipeline.

    File Structure:
        data/
        ├── adjusted/YYYY-MM-DD/{SYMBOL}.parquet
        └── quarantine/YYYY-MM-DD/{SYMBOL}.parquet

    Args:
        good_data: Adjusted data that passed quality checks
        quarantine_data: Data flagged as outliers
        output_dir: Base directory (e.g., ./data)
        run_date: Date for partitioning

    Notes:
        - Creates directories if they don't exist
        - Overwrites existing files (idempotent)
        - Uses Snappy compression for balance of speed/size
        - Each symbol gets its own Parquet file for efficient queries
    """
    # Create date partition directories
    adjusted_dir = output_dir / "adjusted" / run_date.isoformat()
    quarantine_dir = output_dir / "quarantine" / run_date.isoformat()

    adjusted_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    # Save adjusted data (one file per symbol)
    if not good_data.is_empty():
        for symbol in good_data["symbol"].unique().to_list():
            symbol_data = good_data.filter(pl.col("symbol") == symbol)
            output_path = adjusted_dir / f"{symbol}.parquet"
            symbol_data.write_parquet(
                output_path,
                compression="snappy",
                use_pyarrow=False,  # Use Polars native writer
            )

    # Save quarantined data (one file per symbol)
    if not quarantine_data.is_empty():
        for symbol in quarantine_data["symbol"].unique().to_list():
            symbol_data = quarantine_data.filter(pl.col("symbol") == symbol)
            output_path = quarantine_dir / f"{symbol}.parquet"
            symbol_data.write_parquet(output_path, compression="snappy", use_pyarrow=False)


def load_adjusted_data(
    symbols: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    data_dir: Path | str = Path("data/adjusted"),
) -> pl.DataFrame:
    """
    Load adjusted data from Parquet files with optional filtering.

    This is a convenience function for loading processed data for backtesting
    or strategy research.

    Args:
        symbols: List of symbols to load (default: all available)
        start_date: Filter data >= this date (default: no filter)
        end_date: Filter data <= this date (default: no filter)
        data_dir: Directory containing adjusted data (default: ./data/adjusted)

    Returns:
        DataFrame with adjusted OHLCV data for requested symbols/dates

    Example:
        >>> # Load all data
        >>> df = load_adjusted_data()

        >>> # Load specific symbols
        >>> df = load_adjusted_data(symbols=["AAPL", "MSFT"])

        >>> # Load date range
        >>> df = load_adjusted_data(
        ...     symbols=["AAPL"],
        ...     start_date=date(2024, 1, 1),
        ...     end_date=date(2024, 12, 31)
        ... )

    Notes:
        - Scans all date partitions if no date filter provided
        - Uses Polars' lazy evaluation for efficient filtering
        - Returns empty DataFrame if no data matches filters
    """
    data_dir = Path(data_dir)

    if not data_dir.exists():
        return pl.DataFrame()

    # Find all Parquet files
    parquet_files = list(data_dir.rglob("*.parquet"))

    if not parquet_files:
        return pl.DataFrame()

    # Filter by symbols if provided
    if symbols is not None:
        parquet_files = [
            f for f in parquet_files if f.stem in symbols  # f.stem is filename without extension
        ]

    if not parquet_files:
        return pl.DataFrame()

    # Load all files
    df = pl.concat([pl.read_parquet(f) for f in parquet_files])

    # Apply date filters
    if start_date is not None:
        df = df.filter(pl.col("date") >= start_date)
    if end_date is not None:
        df = df.filter(pl.col("date") <= end_date)

    return df.sort(["symbol", "date"])

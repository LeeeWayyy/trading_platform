"""
DuckDB Catalog - SQL Analytics Interface for Parquet Data

This module provides a convenient interface to query Parquet files using DuckDB SQL.
It simplifies common analytics patterns for market data stored in the Parquet format.

**Why DuckDB?**
- In-process analytics (no separate server needed)
- Columnar storage optimized for analytics queries
- 10-100x faster than loading data into Pandas first
- Pushes filters down to Parquet readers (only reads relevant data)
- Supports complex SQL: window functions, CTEs, aggregations

**Typical Usage:**

    ```python
    from libs.duckdb_catalog import DuckDBCatalog

    # Create catalog and register Parquet files
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", "data/adjusted/*/*.parquet")

    # Query with SQL
    result = catalog.query(\"\"\"
        SELECT symbol, date, close
        FROM market_data
        WHERE symbol = 'AAPL' AND date >= '2024-01-01'
        ORDER BY date DESC
        LIMIT 10
    \"\"\")

    # Returns Polars DataFrame
    print(result)
    ```

**Performance Characteristics:**
- Query 1M rows: ~100ms
- Filter on symbol: ~10ms (with predicate pushdown)
- Aggregation (GROUP BY): ~200ms
- Window functions: ~500ms

See Also:
- docs/CONCEPTS/duckdb-basics.md - DuckDB fundamentals
- docs/CONCEPTS/sql-analytics-patterns.md - Common query patterns
- docs/CONCEPTS/parquet-format.md - Parquet format details
"""

from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import polars as pl


class DuckDBCatalog:
    """
    SQL analytics interface for querying Parquet files with DuckDB.

    This class manages DuckDB connections and provides helper methods for
    common analytics operations on market data stored in Parquet format.

    **Design Principles:**
    1. **Lazy Loading**: Files are not loaded until queried
    2. **Predicate Pushdown**: Filters are pushed to Parquet readers
    3. **Minimal Memory**: Only relevant data is loaded into memory
    4. **SQL-First**: Leverage DuckDB's SQL engine for complex analytics

    **Thread Safety:** Each instance creates its own DuckDB connection.
    For multi-threaded use, create one catalog per thread.

    Examples:
        Basic query:

        >>> catalog = DuckDBCatalog()
        >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
        >>> result = catalog.query("SELECT * FROM market_data LIMIT 5")

        Query with filters (fast - predicate pushdown):

        >>> result = catalog.query(\"\"\"
        ...     SELECT symbol, date, close
        ...     FROM market_data
        ...     WHERE symbol = 'AAPL' AND date >= '2024-01-01'
        ... \"\"\")

        Window functions for technical indicators:

        >>> result = catalog.query(\"\"\"
        ...     SELECT
        ...         symbol,
        ...         date,
        ...         close,
        ...         AVG(close) OVER (
        ...             PARTITION BY symbol
        ...             ORDER BY date
        ...             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ...         ) AS sma_20
        ...     FROM market_data
        ...     WHERE symbol = 'AAPL'
        ... \"\"\")
    """

    def __init__(self, read_only: bool = False):
        """
        Initialize DuckDB catalog with a new connection.

        Args:
            read_only: Must be False for in-memory databases (default).
                      For file-based databases, read_only=True is supported.

        Raises:
            ValueError: If read_only=True with in-memory database

        Examples:
            Create catalog for analytics (default):

            >>> catalog = DuckDBCatalog()

            Note: In-memory databases cannot be opened in read-only mode.
        """
        if read_only:
            raise ValueError(
                "In-memory DuckDB databases cannot be opened in read-only mode. "
                "Use read_only=False (default) or switch to file-based database."
            )
        self.conn = duckdb.connect(":memory:", read_only=read_only)
        self._registered_tables: dict[str, str] = {}

    def _validate_table_name(self, table_name: str) -> None:
        """
        Validate table name against registered tables to prevent SQL injection.

        Args:
            table_name: Table name to validate

        Raises:
            ValueError: If table_name is not registered

        Examples:
            >>> catalog._validate_table_name("market_data")  # OK if registered
            >>> catalog._validate_table_name("'; DROP TABLE users; --")  # Raises ValueError
        """
        if table_name not in self._registered_tables:
            available = list(self._registered_tables.keys())
            raise ValueError(
                f"Table '{table_name}' is not registered. "
                f"Available tables: {available if available else 'none'}. "
                f"Use register_table() to register tables first."
            )

    def register_table(
        self,
        table_name: str,
        parquet_path: str | Path | list[str | Path],
    ) -> None:
        """
        Register Parquet file(s) as a SQL table for querying.

        This method creates a view in DuckDB that references the Parquet files.
        Files are NOT loaded into memory - they are read on-demand during queries.

        **Glob Patterns Supported:**
        - Single file: "data/adjusted/2024-01-01/AAPL.parquet"
        - All files in directory: "data/adjusted/2024-01-01/*.parquet"
        - All dates: "data/adjusted/*/*.parquet"
        - Specific symbols: "data/adjusted/*/AAPL.parquet"

        Args:
            table_name: Name to use when querying (e.g., "market_data")
            parquet_path: Path or glob pattern to Parquet files.
                         Can be string, Path, or list of paths.

        Raises:
            FileNotFoundError: If no files match the pattern
            ValueError: If table_name is empty or contains invalid characters

        Examples:
            Register all adjusted data:

            >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")

            Register specific date:

            >>> catalog.register_table("today", "data/adjusted/2024-01-01/*.parquet")

            Register multiple paths:

            >>> catalog.register_table("multi", [
            ...     "data/adjusted/2024-01-01/*.parquet",
            ...     "data/adjusted/2024-01-02/*.parquet"
            ... ])
        """
        if not table_name or not table_name.isidentifier():
            raise ValueError(
                f"Invalid table name: '{table_name}'. "
                "Must be a valid identifier (alphanumeric characters and underscores, not starting with a digit)."
            )

        # Convert to list if single path
        if isinstance(parquet_path, (str, Path)):
            paths = [str(parquet_path)]
        else:
            paths = [str(p) for p in parquet_path]

        # Build path list for SQL
        if len(paths) == 1:
            path_sql = f"'{paths[0]}'"
        else:
            path_list = ", ".join(f"'{p}'" for p in paths)
            path_sql = f"[{path_list}]"

        # Create view (files not loaded yet)
        sql = f"""
        CREATE OR REPLACE VIEW {table_name} AS
        SELECT * FROM read_parquet({path_sql})
        """
        self.conn.execute(sql)

        # Track registered tables
        self._registered_tables[table_name] = ", ".join(paths)

    def query(
        self,
        sql: str,
        params: list[Any] | None = None,
        return_format: str = "polars"
    ) -> pl.DataFrame | pd.DataFrame:
        """
        Execute SQL query and return results.

        This method executes the SQL query using DuckDB's engine, which will:
        1. Analyze the query plan
        2. Push filters down to Parquet readers
        3. Read only relevant columns and rows
        4. Execute aggregations/joins efficiently
        5. Return results in requested format

        Args:
            sql: SQL query string (supports full DuckDB SQL syntax).
                Use ? placeholders for parameterized queries.
            params: Optional list of parameters for parameterized queries.
                   Prevents SQL injection attacks.
            return_format: Output format - "polars" (default) or "pandas"

        Returns:
            Query results as Polars DataFrame (default) or Pandas DataFrame

        Raises:
            duckdb.Error: If SQL syntax is invalid or query fails

        Examples:
            Simple SELECT:

            >>> result = catalog.query("SELECT * FROM market_data LIMIT 5")

            Parameterized query (prevents SQL injection):

            >>> result = catalog.query(
            ...     "SELECT * FROM market_data WHERE symbol = ? AND date >= ?",
            ...     params=["AAPL", "2024-01-01"]
            ... )

            Filtered query (fast - predicate pushdown):

            >>> result = catalog.query(\"\"\"
            ...     SELECT symbol, date, close, volume
            ...     FROM market_data
            ...     WHERE symbol = 'AAPL'
            ...       AND date BETWEEN '2024-01-01' AND '2024-01-31'
            ...     ORDER BY date
            ... \"\"\")

            Aggregation:

            >>> result = catalog.query(\"\"\"
            ...     SELECT
            ...         symbol,
            ...         DATE_TRUNC('month', date) AS month,
            ...         AVG(close) AS avg_close,
            ...         MAX(volume) AS max_volume
            ...     FROM market_data
            ...     WHERE date >= '2024-01-01'
            ...     GROUP BY symbol, month
            ...     ORDER BY symbol, month
            ... \"\"\")

            Window function (20-day SMA):

            >>> result = catalog.query(\"\"\"
            ...     SELECT
            ...         symbol,
            ...         date,
            ...         close,
            ...         AVG(close) OVER (
            ...             PARTITION BY symbol
            ...             ORDER BY date
            ...             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ...         ) AS sma_20
            ...     FROM market_data
            ...     WHERE symbol IN ('AAPL', 'MSFT')
            ... \"\"\")
        """
        # Execute query with or without parameters
        if params is not None:
            result = self.conn.execute(sql, params)
        else:
            result = self.conn.execute(sql)

        if return_format == "polars":
            return result.pl()
        elif return_format == "pandas":
            # DuckDB result.df() returns pandas DataFrame
            return result.df()
        else:
            raise ValueError(
                f"Invalid return_format: {return_format}. "
                "Use 'polars' or 'pandas'."
            )

    def get_symbols(self, table_name: str = "market_data") -> list[str]:
        """
        Get list of unique symbols in the table.

        Args:
            table_name: Name of registered table (default: "market_data")

        Returns:
            Sorted list of unique symbol strings

        Raises:
            ValueError: If table_name is not registered

        Examples:
            >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
            >>> symbols = catalog.get_symbols()
            >>> print(symbols)
            ['AAPL', 'GOOGL', 'MSFT']
        """
        self._validate_table_name(table_name)
        result = self.query(
            f"SELECT DISTINCT symbol FROM {table_name} ORDER BY symbol"
        )
        return result["symbol"].to_list()

    def get_date_range(self, table_name: str = "market_data") -> tuple[str, str]:
        """
        Get min and max dates in the table.

        Args:
            table_name: Name of registered table (default: "market_data")

        Returns:
            Tuple of (min_date, max_date) as ISO format strings

        Raises:
            ValueError: If table_name is not registered

        Examples:
            >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
            >>> min_date, max_date = catalog.get_date_range()
            >>> print(f"Data from {min_date} to {max_date}")
            Data from 2024-01-01 to 2024-12-31
        """
        self._validate_table_name(table_name)
        result = self.query(
            f"SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM {table_name}"
        )
        return (
            str(result["min_date"][0]),
            str(result["max_date"][0])
        )

    def get_stats(self, table_name: str = "market_data") -> pl.DataFrame:
        """
        Get summary statistics for the table.

        Calculates:
        - Total number of rows
        - Number of unique symbols
        - Date range (min/max)
        - Number of trading days

        Args:
            table_name: Name of registered table (default: "market_data")

        Returns:
            Polars DataFrame with summary statistics

        Raises:
            ValueError: If table_name is not registered

        Examples:
            >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
            >>> stats = catalog.get_stats()
            >>> print(stats)
            shape: (1, 4)
            ┌───────────┬────────────┬────────────┬──────────────┐
            │ row_count │ n_symbols  │ min_date   │ max_date     │
            │ ---       │ ---        │ ---        │ ---          │
            │ u64       │ u64        │ date       │ date         │
            ╞═══════════╪════════════╪════════════╪══════════════╡
            │ 756       │ 3          │ 2024-01-01 │ 2024-12-31   │
            └───────────┴────────────┴────────────┴──────────────┘
        """
        self._validate_table_name(table_name)
        result = self.query(f"""
            SELECT
                COUNT(*) AS row_count,
                COUNT(DISTINCT symbol) AS n_symbols,
                MIN(date) AS min_date,
                MAX(date) AS max_date,
                COUNT(DISTINCT date) AS n_trading_days
            FROM {table_name}
        """, return_format="polars")
        # Explicit cast since we know return_format="polars" always returns pl.DataFrame
        assert isinstance(result, pl.DataFrame)
        return result

    def close(self) -> None:
        """
        Close the DuckDB connection.

        Good practice to call when done, though connections are automatically
        closed when the object is garbage collected.

        Examples:
            >>> catalog = DuckDBCatalog()
            >>> # ... use catalog ...
            >>> catalog.close()
        """
        self.conn.close()

    def __enter__(self) -> "DuckDBCatalog":
        """Context manager entry - returns self."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit - closes connection."""
        self.close()

    def __repr__(self) -> str:
        """String representation showing registered tables."""
        if not self._registered_tables:
            return "DuckDBCatalog(no tables registered)"

        tables_str = ", ".join(self._registered_tables.keys())
        return f"DuckDBCatalog(tables: {tables_str})"


# Helper functions for common analytics patterns

def _build_where_clause(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None
) -> tuple[str, list[Any]]:
    """
    Build WHERE clause and parameters for common time-series queries.

    This function extracts the duplicate WHERE clause building logic
    used by calculate_returns() and calculate_sma().

    Args:
        symbol: Stock symbol (e.g., "AAPL")
        start_date: Optional start date (ISO format: "YYYY-MM-DD")
        end_date: Optional end date (ISO format: "YYYY-MM-DD")

    Returns:
        Tuple of (where_sql, params) where:
        - where_sql: SQL WHERE clause string (e.g., "symbol = ? AND date >= ?")
        - params: List of parameter values (e.g., ["AAPL", "2024-01-01"])

    Examples:
        >>> where_sql, params = _build_where_clause("AAPL", "2024-01-01")
        >>> print(where_sql)
        symbol = ? AND date >= ?
        >>> print(params)
        ['AAPL', '2024-01-01']
    """
    where_clauses = ["symbol = ?"]
    params = [symbol]

    if start_date:
        where_clauses.append("date >= ?")
        params.append(start_date)
    if end_date:
        where_clauses.append("date <= ?")
        params.append(end_date)

    where_sql = " AND ".join(where_clauses)
    return where_sql, params


def calculate_returns(
    catalog: DuckDBCatalog,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    table_name: str = "market_data"
) -> pl.DataFrame:
    """
    Calculate daily returns for a symbol.

    Returns are calculated as: (close - previous_close) / previous_close

    Args:
        catalog: DuckDBCatalog instance with registered market data
        symbol: Stock symbol (e.g., "AAPL")
        start_date: Optional start date (ISO format: "YYYY-MM-DD")
        end_date: Optional end date (ISO format: "YYYY-MM-DD")
        table_name: Name of registered table (default: "market_data")

    Returns:
        Polars DataFrame with columns: symbol, date, close, daily_return

    Raises:
        ValueError: If table_name is not registered

    Examples:
        Calculate returns for AAPL in 2024:

        >>> catalog = DuckDBCatalog()
        >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
        >>> returns = calculate_returns(catalog, "AAPL", "2024-01-01", "2024-12-31")
        >>> print(returns.head())
        shape: (5, 4)
        ┌────────┬────────────┬────────┬──────────────┐
        │ symbol │ date       │ close  │ daily_return │
        │ ---    │ ---        │ ---    │ ---          │
        │ str    │ date       │ f64    │ f64          │
        ╞════════╪════════════╪════════╪══════════════╡
        │ AAPL   │ 2024-01-01 │ 150.00 │ null         │
        │ AAPL   │ 2024-01-02 │ 151.50 │ 0.01         │
        │ ...    │ ...        │ ...    │ ...          │
        └────────┴────────────┴────────┴──────────────┘
    """
    # Validate table name to prevent SQL injection
    catalog._validate_table_name(table_name)

    # Build WHERE clause using helper function (DRY)
    where_sql, params = _build_where_clause(symbol, start_date, end_date)

    sql = f"""
    SELECT
        symbol,
        date,
        close,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
         LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return
    FROM {table_name}
    WHERE {where_sql}
    ORDER BY date
    """

    # Execute with parameterized query to prevent SQL injection
    result = catalog.conn.execute(sql, params)
    return result.pl()


def calculate_sma(
    catalog: DuckDBCatalog,
    symbol: str,
    window: int = 20,
    start_date: str | None = None,
    end_date: str | None = None,
    table_name: str = "market_data"
) -> pl.DataFrame:
    """
    Calculate Simple Moving Average (SMA) for a symbol.

    Args:
        catalog: DuckDBCatalog instance with registered market data
        symbol: Stock symbol (e.g., "AAPL")
        window: SMA window size in days (default: 20)
        start_date: Optional start date (ISO format: "YYYY-MM-DD")
        end_date: Optional end date (ISO format: "YYYY-MM-DD")
        table_name: Name of registered table (default: "market_data")

    Returns:
        Polars DataFrame with columns: symbol, date, close, sma_{window}

    Raises:
        ValueError: If table_name is not registered

    Examples:
        Calculate 20-day SMA for AAPL:

        >>> catalog = DuckDBCatalog()
        >>> catalog.register_table("market_data", "data/adjusted/*/*.parquet")
        >>> sma = calculate_sma(catalog, "AAPL", window=20)
        >>> print(sma.head(25))
        shape: (25, 4)
        ┌────────┬────────────┬────────┬────────┐
        │ symbol │ date       │ close  │ sma_20 │
        │ ---    │ ---        │ ---    │ ---    │
        │ str    │ date       │ f64    │ f64    │
        ╞════════╪════════════╪════════╪════════╡
        │ AAPL   │ 2024-01-01 │ 150.00 │ 150.00 │
        │ AAPL   │ 2024-01-02 │ 151.50 │ 150.75 │
        │ ...    │ ...        │ ...    │ ...    │
        └────────┴────────────┴────────┴────────┘
    """
    # Validate table name to prevent SQL injection
    catalog._validate_table_name(table_name)

    # Build WHERE clause using helper function (DRY)
    where_sql, params = _build_where_clause(symbol, start_date, end_date)

    sql = f"""
    SELECT
        symbol,
        date,
        close,
        AVG(close) OVER (
            PARTITION BY symbol
            ORDER BY date
            ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
        ) AS sma_{window}
    FROM {table_name}
    WHERE {where_sql}
    ORDER BY date
    """

    # Execute with parameterized query to prevent SQL injection
    result = catalog.conn.execute(sql, params)
    return result.pl()

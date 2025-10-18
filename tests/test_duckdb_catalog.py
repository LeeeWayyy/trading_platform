"""
Tests for DuckDB Catalog - SQL Analytics Interface

This test suite validates:
1. Connection management
2. Table registration (single/multiple files, glob patterns)
3. SQL query execution
4. Helper functions (returns, SMA)
5. Error handling
6. Performance benchmarks

Test data structure:
- Uses mock market data for AAPL, MSFT, GOOGL
- Date range: 2024-01-01 to 2024-12-31 (252 trading days)
- Creates temporary Parquet files for testing
"""

import pytest
import polars as pl
import duckdb
from pathlib import Path
from datetime import date, timedelta
import shutil

from libs.duckdb_catalog import (
    DuckDBCatalog,
    calculate_returns,
    calculate_sma,
)


@pytest.fixture
def temp_data_dir(tmp_path):
    """
    Create temporary directory with mock Parquet files.

    Directory structure:
    tmp_path/
      adjusted/
        2024-01-01/
          AAPL.parquet
          MSFT.parquet
          GOOGL.parquet
        2024-01-02/
          AAPL.parquet
          MSFT.parquet
          GOOGL.parquet
        ...
    """
    data_dir = tmp_path / "adjusted"
    data_dir.mkdir()

    # Generate 30 days of mock data (enough for testing)
    symbols = ["AAPL", "MSFT", "GOOGL"]
    start_date = date(2024, 1, 1)
    n_days = 30

    for day_offset in range(n_days):
        current_date = start_date + timedelta(days=day_offset)
        date_dir = data_dir / current_date.isoformat()
        date_dir.mkdir()

        for symbol in symbols:
            # Create mock data for this symbol on this date
            df = pl.DataFrame({
                "symbol": [symbol],
                "date": [current_date],
                "open": [100.0 + day_offset],
                "high": [105.0 + day_offset],
                "low": [95.0 + day_offset],
                "close": [100.0 + day_offset + (hash(symbol) % 10)],
                "volume": [1000000 + day_offset * 10000],
            })

            # Write to Parquet
            file_path = date_dir / f"{symbol}.parquet"
            df.write_parquet(file_path)

    return data_dir


# ============================================================================
# Basic Functionality Tests
# ============================================================================


def test_catalog_creation():
    """Test DuckDB catalog can be created."""
    catalog = DuckDBCatalog()
    assert catalog.conn is not None
    assert isinstance(catalog._registered_tables, dict)
    catalog.close()


def test_catalog_context_manager():
    """Test catalog works with context manager (auto-close)."""
    with DuckDBCatalog() as catalog:
        assert catalog.conn is not None

    # Connection should be closed after exiting context
    # (no easy way to test this without triggering error)


def test_catalog_repr():
    """Test string representation of catalog."""
    catalog = DuckDBCatalog()
    assert repr(catalog) == "DuckDBCatalog(no tables registered)"

    catalog._registered_tables["test"] = "path/to/test.parquet"
    assert "test" in repr(catalog)
    catalog.close()


# ============================================================================
# Table Registration Tests
# ============================================================================


def test_register_single_file(temp_data_dir):
    """Test registering a single Parquet file."""
    catalog = DuckDBCatalog()

    # Register single file
    single_file = temp_data_dir / "2024-01-01" / "AAPL.parquet"
    catalog.register_table("aapl_day1", str(single_file))

    # Query should return 1 row
    result = catalog.query("SELECT * FROM aapl_day1")
    assert len(result) == 1
    assert result["symbol"][0] == "AAPL"

    catalog.close()


def test_register_glob_pattern(temp_data_dir):
    """Test registering files with glob pattern."""
    catalog = DuckDBCatalog()

    # Register all files for 2024-01-01
    pattern = str(temp_data_dir / "2024-01-01" / "*.parquet")
    catalog.register_table("day1", pattern)

    # Should have 3 rows (AAPL, MSFT, GOOGL)
    result = catalog.query("SELECT DISTINCT symbol FROM day1 ORDER BY symbol")
    assert len(result) == 3
    assert result["symbol"].to_list() == ["AAPL", "GOOGL", "MSFT"]

    catalog.close()


def test_register_all_dates(temp_data_dir):
    """Test registering all dates with glob pattern."""
    catalog = DuckDBCatalog()

    # Register all files across all dates
    pattern = str(temp_data_dir / "*" / "*.parquet")
    catalog.register_table("market_data", pattern)

    # Should have 30 days * 3 symbols = 90 rows
    result = catalog.query("SELECT COUNT(*) AS count FROM market_data")
    assert result["count"][0] == 90

    catalog.close()


def test_register_multiple_paths(temp_data_dir):
    """Test registering multiple explicit paths."""
    catalog = DuckDBCatalog()

    # Register two specific dates
    paths = [
        str(temp_data_dir / "2024-01-01" / "*.parquet"),
        str(temp_data_dir / "2024-01-02" / "*.parquet"),
    ]
    catalog.register_table("two_days", paths)

    # Should have 2 days * 3 symbols = 6 rows
    result = catalog.query("SELECT COUNT(*) AS count FROM two_days")
    assert result["count"][0] == 6

    catalog.close()


def test_register_replace_existing(temp_data_dir):
    """Test that re-registering a table replaces it."""
    catalog = DuckDBCatalog()

    # Register day 1
    pattern1 = str(temp_data_dir / "2024-01-01" / "*.parquet")
    catalog.register_table("test_table", pattern1)
    result1 = catalog.query("SELECT COUNT(*) AS count FROM test_table")
    assert result1["count"][0] == 3

    # Replace with day 2
    pattern2 = str(temp_data_dir / "2024-01-02" / "*.parquet")
    catalog.register_table("test_table", pattern2)
    result2 = catalog.query("SELECT COUNT(*) AS count FROM test_table")
    assert result2["count"][0] == 3

    catalog.close()


def test_invalid_table_name():
    """Test that invalid table names raise ValueError."""
    catalog = DuckDBCatalog()

    with pytest.raises(ValueError, match="Invalid table name"):
        catalog.register_table("", "dummy.parquet")

    with pytest.raises(ValueError, match="Invalid table name"):
        catalog.register_table("table-with-dash", "dummy.parquet")

    with pytest.raises(ValueError, match="Invalid table name"):
        catalog.register_table("table with space", "dummy.parquet")

    catalog.close()


# ============================================================================
# Query Tests
# ============================================================================


def test_simple_select(temp_data_dir):
    """Test simple SELECT query."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    result = catalog.query("SELECT * FROM market_data LIMIT 5")
    assert len(result) == 5
    assert "symbol" in result.columns
    assert "date" in result.columns
    assert "close" in result.columns

    catalog.close()


def test_filtered_query(temp_data_dir):
    """Test query with WHERE clause (predicate pushdown)."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Filter to AAPL only
    result = catalog.query("""
        SELECT * FROM market_data
        WHERE symbol = 'AAPL'
    """)

    assert len(result) == 30  # 30 days
    assert all(result["symbol"] == "AAPL")

    catalog.close()


def test_aggregation_query(temp_data_dir):
    """Test GROUP BY aggregation."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    result = catalog.query("""
        SELECT
            symbol,
            COUNT(*) AS n_days,
            AVG(close) AS avg_close
        FROM market_data
        GROUP BY symbol
        ORDER BY symbol
    """)

    assert len(result) == 3  # 3 symbols
    assert result["symbol"].to_list() == ["AAPL", "GOOGL", "MSFT"]
    assert all(result["n_days"] == 30)

    catalog.close()


def test_window_function_query(temp_data_dir):
    """Test window function (moving average)."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    result = catalog.query("""
        SELECT
            symbol,
            date,
            close,
            AVG(close) OVER (
                PARTITION BY symbol
                ORDER BY date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            ) AS sma_5
        FROM market_data
        WHERE symbol = 'AAPL'
        ORDER BY date
    """)

    assert len(result) == 30
    # First 4 rows should have partial averages
    # 5th row onwards should have full 5-day average
    assert result["sma_5"][4] is not None

    catalog.close()


def test_query_return_pandas(temp_data_dir):
    """Test query returning Pandas DataFrame."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    result = catalog.query("SELECT * FROM market_data LIMIT 5", return_format="pandas")

    # Check it's a Pandas DataFrame
    import pandas as pd
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 5

    catalog.close()


def test_query_invalid_format(temp_data_dir):
    """Test invalid return format raises ValueError."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    with pytest.raises(ValueError, match="Invalid return_format"):
        catalog.query("SELECT * FROM market_data", return_format="invalid")

    catalog.close()


def test_query_sql_error(temp_data_dir):
    """Test invalid SQL raises duckdb.Error."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    with pytest.raises(duckdb.Error):
        catalog.query("SELECT invalid_column FROM market_data")

    catalog.close()


# ============================================================================
# Helper Method Tests
# ============================================================================


def test_get_symbols(temp_data_dir):
    """Test get_symbols() helper."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    symbols = catalog.get_symbols()
    assert symbols == ["AAPL", "GOOGL", "MSFT"]

    catalog.close()


def test_get_date_range(temp_data_dir):
    """Test get_date_range() helper."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    min_date, max_date = catalog.get_date_range()
    assert min_date == "2024-01-01"
    assert max_date == "2024-01-30"  # 30 days starting from 2024-01-01

    catalog.close()


def test_get_stats(temp_data_dir):
    """Test get_stats() helper."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    stats = catalog.get_stats()
    assert stats["row_count"][0] == 90  # 30 days * 3 symbols
    assert stats["n_symbols"][0] == 3
    assert str(stats["min_date"][0]) == "2024-01-01"
    assert stats["n_trading_days"][0] == 30

    catalog.close()


# ============================================================================
# Analytics Helper Function Tests
# ============================================================================


def test_calculate_returns(temp_data_dir):
    """Test calculate_returns() helper function."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Calculate returns for AAPL
    result = calculate_returns(catalog, "AAPL")

    assert len(result) == 30
    assert "daily_return" in result.columns

    # First row should have null return (no previous day)
    assert result["daily_return"][0] is None

    # Remaining rows should have returns
    assert result["daily_return"][1] is not None

    catalog.close()


def test_calculate_returns_with_dates(temp_data_dir):
    """Test calculate_returns() with date filtering."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Calculate returns for 5 days
    result = calculate_returns(
        catalog, "AAPL",
        start_date="2024-01-01",
        end_date="2024-01-05"
    )

    assert len(result) == 5

    catalog.close()


def test_calculate_sma(temp_data_dir):
    """Test calculate_sma() helper function."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Calculate 5-day SMA for AAPL
    result = calculate_sma(catalog, "AAPL", window=5)

    assert len(result) == 30
    assert "sma_5" in result.columns

    # All rows should have SMA (partial for first 4, full from 5th)
    assert result["sma_5"][0] is not None
    assert result["sma_5"][4] is not None

    catalog.close()


def test_calculate_sma_custom_window(temp_data_dir):
    """Test calculate_sma() with custom window size."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Calculate 10-day SMA
    result = calculate_sma(catalog, "MSFT", window=10)

    assert "sma_10" in result.columns
    assert len(result) == 30

    catalog.close()


# ============================================================================
# Performance Tests
# ============================================================================


def test_performance_large_query(temp_data_dir):
    """
    Test query performance on 90 rows (should be very fast).

    Performance target: < 100ms for simple queries
    """
    import time

    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Measure query time
    start = time.time()
    result = catalog.query("SELECT * FROM market_data")
    elapsed = time.time() - start

    # Should return all rows
    assert len(result) == 90

    # Should be fast (< 100ms, likely < 10ms)
    assert elapsed < 0.1, f"Query took {elapsed:.3f}s, expected < 0.1s"

    catalog.close()


def test_performance_filtered_query(temp_data_dir):
    """
    Test predicate pushdown performance.

    With predicate pushdown, filtering should be almost as fast as full scan
    since DuckDB skips irrelevant data.
    """
    import time

    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Measure filtered query time
    start = time.time()
    result = catalog.query("""
        SELECT * FROM market_data
        WHERE symbol = 'AAPL' AND date >= '2024-01-15'
    """)
    elapsed = time.time() - start

    # Should return filtered rows
    assert len(result) > 0
    assert all(result["symbol"] == "AAPL")

    # Should be fast (< 50ms)
    assert elapsed < 0.05, f"Filtered query took {elapsed:.3f}s, expected < 0.05s"

    catalog.close()


def test_performance_aggregation(temp_data_dir):
    """Test aggregation performance."""
    import time

    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Measure aggregation time
    start = time.time()
    result = catalog.query("""
        SELECT
            symbol,
            COUNT(*) AS n_days,
            AVG(close) AS avg_close,
            MAX(close) AS max_close,
            MIN(close) AS min_close
        FROM market_data
        GROUP BY symbol
    """)
    elapsed = time.time() - start

    # Should return 3 rows (one per symbol)
    assert len(result) == 3

    # Should be fast (< 100ms)
    assert elapsed < 0.1, f"Aggregation took {elapsed:.3f}s, expected < 0.1s"

    catalog.close()


# ============================================================================
# Edge Case Tests
# ============================================================================


def test_empty_query_result(temp_data_dir):
    """Test query that returns no rows."""
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Query with impossible condition
    result = catalog.query("""
        SELECT * FROM market_data
        WHERE symbol = 'NONEXISTENT'
    """)

    assert len(result) == 0
    assert isinstance(result, pl.DataFrame)

    catalog.close()


def test_multiple_catalogs():
    """Test that multiple catalogs can coexist independently."""
    catalog1 = DuckDBCatalog()
    catalog2 = DuckDBCatalog()

    # Each should have independent connections
    assert catalog1.conn != catalog2.conn

    catalog1.close()
    catalog2.close()


# ============================================================================
# Security Tests (Added after automated code review)
# ============================================================================


def test_read_only_parameter_validation():
    """
    Test that read_only=True raises ValueError for in-memory database.

    Security issue identified by: Gemini Code Assist
    Fix: Proactive error handling in __init__
    """
    with pytest.raises(ValueError, match="In-memory DuckDB databases cannot be opened in read-only mode"):
        DuckDBCatalog(read_only=True)


def test_table_name_validation_get_symbols(temp_data_dir):
    """
    Test that get_symbols() validates table name to prevent SQL injection.

    Security issue identified by: Codex and Gemini Code Assist
    Fix: Added _validate_table_name() method
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Valid table name should work
    symbols = catalog.get_symbols("market_data")
    assert len(symbols) == 3

    # Invalid table name should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        catalog.get_symbols("'; DROP TABLE users; --")

    # Unregistered table should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        catalog.get_symbols("nonexistent_table")

    catalog.close()


def test_table_name_validation_get_date_range(temp_data_dir):
    """
    Test that get_date_range() validates table name to prevent SQL injection.

    Security issue identified by: Codex and Gemini Code Assist
    Fix: Added _validate_table_name() method
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Valid table name should work
    min_date, max_date = catalog.get_date_range("market_data")
    assert min_date == "2024-01-01"

    # Invalid table name should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        catalog.get_date_range("'; DROP TABLE users; --")

    catalog.close()


def test_table_name_validation_get_stats(temp_data_dir):
    """
    Test that get_stats() validates table name to prevent SQL injection.

    Security issue identified by: Codex and Gemini Code Assist
    Fix: Added _validate_table_name() method
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Valid table name should work
    stats = catalog.get_stats("market_data")
    assert stats["row_count"][0] == 90

    # Invalid table name should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        catalog.get_stats("'; DROP TABLE users; --")

    catalog.close()


def test_table_name_validation_calculate_returns(temp_data_dir):
    """
    Test that calculate_returns() validates table name to prevent SQL injection.

    Security issue identified by: Codex and Gemini Code Assist
    Fix: Added _validate_table_name() call in calculate_returns()
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Valid table name should work
    result = calculate_returns(catalog, "AAPL", table_name="market_data")
    assert len(result) == 30

    # Invalid table name should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        calculate_returns(catalog, "AAPL", table_name="'; DROP TABLE users; --")

    catalog.close()


def test_table_name_validation_calculate_sma(temp_data_dir):
    """
    Test that calculate_sma() validates table name to prevent SQL injection.

    Security issue identified by: Codex and Gemini Code Assist
    Fix: Added _validate_table_name() call in calculate_sma()
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Valid table name should work
    result = calculate_sma(catalog, "AAPL", table_name="market_data")
    assert len(result) == 30

    # Invalid table name should raise ValueError
    with pytest.raises(ValueError, match="is not registered"):
        calculate_sma(catalog, "AAPL", table_name="'; DROP TABLE users; --")

    catalog.close()


def test_parameterized_query_support(temp_data_dir):
    """
    Test that query() method supports parameterized queries.

    Security issue identified by: Gemini Code Assist
    Fix: Added params parameter to query() method
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Test parameterized query with single parameter
    result = catalog.query(
        "SELECT * FROM market_data WHERE symbol = ?",
        params=["AAPL"]
    )
    assert len(result) == 30
    assert all(result["symbol"] == "AAPL")

    # Test parameterized query with multiple parameters
    result = catalog.query(
        "SELECT * FROM market_data WHERE symbol = ? AND date >= ?",
        params=["AAPL", "2024-01-15"]
    )
    assert len(result) > 0
    assert all(result["symbol"] == "AAPL")
    assert all(result["date"] >= date(2024, 1, 15))

    # Test that non-parameterized queries still work
    result = catalog.query("SELECT * FROM market_data WHERE symbol = 'MSFT'")
    assert len(result) == 30
    assert all(result["symbol"] == "MSFT")

    catalog.close()


def test_sql_injection_prevention_in_where_clauses(temp_data_dir):
    """
    Test that WHERE clauses in helper functions use parameterized queries.

    Security issue identified by: Codex (initially fixed)
    Validation: Ensure parameterized queries are used
    """
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    # Test that malicious input in symbol parameter doesn't cause SQL injection
    # This should return no results (symbol doesn't exist) rather than executing malicious code
    malicious_symbol = "AAPL' OR '1'='1"
    result = calculate_returns(catalog, malicious_symbol)

    # Should return empty result (no such symbol exists)
    assert len(result) == 0

    # Same test for calculate_sma
    result = calculate_sma(catalog, malicious_symbol)
    assert len(result) == 0

    catalog.close()


def test_dry_principle_where_clause_building(temp_data_dir):
    """
    Test that _build_where_clause helper function is used consistently.

    Code quality issue identified by: Gemini Code Assist
    Fix: Extracted duplicate WHERE clause logic into _build_where_clause()
    """
    from libs.duckdb_catalog import _build_where_clause

    # Test with only symbol
    where_sql, params = _build_where_clause("AAPL")
    assert where_sql == "symbol = ?"
    assert params == ["AAPL"]

    # Test with symbol and start_date
    where_sql, params = _build_where_clause("AAPL", start_date="2024-01-01")
    assert where_sql == "symbol = ? AND date >= ?"
    assert params == ["AAPL", "2024-01-01"]

    # Test with symbol, start_date, and end_date
    where_sql, params = _build_where_clause("AAPL", start_date="2024-01-01", end_date="2024-01-31")
    assert where_sql == "symbol = ? AND date >= ? AND date <= ?"
    assert params == ["AAPL", "2024-01-01", "2024-01-31"]

    # Verify that both helper functions produce identical results for same inputs
    catalog = DuckDBCatalog()
    catalog.register_table("market_data", str(temp_data_dir / "*" / "*.parquet"))

    returns_result = calculate_returns(catalog, "AAPL", "2024-01-01", "2024-01-10")
    sma_result = calculate_sma(catalog, "AAPL", start_date="2024-01-01", end_date="2024-01-10")

    # Both should filter to same date range
    assert len(returns_result) == 10
    assert len(sma_result) == 10
    assert returns_result["date"].to_list() == sma_result["date"].to_list()

    catalog.close()

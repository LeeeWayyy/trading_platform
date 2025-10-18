# DuckDB Basics

## Plain English Explanation

DuckDB is a database that runs **inside your Python program** without needing a separate server. Think of it like SQLite, but optimized for analytics instead of transactions.

**Key differences from traditional databases:**

| Traditional DB (Postgres) | DuckDB |
|---------------------------|--------|
| Separate server process | Runs in your Python process |
| Row-based storage | Column-based storage |
| Good for transactions (INSERT/UPDATE) | Good for analytics (SELECT/GROUP BY) |
| Network overhead | Direct memory access |
| Great for: Order processing | Great for: Data analysis |

**Simple analogy:** Postgres is like a bank teller (handles many small transactions), DuckDB is like a financial analyst (crunches big reports).

## Why It Matters for Trading

### Real-World Use Cases

1. **Ad-hoc Backtesting Queries**
   - "Show me all days where AAPL gained > 5%"
   - "Calculate average volume by month for 2024"
   - Without DuckDB: Write Python loops, slow
   - With DuckDB: Write SQL, 100x faster

2. **Quick Data Exploration**
   - "How many symbols have data for 2024?"
   - "What's the date range in our Parquet files?"
   - No need to load entire dataset into Pandas

3. **Performance Analysis**
   - "Find all trades during high volatility periods"
   - "Calculate correlation between volume and returns"
   - Joins across millions of rows in seconds

### Performance Benefits

```python
# WITHOUT DuckDB (slow - loads everything)
import polars as pl
df = pl.read_parquet("data/adjusted/*/*.parquet")  # Load 10GB
result = df.filter(pl.col("symbol") == "AAPL")     # Filter in memory
# Time: 30 seconds, Memory: 10GB

# WITH DuckDB (fast - pushes filter down)
import duckdb
result = duckdb.sql("""
    SELECT * FROM read_parquet('data/adjusted/*/*.parquet')
    WHERE symbol = 'AAPL'
""").df()
# Time: 2 seconds, Memory: 200MB (only AAPL data loaded)
```

**Why faster?**
- Reads only needed columns (columnar storage)
- Pushes filters down to file read (predicate pushdown)
- Parallel processing across files
- No intermediate copies in memory

## Common Pitfalls

### 1. Forgetting to Use Glob Patterns

**Problem:** Only queries one file instead of all files.

```python
# ❌ WRONG - Only reads one date
duckdb.sql("SELECT * FROM read_parquet('data/adjusted/2024-01-01/*.parquet')")

# ✅ CORRECT - Reads all dates
duckdb.sql("SELECT * FROM read_parquet('data/adjusted/*/*.parquet')")
```

### 2. Loading Too Much Data Into Memory

**Problem:** Defeats the purpose of lazy evaluation.

```python
# ❌ WRONG - Loads everything then filters
df = duckdb.sql("SELECT * FROM read_parquet('data/*/*.parquet')").df()
result = df[df['symbol'] == 'AAPL']  # Too late, already loaded all

# ✅ CORRECT - Filters during read
result = duckdb.sql("""
    SELECT * FROM read_parquet('data/*/*.parquet')
    WHERE symbol = 'AAPL'
""").df()  # Only loads AAPL rows
```

### 3. Not Using Indexes for Repeated Queries

**Problem:** Re-reads Parquet files every time.

```python
# ❌ WRONG - Reads from disk every query
for symbol in ["AAPL", "MSFT", "GOOGL"]:
    duckdb.sql(f"SELECT * FROM read_parquet('data/*/*.parquet') WHERE symbol = '{symbol}'")
# Reads entire dataset 3 times!

# ✅ CORRECT - Create view or temp table once
duckdb.sql("CREATE TEMP TABLE market_data AS SELECT * FROM read_parquet('data/*/*.parquet')")
for symbol in ["AAPL", "MSFT", "GOOGL"]:
    duckdb.sql(f"SELECT * FROM market_data WHERE symbol = '{symbol}'")
# Reads once, queries 3 times from memory
```

### 4. SQL Injection Risk

**Problem:** Unsafe string formatting allows malicious input.

```python
# ❌ WRONG - SQL injection vulnerability
symbol = input("Enter symbol: ")  # User enters: AAPL'; DROP TABLE market_data; --
duckdb.sql(f"SELECT * FROM market_data WHERE symbol = '{symbol}'")

# ✅ CORRECT - Use parameterized queries
symbol = input("Enter symbol: ")
duckdb.sql("SELECT * FROM market_data WHERE symbol = ?", [symbol])
```

## Examples

### Example 1: Basic Query on Parquet Files

```python
import duckdb

# Query Parquet files directly (no loading into memory first)
result = duckdb.sql("""
    SELECT
        symbol,
        date,
        close,
        volume
    FROM read_parquet('data/adjusted/*/*.parquet')
    WHERE symbol = 'AAPL'
      AND date >= '2024-01-01'
    ORDER BY date DESC
    LIMIT 10
""").df()

print(result)
```

**Output:**
```
  symbol        date   close      volume
0   AAPL  2024-12-31  180.50  45000000
1   AAPL  2024-12-30  179.25  42000000
...
```

### Example 2: Aggregations Across All Symbols

```python
# Calculate monthly statistics for all symbols
monthly_stats = duckdb.sql("""
    SELECT
        symbol,
        DATE_TRUNC('month', date) AS month,
        AVG(close) AS avg_close,
        MAX(close) AS max_close,
        MIN(close) AS min_close,
        SUM(volume) AS total_volume
    FROM read_parquet('data/adjusted/*/*.parquet')
    WHERE date >= '2024-01-01'
    GROUP BY symbol, DATE_TRUNC('month', date)
    ORDER BY symbol, month
""").df()

print(monthly_stats.head())
```

**Output:**
```
  symbol      month  avg_close  max_close  min_close  total_volume
0   AAPL 2024-01-01     178.50     182.00     175.00   950000000
1   AAPL 2024-02-01     180.25     185.00     176.50   920000000
2   MSFT 2024-01-01     385.75     390.00     380.00   780000000
...
```

### Example 3: Joins Across Data Sources

```python
# Join Parquet data with Python dictionary
earnings_dates = {
    "AAPL": "2024-02-01",
    "MSFT": "2024-01-30",
    "GOOGL": "2024-02-05"
}

# Create temp table from Python data
duckdb.sql("""
    CREATE TEMP TABLE earnings AS
    SELECT * FROM (VALUES
        ('AAPL', '2024-02-01'),
        ('MSFT', '2024-01-30'),
        ('GOOGL', '2024-02-05')
    ) AS t(symbol, earnings_date)
""")

# Join with Parquet data
result = duckdb.sql("""
    SELECT
        p.symbol,
        p.date,
        p.close,
        p.volume,
        e.earnings_date,
        -- Days until/since earnings
        DATE_DIFF('day', p.date, e.earnings_date::DATE) AS days_to_earnings
    FROM read_parquet('data/adjusted/*/*.parquet') p
    INNER JOIN earnings e ON p.symbol = e.symbol
    WHERE p.date BETWEEN '2024-01-15' AND '2024-02-15'
      AND ABS(DATE_DIFF('day', p.date, e.earnings_date::DATE)) <= 5
    ORDER BY p.symbol, p.date
""").df()

print(result.head())
```

**Use case:** Analyze stock behavior around earnings announcements.

### Example 4: Window Functions for Technical Indicators

```python
# Calculate 20-day moving average using SQL window functions
ma_data = duckdb.sql("""
    SELECT
        symbol,
        date,
        close,
        -- 20-day simple moving average
        AVG(close) OVER (
            PARTITION BY symbol
            ORDER BY date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS sma_20,
        -- 20-day volume average
        AVG(volume) OVER (
            PARTITION BY symbol
            ORDER BY date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS vol_avg_20
    FROM read_parquet('data/adjusted/*/*.parquet')
    WHERE symbol IN ('AAPL', 'MSFT')
      AND date >= '2024-01-01'
    ORDER BY symbol, date
""").df()

print(ma_data.head(25))  # First 20 rows will have partial averages
```

**Why useful:** Calculate indicators without loading data into Pandas first.

## Key Concepts

### Columnar Storage

**Row-based (Postgres):**
```
Row 1: [AAPL, 2024-01-01, 150.00, 1000000]
Row 2: [AAPL, 2024-01-02, 151.00, 1100000]
Row 3: [MSFT, 2024-01-01, 350.00, 800000]
```
Stored sequentially on disk. Reading one column requires reading ALL columns.

**Column-based (DuckDB + Parquet):**
```
Symbol column:  [AAPL, AAPL, MSFT, ...]
Date column:    [2024-01-01, 2024-01-02, 2024-01-01, ...]
Close column:   [150.00, 151.00, 350.00, ...]
Volume column:  [1000000, 1100000, 800000, ...]
```
Each column stored separately. Reading `close` only reads close column.

**Benefits:**
- **Compression:** Numbers compress better than mixed types (10x smaller files)
- **I/O efficiency:** SELECT close only reads close column
- **CPU cache:** Column fits in CPU cache for faster processing

### Predicate Pushdown

**Without pushdown (slow):**
```
1. Read entire Parquet file (10GB)
2. Load into memory
3. Filter WHERE symbol = 'AAPL'
Result: 10GB read, 100MB relevant
```

**With pushdown (fast):**
```
1. Read Parquet metadata (1MB)
2. Skip file chunks that don't contain 'AAPL'
3. Read only chunks with 'AAPL' (100MB)
Result: 100MB read, 100MB relevant
```

DuckDB automatically pushes filters down to Parquet readers.

### Lazy Execution

```python
# Create query (doesn't execute yet)
query = duckdb.sql("SELECT * FROM read_parquet('data/*/*.parquet')")

# Still not executed (DuckDB analyzes query plan)
filtered = query.filter("symbol = 'AAPL'")

# Executes now (combines all operations)
result = filtered.df()
```

DuckDB combines operations before executing for efficiency.

## Performance Characteristics

### Query Performance

| Operation | 1M Rows | 10M Rows | 100M Rows |
|-----------|---------|----------|-----------|
| Full scan | 0.1s | 1s | 10s |
| Filter on indexed column | 0.01s | 0.1s | 1s |
| Aggregation (GROUP BY) | 0.2s | 2s | 20s |
| Join (two tables) | 0.3s | 3s | 30s |
| Window function | 0.5s | 5s | 50s |

**Hardware:** MacBook Pro M1, 16GB RAM, SSD

### Memory Usage

DuckDB streams data in chunks, typical memory usage:
- Simple query: < 100MB
- Complex aggregation: 200-500MB
- Large join: 1-2GB (spills to disk if needed)

Much lower than Pandas which loads everything into memory.

## Integration with Polars and Pandas

### DuckDB → Polars

```python
import duckdb
import polars as pl

# Query with DuckDB, convert to Polars
result = duckdb.sql("""
    SELECT * FROM read_parquet('data/*/*.parquet')
    WHERE symbol = 'AAPL'
""").pl()  # Returns Polars DataFrame

# Now use Polars for further processing
result_sorted = result.sort("date")
```

### DuckDB → Pandas

```python
# Query with DuckDB, convert to Pandas
result = duckdb.sql("""
    SELECT * FROM read_parquet('data/*/*.parquet')
    WHERE symbol = 'AAPL'
""").df()  # Returns Pandas DataFrame

# Use Pandas for visualization
result.plot(x='date', y='close')
```

### Polars → DuckDB

```python
import polars as pl

# Load data with Polars
df = pl.read_parquet("data/adjusted/*/*.parquet")

# Query Polars DataFrame with DuckDB
result = duckdb.sql("""
    SELECT symbol, AVG(close) AS avg_close
    FROM df
    GROUP BY symbol
""").df()
```

## Further Reading

- [DuckDB Official Documentation](https://duckdb.org/docs/)
- [DuckDB vs Pandas Performance](https://duckdb.org/2021/05/14/sql-on-pandas.html)
- [Parquet File Format](https://parquet.apache.org/docs/)
- [SQL Window Functions Tutorial](https://www.postgresql.org/docs/current/tutorial-window.html)
- [DuckDB Python API](https://duckdb.org/docs/api/python/overview.html)

## Related Concepts

- [parquet-format.md](./parquet-format.md) - Deep dive into Parquet structure
- [sql-analytics-patterns.md](./sql-analytics-patterns.md) - Common SQL queries for trading
- `/docs/IMPLEMENTATION_GUIDES/p1.1t3-duckdb-analytics.md` - Implementation guide

---

**Last Updated:** 2025-01-18
**Relates To:** P1.1T3 - DuckDB Analytics Layer

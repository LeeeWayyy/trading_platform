# SQL Analytics Patterns for Trading

## Plain English Explanation

SQL (Structured Query Language) is a language for asking questions about your data. Instead of writing Python loops, you write declarative statements describing **what** you want, and the database figures out **how** to get it efficiently.

**Simple analogy:**
- **Python loops:** "Walk through every row, check if it matches, collect the results"
- **SQL:** "Give me all rows where this condition is true"

SQL handles the details (indexes, parallelization, optimization) automatically.

### Why SQL for Trading Analytics?

```python
# WITHOUT SQL (slow, verbose)
results = []
for row in data:
    if row['symbol'] == 'AAPL' and row['date'] >= '2024-01-01':
        if row['close'] > row['open']:
            results.append({
                'date': row['date'],
                'gain': (row['close'] - row['open']) / row['open']
            })
# 100 lines of code, 30 seconds

# WITH SQL (fast, concise)
SELECT
    date,
    (close - open) / open AS gain
FROM market_data
WHERE symbol = 'AAPL'
  AND date >= '2024-01-01'
  AND close > open
# 6 lines, 0.5 seconds
```

## Why It Matters for Trading

### 1. Rapid Hypothesis Testing

Test trading ideas quickly without writing complex Python:

```sql
-- Question: "Do stocks gap up more often on Mondays?"
SELECT
    DAYNAME(date) AS day_of_week,
    COUNT(*) AS total_days,
    SUM(CASE WHEN open > prev_close THEN 1 ELSE 0 END) AS gap_ups,
    SUM(CASE WHEN open > prev_close THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS gap_up_pct
FROM (
    SELECT
        date,
        open,
        close,
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close
    FROM market_data
    WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL')
      AND date >= '2024-01-01'
)
WHERE prev_close IS NOT NULL
GROUP BY day_of_week
ORDER BY gap_up_pct DESC
```

**Result in seconds** - no Python script needed!

### 2. Complex Aggregations Made Simple

```sql
-- Calculate per-symbol monthly volatility
SELECT
    symbol,
    DATE_TRUNC('month', date) AS month,
    STDDEV(daily_return) * SQRT(252) AS annualized_volatility,
    AVG(volume) AS avg_volume,
    COUNT(*) AS trading_days
FROM (
    SELECT
        symbol,
        date,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return,
        volume
    FROM market_data
)
WHERE daily_return IS NOT NULL
GROUP BY symbol, DATE_TRUNC('month', date)
ORDER BY symbol, month
```

### 3. Multi-Table Analysis

```sql
-- Combine market data with earnings dates
SELECT
    m.symbol,
    m.date,
    m.close,
    m.volume,
    e.earnings_date,
    DATEDIFF('day', m.date, e.earnings_date) AS days_to_earnings,
    -- Classify trading days
    CASE
        WHEN DATEDIFF('day', m.date, e.earnings_date) BETWEEN -2 AND 2 THEN 'Earnings Window'
        WHEN DATEDIFF('day', m.date, e.earnings_date) > 0 THEN 'Before Earnings'
        ELSE 'After Earnings'
    END AS period
FROM market_data m
JOIN earnings_calendar e ON m.symbol = e.symbol
WHERE m.date >= '2024-01-01'
  AND ABS(DATEDIFF('day', m.date, e.earnings_date)) <= 10
```

## Common SQL Patterns

### Pattern 1: Time-Series Aggregations

**Use case:** Calculate monthly/weekly statistics

```sql
-- Monthly summary statistics
SELECT
    symbol,
    DATE_TRUNC('month', date) AS month,
    COUNT(*) AS trading_days,
    MIN(low) AS monthly_low,
    MAX(high) AS monthly_high,
    AVG(close) AS avg_close,
    SUM(volume) AS total_volume,
    -- Returns
    (MAX(close) - MIN(close)) / MIN(close) AS monthly_range_pct
FROM market_data
WHERE date >= '2024-01-01'
GROUP BY symbol, DATE_TRUNC('month', date)
ORDER BY symbol, month
```

### Pattern 2: Window Functions (Moving Calculations)

**Use case:** Technical indicators, moving averages

```sql
-- Calculate 20-day SMA and Bollinger Bands
SELECT
    symbol,
    date,
    close,
    -- Simple Moving Average
    AVG(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS sma_20,
    -- Standard Deviation
    STDDEV(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS std_20,
    -- Upper Bollinger Band (SMA + 2*STD)
    AVG(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) + 2 * STDDEV(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS upper_band,
    -- Lower Bollinger Band (SMA - 2*STD)
    AVG(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) - 2 * STDDEV(close) OVER (
        PARTITION BY symbol
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS lower_band
FROM market_data
WHERE symbol = 'AAPL'
  AND date >= '2024-01-01'
ORDER BY date
```

### Pattern 3: Lag/Lead for Previous/Next Values

**Use case:** Calculate returns, detect gaps

```sql
-- Calculate daily returns and identify gaps
SELECT
    symbol,
    date,
    open,
    close,
    -- Previous day's close
    LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date) AS prev_close,
    -- Daily return
    (close - LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date)) /
    LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date) AS daily_return,
    -- Gap (difference between open and previous close)
    (open - LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date)) /
    LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date) AS gap_pct,
    -- Classify gap
    CASE
        WHEN open > LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date) * 1.02 THEN 'Gap Up'
        WHEN open < LAG(close, 1) OVER (PARTITION BY symbol ORDER BY date) * 0.98 THEN 'Gap Down'
        ELSE 'Normal'
    END AS gap_type
FROM market_data
WHERE symbol = 'AAPL'
  AND date >= '2024-01-01'
ORDER BY date
```

### Pattern 4: Pivoting (Wide to Long, Long to Wide)

**Use case:** Compare multiple symbols side-by-side

```sql
-- Pivot: Show multiple symbols' closes in columns
SELECT
    date,
    MAX(CASE WHEN symbol = 'AAPL' THEN close END) AS aapl_close,
    MAX(CASE WHEN symbol = 'MSFT' THEN close END) AS msft_close,
    MAX(CASE WHEN symbol = 'GOOGL' THEN close END) AS googl_close,
    -- Calculate correlations would go here in a CTE
FROM market_data
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL')
  AND date >= '2024-01-01'
GROUP BY date
ORDER BY date
```

### Pattern 5: Ranking and Percentiles

**Use case:** Find top performers, outlier detection

```sql
-- Rank symbols by daily return, identify top movers
SELECT
    date,
    symbol,
    close,
    (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
    LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return,
    -- Rank within each day (1 = best performer)
    RANK() OVER (
        PARTITION BY date
        ORDER BY (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
                 LAG(close) OVER (PARTITION BY symbol ORDER BY date) DESC
    ) AS return_rank,
    -- Percentile (0-100)
    PERCENT_RANK() OVER (
        PARTITION BY date
        ORDER BY (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
                 LAG(close) OVER (PARTITION BY symbol ORDER BY date)
    ) * 100 AS percentile
FROM market_data
WHERE date >= '2024-01-01'
ORDER BY date, return_rank
```

### Pattern 6: Filtering with Subqueries

**Use case:** Find symbols meeting complex criteria

```sql
-- Find symbols that had 3+ consecutive up days
WITH daily_changes AS (
    SELECT
        symbol,
        date,
        close,
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close,
        CASE
            WHEN close > LAG(close) OVER (PARTITION BY symbol ORDER BY date) THEN 1
            ELSE 0
        END AS is_up_day
    FROM market_data
    WHERE date >= '2024-01-01'
),
consecutive_days AS (
    SELECT
        symbol,
        date,
        is_up_day,
        -- Count consecutive up days
        SUM(is_up_day) OVER (
            PARTITION BY symbol, is_up_day
            ORDER BY date
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS consecutive_count
    FROM daily_changes
)
SELECT DISTINCT
    symbol,
    date,
    consecutive_count AS consecutive_up_days
FROM consecutive_days
WHERE consecutive_count >= 3
  AND is_up_day = 1
ORDER BY symbol, date
```

## Common Pitfalls

### 1. Forgetting to Partition Window Functions

**Problem:** Calculations bleed across symbols.

```sql
-- ❌ WRONG - Calculates average across ALL symbols
SELECT
    symbol,
    date,
    AVG(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma_20
FROM market_data
-- AAPL's SMA includes MSFT prices!

-- ✅ CORRECT - Separate calculations per symbol
SELECT
    symbol,
    date,
    AVG(close) OVER (
        PARTITION BY symbol  -- Critical!
        ORDER BY date
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) AS sma_20
FROM market_data
```

### 2. Using Aggregate Functions Without GROUP BY

**Problem:** Query fails or produces wrong results.

```sql
-- ❌ WRONG - Error: Must GROUP BY symbol
SELECT
    symbol,
    AVG(close)
FROM market_data

-- ✅ CORRECT - Add GROUP BY
SELECT
    symbol,
    AVG(close) AS avg_close
FROM market_data
GROUP BY symbol
```

### 3. Mixing Aggregate and Non-Aggregate Columns

```sql
-- ❌ WRONG - Can't mix aggregates and individual rows
SELECT
    symbol,
    date,              -- Individual value
    AVG(close)         -- Aggregate across all dates
FROM market_data
GROUP BY symbol
-- Error: date not in GROUP BY

-- ✅ CORRECT - Either aggregate everything...
SELECT
    symbol,
    AVG(close) AS avg_close
FROM market_data
GROUP BY symbol

-- ... or use window function
SELECT
    symbol,
    date,
    close,
    AVG(close) OVER (PARTITION BY symbol) AS symbol_avg_close
FROM market_data
```

### 4. Inefficient Joins

**Problem:** Joining on non-indexed columns is slow.

```sql
-- ❌ SLOW - String matching on every row
SELECT *
FROM market_data m
JOIN earnings e ON m.symbol = e.ticker_symbol  -- Different column name
WHERE m.date >= '2024-01-01'

-- ✅ FAST - Use same column name, consider indexes
-- Better yet, ensure columns have same name and type
SELECT *
FROM market_data m
JOIN earnings e ON m.symbol = e.symbol  -- Same name
WHERE m.date >= '2024-01-01'
```

### 5. Not Using CTEs for Readability

**Problem:** Nested subqueries are hard to read/debug.

```sql
-- ❌ HARD TO READ
SELECT symbol, avg_return
FROM (
    SELECT symbol, AVG(daily_return) AS avg_return
    FROM (
        SELECT
            symbol,
            (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
            LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return
        FROM market_data
        WHERE date >= '2024-01-01'
    )
    GROUP BY symbol
)
WHERE avg_return > 0.01

-- ✅ READABLE - Use CTEs (Common Table Expressions)
WITH daily_returns AS (
    SELECT
        symbol,
        date,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return
    FROM market_data
    WHERE date >= '2024-01-01'
),
avg_returns AS (
    SELECT
        symbol,
        AVG(daily_return) AS avg_return
    FROM daily_returns
    WHERE daily_return IS NOT NULL
    GROUP BY symbol
)
SELECT *
FROM avg_returns
WHERE avg_return > 0.01
```

## Advanced Patterns

### Pattern 7: Recursive CTEs (Compounding Returns)

```sql
-- Calculate cumulative returns (compounding)
WITH RECURSIVE cumulative AS (
    -- Base case: First date for each symbol
    SELECT
        symbol,
        date,
        close,
        1.0 AS cumulative_return  -- Start at 1.0 (100%)
    FROM market_data
    WHERE date = (SELECT MIN(date) FROM market_data WHERE symbol = market_data.symbol)

    UNION ALL

    -- Recursive case: Multiply by daily return
    SELECT
        m.symbol,
        m.date,
        m.close,
        c.cumulative_return * (1 + (m.close - prev.close) / prev.close) AS cumulative_return
    FROM market_data m
    JOIN cumulative c ON m.symbol = c.symbol AND m.date > c.date
    JOIN market_data prev ON prev.symbol = m.symbol AND prev.date = (
        SELECT MAX(date) FROM market_data WHERE symbol = m.symbol AND date < m.date
    )
)
SELECT * FROM cumulative
WHERE symbol = 'AAPL'
ORDER BY date
```

### Pattern 8: Self-Joins for Comparisons

```sql
-- Find all pairs of symbols with correlation > 0.8
WITH returns AS (
    SELECT
        symbol,
        date,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS return
    FROM market_data
    WHERE date >= '2024-01-01'
)
SELECT
    r1.symbol AS symbol1,
    r2.symbol AS symbol2,
    CORR(r1.return, r2.return) AS correlation,
    COUNT(*) AS observations
FROM returns r1
JOIN returns r2 ON r1.date = r2.date AND r1.symbol < r2.symbol
WHERE r1.return IS NOT NULL
  AND r2.return IS NOT NULL
GROUP BY r1.symbol, r2.symbol
HAVING CORR(r1.return, r2.return) > 0.8
ORDER BY correlation DESC
```

### Pattern 9: Conditional Aggregates

```sql
-- Calculate win rate and avg gain/loss
SELECT
    symbol,
    COUNT(*) AS total_days,
    -- Win rate
    SUM(CASE WHEN daily_return > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS win_rate_pct,
    -- Average gain (on winning days)
    AVG(CASE WHEN daily_return > 0 THEN daily_return END) AS avg_gain,
    -- Average loss (on losing days)
    AVG(CASE WHEN daily_return < 0 THEN daily_return END) AS avg_loss,
    -- Profit factor
    ABS(AVG(CASE WHEN daily_return > 0 THEN daily_return END) /
        AVG(CASE WHEN daily_return < 0 THEN daily_return END)) AS profit_factor
FROM (
    SELECT
        symbol,
        date,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS daily_return
    FROM market_data
    WHERE date >= '2024-01-01'
)
WHERE daily_return IS NOT NULL
GROUP BY symbol
ORDER BY profit_factor DESC
```

### Pattern 10: Date/Time Bucketing

```sql
-- Analyze intraday patterns (if you had minute data)
SELECT
    symbol,
    EXTRACT(HOUR FROM timestamp) AS hour_of_day,
    AVG(close - open) AS avg_move,
    STDDEV(close - open) AS volatility,
    COUNT(*) AS observations
FROM intraday_data
WHERE date >= '2024-01-01'
GROUP BY symbol, EXTRACT(HOUR FROM timestamp)
ORDER BY symbol, hour_of_day
```

## Performance Tips

### 1. Use LIMIT for Exploration

```sql
-- Start small, verify results
SELECT * FROM market_data
WHERE symbol = 'AAPL'
LIMIT 100  -- Check first 100 rows

-- Then run full query
SELECT * FROM market_data
WHERE symbol = 'AAPL'
```

### 2. Filter Early in Query

```sql
-- ❌ SLOW - Filters after aggregation
SELECT * FROM (
    SELECT symbol, AVG(close) AS avg_close
    FROM market_data
    GROUP BY symbol
)
WHERE symbol = 'AAPL'

-- ✅ FAST - Filter before aggregation
SELECT symbol, AVG(close) AS avg_close
FROM market_data
WHERE symbol = 'AAPL'
GROUP BY symbol
```

### 3. Use EXPLAIN to Understand Query Plan

```sql
-- See how DuckDB will execute query
EXPLAIN SELECT * FROM market_data WHERE symbol = 'AAPL'

-- Look for:
-- - "Parquet Scan" with filters pushed down
-- - "Filter" operations (want these early)
-- - "Aggregate" operations (expensive)
```

### 4. Avoid SELECT *

```sql
-- ❌ SLOW - Reads all columns from Parquet
SELECT * FROM market_data WHERE symbol = 'AAPL'

-- ✅ FAST - Reads only needed columns
SELECT date, close, volume FROM market_data WHERE symbol = 'AAPL'
```

## Integration with DuckDB

### Creating Views for Reusable Queries

```python
import duckdb

# Create a view once
duckdb.sql("""
    CREATE OR REPLACE VIEW daily_returns AS
    SELECT
        symbol,
        date,
        close,
        (close - LAG(close) OVER (PARTITION BY symbol ORDER BY date)) /
        LAG(close) OVER (PARTITION BY symbol ORDER BY date) AS return
    FROM read_parquet('data/adjusted/*/*.parquet')
""")

# Use view in multiple queries
result1 = duckdb.sql("SELECT AVG(return) FROM daily_returns WHERE symbol = 'AAPL'")
result2 = duckdb.sql("SELECT * FROM daily_returns WHERE return > 0.05")
result3 = duckdb.sql("SELECT symbol, MAX(return) FROM daily_returns GROUP BY symbol")
```

### Parameterized Queries

```python
# Safe from SQL injection
symbol = "AAPL"
start_date = "2024-01-01"

result = duckdb.sql("""
    SELECT * FROM read_parquet('data/adjusted/*/*.parquet')
    WHERE symbol = ?
      AND date >= ?
    ORDER BY date
""", [symbol, start_date])
```

## Further Reading

- [SQL Tutorial (W3Schools)](https://www.w3schools.com/sql/)
- [Window Functions Explained](https://www.postgresql.org/docs/current/tutorial-window.html)
- [DuckDB SQL Reference](https://duckdb.org/docs/sql/introduction)
- [Common Table Expressions (CTEs)](https://www.essentialsql.com/introduction-common-table-expressions-ctes/)
- [SQL Performance Tuning](https://use-the-index-luke.com/)

## Related Concepts

- [duckdb-basics.md](./duckdb-basics.md) - DuckDB fundamentals
- [parquet-format.md](./parquet-format.md) - Parquet file structure
- `/docs/IMPLEMENTATION_GUIDES/p1.1t3-duckdb-analytics.md` - Implementation guide

---

**Last Updated:** 2025-01-18
**Relates To:** P1.1T3 - DuckDB Analytics Layer

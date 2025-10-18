# Parquet File Format

## Plain English Explanation

Parquet is a file format designed for storing **columnar data** efficiently. Instead of storing data row-by-row like CSV, it stores column-by-column, which makes analytics much faster.

**Simple analogy:**
- **CSV (row-based):** Like reading a book line by line - you have to read everything to find what you want
- **Parquet (column-based):** Like an index at the back of a book - jump directly to the pages you need

### Visual Comparison

**CSV File (row-based):**
```
symbol,date,close,volume
AAPL,2024-01-01,150.00,1000000
AAPL,2024-01-02,151.00,1100000
MSFT,2024-01-01,350.00,800000
```
Stored as: Row1, Row2, Row3, ...

**Parquet File (column-based):**
```
Symbols: [AAPL, AAPL, MSFT, ...]
Dates:   [2024-01-01, 2024-01-02, 2024-01-01, ...]
Closes:  [150.00, 151.00, 350.00, ...]
Volumes: [1000000, 1100000, 800000, ...]
```
Stored as: SymbolColumn, DateColumn, CloseColumn, VolumeColumn

## Why It Matters for Trading

### 1. Massive Space Savings

**Real example from our trading platform:**

```python
# Same data, different formats
data/raw/market_data.csv         # 2.5 GB
data/adjusted/market_data.parquet # 250 MB (10x smaller!)
```

**Why smaller?**
- Columns compress better (all same type)
- Numbers compress especially well
- Built-in compression (Snappy, ZSTD, etc.)

**Cost impact:**
- **AWS S3:** $0.023/GB/month × 2.25GB saved = $0.05/month savings
- **For 1TB data:** $23/month savings
- **Faster downloads:** 10x less data to transfer

### 2. Faster Queries

```python
import time

# Read entire CSV (need all columns)
start = time.time()
df_csv = pd.read_csv("data/market_data.csv")
close_prices = df_csv['close']
print(f"CSV: {time.time() - start:.2f}s")  # 15 seconds

# Read only 'close' column from Parquet
start = time.time()
df_parquet = pl.read_parquet("data/market_data.parquet", columns=['close'])
close_prices = df_parquet['close']
print(f"Parquet: {time.time() - start:.2f}s")  # 0.5 seconds (30x faster!)
```

### 3. Better for Time-Series Data

Trading data is **append-only** - perfect for Parquet:
- New data gets appended to existing files
- Old data never changes (immutable)
- Partitioning by date makes queries fast

**Our data structure:**
```
data/adjusted/
├── 2024-01-01/
│   ├── AAPL.parquet    # Only AAPL data for this date
│   ├── MSFT.parquet
│   └── GOOGL.parquet
├── 2024-01-02/
│   ├── AAPL.parquet
│   ├── MSFT.parquet
│   └── GOOGL.parquet
```

**Query benefit:**
```sql
-- Only reads 3 files (AAPL for 3 days)
SELECT * FROM read_parquet('data/adjusted/*/AAPL.parquet')
WHERE date >= '2024-01-01' AND date <= '2024-01-03'

-- Skips all MSFT.parquet, GOOGL.parquet files automatically
```

## Common Pitfalls

### 1. Using Parquet for Transaction Data

**Problem:** Parquet is for **analytics** (reading), not **transactions** (writing).

```python
# ❌ WRONG - Don't use Parquet for live order tracking
while True:
    new_order = receive_order()
    # Writing one row at a time = SLOW
    df = pl.DataFrame([new_order])
    df.write_parquet("orders.parquet", mode="append")
# Parquet has high write overhead per row
```

```python
# ✅ CORRECT - Use Postgres for transactions, Parquet for historical analysis
while True:
    new_order = receive_order()
    db.insert("orders", new_order)  # Fast transactional write

# Later: Export to Parquet for analytics
df = pl.read_database("SELECT * FROM orders WHERE date < '2024-01-01'")
df.write_parquet("historical/orders_2024.parquet")
```

**Rule of thumb:**
- **High write frequency** (1000s/sec) → Postgres/Redis
- **Append-only data** (end of day) → Parquet
- **Historical analysis** → Parquet

### 2. Not Using Partitioning

**Problem:** Single large file is slow to query.

```python
# ❌ WRONG - One giant file
data/all_market_data.parquet  # 10 GB, query any date reads entire file

# ✅ CORRECT - Partitioned by date
data/adjusted/2024-01-01/*.parquet  # Query one day reads only that day's files
data/adjusted/2024-01-02/*.parquet
```

**Query performance:**
```python
# Without partitioning
SELECT * FROM 'all_market_data.parquet' WHERE date = '2024-01-01'
# Reads: 10 GB, Time: 30 seconds

# With partitioning
SELECT * FROM 'data/adjusted/2024-01-01/*.parquet'
# Reads: 40 MB, Time: 1 second
```

### 3. Mixing Data Types in Columns

**Problem:** Parquet stores columns with a single type - mixing types reduces compression.

```python
# ❌ WRONG - Mixed types reduce compression
df = pl.DataFrame({
    "close": ["150.00", "151.00", "N/A"],  # Strings instead of floats
    "volume": [1000000, 1100000, None]
})
df.write_parquet("bad_data.parquet")  # 'close' stored as string = poor compression

# ✅ CORRECT - Proper types, use null for missing
df = pl.DataFrame({
    "close": [150.00, 151.00, None],   # Float with null
    "volume": [1000000, 1100000, None]
})
df.write_parquet("good_data.parquet")  # 'close' stored as float = great compression
```

**Compression ratio:**
- Numeric column (float64): **10:1 compression**
- String column: **2:1 compression**

### 4. Forgetting About Schema Evolution

**Problem:** Adding columns to existing Parquet files requires rewriting.

```python
# Existing file has: symbol, date, close, volume
df_old = pl.read_parquet("data/2024-01-01/AAPL.parquet")

# ❌ WRONG - Can't just add column to existing file
df_old['new_column'] = some_values
df_old.write_parquet("data/2024-01-01/AAPL.parquet")  # Rewrites entire file

# ✅ CORRECT - Plan schema upfront, add as nulls initially
df = pl.DataFrame({
    "symbol": ["AAPL"],
    "date": ["2024-01-01"],
    "close": [150.00],
    "volume": [1000000],
    "adjusted_close": [None],  # Add new column as null initially
    "split_ratio": [None]       # Fill in later as needed
})
```

## Examples

### Example 1: Reading Parquet with Column Selection

```python
import polars as pl

# Read only specific columns (saves memory and time)
df = pl.read_parquet(
    "data/adjusted/*/AAPL.parquet",
    columns=['date', 'close']  # Only read these 2 columns
)

print(df.head())
```

**Output:**
```
shape: (5, 2)
┌────────────┬────────┐
│ date       ┆ close  │
│ ---        ┆ ---    │
│ date       ┆ f64    │
╞════════════╪════════╡
│ 2024-01-01 ┆ 150.00 │
│ 2024-01-02 ┆ 151.00 │
│ 2024-01-03 ┆ 152.50 │
│ 2024-01-04 ┆ 151.75 │
│ 2024-01-05 ┆ 153.00 │
└────────────┴────────┘
```

### Example 2: Writing Partitioned Parquet Files

```python
import polars as pl
from pathlib import Path

# Sample market data
df = pl.DataFrame({
    "symbol": ["AAPL", "AAPL", "MSFT", "MSFT"],
    "date": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"],
    "close": [150.00, 151.00, 350.00, 351.00],
    "volume": [1000000, 1100000, 800000, 850000]
})

# Partition by date and symbol
for date in df['date'].unique():
    for symbol in df['symbol'].unique():
        # Filter data for this date and symbol
        partition = df.filter(
            (pl.col('date') == date) &
            (pl.col('symbol') == symbol)
        )

        if len(partition) > 0:
            # Create directory structure
            path = Path(f"data/adjusted/{date}")
            path.mkdir(parents=True, exist_ok=True)

            # Write partition
            partition.write_parquet(f"{path}/{symbol}.parquet")

print("Partitioned files created:")
# data/adjusted/2024-01-01/AAPL.parquet
# data/adjusted/2024-01-01/MSFT.parquet
# data/adjusted/2024-01-02/AAPL.parquet
# data/adjusted/2024-01-02/MSFT.parquet
```

### Example 3: Compression Comparison

```python
import polars as pl

# Sample data
df = pl.DataFrame({
    "close": [150.00 + i * 0.5 for i in range(1000000)],  # 1M rows
    "volume": [1000000 + i * 1000 for i in range(1000000)]
})

# Test different compression methods
compressions = ['uncompressed', 'snappy', 'gzip', 'zstd']

for comp in compressions:
    df.write_parquet(f"test_{comp}.parquet", compression=comp)
    size = Path(f"test_{comp}.parquet").stat().st_size / (1024**2)  # MB
    print(f"{comp:15s}: {size:.2f} MB")
```

**Output:**
```
uncompressed   : 15.26 MB
snappy         :  4.82 MB (3x smaller, fast)
gzip           :  3.21 MB (5x smaller, slower)
zstd           :  2.95 MB (5x smaller, balanced)
```

**Recommendation:** Use `zstd` for best compression with good speed.

### Example 4: Handling Missing Data

```python
import polars as pl

# Data with missing values
df = pl.DataFrame({
    "symbol": ["AAPL", "AAPL", "AAPL"],
    "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
    "close": [150.00, None, 152.00],  # Missing price on 2024-01-02
    "volume": [1000000, 1100000, None]  # Missing volume on 2024-01-03
})

# Write to Parquet (nulls stored efficiently)
df.write_parquet("data_with_nulls.parquet")

# Read back
df_read = pl.read_parquet("data_with_nulls.parquet")
print(df_read)

# Check null counts
print(df_read.null_count())
```

**Output:**
```
shape: (3, 4)
┌────────┬────────────┬────────┬─────────┐
│ symbol ┆ date       ┆ close  ┆ volume  │
│ ---    ┆ ---        ┆ ---    ┆ ---     │
│ str    ┆ str        ┆ f64    ┆ i64     │
╞════════╪════════════╪════════╪═════════╡
│ AAPL   ┆ 2024-01-01 ┆ 150.0  ┆ 1000000 │
│ AAPL   ┆ 2024-01-02 ┆ null   ┆ 1100000 │
│ AAPL   ┆ 2024-01-03 ┆ 152.0  ┆ null    │
└────────┴────────────┴────────┴─────────┘

Null counts:
shape: (1, 4)
┌────────┬──────┬───────┬────────┐
│ symbol ┆ date ┆ close ┆ volume │
│ ---    ┆ ---  ┆ ---   ┆ ---    │
│ u32    ┆ u32  ┆ u32   ┆ u32    │
╞════════╪══════╪═══════╪════════╡
│ 0      ┆ 0    ┆ 1     ┆ 1      │
└────────┴──────┴───────┴────────┘
```

## Key Concepts

### File Structure

A Parquet file consists of:

```
+-------------------+
| Header            |  (Magic number: PAR1)
+-------------------+
| Row Group 1       |
|  - Column Chunk 1 |  (Compressed column data)
|  - Column Chunk 2 |
|  - Column Chunk 3 |
+-------------------+
| Row Group 2       |
|  - Column Chunk 1 |
|  - Column Chunk 2 |
|  - Column Chunk 3 |
+-------------------+
| Footer (Metadata) |  (Schema, statistics, offsets)
+-------------------+
| Footer Length     |
+-------------------+
| Magic: PAR1       |
+-------------------+
```

**Row Group:** Horizontal partition (typically 50K-100K rows)
**Column Chunk:** Data for one column in one row group
**Footer:** Contains statistics (min/max/null count) for each column chunk

### Predicate Pushdown

The footer's statistics enable **skipping entire row groups**:

```python
# Query
SELECT * FROM parquet_file WHERE close > 200

# DuckDB reads footer:
# Row Group 1: min_close=150, max_close=180  → SKIP (max < 200)
# Row Group 2: min_close=180, max_close=220  → READ (overlaps range)
# Row Group 3: min_close=220, max_close=250  → READ (overlaps range)
```

### Encoding Types

Parquet uses different encoding for different data patterns:

| Data Pattern | Encoding | Example |
|--------------|----------|---------|
| Repeating values | RLE (Run Length) | ["AAPL", "AAPL", "AAPL", ...] |
| Sequential numbers | Delta encoding | [100, 101, 102, 103, ...] |
| Low cardinality | Dictionary | ["buy", "sell", "buy", "sell"] |
| Random numbers | Plain | [150.23, 98.45, 234.12, ...] |

**Example:**
```python
# Symbol column (low cardinality)
["AAPL", "AAPL", "AAPL", "MSFT", "MSFT", "GOOGL"]

# Stored as:
Dictionary: {0: "AAPL", 1: "MSFT", 2: "GOOGL"}
Values: [0, 0, 0, 1, 1, 2]  # Much smaller than repeating strings!
```

### Compression vs Encoding

- **Encoding:** How data is represented (e.g., dictionary, delta)
- **Compression:** Further reduces size (e.g., Snappy, ZSTD)

**Pipeline:**
```
Raw Data → Encoding → Compression → Disk
[1,2,3,4] → [1,+1,+1,+1] → [compressed bytes] → file.parquet
```

## Performance Characteristics

### Read Performance

| File Size | Full Scan | Single Column | Filtered (10%) |
|-----------|-----------|---------------|----------------|
| 100 MB | 0.5s | 0.1s | 0.05s |
| 1 GB | 5s | 1s | 0.5s |
| 10 GB | 50s | 10s | 5s |
| 100 GB | 500s | 100s | 50s |

**Hardware:** MacBook Pro M1, SSD

### Write Performance

```python
import time
import polars as pl

# Generate 1M rows
df = pl.DataFrame({
    "id": range(1000000),
    "value": [i * 1.5 for i in range(1000000)]
})

# CSV write
start = time.time()
df.write_csv("test.csv")
print(f"CSV write: {time.time() - start:.2f}s")  # 3.5s

# Parquet write (uncompressed)
start = time.time()
df.write_parquet("test.parquet", compression='uncompressed')
print(f"Parquet write (uncompressed): {time.time() - start:.2f}s")  # 0.3s

# Parquet write (zstd)
start = time.time()
df.write_parquet("test.parquet", compression='zstd')
print(f"Parquet write (zstd): {time.time() - start:.2f}s")  # 0.8s
```

### Storage Efficiency

**Compression ratios by data type:**

| Column Type | Uncompressed | Snappy | ZSTD |
|-------------|--------------|--------|------|
| Integers (sequential) | 1x | 15x | 25x |
| Floats (prices) | 1x | 4x | 8x |
| Strings (symbols) | 1x | 3x | 5x |
| Timestamps | 1x | 10x | 20x |

**Real example (our market data):**
- Uncompressed: 2.5 GB
- Snappy: 500 MB (5x)
- ZSTD: 250 MB (10x)

## Best Practices

### 1. Choose Appropriate Row Group Size

```python
# Too small row groups = more overhead
df.write_parquet("data.parquet", row_group_size=1000)  # ❌ Too small

# Too large row groups = can't skip efficiently
df.write_parquet("data.parquet", row_group_size=10000000)  # ❌ Too large

# Good balance
df.write_parquet("data.parquet", row_group_size=100000)  # ✅ Good default
```

### 2. Use Partitioning for Large Datasets

```python
# Good partitioning strategy for market data
data/
├── adjusted/
│   ├── date=2024-01-01/
│   │   ├── symbol=AAPL.parquet
│   │   ├── symbol=MSFT.parquet
│   │   └── symbol=GOOGL.parquet
│   └── date=2024-01-02/
│       ├── symbol=AAPL.parquet
│       └── ...
```

### 3. Select Appropriate Compression

```python
# For archival (rarely read, maximize compression)
df.write_parquet("archive.parquet", compression='zstd', compression_level=9)

# For frequent queries (balance speed and size)
df.write_parquet("active.parquet", compression='zstd', compression_level=3)

# For real-time writes (prioritize speed)
df.write_parquet("realtime.parquet", compression='snappy')
```

### 4. Monitor File Sizes

```python
from pathlib import Path

def check_parquet_size(path: str):
    """Check if Parquet file is too large."""
    size_mb = Path(path).stat().st_size / (1024**2)

    if size_mb > 500:
        print(f"⚠️  {path} is {size_mb:.0f}MB - consider partitioning")
    elif size_mb > 1000:
        print(f"❌ {path} is {size_mb:.0f}MB - TOO LARGE, must partition")
    else:
        print(f"✅ {path} is {size_mb:.0f}MB - good size")

# Check all Parquet files
for f in Path("data/adjusted").rglob("*.parquet"):
    check_parquet_size(f)
```

## Further Reading

- [Apache Parquet Documentation](https://parquet.apache.org/docs/)
- [Parquet File Format Specification](https://github.com/apache/parquet-format)
- [Dremel Paper (Origin of Parquet)](https://research.google/pubs/pub36632/)
- [Polars Parquet Guide](https://pola-rs.github.io/polars/user-guide/io/parquet/)
- [DuckDB Parquet Reader](https://duckdb.org/docs/data/parquet)

## Related Concepts

- [duckdb-basics.md](./duckdb-basics.md) - Querying Parquet with DuckDB
- [sql-analytics-patterns.md](./sql-analytics-patterns.md) - SQL queries on Parquet
- `/docs/IMPLEMENTATION_GUIDES/p0t1-data-etl.md` - How we create Parquet files

---

**Last Updated:** 2025-01-18
**Relates To:** P1.1T3 - DuckDB Analytics Layer

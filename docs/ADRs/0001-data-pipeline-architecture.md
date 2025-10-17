# ADR-0001: Data Pipeline Architecture

## Status
Accepted (2024-10-16)

## Context

For T1 (Data ETL with Corporate Actions, Freshness, Quality Gate), we need to establish the foundational data pipeline architecture. This pipeline will:

1. Ingest raw market data (OHLCV)
2. Adjust for corporate actions (splits, dividends)
3. Validate data quality (outlier detection)
4. Check freshness (staleness detection)
5. Store adjusted data for strategy research
6. Quarantine bad data for investigation

**Requirements:**
- Handle daily bar data for MVP (minute bars later)
- Support ~3 symbols initially (AAPL, MSFT, GOOGL), scale to hundreds
- Data must be immutable once written (append-only for raw)
- Fast queries for backtesting (filter by symbol/date range)
- Clear separation of raw vs. adjusted data
- Deterministic: same input → same output

**Constraints:**
- Solo developer with limited time
- MVP needs simple, proven technologies
- Must work on laptop for development
- No external dependencies for MVP (mock data)

## Decision

We will use the following architecture:

### 1. Storage Format: **Parquet**

Store all data in **Apache Parquet** format with this structure:
```
data/
├── raw/              # Immutable raw data as received
│   └── YYYY-MM-DD/   # Partitioned by date
│       └── {symbol}.parquet
├── adjusted/         # After corporate action adjustments
│   └── YYYY-MM-DD/
│       └── {symbol}.parquet
└── quarantine/       # Data that failed quality checks
    └── YYYY-MM-DD/
        └── {symbol}_reason.parquet
```

**Rationale for Parquet over alternatives:**
- **vs. CSV**: Columnar format = 10x faster for typical queries, schema enforcement, compression
- **vs. HDF5**: Better ecosystem support, easier debugging, schema evolution
- **vs. Feather**: Parquet is more mature, better for long-term storage
- **vs. Database**: Overkill for MVP, Parquet + DuckDB sufficient for ad-hoc queries

### 2. DataFrame Library: **Polars**

Use **Polars** (not Pandas) for all data transformations.

**Rationale:**
- 5-10x faster than Pandas for our workloads (groupby, joins, aggregations)
- Better memory efficiency (crucial for backtests on laptop)
- Native lazy execution (optimization opportunities)
- Built on Apache Arrow (zero-copy interop with Parquet)
- Modern API, better error messages

**Trade-offs:**
- Smaller ecosystem than Pandas (acceptable for MVP)
- Learning curve (mitigated by good docs)
- Qlib uses Pandas (we'll convert at boundaries if needed)

### 3. Corporate Action Strategy: **Backwards Adjustment**

Adjust historical prices backwards from present:

```python
# For a 4:1 split on 2020-08-31:
# - All prices BEFORE 2020-08-31: divide by 4
# - All prices AFTER 2020-08-31: unchanged
# This keeps current prices matching live market
```

**Rationale:**
- Current prices always match live quotes (reduces confusion)
- Industry standard (Yahoo Finance, CRSP, etc. use this)
- Simpler than forward adjustment
- Easier to explain and debug

**For dividends:**
```python
# For $2 dividend on ex-date 2024-01-15:
# - All closes BEFORE 2024-01-15: subtract $2
# - Closes ON/AFTER 2024-01-15: unchanged
```

### 4. Quality Gate Threshold: **30% Daily Change**

Flag as outlier if: `abs(daily_return) > 0.30 AND no_corporate_action`

**Rationale:**
- 30% is extreme for daily moves in liquid stocks
- Balances false positives vs. catching real errors
- Standard threshold in quantitative finance
- Configurable via `OUTLIER_THRESHOLD` env var

**Examples of legitimate 30%+ moves:**
- IPO first day volatility
- Penny stocks (we don't trade these)
- Black swan events (rare, we'd want human review)

### 5. Freshness Threshold: **30 Minutes**

Reject data if latest timestamp is >30 minutes old.

**Rationale:**
- Appropriate for daily strategies (we don't need second-by-second)
- Tight enough to catch stale/stuck feeds
- Loose enough to tolerate reasonable delays
- Much stricter than we need for daily bars, but good safety margin

**Configurable**: `DATA_FRESHNESS_MINUTES` can be adjusted per strategy needs.

### 6. Pipeline Flow

```
Raw Data → Freshness Check → CA Adjustment → Quality Gate → Split(good/bad)
   ↓              ↓                ↓               ↓              ↓
immutable      raise if       adjust prices   detect         adjusted/
Parquet        too old        for splits/     outliers       quarantine/
                              dividends
```

**Sequential processing** (not streaming) for MVP:
- Simpler to reason about and debug
- Deterministic (same input → same output)
- Sufficient for daily batch processing
- Can evolve to streaming later if needed

## Consequences

### Positive
- **Fast backtests**: Parquet + Polars enables analyzing years of data in seconds
- **Data integrity**: Immutable raw data means we can always reprocess if bugs found
- **Clear audit trail**: Quarantine data preserved for debugging
- **Scalable**: Parquet handles millions of rows efficiently on laptop
- **Standard formats**: Easy to integrate with other tools (DuckDB, PyArrow, etc.)
- **Type safety**: Parquet schema enforcement catches bugs early

### Negative
- **Learning curve**: Team needs to learn Polars (mitigated: small team, good docs)
- **Storage overhead**: Keeping raw + adjusted + quarantine uses more disk (acceptable)
- **Not real-time**: Batch pipeline has minutes of latency (fine for daily strategies)

### Risks
- **Polars API changes**: Library is pre-1.0 (mitigated: API is stabilizing, easy to update)
- **Missing corporate actions**: If we don't have complete CA data, adjustments incomplete (mitigated: start with mock data with known CAs)
- **Disk space**: Years of data for hundreds of symbols could be GBs (mitigated: Parquet compression, cheap storage)

## Alternatives Considered

### Alternative 1: Pandas + CSV
**Pros:** Most familiar, maximum ecosystem compatibility
**Cons:** 10x slower, no schema enforcement, larger files
**Why not:** Performance matters for backtesting; CSV debugging nightmares with bad data

### Alternative 2: TimescaleDB (Postgres extension)
**Pros:** SQL queries, ACID, easy aggregations
**Cons:** Overkill for MVP, operational overhead, slower for full-table scans
**Why not:** Save databases for transactional data (orders, positions); files simpler for analytics

### Alternative 3: DuckDB as primary store
**Pros:** SQL queries, fast analytics, can query Parquet directly
**Cons:** Less mature than Parquet, another moving part
**Why not:** Use DuckDB for ad-hoc queries OVER Parquet, but Parquet as source of truth

### Alternative 4: Forward adjustment (vs. backwards)
**Pros:** Historical prices stay at actual traded values
**Cons:** Current prices don't match live market, confusing for debugging
**Why not:** Backwards adjustment is industry standard, less confusing

## Implementation Notes

### MVP Scope (T1)
1. Mock data generator with known split (AAPL 4:1 on 2020-08-31)
2. Corporate action adjuster for splits only (dividends in T2)
3. Quality gate with 30% threshold
4. Freshness check
5. Write to `data/adjusted/` and `data/quarantine/`

### File Naming Convention
```
raw/2024-10-16/AAPL.parquet
adjusted/2024-10-16/AAPL.parquet
quarantine/2024-10-16/AAPL_outlier_50pct.parquet
```

### Schema
```python
{
    "symbol": str,
    "date": date,
    "open": float64,
    "high": float64,
    "low": float64,
    "close": float64,
    "volume": int64,
    "timestamp": datetime (UTC, for freshness check)
}
```

### Testing Strategy
- Unit tests with tiny DataFrames (3-5 rows)
- Integration test with realistic mock data (100 rows, 3 symbols, 1 split)
- Property test: `adjust(adjust(data)) == adjust(data)` (idempotent)

### Performance Targets
- Process 252 days × 3 symbols (756 rows): <1 second
- Scale test (future): 252 days × 100 symbols (25,200 rows): <10 seconds

## Related ADRs
- ADR-0002: Exception Hierarchy (defines errors raised by pipeline)
- (Future) ADR-00XX: Real Data Source Selection
- (Future) ADR-00XX: Streaming vs. Batch Pipeline Evolution

## References
- [Parquet Format Specification](https://parquet.apache.org/docs/)
- [Polars Documentation](https://pola-rs.github.io/polars/)
- [CRSP Data Guide](https://www.crsp.org/) (industry standard for adjustments)

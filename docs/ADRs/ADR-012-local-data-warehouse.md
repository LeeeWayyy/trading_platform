# ADR-012: Local Data Warehouse Architecture

## Status
Accepted

## Date
2025-12-04

## Context

The trading platform requires access to historical academic financial data (CRSP, Compustat, Fama-French) for research and backtesting. WRDS (Wharton Research Data Services) provides this data, but:

1. Direct queries to WRDS are slow and rate-limited
2. Research reproducibility requires versioned data snapshots
3. Multiple processes may need concurrent read access
4. Data integrity must be guaranteed (no partial writes visible)

## Decision

We will implement a **Single-Writer, Multi-Reader** local data warehouse with:

### 1. Storage Format
- **Parquet files** partitioned by year (`data/wrds/{dataset}/{year}.parquet`)
- **DuckDB** for SQL queries over Parquet (read-only connections)
- **JSON manifests** tracking sync state and checksums

### 2. Concurrency Model
- **Exclusive file locking** using OS-atomic `O_CREAT | O_EXCL`
- **Atomic writes** via temp file + checksum + rename + fsync
- **Readers see previous snapshot** during writes (no blocking)

### 3. Stale Lock Recovery
- **4-hour timeout** for lock expiration
- **PID liveness check** for same-host locks
- **Deterministic winner** via atomic rename for concurrent recovery

### 4. Data Integrity
- **SHA-256 checksums** for all Parquet files
- **Schema drift detection** via SchemaRegistry
- **Validation framework** for row counts, nulls, date continuity

### 5. Disk Space Safety
- **Watermarks:** 80% warning, 90% critical, 95% blocked
- **Quarantine directory** for failed writes
- **2x required space** check before writes

## Alternatives Considered

### Alternative 1: Shared Database (PostgreSQL/DuckDB)
- **Rejected:** Write contention, complex replication, overkill for read-heavy workload

### Alternative 2: Cloud Storage (S3/GCS)
- **Rejected:** Latency for iterative research, cost for frequent access, offline capability needed

### Alternative 3: No Locking (Last Write Wins)
- **Rejected:** Risk of data corruption, no crash safety

## Consequences

### Positive
- **Fast queries:** Local Parquet + DuckDB = sub-second for most queries
- **Reproducible:** Versioned snapshots linked to backtests
- **Safe:** Atomic writes prevent corruption
- **Simple:** No distributed coordination needed

### Negative
- **Storage cost:** Local disk space required (~500 GB with snapshots)
- **Sync delay:** Daily sync means data is up to 24 hours stale
- **Single machine:** Not horizontally scalable (acceptable for research)

### Risks
- **Disk failure:** Mitigated by weekly backups
- **Lock deadlock:** Mitigated by 4-hour timeout
- **Schema changes:** Mitigated by drift detection

## Implementation

- `libs/data_providers/locking.py` - AtomicFileLock
- `libs/data_providers/wrds_client.py` - WRDSClient
- `libs/data_providers/sync_manager.py` - SyncManager
- `scripts/wrds_sync.py` - CLI

## References

- [P4T1_TASK.md](../ARCHIVE/TASKS_HISTORY/P4T1_DONE.md) - Task specification
- [DuckDB Concurrency](https://duckdb.org/docs/connect/concurrency) - DuckDB docs
- [Parquet Format](https://parquet.apache.org/) - Apache Parquet

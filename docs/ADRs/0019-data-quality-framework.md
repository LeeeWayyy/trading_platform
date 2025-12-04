# ADR-0019: Data Quality Framework

## Status
ACCEPTED

## Date
2025-12-03

## Context

As part of Phase 4 Data Infrastructure (P4T1), we need a robust data quality and validation framework for WRDS data syncs. The framework must ensure:

1. **Reproducibility**: Track sync state for audit trails and debugging
2. **Data Integrity**: Verify data hasn't been corrupted or tampered with
3. **Schema Stability**: Detect and handle schema changes from upstream sources
4. **Anomaly Detection**: Alert on unusual data patterns that may indicate issues

Key constraints:
- DuckDB uses single-writer/multi-reader model requiring exclusive locking
- File-based storage (parquet) needs atomic writes for crash recovery
- Schema changes from WRDS are common and must be handled gracefully

## Decision

### 1. Checksum Algorithm: SHA-256

**Decision**: Use SHA-256 instead of MD5 for all checksum operations.

**Rationale**:
- MD5 has known collision vulnerabilities
- SHA-256 provides 256-bit security margin
- Modern hardware makes SHA-256 performance acceptable
- Future-proofs against cryptographic attacks

**Implementation**:
- Single file: `hashlib.sha256(file_content).hexdigest()`
- Multiple files: Aggregate hash of sorted path:hash pairs

### 2. Schema Drift Policy

**Decision**: Accept additive changes (warning), reject breaking changes (error).

| Change Type | Policy | Action |
|-------------|--------|--------|
| New columns | Accept | Warning logged, auto-version bump |
| Removed columns | Reject | Raise SchemaError, block manifest |
| Type changes | Reject | Raise SchemaError, block manifest |

**Rationale**:
- New columns from WRDS are common and usually safe
- Removed/changed columns break downstream queries
- Auto-version bump provides audit trail

**Version format**: `v{major}.{minor}.{patch}`
- Minor increments on additive changes
- Major reserved for manual breaking changes

### 3. Lock + fsync Coupling

**Decision**: All manifest writes require holding an exclusive lock with fsync.

**Lock Protocol**:
1. Acquire: `O_CREAT | O_EXCL | O_WRONLY` atomically creates lock file
2. Validate: Check pid/hostname/writer_id match, not expired
3. Write: Temp file → SHA-256 verify → atomic rename → fsync
4. Release: Delete lock file

**Lock file format** (JSON):
```json
{
  "pid": 12345,
  "hostname": "worker-01",
  "writer_id": "sync-job-abc123",
  "acquired_at": "2025-01-15T10:30:00Z",
  "expires_at": "2025-01-15T14:30:00Z"
}
```

**Expiration**: 4 hours max to handle crashed processes.

### 4. Disk-full Procedures

**Decision**: Check disk space before writes with tiered thresholds.

| Threshold | Level | Action |
|-----------|-------|--------|
| < 80% | OK | Proceed normally |
| 80-90% | WARNING | Log warning, proceed |
| 90-95% | CRITICAL | Log alert, proceed |
| ≥ 95% | BLOCKED | Raise DiskSpaceError |

**Additional safeguards**:
- Require 2x expected write size available
- ENOSPC errors trigger quarantine procedure
- Quarantined data preserved for investigation

### 5. Reader Cache Invalidation

**Decision**: Guide DuckDB readers to disable object cache.

**Rationale**: DuckDB may cache stale data when files change.

**Implementation**:
```sql
PRAGMA disable_object_cache;
```

Documented in operational runbook for all reader services.

### 6. Anomaly Detection Thresholds

**Decision**: Conservative thresholds to minimize false positives.

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Row count drop | > 10% | Significant data loss indicator |
| Null spike | > 5% increase | Data quality degradation |
| Date gap | Any missing | Critical for time series continuity |

**Implementation**: Compare current sync stats against previous sync.

## Consequences

### Positive
- Strong data integrity guarantees with SHA-256
- Graceful handling of WRDS schema evolution
- Crash recovery through atomic writes and locking
- Early detection of data issues through anomaly detection
- Audit trail through manifest versioning

### Negative
- Lock overhead on write operations
- Disk space overhead from backups (mitigated by cleanup policy)
- Schema versioning adds complexity

### Neutral
- Requires operator training on new framework
- Integration needed with sync orchestrator (T1.2)

## Related ADRs
- ADR-0001: Data Pipeline Architecture
- ADR-0002: Exception Hierarchy

## Related Components
- T1.2: WRDS Sync Orchestrator
- T1.3: DuckDB Data Catalog

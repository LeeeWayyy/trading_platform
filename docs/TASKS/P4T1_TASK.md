# P4T1: Phase 1 Foundation - Data Infrastructure

**Task ID:** P4T1
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Timeline:** Phase 1 - Foundation (Weeks 1-4)
**Priority:** P0 - Foundation for all subsequent phases
**Estimated Effort:** 30-42 days (9 subtasks across 2 parallel tracks) - REVISED per review
**Status:** ✅ Approved (Ready for Implementation)
**Created:** 2025-12-03
**Last Updated:** 2025-12-03 (v1.3 - APPROVED by Gemini + Codex)

---

## Executive Summary

Phase 1 establishes the foundational data infrastructure required for P4's research platform. This phase focuses on building the local data warehouse using WRDS academic data sources (CRSP, Compustat, Fama-French) alongside a free-tier development option (yfinance).

**Goal:** Operational local data warehouse with data quality validation, versioning, and unified access patterns.

**Key Deliverables:**
1. Data Quality & Validation Framework (T1.1)
2. WRDS Connection & Bulk Sync Manager (T1.2)
3. CRSP, Compustat, Fama-French Local Providers (T1.3-T1.5)
4. Dataset Versioning for Reproducibility (T1.6)
5. yfinance Integration for Development (T4.1)
6. Unified Data Fetcher with Provider Protocol (T4.2-T4.3)

**Parallel Execution Plan (REVISED v2 - balanced workload per Gemini Review 2):**
- **Developer A (Track 1 + T1.5):** T1.1 → T1.2 → T1.3 → T1.4 → T1.5
- **Developer B (Track 4 + T1.6):** T4.1 → T4.2a (yfinance only) → T4.3 → T1.6 → T4.2b (CRSP integration)

**Note:** T4.2 is split to avoid dependency conflict with T1.3. T1.5 moved to Dev A to balance Week 4 workload.

---

## Architecture Overview

### DuckDB Concurrency Policy (CRITICAL)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SINGLE-WRITER, MULTI-READER Architecture             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐                    ┌─────────────────────────────┐ │
│  │  SYNC MANAGER   │ ◀── EXCLUSIVE ──▶  │  data/wrds/*.parquet       │ │
│  │  (Writer)       │     LOCK           │  (Parquet Files)            │ │
│  └─────────────────┘                    └─────────────────────────────┘ │
│         │                                          ▲                    │
│         │                                          │                    │
│         ▼                                          │ READ-ONLY          │
│  ┌─────────────────┐                    ┌─────────────────────────────┐ │
│  │  sync_lock.json │                    │  Web Console, Research      │ │
│  │  (Lock File)    │                    │  Notebooks, Analytics       │ │
│  └─────────────────┘                    └─────────────────────────────┘ │
│                                                                          │
│  Rules:                                                                  │
│  1. Sync Manager acquires exclusive lock before writing                 │
│  2. All readers use read_only=True connection                           │
│  3. If sync is running, readers see previous snapshot                   │
│  4. Sync operations run during off-hours (overnight)                    │
│  5. STALE LOCK RECOVERY: Lock expires after 4 hours + PID validation    │
│  6. OS-ATOMIC LOCKING: Use O_EXCL + fsync for cross-platform atomicity  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### OS-Atomic Lock Implementation (CRITICAL - Codex Review 2)

All writers (WRDS sync, ETL pipeline, Fama-French sync, yfinance cache) MUST use atomic locking:

```python
ATOMIC LOCK PATTERN:
1. Create lock file with O_CREAT | O_EXCL | O_WRONLY (fails if exists)
2. Write lock metadata: {pid, timestamp, hostname, writer_id}  # hostname for containers
3. fsync() to ensure durability
4. On release: unlink() the lock file

STALE LOCK RECOVERY (deterministic winner):
1. Validate lock file JSON schema (reject malformed, including extra fields)
2. Check if lock holder PID is running (OS-specific: kill(pid, 0) or /proc check)
3. Check if lock age > LOCK_TIMEOUT_HOURS
4. Handle PID reuse: compare hostname + writer_id, not just PID
5. If stale: use atomic rename to claim recovery token
   - First process to successfully rename(.lock -> .lock.recovery.<pid>) wins
   - Winner deletes old lock and creates new one
   - Losers retry after backoff
6. Test: split-brain scenario where two processes attempt recovery simultaneously
7. Test: PID reuse within short window (same PID, different writer_id)
8. Test: malformed JSON with extra/missing fields
```

### Manifest/Lock Atomicity Coupling (NEW - Codex Review 3)

Manifest updates MUST be coupled with lock and fsync:

```python
MANIFEST UPDATE PATTERN (under lock):
1. Acquire exclusive lock (O_EXCL pattern above)
2. Write Parquet to temp file
3. Validate checksum
4. Atomic rename temp -> final
5. Update manifest JSON with new file entry
6. fsync() manifest file  # CRITICAL: readers gate on this
7. Release lock

READER SNAPSHOT CONSISTENCY:
1. Read manifest version at query start
2. Only switch to new snapshot AFTER manifest fsync succeeds
3. If manifest version changes mid-query, retry with backoff
```

### Disk-Full/Low-Disk Handling (NEW - Codex Review 3)

```python
DISK-FULL SAFETY:
1. Before sync: check available disk space >= 2x expected write size
2. During atomic write: catch ENOSPC, quarantine temp files, abort cleanly
3. On failure: do NOT update manifest, alert immediately
4. Quarantine location: data/quarantine/<timestamp>_<operation>/
5. Auto-cleanup quarantine after 7 days (with alert if not empty)

LOW-DISK WATERMARKS:
- Warning: 80% capacity (~400 GB)
- Critical: 90% capacity (~450 GB)
- Sync blocked: 95% capacity (refuse to start new sync)
```

### Atomic Write Strategy (CRITICAL - Codex Review)

To prevent readers from seeing partially written files:

```
WRITE PATTERN:
1. Write to temp path: data/wrds/crsp/daily/2024.parquet.tmp
2. Validate checksum and row count
3. Atomic rename: mv 2024.parquet.tmp 2024.parquet
4. Update manifest with new checksum
5. Readers gate on manifest version for snapshot consistency

READER PATTERN:
1. Read manifest version at query start
2. If manifest version changes during query, retry with backoff
3. For long-lived sessions, reopen connection per query or set:
   PRAGMA disable_object_cache;
```

### Cache Invalidation for Long-Lived Readers (Codex Review)

DuckDB can cache file metadata. For consistency during/after sync:
- Readers should reopen connections per query OR
- Set `PRAGMA disable_object_cache;` at session start
- Document in runbook: reader behavior during sync windows

### DuckDB Operational Safety (NEW - Codex Review 3)

```python
DUCKDB CONFIGURATION:
# Sync process (writer):
PRAGMA threads=4;           # Limit CPU for background sync
PRAGMA memory_limit='4GB';  # Prevent OOM during large ingests
PRAGMA temp_directory='data/tmp/duckdb';  # Explicit temp location

# Reader processes:
PRAGMA threads=8;           # Allow more parallelism for queries
PRAGMA memory_limit='8GB';
PRAGMA disable_object_cache;  # For long-lived sessions

WAL LOCATION:
- WAL disabled for Parquet-only workflows (no DuckDB native tables)
- If DuckDB tables added later: WAL at data/duckdb/wal/

CACHE RESET GUIDANCE (runbook):
1. For long-lived sessions: call `PRAGMA disable_object_cache;` OR
2. Reconnect after each query OR
3. Check manifest version and reconnect if changed
4. Test: long-lived session sees updated data after sync completes
```

### Component Types

| Component | Type | Concurrency |
|-----------|------|-------------|
| `market_data_service` (existing) | FastAPI Service | Real-time streaming from Alpaca |
| `libs/data_providers/*` (P4) | Library | Historical data, file-based, read-only |
| `scripts/wrds_sync.py` (P4) | CLI/Cron Job | Single-writer, exclusive lock |

---

## Dependencies & Prerequisites

### External Dependencies
- **WRDS Account:** Academic subscription for CRSP, Compustat access
- **Python Libraries:** (already in requirements)
  - `polars` - DataFrame operations
  - `pyarrow` - Parquet I/O
  - `duckdb` - SQL queries on Parquet
  - `great_expectations` or `pandera` - Data validation
  - `yfinance` - Free market data for development

### Internal Dependencies (REVISED - Gemini Review)

```
T1.1 Data Quality ─────────────────────────────┐
         │                                      │
         ▼                                      │
T1.2 WRDS Sync ────────────────────────────────┼────▶ T1.6 Versioning
         │                                      │
         ├──────────┬──────────┐               │
         ▼          ▼          ▼               │
    T1.3 CRSP  T1.4 Compustat  T1.5 FF ────────┘

T4.1 yfinance ──▶ T4.2a Unified Fetcher (yfinance only)
                         │
                         ▼
                  T4.3 ETL Pipeline
                         │
         ┌───────────────┘
         ▼
    T4.2b Unified Fetcher CRSP Integration (AFTER T1.3 complete)
```

**Key Dependency Notes:**
- T1.6 depends on BOTH T1.1 (validation) AND T1.2 (data to version)
- T4.2 is SPLIT: T4.2a (yfinance support) has no T1.3 dependency
- T4.2b (CRSP integration) runs AFTER T1.3 is complete
- This resolves the schedule conflict identified in review

---

## Storage & Backup Policy (Codex Review - NEW)

### Storage Budget

| Dataset | Estimated Size | Retention |
|---------|---------------|-----------|
| CRSP Daily (2000-present) | ~5-10 GB | Permanent |
| Compustat Annual/Quarterly | ~2-5 GB | Permanent |
| Fama-French Factors | ~100 MB | Permanent |
| TAQ Aggregates (Phase 2) | ~100 GB | Permanent |
| Snapshots/Versions | ~50% overhead | 90 days rolling |

**Total Budget:** ~200 GB initially, ~500 GB with versions

### Backup Strategy (ENHANCED - Codex Review 2 & 3)

```
BACKUP CADENCE & LOCATION:
- Daily: Manifest files and sync state → data/backups/daily/
- Weekly: Full Parquet backup → external storage (NAS or cloud)
- On-demand: Before major sync operations → timestamped snapshot

TOOLING:
- Primary: rclone (supports S3, GCS, local, NAS)
- Alternative: rsync for local/NAS targets
- Encryption: AES-256 at rest for off-site backups

RPO/RTO TARGETS:
- RPO (Recovery Point Objective): 24 hours (daily backups)
- RTO (Recovery Time Objective): 4 hours (restore from weekly full + daily manifests)

VERIFICATION:
- Weekly checksum verification against manifests
- Monthly test restore to staging environment (scheduled drill)
- Automated alert if backup fails or checksum mismatch

DISK MONITORING:
- Low-disk watermark alert at 80% capacity (~400 GB of 500 GB budget)
- Critical alert at 90% capacity

ENCRYPTION KEY MANAGEMENT (NEW - Codex Review 3):
- Keys stored in secrets manager (not in repo or plaintext)
- Key rotation: quarterly, with re-encryption of active backups
- Key escrow: secondary key holder for disaster recovery
- Audit log: all key access logged

RESTORE DRILL PROCEDURE (NEW - Codex Review 3):
- Monthly drill to staging environment
- Checksum-verified restore test case (automated)
- Drill steps documented in runbook
- Success criteria: data matches original checksums, queries return expected results

RUNBOOK REFERENCE:
- See docs/RUNBOOKS/data-backup-restore.md (to be created in T1.2)
- Includes: restore procedure, monthly drill checklist, encryption key management
```

---

## Implementation Plan

### Component 1: T1.1 Data Quality & Validation Framework

**Effort:** 3-4 days | **PR:** `feat(p4): data quality framework`
**Status:** ⏳ Pending
**Priority:** P0 (Foundation - prevents silent corruption)

**Problem:** Bulk downloads can have partial loads, schema changes, or data corruption. Without validation, analytics will produce wrong results silently.

**Deliverables:**
- Ingestion contracts (expected row counts, checksums)
- Schema validation using great_expectations or pandera
- Sync manifest with watermarks and version tracking
- Rollback/backfill paths for failed syncs
- Anomaly detection (sudden drops in row counts, null spikes)

**Implementation:**
```python
# libs/data_quality/validation.py
from dataclasses import dataclass
from datetime import datetime, date
import polars as pl

@dataclass
class SyncManifest:
    """Tracks data sync state for reproducibility."""
    dataset: str
    sync_timestamp: datetime
    start_date: date
    end_date: date
    row_count: int
    checksum: str  # MD5 of parquet file
    schema_version: str
    wrds_query_hash: str  # Hash of SQL query used

class DataValidator:
    """Validates data quality after sync."""

    def validate_crsp_sync(self, df: pl.DataFrame, expected_rows: int) -> list[str]:
        """
        Validate CRSP data quality.
        Returns list of validation errors (empty = success).
        """
        errors = []

        # Row count check (allow 5% variance)
        if abs(df.height - expected_rows) / expected_rows > 0.05:
            errors.append(f"Row count mismatch: got {df.height}, expected ~{expected_rows}")

        # Null checks
        null_pct = df.null_count() / df.height
        if null_pct["ret"].item() > 0.1:
            errors.append(f"High null rate in returns: {null_pct['ret'].item():.2%}")

        # Date continuity check
        dates = df.select("date").unique().sort("date")
        # Check for unexpected gaps

        return errors
```

**Files to Create:**
- `libs/data_quality/__init__.py`
- `libs/data_quality/validation.py`
- `libs/data_quality/manifest.py`
- `libs/data_quality/expectations/` (great_expectations configs)
- `tests/libs/data_quality/test_validation.py`
- `tests/libs/data_quality/test_manifest.py`
- `docs/CONCEPTS/data-quality.md`
- `docs/ADRs/ADR-XXX-data-quality-framework.md`

**Test Cases:**
- [ ] SyncManifest serialization/deserialization
- [ ] Row count validation with tolerance
- [ ] Null percentage detection
- [ ] Checksum verification
- [ ] Schema validation against expected columns
- [ ] Date continuity gap detection
- [ ] **[NEW - Codex]** Interrupted sync rollback behavior
- [ ] **[NEW - Codex]** Checksum mismatch triggers quarantine and alert
- [ ] **[NEW - Codex]** Validation failure prevents manifest update
- [ ] **[NEW - Codex R3]** Schema drift detection (new/removed columns)
- [ ] **[NEW - Codex R3]** Schema migration/compatibility policy enforcement

**Schema Drift Policy (NEW - Codex Review 3):**
```python
SCHEMA DRIFT HANDLING:
1. New columns: accept with warning, add to schema registry
2. Removed columns: reject sync, alert for manual review
3. Type changes: reject sync, alert for manual review
4. Migration path: explicit schema version in manifest
```

---

### Component 2: T1.2 WRDS Connection & Bulk Sync Manager

**Effort:** 3-4 days | **PR:** `feat(p4): wrds sync manager`
**Status:** ⏳ Pending
**Priority:** P0 (Foundation for all WRDS data)
**Dependencies:** T1.1

**Deliverables:**
- WRDS connection wrapper with connection pooling
- Credential management via secrets manager
- Bulk sync manager with progress tracking and resume
- Rate limiting and backoff (WRDS enforces per-user query caps)
- Exclusive file locking for single-writer policy
- **Stale lock recovery** (4-hour timeout + PID validation for crashed writers)
- Lock contention tests (simulated dual writers)
- **[NEW - Codex]** Atomic write pattern (temp file + rename)
- **[NEW - Codex]** Corrupt lock file recovery
- Operational runbook for lock recovery procedures

**Implementation:**
```python
# libs/data_providers/wrds_sync_manager.py
import os
import json
from pathlib import Path
from datetime import datetime, timedelta

class WRDSSyncManager:
    """
    Manages bulk data synchronization from WRDS to local storage.
    Implements single-writer policy with exclusive locking and crash recovery.
    """

    LOCK_TIMEOUT_HOURS = 4  # Auto-expire stale locks

    def __init__(self, wrds_client, storage_path: Path):
        self.client = wrds_client
        self.storage_path = storage_path
        self.lock_file = storage_path / "sync_lock.json"

    def acquire_lock(self) -> bool:
        """
        Acquire exclusive write lock. Returns False if already locked.
        Includes stale lock recovery:
        - Check if lock holder PID is still running
        - Auto-release locks older than LOCK_TIMEOUT_HOURS
        """
        pass

    def release_lock(self):
        """Release exclusive write lock."""
        pass

    def _is_lock_stale(self, lock_info: dict) -> bool:
        """Check if lock is stale (timeout or dead PID)."""
        pass

    def _atomic_write_parquet(self, df: pl.DataFrame, target_path: Path):
        """
        Write Parquet atomically using temp file + rename.
        Prevents readers from seeing partial writes.
        """
        temp_path = target_path.with_suffix('.parquet.tmp')
        df.write_parquet(temp_path)
        # Validate before rename
        checksum = self._compute_checksum(temp_path)
        temp_path.rename(target_path)
        return checksum

    def full_sync_crsp(self, start_year: int = 2000):
        """Download full CRSP daily data with validation."""
        pass

    def incremental_sync_crsp(self):
        """Append new data since last sync."""
        pass
```

**CLI Commands:**
```bash
# Initial full sync (run once)
python scripts/wrds_sync.py --full-sync --dataset crsp

# Daily incremental sync (run via cron, typically overnight)
python scripts/wrds_sync.py --incremental --all

# Check sync status
python scripts/wrds_sync.py --status

# Verify data integrity (NEW)
python scripts/wrds_sync.py --verify-only
```

**Files to Create:**
- `libs/data_providers/__init__.py`
- `libs/data_providers/wrds_client.py`
- `libs/data_providers/wrds_sync_manager.py`
- `scripts/wrds_sync.py` (CLI)
- `tests/libs/data_providers/test_wrds_sync.py`
- `tests/libs/data_providers/test_wrds_client.py`
- `tests/libs/data_providers/test_lock_contention.py`
- `docs/CONCEPTS/wrds-data.md`
- `docs/ADRs/ADR-XXX-local-data-warehouse.md`
- `docs/RUNBOOKS/wrds-lock-recovery.md`
- **[NEW]** `docs/RUNBOOKS/data-backup-restore.md`

**Test Cases:**
- [ ] Lock acquisition succeeds when unlocked
- [ ] Lock acquisition fails when locked by active process
- [ ] Stale lock detection (timeout exceeded)
- [ ] Stale lock recovery (dead PID)
- [ ] Lock contention between two processes
- [ ] Full sync creates expected Parquet files
- [ ] Incremental sync appends correctly
- [ ] Rate limiting prevents WRDS quota violations
- [ ] **[NEW - Codex]** Atomic write: readers don't see .tmp files
- [ ] **[NEW - Codex]** Corrupt/malformed lock file recovery
- [ ] **[NEW - Codex]** Concurrent stale-lock recovery attempts
- [ ] **[NEW - Codex]** PID reuse edge case handling
- [ ] **[NEW - Codex]** --verify-only mode validates checksums
- [ ] **[NEW - Codex R3]** Network timeout with idempotent resume
- [ ] **[NEW - Codex R3]** Partial-year re-run (idempotent)
- [ ] **[NEW - Codex R3]** PID reuse within short window (hostname+writer_id check)
- [ ] **[NEW - Codex R3]** Malformed JSON with extra fields rejected
- [ ] **[NEW - Codex R3]** Disk-full during sync: clean abort, quarantine temp files
- [ ] **[NEW - Codex R3]** Manifest/lock atomicity: manifest only updated after fsync

---

### Component 3: T1.3 CRSP Local Provider

**Effort:** 3-4 days | **PR:** `feat(p4): crsp local provider`
**Status:** ⏳ Pending
**Dependencies:** T1.2

**Deliverables:**
- CRSP bulk download SQL queries
- Local Parquet storage with yearly partitioning
- DuckDB-based query provider (read-only)
- Point-in-time universe construction
- Survivorship-bias-free data access
- **[NEW - Codex R2]** Atomic write pattern (temp+rename+checksum+manifest)

**Storage Schema:**
```
data/wrds/crsp/
├── daily/
│   ├── 2020.parquet
│   ├── 2021.parquet
│   └── 2024.parquet
├── delisting/
│   └── all_delisting.parquet
└── metadata/
    ├── ticker_permno_map.parquet
    └── exchange_codes.parquet
```

**Files to Create:**
- `libs/data_providers/crsp_local_provider.py`
- `tests/libs/data_providers/test_crsp_local_provider.py`
- `docs/CONCEPTS/crsp-data.md`

**Test Cases:**
- [ ] Query returns correct schema
- [ ] Point-in-time universe excludes future data
- [ ] Delisted stocks included (survivorship-bias-free)
- [ ] Yearly partition selection optimizes queries
- [ ] **[NEW - Codex R2]** Atomic write: interrupted write leaves no partial files
- [ ] **[NEW - Codex R2]** Checksum mismatch triggers quarantine
- [ ] **[NEW - Codex R3]** IPO handling: stock appears on correct date, not before
- [ ] **[NEW - Codex R3]** Delisting handling: stock disappears on correct date
- [ ] **[NEW - Codex R3]** Ticker-to-PERMNO changes: historical lookups use correct mapping
- [ ] **[NEW - Codex R3]** Holiday gaps: no spurious gaps flagged for market closures

---

### Component 4: T1.4 Compustat Local Provider

**Effort:** 3-4 days | **PR:** `feat(p4): compustat local provider`
**Status:** ⏳ Pending
**Dependencies:** T1.2

**Deliverables:**
- Compustat bulk download SQL queries (annual + quarterly)
- Local Parquet storage
- Point-in-time fundamentals access (lag_months parameter)
- GVKEY-to-ticker mapping
- **[NEW - Codex R2]** Atomic write pattern (temp+rename+checksum+manifest)

**Key Feature:** Point-in-time data handling to prevent look-ahead bias.

**Files to Create:**
- `libs/data_providers/compustat_local_provider.py`
- `tests/libs/data_providers/test_compustat_local_provider.py`
- `docs/CONCEPTS/fundamental-data.md`

**Test Cases:**
- [ ] Annual fundamentals query
- [ ] Quarterly fundamentals query
- [ ] Point-in-time lag handling (e.g., 3-month lag for 10-K filings)
- [ ] GVKEY-to-ticker mapping accuracy
- [ ] **[NEW - Codex R2]** Atomic write: interrupted write leaves no partial files
- [ ] **[NEW - Codex R2]** Checksum mismatch triggers quarantine
- [ ] **[NEW - Codex R3]** GVKEY changes: historical lookups use correct mapping
- [ ] **[NEW - Codex R3]** Filing lag parameterization: configurable announcement delay
- [ ] **[NEW - Codex R3]** Restatements: use original filing date, not restatement date

**Point-in-Time Correctness (NEW - Codex Review 3):**
```python
PTI JOIN RULES:
1. Use filing_date (or announcement_date), NOT period_end_date
2. Configurable lag: default 90 days for 10-K, 45 days for 10-Q
3. Restatements: always use original filing date to avoid look-ahead
4. Test: query as-of date returns only data available at that time
```

---

### Component 5: T1.5 Fama-French Local Provider

**Effort:** 2-3 days | **PR:** `feat(p4): fama-french local provider`
**Status:** ⏳ Pending
**Dependencies:** T1.2

**Deliverables:**
- Bulk download from Ken French website (pandas-datareader)
- Local Parquet storage for all factor datasets
- 3-factor, 5-factor, 6-factor (momentum) models
- Industry portfolio returns (10, 30, 49 industries)
- **[NEW - Codex R2]** Atomic write pattern (temp+rename+checksum+manifest)

**Files to Create:**
- `libs/data_providers/fama_french_local_provider.py`
- `scripts/fama_french_sync.py` (CLI)
- `tests/libs/data_providers/test_fama_french_local_provider.py`
- `docs/CONCEPTS/fama-french-factors.md`

**Test Cases:**
- [ ] 3-factor model download and storage
- [ ] 5-factor model download and storage
- [ ] Industry portfolios download
- [ ] Daily vs monthly factor data handling
- [ ] **[NEW - Codex R2]** Atomic write: interrupted write leaves no partial files
- [ ] **[NEW - Codex R2]** Checksum mismatch triggers quarantine

---

### Component 6: T1.6 Dataset Versioning & Reproducibility

**Effort:** 4-5 days | **PR:** `feat(p4): dataset versioning`
**Status:** ⏳ Pending
**Priority:** P1 (Critical for research reproducibility)
**Dependencies:** T1.1, T1.2 (REVISED - Gemini Review)

**Note:** Effort increased from 3-4 to 4-5 days per Codex review (catalog/migration work).

**Problem:** Incremental updates can make backtests irreproducible if data corrections occur upstream.

**Deliverables:**
- Dataset snapshots with Git-like versioning
- Time-travel queries ("give me data as of 2024-01-15")
- Manifest files linking backtest results to data versions
- **De-scoped for v1:** Iceberg/DuckDB time-travel integration (follow-up PR)

**Implementation:**
```python
# libs/data_quality/versioning.py
class DatasetVersionManager:
    """Manages dataset versions for reproducibility."""

    def create_snapshot(self, dataset: str, version_tag: str):
        """Create immutable snapshot of current dataset state."""
        pass

    def get_data_at_version(self, dataset: str, version: str) -> pl.DataFrame:
        """Retrieve data from a specific version."""
        pass

    def get_manifest_for_backtest(self, backtest_id: str) -> dict:
        """Get data versions used in a backtest run."""
        pass
```

**Files to Create:**
- `libs/data_quality/versioning.py`
- `tests/libs/data_quality/test_versioning.py`
- `docs/CONCEPTS/dataset-versioning.md`
- `docs/ADRs/ADR-XXX-dataset-versioning.md`

**Snapshot Storage Mechanics (NEW - Codex Review 3):**

```python
STORAGE STRATEGY:
1. Primary: Hardlinks for unchanged partitions (space-efficient, fast)
2. Fallback: Copy if hardlinks not supported (cross-filesystem)
3. Deduplication: Content-addressable storage for identical files
   - Hash file content → store at data/cas/<hash>.parquet
   - Snapshot references hash, not file path

CHECKSUM/DIFF GENERATION:
1. Per-file MD5 checksum stored in manifest
2. Diff = list of (file, old_checksum, new_checksum)
3. Validation: verify checksum on snapshot creation and retrieval

CORRUPTED SNAPSHOT RECOVERY:
1. Detect via checksum mismatch
2. Attempt recovery from nearest valid snapshot + diffs
3. If unrecoverable: alert, mark snapshot as corrupted, exclude from queries
```

**Retention Policy (ENHANCED - Codex Review 2):**

The 90-day rolling retention applies to FULL SNAPSHOTS only. To preserve reproducibility:
- **Manifests:** Kept indefinitely (small metadata files)
- **Full snapshots:** 90-day rolling deletion
- **Referenced snapshots:** NEVER deleted while linked to backtests
- **Compressed diffs:** Keep indefinitely for space-efficient reproducibility

```python
RETENTION RULES:
1. Before deleting snapshot, check: manifest.is_referenced_by_backtest()
2. If referenced: skip deletion, log warning
3. For long-term reproducibility: store manifest + diff from nearest retained snapshot
4. Automatic deduplication: identical Parquet partitions share storage
```

**Test Cases:**
- [ ] Snapshot creation with version tag
- [ ] Snapshot retrieval by version
- [ ] Time-travel query returns correct data
- [ ] Backtest-to-version manifest linkage
- [ ] Snapshot retention policy enforcement (90 days)
- [ ] **[NEW - Codex R2]** Referenced snapshot deletion blocked
- [ ] **[NEW - Codex R2]** Manifest never deleted while referenced by backtest
- [ ] **[NEW - Codex R2]** Diff-based recovery from retained snapshot
- [ ] **[NEW - Codex R3]** Hardlink creation for unchanged partitions
- [ ] **[NEW - Codex R3]** Checksum validation on snapshot creation
- [ ] **[NEW - Codex R3]** Corrupted snapshot diff recovery

---

### Component 7: T4.1 yfinance Integration

**Effort:** 3-4 days | **PR:** `feat(p4): yfinance provider`
**Status:** ⏳ Pending
**Priority:** P0 (Free data source for development)

**Important:** yfinance lacks survivorship handling and corporate actions. Gate to dev-only; production backtests must use CRSP.

**Deliverables:**
- yfinance data fetcher with rate limiting
- Local caching to Parquet
- Clear dev-only warnings in logs
- **[NEW - Codex]** Drift detection against CRSP sample (when available)
- Reconciliation checks against CRSP (when available)
- **[NEW - Codex R3]** Config flag to enforce CRSP-only in production

**Production Gating (NEW - Codex Review 3):**

```python
YFINANCE GATING RULES:
1. Config flag: USE_YFINANCE_IN_PROD = False (default)
2. If CRSP available AND env=production: block yfinance usage
3. Fallback chain: CRSP → (yfinance only if USE_YFINANCE_IN_PROD=True)
4. Log warning if yfinance used in non-dev environment
5. Test: fallback chain cannot silently prioritize yfinance in prod
```

**Drift Detection Baseline (ENHANCED - Codex Review 2):**

For pre-CRSP development, include a minimal baseline dataset in the repo:
```
data/baseline/
├── spy_60d.parquet      # Last 60 trading days for SPY
├── qqq_60d.parquet      # Last 60 trading days for QQQ
└── baseline_manifest.json  # Source, date range, checksums
```
- Baseline refreshed quarterly from CRSP (or manually verified yfinance data)
- Drift check: if |yfinance - baseline| > 1% for price, alert
- Missing baseline: skip drift check with warning (not error)

**Files to Create:**
- `libs/data_providers/yfinance_provider.py`
- `tests/libs/data_providers/test_yfinance_provider.py`
- `docs/CONCEPTS/yfinance-limitations.md`

**Test Cases:**
- [ ] Single symbol fetch
- [ ] Bulk symbol fetch with rate limiting
- [ ] Local cache hit/miss behavior
- [ ] Dev-only warning in non-dev environments
- [ ] **[NEW - Codex]** Drift detection: alert if yfinance vs CRSP delta > threshold
- [ ] **[NEW - Codex]** Drift check runs automatically when CRSP available
- [ ] **[NEW - Codex R2]** Missing baseline handling (warning, not error)
- [ ] **[NEW - Codex R2]** Baseline manifest validation
- [ ] **[NEW - Codex R3]** Prod gating: yfinance blocked when CRSP available + env=prod
- [ ] **[NEW - Codex R3]** Fallback chain cannot silently prioritize yfinance in prod

---

### Component 8: T4.2 Unified Data Fetcher (SPLIT per review)

#### T4.2a: Protocol & yfinance Support
**Effort:** 2-3 days | **PR:** `feat(p4): unified data fetcher`
**Status:** ⏳ Pending
**Dependencies:** T4.1

**Deliverables:**
- Common `DataProvider` protocol/interface
- Provider factory (switch source via config)
- yfinance provider integration
- Usage logging and metrics

**Implementation:**
```python
# libs/data_providers/protocols.py
from typing import Protocol
import polars as pl

class DataProvider(Protocol):
    """Common interface for all data providers."""

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date
    ) -> pl.DataFrame:
        """Fetch daily OHLCV data."""
        ...

    def get_universe(self, as_of_date: date) -> list[str]:
        """Get tradeable universe as of date."""
        ...
```

**Files to Create:**
- `libs/data_providers/protocols.py`
- `libs/data_providers/unified_fetcher.py`
- `scripts/fetch_data.py` (CLI)
- `tests/libs/data_providers/test_unified_fetcher.py`
- `docs/CONCEPTS/unified-data-fetcher.md`
- `docs/ADRs/ADR-XXX-data-provider-protocol.md`

**Test Cases:**
- [ ] Protocol compliance for yfinance provider
- [ ] Provider factory returns correct provider
- [ ] Usage metrics are logged

#### T4.2b: CRSP Provider Integration
**Effort:** 1-2 days | **PR:** `feat(p4): unified fetcher crsp integration`
**Status:** ⏳ Pending (BLOCKED on T1.3)
**Dependencies:** T1.3, T4.2a

**Deliverables:**
- CRSP provider integration into unified fetcher
- Automatic fallback chain (CRSP → yfinance)
- Provider priority configuration

**Test Cases:**
- [ ] Protocol compliance for CRSP provider
- [ ] Fallback chain works when CRSP unavailable
- [ ] Priority selection via config

---

### Component 9: T4.3 Data Storage & ETL Pipeline

**Effort:** 3-4 days | **PR:** `feat(p4): historical etl pipeline`
**Status:** ⏳ Pending
**Dependencies:** T4.2a

**Deliverables:**
- ETL orchestration for historical data
- Partitioned Parquet storage
- DuckDB catalog management
- Incremental update support
- **[NEW - Codex R2]** Atomic write pattern for ALL outputs (temp+rename+checksum+manifest)

**Files to Create:**
- `libs/data_pipeline/__init__.py`
- `libs/data_pipeline/historical_etl.py`
- `tests/libs/data_pipeline/test_historical_etl.py`
- `docs/CONCEPTS/historical-etl-pipeline.md`

**Test Cases:**
- [ ] Full ETL pipeline execution
- [ ] Incremental updates append correctly
- [ ] DuckDB catalog reflects all tables
- [ ] Partition pruning in queries
- [ ] **[NEW - Codex R2]** Atomic write: interrupted write leaves no partial files
- [ ] **[NEW - Codex R2]** Checksum mismatch triggers quarantine and alert

---

## Execution Schedule (REVISED v2 - balanced workload per Gemini Review 2)

### Week 1
| Day | Developer A (Track 1) | Developer B (Track 4) |
|-----|----------------------|----------------------|
| 1-2 | T1.1 Data Quality | T4.1 yfinance |
| 3-4 | T1.1 Data Quality (complete) | T4.1 yfinance (complete) |

### Week 2
| Day | Developer A (Track 1) | Developer B (Track 4) |
|-----|----------------------|----------------------|
| 1-2 | T1.2 WRDS Sync | T4.2a Unified Fetcher (yfinance) |
| 3-4 | T1.2 WRDS Sync (complete) | T4.2a Unified Fetcher (complete) |

### Week 3
| Day | Developer A (Track 1) | Developer B (Track 4) |
|-----|----------------------|----------------------|
| 1-2 | T1.3 CRSP | T4.3 ETL Pipeline |
| 3-4 | T1.3 CRSP (complete) | T4.3 ETL Pipeline (complete) |

### Week 4
| Day | Developer A (Track 1) | Developer B (Track 4) |
|-----|----------------------|----------------------|
| 1-2 | T1.4 Compustat | T1.6 Versioning |
| 3-4 | T1.4 (complete), T1.5 FF start | T1.6 Versioning (complete) |

### Week 5 (Buffer/Overflow)
| Day | Developer A (Track 1) | Developer B (Track 4) |
|-----|----------------------|----------------------|
| 1-2 | T1.5 Fama-French (complete) | T4.2b CRSP Integration |
| 3-4 | Buffer/Integration testing | T4.2b (complete), Integration |

**Milestone:** Local data warehouse operational

**Workload Balance (REVISED v2 per Gemini Review 2):**
- Developer A: T1.1 + T1.2 + T1.3 + T1.4 + T1.5 = ~16-19 days
- Developer B: T4.1 + T4.2a + T4.3 + T1.6 + T4.2b = ~13-16 days

This resolves the Week 4 bottleneck where Developer B was allocated 6-9 days of work in a 5-day week.

---

## Success Metrics

| Metric | Target | Verification |
|--------|--------|--------------|
| **Data providers** | 5 (CRSP, Compustat, FF, yfinance, unified) | Count implementations |
| **Test coverage** | >85% | pytest --cov report |
| **Data validation** | 0 silent corruptions | Validation framework checks |
| **Reproducibility** | 100% with versioned datasets | Version manifest linkage |
| **All components complete** | 9/9 | Task tracking |
| **Atomic writes** | 0 partial reads | No .tmp files in production |

---

## Operational Runbook Entries (NEW - Codex Review 2 & 3)

The following runbook entries MUST be created as part of T1.2:

| Entry | Location | Content |
|-------|----------|---------|
| **Lock Recovery** | `docs/RUNBOOKS/wrds-lock-recovery.md` | Steps to identify/recover stale locks, handle split-brain |
| **Backup/Restore** | `docs/RUNBOOKS/data-backup-restore.md` | rclone setup, restore procedure, monthly drill checklist |
| **DuckDB Cache** | `docs/RUNBOOKS/duckdb-operations.md` | Cache invalidation toggles, reader configuration during sync |
| **Credential Rotation** | `docs/RUNBOOKS/wrds-credentials.md` | WRDS credential rotation, expiry monitoring, alerting |
| **Disk Monitoring** | `docs/RUNBOOKS/data-storage.md` | Low-disk watermark setup, cleanup procedures |

**Monitoring & Alerting Checklist:**
- [ ] Disk usage > 80% triggers warning alert
- [ ] Disk usage > 90% triggers critical alert
- [ ] WRDS credential expiry alert 30 days before
- [ ] Backup failure alert (daily check)
- [ ] Checksum mismatch alert (per sync)
- [ ] Stale lock detection alert (if lock age > 4 hours)
- [ ] **[NEW - Codex R3]** Manifest-version mismatch vs actual files alert
- [ ] **[NEW - Codex R3]** Sync duration SLO breach alert (e.g., > 4 hours)
- [ ] **[NEW - Codex R3]** Late-data arrival alert (data not updated by expected time)

**SLOs (NEW - Codex Review 3):**

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Sync duration (full) | < 2 hours | > 4 hours |
| Sync duration (incremental) | < 30 min | > 1 hour |
| Data freshness | Updated by 6 AM ET | Not updated by 8 AM ET |
| Manifest consistency | 100% | Any mismatch |

## Implementation Notes (Gemini Review 3 - Non-blocking)

The following items are implementation considerations, not blocking issues:

1. **Docker Integration:** Update `docker-compose.yml` to mount `data/` directory as volume for services (e.g., `web_console`) needing read access to local warehouse.

2. **Containerized Locking:** Lock metadata includes `hostname` and `writer_id` (already added in v1.3) to handle PID ambiguity across containers.

3. **Git Ignore:** Verify these paths are in `.gitignore`:
   ```
   data/wrds/
   data/backups/
   data/baseline/*.parquet
   data/quarantine/
   data/tmp/
   ```

---

## Risk Analysis (REVISED)

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| WRDS API rate limiting | HIGH | MEDIUM | Implement exponential backoff, schedule syncs overnight |
| DuckDB write contention | MEDIUM | HIGH | Single-writer policy with file locking + atomic rename |
| yfinance data quality issues | HIGH | LOW | Dev-only gate, drift detection vs CRSP |
| Large data volumes | MEDIUM | MEDIUM | Yearly partitioning, incremental sync |
| WRDS credential management | LOW | HIGH | Use existing secrets manager pattern |
| **[NEW]** Partial writes visible to readers | MEDIUM | HIGH | Atomic write pattern (temp + rename) |
| **[NEW]** DuckDB cache stale reads | MEDIUM | MEDIUM | Cache invalidation guidance in runbook |
| **[NEW]** Lock file corruption | LOW | MEDIUM | Malformed lock recovery, validation |
| **[NEW]** Storage growth unbounded | LOW | MEDIUM | Retention policy, 90-day snapshot limit |

---

## Review Log

### Review 1: Gemini + Codex (2025-12-03)

**Gemini Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. **CRITICAL:** T4.2 depends on T1.3 but scheduled before it → Split T4.2 into T4.2a/T4.2b
  2. T1.6 missing T1.2 dependency → Added
  3. Workload imbalance (Track 1: 20d, Track 4: 12d) → Rebalanced by moving T1.5, T1.6 to Dev B

**Codex Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. **HIGH:** Same schedule conflict (T4.2 → T1.3) → Fixed with split
  2. **HIGH:** No atomic write strategy → Added temp file + rename pattern
  3. **MEDIUM:** DuckDB cache consistency → Added cache invalidation guidance
  4. **MEDIUM:** Missing resilience tests → Added interrupted sync, checksum mismatch tests
  5. **MEDIUM:** T1.6 effort underestimated → Increased to 4-5 days
  6. **LOW:** yfinance drift mitigation → Added automated drift detection
  7. **LOW:** Lock corruption tests → Added malformed lock, concurrent recovery tests
  8. **LOW:** Storage/backup omission → Added storage budget and backup policy section

### Review 2: Gemini + Codex (2025-12-03)

**Gemini Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. **CRITICAL:** Schedule infeasibility - Developer B overloaded in Week 4 (6-9 days vs 5 days capacity)
  2. **SUGGESTION:** Move T1.5 (Fama-French) to Developer A → **FIXED: v1.2**
  3. **VERIFIED:** T4.2 split resolves dependency cycle
  4. **VERIFIED:** Atomic write and cache invalidation patterns sufficient

**Codex Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. **BLOCKING:** OS-atomic lock creation (O_EXCL + fsync) not specified → **FIXED: v1.2**
  2. **BLOCKING:** Stale-lock recovery needs deterministic winner + split-brain test → **FIXED: v1.2**
  3. **HIGH:** Backup policy lacks location/encryption/RPO-RTO → **FIXED: v1.2**
  4. **HIGH:** 90-day retention conflicts with reproducibility → **FIXED: v1.2** (manifest protection)
  5. **MEDIUM:** yfinance drift check needs pre-CRSP baseline → **FIXED: v1.2**
  6. **MEDIUM:** Atomic write pattern needed for ALL components (not just WRDS) → **FIXED: v1.2**
  7. **MEDIUM:** Operational runbook entries missing → **FIXED: v1.2**

### Review 3: Gemini + Codex (2025-12-03)

**Gemini Review:**
- Status: **APPROVED**
- Strengths noted:
  1. Atomic write strategy correctly specified
  2. Dependency management resolved (T4.2 split)
  3. Test coverage robust (lock contention, interrupted syncs, data drift)
- Non-blocking implementation notes:
  1. Docker integration (mount data/ volume) → Added to Implementation Notes
  2. Containerized locking (hostname in metadata) → Already in v1.2
  3. Git ignore paths → Added to Implementation Notes

**Codex Review:**
- Status: CHANGES_REQUESTED
- Key findings:
  1. **HIGH:** Disk-full/low-disk handling → **FIXED: v1.3**
  2. **HIGH:** Manifest/lock atomicity coupling → **FIXED: v1.3**
  3. **MEDIUM:** Network timeout/retry, schema drift tests → **FIXED: v1.3**
  4. **MEDIUM:** Snapshot storage mechanics (hardlinks/dedup) → **FIXED: v1.3**
  5. **MEDIUM:** Backup encryption key management, restore drill → **FIXED: v1.3**
  6. **MEDIUM:** DuckDB operational safety (WAL, PRAGMA) → **FIXED: v1.3**
  7. **MEDIUM:** CRSP/Compustat PTI edge cases (IPO, delisting, ticker changes) → **FIXED: v1.3**
  8. **MEDIUM:** yfinance prod gating enforcement → **FIXED: v1.3**
  9. **LOW:** Lock recovery PID reuse + malformed JSON tests → **FIXED: v1.3**
  10. **LOW:** Monitoring gaps (manifest mismatch, sync SLOs) → **FIXED: v1.3**

### Review 4: Gemini + Codex (2025-12-03) - FINAL

**Gemini Review:**
- Status: **APPROVED**
- Assessment:
  1. Task decomposition comprehensive and logical
  2. T4.2 split correctly resolves dependency conflicts
  3. Workload balance addressed (Dev A: ~19d, Dev B: ~16d), Week 5 buffer prudent
  4. Single-Writer/Multi-Reader with OS-atomic locking robust and appropriate
  5. Test coverage extensive (lock contention, interrupted syncs, failure modes)
- Non-blocking recommendations:
  1. Strictly adhere to "Reader Snapshot Consistency" pattern during T1.3
  2. Drift check should gracefully handle pre-CRSP period (skip if baseline missing)

**Codex Review:**
- Status: **APPROVED**
- Assessment: Technically solid with strong coverage of locking, atomic writes, validation, PTI handling, drift detection, backups, and monitoring
- Non-blocking action items (to address during implementation):
  1. Document same-filesystem requirement + directory fsync for crash safety
  2. Reconcile 4-week vs 5-week schedule wording (Week 5 is buffer)
  3. Formalize restore-drill success criteria (e.g., <4h with checksum parity)
  4. Expose metrics for lock recovery, quarantine growth, drift results

---

## References

- [P4_PLANNING.md](./P4_PLANNING.md) - Full P4 specification
- [CLAUDE.md](../AI/CLAUDE.md) - AI workflow guidance
- [Workflows/03-reviews.md](../AI/Workflows/03-reviews.md) - Review workflow

---

**Last Updated:** 2025-12-03
**Author:** Claude Code
**Version:** 1.3 (Review Iteration 3 feedback incorporated - disk handling, manifest atomicity, PTI edge cases, SLOs)

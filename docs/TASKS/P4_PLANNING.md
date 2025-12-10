# P4 Planning: Advanced Features & Research Infrastructure

**Phase:** P4 (Advanced Features & Research)
**Timeline:** Days 181-315 (~19 weeks / 133 days with parallel execution)
**Status:** 🚧 In Progress
**Previous Phase:** P3 (Issue Remediation - Complete)
**Last Updated:** 2025-12-08

---

## Executive Summary

With P0-P2 complete and P3 addressing critical issues, **P4 focuses on advanced research infrastructure and enhanced analytics capabilities**. The primary goals are:

1. **WRDS Research & Analytics Platform** - Build factor models, risk analytics, and alpha research tools using academic-grade data
2. **Unified Historical Data Service** - Efficient fetching and storage from multiple providers (yfinance, WRDS TAQ, etc.)
3. **Enhanced Backtesting Infrastructure** - Web UI, walk-forward optimization, Monte Carlo simulation
4. **Web Console Analytics** - Performance dashboards, factor attribution, strategy comparison

**P4 Philosophy: "Research-to-Production Parity"**
- Research features must seamlessly integrate with production trading
- All new data sources must work with existing feature store and strategies
- Analytics must be actionable for trading decisions

**Note:** Advanced strategy implementations are deferred to **P5 (Advanced Strategies)**.

---

## 🏗️ Architecture Decisions

### DuckDB Concurrency Policy

**CRITICAL:** The local data warehouse uses DuckDB with file-based Parquet storage. To prevent write contention:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CONCURRENCY POLICY                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  SINGLE-WRITER, MULTI-READER Architecture                               │
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
│     - If lock holder PID not running → auto-release lock                │
│     - Prevents indefinite blocking from crashed writers                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Global Write Coordination Policy (All Analytics/Model Outputs)

**CRITICAL:** Beyond WRDS sync, all analytics and model outputs also write to shared locations. To prevent corruption during parallel track execution:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    GLOBAL WRITE COORDINATION                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  WRITE DOMAINS (each has dedicated lock):                               │
│                                                                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐     │
│  │ data/wrds/      │    │ data/analytics/ │    │ data/models/    │     │
│  │ WRDS Sync Lock  │    │ Analytics Lock  │    │ Registry Lock   │     │
│  │ (sync_lock.json)│    │ (analytics.lock)│    │ (registry.lock) │     │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘     │
│         │                      │                      │                 │
│         ▼                      ▼                      ▼                 │
│  Writers: wrds_sync    Writers: T2.x,T3.x    Writers: T2.8 registry    │
│                        factor, risk, alpha    model promotion          │
│                        microstructure                                   │
│                                                                          │
│  COORDINATION RULES:                                                    │
│  1. Each domain has ONE lock file (same pattern as WRDS)               │
│  2. Writer acquires lock before ANY write to domain                    │
│  3. Atomic write pattern: temp file → validate → rename                │
│  4. Stale lock recovery: 4hr timeout + PID validation                  │
│  5. Cross-domain reads: always allowed (snapshot isolation)            │
│                                                                          │
│  PARALLEL TRACK SAFETY:                                                 │
│  - Track 1 (TAQ): writes data/wrds/ only                               │
│  - Track 2 (Factor/Risk): writes data/analytics/, data/models/         │
│  - Track 3 (Microstructure): writes data/analytics/microstructure/     │
│  - Tracks do NOT contend for same lock during normal operation         │
│  - If contention occurs: second writer waits or fails gracefully       │
│                                                                          │
│  RECOVERY:                                                              │
│  - Corrupt detection: checksum validation on every read                │
│  - Corrupt recovery: restore from backup + invalidate downstream cache │
│  - Lock stuck: kill writer PID, auto-release after 4hr                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Implementation Pattern (shared across all domains):**
```python
# libs/common/lock_manager.py
class DomainLockManager:
    """Shared lock manager for all write domains."""

    DOMAINS = {
        "wrds": Path("data/wrds/sync_lock.json"),
        "analytics": Path("data/analytics/analytics.lock"),
        "models": Path("data/models/registry.lock"),
    }
    LOCK_TIMEOUT_HOURS = 4

    def acquire(self, domain: str) -> bool:
        """Acquire domain lock with stale recovery."""
        ...

    def release(self, domain: str) -> None:
        """Release domain lock."""
        ...

    def with_lock(self, domain: str):
        """Context manager for lock acquisition."""
        ...
```

### Data Service vs Library Clarification

| Component | Type | Concurrency |
|-----------|------|-------------|
| `market_data_service` (existing) | FastAPI Service | Real-time streaming from Alpaca |
| `libs/data_providers/*` (P4) | Library | Historical data, file-based, read-only |
| `scripts/wrds_sync.py` (P4) | CLI/Cron Job | Single-writer, exclusive lock |

---

## 📊 Progress Summary

**Overall:** ~33% (P4T1 Complete, P4T2 54% Complete)

| Track | Tasks | Effort | Progress | Status |
|-------|-------|--------|----------|--------|
| **Track 1: Data Infrastructure** | T1.1-T1.8 | ~23-28 days | ✅ 100% | Complete (P4T1 + T1.7-T1.8 in P4T2) |
| **Track 2: Factor & Risk Analytics** | T2.1-T2.8 | ~26-33 days | 🚧 50% | T2.1-T2.4 ✅, T2.5-T2.8 ⏳ |
| **Track 3: Market Microstructure** | T3.1-T3.3 | ~10-13 days | 🚧 33% | T3.1 ✅, T3.2-T3.3 ⏳ |
| **Track 4: Historical Data Service** | T4.1-T4.3 | ~9-12 days | ✅ 100% | Complete (P4T1) |
| **Track 5: Backtest Enhancement** | T5.1-T5.6 | ~18-23 days | 0% | 📋 Planning |
| **Track 6: Web Console - Core Analytics** | T6.1-T6.6 | ~17-21 days | 0% | 📋 Planning |
| **Track 7: Web Console - Operations** | T7.1-T7.5 | ~12-16 days | 0% | 📋 Planning |
| **Track 8: Web Console - Data Management** | T8.1-T8.3 | ~7-10 days | 0% | 📋 Planning |
| **Track 9: Web Console - Research & Reporting** | T9.1-T9.6 | ~14-19 days | 0% | 📋 Planning |

**P4T2 Task Status (Phase 2 Analytics):**
| Task | Status | Notes |
|------|--------|-------|
| T1.7-T1.8 TAQ Storage/Query | ✅ Complete | |
| T2.1 Multi-Factor Model | ✅ Complete | |
| T2.2 Factor Covariance | ✅ Complete | |
| T2.3 Risk Analytics | ✅ Complete | |
| T2.4 Optimizer & Stress | ✅ Complete | |
| T2.5 Alpha Framework | ⏳ Pending | +Qlib metrics adapter |
| T2.6 Alpha Advanced | ⏳ Pending | |
| T2.7 Factor Attribution | ⏳ Pending | |
| T2.8 Model Registry | ⏳ Pending | +DiskExpressionCache |
| T3.1 Microstructure | ✅ Complete | |
| T3.2 Execution Quality | ⏳ Pending | |
| T3.3 Event Study | ⏳ Pending | |

**Total Estimated Effort:** ~135-174 days sequential
**Parallel Execution:** ~133 days / 19 weeks (assuming 2 developers on parallel tracks)
**Total Tasks:** 48 PRs

**Timeline Assumptions:**
- Parallel track execution requires 2+ developers or alternating focus
- Critical path: Track 1 → Track 2 → Track 5 (Data → Analytics → Backtest)
- Stretch items (can defer to P4b if needed): T3.3 Event Study, T9.3 Notebook Launcher, T9.6 Tax Advanced

### Capacity Plan & Cut-Lines

**Resource Model (2 Developers):**

| Phase | Dev A Focus | Dev B Focus | Dev-Weeks | Buffer |
|-------|-------------|-------------|-----------|--------|
| **Phase 1** (Weeks 1-4) | Track 1: T1.1-T1.6 | Track 4: T4.1-T4.3 | 8 dev-weeks | 1 week |
| **Phase 2** (Weeks 5-10) | Track 2: T2.1-T2.7 | Track 1/3: T1.7-T1.8, T3.1-T3.3, T2.8 | 12 dev-weeks | 2 weeks |
| **Phase 3** (Weeks 11-14) | Track 5: T5.1-T5.6 | Track 6: T6.1-T6.6 | 8 dev-weeks | 1 week |
| **Phase 4** (Weeks 15-17) | Track 7: T7.1-T7.5 | Track 8: T8.1-T8.3 | 6 dev-weeks | 1 week |
| **Phase 5** (Weeks 18-19) | Track 9: T9.1-T9.4 | Track 9: T9.5-T9.6 + Integration | 4 dev-weeks | 1 week |

**Total:** 38 dev-weeks + 6 weeks buffer = 44 dev-weeks (~22 calendar weeks with 2 devs)

**Cut-Line Policy:**
If burndown exceeds capacity at any phase checkpoint, defer items in this priority order:

| Priority | Items to Defer | Trigger Condition |
|----------|----------------|-------------------|
| **Cut-1** (First) | T9.3 Notebook, T9.6 Tax Advanced | Phase 4 >20% over budget |
| **Cut-2** | T3.3 Event Study, T5.5 Monte Carlo | Phase 3 >20% over budget |
| **Cut-3** | T6.4 Strategy Compare, T6.5 Trade Journal | Phase 3 >30% over budget |
| **Cut-4** (Last Resort) | T7.3 Alert Config, T8.3 Data Quality | Phase 2 >40% over budget |

**Integration & Test Buffers (included in phase buffers):**
- Each phase includes 1-2 weeks buffer for:
  - Cross-track integration testing
  - Security review for web console endpoints
  - Performance benchmarking
  - Documentation updates

**Burndown Checkpoints:**
- **Week 4:** Track 1/4 complete → Proceed or escalate
- **Week 10:** Track 2/3 complete → Cut-1 decision point
- **Week 14:** Track 5/6 complete → Cut-2 decision point
- **Week 17:** Track 7/8 complete → Cut-3 decision point

**Related Documentation:**
- [P4T2_TASK.md](./P4T2_TASK.md) - Detailed Phase 2 task specifications
- [qlib-comparison.md](../CONCEPTS/qlib-comparison.md) - Qlib integration analysis
- [ADR-0022-qlib-integration.md](../ADRs/ADR-0022-qlib-integration.md) - Integration architecture decision

---

## Track 1: Data Infrastructure ⭐⭐⭐ FOUNDATION

**Goal:** Build the local data warehouse that powers all downstream analytics

Each task is designed as **one PR** with clear boundaries.

---

### T1.1: Data Quality & Validation Framework
**Effort:** 3-4 days | **PR:** `feat(p4): data quality framework`
**Status:** ✅ Complete (P4T1)
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

        # Row count check
        if abs(df.height - expected_rows) / expected_rows > 0.05:
            errors.append(f"Row count mismatch: got {df.height}, expected ~{expected_rows}")

        # Null checks
        null_pct = df.null_count() / df.height
        if null_pct["ret"].item() > 0.1:
            errors.append(f"High null rate in returns: {null_pct['ret'].item():.2%}")

        # Date continuity
        dates = df.select("date").unique().sort("date")
        # Check for unexpected gaps

        return errors
```

**Files to Create:**
- `libs/data_quality/validation.py`
- `libs/data_quality/manifest.py`
- `libs/data_quality/expectations/` (great_expectations configs)
- `tests/libs/data_quality/test_validation.py`
- `docs/CONCEPTS/data-quality.md`
- `docs/ADRs/ADR-XXX-data-quality-framework.md`

---

### T1.2: WRDS Connection & Bulk Sync Manager
**Effort:** 3-4 days | **PR:** `feat(p4): wrds sync manager`
**Status:** ✅ Complete (P4T1)
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
- Operational runbook for lock recovery procedures

**Implementation:**
```python
# libs/data_providers/wrds_sync_manager.py
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
```

**Files to Create:**
- `libs/data_providers/wrds_client.py`
- `libs/data_providers/wrds_sync_manager.py`
- `scripts/wrds_sync.py` (CLI)
- `tests/libs/data_providers/test_wrds_sync.py`
- `tests/libs/data_providers/test_lock_contention.py`
- `docs/CONCEPTS/wrds-data.md`
- `docs/ADRs/ADR-XXX-local-data-warehouse.md`
- `docs/RUNBOOKS/wrds-lock-recovery.md`

---

### T1.3: CRSP Local Provider
**Effort:** 3-4 days | **PR:** `feat(p4): crsp local provider`
**Status:** ✅ Complete (P4T1)
**Dependencies:** T1.2

**Deliverables:**
- CRSP bulk download SQL queries
- Local Parquet storage with yearly partitioning
- DuckDB-based query provider (read-only)
- Point-in-time universe construction
- Survivorship-bias-free data access

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

---

### T1.4: Compustat Local Provider
**Effort:** 3-4 days | **PR:** `feat(p4): compustat local provider`
**Status:** ✅ Complete (P4T1)
**Dependencies:** T1.2

**Deliverables:**
- Compustat bulk download SQL queries (annual + quarterly)
- Local Parquet storage
- Point-in-time fundamentals access (lag_months parameter)
- GVKEY-to-ticker mapping

**Key Feature:** Point-in-time data handling to prevent look-ahead bias.

**Files to Create:**
- `libs/data_providers/compustat_local_provider.py`
- `tests/libs/data_providers/test_compustat_local_provider.py`
- `docs/CONCEPTS/fundamental-data.md`

---

### T1.5: Fama-French Local Provider
**Effort:** 2-3 days | **PR:** `feat(p4): fama-french local provider`
**Status:** ✅ Complete (P4T1)
**Dependencies:** T1.2

**Deliverables:**
- Bulk download from Ken French website (pandas-datareader)
- Local Parquet storage for all factor datasets
- 3-factor, 5-factor, 6-factor (momentum) models
- Industry portfolio returns (10, 30, 49 industries)

**Files to Create:**
- `libs/data_providers/fama_french_local_provider.py`
- `scripts/fama_french_sync.py` (CLI)
- `tests/libs/data_providers/test_fama_french_local_provider.py`
- `docs/CONCEPTS/fama-french-factors.md`

---

### T1.6: Dataset Versioning & Reproducibility
**Effort:** 3-4 days | **PR:** `feat(p4): dataset versioning`
**Status:** ✅ Complete (P4T1)
**Priority:** P1 (Critical for research reproducibility)
**Dependencies:** T1.1

**Problem:** Incremental updates can make backtests irreproducible if data corrections occur upstream.

**Deliverables:**
- Dataset snapshots with Git-like versioning
- Time-travel queries ("give me data as of 2024-01-15")
- Manifest files linking backtest results to data versions
- Optional: Integration with DuckDB time-travel or Iceberg tables

**Implementation:**
```python
# libs/data_quality/versioning.py
class DatasetVersionManager:
    """
    Manages dataset versions for reproducibility.
    """

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

---

### T1.7: TAQ Data Storage & Sync
**Effort:** 3-4 days | **PR:** `feat(P4T2): TAQ Storage and Query Implementation`
**Status:** ✅ Complete
**Dependencies:** T1.2

**Note:** Split from original TAQ Pipeline task. This handles storage only.

**Deliverables:**
- Tiered TAQ data storage architecture
- Pre-computed aggregates sync (Tier 1)
- Sample dataset management (Tier 3)
- Storage schema and partitioning

**Tiered Strategy:**
```
TIER 1: PRE-COMPUTED AGGREGATES (Local, ~100GB for 500 stocks, 5 years)
├── 1-minute OHLCV bars
├── Daily realized volatility
└── Intraday spread/volume statistics

TIER 3: SAMPLE DATASETS (Local, ~5-10GB per sample day)
├── Full tick data for representative days
└── Used for algorithm development
```

**Files to Create:**
- `libs/data_providers/taq_storage.py`
- `scripts/taq_sync.py` (CLI)
- `tests/libs/data_providers/test_taq_storage.py`
- `docs/CONCEPTS/taq-data.md`

---

### T1.8: TAQ Query Interface
**Effort:** 2-3 days | **PR:** `feat(P4T2): TAQ Storage and Query Implementation`
**Status:** ✅ Complete (combined with T1.7)
**Dependencies:** T1.7

**Deliverables:**
- On-demand query interface for WRDS TAQ (Tier 2)
- Local query provider for stored data (Tier 1, 3)
- Unified API for both local and remote queries
- Query cost estimation and limits

**Tier 2 Strategy:**
```
TIER 2: ON-DEMAND QUERIES (WRDS Direct, no local storage)
├── Full tick data for specific symbol + date
├── Query cost estimation before execution
└── Used for execution analysis deep-dives
```

**Files to Create:**
- `libs/data_providers/taq_query_provider.py`
- `scripts/taq_query.py` (CLI for on-demand)
- `tests/libs/data_providers/test_taq_query_provider.py`
- `docs/CONCEPTS/taq-query-interface.md`

---

### Track 1 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T1.1 Data Quality | 3-4d | Validation framework | `feat(p4): data quality framework` |
| T1.2 WRDS Sync | 3-4d | Bulk sync manager | `feat(p4): wrds sync manager` |
| T1.3 CRSP Provider | 3-4d | Local CRSP access | `feat(p4): crsp local provider` |
| T1.4 Compustat Provider | 3-4d | Local fundamentals | `feat(p4): compustat local provider` |
| T1.5 Fama-French Provider | 2-3d | Local factors | `feat(p4): fama-french local provider` |
| T1.6 Dataset Versioning | 3-4d | Reproducibility | `feat(p4): dataset versioning` |
| T1.7 TAQ Storage | 3-4d | TAQ data storage | `feat(p4): taq storage` |
| T1.8 TAQ Query | 2-3d | TAQ query interface | `feat(p4): taq query interface` |

**Total Track 1:** ~23-28 days

---

## Track 2: Factor & Risk Analytics ⭐⭐⭐ KEY FEATURES

**Goal:** Build factor models, risk analytics, and alpha research tools

**Qlib Integration Strategy:** See [P4T2_TASK.md](./P4T2_TASK.md#qlib-integration-strategy) and [qlib-comparison.md](../CONCEPTS/qlib-comparison.md) for comprehensive analysis.
- **Factor Definitions:** Static Python classes (PIT-safe); FormulaicFactor adapter deferred to Phase 3
- **Alpha Metrics:** Wrap `qlib.contrib.evaluate` (grouped IC, rank IC) with local fallback
- **Microstructure:** Polars/DuckDB only; daily aggregates exported to Qlib

---

### T2.1: Multi-Factor Model Construction
**Effort:** 4-5 days | **PR:** `feat(P4T2): Multi-Factor Model Construction`
**Status:** ✅ Complete
**Dependencies:** T1.3, T1.4

**Deliverables:**
- Factor definition registry (value, quality, momentum, size, low-vol)
- Cross-sectional factor computation
- Sector-neutralization
- Composite factor construction

**Files to Create:**
- `libs/factors/factor_builder.py`
- `libs/factors/factor_definitions.py`
- `libs/factors/factor_analytics.py`
- `tests/libs/factors/test_factor_builder.py`
- `docs/CONCEPTS/factor-investing.md`
- `docs/ADRs/ADR-XXX-factor-model-architecture.md`

---

### T2.2: Risk Model - Covariance Estimation
**Effort:** 4-5 days | **PR:** `feat(P4T2): Factor Covariance & Specific Risk Estimation`
**Status:** ✅ Complete
**Dependencies:** T2.1

**Deliverables:**
- Daily factor return calculation
- Factor covariance matrix estimation (exponential weighting)
- Stock-level factor exposure regression
- Specific risk (idiosyncratic variance) estimation

**Files to Create:**
- `libs/risk/factor_covariance.py`
- `libs/risk/specific_risk.py`
- `tests/libs/risk/test_factor_covariance.py`
- `docs/CONCEPTS/covariance-estimation.md`

---

### T2.3: Risk Model - Portfolio Analytics
**Effort:** 3-4 days | **PR:** `feat(P4T2): Portfolio Risk Analytics`
**Status:** ✅ Complete
**Dependencies:** T2.2

**Note:** Split from original Risk Optimizer task. Analytics only.

**Deliverables:**
- Portfolio risk decomposition (factor vs specific)
- Marginal risk contributions
- Risk attribution by factor
- Barra-style risk model implementation

**Files to Create:**
- `libs/risk/barra_model.py`
- `libs/risk/risk_decomposition.py`
- `tests/libs/risk/test_barra_model.py`
- `docs/CONCEPTS/risk-models.md`

---

### T2.4: Portfolio Optimizer & Stress Testing
**Effort:** 3-4 days | **PR:** `feat(P4T2): Portfolio Optimizer & Stress Testing`
**Status:** ✅ Complete
**Dependencies:** T2.3

**Deliverables:**
- Mean-variance optimization with constraints
- Transaction cost modeling
- Stress testing scenarios (historical, hypothetical)
- Optimization constraints (sector, factor, position limits)

**Files to Create:**
- `libs/risk/portfolio_optimizer.py`
- `libs/risk/stress_testing.py`
- `tests/libs/risk/test_portfolio_optimizer.py`
- `docs/ADRs/ADR-XXX-risk-model-implementation.md`

---

### T2.5: Alpha Research Framework
**Effort:** 4-5 days | **PR:** `feat(p4): alpha framework`
**Status:** ⏳ Pending
**Dependencies:** T2.1, Qlib (for metrics adapter)

**Note:** Split from original Alpha Research task. Framework + core analytics.

**Deliverables:**
- Alpha signal definition framework
- Point-in-time backtesting engine
- IC/ICIR analysis via **Qlib Analysis adapter** with local fallback
- **Grouped IC** (per sector) and **Rank IC** (more robust than Pearson)
- Alpha decay analysis

**Files to Create:**
- `libs/alpha/research_platform.py`
- `libs/alpha/alpha_library.py`
- `libs/alpha/metrics.py` - **Qlib metrics adapter**
- `tests/libs/alpha/test_research_platform.py`
- `docs/CONCEPTS/alpha-research.md`

---

### T2.6: Alpha Advanced Analytics
**Effort:** 2-3 days | **PR:** `feat(p4): alpha advanced`
**Status:** ⏳ Pending
**Dependencies:** T2.5

**Deliverables (Core):**
- Alpha combiner (composite signal construction)
- Signal correlation analysis
- Qlib turnover analysis integration

**Deliverables (STRETCH - defer if behind at Week 9):**
- Overfitting detection (out-of-sample testing)
- Multiple testing correction (Bonferroni, FDR)

**Files to Create:**
- `libs/alpha/alpha_combiner.py`
- `libs/alpha/overfitting_detection.py` - **STRETCH**
- `tests/libs/alpha/test_alpha_combiner.py`
- `docs/CONCEPTS/alpha-overfitting.md` - **STRETCH**

---

### T2.7: Factor Attribution Analysis
**Effort:** 3-4 days | **PR:** `feat(p4): factor attribution`
**Status:** ⏳ Pending
**Dependencies:** T2.1, T1.5 (Fama-French provider)

**Deliverables (Core):**
- Fama-French regression (3/5/6-factor) - reuses T1.5 provider
- Rolling factor exposure tracking
- Attribution dashboard output

**Deliverables (STRETCH):**
- Conditional attribution (up/down markets)

**Files to Create:**
- `libs/analytics/attribution.py`
- `tests/libs/analytics/test_attribution.py`
- `docs/CONCEPTS/performance-attribution.md`

---

### T2.8: Model Registry Integration
**Effort:** 5-6 days | **PR:** `feat(p4): model registry`
**Status:** ⏳ Pending
**Priority:** P1 (Research-to-Production bridge)
**Dependencies:** T2.4, T2.6, T1.6 (Dataset Versioning)

**Note:** Effort increased from 3-4d to 4-5d due to schema additions.

**Problem:** No clear path to deploy trained risk models or factor definitions to production.

**Deliverables:**
- Model serialization (pickle/joblib with metadata)
- Model registry (versioned storage)
- Production model loader
- Integration with signal_service
- **NEW:** Training config capture (`config` + `config_hash`)
- **NEW:** DatasetVersionManager snapshot linkage
- **NEW:** `feature_formulas` placeholder (Phase 3)

**Files to Create:**
- `libs/models/registry.py`
- `libs/models/serialization.py`
- `libs/models/loader.py` - Production model loader
- `tests/libs/models/test_registry.py`
- `docs/CONCEPTS/model-registry.md`
- `docs/ADRs/ADR-XXX-model-deployment.md`

---

### Track 2 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T2.1 Factor Builder | 4-5d | Factor construction | `feat(p4): factor builder` |
| T2.2 Covariance Est. | 4-5d | Risk model math | `feat(p4): factor covariance estimation` |
| T2.3 Risk Analytics | 3-4d | Portfolio analytics | `feat(p4): portfolio risk analytics` |
| T2.4 Risk Optimizer | 3-4d | Optimization | `feat(p4): portfolio optimizer` |
| T2.5 Alpha Framework | **4-5d** | Alpha + Qlib metrics | `feat(p4): alpha framework` |
| T2.6 Alpha Advanced | 2-3d | Combiner (stretch: overfitting) | `feat(p4): alpha advanced` |
| T2.7 Attribution | 3-4d | FF regression (stretch: conditional) | `feat(p4): factor attribution` |
| T2.8 Model Registry | **5-6d** | Deployment + config tracking | `feat(p4): model registry` |

**Total Track 2:** ~28-36 days (increased due to T2.5 metrics and T2.8 schema additions)

---

## Track 3: Market Microstructure ⭐⭐ MEDIUM-HIGH PRIORITY

**Goal:** Analyze price discovery, volatility, and execution quality

---

### T3.1: Microstructure Analytics
**Effort:** 4-5 days | **PR:** `feat(P4T2): Microstructure Analytics Library`
**Status:** ✅ Complete
**Dependencies:** T1.7, T1.8

**⚠️ Architectural Constraint:** All TAQ feature generation (RV, VPIN, spread, depth) implemented in **Polars/DuckDB only**. Daily aggregates exported to Qlib via Parquet. See [P4T2_TASK.md](./P4T2_TASK.md#qlib-integration-strategy).

**Deliverables:**
- Realized volatility calculation (5-min sampling)
- Intraday volatility patterns (U-shape analysis)
- HAR volatility forecasting model
- VPIN (Volume-synchronized PIN) calculation
- Spread and depth analysis
- **Multi-horizon Parquet exports** (daily, 30min, 5min)

**Files to Create:**
- `libs/analytics/microstructure.py`
- `libs/analytics/volatility.py`
- `tests/libs/analytics/test_microstructure.py`
- `tests/libs/analytics/test_volatility.py`
- `docs/CONCEPTS/microstructure.md`
- `docs/CONCEPTS/realized-volatility.md`

---

### T3.2: Execution Quality Analysis
**Effort:** 3-4 days | **PR:** `feat(p4): execution quality`
**Status:** ⏳ Pending
**Dependencies:** T3.1

**Deliverables:**
- Execution benchmarks (VWAP, TWAP, arrival price)
- Implementation shortfall calculation
- Optimal execution timing recommendations
- Integration with execution_gateway fills

**Files to Create:**
- `libs/analytics/execution_quality.py`
- `tests/libs/analytics/test_execution_quality.py`
- `docs/CONCEPTS/execution-analysis.md`

---

### T3.3: Event Study Framework
**Effort:** 3-4 days | **PR:** `feat(p4): event study framework`
**Status:** ⏳ Pending
**Dependencies:** T1.3

**Deliverables:**
- Cumulative abnormal returns (CAR) calculation
- Post-earnings announcement drift (PEAD) analysis
- Index rebalance effect analysis
- Statistical significance testing

**Files to Create:**
- `libs/analytics/event_study.py`
- `tests/libs/analytics/test_event_study.py`
- `docs/CONCEPTS/event-studies.md`

---

### Track 3 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T3.1 Microstructure | 4-5d | Volatility & VPIN | `feat(p4): microstructure analytics` |
| T3.2 Execution Quality | 3-4d | Execution analysis | `feat(p4): execution quality` |
| T3.3 Event Study | 3-4d | Event framework | `feat(p4): event study framework` |

**Total Track 3:** ~10-13 days

---

## Track 4: Historical Data Service ⭐⭐ MEDIUM PRIORITY

**Goal:** Free historical data sources for development and backtesting

---

### T4.1: yfinance Integration
**Effort:** 3-4 days | **PR:** `feat(p4): yfinance provider`
**Status:** ✅ Complete (P4T1)
**Priority:** P0 (Free data source for development)

**Important:** yfinance lacks survivorship handling and corporate actions. Gate to dev-only; production backtests must use CRSP.

**Deliverables:**
- yfinance data fetcher with rate limiting
- Local caching to Parquet
- Clear dev-only warnings in logs
- Reconciliation checks against CRSP (when available)

**Files to Create:**
- `libs/data_providers/yfinance_provider.py`
- `tests/libs/data_providers/test_yfinance_provider.py`
- `docs/CONCEPTS/yfinance-limitations.md`

---

### T4.2: Unified Data Fetcher
**Effort:** 3-4 days | **PR:** `feat(p4): unified data fetcher`
**Status:** ✅ Complete (P4T1)
**Dependencies:** T4.1, T1.3

**Deliverables:**
- Common `DataProvider` protocol/interface
- Provider factory (switch source via config)
- Automatic fallback chain
- Usage logging and metrics

**Files to Create:**
- `libs/data_providers/unified_fetcher.py`
- `libs/data_providers/protocols.py`
- `scripts/fetch_data.py` (CLI)
- `tests/libs/data_providers/test_unified_fetcher.py`
- `docs/CONCEPTS/unified-data-fetcher.md`
- `docs/ADRs/ADR-XXX-data-provider-protocol.md`

---

### T4.3: Data Storage & ETL Pipeline
**Effort:** 3-4 days | **PR:** `feat(p4): historical etl pipeline`
**Status:** ✅ Complete (P4T1)
**Dependencies:** T4.2

**Deliverables:**
- ETL orchestration for historical data
- Partitioned Parquet storage
- DuckDB catalog management
- Incremental update support

**Files to Create:**
- `libs/data_pipeline/historical_etl.py`
- `tests/libs/data_pipeline/test_historical_etl.py`
- `docs/CONCEPTS/historical-etl-pipeline.md`

---

### Track 4 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T4.1 yfinance | 3-4d | Free data source | `feat(p4): yfinance provider` |
| T4.2 Unified Fetcher | 3-4d | Common interface | `feat(p4): unified data fetcher` |
| T4.3 ETL Pipeline | 3-4d | Data orchestration | `feat(p4): historical etl pipeline` |

**Total Track 4:** ~9-12 days

---

## Track 5: Backtest Enhancement ⭐⭐ MEDIUM-HIGH PRIORITY

**Goal:** Enhanced backtesting with UI and advanced features

---

### T5.1: Backtest Job Queue Infrastructure
**Effort:** 3-4 days | **PR:** `feat(p4): backtest job queue`
**Status:** ⏳ Pending
**Priority:** P0 (Prevents UI blocking)

**Problem:** Long-running backtests block the web process and can be killed mid-run.

**CRITICAL:** Use Redis + Celery/RQ only. **DO NOT use file-based queue** (prone to race conditions, locking issues under load).

**Deliverables:**
- Redis-based job queue (Celery or RQ)
- Background worker with progress tracking
- Job prioritization and resource limits
- Idempotent job execution

**Files to Create:**
- `libs/backtest/job_queue.py`
- `libs/backtest/worker.py`
- `tests/libs/backtest/test_job_queue.py`
- `docs/ADRs/ADR-XXX-backtest-job-architecture.md`

---

### T5.2: Backtest Result Storage
**Effort:** 2-3 days | **PR:** `feat(p4): backtest result storage`
**Status:** ⏳ Pending
**Dependencies:** T5.1

**Deliverables:**
- Result storage schema (Postgres)
- Result serialization and retrieval
- Cancellation and resume support
- Result retention policy

**Files to Create:**
- `libs/backtest/result_storage.py`
- `db/migrations/xxx_backtest_results.py`
- `tests/libs/backtest/test_result_storage.py`
- `docs/CONCEPTS/backtest-result-storage.md`

---

### T5.3: Backtest Web UI
**Effort:** 4-5 days | **PR:** `feat(p4): backtest web ui`
**Status:** ⏳ Pending
**Dependencies:** T5.1, T5.2, T6.1

**Note:** Depends on T6.1 Auth since it shares the web console stack.

**Deliverables:**
- Backtest configuration form
- Job status polling
- Results visualization (equity curve, drawdown, metrics)
- Strategy comparison view

**Files to Create:**
- `apps/web_console/pages/backtest.py`
- `apps/web_console/components/backtest_form.py`
- `apps/web_console/components/backtest_results.py`
- `tests/apps/web_console/test_backtest_page.py`
- `tests/apps/web_console/test_backtest_job_status.py`
- `docs/CONCEPTS/backtest-web-ui.md`
- `docs/ADRs/ADR-XXX-backtest-ui-worker-contract.md`

---

### T5.4: Walk-Forward Optimization
**Effort:** 3-4 days | **PR:** `feat(p4): walk-forward optimization`
**Status:** ⏳ Pending
**Dependencies:** T5.1

**Deliverables:**
- Rolling train/test window framework
- Parameter optimization per window
- Out-of-sample performance aggregation
- Overfitting prevention metrics

**Files to Create:**
- `libs/backtest/walk_forward.py`
- `tests/libs/backtest/test_walk_forward.py`
- `docs/CONCEPTS/walk-forward-optimization.md`

---

### T5.5: Monte Carlo Simulation
**Effort:** 3-4 days | **PR:** `feat(p4): monte carlo simulation`
**Status:** ⏳ Pending
**Dependencies:** T5.1

**Deliverables:**
- Trade resampling (bootstrap)
- Return shuffling (path simulation)
- Confidence intervals for metrics
- Visualization of simulation results

**Files to Create:**
- `libs/backtest/monte_carlo.py`
- `tests/libs/backtest/test_monte_carlo.py`
- `docs/CONCEPTS/monte-carlo-backtesting.md`

---

### T5.6: Backtest Regression Harness
**Effort:** 2-3 days | **PR:** `feat(p4): backtest regression harness`
**Status:** ⏳ Pending
**Priority:** P1 (Prevents strategy drift)
**Dependencies:** T1.6, T5.1, T5.2

**Deliverables:**
- Golden backtest results with fixed seeds
- Automated regression tests in CI
- Alert on metric drift > threshold
- Dataset version pinning (via T1.6)

**Files to Create:**
- `tests/regression/test_backtest_golden.py`
- `tests/regression/golden_results/` (fixtures)
- `docs/CONCEPTS/backtest-regression.md`

---

### Track 5 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T5.1 Job Queue | 3-4d | Redis-based queue | `feat(p4): backtest job queue` |
| T5.2 Result Storage | 2-3d | Result persistence | `feat(p4): backtest result storage` |
| T5.3 Web UI | 4-5d | Backtest interface | `feat(p4): backtest web ui` |
| T5.4 Walk-Forward | 3-4d | Rolling optimization | `feat(p4): walk-forward optimization` |
| T5.5 Monte Carlo | 3-4d | Risk simulation | `feat(p4): monte carlo simulation` |
| T5.6 Regression Harness | 2-3d | Drift prevention | `feat(p4): backtest regression harness` |

**Total Track 5:** ~18-23 days

---

## Track 6: Web Console - Core Analytics ⭐⭐ MEDIUM-HIGH PRIORITY

**Goal:** Core trading analytics dashboards with authentication

---

### T6.1: Auth/RBAC for Analytics
**Effort:** 3-4 days | **PR:** `feat(p4): analytics auth`
**Status:** ⏳ Pending
**Priority:** P0 (Security requirement)

**Problem:** Analytics dashboards expose P&L and trade data without access controls.

**Deliverables:**
- Authentication integration (existing auth or new)
- Per-user/strategy authorization
- Audit logging for data access
- Session management

**Files to Create:**
- `apps/web_console/auth/permissions.py`
- `apps/web_console/auth/audit_log.py`
- `tests/apps/web_console/test_auth.py`
- `docs/ADRs/ADR-XXX-analytics-security.md`

---

### T6.2: Performance Dashboard
**Effort:** 3-4 days | **PR:** `feat(p4): performance dashboard`
**Status:** ⏳ Pending
**Dependencies:** T6.1

**Deliverables:**
- Real-time P&L display
- Historical performance charts
- Drawdown visualization
- Position summary

**Files to Create:**
- `apps/web_console/pages/performance.py`
- `apps/web_console/components/pnl_chart.py`
- `tests/apps/web_console/test_performance_dashboard.py`
- `docs/CONCEPTS/performance-dashboard.md`

---

### T6.3: Risk Analytics Dashboard
**Effort:** 3-4 days | **PR:** `feat(p4): risk dashboard`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T2.3

**Deliverables:**
- Factor exposure display
- VaR/CVaR visualization
- Stress test results
- Risk budget monitoring

**Files to Create:**
- `apps/web_console/pages/risk.py`
- `apps/web_console/components/risk_charts.py`
- `tests/apps/web_console/test_risk_dashboard.py`
- `docs/CONCEPTS/risk-dashboard.md`

---

### T6.4: Strategy Comparison Tool
**Effort:** 2-3 days | **PR:** `feat(p4): strategy comparison`
**Status:** ⏳ Pending
**Dependencies:** T6.2

**Deliverables:**
- Side-by-side strategy metrics
- Correlation analysis
- Rolling performance comparison
- Combined portfolio simulation

**Files to Create:**
- `apps/web_console/pages/compare.py`
- `apps/web_console/components/comparison_charts.py`
- `tests/apps/web_console/test_strategy_comparison.py`
- `docs/CONCEPTS/strategy-comparison.md`

---

### T6.5: Trade Journal & Analysis
**Effort:** 2-3 days | **PR:** `feat(p4): trade journal`
**Status:** ⏳ Pending
**Dependencies:** T6.1

**Deliverables:**
- Trade history with filtering
- Win/loss analysis
- Trade tagging and notes
- Export functionality

**Files to Create:**
- `apps/web_console/pages/journal.py`
- `apps/web_console/components/trade_table.py`
- `tests/apps/web_console/test_trade_journal.py`
- `docs/CONCEPTS/trade-journal.md`

---

### T6.6: Manual Trade Controls
**Effort:** 3-4 days | **PR:** `feat(p4): manual trade controls`
**Status:** ⏳ Pending
**Priority:** P0 (Critical operational safety)
**Dependencies:** T6.1

**Problem:** No UI to manually cancel orders, close positions, or adjust holdings. Operators must use CLI or API directly, risking errors under pressure.

**Deliverables:**
- Cancel single order / cancel all orders for symbol
- Close single position / flatten all positions
- Force position adjustment with confirmation dialog
- Audit logging for all manual interventions
- Two-factor confirmation for destructive actions

**Implementation:**
```python
# apps/web_console/pages/manual_controls.py
class ManualTradeControls:
    """Manual trading controls with safety confirmations."""

    @require_auth(role="operator")
    async def cancel_order(self, order_id: str, user: str, reason: str):
        """Cancel a specific order with audit trail."""
        await self.audit_log.record(
            action="cancel_order",
            user=user,
            order_id=order_id,
            reason=reason,
            timestamp=datetime.utcnow()
        )
        return await self.execution_client.cancel_order(order_id)

    @require_auth(role="operator")
    @require_confirmation(type="two_factor")
    async def flatten_all_positions(self, user: str, reason: str):
        """
        Close all positions immediately.
        Requires two-factor confirmation due to severity.
        """
        pass

    @require_auth(role="operator")
    async def close_position(self, symbol: str, user: str, reason: str):
        """Close a specific position."""
        pass
```

**Files to Create:**
- `apps/web_console/pages/manual_controls.py`
- `apps/web_console/components/order_cancel_dialog.py`
- `apps/web_console/components/position_close_dialog.py`
- `apps/web_console/components/confirmation_modal.py`
- `tests/apps/web_console/test_manual_controls.py`
- `docs/CONCEPTS/manual-trade-controls.md`

---

### Track 6 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T6.1 Auth/RBAC | 3-4d | Security layer | `feat(p4): analytics auth` |
| T6.2 Performance | 3-4d | P&L dashboard | `feat(p4): performance dashboard` |
| T6.3 Risk Dashboard | 3-4d | Risk analytics | `feat(p4): risk dashboard` |
| T6.4 Comparison | 2-3d | Strategy compare | `feat(p4): strategy comparison` |
| T6.5 Trade Journal | 2-3d | Trade analysis | `feat(p4): trade journal` |
| T6.6 Manual Controls | 3-4d | Trade controls | `feat(p4): manual trade controls` |

**Total Track 6:** ~17-21 days

---

## Track 7: Web Console - Operations ⭐⭐ MEDIUM-HIGH PRIORITY

**Goal:** Operational monitoring and control dashboards

---

### T7.1: Circuit Breaker Dashboard
**Effort:** 2-3 days | **PR:** `feat(p4): circuit breaker dashboard`
**Status:** ⏳ Pending
**Priority:** P0 (Operational safety)
**Dependencies:** T6.1

**Problem:** Circuit breaker state is only visible via CLI/Redis. Operators need visual monitoring and control.

**Deliverables:**
- Real-time circuit breaker status display
- Trip/reset history with timestamps and reasons
- Manual trip/reset controls with RBAC + two-factor confirmation
- Auto-refresh with WebSocket or polling
- Persistent audit log for all manual interventions
- Rate limiting to prevent accidental rapid toggles

**Security Requirements:**
- **RBAC:** Only `operator` and `admin` roles can trip/reset
- **2FA:** Reset operations require two-factor confirmation dialog
- **Audit Trail:** All trip/reset actions logged with user, timestamp, reason
- **Rate Limiting:** Max 1 reset per minute to prevent accidental spam

**Implementation:**
```python
# apps/web_console/pages/circuit_breaker.py
class CircuitBreakerDashboard:
    """Real-time circuit breaker monitoring with safety controls."""

    def render_status(self):
        """Show current CB state with visual indicator."""
        state = redis.get("cb:state")
        return {
            "status": "OPEN" if state != b"TRIPPED" else "TRIPPED",
            "color": "green" if state != b"TRIPPED" else "red",
            "last_trip": redis.get("cb:last_trip_time"),
            "trip_reason": redis.get("cb:trip_reason"),
        }

    @require_auth(role="operator")
    @audit_log(action="circuit_breaker_trip")
    def manual_trip(self, reason: str, user: str):
        """Manual trip with audit logging."""
        pass

    @require_auth(role="operator")
    @require_confirmation(type="two_factor")
    @rate_limit(max_calls=1, period_seconds=60)
    @audit_log(action="circuit_breaker_reset")
    def manual_reset(self, reason: str, user: str):
        """Manual reset with 2FA and rate limiting."""
        pass
```

**Files to Create:**
- `apps/web_console/pages/circuit_breaker.py`
- `apps/web_console/components/cb_status_card.py`
- `apps/web_console/components/cb_history_table.py`
- `apps/web_console/components/cb_reset_confirmation.py`
- `tests/apps/web_console/test_circuit_breaker_dashboard.py`
- `tests/apps/web_console/test_cb_authorization.py`
- `docs/CONCEPTS/circuit-breaker-ui.md`

---

### T7.2: System Health Monitor
**Effort:** 2-3 days | **PR:** `feat(p4): system health monitor`
**Status:** ⏳ Pending
**Priority:** P0 (Operational visibility)
**Dependencies:** T6.1

**Problem:** No centralized view of system health. Must SSH to check service status.

**Deliverables:**
- Service status dashboard (signal_service, execution_gateway, reconciler, risk_manager)
- Redis/Postgres connectivity indicators
- Queue depths (Redis streams)
- Latency metrics (P50, P95, P99)
- Last successful operations timestamps

**Implementation:**
```python
# apps/web_console/pages/health.py
class SystemHealthDashboard:
    """Centralized system health monitoring."""

    async def check_services(self) -> dict:
        """Ping all services and return status."""
        services = {
            "signal_service": "http://localhost:8001/health",
            "execution_gateway": "http://localhost:8002/health",
            "reconciler": "http://localhost:8004/health",
            "risk_manager": "http://localhost:8005/health",
        }
        results = {}
        for name, url in services.items():
            try:
                resp = await httpx.get(url, timeout=2.0)
                results[name] = {"status": "healthy", "latency_ms": resp.elapsed.total_seconds() * 1000}
            except Exception as e:
                results[name] = {"status": "unhealthy", "error": str(e)}
        return results
```

**Files to Create:**
- `apps/web_console/pages/health.py`
- `apps/web_console/components/service_status_grid.py`
- `apps/web_console/components/latency_chart.py`
- `tests/apps/web_console/test_health_dashboard.py`
- `docs/CONCEPTS/system-health-monitoring.md`

---

### T7.5: Alert Delivery Service
**Effort:** 2-3 days | **PR:** `feat(p4): alert delivery service`
**Status:** ⏳ Pending
**Priority:** P1 (Backend for T7.3)
**Dependencies:** T6.1

**Problem:** T7.3 configures alerts but needs a backend service to deliver them.

**Deliverables:**
- Email delivery (SMTP / SendGrid)
- Slack webhook integration
- SMS delivery (Twilio)
- Delivery retry with exponential backoff
- Delivery status tracking

**Implementation:**
```python
# libs/alerts/delivery_service.py
class AlertDeliveryService:
    """Multi-channel alert delivery."""

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Send email alert with retry."""
        pass

    async def send_slack(self, webhook_url: str, message: dict) -> bool:
        """Send Slack notification."""
        pass

    async def send_sms(self, phone: str, message: str) -> bool:
        """Send SMS via Twilio."""
        pass
```

**Files to Create:**
- `libs/alerts/delivery_service.py`
- `libs/alerts/alert_manager.py`
- `tests/libs/alerts/test_delivery_service.py`
- `tests/libs/alerts/test_alert_manager.py`
- `docs/CONCEPTS/alert-delivery.md`
- `docs/ADRs/ADR-XXX-alerting-system.md`

---

### T7.3: Alert Configuration UI
**Effort:** 2-3 days | **PR:** `feat(p4): alert configuration`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T7.5

**Deliverables:**
- Threshold configuration (drawdown limits, position limits, latency thresholds)
- Notification channel setup (email, Slack webhook, SMS via Twilio)
- Alert rules editor (condition → action)
- Alert history and acknowledgment tracking

**Files to Create:**
- `apps/web_console/pages/alerts.py`
- `apps/web_console/components/alert_rule_editor.py`
- `tests/apps/web_console/test_alert_configuration.py`
- `docs/CONCEPTS/alerting.md`

---

### T7.4: Admin Dashboard
**Effort:** 3-4 days | **PR:** `feat(p4): admin dashboard`
**Status:** ⏳ Pending
**Priority:** P1 (User management)
**Dependencies:** T6.1

**Problem:** No UI for user administration, permission management, or platform configuration.

**Deliverables:**
- User management (create, update, disable accounts)
- Role and permission assignment
- API key management
- System configuration (trading hours, limits, defaults)
- Audit log viewer

**Implementation:**
```python
# apps/web_console/pages/admin.py
class AdminDashboard:
    """Platform administration interface."""

    @require_auth(role="admin")
    async def list_users(self) -> list[dict]:
        """List all platform users with roles."""
        pass

    @require_auth(role="admin")
    async def update_user_role(self, user_id: str, role: str):
        """Update user's role with audit logging."""
        pass

    @require_auth(role="admin")
    async def update_system_config(self, config: dict):
        """Update platform configuration."""
        pass
```

**Files to Create:**
- `apps/web_console/pages/admin.py`
- `apps/web_console/components/user_table.py`
- `apps/web_console/components/role_editor.py`
- `apps/web_console/components/config_editor.py`
- `tests/apps/web_console/test_admin.py`
- `docs/CONCEPTS/platform-administration.md`

---

### Track 7 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T7.1 Circuit Breaker | 2-3d | CB monitoring | `feat(p4): circuit breaker dashboard` |
| T7.2 System Health | 2-3d | Service status | `feat(p4): system health monitor` |
| T7.5 Alert Delivery | 2-3d | Alert backend | `feat(p4): alert delivery service` |
| T7.3 Alert Config | 2-3d | Alert management | `feat(p4): alert configuration` |
| T7.4 Admin Dashboard | 3-4d | User/config admin | `feat(p4): admin dashboard` |

**Total Track 7:** ~12-16 days

---

## Track 8: Web Console - Data Management ⭐⭐ MEDIUM PRIORITY

**Goal:** Data visibility and management dashboards

---

### T8.1: Data Sync Dashboard
**Effort:** 2-3 days | **PR:** `feat(p4): data sync dashboard`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T1.2

**Problem:** No visibility into WRDS/yfinance sync status without checking logs.

**Deliverables:**
- Sync job status (running, completed, failed)
- Last sync timestamps per dataset
- Sync schedule configuration
- Manual sync trigger (with confirmation)
- Sync logs viewer

**Files to Create:**
- `apps/web_console/pages/data_sync.py`
- `apps/web_console/components/sync_status_table.py`
- `apps/web_console/components/sync_logs_viewer.py`
- `tests/apps/web_console/test_data_sync_dashboard.py`
- `docs/CONCEPTS/data-sync-operations.md`

---

### T8.2: Dataset Explorer
**Effort:** 3-4 days | **PR:** `feat(p4): dataset explorer`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T1.3

**Deliverables:**
- Browse available datasets (CRSP, Compustat, Fama-French, TAQ)
- Data preview (first N rows, schema)
- Basic SQL query interface (read-only)
- Data coverage visualization (date ranges, symbol counts)
- Export to CSV/Parquet

**Files to Create:**
- `apps/web_console/pages/data_explorer.py`
- `apps/web_console/components/dataset_browser.py`
- `apps/web_console/components/data_preview.py`
- `apps/web_console/components/query_editor.py`
- `tests/apps/web_console/test_dataset_explorer.py`
- `docs/CONCEPTS/dataset-explorer.md`

---

### T8.3: Data Quality Reports
**Effort:** 2-3 days | **PR:** `feat(p4): data quality reports`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T1.1

**Deliverables:**
- Validation results dashboard
- Anomaly alerts (null spikes, row count drops)
- Coverage gap visualization
- Historical quality metrics trends

**Files to Create:**
- `apps/web_console/pages/data_quality.py`
- `apps/web_console/components/validation_results.py`
- `apps/web_console/components/coverage_chart.py`
- `tests/apps/web_console/test_data_quality_reports.py`
- `docs/CONCEPTS/data-quality-monitoring.md`

---

### Track 8 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T8.1 Data Sync | 2-3d | Sync dashboard | `feat(p4): data sync dashboard` |
| T8.2 Dataset Explorer | 3-4d | Data browser | `feat(p4): dataset explorer` |
| T8.3 Data Quality | 2-3d | Quality reports | `feat(p4): data quality reports` |

**Total Track 8:** ~7-10 days

---

## Track 9: Web Console - Research & Reporting ⭐⭐ MEDIUM PRIORITY

**Goal:** Research tools and reporting features

---

### T9.1: Alpha Signal Explorer
**Effort:** 3-4 days | **PR:** `feat(p4): alpha signal explorer`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T2.8

**Deliverables:**
- Browse registered alpha signals
- IC (Information Coefficient) visualization
- Decay curve analysis
- Signal correlation matrix
- Backtest quick-launch for selected signals

**Files to Create:**
- `apps/web_console/pages/alpha_explorer.py`
- `apps/web_console/components/ic_chart.py`
- `apps/web_console/components/decay_curve.py`
- `apps/web_console/components/signal_correlation_matrix.py`
- `tests/apps/web_console/test_alpha_explorer.py`
- `docs/CONCEPTS/alpha-signal-explorer.md`

---

### T9.2: Factor Exposure Heatmap
**Effort:** 2-3 days | **PR:** `feat(p4): factor heatmap`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T2.1

**Deliverables:**
- Interactive factor exposure heatmap
- Portfolio vs benchmark comparison
- Time-series exposure evolution
- Drill-down to stock-level exposures

**Files to Create:**
- `apps/web_console/pages/factor_heatmap.py`
- `apps/web_console/components/heatmap_chart.py`
- `apps/web_console/components/exposure_timeseries.py`
- `tests/apps/web_console/test_factor_heatmap.py`
- `docs/CONCEPTS/factor-exposure-visualization.md`

---

### T9.3: Research Notebook Launcher
**Effort:** 2-3 days | **PR:** `feat(p4): notebook launcher`
**Status:** ⏳ Pending
**Dependencies:** T6.1

**Deliverables:**
- One-click Jupyter notebook launch
- Pre-configured environment (PYTHONPATH, data paths)
- Template notebooks for common analyses
- Session management (start/stop)

**Files to Create:**
- `apps/web_console/pages/notebooks.py`
- `notebooks/templates/` (analysis templates)
- `scripts/launch_notebook.py`
- `tests/apps/web_console/test_notebook_launcher.py`
- `docs/CONCEPTS/notebook-launcher.md`

---

### T9.4: Scheduled Reports
**Effort:** 3-4 days | **PR:** `feat(p4): scheduled reports`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T6.2

**Deliverables:**
- Report template configuration
- Schedule management (daily, weekly, monthly)
- PDF/HTML generation
- Email distribution
- Report archive and history

**Files to Create:**
- `apps/web_console/pages/reports.py`
- `libs/reporting/report_generator.py`
- `libs/reporting/templates/` (Jinja2 templates)
- `tests/libs/reporting/test_report_generator.py`
- `tests/libs/reporting/golden_reports/` (golden PDF fixtures)
- `tests/apps/web_console/test_scheduled_reports.py`
- `docs/CONCEPTS/reporting.md`

---

### T9.5: Tax Lot Reporter - Core
**Effort:** 2-3 days | **PR:** `feat(p4): tax lot core`
**Status:** ⏳ Pending
**Dependencies:** T6.1, T6.5

**Note:** Split from original Tax Lot task. Core FIFO/LIFO functionality.

**Deliverables:**
- Cost basis tracking (FIFO, LIFO, specific ID)
- Realized gains/losses report
- Year-end tax summary
- Export for tax software (TurboTax, etc.)

**Files to Create:**
- `apps/web_console/pages/tax_lots.py`
- `libs/tax/cost_basis.py`
- `tests/libs/tax/test_cost_basis.py`
- `tests/apps/web_console/test_tax_lots_ui.py`
- `docs/CONCEPTS/tax-lot-accounting.md`

---

### T9.6: Tax Lot Reporter - Advanced
**Effort:** 2-3 days | **PR:** `feat(p4): tax lot advanced`
**Status:** ⏳ Pending
**Dependencies:** T9.5

**Note:** Advanced tax features requiring T9.5 foundation.

**Deliverables:**
- Wash sale rule detection
- Wash sale adjustment calculations
- Disallowed loss tracking
- IRS compliance reporting
- Tax-loss harvesting recommendations

**Files to Create:**
- `libs/tax/wash_sale_detector.py`
- `libs/tax/tax_loss_harvesting.py`
- `tests/libs/tax/test_wash_sale.py`
- `docs/ADRs/ADR-XXX-tax-lot-tracking.md`

---

### Track 9 Summary

| Task | Effort | Deliverable | PR |
|------|--------|-------------|-----|
| T9.1 Alpha Explorer | 3-4d | Signal browser | `feat(p4): alpha signal explorer` |
| T9.2 Factor Heatmap | 2-3d | Exposure viz | `feat(p4): factor heatmap` |
| T9.3 Notebook Launcher | 2-3d | Jupyter launch | `feat(p4): notebook launcher` |
| T9.4 Scheduled Reports | 3-4d | Report generation | `feat(p4): scheduled reports` |
| T9.5 Tax Lot Core | 2-3d | Cost basis | `feat(p4): tax lot core` |
| T9.6 Tax Lot Advanced | 2-3d | Wash sales | `feat(p4): tax lot advanced` |

**Total Track 9:** ~14-19 days

---

## Execution Timeline

### Phase 1: Foundation (Weeks 1-4)

**Parallel Tracks:**
- Track 1 (Data Infrastructure): T1.1 → T1.2 → T1.3, T1.4, T1.5 (parallel) → T1.6
- Track 4 (Historical Data): T4.1 → T4.2 → T4.3

| Week | Track 1 | Track 4 |
|------|---------|---------|
| 1 | T1.1 Data Quality | T4.1 yfinance |
| 2 | T1.2 WRDS Sync | T4.2 Unified Fetcher |
| 3 | T1.3 CRSP, T1.4 Compustat | T4.3 ETL Pipeline |
| 4 | T1.5 Fama-French, T1.6 Versioning | (buffer/integration) |

**Milestone:** Local data warehouse operational

### Phase 2: Analytics (Weeks 5-9)

**Parallel Tracks:**
- Track 1 (Data - TAQ completion): T1.7 → T1.8
- Track 2 (Factor & Risk): T2.1 → T2.2 → T2.3 → T2.4 → T2.5 → T2.6 → T2.7 → T2.8
- Track 3 (Microstructure): T3.1 → T3.2 → T3.3

| Week | Track 2 | Tracks 1/3 (TAQ + Microstructure) |
|------|---------|-----------------------------------|
| 5 | T2.1 Factor Builder | T1.7 TAQ Storage, T1.8 TAQ Query |
| 6 | T2.2 Covariance | T3.1 Microstructure |
| 7 | T2.3 Risk Analytics, T2.4 Optimizer | T3.2 Execution Quality |
| 8 | T2.5 Alpha Framework, T2.6 Alpha Advanced | T3.3 Event Study |
| 9 | T2.7 Attribution, T2.8 Model Registry | (buffer/integration) |

**Milestone:** Factor models and risk analytics ready

### Phase 3: Backtest & Core UI (Weeks 10-13)

**Parallel Tracks:**
- Track 5 (Backtest): T5.1 → T5.2 → T5.3 → T5.4 → T5.5 → T5.6
- Track 6 (Web Console - Core): T6.1 → T6.2 → T6.3 → T6.4 → T6.5 → T6.6

| Week | Track 5 | Track 6 |
|------|---------|---------|
| 10 | T5.1 Job Queue | T6.1 Auth/RBAC |
| 11 | T5.2 Result Storage, T5.3 Web UI | T6.2 Performance, T6.3 Risk Dashboard |
| 12 | T5.4 Walk-Forward, T5.5 Monte Carlo | T6.4 Comparison, T6.5 Trade Journal |
| 13 | T5.6 Regression | T6.6 Manual Controls |

**Milestone:** Backtest and core analytics complete

### Phase 4: Operations & Data UI (Weeks 14-16)

**Parallel Tracks:**
- Track 7 (Web Console - Operations): T7.1 → T7.2 → T7.5 → T7.3 → T7.4
- Track 8 (Web Console - Data Management): T8.1 → T8.2 → T8.3

| Week | Track 7 (Operations) | Track 8 (Data) |
|------|---------------------|----------------|
| 14 | T7.1 Circuit Breaker, T7.2 System Health | T8.1 Data Sync Dashboard |
| 15 | T7.5 Alert Delivery, T7.3 Alert Config | T8.2 Dataset Explorer |
| 16 | T7.4 Admin Dashboard | T8.3 Data Quality |

**Milestone:** Operations and data dashboards complete

### Phase 5: Research & Reporting (Weeks 17-19)

**Parallel Tracks:**
- Track 9 (Web Console - Research & Reporting): T9.1 → T9.2 → T9.3 → T9.4 → T9.5 → T9.6

| Week | Track 9 (Research) | Track 9 (Reporting) |
|------|-------------------|---------------------|
| 17 | T9.1 Alpha Signal Explorer | T9.4 Scheduled Reports |
| 18 | T9.2 Factor Heatmap, T9.3 Notebook Launcher | T9.5 Tax Lot Core |
| 19 | (integration testing) | T9.6 Tax Lot Advanced |

**Milestone:** P4 complete

---

## Success Metrics

| Metric | Target | Status |
|--------|--------|--------|
| **Data Quality** | 0 silent data corruption incidents | 🔴 |
| **Factor models** | 5+ (value, quality, momentum, size, low-vol) | 🔴 |
| **Alpha IC** | >0.03 for composite alpha | 🔴 |
| **Risk model R²** | >60% variance explained | 🔴 |
| **Backtest reproducibility** | 100% with versioned datasets | 🔴 |
| **Analytics auth** | 100% endpoints protected | 🔴 |
| **Test coverage** | >85% | 🔴 |
| **PR size** | All tasks < 5 days effort | 🔴 |

---

## What's Deferred to P5 (Advanced Strategies)

The following strategy implementations will be built in P5 using the P4 research infrastructure:

| Strategy | Description | P4 Dependency |
|----------|-------------|---------------|
| **Multi-Factor Strategy** | Trades composite factor portfolio | T2.1 Factor Builder |
| **Factor Timing** | Dynamic factor allocation | T2.5 Attribution |
| **Event-Driven** | Earnings/M&A announcements | T3.3 Event Study |
| **Microstructure Alpha** | Order flow signals | T3.1 Microstructure |
| **Optimized Allocation** | Risk-aware multi-strategy | T2.3 Risk Model |

---

## Known Limitations & Tech Debt
All P4 tech-debt items TD-001 through TD-005 were addressed on 2025-12-09. No outstanding items remain for this phase.

## Related Documents

- [P3_PLANNING.md](./P3_PLANNING.md) - Previous phase (Issue Remediation)
- [P2_PLANNING.md](./P2_PLANNING.md) - Advanced Features (Complete)
- [P1_PLANNING.md](./P1_PLANNING.md) - Production Hardening (Complete)
- [docs/CONCEPTS/](../CONCEPTS/) - Trading concepts documentation
- [docs/ADRs/](../ADRs/) - Architecture Decision Records

---

**Last Updated:** 2025-12-09
**Status:** In Progress (P4T2 Complete, Track 1 ✅, Track 2 ✅, Track 3 ✅)
**Next Review:** PR Review for P4T2

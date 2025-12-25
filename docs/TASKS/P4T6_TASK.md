# P4T6: Web Console - Data Management

**Track:** 8 (Web Console - Data Management)
**Effort:** 10-13 days total (re-estimated after review)
**Status:** Planning
**Branch:** `feature/P4T6-web-console-data-management`
**Created:** 2025-12-24
**Review Status:** ✅ APPROVED (Gemini iteration 3 + Codex iteration 19)

---

## Executive Summary

P4T6 implements Track 8 of P4 Planning: Web Console Data Management dashboards. This task provides visibility into the data infrastructure created in P4T1, including WRDS/yfinance sync status, dataset exploration, and data quality monitoring.

**Key Deliverables (aligned with P4 Planning Track 8):**
1. **Data Sync Dashboard (T8.1)** - Sync status, logs, schedule config, manual controls
2. **Dataset Explorer (T8.2)** - Browse datasets with preview, query, and export
3. **Data Quality Reports (T8.3)** - Validation results, anomaly alerts, coverage gaps

**Scope Alignment Note:** This task includes ONLY features specified in P4_PLANNING.md Track 8. Extended features (lock management UI, disk gauge, quarantine CRUD, schema histograms) are explicitly deferred to future tasks.

**Scope Extension Justification (7-10 → 10-13 days):**
The increased effort from P4 Planning is justified by:
1. **Dataset-level RBAC** (+1 day) - Required for WRDS/Compustat licensing compliance (not optional)
2. **Server-side rate limiting** (+0.5 day) - Security requirement to prevent abuse
3. **Alert acknowledgment persistence** (+0.5 day) - Required for operational workflow (alerts without ack tracking are not actionable)
4. **Sync schedule persistence** (+0.5 day) - Required for schedule configuration to persist across restarts
5. **Robust SQL validation** (+0.5 day) - Security hardening for query interface

These are not scope creep but necessary implementation details that P4 Planning's high-level estimate did not account for.

---

## Dependencies Analysis

### Completed Dependencies

| Dependency | Task | Status | Description |
|------------|------|--------|-------------|
| T6.1 Auth | P4T5 | ✅ Complete | OAuth2 auth with session cookies, RBAC |
| T1.1 Data Quality | P4T1 | ✅ Complete | Validation framework, anomaly detection |
| T1.2 WRDS Sync | P4T1 | ✅ Complete | Sync manager with manifest tracking |
| T1.3 CRSP Provider | P4T1 | ✅ Complete | DuckDB-based local provider |

### Available Infrastructure

**From libs/data_quality/:**
- `SyncManifest` - Sync metadata (dataset, dates, row_count, checksum, validation_status)
- `ManifestManager` - Atomic manifest operations with locking
- `DataValidator` - Validation rules, anomaly detection
- `AnomalyAlert` - Structured anomaly alerts
- `DiskSpaceStatus` - Disk usage with watermarked levels (available but UI deferred)

**From libs/data_providers/:**
- `SyncManager` - Full/incremental sync orchestration
- `SyncProgress` - Progress checkpointing for crash recovery
- `CRSPLocalProvider` - Read-only DuckDB queries
- `LockToken` - Lock metadata (pid, hostname, acquired_at, expires_at)

**From web console auth:**
- `@require_auth` decorator
- `has_permission()` RBAC checks
- Audit logging infrastructure

**Quarantine Storage (from P4T1):**
- Quarantine data stored at `data/quarantine/{dataset}/{timestamp}/`
- Manifest includes `quarantine_path` field for failed syncs
- ManifestManager provides `quarantine_data()` and list operations
- **T8.3 scope:** Read-only view of quarantine status (no restore/delete CRUD)

---

## API/Service Contract Definitions

### T8.1: Data Sync Service API

```python
# apps/web_console/services/data_sync_service.py

class DataSyncService:
    """
    Service layer for data sync operations.
    Enforces RBAC, dataset-level access, and rate limiting at server-side.

    IMPORTANT: ALL read paths filter by user's dataset permissions.
    Users only see sync status/logs/schedules for datasets they have access to.
    """

    async def get_sync_status(self, user: AuthenticatedUser) -> list[SyncStatusDTO]:
        """
        Get sync status for datasets user has access to.
        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Returns: List of SyncStatusDTO with dataset, last_sync, row_count, status
        Filtering: Only datasets matching user's DatasetPermission set
        """

    async def get_sync_logs(
        self, user: AuthenticatedUser, dataset: str | None, level: str | None, limit: int = 100
    ) -> list[SyncLogEntry]:
        """
        Get recent sync log entries with optional filters.
        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Rate limit: N/A (read-only)
        Filtering: If dataset specified, validate access; otherwise filter to accessible datasets
        """

    async def get_sync_schedule(self, user: AuthenticatedUser) -> list[SyncScheduleDTO]:
        """
        Get sync schedule configuration for accessible datasets.
        Permission: VIEW_DATA_SYNC + dataset-level access (filtered)
        Filtering: Only schedules for datasets user has access to
        """

    async def update_sync_schedule(
        self, user: AuthenticatedUser, dataset: str, schedule: SyncScheduleUpdateDTO
    ) -> SyncScheduleDTO:
        """
        Update sync schedule (cron expression, enabled) for a specific dataset.
        Permission: MANAGE_SYNC_SCHEDULE + dataset-level access for specified dataset
        Security: Validate user has access to the dataset being updated (licensing compliance)
        Audit: Logged with user, dataset, old/new values
        """

    async def trigger_sync(
        self, user: AuthenticatedUser, dataset: str, reason: str
    ) -> SyncJobDTO:
        """
        Trigger manual incremental sync.
        Permission: TRIGGER_DATA_SYNC + dataset-level access for specified dataset
        Security: Validate user has access to the dataset being synced (licensing compliance)
        Rate limit: 1/minute global (server-side enforced)
        Audit: Logged with user, dataset, reason
        """
```

### T8.2: Data Explorer Service API

```python
# apps/web_console/services/data_explorer_service.py

class DataExplorerService:
    """
    Service layer for dataset exploration.
    Enforces query validation, RBAC, and rate limiting.
    """

    async def list_datasets(self, user: AuthenticatedUser) -> list[DatasetInfoDTO]:
        """
        List available datasets with metadata.
        Permission: VIEW_DATA_SYNC (basic access)
        Dataset-level access: Filtered by user's dataset permissions
        """

    async def get_dataset_preview(
        self, user: AuthenticatedUser, dataset: str, limit: int = 100
    ) -> DataPreviewDTO:
        """
        Get first N rows of dataset.
        Permission: QUERY_DATA + dataset-level access
        Limit: Max 1000 rows
        """

    async def execute_query(
        self, user: AuthenticatedUser, dataset: str, query: str, timeout_seconds: int = 30
    ) -> QueryResultDTO:
        """
        Execute read-only SQL query against a SINGLE dataset.
        Permission: QUERY_DATA + dataset-level access for specified dataset
        Rate limit: 10 queries/minute per user (server-side)
        Security: Query validation + table reference validation (see SQL Security section)
        Streaming: Results paginated, max 10,000 rows per page
        Audit: Logged with user, dataset, query_fingerprint, row_count, duration

        CRITICAL: Query is scoped to specified dataset only.
        Cross-dataset queries are rejected at validation time.
        """

    async def export_data(
        self, user: AuthenticatedUser, dataset: str, query: str, format: Literal["csv", "parquet"]
    ) -> ExportJobDTO:
        """
        Export query results to file from a SINGLE dataset.
        Permission: EXPORT_DATA + dataset-level access for specified dataset
        Rate limit: 5 exports/hour per user (server-side)
        Limit: Max 100,000 rows
        Audit: Logged with user, dataset, query_fingerprint, row_count, format
        Storage: Temp directory with 24-hour TTL, auto-cleanup via cron job
        """
```

### T8.3: Data Quality Service API

```python
# apps/web_console/services/data_quality_service.py

class DataQualityService:
    """
    Service layer for data quality reporting.
    Enforces dataset-level access on all read paths for licensing compliance.
    Alert acknowledgments stored in PostgreSQL.

    IMPORTANT: ALL read paths filter by user's dataset permissions.
    Users only see quality data for datasets they have access to.
    """

    async def get_validation_results(
        self, user: AuthenticatedUser, dataset: str | None, limit: int = 50
    ) -> list[ValidationResultDTO]:
        """
        Get recent validation run results.
        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: If dataset specified, validate access; otherwise filter to accessible datasets
        """

    async def get_anomaly_alerts(
        self, user: AuthenticatedUser, severity: str | None, acknowledged: bool | None
    ) -> list[AnomalyAlertDTO]:
        """
        Get anomaly alerts with optional filters.
        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: Only alerts for datasets user has access to
        """

    async def acknowledge_alert(
        self, user: AuthenticatedUser, alert_id: str, reason: str
    ) -> AlertAcknowledgmentDTO:
        """
        Acknowledge an anomaly alert (idempotent).
        Permission: ACKNOWLEDGE_ALERTS + dataset-level access for alert's dataset
        Storage: PostgreSQL alert_acknowledgments table
        Audit: Logged with user, alert_id, reason
        Security: Validate user has access to the dataset referenced by alert_id

        Idempotency: First-write-wins (unique constraint on alert_id)
        - If alert not yet acknowledged: creates acknowledgment, returns AlertAcknowledgmentDTO
        - If alert already acknowledged: returns existing AlertAcknowledgmentDTO (no error)
        - Client can safely retry without side effects
        """

    async def get_quality_trends(
        self, user: AuthenticatedUser, dataset: str, days: int = 30
    ) -> QualityTrendDTO:
        """
        Get historical quality metrics for trend visualization.
        Permission: VIEW_DATA_QUALITY + dataset-level access for specified dataset
        Security: Validate user has access to specified dataset before returning data
        """

    async def get_quarantine_status(self, user: AuthenticatedUser) -> list[QuarantineEntryDTO]:
        """
        Get list of quarantined sync attempts (read-only view).
        Permission: VIEW_DATA_QUALITY + dataset-level access (filtered)
        Filtering: Only quarantine entries for datasets user has access to
        Note: CRUD operations deferred to future task
        """
```

---

## Database Schema Additions

**Migration Naming Convention:** Uses existing numbered `.sql` format: `db/migrations/NNNN_description.sql`

**Migration Dependency Order (CRITICAL):**
1. `0035_sync_logs.sql` - No dependencies
2. `0036_sync_schedule.sql` - No dependencies
3. `0037_query_audit.sql` - No dependencies
4. `0038_validation_results.sql` - No dependencies
5. `0039_anomaly_alerts.sql` - No dependencies
6. `0040_alert_acknowledgments.sql` - **DEPENDS ON** 0039 (FK to data_anomaly_alerts)

### Anomaly Alerts Table (Migration 0039 - BEFORE acknowledgments)

```sql
-- db/migrations/0039_anomaly_alerts.sql
-- MUST run before 0040_alert_acknowledgments.sql due to FK dependency

CREATE TABLE data_anomaly_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL,
    metric VARCHAR(100) NOT NULL,            -- row_drop, null_spike, date_gap
    severity VARCHAR(20) NOT NULL,           -- error, warning
    current_value DOUBLE PRECISION NOT NULL,
    expected_value DOUBLE PRECISION NOT NULL,
    deviation_pct DOUBLE PRECISION NOT NULL,
    message TEXT NOT NULL,
    sync_run_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_anomaly_alerts_dataset ON data_anomaly_alerts(dataset);
CREATE INDEX idx_anomaly_alerts_created ON data_anomaly_alerts(created_at DESC);
CREATE INDEX idx_anomaly_alerts_severity ON data_anomaly_alerts(severity);
```

### Alert Acknowledgments Table (Migration 0040 - AFTER anomaly_alerts)

```sql
-- db/migrations/0040_alert_acknowledgments.sql
-- DEPENDS ON: 0039_anomaly_alerts.sql (FK reference)

CREATE TABLE data_quality_alert_acknowledgments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES data_anomaly_alerts(id),  -- FK to anomaly alerts
    dataset VARCHAR(100) NOT NULL,
    metric VARCHAR(100) NOT NULL,           -- row_drop, null_spike, date_gap
    severity VARCHAR(20) NOT NULL,          -- error, warning
    acknowledged_by VARCHAR(255) NOT NULL,  -- User ID
    acknowledged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    reason TEXT NOT NULL,
    original_alert JSONB NOT NULL,          -- Full AnomalyAlert for audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_alert_ack_alert_id ON data_quality_alert_acknowledgments(alert_id);
CREATE INDEX idx_alert_ack_dataset ON data_quality_alert_acknowledgments(dataset);
CREATE INDEX idx_alert_ack_acknowledged_at ON data_quality_alert_acknowledgments(acknowledged_at);
```

### Sync Schedule Configuration Table

```sql
-- db/migrations/0036_sync_schedule.sql

CREATE TABLE data_sync_schedule (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT true,
    cron_expression VARCHAR(100) NOT NULL DEFAULT '0 2 * * *',  -- 2 AM daily
    last_scheduled_run TIMESTAMP WITH TIME ZONE,
    next_scheduled_run TIMESTAMP WITH TIME ZONE,
    updated_by VARCHAR(255),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    version INTEGER NOT NULL DEFAULT 1  -- For optimistic locking
);

CREATE UNIQUE INDEX idx_sync_schedule_dataset ON data_sync_schedule(dataset);

-- Optimistic locking: UPDATE ... WHERE version = expected_version
-- On conflict: return current version to client, client must retry with fresh data
```

### Sync Logs Storage Table

```sql
-- db/migrations/0035_sync_logs.sql

CREATE TABLE data_sync_logs (
    id BIGSERIAL PRIMARY KEY,
    dataset VARCHAR(100) NOT NULL,
    level VARCHAR(20) NOT NULL,        -- INFO, WARN, ERROR
    message TEXT NOT NULL,
    extra JSONB,                         -- Additional structured data
    sync_run_id UUID,                    -- Links to specific sync run
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_sync_logs_dataset_created ON data_sync_logs(dataset, created_at DESC);
CREATE INDEX idx_sync_logs_level ON data_sync_logs(level);
CREATE INDEX idx_sync_logs_created ON data_sync_logs(created_at DESC);

-- Retention: 30 days, cleaned by scheduled job
-- Query pattern: SELECT * FROM data_sync_logs WHERE dataset = ? ORDER BY created_at DESC LIMIT 100

-- Ingestion: SyncManager writes logs via PostgreSQLLogHandler
-- Handler configured in libs/data_providers/sync_manager.py during sync operations
```

### Query Audit Log Table

```sql
-- db/migrations/0037_query_audit.sql

CREATE TABLE data_query_audit (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    dataset VARCHAR(100) NOT NULL,
    query_fingerprint VARCHAR(64) NOT NULL,  -- SHA-256 of normalized query
    query_text TEXT NOT NULL,                 -- Full query (encrypted at application level)
    row_count INTEGER,
    duration_ms INTEGER NOT NULL,
    client_ip VARCHAR(45),
    result VARCHAR(20) NOT NULL,              -- success, error, timeout, rate_limited
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_query_audit_user ON data_query_audit(user_id);
CREATE INDEX idx_query_audit_dataset ON data_query_audit(dataset);
CREATE INDEX idx_query_audit_created ON data_query_audit(created_at DESC);

-- Retention: 90 days, cleaned by scheduled job
-- Encryption: query_text encrypted using Fernet symmetric encryption
--   Key source: QUERY_AUDIT_ENCRYPTION_KEY from secrets manager (libs/secrets)
--   Encrypt: fernet.encrypt(query_text.encode()) before INSERT
--   Decrypt: fernet.decrypt(query_text) on SELECT for authorized admin access
--   Key rotation: Quarterly via secrets rotation procedure (see docs/RUNBOOKS/secrets-migration.md)
```

### Validation Results Table

**Note:** Current P4T1 SyncManifest only stores `validation_status` (passed/failed/quarantined), not historical validation runs or anomaly details. T8.3 requires persistent storage for validation results and anomaly alerts.

```sql
-- db/migrations/0038_validation_results.sql

CREATE TABLE data_validation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset VARCHAR(100) NOT NULL,
    sync_run_id UUID,                        -- Links to sync operation
    validation_type VARCHAR(50) NOT NULL,    -- row_count, null_pct, schema, date_continuity
    status VARCHAR(20) NOT NULL,             -- passed, failed, warning
    expected_value TEXT,
    actual_value TEXT,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_validation_results_dataset ON data_validation_results(dataset);
CREATE INDEX idx_validation_results_created ON data_validation_results(created_at DESC);
CREATE INDEX idx_validation_results_status ON data_validation_results(status);
```

**(Anomaly Alerts table defined above in Migration 0039)**

**Data Flow for T8.3:**
```python
# 1. SyncManager runs validation via DataValidator
# 2. ValidationErrors and AnomalyAlerts are persisted to PostgreSQL tables above
# 3. DataQualityService queries these tables for dashboards
# 4. Alert acknowledgments link to data_anomaly_alerts.id
#
# Ingestion: DataValidator writes to DB via ValidationResultsRepository
# Query: DataQualityService reads from DB for T8.3 features
```

### Retention and Cleanup Jobs

```yaml
# infra/cron/data-management-cleanup.yml

jobs:
  - name: sync-logs-cleanup
    schedule: "0 3 * * *"  # Daily at 3 AM
    command: |
      psql -c "DELETE FROM data_sync_logs WHERE created_at < NOW() - INTERVAL '30 days'"
    owner: ops-cron

  - name: query-audit-cleanup
    schedule: "0 4 * * *"  # Daily at 4 AM
    command: |
      psql -c "DELETE FROM data_query_audit WHERE created_at < NOW() - INTERVAL '90 days'"
    owner: ops-cron

  - name: export-files-cleanup
    schedule: "0 */6 * * *"  # Every 6 hours
    command: |
      find /tmp/data-exports -mtime +1 -delete  # Files older than 24 hours
    owner: ops-cron
```

### Sync Schedule Integration with Sync Runner

**Cron Evaluation Ownership: Orchestrator**

```python
# apps/orchestrator/schedule_evaluator.py (new file)

from datetime import datetime, timezone
from croniter import croniter  # Cron expression parser

class SyncScheduleEvaluator:
    """
    Evaluates sync schedules and triggers sync jobs.
    Runs as part of orchestrator's main loop.

    IMPORTANT: All timestamps are timezone-aware UTC per project standards.
    """

    def evaluate_schedules(self) -> list[str]:
        """
        Check all enabled schedules and return datasets due for sync.
        Called every minute by orchestrator.
        """
        now = datetime.now(timezone.utc)  # Timezone-aware UTC
        schedules = db.query(DataSyncSchedule).filter_by(enabled=True).all()
        due_datasets = []

        for schedule in schedules:
            if schedule.next_scheduled_run and schedule.next_scheduled_run <= now:
                due_datasets.append(schedule.dataset)

        return due_datasets

    def compute_next_run(self, cron_expression: str, base_time: datetime) -> datetime:
        """
        Compute next scheduled run from cron expression.
        Uses croniter library for parsing.
        Returns timezone-aware UTC datetime.
        """
        # Ensure base_time is timezone-aware
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=timezone.utc)
        cron = croniter(cron_expression, base_time)
        next_run = cron.get_next(datetime)
        # Ensure result is timezone-aware
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        return next_run

    def update_schedule_after_run(self, dataset: str, success: bool):
        """
        Update schedule timestamps after sync completes.
        Called by orchestrator after wrds_sync.py finishes.
        All timestamps are timezone-aware UTC.

        Behavior based on success flag:
        - success=True: Update both last_scheduled_run and next_scheduled_run
        - success=False: Only update last_scheduled_run (next stays same for retry)
        """
        schedule = db.query(DataSyncSchedule).filter_by(dataset=dataset).first()
        now = datetime.now(timezone.utc)  # Timezone-aware UTC

        schedule.last_scheduled_run = now

        if success:
            # Only compute next run on success; on failure, keep same next_scheduled_run
            # so orchestrator retries on next poll (within 1 minute)
            schedule.next_scheduled_run = self.compute_next_run(
                schedule.cron_expression, now
            )

        db.commit()

# Orchestrator main loop (apps/orchestrator/main.py):
# 1. Every minute: evaluator.evaluate_schedules() → list of due datasets
# 2. For each due dataset: spawn wrds_sync.py subprocess
# 3. On completion: evaluator.update_schedule_after_run(dataset, success=True)
# 4. On failure: evaluator.update_schedule_after_run(dataset, success=False) → keeps next_scheduled_run for retry
```

**Web Console Schedule Update Flow:**
```python
# When user updates schedule via T8.1:
# 1. DataSyncService.update_sync_schedule() validates cron expression with croniter
# 2. Computes next_scheduled_run from cron expression
# 3. Increments version for optimistic locking
# 4. Persists to DB
# 5. Orchestrator picks up change on next poll (within 1 minute)
```

---

## Component Breakdown

### T8.1: Data Sync Dashboard (2-3 days)

**Purpose:** Provide visibility into WRDS/yfinance sync status without checking logs.

**Pages:**
- `apps/web_console/pages/data_sync.py`

**Components:**
- `apps/web_console/components/sync_status_table.py` - Dataset sync overview
- `apps/web_console/components/sync_logs_viewer.py` - Recent sync logs
- `apps/web_console/components/sync_schedule_editor.py` - Schedule configuration

**Services:**
- `apps/web_console/services/data_sync_service.py`

**Features (aligned with P4 Planning T8.1):**
1. **Sync Job Status** (P4 Planning: "running, completed, failed")
   - Table showing all datasets with last sync time, row count, validation status
   - Color-coded status indicators (green=passed, yellow=warning, red=failed)

2. **Last Sync Timestamps** (P4 Planning: "per dataset")
   - Per-dataset last sync time with relative time display
   - Schema version display

3. **Sync Schedule Configuration** (P4 Planning: required deliverable)
   - View/edit cron expression per dataset
   - Enable/disable scheduled syncs
   - Next scheduled run display
   - RBAC: `MANAGE_SYNC_SCHEDULE` + dataset-level access (licensing compliance)

4. **Manual Sync Trigger** (P4 Planning: "with confirmation")
   - Trigger incremental sync per dataset
   - Confirmation dialog with reason field
   - Rate limited: 1/minute global (server-side)
   - RBAC: `TRIGGER_DATA_SYNC` + dataset-level access (licensing compliance)
   - Audit logging for all manual syncs

5. **Sync Logs Viewer** (P4 Planning: required)
   - Recent sync log entries (last 100)
   - Filter by dataset, level (INFO/WARN/ERROR)
   - Full log entry expansion

**Deferred Features (not in P4 Planning T8.1):**
- ~~Lock status monitoring panel~~ → Deferred
- ~~Disk usage gauge~~ → Deferred
- ~~Force-unlock UI~~ → Deferred (CLI only: `scripts/wrds_sync.py force-unlock`)

**Prometheus Metrics:**
```python
# apps/web_console/services/data_sync_metrics.py

SYNC_TRIGGER_TOTAL = Counter(
    "data_sync_trigger_total",
    "Total manual sync triggers",
    ["dataset", "user"]
)
SYNC_STATUS_CHECKS = Counter(
    "data_sync_status_checks_total",
    "Total sync status page views"
)
SYNC_SCHEDULE_UPDATES = Counter(
    "data_sync_schedule_updates_total",
    "Total schedule configuration changes",
    ["dataset", "user"]
)
```

**Auth Requirements:**
- View: `VIEW_DATA_SYNC` + dataset-level access (filtered to accessible datasets)
- Update schedule: `MANAGE_SYNC_SCHEDULE` + dataset-level access for target dataset
- Trigger sync: `TRIGGER_DATA_SYNC` + dataset-level access for target dataset

**Tests:**
- `tests/apps/web_console/test_data_sync_dashboard.py` - Page tests
- `tests/apps/web_console/services/test_data_sync_service.py` - Service layer tests
- `tests/apps/web_console/services/test_data_sync_rate_limit.py` - Rate limiting tests

**Documentation:**
- `docs/CONCEPTS/data-sync-operations.md`
- `docs/RUNBOOKS/data-sync-ops.md` - Operational runbook

---

### T8.2: Dataset Explorer (3-4 days)

**Purpose:** Enable browsing and querying of local data warehouse.

**Pages:**
- `apps/web_console/pages/data_explorer.py`

**Components:**
- `apps/web_console/components/dataset_browser.py` - Dataset catalog view
- `apps/web_console/components/data_preview.py` - First N rows preview
- `apps/web_console/components/schema_viewer.py` - Column types (basic stats)
- `apps/web_console/components/query_editor.py` - SQL query interface
- `apps/web_console/components/export_dialog.py` - Export to CSV/Parquet
- `apps/web_console/components/coverage_timeline.py` - Date range visualization

**Services:**
- `apps/web_console/services/data_explorer_service.py`

**Features (aligned with P4 Planning T8.2):**
1. **Dataset Catalog** (P4 Planning: "Browse available datasets")
   - List available datasets (CRSP, Compustat, Fama-French, TAQ)
   - Dataset description and documentation links
   - Symbol/date coverage summary
   - **Dataset-level access control** (per user permissions)

2. **Data Preview** (P4 Planning: "first N rows, schema")
   - First N rows display (default 100, max 1000)
   - Column sorting and filtering
   - Pagination for results (streaming with cursor)

3. **Schema Viewer** (P4 Planning: "schema")
   - Column names, types, descriptions
   - Null percentage per column
   - **Note:** Value histograms deferred to future task

4. **SQL Query Interface** (P4 Planning: "Basic SQL query interface")
   - DuckDB SQL editor with syntax highlighting
   - Query execution with result limit (max 10,000 rows per page)
   - Query timeout: 30 seconds (cancellation supported)
   - **Security:** Strict query validation (see SQL Security section)

5. **Data Coverage Visualization** (P4 Planning: "date ranges, symbol counts")
   - Date range timeline per dataset
   - Symbol count over time
   - Gap detection highlighting

6. **Export Functionality** (P4 Planning: "Export to CSV/Parquet")
   - Export query results to CSV or Parquet
   - Row limit: max 100,000 rows
   - RBAC: `EXPORT_DATA` + dataset-level access
   - Rate limited: 5 exports/hour per user (server-side)

**SQL Query Security (Server-Side Enforcement - Multi-Layer Defense):**

**Primary Defense (HARD fail-safe):** DuckDB `read_only=True` connection prevents all modifications.

**Secondary Defense (Defense in Depth):** SQL validation via parser + regex.

```python
# apps/web_console/services/sql_validator.py

import sqlglot  # SQL parser for robust validation

class SQLValidator:
    """
    Validates SQL queries for safe read-only execution.
    All validation is server-side (not bypassable from UI).

    Defense layers:
    1. DuckDB read_only=True (PRIMARY - guaranteed by DB)
    2. SQL parsing via sqlglot (SECONDARY - rejects obviously bad queries)
    3. Table reference allowlist (enforces dataset-level RBAC)
    """

    # Dataset to allowed tables mapping
    DATASET_TABLES = {
        "crsp": ["crsp_daily", "crsp_monthly"],
        "compustat": ["compustat_annual", "compustat_quarterly"],
        "fama_french": ["ff_factors_daily", "ff_factors_monthly"],
        "taq": ["taq_trades", "taq_quotes"],
    }

    def validate(self, query: str, allowed_dataset: str) -> tuple[bool, str | None]:
        """
        Validate query is safe for execution against specified dataset.
        Returns: (is_valid, error_message)

        Validation steps:
        1. Parse with sqlglot to extract AST
        2. Reject if not single SELECT statement
        3. Reject if contains semicolons (multi-statement)
        4. Extract all table references
        5. Reject if any table not in allowed_dataset's allowlist
        6. Reject blocked functions (read_parquet, read_csv, etc.)
        """

    def extract_tables(self, query: str) -> list[str]:
        """
        Extract all table references from SQL query using sqlglot.
        Handles subqueries, CTEs, JOINs.
        """

    def enforce_row_limit(self, query: str, max_rows: int) -> str:
        """
        Add LIMIT clause if not present.
        Uses sqlglot to safely modify AST.
        """

    # Blocked DuckDB functions (file access, external sources)
    BLOCKED_FUNCTIONS = [
        "read_parquet", "read_csv", "read_json",
        "read_text", "glob", "list_files",
        "httpfs_*", "s3_*",
    ]
```

**Tests MUST verify:**
- `read_only=True` prevents INSERT/UPDATE/DELETE even if validator bypassed
- Multi-statement queries (with `;`) rejected
- Cross-dataset table references rejected
- Blocked functions rejected
- Valid SELECT queries succeed

**DuckDB Connection Configuration:**

```python
# apps/web_console/services/duckdb_connection.py

def get_read_only_connection() -> duckdb.DuckDBPyConnection:
    """
    Create read-only DuckDB connection with security restrictions.
    """
    conn = duckdb.connect(read_only=True)

    # Disable dangerous features
    conn.execute("SET enable_external_access = false")
    conn.execute("SET enable_fsst_vectors = false")

    return conn
```

**Dataset-Level Access Control:**

```python
# libs/web_console_auth/permissions.py

class DatasetPermission(str, Enum):
    """Per-dataset access permissions for licensing compliance."""
    CRSP_ACCESS = "dataset:crsp"
    COMPUSTAT_ACCESS = "dataset:compustat"
    TAQ_ACCESS = "dataset:taq"
    FAMA_FRENCH_ACCESS = "dataset:fama_french"  # Public, default granted

# Role mapping extension
ROLE_DATASET_PERMISSIONS = {
    Role.VIEWER: [DatasetPermission.FAMA_FRENCH_ACCESS],
    Role.ANALYST: [
        DatasetPermission.FAMA_FRENCH_ACCESS,
        DatasetPermission.CRSP_ACCESS,
    ],
    Role.OPERATOR: [
        DatasetPermission.FAMA_FRENCH_ACCESS,
        DatasetPermission.CRSP_ACCESS,
        DatasetPermission.COMPUSTAT_ACCESS,
    ],
    Role.ADMIN: list(DatasetPermission),  # All datasets
}
```

**Prometheus Metrics:**
```python
# Success metrics
QUERY_EXECUTIONS = Counter("data_query_executions_total", "Total queries executed", ["user", "dataset"])
QUERY_DURATION = Histogram("data_query_duration_seconds", "Query execution time")
EXPORT_REQUESTS = Counter("data_export_requests_total", "Total export requests", ["user", "format"])
EXPORT_ROWS = Histogram("data_export_rows", "Rows exported per request")

# Error/rejection metrics (for operational visibility)
QUERY_VALIDATION_FAILURES = Counter(
    "data_query_validation_failures_total",
    "Queries rejected by validator",
    ["reason"]  # blocked_function, cross_dataset, multi_statement, syntax_error
)
QUERY_RATE_LIMIT_REJECTIONS = Counter(
    "data_query_rate_limit_rejections_total",
    "Queries rejected due to rate limit",
    ["user"]
)
EXPORT_RATE_LIMIT_REJECTIONS = Counter(
    "data_export_rate_limit_rejections_total",
    "Exports rejected due to rate limit",
    ["user"]
)
DATASET_ACCESS_DENIALS = Counter(
    "data_dataset_access_denials_total",
    "Dataset access denied due to permissions",
    ["user", "dataset"]
)
```

**Query Execution Audit Log:**
```python
# All query executions logged for compliance
QUERY_AUDIT_LOG = {
    "user_id": str,
    "dataset": str,
    "query_fingerprint": str,  # SHA-256 of normalized query
    "query_text": str,         # Full query (for debugging, encrypted at rest)
    "row_count": int,
    "duration_ms": int,
    "timestamp": datetime,
    "client_ip": str,
    "result": "success" | "error" | "timeout" | "rate_limited",
}
# Storage: PostgreSQL data_query_audit table, 90-day retention
```

**Auth Requirements:**
- View datasets: `VIEW_DATA_SYNC` + dataset-level permission
- Execute queries: `QUERY_DATA` + dataset-level permission
- Export data: `EXPORT_DATA` + dataset-level permission

**Tests:**
- `tests/apps/web_console/test_dataset_explorer.py` - Page tests
- `tests/apps/web_console/services/test_data_explorer_service.py` - Service layer
- `tests/apps/web_console/services/test_sql_validator.py` - Query validation
- `tests/apps/web_console/services/test_query_rate_limit.py` - Rate limiting

**Documentation:**
- `docs/CONCEPTS/dataset-explorer.md`

---

### T8.3: Data Quality Reports (2-3 days)

**Purpose:** Visualize data quality metrics and anomaly alerts.

**Pages:**
- `apps/web_console/pages/data_quality.py`

**Components:**
- `apps/web_console/components/validation_results_table.py` - Validation run results
- `apps/web_console/components/anomaly_alert_feed.py` - Recent anomaly alerts
- `apps/web_console/components/coverage_chart.py` - Coverage gap visualization
- `apps/web_console/components/quality_trend_chart.py` - Historical quality metrics

**Services:**
- `apps/web_console/services/data_quality_service.py`

**Features (aligned with P4 Planning T8.3):**
1. **Validation Results Dashboard** (P4 Planning: required)
   - Table of recent validation runs per dataset
   - Pass/fail status with detailed error messages
   - Validation rule breakdown (row count, null %, schema, dates)
   - Drill-down to specific validation errors

2. **Anomaly Alerts** (P4 Planning: "null spikes, row count drops")
   - Real-time feed of `AnomalyAlert` instances from libs/data_quality
   - Filter by severity (error/warning)
   - Filter by metric type (row_drop, null_spike, date_gap)
   - Deviation percentage and expected vs actual values

3. **Alert Acknowledgment** (P4 Planning implied for alert management)
   - Mark alerts as acknowledged with reason
   - Acknowledgment stored in PostgreSQL (see schema above)
   - Filter: show acknowledged/unacknowledged
   - Audit: logged with user, alert_id, reason, timestamp

4. **Coverage Gap Visualization** (P4 Planning: required)
   - Date range coverage per dataset
   - Missing date highlighting
   - Symbol coverage over time
   - Comparison with expected trading calendar

5. **Historical Quality Trends** (P4 Planning: implied for monitoring)
   - Row count trend over syncs
   - Null percentage trends per critical column
   - Validation pass rate over time
   - Anomaly frequency chart

6. **Quarantine Status Viewer** (Read-only, P4 Planning scope)
   - List quarantined sync attempts (from ManifestManager)
   - Show quarantine reason and timestamp
   - **Note:** Restore/delete CRUD operations deferred to future task

**Deferred Features (not in P4 Planning T8.3):**
- ~~Quarantine CRUD (restore/delete)~~ → Deferred (CLI only)

**Prometheus Metrics:**
```python
ALERT_VIEWS = Counter("data_quality_alert_views_total", "Alert feed views")
ALERT_ACKNOWLEDGMENTS = Counter(
    "data_quality_alert_acks_total",
    "Alert acknowledgments",
    ["user", "metric_type"]
)
QUALITY_REPORT_VIEWS = Counter("data_quality_report_views_total", "Quality dashboard views")
```

**Auth Requirements:**
- View reports: `VIEW_DATA_QUALITY` + dataset-level access (filtered to accessible datasets)
- Acknowledge alerts: `ACKNOWLEDGE_ALERTS` + dataset-level access for alert's dataset
- View trends: `VIEW_DATA_QUALITY` + dataset-level access for specified dataset
- View quarantine: `VIEW_DATA_QUALITY` + dataset-level access (filtered to accessible datasets)

**Tests:**
- `tests/apps/web_console/test_data_quality_reports.py` - Page tests
- `tests/apps/web_console/services/test_data_quality_service.py` - Service layer
- `tests/apps/web_console/services/test_alert_acknowledgment.py` - Ack storage

**Documentation:**
- `docs/CONCEPTS/data-quality-monitoring.md`
- `docs/RUNBOOKS/data-quality-ops.md` - Operational runbook

---

## File Change Summary

### New Files

**Database Migrations (6 files, ordered for FK dependencies):**
```
db/migrations/0035_sync_logs.sql
db/migrations/0036_sync_schedule.sql
db/migrations/0037_query_audit.sql
db/migrations/0038_validation_results.sql
db/migrations/0039_anomaly_alerts.sql
db/migrations/0040_alert_acknowledgments.sql  # Depends on 0039
```

**Pages (3 files):**
```
apps/web_console/pages/data_sync.py
apps/web_console/pages/data_explorer.py
apps/web_console/pages/data_quality.py
```

**Components (13 files):**
```
apps/web_console/components/sync_status_table.py
apps/web_console/components/sync_logs_viewer.py
apps/web_console/components/sync_schedule_editor.py
apps/web_console/components/dataset_browser.py
apps/web_console/components/data_preview.py
apps/web_console/components/schema_viewer.py
apps/web_console/components/query_editor.py
apps/web_console/components/export_dialog.py
apps/web_console/components/coverage_timeline.py
apps/web_console/components/validation_results_table.py
apps/web_console/components/anomaly_alert_feed.py
apps/web_console/components/quality_trend_chart.py
apps/web_console/components/coverage_chart.py  # T8.3 coverage visualization
```

**Services (5 files):**
```
apps/web_console/services/data_sync_service.py
apps/web_console/services/data_sync_metrics.py
apps/web_console/services/data_explorer_service.py
apps/web_console/services/sql_validator.py
apps/web_console/services/data_quality_service.py
```

**Schemas (1 file):**
```
apps/web_console/schemas/data_management.py  # All DTO definitions
```

**Orchestrator (1 file):**
```
apps/orchestrator/schedule_evaluator.py  # SyncScheduleEvaluator with croniter
```

**Page Tests (3 files):**
```
tests/apps/web_console/test_data_sync_dashboard.py
tests/apps/web_console/test_dataset_explorer.py
tests/apps/web_console/test_data_quality_reports.py
```

**Service Tests (7 files):**
```
tests/apps/web_console/services/test_data_sync_service.py
tests/apps/web_console/services/test_data_sync_rate_limit.py
tests/apps/web_console/services/test_data_explorer_service.py
tests/apps/web_console/services/test_sql_validator.py
tests/apps/web_console/services/test_query_rate_limit.py
tests/apps/web_console/services/test_data_quality_service.py
tests/apps/web_console/services/test_alert_acknowledgment.py
```

**Documentation (6 files):**
```
docs/CONCEPTS/data-sync-operations.md
docs/CONCEPTS/dataset-explorer.md
docs/CONCEPTS/data-quality-monitoring.md
docs/RUNBOOKS/data-sync-ops.md
docs/RUNBOOKS/data-quality-ops.md
docs/RUNBOOKS/dataset-explorer-ops.md
```

**Infrastructure (1 file):**
```
infra/cron/data-management-cleanup.yml  # Retention cleanup jobs
```

**DTO Definitions (Phase 1 deliverable):**
All DTOs will be defined as Pydantic models in `apps/web_console/schemas/data_management.py` during C2-DTO-Definitions phase:
- `SyncStatusDTO`, `SyncLogEntry`, `SyncScheduleDTO`, `SyncScheduleUpdateDTO`, `SyncJobDTO`
- `DatasetInfoDTO`, `DataPreviewDTO`, `QueryResultDTO`, `ExportJobDTO`
- `ValidationResultDTO`, `AnomalyAlertDTO`, `AlertAcknowledgmentDTO`, `QualityTrendDTO`, `QuarantineEntryDTO`

**Dataset Explorer Runbook (`docs/RUNBOOKS/dataset-explorer-ops.md`) covers:**
- Query timeout troubleshooting (optimize query, add filters)
- Export storage cleanup (24-hour TTL, manual cleanup if needed)
- Rate limit incident handling (check user activity, temporary increase)
- Query cancellation (CTRL+C handling, connection cleanup)
- Dataset access issues (permission verification, role mapping)
- SQL validation false positives (how to report/fix)

### Modified Files

**RBAC Updates:**
```
libs/web_console_auth/permissions.py  # Add new permissions + dataset-level access
```

**Navigation:**
```
apps/web_console/app.py  # Add navigation entries for new pages
```

**Orchestrator Integration:**
```
apps/orchestrator/main.py  # Integrate SyncScheduleEvaluator into main loop
```

**Dependencies:**
```
requirements.txt  # Add croniter, sqlglot dependencies
```

**Total: 46 new files + 4 modified files = 50 files**

(New files: 6 DB migrations, 3 pages, 13 components, 5 services, 1 schema, 1 orchestrator, 3 page tests, 7 service tests, 6 docs, 1 infra = 46 new files)
(Modified files: permissions.py, app.py, main.py, requirements.txt = 4 modified files)

---

## New Permissions Required

Add to `libs/web_console_auth/permissions.py`:

```python
class Permission(str, Enum):
    # ... existing permissions ...

    # T8.1: Data Sync Dashboard
    VIEW_DATA_SYNC = "view:data_sync"
    TRIGGER_DATA_SYNC = "trigger:data_sync"
    MANAGE_SYNC_SCHEDULE = "manage:sync_schedule"

    # T8.2: Dataset Explorer
    QUERY_DATA = "query:data"
    EXPORT_DATA = "export:data"

    # T8.3: Data Quality
    VIEW_DATA_QUALITY = "view:data_quality"
    ACKNOWLEDGE_ALERTS = "acknowledge:alerts"

class DatasetPermission(str, Enum):
    # Per-dataset access (for licensing compliance)
    CRSP_ACCESS = "dataset:crsp"
    COMPUSTAT_ACCESS = "dataset:compustat"
    TAQ_ACCESS = "dataset:taq"
    FAMA_FRENCH_ACCESS = "dataset:fama_french"
```

**Role Mapping:**
- `viewer`: VIEW_DATA_SYNC, VIEW_DATA_QUALITY, FAMA_FRENCH_ACCESS
- `analyst`: Above + QUERY_DATA, ACKNOWLEDGE_ALERTS, CRSP_ACCESS
- `operator`: Above + TRIGGER_DATA_SYNC, EXPORT_DATA, MANAGE_SYNC_SCHEDULE, COMPUSTAT_ACCESS
- `admin`: All permissions including all dataset access

---

## Component Implementation Order

Following 6-step pattern for each component:

### Phase 1: Foundation (Day 1-3)
1. **C1-Database-Migrations** - All 6 migrations: sync_logs, sync_schedule, query_audit, validation_results, anomaly_alerts, alert_acknowledgments
2. **C2-DTO-Definitions** - Define all Pydantic DTO models in schemas/data_management.py
3. **C3-Permissions** - Add new permissions + dataset-level access to RBAC
4. **C4-Service-Contracts** - Implement service layer classes with API contracts

### Phase 2: T8.1 Data Sync Dashboard (Day 3-5)
5. **C5-SyncService-Tests** - Service layer unit tests + rate limiting tests
6. **C6-SyncComponents** - Sync status table, logs viewer, schedule editor
7. **C7-SyncPage** - Data sync dashboard page

### Phase 3: T8.2 Dataset Explorer (Day 5-8)
8. **C8-SQLValidator** - Query validation with security rules (using sqlglot)
9. **C9-ExplorerService-Tests** - Service layer + SQL validator tests
10. **C10-ExplorerComponents** - Dataset browser, preview, query editor, export
11. **C11-ExplorerPage** - Dataset explorer page

### Phase 4: T8.3 Data Quality Reports (Day 8-10)
12. **C12-QualityService-Tests** - Service layer + acknowledgment tests
13. **C13-QualityComponents** - Validation results, anomaly feed, trends
14. **C14-QualityPage** - Data quality reports page

### Phase 5: Integration & Documentation (Day 10-13)
15. **C15-Navigation** - Update app.py with new page routes + metrics
16. **C16-Documentation** - CONCEPTS docs + operational runbooks

---

## Edge Cases & Error Handling

### T8.1 Data Sync Dashboard
- **No manifests exist:** Show "No datasets synced yet" with setup instructions
- **Sync in progress:** Disable manual sync button, show progress indicator
- **Rate limit exceeded:** Show "Please wait X seconds" with countdown
- **Schedule update conflict:** Optimistic locking with version check
- **Service unavailable:** Graceful degradation with cached last-known status

### T8.2 Dataset Explorer
- **Empty dataset:** Show "No data available" with sync suggestion
- **Query timeout (>30s):** Cancel query, show timeout message with optimization tips
- **Query too large (>10k rows):** Paginate results, show "Page 1 of N"
- **Invalid SQL:** Show syntax error with line/column, helpful message
- **Export too large (>100k rows):** Reject with limit message, suggest date filter
- **Dataset access denied:** Show "Access to {dataset} requires {permission}"
- **Streaming pagination:** Cursor-based pagination for large result sets
- **Concurrent query cancellation:** Support CTRL+C / cancel button

### T8.3 Data Quality Reports
- **No validation runs:** Show "Run sync to generate quality data"
- **Alert storm (>100 alerts):** Group by metric type, show top 10 with "View all"
- **Acknowledgment conflict:** First-write-wins (unique constraint on alert_id); subsequent attempts return existing acknowledgment
- **Historical data missing:** Show available range only with message

---

## Security Considerations

1. **SQL Injection Prevention (Server-Side)**
   - Query validation via SQLValidator (blocked patterns)
   - DuckDB read-only mode enforced
   - External access disabled (`enable_external_access = false`)
   - DDL/DML/PRAGMA/ATTACH/COPY explicitly blocked
   - Row limits enforced at query level

2. **Path Traversal Prevention**
   - All file paths validated against allowed directories
   - No user-supplied paths in file operations
   - Export files written to temp directory only

3. **Rate Limiting (Server-Side Enforcement)**
   - Query execution: 10 queries/minute per user
   - Export: 5 exports/hour per user
   - Manual sync trigger: 1/minute global
   - Enforcement: Redis-based rate limiter in service layer (not UI)
   - **Fail-closed policy:** If Redis unavailable, rate limiting denies all requests (security over availability)

4. **Audit Logging**
   - All manual sync triggers logged with user, dataset, reason
   - All data exports logged with user, query, row_count, format
   - All alert acknowledgments logged with user, alert_id, reason
   - All schedule changes logged with user, old/new values

5. **Dataset-Level Access Control**
   - CRSP/Compustat/TAQ require explicit dataset permissions
   - Fama-French public (default granted)
   - Export permission checked against dataset permission

---

## Success Criteria

| Metric | Target |
|--------|--------|
| Test coverage | >85% for all new code (page + service layer) |
| Page load time | <2 seconds for dashboards |
| Query response | <5 seconds for standard queries, 30s timeout |
| Auth enforcement | 100% endpoints RBAC protected |
| Rate limit enforcement | 100% server-side (not bypassable) |
| Audit coverage | 100% sensitive actions logged |
| Export timeout | 60 seconds max |
| Streaming support | Pagination for results >1000 rows |

---

## Deferred Features (Future Tasks)

The following features were identified during review but are **explicitly deferred** to keep P4T6 aligned with P4 Planning Track 8 scope:

| Feature | Reason | Deferred To |
|---------|--------|-------------|
| Lock status monitoring UI | Not in P4 Planning T8.1 | Future T8.x |
| Disk usage gauge | Not in P4 Planning T8.1 | Future T8.x |
| Force-unlock UI | CLI sufficient | Future T8.x |
| Quarantine CRUD (restore/delete) | Complex, CLI sufficient | Future T8.x |
| Value distribution histograms | Nice-to-have, not required | Future T8.x |
| Query explain plan | Nice-to-have | Future T8.x |
| Query history persistence | Session-only is sufficient | Future T8.x |

---

## Related Documents

- [P4_PLANNING.md](./P4_PLANNING.md) - Track 8 specification
- [P4T1_TASK.md](./P4T1_TASK.md) - Data infrastructure (dependency)
- [P4T5_TASK.md](./P4T5_TASK.md) - Track 7 Operations (auth patterns)
- [ADR-0023-data-quality-framework.md](../ADRs/ADR-0023-data-quality-framework.md)

---

## Review History

**Planning Review Summary (2025-12-24):**
- Gemini (planner): ✅ APPROVED at iteration 3
- Codex (planner): ✅ APPROVED at iteration 19
- Total issues addressed: 5 CRITICAL, 12 HIGH, 18 MEDIUM, 14 LOW

---

**Last Updated:** 2025-12-24
**Author:** AI Agent (Claude Code)
**Status:** ✅ Approved - Ready for Implementation

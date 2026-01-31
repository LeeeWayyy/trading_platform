---
id: P6T8
title: "Professional Trading Terminal - Execution Analytics"
phase: P6
task: T8
priority: P1
owner: "@development-team"
state: TASK
created: 2026-01-13
updated: 2026-01-31
dependencies: [P6T6]
related_adrs: [ADR-0031-nicegui-migration, ADR-XXXX-execution-analytics-architecture]
related_docs: [P6_PLANNING.md]
features: [T8.1-T8.3]
---

# P6T8: Execution Analytics

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P1 (Analytics)
**Owner:** @development-team
**Created:** 2026-01-13
**Updated:** 2026-01-31
**Track:** Track 8 of 18
**Dependency:** P6T6 (Advanced Orders - for TCA analysis)

**Pre-Implementation Requirement:**
- **ADR-XXXX-execution-analytics-architecture** must be created before implementation
- ADR scope: `export_audit` table design, TCA/TAQ service initialization, audit lifecycle patterns
- See `docs/ADRs/` for ADR template

**MANDATORY Pre-Implementation Verification Tasks:**
These tasks MUST be completed before implementation begins:

| Task | File | Verification |
|------|------|--------------|
| Add `VIEW_TCA` permission | `libs/platform/web_console_auth/permissions.py` | Add to Permission enum + ROLE_PERMISSIONS (OPERATOR, ADMIN) |
| Add `is_admin()` helper | `libs/platform/web_console_auth/permissions.py` | Public function + add to `__all__` |
| Verify JWT session_id claim | `libs/platform/web_console_auth/jwt_manager.py` | Check if `session_id` in JWT payload; add if missing |
| Verify user_id format | `libs/platform/web_console_auth/jwt_manager.py` | Confirm UUID (safe) or email (PII); update pii_columns accordingly |
| Verify AuditLogger signature | `libs/platform/web_console_auth/audit_logger.py` | Confirm `_write()` needs `ip_address`/`session_id` params added |
| Verify audit_log index | `db/migrations/` | Confirm `idx_audit_log_resource` exists or create migration |

**BREAKING CHANGE Warning:**
- `AuditLogger._write()` signature change affects all existing call sites
- All code calling `audit_logger.log_action()` MUST be updated to pass ip_address/session_id
- See T8.2 "Order Action Inventory" section for complete call sites list

**Excel Audit ID Reuse Policy:**
- Audit IDs are **SINGLE-USE** for Excel downloads
- If network fails during download, client MUST create a new audit_id
- This is intentional: prevents audit manipulation (one audit for multiple exports)
- The audit record documents the export *intent* regardless of download success/failure

---

## Objective

Build execution analytics: Transaction Cost Analysis (TCA), order audit trail, and CSV export on all grids.

**Success looks like:**
- TCA dashboard showing execution quality metrics via API
- Complete audit trail for all order actions with IP/session tracking
- CSV/Excel export on all data grids with PII handling and audit logging

---

## Implementation Order

```
T8.3 (CSV Export) [HIGH PRIORITY - Foundation]
    │
    ├──> T8.1 (TCA Dashboard) [uses export component]
    │
    └──> T8.2 (Audit Trail) [uses export for compliance]
```

**Rationale**: T8.3 creates a reusable export toolbar that T8.1 and T8.2 will leverage.

---

## Tasks (3 total)

### T8.3: CSV Export on All Grids - HIGH PRIORITY

**Goal:** Enable data verification in external tools with proper security controls.

**Requirements:**
- Every AG Grid should have export toolbar
- CSV export (client-side via AG Grid Community `exportDataAsCsv()`)
- Excel export (server-side via `openpyxl` - AG Grid Enterprise not available)
- Copy to Clipboard (client-side via `navigator.clipboard.writeText()` with grid data as CSV)
  - NOTE: AG Grid Community's `copyToClipboard()` is Enterprise-only; use custom implementation
- **Server-side audit logging for ALL export types**

**Technical Approach:**
- **CSV/Clipboard:** Client-side generation, but MUST call server audit endpoint before/after
- **Excel:** Server-side generation with `openpyxl`
- **PII Handling:** Server-side redaction before sending data to grid
  - **Non-admin users:** Grids never receive PII (ip_address, user_agent, session_id → `[REDACTED]`)
  - **Admin users (Role.ADMIN):** Grids receive full PII for audit/compliance visibility
  - **Export:** Same redaction rules apply - non-admins get redacted, admins see full data
- **Formula Injection:** Sanitize ALL export paths (CSV/Clipboard/Excel)
  - Client-side: Apply shared sanitizer in `grid_export.js` before CSV/clipboard export
  - Server-side: Apply sanitizer before Excel generation
  - **String values only:** Numbers, booleans, null pass through unchanged
  - **Strip leading whitespace/control chars** to find first meaningful character (prevents bypass via `" =FORMULA"`)
  - **Dangerous first chars:** `=`, `+`, `@`, `\t`, `\r`, `\n` → prepend single quote `'` to original value
  - **Leading `-` handling:** Sanitize non-numeric strings starting with `-` (e.g., `-1+1`, `-A1`)
  - **Preserve numeric negatives:** Allow strictly numeric values like `-123.45` to pass through unchanged
  - **Identical behavior:** Client-side (JS) and server-side (Python) sanitizers MUST produce identical output
- **Permissions:** Require `Permission.EXPORT_DATA` (already exists in permissions.py)
  - **LIMITATION:** CSV/Clipboard permission check is UI-enforced only. Users with grid view access can
    bypass via browser devtools (`gridApi.exportDataAsCsv()`). This is a known limitation of client-side exports.
  - **Mitigation:** For environments requiring strict EXPORT_DATA enforcement, use `EXPORT_STRICT_AUDIT_MODE=true`
    to disable CSV/Clipboard entirely and force server-side Excel export (fully enforced).
  - **Note:** PII redaction is still enforced server-side (grid never receives raw PII for non-admins).
- **Audit:** ALL export UI interactions logged server-side (CSV/Clipboard = best-effort, Excel = authoritative)
- **Row Limits:** Server-side enforcement of max 10,000 rows
  - At audit creation (POST /api/v1/export/audit): Server computes `estimated_row_count` using the validated filter_params
  - This server-computed count is authoritative and used for the row limit check
  - If `estimated_row_count > 10,000`: Return `allowed=false` and immediately set status="failed"
  - CSV/Clipboard: `export_scope="visible"` forced; client reports actual_row_count but this is unverified
  - Excel: `export_scope="full"` forced; actual_row_count is server-computed during generation
  - If pagination enabled: CSV/Clipboard DISABLED (see Row Limit Behavior table)

**Export Audit Flow (ALL export types):**
```
1. User clicks Export CSV/Excel/Clipboard
2. Client calls POST /api/v1/export/audit with export metadata
3. Server validates permission, checks row limit:
   - If allowed: INSERT with status="pending", return allowed=true
   - If denied (row limit exceeded): INSERT with status="failed" + error_message, return allowed=false
   (Single INSERT operation, not INSERT + UPDATE; avoids dangling pending records)
4. Server returns audit_id (UUID from export_audit) + allowed=true/false
5. CSV/Clipboard: Client performs export only if allowed=true
6. Excel: Client calls GET /api/v1/export/excel/{audit_id} - server generates + COMPLETES AUDIT server-side
7. CSV/Clipboard ONLY: Client calls PATCH /api/v1/export/audit/{audit_id}/complete with actual row_count
   (Excel skips this step - audit completed server-side in step 6)
8. Server updates export_audit record with status="completed" or status="failed"
```

**Note:** Export tracking uses the dedicated `export_audit` table (not `audit_log`) because exports have a lifecycle (pending → completed/failed/expired) that requires UPDATE capability. The `audit_log` table remains write-once for order actions.

**Export Audit Status Values:**
- `pending`: Audit created, waiting for export completion
- `completed`: Export finished successfully
- `failed`: Export denied (row limit) or failed during generation
- `expired`: Stale pending (client abandoned CSV/clipboard without completing)

**Audit Completion Endpoint:**
```
PATCH /api/v1/export/audit/{audit_id}/complete
    Body: {success: bool, actual_row_count: int, error_message?: str}
    Auth: EXPORT_DATA permission + audit_id ownership
    Server:
      - CRITICAL: Reject if export_type == "excel" (409 Conflict: "Excel audits are server-completed")
        - Excel audit completion happens during GET /api/v1/export/excel/{audit_id}
        - Client PATCH cannot modify server-authenticated audit records
      - CRITICAL: Reject if status != "pending" (409 Conflict: "Audit already completed")
        - Prevents tampering with completed/failed/expired audits
      - Updates audit record: status="completed" or "failed"
      - Records actual_row_count with reported_by="client"
      - Records completion_time
    Response: {audit_id: str, status: str}
    Error Responses:
      - 409 Conflict: export_type is "excel" or status is not "pending"
      - 403 Forbidden: audit_id not owned by user

**CSV/Clipboard Row Count Compliance Note:**
- Client-reported `actual_row_count` for CSV/clipboard is UNVERIFIED
- Column `reported_by="client"` marks this for compliance auditors
- For authoritative counts: use `estimated_row_count` (server-computed at request time)
- UI should display: "Exported ~{estimated_row_count} rows (unverified)"
- For strict compliance requirements: use Excel export (server-generated, reported_by="server")

**CSV/Clipboard Audit Bypass Risk (KNOWN LIMITATION):**
- **IMPORTANT:** Client-side CSV/clipboard export can be bypassed via browser devtools
- A technical user can call `gridApi.exportDataAsCsv()` directly in the console
- This bypasses the audit endpoint entirely - no audit record is created
- **Mitigations:**
  - PII is redacted server-side before data reaches grid (cannot export raw PII)
  - Grid data endpoints enforce server-side row limit (max 10,000 rows per request)
    - If data exceeds limit, server-side pagination is enabled automatically
    - When pagination enabled, CSV/Clipboard buttons are DISABLED (see Row Limit Behavior table)
  - `estimated_row_count` in audit provides upper bound of possible export
- **Detection:** For compliance-critical deployments, use `EXPORT_STRICT_AUDIT_MODE=true` to disable CSV/clipboard entirely

**Strict Audit Mode (Feature Flag):**
```python
# In apps/web_console_ng/config.py
EXPORT_STRICT_AUDIT_MODE = os.getenv("EXPORT_STRICT_AUDIT_MODE", "false").lower() == "true"
```
- **When `EXPORT_STRICT_AUDIT_MODE=true`:**
  - CSV/Clipboard buttons are HIDDEN from export toolbar
  - Only server-side Excel export is available (fully audited)
  - Use this for compliance-critical deployments where bypass is unacceptable
- **When `EXPORT_STRICT_AUDIT_MODE=false` (default):**
  - All export options available (CSV, Excel, Clipboard)
  - CSV/Clipboard use best-effort client-side audit (reported_by="client")
  - Suitable for internal/research use cases

**Clarification on "ALL exports logged" requirement:**
- This requirement means: "ALL export UI interactions are logged"
- It does NOT mean: "ALL possible data egress is logged" (impossible without DLP)
- Client-side exports are logged "best-effort" via audit endpoint
- For authoritative audit: use `EXPORT_STRICT_AUDIT_MODE=true`

**Deployment Policy Guidance:**
| Environment | EXPORT_STRICT_AUDIT_MODE | Rationale |
|-------------|--------------------------|-----------|
| Development | `false` | Convenience for developers |
| Staging | `false` | Match dev behavior for testing |
| Production (internal) | `true` | **CHANGED:** `EXPORT_DATA` permission is only enforceable with strict mode |
| Production (compliance-regulated) | `true` | MANDATORY for SOX/SEC/FINRA compliance |
| Production (client-facing) | `true` | MANDATORY to prevent data leakage |

**IMPORTANT: EXPORT_DATA Permission Enforcement**
- The `Permission.EXPORT_DATA` is only enforceable when `EXPORT_STRICT_AUDIT_MODE=true`
- With strict mode disabled, any user with grid view access can bypass EXPORT_DATA via browser devtools
- **Production recommendation:** Always use strict mode unless there's an explicit business reason to allow client-side exports

**CI/CD Enforcement:** Add a deployment guardrail that FAILS builds deploying to production with `EXPORT_STRICT_AUDIT_MODE=false` unless explicitly approved.

**MANDATORY: App Startup Guard (Fail-Closed Enforcement)**
```python
# In apps/web_console_ng/config.py
import os
import sys
import logging

logger = logging.getLogger(__name__)

EXPORT_STRICT_AUDIT_MODE = os.getenv("EXPORT_STRICT_AUDIT_MODE", "false").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()

# Production environments where strict mode is REQUIRED
STRICT_MODE_REQUIRED_ENVS = {"production", "prod", "production-internal"}

# Fail-closed: Block startup if production + non-strict mode
if ENVIRONMENT in STRICT_MODE_REQUIRED_ENVS and not EXPORT_STRICT_AUDIT_MODE:
    logger.critical(
        "FATAL: EXPORT_STRICT_AUDIT_MODE=false is not allowed in production. "
        "Set EXPORT_STRICT_AUDIT_MODE=true or set ENVIRONMENT to non-production."
    )
    sys.exit(1)
```

**Acceptance Criteria (T8.3):**
- [ ] App fails to start if ENVIRONMENT=production and EXPORT_STRICT_AUDIT_MODE=false
- [ ] CI pipeline validates EXPORT_STRICT_AUDIT_MODE=true for production deployments
- [ ] Deploy script (if any) checks strict mode before deployment

**Files to Modify:**
- `apps/web_console_ng/config.py` - Add startup guard
- `apps/execution_gateway/config.py` - Add SAME startup guard (exports enforced here)
- `.github/workflows/deploy.yml` or equivalent - Add CI validation

**CRITICAL: execution_gateway ALSO needs startup guard**
Export endpoints live in execution_gateway, so both services MUST enforce strict mode:
```python
# In apps/execution_gateway/config.py - SAME guard as web_console_ng
import os
import sys
import logging

logger = logging.getLogger(__name__)

EXPORT_STRICT_AUDIT_MODE = os.getenv("EXPORT_STRICT_AUDIT_MODE", "false").lower() == "true"
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()

# Fail-closed in production
STRICT_MODE_REQUIRED_ENVS = {"production", "prod", "production-internal"}
if ENVIRONMENT in STRICT_MODE_REQUIRED_ENVS and not EXPORT_STRICT_AUDIT_MODE:
    logger.critical(
        "FATAL: EXPORT_STRICT_AUDIT_MODE=false is not allowed in production. "
        "Set EXPORT_STRICT_AUDIT_MODE=true or set ENVIRONMENT to non-production."
    )
    sys.exit(1)
```

**Alternative: Shared Config Module**
To avoid duplication, extract to `libs/platform/common/export_config.py`:
```python
# libs/platform/common/export_config.py
def validate_strict_mode_for_production():
    """Call at startup in both web_console_ng and execution_gateway."""
    # ... same logic
    pass
```

**Export Audit Table (NEW - separate from audit_log):**
```sql
-- db/migrations/XXXX_add_export_audit_and_index.sql
-- Required for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS export_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,  -- TEXT for parity with audit_log
    export_type VARCHAR(20) NOT NULL CHECK (export_type IN ('csv', 'excel', 'clipboard')),
    grid_name VARCHAR(100) NOT NULL,
    filter_params JSONB,
    visible_columns JSONB,  -- List of columns to export (for audit reproducibility)
    sort_model JSONB,  -- AG Grid sort model (for reproducibility)
    strategy_ids JSONB,  -- Server-injected strategy scope (for compliance reproducibility)
    export_scope VARCHAR(20) NOT NULL DEFAULT 'visible' CHECK (export_scope IN ('visible', 'full')),
    estimated_row_count INTEGER,
    actual_row_count INTEGER,
    reported_by VARCHAR(10) CHECK (reported_by IN ('client', 'server')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed', 'expired')),
    ip_address TEXT,  -- TEXT for consistency with audit_log; validate at app layer
    session_id TEXT,  -- TEXT for parity with audit_log
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completion_time TIMESTAMPTZ,
    error_message TEXT
);
CREATE INDEX idx_export_audit_user ON export_audit(user_id, created_at DESC);
```

**Rationale for separate table:**
- Export audit has different lifecycle (pending → completed/failed) vs general audit_log (write-once)
- Needs status updates, which audit_log doesn't support
- Cleaner separation of concerns: export_audit for T8.3, audit_log for T8.2 order audit

**Relationship with existing AuditLogger.log_export():**
- `AuditLogger.log_export()` already exists and is used by legacy exports (e.g., `pages/journal.py`)
- **Decision:** Keep both systems during transition
  - `export_audit` table: Used by T8.3 grid exports (new implementation with lifecycle)
  - `AuditLogger.log_export()`: Continues to work for existing/legacy exports
- **Future:** Consider migrating legacy exports to `export_audit` in a follow-up task
- **Metrics/Dashboards:** Query both tables for complete export metrics until migration

**Export Audit Retention/Cleanup:**
```python
# Similar to AuditLogger.cleanup_old_events()
# In apps/execution_gateway/services/export_audit_service.py

EXPORT_AUDIT_RETENTION_DAYS = int(os.getenv("EXPORT_AUDIT_RETENTION_DAYS", "90"))

STALE_PENDING_HOURS = int(os.getenv("EXPORT_AUDIT_STALE_HOURS", "24"))

def cleanup_old_export_audits(db: DatabaseClient) -> dict:
    """Remove old export_audit records and expire stale pending exports.

    NOTE: DatabaseClient is sync; call via run_in_executor if needed from async context.

    Returns counts: {deleted: int, expired: int}
    """
    from datetime import timezone

    # 1. Delete old completed/failed records (retention period)
    # Use timezone-aware UTC to match TIMESTAMPTZ columns
    retention_cutoff = datetime.now(timezone.utc) - timedelta(days=EXPORT_AUDIT_RETENTION_DAYS)
    deleted = db.execute_returning(
        "DELETE FROM export_audit WHERE created_at < %s RETURNING id",
        [retention_cutoff]
    )

    # 2. Expire stale pending exports (client never completed CSV/clipboard)
    # Mark as "expired" with error message (for compliance audit)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_PENDING_HOURS)
    expired = db.execute_returning(
        """UPDATE export_audit
           SET status = 'expired', error_message = 'Client did not complete export'
           WHERE status = 'pending' AND created_at < %s
           RETURNING id""",
        [stale_cutoff]
    )

    return {"deleted": len(deleted), "expired": len(expired)}

# Schedule via cron job or APScheduler in app lifespan:
# - Run hourly for stale expiration, daily for retention cleanup
# - Log counts for monitoring
```

**Cleanup Acceptance Criteria:**
- [ ] Configurable retention period (default 90 days)
- [ ] Configurable stale pending expiration (default 24 hours)
- [ ] Scheduled cleanup job (hourly for stale, daily for retention)
- [ ] Log deleted/expired counts to metrics/monitoring

**Concrete Scheduling Implementation:**
```python
# In apps/execution_gateway/main.py - add to lifespan context manager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apps.execution_gateway.services.export_audit_service import cleanup_old_export_audits
import asyncio

async def run_cleanup_in_executor(db):
    """Wrap sync DB cleanup in executor to avoid blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, cleanup_old_export_audits, db)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize scheduler
    scheduler = AsyncIOScheduler()

    # Schedule stale expiration (hourly) - uses executor to avoid blocking
    scheduler.add_job(
        run_cleanup_in_executor,
        'interval',
        hours=1,
        id='export_audit_stale_cleanup',
        args=[app.state.db],
    )

    # Schedule retention cleanup (daily at 3 AM UTC)
    scheduler.add_job(
        run_cleanup_in_executor,
        'cron',
        hour=3,
        id='export_audit_retention_cleanup',
        args=[app.state.db],
    )

    scheduler.start()
    yield
    # Shutdown in reverse order: scheduler first, then executor
    scheduler.shutdown()
    # Shutdown TCA executor to prevent thread leaks
    # wait=True ensures pending tasks complete; timeout after 5s
    from apps.execution_gateway.app_context import tca_executor
    if tca_executor:
        tca_executor.shutdown(wait=True)

# File modification required: apps/execution_gateway/main.py
# - Add APScheduler dependency to requirements
# - Integrate with existing lifespan or create new one
```

**Grid-to-Permission Mapping:**
```python
# Grid-level view permissions required for export
GRID_VIEW_PERMISSIONS = {
    "positions": Permission.VIEW_POSITIONS,
    "orders": Permission.VIEW_TRADES,  # VIEW_TRADES covers orders
    "fills": Permission.VIEW_TRADES,
    "audit": Permission.VIEW_AUDIT,
    "tca": Permission.VIEW_TCA,  # For TCA grid export
}
```

**Audit Endpoint:**
```
POST /api/v1/export/audit
    Body: {
        export_type: "csv"|"excel"|"clipboard",
        grid_name: str,
        filter_params: dict,
        visible_columns: list[str],  # Columns to export (validated against allowlist)
        sort_model: list[dict] | null  # AG Grid sort model for reproducibility
    }
    Auth: EXPORT_DATA permission + grid-level view permission required
    Server:
      - CRITICAL: If EXPORT_STRICT_AUDIT_MODE=true AND export_type in {"csv", "clipboard"}:
        - REJECT with 403 Forbidden: "CSV/clipboard exports disabled in strict audit mode"
        - This server-side check prevents bypass of UI-hidden buttons via direct API calls
      - CRITICAL: Check grid-level permission via GRID_VIEW_PERMISSIONS[grid_name]
        - If user lacks VIEW_POSITIONS (for positions grid), reject with 403
        - If grid_name not in GRID_VIEW_PERMISSIONS, reject with 400 (unknown grid)
      - CRITICAL: Inject strategy scoping (see below) regardless of client filters
      - CRITICAL: Validate visible_columns against GRID_COLUMN_CLASSIFICATION[grid_name].allowed_columns
        - REJECT request if any column is not in server-side allowlist (400 Bad Request)
        - Log rejected columns for security monitoring
        - DO NOT trust client-supplied column lists blindly
      - CRITICAL: Validate filter_params keys against known filterable columns for grid
        - REJECT unknown filter fields to prevent SQL injection or data exfiltration
      - Counts rows server-side using VALIDATED filter_params (not client-reported count)
      - Checks estimated_row_count <= 10,000
      - INSERT INTO export_audit with status based on row limit check:
        - If allowed: INSERT with status="pending"
        - If denied: INSERT with status="failed", error_message="Row limit exceeded"
        (Single INSERT, not INSERT+UPDATE; consistent with flow section)
      - **CRITICAL: If INSERT fails (DB error), return 503 and BLOCK export**
        - Audit logging is MANDATORY; export without audit is a compliance violation
      - Records ip_address, session_id, user_agent, strategy_ids (via request context helper)
    Response: {audit_id: str, allowed: bool, estimated_row_count: int, message?: str}
    Error Responses:
      - 503 Service Unavailable: Audit insert failed (DB down) - export blocked
      - 403 Forbidden: EXPORT_DATA permission missing
```

**Excel Export Endpoint (audit_id required):**
```
GET /api/v1/export/excel/{audit_id}
    Auth: EXPORT_DATA permission + grid-level view permission + audit_id ownership
    Server:
      - Validates audit_id exists and belongs to user
      - Validates export_type was "excel" in audit record
      - CRITICAL: Check grid-level permission via GRID_VIEW_PERMISSIONS[audit.grid_name]
      - CRITICAL: Validate audit status is "pending" (see status gating below)
        - Reject if status is "completed" (prevents re-download without new audit)
        - Reject if status is "failed" (export was denied)
        - Reject if status is "expired" (client took too long)
      - CRITICAL: Re-applies strategy scoping when fetching data (defense in depth)
      - CRITICAL: Validates visible_columns against GRID_COLUMN_CLASSIFICATION allowlist
      - CRITICAL: Applies PII redaction via apply_pii_redaction() before file generation
        (Server-side redaction is MANDATORY for ALL data paths; Excel uses same redaction as grid API)
      - Generates Excel file using stored filter_params and validated visible_columns
      - **COMPLETES AUDIT SERVER-SIDE** (does NOT rely on client PATCH):
        - On success: status="completed", actual_row_count=<server-computed>, reported_by="server"
        - On failure: status="failed", error_message=<reason>
      - Returns file download
    Response: Excel file (application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)

**Note on Audit Completion:**
- **Excel:** Audit completed SERVER-SIDE during generation; client MUST NOT call PATCH (server rejects with 409)
- **CSV/Clipboard:** Client MUST call PATCH /complete (reported_by="client")
```

**Audit_id Status Gating and Reuse Rules:**
| Status | Excel Download | CSV/Clipboard Complete | New Audit Required |
|--------|----------------|------------------------|-------------------|
| `pending` | ALLOWED (one-time) | ALLOWED | No |
| `completed` | REJECTED (already used) | REJECTED | Yes, for new export |
| `failed` | REJECTED (denied export) | REJECTED | Yes, fix issue first |
| `expired` | REJECTED (stale) | REJECTED | Yes |

**Key Rules:**
- **One audit_id = one export:** Each export requires a fresh audit_id
- **No re-downloads:** Completed audit_ids cannot be reused for additional downloads
- **Download tracking:** For compliance, `download_count` could be added (future enhancement)
- **Why reject completed:** Prevents audit manipulation (e.g., one audit for multiple exports)

**Server-Side Strategy Scoping (MANDATORY):**
```python
# In apps/execution_gateway/services/grid_query.py
# ALL export queries MUST enforce strategy scoping regardless of client filters

# Grid configs with scoping strategy per grid
# NOTE: audit_log has no strategy_id column, so it uses JOIN-based scoping
GRID_SCOPING_STRATEGY = {
    "positions": "direct",  # positions.strategy_id IN (...)
    "orders": "direct",     # orders.strategy_id IN (...)
    "fills": "direct",      # trades.strategy_id IN (...)
    "audit": "join_orders", # JOIN orders ON audit_log.resource_id = orders.client_order_id
    "tca": "direct",        # Uses TCA-specific query, not this builder
}

def build_export_query(
    grid_name: str,
    filter_params: dict,
    user: AuthenticatedUser,
) -> tuple[str, list]:
    """Build export query with MANDATORY strategy scoping and base_filter injection."""
    # Get user's authorized strategies (SECURITY: server-side, not from client)
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        raise PermissionError("No authorized strategies")

    # Build base query with column filtering
    query_builder = GridQueryBuilder()
    where_clause, params = query_builder.build_where_clause(filter_params, grid_name)

    # MANDATORY: Inject base_filter from grid config if defined
    # This ensures audit grid always filters to resource_type='order'
    grid_config = GRID_QUERY_CONFIG.get(grid_name, {})
    base_filter = grid_config.get("base_filter")
    if base_filter:
        if where_clause:
            where_clause = f"({base_filter}) AND ({where_clause})"
        else:
            where_clause = base_filter

    # CRITICAL: Inject strategy scope regardless of client filters
    # This prevents client from exporting other strategies' data
    strategy_placeholders = ','.join(['%s'] * len(authorized_strategies))

    scoping_strategy = GRID_SCOPING_STRATEGY.get(grid_name, "direct")

    if scoping_strategy == "join_orders":
        # SPECIAL CASE: audit_log has no strategy_id column
        # Must JOIN to orders table and filter by orders.strategy_id
        # NOTE: This only returns audit entries for order-related resources
        # Audit entries for non-order resources (e.g., system actions) are excluded
        # Admins with VIEW_ALL_STRATEGIES get all order-related audit entries
        strategy_scope = f"""
            resource_type = 'order' AND EXISTS (
                SELECT 1 FROM orders o
                WHERE o.client_order_id = audit_log.resource_id
                AND o.strategy_id IN ({strategy_placeholders})
            )
        """
    else:
        # Direct strategy_id column on the table
        strategy_scope = f"strategy_id IN ({strategy_placeholders})"

    if where_clause:
        where_clause = f"({where_clause}) AND {strategy_scope}"
    else:
        where_clause = strategy_scope

    params.extend(authorized_strategies)

    return where_clause, params
```

**IMPORTANT: Audit Export Limitations**
- Audit exports are scoped to **order-related entries only** via `resource_type = 'order'`
- Non-order audit entries (system events, config changes) are NOT exportable via grid export
- This is by design: audit_log has no strategy_id column, so we JOIN to orders for scoping
- Admins can access raw audit_log via database tools if needed for compliance

**MANDATORY: resource_id Standardization for Order-Related Audit Events**
For audit export scoping to work correctly, ALL order-related audit events MUST:
1. Set `resource_type = 'order'`
2. Set `resource_id = client_order_id` (NOT broker_order_id or trade_id)

This applies to:
| Event Source | Example Actions | resource_id |
|--------------|-----------------|-------------|
| Web Console order submission | `order_submitted`, `order_cancelled` | `client_order_id` |
| Execution Gateway webhook | `order_filled`, `order_partially_filled` | `client_order_id` |
| Reconciler adjustments | `order_reconciled`, `position_adjusted` | `client_order_id` |
| Signal service | `signal_generated` | Use `order` type only if linked to order |

**CRITICAL:** If any existing code uses `broker_order_id` or other identifiers as `resource_id` for order events, those entries will be INVISIBLE to audit exports. Verify and fix during implementation.

**Row Limit Enforcement:**
- **Server-side authority:** Row count is computed server-side using filter_params, not client-reported
- **Enforcement point:** /api/v1/export/audit endpoint computes actual row count from database
- **Denial flow:** If row_count > 10,000, return {allowed: false, message: "Export exceeds 10,000 row limit"}

**Formula Sanitization (shared utility):**
```javascript
// In grid_export.js - applied to ALL export paths
// ONLY sanitizes STRING values to avoid corrupting negative numbers
function sanitizeForExport(value) {
    // Only sanitize strings - numbers, booleans, null pass through unchanged
    if (typeof value !== 'string') return value;

    // Strip leading whitespace and control characters, then check first meaningful char
    // This prevents bypass via " =FORMULA" or "\t=FORMULA"
    const trimmed = value.replace(/^[\s\x00-\x1f]+/, '');  // Strip leading whitespace/control
    if (trimmed.length === 0) return value;  // All whitespace - safe

    const firstChar = trimmed[0];
    const dangerous = ['=', '+', '@', '\t', '\r', '\n'];

    // Check if first meaningful character is dangerous
    if (dangerous.includes(firstChar)) {
        return "'" + value;  // Prepend quote to ORIGINAL value
    }

    // For '-', only allow if STRICTLY numeric (e.g., "-123.45")
    // Block "-1+1", "-A1", etc. which could be formulas
    if (firstChar === '-') {
        // Strict numeric pattern: optional minus, digits, optional decimal + digits
        const strictNumericRegex = /^-?\d+(\.\d+)?$/;
        if (!strictNumericRegex.test(trimmed)) {
            return "'" + value;  // Not strictly numeric, sanitize it
        }
        // Strictly numeric negative number - safe to pass through
    }
    return value;
}
```

**Server-Side Formula Sanitization (Python):**
```python
# In apps/execution_gateway/services/export_utils.py
import re

def sanitize_for_export(value: Any) -> Any:
    """Sanitize value for CSV/Excel export to prevent formula injection."""
    if not isinstance(value, str):
        return value

    # Strip leading whitespace/control chars to find first meaningful char
    trimmed = re.sub(r'^[\s\x00-\x1f]+', '', value)
    if not trimmed:
        return value  # All whitespace - safe

    first_char = trimmed[0]
    dangerous = {'=', '+', '@', '\t', '\r', '\n'}

    if first_char in dangerous:
        return "'" + value  # Prepend quote to original

    # For '-', only allow if STRICTLY numeric (e.g., "-123.45")
    # Block "-1+1", "-A1", etc. which could be formulas
    if first_char == '-':
        # Strict numeric pattern: optional minus, digits, optional decimal + digits
        if not re.match(r'^-?\d+(\.\d+)?$', trimmed):
            return "'" + value  # Not strictly numeric, sanitize it
        # Strictly numeric negative number - safe to pass through
    return value
```

**Export Scope (Two Modes):**

**Mode 1: Client-Side Export (CSV/Clipboard) - Visible Rows Only:**
- Exports rows currently loaded in the client-side grid
- NiceGUI AG Grids load all row data client-side (not server-paginated)
- For grids with <10,000 rows, this effectively exports the full filtered dataset
- For very large datasets, server-side pagination may limit visible rows
- **MANDATORY:** Server DERIVES `export_scope` from `export_type` (not client-supplied):
  - `export_type=csv|clipboard` → server sets `export_scope="visible"` in audit record
  - `export_type=excel` → server sets `export_scope="full"` in audit record
  - Client does NOT send `export_scope` in request; server controls this field
- **Row count alignment:**
  - `estimated_row_count`: Server-computed at audit time (authoritative upper bound)
  - `actual_row_count`: Client-reported (may differ if user scrolled/filtered after audit)
  - `reported_by="client"`: Marks count as unverified for compliance
  - Auditors should use `estimated_row_count` as worst-case export size

**Mode 2: Server-Side Export (Excel) - Full Dataset:**
- Server fetches ALL rows matching filter_params from database
- Subject to 10,000 row limit enforced server-side
- **Server enforces** `export_scope="full"` for Excel (server controls entire flow)
- **Row count alignment:**
  - `estimated_row_count` and `actual_row_count` are both server-computed and EQUAL
  - `reported_by="server"`: Marks count as authoritative
  - This is the only fully auditable export path

**Grid Data Loading Strategy:**
- Current grids (positions, orders, fills, audit): Load all data client-side (<10k rows typical)
- If grid row count may exceed 10k, implement server-side pagination with server-only Excel export
- Audit endpoint logs actual export scope for compliance verification

**Row Limit Behavior for CSV/Clipboard (Current vs Future with Pagination):**

| Scenario | Server Row Count | Visible Rows | CSV/Clipboard Allowed | Row Limit Check |
|----------|------------------|--------------|----------------------|-----------------|
| No pagination (current) | 5,000 | 5,000 | YES | server count < 10k |
| No pagination | 15,000 | 15,000 | NO (blocked) | server count >= 10k |
| With pagination | 15,000 | 100 (page 1) | **DISABLED** | N/A |

**Pagination Future Handling:**
- When server-side pagination is enabled for a grid, CSV/Clipboard are DISABLED
- Only Excel export (server-side, full dataset) is available for paginated grids
- This avoids inconsistency between visible rows (small) and server count (large)
- UI should hide CSV/Clipboard buttons for paginated grids (via grid config flag)
- `EXPORT_STRICT_AUDIT_MODE=true` achieves similar effect (Excel-only everywhere)

**Export Scope Enforcement Summary:**
| Export Type | export_scope | row_count source | Auditable |
|-------------|--------------|------------------|-----------|
| CSV | `visible` (forced) | client-reported | Best-effort |
| Clipboard | `visible` (forced) | client-reported | Best-effort |
| Excel | `full` (forced) | server-computed | Authoritative |

**Export Endpoint Ownership:**
- **Export endpoints live in execution_gateway** (not web_console_ng)
- **Reason:** execution_gateway has DB access and AuditLogger
- **Files:** `apps/execution_gateway/routes/export.py` (not web_console_ng/api/export.py)

**PII Redaction Layer:**
```python
# Column classification per grid (apps/execution_gateway/schemas/export.py)
GRID_COLUMN_CLASSIFICATION = {
    "positions": {
        "pii_columns": [],  # No PII in positions grid
        "allowed_columns": ["symbol", "qty", "avg_entry_price", "current_price", ...]
    },
    "orders": {
        # user_id PII status - DEFAULT TO REDACT (fail-safe)
        # SECURITY: Default to treating user_id as PII until verified as non-identifying
        # This prevents accidental PII exposure if user_id is email/SSO identifier
        #
        # VERIFICATION REQUIRED (T8.3 acceptance criteria):
        # 1. Check `libs/platform/web_console_auth/jwt_manager.py` for user_id format
        # 2. If user_id is internal UUID (e.g., "usr_abc123"): Remove from pii_columns
        # 3. If user_id is email/SSO identifier: Keep in pii_columns
        # 4. Document finding in implementation PR
        "pii_columns": ["user_id"],  # DEFAULT: Treat as PII until verified as UUID
        "allowed_columns": ["symbol", "side", "qty", "price", "status", "user_id", ...]
    },
    "audit": {
        # user_agent is stored in details JSONB; extract via: details->>'user_agent' AS user_agent
        # CONSISTENT WITH ORDERS: user_id defaults to PII until verified as non-identifying UUID
        "pii_columns": ["ip_address", "user_agent", "session_id", "user_id"],  # user_id added for consistency
        "allowed_columns": ["timestamp", "action", "outcome", ...],
        "computed_columns": {
            "user_agent": "details->>'user_agent'"  # Extract from JSONB for grid display
        }
    }
}

# UNIFIED user_id PII Policy (applies to ALL grids and APIs):
# - DEFAULT: user_id is treated as PII and redacted for non-admins
# - After verification: if user_id is confirmed as non-identifying UUID (not email/SSO),
#   remove from pii_columns in BOTH orders and audit grids
# - This ensures consistent behavior across all export paths and API responses

# Redaction applied at query layer in DatabaseClient
def apply_pii_redaction(rows: list[dict], grid_name: str, user: AuthenticatedUser) -> list[dict]:
    """Redact PII columns based on explicit permission check.

    PII Access Policy:
    - Use `Role.ADMIN` check (not just any permission) to gate PII visibility
    - This prevents accidental PII exposure if VIEW_ALL_STRATEGIES is granted to non-admin roles
    - Future: Consider dedicated `VIEW_AUDIT_PII` permission if finer-grained control needed
    """
    config = GRID_COLUMN_CLASSIFICATION.get(grid_name, {})

    # SECURITY: Use explicit Role.ADMIN check, NOT permission-based
    # This ensures only true admins see PII, even if VIEW_ALL_STRATEGIES is granted to other roles
    from libs.platform.web_console_auth.permissions import Role, is_admin
    # Use public helper (not private _extract_role) for stable API
    if is_admin(user):
        return rows  # Only admin role sees all PII

    pii_cols = config.get("pii_columns", [])
    redacted_rows = []
    for row in rows:
        redacted_row = {k: "[REDACTED]" if k in pii_cols else v for k, v in row.items()}
        # Also redact PII in nested "details" JSON if present (for audit grid)
        if "details" in redacted_row and isinstance(redacted_row["details"], dict):
            details = redacted_row["details"].copy()
            for nested_pii_key in ["user_agent", "raw_client_ip", "client_ip", "session_id"]:
                if nested_pii_key in details:
                    details[nested_pii_key] = "[REDACTED]"
            redacted_row["details"] = details
        redacted_rows.append(redacted_row)
    return redacted_rows
```

**AG Grid Filter/Sort Canonicalization:**
```python
# Canonical translation from AG Grid filter model to SQL WHERE clauses
# apps/execution_gateway/services/grid_query.py

# Server-side validation configuration per grid
GRID_QUERY_CONFIG = {
    "positions": {
        "table": "positions",
        "filterable_columns": {"symbol", "qty", "side", "strategy_id", "updated_at"},
        "sortable_columns": {"symbol", "qty", "side", "updated_at", "unrealized_pnl"},
        "column_types": {"symbol": str, "qty": int, "side": str, "updated_at": datetime},
    },
    "orders": {
        "table": "orders",
        "filterable_columns": {"symbol", "side", "status", "strategy_id", "created_at"},
        "sortable_columns": {"symbol", "side", "status", "created_at", "qty", "price"},
        "column_types": {"symbol": str, "qty": int, "price": float, "status": str},
    },
    "audit": {
        "table": "audit_log",
        "filterable_columns": {"action", "event_type", "outcome", "timestamp", "user_id"},
        "sortable_columns": {"timestamp", "action", "event_type", "outcome"},
        "column_types": {"timestamp": datetime, "action": str, "outcome": str},
        # IMPORTANT: Audit grid is SCOPED to order-related entries only
        # This aligns grid display with export behavior (both use JOIN-based scoping)
        # Non-order audit entries (system events, config changes) are NOT visible in grid
        "base_filter": "resource_type = 'order'",  # Injected into all audit grid queries
    },
}

# IMPORTANT: Audit Grid UI Alignment
# The audit grid displays ONLY order-related audit entries because:
# 1. Strategy scoping requires JOIN to orders table (audit_log has no strategy_id)
# 2. Export scoping filters to resource_type='order' for the same reason
# 3. Grid display MUST match export to avoid confusion (user sees X rows, exports X rows)
#
# Non-order audit entries (system/config events) are:
# - NOT visible in the web console audit grid
# - Accessible to admins via direct database query or admin dashboard (future)
# - Not subject to strategy scoping (no natural strategy affinity)

ALLOWED_FILTER_OPERATORS = {
    "equals", "notEqual", "contains", "startsWith", "endsWith",
    "greaterThan", "lessThan", "inRange", "blank", "notBlank",
}

ALLOWED_SORT_DIRECTIONS = {"asc", "desc"}

class GridQueryBuilder:
    """Canonical AG Grid filter/sort → SQL translation with strict validation."""

    FILTER_TYPE_MAP = {
        "equals": "= %s",
        "notEqual": "!= %s",
        "contains": "ILIKE %s",  # Wrap value with %%
        "startsWith": "ILIKE %s",  # Append %
        "endsWith": "ILIKE %s",  # Prepend %
        "greaterThan": "> %s",
        "lessThan": "< %s",
        "inRange": "BETWEEN %s AND %s",
    }

    def build_where_clause(self, filter_model: dict, grid_name: str) -> tuple[str, list]:
        """Convert AG Grid filterModel to SQL WHERE clause + params.

        SECURITY: Uses PARAMETERIZED QUERIES ONLY. Never interpolates values into SQL.

        Validation:
        - Reject filter columns not in GRID_QUERY_CONFIG[grid_name].filterable_columns
        - Reject filter operators not in ALLOWED_FILTER_OPERATORS
        - Coerce filter values to expected type per column_types
        - Log and reject malformed filters
        """

    def build_order_clause(self, sort_model: list[dict], grid_name: str) -> str:
        """Convert AG Grid sortModel to SQL ORDER BY.

        Validation:
        - Reject sort columns not in GRID_QUERY_CONFIG[grid_name].sortable_columns
        - Reject sort directions not in ALLOWED_SORT_DIRECTIONS ('asc'/'desc')
        - Use whitelist-based column names (never interpolate client strings)
        """

    def validate_columns(self, columns: list[str], grid_name: str) -> list[str]:
        """Validate requested columns against allowed list."""

    def _coerce_value(self, value: Any, expected_type: type) -> Any:
        """Coerce filter value to expected type, raise ValueError if invalid."""
```

**Query Builder Security Guarantees:**
1. **Parameterized queries only:** All values passed as `%s` parameters, never interpolated
2. **Column allowlists:** Only columns in `filterable_columns`/`sortable_columns` are accepted
3. **Operator allowlist:** Only operators in `ALLOWED_FILTER_OPERATORS` are accepted
4. **Type coercion:** Filter values coerced to expected types (prevents injection via type confusion)
5. **Whitelist-based ORDER BY:** Column names are matched against allowlist, not used directly
6. **Logging:** Unknown columns/operators are logged for security monitoring

**Excel Generation (Buffered with Row Streaming + PII Redaction):**
```python
# Use openpyxl write-only mode for memory efficiency during row processing
# Note: XLSX format requires buffering the full file before response
# apps/execution_gateway/routes/export.py

from openpyxl import Workbook
from tempfile import NamedTemporaryFile
from fastapi.responses import FileResponse

async def generate_excel(audit_id: str, user: AuthenticatedUser) -> FileResponse:
    # Fetch audit record + filter_params from export_audit table
    audit = await get_export_audit_record(audit_id)

    # CRITICAL: Validate columns against allowlist and get PII config
    grid_config = GRID_COLUMN_CLASSIFICATION.get(audit.grid_name, {})
    allowed_columns = grid_config.get("allowed_columns", [])
    pii_columns = grid_config.get("pii_columns", [])

    # Validate visible_columns from audit record
    export_columns = [c for c in audit.visible_columns if c in allowed_columns]
    if not export_columns:
        raise HTTPException(400, "No valid columns to export")

    # Create write-only workbook (streams rows to disk, not memory)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet()

    # Write header row
    ws.append(export_columns)

    # Stream rows from database in batches
    # CRITICAL: Query only allowed columns, apply PII redaction
    async for row_batch in stream_rows(
        filter_params=audit.filter_params,
        columns=export_columns,  # Only fetch allowed columns
        batch_size=1000
    ):
        for row in row_batch:
            # Apply PII redaction (same rules as grid + audit API)
            redacted_row = apply_pii_redaction([row], audit.grid_name, user)[0]
            # Apply formula sanitization
            sanitized = [sanitize_for_export(redacted_row.get(col)) for col in export_columns]
            ws.append(sanitized)

    # Save to temp file and return as FileResponse (true file streaming)
    tmp = NamedTemporaryFile(suffix=".xlsx", delete=False)
    wb.save(tmp.name)
    tmp.close()

    filename = f"{audit.grid_name}_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    return FileResponse(
        path=tmp.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        background=BackgroundTask(lambda: os.unlink(tmp.name))  # Cleanup after response
    )
```

**Server-Side Export PII Redaction (MANDATORY):**
- Excel exports query DB directly, bypassing grid's client-side PII handling
- MUST apply `apply_pii_redaction()` to every row before writing to Excel
- MUST validate `visible_columns` against `GRID_COLUMN_CLASSIFICATION[grid_name].allowed_columns`
- Non-admins get `[REDACTED]` for PII columns (ip_address, user_agent, user_id per grid config)
- PII visibility check: `Role.ADMIN` only (NOT permission-based - see PII Access Policy section)

**Memory Considerations:**
- openpyxl write-only mode streams rows to disk, avoiding memory buildup during row processing
- XLSX format requires full file before HTTP response (ZIP structure limitation)
- For very large exports (>10k rows), consider CSV-only or async background job with download link

**AG Grid Sanitizer Hooks (REQUIRED):**
```javascript
// In grid_export.js - MUST use these AG Grid hooks for sanitization
// Without these, default export paths bypass the sanitizer

const exportParams = {
    // CSV export - sanitize each cell
    processCellCallback: (params) => {
        return sanitizeForExport(params.value);
    },
    // Only export visible columns
    columnKeys: getVisibleColumnKeys(gridApi),
};

// Apply to grid API
gridApi.exportDataAsCsv(exportParams);

// Custom clipboard export (AG Grid Community lacks clipboard API)
async function copyToClipboard(gridApi) {
    // Get visible data as CSV string with sanitization
    const csvData = gridApi.getDataAsCsv({
        processCellCallback: (params) => sanitizeForExport(params.value),
        columnKeys: getVisibleColumnKeys(gridApi),
    });
    // Use Web Clipboard API
    await navigator.clipboard.writeText(csvData);
}
```

**Component Signature:**
```python
def create_grid_export_toolbar(
    grid_api_name: str,      # e.g., "_positionsGridApi"
    filename_prefix: str,    # e.g., "positions"
    column_defs: list[dict],
    include_clipboard: bool = True,
) -> ui.row
```

**Acceptance Criteria:**
- [ ] CSV export on all data grids (positions, orders, fills, history, hierarchical orders)
- [ ] Excel export via `/api/v1/export/excel/{audit_id}` with audit_id validation
- [ ] Copy to clipboard functional
- [ ] Exports include all visible columns + data
- [ ] **Permission check:** `Permission.EXPORT_DATA` enforced (reuse existing)
- [ ] **Audit logging:** ALL exports logged server-side with IP/session/UA via request context
- [ ] **Audit_id required:** Excel export requires valid audit_id from /api/v1/export/audit
- [ ] **Column-level access:** Export respects user's column visibility
- [ ] **Filter-aware:** Export respects active filters and sorting
- [ ] **Row limits:** Server-side computation + enforcement of max 10,000 rows
- [ ] **Formula injection:** Sanitize ALL export paths (CSV/clipboard/Excel)
- [ ] Include timestamp in export filename: `{prefix}_{YYYY-MM-DD_HH-MM}.csv`

**Files to Create:**
- `apps/web_console_ng/components/grid_export_toolbar.py`
- `apps/web_console_ng/static/js/grid_export.js` - Includes shared sanitizer
- `apps/execution_gateway/routes/export.py` - Export audit + Excel endpoints (gateway has DB access)
- `apps/execution_gateway/schemas/export.py` - Export request/response schemas

**Files to Modify:**
- `apps/web_console_ng/components/positions_grid.py` - Add export toolbar
- `apps/web_console_ng/components/orders_table.py` - Add export toolbar
- `apps/web_console_ng/components/tabbed_panel.py` - Add export to fills/history grids
- `apps/web_console_ng/components/hierarchical_orders.py` - Add export toolbar
- `apps/web_console_ng/pages/position_management.py` - Add export to position grid
- `apps/execution_gateway/main.py` - Register export router

---

### T8.1: Execution Quality (TCA) Dashboard - MEDIUM PRIORITY

**Goal:** Visualize how well orders were filled via REST API.

**Current State:**
- Backend has `libs/platform/analytics/execution_quality.py` (ExecutionQualityAnalyzer)
- ExecutionQualityAnalyzer requires `TAQLocalProvider` + `MicrostructureAnalyzer`
- TAQLocalProvider requires `ManifestManager` + `DatasetVersionManager` + data paths
- No API endpoints for TCA data
- No UI for TCA metrics

**Architecture Decision: API-Based TCA**
- **Approach:** Create TCA API endpoints in `execution_gateway`
- **Data Access:** Mount TAQ data volume read-only; initialize TAQLocalProvider with config
- **Async Safety:** Use `loop.run_in_executor(tca_executor, ...)` with bounded ThreadPoolExecutor
- **Concurrency Control:** Bounded executor (max 4 workers) + semaphore for backpressure

**MANDATORY: Thread Safety Verification**
Before using shared `ExecutionQualityAnalyzer` across threads, verify:
1. **TAQLocalProvider:** Read-only file access - generally thread-safe
2. **MicrostructureAnalyzer:** Check if it maintains mutable state between calls
3. **ExecutionQualityAnalyzer:** Check `analyze()` method for shared mutable state

**If NOT thread-safe:** Choose one of:
- **Option A (preferred):** Create analyzer instance per-request in executor:
  ```python
  def compute_tca_sync(fill_batch: FillBatch) -> TCAMetrics:
      # Create fresh analyzer in worker thread to avoid shared state
      analyzer = create_tca_analyzer()  # Factory function
      return analyzer.analyze(fill_batch)
  ```
- **Option B:** Protect shared analyzer with threading.Lock:
  ```python
  tca_lock = threading.Lock()
  def compute_tca_sync(fill_batch: FillBatch) -> TCAMetrics:
      with tca_lock:
          return tca_analyzer.analyze(fill_batch)
  ```

**Implementation task:** Add thread safety verification to T8.1 acceptance criteria

**MANDATED APPROACH: Option A (per-request instances)**
After analysis, Option A is REQUIRED as the safer default:
- ExecutionQualityAnalyzer likely uses immutable Polars DataFrames and Pydantic models
- TAQLocalProvider does read-only file access
- However, creating per-request instances eliminates any risk of shared mutable state
- Implementation PR MUST document thread safety verification findings

**TCA Concurrency Implementation:**
```python
# In apps/execution_gateway/app_context.py
from concurrent.futures import ThreadPoolExecutor

# Create bounded executor at startup (NOT the default executor)
TCA_MAX_WORKERS = int(os.getenv("TCA_MAX_WORKERS", "4"))
tca_executor = ThreadPoolExecutor(max_workers=TCA_MAX_WORKERS, thread_name_prefix="tca")

# In TCA route handler
async def compute_tca(fill_batch: FillBatch) -> TCAMetrics:
    loop = asyncio.get_running_loop()
    # Use explicit executor, NOT to_thread() which uses default executor
    return await loop.run_in_executor(tca_executor, tca_analyzer.analyze, fill_batch)

# Semaphore for additional backpressure (optional, since executor is bounded)
TCA_SEMAPHORE = asyncio.Semaphore(TCA_MAX_WORKERS)

async def handle_tca_request(...):
    async with TCA_SEMAPHORE:
        result = await compute_tca(fill_batch)
```
- **Caching:** Redis-backed cache (reuse existing Redis), 5-minute TTL expiration (no LRU; TTL ensures cleanup without maxmemory config)
- **Pagination:** Max 100 orders per page, max 30-day date range
- **Authorization:** Require `Permission.VIEW_TCA` (new) + `DatasetPermission.TAQ_ACCESS` + strategy scoping

**Cache Key Canonicalization:**
```python
import hashlib
import json

# Global: TAQ dataset version loaded at startup (invalidates cache on data updates)
# Set in app_context.py during TAQLocalProvider initialization
TAQ_DATASET_VERSION: str = ""  # e.g., "2026-01-31-001" from manifest

def build_tca_cache_key(
    user_id: str,
    strategies: list[str],
    filters: dict,
    dataset_version: str = None,
) -> str:
    """Deterministic cache key with version prefix and dataset version.

    Args:
        user_id: User identifier
        strategies: List of authorized strategy IDs
        filters: Query filters (date range, symbol, etc.)
        dataset_version: TAQ dataset version (defaults to global TAQ_DATASET_VERSION)

    Including dataset_version ensures cache invalidation when TAQ data is updated,
    preventing stale TCA results after data refreshes.
    """
    # Sort strategies for determinism
    sorted_strategies = sorted(strategies)
    # Canonicalize filters (sorted keys, stable JSON)
    canonical_filters = json.dumps(filters, sort_keys=True, separators=(',', ':'))
    # Include cache version for invalidation on schema changes
    cache_version = "v1"
    # Include dataset version to invalidate on TAQ data updates
    ds_version = dataset_version or TAQ_DATASET_VERSION or "unknown"
    payload = f"{cache_version}:{ds_version}:{user_id}:{sorted_strategies}:{canonical_filters}"
    key_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"tca:{key_hash}"

# In app_context.py - set TAQ_DATASET_VERSION at startup:
# from libs.data.manifest import ManifestManager
# TAQ_DATASET_VERSION = manifest_manager.get_latest_version("taq") or "unknown"

# Cache stampede protection using redis-lock
from redis.asyncio.lock import Lock

async def get_tca_with_lock(cache_key: str, compute_fn: Callable) -> TCAResponse:
    cached = await redis.get(cache_key)
    if cached:
        return TCAResponse.parse_raw(cached)

    # Acquire lock to prevent stampede (multiple concurrent computes)
    lock = Lock(redis, f"lock:{cache_key}", timeout=30)
    async with lock:
        # Double-check after lock acquired
        cached = await redis.get(cache_key)
        if cached:
            return TCAResponse.parse_raw(cached)

        # Compute and cache
        result = await compute_fn()
        await redis.setex(cache_key, 300, result.json())  # 5 min TTL
        return result
```

**TAQLocalProvider Initialization (Guarded/Lazy):**
```python
# Required dependencies for ExecutionQualityAnalyzer
from libs.data.data_providers.taq_query_provider import TAQLocalProvider
from libs.data.data_providers.manifest_manager import ManifestManager
from libs.data.data_providers.version_manager import DatasetVersionManager
from libs.platform.analytics.microstructure import MicrostructureAnalyzer

# Config paths (via env or defaults)
TAQ_DATA_PATH = os.getenv("TAQ_DATA_PATH", "data/taq")
TAQ_MANIFEST_PATH = os.getenv("TAQ_MANIFEST_PATH", "data/manifests/taq")
TAQ_SNAPSHOT_PATH = os.getenv("TAQ_SNAPSHOT_PATH", "data/snapshots/taq")

# Guarded initialization in app_context.py lifespan
# CRITICAL: Must not crash if TAQ data is unavailable
tca_analyzer: ExecutionQualityAnalyzer | None = None
tca_unavailable_reason: str | None = None

try:
    if not os.path.exists(TAQ_DATA_PATH):
        tca_unavailable_reason = f"TAQ data path not found: {TAQ_DATA_PATH}"
        logger.warning("TCA disabled: %s", tca_unavailable_reason)
    else:
        manifest_manager = ManifestManager(TAQ_MANIFEST_PATH)
        version_manager = DatasetVersionManager(TAQ_SNAPSHOT_PATH)
        taq_provider = TAQLocalProvider(
            data_path=TAQ_DATA_PATH,
            manifest_manager=manifest_manager,
            version_manager=version_manager,
        )
        micro_analyzer = MicrostructureAnalyzer(taq_provider)
        tca_analyzer = ExecutionQualityAnalyzer(taq_provider, micro_analyzer)
        logger.info("TCA analyzer initialized successfully")
except Exception as e:
    tca_unavailable_reason = f"TAQ initialization failed: {e}"
    logger.error("TCA disabled: %s", tca_unavailable_reason, exc_info=True)

# TCA endpoints check tca_analyzer before use:
# if tca_analyzer is None:
#     raise HTTPException(503, detail={"error": "TCA_UNAVAILABLE", "reason": tca_unavailable_reason})
```

**TCA Endpoint Guard:**
```python
# In apps/execution_gateway/routes/tca.py
def get_tca_analyzer() -> ExecutionQualityAnalyzer:
    """Dependency that returns TCA analyzer or raises 503."""
    from apps.execution_gateway.app_context import tca_analyzer, tca_unavailable_reason
    if tca_analyzer is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "TCA_UNAVAILABLE", "message": tca_unavailable_reason or "TCA service unavailable"}
        )
    return tca_analyzer
```

**FillBatch Construction from Database:**
```python
# Required DB queries (add to DatabaseClient)
def get_fills_by_client_order_id(client_order_id: str) -> list[dict]:
    """Query trades table for fills matching client_order_id."""

def get_order_with_fills(client_order_id: str) -> dict | None:
    """Get order + joined fills for TCA construction."""

def get_tca_eligible_orders(
    strategy_ids: list[str],
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Get orders eligible for TCA analysis.

    Filters:
    - strategy_id IN (strategy_ids)  # Strategy scoping
    - status = 'filled' OR status = 'partially_filled'  # Only orders with fills
    - submitted_at IS NOT NULL  # Must have submission timestamp
    - created_at BETWEEN start_date AND end_date
    """

def get_fills_for_tca(client_order_id: str) -> list[dict]:
    """Get fills for TCA analysis, excluding synthetic/superseded.

    CRITICAL: Must exclude synthetic and superseded fills to avoid:
    - Double-counting fills (superseded fills replaced by broker corrections)
    - Including reconciler-generated synthetic fills (not real executions)

    Filters:
    - client_order_id = client_order_id
    - synthetic = FALSE  # Exclude reconciler-generated fills
    - superseded = FALSE  # Exclude fills replaced by corrections
    """

# FillBatch field mapping:
# - decision_time: Use order.created_at (order creation timestamp)
#   Note: signal_timestamp doesn't exist in current schema; use created_at as decision time proxy
#   Future enhancement: Add signal_timestamp to orders table if precise signal timing needed
# - submission_time: order.submitted_at (when order was sent to broker)
# - total_target_qty: order.qty (original order quantity)
# - fills: from trades table joined on client_order_id
```

**TCA Timestamp Handling:**
```python
from datetime import datetime, timezone

def build_fill_batch(order: dict, fills: list[dict]) -> FillBatch:
    """Construct FillBatch from DB order/fills with UTC normalization.

    IMPORTANT: Caller MUST use get_fills_for_tca() which excludes:
    - synthetic fills (reconciler-generated, not real executions)
    - superseded fills (replaced by broker corrections)
    Failure to filter will cause double-counting and incorrect TCA metrics.

    IMPORTANT: ExecutionQualityAnalyzer enforces:
    - All timestamps must be UTC and timezone-aware
    - decision_time <= submission_time

    DB Field Mapping (trades table → Fill model):
    - trade_id → fill_id (broker-assigned fill ID)
    - broker_order_id → order_id (parent order ID)
    - client_order_id → client_order_id
    - executed_at → timestamp (UTC)
    - symbol → symbol
    - side → side
    - price → price
    - qty → quantity (NOTE: trades uses "qty", Fill uses "quantity")
    - exchange → exchange (optional)
    - liquidity → liquidity_flag (optional)
    - fee → fee_amount (default 0.0 if not persisted)
    """

    def to_utc(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        if dt.tzinfo is None:
            # Assume naive timestamps are UTC (DB stores UTC)
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    decision_time = to_utc(order["created_at"])
    submission_time = to_utc(order["submitted_at"])

    # Validate timestamps (ExecutionQualityAnalyzer will reject invalid)
    if decision_time is None or submission_time is None:
        raise ValueError(f"Order {order['client_order_id']} missing required timestamps")
    if decision_time > submission_time:
        # Use submission_time as decision_time fallback (shouldn't happen normally)
        decision_time = submission_time

    return FillBatch(
        symbol=order["symbol"],
        side=order["side"],
        decision_time=decision_time,
        submission_time=submission_time,
        total_target_qty=order["qty"],
        fills=[Fill(
            # Required identity fields
            fill_id=f["trade_id"],           # trades.trade_id → Fill.fill_id
            order_id=f["broker_order_id"],   # trades.broker_order_id → Fill.order_id
            client_order_id=f["client_order_id"],
            # Core fields
            timestamp=to_utc(f["executed_at"]),  # trades.executed_at → Fill.timestamp
            symbol=f["symbol"],
            side=f["side"],
            price=f["price"],
            quantity=f["qty"],               # trades.qty → Fill.quantity (name change!)
            # Optional venue details - NOT IN CURRENT SCHEMA
            # NOTE: trades table lacks exchange/liquidity/fee columns
            # These will use defaults; TCA venue metrics will be unavailable
            exchange=None,           # Would need schema migration to track
            liquidity_flag=None,     # Would need schema migration to track
            # Fees - NOT TRACKED IN CURRENT SCHEMA
            fee_amount=0.0,          # Fees not persisted in trades; IS metrics will exclude fees
            fee_currency="USD",
        ) for f in fills]

# NOTE: Current trades schema (0020_create_trades_table.sql) lacks:
# - exchange: venue where fill occurred
# - liquidity: maker/taker flag
# - fee/fee_currency: transaction fees
# TCA metrics will be approximate until schema is extended to track these fields.
    )
```

**API Endpoints:**
```
GET /api/v1/tca/analysis
    Query: start_date, end_date, symbol?, strategy_id?, page?, limit?
    Auth: VIEW_TCA + TAQ_ACCESS + strategy scoping
    Response: Paginated TCA metrics summary

GET /api/v1/tca/analysis/{client_order_id}
    Auth: VIEW_TCA + TAQ_ACCESS + strategy ownership
    Response: TCA metrics for specific order

GET /api/v1/tca/benchmarks
    Query: client_order_id, benchmark (vwap|twap)
    Auth: VIEW_TCA + TAQ_ACCESS + strategy ownership
    Response: Execution vs benchmark time series for charting
```

**TCA Strategy Scoping (MANDATORY Implementation):**
```python
# In apps/execution_gateway/routes/tca.py
from libs.platform.web_console_auth.permissions import get_authorized_strategies

@router.get("/analysis")
async def list_tca_analysis(
    start_date: date,
    end_date: date,
    strategy_id: str | None = Query(default=None),  # Client-supplied filter (optional)
    symbol: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, le=100),
    user: AuthenticatedUser = Depends(get_authenticated_user),
):
    """List TCA analysis with MANDATORY strategy scoping."""
    # STEP 1: Get user's authorized strategies (server-side, never trust client)
    authorized_strategies = get_authorized_strategies(user)
    if not authorized_strategies:
        raise HTTPException(403, "No authorized strategies")

    # STEP 2: If client provides strategy_id filter, INTERSECT with authorized set
    # This prevents users from requesting data for unauthorized strategies
    if strategy_id:
        if strategy_id not in authorized_strategies:
            raise HTTPException(403, f"Strategy '{strategy_id}' not authorized")
        # User requested specific authorized strategy - use that one only
        scoped_strategies = [strategy_id]
    else:
        # No filter - use all authorized strategies
        scoped_strategies = authorized_strategies

    # STEP 3: Use SCOPED strategies for DB query AND cache key
    # CRITICAL: Cache key uses scoped_strategies, NOT client-supplied filter
    cache_key = build_tca_cache_key(user.user_id, scoped_strategies, {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbol": symbol,
    })

    # STEP 4: Query uses scoped_strategies, not raw client input
    orders = await db.get_tca_eligible_orders(
        strategy_ids=scoped_strategies,  # CRITICAL: Use resolved set
        start_date=start_date,
        end_date=end_date,
    )
    # ... compute TCA for each order
```

**Data Flow:**
```
Web Console → TCA API (execution_gateway) → loop.run_in_executor(tca_executor, ...)
                                          → ExecutionQualityAnalyzer
                                          → TAQLocalProvider (data volume)
                                          → MicrostructureAnalyzer
```

**Metrics to Display:**
- Implementation Shortfall (bps)
- VWAP Slippage (bps)
- Timing Cost (bps)
- Market Impact (bps)
- Fill Rate (%)
- Total Fees

**UI Layout:**
```
┌──────────────────────────────────────────────────────┐
│ Execution Quality Dashboard               [Export]   │
├──────────────────────────────────────────────────────┤
│ Filters: [Date Range] [Symbol] [Strategy] [Apply]    │
├──────────────────────────────────────────────────────┤
│ Summary Cards:                                       │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐     │
│ │ Avg IS  │ │ VWAP    │ │ Market  │ │ Fill    │     │
│ │+2.3 bps │ │+1.1 bps │ │+0.8 bps │ │ 98.5%   │     │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘     │
├──────────────────────────────────────────────────────┤
│ Shortfall Decomposition Chart (stacked bar)          │
├──────────────────────────────────────────────────────┤
│ Execution vs VWAP Timeline (line chart)              │
├──────────────────────────────────────────────────────┤
│ Orders Table with TCA metrics         [Export CSV]   │
└──────────────────────────────────────────────────────┘
```

**Acceptance Criteria:**
- [ ] TCA API endpoints in execution_gateway with loop.run_in_executor(tca_executor, ...)
- [ ] TAQLocalProvider initialized with ManifestManager + DatasetVersionManager
- [ ] DatabaseClient methods for fills/orders query
- [ ] TCA page at `/execution-quality`
- [ ] Shortfall decomposition displayed
- [ ] Chart: execution price vs benchmark (VWAP) over time
- [ ] Filter by date range (max 30 days), symbol, strategy
- [ ] Pagination for large result sets (max 100 per page)
- [ ] Export to CSV (uses T8.3 component)
- [ ] Permission: `Permission.VIEW_TCA` (new) + `DatasetPermission.TAQ_ACCESS`
- [ ] Strategy scoping via `get_authorized_strategies()`

**Files to Create:**
- `apps/execution_gateway/routes/tca.py` - TCA API endpoints
- `apps/execution_gateway/schemas/tca.py` - TCA request/response schemas
- `apps/web_console_ng/pages/execution_quality.py` - TCA dashboard page
- `apps/web_console_ng/components/tca_chart.py` - TCA visualization component

**Files to Modify:**
- `apps/execution_gateway/main.py` - Register TCA router
- `apps/execution_gateway/app_context.py` - Add TAQLocalProvider, ManifestManager, DatasetVersionManager, MicrostructureAnalyzer initialization
- `apps/execution_gateway/api/dependencies.py` - Add TCA analyzer dependency
- `apps/execution_gateway/database.py` - Add `get_fills_by_client_order_id()`, `get_order_with_fills()`
- `apps/web_console_ng/pages/__init__.py` - Register execution_quality page
- `apps/web_console_ng/ui/layout.py` - Add nav link to `/execution-quality`
- `apps/web_console_ng/config.py` - Add `FEATURE_TCA_DASHBOARD` flag
- `infra/docker-compose.yml` - Mount `./data:/app/data:ro` for execution_gateway
- `libs/platform/web_console_auth/permissions.py` - Add `VIEW_TCA` permission + update ROLE_PERMISSIONS
- `libs/platform/web_console_auth/permissions.py` - Add `TAQ_ACCESS` to OPERATOR role in ROLE_DATASET_PERMISSIONS
- `libs/platform/web_console_auth/permissions.py` - Add public `is_admin()` helper (see below)

**New public helper in permissions.py:**
```python
def is_admin(user_or_role: Any) -> bool:
    """Check if user has admin role (public API for PII gating).

    Use this instead of internal _extract_role() for stable public API.
    """
    role = _extract_role(user_or_role)
    return role == Role.ADMIN

# Add to __all__ for stable public API:
# __all__ = [..., "is_admin"]
```

**Error Handling (Structured Response Envelope):**
```python
class TCAResponse(BaseModel):
    data: list[TCAMetrics] | TCAMetrics | None
    warnings: list[TCAWarning] = []  # Structured warnings
    errors: list[TCAError] = []      # Structured errors
    meta: TCAMeta                    # Pagination, totals

class TCAWarning(BaseModel):
    code: str          # e.g., "TAQ_DATA_PARTIAL", "BENCHMARK_MISSING"
    message: str       # Human-readable
    affected_orders: list[str] = []  # Which orders affected

class TCAError(BaseModel):
    code: str          # e.g., "INVALID_DATE_RANGE", "UNAUTHORIZED"
    message: str
```
**Error Handling Contract (CLARIFIED):**
- **TCA service unavailable** (analyzer not initialized at startup) → 503 Service Unavailable
  - Reason: TAQ data path missing, manifest unreadable, or init failure
  - Response: `{"code": "TCA_UNAVAILABLE", "message": "TCA service is disabled"}`
- **TAQ data partially unavailable** (specific dates/symbols missing) → 200 OK with warnings
  - Reason: Some orders can't compute TCA due to missing market data
  - Response: `{"data": [...], "warnings": [{"code": "TAQ_DATA_PARTIAL", ...}]}`
- Invalid date range (>30 days) → 400 Bad Request with structured error
- No authorized strategies → 403 Forbidden
- Missing TAQ_ACCESS → 403 Forbidden
- Timeout (>10s) → 504 Gateway Timeout with structured error

---

### T8.2: Order Entry Audit Trail - MEDIUM PRIORITY

**Goal:** Complete audit log for all order actions with compliance-ready exports.

**Current State:**
- `AuditLogger` exists with `log_action()`, `log_export()` methods
- `audit_log` table has `ip_address` and `session_id` columns (schema migration 0004)
- `AuditLogger._write()` does NOT currently use `ip_address` or `session_id` params
- `AuthenticatedUser` dataclass lacks `session_id` field
- Manual order actions logged in `execution_gateway/api/manual_controls.py`
- No way to capture client IP/User-Agent in current auth flow

**Architecture Decision: Capture Request Context**

1. **Extend AuthenticatedUser** to include session_id (from JWT `session_id` claim):
```python
@dataclass
class AuthenticatedUser:
    user_id: str
    role: Role | None
    strategies: list[str]
    session_version: int
    request_id: str
    session_id: str | None = None  # NEW: from JWT "session_id" claim
```

**GatewayAuthenticator Update:**
```python
# In libs/platform/web_console_auth/gateway_auth.py
# GatewayAuthenticator.authenticate() must extract session_id from JWT claims:
session_id = payload.get("session_id")  # Note: "session_id", not "sid"
# Then include in AuthenticatedUser construction
```

**Token Issuer Verification (session_id may already be present):**
```python
# In libs/platform/web_console_auth/jwt_manager.py or session_manager.py
# Service tokens may already include session_id claim.
#
# Implementation Task:
# 1. Verify existing create_service_token() includes session_id in claims
# 2. If NOT present, add: "session_id": user.session_id
# 3. If already present, no changes needed - just verify GatewayAuthenticator extracts it
#
# Expected claims structure:
claims = {
    "sub": user.user_id,
    "role": user.role.value if user.role else None,
    "strategies": user.strategies,
    "session_version": user.session_version,
    "session_id": user.session_id,  # Verify this exists or add it
    "iat": datetime.utcnow(),
    "exp": datetime.utcnow() + timedelta(minutes=5),
}
```

**Files to Modify (conditional):**
- `libs/platform/web_console_auth/jwt_manager.py` - Verify session_id in service token claims (add if missing)

2. **Capture IP/User-Agent from Request** in dependency:

**IMPORTANT:** Reuse existing trusted proxy infrastructure instead of creating new env vars:
- **Web Console:** Use `apps/web_console_ng/auth/client_ip.extract_trusted_client_ip()` with `config.TRUSTED_PROXY_IPS`
- **Execution Gateway:** Use `request.client.host` after `ProxyHeadersMiddleware` (configured via `TRUSTED_PROXY_HOSTS`)

**REQUIRED: Add RawPeerIPMiddleware BEFORE ProxyHeadersMiddleware in main.py:**
```python
# In apps/execution_gateway/main.py - add BEFORE ProxyHeadersMiddleware
class RawPeerIPMiddleware(BaseHTTPMiddleware):
    """Capture raw peer IP before ProxyHeadersMiddleware modifies request.client."""

    async def dispatch(self, request: Request, call_next):
        # Store raw peer IP in request.state for later trusted proxy validation
        request.state.raw_peer_ip = request.client.host if request.client else None
        return await call_next(request)

# IMPORTANT: FastAPI add_middleware uses LIFO order (last added = runs first in request chain)
# To ensure RawPeerIP runs BEFORE ProxyHeaders modifies request.client:
# - Add RawPeerIPMiddleware AFTER ProxyHeadersMiddleware in the code
# - This means RawPeerIP executes first in the request flow
#
# Example main.py middleware section:
#   app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXY_HOSTS)  # Added first, runs second
#   app.add_middleware(RawPeerIPMiddleware)  # Added second, runs FIRST (captures raw peer before ProxyHeaders)
```

```python
# In apps/execution_gateway/api/dependencies.py
from apps.execution_gateway.config import TRUSTED_PROXY_HOSTS
import ipaddress

def _is_from_trusted_proxy(request: Request) -> bool:
    """Check if request came from a trusted proxy using captured raw peer IP.

    Uses ipaddress module for proper CIDR/hostname matching, consistent with
    ProxyHeadersMiddleware's trust logic and web_console's auth/client_ip.py.
    """
    raw_peer_ip = getattr(request.state, "raw_peer_ip", None)
    if not raw_peer_ip:
        return False

    try:
        peer_addr = ipaddress.ip_address(raw_peer_ip)
    except ValueError:
        return False

    # TRUSTED_PROXY_HOSTS should be parsed into IP addresses/networks at startup
    # Check against all trusted hosts (supports CIDR notation)
    for trusted in TRUSTED_PROXY_HOSTS:
        try:
            if "/" in trusted:
                # CIDR notation
                if peer_addr in ipaddress.ip_network(trusted, strict=False):
                    return True
            else:
                # Single IP
                if peer_addr == ipaddress.ip_address(trusted):
                    return True
        except ValueError:
            continue  # Skip invalid entries
    return False

def get_request_context(request: Request) -> dict[str, str | None]:
    """Extract IP and User-Agent from request.

    NOTE: ProxyHeadersMiddleware processes X-Forwarded-For for trusted proxies.
    Use request.client.host for resolved IP, request.state.raw_peer_ip for proxy validation.
    """
    # After ProxyHeadersMiddleware, request.client.host is the resolved client IP
    client_ip = request.client.host if request.client else None

    # Only accept X-Original-User-Agent if request came from trusted proxy
    # This prevents untrusted callers from spoofing audit User-Agent
    is_from_trusted_proxy = _is_from_trusted_proxy(request)
    if is_from_trusted_proxy:
        user_agent = request.headers.get("X-Original-User-Agent") or request.headers.get("User-Agent")
    else:
        user_agent = request.headers.get("User-Agent")  # Ignore X-Original-User-Agent from untrusted

    # Normalize/truncate UA to prevent log bloat (max 500 chars)
    if user_agent and len(user_agent) > 500:
        user_agent = user_agent[:500] + "..."

    return {
        "client_ip": client_ip,  # Resolved client IP (after proxy chain)
        "user_agent": user_agent
    }
```

**Web Console → Execution Gateway IP/UA Forwarding:**

The web console (NiceGUI) makes API calls to execution_gateway on behalf of end users.
Without forwarding, audit logs would record web_console server's IP, not the user's.

**Integration Approach:** The existing `call_api_with_auth()` function is extended with
`forward_ip` and `forward_ua` parameters. Page handlers explicitly pass these from
NiceGUI session storage. This avoids thread-local/contextvars complexity.

**Context Propagation in NiceGUI (Concrete Implementation):**

**SECURITY NOTE: NiceGUI `app.storage.user` is SERVER-SIDE storage**
- Uses `app.storage.user` to align with existing session/auth patterns in `apps/web_console_ng/auth/middleware.py`
- Storage is backed by Redis or server memory (configurable via `storage_secret`)
- Clients CANNOT read or modify this storage directly
- Values are derived from server-observed request metadata only
- This is NOT browser localStorage/sessionStorage (which would be client-controlled)

```python
# In apps/web_console_ng/main.py
from nicegui import app
from starlette.requests import Request

# CRITICAL: IP/UA is extracted from server-observed Request object
# and stored in SERVER-SIDE session storage. Clients cannot forge these values.
# NOTE: Use app.storage.user to align with existing auth/session patterns

# IMPORTANT: Reuse existing extract_trusted_client_ip() - do NOT create new function
from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
from apps.web_console_ng import config

@app.on_connect
async def capture_context_on_connect(client, request: Request):
    """Capture user context when NiceGUI client connects.

    SECURITY: This handler runs SERVER-SIDE on every client connection.
    IP/UA are extracted from the actual HTTP request, not client-supplied data.
    Values are stored in server-side session storage (not accessible to client JS).

    NOTE: Uses app.storage.user (not client.storage.user) for consistency with
    existing auth/session cache in apps/web_console_ng/auth/middleware.py
    """
    # Extract real client IP using EXISTING helper (reuse, don't duplicate)
    # This uses config.TRUSTED_PROXY_IPS and right-to-left XFF traversal
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)

    # Store in NiceGUI's SERVER-SIDE per-session storage
    # app.storage.user is Redis/memory-backed, keyed by session token
    app.storage.user["_audit_context"] = {
        "client_ip": client_ip,
        "raw_client_ip": request.client.host if request.client else None,  # For security analysis
        "user_agent": request.headers.get("User-Agent"),
        "captured_at": datetime.utcnow().isoformat(),
        "source": "server_observed",  # Marker that this is server-derived, not client-supplied
    }

def get_audit_context() -> dict:
    """Get audit context for current NiceGUI session.

    Call this from any NiceGUI handler (page, button click, etc.)
    Returns server-derived context that cannot be forged by clients.

    NOTE: Uses app.storage.user for consistency with existing patterns.
    """
    return app.storage.user.get("_audit_context", {})

# Usage in page handlers that make API calls:
async def on_export_click():
    audit_ctx = get_audit_context()
    # Pass SERVER-DERIVED context to API client for AUDIT header forwarding
    # NOTE: forward_ip/forward_ua are for audit logging ONLY (X-Forwarded-For, X-Original-User-Agent headers)
    # Session binding in call_api_with_auth() uses separate client_ip/user_agent params
    # (sourced from the same app.storage.user context) - those remain unchanged
    api_response = await api_client.export_data(
        ...,
        forward_ip=audit_ctx.get("client_ip"),      # For audit logging in execution_gateway
        forward_ua=audit_ctx.get("user_agent"),     # For audit logging in execution_gateway
        # Session binding params (if using call_api_with_auth):
        # client_ip=audit_ctx.get("client_ip"),     # For session validation (same source)
        # user_agent=audit_ctx.get("user_agent"),   # For session validation (same source)
    )
```

**Anti-Spoofing Guarantees:**
1. IP/UA extracted from server-observed `Request` object (Starlette/ASGI)
2. Stored in server-side storage (Redis-backed, not client-accessible)
3. X-Forwarded-For only trusted from configured trusted proxy IPs (TRUSTED_PROXY_IPS/TRUSTED_PROXY_HOSTS)
4. `source: "server_observed"` marker for audit verification
5. Execution gateway validates X-Forwarded-For via ProxyHeadersMiddleware (TRUSTED_PROXY_HOSTS)

**API Client Header Forwarding:**
```python
# In libs/platform/web_console_auth/api_client.py
# Existing pattern uses call_api_with_auth() function - extend it to forward IP/UA

async def call_api_with_auth(
    url: str,
    method: str = "GET",
    session_id: str | None = None,
    session_store: RedisSessionStore | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    forward_ip: str | None = None,    # NEW: End-user IP to forward
    forward_ua: str | None = None,    # NEW: End-user User-Agent to forward
    **kwargs: Any,
) -> httpx.Response:
    # ... existing token fetch and validation ...

    headers = kwargs.get("headers", {})
    headers["Authorization"] = f"Bearer {access_token}"

    # Forward end-user context (set by NiceGUI page handlers)
    # execution_gateway trusts web_console as proxy via TRUSTED_PROXY_HOSTS
    if forward_ip:
        headers["X-Forwarded-For"] = forward_ip
    if forward_ua:
        headers["X-Original-User-Agent"] = forward_ua

    kwargs["headers"] = headers
    # ... make request ...
```

**Note on NiceGUI Context Propagation:**
- `@app.on_connect` is the authoritative hook for capturing IP/UA
- NiceGUI passes the Request object to on_connect handlers
- Context is stored in `app.storage.user` (server-side per-session storage, not client-accessible)
- Page handlers call `get_audit_context()` and pass to API client explicitly
- This works for all NiceGUI interactions (HTTP, websocket, background tasks)

**Files to Modify (additional):**
- `libs/platform/web_console_auth/api_client.py` - Add X-Forwarded-For/X-Original-User-Agent forwarding
- `apps/web_console_ng/main.py` - Add middleware to capture user context

**Audit Context Storage:**
- `ip_address` column: Stores resolved `client_ip` (from X-Forwarded-For if trusted, else direct)
- `details` JSON field: Stores `user_agent` for security analysis
  - **NiceGUI context:** Also stores `raw_client_ip` (captured before any proxy processing)
  - **Execution Gateway:** Only stores resolved `client_ip` (raw not available after ProxyHeadersMiddleware)
```python
# From NiceGUI context (has raw_client_ip available):
details = {
    "raw_client_ip": context.get("raw_client_ip"),  # Only in NiceGUI context
    "user_agent": context["user_agent"],
    "request_id": request_id,  # For correlation
    ...other_details
}
```

3. **Enhance AuditLogger methods** to accept IP/session:

**Update _write() signature:**
```python
async def _write(
    self,
    *,
    user_id: str | None,
    action: str,
    event_type: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    amr_method: str | None = None,
    ip_address: str | None = None,      # NEW
    session_id: str | None = None,      # NEW
) -> None:
    # ... INSERT now includes ip_address, session_id columns
```

**Update public methods** (log_action, log_access, log_export) to accept and pass ip_address/session_id:
```python
# Example: log_action() - similar pattern for log_access, log_export
async def log_action(
    self,
    user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,      # NEW
    session_id: str | None = None,      # NEW
) -> None:
    await self._write(
        user_id=user_id,
        action=action,
        event_type="action",
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        details=details,
        ip_address=ip_address,           # Pass through
        session_id=session_id,           # Pass through
    )
```

**Call site update** (manual_controls.py and other order endpoints):
```python
# Get audit context from request (see Component 4 below)
audit_ctx = get_audit_context(request)

def build_audit_details(audit_ctx: dict, **extra) -> dict:
    """Build audit details dict with required fields.

    MANDATORY: Always include user_agent to ensure complete audit trails.

    NOTE: raw_client_ip is ONLY available in NiceGUI web console context where
    the original client IP is captured before the web_console→gateway proxy hop.
    In execution_gateway direct calls (e.g., CLI, external API clients), this
    field will be absent. This is intentional - gateway's client_ip (passed to
    ip_address column) is the resolved IP after ProxyHeadersMiddleware.
    """
    details = {
        "user_agent": audit_ctx.get("user_agent"),  # REQUIRED: always capture UA
        # raw_client_ip: Only present in NiceGUI context (forwarded via X-Forwarded-For)
        # Gateway calls won't have this - use ip_address column for resolved IP
    }
    # Include raw_client_ip only if present in audit_ctx (NiceGUI path only)
    if audit_ctx.get("raw_client_ip"):
        details["raw_client_ip"] = audit_ctx["raw_client_ip"]
    details.update(extra)
    return details

# NOTE: session_id comes from authenticated user (JWT claim), NOT from audit_ctx
# audit_ctx only stores request-observable data (IP, User-Agent)
await audit_logger.log_action(
    user_id=user.user_id,
    action="order_submitted",
    resource_type="order",
    resource_id=client_order_id,
    details=build_audit_details(audit_ctx, symbol=symbol, qty=qty),  # REQUIRED: use helper
    ip_address=audit_ctx["client_ip"],
    session_id=user.session_id,  # From AuthenticatedUser (JWT claim)
)
```

**Audit Query Endpoint:**
```
GET /api/v1/orders/{client_order_id}/audit
    Query Params:
      - limit: int (default 50, max 200) - number of entries per page
      - cursor: str | null - opaque cursor for pagination (base64-encoded "timestamp:id")
      - order: "asc" | "desc" (default "desc") - timestamp ordering
    Auth: VIEW_AUDIT + strategy ownership
    Response: AuditTrailResponse (list of AuditEntry, paginated)
```

**Audit Pagination Contract:**
- **Cursor-based pagination:** More reliable than offset for long-lived orders with frequent updates
- **Cursor format:** Base64-encoded `{timestamp_iso}:{id}` (e.g., `MjAyNi0wMS0zMVQxMDozMDowMFo6MTIzNA==`)
- **Ordering:** Primary by `timestamp` (asc/desc), secondary by `id` (tie-breaker for same-millisecond events)
- **Default limit:** 50 entries (most orders have <50 audit events)
- **Max limit:** 200 entries (prevents abuse)
- **Index requirement:** `idx_audit_log_resource` on `(resource_type, resource_id, timestamp DESC, id DESC)`

**Audit Query Scoping (Strategy Ownership):**
```python
# Strategy ownership check for audit access
async def get_order_audit(
    client_order_id: str,
    user: AuthenticatedUser,
    limit: int = 50,
    cursor: str | None = None,
    order: str = "desc",
):
    # Validate and cap limit
    limit = min(max(1, limit), 200)

    # 1. Fetch order to get strategy_id
    order_record = await db.get_order_by_client_order_id(client_order_id)
    if not order_record:
        raise HTTPException(404, "Order not found")

    # 2. Check user has access to this strategy
    authorized = get_authorized_strategies(user)
    if order_record.strategy_id not in authorized:
        raise HTTPException(403, "Access denied to this order's strategy")

    # 3. Decode cursor if present
    cursor_timestamp, cursor_id = None, None
    if cursor:
        cursor_timestamp, cursor_id = decode_audit_cursor(cursor)

    # 4. Fetch audit entries with pagination
    entries, total_count = await db.get_audit_by_resource_id(
        resource_type="order",
        resource_id=client_order_id,
        limit=limit + 1,  # Fetch one extra to detect has_more
        cursor_timestamp=cursor_timestamp,
        cursor_id=cursor_id,
        order=order,
    )

    # 5. Build response with next_cursor
    has_more = len(entries) > limit
    if has_more:
        entries = entries[:limit]
    next_cursor = encode_audit_cursor(entries[-1]) if has_more else None

    return AuditTrailResponse(
        client_order_id=client_order_id,
        entries=entries,
        total_count=total_count,
        next_cursor=next_cursor,
        has_more=has_more,
    )
```

**Response Schema (apps/execution_gateway/schemas/audit.py):**
```python
class AuditEntry(BaseModel):
    id: int
    timestamp: datetime
    user_id: str  # NOT NULL in DB; use "system" for automated actions
    action: str
    event_type: str
    outcome: str
    details: dict[str, Any] = {}  # Default empty dict; DB is NOT NULL with JSONB default
    ip_address: str | None  # Redacted server-side for non-admins
    session_id: str | None  # Redacted server-side for non-admins

class AuditTrailResponse(BaseModel):
    client_order_id: str
    entries: list[AuditEntry]
    total_count: int
    next_cursor: str | None = None  # Opaque cursor for next page
    has_more: bool = False          # True if more pages available
```

**Server-Side PII Redaction (MANDATORY for API):**
```python
# In apps/execution_gateway/routes/orders.py - audit endpoint
# PII redaction MUST happen server-side, not just UI
def redact_audit_pii(entries: list[dict], user: AuthenticatedUser) -> list[dict]:
    """Redact PII fields in audit entries for non-admin users.

    CRITICAL: This is the security boundary. UI masking is defense-in-depth only.
    API callers can bypass UI, so server-side redaction is mandatory.

    POLICY: Use explicit Role.ADMIN check, NOT permission-based.
    This is consistent with apply_pii_redaction() in export path.
    """
    from libs.platform.web_console_auth.permissions import Role, is_admin
    # Use public helper (not private _extract_role) for stable API
    if is_admin(user):
        return entries  # Only admin role sees all PII

    # Non-admins get redacted PII
    # CONSISTENT WITH GRID PII POLICY: user_id treated as PII until verified non-identifying
    for entry in entries:
        entry["ip_address"] = "[REDACTED]" if entry.get("ip_address") else None
        entry["session_id"] = "[REDACTED]" if entry.get("session_id") else None
        # user_id: DEFAULT to PII (consistent with grid classification)
        # Remove this line after verifying user_id is non-identifying UUID
        entry["user_id"] = "[REDACTED]" if entry.get("user_id") else None
        # Also redact PII from details JSON if present
        if entry.get("details"):
            if "user_agent" in entry["details"]:
                entry["details"]["user_agent"] = "[REDACTED]"
            if "raw_client_ip" in entry["details"]:
                entry["details"]["raw_client_ip"] = "[REDACTED]"
            # user_id may also be in details for some audit events
            if "user_id" in entry["details"]:
                entry["details"]["user_id"] = "[REDACTED]"
    return entries

# Apply in endpoint BEFORE returning response
@router.get("/orders/{client_order_id}/audit")
async def get_order_audit(
    client_order_id: str,
    user: AuthenticatedUser = Depends(get_authenticated_user),
):
    # ... fetch entries ...
    entries = redact_audit_pii(entries, user)  # Server-side redaction
    return AuditTrailResponse(client_order_id=client_order_id, entries=entries, ...)
```

**Audit user_id Convention:**
- DB constraint: `user_id NOT NULL` (per migration 0004)
- For system-originated actions (fills, webhook callbacks): use `user_id = "system"`
- For user actions: use actual user_id from AuthenticatedUser
- Response schema reflects DB reality: `user_id: str` (not nullable)

**Audit Panel UI:**
```
┌─────────────────────────────────────────────┐
│ Order: abc123...                    [Close] │
├─────────────────────────────────────────────┤
│ Audit Trail:                      [Export]  │
│ ┌─────────────────────────────────────────┐ │
│ │ 10:30:00 │ SUBMITTED │ user@example    │ │
│ │ IP: 192.168.1.1 | Session: sess_abc    │ │  ← Admin only
│ ├─────────────────────────────────────────┤ │
│ │ 10:31:00 │ FILLED    │ system          │ │
│ │ Fill: 150.85 @ 100 shares              │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

**UI PII Masking (Consistent with Export Redaction):**
```python
# In order_audit_panel.py - UI rendering applies same rules as export
def render_audit_entry(entry: AuditEntry, user: AuthenticatedUser) -> ui.row:
    """Render audit entry with PII masking for non-admins.

    POLICY: Use explicit Role.ADMIN check, consistent with:
    - apply_pii_redaction() in export path
    - redact_audit_pii() in audit API
    """
    from libs.platform.web_console_auth.permissions import is_admin as check_is_admin
    user_is_admin = check_is_admin(user)  # Use public helper, not _extract_role

    with ui.row():
        ui.label(entry.timestamp.strftime("%H:%M:%S"))
        ui.label(entry.action)
        ui.label(entry.user_id or "system")

        # PII fields only visible to Role.ADMIN
        if user_is_admin:
            ui.label(f"IP: {entry.ip_address or 'N/A'}")
            ui.label(f"Session: {entry.session_id or 'N/A'}")
        else:
            # Non-admins see redacted placeholder
            ui.label("IP: [REDACTED]")
            ui.label("Session: [REDACTED]")
```

**PII Access Policy (UNIFIED):**
- **Single policy:** Use explicit `Role.ADMIN` check everywhere (NOT permission-based)
- **Applied in:**
  1. `apply_pii_redaction()` - export/grid data
  2. `redact_audit_pii()` - audit API response
  3. `render_audit_entry()` - audit UI display
- Non-admins see `[REDACTED]` for IP/User-Agent/session in all paths
- This prevents accidental PII exposure if VIEW_ALL_STRATEGIES is granted to non-admin roles
- Future: Consider dedicated `VIEW_AUDIT_PII` permission if finer-grained control needed

**Order Action Inventory (All Paths Requiring Audit):**

**User-Initiated Actions:**
| Action | File Path | Function | Current Audit | T8.2 Changes |
|--------|-----------|----------|---------------|--------------|
| Manual Submit | `api/manual_controls.py` | `submit_manual_order()` | Yes (AuditLogger) | Add IP/session/UA |
| Manual Cancel | `api/manual_controls.py` | `cancel_order()` | Yes (AuditLogger) | Add IP/session/UA |
| Manual Modify | `api/manual_controls.py` | `modify_order()` | Yes (AuditLogger) | Add IP/session/UA |
| API Submit | `routes/orders.py` | `create_order()` | Partial | Add full audit with IP/session/UA |
| API Cancel | `routes/orders.py` | `cancel_order()` | Partial | Add full audit with IP/session/UA |
| API Modify | `routes/orders.py` | `update_order()` | Partial | Add full audit with IP/session/UA |
| Flatten All | `api/manual_controls.py` | `flatten_all()` | Yes (AuditLogger) | Add IP/session/UA |
| Close Position | `api/manual_controls.py` | `close_position()` | Yes (AuditLogger) | Add IP/session/UA |

**Broker Lifecycle Events (System-Originated):**
| Action | File Path | Function | Current Audit | T8.2 Changes |
|--------|-----------|----------|---------------|--------------|
| Fill/Partial Fill | `services/webhook_handler.py` | `handle_fill()` | Partial (logs only) | Add full audit: user_id="system" |
| Order Rejected | `services/webhook_handler.py` | `handle_rejection()` | None | Add audit with rejection reason |
| Order Expired | `services/webhook_handler.py` | `handle_expiration()` | None | Add audit with expiration details |
| Broker Cancel | `services/webhook_handler.py` | `handle_cancel()` | Partial | Add audit with cancel reason |
| Reconciler Adjustment | `services/reconciler.py` | `heal_position()` | Partial | Add audit with discrepancy details |

**Implementation Checklist:**
- [ ] Audit all 8 user-initiated action paths with IP/session/UA
- [ ] Audit all 5 broker lifecycle events with user_id="system"
- [ ] Create shared `log_order_action()` helper to ensure consistency
- [ ] Verify routes/orders.py has full audit coverage (not just logging)
- [ ] Add webhook_handler.py and reconciler.py to Files to Modify

**Acceptance Criteria:**
- [ ] Extend AuthenticatedUser with session_id field
- [ ] Create helper to capture IP/User-Agent from Request
- [ ] Enhance AuditLogger._write() to persist ip_address/session_id
- [ ] Update manual_controls.py to pass IP/session/UA to audit calls
- [ ] Update routes/orders.py to pass IP/session/UA to audit calls
- [ ] Log all order submissions with user, timestamp, IP, session, User-Agent
- [ ] Log modifications and cancellations
- [ ] API endpoint to query audit by order with pagination
- [ ] Response schema for audit entries
- [ ] Display audit trail in order details panel
- [ ] Export capability for compliance (uses T8.3 component)
- [ ] Permission: `Permission.VIEW_AUDIT` (already exists) + strategy ownership

**Files to Create:**
- `apps/web_console_ng/components/order_audit_panel.py` - Audit trail display
- `apps/execution_gateway/schemas/audit.py` - Audit query response schemas

**Files to Modify:**
- `libs/platform/web_console_auth/gateway_auth.py` - Add session_id to AuthenticatedUser
- `libs/platform/web_console_auth/audit_logger.py` - Add ip_address/session_id params to _write()
  (Note: execution_gateway imports audit_logger.py, not audit_log.py)
- `apps/execution_gateway/api/dependencies.py` - Add get_request_context() helper
- `apps/execution_gateway/api/manual_controls.py` - Pass IP/session/UA to audit calls
- `apps/execution_gateway/routes/orders.py` - Add `GET /orders/{id}/audit` endpoint
- `apps/execution_gateway/database.py` - Add `get_audit_by_resource_id()` query
- `apps/web_console_ng/components/orders_table.py` - Add audit trail button/modal

---

## Dependencies

```
P6T6.2 TWAP ──> T8.1 TCA (analyze TWAP execution quality)
T8.3 Export ──> T8.1 TCA Dashboard (export functionality)
T8.3 Export ──> T8.2 Audit Trail (export functionality)
```

**Additional Dependencies:**
- Order/fill persistence in database (existing)
- TAQ data availability for TCA benchmarks (data/taq directory)
- ManifestManager + DatasetVersionManager for TAQ provider
- Docker volume configuration for execution_gateway data access

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| TCA Analysis | `libs/platform/analytics/execution_quality.py` | Call via API |
| TCA API | `apps/execution_gateway/routes/tca.py` | New endpoints |
| TAQ Provider | `libs/data/data_providers/taq_query_provider.py` | Initialize in app_context |
| Order Audit Logger | `libs/platform/web_console_auth/audit_logger.py` | T8.2: Enhance _write() for IP/session |
| Export Audit (T8.3) | `export_audit` table + `/api/v1/export/audit` | T8.3: Use `export_audit` table (NOT AuditLogger) |
| Legacy Export Audit | `AuditLogger.log_export()` | Legacy pages (e.g., journal.py) - unchanged |
| Permissions | `libs/platform/web_console_auth/permissions.py` | Add VIEW_TCA + VIEW_AUDIT to OPERATOR |
| Strategy Scoping | `get_authorized_strategies()` | Enforce on TCA/audit queries |

**Export Audit Clarification:**
- **T8.3 grid exports:** Use NEW `export_audit` table with lifecycle (pending→completed)
- **Legacy exports (journal.py, etc.):** Continue using `AuditLogger.log_export()` → `audit_log` table
- **Do NOT double-log:** T8.3 exports write to `export_audit` only, NOT both tables
- **Future migration:** Consider migrating legacy exports to `export_audit` in follow-up task

---

## Permissions Model

| Permission | Status | Used By |
|------------|--------|---------|
| `EXPORT_DATA` | Exists | T8.3 |
| `VIEW_AUDIT` | Exists | T8.2 |
| `VIEW_TCA` | **NEW** | T8.1 |
| `TAQ_ACCESS` (dataset) | Exists | T8.1 (add to OPERATOR role) |

**Role Updates:**
```python
# In ROLE_PERMISSIONS
Role.OPERATOR: {
    # ... existing ...
    Permission.VIEW_TCA,    # NEW for T8.1
    Permission.VIEW_AUDIT,  # ADD for T8.2 (operators need audit trail access)
}

# In ROLE_DATASET_PERMISSIONS
Role.OPERATOR: {
    # ... existing ...
    DatasetPermission.TAQ_ACCESS,  # ADD if not present
}
```

**Note on VIEW_AUDIT:** Currently `VIEW_AUDIT` is only granted to `Role.ADMIN`. For T8.2 to be useful, operators must be able to view audit trails for orders they have access to (strategy-scoped). The implementation must:
1. Add `Permission.VIEW_AUDIT` to `Role.OPERATOR` in `ROLE_PERMISSIONS`
2. **Single-order audit endpoint** (`GET /orders/{id}/audit`): Uses two-step validation (see `get_order_audit()` above)
   - Step 1: Fetch order by client_order_id to get strategy_id
   - Step 2: Check user has access to that strategy via `get_authorized_strategies()`
   - Step 3: If authorized, query audit_log by resource_id (no JOIN needed)
   - This is the correct approach for the order detail audit panel.

---

## Testing Strategy

### Unit Tests
- T8.3: Export functions, permission checks, row limit enforcement, formula sanitization (strings only, not negative numbers), audit endpoint
- T8.1: TCA API response formatting, FillBatch construction, caching (Redis), async offload with semaphore, structured warnings
- T8.2: AuditLogger ip_address/session_id persistence, request context with trusted proxy validation

### Integration Tests
- T8.3: Grid + export + audit logging integration, Excel generation
- T8.1: TCA calculation with real fill data, pagination, TAQ provider initialization
- T8.2: Full order lifecycle audit trail, IP/session capture from Request

### E2E Tests
- T8.3: Export file content matches grid display, permission denied for unauthorized
- T8.1: Dashboard renders, filters work, charts display, pagination works
- T8.2: Audit panel displays correctly, export works

### Security Tests
- Export without `EXPORT_DATA` permission → 403
- Excel export without valid audit_id → 404
- Excel export with other user's audit_id → 403
- Row limit exceeded (>10,000) → allowed=false from audit endpoint
- TCA access without `VIEW_TCA` permission → 403
- TCA access without `TAQ_ACCESS` dataset permission → 403
- TCA access to non-owned strategy → 403
- Audit access without `VIEW_AUDIT` permission → 403
- Audit access to non-owned order (strategy check) → 403
- Export audit logging includes IP/session/UA (server-side for ALL types)
- Formula injection in CSV export (string "-formula") → sanitized with leading quote
- Formula injection in clipboard export → sanitized with leading quote
- Formula injection in Excel export → sanitized with leading quote
- Formula injection with leading whitespace (" =SUM(1,2)") → sanitized with leading quote
- Formula injection with control chars ("\t=cmd") → sanitized with leading quote
- Negative number (-123.45) in export → NOT sanitized (preserved as number)
- Trusted proxy IP parsing: untrusted proxy X-Forwarded-For → use raw_client_ip
- TCA concurrency: 5+ concurrent requests → semaphore limits to 4

### Performance Tests
- TCA with 30-day range → completes within 10s (loop.run_in_executor offload)
- Export with 10,000 rows → completes within 5s

---

## Database Changes

**Migration Required: export_audit table + audit_log index**

**Migration File:** `db/migrations/XXXX_add_export_audit_and_index.sql`
```sql
-- Required for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- T8.3: Export audit table (separate lifecycle from audit_log)
CREATE TABLE IF NOT EXISTS export_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,  -- TEXT for parity with audit_log
    export_type VARCHAR(20) NOT NULL CHECK (export_type IN ('csv', 'excel', 'clipboard')),
    grid_name VARCHAR(100) NOT NULL,
    filter_params JSONB,
    visible_columns JSONB,  -- List of columns to export (for audit reproducibility)
    sort_model JSONB,  -- AG Grid sort model (for reproducibility)
    strategy_ids JSONB,  -- Server-injected strategy scope (for compliance reproducibility)
    export_scope VARCHAR(20) NOT NULL DEFAULT 'visible' CHECK (export_scope IN ('visible', 'full')),
    estimated_row_count INTEGER,
    actual_row_count INTEGER,
    reported_by VARCHAR(10) CHECK (reported_by IN ('client', 'server')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed', 'expired')),
    ip_address TEXT,  -- TEXT for consistency with audit_log; validate at app layer
    session_id TEXT,  -- TEXT for parity with audit_log
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completion_time TIMESTAMPTZ,
    error_message TEXT
);
CREATE INDEX idx_export_audit_user ON export_audit(user_id, created_at DESC);
-- Index for retention/expiry cleanup jobs (filter by created_at and status)
CREATE INDEX idx_export_audit_cleanup ON export_audit(status, created_at);

-- T8.2: Index for audit_log resource_id queries (order audit trail)
-- IMPORTANT: Include `id` for cursor pagination tie-breaker (same-millisecond events)
CREATE INDEX IF NOT EXISTS idx_audit_log_resource
ON audit_log(resource_type, resource_id, timestamp DESC, id DESC);
```

**Existing schema (no changes needed):**
- TCA: Computed on-demand from `trades` table + TAQ data
- Audit: `audit_log` table already has `ip_address` and `session_id` columns (migration 0004)
- User-Agent: Stored in `details` JSONB field (no migration needed)

**New DB Methods (DatabaseClient):**
```python
def get_fills_by_client_order_id(self, client_order_id: str) -> list[dict]
def get_order_with_fills(self, client_order_id: str) -> dict | None

# T8.2: Audit query with cursor pagination
def get_audit_by_resource_id(
    self,
    resource_type: str,
    resource_id: str,
    limit: int = 50,
    cursor_timestamp: datetime | None = None,
    cursor_id: int | None = None,
    order: str = "desc",  # "asc" or "desc"
) -> tuple[list[dict], int]:
    """Fetch audit entries with cursor pagination.

    Returns (entries, total_count).
    Query uses idx_audit_log_resource index for efficient pagination.
    """

# T8.3: Export audit methods
def create_export_audit(self, audit: ExportAuditCreate) -> str  # Returns audit_id (UUID)
def get_export_audit(self, audit_id: str) -> ExportAudit | None
def complete_export_audit(self, audit_id: str, success: bool, actual_row_count: int, error_message: str | None = None) -> bool
```

---

## Infrastructure Changes

**All Docker Compose Files to Update:**
- `infra/docker-compose.yml` (primary)
- `docker-compose.yml` (root, if exists)
- `infra/docker-compose.ci.yml` (CI environment)

**Changes for execution_gateway service:**
```yaml
execution_gateway:
  volumes:
    - ./data:/app/data:ro  # Read-only TAQ data access for TCA
  environment:
    - TAQ_DATA_PATH=/app/data/taq
    - TAQ_MANIFEST_PATH=/app/data/manifests/taq
    - TAQ_SNAPSHOT_PATH=/app/data/snapshots/taq
    # TRUSTED_PROXY_HOSTS: existing env var for ProxyHeadersMiddleware
    # Default is empty/localhost - configure for production
    # CRITICAL: Must match web_console_ng setting for server-side enforcement
    - EXPORT_STRICT_AUDIT_MODE=false
```

**Changes for web_console_ng service:**
```yaml
web_console_ng:
  environment:
    # TRUSTED_PROXY_IPS: existing env var used by auth/client_ip.py
    # SECURITY: Restrictive default - production MUST configure explicitly
    # Optional: Enable strict audit mode (Excel-only export)
    - EXPORT_STRICT_AUDIT_MODE=false
```

**Trusted Proxy Configuration (EXISTING - no new env vars needed):**
- **execution_gateway:** Uses existing `TRUSTED_PROXY_HOSTS` env var via `ProxyHeadersMiddleware` (main.py:385-389)
- **web_console_ng:** Uses existing `TRUSTED_PROXY_IPS` env var via `config.py` and `auth/client_ip.py`
- Both services already have trusted proxy infrastructure - just ensure they're configured identically

**Trusted Proxy Deployment Requirements:**

**CRITICAL:** `execution_gateway.TRUSTED_PROXY_HOSTS` MUST include `web_console_ng` service IP to accept forwarded IP/UA headers.

| Environment | execution_gateway (`TRUSTED_PROXY_HOSTS`) | web_console_ng (`TRUSTED_PROXY_IPS`) |
|-------------|------------------------------------------|--------------------------------------|
| Local dev | Default (localhost) | Default (localhost) |
| Docker Compose | Docker gateway + web_console_ng IP | Docker gateway IP |
| Kubernetes | Ingress IPs + web_console_ng pod CIDR | Ingress controller IPs |
| Production | Load balancer IPs + web_console_ng IPs | Load balancer IPs |

**Why web_console_ng must be trusted by execution_gateway:**
- Web console forwards end-user IP/UA via X-Forwarded-For and X-Original-User-Agent headers
- Without trusting web_console, execution_gateway ignores these headers and logs web_console server IP
- This breaks audit integrity - all actions would appear to come from the same internal IP

**SECURITY WARNING:**
- NEVER use broad CIDRs like `10.0.0.0/8` in production
- Broad trusted proxy lists allow any internal host to spoof X-Forwarded-For
- This defeats audit trail integrity for compliance logging
- Always specify explicit, known proxy IPs

**TAQ Data Availability Handling:**
- If TAQ data directory missing → Log warning, TCA endpoints return 503 with structured error
- If TAQ data partial (missing dates/symbols) → Return 200 with warnings in response envelope
- Startup health check verifies TAQ manifest readable

---

## Key Technical Decisions

1. **Export Approach**: Client-side CSV/clipboard, server-side Excel
   - AG Grid Community for CSV (Enterprise not available)
   - Server-side `openpyxl` for Excel with formula injection sanitization

2. **TCA Architecture**: API in execution_gateway with async offload
   - On-demand computation with `loop.run_in_executor(tca_executor, ...)` for CPU-bound work
   - 5-minute caching keyed by user/strategies/filters
   - Max 30-day range, 100 orders per page

3. **Audit Storage**: Enhance existing AuditLogger
   - Use existing `ip_address`/`session_id` columns (migration 0004)
   - User-Agent in `details` JSON (no migration)
   - Capture from Request in dependency layer

4. **Chart Library**: ECharts (already in project via NiceGUI)

5. **Permissions**: Required RBAC changes (all in permissions.py)
   - Add `Permission.VIEW_TCA` to Permission enum (new)
   - Add `Permission.VIEW_AUDIT` to `ROLE_PERMISSIONS[Role.OPERATOR]` (allow operators to view order audit trails)
   - Add `DatasetPermission.TAQ_ACCESS` to `ROLE_DATASET_PERMISSIONS[Role.OPERATOR]` (required for TCA)
   - Existing: `EXPORT_DATA`, `VIEW_AUDIT` enum values already exist; this task grants VIEW_AUDIT to operators

---

## Files Summary

### Files to Create (13 files)

| File | Task | Purpose |
|------|------|---------|
| `db/migrations/XXXX_add_export_audit_and_index.sql` | T8.3/T8.2 | Export audit table + audit_log index |
| `apps/web_console_ng/components/grid_export_toolbar.py` | T8.3 | Reusable export component |
| `apps/web_console_ng/static/js/grid_export.js` | T8.3 | Client-side export + sanitizer |
| `apps/execution_gateway/routes/export.py` | T8.3 | Export audit + Excel endpoints |
| `apps/execution_gateway/schemas/export.py` | T8.3 | Export schemas + GRID_COLUMN_CLASSIFICATION (PII columns, allowed columns per grid) |
| `apps/execution_gateway/services/export_utils.py` | T8.3 | Server-side sanitization + utilities |
| `apps/execution_gateway/services/grid_query.py` | T8.3 | AG Grid filter/sort canonicalization + GRID_QUERY_CONFIG (filter/sort allowlists per grid) |
| `apps/execution_gateway/routes/tca.py` | T8.1 | TCA API endpoints |
| `apps/execution_gateway/schemas/tca.py` | T8.1 | TCA request/response schemas |
| `apps/web_console_ng/pages/execution_quality.py` | T8.1 | TCA dashboard page |
| `apps/web_console_ng/components/tca_chart.py` | T8.1 | TCA visualization |
| `apps/web_console_ng/components/order_audit_panel.py` | T8.2 | Audit trail display |
| `apps/execution_gateway/schemas/audit.py` | T8.2 | Audit response schemas |

### Files to Modify (24 files)

| File | Task | Changes |
|------|------|---------|
| `apps/web_console_ng/components/positions_grid.py` | T8.3 | Add export toolbar |
| `apps/web_console_ng/components/orders_table.py` | T8.3/T8.2 | Add export + audit button |
| `apps/web_console_ng/components/tabbed_panel.py` | T8.3 | Add export to fills/history |
| `apps/web_console_ng/components/hierarchical_orders.py` | T8.3 | Add export toolbar |
| `apps/web_console_ng/pages/position_management.py` | T8.3 | Add export to grid |
| `apps/execution_gateway/main.py` | T8.1/T8.3 | Register TCA + export routers |
| `apps/execution_gateway/config.py` | T8.3 | Add EXPORT_STRICT_AUDIT_MODE setting |
| `apps/execution_gateway/app_context.py` | T8.1 | Add TAQ/Microstructure + ThreadPoolExecutor (guarded init) |
| `apps/execution_gateway/api/dependencies.py` | T8.1/T8.2 | Add TCA analyzer + request context + trusted proxy |
| `apps/execution_gateway/database.py` | T8.1/T8.2/T8.3 | Add fills/audit/export_audit query methods |
| `apps/web_console_ng/pages/__init__.py` | T8.1 | Register execution_quality |
| `apps/web_console_ng/ui/layout.py` | T8.1 | Add nav link |
| `apps/web_console_ng/config.py` | T8.1 | Add feature flag |
| `infra/docker-compose.yml` | T8.1 | Mount data volume + TAQ env vars + EXPORT_STRICT_AUDIT_MODE |
| `libs/platform/web_console_auth/permissions.py` | T8.1/T8.2 | Add VIEW_TCA, VIEW_AUDIT to OPERATOR + TAQ_ACCESS to OPERATOR |
| `libs/platform/web_console_auth/gateway_auth.py` | T8.2 | Add session_id to AuthenticatedUser + extract from JWT |
| `libs/platform/web_console_auth/jwt_manager.py` | T8.2 | Verify session_id in claims (add if missing) |
| `libs/platform/web_console_auth/audit_logger.py` | T8.2 | Add ip/session params to _write() |
| `apps/execution_gateway/api/manual_controls.py` | T8.2 | Pass IP/session/UA to all 5 order actions |
| `apps/execution_gateway/routes/orders.py` | T8.2 | Add audit endpoint + IP/session/UA to 3 API order actions |
| `libs/platform/web_console_auth/api_client.py` | T8.2 | Forward X-Forwarded-For/X-Original-User-Agent |
| `apps/web_console_ng/main.py` | T8.2 | Add middleware to capture user context (IP/UA) |
| `apps/execution_gateway/services/webhook_handler.py` | T8.2 | Add audit for fills/rejects/expirations/cancels |
| `apps/execution_gateway/services/reconciler.py` | T8.2 | Add audit for reconciler adjustments |

---

## Verification Plan

1. **T8.3 Verification:**
   - Export CSV from positions grid → verify content matches visible columns
   - Export Excel from orders grid → verify formatting and data
   - Export clipboard → verify paste content is correct
   - Apply filter, export → verify only filtered data exported
   - Hide column, export → verify hidden column excluded
   - Export without permission → verify 403 from /api/v1/export/audit
   - EXPORT_STRICT_AUDIT_MODE=true + CSV export → verify 403 from /api/v1/export/audit
   - EXPORT_STRICT_AUDIT_MODE=true + clipboard export → verify 403 from /api/v1/export/audit
   - EXPORT_STRICT_AUDIT_MODE=true + Excel export → verify allowed (200)
   - Check export_audit table for ALL export types includes IP/session/UA (NOT audit_log)
   - Verify audit record has status="pending" after POST /api/v1/export/audit
   - Verify audit record has status="completed" after PATCH /api/v1/export/audit/{id}/complete
   - Export >10,000 rows → verify /api/v1/export/audit returns allowed=false
   - Excel export without audit_id → verify 404 from /api/v1/export/excel
   - Excel export with wrong user's audit_id → verify 403
   - CSV cell with `=SUM(1,2)` → verify sanitized to `'=SUM(1,2)`
   - Clipboard cell with `+cmd` → verify sanitized to `'+cmd`
   - Excel cell with `-1+1` → verify sanitized to `'-1+1`

2. **T8.1 Verification:**
   - Call TCA API → returns valid metrics within 10s
   - Navigate to `/execution-quality` → page loads
   - Apply date filter (30+ days) → 400 error
   - Apply date filter → metrics update, pagination works
   - Click on order → TCA breakdown displayed
   - Export → CSV contains all visible metrics
   - Access without VIEW_TCA → 403
   - Access without TAQ_ACCESS → 403
   - Access non-owned strategy → 403

3. **T8.2 Verification:**
   - Submit order → audit entry created with IP/session/User-Agent
   - Cancel order → audit entry shows cancellation
   - Call audit API → returns chronological entries
   - View audit panel → all actions displayed
   - Export audit → CSV contains full trail
   - Access non-owned order audit → 403

---

## Definition of Done

- [ ] All 3 tasks implemented
- [ ] T8.3: Export toolbar on all grids with audit logging + formula sanitization
- [ ] T8.1: TCA API and dashboard with async offload, pagination, auth
- [ ] T8.2: Audit trail with IP/session/UA capture and display
- [ ] All new/existing permissions enforced
- [ ] Dataset permission (TAQ_ACCESS) enforced for TCA
- [ ] Strategy scoping enforced for TCA and audit
- [ ] Unit tests > 85% coverage
- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Security tests pass (permission + injection)
- [ ] Performance tests pass (TCA <10s, export <5s)
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-31
**Status:** TASK (Revised per Codex review feedback - iteration 25)

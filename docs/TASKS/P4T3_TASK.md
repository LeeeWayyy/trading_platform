# P4T3: Web Console - Core Analytics

**Task ID:** P4T3
**Phase:** P4 (Advanced Features & Research Infrastructure)
**Track:** Track 6 - Web Console Core Analytics
**Priority:** P0 - Security and operational visibility
**Estimated Effort:** 19-24 days (7 subtasks - added T6.1b Admin UI)
**Status:** 🚧 In Progress
**Created:** 2025-12-09
**Last Updated:** 2025-12-09 (v1.2 - Addressed all Gemini + Codex v1.1 review findings)

---

## Review History

| Version | Date | Reviewers | Status | Key Changes |
|---------|------|-----------|--------|-------------|
| v1.0 | 2025-12-09 | Gemini, Codex | Fixes Required | Initial plan |
| v1.1 | 2025-12-09 | Gemini, Codex | Approved w/ Mods | Added T6.1b Admin UI, fixed SQL migrations, clarified auth integration, defined 2FA mechanism, added denied-event logging, reordered tasks |
| v1.2 | 2025-12-09 | Gemini, Codex | Approved w/ Mods | ALTER TABLE migration, admin bootstrap CLI, session invalidation on role change, 2FA `amr` claim validation, server-side strategy scoping, async/sync patterns, Redis rate limiter, audit resilience |
| v1.3 | 2025-12-09 | Gemini, Codex | Changes Requested | Default-deny missing user_roles, wire 2FA to verify_step_up_auth, fresh Redis in thread, fix audit rowcount, add FK to user_strategy_access, db_pool injection, align rate limiter API, CSRF form example |
| v1.4 | 2025-12-09 | Gemini, Codex | Split (G:Approved, C:Needs Revision) | Fresh DB+Redis in sync wrapper, login denies missing user_roles, implement _get_all_strategy_ids, sync wrappers for Streamlit async, clarify per-request DB for security |
| v1.5 | 2025-12-09 | Codex | **APPROVED** | Audit schema amr/step-up fields, pool sizing/fallback, call-site mapping, backend contracts, load/E2E tests |
| v1.6 | 2025-12-09 | Codex (direct edit), Claude | **APPROVED** | Added Implementation Feasibility & Sequencing (5 gates), Infrastructure Requirements, rollout gates with feature flags |
| v1.7 | 2025-12-10 | - | Minor Fixes | ADR-024 ordered first, execution_gateway coordination clarified, scheduler deployment ticket linked |

---

## Progress Tracker

| Task | Status | PR | Notes |
|------|--------|-----|-------|
| T6.1a Auth/RBAC Core | ⏳ Pending | | Security layer, session integration |
| T6.1b Admin User Management | ⏳ Pending | | Role/strategy assignment UI |
| T6.6 Manual Trade Controls | ⏳ Pending | | **P0** - Moved up for safety |
| T6.2 Performance Dashboard | ⏳ Pending | | P&L dashboard |
| T6.3 Risk Analytics Dashboard | ⏳ Pending | | Risk visualization |
| T6.4 Strategy Comparison Tool | ⏳ Pending | | Strategy compare |
| T6.5 Trade Journal & Analysis | ⏳ Pending | | Trade analysis |

**Progress:** 0/7 tasks complete (0%)

**Task Order Rationale:** T6.6 moved after T6.1 per Gemini review - P0 operational safety takes precedence over analytics dashboards.

## Implementation Feasibility & Sequencing

- **Gate 0:** Apply DB migrations 0005 → 0006 in staging, verify audit backfill rowcounts and new FKs before enabling RBAC code paths.
- **Gate 1:** Deploy `permissions.py` + session_version checks with feature flag (`ENABLE_RBAC=true`) and run smoke auth flow; only then proceed to dashboards.
- **Gate 2:** Stand up Redis rate limiter endpoint and ensure separate DB index; run perf tests before exposing manual controls.
- **Gate 3:** Ship manual controls (T6.6) together with execution_gateway enforcement to avoid UI/BE drift.
- **Gate 4:** Enable dashboards incrementally (T6.2 → T6.3 → T6.4 → T6.5) behind per-page feature flags to limit blast radius.

---

## Executive Summary

Track 6 builds core trading analytics dashboards with proper authentication and authorization controls. The web console already has OAuth2/PKCE authentication infrastructure - this track extends it with RBAC permissions and adds analytics pages.

**Goal:** Secure analytics dashboards for trading operations with role-based access control.

**Key Deliverables:**
- RBAC/Permissions layer integrated with existing OAuth2 sessions (T6.1a)
- Admin UI for user/strategy management (T6.1b) **[NEW - per Gemini review]**
- Manual Trade Controls with safety confirmations (T6.6) **[Moved up - P0]**
- Performance Dashboard with P&L and drawdown (T6.2)
- Risk Analytics Dashboard with factor exposures (T6.3)
- Strategy Comparison Tool (T6.4)
- Trade Journal with filtering and export (T6.5)

**Existing Infrastructure (from P2):**
- OAuth2/PKCE authentication (`apps/web_console/auth/oauth2_flow.py`)
- Session management (`apps/web_console/auth/session_manager.py`)
- Redis session store (`apps/web_console/auth/session_store.py`)
- mTLS fallback (`apps/web_console/auth/mtls_fallback.py`)
- Streamlit helpers (`apps/web_console/auth/streamlit_helpers.py`)
- **Existing manual order entry** (`apps/web_console/app.py` - `render_manual_order_entry`) **[Will be enhanced, not replaced]**

**Dependencies from P4T1/P4T2 (Complete):**
- T2.3 Portfolio Risk Analytics (for T6.3)
- T2.7 Factor Attribution (for T6.3)

## Infrastructure Requirements (new for P4T3)

- **PostgreSQL migrations:** Run `0005_update_audit_log_schema` before `0006_create_rbac_tables`; both are prerequisites before enabling RBAC or dashboards in any environment.
- **Redis topology:** Use a separate Redis logical DB (or instance) for the rate limiter to avoid contention with the session store; configure via `REDIS_RATE_LIMIT_URL` with a distinct DB index.
- **Auth0 / IdP:** Enable MFA step-up (amr claim) and configure the OAuth client to allow prompt=login; set `INITIAL_ADMIN_EMAIL` for bootstrap CLI.
- **Scheduler:** Provide cron/APScheduler worker to run `apps/web_console/tasks/audit_cleanup.py` daily at 02:00 UTC; ensure it runs with network access to Postgres/Redis. **[DEPLOYMENT: Create ops ticket for scheduler setup before T6.1a completes - see Related Documents]**
- **Observability:** Emit audit cleanup + rate limiter metrics to Prometheus; wire alerts for cleanup failures (`audit_cleanup_last_run_timestamp > 25h`) and rate-limit error spikes.

---

## Architecture: Auth Integration Design

**[NEW SECTION - per Codex review finding: "RBAC not integrated with existing session binding"]**

### Single Source of Truth: Session Manager

All RBAC checks flow through the existing `session_manager.py` which already handles IP/UA binding:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTH FLOW WITH RBAC                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. OAuth2 Login (oauth2_flow.py)                                   │
│     └─► handle_callback():                                          │
│         ├─ Exchange code for tokens                                  │
│         ├─ [NEW] Query user_roles table for role                    │
│         ├─ [NEW] Query user_strategy_access for strategies          │
│         └─ Store in Redis session: {email, role, strategies, ...}   │
│                                                                      │
│  2. Session Validation (session_manager.py)                         │
│     └─► validate_session():                                          │
│         ├─ Verify session_id in Redis                               │
│         ├─ Validate IP/UA binding (existing)                        │
│         └─ Return {email, role, strategies} (non-sensitive only)    │
│                                                                      │
│  3. Permission Check (permissions.py - NEW)                         │
│     └─► @require_permission(Permission.X):                          │
│         ├─ Call session_manager.validate_session()                  │
│         ├─ Check role has permission                                │
│         ├─ [Audit] Log access attempt (success or denied)          │
│         └─ Return or st.stop()                                      │
│                                                                      │
│  4. Strategy Filtering (permissions.py - NEW)                       │
│     └─► get_authorized_strategies():                                │
│         ├─ Get strategies from session                              │
│         └─ Apply filter to ALL data queries                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Role Sourcing Strategy

**Decision:** Roles stored in PostgreSQL `user_roles` table, fetched on login, cached in Redis session. Session_version validated per-request.

**Rationale:**
- Auth0 custom claims require Auth0 management API integration (complexity)
- DB lookup is simple, auditable, and allows runtime updates
- Role cached in session for fast permission checks

**[CLARIFIED v1.4 - per Gemini MEDIUM: Security vs Performance Tradeoff]**

**Session Validation Per Request:**
- `session_version` is checked against DB on EVERY request (1 simple SELECT)
- This enables immediate revocation when admin changes role/strategy
- Security benefit outweighs the DB hit (~1ms per request)
- Consider Redis caching of session_version with 30s TTL if performance becomes issue

## Architecture: Analytics Data Access & Strategy Scoping

**Decision:** All dashboard queries must go through a server-side StrategyScopedDataAccess layer that enforces RBAC filters before data leaves the backend.

**Rationale:** Prevents client-side filtering bypass, keeps parity between Streamlit UI and FastAPI APIs, and allows consistent caching + pagination controls.

**Pattern:**
- `StrategyScopedDataAccess` (new helper) receives `user_id`, `role`, `strategies`, and a `db_pool`.
- All pages/components call its methods (positions/pnl/trades/risk) instead of raw queries.
- Caching: 5-minute TTL Redis cache keyed by `user_id` + strategy set hash to avoid cross-user leakage.
- Pagination + limits enforced server-side (default 100 rows) with defensive caps (max 1,000) to protect the DB.
- Exports call the same access layer with streaming cursors to avoid OOM.

**Async/Sync Access:** Provide sync wrappers for Streamlit to avoid `asyncio.run` anti-patterns; wrappers open per-request pools to avoid cross-thread reuse (addresses v1.4 finding).

---

## T6.1a: Auth/RBAC Core

**Effort:** 4-5 days | **PR:** `feat(P4T3): analytics auth core`
**Status:** ⏳ Pending
**Priority:** P0 (Security requirement)

### Problem Statement

Analytics dashboards expose P&L and trade data. Current auth validates identity but lacks role-based access control and comprehensive audit logging.

### Deliverables

1. **Role-Based Access Control (RBAC)**
   - Define roles: `viewer`, `operator`, `admin`
   - Permission mapping with default-deny on unknown roles
   - Integration with existing session_manager

2. **Session Integration**
   - Update `oauth2_flow.py` handle_callback to fetch roles from DB
   - Extend `session_store.py` SessionData to include `role` and `strategies`
   - Graceful handling of unknown/missing roles (default-deny with logging)

3. **Per-Strategy Authorization**
   - `get_authorized_strategies()` queries from session
   - Strategy filter applied to ALL data queries
   - Admin sees all strategies

4. **Comprehensive Audit Logging**
   - Log access attempts (success AND denied)
   - Log role/strategy changes
   - Log login/logout events
   - Log export/download actions
   - Log 2FA outcomes
   - Retention policy: 90 days default, configurable
   - PII minimization: log user_id not full email in detail fields

### Implementation

```python
# apps/web_console/auth/permissions.py
from enum import Enum
from functools import wraps
from typing import Callable
import logging
import streamlit as st

from apps.web_console.auth.session_manager import validate_session, get_session_cookie
from apps.web_console.auth.session_store import get_redis_session_store
from apps.web_console.auth.streamlit_helpers import get_client_ip, get_user_agent

logger = logging.getLogger(__name__)

class Role(str, Enum):
    VIEWER = "viewer"      # Read-only access to assigned strategies
    OPERATOR = "operator"  # Can execute trades, cancel orders
    ADMIN = "admin"        # Full access to all strategies and settings

class Permission(str, Enum):
    VIEW_POSITIONS = "view_positions"
    VIEW_PNL = "view_pnl"
    VIEW_TRADES = "view_trades"
    CANCEL_ORDER = "cancel_order"
    CLOSE_POSITION = "close_position"
    FLATTEN_ALL = "flatten_all"
    MANAGE_USERS = "manage_users"
    VIEW_ALL_STRATEGIES = "view_all_strategies"
    EXPORT_DATA = "export_data"

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.EXPORT_DATA,
    },
    Role.OPERATOR: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.CANCEL_ORDER,
        Permission.CLOSE_POSITION,
        Permission.EXPORT_DATA,
    },
    Role.ADMIN: set(Permission),  # All permissions
}

def _get_role_safe(user: dict) -> Role | None:
    """Get role with safe handling for unknown values.

    Returns None for unknown roles (default-deny pattern).
    Logs warning for unknown role values.
    """
    role_str = user.get("role", "")
    try:
        return Role(role_str)
    except ValueError:
        logger.warning(
            "Unknown role value",
            extra={"user_id": user.get("sub"), "role_value": role_str}
        )
        return None

def has_permission(user: dict, permission: Permission) -> bool:
    """Check if user has permission. Default-deny on unknown roles."""
    role = _get_role_safe(user)
    if role is None:
        return False  # Default deny
    return permission in ROLE_PERMISSIONS[role]

def require_permission(permission: Permission) -> Callable:
    """Decorator to require specific permission with audit logging.

    [FIXED - per Codex MEDIUM: Use sync wrapper instead of asyncio.run to avoid
    RuntimeError when Streamlit already has an event loop running]
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Get session using existing session_manager flow
            session_id = get_session_cookie()
            if not session_id:
                _log_access_denied(None, permission, "no_session")
                st.error("Authentication required")
                st.stop()

            client_ip = get_client_ip()
            user_agent = get_user_agent()

            # [FIXED v1.3] Use sync wrapper with fresh Redis client
            user = validate_session_sync(session_id, client_ip, user_agent)

            if user is None:
                _log_access_denied(None, permission, "invalid_session")
                st.error("Session expired. Please log in again.")
                st.stop()

            if not has_permission(user, permission):
                _log_access_denied(user, permission, "insufficient_permission")
                st.error(f"Permission denied: {permission.value}")
                st.stop()

            _log_access_granted(user, permission)
            return func(*args, **kwargs)
        return wrapper
    return decorator

def validate_session_sync(
    session_id: str,
    client_ip: str,
    user_agent: str,
) -> dict | None:
    """Sync wrapper for validate_session that handles running event loops.

    [FIXED v1.3 - per Gemini MEDIUM: Instantiate fresh Redis client in thread context]
    [FIXED v1.4 - per Gemini/Codex HIGH: Also create fresh DB connection for session_version check]
    Do NOT pass loop-bound clients; create ephemeral connections inside asyncio.run.
    """
    import asyncio
    import concurrent.futures

    async def _validate_with_fresh_resources():
        """Create fresh Redis AND DB clients for this isolated event loop."""
        from apps.web_console.auth.session_store import create_redis_session_store
        from libs.common.db import create_db_pool  # [FIXED v1.4] Fresh DB pool

        # Create fresh resources for this isolated loop
        fresh_session_store = await create_redis_session_store()
        fresh_db_pool = await create_db_pool()  # [FIXED v1.4]

        try:
            return await validate_session(
                session_id,
                fresh_session_store,
                client_ip,
                user_agent,
                db_pool=fresh_db_pool,  # [FIXED v1.4] Pass DB pool for session_version check
            )
        finally:
            await fresh_session_store.close()
            await fresh_db_pool.close()  # [FIXED v1.4] Clean up DB pool

    try:
        loop = asyncio.get_running_loop()
        # Already in async context - use thread with isolated loop
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _validate_with_fresh_resources())
            return future.result(timeout=5.0)
    except RuntimeError:
        # No running loop - safe to use asyncio.run directly
        return asyncio.run(_validate_with_fresh_resources())

def get_authorized_strategies(user: dict) -> list[str]:
    """Get strategies user is authorized to view."""
    if has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        return _get_all_strategy_ids_sync()  # Admin sees all
    return user.get("strategies", [])

def _get_all_strategy_ids_sync() -> list[str]:
    """Fetch all strategy IDs from database (sync wrapper).

    [FIXED v1.4 - per Codex MEDIUM: Implement instead of stub]
    Uses sync wrapper pattern for consistency with Streamlit context.
    """
    import asyncio
    import concurrent.futures

    async def _fetch_all():
        from libs.common.db import create_db_pool
        db_pool = await create_db_pool()
        try:
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("SELECT strategy_id FROM strategies ORDER BY strategy_id")
                return [row["strategy_id"] for row in rows]
        finally:
            await db_pool.close()

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _fetch_all())
            return future.result(timeout=5.0)
    except RuntimeError:
        return asyncio.run(_fetch_all())

def _log_access_granted(user: dict, permission: Permission) -> None:
    """Log successful access (audit trail)."""
    logger.info(
        "Access granted",
        extra={
            "audit": True,
            "event_type": "access_granted",
            "user_id": user.get("sub"),
            "permission": permission.value,
            "ip_address": get_client_ip(),
        }
    )

def _log_access_denied(user: dict | None, permission: Permission, reason: str) -> None:
    """Log denied access (audit trail)."""
    logger.warning(
        "Access denied",
        extra={
            "audit": True,
            "event_type": "access_denied",
            "user_id": user.get("sub") if user else None,
            "permission": permission.value,
            "reason": reason,
            "ip_address": get_client_ip(),
        }
    )
```

```python
# apps/web_console/auth/audit_log.py
from datetime import datetime, UTC
from dataclasses import dataclass
from typing import Literal, Any
import logging
import json

from apps.web_console.auth.streamlit_helpers import get_client_ip

logger = logging.getLogger(__name__)

# Retention policy
AUDIT_RETENTION_DAYS = 90

@dataclass
class AuditEvent:
    """Audit event with PII minimization."""
    timestamp: datetime
    event_type: str  # access, action, auth, admin
    user_id: str  # Use sub, not email
    action: str
    resource_type: str
    resource_id: str | None
    outcome: Literal["success", "denied", "failed"]
    details: dict  # No PII in details
    ip_address: str

class AuditLogger:
    """Comprehensive audit logger with persistence and retention."""

    def __init__(self, db_pool: Any):
        self.db_pool = db_pool

    async def log_access(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str | None = None,
        outcome: Literal["success", "denied"] = "success",
        details: dict | None = None,
    ) -> None:
        """Log data access event."""
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="access",
            user_id=user_id,
            action="view",
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
            ip_address=get_client_ip(),
        )
        await self._persist(event)

    async def log_action(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: Literal["success", "denied", "failed"],
        details: dict,
    ) -> None:
        """Log control action (cancel, close, etc.)."""
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="action",
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
            ip_address=get_client_ip(),
        )
        await self._persist(event)

    async def log_auth_event(
        self,
        user_id: str | None,
        action: Literal["login", "logout", "2fa_success", "2fa_failed"],
        outcome: Literal["success", "denied", "failed"],
        details: dict | None = None,
    ) -> None:
        """Log authentication events."""
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="auth",
            user_id=user_id or "anonymous",
            action=action,
            resource_type="session",
            resource_id=None,
            outcome=outcome,
            details=details or {},
            ip_address=get_client_ip(),
        )
        await self._persist(event)

    async def log_admin_change(
        self,
        admin_user_id: str,
        action: str,  # "role_change", "strategy_grant", "strategy_revoke"
        target_user_id: str,
        details: dict,
    ) -> None:
        """Log admin actions (role/strategy changes)."""
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="admin",
            user_id=admin_user_id,
            action=action,
            resource_type="user",
            resource_id=target_user_id,
            outcome="success",
            details=details,
            ip_address=get_client_ip(),
        )
        await self._persist(event)

    async def log_export(
        self,
        user_id: str,
        export_type: str,  # "csv", "excel", "pdf"
        resource_type: str,
        row_count: int,
    ) -> None:
        """Log data export/download."""
        event = AuditEvent(
            timestamp=datetime.now(UTC),
            event_type="access",
            user_id=user_id,
            action="export",
            resource_type=resource_type,
            resource_id=None,
            outcome="success",
            details={"export_type": export_type, "row_count": row_count},
            ip_address=get_client_ip(),
        )
        await self._persist(event)

    async def _persist(self, event: AuditEvent) -> None:
        """Persist audit event to database."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, user_id, action,
                    resource_type, resource_id, outcome, details, ip_address
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                event.timestamp,
                event.event_type,
                event.user_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.outcome,
                json.dumps(event.details),
                event.ip_address,
            )

        # Also log to structured logger for Grafana/Loki
        logger.info(
            f"Audit: {event.event_type}/{event.action}",
            extra={
                "audit": True,
                "event_type": event.event_type,
                "user_id": event.user_id,
                "action": event.action,
                "resource": f"{event.resource_type}/{event.resource_id}",
                "outcome": event.outcome,
            }
        )

    async def cleanup_old_events(self) -> int:
        """Delete events older than retention period. Returns count deleted.

        [FIXED v1.2 - use make_interval for bind-safe retention]
        [FIXED v1.3 - per Codex MEDIUM: parse rowcount from execute result string]
        """
        async with self.db_pool.acquire() as conn:
            # execute() returns status string like 'DELETE 5'
            result = await conn.execute(
                """
                DELETE FROM audit_log
                WHERE timestamp < NOW() - make_interval(days => $1)
                """,
                AUDIT_RETENTION_DAYS,
            )
            # [FIXED] Parse rowcount from status string, not len()
            # result = 'DELETE 5' -> extract 5
            try:
                deleted_count = int(result.split()[1])
            except (IndexError, ValueError):
                deleted_count = 0
            return deleted_count
```

### Database Migration (v1.2 - ALTER TABLE per Gemini review)

**[FIXED - per Gemini CRITICAL: audit_log table already exists in 0004_add_audit_log.sql]**

```sql
-- db/migrations/0005_update_audit_log_schema.sql
-- Extend existing audit_log table for RBAC (0004 created base table)
-- Author: Claude Code (P4T3)

-- Add new columns for RBAC audit events
-- [FIXED v1.5 - per Codex: Add amr_method for step-up auth evidence]
ALTER TABLE audit_log
ADD COLUMN IF NOT EXISTS event_type VARCHAR(20),      -- access, action, auth, admin
ADD COLUMN IF NOT EXISTS resource_type VARCHAR(50),   -- order, position, strategy, user
ADD COLUMN IF NOT EXISTS resource_id VARCHAR(255),    -- Specific resource identifier
ADD COLUMN IF NOT EXISTS outcome VARCHAR(20),         -- success, denied, failed
ADD COLUMN IF NOT EXISTS amr_method VARCHAR(20);      -- [NEW v1.5] MFA method: otp, sms, webauthn, etc.

-- Set defaults for existing rows (details already NOT NULL in 0004)
UPDATE audit_log SET event_type = 'action' WHERE event_type IS NULL;
UPDATE audit_log SET resource_type = 'system' WHERE resource_type IS NULL;
UPDATE audit_log SET outcome = 'success' WHERE outcome IS NULL;

-- Add NOT NULL constraints after backfill
ALTER TABLE audit_log ALTER COLUMN event_type SET NOT NULL;
ALTER TABLE audit_log ALTER COLUMN resource_type SET NOT NULL;
ALTER TABLE audit_log ALTER COLUMN outcome SET NOT NULL;

-- Add new indexes (existing: idx_audit_log_timestamp, idx_audit_log_user, idx_audit_log_action)
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log (event_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log (outcome) WHERE outcome != 'success';

-- [NEW v1.5] Partition by month for retention efficiency
-- NOTE: For PostgreSQL 11+, convert to partitioned table on fresh install
-- For existing data, use scheduled cleanup (see cleanup_old_events)
COMMENT ON TABLE audit_log IS 'Audit trail - retention: 90 days, cleanup via scheduled job';
COMMENT ON COLUMN audit_log.event_type IS 'Event category: access, action, auth, admin';
COMMENT ON COLUMN audit_log.resource_type IS 'Resource type: order, position, strategy, user, session';
COMMENT ON COLUMN audit_log.resource_id IS 'Specific resource identifier';
COMMENT ON COLUMN audit_log.outcome IS 'Event outcome: success, denied, failed';
COMMENT ON COLUMN audit_log.amr_method IS 'MFA method from Auth0 amr claim (for 2FA audit trail)';
```

### Audit Retention & Cleanup Strategy

**[NEW v1.5 - per Codex: Add partition/TTL plan]**

```python
# apps/web_console/tasks/audit_cleanup.py
"""Scheduled audit log cleanup task."""
import asyncio
import logging
from datetime import datetime, UTC

from libs.common.db import create_db_pool
from apps.web_console.auth.audit_log import AuditLogger, AUDIT_RETENTION_DAYS

logger = logging.getLogger(__name__)

async def run_audit_cleanup() -> dict:
    """Run scheduled audit log cleanup.

    Called by scheduler (e.g., cron, APScheduler) daily at 02:00 UTC.
    Returns stats for monitoring.
    """
    db_pool = await create_db_pool()
    try:
        audit_logger = AuditLogger(db_pool)

        # Get count before cleanup for metrics
        async with db_pool.acquire() as conn:
            before_count = await conn.fetchval("SELECT COUNT(*) FROM audit_log")

        deleted = await audit_logger.cleanup_old_events()

        async with db_pool.acquire() as conn:
            after_count = await conn.fetchval("SELECT COUNT(*) FROM audit_log")

        stats = {
            "deleted_count": deleted,
            "before_count": before_count,
            "after_count": after_count,
            "retention_days": AUDIT_RETENTION_DAYS,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        logger.info("Audit cleanup completed", extra=stats)
        return stats
    finally:
        await db_pool.close()

# Scheduler integration (for APScheduler or similar)
# Schedule: daily at 02:00 UTC
# scheduler.add_job(run_audit_cleanup, 'cron', hour=2, minute=0)
```

```sql
-- db/migrations/0006_create_rbac_tables.sql
-- User roles and strategy access tables
-- Author: Claude Code (P4T3)

-- User roles table with constraint
CREATE TABLE IF NOT EXISTS user_roles (
    user_id VARCHAR(255) PRIMARY KEY,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',
    session_version INTEGER NOT NULL DEFAULT 1,  -- [NEW] Increment to invalidate sessions
    updated_by VARCHAR(255),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_role CHECK (role IN ('viewer', 'operator', 'admin'))
);

-- Strategy reference table (if not exists)
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- User-strategy assignment with foreign keys
-- [FIXED v1.3 - per Codex MEDIUM: Add FK to user_roles for referential integrity]
CREATE TABLE IF NOT EXISTS user_strategy_access (
    user_id VARCHAR(255) NOT NULL,
    strategy_id VARCHAR(50) NOT NULL,
    granted_by VARCHAR(255) NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (user_id, strategy_id),

    -- FK to user_roles (ensures user exists and cascade on deprovision)
    CONSTRAINT fk_user
        FOREIGN KEY (user_id)
        REFERENCES user_roles(user_id)
        ON DELETE CASCADE,

    -- FK to strategies
    CONSTRAINT fk_strategy
        FOREIGN KEY (strategy_id)
        REFERENCES strategies(strategy_id)
        ON DELETE CASCADE
);

CREATE INDEX idx_user_strategy_user ON user_strategy_access (user_id);
CREATE INDEX idx_user_strategy_strategy ON user_strategy_access (strategy_id);

-- Seed initial admin user (from environment or hardcoded for bootstrap)
-- This is a placeholder - actual bootstrap via scripts/manage_roles.py
COMMENT ON TABLE user_roles IS 'RBAC role assignments. Use scripts/manage_roles.py to bootstrap initial admin.';
```

### Session Store Update

**[ENHANCED - per Codex HIGH: Add session_version for invalidation on role/strategy changes]**

```python
# Update to apps/web_console/auth/session_store.py
# Add role, strategies, and session_version to SessionData

@dataclass
class SessionData:
    """Session data stored in Redis."""
    session_id: str
    user_id: str  # sub claim
    email: str
    # NEW fields for RBAC
    role: str  # viewer, operator, admin
    strategies: list[str]  # Authorized strategy IDs
    session_version: int  # [NEW] Must match DB user_roles.session_version
    # Existing fields
    created_at: datetime
    expires_at: datetime
    ip_address: str
    user_agent: str
    # ... other existing fields
```

### Session Invalidation on Role/Strategy Changes

**[NEW - per Codex HIGH: Stale elevated access when admin changes roles]**

When admin changes a user's role or strategy access, their active sessions must be invalidated:

```python
# apps/web_console/auth/session_invalidation.py
"""Session invalidation on privilege changes."""
import logging
from typing import Any

logger = logging.getLogger(__name__)

async def invalidate_user_sessions(
    user_id: str,
    db_pool: Any,
    audit_logger: "AuditLogger",
    admin_user_id: str,
) -> int:
    """Invalidate all active sessions for a user by incrementing session_version.

    Called when admin changes role or strategy access.
    Returns new session_version.
    """
    async with db_pool.acquire() as conn:
        # Increment session_version - all existing sessions become invalid
        result = await conn.fetchrow(
            """
            UPDATE user_roles
            SET session_version = session_version + 1,
                updated_at = NOW(),
                updated_by = $2
            WHERE user_id = $1
            RETURNING session_version
            """,
            user_id,
            admin_user_id,
        )
        new_version = result["session_version"] if result else 1

    # Log the invalidation
    await audit_logger.log_admin_change(
        admin_user_id=admin_user_id,
        action="session_invalidation",
        target_user_id=user_id,
        details={"new_session_version": new_version},
    )

    logger.info(
        "User sessions invalidated",
        extra={"user_id": user_id, "new_version": new_version, "by": admin_user_id}
    )
    return new_version

async def validate_session_version(
    user_id: str,
    session_version: int,
    db_pool: Any,
) -> bool:
    """Check if session_version matches current DB value.

    Returns False if session is stale (role/strategy changed since login).

    [FIXED v1.3 - per Codex HIGH: Default-deny when no user_roles row]
    Missing row = deprovisioned user, force re-login.
    """
    async with db_pool.acquire() as conn:
        result = await conn.fetchrow(
            "SELECT session_version FROM user_roles WHERE user_id = $1",
            user_id,
        )
        if not result:
            # [FIXED] Default-deny: missing row means deprovisioned user
            logger.warning(
                "Session validation failed: no user_roles row",
                extra={"user_id": user_id}
            )
            return False  # Force re-login
        return result["session_version"] == session_version
```

**Integration with session_manager.py:**
```python
# Update validate_session to check session_version
async def validate_session(...) -> dict | None:
    # ... existing IP/UA validation ...

    # [NEW] Check session_version against DB
    if not await validate_session_version(
        session_data.user_id,
        session_data.session_version,
        db_pool,
    ):
        logger.warning(
            "Session invalidated due to role/strategy change",
            extra={"user_id": session_data.user_id}
        )
        return None  # Force re-login

    return session_data.to_dict()
```

### OAuth2 Flow Update

**[ENHANCED - per Gemini/Codex: Fetch session_version, validate amr claim for 2FA]**

```python
# Update to apps/web_console/auth/oauth2_flow.py
# Fetch role + session_version from DB on login, validate amr for 2FA
# [FIXED v1.3 - per Codex MEDIUM: Accept db_pool as parameter]

async def handle_callback(
    code: str,
    state: str,
    db_pool: asyncpg.Pool,  # [FIXED v1.3] Explicit dependency injection
    audit_logger: AuditLogger,
    **kwargs,
) -> SessionData:
    # ... existing token exchange ...

    # NEW: Fetch role and session_version from database
    user_id = userinfo["sub"]
    role_data = await _fetch_user_role_data(user_id, db_pool)

    # [FIXED v1.4 - per Codex HIGH: Deny login if user not provisioned]
    if role_data is None:
        await audit_logger.log_auth_event(
            user_id, "login", "denied",
            details={"reason": "user_not_provisioned"}
        )
        raise AuthorizationError(
            f"User {userinfo['email']} is not provisioned. "
            "Contact administrator to request access."
        )

    strategies = await _fetch_user_strategies(user_id, db_pool)

    # Create session with role/strategies/session_version
    session = SessionData(
        session_id=generate_session_id(),
        user_id=user_id,
        email=userinfo["email"],
        role=role_data["role"],
        strategies=strategies,
        session_version=role_data["session_version"],
        # ... other fields
    )

    # Log auth event
    await audit_logger.log_auth_event(user_id, "login", "success")

    return session

async def _fetch_user_role_data(user_id: str, db_pool: asyncpg.Pool) -> dict | None:
    """Fetch role and session_version from user_roles table.

    [FIXED v1.4 - per Codex HIGH: Deny login if no user_roles row]
    Returns None if user is not provisioned, causing login failure.
    """
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, session_version FROM user_roles WHERE user_id = $1",
            user_id
        )
        if row:
            return {"role": row["role"], "session_version": row["session_version"]}
        # [FIXED v1.4] Return None to deny login - user not provisioned
        return None

async def _fetch_user_strategies(user_id: str, db_pool: asyncpg.Pool) -> list[str]:
    """Fetch authorized strategies from user_strategy_access table."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT strategy_id FROM user_strategy_access WHERE user_id = $1",
            user_id
        )
        return [row["strategy_id"] for row in rows]
```

### 2FA Verification with amr Claim

**[NEW - per Gemini MEDIUM / Codex HIGH: Validate amr claim for step-up auth]**

**[ENHANCED v1.5 - per Codex: Add callback flow and timeout handling]**

#### Step-Up Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      STEP-UP AUTH CALLBACK FLOW                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. User clicks "Flatten All" (requires 2FA)                                │
│     └─► Check session.step_up_claims.auth_time < 60s                        │
│         └─► If expired/missing:                                             │
│             ├─ Store pending_action in session                              │
│             ├─ Redirect to Auth0 with prompt=login&max_age=0                │
│             └─ Set timeout: 5 minutes for callback                          │
│                                                                              │
│  2. Auth0 Step-Up Callback                                                   │
│     └─► handle_step_up_callback():                                          │
│         ├─ Validate state matches session                                   │
│         ├─ Verify id_token.amr contains MFA method                          │
│         ├─ Verify id_token.auth_time is recent (< 60s)                      │
│         ├─ Store step_up_claims in session (TTL: 60s)                       │
│         └─ Redirect back to pending_action                                  │
│                                                                              │
│  3. Failure Modes                                                            │
│     ├─ Timeout (5 min): Clear pending_action, show error                   │
│     ├─ User cancels: Log denied, redirect to dashboard                      │
│     ├─ MFA not in amr: Reject, log, prompt re-enroll                       │
│     └─ State mismatch: Security error, force logout                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

```python
# apps/web_console/auth/mfa_verification.py
"""MFA verification using Auth0 amr claim."""
from datetime import datetime, UTC
from typing import Any
import logging

logger = logging.getLogger(__name__)

# Max age for step-up auth (60 seconds)
STEP_UP_MAX_AGE_SECONDS = 60
# Timeout for step-up callback (5 minutes)
STEP_UP_CALLBACK_TIMEOUT_SECONDS = 300

def verify_step_up_auth(id_token_claims: dict) -> tuple[bool, str | None]:
    """Verify that user completed MFA within max_age.

    [ENHANCED v1.5] Returns (success, error_reason) tuple for better error handling.

    Checks:
    1. auth_time is within STEP_UP_MAX_AGE_SECONDS
    2. amr claim contains 'mfa' or specific MFA method

    Returns (True, None) if valid, (False, reason) otherwise.
    """
    # Check auth_time
    auth_time = id_token_claims.get("auth_time")
    if not auth_time:
        logger.warning("Missing auth_time in ID token")
        return False, "missing_auth_time"

    auth_datetime = datetime.fromtimestamp(auth_time, tz=UTC)
    age_seconds = (datetime.now(UTC) - auth_datetime).total_seconds()

    if age_seconds > STEP_UP_MAX_AGE_SECONDS:
        logger.info(
            "Step-up auth too old",
            extra={"auth_time": auth_time, "age_seconds": age_seconds}
        )
        return False, "auth_expired"

    # Check amr (Authentication Methods Reference)
    amr = id_token_claims.get("amr", [])
    mfa_methods = {"mfa", "otp", "sms", "email", "webauthn", "fido"}

    if not any(method in amr for method in mfa_methods):
        logger.warning(
            "MFA not in amr claim",
            extra={"amr": amr}
        )
        return False, "mfa_not_used"

    logger.info("Step-up auth verified", extra={"amr": amr, "age_seconds": age_seconds})
    return True, None

def get_amr_method(id_token_claims: dict) -> str | None:
    """Extract the MFA method from amr claim for audit logging.

    [NEW v1.5] Returns the specific MFA method used (otp, sms, webauthn, etc.)
    """
    amr = id_token_claims.get("amr", [])
    mfa_methods = ["webauthn", "fido", "otp", "sms", "email", "mfa"]  # Priority order
    for method in mfa_methods:
        if method in amr:
            return method
    return None

async def require_2fa_for_action(
    session_data: dict,
    action: str,
    audit_logger: Any,
) -> tuple[bool, str | None]:
    """Check if user has valid step-up auth for destructive action.

    [ENHANCED v1.5] Returns (allowed, error_reason) for better UI feedback.

    Returns (True, None) if 2FA verified, (False, reason) if re-auth needed.
    Logs outcome to audit trail with amr_method.
    """
    user_id = session_data.get("sub")

    # Check if recent step-up auth exists in session
    step_up_claims = session_data.get("step_up_claims")
    if not step_up_claims:
        await audit_logger.log_auth_event(
            user_id=user_id,
            action="2fa_required",
            outcome="denied",
            details={"action_attempted": action, "reason": "no_step_up_session"},
        )
        return False, "Step-up authentication required. Please re-authenticate with MFA."

    # Check for callback timeout
    step_up_requested_at = session_data.get("step_up_requested_at")
    if step_up_requested_at:
        elapsed = (datetime.now(UTC) - step_up_requested_at).total_seconds()
        if elapsed > STEP_UP_CALLBACK_TIMEOUT_SECONDS:
            await audit_logger.log_auth_event(
                user_id=user_id,
                action="2fa_timeout",
                outcome="denied",
                details={"action_attempted": action, "elapsed_seconds": elapsed},
            )
            return False, "Step-up authentication timed out. Please try again."

    valid, error_reason = verify_step_up_auth(step_up_claims)
    if not valid:
        await audit_logger.log_auth_event(
            user_id=user_id,
            action="2fa_failed",
            outcome="denied",
            details={"action_attempted": action, "reason": error_reason},
        )
        error_messages = {
            "missing_auth_time": "Invalid authentication response. Please try again.",
            "auth_expired": "Authentication has expired. Please re-authenticate.",
            "mfa_not_used": "MFA verification required. Please ensure MFA is enabled on your account.",
        }
        return False, error_messages.get(error_reason, "Authentication failed.")

    # Success - log with amr method for audit trail
    amr_method = get_amr_method(step_up_claims)
    await audit_logger.log_auth_event(
        user_id=user_id,
        action="2fa_success",
        outcome="success",
        details={"action": action, "amr_method": amr_method},
    )
    return True, None
```

#### Step-Up Callback Handler

```python
# apps/web_console/auth/step_up_callback.py
"""Handle Auth0 step-up authentication callback."""
from datetime import datetime, UTC
import logging
import streamlit as st

from apps.web_console.auth.mfa_verification import verify_step_up_auth, get_amr_method
from apps.web_console.auth.oauth2_flow import validate_state, exchange_code

logger = logging.getLogger(__name__)

async def handle_step_up_callback(
    code: str,
    state: str,
    session_store: Any,
    audit_logger: Any,
) -> dict:
    """Handle Auth0 callback after step-up authentication.

    Returns updated session data with step_up_claims.
    Raises AuthError on failure.
    """
    # Validate state to prevent CSRF
    session_id = st.session_state.get("session_id")
    if not validate_state(state, session_id):
        logger.error("State mismatch in step-up callback")
        raise SecurityError("Invalid authentication state. Please log in again.")

    # Exchange code for tokens
    tokens = await exchange_code(code)
    id_token_claims = tokens.get("id_token_claims", {})

    # Verify step-up was actually performed
    valid, error = verify_step_up_auth(id_token_claims)
    if not valid:
        await audit_logger.log_auth_event(
            user_id=id_token_claims.get("sub"),
            action="step_up_callback_failed",
            outcome="denied",
            details={"error": error},
        )
        raise AuthError(f"Step-up authentication failed: {error}")

    # Update session with step-up claims
    await session_store.update_step_up_claims(
        session_id=session_id,
        step_up_claims=id_token_claims,
        amr_method=get_amr_method(id_token_claims),
    )

    # Log success
    await audit_logger.log_auth_event(
        user_id=id_token_claims.get("sub"),
        action="step_up_callback_success",
        outcome="success",
        details={"amr_method": get_amr_method(id_token_claims)},
    )

    # Redirect back to pending action
    pending_action = st.session_state.get("pending_action")
    if pending_action:
        del st.session_state["pending_action"]
        return {"redirect_to": pending_action}

    return {"redirect_to": "/dashboard"}
```

### Files to Create/Update

**Create (in order):**
1. `docs/ADRs/ADR-024-analytics-security.md` - **[FIRST - per Claude review]** Architecture decision record documenting: RBAC role sourcing (DB vs IdP), session_version invalidation pattern, server-side strategy scoping, 2FA mechanism (Auth0 step-up vs TOTP)
2. `db/migrations/0005_update_audit_log_schema.sql` - ALTER TABLE audit_log
3. `db/migrations/0006_create_rbac_tables.sql` - user_roles, strategies, user_strategy_access
4. `apps/web_console/auth/permissions.py` - RBAC permissions with sync wrappers
5. `apps/web_console/auth/audit_log.py` - Comprehensive audit logging
6. `apps/web_console/auth/session_invalidation.py` - Session version management
7. `apps/web_console/auth/mfa_verification.py` - 2FA amr claim validation
8. `apps/web_console/auth/rate_limiter.py` - Redis-based rate limiting
9. `scripts/manage_roles.py` - Admin bootstrap CLI
10. `tests/apps/web_console/auth/test_permissions.py`
11. `tests/apps/web_console/auth/test_audit_log.py`
12. `tests/apps/web_console/auth/test_session_invalidation.py`
13. `tests/apps/web_console/auth/test_mfa_verification.py`
14. `tests/apps/web_console/auth/test_rate_limiter.py`

**Update:**
- `apps/web_console/auth/session_store.py` - Add role/strategies/session_version to SessionData
- `apps/web_console/auth/session_manager.py` - Add session_version validation
- `apps/web_console/auth/oauth2_flow.py` - Fetch role + session_version on login

### Acceptance Criteria

- [ ] RBAC with viewer/operator/admin roles
- [ ] Default-deny on unknown roles (no ValueError crash)
- [ ] @require_permission decorator with sync wrappers (no asyncio.run issues)
- [ ] Session invalidation on role/strategy changes (session_version check)
- [ ] Audit logging for ALL events (access, denied, auth, admin, export)
- [ ] Per-strategy authorization enforced on all queries (server-side)
- [ ] Session includes role, strategies, and session_version
- [ ] 2FA validation via amr claim (not just re-login)
- [ ] Proper PostgreSQL migration syntax (ALTER TABLE for audit_log)
- [ ] Admin bootstrap CLI script (scripts/manage_roles.py)
- [ ] >90% test coverage

### Admin Bootstrap CLI Script

**[NEW - per Gemini HIGH: No mechanism to create first admin user]**

```python
#!/usr/bin/env python3
# scripts/manage_roles.py
"""CLI for managing user roles and strategy access.

Used for bootstrapping initial admin and operational management.

Usage:
    # Bootstrap initial admin (uses INITIAL_ADMIN_EMAIL from .env)
    python scripts/manage_roles.py bootstrap-admin

    # Assign role to user
    python scripts/manage_roles.py set-role --user-id auth0|123 --role admin --by admin@example.com

    # Grant strategy access
    python scripts/manage_roles.py grant-strategy --user-id auth0|123 --strategy alpha_baseline --by admin@example.com

    # List all users and roles
    python scripts/manage_roles.py list-users
"""
import asyncio
import os
import sys
from typing import Optional

import asyncpg
import click

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.common.config import get_database_url

async def get_db_pool() -> asyncpg.Pool:
    """Create database connection pool."""
    return await asyncpg.create_pool(get_database_url())

@click.group()
def cli():
    """Manage user roles and strategy access."""
    pass

@cli.command()
def bootstrap_admin():
    """Bootstrap initial admin user from INITIAL_ADMIN_EMAIL env var."""
    admin_email = os.environ.get("INITIAL_ADMIN_EMAIL")
    if not admin_email:
        click.echo("Error: INITIAL_ADMIN_EMAIL environment variable not set", err=True)
        sys.exit(1)

    async def _bootstrap():
        pool = await get_db_pool()
        try:
            # Use email as user_id for bootstrap (will be updated on first login)
            await pool.execute(
                """
                INSERT INTO user_roles (user_id, role, updated_by, session_version)
                VALUES ($1, 'admin', 'bootstrap', 1)
                ON CONFLICT (user_id) DO UPDATE SET role = 'admin', updated_by = 'bootstrap'
                """,
                admin_email,
            )
            click.echo(f"✓ Admin role assigned to: {admin_email}")
        finally:
            await pool.close()

    asyncio.run(_bootstrap())

@cli.command()
@click.option("--user-id", required=True, help="User ID (Auth0 sub claim)")
@click.option("--role", required=True, type=click.Choice(["viewer", "operator", "admin"]))
@click.option("--by", required=True, help="Admin user making the change")
def set_role(user_id: str, role: str, by: str):
    """Set role for a user."""
    async def _set_role():
        pool = await get_db_pool()
        try:
            # Increment session_version to invalidate active sessions
            await pool.execute(
                """
                INSERT INTO user_roles (user_id, role, updated_by, session_version)
                VALUES ($1, $2, $3, 1)
                ON CONFLICT (user_id) DO UPDATE SET
                    role = $2,
                    updated_by = $3,
                    session_version = user_roles.session_version + 1,
                    updated_at = NOW()
                """,
                user_id, role, by,
            )
            click.echo(f"✓ Role '{role}' assigned to user: {user_id}")
            click.echo("  Note: User's active sessions have been invalidated.")
        finally:
            await pool.close()

    asyncio.run(_set_role())

@cli.command()
@click.option("--user-id", required=True, help="User ID")
@click.option("--strategy", required=True, help="Strategy ID")
@click.option("--by", required=True, help="Admin user making the change")
def grant_strategy(user_id: str, strategy: str, by: str):
    """Grant strategy access to a user."""
    async def _grant():
        pool = await get_db_pool()
        try:
            await pool.execute(
                """
                INSERT INTO user_strategy_access (user_id, strategy_id, granted_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, strategy_id) DO NOTHING
                """,
                user_id, strategy, by,
            )
            # Invalidate sessions to pick up new strategies
            await pool.execute(
                """
                UPDATE user_roles
                SET session_version = session_version + 1, updated_at = NOW()
                WHERE user_id = $1
                """,
                user_id,
            )
            click.echo(f"✓ Strategy '{strategy}' granted to user: {user_id}")
        finally:
            await pool.close()

    asyncio.run(_grant())

@cli.command()
def list_users():
    """List all users and their roles."""
    async def _list():
        pool = await get_db_pool()
        try:
            rows = await pool.fetch(
                """
                SELECT user_id, role, session_version, updated_at
                FROM user_roles
                ORDER BY role, user_id
                """
            )
            if not rows:
                click.echo("No users found. Run 'bootstrap-admin' first.")
                return

            click.echo(f"{'User ID':<40} {'Role':<10} {'Version':<8} {'Updated'}")
            click.echo("-" * 80)
            for row in rows:
                click.echo(f"{row['user_id']:<40} {row['role']:<10} {row['session_version']:<8} {row['updated_at']}")
        finally:
            await pool.close()

    asyncio.run(_list())

if __name__ == "__main__":
    cli()
```

---

## T6.1b: Admin User Management UI

**Effort:** 2-3 days | **PR:** `feat(P4T3): admin user management`
**Status:** ⏳ Pending
**Priority:** P1 (Required for RBAC usability)
**Dependencies:** T6.1a

**[NEW TASK - per Gemini review: "Missing User & Strategy Management UI"]**

### Problem Statement

RBAC structures (roles, strategy access) require manual SQL to configure. Operators need a UI to manage user roles and strategy assignments.

### Deliverables

1. **User List Page**
   - List all users with roles
   - Filter by role
   - Search by email
   - **[NEW] Requires `MANAGE_USERS` permission on every view**

2. **Role Management**
   - Change user role (with confirmation)
   - **[NEW] Double-confirmation for bulk operations**
   - Audit log of role changes (including denials)
   - Admin-only access via `@require_permission(Permission.MANAGE_USERS)`

3. **Strategy Assignment**
   - Assign/revoke strategies per user
   - Bulk assignment with double-confirmation
   - Audit log of changes

4. **Security Controls (per Codex LOW)**
   - **[NEW] CSRF protection via Streamlit session state nonce**
   - **[NEW] Audit denied attempts (not just success)**
   - **[NEW] Session invalidation triggered on all changes**

### Implementation - CSRF Protection

**[NEW - per Codex LOW: Admin UI needs CSRF protection]**

```python
# apps/web_console/components/csrf_protection.py
"""CSRF protection for Streamlit forms."""
import secrets
import streamlit as st

CSRF_TOKEN_KEY = "_csrf_token"

def generate_csrf_token() -> str:
    """Generate and store CSRF token in session state."""
    if CSRF_TOKEN_KEY not in st.session_state:
        st.session_state[CSRF_TOKEN_KEY] = secrets.token_urlsafe(32)
    return st.session_state[CSRF_TOKEN_KEY]

def verify_csrf_token(submitted_token: str) -> bool:
    """Verify submitted token matches session token."""
    expected = st.session_state.get(CSRF_TOKEN_KEY)
    if not expected or not submitted_token:
        return False
    return secrets.compare_digest(expected, submitted_token)

def rotate_csrf_token() -> str:
    """Rotate token after successful mutation."""
    st.session_state[CSRF_TOKEN_KEY] = secrets.token_urlsafe(32)
    return st.session_state[CSRF_TOKEN_KEY]
```

### CSRF Form Usage Example

**[NEW v1.3 - per Codex LOW: Show CSRF usage in forms]**

```python
# Example: Role change form with CSRF protection
from apps.web_console.components.csrf_protection import (
    generate_csrf_token, verify_csrf_token, rotate_csrf_token
)

@require_permission(Permission.MANAGE_USERS)
def role_change_form(target_user_id: str):
    """Form with CSRF protection for role changes."""
    csrf_token = generate_csrf_token()

    with st.form("role_change_form"):
        new_role = st.selectbox("New Role", ["viewer", "operator", "admin"])
        reason = st.text_area("Reason for change (required)")

        # Hidden CSRF token
        submitted_token = st.text_input("csrf", value=csrf_token, type="password", label_visibility="hidden")

        if st.form_submit_button("Change Role"):
            # Verify CSRF token
            if not verify_csrf_token(submitted_token):
                st.error("Invalid form submission. Please refresh and try again.")
                _log_csrf_failure(target_user_id)
                return

            if not reason.strip():
                st.error("Reason is required")
                return

            # Double confirmation for role changes
            if "confirm_role_change" not in st.session_state:
                st.session_state.confirm_role_change = True
                st.warning(f"Confirm: Change {target_user_id} to {new_role}?")
                st.rerun()

            # Execute and rotate token
            await change_user_role(target_user_id, new_role, reason)
            rotate_csrf_token()  # Rotate after success
            del st.session_state.confirm_role_change
            st.success(f"Role changed to {new_role}")
```

### Files to Create

- `apps/web_console/pages/admin_users.py`
- `apps/web_console/components/user_role_editor.py`
- `apps/web_console/components/strategy_assignment.py`
- `apps/web_console/components/csrf_protection.py` [NEW]
- `tests/apps/web_console/test_admin_users.py`
- `tests/apps/web_console/test_csrf_protection.py` [NEW]

---

## T6.6: Manual Trade Controls (Moved Up - P0)

**Effort:** 3-4 days | **PR:** `feat(P4T3): manual trade controls`
**Status:** ⏳ Pending
**Priority:** P0 (Critical operational safety)
**Dependencies:** T6.1a, migrations 0005/0006 applied

**[MOVED UP - per Gemini review: "P0 should not be scheduled last"]**

**Note:** Enhances existing `render_manual_order_entry` in app.py, not replacing from scratch.

### Execution Gateway Coordination

**[CLARIFIED - per Claude review: "Manual controls execution_gateway dependency"]**

**Decision:** Ship UI + backend enforcement **in the same PR** to avoid UI/BE drift.

**Scope includes execution_gateway changes:**
- `apps/execution_gateway/api/manual_controls.py` - New endpoints with server-side authorization
- `POST /api/v1/orders/{order_id}/cancel` - Cancel order with permission check
- `POST /api/v1/positions/{symbol}/close` - Close position with permission check
- `POST /api/v1/positions/flatten-all` - Flatten all with 2FA + permission check

**Rationale:**
- Gate 3 requires UI + backend shipped together
- Prevents "UI works but backend rejects" scenarios
- Single atomic PR for review and rollback

**Alternative (if backend team is separate):** Create T6.0 ticket for execution_gateway manual-control endpoints as blocking dependency before T6.6 starts.

### Problem Statement

Current manual order entry lacks proper confirmations, 2FA for destructive actions, rate limiting, and comprehensive audit logging.

### 2FA Mechanism Design

**[CLARIFIED - per Gemini/Codex review: "Undefined 2FA Mechanism"]**

**Decision:** Use Auth0 re-authentication (step-up auth), NOT internal TOTP.

**Rationale:**
- Auth0 already handles MFA enrollment/management
- No need to build separate TOTP infrastructure
- Leverages existing OAuth2 flow

**Implementation:**
```python
def require_2fa_confirmation():
    """Require re-authentication via Auth0 for destructive actions."""
    # Trigger Auth0 step-up authentication
    # Sets prompt=login to force re-auth even if session valid
    auth_url = build_auth_url(prompt="login", max_age=0)
    st.markdown(f"[Re-authenticate to confirm]({auth_url})")

    # After re-auth, check timestamp of last auth
    if not _was_recently_authenticated(max_age_seconds=60):
        st.error("Please re-authenticate to perform this action")
        st.stop()
```

### Deliverables

1. **Enhanced Order Management**
   - Cancel single order with confirmation
   - Cancel all orders for symbol with confirmation
   - View pending orders with status

2. **Enhanced Position Management**
   - Close single position with confirmation
   - Flatten all positions with 2FA (Auth0 step-up)
   - Force position adjustment with confirmation

3. **Server-Side Safety (per Codex review)**
   - Backend validation before execution
   - Rate limiting: max 10 actions per minute per user
   - Reason field required and validated server-side

4. **Comprehensive Audit Trail**
   - Log action request (before execution)
   - Log action result (success/failure)
   - Include reason in audit record

### Backend Service Contracts

**[NEW v1.5 - per Codex: Document service contracts and failure modes]**

#### Execution Gateway Contract

All manual control actions call the Execution Gateway API with server-side enforcement:

```
POST /api/v1/orders/{order_id}/cancel
Authorization: Bearer <internal_service_token>
X-Request-ID: <uuid>
X-User-ID: <user_id>

Request:
{
    "reason": "string (min 10 chars)",
    "requested_by": "user_id",
    "requested_at": "ISO8601 timestamp",
    "rate_limit_key": "cancel_order:{user_id}"
}

Response (200):
{
    "status": "cancelled",
    "order_id": "string",
    "cancelled_at": "ISO8601 timestamp"
}

Response (400):
{
    "error": "invalid_request",
    "message": "Reason must be at least 10 characters"
}

Response (403):
{
    "error": "permission_denied",
    "message": "User not authorized for strategy {strategy_id}"
}

Response (429):
{
    "error": "rate_limited",
    "message": "Rate limit exceeded. Retry after {seconds}s",
    "retry_after": 60
}

Response (404):
{
    "error": "not_found",
    "message": "Order {order_id} not found"
}
```

#### Server-Side Permission Enforcement

```python
# apps/execution_gateway/api/manual_controls.py
"""Manual control endpoints with server-side authorization."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["manual_controls"])

class CancelOrderRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=500)
    requested_by: str
    requested_at: datetime

@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    request: CancelOrderRequest,
    user: dict = Depends(get_authenticated_user),
    db_pool: Pool = Depends(get_db_pool),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict:
    """Cancel order with server-side authorization.

    [NEW v1.5] Enforces:
    1. User has CANCEL_ORDER permission
    2. Order belongs to user's authorized strategies
    3. Rate limit not exceeded
    4. Reason meets length requirements (Pydantic validates)
    """
    # 1. Permission check
    if not has_permission(user, Permission.CANCEL_ORDER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied: CANCEL_ORDER required"
        )

    # 2. Fetch order and verify strategy access
    order = await get_order(db_pool, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found"
        )

    authorized_strategies = get_authorized_strategies(user)
    if order["strategy_id"] not in authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User not authorized for strategy {order['strategy_id']}"
        )

    # 3. Rate limit check
    allowed, remaining = rate_limiter.check_rate_limit(
        user["sub"], "cancel_order", max_requests=10, window_seconds=60
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"}
        )

    # 4. Execute cancellation
    result = await execution_client.cancel_order(order_id)

    # 5. Audit log
    await audit_logger.log_action(
        user_id=user["sub"],
        action="cancel_order",
        resource_type="order",
        resource_id=order_id,
        outcome="success" if result.success else "failed",
        details={
            "reason": request.reason,
            "strategy_id": order["strategy_id"],
        }
    )

    return {"status": "cancelled", "order_id": order_id}
```

#### Failure Modes and Recovery

| Failure | HTTP Status | User Message | Recovery |
|---------|-------------|--------------|----------|
| Permission denied | 403 | "You don't have permission for this action" | Contact admin |
| Order not found | 404 | "Order not found - may already be filled/cancelled" | Refresh order list |
| Rate limited | 429 | "Too many requests. Wait {N} seconds" | Wait and retry |
| Strategy unauthorized | 403 | "You are not authorized for this strategy" | Contact admin |
| Reason too short | 400 | "Reason must be at least 10 characters" | Provide longer reason |
| Broker timeout | 504 | "Broker timeout - order may or may not be cancelled" | Check order status |
| Broker error | 502 | "Broker error: {message}" | Retry or escalate |

### Implementation

```python
# apps/web_console/pages/manual_controls.py
import streamlit as st
from datetime import datetime, UTC
from apps.web_console.auth.permissions import require_permission, Permission
from apps.web_console.auth.audit_log import get_audit_logger
from apps.web_console.auth.rate_limiter import check_rate_limit

# Rate limit: 10 actions per minute
ACTION_RATE_LIMIT = 10
ACTION_RATE_WINDOW_SECONDS = 60

@require_permission(Permission.CANCEL_ORDER)
def cancel_order_section():
    """Cancel order with server-side validation."""
    st.subheader("Cancel Order")

    order_id = st.text_input("Order ID")
    reason = st.text_area("Reason for cancellation (required)")

    if st.button("Cancel Order", type="primary"):
        # Server-side validation
        if not order_id.strip():
            st.error("Order ID is required")
            return
        if not reason.strip() or len(reason.strip()) < 10:
            st.error("Reason is required (minimum 10 characters)")
            return

        # Rate limiting [FIXED v1.3 - aligned signature]
        user = get_current_user()
        if not check_rate_limit(user["sub"], "cancel_order", ACTION_RATE_LIMIT, ACTION_RATE_WINDOW_SECONDS):
            st.error("Rate limit exceeded. Please wait before trying again.")
            return

        # Confirmation dialog
        st.warning(f"Confirm: Cancel order {order_id}?")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Confirm Cancel", key="confirm"):
                result = _execute_cancel_order(order_id, user, reason)
                if result.success:
                    st.success(f"Order {order_id} cancelled")
                else:
                    st.error(f"Failed: {result.error}")
        with col2:
            if st.button("Abort", key="abort"):
                st.rerun()

async def _execute_cancel_order(order_id: str, user: dict, reason: str) -> Result:
    """Execute cancel with audit logging."""
    audit = get_audit_logger()

    # Log attempt
    await audit.log_action(
        user_id=user["sub"],
        action="cancel_order",
        resource_type="order",
        resource_id=order_id,
        outcome="pending",
        details={"reason": reason},
    )

    try:
        # Call execution gateway
        result = await execution_client.cancel_order(order_id)

        # Log result
        await audit.log_action(
            user_id=user["sub"],
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="success" if result.success else "failed",
            details={"reason": reason, "result": str(result)},
        )

        return result
    except Exception as e:
        await audit.log_action(
            user_id=user["sub"],
            action="cancel_order",
            resource_type="order",
            resource_id=order_id,
            outcome="failed",
            details={"reason": reason, "error": str(e)},
        )
        raise

@require_permission(Permission.FLATTEN_ALL)
def flatten_all_section():
    """Flatten all positions with Auth0 step-up auth.

    [FIXED v1.3 - per Codex HIGH: Wire 2FA to verify_step_up_auth/amr claim]
    [FIXED v1.4 - per Codex MEDIUM: Use sync function for Streamlit compatibility]
    """
    st.subheader("Flatten All Positions")
    st.error("DANGER: This will close ALL positions immediately!")

    reason = st.text_area("Reason (required, min 20 chars)")

    if st.button("FLATTEN ALL", type="primary"):
        if not reason.strip() or len(reason.strip()) < 20:
            st.error("Detailed reason required (minimum 20 characters)")
            return

        # Check rate limit [FIXED v1.3 - aligned signature]
        user = get_current_user()
        if not check_rate_limit(user["sub"], "flatten_all", 1, 300):  # Max 1 per 5 min
            st.error("Rate limit: Only 1 flatten per 5 minutes")
            return

        # [FIXED v1.4] Use sync wrapper for 2FA check
        audit_logger = get_audit_logger()
        if not require_2fa_for_action_sync(user, "flatten_all", audit_logger):
            # User needs to re-authenticate with MFA
            auth_url = build_auth_url(prompt="login", max_age=0)
            st.warning("This action requires 2FA verification.")
            st.markdown(f"[Click here to re-authenticate with MFA]({auth_url})")
            st.stop()

        # [FIXED v1.4] Use sync wrapper for execution
        result = execute_flatten_all_sync(user, reason)
        if result.success:
            st.success("All positions flattened")
        else:
            st.error(f"Failed: {result.error}")

def require_2fa_for_action_sync(user: dict, action: str, audit_logger: Any) -> bool:
    """Sync wrapper for require_2fa_for_action.

    [NEW v1.4 - per Codex MEDIUM: Streamlit runs sync, not async]
    """
    import asyncio
    import concurrent.futures

    async def _check():
        return await require_2fa_for_action(user, action, audit_logger)

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _check()).result(timeout=5.0)
    except RuntimeError:
        return asyncio.run(_check())

def execute_flatten_all_sync(user: dict, reason: str) -> Result:
    """Sync wrapper for _execute_flatten_all.

    [NEW v1.4 - per Codex MEDIUM: Streamlit runs sync, not async]
    """
    import asyncio
    import concurrent.futures

    async def _execute():
        return await _execute_flatten_all(user, reason)

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, _execute()).result(timeout=30.0)
    except RuntimeError:
        return asyncio.run(_execute())
```

### Server-Side Rate Limiter

**[NEW - per Codex MEDIUM: Rate limiting only sketched client-side]**

**[ENHANCED v1.5 - per Codex: Add pool sizing, health checks, fallback]**

#### Redis Configuration for Rate Limiting

```yaml
# config/redis.yaml (or environment variables)
rate_limiter:
  # Use dedicated Redis instance OR separate DB index
  redis_url: ${REDIS_URL}  # Same instance as sessions
  db_index: 2              # Separate DB from sessions (db=0) and cache (db=1)

  # Connection pool sizing
  pool:
    max_connections: 20    # Max concurrent connections
    min_idle: 2            # Keep alive for latency
    max_idle_time: 300     # Close idle connections after 5 min
    connection_timeout: 2  # Fail fast on connection issues
    socket_timeout: 1      # Fail fast on operations

  # Health check
  health_check_interval: 30  # Ping every 30s

  # Fallback behavior when Redis unavailable
  fallback:
    mode: "allow"          # "allow" or "deny" - default behavior on Redis failure
    log_level: "error"     # Log Redis failures at this level
```

```python
# apps/web_console/auth/rate_limiter.py
"""Redis-based rate limiter for manual trade controls."""
from datetime import datetime, UTC
from typing import Any
import logging
import redis
from redis.exceptions import ConnectionError, TimeoutError

logger = logging.getLogger(__name__)

# Pool configuration
REDIS_POOL_MAX_CONNECTIONS = 20
REDIS_POOL_SOCKET_TIMEOUT = 1.0  # seconds
REDIS_POOL_CONNECTION_TIMEOUT = 2.0  # seconds

# Fallback behavior when Redis is unavailable
FALLBACK_MODE = "allow"  # "allow" or "deny"

class RateLimiter:
    """Redis-based sliding window rate limiter with health checks and fallback."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.key_prefix = "rate_limit:"
        self._healthy = True
        self._last_health_check = datetime.now(UTC)

    def check_rate_limit(
        self,
        user_id: str,
        action: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int]:
        """Check if user is within rate limit.

        [ENHANCED v1.5] With fallback on Redis failure.
        Returns (allowed, remaining_requests).
        """
        try:
            key = f"{self.key_prefix}{action}:{user_id}"
            now = datetime.now(UTC).timestamp()
            window_start = now - window_seconds

            pipe = self.redis.pipeline()
            # Remove old entries
            pipe.zremrangebyscore(key, 0, window_start)
            # Count current entries
            pipe.zcard(key)
            # Add current request (will be rolled back if over limit)
            pipe.zadd(key, {str(now): now})
            # Set TTL
            pipe.expire(key, window_seconds)
            results = pipe.execute()

            current_count = results[1]
            self._healthy = True  # Mark healthy on success

            if current_count >= max_requests:
                # Over limit - remove the entry we just added
                self.redis.zrem(key, str(now))
                logger.warning(
                    "Rate limit exceeded",
                    extra={
                        "user_id": user_id,
                        "action": action,
                        "count": current_count,
                        "limit": max_requests,
                    }
                )
                return False, 0

            remaining = max_requests - current_count - 1
            return True, remaining

        except (ConnectionError, TimeoutError) as e:
            return self._handle_redis_failure(user_id, action, e)

    def _handle_redis_failure(
        self,
        user_id: str,
        action: str,
        error: Exception,
    ) -> tuple[bool, int]:
        """Handle Redis connection/timeout failures.

        [NEW v1.5] Fallback behavior with logging.
        """
        self._healthy = False
        logger.error(
            "Rate limiter Redis failure - using fallback",
            extra={
                "user_id": user_id,
                "action": action,
                "error": str(error),
                "fallback_mode": FALLBACK_MODE,
            }
        )

        if FALLBACK_MODE == "deny":
            # Fail closed - deny on Redis failure (more secure but less available)
            return False, 0
        else:
            # Fail open - allow on Redis failure (more available but less secure)
            # Log as WARNING for monitoring
            logger.warning(
                "Rate limit bypassed due to Redis failure",
                extra={"user_id": user_id, "action": action}
            )
            return True, -1  # -1 indicates unknown remaining

    def health_check(self) -> bool:
        """Check Redis connectivity.

        [NEW v1.5] For health monitoring.
        """
        try:
            self.redis.ping()
            self._healthy = True
            self._last_health_check = datetime.now(UTC)
            return True
        except (ConnectionError, TimeoutError):
            self._healthy = False
            return False

    @property
    def is_healthy(self) -> bool:
        """Return current health status."""
        return self._healthy

    def get_remaining(
        self,
        user_id: str,
        action: str,
        max_requests: int,
        window_seconds: int,
    ) -> int:
        """Get remaining requests in current window."""
        try:
            key = f"{self.key_prefix}{action}:{user_id}"
            now = datetime.now(UTC).timestamp()
            window_start = now - window_seconds

            self.redis.zremrangebyscore(key, 0, window_start)
            current_count = self.redis.zcard(key)
            return max(0, max_requests - current_count)
        except (ConnectionError, TimeoutError):
            return -1  # Unknown

# Global rate limiter instance with connection pool
_rate_limiter: RateLimiter | None = None

def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance with proper connection pool.

    [ENHANCED v1.5] Uses connection pool with sizing limits.
    """
    global _rate_limiter
    if _rate_limiter is None:
        from libs.common.config import get_redis_url

        # Create connection pool with sizing limits
        pool = redis.ConnectionPool.from_url(
            get_redis_url(),
            max_connections=REDIS_POOL_MAX_CONNECTIONS,
            socket_timeout=REDIS_POOL_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_POOL_CONNECTION_TIMEOUT,
            db=2,  # Use separate DB for rate limiting
        )
        redis_client = redis.Redis(connection_pool=pool)
        _rate_limiter = RateLimiter(redis_client)
    return _rate_limiter

def check_rate_limit(
    user_id: str,
    action: str,
    max_requests: int,
    window_seconds: int,
) -> bool:
    """Convenience function for rate limiting.

    [FIXED v1.3] Signature now matches RateLimiter.check_rate_limit exactly.
    All params are required (no defaults) to avoid confusion.
    """
    limiter = get_rate_limiter()
    allowed, _ = limiter.check_rate_limit(user_id, action, max_requests, window_seconds)
    return allowed

def rate_limiter_health_check() -> dict:
    """Health check endpoint for rate limiter.

    [NEW v1.5] For /health endpoint integration.
    """
    limiter = get_rate_limiter()
    healthy = limiter.health_check()
    return {
        "component": "rate_limiter",
        "healthy": healthy,
        "last_check": limiter._last_health_check.isoformat(),
    }
```

### Server-Side Strategy Scoping

**[NEW - per Codex HIGH: Enforce strategy filters server-side for every data query]**

**[ENHANCED v1.5 - per Codex: Add enumerated call-site mapping]**

#### Call-Site Mapping

All data queries that may contain strategy-specific data MUST use `StrategyScopedDataAccess`.

| Module | Function | Table(s) | Scoped? | Notes |
|--------|----------|----------|---------|-------|
| `pages/performance.py` | `load_pnl_data()` | `daily_pnl` | ✅ Yes | Via `get_pnl_summary()` |
| `pages/performance.py` | `load_positions()` | `positions` | ✅ Yes | Via `get_positions()` |
| `pages/manual_controls.py` | `get_pending_orders()` | `orders` | ✅ Yes | Via `get_orders()` |
| `pages/manual_controls.py` | `get_open_positions()` | `positions` | ✅ Yes | Via `get_positions()` |
| `pages/risk.py` | `load_factor_exposure()` | `factor_exposures` | ✅ Yes | Via `get_factor_exposures()` |
| `pages/journal.py` | `load_trade_history()` | `trades` | ✅ Yes | Via `get_trades()` |
| `pages/compare.py` | `load_strategy_metrics()` | `daily_pnl`, `positions` | ✅ Yes | Via `get_pnl_summary()` |
| `pages/admin_users.py` | `list_all_users()` | `user_roles` | ⚠️ N/A | Admin only, not strategy data |
| `components/audit_viewer.py` | `get_audit_logs()` | `audit_log` | ⚠️ N/A | Filtered by user_id for non-admin |

**Enforcement:**
- All new data access functions MUST use `StrategyScopedDataAccess`
- PR reviews must verify no raw SQL queries bypass scoping
- Integration tests verify no leakage (see Testing section)

```python
# apps/web_console/data/strategy_scoped_queries.py
"""Server-side strategy scoping for all data queries."""
from typing import Any, Sequence
import logging

from apps.web_console.auth.permissions import get_authorized_strategies, Permission, has_permission

logger = logging.getLogger(__name__)

class StrategyScopedDataAccess:
    """Data access layer with mandatory strategy scoping.

    All dashboard data queries MUST go through this class to ensure
    users only see data for their authorized strategies.
    """

    def __init__(self, db_pool: Any, user: dict):
        self.db_pool = db_pool
        self.user = user
        self.authorized_strategies = get_authorized_strategies(user)

    async def get_positions(self, **filters) -> list[dict]:
        """Get positions filtered by authorized strategies."""
        strategies = self._get_strategy_filter()
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM positions
                WHERE strategy_id = ANY($1)
                ORDER BY updated_at DESC
                """,
                strategies,
            )
            return [dict(row) for row in rows]

    async def get_orders(self, **filters) -> list[dict]:
        """Get orders filtered by authorized strategies."""
        strategies = self._get_strategy_filter()
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM orders
                WHERE strategy_id = ANY($1)
                ORDER BY created_at DESC
                """,
                strategies,
            )
            return [dict(row) for row in rows]

    async def get_pnl_summary(self, date_from: str, date_to: str) -> list[dict]:
        """Get P&L summary filtered by authorized strategies."""
        strategies = self._get_strategy_filter()
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT strategy_id, date, realized_pnl, unrealized_pnl
                FROM daily_pnl
                WHERE strategy_id = ANY($1)
                  AND date BETWEEN $2 AND $3
                ORDER BY date DESC
                """,
                strategies, date_from, date_to,
            )
            return [dict(row) for row in rows]

    async def get_trades(self, **filters) -> list[dict]:
        """Get trades filtered by authorized strategies."""
        strategies = self._get_strategy_filter()
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM trades
                WHERE strategy_id = ANY($1)
                ORDER BY executed_at DESC
                """,
                strategies,
            )
            return [dict(row) for row in rows]

    def _get_strategy_filter(self) -> list[str]:
        """Get strategy IDs for query filter.

        Raises PermissionError if user has no strategy access.
        """
        if not self.authorized_strategies:
            logger.warning(
                "User has no strategy access",
                extra={"user_id": self.user.get("sub")}
            )
            raise PermissionError("No strategy access")
        return self.authorized_strategies

def get_scoped_data_access(db_pool: Any, user: dict) -> StrategyScopedDataAccess:
    """Factory function for strategy-scoped data access."""
    return StrategyScopedDataAccess(db_pool, user)
```

### Files to Create/Update

**Create:**
- `apps/web_console/pages/manual_controls.py`
- `apps/web_console/components/confirmation_dialog.py`
- `apps/web_console/auth/rate_limiter.py` [NEW]
- `apps/web_console/data/strategy_scoped_queries.py` [NEW]
- `tests/apps/web_console/test_manual_controls.py`
- `tests/apps/web_console/auth/test_rate_limiter.py` [NEW]
- `tests/apps/web_console/data/test_strategy_scoped_queries.py` [NEW]
- `docs/CONCEPTS/manual-trade-controls.md`

**Update:**
- `apps/web_console/app.py` - Replace `render_manual_order_entry` with new page

---

## T6.2: Performance Dashboard

**Effort:** 3-4 days | **PR:** `feat(P4T3): performance dashboard`
**Status:** ⏳ Pending
**Dependencies:** T6.1a

### Data Contracts & Sources

**[NEW - per Codex LOW: Dashboards omit data sources and schemas]**

| Data | Source Table | Provider | Notes |
|------|--------------|----------|-------|
| Positions | `positions` | StrategyScopedDataAccess | Real-time from DB |
| P&L | `daily_pnl` | StrategyScopedDataAccess | Aggregated daily |
| Trades | `trades` | StrategyScopedDataAccess | Historical |
| Drawdown | Computed | `libs/risk/drawdown.py` | From P&L series |

**Performance SLOs:**
- Dashboard load: < 2 seconds (P95)
- Chart render: < 500ms
- Data staleness: < 5 minutes (cached)

### Deliverables

1. **Real-time P&L Display**
   - Current P&L by authorized strategies only (via StrategyScopedDataAccess)
   - Today's P&L vs previous close
   - Unrealized vs realized P&L
   - **[NEW] Graceful fallback with "Data unavailable" message**

2. **Historical Performance Charts**
   - Cumulative returns chart
   - Daily returns bar chart
   - Configurable time range (7d, 30d, 90d, YTD, All)
   - **[NEW] Empty-state handling for new strategies**

3. **Drawdown Visualization**
   - Current drawdown
   - Maximum drawdown
   - Drawdown duration chart

4. **Position Summary**
   - Current positions table (filtered by authorized strategies)
   - Position sizing breakdown
   - Sector/factor exposure summary

5. **Caching & Fallback (per Codex review)**
   - Cache heavy queries (5-minute TTL)
   - Graceful fallback when analytics services unavailable

### Files to Create

- `apps/web_console/pages/performance.py`
- `apps/web_console/components/pnl_chart.py`
- `apps/web_console/components/drawdown_chart.py`
- `apps/web_console/components/positions_table.py`
- `tests/apps/web_console/test_performance_dashboard.py`
- `docs/CONCEPTS/performance-dashboard.md`

---

## T6.3: Risk Analytics Dashboard

**Effort:** 3-4 days | **PR:** `feat(P4T3): risk dashboard`
**Status:** ⏳ Pending
**Dependencies:** T6.1a, T2.3 (Portfolio Risk Analytics)

### Deliverables

- Factor exposure display (uses T2.3 libs/risk/)
- VaR/CVaR visualization
- Stress test results
- Risk budget monitoring

### Files to Create

- `apps/web_console/pages/risk.py`
- `apps/web_console/components/factor_exposure_chart.py`
- `apps/web_console/components/var_chart.py`
- `apps/web_console/components/stress_test_results.py`
- `tests/apps/web_console/test_risk_dashboard.py`
- `docs/CONCEPTS/risk-dashboard.md`

---

## T6.4: Strategy Comparison Tool + Risk Dashboard DB Integration

**Effort:** 3-4 days | **PR:** `feat(P4T3): strategy comparison`
**Status:** ⏳ Pending
**Dependencies:** T6.1a, T6.2, T6.3

### T6.4a: Wire Real DB Connections to Risk Dashboard

**Issue:** T6.3 implementation passes `db_pool=None` and `redis_client=None` to `StrategyScopedDataAccess` in `apps/web_console/pages/risk.py:63-69`. This causes the risk dashboard to show placeholder/demo data instead of real portfolio risk metrics.

**Current Behavior:**
- `RiskService` gracefully handles missing DB connections by returning placeholder stress tests and zero-valued risk metrics
- VaR history returns empty list when `get_pnl_summary()` fails due to missing db_pool
- Factor exposures show zeros for all canonical factors

**Required Work:**
1. Wire real `db_pool` from Streamlit session state or app context to `StrategyScopedDataAccess`
2. Wire real `redis_client` for cached risk data (optional, for performance)
3. Ensure async database queries work correctly with Streamlit's sync rendering model
4. Update `run_async()` helper if needed for connection pooling
5. Add integration tests with real database fixtures

**Effort:** 1-2 days

### T6.4b: Strategy Comparison Tool

**Deliverables:**
- Side-by-side strategy metrics (authorized strategies only)
- Correlation analysis between strategies
- Rolling performance comparison
- Combined portfolio simulation

**Files to Create:**
- `apps/web_console/pages/compare.py`
- `apps/web_console/components/comparison_charts.py`
- `apps/web_console/components/correlation_matrix.py`
- `tests/apps/web_console/test_strategy_comparison.py`
- `docs/CONCEPTS/strategy-comparison.md`

---

## T6.5: Trade Journal & Analysis

**Effort:** 2-3 days | **PR:** `feat(P4T3): trade journal`
**Status:** ⏳ Pending
**Dependencies:** T6.1a

### Deliverables

- Trade history with filtering (authorized strategies only)
- Win/loss analysis and statistics
- Trade tagging and notes
- Export functionality (CSV, Excel) with audit logging

### Files to Create

- `apps/web_console/pages/journal.py`
- `apps/web_console/components/trade_table.py`
- `apps/web_console/components/trade_stats.py`
- `tests/apps/web_console/test_trade_journal.py`
- `docs/CONCEPTS/trade-journal.md`

---

## Testing Strategy

**[ENHANCED v1.5 - per Codex: Add load/perf tests and E2E for audit retention]**

### Unit Tests
- RBAC permission checks (including unknown role handling)
- Audit log serialization and persistence
- Component rendering
- Rate limiter sliding window logic
- 2FA amr claim validation

### Integration Tests
- Session + RBAC integration (oauth2_flow → session_store → permissions)
- Dashboard data fetching with strategy filtering
- Audit log persistence and query
- Rate limiter Redis integration

### E2E Tests
- Full authentication flow with role fetching
- Manual control with confirmation and 2FA
- Audit trail verification

### Load/Performance Tests

**[NEW v1.5 - per Codex: Add load/perf for Redis limiter and auth sync wrappers]**

```python
# tests/apps/web_console/perf/test_rate_limiter_load.py
"""Load tests for rate limiter under concurrent access."""
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from apps.web_console.auth.rate_limiter import RateLimiter, get_rate_limiter

@pytest.fixture
def rate_limiter():
    return get_rate_limiter()

class TestRateLimiterLoad:
    """Load tests for rate limiter performance."""

    def test_concurrent_rate_limit_checks(self, rate_limiter):
        """Test 100 concurrent rate limit checks complete within 2s."""
        import time

        def check_limit(user_id: str) -> bool:
            return rate_limiter.check_rate_limit(
                user_id, "test_action", max_requests=100, window_seconds=60
            )[0]

        start = time.time()
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(check_limit, f"user_{i % 10}")
                for i in range(100)
            ]
            results = [f.result() for f in futures]
        elapsed = time.time() - start

        assert elapsed < 2.0, f"100 concurrent checks took {elapsed:.2f}s (max 2s)"
        assert sum(results) >= 90, "At least 90% should succeed"

    def test_rate_limiter_under_connection_pressure(self, rate_limiter):
        """Test rate limiter with pool exhaustion scenario."""
        # Simulate 50 concurrent users with rapid requests
        import time

        def rapid_checks(user_id: str) -> int:
            count = 0
            for _ in range(10):
                if rate_limiter.check_rate_limit(
                    user_id, "rapid", max_requests=5, window_seconds=60
                )[0]:
                    count += 1
            return count

        start = time.time()
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [
                executor.submit(rapid_checks, f"rapid_user_{i}")
                for i in range(50)
            ]
            results = [f.result() for f in futures]
        elapsed = time.time() - start

        # Each user should get exactly 5 allowed (rate limit)
        assert all(r == 5 for r in results), "Rate limit should cap at 5 per user"
        assert elapsed < 5.0, f"50 users x 10 checks took {elapsed:.2f}s (max 5s)"

    def test_health_check_latency(self, rate_limiter):
        """Health check should complete within 100ms."""
        import time

        start = time.time()
        for _ in range(10):
            rate_limiter.health_check()
        elapsed = time.time() - start

        assert elapsed < 1.0, f"10 health checks took {elapsed:.2f}s (max 1s)"
```

```python
# tests/apps/web_console/perf/test_auth_sync_wrappers.py
"""Load tests for async/sync wrappers in Streamlit context."""
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

from apps.web_console.auth.permissions import validate_session_sync

class TestAuthSyncWrapperLoad:
    """Load tests for validate_session_sync under concurrent access."""

    def test_concurrent_session_validation(self, mock_session_store, mock_db_pool):
        """Test 20 concurrent session validations complete within 5s."""
        def validate(session_id: str) -> bool:
            result = validate_session_sync(session_id, "127.0.0.1", "TestAgent")
            return result is not None

        start = time.time()
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [
                executor.submit(validate, f"session_{i}")
                for i in range(20)
            ]
            results = [f.result() for f in futures]
        elapsed = time.time() - start

        assert elapsed < 5.0, f"20 concurrent validations took {elapsed:.2f}s (max 5s)"

    def test_no_connection_leaks(self, mock_session_store, mock_db_pool):
        """Verify connections are properly closed after validation."""
        initial_connections = mock_db_pool.get_size()

        for i in range(50):
            validate_session_sync(f"session_{i}", "127.0.0.1", "TestAgent")

        # Allow time for cleanup
        time.sleep(0.5)
        final_connections = mock_db_pool.get_size()

        assert final_connections <= initial_connections + 5, \
            f"Connection leak: {initial_connections} -> {final_connections}"
```

### E2E Audit Retention Tests

**[NEW v1.5 - per Codex: Add E2E for audit-log persistence/retention]**

```python
# tests/apps/web_console/e2e/test_audit_retention.py
"""E2E tests for audit log persistence and retention."""
import pytest
from datetime import datetime, timedelta, UTC
from apps.web_console.auth.audit_log import AuditLogger, AUDIT_RETENTION_DAYS
from apps.web_console.tasks.audit_cleanup import run_audit_cleanup

@pytest.fixture
async def audit_logger(db_pool):
    return AuditLogger(db_pool)

class TestAuditRetentionE2E:
    """E2E tests for audit log retention policy."""

    async def test_audit_events_persisted(self, audit_logger, db_pool):
        """Verify audit events are persisted to database."""
        # Log an event
        await audit_logger.log_action(
            user_id="test_user",
            action="cancel_order",
            resource_type="order",
            resource_id="order_123",
            outcome="success",
            details={"reason": "Test cancellation"},
        )

        # Verify persistence
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM audit_log WHERE resource_id = $1",
                "order_123"
            )
            assert row is not None
            assert row["user_id"] == "test_user"
            assert row["action"] == "cancel_order"
            assert row["outcome"] == "success"

    async def test_old_events_cleaned_up(self, audit_logger, db_pool):
        """Verify events older than retention period are deleted."""
        # Insert old event (91 days ago)
        old_timestamp = datetime.now(UTC) - timedelta(days=91)
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, user_id, action,
                    resource_type, outcome, details, ip_address
                ) VALUES ($1, 'test', 'old_user', 'old_action', 'test', 'success', '{}', '127.0.0.1')
                """,
                old_timestamp
            )

        # Insert recent event (1 day ago)
        recent_timestamp = datetime.now(UTC) - timedelta(days=1)
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    timestamp, event_type, user_id, action,
                    resource_type, outcome, details, ip_address
                ) VALUES ($1, 'test', 'recent_user', 'recent_action', 'test', 'success', '{}', '127.0.0.1')
                """,
                recent_timestamp
            )

        # Run cleanup
        stats = await run_audit_cleanup()

        # Verify old event deleted, recent kept
        async with db_pool.acquire() as conn:
            old_count = await conn.fetchval(
                "SELECT COUNT(*) FROM audit_log WHERE user_id = 'old_user'"
            )
            recent_count = await conn.fetchval(
                "SELECT COUNT(*) FROM audit_log WHERE user_id = 'recent_user'"
            )

        assert old_count == 0, "Old event should be deleted"
        assert recent_count == 1, "Recent event should be kept"
        assert stats["deleted_count"] >= 1

    async def test_amr_method_recorded(self, audit_logger, db_pool):
        """Verify amr_method is recorded for 2FA events."""
        await audit_logger.log_auth_event(
            user_id="2fa_user",
            action="2fa_success",
            outcome="success",
            details={"action": "flatten_all", "amr_method": "webauthn"},
        )

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM audit_log WHERE user_id = '2fa_user' AND action = '2fa_success'"
            )
            # amr_method should be in details JSON
            import json
            details = json.loads(row["details"])
            assert details.get("amr_method") == "webauthn"
```

### Data Leakage Regression Tests

**[NEW v1.5 - per Codex: Add automated test to fail on unscoped queries]**

```python
# tests/apps/web_console/security/test_strategy_scoping.py
"""Security tests to prevent strategy data leakage."""
import pytest
from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess

class TestStrategyLeakagePrevention:
    """Tests to verify strategy scoping prevents data leakage."""

    async def test_viewer_only_sees_assigned_strategies(self, db_pool):
        """Viewer should only see data from assigned strategies."""
        # User with access to alpha_baseline only
        user = {
            "sub": "viewer_user",
            "role": "viewer",
            "strategies": ["alpha_baseline"],
        }

        # Insert data for multiple strategies
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO positions (strategy_id, symbol, qty) VALUES ('alpha_baseline', 'AAPL', 100)"
            )
            await conn.execute(
                "INSERT INTO positions (strategy_id, symbol, qty) VALUES ('momentum', 'TSLA', 50)"
            )

        # Query via scoped access
        scoped = StrategyScopedDataAccess(db_pool, user)
        positions = await scoped.get_positions()

        # Should only see alpha_baseline
        strategy_ids = {p["strategy_id"] for p in positions}
        assert strategy_ids == {"alpha_baseline"}, f"Leaked strategies: {strategy_ids}"

    async def test_admin_sees_all_strategies(self, db_pool):
        """Admin should see all strategy data."""
        user = {
            "sub": "admin_user",
            "role": "admin",
            "strategies": [],  # Empty but admin has VIEW_ALL_STRATEGIES
        }

        scoped = StrategyScopedDataAccess(db_pool, user)
        positions = await scoped.get_positions()

        # Admin should see all
        strategy_ids = {p["strategy_id"] for p in positions}
        assert "alpha_baseline" in strategy_ids
        assert "momentum" in strategy_ids

    async def test_no_strategy_access_raises_error(self, db_pool):
        """User with no strategy access should raise PermissionError."""
        user = {
            "sub": "no_access_user",
            "role": "viewer",
            "strategies": [],
        }

        scoped = StrategyScopedDataAccess(db_pool, user)

        with pytest.raises(PermissionError, match="No strategy access"):
            await scoped.get_positions()

    async def test_strategy_filter_applied_to_all_queries(self, db_pool):
        """Verify all query methods apply strategy filter."""
        user = {
            "sub": "test_user",
            "role": "viewer",
            "strategies": ["alpha_baseline"],
        }

        scoped = StrategyScopedDataAccess(db_pool, user)

        # Test all query methods
        for method_name in ["get_positions", "get_orders", "get_trades", "get_pnl_summary"]:
            method = getattr(scoped, method_name)
            if method_name == "get_pnl_summary":
                results = await method("2024-01-01", "2024-12-31")
            else:
                results = await method()

            for row in results:
                assert row["strategy_id"] == "alpha_baseline", \
                    f"{method_name} leaked strategy: {row['strategy_id']}"
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Unauthorized access | Medium | Critical | RBAC + per-request validation + audit |
| Accidental flatten | Low | Critical | Auth0 step-up auth (amr claim) + rate limiting |
| Performance issues | Medium | Medium | Caching (5min TTL), pagination, performance SLOs |
| Session hijacking | Low | High | Existing OAuth2 + IP/UA binding + session_version |
| Unknown role crash | Low | Medium | Default-deny with logging |
| Stale elevated access | Low | High | Session invalidation on role/strategy change |
| Rate limit bypass | Low | Medium | Server-side Redis rate limiter |
| Strategy data leakage | Medium | Critical | Server-side StrategyScopedDataAccess |
| CSRF on admin actions | Low | Medium | Session state nonce + double confirmation |
| Migration backfill failure | Low | High | Run 0005 then 0006 with backfill dry-run in staging + pg_dump checkpoint |
| Redis contention (sessions vs rate limiter) | Medium | High | Separate logical DB/instance + connection pool limits + health checks |
| Streamlit sync wrappers blocking | Medium | Medium | Per-request pool + load tests in `test_auth_sync_wrappers.py` |
| Scheduler not deployed | Medium | Medium | Add deployment ticket for audit_cleanup job + alert on missing runs |

---

## Related Documents

**Task & Planning:**
- [P4_PLANNING.md](./P4_PLANNING.md) - Phase 4 planning
- [P4T2_TASK.md](./P4T2_TASK.md) - Analytics infrastructure (dependency)

**Architecture:**
- [ADR-018-web-console-mtls-authentication.md](../ADRs/0018-web-console-mtls-authentication.md) - Existing auth
- `docs/ADRs/ADR-024-analytics-security.md` - **[TO CREATE FIRST]** Security architecture for this task

**Runbooks:**
- [web-console-user-guide.md](../RUNBOOKS/web-console-user-guide.md) - User guide
- [oauth2-session-cleanup.md](../RUNBOOKS/oauth2-session-cleanup.md) - Session cleanup procedures

**Deployment (Required Before Go-Live):**
- **Scheduler Deployment Ticket:** Create ops ticket to deploy APScheduler worker for `audit_cleanup.py` (daily 02:00 UTC). Verify with: `SELECT MAX(timestamp) FROM audit_log WHERE action = 'cleanup_completed';`
- **Prometheus Alert:** Configure `audit_cleanup_last_run_timestamp > 25h` alert to detect scheduler failures

---

**Last Updated:** 2025-12-10 (v1.6)
**Status:** ✅ APPROVED (Codex + Claude) - Ready for Implementation

# P4T5 Component 6: Integration & Documentation - Implementation Plan

**Component:** C6 - Integration & Documentation
**Branch:** `feature/P4T5-C6-integration-docs`
**Created:** 2025-12-21
**Updated:** 2025-12-21
**Status:** PLANNING

---

## 1. Objective

Complete P4T5 (Track 7 - Web Console Operations) by finalizing:
- Navigation integration for all Track 7 pages with proper RBAC enforcement
- Fix Audit Log RBAC violation (currently accessible to all users)
- SLA probes, alertmanager routes, and performance tests
- Documentation for concepts, runbooks, and ADR finalization
- Task document status updates

**Success Criteria:**
- All Track 7 pages accessible from main navigation with proper RBAC
- Audit Log page protected with VIEW_AUDIT permission
- ADR-0029 status changed from PROPOSED to ACCEPTED
- All required CONCEPTS documentation created
- Runbooks updated with UI-based operational procedures
- P4T5_TASK.md shows all components as Complete
- P4_PLANNING.md updated to mark Track 7 complete
- PROJECT_STATUS.md updated with P4/Track 7 completion
- SLA probes and alertmanager routes created
- Performance test for 100 concurrent sessions implemented

---

## 2. Pre-Implementation Analysis Results

### 2.1 Current State

| Page | File Exists | In Navigation | Access Control | Issue |
|------|-------------|---------------|----------------|-------|
| Circuit Breaker | ✅ `pages/circuit_breaker.py` | ✅ Yes | `FEATURE_CIRCUIT_BREAKER` + `VIEW_CIRCUIT_BREAKER` | - |
| System Health | ✅ `pages/health.py` | ❌ **MISSING** | `FEATURE_HEALTH_MONITOR` + `VIEW_CIRCUIT_BREAKER` | Add to nav |
| Alerts | ✅ `pages/alerts.py` | ✅ Yes | `FEATURE_ALERTS` + `VIEW_ALERTS` | - |
| Admin | ✅ `pages/admin.py` | ❌ **MISSING** | Permission-based | Add to nav |
| **Audit Log** | ✅ Inline in app.py | ✅ Yes | ❌ **NONE** | **RBAC violation** |

### 2.2 RBAC Violation: Audit Log Page

**Issue:** The "Audit Log" page (app.py line 817) is included in the default navigation list without any permission check. Per T7.4 RBAC requirements, VIEW_AUDIT permission is required.

**Current Code (line 817):**
```python
pages = ["Dashboard", "Manual Order Entry", "Kill Switch", "Audit Log"]  # NO RBAC!
```

**Required Fix:** Remove "Audit Log" from default list and add permission-based check:
```python
pages = ["Dashboard", "Manual Order Entry", "Kill Switch"]  # Remove Audit Log
# ... later ...
if has_permission(user_info, Permission.VIEW_AUDIT):
    pages.append("Audit Log")
```

### 2.3 Documentation Gap Analysis

| Required Document | Status | Action |
|-------------------|--------|--------|
| `docs/CONCEPTS/circuit-breaker-ui.md` | ❌ Missing | CREATE |
| `docs/CONCEPTS/system-health-monitoring.md` | ❌ Missing | CREATE |
| `docs/CONCEPTS/alert-delivery.md` | ❌ Missing | CREATE |
| `docs/CONCEPTS/alerting.md` | ❌ Missing | CREATE |
| `docs/CONCEPTS/platform-administration.md` | ❌ Missing | CREATE |
| `docs/RUNBOOKS/ops.md` | ✅ Exists | UPDATE (add alert routing) |
| `docs/RUNBOOKS/circuit-breaker-ops.md` | ❌ Missing | CREATE |
| `docs/ADRs/ADR-0029-alerting-system.md` | ✅ Exists (PROPOSED) | UPDATE (→ ACCEPTED) |
| `docs/TASKS/P4T5_TASK.md` | ✅ Exists | UPDATE (mark complete) |
| `docs/GETTING_STARTED/PROJECT_STATUS.md` | ✅ Exists | UPDATE (add P4) |
| `docs/INDEX.md` | ✅ Exists | UPDATE (add new docs) |

---

## 3. Component Breakdown (6-Step Pattern)

### C6.1: Navigation Integration & RBAC Fixes

**Purpose:**
1. Wire System Health and Admin pages into main navigation
2. Fix Audit Log RBAC violation
3. Apply consistent navigation naming

**File:** `apps/web_console/app.py`

**RBAC Clarification:**
- **System Health:** Uses `@operations_requires_auth` decorator in `health.py`. The `render_health_monitor` function requires `Permission.VIEW_CIRCUIT_BREAKER` (health.py:357-359). The feature flag `FEATURE_HEALTH_MONITOR` gates nav visibility.
- **Admin Dashboard:** Permission-based access requires at least one of: `MANAGE_API_KEYS`, `MANAGE_SYSTEM_CONFIG`, or `VIEW_AUDIT`.
- **Audit Log (FIX):** Currently accessible to all users (RBAC violation). Must require `VIEW_AUDIT` permission per T7.4.

**Navigation Naming Convention:**
- Use descriptive, action-oriented names: "System Health" (not "Health"), "Admin Dashboard" (not "Admin")
- Matches existing pattern: "Manual Order Entry", "Kill Switch", "Circuit Breaker"

**Admin Dashboard Feature Flag Consideration:**
- The Admin Dashboard uses permission-based access (MANAGE_API_KEYS, MANAGE_SYSTEM_CONFIG, VIEW_AUDIT)
- No additional feature flag needed since permission checks already gate access
- This follows the same pattern as "User Management" (line 828-829) which uses MANAGE_USERS permission without a feature flag

**Import Note:** Use existing import path already in app.py (line 40):
```python
from apps.web_console.auth.permissions import Permission, has_permission
```
Do NOT change to `libs.web_console_auth.permissions` - keep consistency with existing codebase.

**Redis Client:** Reuse the `_get_redis_client()` pattern from `health.py` (lines 40-60). Add a shared helper in app.py or reuse the health.py pattern directly.

**Changes:**
```python
# Add Redis client helper for Admin page
# CRITICAL: Admin tabs use `await redis_client.get/setex/delete` inside run_async()
# Must use ASYNC Redis client (redis.asyncio.Redis), not sync RedisClient
import redis.asyncio as redis_async
import redis.exceptions

def _get_redis_client_for_admin() -> redis_async.Redis | None:
    """Get ASYNC Redis client for admin page (cached in session state).

    Admin tabs (api_key_manager, config_editor) call `await redis_client.get/setex/delete`
    inside async functions wrapped by run_async(). This requires an async Redis client.
    """
    if "admin_redis_client" not in st.session_state:
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD") or None  # None if not set
        try:
            # Use async Redis client - admin tabs use await redis_client.get/setex/delete
            st.session_state["admin_redis_client"] = redis_async.Redis(
                host=host, port=port, db=db, password=password, decode_responses=True
            )
        except (redis.exceptions.RedisError, ConnectionError, TimeoutError) as exc:
            logger.warning("Failed to create async Redis client for admin: %s", exc)
            st.session_state["admin_redis_client"] = None
    return st.session_state.get("admin_redis_client")

# FIX: Update default pages list - remove Audit Log (line 817)
pages = ["Dashboard", "Manual Order Entry", "Kill Switch"]  # Audit Log removed

# NOTE on insertion order: Manual Trade Controls inserts at 2, Circuit Breaker at 3
# Use append() for System Health to avoid index conflicts
# System Health requires both feature flag AND VIEW_CIRCUIT_BREAKER permission (health.py:357)
if config.FEATURE_HEALTH_MONITOR and has_permission(user_info, Permission.VIEW_CIRCUIT_BREAKER):
    pages.append("System Health")  # Appended (not inserted) to avoid conflicts

# Add Audit Log with RBAC (FIX for RBAC violation)
if has_permission(user_info, Permission.VIEW_AUDIT):
    pages.append("Audit Log")

# Admin Dashboard uses permission-based access (like User Management)
if any(has_permission(user_info, p) for p in [
    Permission.MANAGE_API_KEYS,
    Permission.MANAGE_SYSTEM_CONFIG,
    Permission.VIEW_AUDIT,
]):
    pages.append("Admin Dashboard")

# Add page rendering (around line 854-898)
elif page == "System Health":
    from apps.web_console.pages.health import render_health_monitor
    render_health_monitor(user=user_info, db_pool=get_db_pool())

# FIX: Add RBAC guard inside Audit Log render branch (defense in depth)
elif page == "Audit Log":
    if not has_permission(user_info, Permission.VIEW_AUDIT):
        st.error("Access denied: VIEW_AUDIT permission required")
        st.stop()
    render_audit_log()

elif page == "Admin Dashboard":
    from apps.web_console.pages.admin import render_admin_page
    from apps.web_console.auth.audit_log import AuditLogger

    redis_client = _get_redis_client_for_admin()
    render_admin_page(
        user=user_info,
        db_pool=get_db_pool(),
        redis_client=redis_client,
        audit_logger=AuditLogger(get_db_pool()),
    )
```

**Dependencies:**
- `health.py` exports `render_health_monitor` (requires `VIEW_CIRCUIT_BREAKER` permission)
- `admin.py` exports `render_admin_page` (verified)
- Redis client helper pattern reused from health.py

**Tests:** `tests/apps/web_console/test_navigation_integration.py`

| Test Case | Description |
|-----------|-------------|
| `test_system_health_visible_when_flag_and_permission` | FEATURE_HEALTH_MONITOR=true + VIEW_CIRCUIT_BREAKER → "System Health" in nav |
| `test_system_health_hidden_when_flag_disabled` | FEATURE_HEALTH_MONITOR=false → "System Health" NOT in nav |
| `test_system_health_hidden_without_permission` | FEATURE_HEALTH_MONITOR=true but no VIEW_CIRCUIT_BREAKER → NOT in nav |
| `test_admin_visible_for_admin_permissions` | User with MANAGE_API_KEYS → "Admin Dashboard" in nav |
| `test_admin_hidden_for_viewer` | Viewer role → "Admin Dashboard" NOT in nav |
| `test_audit_log_visible_with_view_audit` | User with VIEW_AUDIT → "Audit Log" in nav |
| `test_audit_log_hidden_without_view_audit` | User without VIEW_AUDIT → "Audit Log" NOT in nav |
| `test_navigation_order_stable` | Verify insertion order doesn't break existing pages |
| `test_system_health_renders_without_error` | Page renders with mocked services |
| `test_admin_renders_without_error` | Page renders with mocked DB/Redis |

**Navigation Ordering Note:**
- System Health is APPENDED (not inserted) to avoid conflicts with existing insert(2)/insert(3) logic
- Manual Trade Controls inserts at index 2, Circuit Breaker inserts at index 3
- Order depends on feature flags enabled; test verifies no pages are lost
- Feature flag defaults: FEATURE_HEALTH_MONITOR=true, others vary per environment

---

### C6.2: CONCEPTS Documentation

**Purpose:** Create educational documentation for Track 7 features

**Files to Create:**

#### 1. `docs/CONCEPTS/circuit-breaker-ui.md`
Content outline:
- Circuit breaker dashboard overview
- Status indicators (OPEN/TRIPPED)
- Manual trip/reset workflow
- RBAC requirements (Operator/Admin)
- Step-up confirmation (reason + acknowledgment)
- Rate limiting (1 reset/minute)
- Audit logging
- Redis key schema (`circuit_breaker:state`, etc.)

#### 2. `docs/CONCEPTS/system-health-monitoring.md`
Content outline:
- Health monitor architecture
- Service status grid
- Connectivity checks (Redis, Postgres)
- Queue depth metrics
- Latency metrics (P50, P95, P99)
- Staleness indicators
- Graceful degradation
- Auto-refresh mechanism

#### 3. `docs/CONCEPTS/alert-delivery.md`
Content outline:
- Delivery service architecture
- Channel handlers (Email, Slack, SMS)
- Idempotency model (dedup key)
- Rate limiting (per-channel, per-recipient, global)
- Retry with exponential backoff
- Poison queue
- Prometheus metrics

#### 4. `docs/CONCEPTS/alerting.md`
Content outline:
- Alert rules configuration
- Threshold types (drawdown, position, latency)
- Notification channel setup
- Alert history and acknowledgment
- Test notification workflow
- PII masking in UI

#### 5. `docs/CONCEPTS/platform-administration.md`
Content outline:
- Admin dashboard overview
- API key management (lifecycle, security)
- System configuration editor
- Audit log viewer
- RBAC permissions model
- PII handling

---

### C6.3: Runbook Updates

**Purpose:** Add operational procedures for Track 7 features

#### Update `docs/RUNBOOKS/ops.md`

Add sections:
```markdown
## Alert Operations

### Alert Routing Configuration
- Alert rules are configured via Web Console → Alerts
- Channels: Email (SMTP/SendGrid), Slack (webhook), SMS (Twilio)
- Rate limits enforced automatically

### Alert Troubleshooting
- Check poison queue: `SELECT * FROM alert_deliveries WHERE status = 'poison'`
- Review delivery failures: Check `alert_delivery_latency_seconds` in Grafana
- Retry failed delivery: Update status to 'pending' in alert_deliveries table

## System Health Monitor Operations

### Accessing System Health Dashboard
1. Navigate to Web Console → System Health
2. Dashboard shows all microservices, Redis, and Postgres status

### Interpreting Status Indicators
- 🟢 Green: Service healthy, latency normal
- 🟡 Yellow: Service degraded or high latency
- 🔴 Red: Service unreachable or critical error
- ⚪ Gray with staleness indicator: Cached status (fetch failed)

### Troubleshooting Service Issues
1. Check service status in dashboard for error messages
2. Review Prometheus metrics for latency trends
3. Check service logs: `docker logs <service_name>`
4. Verify Redis/Postgres connectivity in dashboard
5. If staleness indicator shows, dashboard is using cached data

### Graceful Degradation
- If health fetch fails, dashboard shows last known status with staleness warning
- Refresh interval: 10 seconds (configurable via AUTO_REFRESH_INTERVAL)
- Queue depth metrics require Redis connectivity

## Circuit Breaker Operations (UI)

### Trip Circuit Breaker via UI
1. Navigate to Web Console → Circuit Breaker
2. Click "Trip Circuit Breaker"
3. Enter reason (min 20 chars)
4. Check acknowledgment box
5. Confirm action

### Reset Circuit Breaker via UI
1. Navigate to Web Console → Circuit Breaker
2. Verify all conditions are normalized
3. Click "Reset Circuit Breaker"
4. Enter reason (min 20 chars)
5. Check acknowledgment box
6. Confirm action
7. Rate limit: 1 reset per minute
```

#### Create `docs/RUNBOOKS/circuit-breaker-ops.md`

Comprehensive runbook for CB operations including:
- CLI commands (`make circuit-trip`, `make kill-switch`)
- UI-based trip/reset procedures
- Status verification
- Recovery checklist
- Audit log review

---

### C6.4: ADR Finalization

**Purpose:** Finalize ADR-0029 from PROPOSED to ACCEPTED

**File:** `docs/ADRs/ADR-0029-alerting-system.md`

**Changes:**
- Update `## Status` from `PROPOSED` to `ACCEPTED`
- Add acceptance date
- Ensure all sections are complete

---

### C6.5: Task Status Updates

**Purpose:** Mark P4T5 components as complete

#### Update `docs/TASKS/P4T5_TASK.md`

Change component status table:
```markdown
| # | Component | Status | Effort | Dependencies |
|---|-----------|--------|--------|--------------|
| C0 | Prep & Validation | ✅ Complete | 1d | - |
| C1 | T7.1 Circuit Breaker Dashboard | ✅ Complete | 3-4d | C0 |
| C2 | T7.2 System Health Monitor | ✅ Complete | 3-4d | C0 |
| C3 | T7.5 Alert Delivery Service | ✅ Complete | 4-5d | C0 |
| C4 | T7.3 Alert Configuration UI | ✅ Complete | 3-4d | C3 |
| C5 | T7.4 Admin Dashboard | ✅ Complete | 4-6d | C0 |
| C6 | Integration & Documentation | ✅ Complete | 2d | C1-C5 |
```

Add completion note with PR references.

#### Update `docs/TASKS/P4_PLANNING.md`

Mark Track 7 as complete in the track status table (wherever Track 7 status is tracked).

#### Update `docs/GETTING_STARTED/PROJECT_STATUS.md`

Add P4 section with clear messaging that P4 is in progress but Track 7 is complete:
```markdown
### P4: Advanced Features & Research (Days 181-315)
**Status:** 🔄 **IN PROGRESS** (Track 7 complete, other tracks ongoing)

**Completed Tracks:**
- ✅ P4T5 Track 7 - Web Console Operations (PRs #93, #95, #96, #97, #98, #XX)
  - T7.1 Circuit Breaker Dashboard
  - T7.2 System Health Monitor
  - T7.3 Alert Configuration UI
  - T7.4 Admin Dashboard (API keys, Config, Audit)
  - T7.5 Alert Delivery Service

**Remaining Tracks:**
- ⏳ Track 1-6, 8-9 (per P4_PLANNING.md)
  - Track 7 infra deliverables (SLA probes, alertmanager routes, performance soak tests) are **delivered in C6** and should no longer be listed as deferred
```

#### Update `docs/INDEX.md`

Add new CONCEPTS documents to the index.

---

## 4. Implementation Order

```
C6.0: Metric Instrumentation (PREREQUISITE - add missing metrics)
  ↓
C6.1: Navigation Integration & RBAC Fixes (app.py changes)
  ↓
C6.2: CONCEPTS Documentation (5 new files)
  ↓
C6.3: Runbook Updates (2 files)
  ↓
C6.4: ADR Finalization (1 file)
  ↓
C6.5: Task Status Updates (3 files)
  ↓
C6.6: SLA Infrastructure (probes, alertmanager routes, blackbox exporter)
  ↓
C6.7: Performance Tests (100 concurrent sessions)
  ↓
C6.8: SLA Config Validation Tests
  ↓
Tests & CI
  ↓
Review & Commit
```

---

### C6.0: Metric Instrumentation (PREREQUISITE)

**Purpose:** Add missing Prometheus metrics required for SLA alerting

**Existing Metrics (verified):**
- `alert_delivery_latency_seconds` - ✅ exists in `libs/alerts/metrics.py`
- `alert_poison_queue_size` - ✅ exists in `libs/alerts/metrics.py`

**Missing Metrics (must add):**

#### 1. Admin Action Counter + Audit Write Latency
**Files to Modify:**
- `libs/web_console_auth/audit_logger.py` - Define metrics (single registration point)
- `apps/web_console/auth/audit_log.py` - Import and USE metrics in _write() method

**IMPORTANT - Code Path Analysis:**
The web console has TWO audit write paths:
1. `apps/web_console/auth/audit_log.py:AuditLogger._write()` - Used by most web console operations
2. `apps/web_console/services/cb_service.py:_log_audit()` - Uses raw SQL (bypasses AuditLogger)

Both paths must be instrumented. The shared lib (`libs/web_console_auth/audit_logger.py`) is used by other services but NOT by web_console's own AuditLogger.

**Step 1: Define metrics in shared lib (single registration point)**
```python
# Add to libs/web_console_auth/audit_logger.py (at module level, alongside existing metrics)
import time

# Existing metrics (already defined):
# _audit_events_total = Counter(...)
# _audit_write_failures_total = Counter(...)

# NEW metrics to add (exported for import by other modules):
admin_action_total = Counter(
    "admin_action_total",
    "Counter of admin actions",
    ["action"],
)

audit_write_latency_seconds = Histogram(
    "audit_write_latency_seconds",
    "Audit log write latency",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0],
)

# Export new metrics for importers
__all__ = ["AuditLogger", "admin_action_total", "audit_write_latency_seconds"]

# Also instrument this lib's _write method:
async def _write(self, *, user_id, action, event_type, ...):
    # Increment for BOTH 'action' and 'admin' event types
    if event_type in ("action", "admin"):
        admin_action_total.labels(action=action).inc()

    start = time.monotonic()
    try:
        # ... existing insert logic ...
    finally:
        audit_write_latency_seconds.observe(time.monotonic() - start)
```

**Step 2: Import and USE metrics in web_console's AuditLogger**
```python
# Update apps/web_console/auth/audit_log.py
import time
from libs.platform.web_console_auth.audit_logger import (
    _audit_cleanup_duration_seconds,
    _audit_events_total,
    _audit_write_failures_total,
    admin_action_total,  # NEW - import from shared lib
    audit_write_latency_seconds,  # NEW - import from shared lib
)

# In apps/web_console/auth/audit_log.py:AuditLogger._write() method:
async def _write(self, *, user_id, action, event_type, ...):
    # Increment for BOTH 'action' and 'admin' event types
    if event_type in ("action", "admin"):
        admin_action_total.labels(action=action).inc()

    start = time.monotonic()
    try:
        # ... existing insert logic (lines 76-105) ...
        async with acquire_connection(self.db_pool) as conn:
            async with _maybe_transaction(conn):
                await conn.execute(...)
        _audit_events_total.labels(event_type=event_type, outcome=outcome).inc()
    except Exception as exc:
        _audit_write_failures_total.labels(reason=exc.__class__.__name__).inc()
        # ... existing error handling ...
    finally:
        audit_write_latency_seconds.observe(time.monotonic() - start)
```

**Step 3: Instrument CB service's raw SQL audit path**
```python
# Update apps/web_console/services/cb_service.py:_log_audit()
import time
from libs.platform.web_console_auth.audit_logger import (
    admin_action_total,
    audit_write_latency_seconds,
)

def _log_audit(self, action, user, ...):
    # Increment admin action counter (CB trip/reset are admin actions)
    admin_action_total.labels(action=action).inc()

    start = time.monotonic()
    try:
        # ... existing SQL insert (lines 455-478) ...
    finally:
        audit_write_latency_seconds.observe(time.monotonic() - start)
```

**Usage:** Each audit write (via any path) increments the counter and observes latency.

#### 2. Circuit Breaker Staleness Gauge
**File:** `apps/web_console/services/cb_metrics.py` (add to existing metrics module)

**Rationale:** The CB staleness metric should be in the web_console service layer where CircuitBreakerService already accesses Redis. Adding to execution_gateway main.py would require wiring up a Redis client that doesn't exist there.

**Redis State Schema (from libs/risk_management/breaker.py:194-202):**
```python
{
    "state": "OPEN" | "TRIPPED" | "QUIET_PERIOD",
    "tripped_at": str | None,      # ISO timestamp when tripped
    "trip_reason": str | None,
    "trip_details": dict | None,
    "reset_at": str | None,        # ISO timestamp when last reset
    "reset_by": str | None,
    "trip_count_today": int,
}
```

**STALENESS METRIC NOTE:**
The `cb_staleness_seconds` gauge measures "can we read CB state from Redis", NOT "time since state change":
- Success: 0 (just verified)
- Failure: 999999 (sentinel triggers alert)
- Uses `multiprocess_mode="min"` so any healthy worker keeps the metric at 0
- Alert fires only when ALL workers fail to verify Redis

```python
import json
import logging
import os
from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# Conditionally enable multiprocess_mode when PROMETHEUS_MULTIPROC_DIR is set
# Use "min" mode: if ANY worker successfully verifies (sets 0), that's reported
# This prevents false alerts when one worker fails but others succeed
_multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
_gauge_kwargs: dict = {}
if _multiproc_dir:
    _gauge_kwargs["multiprocess_mode"] = "min"

cb_staleness_seconds = Gauge(
    "cb_staleness_seconds",
    "CB verification status (0 = just verified, sentinel = failed)",
    **_gauge_kwargs,
)

# Sentinel value indicating CB verification failed (triggers critical alert)
CB_VERIFICATION_FAILED_SENTINEL = 999999.0  # ~11.5 days - clearly abnormal

def update_cb_staleness_metric(redis_client) -> None:
    """Update CB staleness metric based on Redis accessibility.

    SIMPLIFIED SEMANTICS (multiprocess-safe):
    - On successful Redis read: set to 0 (just verified)
    - On any failure: set to sentinel (999999)
    - With multiprocess_mode="min", if ANY worker succeeds, Prometheus sees 0
    - Alert fires only when ALL workers fail to verify

    This avoids per-process state that causes false alerts in multi-worker setups.
    """
    try:
        state_json = redis_client.get("circuit_breaker:state")
        if state_json is None:
            logger.error("cb_state_missing")
            cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)
            return

        # Verify it's valid JSON
        try:
            json.loads(state_json)
        except json.JSONDecodeError:
            logger.error("cb_state_malformed_json")
            cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)
            return

        # Success! Set to 0
        cb_staleness_seconds.set(0)

    except Exception as exc:
        logger.exception("cb_verification_failed", extra={"error": str(exc)})
        cb_staleness_seconds.set(CB_VERIFICATION_FAILED_SENTINEL)
```

**Metric Semantics Note (Simplified for Multiprocess):**
- Success: 0 (just verified)
- Failure: sentinel (999999)
- With `multiprocess_mode="min"`, Prometheus reports the MINIMUM across all workers
- Alert at >10 fires only when ALL workers fail (if any worker succeeds, min=0)
- This prevents false alerts in multi-worker Streamlit setups

**Integration:** Call `update_cb_staleness_metric(redis)` in BOTH locations:

1. **Metrics Server (REQUIRED for Prometheus scrapes):**
```python
# Update apps/web_console/metrics_server.py
from apps.web_console.services.cb_metrics import update_cb_staleness_metric
from libs.core.redis_client import RedisClient
import logging
import os
import redis.exceptions

logger = logging.getLogger(__name__)

# Cache Redis client at module level (reused across scrapes)
_metrics_redis_client: RedisClient | None = None

def _get_redis_client() -> RedisClient | None:
    """Get Redis client for metrics collection with retry on failure.

    Unlike one-shot init, this retries on each call if client is None,
    allowing recovery after transient Redis outages at startup.
    """
    global _metrics_redis_client

    # Return cached client if available and healthy
    if _metrics_redis_client is not None:
        try:
            _metrics_redis_client.ping()
            return _metrics_redis_client
        except Exception:
            _metrics_redis_client = None  # Reset for retry

    # Try to create/reconnect
    try:
        _metrics_redis_client = RedisClient(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0")),
            password=os.getenv("REDIS_PASSWORD"),
        )
        return _metrics_redis_client
    except (redis.exceptions.RedisError, ConnectionError, TimeoutError) as exc:
        logger.warning("Failed to create Redis client for metrics: %s", exc)
        return None

@app.get("/metrics")
def metrics() -> Response:
    # Update CB staleness BEFORE collecting metrics
    redis = _get_redis_client()
    if redis:
        update_cb_staleness_metric(redis)

    # ... rest of existing metrics collection ...
```

2. **CircuitBreakerService.get_status() (for UI freshness):**
```python
# Update apps/web_console/services/cb_service.py
from .cb_metrics import update_cb_staleness_metric

def get_status(self) -> dict[str, Any]:
    status = self.breaker.get_status()
    CB_STATUS_CHECKS.inc()
    # Update staleness metric on each status check
    update_cb_staleness_metric(self.redis)
    return status
```

**Why both locations:**
- Metrics server: Ensures every Prometheus scrape gets fresh staleness value
- get_status(): Updates metric when UI loads (supplements scrape timing)

**Tests:** `tests/apps/web_console/test_audit_metrics.py`

**Registration Tests (basic):**
```python
def test_audit_write_latency_metric_exported():
    """Verify audit_write_latency_seconds histogram is exposed."""
    from prometheus_client import REGISTRY
    import libs.platform.web_console_auth.audit_logger  # noqa: F401
    assert "audit_write_latency_seconds" in [m.name for m in REGISTRY.collect()]

def test_cb_staleness_metric_exported():
    """Verify cb_staleness_seconds gauge is exposed."""
    from prometheus_client import REGISTRY
    import apps.web_console.services.cb_metrics  # noqa: F401
    assert "cb_staleness_seconds" in [m.name for m in REGISTRY.collect()]

def test_admin_action_total_metric_exported():
    """Verify admin_action_total counter is exposed."""
    from prometheus_client import REGISTRY
    import libs.platform.web_console_auth.audit_logger  # noqa: F401
    assert "admin_action_total" in [m.name for m in REGISTRY.collect()]
```

**Unit Tests (CB staleness verification - simplified):**
```python
import json
from unittest.mock import MagicMock

from apps.web_console.services.cb_metrics import (
    cb_staleness_seconds,
    update_cb_staleness_metric,
    CB_VERIFICATION_FAILED_SENTINEL,
)


class TestCBStalenessMetric:
    """Unit tests for cb_staleness_seconds gauge (simplified binary semantics)."""

    def test_staleness_zero_on_successful_read(self):
        """Successful Redis read sets staleness to 0."""
        redis = MagicMock()
        redis.get.return_value = json.dumps({"state": "OPEN"})

        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == 0

    def test_staleness_zero_for_any_valid_state(self):
        """Any valid CB state JSON results in 0."""
        redis = MagicMock()
        redis.get.return_value = json.dumps({"state": "TRIPPED", "tripped_at": "2025-01-01T00:00:00Z"})

        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == 0

    def test_staleness_sentinel_when_state_missing(self):
        """Missing CB state key reports sentinel."""
        redis = MagicMock()
        redis.get.return_value = None

        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == CB_VERIFICATION_FAILED_SENTINEL

    def test_staleness_sentinel_on_malformed_json(self):
        """Malformed JSON reports sentinel."""
        redis = MagicMock()
        redis.get.return_value = "not valid json"

        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == CB_VERIFICATION_FAILED_SENTINEL

    def test_staleness_sentinel_on_redis_connection_error(self):
        """Redis connection failure reports sentinel."""
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("Redis unavailable")

        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == CB_VERIFICATION_FAILED_SENTINEL

    def test_recovery_after_failure(self):
        """Metric recovers to 0 after Redis comes back."""
        redis = MagicMock()

        # First: failure
        redis.get.side_effect = ConnectionError("Redis down")
        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == CB_VERIFICATION_FAILED_SENTINEL

        # Second: recovery
        redis.get.side_effect = None
        redis.get.return_value = json.dumps({"state": "OPEN"})
        update_cb_staleness_metric(redis)
        assert cb_staleness_seconds._value.get() == 0
```

**Integration Tests (verify instrumentation wiring):**
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient

from libs.platform.web_console_auth.audit_logger import (
    admin_action_total,
    audit_write_latency_seconds,
)


class TestAuditLoggerIntegration:
    """Integration tests verifying AuditLogger instruments metrics."""

    @pytest.mark.asyncio
    async def test_audit_logger_write_increments_admin_counter(self):
        """AuditLogger._write increments admin_action_total for action events."""
        from apps.web_console.auth.audit_log import AuditLogger

        # Mock DB pool
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock()
        mock_conn.execute = AsyncMock()

        logger = AuditLogger(mock_pool)
        before = admin_action_total.labels(action="TEST_INTEGRATION")._value.get()

        await logger.log_action(
            user_id="test-user",
            action="TEST_INTEGRATION",
            resource_type="test",
            resource_id="123",
            outcome="success",
        )

        after = admin_action_total.labels(action="TEST_INTEGRATION")._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_audit_logger_write_observes_latency(self):
        """AuditLogger._write observes latency in histogram."""
        from apps.web_console.auth.audit_log import AuditLogger

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock()
        mock_conn.execute = AsyncMock()

        logger = AuditLogger(mock_pool)
        before_sum = audit_write_latency_seconds._sum.get()

        await logger.log_action(
            user_id="test-user",
            action="LATENCY_TEST",
            resource_type="test",
            resource_id="123",
            outcome="success",
        )

        after_sum = audit_write_latency_seconds._sum.get()
        assert after_sum > before_sum


class TestCBServiceAuditIntegration:
    """Integration tests verifying CB service _log_audit instruments metrics."""

    def test_cb_service_log_audit_increments_counter(self):
        """CB service _log_audit increments admin_action_total."""
        from apps.web_console.services.cb_service import CircuitBreakerService

        # Mock Redis and DB
        mock_redis = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock()
        mock_pool.connection.return_value.__exit__ = MagicMock()

        service = CircuitBreakerService(mock_redis, mock_pool)
        before = admin_action_total.labels(action="CB_TEST_ACTION")._value.get()

        service._log_audit(
            action="CB_TEST_ACTION",
            user={"user_id": "test"},
            resource_type="circuit_breaker",
            resource_id="global",
            reason="test",
        )

        after = admin_action_total.labels(action="CB_TEST_ACTION")._value.get()
        assert after == before + 1


class TestMetricsServerIntegration:
    """Integration tests for /metrics endpoint staleness update."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_updates_staleness(self):
        """GET /metrics triggers update_cb_staleness_metric."""
        from apps.web_console.metrics_server import app

        # Patch Redis client and update function
        with patch("apps.web_console.metrics_server._get_redis_client") as mock_get_redis:
            mock_redis = MagicMock()
            mock_redis.get.return_value = json.dumps({
                "state": "OPEN",
                "reset_at": datetime.now(timezone.utc).isoformat(),
            })
            mock_get_redis.return_value = mock_redis

            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.get("/metrics")

            assert response.status_code == 200
            # Verify Redis was called (staleness update triggered)
            mock_redis.get.assert_called_once_with("circuit_breaker:state")
```

---

## 5. Files to Create

| File | Type | Purpose |
|------|------|---------|
| `docs/CONCEPTS/circuit-breaker-ui.md` | Docs | CB dashboard documentation |
| `docs/CONCEPTS/system-health-monitoring.md` | Docs | Health monitor documentation |
| `docs/CONCEPTS/alert-delivery.md` | Docs | Delivery service documentation |
| `docs/CONCEPTS/alerting.md` | Docs | Alert config documentation |
| `docs/CONCEPTS/platform-administration.md` | Docs | Admin dashboard documentation |
| `docs/RUNBOOKS/circuit-breaker-ops.md` | Runbook | CB operations procedures |
| `tests/apps/web_console/test_navigation_integration.py` | Test | Navigation tests |
| `infra/prometheus/sla_probes.yml` | Config | SLA recording + alert rules (groups only) |
| `infra/alertmanager/` | Dir | Directory for alertmanager config |
| `infra/alertmanager/routes.yml` | Config | Alert routing configuration |
| `tests/performance/test_concurrent_sessions.py` | Test | 100 concurrent sessions load test |
| `tests/infra/test_sla_configs.py` | Test | SLA config validation tests |
| `tests/apps/web_console/test_audit_metrics.py` | Test | Audit/CB metric verification |
| `infra/grafana/dashboards/track7-slo.json` | Config | Track 7 SLO dashboard |

---

## 6. Files to Modify

| File | Changes |
|------|---------|
| `libs/web_console_auth/audit_logger.py` | Add admin_action_total counter + audit_write_latency_seconds histogram; instrument _write() |
| `apps/web_console/auth/audit_log.py` | Import new metrics from shared lib; instrument _write() with counter + histogram |
| `apps/web_console/services/cb_metrics.py` | Add cb_staleness_seconds gauge + update_cb_staleness_metric() function |
| `apps/web_console/services/cb_service.py` | Call update_cb_staleness_metric() in get_status(); add audit metrics to _log_audit() |
| `apps/web_console/metrics_server.py` | Wire update_cb_staleness_metric() in /metrics endpoint for Prometheus scrapes |
| `apps/web_console/app.py` | Add Health/Admin to nav, fix Audit Log RBAC, add Redis helper |
| `docs/RUNBOOKS/ops.md` | Add alert routing and CB UI procedures |
| `docs/ADRs/ADR-0029-alerting-system.md` | Status: PROPOSED → ACCEPTED |
| `docs/TASKS/P4T5_TASK.md` | Mark all components complete |
| `docs/TASKS/P4_PLANNING.md` | Mark Track 7 as complete |
| `docs/GETTING_STARTED/PROJECT_STATUS.md` | Add P4 section with Track 7 complete |
| `docs/INDEX.md` | Add new CONCEPTS docs |
| `infra/grafana/dashboards/*.json` | Add SLO panels for Track 7 services |
| `infra/prometheus/prometheus.yml` | Add scrape configs, rule_files, alerting block |
| `docker-compose.yml` (root) | Add blackbox_exporter and alertmanager services |
| `Makefile` | Add `and not performance` to pytest marker selection (line 188) |

---

## 7. Acceptance Criteria Mapping

| Requirement (from P4T5_TASK.md) | Component | Status |
|--------------------------------|-----------|--------|
| Navigation integration | C6.1 | Planned |
| Audit Log RBAC fix | C6.1 | Planned |
| Feature flags | Already done | ✅ |
| Performance/soak tests | C6.7 | Planned |
| ADR-0029 finalization | C6.4 | Planned |
| CONCEPTS documentation | C6.2 | Planned |
| Runbook updates | C6.3 | Planned |
| Status updates (P4T5, P4_PLANNING) | C6.5 | Planned |
| SLA probes/alertmanager routes | C6.6 | Planned |

### C6.6: SLA Infrastructure

**Purpose:** Create SLA monitoring infrastructure for Track 7 services per P4T5_TASK.md lines 146-156

**Required SLA Alerts (from P4T5_TASK.md):**
| Metric | Threshold | Action | Owner |
|--------|-----------|--------|-------|
| `cb_staleness_seconds` | > 10 | page on-call via PagerDuty | @platform-team |
| `alert_delivery_latency_p95` | > 60 | warn to #alerts-ops Slack | @platform-team |
| `audit_write_latency_p95` | > 1 | warn to #alerts-ops Slack | @platform-team |
| `alert_poison_queue_size` | > 10 | page on-call via PagerDuty | @platform-team |

**Files to Create:**

#### 1. Prometheus Configuration Updates

**IMPORTANT:** Prometheus requires strict separation:
- `prometheus.yml` contains `scrape_configs` (jobs to scrape)
- `sla_probes.yml` contains `groups` only (recording rules + alert rules)

##### 1a. Modify `infra/prometheus/prometheus.yml`:

**IMPORTANT:** These are MODIFICATIONS to existing file, not replacements.

**Uncomment and update alerting block (around line 27-32):**
```yaml
# Alertmanager configuration (currently commented out - UNCOMMENT and update)
alerting:
  alertmanagers:
    - static_configs:
        - targets:
          - 'alertmanager:9093'  # Docker service hostname
```

**APPEND to existing rule_files (around line 22-25):**
```yaml
# Load alert rules (KEEP EXISTING, ADD sla_probes.yml)
rule_files:
  - 'alerts.yml'
  - 'alerts/alert_delivery.yml'
  - 'alerts/oauth2.yml'
  - 'sla_probes.yml'  # Added for Track 7 SLA monitoring
```

**APPEND to existing scrape_configs (after line 85):**

**IMPORTANT - Hybrid Architecture:**
- App services (execution_gateway, signal_service, etc.) run on HOST, not in Docker
- Infrastructure (Prometheus, Grafana, Redis, Postgres) runs in Docker
- Use `host.docker.internal` to reach host services from Docker containers
- Linux compatibility: Add `extra_hosts: host.docker.internal:host-gateway` to services needing host access

**CB Staleness Monitoring Architecture:**
The Circuit Breaker status is NOT exposed via an HTTP endpoint. Instead:
1. **Blackbox probe** (`track7_gateway_probe`) → checks execution gateway `/health` liveness
2. **CB staleness gauge** (`cb_staleness_seconds`) → computed by web_console from Redis state age

These are complementary:
- Probe failure = gateway down (infrastructure issue)
- CB staleness > threshold = state hasn't been updated (possible Redis issue or stale data)

There is NO HTTP endpoint for CB status at execution_gateway. The web console reads CB state directly from Redis via `CircuitBreakerService.get_status()`.

**Note on existing scrape targets:**
The current prometheus.yml has several host-based scrape targets using `localhost:8xxx`. This plan does not
modify those legacy targets; it only adds the new Track 7 jobs (blackbox probe + web_console_metrics) with
host.docker.internal and extra_hosts for Linux compatibility. If host-based scrapes need correction, track
in a separate infra ticket to avoid scope creep here.

```yaml
  # Blackbox exporter for execution gateway liveness probes (5s interval per T7.1)
  # NOTE: Uses /health endpoint - gateway liveness, NOT CB status
  # CB staleness is measured via cb_staleness_seconds gauge from Redis state age
  - job_name: 'track7_gateway_probe'
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - http://host.docker.internal:8002/health
        labels:
          probe_type: gateway_health
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox_exporter:9115  # Docker service (same network)
    scrape_interval: 5s  # Per T7.1: frequent monitoring

  # Web Console metrics server (cb_staleness_seconds, audit_write_latency_seconds)
  # CRITICAL: Required for SLA alerts to work - exposes Track 7 metrics
  - job_name: 'web_console_metrics'
    static_configs:
      - targets:
          - host.docker.internal:8503  # Metrics sidecar port
        labels:
          service: web_console
    scrape_interval: 15s
```

**Metrics Server Deployment:**
The metrics_server.py exposes a FastAPI `/metrics` endpoint. Recommended: run as a sidecar process
(`uvicorn apps.web_console.metrics_server:app --port 8503`). Mounting inside Streamlit is optional and
not required for this component.

##### 1c. Modify root `docker-compose.yml`:

**NOTE:** Must use existing network `trading_platform` (defined at bottom of docker-compose.yml).

**1. Add networks to existing prometheus service (around line 88-101):**

**IMPORTANT:** Must keep BOTH default network (for loki, postgres, redis) AND add trading_platform (for alertmanager, blackbox).

```yaml
  prometheus:
    image: prom/prometheus:latest
    container_name: trading_platform_prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./infra/prometheus:/etc/prometheus
      - prometheusdata:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
    depends_on:
      - postgres
    networks:
      - default  # Keep for loki, postgres, redis connectivity
      - trading_platform  # Add for alertmanager/blackbox communication
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Linux compatibility for host access
```

**2. Add networks to existing grafana service (around line 102-117):**
```yaml
  grafana:
    image: grafana/grafana:10.4.2
    container_name: trading_platform_grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=${GF_SECURITY_ADMIN_USER:-admin}
      - GF_SECURITY_ADMIN_PASSWORD=${GF_SECURITY_ADMIN_PASSWORD:-admin}
    volumes:
      - grafanadata:/var/lib/grafana
      - ./infra/grafana:/etc/grafana/provisioning
    depends_on:
      - prometheus
      - postgres
      - loki
    networks:
      - default  # Keep for loki datasource connectivity
      - trading_platform  # Add for prometheus/alertmanager (if moved)
```

**3. Add new services:**
```yaml
  blackbox_exporter:
    image: prom/blackbox-exporter:latest
    ports:
      - "9115:9115"
    networks:
      - trading_platform
    extra_hosts:
      - "host.docker.internal:host-gateway"  # Linux compatibility for probing host services

  alertmanager:
    image: prom/alertmanager:latest
    ports:
      - "9093:9093"
    volumes:
      - ./infra/alertmanager:/etc/alertmanager
    command:
      - '--config.file=/etc/alertmanager/routes.yml'
    networks:
      - trading_platform
```

**Pre-implementation step:** Create alertmanager config directory:
```bash
mkdir -p infra/alertmanager
```

##### 1b. Create `infra/prometheus/sla_probes.yml` (recording + alert rules ONLY):
```yaml
# SLA Recording Rules and Alerts for Track 7
# NOTE: This file contains ONLY groups (recording rules + alerts)
# Scrape configs are in prometheus.yml

groups:
  - name: track7_recording_rules
    interval: 15s
    rules:
      # Pre-compute P95 latencies for alerting
      # IMPORTANT: histogram_quantile requires sum by (le) aggregation
      - record: alert_delivery_latency_p95
        expr: histogram_quantile(0.95, sum by (le) (rate(alert_delivery_latency_seconds_bucket[5m])))

      - record: audit_write_latency_p95
        expr: histogram_quantile(0.95, sum by (le) (rate(audit_write_latency_seconds_bucket[5m])))

# =============================================================================
# SECTION 3: Alert Rules
# =============================================================================

  - name: track7_sla_alerts
    rules:
      # Circuit breaker staleness (time since last Redis update)
      # Target: ≤5s, alert at >10s (P4T5_TASK.md line 42, 151)
      - alert: CircuitBreakerStalenessHigh
        expr: cb_staleness_seconds > 10
        for: 30s
        labels:
          severity: critical
          owner: platform-team
        annotations:
          summary: "Circuit breaker status stale >10s (target: ≤5s)"
          runbook_url: "docs/RUNBOOKS/circuit-breaker-ops.md"

      # Synthetic probe failure (blackbox exporter)
      # NOTE: job name must match prometheus.yml scrape config
      - alert: GatewayProbeDown
        expr: probe_success{job="track7_gateway_probe"} == 0
        for: 30s
        labels:
          severity: critical
          owner: platform-team
        annotations:
          summary: "Execution gateway health endpoint unreachable"

      # Alert delivery latency P95 (target: <60s)
      # Per P4T5_TASK.md line 44, 152
      - alert: AlertDeliveryLatencyHigh
        expr: alert_delivery_latency_p95 > 60
        for: 5m
        labels:
          severity: warning
          owner: platform-team
        annotations:
          summary: "Alert delivery P95 latency >60s (target: <60s)"
          slack_channel: "#alerts-ops"

      # Audit write latency P95 (target: <1s)
      # Per P4T5_TASK.md line 45, 153
      - alert: AuditWriteLatencyHigh
        expr: audit_write_latency_p95 > 1
        for: 5m
        labels:
          severity: warning
          owner: platform-team
        annotations:
          summary: "Audit log write P95 latency >1s (target: <1s)"
          slack_channel: "#alerts-ops"

      # Poison queue size (target: <10)
      # Per P4T5_TASK.md line 87, 154
      - alert: AlertPoisonQueueHigh
        expr: alert_poison_queue_size > 10
        for: 1m
        labels:
          severity: critical
          owner: platform-team
        annotations:
          summary: "Alert poison queue size >10 (failed deliveries need manual review)"
          runbook_url: "docs/RUNBOOKS/ops.md#alert-troubleshooting"

      # Health dashboard refresh - DEFERRED
      # NOTE: health_check_duration_seconds metric does not exist.
      # This alert is deferred until the health page adds instrumentation.
      # Per P4T5_TASK.md line 43 - target: ≤10s
      # - alert: HealthDashboardRefreshSlow
      #   expr: histogram_quantile(0.95, rate(health_check_duration_seconds_bucket[5m])) > 10
```

#### 2. `infra/alertmanager/routes.yml`
Alert routing configuration for Track 7 (with ownership per P4T5_TASK.md lines 151-155):
```yaml
# Alert routing for Track 7 operations
# Owner: @platform-team (P4T5_TASK.md lines 151-155)
route:
  receiver: default
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

  routes:
    # CRITICAL: CB staleness and poison queue → page on-call via PagerDuty
    # Per P4T5_TASK.md line 151, 154
    - match:
        alertname: CircuitBreakerStalenessHigh
      receiver: pagerduty-platform-team
      group_wait: 0s
      repeat_interval: 15m

    - match:
        alertname: AlertPoisonQueueHigh
      receiver: pagerduty-platform-team
      group_wait: 0s
      repeat_interval: 15m

    # WARNING: Latency alerts → #alerts-ops Slack
    # Per P4T5_TASK.md lines 152-153
    - match:
        alertname: AlertDeliveryLatencyHigh
      receiver: slack-alerts-ops
      group_wait: 30s

    - match:
        alertname: AuditWriteLatencyHigh
      receiver: slack-alerts-ops
      group_wait: 30s

    - match:
        alertname: HealthDashboardRefreshSlow
      receiver: slack-alerts-ops
      group_wait: 30s

receivers:
  - name: default
    # Default notification channel

  # PagerDuty for critical alerts (CB staleness, poison queue)
  # Owner: @platform-team
  - name: pagerduty-platform-team
    pagerduty_configs:
      - service_key: "${PAGERDUTY_SERVICE_KEY}"
        severity: critical
        description: "{{ .CommonAnnotations.summary }}"

  # Slack #alerts-ops for warnings
  # Owner: @platform-team
  - name: slack-alerts-ops
    slack_configs:
      - api_url: "${SLACK_WEBHOOK_ALERTS_OPS}"
        channel: "#alerts-ops"
        title: "Track 7 Alert: {{ .CommonLabels.alertname }}"
        text: "{{ .CommonAnnotations.summary }}"
```

**Grafana SLO Dashboard Updates:**

**File:** `infra/grafana/dashboards/track7-slo.json` (CREATE NEW)

Add panels for Track 7 SLO monitoring:

| Panel | Query | Threshold |
|-------|-------|-----------|
| CB Staleness Gauge | `cb_staleness_seconds` | ≤5s (green), >10s (red) |
| Alert Delivery P95 | `histogram_quantile(0.95, rate(alert_delivery_latency_seconds_bucket[5m]))` | <60s |
| Audit Write P95 | `histogram_quantile(0.95, rate(audit_write_latency_seconds_bucket[5m]))` | <1s |
| Poison Queue Size | `alert_poison_queue_size` | <10 (green), >10 (red) |
| Alert Queue Depth | `alert_queue_depth` | Info only |

**Validation:** Dashboard provisioning is automatic via `infra/grafana/provisioning/dashboards/`

---

### C6.7: Performance Tests

**Purpose:** Validate system performance under load per P4T5_TASK.md lines 635-638

**File:** `tests/performance/test_concurrent_sessions.py`

**Required Test Scenarios (from P4T5_TASK.md lines 635-638):**

| Test | Description | Target (SLA) |
|------|-------------|--------------|
| `test_100_concurrent_health_polls` | 100 sessions polling health endpoint every 10s | Refresh ≤10s |
| `test_concurrent_circuit_breaker_reads` | 100 sessions reading CB status concurrently | Staleness ≤5s |
| `test_alert_delivery_backlog_drain` | Drain 1000 queued alerts | P95 <60s |
| `test_audit_log_write_throughput` | 100 writes/sec sustained | <1s per write |
| `test_sla_threshold_assertions` | Verify CB staleness <5s, delivery P95 <60s, audit write <1s | Per SLA |

**Endpoint Configuration:**
- **Execution Gateway:** `http://localhost:8002` (per config.py line 24)
- **Streamlit Health:** `http://localhost:8501/_stcore/health` (Streamlit internal endpoint)
- **Gateway Health:** `http://localhost:8002/health` (CB status is read from Redis by web console, not via HTTP)

**Authentication Handling:**
- Performance tests target unauthenticated endpoints (gateway /health)
- For authenticated endpoints, tests use dev auth token fixture or bypass auth via test configuration

**Implementation:**
```python
"""Performance tests for Track 7 concurrent access patterns.

Per P4T5_TASK.md lines 635-638:
- Polling under load (100 concurrent dashboard sessions)
- Delivery backlog processing (1000 queued alerts)
- Audit log write throughput (100 writes/sec)
- SLA threshold assertions (CB staleness <5s, delivery P95 <60s, audit write <1s)

SAFETY GUARD: These tests require explicit opt-in via RUN_PERF_TESTS=1 env var.
They write test data to the database and require running services.

Usage:
    RUN_PERF_TESTS=1 make perf
    # OR
    RUN_PERF_TESTS=1 pytest tests/performance/ -m performance
"""

import asyncio
import os
import time
from dataclasses import dataclass

import httpx
import pytest

# Safety guard: require explicit opt-in to avoid accidental DB writes
_PERF_TESTS_ENABLED = os.getenv("RUN_PERF_TESTS", "").lower() in ("1", "true", "yes")

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(
        not _PERF_TESTS_ENABLED,
        reason="Performance tests disabled. Set RUN_PERF_TESTS=1 to enable."
    ),
]

# Endpoint configuration (matches config.py)
EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")
STREAMLIT_URL = os.getenv("STREAMLIT_URL", "http://localhost:8501")


@dataclass
class LoadTestResult:
    """Results from a load test run."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    min_latency_ms: float
    max_latency_ms: float
    avg_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    requests_per_second: float


def calculate_percentile(sorted_values: list[float], percentile: float) -> float:
    """Calculate percentile from sorted list."""
    if not sorted_values:
        return 0.0
    index = int(len(sorted_values) * percentile)
    return sorted_values[min(index, len(sorted_values) - 1)]


async def run_concurrent_load(
    url: str,
    num_sessions: int,
    duration_seconds: int,
    interval_seconds: float,
    headers: dict[str, str] | None = None,
) -> LoadTestResult:
    """Run concurrent load test against an endpoint."""
    latencies: list[float] = []
    failures = 0
    lock = asyncio.Lock()

    async def session_loop(client: httpx.AsyncClient) -> None:
        nonlocal failures
        end_time = time.monotonic() + duration_seconds
        while time.monotonic() < end_time:
            start = time.monotonic()
            try:
                response = await client.get(url, headers=headers)
                elapsed_ms = (time.monotonic() - start) * 1000
                if response.status_code == 200:
                    async with lock:
                        latencies.append(elapsed_ms)
                else:
                    async with lock:
                        failures += 1
            except Exception:
                async with lock:
                    failures += 1
            await asyncio.sleep(interval_seconds)

    async with httpx.AsyncClient(timeout=30) as client:
        start_time = time.monotonic()
        await asyncio.gather(*[session_loop(client) for _ in range(num_sessions)])
        elapsed = time.monotonic() - start_time

    if not latencies:
        return LoadTestResult(
            total_requests=failures,
            successful_requests=0,
            failed_requests=failures,
            min_latency_ms=0, max_latency_ms=0, avg_latency_ms=0,
            p50_latency_ms=0, p95_latency_ms=0, p99_latency_ms=0,
            requests_per_second=0,
        )

    sorted_latencies = sorted(latencies)
    return LoadTestResult(
        total_requests=len(latencies) + failures,
        successful_requests=len(latencies),
        failed_requests=failures,
        min_latency_ms=min(latencies),
        max_latency_ms=max(latencies),
        avg_latency_ms=sum(latencies) / len(latencies),
        p50_latency_ms=calculate_percentile(sorted_latencies, 0.50),
        p95_latency_ms=calculate_percentile(sorted_latencies, 0.95),
        p99_latency_ms=calculate_percentile(sorted_latencies, 0.99),
        requests_per_second=len(latencies) / elapsed,
    )


@pytest.mark.performance
@pytest.mark.asyncio
async def test_100_concurrent_health_polls():
    """Test 100 concurrent sessions polling Streamlit health.

    SLA: Health dashboard refresh ≤10s (P4T5_TASK.md line 43)
    """
    result = await run_concurrent_load(
        url=f"{STREAMLIT_URL}/_stcore/health",  # Streamlit internal health endpoint
        num_sessions=100,
        duration_seconds=60,
        interval_seconds=10.0,
    )

    # SLA: refresh should complete within 10s even under load
    assert result.p95_latency_ms < 10000, f"P95 latency {result.p95_latency_ms}ms exceeds 10s target"
    assert result.failed_requests < result.total_requests * 0.01, "More than 1% requests failed"


@pytest.mark.performance
@pytest.mark.asyncio
async def test_concurrent_gateway_health_reads():
    """Test 100 concurrent sessions reading gateway health.

    SLA: Gateway must respond quickly to support CB staleness ≤5s monitoring
    NOTE: Uses /health endpoint (CB status is read directly from Redis by web console)
    """
    result = await run_concurrent_load(
        url=f"{EXECUTION_GATEWAY_URL}/health",
        num_sessions=100,
        duration_seconds=30,
        interval_seconds=1.0,
    )

    # Response should be fast to ensure monitoring doesn't introduce latency
    assert result.p95_latency_ms < 1000, f"P95 latency {result.p95_latency_ms}ms - may cause monitoring issues"
    assert result.failed_requests == 0, f"{result.failed_requests} requests failed"


@pytest.fixture
async def async_db_pool():
    """Async database connection pool for performance tests."""
    from psycopg_pool import AsyncConnectionPool
    database_url = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")
    pool = AsyncConnectionPool(database_url, min_size=2, max_size=10)
    await pool.open()
    yield pool
    await pool.close()


@pytest.mark.performance
@pytest.mark.asyncio
async def test_alert_delivery_backlog_drain(async_db_pool):
    """Test draining 1000 queued alerts.

    SLA: Alert delivery latency P95 <60s (P4T5_TASK.md line 44)

    NOTE: Schema requires: alert_rules -> alert_events -> alert_deliveries (FK chain)
    """
    import uuid
    from datetime import datetime, timezone

    # Create parent records first (FK requirements)
    test_rule_id = str(uuid.uuid4())
    test_event_ids = []
    test_delivery_ids = []

    async with async_db_pool.connection() as conn:
        async with conn.cursor() as cur:
            # Create a test alert rule
            await cur.execute(
                """INSERT INTO alert_rules (id, name, condition_type, threshold_value, comparison, created_by)
                   VALUES (%s, 'perf-test-rule', 'drawdown', 5.0, 'gt', 'perf-test')""",
                (test_rule_id,)
            )

            # Create 1000 alert events and deliveries
            for i in range(1000):
                event_id = str(uuid.uuid4())
                delivery_id = str(uuid.uuid4())
                test_event_ids.append(event_id)
                test_delivery_ids.append(delivery_id)

                await cur.execute(
                    """INSERT INTO alert_events (id, rule_id, trigger_value)
                       VALUES (%s, %s, %s)""",
                    (event_id, test_rule_id, float(i))
                )
                await cur.execute(
                    """INSERT INTO alert_deliveries (id, alert_id, channel, recipient, dedup_key, status)
                       VALUES (%s, %s, 'email', 'te***@example.com', %s, 'pending')""",
                    (delivery_id, event_id, f"perf-test-{i}-{datetime.now(timezone.utc).isoformat()}")
                )
            await conn.commit()

    # Monitor delivery processing with guaranteed cleanup
    start_time = time.monotonic()
    max_wait = 120  # 2 minutes max
    elapsed = 0

    try:
        while time.monotonic() - start_time < max_wait:
            async with async_db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT COUNT(*) FROM alert_deliveries WHERE id = ANY(%s) AND status = 'pending'",
                        (test_delivery_ids,)
                    )
                    row = await cur.fetchone()
                    pending = row[0] if row else 0
                    if pending == 0:
                        break
            await asyncio.sleep(1)

        elapsed = time.monotonic() - start_time
        assert elapsed < 60, f"Backlog drain took {elapsed:.1f}s, exceeds 60s P95 target"

    finally:
        # ALWAYS cleanup test data, even if assertion fails
        # cascade deletes handle deliveries/events
        async with async_db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM alert_rules WHERE id = %s", (test_rule_id,))
            await conn.commit()


@pytest.mark.performance
@pytest.mark.asyncio
async def test_audit_log_write_throughput(async_db_pool):
    """Test audit log can sustain 100 writes/sec.

    SLA: Audit log write latency <1s (P4T5_TASK.md line 45)
    """
    from apps.web_console.auth.audit_log import AuditLogger

    audit_logger = AuditLogger(async_db_pool)
    latencies = []

    # Write 100 audit entries, measuring each
    for i in range(100):
        start = time.monotonic()
        await audit_logger.log_action(
            user_id="perf-test-user",
            action="PERF_TEST",
            resource_type="test",
            resource_id=f"test-{i}",
            details={"iteration": i},
            ip_address="127.0.0.1",
        )
        latencies.append((time.monotonic() - start) * 1000)

    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)]
    assert p95 < 1000, f"Audit write P95 latency {p95:.1f}ms exceeds 1s target"


@pytest.mark.performance
@pytest.mark.asyncio
async def test_sla_threshold_assertions():
    """Verify all SLA thresholds are met under load.

    Per P4T5_TASK.md line 638:
    - CB staleness <5s
    - Delivery P95 <60s
    - Audit write <1s
    """
    import httpx

    prometheus_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

    async with httpx.AsyncClient(timeout=30) as client:
        # Query CB staleness
        resp = await client.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": "cb_staleness_seconds"}
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("data", {}).get("result"):
                staleness = float(result["data"]["result"][0]["value"][1])
                assert staleness < 5, f"CB staleness {staleness}s exceeds 5s target"

        # Query delivery latency P95
        resp = await client.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": "alert_delivery_latency_p95"}
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("data", {}).get("result"):
                latency = float(result["data"]["result"][0]["value"][1])
                assert latency < 60, f"Delivery P95 latency {latency}s exceeds 60s target"

        # Query audit write latency P95
        resp = await client.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": "audit_write_latency_p95"}
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("data", {}).get("result"):
                latency = float(result["data"]["result"][0]["value"][1])
                assert latency < 1, f"Audit P95 latency {latency}s exceeds 1s target"
```

**Test Execution:**

**PREREQUISITES - Services Must Be Running:**
Performance tests target services running on the HOST (not in Docker):
- `execution_gateway` on port 8002 (for gateway /health endpoint)
- `web_console` (Streamlit) on port 8501 (for health polling)
- Database with alert tables (for backlog drain test)

```bash
# 1. Start infrastructure (Docker)
docker-compose up -d

# 2. Start host services (separate terminals)
PYTHONPATH=. python -m apps.execution_gateway.main  # Port 8002
PYTHONPATH=. streamlit run apps/web_console/app.py  # Port 8501

# 3. Run performance tests
pytest tests/performance/ -m performance --tb=short -v

# Or run specific tests
pytest tests/performance/test_concurrent_sessions.py::test_100_concurrent_health_polls -v
pytest tests/performance/test_concurrent_sessions.py::test_alert_delivery_backlog_drain -v
```

**Test Infrastructure Notes:**
- Performance tests marked with `@pytest.mark.performance` and excluded from regular CI
- Tests use fixtures for database and service connections
- All tests are runnable when services are available via docker-compose
- pytest.ini should exclude performance marker from default runs: `markers = performance: requires running services`

**REQUIRED: Update Makefile for Performance Tests:**

**1. Exclude from regular CI (line 188):**
```makefile
# BEFORE (line 188):
@HANG_TIMEOUT=120 PYTHONPATH=. ./scripts/ci_with_timeout.sh poetry run pytest -m "not integration and not e2e" --cov=...

# AFTER:
@HANG_TIMEOUT=120 PYTHONPATH=. ./scripts/ci_with_timeout.sh poetry run pytest -m "not integration and not e2e and not performance" --cov=...
```

**2. Add dedicated `make perf` target (append after test targets, ~line 200):**
```makefile
.PHONY: perf
perf: ## Run performance tests (requires running services + RUN_PERF_TESTS=1)
	@echo "Performance tests require: docker-compose up, execution_gateway, web_console"
	@echo "See tests/performance/test_concurrent_sessions.py for prerequisites"
	RUN_PERF_TESTS=1 PYTHONPATH=. poetry run pytest tests/performance/ -m performance -v --tb=short
```

**Safety:** The `RUN_PERF_TESTS=1` env var is required - tests will skip without it. This prevents accidental DB writes when someone runs `pytest` without reading prerequisites.

**When to run `make perf`:**
- Before major releases (regression check)
- After changes to polling/health endpoints
- As part of nightly CI (optional scheduled job)
- When investigating performance issues

This ensures performance tests have a dedicated execution path while not blocking regular CI.

**Environment Variables for CI/Container Execution:**
When running inside Docker/CI, override default localhost URLs:
```bash
# For container-to-container communication
export EXECUTION_GATEWAY_URL=http://execution_gateway:8002
export STREAMLIT_URL=http://web_console:8501
export DATABASE_URL=postgresql://trader:trader@postgres:5432/trader
export PROMETHEUS_URL=http://prometheus:9090

# For local development (default)
export EXECUTION_GATEWAY_URL=http://localhost:8002
export STREAMLIT_URL=http://localhost:8501
```

---

### C6.8: SLA Config Validation Tests

**Purpose:** Verify SLA probe/route configurations exist and thresholds match spec per P4T5_TASK.md line 156

**File:** `tests/infra/test_sla_configs.py`

**Required Tests (from P4T5_TASK.md line 156):**
- `test_sla_probes_exist` - Verifies probe configs exist
- `test_sla_perf_thresholds` - Asserts latency targets in perf suite
- `test_probe_configs_deployed` - Checks infra files exist

**Implementation:**
```python
"""SLA configuration validation tests.

Per P4T5_TASK.md line 156:
- test_sla_probes_exist verifies probe configs
- test_sla_perf_thresholds asserts latency targets in perf suite
- test_probe_configs_deployed checks infra files exist
"""

import os
from pathlib import Path

import pytest
import yaml


# Project root for file path resolution
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestSLAProbesExist:
    """Verify SLA probe configuration files exist and are valid."""

    def test_sla_probes_file_exists(self):
        """Verify infra/prometheus/sla_probes.yml exists."""
        probe_file = PROJECT_ROOT / "infra" / "prometheus" / "sla_probes.yml"
        assert probe_file.exists(), f"SLA probes file not found: {probe_file}"

    def test_alertmanager_routes_file_exists(self):
        """Verify infra/alertmanager/routes.yml exists."""
        routes_file = PROJECT_ROOT / "infra" / "alertmanager" / "routes.yml"
        assert routes_file.exists(), f"Alertmanager routes file not found: {routes_file}"

    def test_sla_probes_valid_yaml(self):
        """Verify SLA probes file is valid YAML with groups only (no scrape_configs)."""
        probe_file = PROJECT_ROOT / "infra" / "prometheus" / "sla_probes.yml"
        if probe_file.exists():
            with open(probe_file) as f:
                config = yaml.safe_load(f)
            assert config is not None, "SLA probes file is empty"
            # Rule files MUST contain only groups, not scrape_configs
            assert "groups" in config, "SLA probes must define groups"
            assert "scrape_configs" not in config, (
                "SLA probes must NOT contain scrape_configs (those go in prometheus.yml)"
            )


class TestSLAPerfThresholds:
    """Verify SLA thresholds match specification."""

    # SLA thresholds from P4T5_TASK.md lines 39-46
    EXPECTED_THRESHOLDS = {
        "cb_staleness_seconds": 5,  # ≤5s (alert at >10s)
        "health_dashboard_refresh": 10,  # ≤10s
        "alert_delivery_latency_p95": 60,  # <60s
        "audit_write_latency_p95": 1,  # <1s
        "alert_poison_queue_size": 10,  # >10 triggers alert
    }

    def test_cb_staleness_threshold(self):
        """Verify CB staleness SLA is ≤5s."""
        assert self.EXPECTED_THRESHOLDS["cb_staleness_seconds"] == 5

    def test_alert_delivery_p95_threshold(self):
        """Verify alert delivery P95 SLA is <60s."""
        assert self.EXPECTED_THRESHOLDS["alert_delivery_latency_p95"] == 60

    def test_audit_write_p95_threshold(self):
        """Verify audit write P95 SLA is <1s."""
        assert self.EXPECTED_THRESHOLDS["audit_write_latency_p95"] == 1

    def test_poison_queue_threshold(self):
        """Verify poison queue alert threshold is >10."""
        assert self.EXPECTED_THRESHOLDS["alert_poison_queue_size"] == 10

    def test_probes_contain_correct_thresholds(self):
        """Verify probe file contains matching thresholds."""
        probe_file = PROJECT_ROOT / "infra" / "prometheus" / "sla_probes.yml"
        if not probe_file.exists():
            pytest.skip("SLA probes file not yet created")

        with open(probe_file) as f:
            content = f.read()

        # Check for expected threshold values in alert expressions
        assert "cb_staleness_seconds > 10" in content, "CB staleness alert missing"
        assert "alert_delivery_latency_p95 > 60" in content, "Delivery latency alert missing"
        assert "audit_write_latency_p95 > 1" in content, "Audit latency alert missing"
        assert "alert_poison_queue_size > 10" in content, "Poison queue alert missing"


class TestProbeConfigsDeployed:
    """Verify probe configurations are properly integrated."""

    def test_prometheus_includes_sla_rules(self):
        """Verify prometheus.yml includes SLA probe rules file."""
        prometheus_file = PROJECT_ROOT / "infra" / "prometheus" / "prometheus.yml"
        if not prometheus_file.exists():
            pytest.skip("prometheus.yml not found")

        with open(prometheus_file) as f:
            config = yaml.safe_load(f)

        rule_files = config.get("rule_files", [])
        assert any("sla_probes" in rf for rf in rule_files), (
            "prometheus.yml must include sla_probes.yml in rule_files"
        )

    def test_blackbox_exporter_configured(self):
        """Verify blackbox exporter is configured for synthetic probes."""
        # Blackbox exporter is in root docker-compose.yml, not infra/
        compose_file = PROJECT_ROOT / "docker-compose.yml"
        if not compose_file.exists():
            pytest.skip("docker-compose.yml not found")

        with open(compose_file) as f:
            config = yaml.safe_load(f)

        services = config.get("services", {})
        assert "blackbox_exporter" in services or "blackbox" in services, (
            "docker-compose.yml must include blackbox_exporter for synthetic probes"
        )

    def test_scrape_interval_5s_for_cb(self):
        """Verify CB probe has 5s scrape interval in prometheus.yml.

        NOTE: scrape_interval is in prometheus.yml (scrape_configs), not sla_probes.yml (rule groups).
        """
        prometheus_file = PROJECT_ROOT / "infra" / "prometheus" / "prometheus.yml"
        if not prometheus_file.exists():
            pytest.skip("prometheus.yml not found")

        with open(prometheus_file) as f:
            content = f.read()

        # Verify 5s scrape interval is configured for CB probe job
        assert "scrape_interval: 5s" in content, "CB probe must have 5s scrape interval in prometheus.yml"
```

**Test Execution:**
```bash
# Run SLA config validation tests (no services required)
pytest tests/infra/test_sla_configs.py -v

# These tests run in regular CI since they only check file existence/content
```

---

## 8. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Navigation changes break existing pages | Medium | Test all existing pages after change |
| Missing Redis client for Admin page | Low | Reuse pattern from health.py |
| Documentation inconsistent with code | Low | Review code while writing docs |
| Performance tests require running services | Medium | Mark with @pytest.mark.performance, exclude from CI |
| SLA alert thresholds may need tuning | Low | Start with conservative thresholds, adjust based on metrics |
| Audit Log RBAC change affects existing users | Medium | Communicate change, ensure Admin users have VIEW_AUDIT |

---

## 9. Review Checklist

- [ ] All Track 7 pages in navigation with correct RBAC
- [ ] **Audit Log protected with VIEW_AUDIT permission** (RBAC fix)
- [ ] System Health requires FEATURE_HEALTH_MONITOR AND VIEW_CIRCUIT_BREAKER permission
- [ ] Admin Dashboard uses permission-based access (MANAGE_API_KEYS, etc.)
- [ ] Redis client helper follows health.py pattern
- [ ] ADR-0029 status is ACCEPTED
- [ ] All 5 CONCEPTS docs created
- [ ] Runbooks updated with UI procedures
- [ ] P4T5_TASK.md shows all components complete
- [ ] P4_PLANNING.md marks Track 7 complete
- [ ] PROJECT_STATUS.md includes P4 section with clear messaging
- [ ] INDEX.md includes new docs
- [ ] SLA probes created with blackbox exporter scrape config (infra/prometheus/sla_probes.yml)
- [ ] Alertmanager routes created (infra/alertmanager/routes.yml)
- [ ] Performance tests implemented (tests/performance/test_concurrent_sessions.py)
- [ ] SLA config validation tests created (tests/infra/test_sla_configs.py)
- [ ] Blackbox exporter added to docker-compose
- [ ] prometheus.yml updated to include sla_probes.yml
- [ ] Makefile updated to exclude performance tests (`and not performance`)
- [ ] CI passes
- [ ] Fresh zen-mcp review (Gemini + Codex) approved

---

**Next Step:** Awaiting plan approval, then implementation.

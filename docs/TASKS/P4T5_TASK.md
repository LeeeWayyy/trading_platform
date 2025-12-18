---
id: P4T5
title: "Web Console - Operations Dashboards"
phase: P4
task: T5
priority: P0
owner: "@development-team"
state: TASK
created: 2025-12-18
dependencies: [T6.1, health-endpoints, secrets-provisioning]
estimated_effort: "18-26 days"
related_adrs: [ADR-0026-alerting-system]
related_docs: [P4_PLANNING.md]
features: [T7.1, T7.2, T7.5, T7.3, T7.4]
---

# P4T5: Web Console - Operations Dashboards

**Phase:** P4 (Advanced Features & Research)
**Status:** TASK (Not Started)
**Priority:** P0 (Operational Safety)
**Owner:** @development-team
**Created:** 2025-12-18
**Estimated Effort:** 18-26 days
**Track:** Track 7 from P4_PLANNING.md

---

## Objective

Build operational monitoring and control dashboards for the trading platform. These dashboards provide real-time visibility into system health, circuit breaker state, alerting, and administrative controls.

**Success looks like:**
- Operators can monitor and control circuit breakers without SSH/CLI access
- System health is visible at a glance with latency metrics and service status
- Alerts are configurable through UI with multi-channel delivery (email, Slack, SMS)
- Administrators can manage users, roles, and platform configuration

**Measurable SLAs:**
| Metric | Target | Measurement |
|--------|--------|-------------|
| CB status staleness | ≤5s | Time from Redis update to UI display |
| Health dashboard refresh | ≤10s | Polling interval for service status |
| Alert delivery latency | P95 <60s | Time from trigger to delivery confirmation |
| Audit log write latency | <1s | Time from action to audit record commit |
| Dashboard availability | 99.5% | Uptime during trading hours |

---

## Acceptance Criteria

### T7.1 Circuit Breaker Dashboard
- [ ] Real-time circuit breaker status display (OPEN/TRIPPED) with color coding
- [ ] **Canonical Redis key:** `circuit_breaker:state` (OPEN|TRIPPED), `circuit_breaker:last_trip_reason`, `circuit_breaker:last_trip_at`
- [ ] Trip/reset history table with timestamps and reasons
- [ ] Manual trip/reset controls with RBAC enforcement (operator/admin roles only)
- [ ] **Step-up confirmation for reset operations:** Confirmation dialog requiring reason text (min 20 chars) + checkbox acknowledgment (NOT full TOTP/WebAuthn - deferred to T6.1 MFA)
- [ ] Rate limiting (max 1 reset per minute) to prevent accidental spam
- [ ] Persistent audit log for all manual interventions
- [ ] Auto-refresh via polling (≤5s staleness)
- [ ] Prometheus metrics: `cb_status_checks_total`, `cb_trip_total`, `cb_reset_total`

### T7.2 System Health Monitor
- [ ] Service status grid showing health of all microservices
- [ ] Redis and Postgres connectivity indicators
- [ ] Queue depth metrics (Redis streams)
- [ ] Latency metrics (P50, P95, P99) with charts
- [ ] Last successful operation timestamps per service
- [ ] Auto-refresh (≤10s interval)
- [ ] Graceful degradation: show cached status with staleness indicator on fetch failure
- [ ] Contract tests for `/health` endpoint schema stability

### T7.5 Alert Delivery Service
- [ ] Email delivery via SMTP/SendGrid with retry
- [ ] Slack webhook integration
- [ ] SMS delivery via Twilio
- [ ] Delivery retry with exponential backoff (1s, 2s, 4s; max 3 attempts)
- [ ] Delivery status tracking (pending, delivered, failed, poison)
- [ ] **Idempotency:** Dedup key = `{alert_id}:{channel}:{recipient}:{hour_bucket}` (1-hour window)
- [ ] **Hour bucket definition:** UTC ISO 8601 truncated to hour, e.g., `2025-12-18T14:00:00Z` for any time in 14:xx UTC; **derived from original alert trigger timestamp** (not current time), ensuring retries crossing hour boundaries remain idempotent
- [ ] **Rate limits (per-channel):** Email 100/min, Slack 50/min, SMS 10/min
- [ ] **Rate limits (per-recipient):** Max 5 alerts/hour per email, 3/hour per phone
- [ ] **Rate limits (global burst):** Max 500 deliveries/min total; excess queued
- [ ] **Rate limit mechanism:** Redis token bucket with TTL; keys: `ratelimit:{channel}:{minute}`, `ratelimit:recipient:{hash}:{hour}`, `ratelimit:global:{minute}`; INCR + EXPIRE pattern; works across distributed workers via atomic Redis operations
- [ ] **Recipient hashing:** HMAC-SHA256 with `ALERT_RECIPIENT_HASH_SECRET` env var; deterministic across services; secret stored in secrets manager, rotated quarterly; hash = first 16 chars of hex digest
- [ ] **Poison queue:** Failed after 3 attempts → move to poison queue for manual review
- [ ] **Poison queue monitoring:** Metric `alert_poison_queue_size` with alert rule `> 10` → page on-call
- [ ] **Max queue depth:** 10,000 pending deliveries; reject new if exceeded with HTTP 503 + `Retry-After: 60`; increment `alert_queue_full_total` metric; auto-resume accepting when backlog < 8,000
- [ ] Prometheus metrics: `alert_delivery_attempts_total`, `alert_delivery_latency_seconds`, `alert_throttle_total`, `alert_dropped_total`

### T7.3 Alert Configuration UI
- [ ] Threshold configuration form (drawdown limits, position limits, latency thresholds)
- [ ] Notification channel setup with credential masking (show last 4 chars only)
- [ ] Alert rules editor (condition → action mapping) with Pydantic validation
- [ ] Alert history table with acknowledgment tracking (reads from `alert_events` table; UI allows setting `acknowledged_at`, `acknowledged_by`, `acknowledgment_note`)
- [ ] Test notification button for each channel
- [ ] PII handling: phone/email masked in UI and logs (`***@domain.com`, `***1234`)

### T7.4 Admin Dashboard
- [ ] User management table (create, update, disable accounts) with Last Active IP column for session correlation
- [ ] Role and permission assignment UI
- [ ] **API key management:**
  - [ ] **Key format:** 32 random bytes (256-bit entropy), base64url encoded = 43 chars
  - [ ] **Key prefix:** `tp_live_{first8chars}` for identification (checked for uniqueness on create)
  - [ ] One-time key display on creation (never shown again, modal with copy button)
  - [ ] SHA-256 salted hashing for storage (16-byte random salt per key)
  - [ ] Scoped keys with granular permissions (validated via Pydantic model)
  - [ ] Rotation workflow: create new → migrate → revoke old (revoked keys have `revoked_at` timestamp)
  - [ ] **Revocation propagation:** Cache revoked key prefixes in Redis with 5-min TTL; validation checks cache before DB
  - [ ] Last used timestamp tracking (updated on each successful auth, debounced to 1-min resolution)
  - [ ] **Audit/log redaction:** Never log full key, salt, or hash; only log key prefix and last 4 chars
- [ ] System configuration editor (trading hours, limits, defaults)
- [ ] Audit log viewer with filtering (user, action, timestamp)
- [ ] RBAC: Admin role required for all operations
- [ ] Audit events for: user create/update/disable, role change, key create/revoke, config change

### Auth Stub Security (CI Guards)
- [ ] `test_no_dev_auth_in_prod`: CI fails if `OPERATIONS_DEV_AUTH=true` in `.env.prod` or `docker-compose.prod.yml`
- [ ] `test_no_dev_auth_in_staging`: CI fails if `OPERATIONS_DEV_AUTH=true` in staging configs
- [ ] **Runtime guard:** App refuses to start if `OPERATIONS_DEV_AUTH=true` AND `ENVIRONMENT` is `production` or `staging`
- [ ] `test_no_auth_stub_references_after_t61`: After T6.1 ships, CI fails if `operations_requires_auth` referenced

### RBAC Role Matrix (Per Page/Action)
| Page | Action | Viewer | Operator | Admin |
|------|--------|--------|----------|-------|
| CB Dashboard | View status | ✓ | ✓ | ✓ |
| CB Dashboard | Trip circuit | ✗ | ✓ | ✓ |
| CB Dashboard | Reset circuit | ✗ | ✓ | ✓ |
| Health Monitor | View all | ✓ | ✓ | ✓ |
| Alert Config | View rules | ✓ | ✓ | ✓ |
| Alert Config | Create/edit rules | ✗ | ✓ | ✓ |
| Alert Config | Delete rules | ✗ | ✗ | ✓ |
| Alert Config | Test notification | ✗ | ✓ | ✓ |
| Admin Dashboard | View users | ✗ | ✗ | ✓ |
| Admin Dashboard | Manage users | ✗ | ✗ | ✓ |
| Admin Dashboard | Manage API keys | ✗ | ✗ | ✓ |
| Admin Dashboard | View audit log | ✗ | ✗ | ✓ |
| Admin Dashboard | Edit config | ✗ | ✗ | ✓ |

**Auth Transition Plan:**
- [ ] **Phase 1 (dev stub):** `OPERATIONS_DEV_AUTH=true` returns admin role; tests validate role matrix with parameterized fixtures for viewer/operator/admin
- [ ] **Phase 2 (T6.1 ships):** Feature flag `USE_REAL_AUTH=true` routes to OAuth2; dev stub deprecated with removal date
- [ ] **Phase 3 (stub removal):** Remove `operations_requires_auth`, delete dev stub code; CI gate blocks merge if references exist
- [ ] **Denial tests:** `test_viewer_cannot_trip_cb`, `test_operator_cannot_delete_alert_rule`, `test_non_admin_cannot_access_admin_page`

### SLA Validation & Monitoring
- [ ] **Synthetic probes:** Scheduled healthcheck hitting CB status endpoint every 5s, alert if >5s stale
- [ ] **Probe deployment:** Probes defined in `infra/prometheus/sla_probes.yml`; deployed via `make deploy-monitoring`
- [ ] **Grafana SLO dashboard:** Panels for CB staleness, health refresh latency, alert delivery P95
- [ ] **Prometheus alert rules:**
  - `cb_staleness_seconds > 10` → page on-call via PagerDuty (owner: @platform-team)
  - `alert_delivery_latency_p95 > 60` → warn to #alerts-ops Slack (owner: @platform-team)
  - `audit_write_latency_p95 > 1` → warn to #alerts-ops Slack (owner: @platform-team)
  - `alert_poison_queue_size > 10` → page on-call via PagerDuty (owner: @platform-team)
- [ ] **Alert routing config:** Defined in `infra/alertmanager/routes.yml` with team ownership comments
- [ ] **Tests:** `test_sla_probes_exist` verifies probe configs; `test_sla_perf_thresholds` asserts latency targets in perf suite; `test_probe_configs_deployed` checks infra files exist

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **Health endpoints available:** All services expose `/health` with stable schema
- [ ] **Secrets provisioned:** SMTP credentials, SendGrid API key, Slack webhook URL, Twilio SID/token
- [ ] **Database extension:** `pgcrypto` enabled for `gen_random_uuid()`
- [ ] **Redis access:** Read/write access to circuit breaker keys
- [ ] **Async worker infrastructure (REQUIRED):** Celery/RQ worker for alert delivery retries - sync processing cannot meet P95 <60s SLA under load; reuse existing `backtest_worker` with dedicated `alerts` queue or provision `alert_worker`

---

## Approach

### High-Level Plan

1. **Prep & Validation** (1 day)
   - Verify prerequisites checklist
   - Provision secrets in dev environment
   - Confirm health endpoint schema stability
   - Create ADR-0026 outline

2. **T7.1 Circuit Breaker Dashboard** (3-4 days)
   - Create circuit breaker status page with Redis integration
   - Implement trip/reset controls with RBAC and 2FA
   - Add audit logging and metrics

3. **T7.2 System Health Monitor** (3-4 days)
   - Create health check client with schema validation
   - Build status grid with staleness indicators
   - Add latency metric visualization with caching/backoff

4. **T7.5 Alert Delivery Service** (4-5 days)
   - Design idempotency model and migration
   - Implement channel handlers with retry/backoff
   - Add rate limiting and poison queue handling
   - Expose metrics and structured logs

5. **T7.3 Alert Configuration UI** (3-4 days)
   - Build alert rules editor with Pydantic validation
   - Create threshold configuration forms
   - Add credential masking and PII handling
   - Connect to delivery service

6. **T7.4 Admin Dashboard** (4-6 days)
   - Implement user management with RBAC
   - Build API key lifecycle (generate, hash, rotate, revoke)
   - Add system config editor with audit logging
   - Create audit log viewer with PII masking

7. **Integration & NFRs** (2 days)
   - Navigation integration and feature flags
   - Performance/soak tests for polling
   - Documentation, runbooks, ADR finalization

### Auth Dependency Strategy

Since T6.1 (Auth/RBAC) is not yet complete, we use the same dev auth stub pattern as T5.3:

- **If T6.1 complete:** Use production OAuth2 auth via `@requires_auth`
- **If T6.1 pending:** Use dev-mode auth stub with `OPERATIONS_DEV_AUTH=true` env var
- **Dev-mode stub:** Returns fixed user `{"username": "dev_user", "role": "admin"}`
- **CI enforcement:** Tests fail if `OPERATIONS_DEV_AUTH=true` in prod/staging configs
- **Runtime enforcement:** App refuses to start with stub in non-dev environment

**Auth Stub with Runtime Guard:**
```python
# apps/web_console/auth/operations_auth.py
import functools
import os
import sys
from typing import Any
from collections.abc import Callable

import streamlit as st

from apps.web_console.auth.streamlit_helpers import requires_auth


def _check_dev_auth_safety() -> None:
    """Runtime guard: refuse to start if dev auth enabled in prod/staging."""
    if os.getenv("OPERATIONS_DEV_AUTH", "false").lower() == "true":
        env = os.getenv("ENVIRONMENT", "development").lower()
        if env in ("production", "staging"):
            print(
                f"FATAL: OPERATIONS_DEV_AUTH=true is not allowed in {env}. "
                "Remove this env var or set ENVIRONMENT=development.",
                file=sys.stderr,
            )
            sys.exit(1)


# Run check at module import time
_check_dev_auth_safety()


def operations_requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Auth decorator with dev-mode fallback for Track 7 operations.

    CRITICAL: Dev stub must set the same session keys as real OAuth2 auth.
    """
    if os.getenv("OPERATIONS_DEV_AUTH", "false").lower() == "true":
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            st.session_state["authenticated"] = True
            st.session_state["username"] = "dev_user"
            st.session_state["user_id"] = "dev_user_id"
            st.session_state["auth_method"] = "dev_stub"
            st.session_state["session_id"] = "dev_session"
            st.session_state["role"] = "admin"  # Admin for full operations access
            st.session_state["strategies"] = ["*"]
            return func(*args, **kwargs)
        return wrapper
    else:
        return requires_auth(func)
```

### Logical Components

**Component 0: Prep & Validation**
- Verify all prerequisites
- Provision secrets in dev
- Confirm health endpoint stability
- Request zen-mcp review
- Commit after approval

**Component 1: Circuit Breaker Dashboard (T7.1)**
- Implement Redis-based CB status fetching
- Create status card component with color indicators
- Implement trip/reset controls with RBAC
- Add 2FA confirmation dialog
- Add rate limiting (1 reset/minute)
- Create audit log table
- Add Prometheus metrics
- Request zen-mcp review
- Commit after approval

**Component 2: System Health Monitor (T7.2)**
- Implement health check client with schema validation
- Create service status grid with staleness indicators
- Add Redis/Postgres connectivity checks
- Implement queue depth fetching with caching
- Add latency chart component
- Add graceful degradation on failures
- Request zen-mcp review
- Commit after approval

**Component 3: Alert Delivery Service (T7.5)**
- Create delivery service with idempotency model
- Implement SMTP/SendGrid email with rate limiting
- Implement Slack webhook with rate limiting
- Implement Twilio SMS with rate limiting
- Add retry logic with exponential backoff
- Implement poison queue for failed deliveries
- Create delivery tracking schema/migration
- Add Prometheus metrics and structured logs
- Request zen-mcp review
- Commit after approval

**Component 4: Alert Configuration UI (T7.3)**
- Create alert rules editor with Pydantic validation
- Implement threshold configuration form
- Add notification channel setup with credential masking
- Create alert history table
- Add test notification functionality
- Implement PII masking in UI and logs
- Connect to delivery service
- Request zen-mcp review
- Commit after approval

**Component 5: Admin Dashboard (T7.4)**
- Create user management table with RBAC
- Implement user CRUD operations with audit
- Add role/permission assignment UI
- Create API key management with:
  - One-time display on creation
  - SHA-256 salted hashing
  - Scoped permissions
  - Rotation workflow
- Add system configuration editor with audit
- Create audit log viewer with PII masking
- Request zen-mcp review
- Commit after approval

**Component 6: Integration & Documentation**
- Add all pages to navigation
- Update feature flags
- Create concept documentation
- Create ADR-0026 for alerting system
- Update runbooks for alert routing and CB operations
- Performance/soak tests
- End-to-end integration testing
- Request zen-mcp review
- Commit after approval

---

## Technical Details

### Files to Create

**T7.1 Circuit Breaker Dashboard:**
- `apps/web_console/pages/circuit_breaker.py` - Main CB dashboard page
- `apps/web_console/components/cb_status_card.py` - Status display component
- `apps/web_console/components/cb_history_table.py` - Trip/reset history
- `apps/web_console/components/cb_controls.py` - Trip/reset controls with 2FA
- `apps/web_console/auth/operations_auth.py` - Dev auth stub with runtime guard
- `tests/apps/web_console/test_circuit_breaker_dashboard.py`
- `tests/apps/web_console/test_cb_authorization.py`
- `tests/apps/web_console/test_operations_auth_governance.py`
- `docs/CONCEPTS/circuit-breaker-ui.md`

**T7.2 System Health Monitor:**
- `apps/web_console/pages/health.py` - Health monitor page
- `apps/web_console/components/service_status_grid.py` - Status grid with staleness
- `apps/web_console/components/latency_chart.py` - Latency visualization
- `libs/health/health_client.py` - Health check client with validation
- `tests/apps/web_console/test_health_dashboard.py`
- `tests/libs/health/test_health_client.py`
- `tests/libs/health/test_health_contract.py` - Contract tests for /health schema
- `docs/CONCEPTS/system-health-monitoring.md`

**T7.5 Alert Delivery Service:**
- `libs/alerts/delivery_service.py` - Multi-channel delivery with idempotency
- `libs/alerts/alert_manager.py` - Alert orchestration
- `libs/alerts/channels/email.py` - Email delivery with rate limiting
- `libs/alerts/channels/slack.py` - Slack webhook with rate limiting
- `libs/alerts/channels/sms.py` - Twilio SMS with rate limiting
- `libs/alerts/dedup.py` - Deduplication logic
- `libs/alerts/poison_queue.py` - Poison queue handling
- `libs/alerts/models.py` - Pydantic models for channel config
- `db/migrations/0010_create_alert_tables.sql` - Alert rules, events (with ack fields), deliveries schemas (idempotent)
- `tests/libs/alerts/test_delivery_service.py`
- `tests/libs/alerts/test_alert_manager.py`
- `tests/libs/alerts/test_dedup.py`
- `tests/libs/alerts/test_retry_logic.py`
- `docs/CONCEPTS/alert-delivery.md`
- `docs/ADRs/ADR-0026-alerting-system.md`

**T7.3 Alert Configuration UI:**
- `apps/web_console/pages/alerts.py` - Alert configuration page
- `apps/web_console/components/alert_rule_editor.py` - Rules editor with validation
- `apps/web_console/components/threshold_config.py` - Threshold form
- `apps/web_console/components/notification_channels.py` - Channel setup with masking
- `apps/web_console/components/alert_history.py` - Alert history table
- `tests/apps/web_console/test_alert_configuration.py`
- `docs/CONCEPTS/alerting.md`

**T7.4 Admin Dashboard:**
- `apps/web_console/pages/admin.py` - Admin dashboard page
- `apps/web_console/components/user_table.py` - User management
- `apps/web_console/components/role_editor.py` - Role assignment
- `apps/web_console/components/api_key_manager.py` - API key lifecycle
- `apps/web_console/components/config_editor.py` - System config
- `apps/web_console/components/audit_log_viewer.py` - Audit log with masking
- `libs/admin/api_keys.py` - Key generation and hashing
- `db/migrations/0011_create_api_keys.sql` - API key schema (idempotent)
- `tests/apps/web_console/test_admin.py`
- `tests/libs/admin/test_api_keys.py`
- `docs/CONCEPTS/platform-administration.md`

### Files to Modify

- `apps/web_console/app.py` - Add new pages to navigation
- `apps/web_console/config.py` - Add feature flags
- `pyproject.toml` - Add new dependencies (sendgrid, twilio, etc.)

### Database Changes

**Alert Delivery Tracking (T7.5):**
```sql
-- Enable pgcrypto if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Alert events for history/acknowledgment (T7.3)
CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id UUID NOT NULL REFERENCES alert_rules(id),
    triggered_at TIMESTAMPTZ NOT NULL,
    trigger_value DECIMAL(10, 4),  -- actual value that triggered alert
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by UUID,  -- user_id who acknowledged
    acknowledgment_note TEXT,
    routed_channels JSONB NOT NULL DEFAULT '[]',  -- ["email", "slack"]
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alert_events_rule ON alert_events(rule_id);
CREATE INDEX idx_alert_events_triggered ON alert_events(triggered_at);
CREATE INDEX idx_alert_events_unacked ON alert_events(acknowledged_at) WHERE acknowledged_at IS NULL;

-- Data retention: partition by month, retain 90 days
-- CREATE TABLE alert_events_y2025m01 PARTITION OF alert_events FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');

CREATE TABLE IF NOT EXISTS alert_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id UUID NOT NULL REFERENCES alert_events(id),
    channel VARCHAR(20) NOT NULL,  -- email, slack, sms
    recipient TEXT NOT NULL,
    dedup_key VARCHAR(255) NOT NULL,  -- {alert_id}:{channel}:{recipient}:{hour_bucket}
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, delivered, failed, poison
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(dedup_key)  -- Enforce idempotency
);

CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status ON alert_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_created ON alert_deliveries(created_at);

CREATE TABLE IF NOT EXISTS alert_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    condition_type VARCHAR(50) NOT NULL,  -- drawdown, position_limit, latency
    threshold_value DECIMAL(10, 4) NOT NULL,
    comparison VARCHAR(10) NOT NULL,  -- gt, lt, eq, gte, lte
    channels JSONB NOT NULL DEFAULT '[]',  -- Validated via Pydantic model
    enabled BOOLEAN DEFAULT true,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pydantic model for channels validation:
-- class ChannelConfig(BaseModel):
--     type: Literal["email", "slack", "sms"]
--     recipient: str  # email, webhook URL, or phone
--     enabled: bool = True
```

**API Keys (T7.4):**
```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,  -- SHA-256 salted hash
    key_salt VARCHAR(64) NOT NULL,   -- Unique salt per key (16 bytes hex)
    key_prefix VARCHAR(20) NOT NULL UNIQUE,  -- "tp_live_abc12345" - UNIQUE constraint enforced
    scopes JSONB NOT NULL DEFAULT '[]',  -- ["read:positions", "write:orders"]
    expires_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Prefix uniqueness enforced via UNIQUE constraint; collision retry in app layer (max 3 attempts)
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
```

**Audit Log (T7.4):**
```sql
-- Idempotent migration: check if table exists, extend if needed
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_log') THEN
        CREATE TABLE audit_log (
            id BIGSERIAL PRIMARY KEY,
            user_id UUID,
            username VARCHAR(255),
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(100),
            resource_id VARCHAR(255),
            details JSONB,  -- PII-masked before storage
            ip_address INET,
            request_id VARCHAR(36),  -- For tracing correlation
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    ELSE
        -- Add missing columns if table exists from earlier migration
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name = 'request_id') THEN
            ALTER TABLE audit_log ADD COLUMN request_id VARCHAR(36);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'audit_log' AND column_name = 'ip_address') THEN
            ALTER TABLE audit_log ADD COLUMN ip_address INET;
        END IF;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_request ON audit_log(request_id);
```

---

## Dependencies

**Blockers (must verify before starting):**
- T6.1 (Auth/RBAC): Using dev auth stub as workaround with runtime guards
- Health endpoints: All services must expose stable `/health` schema
- Secrets: SMTP, SendGrid, Slack webhook, Twilio credentials provisioned
- Database: `pgcrypto` extension enabled

**Infrastructure Requirements:**
- Redis: Read/write access to circuit breaker keys
- Celery/RQ worker: For async delivery retries (or sync-with-timeout design)

**Blocks (other tasks waiting on this):**
- Track 8 (Data Management) - Can proceed in parallel
- Track 9 (Research & Reporting) - Can proceed in parallel

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| T6.1 Auth not ready | Med | High | Dev auth stub with runtime guard blocking prod/staging |
| Circuit breaker race conditions | High | Low | Use Redis atomic operations (WATCH/MULTI/EXEC) |
| Alert delivery failures | Med | Med | Retry with backoff, poison queue for manual review |
| Admin actions without audit | High | Low | Mandatory audit logging, verified in tests |
| 2FA implementation complexity | Med | Med | Simple confirmation dialog with reason text |
| Dev auth stub leaks to prod | High | Med | Runtime guard + CI tests + env checks |
| Provider rate limits | Med | Med | Per-channel rate limiting in delivery service |
| Redis/Postgres outage | High | Low | Graceful degradation with staleness indicators |

---

## Testing Strategy

### Test Coverage Needed

**Unit Tests:**
- Circuit breaker status parsing
- Alert rule condition evaluation
- Delivery channel handlers (mocked providers)
- Deduplication key generation
- API key hash/salt generation
- Health check client parsing
- Audit log formatting with PII masking

**Integration Tests:**
- Redis circuit breaker read/write
- Postgres alert rule CRUD
- Alert event creation and acknowledgment flow
- Service health check endpoints
- Delivery service with mock channels
- API key lifecycle (create, use, revoke)

**Contract Tests:**
- `/health` endpoint schema stability across services

**E2E Tests:**
- Circuit breaker trip/reset flow
- Alert configuration and test notification
- User management workflow
- Audit log capture verification
- API key rotation workflow (create new → migrate → revoke old)
- API key replay after revocation (must fail auth)
- API key last-used timestamp updates (debounced)

**Security Tests:**
- RBAC enforcement for all operations
- Step-up confirmation for destructive actions (reason + checkbox validation)
- Rate limiting verification (per-channel, per-recipient, global burst)
- Distributed rate limit enforcement (simulate 2+ workers, verify Redis atomic ops)
- Audit log completeness
- Dev auth stub blocked in prod/staging
- PII masking verification
- Log redaction verification (no PII in captured logs)

**Fault Injection Tests:**
- Redis unavailable: CB dashboard shows cached status with warning
- Postgres unavailable: Alert config shows read-only mode
- Provider timeout: Delivery retries with backoff
- Health endpoint timeout: Grid shows stale indicator

**Performance Tests:**
- Polling under load (100 concurrent dashboard sessions)
- Delivery backlog processing (1000 queued alerts)
- Audit log write throughput (100 writes/sec)
- SLA threshold assertions (CB staleness <5s, delivery P95 <60s, audit write <1s)

**Accessibility Tests:**
- WCAG 2.1 AA compliance for all dashboards
- Keyboard navigation for critical controls
- Screen reader compatibility

---

## Non-Functional Requirements

### Observability

**Prometheus Metrics:**
- `cb_status_checks_total` - Counter of CB status fetches
- `cb_trip_total{reason}` - Counter of CB trips by reason
- `cb_reset_total` - Counter of CB resets
- `health_check_duration_seconds{service}` - Histogram of health check latency
- `alert_delivery_attempts_total{channel,status}` - Counter of delivery attempts
- `alert_delivery_latency_seconds{channel}` - Histogram of delivery latency
- `admin_action_total{action}` - Counter of admin actions

**Structured Logging:**
- All operations include `request_id` for tracing
- Alert deliveries log: `alert_id`, `channel`, `recipient_masked`, `status`
- Admin actions log: `user_id`, `action`, `resource_type`, `resource_id`

**Log Redaction Policy:**
- **PII masking:** Email → `***@domain.com`, phone → `***1234`, names → first initial only
- **Secrets:** Never log API keys, salts, hashes, passwords, tokens
- **Audit-safe fields:** `user_id`, `key_prefix`, `action`, `resource_type`, `timestamp`
- **Implementation:** Use `libs/common/log_sanitizer.py` with regex patterns; applied at logger config level
- **Test:** `test_log_redaction_no_pii` verifies no email/phone patterns in captured logs

**Tracing:**
- Request IDs propagated through health check flows
- Alert delivery chains traceable from trigger to confirmation

---

## Documentation Requirements

### Must Create
- [ ] `docs/CONCEPTS/circuit-breaker-ui.md`
- [ ] `docs/CONCEPTS/system-health-monitoring.md`
- [ ] `docs/CONCEPTS/alert-delivery.md`
- [ ] `docs/CONCEPTS/alerting.md`
- [ ] `docs/CONCEPTS/platform-administration.md`
- [ ] `docs/ADRs/ADR-0026-alerting-system.md` - Covers architecture, data retention, rate limits

### Must Update
- [ ] `docs/GETTING_STARTED/PROJECT_STATUS.md` when complete
- [ ] `docs/TASKS/P4_PLANNING.md` - Mark Track 7 complete
- [ ] `docs/RUNBOOKS/ops.md` - Add alert routing procedures
- [ ] `docs/RUNBOOKS/circuit-breaker-ops.md` - Add UI-based trip/reset procedures

---

## Related

**ADRs:**
- ADR-0026: Alerting System Architecture (to create)
  - Scope: Architecture, channel handlers, data retention, rate limits, idempotency

**Documentation:**
- [P4_PLANNING.md](./P4_PLANNING.md) - Track 7 specification
- [P4T4_5.3_TASK.md](./P4T4_5.3_TASK.md) - Auth stub pattern reference

**Tasks:**
- Depends on: T6.1 (Auth/RBAC) - using stub workaround with runtime guards
- Related: T5.3 (Backtest Web UI) - shares auth stub pattern

---

## Implementation Tracking

**Branch:** `feature/P4T5-web-console-operations`
**Started:** 2025-12-18

### Component Breakdown

| # | Component | Status | Effort | Dependencies |
|---|-----------|--------|--------|--------------|
| C0 | Prep & Validation | 📋 Pending | 1d | - |
| C1 | T7.1 Circuit Breaker Dashboard | 📋 Pending | 3-4d | C0 |
| C2 | T7.2 System Health Monitor | 📋 Pending | 3-4d | C0 |
| C3 | T7.5 Alert Delivery Service | 📋 Pending | 4-5d | C0 |
| C4 | T7.3 Alert Configuration UI | 📋 Pending | 3-4d | C3 |
| C5 | T7.4 Admin Dashboard | 📋 Pending | 4-6d | C0 |
| C6 | Integration & Documentation | 📋 Pending | 2d | C1-C5 |

**Total Estimated Effort:** 17-25 days (with buffer for secrets provisioning and integration)

### Key Implementation Notes

1. **Auth Stub Pattern:** Follows T5.3 pattern with `OPERATIONS_DEV_AUTH` env var + runtime guard
2. **Circuit Breaker Keys (CANONICAL):**
   - `circuit_breaker:state` → OPEN | TRIPPED
   - `circuit_breaker:last_trip_reason` → string reason
   - `circuit_breaker:last_trip_at` → ISO 8601 timestamp
   - **NOT** `cb:state` (legacy alias, do not use)
3. **Health Endpoints:** Each service exposes `/health` - verify schema stability before implementation
4. **Alert Idempotency:** Dedup key = `{alert_id}:{channel}:{recipient}:{hour_bucket}` (UTC ISO truncated to hour)
5. **API Key Security:** 32-byte entropy, one-time display, SHA-256 salted hash, prefix uniqueness check, never log full key
6. **PII Handling:** Mask email (`***@domain.com`), phone (`***1234`) in UI and logs via `log_sanitizer.py`
7. **Step-up Confirmation:** Confirmation dialog with reason text (min 20 chars) + checkbox acknowledgment; **TODO in code: link to T6.1 MFA upgrade path** - include comment `# TODO(T6.1): Replace with TOTP/WebAuthn when MFA ships`
8. **Async Worker:** Alert delivery retries MUST run on async worker to meet P95 <60s SLA; reuse `backtest_worker` with `alerts` queue
9. **Migration Idempotency:** Migrations 0010 and 0011 must check for existing tables/extensions before CREATE
10. **Rate Limit Keys:** Use `ratelimit:{channel}:{minute}`, `ratelimit:recipient:{hash}:{hour}`, `ratelimit:global:{minute}` with INCR+EXPIRE

---

## Notes

- Track 7 is marked as P0 (Operational Safety) because circuit breaker visibility and control are critical for production operations
- The Admin Dashboard (T7.4) is the most complex component due to API key security requirements
- Alert Delivery Service (T7.5) is listed before Alert Config UI (T7.3) because it's the backend dependency
- Polling is acceptable for MVP; WebSocket can be added in future iteration
- Time estimates (17-25 days) account for: dual FE/BE work, migrations, security hardening, SLA probes, and async worker setup
- **Step-up confirmation vs MFA:** This task implements simple confirmation gates (reason + checkbox); true 2FA (TOTP/WebAuthn) is deferred to T6.1 which owns auth infrastructure
- **Audit log:** Check if `audit_log` table exists in earlier migrations; if so, reuse with added columns rather than creating new table

---

## Task Creation Review Checklist

See [./AI/Workflows/02-planning.md](../AI/Workflows/02-planning.md) for workflow details.

**Review validates:**
- [x] Objective is clear and measurable (with SLAs)
- [x] Success criteria are testable
- [x] Functional requirements are comprehensive
- [x] Trading safety requirements specified (circuit breakers, RBAC)
- [x] Non-functional requirements documented (rate limiting, audit, observability)
- [x] Component breakdown follows 6-step pattern
- [x] Time estimates are reasonable (17-25 days with buffer)
- [x] Dependencies and blockers identified (including prerequisites)
- [x] ADR requirement clear for architectural changes
- [x] Test strategy comprehensive (including fault injection, accessibility, perf)
- [x] Security requirements specified (auth stub guards, API key handling, PII masking)

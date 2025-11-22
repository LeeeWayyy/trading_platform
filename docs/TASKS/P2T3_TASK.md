# P2T3: Web Console - Task Document

**Task ID:** P2T3
**Phase:** P2 (Advanced Features & Live Trading Readiness)
**Track:** Track 2 - Production Readiness
**Priority:** ⭐ HIGH (authentication required before live trading - upgraded from MEDIUM per Gemini/Codex review)
**Estimated Effort:** 9-13 days (REVISED: +2-3 days for OAuth2/OIDC + HTTPS hardening)
**Status:** 📋 Planning
**Created:** 2025-11-17
**Last Reviewed:** 2025-11-17 (Gemini + Codex planning review iteration 1)

---

## Executive Summary

Build a minimal web-based console using Streamlit for operational oversight and manual intervention. Enables non-technical operators to monitor system health, manually submit orders, control strategies, and execute emergency procedures without CLI access.

**Goal:** Operational visibility + emergency controls via web UI

**Key Deliverables:**
1. Dashboard with live positions, P&L, strategy status
2. Manual order entry with confirmation dialogs
3. Strategy enable/disable controls
4. Emergency kill switch integration
5. Audit log viewer for all manual actions
6. Docker containerization for deployment
7. Basic authentication

---

## Context & Dependencies

### Prerequisites (from P1)
✅ **P1T4**: Operational Status Command - provides position/P&L data
✅ **P1T7**: Risk Management System - kill switch + circuit breaker APIs
✅ **P0T4**: Execution Gateway - order submission + position APIs

### Existing Infrastructure Analysis

**Available APIs (from execution_gateway/main.py):**
- `GET /api/v1/positions` - Current positions
- `POST /api/v1/orders` - Submit orders (idempotent)
- `GET /api/v1/orders/{client_order_id}` - Query order status
- `POST /api/v1/kill-switch/engage` - Activate kill switch (from KillSwitchEngageRequest schema)
- `POST /api/v1/kill-switch/disengage` - Deactivate kill switch (from KillSwitchDisengageRequest schema)
- `GET /api/v1/config` - Get current configuration
- `GET /api/v1/pnl/realtime` - Real-time P&L (from RealtimePnLResponse schema)

**Database Tables:**
- `orders` - Order history with status tracking
- `positions` - Current positions (tracked via fills)
- **Missing:** `audit_log` table (need to create in migration)

**Tech Stack Decision:**
- **Framework:** Streamlit (fast MVP, Python-native)
- **Alternative considered:** Next.js + FastAPI backend (more control, but 2-3x effort)
- **Rationale:** Streamlit delivers MVP in 7-10 days vs 14-20 days for React

---

## Pre-Implementation Analysis

### 1. Impacted Components

**Files to CREATE:**
```
apps/web_console/
├── app.py                    # Main Streamlit application
├── __init__.py               # Package marker
├── Dockerfile               # Container for deployment
├── requirements.txt         # Python dependencies (streamlit, requests, etc.)
├── auth.py                  # Basic authentication module
└── config.py                # Configuration (EXECUTION_GATEWAY_URL, etc.)

tests/apps/web_console/
├── __init__.py
├── test_app.py              # Streamlit logic tests
└── test_auth.py             # Authentication tests

db/migrations/
└── 0004_add_audit_log.sql   # Create audit_log table (numbered to avoid conflict with existing migrations)

docs/RUNBOOKS/
└── web-console-user-guide.md  # User documentation with screenshots
```

**Files to MODIFY:**
```
docker-compose.yml           # Add web_console service (port 8501)
apps/execution_gateway/main.py  # Potentially add strategy control endpoints (enable/disable)
```

### 2. ALL Required Functionality

**Component 1: Dashboard**
- Display current positions (symbol, qty, avg_price, current_price, unrealized_pnl)
- Show P&L metrics (total P&L, daily P&L, position count)
- Display strategy status (active/inactive, last signal time)
- Auto-refresh every 10 seconds

**Component 2: Manual Order Entry**
- Form inputs: symbol, side (buy/sell), quantity
- Two-step confirmation: Preview → Confirm with reason
- Audit logging: user, timestamp, reason, order details
- Success/error feedback

**Component 3: Strategy Controls**
- Toggle switches per strategy (enable/disable)
- Requires execution gateway endpoint: `POST /api/v1/strategies/{id}/toggle`
- Audit log all changes

**Component 4: Kill Switch**
- Big red button with confirmation dialog
- Required reason input (audit trail)
- Shows current kill switch status
- Disengage button (with confirmation + reason)

**Component 5: Audit Log Viewer**
- Table view with filtering (date range, action type, user)
- Columns: timestamp, user, action, details, reason
- Pagination for large datasets

**Component 6: Authentication**
- **REVISED (Gemini/Codex CRITICAL finding):** OAuth2/OIDC with existing IdP
- Fallback for dev/paper-only: Mutual TLS + signed JWT tokens
- HTTPS-only deployment (enforced via nginx reverse proxy)
- Session timeout (15 min idle, 4 hour absolute)
- Audit all authentication attempts (success + failure)
- Secret rotation policy (90 days for any temp credentials)
- **Phase gate:** Basic Auth ONLY allowed in isolated dev environment, BLOCKED for staging/production until OAuth2 implemented

### 3. Database Schema Changes

**New Table: audit_log**
```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id TEXT NOT NULL,  -- Username from auth
    action TEXT NOT NULL,   -- 'manual_order', 'kill_switch_engage', 'kill_switch_disengage', 'strategy_toggle'
    details JSONB NOT NULL, -- Action-specific data (order params, strategy name, etc.)
    reason TEXT,            -- User-provided justification
    ip_address TEXT,        -- Client IP for security audit
    session_id TEXT         -- Session tracking
);

CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_log_user ON audit_log(user_id);
CREATE INDEX idx_audit_log_action ON audit_log(action);
```

### 4. Edge Cases & Error Handling

**API Failures:**
- Execution gateway unavailable → Show error banner, disable order submission
- Redis/database unavailable → Fallback to cached data, warn user
- Network timeout → Retry with exponential backoff, show status

**Invalid User Input:**
- Non-existent symbol → Validate against known universe before submission
- Negative quantity → Form validation (min=1)
- Empty reason field → Block confirmation until filled

**Concurrency:**
- Multiple users editing same strategy → Last-write-wins (acceptable for MVP)
- Kill switch race condition → Backend handles (idempotent)

**Security:**
- SQL injection → Use parameterized queries (audit log inserts)
- XSS → Streamlit handles sanitization
- Auth bypass → Enforce session checks on all actions

### 5. Testing Strategy

**Unit Tests (85%+ coverage target):**
- `test_app.py`: Dashboard data fetching, form validation, error handling
- `test_auth.py`: Authentication logic, session management

**Integration Tests:**
- End-to-end: Submit order via UI → Verify in database
- Kill switch: Engage via UI → Verify state in execution gateway
- Audit logging: Perform action → Verify audit log entry

**Manual Tests (Playwright automation):**
- Tablet viewport (768px) responsiveness check
- Form submission flow with network errors
- Multi-user concurrent access

**Performance Tests:**
- Dashboard load time <2s (with 100 positions)
- Form submission response time <500ms

### 6. Acceptance Criteria (from P2_PLANNING.md)

- [ ] Dashboard shows live positions and P&L
- [ ] Manual order entry works with confirmation
- [ ] All manual actions logged to audit table
- [ ] Strategy enable/disable toggles functional
- [ ] Kill switch cancels all orders and flattens positions
- [ ] Kill switch blocks new signals until manual reset
- [ ] Authentication required (basic auth minimum)
- [ ] UI loads and renders correctly on 768px viewport (Playwright test)
- [ ] Docker container builds and serves on port 8501
- [ ] All tests pass with >85% coverage
- [ ] User guide with screenshots

---

## Implementation Plan

### Component Breakdown (6-step pattern per component)

**Component 1: Streamlit Dashboard with Position Display** (1-2 days)
- Display positions table from `/api/v1/positions`
- Fetch and display P&L from `/api/v1/pnl/realtime`
- Auto-refresh with `st.cache_data(ttl=10)`
- Error handling for API unavailability

**Component 2: Manual Order Entry with Confirmation** (1-2 days)
- Form with symbol, side, quantity inputs
- Two-step confirmation flow
- Audit log integration
- Success/error notifications

**Component 3: Strategy Controls & Enable/Disable Toggles** (1 day) - **OPTIONAL for MVP**
- **Prerequisite:** Add `POST /api/v1/strategies/{id}/toggle` to execution gateway (NEW ENDPOINT - dependency risk)
- **Mitigation:** Feature-flagged; can defer if endpoint slips
- Toggle UI with current status
- Audit logging for changes
- **Acceptance:** Component 3 can be deferred to P3 if dependency blocks timeline

**Component 4: Emergency Kill Switch Integration** (1 day)
- Kill switch status display
- Engage button with confirmation + reason
- Disengage button with confirmation + reason
- Audit logging

**Component 5: Audit Log Viewer** (1 day)
- Query audit_log table
- Table display with filtering
- Pagination

**Component 6: Docker Containerization & Deployment** (1 day)
- Create Dockerfile
- Add to docker-compose.yml
- Test container startup
- Document deployment

**Component 7: Authentication & Documentation** (2-3 days - REVISED for OAuth2)
- **Phase 1 (Dev only):** Basic auth for local development
- **Phase 2 (Paper trading):** Mutual TLS + signed JWT tokens
- **Phase 3 (Production):** OAuth2/OIDC integration with IdP
- HTTPS-only deployment with nginx reverse proxy
- Session management (15-min idle timeout, 4-hour absolute)
- Rate limiting on auth endpoints
- Penetration testing before production
- Create user guide with screenshots
- Document security architecture and auth upgrade path

---

## Risks & Mitigations

### Risk 1: Streamlit Performance with Large Datasets
**Impact:** MEDIUM (UI lag with 100+ positions)
**Probability:** LOW
**Mitigation:**
- Use `st.cache_data` aggressively
- Pagination for audit logs
- Benchmark with realistic data

### Risk 2: Auth Bypass Vulnerability (CRITICAL - Gemini/Codex Finding)
**Impact:** CRITICAL (unauthorized kill switch activation, manual trades with replayed credentials)
**Probability:** MEDIUM (Basic Auth exposes credentials, lacks MFA/SSO, vulnerable to traffic capture)
**Mitigation (REVISED):**
- **MANDATORY:** Implement OAuth2/OIDC with existing IdP before production deployment
- **Fallback for paper-trading dev:** Mutual TLS + signed JWT tokens (short-term only)
- HTTPS-only with HSTS headers (prevent downgrade attacks)
- Session checks on every action + 15-min idle timeout
- Audit log all auth attempts (success + failure) with IP + user agent
- Rate limiting on auth endpoint (5 attempts/15 min per IP)
- **Deployment gates:**
  - Basic Auth: Isolated dev environment ONLY
  - mTLS + JWT: Paper trading ONLY
  - OAuth2: Required for staging + production
- Document penetration test results before production enablement

### Risk 3: Strategy Toggle API Dependency (MEDIUM - Codex Finding)
**Impact:** MEDIUM (delays Component 3 if endpoint slips)
**Probability:** MEDIUM (new endpoint not yet implemented)
**Mitigation (REVISED):**
- **Formally track dependency:** Add to risk register with owner + delivery date
- **API contract freeze:** Block console strategy UI work until endpoint contract finalized
- **Fallback plan:** Feature-flag strategy controls - ship dashboard/order/kill-switch first
- **Acceptance criteria update:** Strategy toggle component is OPTIONAL for MVP (can defer to P3)
- Integration tests verify endpoint contract once available

### Risk 4: Execution Gateway Breaking Changes
**Impact:** LOW (breaking changes in existing APIs)
**Probability:** LOW (stable P1 APIs)
**Mitigation:**
- Pin API versions in integration tests
- Contract tests catch breaking changes

---

## Success Metrics

**Functional:**
- All 6 components pass acceptance criteria
- No manual action possible without audit trail
- Kill switch prevents new orders (verified in integration test)

**Performance:**
- Dashboard load <2s
- Form submission <500ms
- Auto-refresh every 10s without lag

**Quality:**
- >85% test coverage
- All Playwright tests pass
- Docker container starts successfully

---

## Open Questions & Decisions

### Q1: Should we add position flattening UI (sell all)?
**Decision:** DEFER to P3. Kill switch already handles emergency flatten. Manual flatten is nice-to-have.

### Q2: OAuth2 vs Basic Auth? (REVISED - Gemini/Codex CRITICAL)
**Decision:** OAuth2 REQUIRED for production (adds 2-3 days to implementation).
- **Development phase:** Can prototype with Basic Auth in isolated dev environment
- **Paper trading:** Requires mTLS + JWT minimum
- **Staging/Production:** OAuth2 mandatory (not negotiable)
- **Rationale:** Kill switch + manual trading authority cannot use weak auth; replay attacks unacceptable
- **Timeline impact:** Original 7-10 days becomes 9-13 days (OAuth2 + HTTPS setup)

### Q3: Real-time WebSocket updates vs polling?
**Decision:** Polling (10s refresh) for MVP. WebSocket adds complexity (2-3 days). Document as P3 enhancement.

### Q4: Do we need strategy-specific parameters UI?
**Decision:** NO for MVP. Enable/disable toggle is sufficient. Parameter tuning requires deep domain knowledge (CLI appropriate).

---

## Next Steps

1. ✅ Complete this task document
2. ⏳ Request task creation review (zen-mcp planner)
3. ⏳ Create branch: `feature/P2T3-web-console`
4. ⏳ Record planning artifacts:
   - `./scripts/workflow_gate.py record-analysis-complete`
   - `./scripts/workflow_gate.py set-components "Dashboard" "Manual Order Entry" "Strategy Controls" "Kill Switch" "Audit Log" "Docker & Auth"`
5. ⏳ Start Component 1 (plan → plan-review → implement → test → review → commit)

---

## References

- P2_PLANNING.md: Lines 505-630 (T3 specification)
- ./AI/Workflows/12-component-cycle.md: 6-step pattern
- ./AI/Workflows/00-analysis-checklist.md: Analysis template
- apps/execution_gateway/main.py: Existing APIs
- apps/execution_gateway/schemas.py: Request/response models

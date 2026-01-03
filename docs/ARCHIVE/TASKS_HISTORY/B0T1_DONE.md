---
id: B0T1
title: "Codebase Issues Remediation"
phase: B0
task: T1
priority: P0
owner: "@development-team"
state: DONE
created: 2025-12-20
dependencies: []
estimated_effort: "~30 days"
related_adrs: ["ADR-0021-risk-model-implementation"]
related_docs: ["P4T5_DONE.md"]
features: ["C1-startup-reconciliation", "C2-blocking-io", "C3-kill-switch", "C4-security-headers", "C5-rate-limiting", "C6-api-auth", "C7-secrets", "C8-polling-latency", "C9-zombie-recovery", "C10-redis-fallback", "C11-structured-logging", "C12-feature-freshness", "C13-broker-abstraction", "C14-dependency-injection", "C15-trading-calendar", "C16-db-schema", "C17-medium-issues"]
---

# B0T1: Codebase Issues Remediation

**Phase:** B0 (Bug Fix Track)
**Status:** ✅ Complete (C5-C7 Complete)
**Priority:** P0 (Critical)
**Owner:** @development-team
**Created:** 2025-12-20
**Estimated Effort:** ~30 days (see Implementation Order for breakdown)

---

## Executive Summary

This task addresses 22 validated codebase issues identified through comprehensive analysis and independently reviewed by Codex and Gemini. Three issues from the original list were found to be already mitigated or non-issues. A second review identified 9 additional issues related to security, observability, and data quality. The remediation is organized into 3 phases based on severity and trading safety impact.

### Issues Summary - Original Analysis

| # | Issue | Status | Severity | Validated By |
|---|-------|--------|----------|--------------|
| 1 | Blocking I/O in Async Contexts | **VALID** | CRITICAL | Codex, Gemini |
| 2 | Global State Singletons | **VALID** | MEDIUM | Codex, Gemini |
| 3 | Error Swallowing Startup Reconciliation | **VALID** | CRITICAL | Codex, Gemini |
| 4 | Hard Redis Dependency | **PARTIAL** | MEDIUM | Codex |
| 5 | Roll Your Own Crypto | **INVALID** | N/A | Properly implemented |
| 6 | Fragile Cookie Parsing | **VALID** | MEDIUM | Codex |
| 7 | Critical Controls on Streamlit Frontend | **VALID** | CRITICAL | Codex, Gemini |
| 8 | Calendar vs Trading Days | **VALID** | MEDIUM | Gemini |
| 9 | Unsafe Model Serialization (pickle) | **PARTIAL** | MEDIUM | Codex |
| 10 | Local-First Storage Limitations | **VALID** | HIGH | Analysis |
| 11 | Blocking Bulk Data Fetches | **INVALID** | N/A | Already uses MGET |
| 12 | Fail-Open Fat Finger Logic | **INVALID** | N/A | Already fail-closed |
| 13 | Underestimation of Tail Risk | **VALID** | MEDIUM | Analysis |
| 14 | Polling Latency in Reconciliation | **VALID** | HIGH | Codex, Gemini |
| 15 | Single Broker Dependency | **VALID** | HIGH | Codex, Gemini |
| 16 | Zombie Slices on Crash | **VALID** | MEDIUM | Gemini |

### Issues Summary - Second Review

| # | Issue | Status | Severity | Category |
|---|-------|--------|----------|----------|
| 17 | No CSP/HTTP Security Headers | **VALID** | CRITICAL | Web Security |
| 18 | Rate Limiting Missing on Order Submission | **PARTIAL** | CRITICAL | Web Security |
| 19 | Auth Hooks Missing on Trading APIs | **PARTIAL** | HIGH | Web Security |
| 20 | Structured Logging Not Used | **PARTIAL** | HIGH | Observability |
| 21 | Secrets from Environment (No Abstraction) | **VALID** | CRITICAL | Security |
| 22 | No Feature Freshness Guards at Inference | **VALID** | HIGH | Data Quality |
| 23 | No Leakage Detection in Production | **VALID** | HIGH | Data Quality |
| 24 | Status TEXT without ENUM Constraints | **PARTIAL** | MEDIUM | Database |
| 25 | No Slippage Modeling for TWAP | **VALID** | MEDIUM | Execution |
| 26 | Position current_price Allows NULL | **VALID** | MEDIUM | Database |

---

## Objective

Address all validated codebase issues to improve trading system stability, safety, and maintainability.

**Success looks like:**
- Trading engine fails fast on startup reconciliation failure (no zombie state)
- Async endpoints no longer block event loop on broker/DB calls
- Emergency controls accessible via CLI/API independent of Streamlit
- Reconciliation latency reduced from 5 minutes to <60 seconds
- Broker abstraction layer enables future failover capability

---

## Acceptance Criteria

### Phase 1: Critical Safety & Security
- [ ] **AC1:** Startup reconciliation failure blocks order routes and trips circuit breaker
- [ ] **AC2:** All blocking I/O calls wrapped with `asyncio.to_thread()` or replaced with async clients
- [ ] **AC3:** Kill switch accessible via CLI with mTLS/JWT auth, audit logging, and documented DR drill
- [ ] **AC4:** Security headers (CSP, X-Frame-Options, HSTS) added to all FastAPI apps
- [x] **AC5:** Rate limiting applied to order submission endpoints with per-user buckets
- [x] **AC6:** S2S authentication required on trading APIs (order submission, signal generation)
- [x] **AC7:** Secrets abstraction layer with validation and rotation hooks deployed

### Phase 2: Reliability & Observability
- [ ] **AC8:** Reconciliation interval configurable, default reduced to 60 seconds
- [ ] **AC9:** Zombie slice recovery handles all edge cases (scheduler state loss, DB unavailability)
- [ ] **AC10:** Redis fallback provides clear degraded-mode behavior
- [ ] **AC11:** Structured JSON logging with trace IDs enabled across all services
- [ ] **AC12:** Feature freshness validated at inference time in signal service

### Phase 3: Architecture & Data Quality
- [ ] **AC13:** Broker abstraction protocol enables multi-broker support (separate epic with ADR)
- [ ] **AC14:** Global state refactored to FastAPI dependency injection
- [ ] **AC15:** Trading day calendar used for performance calculations and backtest windows
- [ ] **AC16:** Database status columns use CHECK constraints with zero-downtime migration

---

## Phase 1: Critical Safety & Security (Days 1-10)

### Component C1: Startup Reconciliation Fail-Fast

**Issue 3 - Error Swallowing During Startup**

**Effort Estimate:** 1.5 days

**Problem:**
- `run_startup_reconciliation()` returns `False` on failure (reconciliation.py:126-128)
- Startup handler ignores return value (main.py:4234)
- Trading engine starts with potentially inconsistent broker state

**Evidence:**
```python
# reconciliation.py:118-128
async def run_startup_reconciliation(self) -> bool:
    try:
        await self.run_reconciliation_once("startup")
        return True
    except Exception as exc:
        logger.error("Startup reconciliation failed", exc_info=True)
        return False  # RETURNS FALSE, NOT RAISED

# main.py:4234
await reconciliation_service.run_startup_reconciliation()  # RETURN VALUE IGNORED
```

**Solution:**
1. Check return value of `run_startup_reconciliation()`
2. If `False`: trip circuit breaker AND set startup_failed flag
3. Block order routes until reconciliation succeeds
4. Expose health check endpoint that reflects reconciliation status

**Files to Modify:**
- `apps/execution_gateway/main.py` - Check return value, fail-fast logic
- `apps/execution_gateway/reconciliation.py` - Add retry with backoff option
- `libs/risk_management/breaker.py` - Add startup-failure trip reason

**Test Cases:**
- Test reconciliation failure blocks order submission
- Test circuit breaker trips on reconciliation failure
- Test health endpoint returns unhealthy on reconciliation failure
- Test successful retry after transient failure

---

### Component C2: Blocking I/O Remediation

**Issue 1 - Blocking I/O in Async Contexts**

**Effort Estimate:** 2 days

**Problem:**
- FastAPI endpoints are `async def` but call synchronous drivers
- `alpaca_client.submit_order()` blocks event loop (main.py:2717)
- `db_client.*` calls block event loop (main.py:2655, 2731)
- Redis client is synchronous (libs/redis_client/client.py)

**Evidence:**
```python
# main.py:2717 - BLOCKS EVENT LOOP
alpaca_response = alpaca_client.submit_order(order, client_order_id)

# main.py:2655 - BLOCKS EVENT LOOP
db_client.create_order(...)

# Some calls ARE wrapped correctly (main.py:1036-1040)
position = await asyncio.to_thread(alpaca_client.get_open_position, order.symbol)
```

**Solution:**
1. **Immediate:** Wrap all blocking calls with `asyncio.to_thread()`
2. **Long-term:** Consider async drivers (psycopg[async], redis.asyncio, httpx)

**Priority Wrapping Locations:**
| File | Line | Call | Priority |
|------|------|------|----------|
| main.py | 2717 | `alpaca_client.submit_order()` | P0 |
| main.py | 2655 | `db_client.create_order()` | P0 |
| main.py | 2731 | `db_client.update_order_status()` | P0 |
| main.py | 2810-2860 | `alpaca_client.cancel_order()` | P0 |
| reconciliation.py | * | Already wrapped in `_run_reconciliation()` | OK |

**Files to Modify:**
- `apps/execution_gateway/main.py` - Wrap blocking calls
- Create helper: `libs/common/async_utils.py` (if not exists)

**Threadpool Considerations:**
- `asyncio.to_thread()` uses default ThreadPoolExecutor
- Risk: Threadpool exhaustion under high load, DB connection reuse issues
- **Explicit Config:**
  - `max_workers = min(32, os.cpu_count() + 4)` (Python default)
  - For trading: Start with `max_workers = 16`, tune based on load test
  - Configurable via `EXECUTOR_MAX_WORKERS` env var
- **Saturation Metrics:**
  - `executor_queue_depth` gauge: Current pending tasks
  - `executor_active_threads` gauge: Currently executing tasks
  - Alert: `executor_queue_depth > max_workers * 2` for 30s
- **Rollback Toggle:**
  - Feature flag `USE_ASYNC_WRAPPERS=true` (default)
  - Set to `false` to revert to synchronous calls if issues arise

**Thread-Safe DB Access Policy:**
- **Critical:** psycopg connections are NOT thread-safe for concurrent use
- **Policy:** Each thread in executor gets its own connection from pool
- **Implementation Options:**
  1. Use connection pool with `min_size = max_workers` (recommended)
  2. Use `contextvar` to store per-thread connection
- **Gate:** Load test must verify:
  - No "connection already in use" errors
  - Pool saturation stays below 80%
  - No connection errors under 100 concurrent requests

**Test Cases:**
- Load test: verify no event loop blocking under concurrent requests
- Test health check responds during slow broker call
- Test webhook processing not blocked during order submission
- Test threadpool doesn't exhaust under 100 concurrent requests

**Event-Loop Observability:**
- **Lag Metric:** `asyncio_event_loop_lag_seconds` histogram
- **Alert:** Event loop lag > 100ms for 30s triggers warning
- **Implementation:** Use `asyncio.get_event_loop().time()` delta or uvloop metrics

**Rollout Gate:**
- [ ] Load test passed with 100+ concurrent requests
- [ ] p99 latency within SLO (<200ms for order submission)
- [ ] Threadpool queue depth stays below 50% capacity
- [ ] DB connection pool not exhausted during load test
- [ ] Event-loop lag stays < 50ms under load
- [ ] Rollback toggle tested (USE_ASYNC_WRAPPERS=false works)

**Follow-Up Ticket (Post-Phase 1):**
Create B0T2 to migrate to native async drivers:
- `psycopg[async]` for PostgreSQL
- `redis.asyncio` for Redis
- `httpx` for HTTP clients
This provides proper async without threadpool overhead.

---

### Component C3: Redundant Kill Switch

**Issue 7 - Critical Controls on Heavy Frontend**

**Effort Estimate:** 2 days

**Problem:**
- Kill switch only accessible via Streamlit UI (manual_controls.py:507-552)
- If Streamlit crashes, operators cannot halt trading
- No CLI/API alternative for emergency operations

**Evidence:**
```python
# apps/web_console/pages/manual_controls.py:535 - UI-ONLY
if st.button("Flatten All", type="primary"):
    flatten_all_positions(user, reason.strip(), id_token)

# apps/execution_gateway/main.py:348-380 - Kill switch API exists but needs auth hardening
@app.post("/api/v1/kill-switch/engage")
```

**Solution:**
1. Create CLI script `scripts/emergency_halt.sh` that calls kill-switch API directly
2. Add mTLS or JWT authentication to kill-switch endpoints
3. Add tamper-evident audit logging for all kill-switch operations
4. Document emergency procedures and quarterly DR drill schedule
5. Implement limited blast-radius (per-strategy kill vs global)

**Security Hardening:**
- **Authentication:** mTLS client certificates (primary - works offline if IdP is down)
  - Pre-provisioned operator certificates stored securely on ops machines
  - JWT fallback for web console (secondary, requires IdP)
- **Offline/Failure Mode:** mTLS certs work even during IdP outage
  - Test runbook: "Kill switch with IdP down" scenario verified quarterly
- **Dual-Control for Disengage:**
  - Disengage requires second operator confirmation OR 2FA token
  - Prevents accidental re-enable after emergency halt
  - Single operator can engage (emergency), but two required to resume
- **Partial-Engage Failure Handling:**
  - If engage succeeds but audit sink is down: proceed (safety first)
  - Log locally to disk as fallback, reconcile when sink recovers
  - Alert on audit sink failure during kill switch operations
- **Audit Logging:** Immutable log entries with timestamp, operator ID, action, reason
- **Rate Limiting:** Max 1 engage/disengage per 60 seconds to prevent flapping
- **Confirmation:** Require reason field (min 10 chars) for audit trail

**Files to Create:**
- `scripts/emergency_halt.sh` - CLI kill switch with auth
- `scripts/emergency_disengage.sh` - CLI disengage (with safety checks)

**Files to Modify:**
- `apps/execution_gateway/main.py` - Add mTLS/JWT auth to kill-switch endpoints
- `docs/RUNBOOKS/ops.md` - Document CLI procedures and quarterly DR drill

**DR Drill Requirements:**
- Quarterly execution of kill-switch drill in staging environment
- Document drill results and any issues found
- Update runbook based on drill learnings

**mTLS Certificate Management:**
- **Provisioning:**
  - Generate operator certificates using internal CA
  - Secure storage: macOS Keychain or encrypted file with passphrase
  - Distribute to designated operators (3-5 operators per environment)
- **Rotation Cadence:**
  - Certificates valid for 1 year
  - Rotation 30 days before expiry with automated reminder
  - Emergency rotation playbook for compromised certs
- **Revocation:**
  - Maintain CRL (Certificate Revocation List) or OCSP responder
  - Immediate revocation process for terminated operators
  - Runbook: "Revoke operator kill-switch access" with verification step

**Test Cases:**
- Test CLI script successfully engages kill switch
- Test unauthenticated requests rejected with 401
- Test audit log entries created for all operations
- Test CLI works when Streamlit is down
- Test rate limiting prevents rapid flapping

**Rollout Gate:**
- [ ] DR drill completed in staging
- [ ] Runbook reviewed by ops team
- [ ] "Kill switch with IdP down" scenario tested successfully
- [ ] mTLS certificates provisioned for all designated operators
- [ ] Dual-control disengage tested in staging with audit logs verified
- [ ] Certificate revocation process tested

---

### Component C4: HTTP Security Headers

**Issue 17 - No CSP/HTTP Security Headers**

**Effort Estimate:** 1 day

**Problem:**
- FastAPI apps only have CORS middleware, no security headers
- No Content-Security-Policy, X-Frame-Options, HSTS
- Browsers have no XSS/clickjacking protection

**Evidence:**
```python
# signal_service/main.py:689-695 - ONLY CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# NO security headers middleware
```

**Solution:**
1. Create security headers middleware for FastAPI
2. Add CSP policy (script-src, style-src, frame-ancestors)
3. Add X-Frame-Options: DENY
4. Add X-Content-Type-Options: nosniff
5. Add Strict-Transport-Security for HTTPS

**Files to Create:**
- `libs/common/security_middleware.py` - Security headers middleware

**Files to Modify:**
- `apps/execution_gateway/main.py` - Add security middleware
- `apps/signal_service/main.py` - Add security middleware
- `apps/web_console/main.py` - Add security middleware (if applicable)

**CSP Rollout Strategy (Report-Only First):**
1. **Phase A:** Deploy with `Content-Security-Policy-Report-Only` header
2. **Phase B:** Collect violation reports for 24h via `/csp-report` endpoint
3. **Phase C:** Allowlist existing inline assets or migrate them to external files
4. **Phase D:** Switch to enforcing `Content-Security-Policy` header
5. **Rollback:** Revert to report-only if violations spike

**CSP Report Storage & Alerting:**
- **Sink:** Log to CloudWatch/Datadog with `csp_violation` metric
- **Privacy:** Scrub PII from `blocked-uri` and `document-uri` fields
- **Retention:** 30 days for debugging, aggregate metrics retained longer
- **Alert Threshold:** > 10 violations/minute triggers PagerDuty warning
- **Dashboard:** Grafana panel for violation rate by directive type

**Test Cases:**
- Test all security headers present in responses
- Test CSP violations logged to `/csp-report` endpoint
- Test X-Frame-Options prevents framing
- Test existing web console functionality works with CSP

**Rollout Gate:**
- [ ] CSP report-only deployed for 24h with zero critical violations
- [ ] All inline scripts migrated or allowlisted
- [ ] Web console functionality verified with enforcing CSP
- [ ] Violation rate < 0.1% of requests after enforcement

---

### Component C5: Rate Limiting on Order Submission

**Issue 18 - Rate Limiting Missing on Order Submission**

**Effort Estimate:** 1 day

**Problem:**
- Manual controls have rate limiting (cancel_order, flatten_all)
- Order submission endpoint (`POST /api/v1/orders`) has NO rate limiting
- Signal generation endpoint has NO rate limiting
- High-traffic abuse could bypass throttling

**Evidence:**
```python
# manual_controls.py - HAS rate limiting
RATE_LIMITS = {"cancel_order": (10, 60), "flatten_all": (1, 300)}

# main.py:2181 - NO rate limiting
@app.post("/api/v1/orders", response_model=OrderResponse)
async def submit_order(...):  # NO rate limit decorator
```

**Solution:**
1. Apply shared rate limiter to order submission endpoint
2. Add rate limiting to signal generation endpoint
3. Configure per-user and per-strategy buckets
4. Emit metrics for blocked attempts

**Files to Modify:**
- `apps/execution_gateway/main.py` - Add rate limiting to order endpoints
- `apps/signal_service/main.py` - Add rate limiting to signal endpoints
- `libs/web_console_auth/rate_limiter.py` - Extend for API use

**Test Cases:**
- Test rate limit triggers after threshold
- Test per-user rate limiting works
- Test metrics emitted for blocked requests

**Rollout Gate:**
- [ ] Rate limits tested with production traffic patterns
- [ ] Legitimate trading flow not blocked at configured thresholds
- [ ] Success metrics defined: 429 rate < 0.1% of legitimate traffic
- [ ] Rollback trigger: 429 rate > 1% or latency p99 > 500ms

**Staged Deploy:**
1. Deploy with logging-only mode (no blocking)
2. Monitor for 24h to baseline traffic patterns
3. Enable blocking with conservative thresholds
4. Tighten thresholds incrementally

---

### Component C6: API Authentication

**Issue 19 - Auth Hooks Missing on Trading APIs**

**Effort Estimate:** 1.5 days

**Problem:**
- Order submission endpoint has NO authentication
- Signal generation endpoint is public (no auth required)
- Any client can submit orders or generate signals
- S2S authentication not enforced

**Evidence:**
```python
# apps/execution_gateway/main.py:2181 - NO auth dependency
@app.post("/api/v1/orders", response_model=OrderResponse)
async def submit_order(order: OrderRequest):  # NO Depends(get_authenticated_user)

# apps/signal_service/main.py:1275 - NO auth
@app.post("/api/v1/signals/generate")
async def generate_signals(...):  # NO authentication
```

**Solution:**
1. Add JWT/Bearer token authentication to order submission
2. Add S2S authentication to signal generation
3. Use existing `GatewayAuthenticator` from dependencies.py
4. Document auth flow for orchestrator and web console

**Files to Modify:**
- `apps/execution_gateway/main.py` - Add auth dependency to order endpoints
- `apps/signal_service/main.py` - Add auth dependency to signal endpoints
- `apps/execution_gateway/api/dependencies.py` - Ensure GatewayAuthenticator works for APIs

**Test Cases:**
- Test unauthenticated requests rejected with 401
- Test valid JWT allows order submission
- Test invalid JWT rejected
- Test S2S token works for internal services

**Rollout Gate:**
- [ ] Coordinate with orchestrator team on auth flow
- [ ] Update all S2S clients to include tokens
- [ ] Health check endpoints excluded from auth (bypass list)
- [ ] Success metrics: 401 rate < 0.01% of legitimate traffic
- [ ] Rollback trigger: 401 rate > 0.5% or S2S latency p99 > 200ms

**Staged Deploy:**
1. Deploy with dual-mode: accept both authenticated and unauthenticated
2. Monitor and log unauthenticated requests for 48h
3. Alert on high unauthenticated rate (indicates missed client updates)
4. Switch to auth-required after all clients updated
5. Token distribution plan documented in ops runbook

---

### Component C7: Secrets Abstraction

**Issue 21 - Secrets from Environment (No Abstraction)**

**Effort Estimate:** 1.5 days

**Problem:**
- API keys and credentials loaded directly via `os.getenv()`
- No centralized secrets management
- No rotation support or encrypted storage
- Credentials exposed as plaintext in environment

**Evidence:**
```python
# apps/execution_gateway/main.py:175-287
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# apps/execution_gateway/api/dependencies.py:55-72
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
```

**Solution:**
1. Create secrets interface abstraction (`libs/common/secrets.py`)
2. Implement env var loader with validation (fail-closed on missing required secrets)
3. Add hooks for rotation (e.g., Vault, AWS Secrets Manager integration)
4. Centralize all credential loading through the abstraction
5. Add explicit empty-value checks with clear error messages

**Storage Decision:**
- **Phase 1 (Current):** Environment variables with validation layer
- **Phase 2 (Future):** AWS Secrets Manager or HashiCorp Vault integration
- Interface designed to support either backend without code changes

**Migration Steps:**
1. Create secrets abstraction layer with env var backend
2. Update each service one-by-one (execution_gateway first)
3. Run dry-run checklist per environment:
   - [ ] All required secrets present
   - [ ] No empty values for required secrets
   - [ ] Service starts successfully in DRY_RUN mode
4. Deploy to staging, validate 24h
5. Deploy to production with canary

**Rotation Strategy:**
- Secrets interface supports `refresh()` method for rotation
- Initial implementation: restart service to pick up new env vars
- Future: Integrate with secret manager's rotation callbacks

**Fallback/Rollback Strategy:**
- **Default Mode:** `SECRETS_VALIDATION_MODE=strict` (fail-closed)
- If secrets validation blocks startup:
  1. Check logs for specific missing/invalid secret
  2. **EMERGENCY ONLY:** Set `SECRETS_VALIDATION_MODE=warn` to start with warnings
     - Requires explicit executive/on-call approval
     - Document approval in incident ticket
     - Max 24h before reverting to strict
  3. Fix secret values and restart with strict mode
- Rollback: Revert to direct `os.getenv()` calls if abstraction causes issues

**Environment Completeness Checklist (MANDATORY before prod):**
Per-environment verification before production rollout:
- [ ] All required secrets defined in secrets manifest
- [ ] Each secret has valid, non-empty value
- [ ] Dry-run startup successful in dev, staging
- [ ] Rotation test passed (refresh without restart)
- [ ] Rollback procedure documented and tested

**Managed Backend Timeline:**
- **B0T1 (Current):** Env var backend with validation layer
- **B0T2 (Future, 60-90 days):** AWS Secrets Manager or HashiCorp Vault integration
- **Owner:** Platform team to evaluate and implement
- This ensures abstraction doesn't ossify on env vars

**Kill-Switch Credential Integration:**
- **Scope:** Secrets abstraction MUST cover operator mTLS certs and JWT signing keys
- **Operator Certs:** Include in secrets manifest with:
  - `KILLSWITCH_MTLS_CERT_PATH` - Path to operator certificate
  - `KILLSWITCH_MTLS_KEY_PATH` - Path to operator private key
  - `KILLSWITCH_CA_CERT_PATH` - Path to CA certificate for validation
- **JWT Keys:** Include JWT signing/verification keys if using JWT fallback
- **Offline Storage:** Document secure offline storage for DR scenarios
  - Encrypted USB with operator certs for emergency access
  - Recovery procedure in ops runbook
- **Rotation:** Secrets refresh must support cert rotation without service restart

**Files to Create:**
- `libs/common/secrets.py` - Secrets abstraction layer with validation

**Files to Modify:**
- `apps/execution_gateway/main.py` - Use secrets abstraction
- `apps/signal_service/main.py` - Use secrets abstraction
- `apps/execution_gateway/api/dependencies.py` - Use secrets abstraction

**Test Cases:**
- Test missing required secret fails startup with clear error
- Test empty secret value fails validation
- Test rotation hook can be invoked
- Test fallback to env vars works when no secret manager configured
- Test dry-run mode with valid secrets

**Rollout Gate:**
- [ ] All required secrets documented in ops runbook
- [ ] Dry-run checklist passed in dev, staging, prod
- [ ] Validation passes in all environments
- [ ] Rotation test completed (manual secret refresh)

---

## Phase 2: Reliability & Observability (Days 11-16)

### Component C8: Reduce Polling Latency

**Issue 14 - Polling Latency in Reconciliation**

**Effort Estimate:** 1 day

**Problem:**
- Default reconciliation interval is 300 seconds (5 minutes)
- If webhook fails, order state healing takes up to 5 minutes
- Too slow for production trading

**Evidence:**
```python
# apps/execution_gateway/reconciliation.py:81
RECONCILIATION_INTERVAL_SECONDS = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300"))
```

**Solution:**
1. Reduce default interval to 60 seconds
2. Add configurable jitter to prevent thundering herd
3. Consider WebSocket stream for real-time updates (future enhancement)
4. Add metrics for reconciliation latency

**Files to Modify:**
- `apps/execution_gateway/reconciliation.py` - Reduce default, add jitter
- `infra/prometheus/alerts.yml` - Add reconciliation latency alert

**Test Cases:**
- Test reconciliation runs every 60 seconds
- Test jitter prevents exact timing
- Test metrics exposed correctly

**Rollout Gate:**
- [ ] Monitor Redis/DB load after interval reduction in staging
- [ ] Canary deployment with 10% traffic first

---

### Component C9: Zombie Slice Recovery Hardening

**Issue 16 - Zombie Slices on Crash**

**Effort Estimate:** 1.5 days

**Problem:**
- Recovery depends on application restart
- No external watchdog for unmanaged orders
- Edge cases: DB unavailable during recovery, scheduler state lost

**Current Mitigation:**
- `recover_zombie_slices()` exists (slice_scheduler.py:325-662)
- Runs after reconciliation gate opens (main.py:4239)

**Solution:**
1. Add graceful shutdown hook to cancel pending slices (handle SIGTERM/SIGINT explicitly)
2. Improve recovery edge case handling
3. Add monitoring for orphaned slices
4. Document "Dead Man's Switch" pattern for future implementation

**Files to Modify:**
- `apps/execution_gateway/slice_scheduler.py` - Graceful shutdown, edge cases
- `apps/execution_gateway/main.py` - Shutdown hook with signal handlers

**Test Cases:**
- Test graceful shutdown cancels pending slices
- Test recovery handles DB unavailability
- Test recovery handles scheduler state loss
- Test SIGTERM triggers graceful shutdown

---

### Component C10: Redis Fallback Mode

**Issue 4 - Hard Redis Dependency (Partial)**

**Effort Estimate:** 1 day

**Current State:**
- Redis optional at startup (main.py:311-326)
- Circuit breaker fails-closed if Redis state missing (breaker.py:239)
- Some features silently degrade (quarantine, exposure tracking)

**Solution:**
1. Provide clear degraded-mode behavior documentation
2. Add explicit health status for Redis connectivity
3. Consider local memory fallback for circuit breaker (safe mode = reject all)

**Files to Modify:**
- `libs/risk_management/breaker.py` - Add fallback behavior
- `apps/execution_gateway/main.py` - Add Redis health status

**Test Cases:**
- Test circuit breaker behavior when Redis unavailable
- Test health endpoint reflects Redis status
- Test fallback mode rejects new orders

---

### Component C11: Structured Logging

**Issue 20 - Structured Logging Not Used**

**Effort Estimate:** 1 day

**Problem:**
- Services use `logging.basicConfig()` with plain text format
- Structured logging utilities exist in `libs/common/logging/` but NOT used
- No consistent trace IDs, request IDs, or strategy_id context

**Evidence:**
```python
# apps/signal_service/main.py:78-81 - PLAIN TEXT
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# libs/common/logging/formatter.py - EXISTS but NOT USED
class JSONFormatter:  # Produces JSON with trace_id, service, context
```

**Solution:**
1. Replace `basicConfig()` with `configure_logging()` from libs/common/logging
2. Enable JSONFormatter for production
3. Wire trace ID propagation via contextvars
4. Add mandatory fields: trace_id, request_id, strategy_id, client_order_id

**Schema Versioning:**
- Add `log_schema_version: "1.0"` field to all log entries
- **v1.0 Fields (Backward Compatible):**
  - `timestamp`, `level`, `message`, `service`, `trace_id`
  - `request_id`, `strategy_id`, `client_order_id` (optional context)
- Schema changes require version bump and dashboard update
- Log aggregator must parse both old and new schema during rollout

**Files to Modify:**
- `apps/execution_gateway/main.py` - Use configure_logging()
- `apps/signal_service/main.py` - Use configure_logging()
- `libs/common/logging/config.py` - Ensure easy integration

**Test Cases:**
- Test logs are JSON formatted in production mode
- Test trace_id propagates across requests (end-to-end)
- Test mandatory fields present in log output
- Test trace_id propagates across service boundaries (signal → execution gateway)
- Test log_schema_version field present in all entries

**Rollout Gate:**
- [ ] Log aggregator (e.g., CloudWatch, Datadog) successfully parses JSON logs
- [ ] Dashboards updated for new structured format
- [ ] Trace-id propagation verified across services
- [ ] Old log format still parseable during rollout window
- [ ] Log parse error rate < 1% after deploy

---

### Component C12: Feature Freshness Guards

**Issue 22 - No Feature Freshness Guards at Inference**

**Effort Estimate:** 1 day

**Problem:**
- Signal generator fetches features without timestamp validation
- Features could be stale (old data) without detection
- No freshness threshold enforcement before scoring

**Evidence:**
```python
# apps/signal_service/signal_generator.py:334-420
# Features fetched from T1 data provider
# NO validation of feature age or recency before scoring
```

**Solution:**
1. Add feature timestamp validation before scoring
2. Configure freshness threshold (e.g., reject if >30 minutes old)
3. Emit metrics for rejected stale payloads
4. Log warnings for near-threshold freshness

**Files to Modify:**
- `apps/signal_service/signal_generator.py` - Add freshness validation
- `apps/signal_service/main.py` - Add freshness config

**Alerting & SLO:**
- **Freshness Rejection Rate Alert:** > 1% rejections in 5min window → PagerDuty
- **Scoring Latency SLO:** p99 < 100ms (freshness check adds ~5ms)
- **Dashboard:** Grafana panel for rejection rate, freshness distribution
- Metric: `signal_feature_freshness_rejection_total{reason="stale|missing"}`

**Test Cases:**
- Test stale features rejected with clear error
- Test fresh features processed normally
- Test metrics emitted for freshness violations
- Test scoring latency within SLO with freshness check enabled

**Rollout Gate:**
- [ ] Freshness threshold tuned to avoid false rejections (baseline from prod logs)
- [ ] Alert configured for elevated rejection rate
- [ ] Dashboard created with freshness metrics
- [ ] Scoring latency validated within SLO

---

## Phase 3: Architecture & Data Quality (Days 17-28)

### Component C13: Broker Abstraction Layer (Separate Epic)

**Issue 15 - Single Broker Dependency**

**Effort Estimate:** 4 days (conservative, given codebase blast radius)

**Problem:**
- Tight coupling to Alpaca throughout codebase
- No abstraction layer for broker operations
- Cannot switch brokers or add failover

**Solution:**
1. Create `ExecutionAdapter` Protocol/ABC
2. Refactor `AlpacaExecutor` to implement protocol
3. Update all call sites to use protocol type hints
4. Prepare for future second broker implementation

**Files to Create:**
- `apps/execution_gateway/protocols.py` - ExecutionAdapter protocol

**Files to Modify:**
- `apps/execution_gateway/alpaca_client.py` - Implement protocol
- `apps/execution_gateway/main.py` - Use protocol type hints
- `apps/execution_gateway/reconciliation.py` - Use protocol type hints

**Rollout Gate:**
- [ ] Integration tests pass with mock broker
- [ ] Production smoke test with Alpaca (existing behavior unchanged)

---

### Component C14: Dependency Injection Refactoring

**Issue 2 - Global State Singletons**

**Effort Estimate:** 3 days (conservative, affects many modules)

**Problem:**
- Redis, DB, Alpaca clients are global variables
- Makes testing difficult (requires monkeypatching)
- No clean separation of initialization and request handling

**Solution:**
1. Use FastAPI Lifespan for client initialization
2. Create dependency container or use `Depends()`
3. Inject clients into route handlers

**Files to Modify:**
- `apps/execution_gateway/main.py` - Refactor to lifespan pattern
- `apps/execution_gateway/dependencies.py` (create) - Dependency providers

**Rollout Gate:**
- [ ] All existing tests pass with new DI pattern
- [ ] Memory usage stable during load test

---

### Component C15: Trading Calendar Integration

**Issue 8 - Calendar vs Trading Days**

**Effort Estimate:** 1.5 days

**Problem:**
- Walk-forward optimizer validates with calendar days, not trading days
- Performance calculations iterate with `timedelta(days=1)` including weekends

**Solution:**
1. Integrate `pandas_market_calendars` or equivalent
2. Update walk-forward validation to use trading days
3. Update performance calculations to skip non-trading days

**Files to Modify:**
- `libs/backtest/walk_forward.py` - Use trading day calendar
- `apps/execution_gateway/main.py` - Performance calculations

**Rollout Gate:**
- [ ] Backtest results validated against historical trading days
- [ ] Holiday edge cases tested (half days, early closes)

---

### Component C16: Database Schema Hardening

**Issues 24, 26 - Status TEXT and NULL current_price**

**Effort Estimate:** 2 days

**Problem:**
- Orders status column uses TEXT without CHECK constraint
- Position current_price allows NULL, delaying P&L visibility

**Full Status Vocabulary (Verify Before Migration):**
- `pending` - Order created, not yet submitted to broker
- `submitted` - Order submitted to broker, awaiting fill
- `partial_fill` - Order partially filled (if applicable)
- `filled` - Order fully executed
- `cancelled` - Order cancelled by user/system
- `expired` - Order expired (GTD/GTC orders)
- `rejected` - Order rejected by broker

**Preflight Check (MANDATORY before Step 1):**
```sql
-- Run this query to find unexpected status values
SELECT DISTINCT status, COUNT(*) as count
FROM orders
WHERE status NOT IN ('pending', 'submitted', 'partial_fill', 'filled', 'cancelled', 'expired', 'rejected')
GROUP BY status;
```
- **If unexpected statuses found:** STOP - clean up or rename before proceeding
- **Gate:** Preflight must return 0 rows before applying constraint
- Document any data cleanup in migration script

**Status Vocabulary Audit (MANDATORY before implementation):**
1. **Broker Enum Alignment:** Verify status values match Alpaca order status enum
   - Alpaca uses: `new`, `partially_filled`, `filled`, `done_for_day`, `canceled`, `expired`, `replaced`, `pending_cancel`, `pending_replace`, `accepted`, `pending_new`, `accepted_for_bidding`, `stopped`, `rejected`, `suspended`, `calculated`
2. **Mapping Required:** Create mapping from broker enum to internal enum
   - Example: `partially_filled` (Alpaca) → `partial_fill` (internal)
3. **Data Cleanup Script:** If spelling mismatches exist (e.g., `cancelled` vs `canceled`), prepare UPDATE script
4. **Gate:** Mapping document reviewed and approved before migration

**Zero-Downtime Migration Strategy:**
1. **Step 1:** Add CHECK constraint with NOT VALID (no table lock)
   ```sql
   SET LOCAL lock_timeout = '5s';
   SET LOCAL statement_timeout = '30s';
   ALTER TABLE orders ADD CONSTRAINT orders_status_check
   CHECK (status IN ('pending', 'submitted', 'partial_fill', 'filled', 'cancelled', 'expired', 'rejected')) NOT VALID;
   ```
2. **Step 2:** Validate constraint in background (VALIDATE CONSTRAINT)
   ```sql
   SET LOCAL lock_timeout = '5s';
   SET LOCAL statement_timeout = '300s';  -- Validation can take longer
   ALTER TABLE orders VALIDATE CONSTRAINT orders_status_check;
   ```
   - **Note:** VALIDATE CONSTRAINT acquires ShareUpdateExclusiveLock (blocks DDL, not DML)
   - Schedule during low-traffic window (early morning or weekend)
   - Pre-check: Kill any long-running transactions before validation
3. **Step 3:** Backfill NULL current_price values
   - **Source of Truth:** Fetch latest market quote from broker/data provider
   - **CRITICAL:** Do NOT use avg_entry_price (would mis-state P&L)
   - Batch updates: 1000 rows at a time to avoid long transactions
   - Handle concurrent updates: Use `UPDATE ... WHERE current_price IS NULL`
   ```python
   # Backfill script (NOT raw SQL - needs market data lookup)
   for batch in get_positions_with_null_price(batch_size=1000):
       for position in batch:
           quote = broker_client.get_latest_quote(position.symbol)
           if quote:
               position.current_price = quote.last_price
           else:
               position.current_price = position.avg_entry_price  # Last resort fallback
               position.price_stale = True  # Mark for refresh
       db.commit()
   ```
   - If market data unavailable: mark position as stale and trigger refresh job
4. **Step 4:** Add NOT NULL constraint (safe pattern - no exclusive lock)
   ```sql
   -- 4a: Add CHECK constraint with NOT VALID (instant, no scan)
   SET LOCAL lock_timeout = '5s';
   ALTER TABLE positions ADD CONSTRAINT current_price_not_null
   CHECK (current_price IS NOT NULL) NOT VALID;

   -- 4b: Validate constraint (ShareUpdateExclusive lock only, not exclusive)
   SET LOCAL lock_timeout = '5s';
   ALTER TABLE positions VALIDATE CONSTRAINT current_price_not_null;

   -- 4c: Promote to native NOT NULL (instant if validated constraint exists)
   ALTER TABLE positions ALTER COLUMN current_price SET NOT NULL;

   -- 4d: Cleanup redundant CHECK constraint
   ALTER TABLE positions DROP CONSTRAINT current_price_not_null;
   ```
   - Run on production-sized clone first to validate timing
   - Each sub-step is independently rollback-able

**Lock Mitigation:**
- Set `lock_timeout = '5s'` before migration to fail fast if blocked
- Pre-check for long-running transactions: `SELECT * FROM pg_stat_activity WHERE state != 'idle' AND query_start < now() - interval '5 minutes'`
- Alerting: Monitor `pg_locks` during migration window

**Rollback Plan:**
- Each step is independently rollback-able
- Constraint can be dropped without data loss
- Keep migration scripts for forward/backward

**Files to Create:**
- `db/migrations/XXXX_add_status_check_constraint.sql` - Step 1
- `db/migrations/XXXX_validate_status_constraint.sql` - Step 2
- `db/migrations/XXXX_backfill_current_price.sql` - Step 3
- `db/migrations/XXXX_add_current_price_not_null.sql` - Step 4

**Test Cases:**
- Test invalid status values rejected by database
- Test position queries require current_price
- Test migration runs without locking tables
- Test rollback restores previous state

**Rollout Gate:**
- [ ] Migration tested on production-sized dataset in staging
- [ ] Rollback tested successfully
- [ ] No table locks observed during dry run

---

### Component C17: Additional Medium Issues

**Effort Estimate:** 3 days (aggregate)

**Issue 6 - Cookie Parsing:** (0.5 days)
- Replace manual split with `SimpleCookie` or proper parser
- File: `apps/web_console/auth/session_manager.py`

**Issue 9 - Model Serialization:**
- Add HMAC signatures to model metadata
- Consider safe alternatives (safetensors, ONNX)
- File: `libs/models/serialization.py`

**Issue 13 - Tail Risk:**
- Implement Historical VaR as supplementary measure
- Document limitations of parametric approach
- File: `libs/risk/risk_decomposition.py`

**Issue 23 - Leakage Detection:**
- Add schema validators to block future-dated features
- Enforce feature timestamp <= current_date at inference
- File: `apps/signal_service/signal_generator.py`

**Issue 25 - Slippage Modeling for TWAP:**
- Integrate ADV-based slice sizing
- Add market impact penalty to slice quantities
- File: `apps/execution_gateway/slice_scheduler.py`

**Issue 10 - Storage Abstraction (Future Phase):**
- Large refactoring, consider separate phase
- Create abstract storage backend for cloud support
- Affects: `libs/data_providers/*.py`

**Rollout Gate:**
- [ ] Each sub-issue tested independently before merge
- [ ] No regression in existing functionality

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Blocking I/O fix causes async race conditions | High | Medium | Comprehensive testing, staged rollout |
| DB connection reuse across threads (thread-safety) | High | Medium | Per-thread connections, pool sizing, load test gate |
| Startup fail-fast causes frequent restarts | Medium | Low | Add retry logic with backoff |
| Reduced reconciliation interval increases load | Medium | Medium | Monitor Redis/DB performance |
| Broker abstraction breaks Alpaca integration | High | Low | Thorough integration testing |
| Security headers break existing clients | Medium | Low | Test with all known clients before deploy |
| API auth breaks S2S communication | High | Medium | Coordinate with orchestrator, document flows |
| Rate limiting blocks legitimate traffic | High | Low | Set conservative limits initially, add bypass for trusted IPs |
| Feature freshness rejects valid signals | Medium | Medium | Configure threshold with buffer, add override flag |
| Kill switch cert expiry blocks emergency halt | Critical | Low | 1-year validity, 30-day renewal reminder, backup certs |
| Kill switch cert compromise | Critical | Low | CRL/OCSP, immediate revocation playbook, cert rotation |
| Unexpected DB status values fail migration | High | Low | Preflight query gate, data cleanup script |

---

## Testing Strategy

### Unit Tests
- Each component has dedicated test file
- Mock external dependencies (broker, DB, Redis)
- Test edge cases and failure modes

### Integration Tests
- Test full order submission flow with wrapped I/O
- Test reconciliation with various failure scenarios
- Test kill switch via CLI and API

### Load Tests
- Verify event loop not blocked under concurrent requests
- Test reconciliation performance at 60s interval
- Measure latency impact of `asyncio.to_thread()` wrappers

---

## Phase-Level Governance Gates

Each phase follows the project's standard governance process:

### Per-Component Gates (Mandatory)
1. **Plan Review:** Component plan reviewed by Gemini + Codex planners
2. **Implementation:** Code follows 6-step pattern (plan → plan-review → implement → test → review → commit)
3. **CI-Local:** `make ci-local` must pass (single instance only, per CLAUDE.md)
4. **Dual Reviews:** Independent Gemini + Codex code reviews required
5. **Security Review:** C4-C7 changes require additional security team signoff
6. **Commit:** Only after all reviews approve

### Per-Component Rollback Triggers

| Component | Metric | Rollback Threshold |
|-----------|--------|-------------------|
| C2: Blocking I/O | executor_queue_depth | > max_workers*2 for 60s |
| C4: CSP | csp_violation_rate | > 1% of requests |
| C5: Rate Limiting | 429_rate | > 1% legitimate traffic |
| C6: API Auth | 401_rate | > 0.5% legitimate traffic |
| C7: Secrets | startup_failure_rate | Any failure blocks deploy |
| C8: Polling | reconciliation_latency_p99 | > 120s (2x target) |
| C11: Logging | log_parse_error_rate | > 5% (dashboards broken) |
| C12: Freshness | feature_rejection_rate | > 5% (too aggressive) |

**Rollback Process:**
1. Alert fires on threshold breach
2. On-call evaluates impact (false positive vs real issue)
3. If real issue: revert deployment or toggle feature flag
4. Post-mortem within 24h

### Phase Transition Gates

**Phase 1 → Phase 2 Go/No-Go Checklist:**
| Category | Checklist Item | Owner |
|----------|----------------|-------|
| **Components** | All C1-C7 components completed and deployed to staging | Dev |
| **Bugs** | No P0/P1 bugs open from Phase 1 | Dev |
| **Canary** | Canary deployment successful (10% traffic for 24h) | Ops |
| **Dashboards** | Metrics dashboards operational and validated | Ops |
| **Alerts** | All Phase 1 alerts configured and tested | Ops |
| **Runbooks** | Runbook updates reviewed and published | Ops |
| **Rollback** | Rollback procedure tested in staging | Dev |
| **On-Call** | On-call team briefed on new components | Ops |

**Phase 2 → Phase 3 Go/No-Go Checklist:**
| Category | Checklist Item | Owner |
|----------|----------------|-------|
| **Components** | All C8-C12 components completed and deployed to staging | Dev |
| **Stability** | Phase 1 components stable in production (7 days) | Ops |
| **Canary** | Canary deployment successful for Phase 2 | Ops |
| **Alerts** | Alerting verified with synthetic failures | Ops |
| **Log Parsers** | Structured logging parsers validated | Ops |
| **Dashboards** | Phase 2 dashboards operational | Ops |
| **Rollback** | Rollback procedure tested for Phase 2 | Dev |

**Phase 3 Complete Go/No-Go Checklist:**
| Category | Checklist Item | Owner |
|----------|----------------|-------|
| **Components** | All C13-C17 components completed and deployed | Dev |
| **Tests** | Full regression test suite passes | Dev |
| **Staged Rollout** | Production deployment with staged rollout complete | Ops |
| **Runbooks** | Ops team signoff on all runbook updates | Ops |
| **Documentation** | Architecture docs updated for broker abstraction | Dev |

### Staged Deploy/Canary Process
1. Deploy to staging, run integration tests
2. Deploy to production canary (10% traffic)
3. Monitor for 24h, check error rates, latency
4. If metrics healthy, promote to 100%
5. Keep rollback ready for 48h post-deploy

---

## Implementation Order

Based on independent reviews from Codex and Gemini, plus second review:

### Phase 1: Critical Safety & Security (Days 1-10)

**Execution Order:** Security items (C5, C6) execute FIRST to reduce attack surface before other changes.

| Order | Component | Issue(s) | Severity | Effort | Dependency |
|-------|-----------|----------|----------|--------|------------|
| 1 | C5: Rate Limiting on Orders | 18 | CRITICAL (P0) | 1d | None |
| 2 | C6: API Authentication | 19 | CRITICAL (P0) | 1.5d | None |
| 3 | C7: Secrets Abstraction | 21 | CRITICAL | 1.5d | Before C3 |
| 4 | C1: Startup Reconciliation Fail-Fast | 3 | CRITICAL | 1.5d | None |
| 5 | C2: Blocking I/O Remediation | 1 | CRITICAL | 2d | After C5/C6 |
| 6 | C3: Redundant Kill Switch | 7 | CRITICAL | 2d | After C7 |
| 7 | C4: HTTP Security Headers | 17 | CRITICAL | 1d | None |
| | **Phase 1 Total** | | | **10.5d** | |

### Phase 2: Reliability & Observability (Days 11-16)
| # | Component | Issue(s) | Severity | Effort |
|---|-----------|----------|----------|--------|
| 8 | C8: Reduce Polling Latency | 14 | HIGH | 1d |
| 9 | C9: Zombie Slice Recovery | 16 | MEDIUM | 1.5d |
| 10 | C10: Redis Fallback Mode | 4 | MEDIUM | 1d |
| 11 | C11: Structured Logging | 20 | HIGH | 1d |
| 12 | C12: Feature Freshness Guards | 22 | HIGH | 1d |
| | **Phase 2 Total** | | | **5.5d** |

### Phase 3: Architecture & Data Quality (Days 17-30)
| # | Component | Issue(s) | Severity | Effort | Notes |
|---|-----------|----------|----------|--------|-------|
| 13 | C13: Broker Abstraction | 15 | HIGH | 4d (+2d buffer) | Large blast radius |
| 14 | C14: Dependency Injection | 2 | MEDIUM | 3d (+1d buffer) | Many call sites |
| 15 | C15: Trading Calendar | 8 | MEDIUM | 1.5d | |
| 16 | C16: Database Schema Hardening | 24, 26 | MEDIUM | 2d | |
| 17 | C17: Additional Medium Issues | 6, 9, 13, 23, 25 | MEDIUM | 3d | |
| | **Phase 3 Total** | | | **13.5d (+3d buffer)** | |

**Effort Buffer Note:** C13 and C14 have large blast radius affecting many call sites and tests. Consider 0.5-1 day spike at start of Phase 3 to confirm scope before committing to estimates.

**Grand Total: 29.5 days** (conservative estimates, +3d buffer for C13/C14 if needed)

---

## Appendix: Invalid Issues

The following issues were analyzed and found to be non-issues or already mitigated:

### Issue 5: Roll Your Own Cryptography
**Status:** INVALID - Properly Implemented

The HMAC verification in `main.py:463-559` uses:
- `hmac.compare_digest()` for constant-time comparison (prevents timing attacks)
- Canonical JSON for signature payload (prevents delimiter injection)
- Timestamp validation with tolerance (prevents replay attacks)

### Issue 11: Blocking Bulk Data Fetches
**Status:** INVALID - Already Optimized

Redis batch fetching uses `MGET` (main.py:1223-1226):
```python
price_values = redis_client.mget(price_keys)
```
This provides O(1) network round-trips regardless of symbol count.

### Issue 12: Fail-Open Fat Finger Logic
**Status:** INVALID - Already Fail-Closed

When price is missing, `data_unavailable` breach is created (fat_finger_validator.py:219-227):
```python
if missing_fields:
    breaches.append(FatFingerBreach(threshold_type="data_unavailable", ...))
return FatFingerResult(breached=bool(breaches), ...)
```
Missing price causes validation failure, not pass-through.

---

## Related Documents

- [ADR-0021: Risk Model Implementation](../ADRs/ADR-0021-risk-model-implementation.md)
- [P4T5: Web Console Features](../ARCHIVE/TASKS_HISTORY/P4T5_DONE.md)
- [Ops Runbook](../RUNBOOKS/ops.md)

---

**Last Updated:** 2025-12-21
**Reviewed By:** Codex, Gemini (Independent Reviews), Second Expert Review
**Total Issues:** 26 identified, 22 valid, 3 invalid, 1 already covered

---

## Progress Log

### 2025-12-21: C5 Rate Limiting + C6 API Authentication Complete

**Completed Components:**
- **C5: Rate Limiting** - Added rate limiting to order submission and signal generation endpoints with per-user/per-service buckets
- **C6: API Authentication** - Implemented comprehensive S2S authentication with:
  - HMAC-SHA256 signed internal tokens
  - Body hash verification for payload integrity (POST/PUT/PATCH/DELETE)
  - Query string signing using `request.url.query` (tamper-proof)
  - Per-service secrets support (`INTERNAL_TOKEN_SECRET_{SERVICE_ID}`)
  - Nonce-based replay protection via Redis with service-scoped keys
  - Secret rotation support (read at call time, not cached)
  - Fail-closed authentication (RuntimeError when secret missing)

**Key Files:**
- `libs/common/api_auth_dependency.py` - Server-side S2S auth (NEW)
- `libs/common/rate_limit_dependency.py` - Rate limiting integration
- `apps/orchestrator/clients.py` - Client-side auth headers
- `apps/execution_gateway/main.py` - Integrated auth dependencies
- `apps/signal_service/main.py` - Integrated auth dependencies
- `tests/libs/common/test_api_auth_dependency.py` - 44 tests (NEW)

**Security Fixes (from Codex review iterations):**
1. Server uses `request.url.query` instead of X-Query header (tamper-proof)
2. Body hash required for state-changing requests (POST/PUT/PATCH/DELETE)
3. Client computes hash of empty bytes when body is None
4. Nonce cache scoped by service_id
5. Nonce length bounded (MAX_NONCE_LENGTH = 128)
6. Secret rotation support (read at call time)

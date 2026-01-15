# Component 6+7: mTLS Fallback, Runbooks & Documentation

**Task ID:** P2T3-C6-C7
**Component:** mTLS Fallback + Runbooks (C6) + Documentation (C7)
**Estimated Duration:** 2.5 days (20 hours total: C6=14h, C7=6h)
**Dependencies:** Components 1-5 ✅
**Status:** PLANNING (Revision 3 - Addressing Codex CRITICAL/HIGH findings)

---

## Objective

Complete Phase 3 (OAuth2 Authentication & Authorization) with operational resilience features and comprehensive documentation:

1. **Component 6 (14h):** mTLS fallback authentication (admin-only), emergency runbooks, monitoring/alerting
2. **Component 7 (6h):** Architecture documentation, ADR updates, developer guides

**Rationale for combining:** Component 7 is documentation-only (no code review needed), making it efficient to bundle with Component 6's final implementation.

**Revision 2 Changes (Gemini feedback):**
- mTLS scope clarified (admin-only), feature flag, hysteresis
- Testing expanded (negative cases), script safety (SCAN + UNLINK)
- Runbooks enhanced (prerequisites, escalation), docs bump (4h → 6h)

**Revision 3 Changes (Codex CRITICAL/HIGH findings):**
- **CRITICAL FIX:** Production rollout keeps mTLS disabled by default (incident-only enablement with change control)
- **HIGH FIX (R3.1):** Certificate lifetime enforcement mechanics specified (7-day max: reject if `notAfter-notBefore > 7d`)
- **HIGH FIX (R3.1):** CRL enforcement mechanics detailed (1h cache TTL, 24h freshness, fail-secure on fetch errors, CRL source URL)
- **HIGH FIX (R3.0):** File manifest reconciled (16 new + 9 modified = 25 total, all files explicitly named)
- **MEDIUM FIX (R3.1):** Runbook URLs changed to absolute GitHub Pages URLs with templating (`GITHUB_ORG` placeholder)
- **MEDIUM FIX (R3.1):** Prometheus `external_url` configuration added for alert routing
- **MEDIUM FIX (R3.0):** Cert expiry + CRL failure alerts added to Prometheus
- **MEDIUM FIX (R3.0):** Redis DEL fallback tested (<4.0 compatibility), rate-limit verified (>50k keys)
- **LOW NOTE (Codex R3):** 14h estimate is tight; contingency buffer available if mTLS/testing requires extra debug time

---

## Component 6: mTLS Fallback + Runbooks (14 hours)

### 6.1 mTLS Fallback Authentication (6 hours)

**Problem:** Auth0 IdP outage blocks all admin/operator logins, creating operational blind spot during incidents.

**Solution:** Admin-only mTLS fallback mode with hysteresis and feature flag controls.

**SCOPE CLARIFICATION (Gemini/Codex Review):**
- **Target Audience:** Administrators/operators only (not general users)
- **Certificate Distribution:** Pre-distributed admin client certificates (manual provisioning)
- **Use Case:** Emergency administrative access during prolonged Auth0 outages

**Implementation:**

1. **IdP Health Monitor Enhancement** (`apps/web_console/auth/idp_health.py`)
   - **Polling Interval:** 10 seconds (health check frequency)
   - **Fallback Entry Trigger:** 3 consecutive failures (30s sustained outage) OR 1-minute error rate >50%
   - **Fallback Exit Trigger (Hysteresis):** 5 consecutive successes AND 5-minute stable period
   - **Exponential Backoff:** After fallback activation, check every 60s (reduce noise)

2. **mTLS Fallback Mode** (`apps/web_console/auth/mtls_fallback.py` - NEW)
   - **Feature Flag:** `ENABLE_MTLS_FALLBACK` (default: disabled, requires explicit enable)
   - **Admin Certificate Validation:** CN must match admin allowlist
   - **Certificate Lifetime Enforcement (Codex R3 HIGH):**
     - Reject certs with `(notAfter - notBefore) > 7 days` (short-lived only)
     - Reject certs with `notAfter < now()` (expired)
     - Warn if `notAfter < now() + 24h` (expiring soon)
   - **Certificate Revocation (CRL) Enforcement (Codex R3 HIGH):**
     - CRL source: `http://ca.trading-platform.local/crl/admin-ca.crl` (internal CA)
     - CRL cache: 1-hour TTL, refresh on cache miss
     - CRL freshness: Reject auth if CRL age > 24h (fail-secure)
     - HTTP failure: Reject auth if CRL fetch fails (fail-secure, alert fired)
   - **Audit Logging:** All fallback authentications logged with cert fingerprint, source IP, expiry date, CRL validation status
   - **Auto-Disable on Auth Errors:** >10 mTLS auth failures/min triggers auto-disable + alert
   - **Rollback Command:** `scripts/disable_mtls_fallback.sh` (emergency kill switch, idempotent)

3. **App Integration** (`apps/web_console/app.py`)
   - Check fallback mode AND feature flag before certificate auth
   - Display fallback mode banner to admins (yellow warning)
   - Prometheus metrics: fallback duration, mTLS auth success/failure rates

**Files to Create:**
- `apps/web_console/auth/mtls_fallback.py` - mTLS fallback logic with feature flag + cert validation
- `tests/apps/web_console/auth/test_mtls_fallback.py` - Fallback tests (positive + negative cases)
- `scripts/disable_mtls_fallback.sh` - Emergency disable script (idempotent, dry-run support, logs actions)

**Files to Modify:**
- `apps/web_console/auth/idp_health.py` - Add hysteresis + exponential backoff
- `apps/web_console/app.py` - Add fallback mode check + feature flag guard
- `apps/web_console/auth/__init__.py` - Export fallback functions
- `.env.example` - Add `ENABLE_MTLS_FALLBACK=false` (default disabled)

**Testing (Codex Review - Comprehensive Certificate Validation):**
- [ ] mTLS fallback activates after 3 consecutive IdP failures (30s)
- [ ] Hysteresis exit: 5 consecutive successes at 60s intervals (deterministic, Codex LOW)
- [ ] Feature flag disabled → mTLS fallback never activates
- [ ] **Cert Expiry: Expired client certificate (notAfter < now)** → mTLS auth fails with error log (Codex HIGH)
- [ ] **Cert Expiry: Certificate expiring in <24h** → Warning log, auth succeeds (Codex MEDIUM)
- [ ] **Cert Revocation: Revoked certificate (CRL check)** → mTLS auth rejected (Codex HIGH)
- [ ] **Cert Revocation: CRL fetch failure** → Fail-secure: reject auth + alert (Codex MEDIUM)
- [ ] **Negative: Invalid CN (not in admin allowlist)** → mTLS auth rejected
- [ ] **Negative: Missing client certificate** → Fallback auth fails gracefully
- [ ] Audit logging: All fallback authentications have cert fingerprint + IP + expiry date
- [ ] Auto-disable: >10 auth failures/min triggers fallback disable + alert
- [ ] Prometheus metrics: fallback duration, mTLS auth rates (success/failure), cert expiry warnings

---

### 6.2 Emergency Runbooks (4 hours)

**Scope:** Step-by-step operational procedures for common failure scenarios.

**Runbooks to Create:**

1. **Auth0 IdP Outage Response** (`docs/RUNBOOKS/auth0-idp-outage.md`)
   - **Prerequisites:** Admin client certificates pre-distributed, `ENABLE_MTLS_FALLBACK` configured
   - **Detection:** IdP health check failures, Prometheus `IdPHealthCheckFailed` alert
   - **Immediate Actions:** Verify mTLS fallback activated (check logs), enable feature flag if disabled
   - **Investigation:** Check Auth0 status page, network connectivity, DNS resolution
   - **Escalation:** Notify on-call SRE if fallback auth fails, escalate to Auth0 support if outage >30min
   - **Recovery:** Monitor IdP health restoration, verify auto-recovery (hysteresis), disable fallback manually if needed
   - **Rollback:** Run `scripts/disable_mtls_fallback.sh` if certificate errors spike

2. **Session Encryption Key Rotation** (`docs/RUNBOOKS/session-key-rotation.md`)
   - **Prerequisites:** Dual-key support tested, backup of current key, staging validation complete
   - **Owners:** Platform Team (primary), On-call SRE (secondary)
   - **Zero-Downtime Procedure:** Add new key (old + new dual-key support), monitor 24h, remove old key
   - **Pre-rotation Checklist:** Backup current key, test dual-key in staging, verify session metrics baseline
   - **Rotation Steps:** Set `SESSION_ENCRYPTION_KEY_NEW`, restart auth_service, wait 24h, remove old key
   - **Rollback Plan:** If sessions break, revert to old key only, investigate new key format issues
   - **Post-Rotation Validation:** Session continuity (no user logouts), metrics stable, new sessions use new key

3. **OAuth2 Session Cleanup** (`docs/RUNBOOKS/oauth2-session-cleanup.md`)
   - **Prerequisites:** Redis access, read-only mode tested, dry-run executed successfully
   - **When to Run:** IdP migration, mass logout (security incident), stale session cleanup (>30 days)
   - **Owners:** Platform Team (execution), Security Team (approval for mass cleanup)
   - **Safe Procedure:** SCAN + UNLINK with batching (500 keys/iteration), 10ms sleep between batches
   - **Script Usage:** `scripts/clear_oauth2_sessions.py --dry-run --prefix oauth2:session: --batch-size 500`
   - **Validation:** Verify user re-authentication flow, check Redis memory reduction, confirm no active session breaks
   - **Escalation:** If users report auth loops, immediately stop script, investigate stuck sessions

4. **mTLS Fallback Disable** (`scripts/disable_mtls_fallback.sh` - Codex MEDIUM)
   - **Purpose:** Emergency kill switch to disable mTLS fallback (incident-only use)
   - **Behavior:** Idempotent toggle of `ENABLE_MTLS_FALLBACK` env var, triggers app reload, logs all actions
   - **Dry-Run Mode:** `--dry-run` flag shows actions without execution
   - **Safety:** Checks current fallback state before toggle, prevents double-disable
   - **Audit:** Logs disable reason, operator name, timestamp to structured log + Prometheus metric

**Files to Create:**
- `docs/RUNBOOKS/auth0-idp-outage.md` - Now includes prerequisites, escalation paths
- `docs/RUNBOOKS/session-key-rotation.md` - Now includes owners, rollback plan
- `docs/RUNBOOKS/oauth2-session-cleanup.md` - Now includes prerequisites, owners
- `docs/RUNBOOKS/mtls-certificate-management.md` - **NEW:** Admin cert distribution, CRL/OCSP validation
- `scripts/clear_oauth2_sessions.py` - Safe cleanup with SCAN + UNLINK + batching + rate-limiting

**Testing (Codex Review - Comprehensive Script Safety):**
- [ ] **Dry-run mode:** Script logs deletion count without actually deleting
- [ ] **SCAN + UNLINK:** Uses UNLINK (non-blocking), falls back to DEL if Redis <4.0 (Codex MEDIUM)
- [ ] **DEL Fallback:** Tested on Redis 3.x - graceful fallback to DEL when UNLINK unavailable
- [ ] **Batching:** Processes 500 keys per iteration with 10ms sleep (prevents blocking)
- [ ] **Rate-limiting:** Configurable delay between batches (default 10ms), enforced at 10ms intervals
- [ ] **Large Keyset Test:** >50k keys deleted respecting rate-limit (Codex MEDIUM)
- [ ] **Prefix filtering:** Only deletes keys matching `oauth2:session:*` prefix
- [ ] **Redis connection failure:** Script exits gracefully with error code, no partial deletes
- [ ] **Cursor exhaustion:** SCAN continues until cursor=0 (complete keyspace coverage)
- [ ] **Disable Script Tests:** Idempotency (double-disable safe), dry-run, audit logging (Codex MEDIUM)

---

### 6.3 Monitoring & Alerting (4 hours)

**Scope:** Prometheus alerts and Grafana dashboards for OAuth2 operational visibility.

**Prometheus Alerts** (`infra/prometheus/alerts/oauth2.yml` - Codex R3: Absolute URLs):
- `IdPHealthCheckFailed` - IdP unreachable (3 consecutive failures)
  - **Severity:** warning
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/auth0-idp-outage.html`
  - **Labels:** `service=auth_service, component=idp_health`
- `IdPFallbackModeActive` - mTLS fallback engaged (P1 alert)
  - **Severity:** critical
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/auth0-idp-outage.html#fallback-mode`
  - **Labels:** `service=auth_service, component=mtls_fallback`
- `MtlsAuthFailureRateHigh` - **NEW:** mTLS auth failures >5/min during fallback
  - **Severity:** warning
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/mtls-certificate-management.html`
- `MtlsCertificateExpiringSoon` - **NEW:** Admin cert expiring in <7 days
  - **Severity:** warning
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/mtls-certificate-management.html#rotation`
- `MtlsCrlFetchFailure` - **NEW:** CRL fetch error during fallback (fail-secure)
  - **Severity:** critical (rejects auth if CRL unavailable)
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/mtls-certificate-management.html#crl-troubleshooting`
- `SessionEncryptionFailureRate` - Session encryption errors >1%
  - **Severity:** warning
  - **Runbook:** `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/session-key-rotation.html`
- `OAuthCallbackLatencyHigh` - /callback endpoint >2s P95
  - **Severity:** info

**Runbook Publishing (Codex R3 MEDIUM):**
- Runbooks deployed via GitHub Pages: `https://GITHUB_ORG.github.io/trading_platform/RUNBOOKS/`
- Placeholder `GITHUB_ORG` replaced during deployment via CI templating
- Automated link checking in CI validates all URLs before deployment
- Prometheus `external_url` configured to match gh-pages domain for alert routing

**Grafana Dashboard** (`infra/grafana/dashboards/oauth2-sessions.json`):
- Active sessions by authentication method (OAuth2 vs mTLS)
- Session creation/destruction rates
- IdP health check success rate (7-day trend)
- OAuth2 callback latency (P50, P95, P99)
- Fallback mode duration histogram
- **NEW:** mTLS auth success/failure rates (during fallback only)

**Files to Create:**
- `infra/prometheus/alerts/oauth2.yml` - Now includes severity, runbook_url, service labels
- `infra/grafana/dashboards/oauth2-sessions.json` - Wired to provisioning folder

**Files to Modify:**
- `infra/prometheus/prometheus.yml` - Add oauth2.yml to alert rules
- `docker-compose.yml` - Mount Grafana dashboard to `/var/lib/grafana/dashboards/`
- `infra/grafana/provisioning/dashboards/dashboards.yaml` - Add oauth2-sessions.json to auto-load

**Testing (Codex Review - Enhanced):**
- [ ] Prometheus alerts trigger correctly (simulate IdP failure via DNS block)
- [ ] Alert payloads include `severity`, `runbook_url`, `service` labels
- [ ] **Synthetic alert test:** `make test-prometheus-alerts` fires test alerts in staging
- [ ] Grafana dashboard auto-loads on Grafana startup (provisioning works)
- [ ] Grafana dashboard displays all metrics (no missing data sources)
- [ ] Alert annotations link to runbooks (URLs valid and accessible)

---

## Post-Implementation: Critical Fixes (2025-11-26)

**Status:** 2 CRITICAL + 2 MEDIUM fixes completed, 1 HIGH (Prometheus metrics) deferred

### Code Review Findings

Comprehensive code reviews by Codex and Gemini identified critical architectural flaws that prevented the mTLS fallback mechanism from functioning. All blocking issues have been resolved.

### CRITICAL #1: IdPHealthChecker State Not Persisted ✅

**Problem:** `IdPHealthChecker` was instantiated fresh on every Streamlit request in `_oauth2_auth()`. Consecutive failure counters reset to 0, so `is_fallback_mode()` never returned `True`.

**Impact:** During real Auth0 outages, the system failed to detect it. Fallback mechanism was non-functional.

**Fix Applied:**
- Added `_idp_health_checker` global singleton variable
- Created `_get_idp_health_checker()` function using global pattern (consistent with `_get_session_manager()`)
- Updated `_oauth2_auth()` to use `_get_idp_health_checker()` instead of creating new instance

**Location:** `apps/web_console/auth/__init__.py:133-159`

### CRITICAL #2: Health Check Never Executed ✅

**Problem:** Code called `is_fallback_mode()` but never called `check_health()` to update state. Even with persistent state fix, health checker never ran.

**Impact:** Health monitor was completely inactive. System could not detect Auth0 outages.

**Fix Applied:**
- Added `asyncio.run(idp_checker.check_health())` call in `_oauth2_auth()` before querying fallback mode
- Included `nest_asyncio` fallback for Streamlit compatibility
- Wrapped in try-except to gracefully handle errors without blocking OAuth2 login

**Location:** `apps/web_console/auth/__init__.py:523-566`

### MEDIUM #3: mTLS Validator Not Cached ✅

**Problem:** `MtlsFallbackValidator` and `CRLCache` recreated on every authentication attempt in `_mtls_fallback_auth()`. The 1-hour CRL cache was useless.

**Impact:** Performance degradation. CRL fetched on every auth attempt instead of cached.

**Fix Applied:**
- Added `_mtls_fallback_validator` global singleton variable
- Created `_get_mtls_fallback_validator()` function
- Updated `_mtls_fallback_auth()` to use singleton instead of creating new instance

**Location:** `apps/web_console/auth/__init__.py:162-196, 1340-1353`

### MEDIUM #4: asyncio.run() Streamlit Incompatibility ✅

**Problem:** `asyncio.run()` inside Streamlit may fail with `RuntimeError: This event loop is already running` in some deployment scenarios.

**Impact:** Valid admin certificates could fail authentication unpredictably.

**Fix Applied:**
- Included `nest_asyncio` fallback pattern in both `_oauth2_auth()` (for health checks) and `_mtls_fallback_auth()` (for certificate validation)
- Catches `RuntimeError` and retries with `nest_asyncio.apply()`

---

### HIGH #5: Prometheus Metrics Instrumentation 🔲 DEFERRED

**Problem:** All 14 Prometheus alerts in `infra/prometheus/alerts/oauth2.yml` reference metrics that are never emitted by the code. Monitoring is blind.

**Impact:**
- IdP health alerts never fire
- mTLS failure alerts never fire
- Session management alerts never fire
- Certificate expiry alerts never fire
- Operators have no visibility into system state

**Decision:** Deferred to follow-up task. Core functionality is now working (CRITICAL fixes completed). Monitoring visibility can be added incrementally without blocking deployment.

**Review Status:** ✅ Reviewed by Gemini + Codex (2 iterations, 2025-11-26)

**First Review Fixes:**
- **Critical Fix:** CN label cardinality addressed with allowlist bucketing
- **Critical Fix:** Metrics endpoint integrated with main app (not standalone)
- **High Fix:** Active sessions gauge uses SCAN with timeout
- **High Fix:** Added CRL fetch duration histogram
- **Medium Fix:** Added IdP health counters for rate signals
- **Effort Adjusted:** 5h → 7h (includes multiprocess integration + validation)

**Second Review Fixes (Iteration 2):**
- **HIGH Fix (Gemini):** Added shared volume mount for `web_console` service
- **HIGH Fix (Codex):** Added `oauth2_mtls_crl_fetch_failures_total` for alert compatibility
- **HIGH Fix (Codex):** Added multiprocess mode wiring for main app
- **MEDIUM Fix (Gemini):** Skip gauge update on SCAN timeout (prevent partial data)
- **MEDIUM Fix (Codex):** Added expiry gauge allowlist guard
- **MEDIUM Fix (Codex):** Added CRL cache age gauge updates
- **MEDIUM Fix (Gemini):** Added multiprocess directory cleanup on startup
- **LOW Fix (Gemini):** Initialize session rotation timestamp gauge

**Required Metrics (26 total - revised after 2nd review):**

**IdP Health Monitoring (7 metrics - Codex/Gemini: Added counters for rate signals):**
```python
# apps/web_console/auth/idp_health.py

from prometheus_client import Counter, Gauge, Histogram

# REVIEW FIX: Added counters for rate-based alerting (Codex MEDIUM)
idp_health_checks_total = Counter(
    'oauth2_idp_health_checks_total',
    'Total IdP health checks performed',
    ['auth0_domain', 'result']  # result = success|failure
)

# Gauge metrics (current state)
idp_consecutive_failures = Gauge(
    'oauth2_idp_health_consecutive_failures',
    'Consecutive IdP health check failures',
    ['auth0_domain']
)

idp_consecutive_successes = Gauge(
    'oauth2_idp_health_consecutive_successes',
    'Consecutive IdP health check successes',
    ['auth0_domain']
)

idp_fallback_mode = Gauge(
    'oauth2_idp_fallback_mode',
    'Whether mTLS fallback mode is active (1=active, 0=inactive)',
    ['auth0_domain']
)

idp_stability_period_active = Gauge(
    'oauth2_idp_stability_period_active',
    'Whether stability period is active (1=active, 0=inactive)',
    ['auth0_domain']
)

# Histogram metric (latency tracking)
# REVIEW FIX: Custom buckets tuned for <1s p99 (Codex MEDIUM)
idp_health_check_duration = Histogram(
    'oauth2_idp_health_check_duration_seconds',
    'IdP health check duration in seconds',
    ['auth0_domain'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0]  # Tuned for sub-second p99
)

# Instrumentation in IdPHealthChecker.check_health():
async def check_health(self):
    start_time = time.time()
    try:
        # ... health check logic ...
        if success:
            idp_health_checks_total.labels(self.auth0_domain, result='success').inc()
            self._consecutive_successes += 1
            self._consecutive_failures = 0  # REVIEW FIX: Reset on success (Codex)
            idp_consecutive_successes.labels(self.auth0_domain).set(self._consecutive_successes)
            idp_consecutive_failures.labels(self.auth0_domain).set(0)
        else:
            idp_health_checks_total.labels(self.auth0_domain, result='failure').inc()
            self._consecutive_failures += 1
            self._consecutive_successes = 0  # REVIEW FIX: Reset on failure (Codex)
            idp_consecutive_failures.labels(self.auth0_domain).set(self._consecutive_failures)
            idp_consecutive_successes.labels(self.auth0_domain).set(0)

        idp_fallback_mode.labels(self.auth0_domain).set(1 if self._fallback_mode else 0)
        idp_stability_period_active.labels(self.auth0_domain).set(1 if self._stability_start else 0)
    finally:
        duration = time.time() - start_time
        idp_health_check_duration.labels(self.auth0_domain).observe(duration)
```

**mTLS Fallback Authentication (10 metrics - Review iteration 2: Added alert-compatible failures counter):**
```python
# apps/web_console/auth/mtls_fallback.py

from prometheus_client import Counter, Gauge, Histogram

# Counter metrics (cumulative)
# REVIEW FIX: Added CRL fetch counter for rate-based alerting (Codex MEDIUM)
mtls_crl_fetch_total = Counter(
    'oauth2_mtls_crl_fetch_total',
    'Total CRL fetch attempts',
    ['crl_url', 'result']  # result = success|failure
)

# REVIEW FIX 2: Dedicated failures counter for alert compatibility (Codex HIGH)
mtls_crl_fetch_failures = Counter(
    'oauth2_mtls_crl_fetch_failures_total',
    'CRL fetch failures (alert rule compatibility)',
    ['crl_url']
)

# REVIEW FIX: CN label protected from cardinality explosion (Gemini/Codex CRITICAL)
mtls_auth_total = Counter(
    'oauth2_mtls_auth_total',
    'Total mTLS authentication attempts',
    ['cn', 'result']  # cn = allowlisted CN or "unauthorized"
)

mtls_auth_failures = Counter(
    'oauth2_mtls_auth_failures_total',
    'mTLS authentication failures by reason',
    ['cn', 'reason']  # cn = allowlisted CN or "unauthorized", reason = expired|revoked|cn_not_allowed|crl_error
)

# Gauge metrics (current state)
mtls_cert_expiry = Gauge(
    'oauth2_mtls_cert_not_after_timestamp',
    'Certificate expiry timestamp (Unix epoch)',
    ['cn']  # Only for allowlisted CNs
)

mtls_crl_last_update = Gauge(
    'oauth2_mtls_crl_last_update_timestamp',
    'CRL last update timestamp (Unix epoch)',
    ['crl_url']
)

mtls_crl_cache_age = Gauge(
    'oauth2_mtls_crl_cache_age_seconds',
    'CRL cache age in seconds',
    ['crl_url']
)

# REVIEW FIX: Added CRL fetch duration histogram (Gemini/Codex HIGH)
mtls_crl_fetch_duration = Histogram(
    'oauth2_mtls_crl_fetch_duration_seconds',
    'CRL fetch duration in seconds',
    ['crl_url'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]  # Tuned for CRL endpoint latency
)

# Instrumentation in MtlsFallbackValidator.validate_certificate():
async def validate_certificate(self, cert_pem: str, headers: dict) -> CertificateInfo:
    try:
        # ... validation logic ...

        # REVIEW FIX: Protect CN label from cardinality explosion (Gemini/Codex CRITICAL)
        # Only use actual CN if in allowlist, otherwise use "unauthorized"
        if cert_info.cn in self.admin_cn_allowlist:
            label_cn = cert_info.cn
        else:
            label_cn = "unauthorized"

        if cert_info.valid:
            mtls_auth_total.labels(cn=label_cn, result='success').inc()
            # REVIEW FIX 2: Only track expiry for allowlisted certs (Codex MEDIUM)
            # Guard the expiry gauge with same allowlist check to prevent cardinality bypass
            if cert_info.cn in self.admin_cn_allowlist:
                mtls_cert_expiry.labels(cn=cert_info.cn).set(cert_info.not_after.timestamp())
        else:
            mtls_auth_total.labels(cn=label_cn, result='failure').inc()
            mtls_auth_failures.labels(cn=label_cn, reason=cert_info.error_reason).inc()

        return cert_info
    except Exception as e:
        mtls_auth_failures.labels(cn='unknown', reason='validation_error').inc()
        raise

# Instrumentation in CRLCache.fetch_crl():
async def fetch_crl(self):
    start_time = time.time()
    now = time.time()
    try:
        # ... CRL fetch logic ...
        mtls_crl_fetch_total.labels(self.crl_url, result='success').inc()
        mtls_crl_last_update.labels(self.crl_url).set(now)

        # REVIEW FIX 2: Update CRL cache age gauge (Codex MEDIUM)
        if hasattr(self, '_last_successful_fetch'):
            cache_age = now - self._last_successful_fetch
            mtls_crl_cache_age.labels(self.crl_url).set(cache_age)
        self._last_successful_fetch = now
    except Exception as e:
        # REVIEW FIX 2: Emit both _total{result="failure"} and _failures_total (Codex HIGH)
        mtls_crl_fetch_total.labels(self.crl_url, result='failure').inc()
        mtls_crl_fetch_failures.labels(self.crl_url).inc()  # For alert rule compatibility
        raise
    finally:
        duration = time.time() - start_time
        mtls_crl_fetch_duration.labels(self.crl_url).observe(duration)
```

**Session Management (5 metrics):**
```python
# apps/web_console/auth/__init__.py + session_manager.py

from prometheus_client import Counter, Gauge

# Gauge metrics
active_sessions = Gauge(
    'oauth2_active_sessions_count',
    'Number of active OAuth2 sessions'
)

session_secret_rotation = Gauge(
    'oauth2_session_secret_last_rotation_timestamp',
    'Last session secret rotation timestamp (Unix epoch)'
)

# Counter metrics
session_created = Counter(
    'oauth2_session_created_total',
    'Total sessions created'
)

session_signature_failures = Counter(
    'oauth2_session_signature_failures_total',
    'Session signature verification failures',
    ['reason']  # reason = invalid|expired|missing
)

session_cleanup_failures = Counter(
    'oauth2_session_cleanup_failures_total',
    'Session cleanup script failures'
)

# REVIEW FIX: Active sessions gauge implementation (Codex HIGH)
# Problem: SCARD doesn't accept patterns, redis.scard("oauth2:session:*") is invalid
# Solution: Periodic SCAN with timeout guard (option B from review)
#
# Implementation:
def update_active_sessions_gauge():
    """Update active sessions gauge by scanning Redis keys with timeout guard."""
    import time
    redis_client = get_redis()  # from session_manager
    cursor = 0
    count = 0
    start_time = time.time()
    timeout_seconds = 5  # Guard against slow SCAN
    timed_out = False

    try:
        while True:
            if time.time() - start_time > timeout_seconds:
                logger.warning("Active sessions SCAN timeout after 5s - skipping gauge update")
                timed_out = True
                break

            cursor, keys = redis_client.scan(
                cursor=cursor,
                match="oauth2:session:*",
                count=100
            )
            count += len(keys)

            if cursor == 0:
                break

        # REVIEW FIX 2: Skip gauge update on timeout to prevent partial data (Gemini MEDIUM)
        if not timed_out:
            active_sessions.set(count)
    except Exception as e:
        logger.error(f"Failed to update active_sessions gauge: {e}")

# Session secret rotation timestamp initialization (Gemini LOW)
# Initialize on app startup to prevent immediate alert firing
session_secret_rotation.set(time.time())  # Default to "now" if no rotation record exists
# TODO: Load actual rotation timestamp from persistent storage if available

# Instrumentation locations:
# - SessionManager.create_session() → session_created.inc()
# - SessionManager.validate_session() → session_signature_failures on error
# - scripts/clear_oauth2_sessions.py → session_cleanup_failures on exception
# - Periodic task (every 30s) → update_active_sessions_gauge()
#
# Note: Alternative option A (maintain Redis SET):
#   SADD oauth2:active_sessions {session_id} on create
#   SREM oauth2:active_sessions {session_id} on delete/expire
#   active_sessions.set(redis.scard("oauth2:active_sessions"))
# Option B chosen for simplicity (no code changes to session creation/deletion)
```

**OAuth2 Flow (4 metrics):**
```python
# apps/web_console/auth/__init__.py (OAuth2 endpoints)

from prometheus_client import Counter

# Counter metrics
oauth2_authorization_total = Counter(
    'oauth2_authorization_total',
    'OAuth2 authorization attempts',
    ['result']  # result = success|failure
)

oauth2_authorization_failures = Counter(
    'oauth2_authorization_failures_total',
    'OAuth2 authorization failures by reason',
    ['reason']  # reason = invalid_client|redirect_uri_mismatch|access_denied|server_error
)

oauth2_token_refresh_total = Counter(
    'oauth2_token_refresh_total',
    'OAuth2 token refresh attempts',
    ['result']  # result = success|failure
)

oauth2_token_refresh_failures = Counter(
    'oauth2_token_refresh_failures_total',
    'OAuth2 token refresh failures by reason',
    ['reason']  # reason = invalid_token|expired_token|network_error
)

# Instrumentation in OAuth2 endpoints (auth_service)
```

**Metrics Endpoint Integration:**

**REVIEW FIX: Integrate with main app instead of standalone server (Codex HIGH)**

The original plan created a separate WSGI app on port 9090, but Streamlit apps need metrics integrated into the main process.

**Implementation:**
```python
# apps/web_console/auth/__init__.py (add to existing file)

from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import streamlit as st

def metrics_endpoint():
    """Expose Prometheus metrics at /metrics for scraping.

    Note: This is called from Streamlit's main app, not a separate server.
    Streamlit doesn't support custom HTTP endpoints, so we use st.download_button
    or configure nginx reverse proxy to scrape metrics from a dedicated endpoint.
    """
    # For development/debugging: expose metrics via Streamlit download button
    if st.sidebar.checkbox("Show Metrics (Dev Only)", value=False):
        metrics_data = generate_latest()
        st.download_button(
            label="Download Prometheus Metrics",
            data=metrics_data,
            file_name="metrics.txt",
            mime=CONTENT_TYPE_LATEST
        )

# PRODUCTION: Configure nginx to scrape metrics from /metrics endpoint
# nginx.conf:
#   location /metrics {
#       proxy_pass http://web_console:8501/_stcore/metrics;
#       # Streamlit exposes internal metrics at /_stcore/metrics
#       # We combine with custom metrics via prometheus multiprocess mode
#   }
#
# Enable multiprocess mode for prometheus_client:
#   export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
#   mkdir -p $PROMETHEUS_MULTIPROC_DIR
#
# On app startup (streamlit_app.py):
#   import prometheus_client
#   prometheus_client.values.ValueClass = prometheus_client.values.MultiProcessValue
```

**Alternative: FastAPI Sidecar (Recommended for Production)**

Create a lightweight FastAPI app that runs alongside Streamlit:

```python
# apps/web_console/metrics_server.py (NEW)

from fastapi import FastAPI, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

app = FastAPI()

@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )

# Run with: uvicorn apps.web_console.metrics_server:app --host 0.0.0.0 --port 9090
```

**Docker Compose Integration:**

**REVIEW FIX 2: Both services must mount shared volume (Gemini/Codex HIGH)**

```yaml
# infra/docker-compose.yml
volumes:
  prometheus_multiproc_data:

services:
  web_console:
    # ... existing config
    environment:
      # REVIEW FIX 2: Enable multiprocess mode for main app (Codex HIGH)
      - PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
    volumes:
      # REVIEW FIX 2: Mount shared volume so sidecar can read metrics (Gemini HIGH)
      - prometheus_multiproc_data:/tmp/prometheus_multiproc
    # REVIEW FIX 2: Cleanup on startup (Gemini MEDIUM)
    entrypoint: >
      sh -c "rm -rf /tmp/prometheus_multiproc/* &&
             exec streamlit run apps/web_console/streamlit_app.py"

  web_console_metrics:
    build: .
    command: uvicorn apps.web_console.metrics_server:app --host 0.0.0.0 --port 9090
    ports:
      - "9090:9090"
    environment:
      - PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
    volumes:
      - prometheus_multiproc_data:/tmp/prometheus_multiproc
    depends_on:
      - web_console
```

**App Startup Configuration:**
```python
# apps/web_console/streamlit_app.py (or __init__.py startup)

import os
import prometheus_client

# REVIEW FIX 2: Enable multiprocess mode (Codex HIGH)
if os.getenv('PROMETHEUS_MULTIPROC_DIR'):
    # Use multiprocess value class for gauge/counter aggregation
    prometheus_client.values.ValueClass = prometheus_client.values.MultiProcessValue
    # Cleanup old metric files on startup (Gemini MEDIUM)
    multiproc_dir = os.getenv('PROMETHEUS_MULTIPROC_DIR')
    if os.path.exists(multiproc_dir):
        import shutil
        shutil.rmtree(multiproc_dir, ignore_errors=True)
        os.makedirs(multiproc_dir, exist_ok=True)
```

**Prometheus Scrape Config:**
```yaml
# infra/prometheus/prometheus.yml
scrape_configs:
  - job_name: 'web_console'
    static_configs:
      - targets: ['web_console_metrics:9090']
    scrape_interval: 15s
```

**Implementation Plan:**

**REVIEW FIX: Revised effort estimate 5h → 7h (Codex feedback)**

1. **Create FastAPI metrics sidecar** (1h)
   - Create `apps/web_console/metrics_server.py`
   - Add FastAPI `/metrics` endpoint
   - Configure docker-compose for web_console_metrics service
   - Update prometheus scrape config

2. **Instrument IdP health monitoring** (1.5h)
   - Add 7 metrics to `apps/web_console/auth/idp_health.py`
   - Configure custom histogram buckets
   - Instrument `check_health()` with counters + reset logic
   - Test metrics emission with manual health check

3. **Instrument mTLS fallback authentication** (2h)
   - Add 10 metrics to `apps/web_console/auth/mtls_fallback.py` (includes alert-compatible failures counter)
   - Implement CN label allowlist bucketing (cardinality protection)
   - Guard expiry gauge with allowlist check (prevent cardinality bypass)
   - Instrument certificate validation with counters
   - Instrument CRL fetching with histogram + dual counters (_total + _failures_total)
   - Add CRL cache age gauge updates
   - Test metrics with valid/invalid certificates

4. **Instrument session management** (1.5h)
   - Add 5 metrics to session management modules
   - Implement `update_active_sessions_gauge()` with SCAN + timeout (skip on timeout, no partial data)
   - Initialize session_secret_rotation gauge to prevent alert on startup
   - Instrument session creation/validation
   - Add periodic task for gauge updates (every 30s)
   - Test with session create/validate/cleanup

5. **Instrument OAuth2 flow** (0.5h)
   - Add 4 metrics to OAuth2 endpoints
   - Track authorization + token refresh attempts
   - Test with successful/failed OAuth2 flows

6. **Docker/Multiprocess Integration** (0.5h)
   - Add shared volume `prometheus_multiproc_data` to docker-compose
   - Configure `web_console` service with PROMETHEUS_MULTIPROC_DIR env
   - Add multiprocess mode initialization to app startup
   - Add cleanup entrypoint (`rm -rf /tmp/prometheus_multiproc/*`)
   - Verify sidecar can read metrics from main app

7. **Integration testing** (0.5h)
   - Verify all 26 metrics exposed at `/metrics`
   - Check Prometheus scraping successfully
   - Validate alert rules fire correctly (especially CRL failures alert)
   - Test Grafana dashboard queries
   - Verify no cardinality explosion from unauthorized CNs
   - Test SCAN timeout behavior (no partial gauge updates)

**Total Estimated Effort:** 7 hours (26 metrics total)

**Priority:** HIGH (monitoring blind without this, but core functionality working)

**Follow-up Task:** Create `P2T3-Phase3_Component6_PrometheusMetrics.md` task document

---

## Component 7: Documentation & Architecture (6 hours - Gemini Review Bump)

### 7.1 Architecture Concepts (3 hours - Quality Bump)

**Purpose:** Explain OAuth2/OIDC architecture for developers new to the codebase.

**Documents to Create:**

1. **OAuth2/OIDC Architecture** (`docs/CONCEPTS/oauth2-oidc-architecture.md`)
   - Flow diagram: Authorization Code Flow with PKCE
   - Component interactions: Streamlit → Auth Service → Auth0 → Callback
   - Security model: State/nonce CSRF protection, token validation
   - Session lifecycle: Creation, refresh, idle timeout, logout

2. **Session Security Model** (`docs/CONCEPTS/session-security-model.md`)
   - Encryption: Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256)
   - Storage: Redis with TTL-based expiration
   - Rotation: Dual-key support for zero-downtime key rotation
   - Security properties: HttpOnly cookies, SameSite=Lax, Secure flag

3. **Auth0 Integration Guide** (`docs/CONCEPTS/auth0-integration.md`)
   - Auth0 application configuration (callback URLs, CORS, JWKS)
   - JWKS validation flow (RS256 signature verification)
   - Token refresh flow (rotation with grace period)
   - IdP health monitoring and fallback

**Files to Create:**
- `docs/CONCEPTS/oauth2-oidc-architecture.md`
- `docs/CONCEPTS/session-security-model.md`
- `docs/CONCEPTS/auth0-integration.md`

---

### 7.2 Developer Guide (1.5 hours)

**Purpose:** Help developers integrate new pages/features with OAuth2 authentication.

**Guide:** `docs/GETTING_STARTED/OAUTH2_DEVELOPER_GUIDE.md`

**Contents:**
- Quick start: Adding authentication to a new Streamlit page
- Session management: Accessing current user, checking auth status
- Error handling: Session expiration, IdP errors
- Testing: Mocking OAuth2 in tests, local development setup
- Security checklist: CSP nonces, cookie flags, logout handling

**Code Examples:**
```python
# Check authentication status
from libs.platform.web_console_auth import require_auth, get_current_user

@require_auth
def my_protected_page():
    user = get_current_user()
    st.write(f"Welcome, {user['name']}")
```

**Files to Create:**
- `docs/GETTING_STARTED/OAUTH2_DEVELOPER_GUIDE.md`

---

### 7.3 ADR Updates + Peer Review (1.5 hours - Codex Explicit Review Gate)

**Scope:** Update ADR-015 with final implementation notes and lessons learned.

**Updates to ADR-015** (`docs/ADRs/ADR-015-auth0-idp-selection.md`):

**New Section: Implementation Notes**
- mTLS fallback: Chosen 3-failure threshold (balances responsiveness vs flapping)
- Session encryption: Fernet chosen for simplicity over JWT (session data opaque to client)
- CSP nonces: Base64 chosen over hex (CSP spec compliant, 16-byte entropy)
- Nginx integration: Templating for CSP report-only mode (safe rollout)

**New Section: Lessons Learned**
- IdP health checks: Exponential backoff prevents alert fatigue
- Session cleanup: SCAN + DEL pattern prevents Redis blocking
- Key rotation: Dual-key support essential for zero-downtime rotation
- Testing: Integration tests for mTLS fallback required Docker compose

**Files to Modify:**
- `docs/ADRs/ADR-015-auth0-idp-selection.md` - Add sections
- `docs/INDEX.md` - Add links to new CONCEPTS and guides

**Testing (Codex Review - Documentation QA Gate):**
- [ ] **Link checker:** `make check-docs-links` passes (0 broken links)
- [ ] **Markdown lint:** `markdownlint docs/` passes (formatting consistency)
- [ ] **Code examples:** All developer guide examples execute successfully in clean environment
- [ ] **ADR accuracy:** Peer review confirms ADR reflects actual implementation decisions
- [ ] **Peer review:** Platform team member reviews all CONCEPT docs for clarity
- [ ] **CI integration:** Link checker + markdown lint run in CI pipeline

---

## Pre-flight Dependencies Checklist

**CRITICAL:** Verify Components 1-5 artifacts before starting Component 6 implementation (Codex Review: LOW).

### Component 1: Auth0 Configuration ✅
- [ ] Auth0 tenant configured: `dev-qlib-trading.us.auth0.com`
- [ ] Application created: "Qlib Trading Platform Web Console"
- [ ] Callback URLs configured: `https://localhost:8443/auth/callback`
- [ ] CORS origins configured: `https://localhost:8443`
- [ ] Environment variables set: `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`

### Component 2: OAuth2 Core Flow ✅
- [ ] OAuth2 handler implemented: `apps/auth_service/oauth2/handler.py`
- [ ] PKCE implemented: S256 code challenge generation
- [ ] State validation: Redis-backed CSRF protection
- [ ] Token exchange: `/callback` endpoint functional
- [ ] Integration test: `test_oauth2_login_flow_e2e.py` passes

### Component 3: Session Management ✅
- [ ] Session encryption: Fernet symmetric encryption working
- [ ] Redis storage: Sessions persisted with TTL
- [ ] Cookie handling: HttpOnly, Secure, SameSite=Lax flags set
- [ ] Session refresh: Token refresh endpoint functional
- [ ] Dual-key support: Zero-downtime key rotation tested

### Component 4: Nginx Reverse Proxy ✅
- [ ] Nginx configured: `apps/web_console/nginx/nginx-oauth2.conf.template`
- [ ] TLS termination: HTTPS on port 8443 working
- [ ] Proxy headers: X-Forwarded-For, X-Real-IP, X-Forwarded-Proto set
- [ ] Auth endpoints proxied: `/auth/*` → `auth_service:8001`
- [ ] mTLS configured: `ssl_verify_client optional` for fallback support

### Component 5: CSP Hardening ✅
- [ ] CSP middleware: `apps/auth_service/middleware/csp_middleware.py` deployed
- [ ] Nonce generation: Base64-encoded 16-byte nonces per request
- [ ] CSP report endpoint: `/csp-report` receiving violations
- [ ] HTTPException handling: CSP headers on error responses (404, 401, 429)
- [ ] Nginx integration: CSP_REPORT_ONLY env var templating working

**Validation Commands:**
```bash
# Verify Auth0 config
grep -E "AUTH0_(DOMAIN|CLIENT_ID)" .env

# Verify OAuth2 flow works
curl -I https://localhost:8443/auth/login  # Should 302 to Auth0

# Verify session storage
redis-cli KEYS "oauth2:session:*" | wc -l  # Should show active sessions

# Verify Nginx proxy
curl -I https://localhost:8443/  # Should return 200 with CSP headers

# Verify CSP middleware
curl https://localhost:8443/auth/health -I | grep "Content-Security-Policy"
```

**If any checklist item fails:** Do NOT proceed with Component 6 implementation. Fix dependencies first.

---

## Dependencies & Sequencing

### External Dependencies
- Auth0 tenant configuration (COMPLETED - Components 1-2)
- Redis for session storage (COMPLETED - Component 3)
- Nginx reverse proxy (COMPLETED - Component 5)
- Prometheus/Grafana infrastructure (EXISTING)

### Internal Dependencies
- IdP health check framework (COMPLETED - Component 1)
- Session manager (COMPLETED - Component 3)
- CSP middleware (COMPLETED - Component 5)

### Sequencing (Combined Implementation - Revised)
1. **Day 1 (8h):** mTLS fallback implementation
   - Implement `mtls_fallback.py` with feature flag + hysteresis (4h)
   - Enhance `idp_health.py` with exponential backoff (1h)
   - Integrate fallback mode into `app.py` (1h)
   - Write unit + integration tests (2h)

2. **Day 2 (8h):** Runbooks + Monitoring + Scripts
   - Write 4 runbooks (auth0-idp-outage, session-key-rotation, oauth2-session-cleanup, mtls-cert-management) (4h)
   - Create session cleanup script with SCAN + UNLINK + batching (1.5h)
   - Create mTLS disable script (0.5h)
   - Prometheus alerts (4 alerts with severity + runbook_url) (1h)
   - Grafana dashboard + provisioning config (1h)

3. **Day 3 (4h):** Documentation + QA
   - Write 3 CONCEPT docs (oauth2-oidc, session-security, auth0-integration) (2h)
   - Write developer guide with code examples (1h)
   - Update ADR-015 implementation notes + INDEX.md (0.5h)
   - Documentation QA: link checker, markdown lint, peer review (0.5h)

---

## Testing Strategy

### Unit Tests
- `test_mtls_fallback.py` - Fallback activation/recovery logic
- `test_idp_health.py` (updated) - Fallback trigger threshold
- `test_session_cleanup_script.py` - Safe SCAN + DEL pattern

### Integration Tests
- `test_mtls_fallback_e2e.py` - End-to-end fallback flow
- `test_prometheus_alerts.py` - Alert triggering simulation
- `test_session_cleanup_redis.py` - Actual Redis cleanup

### Manual Testing
- Simulate Auth0 outage (block DNS), verify fallback activation
- Run session cleanup script in test environment
- Trigger Prometheus alerts, verify Grafana dashboard updates
- Validate all documentation links work

---

## Success Criteria

### Component 6 (mTLS Fallback + Runbooks)
- [ ] mTLS fallback activates within 30s of IdP sustained outage (3 failures)
- [ ] Auto-recovery works when IdP healthy again
- [ ] Session cleanup script deletes 10k+ sessions without blocking Redis
- [ ] Prometheus alerts trigger and link to runbooks
- [ ] Grafana dashboard shows real-time OAuth2 metrics

### Component 7 (Documentation)
- [ ] All documentation links valid (0 broken links)
- [ ] Developer guide code examples execute successfully
- [ ] CONCEPT docs explain architecture clearly (peer review)
- [ ] ADR-015 reflects actual implementation (diff review)

### Combined Acceptance
- [ ] Dual review approved (Gemini + Codex)
- [ ] CI passes (linters, tests, link checker)
- [ ] Runbooks successfully executed in test environment

---

## Risk Mitigation

### High Risk: mTLS Fallback Complexity
- **Mitigation:** Thorough integration tests with Docker compose
- **Fallback:** Feature flag to disable mTLS fallback if issues found

### Medium Risk: Session Cleanup Script Safety
- **Mitigation:** Dry-run mode, SCAN pattern (never KEYS), rate limiting
- **Fallback:** Manual Redis CLI cleanup if script fails

### Low Risk: Documentation Drift
- **Mitigation:** Link checker in CI, code example validation
- **Fallback:** Quarterly documentation review cadence

---

## Rollout Plan

**⚠️ CRITICAL (Codex): mTLS fallback is INCIDENT-ONLY, NOT default-enabled in production**

### Phase 1: Staging Deployment
1. Deploy with `ENABLE_MTLS_FALLBACK=false` (feature flag disabled)
2. Test IdP health monitoring, Prometheus alerts
3. Manually test session cleanup script
4. Test cert expiry/CRL validation logic with synthetic certs

### Phase 2: mTLS Fallback Validation (Staging Only)
1. **Temporarily** enable mTLS fallback in staging: `ENABLE_MTLS_FALLBACK=true`
2. Simulate Auth0 outage (DNS block), verify fallback activates
3. Verify hysteresis exit (5 successes + 5-min stability)
4. Test disable script (`scripts/disable_mtls_fallback.sh`)
5. Monitor for 48h, verify no false positives
6. **Disable fallback after validation:** `ENABLE_MTLS_FALLBACK=false`

### Phase 3: Production Deployment (Fallback DISABLED)
1. Deploy with `ENABLE_MTLS_FALLBACK=false` (default: disabled, Codex CRITICAL fix)
2. Monitor IdP health check metrics (auto-monitoring only, no fallback)
3. Keep runbooks accessible for on-call engineers
4. **Change Control Gate:** mTLS fallback enablement requires:
   - Authenticated incident (Auth0 outage >30min confirmed)
   - Change record approval (SRE + Security teams)
   - Post-incident disable within 4h of IdP recovery
   - Incident postmortem documenting fallback usage

---

## File Manifest

### New Files (16 total)
**Code (3):**
- `apps/web_console/auth/mtls_fallback.py`
- `tests/apps/web_console/auth/test_mtls_fallback.py`
- `scripts/clear_oauth2_sessions.py`

**Scripts (1):**
- `scripts/disable_mtls_fallback.sh` - **NEW:** Emergency mTLS disable

**Runbooks (4):**
- `docs/RUNBOOKS/auth0-idp-outage.md`
- `docs/RUNBOOKS/session-key-rotation.md`
- `docs/RUNBOOKS/oauth2-session-cleanup.md`
- `docs/RUNBOOKS/mtls-certificate-management.md` - **NEW:** Admin cert provisioning + validation

**Monitoring (2):**
- `infra/prometheus/alerts/oauth2.yml`
- `infra/grafana/dashboards/oauth2-sessions.json`

**Documentation (3):**
- `docs/CONCEPTS/oauth2-oidc-architecture.md`
- `docs/CONCEPTS/session-security-model.md`
- `docs/CONCEPTS/auth0-integration.md`

**Guides (1):**
- `docs/GETTING_STARTED/OAUTH2_DEVELOPER_GUIDE.md`

**Planning (2):**
- `docs/TASKS/P2T3-Phase3_Component6-7_Plan.md` (this file)
- `tests/apps/web_console/auth/docker-compose.mtls-test.yml` (Codex R3 MEDIUM: mTLS integration test config)

### Modified Files (9 total - Codex HIGH: reconciled counts)
**Code (3):**
- `apps/web_console/auth/idp_health.py` - Hysteresis + exponential backoff
- `apps/web_console/app.py` - Fallback mode check + feature flag guard
- `apps/web_console/auth/__init__.py` - Export fallback functions

**Infrastructure (3):**
- `infra/prometheus/prometheus.yml` - Load oauth2.yml alerts
- `docker-compose.yml` - Mount Grafana dashboard
- `infra/grafana/provisioning/dashboards/dashboards.yaml` - Auto-load oauth2-sessions.json

**Configuration (1):**
- `.env.example` - Add `ENABLE_MTLS_FALLBACK=false` (default disabled)

**Documentation (2):**
- `docs/ADRs/ADR-015-auth0-idp-selection.md` - Implementation notes + lessons learned
- `docs/INDEX.md` - Add new CONCEPTS, guides, runbooks links

---

## Estimated Hours Breakdown

| Task | Hours | Notes |
|------|-------|-------|
| **Component 6: mTLS Fallback + Runbooks** | **14** | |
| mTLS fallback implementation | 4 | Core logic + feature flag + hysteresis |
| IdP health enhancement | 1 | Exponential backoff + hysteresis trigger |
| App integration | 1 | Fallback mode check + feature flag guard |
| mTLS fallback tests | 2 | Unit + integration + negative cases |
| Runbooks (4 documents) | 4 | Auth0 outage, key rotation, session cleanup, mTLS certs |
| Session cleanup script | 1.5 | SCAN + UNLINK + batching + rate-limiting |
| mTLS disable script | 0.5 | Emergency kill switch |
| Prometheus alerts | 1 | 4 alerts with severity + runbook_url |
| Grafana dashboard | 1 | OAuth2 metrics + provisioning config |
| **Component 7: Documentation** | **6** | |
| CONCEPT docs (3 files) | 2 | OAuth2 architecture, session security, Auth0 integration |
| Developer guide | 1 | OAuth2 integration examples with code |
| ADR updates + INDEX | 0.5 | Implementation notes, lessons learned |
| .env.example update | 0.5 | Add ENABLE_MTLS_FALLBACK flag |
| Documentation QA | 0.5 | Link checker + markdown lint |
| Peer review | 1.5 | Platform team review of all CONCEPT docs |
| **TOTAL** | **20** | Component 6: 14h, Component 7: 6h |

---

## Notes

- **Combining rationale:** Component 7 is pure documentation (no code review), efficient to bundle with C6
- **mTLS fallback:** Considered optional but critical for production resilience
- **Documentation:** Essential for maintainability and onboarding new developers
- **Runbooks:** Proven valuable during past incidents (kill-switch activation, etc.)

---

# IMPLEMENTATION STATUS

**Status:** ✅ CODE COMPLETE
**Date Completed:** 2025-11-26
**Code Reviews:** Gemini ✅ + Codex ✅ (2 iterations)

## Completed Work

### Prometheus Metrics (24/26 metrics implemented)

**Implementation Files:**
1. `apps/web_console/metrics_server.py` (NEW) - FastAPI metrics sidecar with multiprocess support
2. `apps/web_console/auth/idp_health.py` (MODIFIED) - 7 IdP health monitoring metrics
3. `apps/web_console/auth/mtls_fallback.py` (NEW) - 10 mTLS fallback authentication metrics
4. `apps/web_console/auth/__init__.py` (MODIFIED) - 3 session + 4 OAuth2 flow metrics

**Metrics Breakdown:**
- **IdP Health (7):** health_checks_total, consecutive_failures/successes, fallback_mode, stability_period, check_duration, failures_total
- **mTLS Fallback (10):** auth_total, auth_failures_total, cert_not_after, crl_fetch_total, crl_fetch_failures, crl_last_update, crl_cache_age, crl_fetch_duration, cert_validation_duration, active_admin_sessions
- **Session Management (3):** session_created, signature_failures, active_sessions_count
- **OAuth2 Flow (4):** authorization_total, authorization_failures, token_refresh_total, token_refresh_failures

**Metrics Removed After Review:**
- `session_secret_last_rotation_timestamp` - Resets on deployment (alert would never fire)
- `session_cleanup_failures_total` - No cleanup code in Streamlit (Redis TTL auto-expires)

### Review Fixes Applied (9 total across 2 iterations)

**Iteration 1 - Gemini + Codex Initial Review:**
1. **CRITICAL:** Removed `session_secret_last_rotation_timestamp` - resets on deployment
2. **CRITICAL:** Removed `session_cleanup_failures_total` - no cleanup code exists
3. **HIGH:** Implemented `active_sessions_count` with Redis SCAN + 60s throttling
4. **HIGH:** Documented `token_refresh_*` as FastAPI-only (not Streamlit scope)
5. **MEDIUM:** Added IdP health check throttling with `should_check_now()`
6. **MEDIUM:** Improved session signature failure reason tracking (exception type mapping)

**Iteration 2 - Codex Follow-up Review:**
7. **HIGH:** Fixed Redis DB mismatch - added `db=1` to match session store
8. **MEDIUM:** Moved `_update_active_sessions_count()` outside `oauth2_logged` guard for continuous refresh
9. **LOW:** Updated documentation to reflect 3 implemented + 2 removed metrics

### Alert Compatibility (12/14 alerts functional)

**Functional Alerts (12):**
- IdP Health (5): HealthCheckFailureRate, FallbackModeActive, HealthCheckDurationHigh, StabilityPeriodStuck, ConsecutiveFailuresHigh
- mTLS Fallback (5): AuthFailureRate, CertExpiringSoon, CRLFetchFailures, CRLStale, CertValidationSlow
- Session (2): SessionSignatureFailures, ActiveSessionsHigh

**Non-Functional Alerts (2):**
- `OAuth2SessionSecretRotationOverdue` - metric removed (resets on deployment)
- `OAuth2SessionCleanupFailures` - metric removed (no cleanup code)

### Files Modified (19 total)

**New Files (13):**
- `apps/web_console/auth/mtls_fallback.py` - mTLS fallback validator
- `apps/web_console/metrics_server.py` - FastAPI metrics sidecar
- `docs/CONCEPTS/oauth2-mtls-fallback-architecture.md`
- `docs/RUNBOOKS/auth0-idp-outage.md`
- `docs/RUNBOOKS/mtls-fallback-admin-certs.md`
- `docs/RUNBOOKS/oauth2-session-cleanup.md`
- `docs/RUNBOOKS/session-key-rotation.md`
- `infra/grafana/dashboards/oauth2-sessions-spec.md`
- `infra/prometheus/alerts/oauth2.yml`
- `scripts/disable_mtls_fallback.sh`

**Modified Files (6):**
- `apps/web_console/auth/__init__.py` - Added session + OAuth2 metrics
- `apps/web_console/auth/idp_health.py` - Added 7 IdP health metrics
- `.env.example` - Added mTLS config vars
- `.gitignore` - Added prometheus_multiproc_data
- `requirements.txt` - Added prometheus_client

# OAuth2/OIDC Authentication with mTLS Fallback Architecture

**Component:** P2T3 Phase 3 - Web Console Authentication

**Status:** Implemented

**Related ADR:** ADR-015

**Last Updated:** 2025-11-26

---

## Overview

This document describes the architecture of the OAuth2/OIDC authentication system with automatic mTLS fallback for the web console. The system provides secure, production-ready authentication with built-in resilience against Auth0 IdP outages.

### Key Features

1. **Primary Authentication:** OAuth2/OIDC via Auth0
2. **Fallback Authentication:** mTLS client certificates (admin-only)
3. **Automatic Failover:** Hysteresis-based health monitoring
4. **Zero Downtime:** Graceful degradation during IdP outages
5. **Security:** Fail-secure design, CRL validation, certificate lifetime enforcement

---

## System Components

### Component 1: OAuth2/OIDC Authentication

**Purpose:** Normal authentication flow using Auth0 as Identity Provider

**Flow:**
```
User → Web Console → Auth0 Authorization → Auth0 Token Exchange → Session Creation
```

**Implementation:** `apps/web_console/auth/__init__.py:_oauth2_auth()`

**Key Features:**
- Authorization Code Flow with PKCE
- Refresh token support (24-hour lifetime)
- Session cookies (1-hour TTL)
- CSRF protection

---

### Component 2: IdP Health Monitor

**Purpose:** Detect Auth0 IdP availability and trigger fallback mode

**File:** `apps/web_console/auth/idp_health.py`

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│ IdPHealthChecker                                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐       ┌─────────────────────────┐       │
│  │ Health Check │──────▶│ .well-known/            │       │
│  │ (every 10s)  │       │ openid-configuration    │       │
│  └──────────────┘       └─────────────────────────┘       │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────────────────────────────┐             │
│  │ Hysteresis State Machine                 │             │
│  ├──────────────────────────────────────────┤             │
│  │ ENTRY: 3 consecutive failures (30s)      │             │
│  │ EXIT:  5 consecutive successes + 5min    │             │
│  └──────────────────────────────────────────┘             │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────────────────────────────┐             │
│  │ Fallback Mode State: Boolean             │             │
│  │ - false: Normal OAuth2 Active            │             │
│  │ - true:  mTLS Fallback Active            │             │
│  └──────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
```

**Hysteresis Benefits:**
- **Anti-flapping:** Prevents rapid mode switching on transient failures
- **Stability:** Requires sustained recovery before exiting fallback
- **Exponential backoff:** Reduces monitoring overhead during outages (10s → 60s)

**Configuration:**
```python
IdPHealthChecker(
    auth0_domain="trading-platform.us.auth0.com",
    normal_check_interval_seconds=10,     # Normal polling: 10s
    fallback_check_interval_seconds=60,   # Fallback polling: 60s (exponential backoff)
    failure_threshold=3,                   # Enter fallback after 3 failures
    success_threshold=5,                   # Exit fallback after 5 successes
    stable_period_seconds=300,             # + 5min stable period
)
```

---

### Component 3: mTLS Fallback Authentication

**Purpose:** Emergency admin-only authentication during Auth0 outages

**File:** `apps/web_console/auth/mtls_fallback.py`

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│ MtlsFallbackValidator                                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐       ┌─────────────────────────┐       │
│  │ nginx        │──────▶│ X-SSL-Client-Cert       │       │
│  │ (mTLS)       │       │ (PEM-encoded)           │       │
│  └──────────────┘       └─────────────────────────┘       │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────────────────────────────┐             │
│  │ Certificate Validation Pipeline          │             │
│  ├──────────────────────────────────────────┤             │
│  │ 1. X-SSL-Client-Verify = SUCCESS?        │             │
│  │ 2. Parse certificate from PEM             │             │
│  │ 3. Lifetime ≤ 7 days? (enforced)         │             │
│  │ 4. Not expired? (notBefore/notAfter)     │             │
│  │ 5. CN in admin allowlist?                │             │
│  │ 6. Check CRL (fail-secure)               │             │
│  │ 7. Compute fingerprint (audit)           │             │
│  └──────────────────────────────────────────┘             │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────────────────────────────┐             │
│  │ CRL Cache (1-hour TTL)                    │             │
│  ├──────────────────────────────────────────┤             │
│  │ - Fetch from distribution point          │             │
│  │ - Validate freshness (<24h)               │             │
│  │ - Check revocation status                 │             │
│  │ - Fail-secure on error                    │             │
│  └──────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
```

**Security Controls:**

| Control | Enforcement | Purpose |
|---------|-------------|---------|
| **Certificate Lifetime** | ≤ 7 days (hard limit) | Limits exposure window if compromised |
| **Admin CN Allowlist** | `MTLS_ADMIN_CN_ALLOWLIST` | Only pre-authorized administrators |
| **CRL Validation** | Fail-secure (reject if CRL unavailable) | Immediate revocation capability |
| **Expiry Warnings** | <24 hours (Prometheus alert) | Proactive rotation |
| **Audit Logging** | All auth attempts logged | Forensics and compliance |

---

### Component 4: Session Management

**Purpose:** Stateful session cookies with Redis storage

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│ Session Cookie (HTTP-only, Secure, SameSite=Lax)           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  session_id → HMAC-SHA256(payload, SESSION_ENCRYPTION_KEY) │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ Redis Key: session:{session_id}                            │
├─────────────────────────────────────────────────────────────┤
│ {                                                           │
│   "user_id": "auth0|123456789",                            │
│   "username": "john.doe@example.com",                      │
│   "auth_method": "oauth2" | "mtls_fallback",              │
│   "created_at": "2025-11-26T10:00:00Z",                    │
│   "last_activity": "2025-11-26T10:30:00Z",                 │
│   "expires_at": "2025-11-26T11:00:00Z",                    │
│   "access_token": "...",  // OAuth2 only                   │
│   "refresh_token": "...", // OAuth2 only                   │
│   "cert_cn": "admin.local", // mTLS only                   │
│   "cert_fingerprint": "abc123..." // mTLS only             │
│ }                                                           │
│ TTL: 3600 seconds (1 hour)                                 │
└─────────────────────────────────────────────────────────────┘
```

**Session Secret Rotation:**
- **Grace Period:** Multi-key verification during rotation
- **Zero Downtime:** Old sessions remain valid during grace period (24h)
- **Compliance:** 90-day mandatory rotation (PCI DSS, SOC2)

---

## Authentication Flow Diagrams

### Normal Operation (OAuth2)

```
┌──────┐                  ┌─────────────┐                  ┌────────┐
│ User │                  │ Web Console │                  │ Auth0  │
└──┬───┘                  └──────┬──────┘                  └───┬────┘
   │                              │                             │
   │ 1. GET /login               │                             │
   │────────────────────────────▶│                             │
   │                              │                             │
   │                              │ 2. Check IdP Health         │
   │                              │    (fallback_mode = false)  │
   │                              │                             │
   │                              │ 3. Redirect to Auth0        │
   │                              │    /authorize               │
   │                              │────────────────────────────▶│
   │                              │                             │
   │ 4. User authenticates        │                             │
   │◀───────────────────────────────────────────────────────────│
   │                              │                             │
   │ 5. Callback with auth code   │                             │
   │────────────────────────────▶│                             │
   │                              │                             │
   │                              │ 6. Exchange code for tokens │
   │                              │────────────────────────────▶│
   │                              │                             │
   │                              │ 7. Tokens (access + refresh)│
   │                              │◀────────────────────────────│
   │                              │                             │
   │                              │ 8. Create session in Redis  │
   │                              │    (TTL: 1 hour)            │
   │                              │                             │
   │ 9. Set session cookie        │                             │
   │◀────────────────────────────│                             │
   │                              │                             │
   │ 10. Authenticated access     │                             │
   │────────────────────────────▶│                             │
```

### Fallback Mode (mTLS)

```
┌──────┐                  ┌─────────────┐                  ┌────────┐
│ Admin│                  │ Web Console │                  │ CRL    │
└──┬───┘                  └──────┬──────┘                  └───┬────┘
   │                              │                             │
   │ 1. GET /login               │                             │
   │────────────────────────────▶│                             │
   │                              │                             │
   │                              │ 2. Check IdP Health         │
   │                              │    (fallback_mode = true)   │
   │                              │                             │
   │                              │ 3. Display fallback banner  │
   │                              │    (mTLS required)          │
   │◀────────────────────────────│                             │
   │                              │                             │
   │ 4. Browser prompts for       │                             │
   │    client certificate        │                             │
   │    (admin selects cert)      │                             │
   │                              │                             │
   │ 5. HTTPS request with        │                             │
   │    client cert in TLS        │                             │
   │────────────────────────────▶│                             │
   │                              │                             │
   │                              │ 6. nginx validates cert     │
   │                              │    (sets X-SSL-Client-Cert) │
   │                              │                             │
   │                              │ 7. Validate certificate     │
   │                              │    - Lifetime ≤ 7 days      │
   │                              │    - Not expired            │
   │                              │    - CN in allowlist        │
   │                              │                             │
   │                              │ 8. Fetch CRL                │
   │                              │────────────────────────────▶│
   │                              │                             │
   │                              │ 9. CRL data (cached 1h)     │
   │                              │◀────────────────────────────│
   │                              │                             │
   │                              │ 10. Check revocation status │
   │                              │     (fail-secure)           │
   │                              │                             │
   │                              │ 11. Create session in Redis │
   │                              │     (TTL: 1 hour)           │
   │                              │     auth_method: mtls       │
   │                              │                             │
   │ 12. Set session cookie       │                             │
   │◀────────────────────────────│                             │
   │                              │                             │
   │ 13. Authenticated access     │                             │
   │     (admin-only)             │                             │
   │────────────────────────────▶│                             │
```

---

## Failure Modes & Recovery

### Scenario 1: Auth0 Gradual Degradation

**Timeline:**
- T=0s: First health check failure
- T=10s: Second health check failure
- T=20s: Third health check failure → **Fallback mode activated**
- T=20s-recovery: IdP unavailable, admins use mTLS certificates
- Recovery+0s: First successful health check
- Recovery+10s: Second successful health check
- Recovery+20s: Third successful health check
- Recovery+30s: Fourth successful health check
- Recovery+40s: Fifth successful health check → **Stability period starts**
- Recovery+5min40s: Stability period complete → **Fallback mode deactivated**

**User Impact:**
- Normal users: Cannot authenticate (must wait for recovery)
- Administrators: Can authenticate via mTLS certificates

---

### Scenario 2: CRL Distribution Point Failure

**Failure:** CRL HTTP server unreachable

**Impact:** ALL mTLS authentication rejected (fail-secure)

**Detection:** Prometheus alert `MtlsCrlFetchFailure` (CRITICAL)

**Recovery:**
1. Restart CRL HTTP server (nginx on CA server)
2. Verify CRL accessible via curl
3. Wait for next mTLS auth attempt (CRL fetch retries automatically)

---

### Scenario 3: Certificate Compromise

**Action:** Emergency revocation

**Steps:**
1. Add certificate to CRL (CA server)
2. Publish updated CRL to distribution point
3. Wait for cache refresh (up to 1 hour)
4. Optional: Remove CN from `MTLS_ADMIN_CN_ALLOWLIST` (defense-in-depth)

**Timeline:**
- T=0: Certificate added to CRL
- T=0-1h: CRL cache may serve stale data (certificate still valid)
- T=1h: CRL cache expires, new CRL fetched
- T=1h+: Certificate rejected immediately (revocation effective)

---

## Monitoring & Observability

### Prometheus Metrics

**IdP Health:**
- `oauth2_idp_health_consecutive_failures` - Consecutive health check failures
- `oauth2_idp_health_consecutive_successes` - Consecutive successes
- `oauth2_idp_fallback_mode` - Boolean (1 = fallback active)
- `oauth2_idp_stability_period_active` - Boolean (1 = in stability period)

**mTLS Fallback:**
- `oauth2_mtls_auth_total{cn, result}` - Authentication attempts
- `oauth2_mtls_auth_failures_total{cn, reason}` - Failure reasons
- `oauth2_mtls_cert_not_after_timestamp{cn}` - Certificate expiry timestamp
- `oauth2_mtls_crl_fetch_failures_total` - CRL fetch failures
- `oauth2_mtls_crl_last_update_timestamp` - Last successful CRL fetch

**Sessions (3 metrics - 2 removed after review):**
- `oauth2_session_created_total` - Total sessions created
- `oauth2_active_sessions_count` - Current active sessions
- `oauth2_session_signature_failures_total{reason}` - Signature verification failures
  - NOTE: `session_secret_last_rotation_timestamp` removed (resets on deployment)
  - NOTE: `session_cleanup_failures_total` removed (no cleanup code - Redis TTL handles expiry)

### Grafana Dashboard

**Dashboard:** OAuth2 Sessions & Authentication

**Key Panels:**
- IdP Health Status (green/red indicator)
- Fallback Mode Active (boolean)
- Consecutive Failures/Successes (graphs)
- mTLS Authentication Rate (success vs. failure)
- Admin Certificate Expiry Timeline (table)
- Active Sessions Count (graph)
- Session Signature Failures (by reason)

**Dashboard Spec:** `infra/grafana/dashboards/oauth2-sessions-spec.md`

### Prometheus Alerts

**Alert File:** `infra/prometheus/alerts/oauth2.yml`

**Critical Alerts (3):**
- `MtlsAuthFailureRateHigh` - >10 mTLS failures/minute (possible attack or CRL issues)
- `MtlsCrlFetchFailure` - CRL fetch failed >2x in 5 minutes (blocks all mTLS auth)
- `MtlsCrlTooOld` - CRL not updated in >24 hours (blocks all mTLS auth)

**Warning Alerts (7):**
- `IdPHealthCheckFailed` - 3 consecutive IdP health check failures
- `IdPFallbackModeActive` - mTLS fallback mode activated for >5 minutes
- `MtlsCertificateExpiringSoon` - Admin cert expires within 24 hours
- `SessionSignatureVerificationFailures` - >10 signature failures/minute
- `SessionCountHigh` - Active sessions >1000 (possible DoS or TTL issue)
- `OAuth2AuthorizationFailureRateHigh` - >5 authorization failures/minute
- `OAuth2TokenRefreshFailureRateHigh` - >5 token refresh failures/minute

**Info Alerts (1):**
- `IdPRecoveryInProgress` - IdP health recovered, stability period active

**NOTE:** Session secret rotation alerts (`SessionSecretRotationCritical`, `SessionSecretRotationOverdue`) were removed because the `session_secret_last_rotation_timestamp` metric resets on every deployment. Session secret rotation must be tracked externally via runbook procedures.

---

## Operational Procedures

### Runbooks

1. **Auth0 IdP Outage Response** (`docs/RUNBOOKS/auth0-idp-outage.md`)
   - Detect outage
   - Enable fallback if needed
   - Monitor fallback operation
   - Verify automatic recovery

2. **mTLS Certificate Management** (`docs/RUNBOOKS/mtls-fallback-admin-certs.md`)
   - Issue new admin certificates
   - Rotate expiring certificates (weekly)
   - Revoke compromised certificates
   - Manage admin CN allowlist

3. **OAuth2 Session Cleanup** (`docs/RUNBOOKS/oauth2-session-cleanup.md`)
   - Cleanup expired sessions
   - Mass logout (security incidents)
   - Selective cleanup by user/auth method

4. **Session Key Rotation** (`docs/RUNBOOKS/session-key-rotation.md`)
   - Scheduled 90-day rotation (with grace period)
   - Emergency rotation (key compromise)
   - Post-incident rotation

### Emergency Procedures

**Emergency Disable mTLS Fallback:**
```bash
./scripts/disable_mtls_fallback.sh
# Idempotent, creates backup, audit logging
```

**Emergency Session Cleanup:**
```bash
python3 scripts/clear_oauth2_sessions.py --all
# Mass logout (requires approval)
```

---

## Configuration Reference

### Environment Variables

```bash
# Auth0 Configuration
AUTH0_DOMAIN=trading-platform.us.auth0.com
AUTH0_CLIENT_ID=your_client_id_here
AUTH0_CLIENT_SECRET=your_client_secret_here
AUTH0_API_AUDIENCE=https://api.trading-platform.local

# Session Management
SESSION_COOKIE_NAME=trading_platform_session
# Generate with: python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
SESSION_ENCRYPTION_KEY=your_base64_encoded_32_byte_key_here
SESSION_COOKIE_MAX_AGE=3600  # 1 hour
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_HTTPONLY=true
SESSION_COOKIE_SAMESITE=Lax

# mTLS Fallback (Component 6)
ENABLE_MTLS_FALLBACK=false  # Default: disabled
MTLS_ADMIN_CN_ALLOWLIST=admin.local,emergency-admin.local
MTLS_CRL_URL=http://ca.trading-platform.local/crl/admin-ca.crl
```

### Files

```
apps/web_console/auth/
├── __init__.py            # Main auth logic (OAuth2 + mTLS integration)
├── idp_health.py          # IdP health monitoring with hysteresis
└── mtls_fallback.py       # mTLS certificate validation + CRL caching

scripts/
└── disable_mtls_fallback.sh  # Emergency disable script

infra/
├── prometheus/alerts/oauth2.yml      # Prometheus alert rules
└── grafana/dashboards/
    └── oauth2-sessions-spec.md       # Dashboard specification

docs/RUNBOOKS/
├── auth0-idp-outage.md               # IdP outage response
├── mtls-fallback-admin-certs.md      # Certificate management
├── oauth2-session-cleanup.md         # Session cleanup procedures
└── session-key-rotation.md           # Session secret rotation
```

---

## Security Considerations

### Threat Model

| Threat | Mitigation |
|--------|------------|
| **Auth0 Outage** | mTLS fallback with admin-only access |
| **Certificate Compromise** | 7-day max lifetime, CRL revocation, fail-secure |
| **Session Hijacking** | HMAC-signed cookies, HTTPS-only, SameSite=Lax |
| **Session Fixation** | Regenerate session ID on auth success |
| **CSRF** | CSRF tokens + SameSite cookies |
| **CRL MITM** | HTTPS for CRL distribution point (future enhancement) |
| **Expired Certificates** | Expiry validation enforced, Prometheus alerts |

### Defense-in-Depth

1. **Certificate Validation:**
   - nginx mTLS verification (Layer 1)
   - Application certificate validation (Layer 2)
   - CRL check (Layer 3)
   - Admin CN allowlist (Layer 4)

2. **Session Security:**
   - HMAC signature verification
   - Redis TTL enforcement
   - Session secret rotation
   - Multi-key verification during rotation

3. **Audit Logging:**
   - All mTLS authentication attempts logged
   - Certificate fingerprint + IP + timestamp
   - CRL status recorded
   - Session cleanup operations audited

---

## Related Documentation

- **ADR-015:** OAuth2/OIDC Authentication Architecture (decision record)
- **P2T3 Phase 3 Plan:** `docs/TASKS/P2T3-Phase3_Component6-7_Plan.md` (implementation plan)
- **Runbooks:** `docs/RUNBOOKS/auth0-idp-outage.md` and related
- **Prometheus Alerts:** `infra/prometheus/alerts/oauth2.yml`
- **Grafana Dashboard:** `infra/grafana/dashboards/oauth2-sessions-spec.md`

---

**Version:** 1.0

**Last Updated:** 2025-11-26

**Authors:** Platform Team

**Reviewers:** Security Team, Gemini Planner, Codex Planner

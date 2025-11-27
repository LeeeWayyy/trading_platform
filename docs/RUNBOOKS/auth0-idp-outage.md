# Auth0 IdP Outage Response Runbook

**Purpose:** Respond to Auth0 Identity Provider outages and manage mTLS fallback authentication

**Owner:** @platform-team

**Last Updated:** 2025-11-26

**Related:** ADR-015, P2T3-Phase3_Component6-7_Plan.md, mtls-certificate-management.md

---

## Overview

This runbook guides operators through detecting and responding to Auth0 IdP outages using the automated mTLS fallback authentication system. The fallback provides emergency admin-only access via client certificates when Auth0 is unavailable.

**Estimated Response Time:** 5-10 minutes for fallback activation

**Prerequisites:**
- Admin client certificates pre-distributed to administrators
- `ENABLE_MTLS_FALLBACK=true` in production .env
- CRL (Certificate Revocation List) accessible at configured URL
- Access to server logs (Grafana/Loki)
- Access to Prometheus alerts

---

## System Behavior

### Automatic Hysteresis-Based Fallback

The mTLS fallback system activates **automatically** when the IdP health monitor detects sustained outages:

**Entry Conditions (Fallback Activation):**
- 3 consecutive IdP health check failures (30-second sustained outage)
- Each check timeout: 5 seconds
- Total time to activation: ~30 seconds

**Exit Conditions (Fallback Deactivation):**
- 5 consecutive successful health checks AND
- 5-minute stable period (no failures during this time)
- Exponential backoff: polling changes from 10s → 60s during fallback mode

**Health Check Details:**
- Endpoint: `https://{auth0_domain}/.well-known/openid-configuration`
- Validation: Checks required OIDC fields (issuer, authorization_endpoint, token_endpoint, jwks_uri, userinfo_endpoint)
- Issuer verification: Prevents DNS poisoning by validating issuer matches expected Auth0 tenant

---

## Detection

### Signs of Auth0 IdP Outage

1. **Prometheus Alerts**
   - `IdPHealthCheckFailed` — 3 consecutive IdP health check failures
   - `IdPFallbackModeActive` — mTLS fallback mode activated

2. **User Reports**
   - Users unable to log in via normal OAuth2 flow
   - Login redirects fail or timeout
   - "Service Unavailable" errors from Auth0

3. **Application Logs**
   ```bash
   # Check for IdP health check failures
   kubectl logs -l app=web-console --tail=100 | grep "IdP health check failed"

   # Check for fallback mode activation
   kubectl logs -l app=web-console --tail=100 | grep "entering fallback mode"
   ```

4. **Grafana Dashboard**
   - Navigate to "OAuth2 Sessions" dashboard
   - Check "IdP Health Status" panel
   - Verify "Fallback Mode" indicator is active

---

## Response Workflow

### Phase 1: Confirm Outage (< 1 minute)

#### Step 1: Verify Auth0 Status

```bash
# Check Auth0 status page
curl -I https://status.auth0.com

# Manually test OIDC discovery endpoint
curl -v https://YOUR-AUTH0-DOMAIN/.well-known/openid-configuration
```

**Expected during outage:**
- HTTP 5xx errors (500, 502, 503, 504)
- Connection timeout
- DNS resolution failure

#### Step 2: Check IdP Health Monitor State

```bash
# Check logs for last health check status
kubectl logs -l app=web-console --tail=50 | grep "IdP health check"

# Expected output during outage:
# IdP health check failed | consecutive_failures: 3 | fallback_mode: true
```

#### Step 3: Verify Fallback Mode Active

```bash
# Check for fallback mode activation log
kubectl logs -l app=web-console --tail=100 | grep "entering fallback mode"

# Expected output:
# IdP outage detected: entering fallback mode (hysteresis entry) | consecutive_failures: 3
```

---

### Phase 2: Enable mTLS Fallback (if not already enabled)

**NOTE:** If `ENABLE_MTLS_FALLBACK=false` in production, the health monitor will detect the outage but **will not** activate fallback authentication. You must manually enable the feature flag.

#### Step 1: Check Current Feature Flag State

```bash
# SSH to production server
ssh user@prod-web-console-01

# Check current .env setting
grep "ENABLE_MTLS_FALLBACK" /app/.env
```

#### Step 2: Enable mTLS Fallback (if disabled)

```bash
# Use emergency disable script to enable fallback
./scripts/disable_mtls_fallback.sh --enable

# Verify change
grep "ENABLE_MTLS_FALLBACK" /app/.env
# Expected: ENABLE_MTLS_FALLBACK=true
```

#### Step 3: Restart Web Console Service

```bash
# Restart to load new environment variable
kubectl rollout restart deployment/web-console

# Wait for rollout to complete (30-60 seconds)
kubectl rollout status deployment/web-console

# Verify service healthy
curl -k https://YOUR-DOMAIN/health
```

---

### Phase 3: Notify Administrators (< 5 minutes)

#### Step 1: Send Incident Notification

**Slack Template:**
```
:warning: **Auth0 IdP Outage Detected**

**Status:** mTLS fallback mode activated
**Impact:** Normal user logins unavailable; admin access via client certificates only
**Action Required:**
1. Administrators: Use client certificates for emergency access
2. Users: Wait for Auth0 recovery (estimated: TBD)

**Monitoring:**
- Prometheus alert: IdPFallbackModeActive
- Grafana dashboard: OAuth2 Sessions
- Auth0 status: https://status.auth0.com

**Next Update:** Every 15 minutes
```

#### Step 2: Email Administrator Instructions

**Email Template:**
```
Subject: [URGENT] Auth0 IdP Outage - Admin Access via Client Certificate

Body:
Auth0 IdP is currently unavailable. The web console has activated mTLS fallback mode.

TO ACCESS THE SYSTEM:
1. Ensure your admin client certificate is installed in your browser
2. Navigate to: https://YOUR-DOMAIN
3. Browser will prompt for certificate selection - choose your admin cert
4. You will be authenticated as admin (CN from your certificate)

FALLBACK MODE RESTRICTIONS:
- Only administrators with pre-distributed client certificates can access
- Certificate must not be expired (max lifetime: 7 days)
- Certificate must not be revoked (checked against CRL)
- Session duration: 1 hour (same as normal OAuth2)

MONITORING:
- IdP health checks run every 60 seconds during fallback mode
- Automatic recovery when Auth0 returns (5 consecutive successes + 5min stable)
- Dashboard: https://grafana.YOUR-DOMAIN/d/oauth2-sessions

SUPPORT:
- Contact: platform-team@company.com
- On-call: Check PagerDuty rotation
```

---

### Phase 4: Monitor Fallback Operation (ongoing)

#### Step 1: Watch mTLS Authentication Logs

```bash
# Monitor fallback authentication attempts
kubectl logs -f -l app=web-console | grep "mTLS fallback"

# Expected logs for successful admin auth:
# mTLS fallback authentication successful | cn: admin.trading-platform.local | fingerprint: abc123... | crl_status: valid
```

#### Step 2: Check for Authentication Failures

```bash
# Look for failed authentication attempts
kubectl logs -l app=web-console --tail=200 | grep -E "Certificate (revoked|expired|not in allowlist)"

# Investigate excessive failures
kubectl logs -l app=web-console --tail=500 | grep "mTLS fallback authentication" | grep -c "error"
```

#### Step 3: Monitor CRL Fetch Status

```bash
# Verify CRL cache is working
kubectl logs -l app=web-console --tail=100 | grep "CRL fetch"

# Expected (every 1 hour cache refresh):
# CRL fetched successfully | last_update: 2025-11-26T10:00:00Z | revoked_count: 2
```

**Warning Signs:**
- `CRL fetch failed (HTTP error)` — CRL distribution point unavailable (fail-secure: all auth rejected)
- `CRL too old` — CRL not updated in >24 hours (fail-secure: all auth rejected)

#### Step 4: Monitor Prometheus Alerts

```bash
# Check Prometheus for mTLS-related alerts
curl -s http://prometheus:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.alertname | contains("Mtls"))'
```

**Expected Alerts During Fallback:**
- `IdPFallbackModeActive` (WARNING) — Expected during outage
- `MtlsAuthFailureRateHigh` (CRITICAL) — >10 failed auth attempts/min (investigate)
- `MtlsCertificateExpiringSoon` (WARNING) — Admin cert expires <24h (rotate certificate)
- `MtlsCrlFetchFailure` (CRITICAL) — CRL unavailable (all auth blocked)

---

### Phase 5: Auth0 Recovery Detection (automatic)

The system **automatically** exits fallback mode when Auth0 recovers. No manual intervention required.

#### Recovery Process

1. **IdP Health Monitor Detects Recovery**
   - 5 consecutive successful health checks (50 seconds)
   - 5-minute stable period (no failures)
   - Total recovery time: ~5 minutes 50 seconds

2. **Fallback Mode Deactivation**
   ```bash
   # Check logs for automatic recovery
   kubectl logs -l app=web-console --tail=50 | grep "exiting fallback mode"

   # Expected output:
   # IdP recovery: exiting fallback mode (hysteresis satisfied) | consecutive_successes: 5 | stable_duration_seconds: 300
   ```

3. **Normal OAuth2 Resumes**
   - New login attempts use normal OAuth2/OIDC flow
   - Existing mTLS sessions remain valid until expiry (1 hour)
   - Prometheus alert `IdPFallbackModeActive` auto-resolves

#### Step 1: Verify OAuth2 Recovery

```bash
# Test normal OAuth2 login flow
curl -v https://YOUR-DOMAIN/login

# Should redirect to Auth0 (not show mTLS fallback banner)
```

#### Step 2: Confirm Fallback Deactivated

```bash
# Check for fallback exit log
kubectl logs -l app=web-console --tail=100 | grep "exiting fallback mode"

# Verify no new mTLS authentications occurring
kubectl logs -l app=web-console --since=5m | grep "mTLS fallback authentication" | wc -l
# Expected: 0 (or only existing sessions refreshing)
```

#### Step 3: Notify Users

**Slack Template:**
```
:white_check_mark: **Auth0 IdP Recovered**

**Status:** Normal OAuth2/OIDC authentication restored
**Impact:** All users can log in normally
**Action Required:** None (automatic recovery)

**Fallback Mode:** Deactivated at 2025-11-26T10:15:00Z
**Recovery Duration:** 5min 50s (hysteresis: 5 successes + 5min stable)

**Next Steps:**
- Existing mTLS sessions will expire naturally (1 hour)
- New logins use normal OAuth2 flow
- Post-incident review scheduled for: TBD
```

---

### Phase 6: Post-Incident Cleanup (< 10 minutes)

#### Step 1: Review Fallback Audit Logs

```bash
# Extract all fallback authentications during outage
kubectl logs -l app=web-console --since=1h | grep "mTLS fallback authentication" > /tmp/fallback-audit.log

# Count unique administrators who accessed during outage
cat /tmp/fallback-audit.log | grep "cn:" | sed 's/.*cn: //' | sed 's/ .*//' | sort -u

# Review any authentication failures
cat /tmp/fallback-audit.log | grep "error"
```

#### Step 2: Check for Certificate Expiry

```bash
# Identify certificates expiring soon
kubectl logs -l app=web-console --since=1h | grep "Certificate expiring soon"

# Extract certificate details for rotation
kubectl logs -l app=web-console --since=1h | grep "expiring soon" | sed 's/.*cn: //' | sed 's/ .*//'
```

**Action:** If any certificates expire within 24 hours, schedule rotation (see: mtls-certificate-management.md)

#### Step 3: Verify CRL Status

```bash
# Check last successful CRL fetch
kubectl logs -l app=web-console --tail=500 | grep "CRL fetched successfully" | tail -1

# Expected: CRL fetched within last 1 hour (cache TTL)
```

#### Step 4: Disable mTLS Fallback (Optional)

**NOTE:** Only disable fallback if you want to deactivate the feature entirely (not recommended for production).

```bash
# Disable fallback feature flag
./scripts/disable_mtls_fallback.sh

# Verify change
grep "ENABLE_MTLS_FALLBACK" /app/.env
# Expected: ENABLE_MTLS_FALLBACK=false

# Restart service to apply
kubectl rollout restart deployment/web-console
```

**Recommendation:** Keep `ENABLE_MTLS_FALLBACK=true` in production for future outages.

---

## Emergency Procedures

### Scenario 1: CRL Unavailable During Fallback

**Problem:** CRL distribution point is unreachable, blocking all mTLS authentication (fail-secure).

**Impact:** Administrators cannot access system even with valid certificates.

**Solution:**

```bash
# Option 1: Fix CRL distribution point
# SSH to CA server
ssh admin@ca.trading-platform.local

# Restart CRL HTTP server
sudo systemctl restart nginx

# Verify CRL accessible
curl -I http://ca.trading-platform.local/crl/admin-ca.crl
```

```bash
# Option 2: Temporarily use cached CRL (if recent)
# Check last successful CRL fetch
kubectl logs -l app=web-console --tail=1000 | grep "CRL fetched successfully" | tail -1

# If CRL < 24 hours old, cached copy is still valid (no action needed)
# If CRL > 24 hours old, fail-secure behavior is correct (do NOT bypass)
```

**DO NOT:** Disable CRL checks or bypass fail-secure behavior.

---

### Scenario 2: Excessive mTLS Authentication Failures

**Problem:** >10 failed authentication attempts per minute (Prometheus alert: `MtlsAuthFailureRateHigh`)

**Possible Causes:**
- Attacker attempting to use invalid certificates
- Administrator using expired certificate
- Certificate revoked but admin unaware

**Response:**

```bash
# Step 1: Identify failure sources
kubectl logs -l app=web-console --since=5m | grep "mTLS fallback authentication" | grep "error" | sed 's/.*client_ip: //' | sed 's/ .*//' | sort | uniq -c | sort -rn

# Step 2: Review failure reasons
kubectl logs -l app=web-console --since=5m | grep "mTLS fallback authentication" | grep "error"
```

**Common Errors:**
- `Certificate expired` → Admin needs new certificate (see: mtls-certificate-management.md)
- `CN not in admin allowlist` → Unauthorized access attempt (block IP at firewall)
- `Certificate revoked` → Valid behavior (certificate was intentionally revoked)
- `CRL check failed` → CRL unavailable (see Scenario 1)

**Action:**
- If legitimate admin with expired cert → Issue emergency certificate (7-day max)
- If unauthorized attempts → Block source IP at nginx/firewall level
- If CRL issue → Follow Scenario 1 procedure

---

### Scenario 3: Fallback Mode Stuck Active After Auth0 Recovery

**Problem:** Auth0 is healthy but fallback mode does not deactivate automatically.

**Diagnosis:**

```bash
# Check IdP health check results
kubectl logs -l app=web-console --tail=100 | grep "IdP health check"

# Verify consecutive successes counter
# Expected: consecutive_successes should increment on each success
```

**Possible Causes:**
1. **Stability period not yet satisfied** (5 successes achieved but <5min elapsed)
2. **Intermittent Auth0 failures** (resets stability timer)
3. **Network issues** preventing health checks from succeeding

**Solution:**

```bash
# Case 1: Stability period in progress (normal)
# Wait for 5-minute timer to complete (no action needed)
# Check logs for "stability period reset" messages

# Case 2: Intermittent Auth0 failures
# Verify Auth0 status
curl -v https://YOUR-AUTH0-DOMAIN/.well-known/openid-configuration
# If failures persist, Auth0 not fully recovered (wait longer)

# Case 3: Network issue
# Test connectivity from web-console pod
kubectl exec -it deployment/web-console -- curl -v https://YOUR-AUTH0-DOMAIN/.well-known/openid-configuration
# If connection fails, check network policies / DNS
```

**Manual Override (Last Resort):**

```bash
# Force disable fallback (NOT RECOMMENDED - only if Auth0 confirmed healthy)
# This bypasses hysteresis safety mechanism
./scripts/disable_mtls_fallback.sh

# Restart service
kubectl rollout restart deployment/web-console
```

**WARNING:** Manual override defeats hysteresis protection against flapping. Only use if absolutely necessary.

---

## Monitoring & Alerts

### Prometheus Alerts

**Alert Definitions:** `infra/prometheus/alerts/oauth2.yml`

| Alert | Severity | Threshold | Response |
|-------|----------|-----------|----------|
| `IdPHealthCheckFailed` | WARNING | 3 consecutive failures | Verify Auth0 status; wait for fallback activation |
| `IdPFallbackModeActive` | WARNING | Fallback mode active >5min | Monitor fallback operation; notify administrators |
| `MtlsAuthFailureRateHigh` | CRITICAL | >10 failures/min | Investigate failure reasons (see Scenario 2) |
| `MtlsCertificateExpiringSoon` | WARNING | Admin cert expires <24h | Rotate certificate (mtls-certificate-management.md) |
| `MtlsCrlFetchFailure` | CRITICAL | CRL fetch failed >2x | Fix CRL distribution point (see Scenario 1) |

### Grafana Dashboard

**Dashboard:** OAuth2 Sessions (`infra/grafana/dashboards/oauth2-sessions.json`)

**Key Panels:**
- IdP Health Status (green/red indicator)
- Fallback Mode Active (boolean)
- Consecutive Failures/Successes (counters)
- mTLS Authentication Rate (requests/min)
- Certificate Expiry Timeline (days until expiry)
- CRL Fetch Status (last successful fetch timestamp)

### Log Queries

**Useful Grafana/Loki queries:**

```promql
# IdP health check failures (last 1 hour)
{app="web-console"} |= "IdP health check failed"

# Fallback mode activation events
{app="web-console"} |= "entering fallback mode"

# mTLS authentication attempts (success + failure)
{app="web-console"} |= "mTLS fallback authentication"

# Certificate expiry warnings
{app="web-console"} |= "Certificate expiring soon"

# CRL fetch errors
{app="web-console"} |= "CRL fetch failed"
```

---

## Testing

### Simulate Auth0 Outage (Staging Only)

**WARNING:** Do NOT run on production.

```bash
# Block Auth0 domain at DNS level (staging only)
ssh staging-web-console-01

# Add fake DNS entry to /etc/hosts (requires root)
sudo sh -c 'echo "127.0.0.1 YOUR-AUTH0-DOMAIN" >> /etc/hosts'

# Verify health check failures
kubectl logs -f -l app=web-console | grep "IdP health check"

# Wait 30 seconds for 3 consecutive failures
# Expected: "entering fallback mode" log

# Test mTLS authentication with admin certificate
# Navigate to https://staging.YOUR-DOMAIN and select admin certificate

# Verify authentication succeeds and fallback banner displays

# Restore Auth0 connectivity
sudo sed -i '/YOUR-AUTH0-DOMAIN/d' /etc/hosts

# Wait for automatic recovery (~6 minutes)
kubectl logs -f -l app=web-console | grep "exiting fallback mode"
```

### Test mTLS Certificate Authentication

```bash
# Verify admin certificate valid
openssl x509 -in admin.crt -noout -checkend 86400
# Exit code 0 = valid for >24h, 1 = expires <24h

# Verify CN in allowlist
openssl x509 -in admin.crt -noout -subject | grep CN
# Expected: CN = admin.trading-platform.local (or other allowlist entry)

# Test CRL check (manual)
curl -I http://ca.trading-platform.local/crl/admin-ca.crl
# Expected: HTTP 200 OK
```

---

## Rollback Plan

### Disable mTLS Fallback Immediately

**Use Case:** Security incident (compromised client certificate)

```bash
# Emergency disable
./scripts/disable_mtls_fallback.sh

# Verify disabled
grep "ENABLE_MTLS_FALLBACK" /app/.env
# Expected: ENABLE_MTLS_FALLBACK=false

# Restart service
kubectl rollout restart deployment/web-console

# Verify fallback inactive
kubectl logs -l app=web-console --tail=50 | grep "mTLS fallback"
# Expected: No new mTLS authentication logs
```

**Impact:**
- Existing mTLS sessions invalidated immediately
- Only OAuth2/OIDC authentication available
- If Auth0 still unavailable → NO ADMIN ACCESS (full outage)

**Audit:**
```bash
# Script creates audit log automatically
cat logs/mtls_fallback_audit.log | tail -1
# Expected: AUDIT: ... Action: DISABLE ...
```

---

## Related Documentation

- **ADR-015:** OAuth2/OIDC Authentication Architecture
- **P2T3-Phase3_Component6-7_Plan.md:** mTLS Fallback Implementation Plan
- **mtls-certificate-management.md:** Certificate Issuance & Rotation Procedures
- **session-key-rotation.md:** Session Secret Rotation (related to OAuth2 sessions)
- **oauth2-session-cleanup.md:** Session Cleanup Procedures

---

## Appendix: Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENABLE_MTLS_FALLBACK` | Yes | `false` | Feature flag (must be `true` for fallback) |
| `MTLS_ADMIN_CN_ALLOWLIST` | Yes | (empty) | Comma-separated admin CNs (e.g., `admin.local,emergency-admin.local`) |
| `MTLS_CRL_URL` | Yes | `http://ca.local/crl/admin-ca.crl` | CRL distribution point URL |

### IdP Health Monitor Configuration

**File:** `apps/web_console/auth/idp_health.py`

| Parameter | Default | Description |
|-----------|---------|-------------|
| `normal_check_interval_seconds` | 10 | Polling interval in normal mode |
| `fallback_check_interval_seconds` | 60 | Polling interval in fallback mode (exponential backoff) |
| `failure_threshold` | 3 | Consecutive failures to enter fallback |
| `success_threshold` | 5 | Consecutive successes to exit fallback |
| `stable_period_seconds` | 300 | Stable period after success_threshold (5 minutes) |
| `timeout_seconds` | 5.0 | HTTP request timeout |

### Certificate Validation Rules

**File:** `apps/web_console/auth/mtls_fallback.py`

| Rule | Enforcement | Default |
|------|-------------|---------|
| Max certificate lifetime | Hard limit | 7 days (`notAfter - notBefore`) |
| Expiry warning threshold | Warning log | 24 hours before expiry |
| CRL cache TTL | Cache refresh | 1 hour |
| CRL max age | Fail-secure reject | 24 hours (since `last_update`) |
| Admin CN allowlist | Hard limit | From `MTLS_ADMIN_CN_ALLOWLIST` env var |

---

## Contact

**On-Call:** Check PagerDuty rotation for platform-team

**Email:** platform-team@company.com

**Slack:** #platform-incidents

---

**Version:** 1.0

**Last Tested:** 2025-11-26 (staging environment)

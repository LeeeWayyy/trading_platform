# Session Secret Key Rotation Runbook

**Purpose:** Rotate SESSION_SECRET_KEY with zero downtime to maintain security compliance

**Owner:** @platform-team

**Last Updated:** 2025-11-26

**Related:** ADR-015, oauth2-session-cleanup.md, auth0-idp-outage.md, secret-rotation.md

---

## Overview

This runbook guides operators through rotating the `SESSION_SECRET_KEY` used to sign and verify HTTP session cookies. Regular rotation is a security best practice to limit the impact of key compromise.

**Estimated Time:** 10-15 minutes

**Prerequisites:**
- Secrets backend access (Vault or AWS Secrets Manager)
- Write permissions to update environment variables
- Access to redeploy web_console service
- Understanding of grace period concept

---

## Session Secret Architecture

### Purpose

The `SESSION_SECRET_KEY` is used to:

1. **Sign session cookies** (HMAC-SHA256 signature)
2. **Verify session cookies** (prevent tampering)
3. **Encrypt session data** (optional, if enabled)

### Security Requirements

**Key Properties:**
- **Length:** Minimum 32 bytes (256 bits)
- **Randomness:** Cryptographically secure random generation
- **Secrecy:** Never logged, never committed to git, never transmitted in plaintext

**Rotation Frequency:**
- **Mandatory:** Every 90 days (compliance requirement: PCI DSS, SOC2)
- **Recommended:** Every 30 days (defense-in-depth)
- **Emergency:** Immediate rotation if key compromise suspected

---

## Rotation Schedule

### Mandatory 90-Day Rotation

**Reminder Mechanism:**
- Calendar reminder: 85 days after last rotation (5-day warning)
- Monitoring alert: 88 days after last rotation (2-day final warning)
- Compliance audit: Verify rotation occurred within 90 days

**Next Rotation Due:** [DATE - Update after each rotation]

**Last Rotation:** [DATE - Update after each rotation]

---

## When to Rotate Session Secret

### Scheduled Rotation (Every 90 Days)

**Trigger:** Calendar reminder or compliance requirement

**Action:** Follow normal rotation procedure (see Procedure 1)

---

### Emergency Rotation (Key Compromise)

**Triggers:**
- Session secret leaked in logs
- Secrets backend compromised
- Unauthorized session access detected
- Security audit finding
- Insider threat incident

**Action:** Immediate rotation without grace period (see Procedure 2)

---

### Post-Incident Rotation

**Triggers:**
- Security incident involving authentication system
- Suspicious session activity
- Failed security audit

**Action:** Follow normal rotation procedure + forensic analysis (see Procedure 3)

---

## Rotation Procedures

### Procedure 1: Scheduled Rotation with Grace Period (Zero Downtime)

**Purpose:** Rotate session secret every 90 days with zero user impact

**Safety:** Safe - uses grace period to avoid invalidating active sessions

#### Step 1: Pre-Rotation Checklist

- [ ] **Backup current secret** (emergency rollback)
- [ ] **Verify services healthy** (check /health endpoints)
- [ ] **Check active sessions** (count in Redis)
- [ ] **Schedule maintenance window** (off-peak hours recommended, but not required)
- [ ] **Notify team** (Slack notification 24h advance notice)
- [ ] **Review rollback plan** (see "Emergency Rollback" section)

```bash
# Check current session count
redis-cli --scan --pattern "session:*" | wc -l

# Check web_console service health
curl -k https://YOUR-DOMAIN/health
# Expected: {"status": "healthy"}
```

#### Step 2: Backup Current Secret

```bash
# Export current secret from secrets backend
# Vault example:
vault kv get -field=SESSION_SECRET_KEY secret/trading-platform/web-console > /tmp/old_session_secret.txt

# AWS Secrets Manager example:
aws secretsmanager get-secret-value --secret-id trading-platform/web-console/session-secret \
  --query 'SecretString' --output text | jq -r '.SESSION_SECRET_KEY' > /tmp/old_session_secret.txt

# Verify backup
wc -c /tmp/old_session_secret.txt
# Expected: 64 characters (32 bytes hex-encoded)

# Secure backup file
chmod 600 /tmp/old_session_secret.txt
```

#### Step 3: Generate New Secret Key

```bash
# Generate cryptographically secure random key (32 bytes = 256 bits)
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Verify length (should be 64 characters = 32 bytes hex)
echo "$NEW_SECRET" | wc -c
# Expected: 65 (64 characters + newline)

# Display new secret (for manual verification)
echo "New session secret: $NEW_SECRET"
```

**IMPORTANT:** Save this secret securely. Do NOT commit to git, log, or transmit in plaintext.

#### Step 4: Implement Grace Period (Multi-Key Verification)

**Concept:** Temporarily accept BOTH old and new keys for verification, but sign new cookies with new key only.

**Implementation:**

```bash
# Update .env to include BOTH keys (comma-separated)
OLD_SECRET=$(cat /tmp/old_session_secret.txt)
NEW_SECRET="[from Step 3]"

# Method 1: Direct .env edit
echo "SESSION_SECRET_KEY=$NEW_SECRET,$OLD_SECRET" >> /app/.env

# Method 2: Secrets backend update
vault kv put secret/trading-platform/web-console SESSION_SECRET_KEY="$NEW_SECRET,$OLD_SECRET"
```

**Code Behavior During Grace Period:**

```python
# Session verification accepts BOTH keys (old sessions still valid)
ALLOWED_KEYS = SESSION_SECRET_KEY.split(',')
for key in ALLOWED_KEYS:
    if verify_signature(cookie, key):
        return True  # Valid session

# Session signing uses ONLY first key (new sessions use new secret)
sign_cookie(session_data, ALLOWED_KEYS[0])
```

#### Step 5: Deploy Grace Period Configuration

```bash
# Restart web_console service to load new config
kubectl rollout restart deployment/web-console

# Wait for rollout to complete
kubectl rollout status deployment/web-console
# Expected: deployment "web-console" successfully rolled out

# Verify new pods running
kubectl get pods -l app=web-console
# Expected: All pods Running with recent start time

# Check logs for startup errors
kubectl logs -l app=web-console --tail=50 | grep -i error
# Expected: No errors
```

#### Step 6: Verify Grace Period Active

```bash
# Test that OLD sessions still work
# (Use existing session cookie from before rotation)
curl -k -b "session_cookie=[OLD_COOKIE]" https://YOUR-DOMAIN/dashboard
# Expected: HTTP 200 (authenticated)

# Test that NEW sessions use new secret
# (Login to get new session cookie)
curl -k https://YOUR-DOMAIN/login
# Expected: Set-Cookie header with new session signed by new secret
```

#### Step 7: Monitor During Grace Period

**Grace Period Duration:** 24 hours (recommended)
- Allows all active sessions (1-hour TTL) to expire naturally
- Provides buffer for users who logged in during rotation

```bash
# Monitor session creation/expiry during grace period
redis-cli --scan --pattern "session:*" | wc -l
# Count should remain stable (new sessions created, old sessions expire)

# Check for authentication errors
kubectl logs -l app=web-console --since=1h | grep -i "signature verification failed"
# Expected: None (grace period should prevent verification failures)
```

#### Step 8: Remove Old Secret (Grace Period Exit)

**After 24 hours:**

```bash
# Update .env to use ONLY new secret
echo "SESSION_SECRET_KEY=$NEW_SECRET" > /app/.env

# Or update secrets backend
vault kv put secret/trading-platform/web-console SESSION_SECRET_KEY="$NEW_SECRET"

# Restart service
kubectl rollout restart deployment/web-console

# Verify restart
kubectl rollout status deployment/web-console
```

#### Step 9: Verify Rotation Complete

```bash
# Check that old sessions are now invalid
# (Use session cookie created with old secret before rotation)
curl -k -b "session_cookie=[OLD_COOKIE]" https://YOUR-DOMAIN/dashboard
# Expected: HTTP 401 or redirect to /login (signature verification fails)

# Confirm new sessions still work
curl -k -b "session_cookie=[NEW_COOKIE]" https://YOUR-DOMAIN/dashboard
# Expected: HTTP 200 (authenticated)

# Verify no signature errors in logs
kubectl logs -l app=web-console --since=5m | grep -i "signature"
# Expected: No errors (all active sessions use new secret)
```

#### Step 10: Update Rotation Metadata

```bash
# Update rotation tracking
echo "Last rotation: $(date -u +"%Y-%m-%d")" >> /var/log/session-secret-rotation.log
echo "Rotated by: $USER" >> /var/log/session-secret-rotation.log
echo "Next rotation due: $(date -u -d '+90 days' +"%Y-%m-%d")" >> /var/log/session-secret-rotation.log

# Update calendar reminder for next rotation
# Set reminder for 85 days from today

# Update this runbook's "Last Rotation" date at top of file
```

#### Step 11: Cleanup

```bash
# Securely delete backed-up old secret
shred -u /tmp/old_session_secret.txt

# Verify deletion
ls /tmp/old_session_secret.txt
# Expected: No such file or directory
```

---

### Procedure 2: Emergency Rotation (No Grace Period)

**Purpose:** Immediate rotation in response to key compromise

**Safety:** DESTRUCTIVE - all active sessions will be invalidated immediately

**Authorization Required:** VP Engineering or Security Officer approval

#### Step 1: Confirm Authorization

```bash
# Document approval
echo "Emergency rotation authorized by: [NAME]" >> /var/log/session-secret-rotation.log
echo "Reason: [KEY COMPROMISE / SECURITY INCIDENT]" >> /var/log/session-secret-rotation.log
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /var/log/session-secret-rotation.log
```

#### Step 2: Generate New Secret Immediately

```bash
# Generate new secret
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Update secrets backend (NO grace period - single key only)
vault kv put secret/trading-platform/web-console SESSION_SECRET_KEY="$NEW_SECRET"
```

#### Step 3: Deploy New Secret Immediately

```bash
# Restart service to load new secret
kubectl rollout restart deployment/web-console

# Force immediate rollout (no wait)
kubectl rollout status deployment/web-console --timeout=60s
```

#### Step 4: Invalidate All Active Sessions

**Rationale:** If session secret is compromised, ALL existing sessions are potentially forged.

```bash
# Delete all sessions from Redis (mass logout)
python3 scripts/clear_oauth2_sessions.py --all

# Verify no sessions remain
redis-cli --scan --pattern "session:*" | wc -l
# Expected: 0
```

#### Step 5: Notify Users

**Slack/Email Template:**
```
:warning: **Security Incident: Session Secret Rotated**

**Timestamp:** 2025-11-26T10:00:00Z
**Impact:** All users must re-authenticate immediately
**Reason:** [Session secret compromise / Security incident]

**Action Required:**
1. All active sessions have been invalidated
2. Navigate to https://YOUR-DOMAIN/login to re-authenticate
3. Contact security team if you notice suspicious activity

**Support:**
- Email: security@company.com
- On-call: Check PagerDuty rotation
```

#### Step 6: Post-Incident Analysis

```bash
# Review logs for unauthorized session usage
kubectl logs -l app=web-console --since=24h | grep -E "(session created|authentication)" > /tmp/session-forensics.log

# Analyze session creation sources (IP addresses, user agents)
cat /tmp/session-forensics.log | grep "client_ip" | sed 's/.*client_ip: //' | sort -u

# Check for suspicious patterns
# - Multiple sessions from unexpected geolocations
# - Unusual user agents (automated tools)
# - High-frequency session creation (>10/min from single IP)
```

---

### Procedure 3: Post-Incident Rotation with Forensics

**Purpose:** Rotate secret after security incident + preserve evidence

**Combines:** Emergency rotation + forensic log preservation

#### Step 1: Preserve Evidence BEFORE Rotation

```bash
# Backup all current sessions (for forensic analysis)
redis-cli --scan --pattern "session:*" | while read key; do
  echo "KEY: $key"
  redis-cli GET "$key"
  echo "TTL: $(redis-cli TTL "$key")"
  echo "---"
done > /tmp/sessions-forensics-$(date +%Y%m%d_%H%M%S).log

# Compress and secure forensic log
gzip /tmp/sessions-forensics-*.log
chmod 600 /tmp/sessions-forensics-*.log.gz

# Move to secure storage (not on production server)
scp /tmp/sessions-forensics-*.log.gz forensics-server:/secure/investigations/
```

#### Step 2: Follow Emergency Rotation Procedure

```bash
# Execute Procedure 2 (Emergency Rotation)
# See above for steps
```

#### Step 3: Forensic Analysis

```bash
# Extract suspicious sessions from forensic log
zcat /tmp/sessions-forensics-*.log.gz | grep -A 10 "KEY: session:" > /tmp/analysis.log

# Identify sessions with unusual characteristics:
# - auth_method = "mtls_fallback" when IdP not in fallback mode (forged cert?)
# - expires_at in the past (expired but not cleaned up - bug or attack?)
# - user_id not matching known users (account takeover?)

# Generate report for security team
```

---

## Grace Period Implementation Details

### Why Grace Period?

**Problem:** Rotating session secret immediately invalidates all active sessions (poor user experience).

**Solution:** Temporarily accept BOTH old and new secrets for signature verification, while signing new sessions with new secret only.

**Result:** Zero downtime rotation - existing sessions remain valid until natural expiry.

### Multi-Key Verification Logic

**Environment Variable Format:**
```bash
# Grace period: comma-separated list (new key first)
SESSION_SECRET_KEY=new_secret_here,old_secret_here

# Post-grace period: single key only
SESSION_SECRET_KEY=new_secret_here
```

**Code Implementation:**

```python
# apps/web_console/auth/__init__.py (simplified)

def verify_session_cookie(cookie: str) -> bool:
    """Verify session cookie signature (supports multi-key for rotation)."""
    allowed_keys = os.getenv("SESSION_SECRET_KEY", "").split(',')

    for key in allowed_keys:
        try:
            # Try to verify with this key
            if hmac.compare_digest(
                compute_signature(cookie.payload, key),
                cookie.signature
            ):
                return True  # Valid signature
        except Exception:
            continue  # Try next key

    return False  # No valid signature found

def sign_session_cookie(session_data: dict) -> str:
    """Sign new session cookie (uses FIRST key only)."""
    allowed_keys = os.getenv("SESSION_SECRET_KEY", "").split(',')
    primary_key = allowed_keys[0]  # ALWAYS use first key for signing

    signature = compute_signature(session_data, primary_key)
    return f"{base64.b64encode(session_data)}.{signature}"
```

### Grace Period Timeline

```
T=0:     Rotation starts
         - Generate new secret
         - Update SESSION_SECRET_KEY="new,old" (comma-separated)
         - Restart service

T=0-24h: Grace period active
         - Old sessions verified with old secret (still work)
         - New sessions signed with new secret
         - Both secrets accepted for verification

T=24h:   Grace period ends
         - Update SESSION_SECRET_KEY="new" (single key)
         - Restart service
         - Old sessions now invalid (signature verification fails)
         - All sessions must use new secret
```

---

## Monitoring & Alerts

### Prometheus Alerts

**Alert Definitions:** `infra/prometheus/alerts/oauth2.yml`

| Alert | Severity | Threshold | Response |
|-------|----------|-----------|----------|
| `SessionSecretRotationOverdue` | WARNING | Last rotation >85 days | Schedule rotation within 5 days |
| `SessionSecretRotationCritical` | CRITICAL | Last rotation >90 days | Immediate rotation required (compliance violation) |
| `SessionSignatureVerificationFailures` | WARNING | >10 failures/min | Investigate (possible rotation issue or attack) |

### Grafana Dashboard

**Dashboard:** OAuth2 Sessions (`infra/grafana/dashboards/oauth2-sessions.json`)

**Key Panels:**
- Days Since Last Rotation (gauge)
- Next Rotation Due (countdown)
- Session Signature Verification Rate (success vs. failure)
- Grace Period Active (boolean indicator)

### Rotation Tracking

**Metadata Storage:** `/var/log/session-secret-rotation.log`

**Format:**
```
Last rotation: 2025-11-26
Rotated by: admin@company.com
Next rotation due: 2026-02-24
Grace period duration: 24 hours
Reason: Scheduled 90-day rotation
```

**Query Last Rotation:**
```bash
grep "Last rotation" /var/log/session-secret-rotation.log | tail -1
# Expected: Last rotation: 2025-11-26

# Calculate days since last rotation
LAST_ROTATION=$(grep "Last rotation" /var/log/session-secret-rotation.log | tail -1 | awk '{print $3}')
DAYS_SINCE=$(( ( $(date +%s) - $(date -d "$LAST_ROTATION" +%s) ) / 86400 ))
echo "Days since last rotation: $DAYS_SINCE"
```

---

## Troubleshooting

### Problem 1: Session Signature Verification Failures After Rotation

**Symptoms:**
- Users get logged out unexpectedly
- "Invalid session" errors in logs
- HTTP 401 responses

**Diagnosis:**
```bash
# Check current SESSION_SECRET_KEY configuration
kubectl exec deployment/web-console -- env | grep SESSION_SECRET_KEY
# Should show new secret (or "new,old" during grace period)

# Check logs for signature errors
kubectl logs -l app=web-console --since=1h | grep -i "signature"
# Look for "signature verification failed" messages
```

**Causes:**
1. Grace period not implemented (old secret removed too quickly)
2. Environment variable not updated correctly
3. Service not restarted after secret update

**Solution:**
```bash
# Re-enable grace period (add old secret back)
OLD_SECRET="[backed up old secret]"
NEW_SECRET="[current new secret]"

vault kv put secret/trading-platform/web-console SESSION_SECRET_KEY="$NEW_SECRET,$OLD_SECRET"

# Restart service
kubectl rollout restart deployment/web-console

# Wait 24 hours before removing old secret again
```

---

### Problem 2: Grace Period Not Working (Old Sessions Still Invalid)

**Symptoms:**
- All users forced to re-login immediately after rotation
- Grace period configured but old sessions don't work

**Diagnosis:**
```bash
# Verify multi-key configuration
kubectl exec deployment/web-console -- python3 -c "import os; print(os.getenv('SESSION_SECRET_KEY').split(','))"
# Should output: ['new_secret', 'old_secret']

# Check code implementation
kubectl exec deployment/web-console -- grep -A 10 "verify_session_cookie" /app/apps/web_console/auth/__init__.py
# Verify multi-key loop exists
```

**Causes:**
1. Code doesn't implement multi-key verification
2. Secrets not comma-separated correctly
3. Old secret incorrect (typo during backup/restore)

**Solution:**
```bash
# Verify code implements multi-key verification
# See "Grace Period Implementation Details" section for correct code

# Test old secret manually
OLD_COOKIE="[existing session cookie from before rotation]"
python3 -c "
import hmac
import base64
cookie_payload, cookie_sig = '$OLD_COOKIE'.split('.')
old_secret = '$OLD_SECRET'
expected_sig = hmac.new(old_secret.encode(), cookie_payload.encode(), 'sha256').hexdigest()
print(f'Expected: {expected_sig}')
print(f'Got: {cookie_sig}')
print(f'Valid: {hmac.compare_digest(expected_sig, cookie_sig)}')
"
```

---

### Problem 3: Emergency Rotation Didn't Invalidate All Sessions

**Symptoms:**
- Sessions still work after emergency rotation + Redis flush
- Suspected forged sessions still active

**Diagnosis:**
```bash
# Verify Redis was actually flushed
redis-cli --scan --pattern "session:*" | wc -l
# Should be 0 immediately after flush

# Check for multiple Redis instances (sessions in different database)
redis-cli INFO keyspace
# Should show db0 only (or whichever DB is configured)

# Check application cache (sessions cached in memory?)
kubectl logs -l app=web-console --tail=100 | grep -i "cache"
```

**Causes:**
1. Sessions cached in application memory (not just Redis)
2. Multiple Redis instances (flush didn't affect all)
3. CDN/proxy caching session cookies

**Solution:**
```bash
# Force application restart (clears memory cache)
kubectl rollout restart deployment/web-console

# Verify all Redis databases flushed
redis-cli FLUSHALL  # WARNING: Deletes ALL Redis data (circuit breaker, features, etc.)

# Clear CDN/proxy cache (if applicable)
# Cloudflare example:
curl -X POST "https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache" \
  -H "Authorization: Bearer {api_token}" \
  -d '{"purge_everything":true}'
```

---

## Testing

### Test Grace Period Rotation (Staging Only)

**WARNING:** Do NOT run on production without approval.

```bash
# Step 1: Create test session (login to staging)
curl -k https://staging.YOUR-DOMAIN/login -c /tmp/cookies.txt
# Save session cookie for later testing

# Step 2: Backup current secret
OLD_SECRET=$(kubectl exec deployment/web-console -- env | grep SESSION_SECRET_KEY | cut -d= -f2)

# Step 3: Generate new secret
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Step 4: Enable grace period
kubectl set env deployment/web-console SESSION_SECRET_KEY="$NEW_SECRET,$OLD_SECRET"

# Step 5: Wait for rollout
kubectl rollout status deployment/web-console

# Step 6: Test old session still works
curl -k -b /tmp/cookies.txt https://staging.YOUR-DOMAIN/dashboard
# Expected: HTTP 200 (authenticated with old cookie)

# Step 7: Create new session (login again)
curl -k https://staging.YOUR-DOMAIN/login -c /tmp/cookies-new.txt

# Step 8: Test new session works
curl -k -b /tmp/cookies-new.txt https://staging.YOUR-DOMAIN/dashboard
# Expected: HTTP 200 (authenticated with new cookie)

# Step 9: Remove old secret (grace period exit)
kubectl set env deployment/web-console SESSION_SECRET_KEY="$NEW_SECRET"

# Step 10: Wait for rollout
kubectl rollout status deployment/web-console

# Step 11: Test old session now invalid
curl -k -b /tmp/cookies.txt https://staging.YOUR-DOMAIN/dashboard
# Expected: HTTP 401 or redirect to /login

# Step 12: Test new session still works
curl -k -b /tmp/cookies-new.txt https://staging.YOUR-DOMAIN/dashboard
# Expected: HTTP 200

# SUCCESS: Grace period rotation working correctly
```

---

## Emergency Rollback

### Scenario: Rotation Caused Widespread Authentication Failures

**Symptoms:**
- All users unable to login after rotation
- Widespread 401 errors
- Incorrect new secret deployed

**Action:**

```bash
# Step 1: Restore old secret IMMEDIATELY
OLD_SECRET=$(cat /tmp/old_session_secret.txt)
kubectl set env deployment/web-console SESSION_SECRET_KEY="$OLD_SECRET"

# Step 2: Force immediate restart
kubectl rollout restart deployment/web-console
kubectl rollout status deployment/web-console --timeout=60s

# Step 3: Verify rollback successful
curl -k -b "[OLD_COOKIE]" https://YOUR-DOMAIN/dashboard
# Expected: HTTP 200 (authenticated)

# Step 4: Investigate root cause
# - Was new secret generated incorrectly?
# - Was environment variable updated incorrectly?
# - Was code changed recently (breaking multi-key verification)?

# Step 5: Plan retry (after root cause fixed)
# Follow Procedure 1 again with correct secret
```

---

## Related Documentation

- **ADR-015:** OAuth2/OIDC Authentication Architecture (session design)
- **oauth2-session-cleanup.md:** Session Cleanup Procedures
- **secret-rotation.md:** General Secret Rotation Runbook (Alpaca API keys, DB passwords)
- **auth0-idp-outage.md:** IdP Outage Response (mTLS fallback uses separate auth, not affected by session secret)

---

## Contact

**On-Call:** Check PagerDuty rotation for platform-team

**Email:** platform-team@company.com

**Slack:** #platform-security

---

**Version:** 1.0

**Last Tested:** 2025-11-26 (staging environment)

# OAuth2 Session Cleanup Runbook

**Purpose:** Manually cleanup expired or orphaned OAuth2 sessions from Redis

**Owner:** @platform-team

**Last Updated:** 2025-11-26

**Related:** ADR-015, session-key-rotation.md, auth0-idp-outage.md

---

## Overview

This runbook guides operators through cleaning up OAuth2 session data stored in Redis. Session cleanup is necessary when:

1. **Expired sessions accumulate** (TTL not working correctly)
2. **Mass logout required** (security incident, compromised session secret)
3. **Testing/debugging** (clear sessions to test authentication flow)
4. **Redis memory pressure** (orphaned session data consuming memory)

**Estimated Time:** 5-10 minutes

**Prerequisites:**
- Redis CLI access (`redis-cli` or `kubectl exec`)
- Read/write permissions to Redis
- Understanding of session key patterns

---

## Session Storage Architecture

### Redis Key Structure

OAuth2 sessions are stored using the following key patterns:

```
# Session data (JSON blob with user_id, auth_method, expires_at, etc.)
session:{session_id}

# Examples:
session:a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
session:9f8e7d6c5b4a3210fedcba9876543210
```

### Session Data Schema

```json
{
  "user_id": "auth0|123456789",
  "username": "john.doe@example.com",
  "auth_method": "oauth2",  // or "mtls_fallback"
  "created_at": "2025-11-26T10:00:00Z",
  "last_activity": "2025-11-26T10:30:00Z",
  "expires_at": "2025-11-26T11:00:00Z",

  // OAuth2-specific fields
  "access_token": "eyJhbGc...",
  "refresh_token": "RT_abc123...",
  "token_expires_at": "2025-11-26T11:00:00Z",

  // mTLS fallback-specific fields (if auth_method = "mtls_fallback")
  "cert_cn": "admin.trading-platform.local",
  "cert_fingerprint": "abc123...",
  "cert_not_after": "2025-12-03T10:00:00Z",
  "crl_status": "valid"
}
```

### TTL (Time To Live)

Sessions have automatic expiry via Redis TTL:

- **Default session TTL:** 1 hour (3600 seconds)
- **Refresh token TTL:** 24 hours (86400 seconds) for OAuth2 sessions
- **mTLS fallback TTL:** 1 hour (same as OAuth2)

Redis automatically deletes expired keys when TTL reaches 0.

---

## When to Cleanup Sessions

### Scenario 1: Expired Sessions Not Auto-Expiring

**Symptoms:**
- Redis memory usage increasing over time
- Large number of session keys with negative TTL or TTL = -1 (no expiry)

**Cause:**
- Bug in session creation code (TTL not set correctly)
- Redis persistence issues (TTL lost on restart)

**Action:** Manual cleanup of expired sessions (see Procedure 1)

---

### Scenario 2: Mass Logout (Security Incident)

**Symptoms:**
- Compromised session secret key
- Unauthorized session access detected
- Need to force re-authentication for all users

**Cause:**
- Security incident requiring immediate session invalidation

**Action:** Delete ALL sessions (see Procedure 2)

---

### Scenario 3: Orphaned Sessions After Auth Method Change

**Symptoms:**
- Sessions from previous auth method (e.g., basic auth) still present after migration to OAuth2
- Mixed session types causing confusion

**Cause:**
- Migration from one auth method to another without cleanup

**Action:** Selective cleanup by auth_method (see Procedure 3)

---

### Scenario 4: Testing/Debugging

**Symptoms:**
- Need to test fresh authentication flow
- Want to clear specific user's sessions for debugging

**Cause:**
- Development/staging environment testing

**Action:** Selective cleanup by user_id or session_id (see Procedure 4)

---

## Cleanup Procedures

### Procedure 1: Cleanup Expired Sessions

**Purpose:** Remove sessions where `expires_at` < current time

**Safety:** Safe (only removes truly expired sessions)

#### Step 1: Connect to Redis

```bash
# Production (via kubectl)
kubectl exec -it deployment/redis -- redis-cli

# Staging/Local (direct connection)
redis-cli -h localhost -p 6379
```

#### Step 2: Scan for Session Keys

```bash
# Count total session keys
redis-cli --scan --pattern "session:*" | wc -l

# Sample 10 random sessions to check expiry
redis-cli --scan --pattern "session:*" | head -10 | while read key; do
  echo "Key: $key"
  redis-cli TTL "$key"
  echo "---"
done
```

**Expected Output:**
```
Key: session:abc123...
3600   # Positive TTL = expires in 3600 seconds (OK)
---
Key: session:def456...
-1     # No expiry set (PROBLEM - orphaned session)
---
Key: session:ghi789...
-2     # Key doesn't exist (already expired)
---
```

#### Step 3: Use Cleanup Script (Recommended)

```bash
# Run cleanup script (safe - checks expires_at field)
cd /app
source .venv/bin/activate
python3 scripts/clear_oauth2_sessions.py --dry-run

# Review proposed deletions
# Expected output:
# Found 150 total session keys
# 45 sessions expired (will be deleted)
# 105 sessions still valid (will be kept)

# Execute cleanup (no dry-run)
python3 scripts/clear_oauth2_sessions.py

# Verify deletion
redis-cli --scan --pattern "session:*" | wc -l
# Expected: 105 (only valid sessions remain)
```

#### Step 4: Manual Cleanup (Alternative)

**WARNING:** This method deletes keys based on TTL only (less precise than script).

```bash
# Find keys with no TTL set (TTL = -1)
redis-cli --scan --pattern "session:*" | while read key; do
  ttl=$(redis-cli TTL "$key")
  if [ "$ttl" = "-1" ]; then
    echo "$key"
  fi
done > /tmp/orphaned-sessions.txt

# Review list
wc -l /tmp/orphaned-sessions.txt
cat /tmp/orphaned-sessions.txt | head -20

# Delete orphaned sessions
cat /tmp/orphaned-sessions.txt | while read key; do
  redis-cli DEL "$key"
done

# Verify deletion
redis-cli --scan --pattern "session:*" | wc -l
```

---

### Procedure 2: Mass Logout (Delete ALL Sessions)

**Purpose:** Force re-authentication for all users (security incident response)

**Safety:** DESTRUCTIVE - all users will be logged out immediately

**Authorization Required:** VP Engineering or Security Officer approval

#### Step 1: Confirm Authorization

```bash
# Document approval
echo "Mass logout authorized by: [NAME]" >> /tmp/session-cleanup-audit.log
echo "Reason: [REASON]" >> /tmp/session-cleanup-audit.log
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")" >> /tmp/session-cleanup-audit.log
```

#### Step 2: Backup Session Keys (Emergency Rollback)

```bash
# Export all session keys and values (for forensics)
redis-cli --scan --pattern "session:*" | while read key; do
  echo "KEY: $key"
  redis-cli GET "$key"
  echo "TTL: $(redis-cli TTL "$key")"
  echo "---"
done > /tmp/sessions-backup-$(date +%Y%m%d_%H%M%S).txt

# Compress backup
gzip /tmp/sessions-backup-*.txt

# Verify backup
ls -lh /tmp/sessions-backup-*.txt.gz
```

#### Step 3: Count Sessions Before Deletion

```bash
# Record count for audit
BEFORE_COUNT=$(redis-cli --scan --pattern "session:*" | wc -l)
echo "Session count before deletion: $BEFORE_COUNT"
```

#### Step 4: Delete All Sessions

```bash
# Method 1: Using script (recommended - rate-limited, batched)
python3 scripts/clear_oauth2_sessions.py --all

# Method 2: Direct Redis deletion (FAST but may cause Redis latency spike)
redis-cli --scan --pattern "session:*" | xargs -L 1000 redis-cli DEL

# Method 3: FLUSHDB (NUCLEAR OPTION - deletes ALL Redis data, not just sessions)
# DO NOT USE unless you understand the impact
# redis-cli FLUSHDB
```

#### Step 5: Verify Deletion

```bash
# Confirm no sessions remain
AFTER_COUNT=$(redis-cli --scan --pattern "session:*" | wc -l)
echo "Session count after deletion: $AFTER_COUNT"
# Expected: 0

# Verify other Redis keys unaffected (circuit breaker, online features, etc.)
redis-cli --scan --pattern "cb:*" | wc -l  # Circuit breaker keys should still exist
redis-cli --scan --pattern "feature:*" | wc -l  # Feature store keys should still exist
```

#### Step 6: Notify Users

**Slack/Email Template:**
```
:warning: **Mass Logout Executed**

**Timestamp:** 2025-11-26T10:00:00Z
**Affected Users:** All active users
**Reason:** [Security incident / Session secret rotation / etc.]

**Action Required:**
All users must re-authenticate to access the system.

**Impact:**
- All active sessions invalidated immediately
- Users will be redirected to login page on next request
- No data loss (only authentication state affected)

**Support:**
- Contact: platform-team@company.com
- On-call: Check PagerDuty rotation
```

---

### Procedure 3: Selective Cleanup by Auth Method

**Purpose:** Remove sessions from specific auth method (e.g., cleanup old basic auth sessions after OAuth2 migration)

**Safety:** Moderate - affects subset of users

#### Step 1: Identify Target Auth Method

```bash
# Sample sessions to find auth_method values
redis-cli --scan --pattern "session:*" | head -20 | while read key; do
  echo "Key: $key"
  redis-cli GET "$key" | jq -r '.auth_method'
done | sort -u

# Expected output:
# oauth2
# mtls_fallback
# basic_auth  # Old auth method (target for cleanup)
```

#### Step 2: Use Cleanup Script with Filter

```bash
# Dry-run with auth_method filter
python3 scripts/clear_oauth2_sessions.py --auth-method basic_auth --dry-run

# Review proposed deletions
# Expected:
# Found 500 total session keys
# 50 sessions with auth_method=basic_auth (will be deleted)
# 450 sessions with other auth methods (will be kept)

# Execute cleanup
python3 scripts/clear_oauth2_sessions.py --auth-method basic_auth

# Verify deletion
redis-cli --scan --pattern "session:*" | head -100 | while read key; do
  redis-cli GET "$key" | jq -r '.auth_method'
done | grep -c "basic_auth"
# Expected: 0
```

---

### Procedure 4: Selective Cleanup (Specific User or Session)

**Purpose:** Remove sessions for specific user (debugging, account compromise)

**Safety:** Safe - affects single user only

#### Step 1: Find User's Sessions

```bash
# Search by user_id
USER_ID="auth0|123456789"

redis-cli --scan --pattern "session:*" | while read key; do
  user_id=$(redis-cli GET "$key" | jq -r '.user_id')
  if [ "$user_id" = "$USER_ID" ]; then
    echo "$key"
  fi
done > /tmp/user-sessions.txt

# Count sessions for user
wc -l /tmp/user-sessions.txt
```

#### Step 2: Review Session Data

```bash
# Inspect sessions before deletion
cat /tmp/user-sessions.txt | while read key; do
  echo "Key: $key"
  redis-cli GET "$key" | jq '.'
  echo "---"
done
```

#### Step 3: Delete User's Sessions

```bash
# Delete all sessions for user
cat /tmp/user-sessions.txt | while read key; do
  redis-cli DEL "$key"
  echo "Deleted: $key"
done

# Verify deletion
redis-cli --scan --pattern "session:*" | while read key; do
  redis-cli GET "$key" | jq -r '.user_id'
done | grep -c "$USER_ID"
# Expected: 0
```

#### Step 4: Delete Single Session (by session_id)

```bash
# If you have specific session ID from logs/monitoring
SESSION_ID="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"

# Verify session exists
redis-cli GET "session:$SESSION_ID"

# Delete session
redis-cli DEL "session:$SESSION_ID"
# Expected output: (integer) 1 (1 key deleted)

# Verify deletion
redis-cli GET "session:$SESSION_ID"
# Expected: (nil)
```

---

## Cleanup Script Reference

### Script Location

**Path:** `scripts/clear_oauth2_sessions.py`

### Usage

```bash
# Activate virtual environment
source .venv/bin/activate

# Basic cleanup (expired sessions only)
python3 scripts/clear_oauth2_sessions.py

# Dry-run mode (show what would be deleted)
python3 scripts/clear_oauth2_sessions.py --dry-run

# Delete ALL sessions (mass logout)
python3 scripts/clear_oauth2_sessions.py --all

# Filter by auth_method
python3 scripts/clear_oauth2_sessions.py --auth-method oauth2
python3 scripts/clear_oauth2_sessions.py --auth-method mtls_fallback

# Filter by user_id
python3 scripts/clear_oauth2_sessions.py --user-id "auth0|123456789"

# Combine filters
python3 scripts/clear_oauth2_sessions.py --auth-method oauth2 --older-than 7d
```

### Script Implementation Details

**Key Features:**
- **SCAN + UNLINK pattern** (non-blocking deletion, safe for production)
- **Batching:** Processes 1000 keys per batch (configurable)
- **Rate limiting:** 10ms sleep between batches (prevents Redis latency spike)
- **Dry-run mode:** Preview deletions without executing
- **Audit logging:** All deletions logged to syslog + stdout

**Safety Mechanisms:**
- Uses `UNLINK` instead of `DEL` (asynchronous deletion, non-blocking)
- Cursor-based SCAN (no KEYS command that blocks Redis)
- Pagination with MATCH filter (efficient)
- Explicit confirmation for `--all` flag (prevents accidental mass deletion)

---

## Monitoring Session Health

### Redis Memory Usage

```bash
# Check Redis memory usage
redis-cli INFO memory

# Key metrics:
# used_memory_human: 256.00M
# used_memory_peak_human: 512.00M
# maxmemory_human: 1.00G

# Calculate session memory usage
TOTAL_SESSIONS=$(redis-cli --scan --pattern "session:*" | wc -l)
TOTAL_MEMORY=$(redis-cli INFO memory | grep used_memory: | cut -d: -f2 | tr -d '\r')
echo "Total sessions: $TOTAL_SESSIONS"
echo "Total memory: $TOTAL_MEMORY bytes"
echo "Avg per session: $(($TOTAL_MEMORY / $TOTAL_SESSIONS)) bytes"
```

### Session Expiry Distribution

```bash
# Check TTL distribution
redis-cli --scan --pattern "session:*" | head -1000 | while read key; do
  redis-cli TTL "$key"
done | sort -n | uniq -c

# Expected output:
#   5 -2    # Expired (cleaned up by Redis)
#  50 600   # Expires in 10 minutes
# 200 1800  # Expires in 30 minutes
# 500 3000  # Expires in 50 minutes
# 245 3600  # Just created (1 hour TTL)
```

### Grafana Dashboard Queries

**Panel: Active Sessions Count**
```promql
count(redis_key{pattern="session:*"})
```

**Panel: Session Creation Rate**
```promql
rate(redis_key_creates{pattern="session:*"}[5m])
```

**Panel: Session Expiry Rate**
```promql
rate(redis_key_expirations{pattern="session:*"}[5m])
```

**Panel: Expired Sessions Not Cleaned (Orphans)**
```promql
count(redis_key{pattern="session:*", ttl="-1"})
```

---

## Troubleshooting

### Problem 1: Sessions Not Expiring Automatically

**Symptoms:**
- Session count growing over time
- Redis memory usage increasing
- Many sessions with TTL = -1 (no expiry)

**Diagnosis:**
```bash
# Check for sessions without TTL
redis-cli --scan --pattern "session:*" | head -1000 | while read key; do
  ttl=$(redis-cli TTL "$key")
  if [ "$ttl" = "-1" ]; then
    echo "$key has no TTL"
  fi
done | wc -l
```

**Causes:**
1. Bug in session creation code (TTL not set)
2. Redis persistence config issue (TTL lost on restart)
3. Manual SET command without EX/PX flag

**Solution:**
```bash
# Short-term: Manual cleanup (Procedure 1)
python3 scripts/clear_oauth2_sessions.py

# Long-term: Fix session creation code
# Ensure all SET commands use SETEX or SET ... EX
# Example: redis.setex("session:abc", 3600, json.dumps(session_data))
```

---

### Problem 2: Script Fails with "Redis Connection Error"

**Symptoms:**
```
redis.exceptions.ConnectionError: Error connecting to Redis
```

**Diagnosis:**
```bash
# Test Redis connectivity
redis-cli PING
# Expected: PONG

# Check Redis is running
ps aux | grep redis-server

# Check Redis port
netstat -an | grep 6379
```

**Causes:**
1. Redis not running
2. Wrong host/port configuration
3. Firewall blocking connection

**Solution:**
```bash
# Start Redis (if not running)
sudo systemctl start redis

# Check Redis connection details in script
grep "REDIS_URL" scripts/clear_oauth2_sessions.py
# Should match actual Redis endpoint

# Test connection with explicit host/port
redis-cli -h localhost -p 6379 PING
```

---

### Problem 3: Mass Deletion Causes Redis Latency Spike

**Symptoms:**
- Application timeouts during session cleanup
- Redis latency alerts in Grafana
- Slow API responses

**Causes:**
- Deleting too many keys at once (blocking Redis)
- Using DEL instead of UNLINK
- No rate limiting between batches

**Solution:**
```bash
# Use cleanup script (already implements UNLINK + rate limiting)
python3 scripts/clear_oauth2_sessions.py --all

# Manual deletion with rate limiting
redis-cli --scan --pattern "session:*" | while read key; do
  redis-cli UNLINK "$key"  # UNLINK is asynchronous (non-blocking)
  sleep 0.01  # 10ms delay between deletions
done
```

---

## Audit Logging

### Session Cleanup Audit Log

**Location:** `/var/log/session-cleanup-audit.log` (or syslog)

**Format:**
```
2025-11-26T10:00:00Z | Cleanup Started | User: admin@company.com | Mode: dry-run | Filters: expired_only
2025-11-26T10:00:05Z | Sessions Scanned | Total: 1000 | Expired: 150 | Valid: 850
2025-11-26T10:00:10Z | Cleanup Completed | Deleted: 150 | Kept: 850 | Errors: 0
```

### View Audit Logs

```bash
# Recent cleanup operations
tail -50 /var/log/session-cleanup-audit.log

# Cleanup operations today
grep "$(date +%Y-%m-%d)" /var/log/session-cleanup-audit.log

# Mass logout events
grep "Mode: all" /var/log/session-cleanup-audit.log
```

---

## Safety Checklist

Before running any cleanup operation, verify:

- [ ] **Backup created** (if mass deletion)
- [ ] **Dry-run executed** (reviewed proposed deletions)
- [ ] **Authorization obtained** (if `--all` flag)
- [ ] **Off-peak hours** (if production mass deletion)
- [ ] **Monitoring ready** (Grafana/Prometheus alerts active)
- [ ] **Rollback plan** (session backup available)
- [ ] **Notification drafted** (user communication ready)
- [ ] **On-call aware** (platform team notified)

---

## Related Documentation

- **ADR-015:** OAuth2/OIDC Authentication Architecture (session design)
- **session-key-rotation.md:** Session Secret Rotation Procedures (related to mass logout)
- **auth0-idp-outage.md:** IdP Outage Response (mTLS fallback sessions)
- **P2T3-Phase3_Component3_Plan_v2.md:** Session Management Implementation Plan ([archive](../ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component3_Plan_v2.md))

---

## Contact

**On-Call:** Check PagerDuty rotation for platform-team

**Email:** platform-team@company.com

**Slack:** #platform-incidents

---

**Version:** 1.0

**Last Tested:** 2025-11-26 (staging environment)

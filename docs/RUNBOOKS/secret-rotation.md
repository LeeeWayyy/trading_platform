# Secret Rotation Runbook

**Purpose:** Execute 90-day secret rotation with zero downtime

**Owner:** @platform-team

**Last Updated:** 2025-11-06

**Related:** ADR-0017, secrets-migration.md

---

## Overview

This runbook guides operators through rotating secrets (Alpaca API keys, database passwords) every 90 days to maintain security compliance. The workflow supports **zero-downtime rotation** via grace periods.

**Estimated Time:** 10-15 minutes per secret

**Prerequisites:**
- Secrets backend accessible (Vault or AWS Secrets Manager)
- Write permissions to secrets backend
- Access to Alpaca dashboard (for API key rotation)
- Database admin access (for password rotation)
- Services running in production

---

## Rotation Schedule

**Mandatory Frequency:** Every 90 days

**Reminder Mechanism:**
- Calendar reminder: 85 days after last rotation (5-day warning)
- Monitoring alert: 88 days after last rotation (2-day final warning)
- Compliance requirement: 90-day maximum (PCI DSS, SOC2)

**Rotation Order (Recommended):**
1. Database password (least disruptive)
2. Alpaca API keys (requires Alpaca dashboard access)

---

## Pre-Rotation Checklist

- [ ] **Backup current secrets** (emergency rollback)
- [ ] **Verify services healthy** (`curl /health` on all services)
- [ ] **Check trading status** (not during active trading hours if possible)
- [ ] **Notify team** (Slack/email notification 24h advance notice)
- [ ] **Review rollback plan** (see "Emergency Rollback" section)

---

## Rotation Workflow

### Part A: Database Password Rotation

**Goal:** Rotate PostgreSQL password with zero downtime

#### Step 1: Generate New Password

```bash
# Generate secure password (24 characters, alphanumeric + symbols)
NEW_DB_PASSWORD=$(openssl rand -base64 18 | tr -d "=+/")
echo "New password generated (keep secure): $NEW_DB_PASSWORD"
```

#### Step 2: Update Database User Password

```bash
# Connect to database as admin
psql -h localhost -U postgres -d trader

# Update password (keep old password valid during grace period - PostgreSQL doesn't support dual passwords)
ALTER USER trader PASSWORD '<NEW_DB_PASSWORD>';

# Verify connection with new password
\q
psql -h localhost -U trader -d trader -W  # Enter new password when prompted
# Should connect successfully
```

**Note:** PostgreSQL doesn't support multiple active passwords. Grace period handled via service restart coordination.

#### Step 3: Update Secret in Backend

```bash
# Vault:
vault kv put secret/prod/database/password value="$NEW_DB_PASSWORD"

# AWS:
aws secretsmanager update-secret \
  --secret-id prod/database/password \
  --secret-string "$NEW_DB_PASSWORD" \
  --region us-east-1
```

#### Step 4: Rolling Service Restart (Zero Downtime)

```bash
# Restart services one at a time to pick up new password
# (Credential cache invalidates on restart)

systemctl restart signal-service
sleep 10  # Wait for health check
curl http://localhost:8000/health | jq '.status'  # Should return "healthy"

systemctl restart orchestrator
sleep 10
curl http://localhost:8001/health | jq '.status'  # Should return "healthy"

# Note: execution-gateway, market-data-service don't use database (skip)
```

#### Step 5: Log Rotation to Database

```bash
python scripts/rotate_secrets.py log \
  --secret-name database/password \
  --rotated-by $(whoami) \
  --rotation-date $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Inserts row into secret_rotation_log table:
# | secret_name         | rotated_by | rotation_date        | status  |
# |--------------------|------------|----------------------|---------|
# | database/password | ops-user   | 2025-11-06T14:30:00Z | SUCCESS |
```

**Success Criteria:**
- ✅ New password works in PostgreSQL
- ✅ Secret updated in backend
- ✅ All services restarted and healthy
- ✅ Rotation logged to database

---

### Part B: Alpaca API Key Rotation

**Goal:** Rotate Alpaca API keys with 24-hour grace period

#### Step 1: Generate New API Key (Alpaca Dashboard)

1. **Login to Alpaca Dashboard:**
   - Paper trading: https://app.alpaca.markets/paper/dashboard/overview
   - Live trading: https://app.alpaca.markets/live/dashboard/overview

2. **Navigate to API Keys:**
   - Click "Generate New Key" (keep old key active)
   - Label: `paper-trading-2025-11-06` (descriptive name with date)
   - Copy `API Key ID` and `Secret Key` (keep secure)

3. **Verify new key works:**
   ```bash
   curl -X GET https://paper-api.alpaca.markets/v2/account \
     -H "APCA-API-KEY-ID: <NEW_KEY_ID>" \
     -H "APCA-API-SECRET-KEY: <NEW_SECRET_KEY>"
   # Should return account details (not 401 Unauthorized)
   ```

#### Step 2: Update Secrets in Backend

```bash
# Vault:
vault kv put secret/prod/alpaca/api_key_id value="<NEW_KEY_ID>"
vault kv put secret/prod/alpaca/api_secret_key value="<NEW_SECRET_KEY>"

# AWS:
aws secretsmanager update-secret \
  --secret-id prod/alpaca/api_key_id \
  --secret-string "<NEW_KEY_ID>" \
  --region us-east-1

aws secretsmanager update-secret \
  --secret-id prod/alpaca/api_secret_key \
  --secret-string "<NEW_SECRET_KEY>" \
  --region us-east-1
```

#### Step 3: Graceful Service Restart (Zero Downtime)

```bash
# Restart services that use Alpaca API
systemctl restart execution-gateway
sleep 10
curl http://localhost:8002/health | jq '.status'  # Should return "healthy"

systemctl restart market-data-service
sleep 10
curl http://localhost:8003/health | jq '.status'  # Should return "healthy"

systemctl restart orchestrator  # Uses Alpaca for paper run
sleep 10
curl http://localhost:8001/health | jq '.status'  # Should return "healthy"
```

#### Step 4: Verify New Key Works in Production

```bash
# Test live trading flow (dry-run mode)
python -m apps.orchestrator.main --dry-run

# Should log:
# INFO: Initialized AlpacaClient with key_id=<NEW_KEY_ID>
# INFO: Account fetched successfully (buying_power=$100000)
# No errors should appear
```

#### Step 5: Deprecate Old Key (24-Hour Grace Period)

**Immediate (Day 0):**
```bash
# Mark old key as deprecated in backend (don't delete yet)
vault kv metadata put secret/prod/alpaca/api_key_id \
  custom_metadata=rotation_date=$(date -u +"%Y-%m-%dT%H:%M:%SZ"),status=deprecated

# AWS: Use tags
aws secretsmanager tag-resource \
  --secret-id prod/alpaca/api_key_id \
  --tags Key=rotation_date,Value=$(date -u +"%Y-%m-%d") Key=status,Value=deprecated
```

**After 24 Hours (Day 1):**
```bash
# Delete old key from Alpaca dashboard
# (Navigate to API Keys → click "Delete" on old key)

# Confirm deletion:
curl -X GET https://paper-api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: <OLD_KEY_ID>" \
  -H "APCA-API-SECRET-KEY: <OLD_SECRET_KEY>"
# Should return: 401 Unauthorized (expected)
```

#### Step 6: Log Rotation to Database

```bash
python scripts/rotate_secrets.py log \
  --secret-name alpaca/api_key \
  --rotated-by $(whoami) \
  --rotation-date $(date -u +"%Y-%m-%dT%H:%M:%SZ")
```

**Success Criteria:**
- ✅ New API key works in Alpaca API
- ✅ Secrets updated in backend
- ✅ All services restarted and healthy
- ✅ Trading continues without interruption
- ✅ Old key deleted after 24h grace period
- ✅ Rotation logged to database

---

## Emergency Rollback

**Scenario:** New secret doesn't work, need to revert immediately

### Rollback Database Password

```bash
# Step 1: Restore old password in database
psql -h localhost -U postgres -d trader
ALTER USER trader PASSWORD '<OLD_DB_PASSWORD>';  # From backup
\q

# Step 2: Revert secret in backend
vault kv put secret/prod/database/password value="<OLD_DB_PASSWORD>"

# Step 3: Restart services
systemctl restart signal-service orchestrator
```

### Rollback Alpaca API Key

```bash
# Step 1: Revert secrets in backend
vault kv put secret/prod/alpaca/api_key_id value="<OLD_KEY_ID>"
vault kv put secret/prod/alpaca/api_secret_key value="<OLD_SECRET_KEY>"

# Step 2: Restart services
systemctl restart execution-gateway market-data-service orchestrator

# Step 3: Verify old key still works
curl -X GET https://paper-api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: <OLD_KEY_ID>" \
  -H "APCA-API-SECRET-KEY: <OLD_SECRET_KEY>"
# Should return account details (if within 24h grace period)

# Step 4: Delete new key from Alpaca dashboard (if created)
```

**Estimated Rollback Time:** 5 minutes

---

## Troubleshooting

### Issue 1: Service fails to start after password rotation

**Symptoms:**
```
ERROR sqlalchemy.exc.OperationalError: password authentication failed for user "trader"
```

**Diagnosis:**
```bash
# Verify new password works manually
psql -h localhost -U trader -d trader -W
# Enter new password - should connect
```

**Fix:**
```bash
# Check if secret in backend matches database
vault kv get secret/prod/database/password
# Compare value with password used in ALTER USER command

# If mismatch, update secret:
vault kv put secret/prod/database/password value="<CORRECT_PASSWORD>"
systemctl restart signal-service
```

---

### Issue 2: Alpaca API returns 401 after key rotation

**Symptoms:**
```
ERROR AlpacaValidationError: 401 Unauthorized (invalid API key)
```

**Diagnosis:**
```bash
# Test new key manually
curl -X GET https://paper-api.alpaca.markets/v2/account \
  -H "APCA-API-KEY-ID: <NEW_KEY_ID>" \
  -H "APCA-API-SECRET-KEY: <NEW_SECRET_KEY>"
# Should return account details, not 401
```

**Fix:**
```bash
# If 401: Key not activated yet in Alpaca dashboard
# Check dashboard: https://app.alpaca.markets/paper/dashboard/overview → API Keys
# Ensure new key is marked as "Active"

# If still 401: Copied key incorrectly
# Re-copy key from dashboard, update backend:
vault kv put secret/prod/alpaca/api_key_id value="<CORRECT_KEY_ID>"
vault kv put secret/prod/alpaca/api_secret_key value="<CORRECT_SECRET_KEY>"
systemctl restart execution-gateway
```

---

### Issue 3: Old key deleted before grace period ends

**Symptoms:**
```
ERROR AlpacaValidationError: 401 Unauthorized (cached credentials invalid)
```

**Diagnosis:**
```bash
# Check if old key still exists in Alpaca dashboard
# If deleted prematurely, services using cached old key will fail after cache expires (1 hour)
```

**Fix:**
```bash
# Can't restore old key (Alpaca doesn't allow)
# Must force all services to use new key immediately:

# Restart all services (invalidates cache):
systemctl restart execution-gateway market-data-service orchestrator

# Verify new key works:
curl /health | jq '.secrets_backend'  # Should return "vault" (cache refreshed)
```

---

## Validation

**Post-rotation validation checklist:**

- [ ] **Secrets updated:** `vault kv get secret/prod/<secret_name>` shows new value
- [ ] **Services healthy:** All health checks return `{"status": "healthy"}`
- [ ] **Logs clean:** No authentication errors in logs
- [ ] **Trading functional:** `make paper-run` completes successfully
- [ ] **Old credentials invalid:** Old password/key returns 401 Unauthorized (after grace period)
- [ ] **Rotation logged:** Query `secret_rotation_log` table shows latest rotation

**SQL Query to Verify Rotation:**
```sql
SELECT secret_name, rotated_by, rotation_date, status
FROM secret_rotation_log
WHERE rotation_date >= NOW() - INTERVAL '7 days'
ORDER BY rotation_date DESC;
```

---

## Compliance

**Regulatory Requirements:**
- **PCI DSS 3.6.4:** Passwords must be rotated every 90 days
- **SOC2:** Credentials must be rotated regularly and logged for audit
- **NIST 800-53:** Access credentials must be reviewed and rotated periodically

**Audit Trail:**
- All rotations logged to `secret_rotation_log` database table
- Logs retained for 2 years (compliance requirement)
- Rotation script generates audit report: `scripts/audit_secrets.py report --last 90d`

---

## Automation (Future Enhancement)

**Current State:** Manual rotation (operator-initiated)

**Future Improvement (P3):**
- Automated rotation reminders via Slack/email (85 days after last rotation)
- Cron job triggers rotation script automatically (90-day schedule)
- Terraform/Ansible integration for infrastructure-as-code
- Dynamic secrets (Vault auto-generates DB credentials, auto-rotates)

**Tracking Issue:** Create P3 epic for "Automated Secret Rotation"

---

## References

- ADR-0017: Secrets Management Architecture (zero-downtime rotation design)
- secrets-migration.md: Initial migration from `.env` to secrets backend
- Alpaca API Documentation: https://docs.alpaca.markets/reference/
- Vault Secret Rotation: https://developer.hashicorp.com/vault/tutorials/db-credentials/database-secrets
- AWS Secrets Manager Rotation: https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html

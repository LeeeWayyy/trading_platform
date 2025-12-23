# Session Encryption Key Rotation Runbook

**Purpose:** Rotate `SESSION_ENCRYPTION_KEY` to maintain OAuth2/web console session security

**Owner:** @platform-team

**Last Updated:** 2025-12-22

**Related:** oauth2-session-cleanup.md, auth0-idp-outage.md, secret-rotation.md

---

## Overview

This runbook guides operators through rotating the `SESSION_ENCRYPTION_KEY` used to encrypt and decrypt OAuth2 session data (AES-256-GCM). The key is required by both:

- `web_console` (OAuth2 profile)
- `auth_service` (OAuth2 profile)

**Current Behavior (IMPORTANT):**
- **Single key only** (no multi-key grace period support)
- Rotation **invalidates all active sessions** immediately
- Plan a short maintenance window and force re-login

**Estimated Time:** 10-20 minutes

**Prerequisites:**
- Access to secrets backend (Vault/AWS Secrets Manager)
- Permission to redeploy web_console + auth_service
- Ability to clear Redis session DB (DB 1)

---

## When to Rotate

### Scheduled Rotation
- **Mandatory:** Every 90 days
- **Recommended:** Every 30 days

### Emergency Rotation
- Key compromise suspected (logs, secrets leak, incident)
- OAuth2/session security audit failure

---

## Key Requirements

- **Length:** 32 bytes (256-bit)
- **Encoding:** Base64
- **Generation command:**

```bash
python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

---

## Procedure 1: Scheduled Rotation (Planned Downtime)

### Step 1: Pre-Rotation Checklist

- [ ] Confirm OAuth2 profile is in use (`web_console_oauth2`, `auth_service` running)
- [ ] Notify stakeholders of forced re-login
- [ ] Verify services are healthy

```bash
curl -f https://YOUR-DOMAIN/health
```

### Step 2: Backup Current Key

```bash
# Vault example
vault kv get -field=SESSION_ENCRYPTION_KEY secret/trading-platform/web-console > /tmp/old_session_key.b64

# AWS Secrets Manager example
aws secretsmanager get-secret-value --secret-id trading-platform/web-console/session-key \
  --query 'SecretString' --output text | jq -r '.SESSION_ENCRYPTION_KEY' > /tmp/old_session_key.b64

chmod 600 /tmp/old_session_key.b64
```

### Step 3: Generate New Key

```bash
NEW_KEY=$(python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")

echo "$NEW_KEY" | wc -c
# Expected: 45 (44 chars + newline)
```

### Step 4: Update Secrets Backend

```bash
# Vault example
vault kv put secret/trading-platform/web-console SESSION_ENCRYPTION_KEY="$NEW_KEY"

# AWS Secrets Manager example (JSON payload)
aws secretsmanager put-secret-value \
  --secret-id trading-platform/web-console/session-key \
  --secret-string "{\"SESSION_ENCRYPTION_KEY\":\"$NEW_KEY\"}"
```

### Step 5: Redeploy Services

```bash
# Kubernetes example
kubectl rollout restart deployment/web-console
kubectl rollout restart deployment/auth-service

kubectl rollout status deployment/web-console
kubectl rollout status deployment/auth-service
```

### Step 6: Invalidate Existing Sessions

**Why:** Old sessions cannot be decrypted with the new key.

```bash
# Clear OAuth2 sessions from Redis DB 1
redis-cli -n 1 --scan --pattern "session:*" | xargs -L 1000 redis-cli -n 1 DEL

# Verify cleanup
redis-cli -n 1 --scan --pattern "session:*" | wc -l
# Expected: 0
```

### Step 7: Validate

```bash
# New login should succeed
curl -k https://YOUR-DOMAIN/login

# Ensure no key errors in logs
kubectl logs -l app=web-console --since=10m | grep -i "SESSION_ENCRYPTION_KEY" -n
kubectl logs -l app=auth-service --since=10m | grep -i "SESSION_ENCRYPTION_KEY" -n
```

---

## Procedure 2: Emergency Rotation (Key Compromise)

**Goal:** Rotate immediately and force logout.

1. Generate new key (Step 3)
2. Update secrets backend (Step 4)
3. Redeploy services (Step 5)
4. Clear Redis sessions (Step 6)
5. Notify users to re-authenticate

**Slack/Email Template:**

```
:warning: OAuth2 Session Key Rotated

Timestamp: 2025-12-22T12:00:00Z
Impact: All sessions invalidated; re-login required
Reason: Security rotation / suspected key exposure
```

---

## Troubleshooting

### Error: "SESSION_ENCRYPTION_KEY environment variable not set"
- Ensure secret is injected into both `web_console_oauth2` and `auth_service`
- Verify Kubernetes deployment env vars or compose env

### Error: "SESSION_ENCRYPTION_KEY must decode to 32 bytes"
- Key is not base64 or not 32 bytes
- Regenerate using the command in **Key Requirements**

### Users still logged in after rotation
- Sessions not cleared from Redis DB 1
- Re-run the Redis cleanup command and confirm DB index

---

## Notes / Limitations

- **No grace period support yet.** Multi-key rotation is not implemented in code.
- Rotation will always force a re-login for all users.
- Consider scheduling during low-traffic windows.

---

## Related Documentation

- `docs/RUNBOOKS/oauth2-session-cleanup.md`
- `docs/RUNBOOKS/secret-rotation.md`
- `docs/RUNBOOKS/auth0-idp-outage.md`

---

**Version:** 2.0

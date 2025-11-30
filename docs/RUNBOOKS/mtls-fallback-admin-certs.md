# mTLS Fallback Admin Certificate Management Runbook

**Purpose:** Manage admin client certificates for mTLS fallback authentication during Auth0 IdP outages

**Owner:** @security-team, @platform-team

**Last Updated:** 2025-11-26

**Related:** ADR-015, auth0-idp-outage.md, web-console-cert-rotation.md, P2T3-Phase3_Component6-7_Plan.md

---

## Overview

This runbook guides security administrators through managing admin client certificates used for mTLS fallback authentication. These certificates provide emergency admin-only access when Auth0 IdP is unavailable.

**Key Characteristics:**
- **Purpose:** Emergency authentication during Auth0 outages
- **Users:** Administrators only (pre-distributed certificates)
- **Lifetime:** Maximum 7 days (enforced by validation logic)
- **Rotation:** Weekly or on-demand
- **Revocation:** Via CRL (Certificate Revocation List)

**Estimated Time:** 15-20 minutes per certificate issuance/rotation

**Prerequisites:**
- Access to internal CA (Certificate Authority)
- Root/intermediate CA key material
- CRL distribution point operational
- Admin user list with CNs (Common Names)

---

## Certificate Lifetime Policy

### Enforcement

**Maximum Lifetime:** 7 days (`notAfter - notBefore <= 7 days`)

**Validation Location:** `apps/web_console/auth/mtls_fallback.py:254-272`

```python
# Certificate lifetime check
if lifetime > self.max_cert_lifetime:  # Default: 7 days
    return CertificateInfo(
        valid=False,
        error=f"Certificate lifetime ({lifetime_days:.1f} days) exceeds maximum (7 days)"
    )
```

**Rationale:**
- Limits exposure window if certificate is compromised
- Forces regular rotation (reduces stale credentials)
- Aligns with emergency/temporary access nature

---

## Certificate Requirements

### Subject Fields

| Field | Required | Example | Notes |
|-------|----------|---------|-------|
| Common Name (CN) | Yes | `admin.trading-platform.local` | Must be in `MTLS_ADMIN_CN_ALLOWLIST` |
| Organization (O) | Recommended | `Trading Platform Inc.` | For identification |
| Organizational Unit (OU) | Recommended | `Security Team` | Department/team |
| Country (C) | Optional | `US` | 2-letter country code |
| State/Province (ST) | Optional | `California` | Full state name |
| Locality (L) | Optional | `San Francisco` | City name |

**CRITICAL:** The `CN` field must match an entry in the `MTLS_ADMIN_CN_ALLOWLIST` environment variable.

### Key Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Key Algorithm | RSA | Widely supported |
| Key Size | 2048 bits minimum, 4096 bits recommended | Balance security/performance |
| Signature Algorithm | SHA256WithRSA | Modern, secure |
| Validity Period | **≤ 7 days** | Hard limit enforced |
| Key Usage | Digital Signature, Key Encipherment | Standard for TLS client auth |
| Extended Key Usage | TLS Web Client Authentication | `1.3.6.1.5.5.7.3.2` |

---

## Admin CN Allowlist Management

### View Current Allowlist

```bash
# Production environment
kubectl exec deployment/web-console -- env | grep MTLS_ADMIN_CN_ALLOWLIST

# Example output:
# MTLS_ADMIN_CN_ALLOWLIST=admin.trading-platform.local,emergency-admin.trading-platform.local
```

### Update Allowlist

```bash
# Add new admin
CURRENT_LIST=$(kubectl exec deployment/web-console -- env | grep MTLS_ADMIN_CN_ALLOWLIST | cut -d= -f2)
NEW_CN="new-admin.trading-platform.local"
UPDATED_LIST="$CURRENT_LIST,$NEW_CN"

# Update environment variable
kubectl set env deployment/web-console MTLS_ADMIN_CN_ALLOWLIST="$UPDATED_LIST"

# Restart to apply (rolling restart, zero downtime)
kubectl rollout restart deployment/web-console
kubectl rollout status deployment/web-console
```

### Remove Admin from Allowlist

```bash
# Remove admin (requires certificate revocation + allowlist update)
CURRENT_LIST=$(kubectl exec deployment/web-console -- env | grep MTLS_ADMIN_CN_ALLOWLIST | cut -d= -f2)
REMOVE_CN="old-admin.trading-platform.local"

# Remove from comma-separated list
UPDATED_LIST=$(echo "$CURRENT_LIST" | sed "s/$REMOVE_CN,\?//g" | sed 's/,$//')

# Update environment variable
kubectl set env deployment/web-console MTLS_ADMIN_CN_ALLOWLIST="$UPDATED_LIST"

# Restart to apply
kubectl rollout restart deployment/web-console
```

**IMPORTANT:** Always revoke the certificate via CRL before removing from allowlist (see "Certificate Revocation" section).

---

## Certificate Issuance

### Procedure 1: Issue New Admin Certificate

**Use Case:** Onboard new administrator for emergency access

#### Step 1: Verify Admin Authorization

**Approval Required:** VP Engineering or Security Officer

```bash
# Document approval in audit log
echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") | New Admin Certificate Request | CN: $CN | Requested by: $USER | Approved by: [APPROVER_NAME]" >> /var/log/mtls-admin-cert-audit.log
```

#### Step 2: Generate CSR (Certificate Signing Request)

**Option A: Admin generates CSR themselves (more secure - private key never leaves admin's machine)**

```bash
# Admin runs this on their local machine
CN="admin.trading-platform.local"

# Generate private key + CSR
openssl req -new -newkey rsa:4096 -nodes \
  -keyout admin.key \
  -out admin.csr \
  -subj "/C=US/ST=California/L=San Francisco/O=Trading Platform Inc./OU=Security Team/CN=$CN"

# Secure private key
chmod 600 admin.key

# Send CSR to security team (CSR is safe to transmit - contains no secrets)
cat admin.csr
```

**Option B: Security team generates key + CSR (requires secure key distribution)**

```bash
# Security team generates on secure CA server
CN="admin.trading-platform.local"

# Generate private key
openssl genrsa -out admin.key 4096
chmod 600 admin.key

# Generate CSR from private key
openssl req -new -key admin.key -out admin.csr \
  -subj "/C=US/ST=California/L=San Francisco/O=Trading Platform Inc./OU=Security Team/CN=$CN"

# Key must be securely transmitted to admin (encrypted channel, password-protected archive, etc.)
```

#### Step 3: Sign CSR with CA (7-Day Lifetime)

```bash
# Security team runs this on CA server
CA_CERT="ca.crt"
CA_KEY="ca.key"  # Must be protected (HSM, encrypted storage, etc.)

CSR="admin.csr"
OUTPUT_CERT="admin.crt"

# Create OpenSSL config for 7-day certificate with client auth extension
cat > /tmp/admin-cert.conf <<EOF
[ req ]
distinguished_name = req_distinguished_name

[ v3_client ]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

# Sign CSR with 7-day validity
openssl x509 -req \
  -in "$CSR" \
  -CA "$CA_CERT" \
  -CAkey "$CA_KEY" \
  -CAcreateserial \
  -out "$OUTPUT_CERT" \
  -days 7 \
  -sha256 \
  -extfile /tmp/admin-cert.conf \
  -extensions v3_client

# Cleanup config
rm /tmp/admin-cert.conf
```

#### Step 4: Verify Certificate

```bash
# Check certificate validity period (must be ≤ 7 days)
openssl x509 -in admin.crt -noout -dates
# Expected output:
# notBefore=Nov 26 10:00:00 2025 GMT
# notAfter=Dec  3 10:00:00 2025 GMT  # Exactly 7 days later

# Verify lifetime calculation
NOT_BEFORE=$(openssl x509 -in admin.crt -noout -startdate | cut -d= -f2)
NOT_AFTER=$(openssl x509 -in admin.crt -noout -enddate | cut -d= -f2)
LIFETIME_SECONDS=$(( $(date -d "$NOT_AFTER" +%s) - $(date -d "$NOT_BEFORE" +%s) ))
LIFETIME_DAYS=$(echo "scale=2; $LIFETIME_SECONDS / 86400" | bc)
echo "Certificate lifetime: $LIFETIME_DAYS days"
# Expected: ≤ 7.00 days

# Verify CN
openssl x509 -in admin.crt -noout -subject | grep CN
# Expected: CN = admin.trading-platform.local

# Verify extended key usage (clientAuth)
openssl x509 -in admin.crt -noout -text | grep -A 1 "Extended Key Usage"
# Expected: TLS Web Client Authentication

# Verify signature
openssl verify -CAfile ca.crt admin.crt
# Expected: admin.crt: OK
```

#### Step 5: Update Admin CN Allowlist

```bash
# Add new admin CN to allowlist
CN="admin.trading-platform.local"

# Get current allowlist
CURRENT_LIST=$(kubectl exec deployment/web-console -- env | grep MTLS_ADMIN_CN_ALLOWLIST | cut -d= -f2)

# Check if CN already exists
if echo "$CURRENT_LIST" | grep -q "$CN"; then
  echo "CN already in allowlist: $CN"
else
  # Append to allowlist
  UPDATED_LIST="$CURRENT_LIST,$CN"
  kubectl set env deployment/web-console MTLS_ADMIN_CN_ALLOWLIST="$UPDATED_LIST"
  kubectl rollout restart deployment/web-console
fi
```

#### Step 6: Distribute Certificate to Admin

**Secure Distribution Methods:**

1. **Password-Protected PKCS#12 Bundle (Recommended)**
   ```bash
   # Create PKCS#12 bundle with private key + certificate + CA cert
   openssl pkcs12 -export \
     -in admin.crt \
     -inkey admin.key \
     -certfile ca.crt \
     -name "Admin mTLS Fallback Certificate" \
     -out admin.p12 \
     -passout pass:[STRONG_PASSWORD]

   # Send to admin via encrypted channel (1Password, LastPass, secure email)
   # Communicate password via separate channel (phone, Slack DM)
   ```

2. **Separate Files with Encryption**
   ```bash
   # Encrypt private key with password
   openssl rsa -in admin.key -aes256 -out admin.key.enc

   # Send encrypted key + certificate + CA cert + password separately
   # - admin.key.enc via email
   # - admin.crt via email
   # - ca.crt via email
   # - Password via phone/Slack
   ```

#### Step 7: Admin Installation (Browser)

**Chrome/Edge:**
```
1. Open Settings → Privacy and Security → Security → Manage Certificates
2. Import → Browse → Select admin.p12 file
3. Enter password
4. Certificate installed in "Personal" store
5. Restart browser
```

**Firefox:**
```
1. Open Settings → Privacy & Security → Certificates → View Certificates
2. Your Certificates → Import
3. Browse → Select admin.p12 file
4. Enter password
5. Certificate installed
6. Restart browser
```

**Safari (macOS):**
```
1. Double-click admin.p12 file
2. Keychain Access prompts for password
3. Select "login" keychain
4. Enter password
5. Certificate installed
6. System Preferences → Profiles → Verify certificate present
```

#### Step 8: Test Authentication

```bash
# Admin tests certificate authentication (staging first)
curl -k --cert admin.crt --key admin.key https://staging.YOUR-DOMAIN/health

# Expected: HTTP 200 (if fallback mode active) or 401 (if normal OAuth2 active)

# Browser test:
# 1. Navigate to https://staging.YOUR-DOMAIN
# 2. If fallback mode active, browser prompts for certificate selection
# 3. Select admin certificate
# 4. Should authenticate successfully
```

#### Step 9: Audit Logging

```bash
# Record certificate issuance
echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") | Certificate Issued | CN: $CN | Serial: $(openssl x509 -in admin.crt -noout -serial | cut -d= -f2) | Valid Until: $(openssl x509 -in admin.crt -noout -enddate | cut -d= -f2)" >> /var/log/mtls-admin-cert-audit.log

# Set reminder for rotation (6 days from now - 1 day before expiry)
EXPIRY_DATE=$(openssl x509 -in admin.crt -noout -enddate | cut -d= -f2)
ROTATION_DATE=$(date -d "$EXPIRY_DATE - 1 day" +"%Y-%m-%d")
echo "Set calendar reminder: Rotate $CN certificate on $ROTATION_DATE"
```

---

## Certificate Rotation

### Procedure 2: Rotate Expiring Admin Certificate

**Use Case:** Certificate expiring within 24 hours

**Frequency:** Weekly (every 6 days recommended - 1 day before expiry)

#### Step 1: Identify Expiring Certificates

```bash
# Check application logs for expiry warnings
kubectl logs -l app=web-console --since=24h | grep "Certificate expiring soon"

# Expected output:
# Certificate expiring soon | cn: admin.trading-platform.local | expires_in_hours: 18.5

# Manual check
openssl x509 -in admin.crt -noout -checkend 86400
# Exit code 0 = valid for >24h, 1 = expires <24h
```

#### Step 2: Issue New Certificate

```bash
# Follow Procedure 1 (Certificate Issuance)
# Use SAME CN as expiring certificate (replace in-place)
# New certificate will have fresh 7-day validity period
```

#### Step 3: Distribute New Certificate

```bash
# Send new PKCS#12 bundle to admin
# Admin replaces old certificate in browser (same installation steps)
```

#### Step 4: Verify Old Certificate Expires Naturally

```bash
# Old certificate expires automatically (no revocation needed for rotation)
# mTLS validator rejects expired certificates (apps/web_console/auth/mtls_fallback.py:289-300)

# Monitor for authentication using old certificate (should fail after expiry)
kubectl logs -l app=web-console --since=1h | grep "Certificate expired"
```

---

## Certificate Revocation

### Procedure 3: Revoke Compromised Certificate

**Use Case:** Certificate compromised, lost, or admin leaving organization

**Impact:** Immediate - certificate invalid as soon as CRL updated (1-hour cache)

#### Step 1: Confirm Revocation Authorization

**Approval Required:** VP Engineering or Security Officer

```bash
# Document approval
echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") | Certificate Revocation | CN: $CN | Serial: $SERIAL | Reason: [COMPROMISE/TERMINATION/LOST] | Approved by: [APPROVER_NAME]" >> /var/log/mtls-admin-cert-audit.log
```

#### Step 2: Add Certificate to CRL

```bash
# Revoke certificate using CA tools
CA_KEY="ca.key"
CERT_TO_REVOKE="admin.crt"
CRL_FILE="ca.crl"

# Get certificate serial number
SERIAL=$(openssl x509 -in "$CERT_TO_REVOKE" -noout -serial | cut -d= -f2)

# Create/update CRL with revocation
openssl ca -revoke "$CERT_TO_REVOKE" \
  -keyfile "$CA_KEY" \
  -cert ca.crt \
  -config /etc/ssl/openssl.cnf

# Generate updated CRL
openssl ca -gencrl \
  -keyfile "$CA_KEY" \
  -cert ca.crt \
  -out "$CRL_FILE" \
  -config /etc/ssl/openssl.cnf

# Verify certificate in CRL
openssl crl -in "$CRL_FILE" -noout -text | grep -A 5 "Serial Number: $SERIAL"
# Expected: Serial Number: [SERIAL] | Revocation Date: [DATE]
```

#### Step 3: Publish Updated CRL

```bash
# Copy CRL to distribution point
CRL_DIST_POINT="http://ca.trading-platform.local/crl/admin-ca.crl"

# Upload to HTTP server
scp "$CRL_FILE" ca-server:/var/www/html/crl/admin-ca.crl

# Verify CRL accessible
curl -I "$CRL_DIST_POINT"
# Expected: HTTP 200 OK

# Check CRL freshness
curl "$CRL_DIST_POINT" | openssl crl -inform DER -text -noout | grep -E "(Last Update|Next Update)"
# Expected: Last Update: [RECENT DATE] | Next Update: [FUTURE DATE]
```

#### Step 4: Wait for CRL Cache Refresh (Up to 1 Hour)

**CRL Cache TTL:** 1 hour (configured in `apps/web_console/auth/mtls_fallback.py:58`)

```bash
# Monitor for revoked certificate authentication attempts
kubectl logs -l app=web-console -f | grep "Certificate revoked"

# Expected after cache refresh:
# Certificate revoked | cn: admin.trading-platform.local | fingerprint: abc123... | serial: [SERIAL]
```

#### Step 5: Remove CN from Allowlist (Optional)

**Only if admin permanently leaving:**

```bash
# Remove CN from allowlist
CN="admin.trading-platform.local"
CURRENT_LIST=$(kubectl exec deployment/web-console -- env | grep MTLS_ADMIN_CN_ALLOWLIST | cut -d= -f2)
UPDATED_LIST=$(echo "$CURRENT_LIST" | sed "s/$CN,\?//g" | sed 's/,$//')

kubectl set env deployment/web-console MTLS_ADMIN_CN_ALLOWLIST="$UPDATED_LIST"
kubectl rollout restart deployment/web-console
```

**Note:** Revocation via CRL is sufficient to block authentication immediately. Removing from allowlist provides defense-in-depth.

---

## CRL Management

### CRL Distribution Point

**URL:** Configured in `MTLS_CRL_URL` environment variable

**Default:** `http://ca.trading-platform.local/crl/admin-ca.crl`

**Requirements:**
- HTTP accessible (public or internal network)
- Updated within 24 hours (enforced by validation logic)
- DER format (binary CRL)

### CRL Freshness Monitoring

```bash
# Check CRL last update time
curl -s "$MTLS_CRL_URL" | openssl crl -inform DER -text -noout | grep "Last Update"

# Expected: Last Update: Nov 26 10:00:00 2025 GMT (within 24 hours)

# Check CRL age in application logs
kubectl logs -l app=web-console --tail=100 | grep "CRL fetched successfully"
# Expected: last_update: [RECENT TIMESTAMP] (< 24h ago)
```

### CRL Failure Modes

**Fail-Secure Behavior:**
- If CRL fetch fails → ALL mTLS authentication rejected (fail-secure)
- If CRL too old (>24h) → ALL mTLS authentication rejected (fail-secure)
- If CRL unreachable → ALL mTLS authentication rejected (fail-secure)

**Prometheus Alert:** `MtlsCrlFetchFailure` (CRITICAL)

**Recovery:**
```bash
# Fix CRL distribution point
ssh ca-server

# Restart CRL HTTP server
sudo systemctl restart nginx

# Verify CRL accessible
curl -I http://ca.trading-platform.local/crl/admin-ca.crl
# Expected: HTTP 200 OK
```

---

## Monitoring & Alerts

### Prometheus Alerts

**Alert Definitions:** `infra/prometheus/alerts/oauth2.yml`

| Alert | Severity | Threshold | Response |
|-------|----------|-----------|----------|
| `MtlsCertificateExpiringSoon` | WARNING | Certificate expires <24h | Rotate certificate (Procedure 2) |
| `MtlsCrlFetchFailure` | CRITICAL | CRL fetch failed >2x | Fix CRL distribution point |
| `MtlsAuthFailureRateHigh` | CRITICAL | >10 auth failures/min | Investigate (check logs for reasons) |

### Grafana Dashboard

**Dashboard:** OAuth2 Sessions (`infra/grafana/dashboards/oauth2-sessions.json`)

**Key Panels:**
- Admin Certificate Expiry Timeline (table showing CN, expires_at)
- CRL Fetch Status (last successful fetch timestamp)
- mTLS Authentication Rate (success vs. failure)
- Certificate Revocation Count (from CRL)

### Log Queries

**Useful Grafana/Loki queries:**

```promql
# Certificate expiry warnings
{app="web-console"} |= "Certificate expiring soon"

# Revoked certificate authentication attempts
{app="web-console"} |= "Certificate revoked"

# CRL fetch errors
{app="web-console"} |= "CRL fetch failed"

# Successful mTLS authentication
{app="web-console"} |= "mTLS fallback authentication successful"
```

---

## Troubleshooting

### Problem 1: Certificate Rejected (Lifetime > 7 Days)

**Symptoms:**
```
Certificate lifetime (10.0 days) exceeds maximum (7 days)
```

**Cause:** Certificate issued with validity > 7 days

**Solution:**
```bash
# Re-issue certificate with -days 7 (not -days 10+)
openssl x509 -req -in admin.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out admin.crt -days 7 -sha256 -extfile admin-cert.conf -extensions v3_client

# Verify lifetime
openssl x509 -in admin.crt -noout -dates
```

---

### Problem 2: CN Not in Allowlist

**Symptoms:**
```
CN 'unknown-admin.trading-platform.local' not in admin allowlist
```

**Cause:** Certificate CN not in `MTLS_ADMIN_CN_ALLOWLIST` environment variable

**Solution:**
```bash
# Add CN to allowlist
kubectl set env deployment/web-console MTLS_ADMIN_CN_ALLOWLIST="existing-admin.local,unknown-admin.trading-platform.local"

# Restart service
kubectl rollout restart deployment/web-console
```

---

### Problem 3: CRL Fetch Failed (Authentication Blocked)

**Symptoms:**
```
CRL fetch failed - rejecting auth
ALL mTLS authentication attempts fail with "CRL check failed"
```

**Cause:** CRL distribution point unreachable

**Solution:**
```bash
# Test CRL URL from web-console pod
kubectl exec deployment/web-console -- curl -I http://ca.trading-platform.local/crl/admin-ca.crl

# If fails, check CA server
ssh ca-server
sudo systemctl status nginx
sudo systemctl restart nginx

# Verify CRL file exists
ls -lh /var/www/html/crl/admin-ca.crl

# Test from web-console pod again
kubectl exec deployment/web-console -- curl -I http://ca.trading-platform.local/crl/admin-ca.crl
# Expected: HTTP 200 OK
```

---

## Audit Trail

### Certificate Audit Log

**Location:** `/var/log/mtls-admin-cert-audit.log`

**Format:**
```
2025-11-26T10:00:00Z | Certificate Issued | CN: admin.trading-platform.local | Serial: 0x1A2B3C | Valid Until: Dec 3 10:00:00 2025 GMT | Issued By: security-team@company.com
2025-11-26T10:05:00Z | Certificate Distributed | CN: admin.trading-platform.local | Recipient: john.doe@company.com | Method: PKCS#12
2025-12-02T10:00:00Z | Certificate Rotation | CN: admin.trading-platform.local | Old Serial: 0x1A2B3C | New Serial: 0x4D5E6F
2025-12-05T15:00:00Z | Certificate Revocation | CN: admin.trading-platform.local | Serial: 0x4D5E6F | Reason: COMPROMISE | Approved By: vp-engineering@company.com
```

### Query Audit Log

```bash
# Recent certificate operations
tail -50 /var/log/mtls-admin-cert-audit.log

# Operations today
grep "$(date +%Y-%m-%d)" /var/log/mtls-admin-cert-audit.log

# All operations for specific CN
grep "CN: admin.trading-platform.local" /var/log/mtls-admin-cert-audit.log

# Revocations only
grep "Revocation" /var/log/mtls-admin-cert-audit.log
```

---

## Security Best Practices

### Certificate Storage

- **Private Keys:** Never transmitted unencrypted, never logged, never committed to git
- **PKCS#12 Bundles:** Strong password protection (minimum 20 characters, random)
- **CA Key:** HSM storage or encrypted at rest, access restricted to security team

### Distribution Channels

- **Approved Methods:** 1Password, LastPass, encrypted email, in-person handoff
- **Prohibited Methods:** Slack, plaintext email, shared drives, public cloud storage

### Access Control

- **CA Key Access:** Security team only (minimum 2 people, audit logging)
- **Admin Certificate Distribution:** Manager approval required
- **Revocation Authority:** VP Engineering or Security Officer only

---

## Related Documentation

- **ADR-015:** OAuth2/OIDC Authentication Architecture (fallback design)
- **auth0-idp-outage.md:** IdP Outage Response (when fallback activates)
- **web-console-cert-rotation.md:** General Certificate Rotation (server TLS certs)
- **P2T3-Phase3_Component6-7_Plan.md:** mTLS Fallback Implementation Plan

---

## Contact

**Security Team:** security@company.com

**On-Call:** Check PagerDuty rotation for platform-team

**Slack:** #security-ops

---

**Version:** 1.0

**Last Tested:** 2025-11-26 (staging environment)

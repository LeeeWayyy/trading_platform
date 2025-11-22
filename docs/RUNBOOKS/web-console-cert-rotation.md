# Web Console Certificate Rotation Runbook

**Purpose:** Step-by-step procedures for rotating TLS/mTLS certificates for web console authentication.

**Audience:** Operations team, DevOps engineers

**Related Documents:**
- Certificate README: `apps/web_console/certs/README.md`
- Planning Document: `docs/AI/Implementation/P2T3_PHASE2_PLAN.md`
- nginx Configuration: `apps/web_console/nginx/nginx.conf`

---

## Certificate Expiration Timeline

| Certificate Type | Validity Period | Rotation Frequency | Advance Notice |
|------------------|-----------------|-------------------|----------------|
| CA Certificate | 10 years | Once per decade | 1 year before expiration |
| Server Certificate | 1 year | Annual | 1 month before expiration |
| Client Certificate | 90 days | Quarterly | 2 weeks before expiration |

---

## Server Certificate Rotation (Annual)

**When:** 1 month before expiration
**Downtime:** None (zero-downtime reload)
**Duration:** ~5 minutes

### Prerequisites
- [ ] CA certificate exists (`apps/web_console/certs/ca.crt` and `ca.key`)
- [ ] Backup access to server in case rollback needed
- [ ] nginx container running

### Procedure

#### 1. Verify Current Certificate Expiration
```bash
# Check current expiration date
openssl x509 -in apps/web_console/certs/server.crt -noout -dates

# Example output:
# notBefore=Nov 21 10:00:00 2025 GMT
# notAfter=Nov 21 10:00:00 2026 GMT  ← Target this date
```

#### 2. Backup Current Certificate
```bash
# Create backup directory
mkdir -p apps/web_console/certs/backups

# Backup current certificate and key
cp apps/web_console/certs/server.crt \
   apps/web_console/certs/backups/server.crt.$(date +%Y%m%d)
cp apps/web_console/certs/server.key \
   apps/web_console/certs/backups/server.key.$(date +%Y%m%d)

# Verify backup
ls -lh apps/web_console/certs/backups/
```

#### 3. Generate New Server Certificate
```bash
# Generate new server certificate (signed by existing CA)
./scripts/generate_certs.py --server-only

# Expected output:
# 🔐 Generating server certificate (1-year validity, 4096-bit RSA)...
# ✅ Server certificate generated:
#    Private key: apps/web_console/certs/server.key (permissions: 0600)
#    Certificate: apps/web_console/certs/server.crt
#    Valid until: 2027-11-21 10:00:00 UTC
#    SANs: web-console.trading-platform.local, localhost, 127.0.0.1
```

#### 4. Verify New Certificate
```bash
# Verify certificate chain
openssl verify -CAfile apps/web_console/certs/ca.crt \
                        apps/web_console/certs/server.crt

# Expected: apps/web_console/certs/server.crt: OK

# Verify SANs
openssl x509 -in apps/web_console/certs/server.crt -text -noout | \
  grep -A1 "Subject Alternative Name"

# Expected:
#   X509v3 Subject Alternative Name:
#     DNS:web-console.trading-platform.local, DNS:localhost, IP Address:127.0.0.1

# Verify key permissions
stat -f "%A %N" apps/web_console/certs/server.key

# Expected: 600 apps/web_console/certs/server.key
```

#### 5. Reload nginx (Zero-Downtime)
```bash
# Test nginx configuration first
docker exec nginx nginx -t

# Expected output:
# nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
# nginx: configuration file /etc/nginx/nginx.conf test is successful

# Reload nginx (zero-downtime)
docker exec nginx nginx -s reload

# Expected output: (none if successful)
```

#### 6. Verify New Certificate in Production
```bash
# Test SSL handshake
openssl s_client -connect localhost:443 -CAfile apps/web_console/certs/ca.crt \
  </dev/null 2>/dev/null | openssl x509 -noout -dates

# Should show new expiration date

# Test via browser
# Navigate to: https://web-console.trading-platform.local
# Click padlock icon → Certificate → Details → Valid Until
# Should show new expiration date (1 year from today)
```

#### 7. Monitor for Issues
```bash
# Check nginx logs for SSL errors
docker logs nginx --tail 100 | grep -i ssl

# Check for failed handshakes
docker logs nginx --tail 100 | grep -i "handshake"

# Verify client connections successful
# (Users should be able to authenticate without errors)
```

### Rollback Procedure (If Issues Occur)

```bash
# Restore backup certificate
cp apps/web_console/certs/backups/server.crt.YYYYMMDD \
   apps/web_console/certs/server.crt
cp apps/web_console/certs/backups/server.key.YYYYMMDD \
   apps/web_console/certs/server.key

# Reload nginx
docker exec nginx nginx -s reload

# Verify rollback successful
openssl s_client -connect localhost:443 -CAfile apps/web_console/certs/ca.crt \
  </dev/null 2>/dev/null | openssl x509 -noout -dates
```

---

## Client Certificate Rotation (90-Day)

**When:** 2 weeks before expiration
**Downtime:** None (users rotate individually)
**Duration:** ~10 minutes per user

### Prerequisites
- [ ] CA certificate exists
- [ ] User's old certificate expiring soon
- [ ] Secure channel to distribute new certificate (encrypted)

### Procedure

#### 1. Check User's Current Certificate Expiration
```bash
# Verify user's current certificate
openssl x509 -in apps/web_console/certs/client-alice.crt -noout -dates

# Example output:
# notBefore=Nov 21 10:00:00 2025 GMT
# notAfter=Feb 19 10:00:00 2026 GMT  ← 90 days from issue
```

#### 2. Generate New Client Certificate
```bash
# Generate new client certificate for user
./scripts/generate_certs.py --client alice

# Expected output:
# 🔐 Generating client certificate for 'alice' (90-day validity, 4096-bit RSA)...
# ✅ Client certificate generated:
#    Private key: apps/web_console/certs/client-alice.key (permissions: 0600)
#    Certificate: apps/web_console/certs/client-alice.crt
#    Valid until: 2026-02-19 10:00:00 UTC
#    SAN: client-alice.trading-platform.local
```

#### 3. Package Certificate for Secure Distribution

**⚠️ Security Note:** Use GPG or age for strong encryption (not `zip -e` which uses weak ZipCrypto).

**Recommended: GPG encryption (AES-256)**
```bash
# Create encrypted archive with GPG (AES-256)
tar -czf - apps/web_console/certs/client-alice.crt \
           apps/web_console/certs/client-alice.key | \
  gpg --symmetric --cipher-algo AES256 \
      --output apps/web_console/certs/client-alice-$(date +%Y%m%d).tar.gz.gpg

# You will be prompted for passphrase - use strong passphrase (20+ chars, random)
# Share passphrase via separate channel (e.g., phone call, Signal, password manager)

# Verify encryption
gpg --list-packets apps/web_console/certs/client-alice-*.tar.gz.gpg | grep cipher
# Should show: cipher algo 9 (AES256)
```

**Alternative: age encryption (modern, simpler)**
```bash
# Install age: brew install age  (macOS)
tar -czf - apps/web_console/certs/client-alice.crt \
           apps/web_console/certs/client-alice.key | \
  age --passphrase > apps/web_console/certs/client-alice-$(date +%Y%m%d).tar.gz.age

# User decrypts with: age --decrypt < file.tar.gz.age | tar -xzf -
```

#### 4. Distribute Certificate to User

**Secure Distribution Options:**

**Option A: Encrypted File Sharing**
1. Upload encrypted archive (.tar.gz.gpg or .tar.gz.age) to secure file sharing (e.g., encrypted cloud storage)
2. Send download link to user via email
3. Send passphrase via separate channel (phone, Signal, password manager)

**Option B: In-Person Transfer**
1. Copy encrypted archive to USB drive
2. Hand deliver to user
3. Provide passphrase verbally

**Option C: Secure File Transfer**
```bash
# Transfer via SCP (requires user's SSH access)
# GPG-encrypted archive
scp apps/web_console/certs/client-alice-$(date +%Y%m%d).tar.gz.gpg \
  alice@user-machine:/tmp/

# OR age-encrypted archive
scp apps/web_console/certs/client-alice-$(date +%Y%m%d).tar.gz.age \
  alice@user-machine:/tmp/

# Share passphrase via separate channel (NOT via email/chat)
```

**User Decryption:**
```bash
# Decrypt GPG archive
gpg --decrypt client-alice-YYYYMMDD.tar.gz.gpg | tar -xzf -

# OR decrypt age archive
age --decrypt < client-alice-YYYYMMDD.tar.gz.age | tar -xzf -

# Verify extracted files
ls -lh client-alice.*
# Should show: client-alice.crt (public) and client-alice.key (private, 0600)
```

#### 5. User Installation (Browser - Chrome/Firefox)

**Chrome:**
1. Settings → Privacy and Security → Security → Manage Certificates
2. Import → Select `client-alice.crt` and `client-alice.key`
3. Enter password if prompted
4. Restart browser

**Firefox:**
1. Settings → Privacy & Security → Certificates → View Certificates
2. Your Certificates → Import
3. Select `client-alice.crt` and `client-alice.key`
4. Enter password
5. Restart browser

#### 6. Verify User Can Authenticate
```bash
# User tests connection (from their machine)
curl --cert client-alice.crt --key client-alice.key \
  --cacert ca.crt https://web-console.trading-platform.local/health

# Expected: {"status": "healthy"}
```

#### 7. Revoke Old Certificate (Optional)

**Note:** Old certificate will expire automatically after 90 days.
For immediate revocation:

```bash
# Remove old certificate from CA trust (if needed)
# This requires updating ca.crt and reloading nginx
# See "Certificate Revocation" section below
```

---

## CA Certificate Rotation (10-Year)

**⚠️ WARNING:** CA rotation requires regenerating ALL certificates (server + all clients).

**When:** 1 year before expiration
**Downtime:** Brief (~15 minutes)
**Duration:** ~1 hour (includes regenerating all certificates)

### Prerequisites
- [ ] Scheduled maintenance window
- [ ] All users notified in advance (2+ weeks)
- [ ] Backup of all existing certificates
- [ ] List of all active users (for client certificate regeneration)

### Procedure

#### 1. Verify Current CA Expiration
```bash
# Check current CA expiration
openssl x509 -in apps/web_console/certs/ca.crt -noout -dates

# Example output:
# notBefore=Nov 21 10:00:00 2025 GMT
# notAfter=Nov 21 10:00:00 2035 GMT  ← 10 years
```

#### 2. Backup All Existing Certificates
```bash
# Create backup directory
mkdir -p apps/web_console/certs/backups/ca-rotation-$(date +%Y%m%d)

# Backup all certificates
cp apps/web_console/certs/*.crt \
   apps/web_console/certs/backups/ca-rotation-$(date +%Y%m%d)/
cp apps/web_console/certs/*.key \
   apps/web_console/certs/backups/ca-rotation-$(date +%Y%m%d)/

# Verify backup
ls -lh apps/web_console/certs/backups/ca-rotation-$(date +%Y%m%d)/
```

#### 3. Generate New CA
```bash
# Generate new CA certificate
./scripts/generate_certs.py --ca-only

# Expected output:
# 🔐 Generating CA certificate (10-year validity, 4096-bit RSA)...
# ✅ CA certificate generated:
#    Private key: apps/web_console/certs/ca.key (permissions: 0600)
#    Certificate: apps/web_console/certs/ca.crt
#    Valid until: 2035-11-21 10:00:00 UTC
```

#### 4. Regenerate Server Certificate
```bash
# Generate new server certificate (signed by new CA)
./scripts/generate_certs.py --server-only

# Verify certificate chain
openssl verify -CAfile apps/web_console/certs/ca.crt \
                        apps/web_console/certs/server.crt
```

#### 5. Regenerate All Client Certificates
```bash
# List all active users (example)
USERS=("alice" "bob" "charlie" "admin")

# Generate client certificates for all users
for user in "${USERS[@]}"; do
  echo "Generating certificate for $user..."
  ./scripts/generate_certs.py --client "$user"
done

# Package all certificates for distribution
for user in "${USERS[@]}"; do
  zip -e apps/web_console/certs/client-$user-$(date +%Y%m%d).zip \
    apps/web_console/certs/client-$user.crt \
    apps/web_console/certs/client-$user.key
done
```

#### 6. Deploy New CA to nginx
```bash
# nginx configuration should already reference ca.crt
# Just reload nginx to pick up new CA

# Test nginx configuration
docker exec nginx nginx -t

# Reload nginx
docker exec nginx nginx -s reload
```

#### 7. Distribute New Client Certificates to Users
```bash
# Follow "Client Certificate Rotation" procedure for each user
# Use secure channels (encrypted file sharing, in-person, etc.)
```

#### 8. Verify All Users Can Authenticate
```bash
# Coordinate with users to test authentication
# Monitor audit logs for successful logins

# Check audit log
docker exec -it postgres psql -U postgres -d trading_platform -c \
  "SELECT timestamp, user_id, action FROM audit_log WHERE action='login_success' ORDER BY timestamp DESC LIMIT 10;"
```

---

## Certificate Revocation

**When:** User leaves organization, certificate compromised, or security incident

**Downtime:** None (nginx reload)
**Duration:** ~5 minutes

### Procedure

#### 1. Identify Certificate to Revoke
```bash
# Verify certificate subject
openssl x509 -in apps/web_console/certs/client-alice.crt -noout -subject

# Example output:
# subject=C=US, ST=California, L=San Francisco, O=Trading Platform, OU=Users, CN=client-alice
```

#### 2. Remove Certificate from CA Trust (Manual Revocation)

**Note:** This is a simplified manual revocation. For production, implement CRL or OCSP (Phase 3).

```bash
# Current implementation: Manual revocation via CA update
# 1. Remove compromised client certificate public key from CA trust
# 2. Update ca.crt to exclude revoked certificate
# 3. Reload nginx

# For MVP: Simply delete client certificate files
rm apps/web_console/certs/client-alice.crt
rm apps/web_console/certs/client-alice.key

# nginx will reject connections from this client (no valid cert)
```

#### 3. Reload nginx
```bash
# Reload nginx to apply changes
docker exec nginx nginx -s reload
```

#### 4. Verify Revocation
```bash
# Test that revoked certificate is rejected
openssl s_client -connect localhost:443 \
  -cert apps/web_console/certs/backups/client-alice.crt \
  -key apps/web_console/certs/backups/client-alice.key \
  -CAfile apps/web_console/certs/ca.crt

# Expected: SSL handshake failure or certificate verification failed
```

#### 5. Audit Log Verification
```bash
# Verify no successful logins from revoked user
docker exec -it postgres psql -U postgres -d trading_platform -c \
  "SELECT timestamp, user_id, action FROM audit_log WHERE user_id='alice' ORDER BY timestamp DESC LIMIT 10;"

# Should show failed login attempts after revocation
```

---

## Monitoring & Alerts

### Manual Monitoring (MVP)

**Calendar Reminders:**
- CA expiration: 1 year before (2034-11-21)
- Server expiration: 1 month before (check quarterly)
- Client expiration: 2 weeks before (check monthly)

### Automated Monitoring (Production - Future Enhancement)

**Prometheus Metrics:**
```yaml
# Example metric: certificate_expiry_days
# Alert when expiry < 30 days
- alert: CertificateExpiringSoon
  expr: certificate_expiry_days < 30
  for: 24h
  annotations:
    summary: "Certificate {{ $labels.cert_type }} expires in {{ $value }} days"
```

**Grafana Dashboard:**
- Certificate expiration timeline
- Days until expiration per certificate type
- Rotation history

---

## Troubleshooting

### Certificate Chain Broken After Rotation

**Symptom:** nginx fails to start or SSL handshake fails

**Diagnosis:**
```bash
openssl verify -CAfile apps/web_console/certs/ca.crt \
                        apps/web_console/certs/server.crt
```

**Resolution:**
- Ensure server certificate was signed by current CA
- Regenerate server certificate: `./scripts/generate_certs.py --server-only`
- Reload nginx: `docker exec nginx nginx -s reload`

### Client Cannot Authenticate After Rotation

**Symptom:** User sees "certificate verification failed" error

**Diagnosis:**
- Verify user installed new certificate in browser
- Check certificate expiration: `openssl x509 -in client-alice.crt -noout -dates`

**Resolution:**
- Resend new client certificate to user
- Verify user followed installation steps correctly
- Check audit log for detailed error: `SELECT * FROM audit_log WHERE user_id='alice' AND action='login_failed';`

### nginx Reload Fails

**Symptom:** `docker exec nginx nginx -s reload` returns error

**Diagnosis:**
```bash
# Check nginx configuration
docker exec nginx nginx -t

# Check nginx logs
docker logs nginx --tail 50
```

**Resolution:**
- Fix configuration errors shown in `nginx -t` output
- Verify certificate files exist and have correct permissions
- Rollback to backup certificates if needed

---

## Post-Rotation Checklist

### Server Certificate Rotation
- [ ] Backup created successfully
- [ ] New certificate generated and verified
- [ ] nginx reloaded without errors
- [ ] SSL handshake test successful
- [ ] Browser test successful
- [ ] No SSL errors in logs
- [ ] Update calendar reminder for next rotation (1 year from now)

### Client Certificate Rotation
- [ ] New certificate generated
- [ ] Certificate packaged securely (encrypted)
- [ ] Certificate distributed to user via secure channel
- [ ] User confirmed successful installation
- [ ] User tested authentication successfully
- [ ] Audit log shows successful login
- [ ] Update calendar reminder for next rotation (90 days from now)

### CA Certificate Rotation
- [ ] All existing certificates backed up
- [ ] New CA generated and verified
- [ ] Server certificate regenerated and verified
- [ ] All client certificates regenerated
- [ ] All client certificates distributed to users
- [ ] nginx reloaded successfully
- [ ] All users confirmed successful authentication
- [ ] Update calendar reminder for next rotation (10 years from now)

---

## Emergency Contacts

**Production Incidents:**
- On-call DevOps: [Pager Duty / Phone Number]
- Security Team: [Email / Slack Channel]
- Platform Lead: [Email / Phone]

**Certificate Issues:**
- Check runbook first: `docs/RUNBOOKS/web-console-cert-rotation.md`
- Certificate README: `apps/web_console/certs/README.md`
- Planning document: `docs/AI/Implementation/P2T3_PHASE2_PLAN.md`

---

**Last Updated:** 2025-11-21
**Document Owner:** DevOps Team
**Review Frequency:** Quarterly (or after each rotation)

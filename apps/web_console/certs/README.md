# Web Console Certificates

This directory contains TLS/mTLS certificates for the web console's secure authentication.

## Certificate Types

### 1. CA Certificate (`ca.crt` / `ca.key`)
- **Purpose:** Certificate Authority for signing server and client certificates
- **Validity:** 10 years
- **Key Size:** RSA 4096-bit
- **Usage:** Sign server and client certificates

### 2. Server Certificate (`server.crt` / `server.key`)
- **Purpose:** HTTPS/TLS termination in nginx
- **Validity:** 1 year
- **Key Size:** RSA 4096-bit
- **Subject Alternative Names:**
  - `DNS:web-console.trading-platform.local`
  - `DNS:localhost`
  - `IP:127.0.0.1`
- **Usage:** nginx SSL configuration

### 3. Client Certificates (`client-{username}.crt` / `client-{username}.key`)
- **Purpose:** Client authentication for mTLS
- **Validity:** 90 days
- **Key Size:** RSA 4096-bit
- **Subject Alternative Names:**
  - `DNS:client-{username}.trading-platform.local`
- **Usage:** User authentication via client certificate

### 4. JWT Signing Keys (`jwt_private.key` / `jwt_public.pem`)
- **Purpose:** Sign and validate JWT tokens
- **Key Size:** RSA 4096-bit
- **Usage:**
  - `jwt_private.key`: Sign JWT tokens (Component 2)
  - `jwt_public.pem`: Validate JWT signatures (Component 2)

## Generating Certificates

### Generate All Certificates (Default)
```bash
./scripts/generate_certs.py
```

This generates:
- CA certificate and private key
- Server certificate and private key
- Default client certificate (`client-admin`)
- JWT signing key pair

### Generate Individual Certificates

**CA Only:**
```bash
./scripts/generate_certs.py --ca-only
```

**Server Only** (requires existing CA):
```bash
./scripts/generate_certs.py --server-only
```

**Client Certificate for Specific User:**
```bash
./scripts/generate_certs.py --client alice
./scripts/generate_certs.py --client bob
```

**Custom Output Directory:**
```bash
./scripts/generate_certs.py --output /path/to/certs
```

## Certificate Rotation

### Server Certificate Rotation (Annual)

1. Generate new server certificate:
   ```bash
   # Backup old certificate
   cp apps/web_console/certs/server.crt apps/web_console/certs/server.crt.backup
   cp apps/web_console/certs/server.key apps/web_console/certs/server.key.backup

   # Generate new server certificate
   ./scripts/generate_certs.py --server-only
   ```

2. Reload nginx without downtime:
   ```bash
   docker exec nginx nginx -s reload
   ```

3. Verify new certificate:
   ```bash
   openssl x509 -in apps/web_console/certs/server.crt -text -noout | grep "Not After"
   ```

### Client Certificate Rotation (90-day)

1. Generate new client certificate:
   ```bash
   ./scripts/generate_certs.py --client username
   ```

2. Distribute new certificate securely to user (encrypted channel)

3. User updates their client certificate in browser/application

4. Old certificate expires automatically after 90 days

### CA Certificate Rotation (10-year)

⚠️ **WARNING:** CA rotation requires regenerating ALL certificates.

1. Generate new CA:
   ```bash
   # Backup old CA
   cp apps/web_console/certs/ca.crt apps/web_console/certs/ca.crt.backup
   cp apps/web_console/certs/ca.key apps/web_console/certs/ca.key.backup

   # Generate new CA
   ./scripts/generate_certs.py --ca-only
   ```

2. Regenerate all server and client certificates with new CA:
   ```bash
   ./scripts/generate_certs.py --server-only
   ./scripts/generate_certs.py --client admin
   # ... regenerate all client certificates
   ```

3. Update nginx configuration with new CA certificate

4. Distribute new client certificates to all users

## Certificate Verification

### Verify Certificate Details
```bash
# View certificate
openssl x509 -in apps/web_console/certs/server.crt -text -noout

# Check expiration date
openssl x509 -in apps/web_console/certs/server.crt -noout -dates

# Verify certificate chain
openssl verify -CAfile apps/web_console/certs/ca.crt apps/web_console/certs/server.crt
openssl verify -CAfile apps/web_console/certs/ca.crt apps/web_console/certs/client-admin.crt
```

### Verify Private Key Permissions
```bash
# Should show 0600 (owner read/write only)
# macOS/BSD:
stat -f "%A %N" apps/web_console/certs/*.key
# Linux:
# stat -c "%a %n" apps/web_console/certs/*.key
```

### Test Certificate with OpenSSL
```bash
# Test server certificate
openssl s_client -connect localhost:443 -CAfile apps/web_console/certs/ca.crt

# Test client certificate
openssl s_client -connect localhost:443 \
  -cert apps/web_console/certs/client-admin.crt \
  -key apps/web_console/certs/client-admin.key \
  -CAfile apps/web_console/certs/ca.crt
```

## Security Considerations

### Private Key Protection

**Critical Security Rules:**
1. **Never commit private keys to version control**
   - `.gitignore` excludes `*.key` and `*.pem` files
   - Verify before committing: `git status`

2. **Strict file permissions (0600)**
   - Private keys are automatically created with 0600 permissions
   - Only owner can read/write
   - Verify: `ls -l apps/web_console/certs/*.key`

3. **Production deployment:**
   - Load private keys from secrets manager (HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager)
   - **Do NOT** store private keys on filesystem in production
   - Use environment variables to inject keys at runtime

### Certificate Distribution

**Client Certificates:**
- Distribute via encrypted channels only (never plain email)
- Options:
  - Encrypted file sharing (e.g., encrypted zip with separate password)
  - Secure file transfer (SFTP, SCP with SSH keys)
  - In-person USB transfer for high-security environments

**Server Certificates:**
- Mount as Docker volumes (read-only)
- Never expose in environment variables
- Rotate annually or on-demand if compromised

### Revocation

**Manual Revocation Process:**
1. Remove client certificate from CA trust list
2. Update `ca.crt` in nginx
3. Reload nginx: `docker exec nginx nginx -s reload`
4. Client will be rejected on next connection attempt

**Future Enhancement (Phase 3):**
- Implement CRL (Certificate Revocation List) or OCSP if needed
- See: `docs/AI/Implementation/P2T3_PHASE2_PLAN.md` for details

## Expiration Monitoring

### Check Expiration Dates

```bash
# Check all certificates
for cert in apps/web_console/certs/*.crt; do
  echo "=== $cert ==="
  openssl x509 -in "$cert" -noout -dates
  echo
done
```

### Expiration Timeline

- **CA:** 10 years (low urgency, plan well in advance)
- **Server:** 1 year (annual rotation required)
- **Client:** 90 days (quarterly rotation required)

### Monitoring Recommendations

1. **Manual Checks (MVP):**
   - Set calendar reminders for certificate expiration
   - CA: 1 year before expiration
   - Server: 1 month before expiration
   - Client: 2 weeks before expiration

2. **Automated Monitoring (Production):**
   - Add certificate expiration alerts to Prometheus/Grafana
   - Alert thresholds:
     - Warning: 30 days before expiration
     - Critical: 7 days before expiration

## Troubleshooting

### Certificate Chain Broken

**Symptom:** nginx fails to start or SSL handshake fails

**Diagnosis:**
```bash
openssl verify -CAfile apps/web_console/certs/ca.crt apps/web_console/certs/server.crt
```

**Fix:**
- Ensure server certificate was signed by current CA
- Regenerate server certificate if CA was rotated

### Certificate Expired

**Symptom:** Browser shows "certificate expired" error

**Diagnosis:**
```bash
openssl x509 -in apps/web_console/certs/server.crt -noout -dates
```

**Fix:**
- Regenerate certificate: `./scripts/generate_certs.py --server-only`
- Reload nginx: `docker exec nginx nginx -s reload`

### Clock Skew

**Symptom:** Certificate rejected even though valid

**Cause:** System clock out of sync with certificate timestamps

**Fix:**
- Sync system clock: `sudo ntpdate -s time.nist.gov` (macOS/Linux)
- JWT validation includes 10-second leeway to handle minor clock skew

### Wrong SAN (Subject Alternative Name)

**Symptom:** Browser shows "certificate not valid for hostname"

**Diagnosis:**
```bash
openssl x509 -in apps/web_console/certs/server.crt -text -noout | grep -A1 "Subject Alternative Name"
```

**Fix:**
- Verify SANs match your hostname
- Regenerate certificate with correct SANs if needed
- For local development, add `127.0.0.1 web-console.trading-platform.local` to `/etc/hosts`

## Files in This Directory

```
apps/web_console/certs/
├── README.md                    # This file
├── ca.crt                       # CA certificate (public, can be committed)
├── ca.key                       # CA private key (gitignored, 0600 permissions)
├── server.crt                   # Server certificate (public, can be committed)
├── server.key                   # Server private key (gitignored, 0600 permissions)
├── client-admin.crt             # Default client certificate (public)
├── client-admin.key             # Client private key (gitignored, 0600 permissions)
├── jwt_private.key              # JWT signing key (gitignored, 0600 permissions)
└── jwt_public.pem               # JWT validation key (public)
```

**Committed to Git:**
- `README.md`
- `*.crt` (certificates are public data)

**Gitignored (NEVER commit):**
- `*.key` (private keys)
- `*.pem` (private keys in PEM format, except `jwt_public.pem` which is public)

## References

- **Planning Document:** `docs/AI/Implementation/P2T3_PHASE2_PLAN.md`
- **Rotation Runbook:** `docs/RUNBOOKS/web-console-cert-rotation.md`
- **nginx Configuration:** `apps/web_console/nginx/nginx.conf`
- **Certificate Generation Script:** `scripts/generate_certs.py`

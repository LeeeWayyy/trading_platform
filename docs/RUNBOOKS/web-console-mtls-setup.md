# Web Console mTLS Setup Runbook

**Component:** P2T3 Phase 2 - Component 3: Nginx Reverse Proxy with HTTPS/TLS
**Purpose:** Secure web console access with mutual TLS (mTLS) and JWT-DN binding
**Last Updated:** 2025-11-22

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Initial Setup](#initial-setup)
4. [Starting Services](#starting-services)
5. [Certificate Management](#certificate-management)
6. [Troubleshooting](#troubleshooting)
7. [Security Validation](#security-validation)
8. [Operational Procedures](#operational-procedures)

---

## Overview

The web console supports two deployment modes:

### Dev Mode (Default)
```
User → http://localhost:8501 → Streamlit (no nginx, basic auth)
```
- **Usage:** `docker compose --profile dev up -d`
- **Port:** 8501 (direct access)
- **Auth:** Username/password (WEB_CONSOLE_AUTH_TYPE=dev)

### mTLS Mode (Production)
```
User → https://localhost:443 → Nginx (mTLS + HTTPS) → http://web_console:8501 → Streamlit
```
- **Usage:** `docker compose --profile mtls up -d`
- **Ports:** 443 (HTTPS), 80 (redirect to HTTPS)
- **Auth:** Client certificates + JWT-DN binding (WEB_CONSOLE_AUTH_TYPE=mtls)

**Key Security Features:**
- Mutual TLS (client certificate required)
- Three-layer rate limiting (connection, IP, DN-based)
- JWT-DN binding contract (prevents token replay)
- Header spoofing prevention (X-SSL-Client-Verify validation)
- WebSocket support for Streamlit (3600s timeout)
- OCSP stapling for certificate validation
- TLS 1.3 with Mozilla Intermediate cipher suite

---

## Prerequisites

### Required Software
- Docker & Docker Compose
- OpenSSL (for certificate generation)
- Python 3.11+ (for certificate generation scripts)

### Required Files
All certificates must exist in `apps/web_console/certs/`:
- `ca.crt` - Certificate Authority (for client cert validation)
- `ca.key` - CA private key (for signing certificates)
- `server.crt` - Server certificate (with proper SANs)
- `server.key` - Server private key
- `dhparam.pem` - Diffie-Hellman parameters (4096-bit)
- `client_<username>.crt` - Client certificate(s)
- `client_<username>.key` - Client private key(s)

---

## Initial Setup

### Step 1: Generate Certificates

**Generate CA (once):**
```bash
source .venv/bin/activate
PYTHONPATH=. python3 scripts/generate_certs.py --ca-only
```

**Generate server certificate with SANs:**
```bash
PYTHONPATH=. python3 scripts/generate_certs.py --server-only \
  --san DNS:web-console.trading-platform.local,DNS:localhost,DNS:web_console_nginx
```

**Verify server certificate SANs:**
```bash
openssl x509 -in apps/web_console/certs/server.crt -noout -text | grep -A 5 "Subject Alternative Name"
# Expected output:
#   X509v3 Subject Alternative Name:
#     DNS:web-console.trading-platform.local, DNS:localhost, DNS:web_console_nginx
```

**Generate client certificate:**
```bash
PYTHONPATH=. python3 scripts/generate_certs.py --client <username>
```

### Step 2: Generate DH Parameters

**⚠️ CRITICAL:** DH parameters MUST be generated on the host before starting nginx.

**Why:** The nginx Dockerfile does NOT generate dhparam.pem. It is mounted from the host via docker compose volume. If missing, nginx will fail to start.

```bash
# Generate 4096-bit DH parameters (takes 5-15 minutes)
openssl dhparam -out apps/web_console/certs/dhparam.pem 4096

# Verify file was created
ls -lh apps/web_console/certs/dhparam.pem
# Expected: -rw------- ... 800-1000 bytes

# Secure permissions
chmod 600 apps/web_console/certs/dhparam.pem
```

**Performance Note:** This is a one-time operation. The same dhparam.pem can be reused across deployments for several months. Regenerate annually or when security policies require.

### Step 3: Secure Private Keys

**Set proper permissions (CRITICAL):**
```bash
chmod 600 apps/web_console/certs/*.key apps/web_console/certs/dhparam.pem
```

**Verify permissions:**
```bash
ls -l apps/web_console/certs/*.key apps/web_console/certs/dhparam.pem
# Output: -rw------- (owner read/write only)
```

### Step 4: Configure Environment Variables

**Create `.env` file (if not exists):**
```bash
cp .env.example .env

# Set at minimum:
# WEB_CONSOLE_USER, WEB_CONSOLE_PASSWORD
# Optional for OAuth2 profile:
# AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_AUDIENCE, SESSION_ENCRYPTION_KEY

chmod 600 .env  # Secure credentials file
```

**Note:** `SESSION_ENCRYPTION_KEY` is required only for OAuth2 profile (`--profile oauth2`), not for mTLS.

---

## Starting Services

### Dev Mode (Direct Access, Basic Auth)

```bash
# Start web console without nginx
docker compose --profile dev up -d

# Verify service is running
docker ps | grep web_console

# Access via browser
open http://localhost:8501

# Login with credentials from .env
```

### mTLS Mode (Production, Certificate Auth)

```bash
# Start web console + nginx reverse proxy
docker compose --profile mtls up -d

# Verify both services running
docker ps | grep -E "nginx|web_console"
# Expected: trading_platform_nginx_mtls, trading_platform_web_console_mtls

# Verify port 8501 NOT exposed
curl -I http://localhost:8501 2>&1
# Expected: "Connection refused" or "Failed to connect"
# If succeeds: SECURITY ISSUE - investigate docker-compose.yml

# Access via HTTPS with client certificate
# Browser configuration: Import client_<username>.crt + client_<username>.key
open https://localhost:443
```

### Verify Services Healthy

```bash
# Check nginx health
docker exec trading_platform_nginx_mtls nginx -t
# Expected: "nginx: configuration file /etc/nginx/nginx.conf test is successful"

# Check nginx logs
docker logs trading_platform_nginx_mtls --tail 50

# Check web console logs
docker logs trading_platform_web_console_mtls --tail 50

# Verify HTTPS endpoint responds
curl -k https://localhost/health
# Expected: OK
```

---

## Certificate Management

### Certificate Rotation (Zero Downtime)

**Procedure:**
```bash
# 1. Backup current certificates
cp apps/web_console/certs/ca.crt apps/web_console/certs/ca.crt.backup
cp apps/web_console/certs/server.crt apps/web_console/certs/server.crt.backup
cp apps/web_console/certs/server.key apps/web_console/certs/server.key.backup

# 2. Generate new certificates (same as Initial Setup)
PYTHONPATH=. python3 scripts/generate_certs.py --ca-only --force
PYTHONPATH=. python3 scripts/generate_certs.py --server-only \
  --san DNS:web-console.trading-platform.local,DNS:localhost,DNS:web_console_nginx --force

# 3. Test configuration BEFORE reload (CRITICAL)
docker exec trading_platform_nginx_mtls nginx -t
if [ $? -ne 0 ]; then
  echo "ERROR: nginx config test failed, rolling back"
  mv apps/web_console/certs/ca.crt.backup apps/web_console/certs/ca.crt
  mv apps/web_console/certs/server.crt.backup apps/web_console/certs/server.crt
  mv apps/web_console/certs/server.key.backup apps/web_console/certs/server.key
  exit 1
fi

# 4. Reload nginx configuration (graceful, no downtime)
docker exec trading_platform_nginx_mtls nginx -s reload

# 5. Verify reload succeeded
docker exec trading_platform_nginx_mtls nginx -T | grep "ssl_certificate"
docker logs trading_platform_nginx_mtls --tail 10

# 6. Test with new client certificate
# Browser: Import new client_<username>.crt
# Access https://localhost:443
```

**How It Works:**
- `nginx -t` - Tests config without applying (catches errors before reload)
- `nginx -s reload` - Graceful reload (keeps existing connections alive)
- New connections use new certificates
- Old connections continue with old certificates until closed
- **If config test fails:** Nginx preserves old config, no service disruption

### Adding New Client Certificates

```bash
# Generate client certificate
PYTHONPATH=. python3 scripts/generate_certs.py --client new_user

# Client receives:
# - apps/web_console/certs/client_new_user.crt
# - apps/web_console/certs/client_new_user.key

# Import to browser (example: Chrome/Firefox)
# 1. Settings → Privacy and Security → Certificates → Import
# 2. Select client_new_user.crt + client_new_user.key
# 3. Enter passphrase (if protected)
# 4. Certificate now available for mTLS authentication

# No nginx reload required (CA unchanged)
```

### Revoking Client Certificates

**Option 1: CA Rotation (Revokes All Clients)**
```bash
# Generate new CA
PYTHONPATH=. python3 scripts/generate_certs.py --ca-only --force

# Update nginx configuration
docker exec trading_platform_nginx_mtls nginx -s reload

# All old client certificates immediately invalid
# Generate new client certificates for authorized users
```

**Option 2: Certificate Revocation List (CRL) - Future Enhancement**
- Planned for Phase 3: OAuth2 integration
- Will support granular certificate revocation
- See `docs/ARCHIVE/TASKS_HISTORY/P2T3_Phase3_PLANNING_SUMMARY.md` for details

---

## Troubleshooting

### Port 8501 Accessible in mTLS Mode

**Symptom:**
```bash
curl -I http://localhost:8501
# Returns: HTTP/1.1 200 OK (SHOULD BE "Connection refused")
```

**Root Cause:** Port exposed in docker-compose.yml for wrong profile

**Fix:**
```bash
# Check docker-compose.yml
grep -A 5 "web_console_mtls:" docker-compose.yml
# Verify NO "ports:" section under web_console_mtls service

# Restart services
docker compose down
docker compose --profile mtls up -d
```

### WebSocket Connections Dropping After 2 Minutes

**Symptom:** Streamlit shows "Connection lost" after idle period

**Root Cause:** nginx proxy_read_timeout too short

**Fix:**
```bash
# Verify WebSocket location exists in nginx.conf
docker exec trading_platform_nginx_mtls cat /etc/nginx/nginx.conf | grep -A 10 "_stcore/stream"

# Expected output:
# location /_stcore/stream {
#   proxy_read_timeout 3600s;  # 1 hour
#   proxy_send_timeout 3600s;
#   proxy_buffering off;
#   ...
# }

# If missing or incorrect:
# 1. Update apps/web_console/nginx/nginx.conf
# 2. Rebuild nginx image: docker compose --profile mtls build nginx_mtls
# 3. Restart: docker compose --profile mtls up -d nginx_mtls
```

### JWT-DN Binding Validation Failures

**Symptom:** "Session validation failed" error after login

**Root Cause:** X-SSL-Client-S-DN header not reaching backend

**Diagnosis:**
```bash
# Check nginx forwards mTLS headers
docker exec trading_platform_nginx_mtls cat /etc/nginx/nginx.conf | grep "X-SSL-Client"

# Expected:
# proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
# proxy_set_header X-SSL-Client-S-DN $ssl_client_s_dn;

# Test header visibility in Streamlit
# Add debug logging to apps/web_console/auth.py _get_request_headers()
# Verify headers appear in logs
```

**Fix:**
```bash
# If headers missing:
# 1. Verify nginx.conf has proxy_set_header directives
# 2. Rebuild nginx: docker compose --profile mtls build nginx_mtls
# 3. Restart: docker compose --profile mtls up -d nginx_mtls

# If headers present but not accessible in Streamlit:
# 1. Check Streamlit version: docker exec trading_platform_web_console_mtls python3 -c "import streamlit; print(streamlit.__version__)"
# 2. Verify >= 1.28.0 (required for session_info API)
# 3. Upgrade if needed: Update apps/web_console/requirements.txt
```

### OCSP Stapling Not Working

**Symptom:**
```bash
openssl s_client -connect localhost:443 -status < /dev/null 2>&1 | grep "OCSP"
# Output: "OCSP response: no response sent"
```

**Root Cause:** DNS resolver cannot reach OCSP servers

**Fix (Option 1: Use Public DNS):**
```nginx
# In apps/web_console/nginx/nginx.conf
resolver 8.8.8.8 8.8.4.4 valid=300s;
resolver_timeout 5s;
```

**Fix (Option 2: Use Internal DNS):**
```nginx
# Replace with your network's DNS server
resolver 10.0.0.53 valid=300s;
resolver_timeout 5s;
```

**Fix (Option 3: Use Docker Embedded DNS):**
```nginx
# For containerized environments
resolver 127.0.0.11 valid=30s;
resolver_timeout 5s;
```

**Fix (Option 4: Disable OCSP for Air-Gapped Networks):**
```nginx
# Comment out in apps/web_console/nginx/nginx.conf
# ssl_stapling on;
# ssl_stapling_verify on;
```

**Apply Changes:**
```bash
docker compose --profile mtls build nginx_mtls
docker compose --profile mtls up -d nginx_mtls
```

### Rate Limiting Too Aggressive

**Symptom:** Legitimate requests receive 429 Too Many Requests

**Diagnosis:**
```bash
# Check nginx access logs for rate limit denials
docker logs trading_platform_nginx_mtls 2>&1 | grep "limiting requests"

# Example: "limiting requests, excess: 5.123 by zone 'mtls_limit'"
```

**Adjustment:**
```nginx
# In apps/web_console/nginx/nginx.conf
# Increase rate or burst limits

# Layer 2: Pre-auth (IP-based)
limit_req_zone $binary_remote_addr zone=preauth_limit:10m rate=50r/s;  # Was 20r/s
limit_req zone=preauth_limit burst=100 nodelay;  # Was 50

# Layer 3: Post-auth (DN-based)
limit_req_zone $ssl_client_s_dn zone=mtls_limit:10m rate=20r/s;  # Was 10r/s
limit_req zone=mtls_limit burst=50 nodelay;  # Was 20
```

**Apply:**
```bash
docker exec trading_platform_nginx_mtls nginx -t
docker exec trading_platform_nginx_mtls nginx -s reload
```

---

## Security Validation

### Verify mTLS Enforcement

**Test 1: Request without client certificate fails**
```bash
curl -k https://localhost:443
# Expected: SSL handshake failure (connection error)
# If succeeds: mTLS NOT enforced - check ssl_verify_client in nginx.conf
```

**Test 2: Request with valid client certificate succeeds**
```bash
curl -k --cert apps/web_console/certs/client_<username>.crt \
     --key apps/web_console/certs/client_<username>.key \
     https://localhost:443/health
# Expected: HTTP/1.1 200 OK
```

**Test 3: Request with invalid client certificate fails**
```bash
# Generate self-signed cert (not CA-signed)
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout /tmp/invalid.key -out /tmp/invalid.crt \
  -subj "/CN=invalid" -days 1

curl -k --cert /tmp/invalid.crt --key /tmp/invalid.key https://localhost:443
# Expected: SSL handshake failure
```

### Verify HTTPS/TLS Configuration

**Test 1: HSTS header present**
```bash
curl -k --cert apps/web_console/certs/client_<username>.crt \
     --key apps/web_console/certs/client_<username>.key \
     -I https://localhost:443 | grep Strict-Transport-Security
# Expected: Strict-Transport-Security: max-age=63072000; includeSubDomains
```

**Test 2: HTTP redirects to HTTPS**
```bash
curl -I http://localhost:80
# Expected: HTTP/1.1 301 Moved Permanently
#           Location: https://localhost/
```

**Test 3: TLS version and cipher**
```bash
openssl s_client -connect localhost:443 \
  -cert apps/web_console/certs/client_<username>.crt \
  -key apps/web_console/certs/client_<username>.key \
  < /dev/null 2>&1 | grep -E "Protocol|Cipher"
# Expected: Protocol : TLSv1.3 (or TLSv1.2)
#           Cipher    : TLS_AES_256_GCM_SHA384 (or similar strong cipher)
```

### Verify Rate Limiting

**Test 1: Connection-level rate limiting**
```bash
# Open 60 concurrent connections
for i in {1..60}; do
  curl -k --cert apps/web_console/certs/client_<username>.crt \
       --key apps/web_console/certs/client_<username>.key \
       https://localhost:443/health &
done
wait

# Check logs for connection rejections
docker logs trading_platform_nginx_mtls 2>&1 | grep "limiting connections"
# Expected: Some connections rejected after limit (50)
```

**Test 2: Request rate limiting**
```bash
# Send 100 rapid requests
for i in {1..100}; do
  curl -k --cert apps/web_console/certs/client_<username>.crt \
       --key apps/web_console/certs/client_<username>.key \
       -s -o /dev/null -w "%{http_code}\n" \
       https://localhost:443/health
done | sort | uniq -c
# Expected: Mix of 200 and 429 responses
```

### Verify OCSP Stapling

```bash
openssl s_client -connect localhost:443 -status \
  -cert apps/web_console/certs/client_<username>.crt \
  -key apps/web_console/certs/client_<username>.key \
  < /dev/null 2>&1 | grep -A 10 "OCSP Response Status"
# Expected: OCSP Response Status: successful
#           Cert Status: good
```

---

## Operational Procedures

### Daily Operations

**Health Check:**
```bash
# Verify nginx running
docker ps | grep nginx

# Check recent errors
docker logs trading_platform_nginx_mtls --since 1h | grep -i error

# Verify HTTPS endpoint
curl -k --cert apps/web_console/certs/client_<username>.crt \
     --key apps/web_console/certs/client_<username>.key \
     https://localhost:443/health
```

**Monitor Rate Limiting:**
```bash
# Check for excessive rate limit hits (may indicate DoS)
docker logs trading_platform_nginx_mtls --since 1h | grep "limiting" | wc -l

# Investigate source IPs if count high
docker logs trading_platform_nginx_mtls --since 1h | grep "limiting" | awk '{print $1}' | sort | uniq -c | sort -rn
```

### Certificate Expiration Monitoring

**Check certificate expiration:**
```bash
# Server certificate
openssl x509 -in apps/web_console/certs/server.crt -noout -enddate
# Expected: notAfter=<date>

# CA certificate
openssl x509 -in apps/web_console/certs/ca.crt -noout -enddate

# Client certificate
openssl x509 -in apps/web_console/certs/client_<username>.crt -noout -enddate

# Alert if < 30 days remaining
```

**Automated monitoring (cron job):**
```bash
#!/bin/bash
# /etc/cron.daily/check-cert-expiry

CERT_PATH="apps/web_console/certs/server.crt"
DAYS_WARN=30

EXPIRY_DATE=$(openssl x509 -in $CERT_PATH -noout -enddate | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY_DATE" +%s)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( ($EXPIRY_EPOCH - $NOW_EPOCH) / 86400 ))

if [ $DAYS_LEFT -lt $DAYS_WARN ]; then
  echo "WARNING: Server certificate expires in $DAYS_LEFT days"
  # Send alert (email, Slack, PagerDuty, etc.)
fi
```

### Incident Response

**Procedure: Compromised Client Certificate**
1. Revoke access immediately (CA rotation or CRL)
2. Audit logs for unauthorized access
3. Generate new CA and client certificates
4. Distribute new certificates to authorized users
5. Update nginx configuration and reload
6. Verify old certificates rejected

**Procedure: nginx Down**
1. Check container status: `docker ps -a | grep nginx`
2. Review logs: `docker logs trading_platform_nginx_mtls`
3. Verify certificates exist and valid
4. Test configuration: `docker exec trading_platform_nginx_mtls nginx -t`
5. Restart if healthy: `docker compose --profile mtls restart nginx_mtls`
6. If persistent failure: rollback to last known good config

---

## References

- **Implementation Plan:** `docs/ARCHIVE/TASKS_HISTORY/P2T3-Phase3_Component6-7_Plan.md`
- **Certificate Generation:** `scripts/generate_certs.py`
- **nginx Configuration:** `apps/web_console/nginx/nginx.conf`
- **Docker Compose:** `docker-compose.yml`
- **Integration Tests:** `tests/integration/test_mtls_integration.py`

---

**Last Updated:** 2025-11-22
**Maintained By:** Development Team
**Version:** 1.0 (P2T3 Phase 2 - Component 3)

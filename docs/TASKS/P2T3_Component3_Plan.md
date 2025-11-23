# Component 3: Nginx Reverse Proxy with HTTPS/TLS - Implementation Plan

**Component:** P2T3 Phase 2 - Component 3
**Estimated Effort:** 8-10 hours
**Dependencies:** Component 1 (certs) + Component 2 (JWT) complete
**Date:** 2025-11-22

---

## Objective

Configure nginx as a reverse proxy for the web console with:
- HTTPS termination using server certificates
- Client certificate validation (mTLS)
- TLS hardening (TLS 1.2+, cipher suites, HSTS)
- HTTP to HTTPS redirect
- Rate limiting for authenticated sessions (prevents abuse, allows normal Streamlit asset loading)
- Certificate reload without downtime

---

## Files to Create

### 1. `apps/web_console/nginx/nginx.conf`
**Purpose:** Nginx configuration for HTTPS/mTLS

**Key sections:**
```nginx
# http context (top-level configuration)
http {
    # Rate limiting zones (MUST be in http context, not server block)
    # Layer 1: Pre-HTTP rate limiting (IP-based, defense-in-depth)
    limit_req_zone $binary_remote_addr zone=preauth_limit:10m rate=20r/s;

    # Layer 2: Post-auth rate limiting (authenticated sessions by client DN)
    limit_req_zone $ssl_client_s_dn zone=mtls_limit:10m rate=10r/s;

    # Connection-level rate limiting (handshake flood protection)
    limit_conn_zone $binary_remote_addr zone=conn_limit:10m;

    # Rate limit response code
    limit_req_status 429;

    # HTTP to HTTPS redirect (301 permanent)
    server {
        listen 80;
        server_name web-console.trading-platform.local;
        return 301 https://$server_name$request_uri;
    }

    server {
        listen 443 ssl;
        server_name web-console.trading-platform.local;

        # TLS configuration
        ssl_certificate /etc/nginx/certs/server.crt;
        ssl_certificate_key /etc/nginx/certs/server.key;
        ssl_protocols TLSv1.2 TLSv1.3;

        # Mozilla Intermediate cipher suite (OpenSSL 1.1.1+)
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;  # Let client choose (modern best practice)

        # Modern elliptic curves (X25519 preferred for TLS 1.3)
        ssl_ecdh_curve X25519:secp384r1;

        # DH parameters for DHE cipher suites (4096-bit)
        ssl_dhparam /etc/nginx/certs/dhparam.pem;

        # Session cache for performance
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;
        ssl_session_tickets off;  # Disable for PFS (no ticket rotation)

        # OCSP stapling (production trust signaling)
        ssl_stapling on;
        ssl_stapling_verify on;
        ssl_trusted_certificate /etc/nginx/certs/ca.crt;  # Full chain
        resolver 8.8.8.8 8.8.4.4 valid=300s;  # Google DNS for OCSP
        resolver_timeout 5s;

        # Client certificate validation (mTLS)
        ssl_client_certificate /etc/nginx/certs/ca.crt;
        ssl_verify_client on;
        ssl_verify_depth 2;

        # HSTS header (prevent downgrade attacks)
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

        # Connection-level rate limiting (handshake flood protection)
        limit_conn conn_limit 50;  # Max 50 concurrent connections per IP

        # Request-level rate limiting (applied per-request)
        limit_req zone=preauth_limit burst=50 nodelay;  # Layer 1: IP-based
        limit_req zone=mtls_limit burst=20 nodelay;     # Layer 2: DN-based

        # TLS handshake DoS protection
        ssl_handshake_timeout 10s;
        keepalive_timeout 75s;
        keepalive_requests 100;

        # Pass client cert info to backend
        proxy_set_header X-SSL-Client-Cert $ssl_client_cert;
        proxy_set_header X-SSL-Client-S-DN $ssl_client_s_dn;
        proxy_set_header X-SSL-Client-Verify $ssl_client_verify;  # REQUIRED: Verification status
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;  # Original host for CSRF/routing

        # Default proxy timeouts (regular HTTP requests)
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;

        # WebSocket-specific location (long-lived connections)
        location /_stcore/stream {
            proxy_pass http://web_console:8501;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;

            # WebSocket-specific timeouts (Streamlit keeps WS open)
            proxy_read_timeout 3600s;  # 1 hour for idle WebSocket
            proxy_send_timeout 3600s;
            proxy_buffering off;  # Disable buffering for real-time streaming
        }

        # Default location (regular HTTP, assets, health checks)
        location / {
            proxy_pass http://web_console:8501;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
        }
    }
}
```

### 2. `apps/web_console/nginx/Dockerfile`
**Purpose:** Nginx container image with certificate support

```dockerfile
FROM nginx:alpine

# Install OpenSSL for dhparam generation if needed
RUN apk add --no-cache openssl

# Copy nginx configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Create certificate directory
RUN mkdir -p /etc/nginx/certs

# Expose ports
EXPOSE 80 443

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:80 || exit 1

CMD ["nginx", "-g", "daemon off;"]
```

**Note:** DH parameters (`dhparam.pem`) should be generated during certificate setup and mounted as a volume (not baked into image).

### 3. `apps/web_console/auth.py` (modification required)
**Purpose:** Add Streamlit header access mechanism for X-SSL-Client-S-DN

**Critical integration requirement:**
Streamlit does not expose HTTP headers via standard `request.headers` in script context. Need to access headers via one of these methods:

**Option 1: streamlit.web.server.websocket_headers (recommended)**
```python
from streamlit.web.server.websocket_headers import _get_websocket_headers

def get_client_dn_from_nginx() -> str | None:
    """Extract client DN from nginx-provided header."""
    try:
        headers = _get_websocket_headers()
        return headers.get("X-SSL-Client-S-DN")
    except Exception as e:
        logger.error(f"Failed to get client DN from headers: {e}")
        return None
```

**Option 2: Streamlit session state + custom middleware**
```python
# In app.py, register middleware to capture headers
from streamlit.web.server import Server
from streamlit.runtime.scriptrunner import get_script_run_ctx

# Store headers in session state during initial request
if "client_dn" not in st.session_state:
    ctx = get_script_run_ctx()
    if ctx and hasattr(ctx, "session_info"):
        headers = ctx.session_info.ws.request.headers
        st.session_state["client_dn"] = headers.get("X-SSL-Client-S-DN")
```

**Decision required:** Test both approaches during implementation to determine which is more reliable across Streamlit versions.

### 4. Backend JWT-DN Binding Contract (CRITICAL)

**Purpose:** Prevent header spoofing by enforcing cryptographic binding between client certificate and JWT.

**Required backend logic (`apps/web_console/auth.py`):**

```python
def verify_mtls_and_issue_jwt(request_headers: dict) -> tuple[str, dict] | tuple[None, None]:
    """
    Verify mTLS authentication and issue JWT bound to client certificate.

    SECURITY: This function enforces the JWT-DN binding contract to prevent
    header spoofing attacks from compromised containers inside the network.

    Returns:
        (jwt_token, claims) if verification succeeds, (None, None) otherwise
    """
    # Step 1: Verify nginx mTLS validation succeeded
    client_verify = request_headers.get("X-SSL-Client-Verify")
    if client_verify != "SUCCESS":
        logger.warning(f"mTLS verification failed: {client_verify}")
        return None, None

    # Step 2: Extract client DN from nginx header
    client_dn = request_headers.get("X-SSL-Client-S-DN")
    if not client_dn:
        logger.error("Missing X-SSL-Client-S-DN header despite SUCCESS verification")
        return None, None

    # Step 3: Parse client DN to extract CN (common name)
    # Example DN: "CN=alice,OU=trading,O=platform,C=US"
    import re
    match = re.search(r'CN=([^,]+)', client_dn)
    if not match:
        logger.error(f"Invalid client DN format: {client_dn}")
        return None, None
    client_cn = match.group(1)

    # Step 4: Issue JWT with DN binding
    # BINDING CONTRACT:
    # - JWT 'sub' (subject) = client DN (full DN, not just CN)
    # - JWT 'cn' (custom claim) = client CN (for display/logging)
    # - JWT 'cert_verify' (custom claim) = verification status
    # - JWT 'iat' (issued at) = current timestamp
    # - JWT 'exp' (expires) = iat + 8 hours (session duration)

    import time
    now = int(time.time())
    claims = {
        "sub": client_dn,  # CRITICAL: Full DN as subject
        "cn": client_cn,   # Common name for display
        "cert_verify": client_verify,  # Should be "SUCCESS"
        "iat": now,
        "exp": now + (8 * 3600),  # 8-hour session
        "jti": generate_unique_id(),  # JWT ID for audit logging
    }

    jwt_token = encode_jwt(claims, secret_key=JWT_SECRET)

    # Step 5: Log issuance with correlation
    logger.info(f"JWT issued for client_dn={client_dn}, jti={claims['jti']}")

    return jwt_token, claims


def validate_jwt_request(request_headers: dict, jwt_token: str) -> bool:
    """
    Validate JWT on subsequent requests and enforce DN binding.

    SECURITY: Prevents token reuse if client certificate changes.
    """
    # Decode JWT
    try:
        claims = decode_jwt(jwt_token, secret_key=JWT_SECRET)
    except Exception as e:
        logger.warning(f"JWT decode failed: {e}")
        return False

    # Verify current client DN matches JWT subject
    current_dn = request_headers.get("X-SSL-Client-S-DN")
    jwt_dn = claims.get("sub")

    if current_dn != jwt_dn:
        logger.error(f"DN mismatch: JWT sub={jwt_dn}, current DN={current_dn}")
        return False

    # Verify nginx still reports SUCCESS
    if request_headers.get("X-SSL-Client-Verify") != "SUCCESS":
        logger.error("Client verification status changed after JWT issuance")
        return False

    # Log successful validation with correlation
    logger.info(f"JWT validated for client_dn={current_dn}, jti={claims.get('jti')}")
    return True
```

**Enforcement points:**
1. **Initial authentication:** Check `X-SSL-Client-Verify == "SUCCESS"` before issuing JWT
2. **JWT issuance:** Bind `JWT.sub = X-SSL-Client-S-DN` (full DN)
3. **Subsequent requests:** Validate `JWT.sub == X-SSL-Client-S-DN` on every request
4. **Audit logging:** Log `jti` + `client_dn` together for correlation

**Attack prevention:**
- **Spoofed headers from inside network:** Rejected because `X-SSL-Client-Verify != "SUCCESS"`
- **JWT stolen/reused with different cert:** Rejected because `JWT.sub != current DN`
- **Legitimate JWT with no cert:** Rejected because `X-SSL-Client-Verify` missing

### 5. `docs/RUNBOOKS/web-console-mtls-setup.md`
**Purpose:** Deployment and troubleshooting documentation

**Sections:**
- Prerequisites (certificates generated, DH params)
- **Certificate SAN requirements:**
  - `web-console.trading-platform.local` (primary hostname)
  - `localhost` (local testing)
  - Docker Compose service hostname (e.g., `web_console_nginx`)
  - Example: `--san DNS:web-console.trading-platform.local,DNS:localhost,DNS:web_console_nginx`
- Docker Compose setup (dev vs mTLS profiles)
- Environment variables (WEB_CONSOLE_AUTH_TYPE)
- Port topology (profile-based isolation)
- **Production hardening checklist:**
  - [ ] DH parameters generated (4096-bit): `openssl dhparam -out dhparam.pem 4096`
  - [ ] Cipher suite configured (Mozilla Intermediate)
  - [ ] Server cert SANs cover all hostnames (localhost, web-console.trading-platform.local, nginx)
  - [ ] Docker Compose uses `--profile mtls` (NOT default/dev profile)
  - [ ] No direct port 8501 access (verify: `curl localhost:8501` should fail)
  - [ ] HSTS header verified (check browser dev tools)
  - [ ] Certificate expiration monitoring enabled (30-day warning)
  - [ ] OCSP stapling enabled and functional (`openssl s_client -status`)
  - [ ] DNS resolver configured for network environment (public vs internal vs Docker)
  - [ ] WebSocket timeout configured (3600s for `/_stcore/stream`)
  - [ ] X-SSL-Client-Verify header forwarding enabled
  - [ ] Backend JWT-DN binding enforced (JWT.sub == client DN)
- Certificate reload procedure (graceful, zero-downtime)
- Certificate revocation process
- Troubleshooting matrix (cert expired, chain broken, clock skew, SAN mismatch, reload failure)
- **HSTS considerations:**
  - Safe for `*.local` domains (non-routable)
  - For production domains: test with short max-age first, then extend to 1 year
  - Optionally add `preload` directive after testing (requires HTTPS for all subdomains)
  - Clearing HSTS: chrome://net-internals/#hsts (for testing only)

---

## Files to Modify

### 1. `docker-compose.yml`
**Changes:**
- Add nginx service
- Update web_console service to only expose 8501 internally (remove external port mapping)
- Add volume mounts for certificates (read-only)
- Conditional service startup based on AUTH_TYPE

**New nginx service:**
```yaml
nginx:
  build:
    context: ./apps/web_console/nginx
  container_name: web_console_nginx
  ports:
    - "80:80"    # HTTP redirect
    - "443:443"  # HTTPS
  volumes:
    - ./apps/web_console/certs:/etc/nginx/certs:ro  # Read-only cert access
    - ./apps/web_console/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
  depends_on:
    - web_console
  networks:
    - trading_platform
  profiles:
    - mtls  # Only start in mTLS mode
```

**Updated web_console service (profile-based port isolation):**
```yaml
# Base web_console service (shared config)
web_console:
  build:
    context: ./apps/web_console
  container_name: web_console
  environment:
    - WEB_CONSOLE_AUTH_TYPE=${WEB_CONSOLE_AUTH_TYPE:-dev}
  networks:
    - trading_platform
  # NO ports section in base - added per profile below

# Dev profile - exposes port 8501 for direct access
web_console_dev:
  extends: web_console
  ports:
    - "8501:8501"  # Dev mode: direct access without nginx
  profiles:
    - dev

# mTLS profile - NO host port mapping (nginx only)
web_console_mtls:
  extends: web_console
  # No ports section - only accessible via nginx on port 443
  profiles:
    - mtls
```

**Security enforcement:**
- Dev mode (`docker-compose --profile dev up`): port 8501 exposed for direct access
- mTLS mode (`docker-compose --profile mtls up`): port 8501 NOT exposed to host, only accessible via nginx
- **CRITICAL:** This prevents mTLS/JWT bypass - users cannot access port 8501 directly in mTLS mode
- No reliance on firewall rules as primary control (defense-in-depth only)

---

## Implementation Details

### 1. TLS Hardening

**Protocol versions:**
- Enable: TLSv1.2, TLSv1.3
- Disable: TLSv1.0, TLSv1.1 (deprecated, insecure)

**Cipher suites (Mozilla Intermediate profile):**
- ECDHE-ECDSA-AES128-GCM-SHA256 (ECDSA + AES-GCM, TLS 1.2+)
- ECDHE-RSA-AES128-GCM-SHA256 (RSA + AES-GCM, TLS 1.2+)
- ECDHE-ECDSA-AES256-GCM-SHA384 (ECDSA + AES-256, TLS 1.2+)
- ECDHE-RSA-AES256-GCM-SHA384 (RSA + AES-256, TLS 1.2+)
- ECDHE-ECDSA-CHACHA20-POLY1305 (Modern AEAD, mobile-friendly)
- ECDHE-RSA-CHACHA20-POLY1305 (Modern AEAD, mobile-friendly)
- DHE-RSA-AES128-GCM-SHA256 (Fallback with PFS)
- DHE-RSA-AES256-GCM-SHA384 (Fallback with PFS)
- `ssl_prefer_server_ciphers off` - Modern best practice (let client choose for performance)

**DH parameters:**
- 4096-bit DH params (`ssl_dhparam`) - Prevents weak DH attacks
- Generated during certificate setup: `openssl dhparam -out dhparam.pem 4096`
- Stored in `/etc/nginx/certs/dhparam.pem`

**Session caching:**
- `ssl_session_cache shared:SSL:10m` - 10MB shared cache (reduces handshake overhead)
- `ssl_session_timeout 10m` - Cache entries expire after 10 minutes

**Modern elliptic curves:**
- `ssl_ecdh_curve X25519:secp384r1` - X25519 preferred for TLS 1.3 (faster, secure)
- secp384r1 fallback for compatibility

**Session tickets:**
- `ssl_session_tickets off` - Disabled to preserve perfect forward secrecy
- No ticket rotation scheme implemented; safer to disable entirely

**OCSP stapling (production):**
- `ssl_stapling on` - Enable online certificate status checking
- `ssl_stapling_verify on` - Verify OCSP response
- `ssl_trusted_certificate /etc/nginx/certs/ca.crt` - Full chain for validation
- `resolver 8.8.8.8 8.8.4.4 valid=300s` - Google DNS for OCSP queries (5s timeout)
- `resolver_timeout 5s` - OCSP query timeout
- **Purpose:** Browsers verify cert validity without contacting CA (privacy + performance)
- **Restricted networks:** If outbound DNS blocked, use:
  - Internal DNS server: `resolver 10.0.0.53 valid=300s;`
  - Docker embedded DNS: `resolver 127.0.0.11 valid=30s;` (dynamic, shorter cache)
  - No OCSP: Comment out `ssl_stapling*` directives (fallback for air-gapped deployments)

**Compliance:**
- Mozilla Intermediate compatibility: supports modern browsers (5+ years old)
- PCI DSS compliant (TLS 1.2+, strong ciphers)
- NIST 800-52r2 compliant (forward secrecy, AEAD ciphers)

### 2. Client Certificate Validation (mTLS)

**Required headers:**
- `ssl_client_certificate /etc/nginx/certs/ca.crt` - CA for client cert validation
- `ssl_verify_client on` - Enforce client cert requirement
- `ssl_verify_depth 2` - Allow 2-level cert chain (client → CA)

**Passed to backend:**
- `X-SSL-Client-Cert` - Full client certificate (PEM)
- `X-SSL-Client-S-DN` - Client cert subject DN (for logging)
- `X-Forwarded-For` - Client IP address
- `X-Forwarded-Proto` - Protocol (https)

### 3. HSTS Header

**Configuration:**
```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

**Purpose:**
- `max-age=31536000` - Browser remembers HTTPS-only for 1 year
- `includeSubDomains` - Apply to all subdomains
- `always` - Add header even on error responses

**Effect:**
- After first HTTPS visit, browser refuses HTTP connections for 1 year
- Prevents downgrade attacks (MITM forcing HTTP)

### 4. HTTP to HTTPS Redirect

**Configuration:**
```nginx
server {
    listen 80;
    server_name web-console.trading-platform.local;
    return 301 https://$server_name$request_uri;
}
```

**Purpose:**
- 301 permanent redirect (browser caches)
- Ensures all traffic uses HTTPS
- Combined with HSTS, provides defense-in-depth

### 5. Rate Limiting (Three-Layer Protection)

**Layer 1: Connection-level rate limiting (handshake flood protection)**
```nginx
limit_conn_zone $binary_remote_addr zone=conn_limit:10m;
limit_conn conn_limit 50;  # Max 50 concurrent connections per IP
```
- **Purpose:** TRUE handshake flood protection (applies during connection establishment)
- **Key:** `$binary_remote_addr` (IP address)
- **Limit:** 50 concurrent connections per IP
- **Coverage:** Prevents TLS handshake DoS before HTTP layer

**Layer 2: Pre-HTTP rate limiting (IP-based defense-in-depth)**
```nginx
limit_req_zone $binary_remote_addr zone=preauth_limit:10m rate=20r/s;
limit_req zone=preauth_limit burst=50 nodelay;
```
- **Purpose:** Rate limit HTTP requests per IP (after handshake, before mTLS validation)
- **Limit:** 20 requests/second per IP, burst 50
- **Note:** Applied in HTTP phase, so cannot prevent handshake floods (Layer 1 handles that)

**Layer 3: Post-authentication rate limiting (DN-based)**
```nginx
limit_req_zone $ssl_client_s_dn zone=mtls_limit:10m rate=10r/s;
limit_req zone=mtls_limit burst=20 nodelay;
limit_req_status 429;
```
- **Purpose:** Prevents abuse from authenticated clients
- **Key:** `$ssl_client_s_dn` (client cert subject DN) - active AFTER successful mTLS
- **Limit:** 10 requests/second per client DN, burst 20
- **Streamlit compatibility:** Burst allows multiple asset loads (JS, CSS, fonts, WebSocket)
- **Response:** 429 Too Many Requests on limit exceeded

**Additional DoS protections:**
```nginx
ssl_handshake_timeout 10s;    # Limit handshake duration
keepalive_timeout 75s;         # Connection reuse window
keepalive_requests 100;        # Max requests per connection
worker_connections 1024;       # Max concurrent connections per worker (nginx.conf main context)
```

**Coverage:**
- Layer 1 (conn_limit): Mitigates handshake floods (connection-level)
- Layer 2 (preauth_limit): IP-based HTTP request limiting (defense-in-depth)
- Layer 3 (mtls_limit): Authenticated client abuse prevention
- Three layers provide comprehensive DoS protection at different protocol stages

### 6. Certificate Reload Without Downtime

**Procedure (with validation):**
```bash
# 1. Backup current certificates (safety)
cp apps/web_console/certs/ca.crt apps/web_console/certs/ca.crt.backup
cp apps/web_console/certs/server.crt apps/web_console/certs/server.crt.backup
cp apps/web_console/certs/server.key apps/web_console/certs/server.key.backup

# 2. Update certificate files
cp new_ca.crt apps/web_console/certs/ca.crt
cp new_server.crt apps/web_console/certs/server.crt
cp new_server.key apps/web_console/certs/server.key

# 3. Test configuration BEFORE reload (CRITICAL)
docker exec web_console_nginx nginx -t
if [ $? -ne 0 ]; then
  echo "ERROR: nginx config test failed, rolling back"
  mv apps/web_console/certs/ca.crt.backup apps/web_console/certs/ca.crt
  mv apps/web_console/certs/server.crt.backup apps/web_console/certs/server.crt
  mv apps/web_console/certs/server.key.backup apps/web_console/certs/server.key
  exit 1
fi

# 4. Reload nginx configuration (graceful, no downtime)
docker exec web_console_nginx nginx -s reload

# 5. Verify reload succeeded
docker exec web_console_nginx nginx -T | grep "ssl_certificate"
```

**How it works:**
- `nginx -t` - Tests config without applying (catches errors before reload)
- `nginx -s reload` - Graceful reload (keeps existing connections alive)
- New connections use new certificates
- Old connections continue with old certificates until closed
- **If config test fails:** Nginx preserves old config, no service disruption

---

## Environment-Based Mode Switching

**Dev mode (`WEB_CONSOLE_AUTH_TYPE=dev`):**
```
User → http://localhost:8501 → Streamlit (no nginx)
```

**mTLS mode (`WEB_CONSOLE_AUTH_TYPE=mtls_jwt`):**
```
User → https://localhost:443 → Nginx (mTLS + HTTPS) → http://web_console:8501 → Streamlit
```

**Docker Compose profiles:**
- `docker-compose up` - Starts web_console only (dev mode)
- `docker-compose --profile mtls up` - Starts web_console + nginx (mTLS mode)

---

## Testing Strategy

### Unit Tests
None required (nginx configuration, no Python code)

### Integration Tests (`test_mtls_integration.py`)

**Fixtures:**
- `nginx_container` - Start nginx + web_console in Docker
- `valid_client_cert` - Load client cert from Component 1
- `invalid_client_cert` - Generate self-signed cert (not CA-signed)

**Test cases:**
1. **HTTPS connection with valid client cert succeeds**
   - Make request with client cert
   - Verify 200 response
   - Verify X-Forwarded-Proto: https header received by backend

2. **Connection rejected without client cert**
   - Make request without client cert
   - Verify SSL handshake failure (connection refused)

3. **Connection rejected with invalid client cert**
   - Make request with self-signed cert
   - Verify SSL handshake failure

4. **Connection rejected with expired client cert**
   - Generate expired cert
   - Verify SSL handshake failure

5. **Connection rejected with client cert having invalid SAN**
   - Generate cert with wrong SAN
   - Verify SSL handshake failure

6. **HSTS header present in HTTPS responses**
   - Make HTTPS request
   - Verify Strict-Transport-Security header present
   - Verify max-age=31536000, includeSubDomains

7. **HTTP to HTTPS redirect (301)**
   - Make HTTP request to port 80
   - Verify 301 redirect to https://

8. **X-Forwarded-For header passed to backend**
   - Make HTTPS request
   - Verify backend receives X-Forwarded-For with client IP

9. **X-Forwarded-Proto header set to https**
   - Make HTTPS request
   - Verify backend receives X-Forwarded-Proto: https

10. **Rate limiting: Excessive authenticated requests return 429**
    - Make 30+ rapid requests with valid cert (>10/sec sustained for 2+ seconds)
    - Verify some requests return 429 Too Many Requests
    - Verify burst capacity (first 20 requests succeed quickly)

11. **Certificate reload without downtime**
    - Make HTTPS request with valid cert
    - Update ca.crt
    - Reload nginx (`nginx -s reload`)
    - Verify reload succeeds (exit code 0)
    - Make request with new cert
    - Verify new cert accepted
    - Make request with old cert
    - Verify old cert rejected

12. **Certificate reload failure handling**
    - Backup current ca.crt
    - Replace ca.crt with invalid file (e.g., empty or malformed PEM)
    - Attempt reload: `nginx -s reload`
    - Verify reload FAILS (non-zero exit code)
    - Verify nginx continues with old config (test connection still works)
    - Restore backup ca.crt
    - Verify reload succeeds with valid cert

13. **Client DN logged for auth events**
    - Make request with valid cert
    - Check nginx logs
    - Verify client DN (X-SSL-Client-S-DN) present

14. **Connection-level rate limiting (handshake flood protection)**
    - Open 60+ concurrent connections from same IP simultaneously
    - Verify connections after limit (50) are rejected or delayed
    - Verify established connections can still make requests
    - Purpose: Ensure `limit_conn` prevents connection exhaustion attacks

15. **Pre-HTTP rate limiting (IP-based)**
    - Make 60+ rapid requests from same IP (>20/sec sustained for 3+ seconds)
    - Verify some requests return 429 Too Many Requests
    - Verify burst capacity (first 50 requests succeed quickly)
    - Verify legitimate request from different IP succeeds (not blocked by attacker's IP)
    - Purpose: Ensure Layer 2 rate limiting functions correctly

16. **Backend receives client cert metadata for JWT validation**
    - Make HTTPS request with valid cert
    - Verify backend receives X-SSL-Client-S-DN header (via Streamlit header access mechanism)
    - Verify backend ties JWT issuance to client DN
    - Verify backend logs include both JWT `jti` and client DN for correlation

17. **TLS configuration validation (config lint)**
    - Run `docker exec web_console_nginx nginx -T`
    - Verify `ssl_session_tickets off` present
    - Verify `ssl_stapling on` present
    - Verify `ssl_prefer_server_ciphers off` present
    - Verify `ssl_ecdh_curve X25519:secp384r1` present
    - Verify `limit_req_zone` in http context (not server block)
    - Verify `limit_conn_zone` in http context
    - Purpose: Catch configuration regressions

18. **OCSP stapling validation (automated)**
    - Run `openssl s_client -connect localhost:443 -status -servername web-console.trading-platform.local < /dev/null`
    - Verify output contains "OCSP Response Status: successful"
    - Verify "OCSP response: no response sent" does NOT appear
    - Test with valid client cert to complete handshake
    - Purpose: Ensure OCSP stapling is active and functional

19. **WebSocket upgrade and header visibility**
    - Establish WebSocket connection to `wss://localhost:443/_stcore/stream` with valid client cert
    - Send initial Streamlit WebSocket handshake
    - Verify connection stays open for >2 minutes (idle timeout test)
    - Verify backend receives X-SSL-Client-S-DN header via Streamlit access mechanism
    - Verify backend receives X-SSL-Client-Verify = "SUCCESS" header
    - Send data over WebSocket and verify real-time response
    - Purpose: Ensure WebSocket timeout (3600s) and header forwarding work correctly

20. **JWT-DN binding enforcement**
    - Issue JWT for client DN "CN=alice,OU=trading,O=platform,C=US"
    - Verify JWT.sub == "CN=alice,OU=trading,O=platform,C=US" (full DN)
    - Attempt request with JWT but different client cert DN
    - Verify request REJECTED with "DN mismatch" error
    - Attempt request with JWT but X-SSL-Client-Verify != "SUCCESS"
    - Verify request REJECTED with "Verification status changed" error
    - Purpose: Prevent JWT reuse across different certificates

### Manual Tests

1. **Generate certificates with proper SANs**
   ```bash
   source .venv/bin/activate
   PYTHONPATH=. python3 scripts/generate_certs.py --ca-only
   PYTHONPATH=. python3 scripts/generate_certs.py --server-only \
     --san DNS:web-console.trading-platform.local,DNS:localhost,DNS:web_console_nginx
   PYTHONPATH=. python3 scripts/generate_certs.py --client test_user
   ```

2. **Generate DH parameters (4096-bit, ~10 minutes)**
   ```bash
   openssl dhparam -out apps/web_console/certs/dhparam.pem 4096
   # Expected: takes 5-15 minutes depending on CPU
   # Verify: file size should be ~800-1000 bytes
   ```

3. **Verify private key permissions (0600)**
   ```bash
   ls -l apps/web_console/certs/*.key apps/web_console/certs/dhparam.pem
   # Output: -rw------- (owner read/write only)
   # If not: chmod 600 apps/web_console/certs/*.key apps/web_console/certs/dhparam.pem
   ```

4. **Verify server certificate SANs**
   ```bash
   openssl x509 -in apps/web_console/certs/server.crt -noout -text | grep -A 5 "Subject Alternative Name"
   # Expected output:
   #   X509v3 Subject Alternative Name:
   #     DNS:web-console.trading-platform.local, DNS:localhost, DNS:web_console_nginx
   ```

5. **Verify port 8501 NOT exposed in mTLS mode**
   ```bash
   docker-compose --profile mtls up -d
   # Wait for services to start
   sleep 5
   # Attempt direct access to port 8501
   curl -I http://localhost:8501 2>&1
   # Expected: "Connection refused" or "Failed to connect"
   # If succeeds: SECURITY ISSUE - port should NOT be accessible
   ```

6. **Start nginx + web_console with mTLS**
   ```bash
   export WEB_CONSOLE_AUTH_TYPE=mtls_jwt
   docker-compose --profile mtls up -d
   ```

7. **Authenticate with valid client certificate**
   ```bash
   curl --cert apps/web_console/certs/client_test_user.crt \
        --key apps/web_console/certs/client_test_user.key \
        --cacert apps/web_console/certs/ca.crt \
        https://localhost:443
   # Verify: 200 OK
   ```

8. **Verify HSTS header in browser dev tools**
   - Open https://localhost:443 in Chrome
   - Open Dev Tools → Network
   - Check response headers
   - Verify: Strict-Transport-Security: max-age=31536000; includeSubDomains

9. **Test HTTP downgrade attempt**
   ```bash
   curl -I http://localhost:80
   # Verify: HTTP/1.1 301 Moved Permanently
   # Verify: Location: https://localhost:443
   ```

10. **Test certificate revocation**
   ```bash
   # Remove client cert from CA trust
   cp apps/web_console/certs/ca.crt apps/web_console/certs/ca.crt.bak
   # Edit ca.crt to remove test_user cert (in practice, regenerate CA without that client)

   # Reload nginx
   docker exec web_console_nginx nginx -s reload

   # Test with revoked cert
   curl --cert apps/web_console/certs/client_test_user.crt \
        --key apps/web_console/certs/client_test_user.key \
        --cacert apps/web_console/certs/ca.crt \
        https://localhost:443
   # Verify: SSL handshake failure
   ```

---

## Security Considerations

### 1. Certificate Storage
- Private keys NEVER committed to git (.gitignore enforcement)
- Private keys have 0600 permissions (owner read/write only)
- Certificates stored in volume mounts for Docker (read-only for nginx)

### 2. HTTPS Enforcement
- HSTS header with 1-year max-age
- HTTP to HTTPS redirect (301 permanent)
- No HTTP fallback in mTLS mode
- X-Forwarded-Proto header for backend HTTPS awareness

### 3. Certificate Revocation
- Manual revocation: Update ca.crt, reload nginx (no downtime)
- Document revocation process in runbook
- Future: Add CRL/OCSP if needed (Phase 3)

### 4. Audit Logging
**Required fields:**
- Client cert subject DN (from X-SSL-Client-S-DN header)
- Client IP (from X-Forwarded-For header)
- Timestamp
- Event type (mTLS success/failure)
- Failure reason (if applicable)

**Implementation:**
- Nginx access logs include client DN
- Backend logs JWT validation events with client DN

---

## Acceptance Criteria

**Security:**
- [ ] Port 8501 NOT exposed in mTLS mode (profile-based isolation prevents bypass)
- [ ] Client certificate validation enforces mTLS (ssl_verify_client on)
- [ ] Mozilla Intermediate cipher suite configured (strong AEAD ciphers only)
- [ ] 4096-bit DH parameters generated and configured
- [ ] Modern ECDH curves configured (X25519, secp384r1)
- [ ] Session tickets disabled (ssl_session_tickets off for PFS)
- [ ] OCSP stapling enabled (production trust signaling)
- [ ] HSTS header prevents downgrade attacks (max-age=31536000, includeSubDomains)
- [ ] HTTP to HTTPS redirect (301) enforced
- [ ] Three-layer rate limiting (conn: 50/IP, req: 20/sec per IP, auth: 10/sec per DN)
- [ ] TLS handshake DoS protections enabled (timeout, keepalive limits, connection limits)

**Functionality:**
- [ ] Nginx terminates TLS with server certificate
- [ ] Server certificate SANs cover all required hostnames (localhost, web-console.trading-platform.local, nginx service)
- [ ] Reverse proxy correctly forwards requests to Streamlit
- [ ] Proxy timeouts configured (120s default, 3600s for WebSocket `/_stcore/stream`)
- [ ] WebSocket connections stay alive for ≥1 hour idle (proxy_buffering off)
- [ ] X-Forwarded-Proto header informs backend of HTTPS
- [ ] X-Forwarded-Host header passed for CSRF/routing
- [ ] X-SSL-Client-S-DN header passed to backend for JWT correlation
- [ ] X-SSL-Client-Verify header passed (REQUIRED for spoofing prevention)
- [ ] Backend can access headers via Streamlit mechanism (tested both options)
- [ ] Backend JWT-DN binding enforced (JWT.sub == client DN, verified on every request)
- [ ] Backend rejects spoofed headers (X-SSL-Client-Verify != "SUCCESS")
- [ ] Backend rejects JWT reuse across different certs (DN mismatch detection)
- [ ] Certificate reload without downtime works (nginx -t validation before reload)
- [ ] Certificate reload failure handling works (old config preserved on error, rollback successful)

**Operations:**
- [ ] Docker Compose profiles support dev and mTLS modes (mutually exclusive)
- [ ] nginx.conf structure valid (limit_req_zone, limit_conn_zone in http context)
- [ ] Production hardening checklist complete (DH params, OCSP, session tickets off, cipher suite, SAN coverage, port isolation)
- [ ] Troubleshooting matrix documented (cert expired, chain broken, clock skew, SAN mismatch, reload failure, rate limits, websocket issues)
- [ ] HSTS guidance for production domains documented
- [ ] Backend JWT-to-DN contract documented
- [ ] Monitoring guidance documented (cert expiry alerts, handshake error counters)

**Operations:**
- [ ] DNS resolver configured for network (public/internal/Docker embedded)
- [ ] OCSP stapling fallback documented for air-gapped deployments

**Testing:**
- [ ] All 20 integration tests pass:
  - Config lint, connection-level rate limiting, Streamlit header access
  - OCSP stapling validation (automated via openssl s_client)
  - WebSocket upgrade and header visibility (idle timeout >2min)
  - JWT-DN binding enforcement (DN mismatch rejection)
- [ ] All 10 manual tests pass (including nginx -t before reload, SAN verification)
- [ ] Three-layer rate limiting verified (connection, request, authenticated)
- [ ] Port 8501 isolation verified (curl localhost:8501 fails in mTLS mode)
- [ ] Configuration regressions prevented (automated lint checks)
- [ ] WebSocket stays alive ≥1 hour under idle conditions

---

## Troubleshooting Matrix

| Issue | Symptoms | Diagnosis | Resolution |
|-------|----------|-----------|------------|
| **Certificate expired** | SSL handshake failure, browser warning | `openssl x509 -in server.crt -noout -dates` | Regenerate cert with `generate_certs.py --server-only`, reload nginx |
| **Certificate chain broken** | SSL handshake failure, "unable to verify the first certificate" | `openssl verify -CAfile ca.crt server.crt` | Verify CA cert present, check cert chain |
| **Clock skew** | JWT validation fails with "Token expired" | Check server time: `date`, compare with JWT exp | Sync system clock (NTP), use 10s leeway in JWT validation |
| **SAN mismatch** | SSL handshake failure, "certificate hostname mismatch" | `openssl x509 -in server.crt -noout -text \| grep DNS` | Regenerate cert with correct SAN |
| **nginx config error** | nginx fails to start, "configuration file test failed" | `nginx -t` | Check nginx.conf syntax, verify cert paths |
| **Reload failure (invalid cert)** | `nginx -s reload` fails, service continues with old config | Check nginx error log, verify cert file validity | Fix cert file, retry reload; nginx preserves old config on failure |
| **Rate limit triggered (post-auth)** | 429 Too Many Requests after authentication | Check nginx error logs for "limiting requests, excess:" | Wait 1 second (burst recovery), or contact admin to increase limit |
| **Rate limit triggered (pre-HTTP)** | 429 Too Many Requests (IP-based rate limit) | Check nginx error logs for "limiting requests, excess:" | IP hitting Layer 2 limit; wait 1 second or use different IP |
| **Rate limit triggered (connection-level)** | Connection refused or delayed | Check nginx error logs for "limiting connections" | IP hitting Layer 1 connection limit (50 concurrent); close idle connections |
| **HTTP not redirecting** | HTTP requests not redirected to HTTPS | Check nginx config for port 80 server block | Verify `return 301 https://...` present |
| **Port 8501 still accessible in mTLS mode** | Direct access to port 8501 bypasses mTLS | `docker ps` - check if web_console has host port mapping | Ensure using `docker-compose --profile mtls up` (not default profile) |
| **WebSocket upgrade failure** | Streamlit connection fails, "WebSocket closed" | Check nginx logs for upgrade header; verify `proxy_http_version 1.1` | Ensure `proxy_set_header Upgrade` and `Connection "upgrade"` present |
| **504 Gateway Timeout** | Nginx times out waiting for Streamlit response | Check `proxy_read_timeout` value; check Streamlit logs for slow queries | Increase `proxy_read_timeout` (currently 120s); optimize DB queries |
| **OCSP stapling failure** | Browser shows "Unable to check certificate revocation" | Check nginx error log for OCSP errors; verify DNS resolver reachable | Ensure `resolver 8.8.8.8` reachable; check firewall; verify CA supports OCSP |
| **Streamlit cannot access client DN header** | JWT validation fails, no client DN in logs | Test header access mechanism in auth.py | Verify Streamlit version compatibility; try alternate header access method (websocket_headers vs session_info) |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Port 8501 exposed in mTLS mode (bypass attack)** | **CRITICAL** | **Profile-based port isolation (dev vs mtls profiles); manual test #5 verifies port NOT accessible; integration tests enforce** |
| nginx misconfiguration allows HTTP bypass | CRITICAL | Integration tests verify HTTPS-only, HSTS enforcement, HTTP→HTTPS redirect; manual downgrade test required |
| Weak cipher suite allows downgrade attack | HIGH | Mozilla Intermediate profile (AEAD ciphers only); 4096-bit DH params; manual test with ssllabs.com scan |
| Certificate expiration in production | HIGH | Document 1-year server rotation with exact commands; add expiration monitoring (30-day warning) |
| SAN mismatch breaks localhost/docker testing | MEDIUM | Codify SAN requirements in cert generation script; runbook documents all required SANs; manual test #4 verifies |
| Rate limiting too aggressive for Streamlit | MEDIUM | Dual-layer design: pre-auth (20/sec), post-auth (10/sec, burst 20); monitor 429 errors |
| DH param generation takes too long | LOW | Document 5-15 minute generation time; consider caching dhparam.pem in CI/deployment artifacts |
| Docker volume mount issues | LOW | Use read-only mounts (`:ro`); verify permissions in manual tests; health check detects startup failures |

---

## Dependencies

**Requires:**
- Component 1: Certificate infrastructure (server.crt, server.key, ca.crt)
- Component 2: JWT validation logic (for backend integration)
- Docker + Docker Compose

**Blocks:**
- Phase 3 (OAuth2 implementation)

---

## Next Steps

1. ✅ Complete this planning document
2. 🔄 Request plan review via zen-mcp (Gemini planner + Codex planner)
3. ⏳ After approval, advance to implement step
4. ⏳ Create nginx configuration files
5. ⏳ Update docker-compose.yml
6. ⏳ Create runbook documentation
7. ⏳ Write integration tests
8. ⏳ Run manual tests
9. ⏳ Request code review (Gemini + Codex)
10. ⏳ Commit after review approval + CI pass

---

## References

- P2T3_PHASE2_PLAN.md: Lines 252-390 (Component 3 specification)
- docs/AI/Workflows/12-component-cycle.md: 6-step pattern
- nginx documentation: https://nginx.org/en/docs/
- OWASP TLS Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Transport_Layer_Protection_Cheat_Sheet.html

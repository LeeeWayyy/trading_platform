# P2T3 Phase 2 Implementation Plan: Mutual TLS + JWT Authentication

**Phase:** P2T3 - Web Console Authentication Hardening
**Component:** Phase 2 (Paper Trading Security)
**Estimated Effort:** 3-4 days (REVISED: +1 day for security hardening)
**Priority:** HIGH (required before paper trading deployment)
**Review Iteration:** 2 (addressing Gemini + Codex feedback)

---

## Objective

Implement mutual TLS (mTLS) + signed JWT tokens for paper trading deployment, providing certificate-based authentication with short-lived token validation.

**Success Criteria:**
- Client certificate validation enforced via nginx
- JWT tokens signed and validated with <1 hour expiration
- HTTPS-only deployment with HSTS headers
- Backward compatible with Phase 1 (dev mode)
- All tests pass with >85% coverage
- **All P2T3_TASK.md Phase 2 requirements met (lines 249-258)**

---

## Component Breakdown & Dependencies

**Sequential Component Dependencies:**
- Component 1 → Component 2: JWT signing requires RSA key pair from Component 1
- Component 2 → Component 3: Nginx requires JWT public key for validation
- All components required for Phase 3 (OAuth2)

**Workflow Gate Checkpoints (per docs/AI/Workflows/12-component-cycle.md):**
Each component follows 6-step pattern:
1. Plan → 2. Plan-review → 3. Implement → 4. Test → 5. Code Review → 6. Commit

---

### Component 1: Certificate Infrastructure Setup (6-8 hours, REVISED)

**What:** Create self-signed CA and certificate generation tooling with secure key storage

**Workflow Gates:**
- [ ] `./scripts/workflow_gate.py set-component "Component 1: Certificate Infrastructure"`
- [ ] `./scripts/workflow_gate.py advance plan-review` (this document)
- [ ] `./scripts/workflow_gate.py advance implement` (after plan approval)
- [ ] `./scripts/workflow_gate.py advance test` (after implementation)
- [ ] `./scripts/workflow_gate.py advance review` (after tests pass)
- [ ] `git commit` (after review approval + CI pass)

**Files to Create:**
- `scripts/generate_certs.py` - Automated certificate generation script
- `apps/web_console_ng/certs/README.md` - Certificate usage and rotation documentation
- `.gitignore` update - Exclude `certs/*.key` and `certs/*.pem`
- `docs/RUNBOOKS/web-console-cert-rotation.md` - Certificate rotation runbook

**Implementation:**
1. Create certificate generation script using `cryptography` library:
   - Generate self-signed CA (validity: 10 years, **RSA 4096-bit**)
   - Generate server certificate signed by CA (validity: 1 year, **RSA 4096-bit**)
     - **Subject Alternative Names (SANs):** `DNS:web-console.trading-platform.local, DNS:localhost, IP:127.0.0.1`
   - Generate client certificates signed by CA (validity: 90 days, **RSA 4096-bit**)
     - **Subject Alternative Names:** `DNS:client-{username}.trading-platform.local`
   - Support certificate renewal/rotation
   - **Key Permissions:** Set private keys to `0600` (owner read/write only)
   - **Script Flags:** `--ca-only`, `--server-only`, `--client <username>`, `--renew <cert-path>`
2. Document certificate generation process with exact commands
3. Add certificate validation helpers (chain verification, expiration checks)
4. **Certificate Revocation Strategy:**
   - Manual revocation: Remove client cert from CA trust list, redeploy nginx
   - Document revocation process in runbook
   - Future: Add CRL/OCSP support (Phase 3 if needed)

**Dependencies:**
- Add `cryptography>=41.0.0` to `apps/web_console_ng/requirements.txt`

**Tests:**
- `test_generate_certs.py`:
  - Verify CA, server, and client cert generation
  - Expiration date validation (10y CA, 1y server, 90d client)
  - Certificate chain validation (client → CA, server → CA)
  - **SAN validation:** Verify DNS/IP SANs present and correct
  - **Invalid SAN rejection:** Test cert with wrong SAN fails validation
  - Edge cases: Expired certs, invalid signatures, missing CA
  - **Key size validation:** Verify all keys are 4096-bit RSA
  - **Key permissions:** Verify private keys have 0600 permissions

**Acceptance Criteria:**
- [ ] Script generates valid CA, server, and client certificates (4096-bit RSA)
- [ ] Certificates have correct Subject Alternative Names (SANs)
- [ ] Certificate chain validates correctly
- [ ] Private keys have 0600 permissions
- [ ] Documentation covers generation, rotation, and revocation
- [ ] Runbook provides exact commands for 90-day client cert rotation
- [ ] Runbook provides exact commands for 1-year server cert rotation
- [ ] Certificate expiration monitoring documented (manual checks or future alerting)

**Outputs for Component 2:**
- `certs/ca.crt` - CA certificate
- `certs/jwt_private.key` - RSA private key for JWT signing (4096-bit, 0600 permissions)
- `certs/jwt_public.pem` - RSA public key for JWT validation
- `certs/server.crt` + `certs/server.key` - Server certificate for nginx

---

### Component 2: JWT Token Generation and Validation (8-10 hours, REVISED)

**What:** Implement JWT-based authentication with secure key management and comprehensive validation

**Workflow Gates:**
- [ ] `./scripts/workflow_gate.py set-component "Component 2: JWT Authentication"`
- [ ] Plan-review → Implement → Test → Code Review → Commit (same pattern)

**Dependencies:**
- **BLOCKS:** Requires Component 1 complete (jwt_private.key, jwt_public.pem)

**Files to Create:**
- `apps/web_console_ng/auth_jwt.py` - JWT token generation and validation

**Files to Modify:**
- `apps/web_console_ng/auth.py` - Add `_mtls_jwt_auth()` function
- `apps/web_console_ng/config.py` - Add JWT configuration
  - **Add AUTH_TYPE option:** `mtls_jwt` (alongside existing `dev`, `basic`, `oauth2`)
  - **Default to `dev`** for backward compatibility
  - **Config Migration:** Add validation that prevents `mtls_jwt` if certs missing

**Implementation:**

1. **Secure Key Loading (CRITICAL - addresses Gemini/Codex findings):**
   - **Option 1 (Recommended):** Load RSA private key from environment variable:
     ```python
     JWT_PRIVATE_KEY_PEM = os.getenv("JWT_PRIVATE_KEY_PEM")
     if not JWT_PRIVATE_KEY_PEM:
         raise ConfigError("JWT_PRIVATE_KEY_PEM environment variable required for mtls_jwt mode")
     private_key = serialization.load_pem_private_key(JWT_PRIVATE_KEY_PEM.encode(), password=None)
     ```
   - **Option 2 (Development Only):** Read from file with strict permissions check:
     ```python
     key_path = Path("certs/jwt_private.key")
     if key_path.stat().st_mode & 0o077:  # Check no group/other permissions
         raise ConfigError(f"Insecure key permissions on {key_path}")
     private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
     ```
   - **Production:** Document use of secrets manager (HashiCorp Vault, AWS Secrets Manager)
   - **Key Rotation Strategy:**
     - Document rotation cadence (annual or on-demand)
     - Support dual-key validation during rotation (old + new public keys)
     - Update runbook with rotation steps

2. **JWT Token Generation:**
   - Sign tokens with RS256 (RSA private key, 4096-bit)
   - **Include claims:**
     - `sub` (username)
     - `exp` (expiration timestamp)
     - `iat` (issued at timestamp)
     - `jti` (JWT ID - unique token identifier for revocation)
     - `session_id` (ties JWT to web console session)
     - **`aud` (audience):** `"web-console"`
     - **`iss` (issuer):** `"trading-platform-auth"`
   - Token expiration: **50 minutes** (under 1-hour requirement)
   - **Absolute session timeout:** 4 hours (no refresh beyond this)

3. **JWT Token Validation:**
   - Verify signature using RSA public key
   - Check expiration timestamp with **10-second leeway** (addresses clock skew)
   - **Validate all claims:**
     - `aud` must equal `"web-console"`
     - `iss` must equal `"trading-platform-auth"`
     - `sub` must be non-empty
     - `session_id` must match current session
     - `jti` must not be in revocation list
   - Audit invalid token attempts with full context:
     - Client cert subject DN (from `X-SSL-Client-S-DN` header)
     - Client IP (from `X-Forwarded-For` header)
     - Token claims (if parseable)
     - Failure reason (expired, invalid signature, revoked, etc.)

4. **Token Refresh Mechanism:**
   - Auto-refresh tokens at **75% lifetime** (37.5 min)
   - **Cap refresh count:** Max 5 refreshes per session (prevents infinite extension)
   - **Enforce absolute timeout:** No refresh if session > 4 hours old
   - Refresh resets token `exp` and `iat`, but keeps same `jti` and `session_id`

5. **JTI Revocation List:**
   - **Storage:** Redis with TTL aligned to absolute session timeout
     - Key: `jwt:revoked:{jti}`
     - Value: `{username, revoked_at, reason}`
     - **TTL: 4 hours** (match absolute session timeout, NOT token expiration)
     - **Critical:** TTL must cover full session window since same jti is reused across refreshes
     - **TTL Extension:** Extend TTL to 4h on each token refresh to prevent revoked sessions from becoming valid again
   - **Revocation API:** Add endpoint `/api/v1/auth/revoke-token` (admin only)
   - **Check on validation:** Query Redis before accepting token

6. **Integration with Existing Session Management:**
   - Store JWT in `st.session_state["jwt_token"]`
   - Validate JWT on every request before session timeout check
   - **Log client cert subject DN** during JWT exchange for audit trail:
     ```python
     client_dn = request.headers.get("X-SSL-Client-S-DN", "UNKNOWN")
     logger.info("JWT issued", extra={"client_dn": client_dn, "jti": jti, "sub": username})
     ```

7. **Backward Compatibility:**
   - Add `AUTH_TYPE=mtls_jwt` config option
   - Keep `AUTH_TYPE=dev` as default
   - **Regression tests:** Ensure Phase 1 (dev mode) flows still work unchanged
   - Config validation prevents `mtls_jwt` if certs directory missing

**Dependencies:**
- Add `PyJWT[crypto]>=2.8.0` to `apps/web_console_ng/requirements.txt`
- **Requires Redis** for JTI revocation list (already available from Phase 1)

**Tests:**
- `test_auth_jwt.py`:
  - Token generation with all required claims (sub, exp, iat, jti, session_id, aud, iss)
  - Token validation (signature, expiration, claims)
  - **Clock skew handling:** Token expiring in 5 seconds accepted with 10s leeway
  - Expired token rejection (no leeway if >10s past exp)
  - Invalid signature rejection
  - **Invalid audience/issuer rejection**
  - **Missing claims rejection** (sub, aud, iss)
  - **JTI revocation:** Revoked token rejected even if signature valid
  - **JTI revocation persistence:** Verify revoked jti stays revoked across refreshes (4-hour TTL)
  - **JTI revocation TTL extension:** Verify TTL extends to 4h on each refresh
  - Token refresh logic (count cap, absolute timeout enforcement)
  - Integration with session timeout
  - **Replayed JWT with same client cert:** Same jti rejected if revoked
  - **Key rotation simulation:** Dual-key validation during rotation
  - **Infinite refresh prevention:** 6th refresh attempt blocked
  - **Absolute timeout enforcement:** Refresh blocked after 4 hours

**Acceptance Criteria:**
- [ ] JWT tokens generated with RS256 algorithm and all required claims
- [ ] Tokens expire in <1 hour (50 minutes)
- [ ] Token validation checks signature, expiration, aud, iss, jti revocation
- [ ] Clock skew handled with 10-second leeway
- [ ] Auto-refresh at 75% lifetime with 5-refresh cap and 4-hour absolute timeout
- [ ] JTI revocation list stored in Redis with 4-hour TTL (aligned to absolute session timeout)
- [ ] JTI revocation TTL extends to 4h on each token refresh (prevents revoked sessions from expiring)
- [ ] Audit logging includes client cert DN, IP, jti, failure reasons
- [ ] Secure key loading from environment variable (or file with permission checks)
- [ ] Key rotation strategy documented in runbook
- [ ] Backward compatibility with Phase 1 (dev mode) verified via regression tests
- [ ] AUTH_TYPE=mtls_jwt config added with dev as default

**Outputs for Component 3:**
- `apps/web_console_ng/auth_jwt.py` module for nginx integration
- JWT validation logic for backend
- Redis-based JTI revocation list

---

### Component 3: Nginx Reverse Proxy with HTTPS/TLS (8-10 hours, REVISED)

**What:** Configure nginx for HTTPS termination, client certificate validation, and TLS hardening

**Workflow Gates:**
- [ ] `./scripts/workflow_gate.py set-component "Component 3: Nginx Reverse Proxy"`
- [ ] Plan-review → Implement → Test → Code Review → Commit (same pattern)

**Dependencies:**
- **BLOCKS:** Requires Component 1 complete (server.crt, server.key, ca.crt)
- **BLOCKS:** Requires Component 2 complete (JWT validation logic)

**Files to Create:**
- `apps/web_console_ng/nginx/nginx.conf` - Nginx configuration
- `apps/web_console_ng/nginx/Dockerfile` - Nginx container image
- `docs/RUNBOOKS/web-console-mtls-setup.md` - Deployment and troubleshooting documentation

**Files to Modify:**
- `docker-compose.yml` - Add nginx service, update web_console port mapping

**Implementation:**

1. **Nginx Configuration (TLS Hardening):**
   ```nginx
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
       ssl_ciphers HIGH:!aNULL:!MD5;
       ssl_prefer_server_ciphers on;  # Prevent client cipher downgrade

       # Session cache for performance
       ssl_session_cache shared:SSL:10m;
       ssl_session_timeout 10m;

       # Client certificate validation (mTLS)
       ssl_client_certificate /etc/nginx/certs/ca.crt;
       ssl_verify_client on;
       ssl_verify_depth 2;

       # HSTS header (prevent downgrade attacks)
       add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

       # Rate limiting for failed mTLS handshakes (prevent cert brute force)
       limit_req_zone $ssl_client_s_dn zone=mtls_limit:10m rate=10r/m;
       limit_req zone=mtls_limit burst=5 nodelay;
       limit_req_status 429;

       # Pass client cert info to backend
       proxy_set_header X-SSL-Client-Cert $ssl_client_cert;
       proxy_set_header X-SSL-Client-S-DN $ssl_client_s_dn;
       proxy_set_header X-Forwarded-For $remote_addr;
       proxy_set_header X-Forwarded-Proto https;  # Inform backend of HTTPS

       location / {
           proxy_pass http://web_console:8501;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
       }
   }
   ```

2. **Certificate Revocation Handling:**
   - Document manual revocation process (update ca.crt, reload nginx)
   - Provide nginx reload command without downtime: `docker exec nginx nginx -s reload`
   - Future: Add OCSP/CRL support if needed (Phase 3)

3. **Docker Integration:**
   - Create nginx container with certificate volumes
   - Update web_console service to only expose 8501 internally (not externally)
   - Expose nginx on ports 80 (redirect) and 443 (HTTPS) externally
   - **Volume mounts:**
     ```yaml
     nginx:
       volumes:
         - ./apps/web_console_ng/certs:/etc/nginx/certs:ro  # Read-only cert access
         - ./apps/web_console_ng/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
     ```

4. **Environment-based Mode Switching:**
   - `WEB_CONSOLE_AUTH_TYPE=dev` - HTTP only, no nginx (localhost:8501)
   - `WEB_CONSOLE_AUTH_TYPE=mtls_jwt` - HTTPS via nginx, client certs required (localhost:443)
   - Docker Compose conditional service startup based on AUTH_TYPE

5. **Port Topology Documentation:**
   ```
   Dev Mode (AUTH_TYPE=dev):
     User → http://localhost:8501 → Streamlit (no nginx)

   mTLS Mode (AUTH_TYPE=mtls_jwt):
     User → https://localhost:443 → Nginx (mTLS + HTTPS) → http://web_console:8501 → Streamlit
   ```

**Tests:**
- **Integration tests** (`test_mtls_integration.py`):
  - HTTPS connection succeeds with valid client cert
  - Connection rejected without client cert (SSL handshake failure)
  - Connection rejected with invalid/expired client cert
  - **Connection rejected with client cert having invalid SAN**
  - HSTS header present in all HTTPS responses
  - X-Forwarded-For header passed to backend
  - X-Forwarded-Proto header set to `https`
  - **HTTP to HTTPS redirect:** Request to http://localhost:80 → 301 to https://
  - **HTTPS downgrade prevention:** Verify HTTP requests blocked when nginx running
  - **Rate limiting:** 11th failed mTLS handshake in 1 minute returns 429
  - **Certificate reload:** Update ca.crt, reload nginx, verify new cert accepted
  - **Logging/audit content:** Verify client DN, IP, jti logged for auth events

**Acceptance Criteria:**
- [ ] Nginx terminates TLS with server certificate
- [ ] Client certificate validation enforces mTLS
- [ ] HSTS header prevents downgrade attacks
- [ ] HTTP to HTTPS redirect (301) enforced
- [ ] TLS hardening applied (ssl_prefer_server_ciphers, session cache)
- [ ] Rate limiting prevents mTLS brute force (10 req/min limit)
- [ ] Reverse proxy correctly forwards requests to Streamlit
- [ ] X-Forwarded-Proto header informs backend of HTTPS
- [ ] Docker Compose configuration supports both dev and mTLS modes
- [ ] Certificate reload without downtime documented
- [ ] Troubleshooting matrix documented (cert expired, chain broken, clock skew)

**Outputs:**
- Production-ready nginx reverse proxy
- Complete deployment documentation
- Troubleshooting runbook

---

## Testing Strategy

### Unit Tests (target: >85% coverage)
- `test_generate_certs.py`: Certificate generation, validation, SANs, key sizes
- `test_auth_jwt.py`: JWT token lifecycle, claims validation, revocation, refresh caps

### Integration Tests
- `test_mtls_integration.py`:
  - Full authentication flow (cert → JWT → session)
  - Token refresh during long session
  - Session timeout with valid JWT
  - Concurrent sessions with different JWTs
  - **Replayed JWT with same client cert**
  - **Invalid SAN on client cert**
  - **Rate limiting on failed mTLS handshakes**
  - **HTTP to HTTPS redirect verification**
  - **Certificate reload without downtime**
  - Audit log verification (client DN, IP, jti, failure reasons)

### Manual Tests
- [ ] Generate certificates using `scripts/generate_certs.py`
- [ ] Verify private key permissions (0600)
- [ ] Start nginx + web_console with mTLS enabled
- [ ] Authenticate with valid client certificate
- [ ] Verify JWT token in session state (all claims present)
- [ ] Wait for token auto-refresh (37.5 min)
- [ ] Trigger session timeout (15 min idle)
- [ ] Verify HSTS header in browser dev tools
- [ ] Test certificate revocation (update ca.crt, reload nginx, verify rejection)
- [ ] **Test HTTP downgrade attempt** (curl http://localhost:80, expect 301)
- [ ] **Test 6th refresh blocked** (simulate 4+ hour session)
- [ ] **Verify audit logs** contain client DN, IP, jti

---

## Security Considerations

1. **Certificate Storage:**
   - Private keys NEVER committed to git (.gitignore enforcement)
   - Private keys have 0600 permissions (owner read/write only)
   - Certificates stored in volume mounts for Docker (read-only for nginx)
   - Document secure key management for production (secrets manager)

2. **JWT Private Key Protection (CRITICAL):**
   - **Development:** Load from file with strict permission checks (0600)
   - **Production:** Load from environment variable or secrets manager
   - **Supported secrets managers:** HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager
   - **Key rotation:** Annual or on-demand, with dual-key validation during rotation
   - **Risk elevated to CRITICAL:** Compromised key allows full user impersonation

3. **Token Lifetime & Refresh:**
   - 50-minute expiration (under 1-hour requirement)
   - **Refresh cap:** Max 5 refreshes per session
   - **Absolute timeout:** 4 hours, no refresh beyond this
   - Token revocation via Redis-based JTI list (**4-hour TTL**, extended on each refresh)
   - Clock skew handled with 10-second leeway

4. **HTTPS Enforcement:**
   - HSTS header with 1-year max-age
   - HTTP to HTTPS redirect (301 permanent)
   - No HTTP fallback in mTLS mode
   - X-Forwarded-Proto header for backend HTTPS awareness

5. **Certificate Revocation:**
   - Manual revocation: Update ca.crt, reload nginx (no downtime)
   - Document revocation process in runbook
   - Future: Add CRL/OCSP if needed (Phase 3)

6. **Audit Logging:**
   - **Required fields:**
     - Client cert subject DN (from X-SSL-Client-S-DN header)
     - Client cert fingerprint (if available)
     - Client IP (from X-Forwarded-For header)
     - JWT ID (jti)
     - Username (sub claim)
     - Timestamp
     - Event type (issued, validated, refreshed, revoked, failed)
     - Failure reason (if applicable)
   - **PII handling:** Redact sensitive fields in non-secure logs
   - **Retention:** 90 days minimum for security audit trail

---

## P2T3_TASK.md Requirements Coverage (Lines 249-258)

**All Phase 2 requirements from task document:**
- [x] mTLS authentication (Component 3: nginx client cert validation)
- [x] JWT token generation and validation (Component 2)
- [x] Certificate infrastructure (Component 1)
- [x] **Rate limiting on auth endpoints** (Component 3: nginx rate limiting for mTLS failures)
- [x] **User guide with screenshots** (docs/RUNBOOKS/web-console-mtls-setup.md)
- [x] **Security architecture documentation** (this plan + runbooks)
- [x] **Auth upgrade path** (Phase 1 → Phase 2 → Phase 3 documented)
- [ ] **Penetration test before production** (scheduled after Phase 3, before live trading)

---

## Deployment Gates (from P2T3_TASK.md Risk #2)

- ✅ **Dev mode (AUTH_TYPE=dev):** Isolated development only
- 🎯 **mTLS+JWT mode (AUTH_TYPE=mtls_jwt):** Paper trading ONLY (this phase)
- ❌ **OAuth2 mode (AUTH_TYPE=oauth2):** Required for staging/production (Phase 3)

---

## Risks & Mitigations (UPDATED)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Certificate expiration in production | HIGH | Document 90-day client rotation, 1-year server rotation with exact commands; add expiration monitoring (future: alerting) |
| nginx misconfiguration allows HTTP bypass | CRITICAL | Integration tests verify HTTPS-only, HSTS enforcement, HTTP→HTTPS redirect; manual downgrade test required |
| JWT private key compromise | **CRITICAL** (ELEVATED) | **Use secrets manager for production (Vault, AWS/GCP); environment variable minimum; strict file permissions (0600); annual key rotation with dual-key validation** |
| Client cert distribution complexity | MEDIUM | Automate cert generation with script flags; document secure distribution (encrypted channels) |
| Infinite JWT refresh extends session | MEDIUM | **Cap refreshes at 5 per session; enforce 4-hour absolute timeout; no refresh beyond limit** |
| Clock skew causes valid token rejection | LOW | **10-second leeway in JWT validation using PyJWT's `leeway` parameter** |
| Certificate revocation before expiry | MEDIUM | **Manual revocation process: update ca.crt, reload nginx; document in runbook; future CRL/OCSP support** |

---

## Dependencies

**Component Dependencies (Sequential):**
- Component 1 → Component 2: RSA key pair required for JWT signing
- Component 2 → Component 3: JWT validation logic required for nginx backend integration

**Blocked by:** None (Phase 1 complete)
**Blocks:** Phase 3 (OAuth2 implementation)

**External Dependencies:**
- `cryptography>=41.0.0` - Certificate generation
- `PyJWT[crypto]>=2.8.0` - JWT token handling
- `nginx:alpine` - Reverse proxy
- **Redis** (already available from Phase 1) - JTI revocation list

---

## Acceptance Criteria Summary

**Component 1:**
- [ ] Certificate infrastructure generates valid certs (4096-bit RSA, correct SANs, 0600 permissions)
- [ ] Certificate rotation runbook complete with exact commands
- [ ] Certificate revocation process documented

**Component 2:**
- [ ] JWT tokens signed with RS256, all required claims (sub, exp, iat, jti, aud, iss, session_id)
- [ ] JWT validation with signature, expiration, aud/iss, jti revocation checks
- [ ] Clock skew handling (10s leeway)
- [ ] Refresh capped at 5x with 4-hour absolute timeout
- [ ] JTI revocation list in Redis with 4-hour TTL, extended on each refresh
- [ ] Revoked sessions remain revoked for full 4-hour window (verified via tests)
- [ ] Secure key loading (environment variable or file with permission checks)
- [ ] Backward compatibility with Phase 1 (dev mode) verified

**Component 3:**
- [ ] Nginx enforces mTLS and HTTPS-only
- [ ] HSTS header, HTTP→HTTPS redirect, TLS hardening applied
- [ ] Rate limiting for failed mTLS handshakes (10 req/min)
- [ ] Certificate reload without downtime documented
- [ ] Troubleshooting runbook complete

**Phase 2 Overall:**
- [ ] All tests pass with >85% coverage
- [ ] Documentation covers setup, rotation, troubleshooting
- [ ] Docker Compose supports dev/mTLS mode switching
- [ ] Audit logging includes client DN, IP, jti, failure reasons
- [ ] All P2T3_TASK.md Phase 2 requirements addressed
- [ ] Penetration test scheduled (after Phase 3, before production)

---

## Next Steps

1. ✅ Complete this planning document
2. 🔄 Request plan review via zen-mcp (Gemini + Codex) - **IN PROGRESS**
3. ⏳ After approval, implement Component 1 (Certificate Infrastructure)
   - Follow 6-step pattern: Plan → Plan-review → Implement → Test → Code Review → Commit
4. ⏳ Implement Component 2 (JWT Authentication)
   - Follow 6-step pattern
5. ⏳ Implement Component 3 (Nginx Reverse Proxy)
   - Follow 6-step pattern
6. ⏳ Comprehensive Phase 2 review before final commit
7. ⏳ Update task state, proceed to Phase 3 (OAuth2)

---

## References

- P2T3_TASK.md: Lines 249-258 (Component 7 phases)
- P2T3_TASK.md: Lines 272-286 (Risk #2: Auth Bypass Vulnerability)
- docs/AI/Workflows/12-component-cycle.md: 6-step pattern
- docs/AI/Workflows/00-analysis-checklist.md: Analysis requirements
- **Gemini Review Findings:** Continuation ID `adef16ed-ff4d-461b-afd4-ed1251bcd329`
- **Codex Review Findings:** Continuation ID `cb07e22e-026a-4f0f-b159-044c6a601e7b`

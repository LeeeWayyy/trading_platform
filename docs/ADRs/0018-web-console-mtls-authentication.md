# ADR 0018: Web Console mTLS Authentication with JWT Session Management

- Status: Accepted
- Date: 2025-11-23
- Related Task: P2T3 - Web Console Phase 2 Authentication
- PR: #65

## Context

The web console controls critical trading operations (manual orders, kill switch, circuit breakers). The initial Phase 1 implementation used basic HTTP authentication with hardcoded credentials, which presented several security risks:

1. **Credential exposure**: Basic Auth sends credentials with every request (base64-encoded, trivially reversible)
2. **No session management**: No idle timeout or absolute session limits
3. **No audit trail**: Cannot tie actions to specific authenticated sessions
4. **No certificate-based identity**: Cannot leverage PKI for strong cryptographic identity
5. **Replay attacks**: Captured Authorization headers can be replayed indefinitely
6. **No rate limiting**: Vulnerable to brute-force attacks
7. **Production blocker**: Basic Auth is unsuitable for paper trading or production environments

### Business Requirements

- **Kill switch protection**: Only authorized operators can halt trading
- **Manual order authorization**: Prevent unauthorized trade execution
- **Audit compliance**: Track who performed what action and when
- **Session security**: Automatic logout after inactivity
- **Defense in depth**: Multiple layers of authentication and authorization
- **Fail-closed design**: System rejects requests when security verification fails

### Technical Constraints

- **Streamlit framework**: Limited native support for custom auth flows
- **Docker deployment**: Client certificates must work across containerized services
- **No external dependencies**: Cannot rely on external IdP for paper trading
- **Browser compatibility**: Must support standard client certificate dialogs
- **Backward compatibility**: Dev mode must still support simple password auth

## Decision

Implement **Mutual TLS (mTLS) authentication** with **RS256 JWT session tokens** and **Redis-backed revocation**. This provides cryptographic client identity verification combined with stateful session management.

### Architecture Components

#### Component 1: Certificate Infrastructure

**Certificates:**
- Root CA (4096-bit RSA, 10-year validity) for internal PKI
- Server certificate (2048-bit RSA, 1-year validity) for nginx TLS
- Client certificates (2048-bit RSA, 1-year validity) per authorized user
- Diffie-Hellman parameters (4096-bit) for forward secrecy

**Key Features:**
- Automated certificate generation via `scripts/generate_certs.py`
- Certificate rotation procedures documented in runbooks
- Atomic rotation without service downtime
- Secure key storage (0600 permissions, Docker secrets-compatible)
- GPG-encrypted distribution packages for client certificates

**Rejection Rationale:**
- ❌ **Let's Encrypt**: Requires public DNS, cannot issue client certificates
- ❌ **Self-signed per-service**: No centralized trust root, rotation complexity
- ❌ **Hardware PKI**: Overkill for paper trading, adds operational complexity

#### Component 2: JWT Session Management

**JWT Design:**
- **Algorithm**: RS256 (asymmetric signing) instead of HS256 (symmetric)
  - Private key (4096-bit RSA) signs tokens on server
  - Public key verifies tokens (can be distributed safely)
  - Prevents token forgery even if public key is exposed
- **Token Pair**: Access token (15 min) + Refresh token (4 hours)
- **Claims**:
  - `sub`: Client certificate DN (subject distinguished name)
  - `jti`: Unique token identifier for revocation
  - `exp`: Expiration timestamp (UTC)
  - `iat`: Issued-at timestamp (UTC)
  - `session_binding`: HMAC(IP + User-Agent) for session hijacking prevention
- **Storage**: Redis for JTI revocation blacklist, in-memory for session state

**Atomic Token Rotation:**
```
Client sends:        access_token (expired) + refresh_token (valid)
Server validates:    refresh_token signature + exp + JTI not revoked
Server atomically:   1. Revoke old refresh_token JTI
                     2. Issue new access_token
                     3. Issue new refresh_token
                     4. Update session with new JTIs
Client receives:     Both tokens updated simultaneously
```

**Why RS256 over HS256:**
- **Key distribution**: Public key can be shared with monitoring systems for token verification without granting signing capability
- **Rotation safety**: Private key rotation doesn't require updating all validators
- **Audit clarity**: Signature proves server issued the token (only server has private key)
- **Industry standard**: OAuth2/OIDC best practice for production systems

**Rejection Rationale:**
- ❌ **HS256 (symmetric)**: Shared secret must be distributed to all validators, rotation requires coordinated updates
- ❌ **Opaque tokens**: Requires DB lookup on every request, no offline verification
- ❌ **Long-lived tokens**: Cannot enforce idle timeout, increases replay window

#### Component 3: Nginx Reverse Proxy

**Responsibilities:**
- TLS termination with client certificate verification
- mTLS handshake validation (certificate chain, validity dates, revocation)
- Request source verification (prevent direct Streamlit access)
- Rate limiting (connection-level, pre-auth, post-auth)
- Header forwarding with spoofing prevention
- WebSocket support for Streamlit streaming

**Configuration Highlights:**
- **TLS**: TLSv1.2/1.3 only, Mozilla Intermediate cipher suite
- **OCSP Stapling**: Online certificate status checking
- **Rate Limiting**:
  - Connection-level: 30 concurrent connections per IP (handles multiple browser tabs)
  - Pre-auth: 20 req/sec per IP (before mTLS verification)
  - Post-auth: 10 req/sec per client DN (after successful mTLS)
- **WebSocket Timeout**: 3600s for long-lived Streamlit connections
- **Static IP**: 172.28.0.10 in Docker network for deterministic trust anchor

**TRUSTED_PROXY_IPS Security:**
- **Fail-closed**: Empty TRUSTED_PROXY_IPS rejects all requests (prevents production misconfiguration)
- **Spoofing prevention**: `proxy_set_header` OVERWRITES incoming X-SSL-Client-* headers
- **Dev override**: `ALLOW_INSECURE_MTLS_DEV=true` allows bypassing proxy check (dev only)

**Rejection Rationale:**
- ❌ **Streamlit native TLS**: No client certificate support, cannot do mTLS
- ❌ **Traefik/Envoy**: Added complexity, nginx sufficient for our scale
- ❌ **HAProxy**: Less mature TLS client cert features vs nginx

### Security Design Principles

#### 1. Fail-Closed Architecture

System defaults to **deny** when verification cannot be performed:

```python
# Example: TRUSTED_PROXY_IPS enforcement
if not TRUSTED_PROXY_IPS:
    if not os.environ.get("ALLOW_INSECURE_MTLS_DEV", "").lower() == "true":
        # FAIL CLOSED: Reject request
        logger.error("mTLS auth rejected: TRUSTED_PROXY_IPS not configured")
        return False
```

All security checks follow this pattern:
- Certificate validation fails → Reject
- JWT signature invalid → Reject
- Token revoked → Reject
- Session binding mismatch → Reject
- Rate limit exceeded → Reject (429 Too Many Requests)

#### 2. Defense in Depth (5 Layers)

1. **Network**: Only nginx exposed (443/tcp), web_console internal only
2. **TLS**: Client certificate required for connection establishment
3. **JWT**: Valid signature + unexpired + not revoked
4. **Session**: IP + User-Agent binding prevents session hijacking
5. **Audit**: All authentication attempts logged with client DN

#### 3. JWT-DN Cryptographic Binding

**Problem**: Traditional JWT allows any valid token to be used from any client.

**Solution**: Bind JWT `sub` claim to mTLS client certificate DN:

```python
# Token issuance: Record DN from client certificate
client_dn = request.headers["X-SSL-Client-S-DN"]  # From nginx mTLS
jwt_claims = {
    "sub": client_dn,  # Must match certificate for all future requests
    ...
}

# Token validation: Verify DN matches current certificate
current_dn = request.headers["X-SSL-Client-S-DN"]
token_dn = jwt_claims["sub"]
if current_dn != token_dn:
    # Prevents stolen JWT from being used with different certificate
    return False
```

**Attack Prevention:**
- Stolen JWT cannot be used without corresponding client certificate private key
- Compromised certificate cannot use JWTs issued to other certificates
- Both cryptographic secrets (cert private key + JWT signature) required

#### 4. Session Binding (Anti-Hijacking)

**Problem**: Stolen session cookies can be used from different browser/IP.

**Solution**: Bind session to IP address + User-Agent:

```python
session_binding = hmac_sha256(client_ip + user_agent, secret_key)
session_state["session_binding"] = session_binding

# On every request:
current_binding = hmac_sha256(current_ip + current_user_agent, secret_key)
if current_binding != stored_binding:
    # Session hijacking detected: IP or User-Agent changed
    logger.error(f"Session binding mismatch for {username}")
    logout_user()
    return False
```

**Trade-offs:**
- ✅ Prevents session stealing across different browsers/IPs
- ❌ Breaks on legitimate IP changes (VPN reconnect, mobile network switch)
- 💡 Mitigation: User must re-login after network change (acceptable for high-security console)

#### 5. Token Revocation via Redis JTI Blacklist

**Why Revocation Needed:**
- JWTs are stateless and cannot be "invalidated" by design
- Logout must prevent continued token use
- Idle timeout requires server-side enforcement
- Compromised tokens must be revocable before natural expiry

**Implementation:**
```python
# On logout or timeout:
redis.setex(f"revoked_jti:{jti}", ttl=token_exp_seconds, value="revoked")

# On every request:
if redis.exists(f"revoked_jti:{jti}"):
    raise TokenRevokedError("Token was revoked on logout")
```

**Efficiency:**
- Only stores revoked JTIs (not all valid JTIs)
- TTL matches token expiration (auto-cleanup)
- Redis memory: ~100 bytes per revoked token × users × tokens
- Example: 10 users × 2 tokens × 100 bytes = 2 KB

**Rejection Rationale:**
- ❌ **Database revocation**: Too slow for per-request validation
- ❌ **Short-lived tokens only**: Cannot enforce idle timeout without refresh token rotation
- ❌ **No revocation**: Security incident requires waiting for natural expiry (up to 4 hours)

### Session Lifecycle

```
┌─────────────────┐
│ User opens      │
│ browser with    │
│ client cert     │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ 1. TLS Handshake                        │
│    - Nginx verifies client cert         │
│    - Extracts DN, serial, fingerprint   │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ 2. JWT Issuance (First Request)         │
│    - Verify X-SSL-Client-Verify=SUCCESS │
│    - Generate access token (15 min)     │
│    - Generate refresh token (4 hours)   │
│    - Bind to DN + IP + User-Agent       │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ 3. Active Usage                         │
│    - Every request validates access JWT │
│    - Idle timeout: 15 minutes           │
│    - Absolute timeout: 4 hours          │
│    - Token rotation on expiry           │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ 4. Automatic Logout                     │
│    - Idle timeout: 15 min no activity   │
│    - Absolute: 4 hours since login      │
│    - Revoke both JTIs in Redis          │
│    - Clear session state                │
└─────────────────────────────────────────┘
```

### Authentication Modes

The system supports three authentication modes via `WEB_CONSOLE_AUTH_TYPE` environment variable:

#### Mode 1: `dev` (Development Only)
- HTTP basic authentication
- Hardcoded credentials: `admin` / `trading_platform_admin_2024`
- No HTTPS required
- No session management
- **⚠️ NEVER use in production or paper trading**

#### Mode 2: `mtls` (Paper Trading & Production)
- Mutual TLS with client certificates
- RS256 JWT session tokens
- Redis-backed revocation
- Session binding (IP + User-Agent)
- Nginx reverse proxy required
- **✅ Approved for paper trading and production**

#### Mode 3: `oauth2` (Future Production)
- OAuth2/OIDC integration with external IdP (Okta, Auth0, Azure AD)
- Single Sign-On (SSO) support
- Multi-Factor Authentication (MFA)
- Centralized user management
- **🔮 Planned for Phase 3**

### Deployment Configurations

#### Docker Compose Profiles

**Dev Profile** (default):
```bash
docker-compose up -d
# Starts web_console with auth_type=dev
# Port 8501 exposed on host
# No nginx, no certificates required
```

**mTLS Profile**:
```bash
docker-compose --profile mtls up -d
# Starts web_console_mtls (auth_type=mtls) + nginx
# Port 443 exposed on host (HTTPS with mTLS)
# Requires certificates in apps/web_console/certs/
# TRUSTED_PROXY_IPS=172.28.0.10 (nginx static IP)
```

## Consequences

### Positive Outcomes

1. **Strong Authentication**: Client certificates provide cryptographic identity
2. **Session Security**: Automatic logout prevents unattended access
3. **Audit Compliance**: Full trail of authentication events with DN
4. **Attack Resistance**:
   - No credential stuffing (certs, not passwords)
   - No session hijacking (IP + UA binding)
   - No token replay (JTI revocation)
   - No header spoofing (nginx overwrites X-SSL-Client-*)
5. **Operational Safety**: Fail-closed design prevents misconfiguration
6. **Certificate Rotation**: Documented procedures with zero downtime
7. **Production Ready**: Security model approved for paper trading

### Trade-offs & Limitations

1. **Certificate Distribution**:
   - Must securely distribute client cert + private key to users
   - GPG encryption required for distribution
   - Lost certificates require CA-signed revocation

2. **Browser UX**:
   - Client cert selection dialog on first access
   - Certificate must be imported into browser/OS keychain
   - May confuse non-technical users

3. **Network Changes**:
   - Session binding breaks on IP/User-Agent change
   - User must re-login after VPN reconnect or browser update
   - Acceptable for high-security console, may annoy mobile users

4. **Operational Complexity**:
   - Certificate lifecycle management (issuance, rotation, revocation)
   - Redis dependency for token revocation
   - Nginx configuration complexity vs. direct Streamlit

5. **Redis Dependency**:
   - Redis unavailable → Cannot revoke tokens (fail-open risk)
   - Mitigation: Use short-lived tokens (15 min access, 4 hour refresh)
   - Consider: Fallback to in-memory revocation for Redis outage

6. **Token Storage**:
   - Streamlit session state (in-memory, lost on browser close)
   - No persistent storage (user must re-auth after browser restart)
   - Cannot use HttpOnly cookies (Streamlit limitation)

### Migration Plan

#### Phase 1 → Phase 2 (Basic Auth → mTLS)

**Pre-Migration:**
1. Generate CA and server certificates
2. Issue client certificates for all authorized users
3. Test mTLS authentication in staging environment
4. Document certificate import procedures for users

**Migration:**
1. Deploy nginx reverse proxy in docker-compose
2. Switch `WEB_CONSOLE_AUTH_TYPE=mtls`
3. Distribute client certificates to users (GPG-encrypted)
4. Update documentation and user guides
5. Monitor authentication logs for issues

**Rollback:**
- Switch `WEB_CONSOLE_AUTH_TYPE=dev` and remove nginx profile
- Client certificates remain valid for future retry

#### Phase 2 → Phase 3 (mTLS → OAuth2)

**Planned for future production deployment:**
1. Configure external IdP (Okta, Auth0, Azure AD)
2. Implement OAuth2 authorization code flow
3. Migrate user accounts to IdP
4. Keep mTLS as fallback for emergency access
5. Deprecate client certificates after validation period

### Security Audit & Compliance

**Pre-Production Requirements:**
1. ✅ Penetration testing of authentication flow
2. ✅ Code review by independent security reviewers (Gemini + Codex)
3. ✅ Verify certificate rotation procedures
4. ✅ Test session timeout enforcement
5. ✅ Validate rate limiting effectiveness
6. ⏳ Production readiness review with stakeholders

**Ongoing Monitoring:**
- Alert on failed authentication attempts (> 5/min from same IP)
- Track session duration metrics
- Monitor Redis revocation list size
- Review audit logs weekly for anomalies

### Follow-up Tasks

1. **Certificate Revocation**: Implement OCSP responder for real-time revocation checks
2. **MFA**: Add TOTP second factor for client certificate authentication
3. **Session Persistence**: Evaluate Redis-backed session storage for browser restart resilience
4. **OAuth2 Migration**: Design Phase 3 IdP integration architecture
5. **Monitoring**: Add Grafana dashboard for authentication metrics
6. **Documentation**: Create video tutorial for client certificate import

## References

- **Task Document**: `docs/TASKS/P2T3_TASK.md`
- **Implementation Plan**: `docs/TASKS/P2T3_Component{1,2,3}_Plan.md`
- **Runbooks**:
  - `docs/RUNBOOKS/web-console-mtls-setup.md` - Initial setup
  - `docs/RUNBOOKS/web-console-cert-rotation.md` - Certificate rotation
- **Code Locations**:
  - Certificate generation: `scripts/generate_certs.py`
  - JWT manager: `libs/web_console_auth/jwt_manager.py`
  - Session manager: `libs/web_console_auth/session.py`
  - Authentication flow: `apps/web_console/auth.py`
  - Nginx configuration: `apps/web_console/nginx/nginx.conf`
- **Security Standards**:
  - RFC 5280: X.509 Public Key Infrastructure
  - RFC 7519: JSON Web Token (JWT)
  - RFC 8446: TLS 1.3
  - Mozilla TLS Configuration: https://ssl-config.mozilla.org/
- **Related ADRs**:
  - ADR 0017: Secrets Management
  - ADR 0013: Workflow Automation Gates

---

**Acceptance Criteria Met:**
- ✅ Strong cryptographic authentication (mTLS + JWT)
- ✅ Session management with timeouts
- ✅ Full audit trail
- ✅ Fail-closed security design
- ✅ Production-ready for paper trading
- ✅ Certificate rotation procedures
- ✅ Independent security review (6 iterations)

**Status**: Accepted (2025-11-23)
**Implemented**: PR #65 - P2T3 mTLS Authentication System

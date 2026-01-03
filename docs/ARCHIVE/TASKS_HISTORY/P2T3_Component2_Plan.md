# P2T3 Component 2: JWT Token Generation and Validation

**Component:** Component 2 of P2T3 Phase 2 (mTLS + JWT Authentication)
**Estimated Effort:** 1-2 days
**Dependencies:** Component 1 (Certificate Infrastructure) - COMPLETE

---

## Scope

Implement JWT token generation and validation functionality for the Web Console authentication system. This component provides the cryptographic foundation for secure session management on top of mutual TLS.

**What Component 1 Delivered:**
- Certificate generation script (`scripts/generate_certs.py`)
- mTLS certificates (CA, server, client)
- JWT key pair (RSA 4096-bit: `jwt_private.key`, `jwt_public.pem`)
- Certificate rotation runbook
- Comprehensive tests (30 passing)

**What Component 2 Will Deliver:**
- JWT token generation library (`libs/web_console_auth/`)
- Token validation and claims verification
- Session management utilities (with rate limiting)
- Token refresh mechanism
- Structured security logging (authentication events)
- Cookie security parameter helpers
- Comprehensive unit tests
- Integration with existing certificate infrastructure

---

## Technical Design

### 1. Library Structure

```
libs/web_console_auth/
├── __init__.py
├── jwt_manager.py       # Core JWT operations
├── session.py           # Session management
├── config.py            # Auth configuration
└── exceptions.py        # Auth-specific exceptions

tests/libs/web_console_auth/
├── __init__.py
├── test_jwt_manager.py
├── test_session.py
└── test_integration.py
```

### 2. JWT Token Format

**Access Token (15-minute expiration):**
```python
{
    "sub": "user_id",           # Subject (user identifier)
    "iat": 1234567890,          # Issued at (Unix timestamp)
    "exp": 1234568790,          # Expiration (iat + 15 min)
    "jti": "uuid4",             # JWT ID (unique token identifier)
    "type": "access",           # Token type
    "session_id": "uuid4",      # Session identifier
    "ip": "192.168.1.1",        # Client IP (binding)
    "user_agent_hash": "sha256" # User agent hash (fingerprinting)
}
```

**Refresh Token (4-hour expiration):**
```python
{
    "sub": "user_id",
    "iat": 1234567890,
    "exp": 1234582290,          # iat + 4 hours
    "jti": "uuid4",
    "type": "refresh",
    "session_id": "uuid4",
    "access_jti": "uuid4"       # Linked access token
}
```

### 3. Key Components

#### JWTManager Class

**Responsibilities:**
- Load RSA keys from certificate infrastructure
- Generate signed JWT tokens (access + refresh)
- Validate token signatures
- Verify token claims (exp, type, session binding)
- Decode tokens safely

**Methods:**
```python
class JWTManager:
    def __init__(self, private_key_path: Path, public_key_path: Path)
    def generate_access_token(self, user_id: str, session_id: str, client_ip: str, user_agent: str) -> str
    def generate_refresh_token(self, user_id: str, session_id: str, access_jti: str) -> str
    def validate_token(self, token: str, expected_type: str) -> dict[str, Any]
    def decode_token(self, token: str) -> dict[str, Any]  # Without validation
    def revoke_token(self, jti: str) -> None  # Blacklist token
    def is_token_revoked(self, jti: str) -> bool
```

#### SessionManager Class

**Responsibilities:**
- Track active sessions in Redis
- Enforce session limits (max 3 concurrent per user)
- Handle session expiration and renewal
- Session binding (IP + user agent fingerprinting)

**Methods:**
```python
class SessionManager:
    def __init__(self, redis_client: Redis, jwt_manager: JWTManager, auth_config: AuthConfig)
    def create_session(self, user_id: str, client_ip: str, user_agent: str) -> tuple[str, str]  # Returns (access_token, refresh_token)
    def refresh_session(self, refresh_token: str, client_ip: str, user_agent: str) -> str  # Returns new access_token
    def validate_session(self, access_token: str, client_ip: str, user_agent: str) -> dict[str, Any]
    def terminate_session(self, session_id: str) -> None
    def cleanup_expired_sessions(self) -> int  # Returns count of cleaned sessions
    def check_rate_limit(self, client_ip: str, action: str = "auth") -> bool  # Returns True if within limits
    def get_session_cookie_params(self) -> dict[str, Any]  # Returns cookie security settings with max_age
```

### 4. Security Features

**Token Binding:**
- Bind access token to client IP + user agent hash
- Reject tokens from different IP/UA (MITM protection)
- Configurable strict mode (can relax IP check for NAT environments)

**Token Revocation (Codex Recommendation #5):**
- Redis-based blacklist (TTL = token expiration time - current time)
- Check blacklist on every validation
- Automatic cleanup via Redis TTL expiry (no dangling entries)

**Session Limits:**
- Max 3 concurrent sessions per user
- Oldest session auto-terminated on new login
- Redis sorted set tracks sessions by creation time

**Rate Limiting:**
- Redis-based sliding window rate limiting
- Default: 5 auth attempts per 15 minutes per IP
- Configurable limits for different actions (auth, refresh, validate)
- Automatic expiry of rate limit counters
- Returns HTTP 429 when limit exceeded

### 5. Configuration

```python
# libs/web_console_auth/config.py
@dataclass
class AuthConfig:
    # JWT Settings
    jwt_private_key_path: Path = Path("apps/web_console/certs/jwt_private.key")
    jwt_public_key_path: Path = Path("apps/web_console/certs/jwt_public.pem")
    jwt_algorithm: str = "RS256"

    # Token Expiration
    access_token_ttl: int = 900  # 15 minutes
    refresh_token_ttl: int = 14400  # 4 hours

    # Session Settings
    max_sessions_per_user: int = 3
    session_binding_strict: bool = True  # Reject IP/UA mismatch

    # Rate Limiting (Task Document Requirement)
    rate_limit_window: int = 900  # 15 minutes
    rate_limit_max_attempts: int = 5  # Max attempts per window per IP
    rate_limit_enabled: bool = True

    # Cookie Security Parameters (Codex Recommendation #3)
    cookie_secure: bool = True  # HTTPS-only
    cookie_httponly: bool = True  # No JavaScript access
    cookie_samesite: str = "Strict"  # CSRF protection
    cookie_domain: str | None = None  # Set to domain for subdomain sharing
    cookie_path: str = "/"
    cookie_max_age: int | None = None  # Defaults to refresh_token_ttl when None

    # Redis Keys
    redis_session_prefix: str = "web_console:session:"
    redis_blacklist_prefix: str = "web_console:token_blacklist:"
    redis_session_index_prefix: str = "web_console:user_sessions:"
    redis_rate_limit_prefix: str = "web_console:rate_limit:"

    @classmethod
    def from_env(cls) -> "AuthConfig":
        """Load from environment variables"""
        ...
```

---

## Implementation Steps (6-Step Pattern)

### Step 1: Plan (CURRENT)
- ✅ Define JWT token format and claims
- ✅ Design JWTManager and SessionManager APIs
- ✅ Specify security features (binding, revocation, limits)
- ✅ Document configuration schema

### Step 2: Plan Review
- Request Gemini + Codex review of this plan
- Address feedback and finalize design

### Step 3: Implement
- Create library structure
- Implement JWTManager (generation, validation, revocation)
- Implement SessionManager (create, refresh, validate, cleanup)
- Implement AuthConfig with environment variable loading
- Add custom exceptions (TokenExpiredError, TokenRevokedError, SessionLimitExceeded, etc.)

### Step 4: Test
- Unit tests for JWTManager:
  - Token generation (valid claims, correct signatures)
  - Token validation (signature verification, expiration, type checking)
  - Token revocation (blacklist storage and checking)
  - Invalid token handling (malformed, expired, wrong signature)
- Unit tests for SessionManager:
  - Session creation (token generation, Redis storage)
  - Session refresh (token rotation, binding verification)
  - Session validation (IP/UA binding, revocation checks)
  - Session limits (max 3 concurrent, oldest eviction)
  - Expired session cleanup
- Integration tests:
  - End-to-end token lifecycle (create → validate → refresh → revoke)
  - Multi-session concurrency (session limit enforcement)
  - Certificate infrastructure integration (load real JWT keys)

### Step 5: Code Review
- Request zen-mcp review (Codex + Gemini)
- Address all findings

### Step 6: Commit
- Run `make ci-local` (all tests + linting)
- Commit with review approval

---

## Test Strategy

### Unit Tests (Target: 95%+ coverage)

**test_jwt_manager.py:**
- `test_generate_access_token_valid_claims` - Verify token structure and claims
- `test_generate_refresh_token_linked_to_access` - Verify access_jti linkage
- `test_validate_token_valid_signature` - Accept valid signed tokens
- `test_validate_token_expired` - Reject expired tokens
- `test_validate_token_wrong_type` - Reject access token when refresh expected
- `test_validate_token_invalid_signature` - Reject tampered tokens
- `test_validate_token_malformed` - Reject non-JWT strings
- `test_revoke_token_blacklists_jti` - Verify Redis blacklist entry
- `test_validate_revoked_token_rejected` - Reject blacklisted tokens
- `test_decode_token_without_validation` - Raw decode for debugging

**test_session.py:**
- `test_create_session_returns_tokens` - Verify access + refresh token pair
- `test_create_session_stores_in_redis` - Verify session metadata in Redis
- `test_create_session_enforces_limit` - Evict oldest when exceeding max sessions
- `test_refresh_session_rotates_access_token` - New access token with same session_id
- `test_refresh_session_validates_binding` - Reject refresh from different IP/UA
- `test_validate_session_checks_ip_binding` - Reject token from different IP
- `test_validate_session_checks_ua_binding` - Reject token from different UA
- `test_terminate_session_revokes_tokens` - Blacklist all session tokens
- `test_cleanup_expired_sessions` - Remove expired session metadata
- `test_check_rate_limit_allows_within_limit` - Allow requests under rate limit
- `test_check_rate_limit_blocks_exceeded` - Block requests exceeding 5/15min
- `test_check_rate_limit_auto_expires` - Rate limit counter expires after window
- `test_get_session_cookie_params_secure_defaults` - Verify Secure, HttpOnly, SameSite=Strict

**test_integration.py:**
- `test_full_token_lifecycle` - Create → validate → refresh → revoke → validate (rejected)
- `test_concurrent_sessions_with_limit` - Create 4 sessions, verify 1st evicted
- `test_real_jwt_keys_from_certs` - Load actual JWT keys from apps/web_console/certs/

### Edge Cases

**Clock Skew:**
- Accept tokens issued up to 30s in the future (clock drift tolerance)
- Document NTP requirement for production

**Key Rotation:**
- Document graceful key rotation procedure (overlap period)
- Out of scope for Component 2, but design supports it

**Redis Failure:**
- Fail closed (reject all tokens if Redis unavailable)
- Log errors for monitoring

---

## Dependencies

**External Libraries (add to requirements.txt):**
```
pyjwt[crypto]>=2.8.0    # JWT encoding/decoding with RSA
cryptography>=41.0.0     # Already added in Component 1
redis>=5.0.0             # Already in project
pydantic>=2.0.0          # Already in project
```

**Internal Dependencies:**
- Component 1 deliverables (JWT key pair in `apps/web_console/certs/`)
- Redis (already in docker-compose.yml)
- Existing `libs/common/` utilities (logging, config)

---

## Security Considerations

**Token Storage:**
- NEVER log full tokens (log jti only)
- Tokens transmitted HTTPS-only (enforced in Component 3: Nginx)
- Refresh tokens stored in httpOnly cookies (when integrated with Streamlit)

**Cryptographic Strength:**
- RS256 algorithm (RSA-SHA256)
- 4096-bit RSA keys (generated in Component 1)
- UUID4 for jti (collision-resistant)

**Attack Surface:**
- Token replay: Mitigated by IP/UA binding + short expiration
- Token theft: Mitigated by HTTPS + httpOnly cookies (future)
- Session fixation: New session_id on every login
- Brute force: Rate limiting (future component)

**Security Logging (Gemini Finding #2 + Codex Recommendation #1 - ADDRESSED):**
- **Structured logging** using Python `logging` module (JSON format)
- Log all authentication events (success + failure) with severity levels
- **Token redaction:** NEVER log full tokens - log `jti` (token ID) only
- Required fields: timestamp, user_id, client_ip, user_agent_hash (SHA256), action, outcome, reason, jti
- Examples:
  - `logger.info("auth_success", extra={"user_id": ..., "ip": ..., "session_id": ..., "jti": "abc123..."})`
  - `logger.warning("auth_failure", extra={"user_id": ..., "ip": ..., "reason": "invalid_token", "jti": "def456..."})`
  - `logger.warning("rate_limit_exceeded", extra={"ip": ..., "action": "auth"})`
- Logs ingested by Loki/Prometheus (existing infrastructure)
- Persistent audit_log table integration deferred to Component 5 (Streamlit UI)

---

## Forward Compatibility with OAuth2 (Gemini Finding #4 - ADDRESSED)

**Context:**
- Component 2 implements custom JWT for Phase 2 (mTLS + JWT)
- Task Document mandates OAuth2/OIDC for Phase 3 (production)

**Design Decision:**
This JWT/Session infrastructure will **NOT be discarded** when migrating to OAuth2. Instead:

1. **OAuth2 as Identity Provider:**
   - OAuth2/OIDC provider handles **user authentication** (login flow, password management, MFA)
   - Returns **ID token** (user identity assertion) after successful authentication

2. **This Library as Session Manager:**
   - Accept ID token from OAuth2 provider, validate signature
   - Extract `sub` claim (user_id) from ID token
   - **Create session using existing `SessionManager.create_session()`**
   - Issue **our own access/refresh tokens** for API authorization

3. **Migration Path:**
   - Phase 2: User presents mTLS client cert → session created directly
   - Phase 3: User authenticates via OAuth2 → ID token validated → session created
   - **Same session management logic, different authentication upstream**

**Benefits:**
- Session binding (IP/UA), rate limiting, and revocation logic remains unchanged
- No rewrite of token validation in all downstream services
- OAuth2 handles authentication, our JWTs handle authorization/session

**Implementation Notes (Codex Recommendation #4):**
- Add `validate_id_token()` method to JWTManager in Phase 3 (validate OAuth2 ID token signature)
- ID token validation must verify:
  - **Issuer (`iss`):** Must match configured OAuth2 provider (e.g., "https://accounts.google.com")
  - **Audience (`aud`):** Must match our client ID registered with OAuth2 provider
  - **Expiration (`exp`):** Token not expired
  - **Signature:** Valid signature from OAuth2 provider's public key (JWKS endpoint)
- Existing session creation/validation logic untouched

## Out of Scope (Deferred to Later Components)

- ❌ Streamlit UI integration (Component 4)
- ❌ Nginx reverse proxy + HTTPS (Component 3)
- ❌ OAuth2/OIDC (Phase 3 - production auth)
- ❌ Persistent audit_log table integration (Component 5) - structured logging provided instead
- ❌ User management (create/delete users) - assumes external user directory
- ❌ Advanced rate limiting (per-user limits, dynamic thresholds) - basic IP-based limiting included
- ❌ HTTP 429 response mapping (Codex Recommendation #2) - deferred to Component 4 (Streamlit UI integration) where rate limit exceptions will be caught and mapped to HTTP responses

---

## Success Criteria

**Core JWT Functionality:**
- [ ] JWTManager generates valid RS256 tokens with correct claims
- [ ] JWTManager validates signatures and rejects tampered tokens
- [ ] Token revocation blacklists tokens in Redis with TTL

**Session Management:**
- [ ] SessionManager creates sessions with access + refresh token pairs
- [ ] SessionManager enforces max 3 concurrent sessions per user
- [ ] Session binding rejects tokens from different IP/UA
- [ ] Session refresh rotates access token while preserving session

**Security Features (Gemini Findings Addressed):**
- [ ] Rate limiting enforces 5 attempts/15min per IP (Finding #1)
- [ ] Structured logging for all auth events with required fields (Finding #2)
- [ ] Cookie security params helper with secure defaults (Finding #3)
- [ ] Forward compatibility design documented for OAuth2 migration (Finding #4)

**Quality Gates:**
- [ ] All unit tests pass with 95%+ coverage
- [ ] Integration test verifies full token lifecycle
- [ ] Integration test loads real JWT keys from Component 1 certs
- [ ] Rate limiting tests verify blocking and auto-expiry
- [ ] All mypy --strict type checks pass
- [ ] All ruff linting passes
- [ ] Documentation complete (docstrings + inline comments)

---

## Next Steps

1. Request plan review from Gemini + Codex
2. Transition to implementation phase
3. Create library structure and core classes
4. Implement JWT generation and validation
5. Implement session management
6. Write comprehensive tests
7. Request code review
8. Commit Component 2

---

**Created:** 2025-11-21
**Author:** Claude Code
**Status:** ✅ APPROVED - Ready for Implementation

## Plan Review History

**Iteration 1 (Gemini Review):**
- Status: NEEDS_REVISION
- Continuation ID: b95f03b3-21d4-4d4e-b960-c58b155d0c63
- Findings: 4 (2 MEDIUM, 2 LOW)

**Changes Made:**
1. ✅ **Finding #1 (MEDIUM - Completeness):** Added rate limiting to scope
   - Added `check_rate_limit()` method to SessionManager
   - Added rate limiting config (5 attempts/15min per IP)
   - Added Redis-based sliding window implementation
   - Added comprehensive tests

2. ✅ **Finding #2 (MEDIUM - Security):** Added structured logging
   - Mandatory Python `logging` module usage for all auth events
   - Required fields: timestamp, user_id, client_ip, user_agent, action, outcome, reason
   - Integration with existing Loki/Prometheus infrastructure
   - Examples provided in Security Considerations section

3. ✅ **Finding #3 (LOW - Architecture):** Added cookie security helper
   - Added `get_session_cookie_params()` method to SessionManager
   - Added cookie security params to AuthConfig (Secure, HttpOnly, SameSite, domain, path)
   - Enforces secure defaults centrally

4. ✅ **Finding #4 (LOW - Integration):** Added OAuth2 forward compatibility note
   - New section: "Forward Compatibility with OAuth2"
   - Documented migration path (OAuth2 as IdP, this library as session manager)
   - Clarified library will NOT be discarded in Phase 3

**Iteration 2 (Codex Review):**
- Status: **APPROVED**
- Continuation ID: dbc3296e-85da-442d-8eaa-696d679fb444
- Findings: 6 (ALL LOW severity - recommendations only)
- Decision: "Plan addresses Gemini findings and aligns with P2T3 task requirements for Component 2. Architecture is sound with clear security posture and test coverage. No blocking dependencies. Approved to move to implementation."

**Codex Recommendations Addressed:**
1. ✅ **Recommendation #1:** Ensure logger redacts tokens (log jti only)
   - Updated Security Logging section to mandate token redaction
   - Added `jti` to required log fields, NEVER log full tokens

2. ✅ **Recommendation #2:** Add 429 response mapping
   - Documented in Out of Scope - deferred to Component 4 (Streamlit UI integration)
   - Rate limit check will raise exception, UI layer will map to HTTP 429

3. ✅ **Recommendation #3:** Include max_age in cookie params aligned to refresh TTL
   - Added `cookie_max_age` to AuthConfig (defaults to refresh_token_ttl)
   - Updated `get_session_cookie_params()` signature

4. ✅ **Recommendation #4:** Document ID token issuer/audience checks for Phase 3 OAuth2
   - Added detailed OAuth2 ID token validation requirements (iss, aud, exp, signature)
   - Documented in Forward Compatibility section

5. ✅ **Recommendation #5:** Ensure blacklist TTL uses token exp
   - Updated Token Revocation section: TTL = token_exp - current_time
   - Prevents dangling entries in Redis

6. ✅ **Recommendation #6:** Test coverage adequate at 95% target
   - No changes needed

**Iteration 3 (Gemini Final Review):**
- Status: **✅ APPROVED**
- Continuation ID: b95f03b3-21d4-4d4e-b960-c58b155d0c63 (continued)
- Decision: "The plan is now complete, technically sound, and ready to proceed to implementation. There are no remaining concerns or risks that require further planning revision."

**Final Approval Summary:**
- ✅ All Gemini iteration 1 findings addressed
- ✅ All Codex recommendations addressed
- ✅ Gemini final approval obtained
- ✅ Codex approval obtained
- **Status: READY FOR IMPLEMENTATION**

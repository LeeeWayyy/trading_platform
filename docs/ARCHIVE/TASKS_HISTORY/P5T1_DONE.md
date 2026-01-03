---
id: P5T1
title: "NiceGUI Migration - Foundation & Async Infrastructure"
phase: P5
task: T1
priority: P0
owner: "@development-team"
state: COMPLETE
created: 2025-12-30
dependencies:
  - "ADR-0031 (in-scope: created as C0 first deliverable, gates remaining C0 tasks)"
estimated_effort: "10-14 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md]
features: [T1.1, T1.2, T1.3, T1.4]
---

# P5T1: NiceGUI Migration - Foundation & Async Infrastructure

**Phase:** P5 (Web Console Modernization)
**Status:** ✅ Complete
**Priority:** P0 (Foundation - Must Complete First)
**Owner:** @development-team
**Created:** 2025-12-30
**Estimated Effort:** 10-14 days
**Track:** Phase 1 from P5_PLANNING.md

---

## Objective

Establish the foundational infrastructure for the NiceGUI-based web console, including project structure, async HTTP client, server-side session management, and WebSocket connection recovery.

**Success looks like:**
- NiceGUI development server runs with hot reload
- Async HTTP client can communicate with execution gateway
- Server-side session store (Redis) securely manages user sessions
- WebSocket disconnection is handled gracefully with automatic reconnection
- All 4 auth types (dev, basic, mTLS, OAuth2) have middleware stubs ready for Phase 2

**Measurable SLAs:**
| Metric | Target | Measurement | Environment |
|--------|--------|-------------|-------------|
| Dev server startup time | < 5s | Time from `python main.py` to ready | Local dev |
| Async API client overhead | < 20ms p95 | Time in client code excluding backend response | Mocked gateway |
| Session creation time | < 50ms p95 | Time to create/store session in Redis | Local Redis |
| Session validation time | < 10ms p95 | Time to validate session per request | Local Redis |
| WS first reconnect attempt | < 2s | Time from disconnect to first reconnect attempt | Browser |
| WS reconnect (happy path) | < 3s | Time to restore connection when network available | Browser |

**Note:** SLA targets are measured with p95 latency in specified environment. Production targets may differ based on network topology.

---

## Acceptance Criteria

### T1.1 Project Structure & Dependencies
- [ ] Create `apps/web_console_ng/` directory with proper structure
- [ ] Add dependencies via Poetry: `poetry add nicegui httpx "redis[hiredis]" cryptography`
- [ ] Run `make requirements` to regenerate `requirements.txt` (never edit directly)
- [ ] Create `main.py` entry point with NiceGUI server configuration
- [ ] Create `config.py` with environment-based configuration (port from `web_console/config.py`)
- [ ] Configure hot reload for development mode
- [ ] Create `Dockerfile` for NiceGUI (port 8080) using generated `requirements.txt`
- [ ] Health check endpoint at `/health` returns JSON `{"status": "ok"}`
- [ ] All existing tests pass (no regressions)

### T1.2 Async Trading Client
- [ ] Create `AsyncTradingClient` singleton with connection pooling
- [ ] Implement all methods from `web_console/utils/api_client.py` as async
- [ ] Maintain auth header generation (`X-User-Id`, `X-User-Role`, `X-User-Strategies`, `X-User-Signature`)
- [ ] **Retry policy (idempotency-aware):**
  - [ ] **Idempotent operations (GET, HEAD):** Retry on transport errors AND 5xx responses
  - [ ] **Non-idempotent operations (POST, PUT, DELETE):** Retry ONLY on transport errors (ConnectionError, TimeoutError), NEVER on 5xx
  - [ ] **Trading safety:** Manual controls endpoints (kill switch, cancel/flatten) MUST NOT be retried on 5xx to prevent duplicate state changes
  - [ ] **Per-method retry map:** Define `RETRY_METHODS = {"GET", "HEAD"}` for 5xx retry eligibility
  - [ ] NEVER retry 4xx (client errors) regardless of method
- [ ] Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s)
- [ ] Timeout configuration: 5s total, 2s connect
- [ ] Error handling with structured logging
- [ ] **Client lifecycle guarantees:**
  - [ ] `startup()` MUST be called before any API calls (via `app.on_startup` hook)
  - [ ] `_client` property raises `RuntimeError("Client not initialized - call startup() first")` if None
  - [ ] `shutdown()` closes client gracefully (via `app.on_shutdown` hook)
  - [ ] Test: verify clear error when calling methods before startup
- [ ] Unit tests with mocked HTTP responses

### T1.3 Server-Side Session Architecture

**NiceGUI Request Access (validated by Spike C0.1 in C0):**
- [ ] **Primary method:** Pass `request: Request` parameter in `@ui.page` handler: `@ui.page('/') async def page(request: Request)`
- [ ] **Middleware access:** Use Starlette middleware's `request` argument directly (NiceGUI is FastAPI/Starlette-based)
- [ ] **NEVER use `app.storage.user`** for session ID - that's client-side localStorage, not secure HttpOnly cookie
- [ ] **Acceptance test:** Verify `request.cookies.get()` returns session cookie in page context

**Session Middleware Integration (cookie set/clear lifecycle):**
- [ ] **Middleware class:** `SessionMiddleware` registered via `app.add_middleware(SessionMiddleware)`
- [ ] **Cookie reading:** Middleware reads session cookie from `request.cookies.get(COOKIE_NAME)`, validates, attaches user to `request.state.user`
- [ ] **Cookie writing (login):** After successful auth, set cookie via `response.set_cookie()` in auth route handler
- [ ] **Cookie clearing (logout):** Delete cookie via `response.delete_cookie(COOKIE_NAME, path="/", domain=None)` in logout handler
- [ ] **Response access pattern:** Use `@app.post("/auth/login")` FastAPI route (NOT @ui.page) for login/logout to access Response object directly

**Middleware Registration Order (CRITICAL - add_middleware is LIFO):**
```python
# main.py - Middleware registration order
# NOTE: add_middleware() uses LIFO order - LAST added runs FIRST

# 1. Add AuthMiddleware FIRST (runs LAST) - provider-specific auth
app.add_middleware(AuthMiddleware)

# 2. Add SessionMiddleware SECOND (runs SECOND) - session validation
app.add_middleware(SessionMiddleware, session_store=session_store)

# 3. Add TrustedHostMiddleware LAST (runs FIRST) - security checks
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)

# Request flow: TrustedHost → Session → Auth → PageHandler
# Response flow: PageHandler → Auth → Session → TrustedHost
```

**Middleware Interaction (fail-closed behavior):**
- [ ] **SessionMiddleware:** Populates `request.state.user` from session store; sets to `None` if no/invalid session (fail-closed)
- [ ] **AuthMiddleware:** Reads `request.state.user`; if `None` and provider requires auth, returns 401/redirect
- [ ] **AuthMiddleware fail-closed (explicit):**
  - [ ] AuthMiddleware enforces authorization based on `request.state.user` (set by SessionMiddleware)
  - [ ] If `request.state.user` is `None` (no valid session): return 401 Unauthorized
  - [ ] **Middleware does NOT call provider.authenticate()** - providers are only called in login routes
  - [ ] **503 for stubbed providers:** Returned by `/auth/login` route when provider raises `NotImplementedError` (NOT by middleware)
  - [ ] **Phase 1:** Only DevAuthProvider is functional; non-dev providers return 503 at login time
- [ ] **Fail-closed rule:** If session validation fails for ANY reason, `request.state.user = None` (never bypass)
- [ ] **Acceptance tests:**
  - [ ] Cookie set on successful login with correct flags
  - [ ] Cookie cleared on logout
  - [ ] `request.state.user` populated after middleware for valid session
  - [ ] `request.state.user` is `None` for invalid/missing session (fail-closed)
  - [ ] Middleware order verified: TrustedHost runs before Session before Auth

**Core Session Store:**
- [ ] Create `ServerSessionStore` with **async Redis** backend (`redis.asyncio`)
- [ ] Session data encrypted with Fernet (key from env `SESSION_ENCRYPTION_KEY`)
- [ ] Signed session cookie (HMAC-SHA256) with session ID only - read from HTTP `request.cookies`, NOT `app.storage.user`
- [ ] **Session cookie configuration:** `HttpOnly=True`, `Secure=True`, `SameSite=Lax`, `Path=/` (Lax required for OAuth2 redirects)
- [ ] **__Host- prefix requirements (production):** `Secure=True`, `Path=/`, NO Domain attribute (required by spec)
- [ ] **Cookie name selection logic (aligned with P5_PLANNING):**
  - [ ] Production (`SESSION_COOKIE_SECURE=true` default): Cookie name = `__Host-nicegui_session`
  - [ ] Development (`SESSION_COOKIE_SECURE=false`): Cookie name = `nicegui_session` (no prefix, allows HTTP)
  - [ ] Config class: `CookieConfig` with `get_cookie_name()`, `get_cookie_flags()` methods
  - [ ] **Note:** Cookie name aligns with P5_PLANNING parallel-run guidance for Streamlit/NiceGUI separation
- [ ] **Cookie flags by environment:**
  - [ ] Prod: `secure=True, httponly=True, samesite="lax", path="/", domain=None`
  - [ ] Dev: `secure=False, httponly=True, samesite="lax", path="/", domain=None`
- [ ] **SameSite assumption for OAuth2:** `Lax` works for most OAuth2 IdPs (same-site top-level navigation)
  - [ ] Document: If specific IdP requires cross-site POST, may need `SameSite=None` + `Secure=True`
  - [ ] Config option: `SESSION_COOKIE_SAMESITE=lax|none` (default lax)
- [ ] **CSRF cookie configuration (environment-driven, mirrors session cookie):**
  - [ ] Cookie name: `ng_csrf` (no `__Host-` prefix - CSRF cookie must be readable by JS)
  - [ ] `HttpOnly=False` (must be readable by JS for double-submit pattern)
  - [ ] `Secure=SESSION_COOKIE_SECURE` (follows session cookie - True in prod, False in dev)
  - [ ] `SameSite=SESSION_COOKIE_SAMESITE` (follows session cookie - default Lax)
  - [ ] `Path=/`
- [ ] **CSRF cookie lifecycle (synchronized with session):**
  - [ ] **Set on login:** When session is created, set `ng_csrf` cookie with generated CSRF token
  - [ ] **Set on session rotation:** When session ID rotates (login, privilege escalation), regenerate CSRF token and update cookie
  - [ ] **Clear on logout:** Delete `ng_csrf` cookie when session is invalidated
  - [ ] **Implementation:** Login handler calls `response.set_cookie("ng_csrf", csrf_token, ...)` after session creation
  - [ ] **Implementation:** Session rotation regenerates CSRF token in session data AND updates cookie
  - [ ] **Implementation:** Logout handler calls `response.delete_cookie("ng_csrf", ...)` with session cookie
  - [ ] **Acceptance tests:**
    - [ ] CSRF cookie set on successful login
    - [ ] CSRF cookie value matches session's stored CSRF token
    - [ ] CSRF cookie updated after session rotation
    - [ ] CSRF cookie cleared on logout
    - [ ] CSRF cookie flags match environment (dev: secure=False, prod: secure=True)
- [ ] **Acceptance tests:**
  - [ ] Prod config uses `__Host-nicegui_session` with all required flags
  - [ ] Dev config uses `nicegui_session` with `secure=False`
  - [ ] Cookie flags match spec for each environment
- [ ] Idle timeout: 15 minutes (configurable via `SESSION_IDLE_TIMEOUT_MINUTES`)
- [ ] **Absolute timeout enforcement:** Store `created_at`, calculate remaining TTL = `absolute_timeout - (now - created_at)`
- [ ] Refuse session if absolute timeout exceeded; set Redis TTL to remaining duration only (NOT full 4 hours)

**CSRF Protection (explicit enforcement):**
- [ ] **Scope:** CSRF protection applies to HTTP POST/PUT/DELETE endpoints only
- [ ] **WebSocket events:** WS state changes rely on session validation + origin validation (no double-submit for WS)
- [ ] **CSRF-exempt paths (pre-authentication):**
  - [ ] `/auth/login` - No session exists yet, CSRF token unavailable
  - [ ] `/auth/callback` - OAuth2 callback, no session exists yet
  - [ ] `/auth/logout` - Session termination, CSRF optional (session invalidation is safe even if spoofed)
  - [ ] `/health` - Health check, no state change
  - [ ] **Implementation:** `CSRF_EXEMPT_PATHS = {"/auth/login", "/auth/callback", "/auth/logout", "/health"}`
  - [ ] **Validation logic:** Skip CSRF check if `request.url.path in CSRF_EXEMPT_PATHS`
- [ ] **CSRF validation component:** FastAPI dependency `verify_csrf_token(request: Request, x_csrf_token: str = Header(...))`
- [ ] **Validation logic:**
  1. Check if path is in `CSRF_EXEMPT_PATHS` → skip validation
  2. Check if session exists (`request.state.user` is not None):
     - If no session AND path requires auth: rely on `@requires_auth` to reject (401)
     - If no session AND path is public: no CSRF needed (no session = no CSRF token to validate)
  3. If session exists: Extract CSRF token from session (via `request.state.user.csrf_token`)
  4. Compare with `X-CSRF-Token` header (constant-time comparison)
  5. If mismatch or missing: return 403 Forbidden with `{"error": "csrf_invalid"}`
- [ ] **Auth + CSRF layering:** State-changing routes use BOTH `@requires_auth` AND `Depends(verify_csrf_token)`:
  - `@requires_auth` runs first → rejects unauthenticated (401)
  - `verify_csrf_token` runs second → validates CSRF for authenticated users (403)
- [ ] **Integration:** Apply `Depends(verify_csrf_token)` to all state-changing FastAPI routes (automatically skips exempt paths)
- [ ] Double-submit cookie pattern: CSRF token in `ng_csrf` cookie (JS-readable) + `X-CSRF-Token` header
- [ ] Generate per-session CSRF token on session creation, store in session data
- [ ] **Acceptance tests:**
  - [ ] Valid CSRF token in header: request succeeds
  - [ ] Missing CSRF header: 403 Forbidden
  - [ ] Invalid/tampered CSRF token: 403 Forbidden
  - [ ] GET/HEAD requests: no CSRF validation required
  - [ ] `/auth/login` POST without CSRF: succeeds (exempt path)
  - [ ] `/auth/callback` POST without CSRF: succeeds (exempt path)
  - [ ] Unauthenticated POST to non-exempt path: 401 Unauthorized (session middleware rejects first)

**Security Hardening:**
- [ ] **Session fixation protection:** Rotate session ID on login AND privilege escalation
- [ ] **Replay protection (implemented via absolute timeout):**
  - [ ] **Note:** Replay protection is implemented through the existing absolute timeout mechanism
  - [ ] Store `created_at` timestamp in session data (already specified in "Absolute timeout enforcement")
  - [ ] **Staleness = absolute timeout:** Session rejected if `(now - created_at) > SESSION_ABSOLUTE_TIMEOUT` (4 hours)
  - [ ] **Rotation invalidation:** When session ID rotates, old session ID is immediately deleted from Redis
  - [ ] **No separate `rotation_count`:** Session rotation deletes old ID, making replay of old tokens impossible
  - [ ] **Implementation:** Already in `validate_session()` - checks `remaining_ttl = absolute_timeout - age_seconds`
  - [ ] **Acceptance tests (covered by session store tests):**
    - [ ] Session within absolute timeout: validates successfully
    - [ ] Session past absolute timeout: rejected
    - [ ] After rotation: old session ID returns None, new session ID validates

**Deferred Security Items (P5_PLANNING → Phase 2):**
The following security hardening items from P5_PLANNING are explicitly deferred to Phase 2 (T2.x auth implementation):
- [ ] **Account lockout:** Track failed login attempts per user, lock after N failures
  - **Rationale:** Requires auth provider implementation (Phase 2)
  - **Dependency:** T2.1 (DevAuthProvider), T2.2 (BasicAuthProvider), T2.4 (OAuth2Provider)
  - **Reference:** P5_PLANNING "Session Security Hardening Checklist" item
- [ ] **JWKS cache/rotation:** Cache JWKS keys for OAuth2/JWT validation, handle rotation
  - **Rationale:** Only needed for OAuth2/JWT auth types (Phase 2)
  - **Dependency:** T2.4 (OAuth2AuthProvider implementation)
  - **Reference:** P5_PLANNING "Session Security Hardening Checklist" item
- [ ] **OAuth2 refresh token storage/rotation:** Secure storage and rotation of OAuth2 refresh tokens
  - **Rationale:** Only needed for OAuth2 auth type (Phase 2)
  - **Dependency:** T2.4 (OAuth2AuthProvider implementation)
  - **Reference:** P5_PLANNING "Session Security Hardening Checklist" item
  - **Note:** This is DISTINCT from session ID rotation (implemented in T1.3 as session fixation protection)
- [ ] **P5T2 prerequisite:** T2.x task document MUST include these items as acceptance criteria

**Token Rotation Clarification (P5_PLANNING alignment):**
P5_PLANNING T1.3 mentions "Token refresh/rotation strategy". This is implemented as:
- [ ] **Session ID rotation (Phase 1):** Rotate session ID on login and privilege escalation → Implemented in T1.3 "Session fixation protection"
- [ ] **CSRF token rotation (Phase 1):** Regenerate CSRF token on session rotation → Implemented in T1.3 "CSRF cookie lifecycle"
- [ ] **OAuth2 refresh token rotation (Phase 2):** Rotate refresh tokens on use → Deferred to T2.4 (OAuth2AuthProvider)
- [ ] **Key rotation support (explicit format):**
  - [ ] **Fernet:** Use `MultiFernet` with list of keys `[current_key, previous_key]` - first key encrypts, any key decrypts
  - [ ] **Full cookie value format:** `{session_id}.{key_id}:{signature}` (e.g., `abc123xyz.01:deadbeef...`)
  - [ ] **Parsing logic:** `rsplit('.', 1)` to get `(session_id, key_sig)`, then `split(':', 1)` on `key_sig` to get `(key_id, signature)`
  - [ ] **Example:** `abc123.01:a1b2c3` → session_id=`abc123`, key_id=`01`, signature=`a1b2c3`
  - [ ] Support multiple signing keys: `HMAC_SIGNING_KEYS=01:key1_hex,02:key2_hex` (first is current, others for validation)
  - [ ] Validation logic: Parse cookie → extract key_id → lookup key → verify HMAC; reject if parse fails or key_id not found
  - [ ] **Acceptance tests:**
    - [ ] Valid cookie with current key parses and validates
    - [ ] Valid cookie with previous key (rotation) validates
    - [ ] Cookie with unknown key_id rejected
    - [ ] Malformed cookie (missing `.` or `:`) rejected
    - [ ] Tampered signature rejected
- [ ] **Redis transport security:** Require `REDIS_TLS=true` in production, Redis AUTH password
- [ ] **Rate limiting:** Max 10 session creations per IP per minute, 100 validations per minute
- [ ] **Device binding (configurable):**
  - [ ] **Storage format at session creation:**
    - [ ] `ip_subnet`: First 3 octets of client IP (e.g., `192.168.1` for `192.168.1.50`)
    - [ ] `ua_hash`: SHA256 hash of normalized User-Agent string (lowercase, trimmed)
    - [ ] Store in session data: `{"device": {"ip_subnet": "...", "ua_hash": "..."}}`
  - [ ] **Validation on each request:**
    - [ ] Extract current IP subnet (first 3 octets of `get_client_ip(request)`)
    - [ ] Compute current UA hash from `request.headers.get("User-Agent")`
    - [ ] Compare against stored values (exact match required)
  - [ ] **Config toggles:**
    - [ ] `DEVICE_BINDING_ENABLED=true|false` (default: `true` in prod, `false` in dev)
    - [ ] `DEVICE_BINDING_SUBNET_MASK=24|16|8` (default: 24 - /24 subnet)
  - [ ] **Mismatch handling:**
    - [ ] If IP subnet mismatch: Invalidate session, return 401, log security event
    - [ ] If UA hash mismatch: Invalidate session, return 401, log security event
  - [ ] **IP churn handling:**
    - [ ] Mobile clients with changing IPs: Recommend `/16` subnet mask or disable binding
    - [ ] Document trade-off: tighter binding = more security, more false positives
  - [ ] **Trusted proxy integration:** Uses `get_client_ip(request)` which handles X-Forwarded-For
  - [ ] **Acceptance tests:**
    - [ ] Session created with device fingerprint (IP subnet + UA hash stored)
    - [ ] Same device: session validates successfully
    - [ ] Different IP subnet: session invalidated, 401 returned
    - [ ] Different User-Agent: session invalidated, 401 returned
    - [ ] `DEVICE_BINDING_ENABLED=false`: no device validation (passes regardless)
    - [ ] `/16` subnet mask: allows IPs in same /16 range
- [ ] **Trusted proxy handling (X-Forwarded-For):**
  - [ ] Configure via `TRUSTED_PROXY_IPS` (comma-separated CIDRs, e.g., `10.0.0.0/8,172.16.0.0/12`)
  - [ ] **Integration point:** `get_client_ip(request: Request) -> str` utility function
  - [ ] **Called from:** SessionMiddleware (for rate limiting, device binding), audit logging
  - [ ] **Implementation location:** `apps/web_console_ng/auth/client_ip.py`
  - [ ] **Parsing algorithm:**
    1. Get direct IP from `scope["client"][0]` (ASGI connection info)
    2. If direct client IP is in `TRUSTED_PROXY_IPS`, trust `X-Forwarded-For` header
    3. Parse `X-Forwarded-For` right-to-left (rightmost is closest proxy)
    4. Skip IPs in trusted proxy list until first untrusted IP = real client IP
    5. If no `X-Forwarded-For` or all IPs trusted, use direct connection IP
  - [ ] **Spoofing prevention:** NEVER trust `X-Forwarded-For` from untrusted source
  - [ ] **Default:** Empty `TRUSTED_PROXY_IPS` = use direct connection IP only (secure default)
  - [ ] **Acceptance tests:**
    - [ ] Direct connection (no proxy): returns connection IP from `scope["client"]`
    - [ ] Trusted proxy with valid `X-Forwarded-For`: returns first untrusted IP
    - [ ] Untrusted source with `X-Forwarded-For`: ignores header, returns connection IP
    - [ ] Multiple proxies: correctly parses right-to-left
    - [ ] SessionMiddleware correctly uses `get_client_ip()` for rate limiting

**Access Control:**
- [ ] Session invalidation on logout (Redis delete + cookie clear)
- [ ] `@requires_auth` decorator for page protection
- [ ] `has_permission()` helper for RBAC checks

**Audit Logging (auth events):**
- [ ] **Implementation:** `apps/web_console_ng/auth/audit.py` module
- [ ] **Event types logged:**
  - [ ] `login_success`: User successfully authenticated
  - [ ] `login_failure`: Authentication failed (wrong credentials, provider error)
  - [ ] `logout`: User logged out (explicit or session expiry)
  - [ ] `session_rotation`: Session ID rotated (fixation protection)
  - [ ] `session_validation_failure`: Session validation failed (tampered, expired, redis error)
  - [ ] `device_mismatch`: Device binding check failed (IP subnet or UA hash changed)
  - [ ] `csrf_failure`: CSRF token validation failed
  - [ ] `rate_limit_exceeded`: Rate limit exceeded (create or validate)
- [ ] **Required fields (per event):**
  - [ ] `timestamp`: ISO 8601 UTC timestamp
  - [ ] `event_type`: One of the event types above
  - [ ] `user_id`: User identifier (or "anonymous" for pre-auth events)
  - [ ] `session_id`: Session ID (first 8 chars, truncated for privacy; or null)
  - [ ] `client_ip`: Client IP address (from `get_client_ip()`)
  - [ ] `user_agent`: User-Agent header (truncated to 256 chars)
  - [ ] `auth_type`: Auth type from config (dev, basic, mtls, oauth2)
  - [ ] `outcome`: "success" or "failure"
  - [ ] `failure_reason`: Reason for failure (only on failure events)
  - [ ] `request_id`: Unique request identifier for correlation
- [ ] **Sink configuration (dual-write: logs + database per P5_PLANNING):**
  - [ ] **Primary sink:** Structured JSON logs via `libs/common/logging` (real-time, stdout/file)
  - [ ] **Secondary sink:** Postgres database (persistent, queryable for T6.6 Audit Log Viewer)
  - [ ] Log format: JSON with fields above, compatible with log aggregation (ELK, Datadog)
  - [ ] Log level: `INFO` for success events, `WARNING` for failure events
  - [ ] Config: `AUDIT_LOG_SINK=log|db|both` env var (default: `both` in prod, `log` in dev)
- [ ] **Database schema (Alembic migration):**
  - [ ] Table: `auth_audit_log`
  - [ ] Columns:
    - [ ] `id`: BIGSERIAL PRIMARY KEY
    - [ ] `timestamp`: TIMESTAMPTZ NOT NULL (indexed)
    - [ ] `event_type`: VARCHAR(50) NOT NULL (indexed)
    - [ ] `user_id`: VARCHAR(100) NOT NULL (indexed)
    - [ ] `session_id`: VARCHAR(8) (truncated, indexed)
    - [ ] `client_ip`: INET NOT NULL
    - [ ] `user_agent`: VARCHAR(256)
    - [ ] `auth_type`: VARCHAR(20) NOT NULL
    - [ ] `outcome`: VARCHAR(10) NOT NULL (indexed)
    - [ ] `failure_reason`: TEXT
    - [ ] `request_id`: UUID NOT NULL
    - [ ] `extra_data`: JSONB (extensibility)
  - [ ] Indexes: `(timestamp)`, `(user_id, timestamp)`, `(event_type, outcome)`, `(session_id)`
  - [ ] Retention: 90 days (configurable via `AUDIT_LOG_RETENTION_DAYS`)
  - [ ] Migration file: `db/migrations/versions/xxx_add_auth_audit_log.py`
- [ ] **Async database write (non-blocking):**
  - [ ] Use `asyncio.create_task()` to write to DB without blocking request
  - [ ] On DB write failure: log error, do NOT fail the request (audit is best-effort for DB)
  - [ ] Batch writes: Queue events, flush every 100ms or 10 events (whichever first)
  - [ ] Connection pool: Use existing `libs/common/db` async pool
- [ ] **Integration points:**
  - [ ] Session store: `create_session()`, `validate_session()`, `rotate_session()`, `invalidate_session()`
  - [ ] Auth routes: `/dev/login`, `/auth/login`, `/auth/logout`
  - [ ] Middleware: `SessionMiddleware`, `AuthMiddleware` (on validation failures)
  - [ ] CSRF validation: `verify_csrf_token()` dependency
- [ ] **Audit logging implementation:**
```python
# apps/web_console_ng/auth/audit.py
import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Any
from collections import deque
import uuid

from libs.common.db import get_async_pool  # Existing async DB pool

logger = logging.getLogger("audit.auth")

class AuthAuditLogger:
    """Dual-sink audit logging: JSON logs + Postgres database."""

    _instance: "AuthAuditLogger | None" = None
    _queue: deque  # Event buffer for batch DB writes
    _flush_task: asyncio.Task | None = None

    def __init__(self, db_enabled: bool = True):
        self._queue = deque(maxlen=1000)  # Bounded buffer
        self._db_enabled = db_enabled
        self._flush_interval = 0.1  # 100ms
        self._batch_size = 10

    @classmethod
    def get(cls, db_enabled: bool = True) -> "AuthAuditLogger":
        if cls._instance is None:
            cls._instance = cls(db_enabled)
        return cls._instance

    async def start(self) -> None:
        """Start background flush task (call on app startup)."""
        if self._db_enabled and self._flush_task is None:
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop flush task and flush remaining events."""
        if self._flush_task:
            self._flush_task.cancel()
            await self._flush_to_db()  # Final flush

    def log_event(
        self,
        event_type: str,
        user_id: str | None,
        session_id: str | None,
        client_ip: str,
        user_agent: str,
        auth_type: str,
        outcome: str,
        failure_reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Log auth event to JSON logs and queue for DB."""
        now = datetime.now(timezone.utc)
        req_id = request_id or str(uuid.uuid4())

        event = {
            "timestamp": now.isoformat(),
            "event_type": event_type,
            "user_id": user_id or "anonymous",
            "session_id": session_id[:8] if session_id else None,
            "client_ip": client_ip,
            "user_agent": user_agent[:256] if user_agent else None,
            "auth_type": auth_type,
            "outcome": outcome,
            "failure_reason": failure_reason,
            "request_id": req_id,
        }

        # Primary sink: JSON log (immediate, real-time)
        level = logging.INFO if outcome == "success" else logging.WARNING
        logger.log(level, json.dumps(event))

        # Secondary sink: Queue for DB batch write
        if self._db_enabled:
            self._queue.append((now, event_type, user_id or "anonymous",
                session_id[:8] if session_id else None, client_ip,
                user_agent[:256] if user_agent else None, auth_type,
                outcome, failure_reason, uuid.UUID(req_id)))

    async def _flush_loop(self) -> None:
        """Background loop: flush to DB every 100ms or 10 events."""
        while True:
            await asyncio.sleep(self._flush_interval)
            if len(self._queue) >= self._batch_size or self._queue:
                await self._flush_to_db()

    async def _flush_to_db(self) -> None:
        """Batch insert queued events to Postgres."""
        if not self._queue:
            return
        batch = []
        while self._queue and len(batch) < self._batch_size:
            batch.append(self._queue.popleft())
        try:
            pool = await get_async_pool()
            async with pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO auth_audit_log
                       (timestamp, event_type, user_id, session_id, client_ip,
                        user_agent, auth_type, outcome, failure_reason, request_id)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    batch
                )
        except Exception as e:
            # Best-effort: log error, do NOT fail request
            logger.error(f"Audit DB write failed: {e}")
            # Re-queue failed events (bounded, will drop oldest if full)
            for event in reversed(batch):
                self._queue.appendleft(event)
```
- [ ] **Acceptance tests (`test_audit_logging.py`):**
  - [ ] `login_success` event logged with all required fields on successful login
  - [ ] `login_failure` event logged with `failure_reason` on failed login
  - [ ] `logout` event logged on explicit logout
  - [ ] `session_rotation` event logged on session ID rotation
  - [ ] `session_validation_failure` logged on tampered/expired session
  - [ ] `device_mismatch` logged when IP subnet or UA hash differs
  - [ ] `csrf_failure` logged when CSRF validation fails
  - [ ] `rate_limit_exceeded` logged when rate limit hit
  - [ ] All events include `timestamp`, `request_id`, `client_ip`
  - [ ] `session_id` is truncated to 8 characters in logs
  - [ ] `user_agent` is truncated to 256 characters in logs
  - [ ] Log level is INFO for success, WARNING for failure events
  - [ ] **Database persistence tests:**
    - [ ] Events written to `auth_audit_log` table within flush interval
    - [ ] Batch writes: 10 events inserted in single transaction
    - [ ] DB write failure: events re-queued, request not failed
    - [ ] Schema migration: `auth_audit_log` table created with correct columns and indexes
    - [ ] Query by user_id: events retrievable for specific user
    - [ ] Query by time range: events retrievable for date range (T6.6 Audit Log Viewer support)

**Fail-Closed Security (CRITICAL):**
- [ ] **Redis unavailable:** Session validation MUST return `None` (deny access), never allow unauthenticated access
- [ ] **Decryption failure:** Session validation MUST return `None`, invalidate corrupted session, log security event
- [ ] **Signature verification failure:** MUST return `None`, log tampering attempt with client IP
- [ ] **Any exception in validation:** MUST catch, log, return `None` - never raise through to allow access
- [ ] **Acceptance test:** Verify session validation returns `None` when Redis is unreachable

**Testing:**
- [ ] Unit tests for session lifecycle (create, validate, rotate, invalidate)
- [ ] Security tests: CSRF validation, cookie flags, signature tampering, key rotation
- [ ] Failure mode tests: Redis unavailable, encryption key mismatch

### T1.4 WebSocket Reconnection & State Recovery
**Connection Security (CRITICAL - verify NiceGUI socket.io integration):**
- [ ] **WebSocket origin validation hook:** Use `@app.on_connect` which receives `client: Client` parameter
- [ ] **Access origin header:** `client.environ.get('HTTP_ORIGIN')` or via underlying socket.io request headers
- [ ] **Prototype validation:** Create minimal test page to confirm how to access `Origin` in NiceGUI `on_connect` callback
- [ ] If `app.on_connect` doesn't expose headers, use socket.io-level middleware via `socketio.Server` access
- [ ] Reject connections from unauthorized origins with proper logging; disconnect client with `client.disconnect()`
- [ ] Add `ALLOWED_WS_ORIGINS` environment config (comma-separated, e.g., `https://app.example.com,https://localhost:8080`)

**WebSocket Session Validation (CRITICAL - authentication binding):**
- [ ] **Session cookie validation in WS handshake:** Validate session cookie during `app.on_connect` callback
- [ ] **Access cookies in WS connect:** Via `client.environ.get('HTTP_COOKIE')` or socket.io handshake data
- [ ] **Spike task (in C0.1):** Confirm cookie access method in NiceGUI socket.io connect event
- [ ] **Validation flow on WS connect:**
  1. Extract session cookie from handshake headers/environ
  2. Call `session_store.validate_session(cookie_value, client_ip)` (same as HTTP middleware)
  3. If valid: attach user context to client state `client.state.user = session_data["user"]`
  4. If invalid/expired: reject connection with `client.disconnect()` and log security event
- [ ] **User context binding:** All WS event handlers MUST check `client.state.user` before processing state-changing events
- [ ] **Re-validation on reconnect:** On reconnect, re-validate session (may have expired during disconnect)
- [ ] **Session expiry during connection:** If session expires while WS is active, force disconnect on next event
- [ ] **Acceptance tests:**
  - [ ] Valid session cookie in WS handshake: connection allowed, user context attached
  - [ ] Invalid session cookie in WS handshake: connection rejected (disconnect called)
  - [ ] Missing session cookie in WS handshake: connection rejected (disconnect called)
  - [ ] Expired session on reconnect: connection rejected
  - [ ] Session expires during active connection: next WS event triggers disconnect

**WebSocket Origin Matching Rules:**
- [ ] **Origin format:** `{scheme}://{host}:{port}` (e.g., `https://app.example.com:443`)
- [ ] **Normalization:**
  - [ ] Convert to lowercase
  - [ ] Remove default ports (`:443` for https, `:80` for http)
  - [ ] Strip trailing slashes
- [ ] **Missing Origin header behavior:**
  - [ ] If `Origin` header is missing and `REQUIRE_WS_ORIGIN=true` (default): REJECT connection
  - [ ] If `Origin` header is missing and `REQUIRE_WS_ORIGIN=false` (dev only): ALLOW (log warning)
- [ ] **Matching algorithm:**
  1. Normalize incoming Origin header
  2. Compare against normalized `ALLOWED_WS_ORIGINS` list
  3. Exact match required (no wildcards in Phase 1)
- [ ] **Acceptance tests:**
  - [ ] Valid origin in allowlist: connection allowed
  - [ ] Invalid origin not in allowlist: connection rejected (client.disconnect() called, security event logged)
  - [ ] Missing Origin header + REQUIRE_WS_ORIGIN=true: connection rejected (disconnect + log)
  - [ ] Missing Origin header + REQUIRE_WS_ORIGIN=false: connection allowed (dev mode)
  - [ ] Origin with default port normalized correctly (`:443` stripped)
  - [ ] **Note:** Socket.io rejects via disconnect, not HTTP 403 - client receives `connect_error` event

**Connection Monitoring (singleton pattern):**
- [ ] Create `ConnectionMonitorRegistry` singleton - register lifecycle hooks ONCE at app startup
- [ ] Maintain per-client state registry to track connection status and UI elements
- [ ] Register `app.on_connect` / `app.on_disconnect` hooks only once globally (avoid duplicate handlers)
- [ ] Use per-client `client.run_javascript()` for JS execution (not global `ui.run_javascript`)

**Client-Side Disconnect UX (CRITICAL: server can't push UI after WS loss):**
- [ ] **Pre-inject client-side JS** on page load to handle disconnect locally
- [ ] Client-side overlay via JS (not server-side ui.dialog - won't render after disconnect)
- [ ] **Verify NiceGUI socket object:** Prototype to discover actual socket variable name (may be `window.socket`, `nicegui.socket`, or other)
- [ ] **Verify socket.io event names:** Confirm `disconnect`, `connect`, `reconnect_attempt`, `reconnect_failed` events work with NiceGUI's socket.io version
- [ ] **Fallback detection:** If socket object not accessible, use `navigator.onLine` + periodic ping via `fetch('/health')` for connectivity check
- [ ] Disable NiceGUI default disconnect overlay via `ui.run(reconnect_timeout=...)`
- [ ] Client-side "Connection Lost" banner with "Reconnecting..." spinner
- [ ] Graceful degradation: disable submit buttons via client-side JS
- [ ] **Manual/automated test:** Simulate WS disconnect (browser DevTools → Network → Offline) and verify overlay appears

**Reconnection Strategy:**
- [ ] **Heartbeat/ping:** Client-side ping every 15s, detect half-open connections
- [ ] **Immediate first attempt:** First reconnect within 500ms if online, then backoff
- [ ] Exponential backoff with **jitter:** base * 2^attempt + random(0, 500ms) to avoid thundering herd
- [ ] Backoff schedule: 0.5s, 1s, 2s, 4s, max 8s (capped for faster recovery)
- [ ] Maximum 5 reconnection attempts before showing refresh prompt

**State Management:**
- [ ] State persistence for critical data (user preferences) in Redis
- [ ] **State rehydration sequence:** On reconnect, fetch critical state before re-enabling UI
- [ ] **Server-side session invalidation handling:** Redirect to login if session expired
- [ ] UI state re-render from server data on reconnect
- [ ] Per-client task cleanup on disconnect (cancel background asyncio tasks safely)

**Testing:**
- [ ] Unit tests for reconnection logic (backoff calculation, jitter bounds)
- [ ] Integration tests: browser-level reconnect with state rehydration
- [ ] Failure mode tests: network interruption, session expiry during disconnect
- [ ] **Security test: websocket origin validation** - verify unauthorized origins are rejected and logged

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **ADR-0031 created in C0:** Create `docs/ADRs/ADR-0031-nicegui-migration.md` as FIRST task in C0 (before any code), covering migration rationale, security architecture, and session design decisions. ADR approval gates remaining C0 tasks.
- [ ] **Redis available:** Docker compose includes Redis instance (dev: no AUTH required)
- [ ] **Redis AUTH/TLS (production only):**
  - [ ] Production: `REDIS_TLS=true`, `REDIS_URL=rediss://...`, `REDIS_PASSWORD` required
  - [ ] Development/CI: `REDIS_TLS=false` (default), no AUTH (local docker-compose Redis)
  - [ ] Config: `RedisConfig.from_env()` handles both modes, validates prod requirements
  - [ ] Acceptance test: Config raises `ValueError` if `REDIS_TLS=true` but `REDIS_PASSWORD` not set
- [ ] **Execution Gateway running:** Can receive API calls on configured port
- [ ] **Python 3.11+:** Required for NiceGUI compatibility
- [ ] **SESSION_ENCRYPTION_KEY:** 32-byte Fernet key in environment (base64-encoded)
- [ ] **SESSION_ENCRYPTION_KEY_PREV:** Previous key for rotation (optional, base64-encoded)
- [ ] **HMAC_SIGNING_KEYS:** Cookie signing keys with IDs, format: `01:key1_hex,02:key2_hex` (first is current)
- [ ] **HMAC_CURRENT_KEY_ID:** 2-char hex ID of current signing key (e.g., `01`)
- [ ] **INTERNAL_TOKEN_SECRET:** For API HMAC signing (existing, separate from session signing)
- [ ] **TRUSTED_PROXY_IPS:** Comma-separated list for X-Forwarded-For trust
- [ ] **AUDIT_LOG_DB_ENABLED:** Enable Postgres audit logging (default: `true` prod, `false` dev)
- [ ] **AUDIT_LOG_SINK:** Sink selection (`log`, `db`, `both`) - default: `both` prod, `log` dev
- [ ] **AUDIT_LOG_RETENTION_DAYS:** Retention period for audit records (default: 90)

---

## Approach

### High-Level Plan

1. **C0: Project Setup & Spike** (2-3 days)
   - Create ADR-0031 (gates all code)
   - Create directory structure
   - Add dependencies
   - Configure NiceGUI server
   - Create health endpoint
   - **Spike C0.1:** Validate NiceGUI request/WS access patterns (gates C1/C2/C3)
   - Verify hot reload works

2. **C1: Async HTTP Client** (2-3 days)
   - Port `api_client.py` methods to async
   - Implement retry logic
   - Add connection pooling
   - Create unit tests

3. **C2: Session Store** (4-5 days)
   - Implement async Redis-backed session store
   - Add encryption/signing with key rotation support
   - Create middleware decorator with CSRF protection
   - Implement timeout logic and rate limiting
   - Add RBAC helpers and session fixation protection
   - Security and failure mode testing

4. **C3: WebSocket Recovery** (3-4 days)
   - Implement connection monitor with NiceGUI lifecycle hooks
   - Add jittered exponential backoff
   - Add heartbeat/ping and offline detection
   - Create UI banners with state rehydration
   - Create client lifecycle manager with task cleanup
   - Browser-level integration tests

---

## Component Breakdown

### C0: Project Setup

**Files to Create (aligned with P5_PLANNING T1.1):**
```
apps/web_console_ng/
├── __init__.py
├── main.py                    # NiceGUI entry point
├── config.py                  # Environment configuration (port from web_console)
├── core/
│   ├── __init__.py
│   ├── health.py              # Health endpoint
│   ├── client.py              # AsyncTradingClient (C1 deliverable)
│   └── state_manager.py       # UserStateManager (C3 deliverable - replaces storage.py)
├── ui/
│   ├── __init__.py
│   ├── layout.py              # Main layout wrapper
│   └── theme.py               # Tailwind/CSS configuration
├── pages/
│   └── __init__.py            # Page modules placeholder
├── components/
│   └── __init__.py            # Reusable components placeholder
├── auth/
│   └── __init__.py            # Auth module (see C2)
├── services/                  # Symlink or import from web_console
│   └── __init__.py
└── Dockerfile
```

**main.py (complete lifecycle wiring):**
```python
from nicegui import ui, app
from starlette.middleware.trustedhost import TrustedHostMiddleware
from apps.web_console_ng import config
from apps.web_console_ng.core.health import setup_health_endpoint
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.core.connection_monitor import ConnectionMonitorRegistry
from apps.web_console_ng.auth.session_store import ServerSessionStore
from apps.web_console_ng.auth.middleware import SessionMiddleware, AuthMiddleware
from apps.web_console_ng.auth.audit import AuthAuditLogger

# Initialize singletons (not yet started)
trading_client = AsyncTradingClient.get()
connection_monitor = ConnectionMonitorRegistry.get()
audit_logger = AuthAuditLogger.get(db_enabled=config.AUDIT_LOG_DB_ENABLED)
session_store = ServerSessionStore(
    redis_url=config.REDIS_URL,
    encryption_keys=config.get_encryption_keys(),
    signing_keys=config.get_signing_keys(),
    current_signing_key_id=config.HMAC_CURRENT_KEY_ID,
)

async def startup():
    """Initialize application on startup - register hooks ONCE."""
    # Start async clients
    await trading_client.startup()
    await session_store.connect()
    await audit_logger.start()  # Start audit DB flush loop

    # Register connection monitor hooks (singleton - once only)
    connection_monitor.register_hooks_once()

    # Setup health endpoint
    setup_health_endpoint()

async def shutdown():
    """Cleanup on shutdown."""
    await audit_logger.stop()  # Flush remaining audit events
    await trading_client.shutdown()
    await session_store.disconnect()

# Register lifecycle hooks
app.on_startup(startup)
app.on_shutdown(shutdown)

# Register middleware (LIFO order - last added runs first)
app.add_middleware(AuthMiddleware)  # Runs last (after session)
app.add_middleware(SessionMiddleware, session_store=session_store)  # Runs second
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)  # Runs first

if __name__ == "__main__":
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title=config.PAGE_TITLE,
        reload=config.DEBUG,
        show=False,
    )
```

**ADR-0031 Creation (C0 FIRST DELIVERABLE - gates remaining C0 tasks):**
- [ ] Create `docs/ADRs/ADR-0031-nicegui-migration.md` BEFORE any code implementation
- [ ] ADR covers: Migration rationale, security architecture, session design decisions
- [ ] ADR includes "Implementation Notes" section (populated by Spike C0.1)
- [ ] ADR reviewed and approved before proceeding with C0 code deliverables
- [ ] **Gating:** No C0 code (main.py, config.py, etc.) until ADR-0031 is approved

**NiceGUI API Validation Spike (C0.1 - REQUIRED before C1/C2/C3):**
- [ ] **Spike C0.1:** Create minimal NiceGUI app to validate request/WS access patterns IMMEDIATELY after ADR approval
- [ ] **Request access test:** Verify `request: Request` parameter in `@ui.page` handler provides cookie access
- [ ] **WS origin test (GATED for C3):** Verify `app.on_connect` callback can access `Origin` header (try `client.environ`, socket.io request)
- [ ] **WS cookie access test (GATED for C3):** Verify `app.on_connect` callback can access session cookie from handshake headers
- [ ] **WS reconnection config test (GATED for C3):**
  - [ ] Determine how to access NiceGUI's socket.io client object in browser (check `window.socket`, `nicegui.socket`, etc.)
  - [ ] Verify reconnection options are configurable (delay, attempts, randomization)
  - [ ] Test: Confirm `ui.run(reconnect_timeout=...)` or socket.io client options work
  - [ ] **If not configurable:** Document socket.io default behavior, adjust T1.4 SLAs to match feasible defaults
- [ ] **Lifecycle hooks test (GATED for all components):**
  - [ ] Verify `app.on_startup(async_func)` and `app.on_shutdown(async_func)` work as expected
  - [ ] Test: Confirm startup hook is called before first request
  - [ ] Test: Confirm shutdown hook is called on app termination
  - [ ] **If API differs:** Update main.py sample code to use correct NiceGUI lifecycle API
- [ ] **Fallback design if request access fails:** Use ASGI middleware (`BaseHTTPMiddleware`) to intercept request BEFORE NiceGUI routes
- [ ] **Fallback design if WS origin access fails:** Use socket.io-level middleware via `socketio.AsyncServer` connect handler
- [ ] **Fallback design if WS cookie access fails:** Use socket.io handshake data or custom auth event immediately after connect
- [ ] **Fallback design if WS reconnection not configurable:** Use socket.io defaults (typically 1s initial, 2x backoff, max 5 attempts), update T1.4 spec to match
- [ ] **Spike deliverable:** Update ADR-0031 "Implementation Notes" section with discovered patterns, fallback decisions, deviations
- [ ] **Spike acceptance criteria (ALL must pass before C1/C2/C3 can start):**
  - [ ] **HTTP request access confirmed:** `request.cookies.get()` works in `@ui.page` handler OR ASGI middleware fallback validated
  - [ ] **Lifecycle hooks confirmed:** `app.on_startup()` and `app.on_shutdown()` work OR alternative API documented
  - [ ] **WS origin access confirmed:** `Origin` header accessible in `app.on_connect` OR socket.io middleware fallback validated
  - [ ] **WS cookie access confirmed:** Session cookie accessible in `app.on_connect` OR socket.io handshake fallback validated
  - [ ] **WS reconnection config documented:** Either configurable with specified API OR defaults documented and T1.4 SLAs adjusted
  - [ ] **Block ALL components if lifecycle hooks not confirmed**
  - [ ] **Block C2 if HTTP request access not confirmed**
  - [ ] **Block C3 if WS origin OR WS cookie OR WS reconnection feasibility not confirmed**

**Lifecycle Acceptance Tests:**
- [ ] `startup()` called before first request (trading client initialized)
- [ ] `shutdown()` called on app termination (clients closed)
- [ ] Connection monitor hooks registered exactly ONCE (no duplicates)
- [ ] Middleware registered in correct order (TrustedHost → Session → Auth)

**Acceptance Tests:**
- [ ] ADR-0031 created and approved (gating requirement)
- [ ] `python apps/web_console_ng/main.py` starts server
- [ ] `curl http://localhost:8080/health` returns `{"status": "ok"}`
- [ ] File changes trigger hot reload in debug mode

---

### C1: Async HTTP Client

**Files to Create:**
```
apps/web_console_ng/core/
├── client.py                  # AsyncTradingClient
└── retry.py                   # Retry decorator
tests/apps/web_console_ng/
├── __init__.py
├── conftest.py
└── test_async_client.py
```

**Key Implementation:**
```python
# apps/web_console_ng/core/client.py
import httpx
from typing import Any
from apps.web_console_ng import config
from apps.web_console_ng.core.retry import with_retry

class AsyncTradingClient:
    """Async HTTP client for trading API calls."""

    _instance: "AsyncTradingClient | None" = None

    def __init__(self) -> None:
        self._http_client: httpx.AsyncClient | None = None

    @classmethod
    def get(cls) -> "AsyncTradingClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def _client(self) -> httpx.AsyncClient:
        """Get HTTP client with lifecycle validation."""
        if self._http_client is None:
            raise RuntimeError("Client not initialized - call startup() first")
        return self._http_client

    async def startup(self) -> None:
        """Initialize client on app startup. MUST be called before any API calls."""
        self._http_client = httpx.AsyncClient(
            base_url=config.EXECUTION_GATEWAY_URL,
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={"Content-Type": "application/json"},
        )

    async def shutdown(self) -> None:
        """Close client on app shutdown."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    @with_retry(max_attempts=3, backoff_base=1.0, method="GET")
    async def fetch_positions(self, user_id: str) -> dict[str, Any]:
        """Fetch current positions (GET - idempotent, safe to retry on 5xx)."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.get("/api/v1/positions", headers=headers)
        resp.raise_for_status()
        return resp.json()

    @with_retry(max_attempts=3, backoff_base=1.0, method="POST")
    async def trigger_kill_switch(self, user_id: str) -> dict[str, Any]:
        """Trigger kill switch (POST - NON-idempotent, only retry on transport errors)."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.post("/api/v1/kill-switch", headers=headers)
        resp.raise_for_status()
        return resp.json()

# apps/web_console_ng/core/retry.py
# Only GET and HEAD are retried on 5xx (matches T1.2 acceptance criteria)
IDEMPOTENT_METHODS = {"GET", "HEAD"}

class RetryableError(Exception):
    """Wrapper to mark an error as retryable."""
    pass

def with_retry(max_attempts: int = 3, backoff_base: float = 1.0, method: str = "GET"):
    """Retry decorator - idempotency-aware retry policy.

    - Idempotent methods (GET, HEAD): Retry on transport errors AND 5xx
    - Non-idempotent methods (POST, PUT, DELETE): Retry ONLY on transport errors
    - Never retry on 4xx (client errors)
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except httpx.TransportError as e:
                    # Transport errors: always retry (connection lost, timeout)
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(backoff_base * (2 ** attempt))
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500 and method.upper() in IDEMPOTENT_METHODS:
                        # 5xx on idempotent method: retry
                        if attempt == max_attempts - 1:
                            raise
                        await asyncio.sleep(backoff_base * (2 ** attempt))
                    else:
                        # 4xx or 5xx on non-idempotent: fail immediately
                        raise
            raise RuntimeError("Retry exhausted")  # Should not reach
        return wrapper
    return decorator

    # ... other methods
```

**Acceptance Tests:**
- [ ] Client connects to execution gateway
- [ ] **Idempotent retry (GET):** Retry triggers on 5xx errors
- [ ] **Non-idempotent no retry (POST):** Retry does NOT trigger on 5xx errors
- [ ] **Transport error retry:** Both GET and POST retry on connection/timeout errors
- [ ] Retry does NOT trigger on 4xx errors (client errors) regardless of method
- [ ] Timeout triggers after 5 seconds
- [ ] Auth headers are correctly generated
- [ ] `RuntimeError` raised when calling methods before `startup()`
- [ ] Client properly closed after `shutdown()`

---

### C2: Session Store & Auth Stubs

**Files to Create:**
```
apps/web_console_ng/auth/
├── __init__.py
├── session_store.py           # ServerSessionStore
├── middleware.py              # SessionMiddleware + AuthMiddleware
├── routes.py                  # Auth routes (/dev/login, /auth/login, /auth/logout)
├── cookie_config.py           # Cookie settings (env-based __Host- prefix)
├── client_ip.py               # get_client_ip() with proxy trust handling
├── permissions.py             # RBAC helpers
├── audit.py                   # AuthAuditLogger (dual-sink: logs + Postgres)
├── providers/                 # Auth provider stubs (Phase 2 ready)
│   ├── __init__.py
│   ├── base.py                # AuthProvider ABC with authenticate() interface
│   ├── dev.py                 # DevAuthProvider stub (AUTH_TYPE=dev)
│   ├── basic.py               # BasicAuthProvider stub (AUTH_TYPE=basic)
│   ├── mtls.py                # MTLSAuthProvider stub (AUTH_TYPE=mtls)
│   └── oauth2.py              # OAuth2AuthProvider stub (AUTH_TYPE=oauth2)
└── factory.py                 # get_auth_provider(auth_type) factory
db/migrations/versions/
└── xxx_add_auth_audit_log.py  # Alembic migration for auth_audit_log table
tests/apps/web_console_ng/
├── test_session_store.py
├── test_middleware.py
├── test_auth_factory.py       # Verify factory returns correct provider stubs
└── test_audit_logging.py      # Audit logging (JSON + DB) tests
```

**Auth Provider Interface (stub for Phase 2):**
```python
# apps/web_console_ng/auth/providers/base.py
from abc import ABC, abstractmethod
from typing import Any

class AuthProvider(ABC):
    """Base class for authentication providers.

    Phase 1: Stub implementation that returns NotImplementedError.
    Phase 2: Concrete implementations for each auth type.
    """

    @abstractmethod
    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        """Authenticate request, return user dict or None."""
        ...

    @abstractmethod
    async def logout(self, session_id: str) -> None:
        """Perform provider-specific logout cleanup."""
        ...

# apps/web_console_ng/auth/providers/dev.py
class DevAuthProvider(AuthProvider):
    """Development auth - auto-login with configured dev user.

    FUNCTIONAL in Phase 1 to enable session store testing.
    Provides /dev/login endpoint for test session creation.
    """

    async def authenticate(self, request: Any) -> dict[str, Any] | None:
        """In dev mode, auto-provision session for configured dev user."""
        # Return dev user from config (no password check in dev mode)
        return {
            "user_id": config.DEV_USER_ID,
            "role": config.DEV_ROLE,
            "strategies": config.DEV_STRATEGIES,
        }

    async def logout(self, session_id: str) -> None:
        pass  # No-op for dev

# apps/web_console_ng/auth/routes.py

# ============= Phase 1 Auth Route Stubs =============

@app.post("/dev/login")
async def dev_login(response: Response):
    """DEV ONLY: Create test session for session store validation.

    CRITICAL: This endpoint MUST be disabled in production (AUTH_TYPE != 'dev').
    """
    if config.AUTH_TYPE != "dev":
        raise HTTPException(status_code=404, detail="Not found")

    user_data = {"user_id": config.DEV_USER_ID, "role": config.DEV_ROLE}
    cookie_value, csrf_token = await session_store.create_session(user_data, {}, "127.0.0.1")
    cookie_config = CookieConfig.from_env()

    response.set_cookie(
        cookie_config.get_cookie_name(),
        cookie_value,
        **cookie_config.get_cookie_flags()
    )
    response.set_cookie("ng_csrf", csrf_token, httponly=False, **cookie_config.get_csrf_flags())
    return {"status": "ok", "user_id": config.DEV_USER_ID}

@app.post("/auth/login")
async def auth_login(request: Request, response: Response):
    """Phase 1 stub: Returns 503 for non-dev auth types (provider not implemented).

    Phase 2: Delegates to BasicAuthProvider, OAuth2AuthProvider based on AUTH_TYPE.
    """
    if config.AUTH_TYPE == "dev":
        # Redirect to /dev/login for dev mode
        return RedirectResponse("/dev/login", status_code=307)

    # Non-dev providers are stubs in Phase 1
    raise HTTPException(
        status_code=503,
        detail={"error": "auth_provider_not_configured", "auth_type": config.AUTH_TYPE}
    )

@app.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Logout: Always clears cookies regardless of provider state.

    Fail-safe: Even if session store is unavailable, cookies are cleared.
    """
    cookie_config = CookieConfig.from_env()

    # Extract session ID from cookie (if present) and invalidate in store
    cookie_value = request.cookies.get(cookie_config.get_cookie_name())
    if cookie_value:
        session_id, _ = session_store._parse_cookie(cookie_value)
        if session_id:
            try:
                await session_store.invalidate_session(session_id)
            except Exception:
                pass  # Best-effort invalidation; cookie clear is primary

    # Always clear cookies (fail-safe)
    response.delete_cookie(cookie_config.get_cookie_name(), path="/")
    response.delete_cookie("ng_csrf", path="/")
    return {"status": "logged_out"}

# apps/web_console_ng/auth/factory.py
from apps.web_console_ng.auth.providers.base import AuthProvider
from apps.web_console_ng.auth.providers import dev, basic, mtls, oauth2

_PROVIDERS: dict[str, type[AuthProvider]] = {
    "dev": dev.DevAuthProvider,
    "basic": basic.BasicAuthProvider,
    "mtls": mtls.MTLSAuthProvider,
    "oauth2": oauth2.OAuth2AuthProvider,
}

def get_auth_provider(auth_type: str) -> AuthProvider:
    """Factory to get auth provider by type. Raises KeyError if unknown."""
    if auth_type not in _PROVIDERS:
        raise KeyError(f"Unknown auth type: {auth_type}")
    return _PROVIDERS[auth_type]()
```

**Auth Provider Wiring (middleware reads from SessionMiddleware - NO per-request auth):**
```python
# apps/web_console_ng/auth/middleware.py
from apps.web_console_ng import config
from starlette.responses import JSONResponse

class AuthMiddleware:
    """Authorization middleware - reads user from SessionMiddleware, enforces access.

    CRITICAL ARCHITECTURE:
    - SessionMiddleware runs FIRST: validates cookie, sets request.state.user
    - AuthMiddleware runs SECOND: reads request.state.user, enforces authorization
    - provider.authenticate() is ONLY called in LOGIN routes, NOT per-request

    FAIL-CLOSED BEHAVIOR:
    - If request.state.user is None (no session or invalid session): DENY ACCESS (401)
    - Exception: paths in AUTH_EXEMPT_PATHS (login, callback, health) skip auth check
    """

    AUTH_EXEMPT_PATHS = {"/auth/login", "/auth/callback", "/dev/login", "/health"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope, receive)
            path = request.url.path

            # Skip auth for exempt paths (login, health, etc.)
            if path in self.AUTH_EXEMPT_PATHS:
                await self.app(scope, receive, send)
                return

            # Read user from SessionMiddleware (already validated cookie)
            user = getattr(request.state, "user", None)

            if user is None:
                # No valid session - DENY ACCESS (fail-closed)
                response = JSONResponse(
                    {"error": "authentication_required"},
                    status_code=401
                )
                await response(scope, receive, send)
                return

            # User authenticated via session - proceed
            scope["state"]["user"] = user
        await self.app(scope, receive, send)

# NOTE: provider.authenticate() is called ONLY in login routes:
# - /dev/login: calls DevAuthProvider.authenticate() to get user, then creates session
# - /auth/login: calls BasicAuthProvider.authenticate() (Phase 2)
# - /auth/callback: calls OAuth2AuthProvider.authenticate() (Phase 2)
```

**Auth Provider Acceptance Tests:**
- [ ] `get_auth_provider("dev")` returns `DevAuthProvider` instance
- [ ] `get_auth_provider("basic")` returns `BasicAuthProvider` instance
- [ ] `get_auth_provider("mtls")` returns `MTLSAuthProvider` instance
- [ ] `get_auth_provider("oauth2")` returns `OAuth2AuthProvider` instance
- [ ] `get_auth_provider("unknown")` raises `KeyError`
- [ ] **DevAuthProvider (functional):** `authenticate()` returns dev user dict (not stub)
- [ ] **Non-dev provider stubs:** `BasicAuthProvider`, `MTLSAuthProvider`, `OAuth2AuthProvider` raise `NotImplementedError`
- [ ] **Config integration:** `AUTH_TYPE` env var selects provider (default: `dev`)
- [ ] **Dev login endpoint:** `/dev/login` creates session and sets cookies (only when AUTH_TYPE=dev)
- [ ] **Dev login disabled in prod:** `/dev/login` returns 404 when AUTH_TYPE != dev

**AuthMiddleware Acceptance Tests (reads from SessionMiddleware):**
- [ ] **Session required:** Request without valid session cookie returns 401 (fail-closed)
- [ ] **Session validated by SessionMiddleware:** `request.state.user` is set by SessionMiddleware before AuthMiddleware runs
- [ ] **Exempt paths:** `/dev/login`, `/auth/login`, `/auth/logout`, `/auth/callback`, `/health` skip auth check
- [ ] **Middleware does NOT call provider.authenticate:** Provider only used in login routes

**Auth Routes Acceptance Tests (Phase 1):**
- [ ] **Dev login (AUTH_TYPE=dev):**
  - [ ] POST `/dev/login` creates session and sets cookies
  - [ ] Response includes session cookie and CSRF cookie
  - [ ] Subsequent requests with cookie are authenticated
- [ ] **Dev login disabled (AUTH_TYPE != dev):**
  - [ ] POST `/dev/login` returns 404
- [ ] **Auth login stub (non-dev):**
  - [ ] POST `/auth/login` with AUTH_TYPE=basic returns 503 (provider not implemented)
  - [ ] POST `/auth/login` with AUTH_TYPE=oauth2 returns 503 (provider not implemented)
  - [ ] POST `/auth/login` with AUTH_TYPE=dev redirects to /dev/login
- [ ] **Logout (all modes):**
  - [ ] POST `/auth/logout` clears session and CSRF cookies
  - [ ] POST `/auth/logout` invalidates session in Redis
  - [ ] POST `/auth/logout` succeeds even if Redis is unavailable (cookie clear is primary)
  - [ ] Subsequent requests with cleared cookie return 401

**Session Store Implementation (Async Redis):**
```python
# apps/web_console_ng/auth/session_store.py
import redis.asyncio as redis  # Use async Redis client
import secrets
import json
import hmac
import hashlib
from datetime import datetime, timezone
from cryptography.fernet import Fernet, MultiFernet
from typing import Any

class ServerSessionStore:
    """Server-side session store with async Redis backend."""

    def __init__(
        self,
        redis_url: str,
        encryption_keys: list[bytes],  # Support key rotation [current, previous]
        signing_keys: dict[str, bytes],  # Key ID -> key mapping for HMAC rotation
        current_signing_key_id: str,  # 2-char hex ID of current signing key
    ):
        # Async Redis connection pool
        self.redis = redis.from_url(redis_url, decode_responses=False)
        # MultiFernet for key rotation support - first key encrypts, any decrypts
        self.fernet = MultiFernet([Fernet(k) for k in encryption_keys])
        self.signing_keys = signing_keys  # {"01": key1, "02": key2, ...}
        self.current_signing_key_id = current_signing_key_id
        self.session_prefix = "ng_session:"
        self.rate_limit_prefix = "ng_rate:"
        self.idle_timeout = config.SESSION_IDLE_TIMEOUT_MINUTES * 60
        self.absolute_timeout = config.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600

    async def create_session(
        self, user_data: dict[str, Any], device_info: dict, client_ip: str
    ) -> tuple[str, str]:
        """Create session, return (cookie_value, csrf_token).

        Cookie value format: {session_id}.{key_id}:{signature}
        FAIL-CLOSED: Raises exception on Redis error (deny session creation).
        """
        try:
            # Rate limiting check
            if not await self._check_rate_limit(client_ip, "create", 10):
                raise RateLimitExceeded("Session creation rate limit exceeded")

            session_id = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)

            session_data = {
                "user": user_data,
                "csrf_token": csrf_token,
                "created_at": now.isoformat(),
                "issued_at": now.isoformat(),  # For replay protection
                "last_activity": now.isoformat(),
                "device": device_info,
            }

            encrypted = self.fernet.encrypt(json.dumps(session_data).encode())
            await self.redis.setex(
                f"{self.session_prefix}{session_id}",
                self.absolute_timeout,
                encrypted
            )

            # Build canonical cookie value: {session_id}.{key_id}:{signature}
            key_sig = self._sign_with_key_id(session_id)  # Returns "key_id:signature"
            cookie_value = f"{session_id}.{key_sig}"  # Full format
            return cookie_value, csrf_token
        except redis.RedisError as e:
            # FAIL-CLOSED: Redis outage = deny session creation
            logger.error(f"Redis error during session creation: {e}")
            raise SessionCreationError("Session creation failed - storage unavailable") from e

    async def validate_session(
        self, cookie_value: str, client_ip: str
    ) -> dict[str, Any] | None:
        """Validate session and return data if valid.

        Cookie value format: {session_id}.{key_id}:{signature}
        FAIL-CLOSED: Any error (Redis, decryption, parsing) returns None.
        """
        try:
            # Rate limiting check
            if not await self._check_rate_limit(client_ip, "validate", 100):
                return None

            # Parse canonical cookie format: {session_id}.{key_id}:{signature}
            session_id, key_sig = self._parse_cookie(cookie_value)
            if session_id is None:
                logger.warning(f"Malformed cookie value, ip={client_ip}")
                return None

            # Verify signature with key rotation support
            if not self._verify_signature(session_id, key_sig):
                logger.warning(f"Signature verification failed for session, ip={client_ip}")
                return None

            # Get session from Redis (async) - wrapped in try for Redis errors
            data = await self.redis.get(f"{self.session_prefix}{session_id}")
            if not data:
                return None

            # Decrypt and decode (fernet.decrypt returns bytes, need .decode())
            decrypted = self.fernet.decrypt(data).decode("utf-8")
            session = json.loads(decrypted)
            now = datetime.now(timezone.utc)

            # Check ABSOLUTE timeout first (hard limit - cannot be extended)
            created_at = datetime.fromisoformat(session["created_at"])
            age_seconds = (now - created_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age_seconds > self.absolute_timeout:
                await self.invalidate_session(session_id)
                return None

            # Check idle timeout
            last_activity = datetime.fromisoformat(session["last_activity"])
            if (now - last_activity.replace(tzinfo=timezone.utc)).total_seconds() > self.idle_timeout:
                await self.invalidate_session(session_id)
                return None

            # Calculate REMAINING TTL (do NOT reset to full absolute_timeout!)
            remaining_ttl = int(self.absolute_timeout - age_seconds)
            if remaining_ttl <= 0:
                await self.invalidate_session(session_id)
                return None

            # Update last activity with remaining TTL only
            session["last_activity"] = now.isoformat()
            encrypted = self.fernet.encrypt(json.dumps(session).encode())
            await self.redis.setex(
                f"{self.session_prefix}{session_id}",
                remaining_ttl,  # Use remaining time, NOT full absolute_timeout
                encrypted
            )

            return session
        except redis.RedisError as e:
            # Redis connection/timeout error - fail closed
            logger.error(f"Redis error during session validation: {e}")
            return None
        except Exception as e:
            # Decryption, parsing, or other error - fail closed
            logger.warning(f"Session validation failed: {type(e).__name__}: {e}")
            return None

    async def rotate_session(self, old_session_id: str) -> tuple[str, str] | None:
        """Rotate session ID (for fixation protection). Returns new (session_id, signature)."""
        try:
            data = await self.redis.get(f"{self.session_prefix}{old_session_id}")
            if not data:
                return None

            # Decrypt and decode to get created_at for remaining TTL calculation
            decrypted = self.fernet.decrypt(data).decode("utf-8")
            session = json.loads(decrypted)
            created_at = datetime.fromisoformat(session["created_at"])
            now = datetime.now(timezone.utc)
            remaining_ttl = int(self.absolute_timeout - (now - created_at.replace(tzinfo=timezone.utc)).total_seconds())

            if remaining_ttl <= 0:
                await self.redis.delete(f"{self.session_prefix}{old_session_id}")
                return None

            # Create new session ID, preserve data with remaining TTL
            new_session_id = secrets.token_urlsafe(32)
            await self.redis.setex(
                f"{self.session_prefix}{new_session_id}",
                remaining_ttl,  # Preserve remaining lifetime, don't extend
                data
            )
            await self.redis.delete(f"{self.session_prefix}{old_session_id}")

            return new_session_id, self._sign_with_key_id(new_session_id)
        except Exception as e:
            logger.warning(f"Session rotation failed: {type(e).__name__}: {e}")
            return None

    async def invalidate_session(self, session_id: str) -> None:
        """Delete session (logout)."""
        await self.redis.delete(f"{self.session_prefix}{session_id}")

    async def _check_rate_limit(self, client_ip: str, action: str, limit: int) -> bool:
        """Check rate limit, return True if allowed."""
        key = f"{self.rate_limit_prefix}{action}:{client_ip}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 60)  # 1-minute window
        return count <= limit

    def _sign_with_key_id(self, data: str) -> str:
        """Create HMAC signature with key ID prefix for rotation support.

        Format: {key_id}:{signature} where key_id is 2-char hex identifier.
        """
        key = self.signing_keys[self.current_signing_key_id]
        sig = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
        return f"{self.current_signing_key_id}:{sig}"

    def _parse_cookie(self, cookie_value: str) -> tuple[str | None, str | None]:
        """Parse canonical cookie format: {session_id}.{key_id}:{signature}

        Returns (session_id, key_sig) or (None, None) if malformed.
        """
        if "." not in cookie_value:
            return None, None
        parts = cookie_value.rsplit(".", 1)
        if len(parts) != 2:
            return None, None
        session_id, key_sig = parts
        if ":" not in key_sig:
            return None, None
        return session_id, key_sig

    def _verify_signature(self, data: str, signature: str) -> bool:
        """Verify signature with key rotation support.

        Extracts key_id from signature, looks up key, verifies.
        Returns False if key_id not found or signature invalid.
        """
        if ":" not in signature:
            return False
        key_id, sig = signature.split(":", 1)
        if key_id not in self.signing_keys:
            return False  # Unknown key ID - reject
        key = self.signing_keys[key_id]
        expected = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)

class SessionCreationError(Exception):
    """Raised when session creation fails (Redis unavailable, etc.)."""
    pass

class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    pass
```

**Acceptance Tests:**
- [ ] Session created and stored in Redis with correct TTL
- [ ] Session creation returns canonical cookie value format `{session_id}.{key_id}:{signature}`
- [ ] **Session creation fails closed:** `SessionCreationError` raised when Redis unavailable
- [ ] Session retrieved with valid signature
- [ ] Session rejected with invalid/tampered signature
- [ ] Session rejected with malformed cookie (missing `.` or `:`)
- [ ] Session expires after idle timeout
- [ ] Session invalidated on logout (Redis key deleted)
- [ ] Session ID rotated on login (fixation protection)
- [ ] CSRF token generated and validated
- [ ] Rate limiting enforced (10 creates/min, 100 validates/min)
- [ ] Key rotation works (old key decrypts, new key encrypts)

---

### C3: WebSocket Recovery

**Files to Create:**
```
apps/web_console_ng/core/
├── connection_monitor.py      # ConnectionMonitorRegistry (singleton)
├── state_manager.py           # UserStateManager (per-client state persistence)
└── client_lifecycle.py        # ClientLifecycleManager (task cleanup)
tests/apps/web_console_ng/
└── test_connection_recovery.py
```

**UserStateManager (per-client state persistence for reconnection):**
```python
# apps/web_console_ng/core/state_manager.py
from typing import Any
import redis.asyncio as redis
import json

class UserStateManager:
    """Manages per-user state persistence for reconnection recovery.

    Stores critical user state (preferences, UI selections) in Redis
    so that it survives WebSocket disconnections and can be rehydrated
    on reconnect. NOT for session auth - that's in ServerSessionStore.

    NOTE: Uses decode_responses=True for JSON state data (unlike session store
    which uses bytes for Fernet encryption).
    """

    def __init__(self, redis_url: str):
        # Use decode_responses=True for JSON data (returns str, not bytes)
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.prefix = "ng_user_state:"
        self.ttl = 3600  # 1 hour state retention

    async def save_state(self, user_id: str, state_key: str, state_data: dict[str, Any]) -> None:
        """Save user state for reconnection recovery."""
        key = f"{self.prefix}{user_id}:{state_key}"
        await self.redis.setex(key, self.ttl, json.dumps(state_data))

    async def load_state(self, user_id: str, state_key: str) -> dict[str, Any] | None:
        """Load user state on reconnection.

        Returns None if key doesn't exist. json.loads works directly
        because Redis client is configured with decode_responses=True.
        """
        key = f"{self.prefix}{user_id}:{state_key}"
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def clear_state(self, user_id: str, state_key: str | None = None) -> None:
        """Clear user state (logout or explicit clear)."""
        if state_key:
            await self.redis.delete(f"{self.prefix}{user_id}:{state_key}")
        else:
            # Clear all state for user
            pattern = f"{self.prefix}{user_id}:*"
            async for key in self.redis.scan_iter(pattern):
                await self.redis.delete(key)
```

**UserStateManager Acceptance Tests:**
- [ ] State saved and retrieved correctly
- [ ] State expires after TTL
- [ ] State cleared on logout
- [ ] State survives WS disconnect and available on reconnect

**Connection Monitor (SINGLETON pattern - register hooks ONCE at startup):**
```python
# apps/web_console_ng/core/connection_monitor.py
from nicegui import ui, app, Client
from typing import ClassVar
import asyncio
import logging

logger = logging.getLogger(__name__)

class ConnectionMonitorRegistry:
    """SINGLETON: Manages connection state for all clients.

    CRITICAL: Register lifecycle hooks ONCE at app startup to avoid duplicate handlers.
    Maintain per-client state in a registry dict.
    """

    _instance: ClassVar["ConnectionMonitorRegistry | None"] = None
    _hooks_registered: ClassVar[bool] = False

    def __init__(self) -> None:
        self._client_states: dict[str, dict] = {}  # client_id -> state
        self._client_tasks: dict[str, list[asyncio.Task]] = {}  # client_id -> tasks

    @classmethod
    def get(cls) -> "ConnectionMonitorRegistry":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_hooks_once(self) -> None:
        """Register global lifecycle hooks ONCE at app startup."""
        if ConnectionMonitorRegistry._hooks_registered:
            return  # Already registered

        app.on_connect(self._on_connect)
        app.on_disconnect(self._on_disconnect)
        ConnectionMonitorRegistry._hooks_registered = True
        logger.info("Connection monitor hooks registered")

    async def setup_client(self, client: Client) -> None:
        """Setup per-client monitoring (called for each new page load)."""
        self._client_states[client.id] = {"connected": True, "reconnect_attempts": 0}

        # Inject client-side disconnect UI handler (runs in browser, not server)
        await self._inject_client_disconnect_handler(client)

    async def _inject_client_disconnect_handler(self, client: Client) -> None:
        """Inject client-side JS that handles disconnect UX locally.

        CRITICAL: Server cannot push UI updates after WS disconnects!
        All disconnect UX must be pre-injected and handled client-side.
        """
        await client.run_javascript("""
            (function() {
                // Create disconnect overlay (hidden initially)
                const overlay = document.createElement('div');
                overlay.id = 'ng-disconnect-overlay';
                overlay.style.cssText = 'display:none; position:fixed; top:0; left:0; ' +
                    'width:100%; height:100%; background:rgba(0,0,0,0.7); z-index:9999; ' +
                    'justify-content:center; align-items:center;';
                overlay.innerHTML = `
                    <div style="background:white; padding:24px; border-radius:8px; text-align:center;">
                        <h3 style="color:#dc2626; margin:0 0 12px;">Connection Lost</h3>
                        <p id="ng-reconnect-status">Reconnecting...</p>
                        <div id="ng-reconnect-spinner" class="spinner"></div>
                        <button id="ng-refresh-btn" style="display:none; margin-top:12px; padding:8px 16px;"
                            onclick="location.reload()">Refresh Page</button>
                    </div>
                `;
                document.body.appendChild(overlay);

                // Track reconnection state
                let reconnectAttempts = 0;
                const maxAttempts = 5;

                // Listen for socket.io disconnect (NiceGUI uses socket.io)
                if (window.socket) {
                    window.socket.on('disconnect', function() {
                        overlay.style.display = 'flex';
                        reconnectAttempts = 0;
                        updateStatus('Reconnecting...');
                    });

                    window.socket.on('connect', function() {
                        overlay.style.display = 'none';
                        reconnectAttempts = 0;
                    });

                    window.socket.on('reconnect_attempt', function(attempt) {
                        reconnectAttempts = attempt;
                        updateStatus(`Reconnecting... (attempt ${attempt}/${maxAttempts})`);
                    });

                    window.socket.on('reconnect_failed', function() {
                        document.getElementById('ng-reconnect-spinner').style.display = 'none';
                        document.getElementById('ng-refresh-btn').style.display = 'block';
                        updateStatus('Connection failed. Please refresh.');
                    });
                }

                // Offline detection
                window.addEventListener('offline', () => {
                    overlay.style.display = 'flex';
                    updateStatus('Network offline. Waiting for connection...');
                });
                window.addEventListener('online', () => {
                    updateStatus('Network restored. Reconnecting...');
                });

                function updateStatus(msg) {
                    document.getElementById('ng-reconnect-status').textContent = msg;
                }
            })();
        """)

    async def _on_connect(self, client: Client) -> None:
        """Handle successful connection/reconnection (server-side)."""
        if client.id in self._client_states:
            self._client_states[client.id]["connected"] = True
            self._client_states[client.id]["reconnect_attempts"] = 0
            logger.info(f"Client {client.id[:8]} connected")

    async def _on_disconnect(self, client: Client) -> None:
        """Handle disconnection (server-side cleanup only)."""
        if client.id in self._client_states:
            self._client_states[client.id]["connected"] = False

            # Cancel background tasks for this client
            await self._cleanup_client_tasks(client.id)
            logger.info(f"Client {client.id[:8]} disconnected")

    async def _cleanup_client_tasks(self, client_id: str) -> None:
        """Cancel all background tasks for a disconnected client."""
        tasks = self._client_tasks.pop(client_id, [])
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if tasks:
            logger.info(f"Cleaned up {len(tasks)} tasks for client {client_id[:8]}")

    def register_task(self, client_id: str, task: asyncio.Task) -> None:
        """Register a background task for cleanup on disconnect."""
        if client_id not in self._client_tasks:
            self._client_tasks[client_id] = []
        self._client_tasks[client_id].append(task)
```

**Acceptance Tests:**
- [ ] Disconnect triggers reconnection attempts
- [ ] Backoff delay increases exponentially
- [ ] UI shows connection status
- [ ] Max attempts triggers refresh prompt
- [ ] Successful reconnect restores UI

---

## Testing Strategy

### Unit Tests
- `test_async_client.py`: HTTP client with mocked responses, retry logic, timeout handling
- `test_session_store.py`: Session CRUD, timeouts, signing, rate limiting
- `test_middleware.py`: Auth decorator, RBAC checks, CSRF validation
- `test_connection_recovery.py`: Backoff calculation, jitter bounds, state transitions

### Security Tests
- `test_session_security.py`:
  - [ ] Cookie flags verification (HttpOnly, Secure, SameSite, __Host- prefix)
  - [ ] Signature tampering detection (modified session ID rejected)
  - [ ] CSRF token validation (missing/invalid token rejected)
  - [ ] Session fixation protection (ID rotates on login)
  - [ ] Key rotation (old key decrypts, new key encrypts)
  - [ ] Rate limiting (session creation/validation limits enforced)

### Failure Mode Tests
- `test_redis_failures.py`:
  - [ ] Redis unavailable at startup (graceful error)
  - [ ] Redis connection lost mid-request (retry/fallback)
  - [ ] Redis slow response (timeout handling)
- `test_encryption_failures.py`:
  - [ ] Invalid encryption key (clear error message)
  - [ ] Corrupted session data (session invalidated, not crashed)

### Integration Tests
- `test_health_endpoint.py`: Health check returns correct response
- `test_session_integration.py`: Full session lifecycle with Redis
- `test_reconnect_e2e.py`: Browser-level reconnection with state rehydration

### Manual Verification
- [ ] Start server, verify hot reload works
- [ ] Create session, verify in Redis with correct TTL
- [ ] Simulate network disconnect (browser DevTools), verify reconnection
- [ ] Test session timeout by waiting (idle + absolute)
- [ ] Test rate limiting by rapid session creation

---

## Dependencies

### External
- `nicegui>=2.0`: Web framework
- `httpx>=0.25`: Async HTTP client
- `redis[hiredis]>=5.0`: Redis client with C extension
- `cryptography>=41.0`: Fernet encryption

### Internal
- `libs/common/`: Logging configuration
- `apps/web_console/config.py`: Port existing config values

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| NiceGUI API changes | Low | Medium | Pin version, test before upgrade |
| Redis connection issues | Low | High | Async connection pooling, retry logic, graceful degradation |
| Session encryption key rotation | Medium | Medium | MultiFernet with multiple keys during rotation period |
| WebSocket stability in browser | Medium | Medium | Jittered backoff, offline detection, heartbeat monitoring |
| Redis transport security | Medium | High | Require TLS in production, Redis AUTH password |
| Rate limiting bypass | Low | Medium | Per-IP rate limiting with Redis counters |
| Thundering herd on reconnect | Medium | Medium | Jittered backoff prevents synchronized reconnects |
| Session fixation attacks | Low | High | Rotate session ID on login and privilege escalation |
| Deferred security items | Low | Medium | Items deferred to Phase 2 (lockout, JWKS, refresh tokens) have explicit dependencies and P5T2 prerequisite tracking |
| Unauthenticated WS connections | Medium | High | Session validation in WS handshake, reject invalid/missing session before allowing events |
| NiceGUI API spike failure | Medium | High | Spike C0.1 validates API access patterns before C2/C3; fallback designs defined (ASGI middleware, socket.io middleware) |

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests pass
- [ ] No regressions in existing tests
- [ ] Code reviewed and approved
- [ ] Documentation updated (README in web_console_ng)
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-30 (Rev 2)
**Status:** ✅ Complete

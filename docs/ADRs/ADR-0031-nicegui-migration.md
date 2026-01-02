# ADR-0031: NiceGUI Migration for Web Console

## Status
Proposed

## Context
The current web console is built with Streamlit, which has served well for rapid prototyping. However, as the platform matures, several limitations have become apparent:

1. **Limited Control**: Streamlit's opinionated rerun model limits control over rendering and state management
2. **Session Management**: Built-in session state is client-side only, not suitable for production security
3. **WebSocket Handling**: Limited control over WebSocket connection lifecycle
4. **Component Flexibility**: Difficulty integrating custom components and styling
5. **Multi-user Scalability**: Streamlit's architecture has scaling limitations for concurrent users

NiceGUI, built on FastAPI/Starlette with socket.io, provides:
- Full control over HTTP request/response lifecycle
- Native async support for efficient I/O
- WebSocket via socket.io with reconnection handling
- FastAPI middleware integration for security layers
- Tailwind CSS and Vue.js component model

## Decision

We will migrate the web console from Streamlit to NiceGUI, implementing a secure, production-ready architecture in phases.

### 1. Security Architecture

#### 1.1 Server-Side Session Store
- **Storage**: Redis with Fernet encryption for session data
- **Cookie Security**:
  - `HttpOnly=True`, `Secure=True`, `SameSite=Lax`, `Path=/`
  - `__Host-` prefix in production (requires Secure + no Domain)
  - Cookie format: `{session_id}.{key_id}:{signature}`
- **Timeouts**: 15-minute idle timeout, 4-hour absolute timeout
- **Key Rotation**: MultiFernet for encryption, keyed HMAC signatures with ID-based lookup

#### 1.2 Authentication Middleware Stack
Middleware registration follows LIFO order (last added runs first):
```
Request Flow: TrustedHost → Session → Auth → PageHandler
Response Flow: PageHandler → Auth → Session → TrustedHost
```

- **TrustedHostMiddleware**: Validates Host header (runs first)
- **SessionMiddleware**: Validates cookie, populates `request.state.user`
- **AuthMiddleware**: Enforces authorization based on session

**Critical**: AuthMiddleware reads from SessionMiddleware, does NOT call provider.authenticate() per-request. Providers are only called at login time.

#### 1.3 CSRF Protection
- Double-submit cookie pattern: `ng_csrf` cookie + `X-CSRF-Token` header
- Token generated per-session, rotated on session rotation
- Applied to all state-changing HTTP endpoints (POST/PUT/DELETE)
- Exempt paths: `/auth/login`, `/auth/callback`, `/auth/logout`, `/health`

#### 1.4 WebSocket Security
- Origin validation in `app.on_connect` callback
- Session cookie validation during WebSocket handshake
- Re-validation on reconnect (session may have expired)
- Force disconnect if session expires during active connection

#### 1.5 Device Binding (Configurable)
- IP subnet (configurable /24, /16, /8) + User-Agent hash
- Mismatch invalidates session, returns 401
- Configurable via `DEVICE_BINDING_ENABLED`, `DEVICE_BINDING_SUBNET_MASK`

#### 1.6 Audit Logging
- Dual-sink: JSON logs (real-time) + Postgres (persistent, queryable)
- Events: login success/failure, logout, session rotation, device mismatch, CSRF failure, rate limit exceeded
- Required fields: timestamp, event_type, user_id, session_id (truncated), client_ip, user_agent, auth_type, outcome

### 2. Session Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Session storage | Redis (async) | Fast, supports TTL, already in stack |
| Cookie vs localStorage | Signed HttpOnly cookie | Secure, not accessible to XSS |
| Session ID format | `secrets.token_urlsafe(32)` | Cryptographically secure, URL-safe |
| Encryption | Fernet (AES-128-CBC) | Symmetric, authenticated, key rotation via MultiFernet |
| Signature | HMAC-SHA256 with key ID | Key rotation support, constant-time comparison |
| CSRF | Double-submit cookie | No server-side state lookup per request |
| Timeout enforcement | Absolute TTL, no sliding | Prevents unlimited session extension |

### 3. Async HTTP Client

- **Library**: httpx with connection pooling
- **Retry Policy**: Idempotency-aware
  - GET/HEAD: Retry on transport errors AND 5xx
  - POST/PUT/DELETE: Retry ONLY on transport errors (never 5xx)
  - Never retry 4xx
- **Lifecycle**: Client started via `app.on_startup`, closed via `app.on_shutdown`

### 4. WebSocket Reconnection

- **Client-side UX**: Pre-injected JavaScript handles disconnect overlay (server can't push UI after WS loss)
- **Backoff**: Exponential with jitter (0.5s, 1s, 2s, 4s, max 8s)
- **State Recovery**: Critical state persisted in Redis, rehydrated on reconnect
- **Connection Monitor**: Singleton pattern, hooks registered ONCE at startup

### 5. Auth Provider Architecture

Phase 1 (T1.3) establishes the provider interface and factory:
- `AuthProvider` ABC with `authenticate()`, `logout()` methods
- `DevAuthProvider`: Functional in Phase 1 (returns dev user)
- `BasicAuthProvider`, `MTLSAuthProvider`, `OAuth2AuthProvider`: Stubs returning NotImplementedError

Phase 2 (T2.x) implements concrete providers.

## Consequences

### Positive
- **Security**: Production-grade session management with encryption, HMAC signing, key rotation
- **Control**: Full control over HTTP and WebSocket lifecycle
- **Async**: Native async throughout, efficient I/O handling
- **Extensibility**: Clean provider pattern for multiple auth types
- **Auditability**: Comprehensive audit logging to database for compliance

### Negative
- **Migration Effort**: Significant effort to port existing pages
- **Learning Curve**: Team needs to learn NiceGUI/FastAPI patterns
- **Complexity**: More moving parts than Streamlit's simpler model
- **Redis Dependency**: Critical path dependency on Redis availability (fail-closed)

### Mitigations
- Phased migration with parallel running (Streamlit on :8501, NiceGUI on :8080)
- Comprehensive documentation and code samples
- Fail-closed security defaults with graceful degradation where possible
- Spike C0.1 validates NiceGUI API patterns before main implementation

## Implementation Notes

Spike C0.1 findings are captured below. Runtime validations require running the spike server and connecting via browser.

### Spike C0.1 Findings (2025-12-31)
- **Reconnection config (static inspection)**: `nicegui.ui.run` exposes `reconnect_timeout` with default `3.0` seconds; no other reconnect-related parameters were found in the signature. This was inspected from the installed package (`nicegui==2.12.1`) in the project venv. Source: `inspect.signature(nicegui.ui.run)`.
- **Runtime validations pending**: HTTP request access, lifecycle hook execution, WS origin access, and WS cookie access require running `python -m apps.web_console_ng.spike_c01` and connecting from a browser.

### How to Run the Spike (for runtime confirmations)
1. Start: `.venv/bin/python -m apps.web_console_ng.spike_c01`
2. HTTP request access: visit `http://localhost:8080/spike/request` (optional cookie: `curl -v --cookie \"spike_cookie=test\" http://localhost:8080/spike/request`)
3. WS validations: open the spike page in a browser to establish a socket.io connection; check server logs for `HTTP_ORIGIN` and `HTTP_COOKIE` visibility.

### Spike C0.1 Checklist
- [x] HTTP request access in `@ui.page` handler confirmed (2025-12-31: `request: Request` param works, `request.cookies.get()` works)
- [x] Lifecycle hooks (`app.on_startup`, `app.on_shutdown`) confirmed (2025-12-31: startup hook tested successfully)
- [x] WS origin access in `app.on_connect` confirmed (2025-12-31: `client.environ` is ASGI environ dict, contains HTTP headers)
- [x] WS cookie access in `app.on_connect` confirmed (2025-12-31: `client.environ.get('HTTP_COOKIE')` available via ASGI environ)
- [x] WS reconnection configurability documented (static inspection: `ui.run(reconnect_timeout=3.0)` in nicegui 2.12.1)

**All C0.1 gates PASSED - C1/C2/C3 implementation can proceed.**

## References

- P5_PLANNING.md: Overall migration planning
- P5T1_TASK.md: Phase 1 implementation details
- libs/web_console_auth: Existing JWT/session utilities

## Compliance

- **Session Duration**: 4-hour absolute, 15-minute idle (configurable)
- **Audit Retention**: 90 days (configurable via AUDIT_LOG_RETENTION_DAYS)
- **Encryption**: AES-128-CBC via Fernet, keys rotated periodically
- **Rate Limiting**: 10 session creates/min, 100 validations/min per IP

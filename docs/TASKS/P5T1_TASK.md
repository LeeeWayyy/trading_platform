---
id: P5T1
title: "NiceGUI Migration - Foundation & Async Infrastructure"
phase: P5
task: T1
priority: P0
owner: "@development-team"
state: PLANNING
created: 2025-12-30
dependencies: []
estimated_effort: "10-14 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md]
features: [T1.1, T1.2, T1.3, T1.4]
---

# P5T1: NiceGUI Migration - Foundation & Async Infrastructure

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
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
- [ ] Add dependencies to `requirements.txt`: `nicegui>=2.0`, `httpx>=0.25`, `redis[hiredis]>=5.0`
- [ ] Create `main.py` entry point with NiceGUI server configuration
- [ ] Create `config.py` with environment-based configuration (port from `web_console/config.py`)
- [ ] Configure hot reload for development mode
- [ ] Create `Dockerfile` for NiceGUI (port 8080)
- [ ] Health check endpoint at `/health` returns JSON `{"status": "ok"}`
- [ ] All existing tests pass (no regressions)

### T1.2 Async Trading Client
- [ ] Create `AsyncTradingClient` singleton with connection pooling
- [ ] Implement all methods from `web_console/utils/api_client.py` as async
- [ ] Maintain auth header generation (`X-User-Id`, `X-User-Role`, `X-User-Strategies`, `X-User-Signature`)
- [ ] Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s)
- [ ] Timeout configuration: 5s total, 2s connect
- [ ] Error handling with structured logging
- [ ] Connection lifecycle management (startup/shutdown hooks)
- [ ] Unit tests with mocked HTTP responses

### T1.3 Server-Side Session Architecture
**Core Session Store:**
- [ ] Create `ServerSessionStore` with **async Redis** backend (`redis.asyncio`)
- [ ] Session data encrypted with Fernet (key from env `SESSION_ENCRYPTION_KEY`)
- [ ] Signed session cookie (HMAC-SHA256) with session ID only
- [ ] Cookie configuration: `HttpOnly=True`, `Secure=True`, `SameSite=Strict`
- [ ] Cookie prefix: Use `__Host-` prefix for session cookie (requires Secure + path=/)
- [ ] Idle timeout: 15 minutes (configurable via `SESSION_IDLE_TIMEOUT_MINUTES`)
- [ ] Absolute timeout: 4 hours (configurable via `SESSION_ABSOLUTE_TIMEOUT_HOURS`)

**Security Hardening:**
- [ ] **Session fixation protection:** Rotate session ID on login AND privilege escalation
- [ ] **CSRF protection:** Generate per-session CSRF token, validate on state-changing requests
- [ ] **Replay protection:** Include `issued_at` timestamp in session, reject stale tokens
- [ ] **Key rotation support:** Accept multiple Fernet/HMAC keys during rotation period
- [ ] **Redis transport security:** Require `REDIS_TLS=true` in production, Redis AUTH password
- [ ] **Rate limiting:** Max 10 session creations per IP per minute, 100 validations per minute
- [ ] **Device binding (configurable):** IP subnet (/24) + User-Agent validation
- [ ] **Trusted proxy allowlist:** Configure via `TRUSTED_PROXY_IPS` for X-Forwarded-For

**Access Control:**
- [ ] Session invalidation on logout (Redis delete + cookie clear)
- [ ] `@requires_auth` decorator for page protection
- [ ] `has_permission()` helper for RBAC checks
- [ ] Audit logging for all auth events (login, logout, session rotation, failures)

**Testing:**
- [ ] Unit tests for session lifecycle (create, validate, rotate, invalidate)
- [ ] Security tests: CSRF validation, cookie flags, signature tampering, key rotation
- [ ] Failure mode tests: Redis unavailable, encryption key mismatch

### T1.4 WebSocket Reconnection & State Recovery
**Connection Monitoring (hooks into NiceGUI's socket.io lifecycle):**
- [ ] Create `ConnectionMonitor` class using `app.on_connect` / `app.on_disconnect` hooks
- [ ] **Heartbeat/ping:** Client-side ping every 15s, detect half-open connections
- [ ] **Offline detection:** Use `navigator.onLine` API to detect network availability
- [ ] **Immediate first attempt:** First reconnect within 500ms if online, then backoff
- [ ] Exponential backoff with **jitter:** base * 2^attempt + random(0, 500ms) to avoid thundering herd
- [ ] Backoff schedule: 0.5s, 1s, 2s, 4s, max 8s (capped for faster recovery)
- [ ] Maximum 5 reconnection attempts before requiring page refresh

**UI Feedback:**
- [ ] Disable NiceGUI default disconnect overlay via `ui.run(reconnect_timeout=...)`
- [ ] Custom "Connection Lost" UI banner during disconnection
- [ ] "Reconnecting..." spinner with attempt count and network status
- [ ] Graceful degradation: read-only mode during disconnection (disable submit buttons)

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

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **Redis available:** Docker compose includes Redis instance with AUTH password
- [ ] **Redis TLS (production):** `REDIS_TLS=true` and `REDIS_URL=rediss://...` for encrypted transport
- [ ] **Execution Gateway running:** Can receive API calls on configured port
- [ ] **Python 3.11+:** Required for NiceGUI compatibility
- [ ] **SESSION_ENCRYPTION_KEY:** 32-byte Fernet key in environment
- [ ] **SESSION_ENCRYPTION_KEY_PREV:** Previous key for rotation (optional)
- [ ] **INTERNAL_TOKEN_SECRET:** For HMAC signing (existing)
- [ ] **TRUSTED_PROXY_IPS:** Comma-separated list for X-Forwarded-For trust

---

## Approach

### High-Level Plan

1. **C0: Project Setup** (1-2 days)
   - Create directory structure
   - Add dependencies
   - Configure NiceGUI server
   - Create health endpoint
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

**Files to Create:**
```
apps/web_console_ng/
├── __init__.py
├── main.py                    # NiceGUI entry point
├── config.py                  # Environment configuration
├── core/
│   ├── __init__.py
│   └── health.py              # Health endpoint
└── Dockerfile
```

**main.py:**
```python
from nicegui import ui, app
from apps.web_console_ng import config
from apps.web_console_ng.core.health import setup_health_endpoint

def startup():
    """Initialize application on startup."""
    setup_health_endpoint()

app.on_startup(startup)

if __name__ == "__main__":
    ui.run(
        host=config.HOST,
        port=config.PORT,
        title=config.PAGE_TITLE,
        reload=config.DEBUG,
        show=False,
    )
```

**Acceptance Tests:**
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
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def get(cls) -> "AsyncTradingClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def startup(self) -> None:
        """Initialize client on app startup."""
        self._client = httpx.AsyncClient(
            base_url=config.EXECUTION_GATEWAY_URL,
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={"Content-Type": "application/json"},
        )

    async def shutdown(self) -> None:
        """Close client on app shutdown."""
        if self._client:
            await self._client.aclose()

    @with_retry(max_attempts=3, backoff_base=1.0)
    async def fetch_positions(self, user_id: str) -> dict[str, Any]:
        """Fetch current positions."""
        headers = self._get_auth_headers(user_id)
        resp = await self._client.get("/api/v1/positions", headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ... other methods
```

**Acceptance Tests:**
- [ ] Client connects to execution gateway
- [ ] Retry logic triggers on 5xx errors
- [ ] Timeout triggers after 5 seconds
- [ ] Auth headers are correctly generated

---

### C2: Session Store

**Files to Create:**
```
apps/web_console_ng/auth/
├── __init__.py
├── session_store.py           # ServerSessionStore
├── middleware.py              # @requires_auth decorator
├── cookie_config.py           # Cookie settings
├── permissions.py             # RBAC helpers
└── audit.py                   # Audit logging
tests/apps/web_console_ng/
├── test_session_store.py
└── test_middleware.py
```

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
        encryption_keys: list[bytes],  # Support key rotation
        signing_key: bytes,
    ):
        # Async Redis connection pool
        self.redis = redis.from_url(redis_url, decode_responses=False)
        # MultiFernet for key rotation support
        self.fernet = MultiFernet([Fernet(k) for k in encryption_keys])
        self.signing_key = signing_key
        self.session_prefix = "ng_session:"
        self.rate_limit_prefix = "ng_rate:"
        self.idle_timeout = config.SESSION_IDLE_TIMEOUT_MINUTES * 60
        self.absolute_timeout = config.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600

    async def create_session(
        self, user_data: dict[str, Any], device_info: dict, client_ip: str
    ) -> tuple[str, str, str]:
        """Create session, return (session_id, signature, csrf_token)."""
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

        signature = self._sign(session_id)
        return session_id, signature, csrf_token

    async def validate_session(
        self, session_id: str, signature: str, client_ip: str
    ) -> dict[str, Any] | None:
        """Validate session and return data if valid."""
        # Rate limiting check
        if not await self._check_rate_limit(client_ip, "validate", 100):
            return None

        # Verify signature (constant-time comparison)
        if not hmac.compare_digest(signature, self._sign(session_id)):
            return None

        # Get session from Redis (async)
        data = await self.redis.get(f"{self.session_prefix}{session_id}")
        if not data:
            return None

        try:
            session = json.loads(self.fernet.decrypt(data))

            # Check idle timeout
            last_activity = datetime.fromisoformat(session["last_activity"])
            now = datetime.now(timezone.utc)
            if (now - last_activity.replace(tzinfo=timezone.utc)).total_seconds() > self.idle_timeout:
                await self.invalidate_session(session_id)
                return None

            # Update last activity
            session["last_activity"] = now.isoformat()
            encrypted = self.fernet.encrypt(json.dumps(session).encode())
            await self.redis.setex(
                f"{self.session_prefix}{session_id}",
                self.absolute_timeout,
                encrypted
            )

            return session
        except Exception:
            return None

    async def rotate_session(self, old_session_id: str) -> tuple[str, str] | None:
        """Rotate session ID (for fixation protection). Returns new (session_id, signature)."""
        data = await self.redis.get(f"{self.session_prefix}{old_session_id}")
        if not data:
            return None

        # Create new session ID, preserve data
        new_session_id = secrets.token_urlsafe(32)
        await self.redis.setex(
            f"{self.session_prefix}{new_session_id}",
            self.absolute_timeout,
            data
        )
        await self.redis.delete(f"{self.session_prefix}{old_session_id}")

        return new_session_id, self._sign(new_session_id)

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

    def _sign(self, data: str) -> str:
        """Create HMAC signature."""
        return hmac.new(self.signing_key, data.encode(), hashlib.sha256).hexdigest()
```

**Acceptance Tests:**
- [ ] Session created and stored in Redis with correct TTL
- [ ] Session retrieved with valid signature
- [ ] Session rejected with invalid/tampered signature
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
├── connection_monitor.py      # ConnectionMonitor
├── state_manager.py           # UserStateManager
└── client_lifecycle.py        # ClientLifecycleManager
tests/apps/web_console_ng/
└── test_connection_recovery.py
```

**Connection Monitor (hooks into NiceGUI's socket.io lifecycle):**
```python
# apps/web_console_ng/core/connection_monitor.py
from nicegui import ui, app, Client
import asyncio
import random

class ConnectionMonitor:
    """Monitor WebSocket connection using NiceGUI's native lifecycle hooks."""

    def __init__(self, client: Client):
        self.client = client
        self.connected = True
        self.reconnect_attempts = 0
        self.max_attempts = 5
        self.backoff_base = 0.5  # Start at 500ms for faster recovery
        self.max_backoff = 8.0   # Cap at 8s for better UX
        self._heartbeat_task: asyncio.Task | None = None
        self._disconnect_dialog = None

    def setup(self) -> None:
        """Register lifecycle hooks with NiceGUI."""
        # Hook into NiceGUI's native socket.io events
        app.on_connect(self._on_connect)
        app.on_disconnect(self._on_disconnect)

        # Start client-side heartbeat monitoring
        self._setup_client_heartbeat()

    def _setup_client_heartbeat(self) -> None:
        """Setup client-side heartbeat and offline detection."""
        ui.run_javascript("""
            // Offline detection using navigator.onLine
            window.addEventListener('offline', () => {
                window.networkOnline = false;
                console.log('Network offline detected');
            });
            window.addEventListener('online', () => {
                window.networkOnline = true;
                console.log('Network online detected');
            });
            window.networkOnline = navigator.onLine;

            // Heartbeat ping every 15 seconds
            setInterval(() => {
                if (window.networkOnline) {
                    fetch('/health', { method: 'HEAD' })
                        .catch(() => console.log('Heartbeat failed'));
                }
            }, 15000);
        """)

    async def _on_connect(self, client: Client) -> None:
        """Handle successful connection/reconnection."""
        if client.id == self.client.id:
            self.connected = True
            self.reconnect_attempts = 0

            # Close disconnect dialog if open
            if self._disconnect_dialog:
                self._disconnect_dialog.close()
                self._disconnect_dialog = None
                ui.notify("Reconnected successfully", type="positive")

            # Rehydrate state from server
            await self._rehydrate_state()

    async def _on_disconnect(self, client: Client) -> None:
        """Handle disconnection with jittered exponential backoff."""
        if client.id != self.client.id:
            return

        self.connected = False
        self.reconnect_attempts = 0

        # Cancel any running background tasks for this client
        await self._cancel_client_tasks()

        # Show connection lost UI (custom, not NiceGUI default)
        await self._show_disconnect_ui()

    async def _show_disconnect_ui(self) -> None:
        """Show custom disconnect dialog with reconnection status."""
        with ui.dialog(value=True) as self._disconnect_dialog:
            with ui.card().classes("bg-red-100 p-4"):
                ui.label("Connection Lost").classes("text-red-600 font-bold text-lg")
                self._status_label = ui.label("Checking network...")
                self._spinner = ui.spinner()

                # Check network status first
                is_online = await ui.run_javascript("window.networkOnline")

                if not is_online:
                    self._status_label.set_text("Network offline. Waiting for connection...")
                    # Will reconnect automatically when online event fires
                    return

                # Immediate first attempt (500ms)
                await asyncio.sleep(0.5)
                if await self._try_reconnect():
                    return

                # Exponential backoff with jitter
                while self.reconnect_attempts < self.max_attempts:
                    delay = self._calculate_backoff_with_jitter()
                    self._status_label.set_text(
                        f"Reconnecting in {delay:.1f}s... "
                        f"(attempt {self.reconnect_attempts + 1}/{self.max_attempts})"
                    )

                    await asyncio.sleep(delay)

                    if await self._try_reconnect():
                        return

                    self.reconnect_attempts += 1

                # Max attempts reached
                self._spinner.delete()
                self._status_label.set_text("Connection failed. Please refresh the page.")
                ui.button("Refresh", on_click=lambda: ui.run_javascript("location.reload()"))

    def _calculate_backoff_with_jitter(self) -> float:
        """Calculate backoff delay with jitter to avoid thundering herd."""
        base_delay = min(self.max_backoff, self.backoff_base * (2 ** self.reconnect_attempts))
        jitter = random.uniform(0, 0.5)  # Add 0-500ms jitter
        return base_delay + jitter

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect and validate session."""
        try:
            # Check if session is still valid on server
            session_valid = await self._validate_session()
            if not session_valid:
                # Session expired during disconnect, redirect to login
                ui.run_javascript("window.location.href = '/login?reason=session_expired'")
                return False

            # NiceGUI will handle the actual WebSocket reconnection
            return self.connected
        except Exception:
            return False

    async def _rehydrate_state(self) -> None:
        """Rehydrate critical UI state from server after reconnect."""
        # Fetch latest state before re-enabling UI
        # Implementation depends on what state needs to be restored
        pass

    async def _cancel_client_tasks(self) -> None:
        """Cancel background asyncio tasks for this client."""
        # Clean up any per-client background tasks safely
        pass

    async def _validate_session(self) -> bool:
        """Check if session is still valid on server."""
        # Call session validation endpoint
        return True
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
**Status:** PLANNING

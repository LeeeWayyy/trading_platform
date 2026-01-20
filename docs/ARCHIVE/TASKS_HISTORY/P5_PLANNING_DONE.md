# P5 Planning: Web Console Migration - Streamlit to NiceGUI

**Phase:** P5 (Web Console Modernization)
**Status:** Draft - v3
**Previous Phase:** P4 (Advanced Features & Research - In Progress)
**Last Updated:** 2025-12-30

---

## Executive Summary

**P5 focuses on migrating the web console from Streamlit to NiceGUI**, addressing fundamental architectural limitations in the current implementation. This is a **full UI layer rewrite**, not a simple framework swap.

**Why NiceGUI?**
- **Event-driven architecture** vs Streamlit's script-rerun model
- **Real-time updates** via WebSocket push (critical for trading dashboards)
- **AsyncIO native** - no UI freezing during API calls
- **FastAPI foundation** - seamless integration with existing backend patterns
- **Production-grade** - proper session management, no page flicker

**Scope:**
- 20 feature-complete pages
- 56 reusable components
- 23 authentication modules
- 18 service classes
- 30+ test files

**This migration requires an ADR** per repository standards (architectural change).

---

## Problem Statement

### Current Streamlit Limitations

| Issue | Impact | NiceGUI Solution |
|-------|--------|------------------|
| **Full script re-run on every interaction** | UI flickers, slow response | Event loop (AsyncIO) - instant feedback |
| **Synchronous requests** | UI freezes during API calls | Async httpx - non-blocking |
| **`st.session_state` coupling** | Auth/state deeply embedded | Python class attributes + `app.storage` |
| **`st.stop()` for access control** | Non-standard flow control | FastAPI middleware + guards |
| **Static `st.dataframe`** | No client-side interaction | `ui.aggrid` - sorting, filtering, high perf |
| **`streamlit_autorefresh` polling** | Inefficient, battery drain | `ui.timer` + WebSocket push |
| **Limited layout control** | Basic columns/sidebar | Flexbox, CSS, pixel-perfect control |

### Evidence of Deep Coupling

From codebase analysis:
- `apps/web_console/auth/streamlit_helpers.py` - `@requires_auth` decorator uses `st.session_state` and `st.stop()`
- `apps/web_console/app.py` (985 LOC) - Heavy `st.session_state`, `st.cache_data`, `st.rerun` patterns
- `apps/web_console/pages/backtest.py` - Uses `streamlit_autorefresh` for polling
- `apps/web_console/Dockerfile` - Streamlit-specific CMD and healthcheck
- `tests/integration/test_streamlit_csp.py` - CSP rules tied to Streamlit

---

## Architecture Decision

### Execution Model Shift

```
STREAMLIT (Current)                    NICEGUI (Target)
┌─────────────────────────┐            ┌─────────────────────────┐
│   User clicks button    │            │   User clicks button    │
│           ↓             │            │           ↓             │
│   Re-run entire script  │            │   Event handler fires   │
│           ↓             │            │           ↓             │
│   Rebuild all widgets   │            │   Update specific DOM   │
│           ↓             │            │           ↓             │
│   Re-fetch all data     │            │   Async API call        │
│           ↓             │            │           ↓             │
│   Re-render page        │            │   Patch UI element      │
└─────────────────────────┘            └─────────────────────────┘
       ~500-2000ms                            ~50-100ms
```

### Tech Stack Comparison

| Component | Streamlit | NiceGUI |
|-----------|-----------|---------|
| Framework | Streamlit 1.x | NiceGUI 2.x |
| HTTP Client | `requests` (sync) | `httpx` (async) |
| State | `st.session_state` | `app.storage` + class attributes |
| Tables | `st.dataframe` | `ui.aggrid` (AG Grid) |
| Charts | `st.plotly_chart` | `ui.plotly` |
| Layout | `st.columns`, `st.sidebar` | `ui.row`, `ui.left_drawer`, Flexbox |
| Auth | Custom Streamlit helpers | FastAPI middleware |
| Real-time | `streamlit_autorefresh` | `ui.timer` + WebSocket |

---

## Migration Strategy

### Guiding Principles

1. **Incremental migration** - Run both apps in parallel during transition
2. **Feature parity first** - Match existing functionality before enhancements
3. **Reuse service layer** - 18 service classes remain unchanged
4. **Preserve security** - All auth flows must be ported with equivalent security
5. **Test coverage** - Port all 30+ test files to new patterns

### Parallel Run Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     NGINX / Load Balancer                        │
│                                                                  │
│   /legacy/* → Streamlit (port 8501)    [Phase 1-3]              │
│   /*        → NiceGUI (port 8080)      [Phase 4+]               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase Breakdown

### Phase 1: Foundation & Async Infrastructure
**Effort:** 5-7 days | **Priority:** P0 (Foundation)

**Goal:** Establish async client, auth middleware, and project structure.

#### T1.1: Project Structure & Dependencies
**Effort:** 1-2 days | **PR:** `feat(P5): NiceGUI project initialization`

**Deliverables:**
- Create `apps/web_console_ng/` directory structure
- Update `requirements.txt` (add `nicegui`, `httpx`)
- Create `main.py` entry point
- Configure Docker for NiceGUI (port 8080)
- Set up development server with hot reload

**Files to Create:**
```
apps/web_console_ng/
├── main.py                    # NiceGUI entry point
├── config.py                  # Shared config (port from web_console)
├── core/
│   ├── __init__.py
│   ├── client.py              # Async HTTP client
│   └── storage.py             # State management
├── ui/
│   ├── __init__.py
│   ├── layout.py              # Main layout wrapper
│   └── theme.py               # Tailwind/CSS configuration
├── pages/
│   └── __init__.py
├── components/
│   └── __init__.py
├── auth/
│   └── __init__.py
├── services/                  # Symlink or import from web_console
└── Dockerfile
```

---

#### T1.2: Async Trading Client
**Effort:** 2-3 days | **PR:** `feat(P5): Async HTTP client`

**Problem:** Current `api_client.py` uses synchronous `requests`. In NiceGUI, blocking calls freeze the UI for all users.

**Deliverables:**
- Singleton async client with connection pooling
- Port all `fetch_api` methods to async
- Maintain auth header generation (`get_auth_headers`)
- Retry logic with exponential backoff
- Error handling and logging

**Implementation:**
```python
# apps/web_console_ng/core/client.py
import httpx
from typing import Any
from apps.web_console_ng import config

class AsyncTradingClient:
    """Async HTTP client for trading API calls."""

    _instance: "AsyncTradingClient | None" = None

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=config.EXECUTION_GATEWAY_URL,
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={"Content-Type": "application/json"},
        )

    @classmethod
    def get(cls) -> "AsyncTradingClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def fetch_positions(self, user_id: str) -> dict[str, Any]:
        """Fetch current positions."""
        headers = self._get_auth_headers(user_id)
        resp = await self.client.get("/api/v1/positions", headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def submit_order(self, order: dict[str, Any], user_id: str) -> dict[str, Any]:
        """Submit order with idempotent client_order_id."""
        headers = self._get_auth_headers(user_id)
        resp = await self.client.post("/api/v1/orders", json=order, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def fetch_kill_switch_status(self) -> dict[str, Any]:
        """Fetch kill switch status (uncached, real-time critical)."""
        resp = await self.client.get("/api/v1/kill_switch_status")
        resp.raise_for_status()
        return resp.json()

    # ... Port remaining methods from web_console/utils/api_client.py
```

**Files to Create:**
- `apps/web_console_ng/core/client.py`
- `tests/apps/web_console_ng/test_async_client.py`

---

#### T1.3: Auth Middleware & Server-Side Session Architecture
**Effort:** 5-6 days | **PR:** `feat(P5): NiceGUI auth middleware and session store`

**Problem:** Current auth uses `st.session_state` and `st.stop()`. NiceGUI requires FastAPI-style middleware with **server-side session storage** (not just client-side `app.storage`).

**⚠️ CRITICAL:** `app.storage.user` is client-side only. We MUST implement server-side session store for security.

**Deliverables:**
- **Server-side session store** (Redis) with encrypted session data
- Signed session cookie (session ID only, not full session)
- CSRF token generation and validation
- Session timeout enforcement (15min idle, 4hr absolute)
- Device binding (IP + User-Agent) with X-Forwarded-For handling for proxies
- Token refresh/rotation strategy
- Logout invalidation (Redis session delete)

**Session Architecture:**
```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Browser       │      │   NiceGUI App   │      │     Redis       │
│                 │      │                 │      │                 │
│  session_id     │─────▶│  Validate       │─────▶│  session:{id}   │
│  (signed cookie)│      │  Decrypt        │      │  {user, role,   │
│                 │◀─────│  Load session   │◀─────│   tokens, ...}  │
│  csrf_token     │      │                 │      │                 │
└─────────────────┘      └─────────────────┘      └─────────────────┘
```

**Implementation:**
```python
# apps/web_console_ng/auth/session_store.py
import redis
import secrets
from cryptography.fernet import Fernet
from typing import Any
import json

class ServerSessionStore:
    """
    Server-side session store with Redis backend.

    CRITICAL: Tokens NEVER stored client-side.
    Client only gets signed session_id cookie.
    """

    def __init__(self, redis_url: str, encryption_key: bytes):
        self.redis = redis.from_url(redis_url)
        self.fernet = Fernet(encryption_key)
        self.session_prefix = "nicegui_session:"
        self.idle_timeout = 15 * 60  # 15 minutes
        self.absolute_timeout = 4 * 60 * 60  # 4 hours

    def create_session(self, user_data: dict[str, Any]) -> str:
        """Create new session, return session_id for cookie."""
        session_id = secrets.token_urlsafe(32)
        session_data = {
            "user": user_data,
            "created_at": datetime.utcnow().isoformat(),
            "last_activity": datetime.utcnow().isoformat(),
            "device_fingerprint": self._get_device_fingerprint(),
        }

        # Encrypt and store
        encrypted = self.fernet.encrypt(json.dumps(session_data).encode())
        self.redis.setex(
            f"{self.session_prefix}{session_id}",
            self.absolute_timeout,
            encrypted
        )

        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve and validate session."""
        data = self.redis.get(f"{self.session_prefix}{session_id}")
        if not data:
            return None

        try:
            decrypted = self.fernet.decrypt(data)
            session = json.loads(decrypted)

            # Check idle timeout
            last_activity = datetime.fromisoformat(session["last_activity"])
            if (datetime.utcnow() - last_activity).seconds > self.idle_timeout:
                self.invalidate_session(session_id)
                return None

            # Update last activity
            session["last_activity"] = datetime.utcnow().isoformat()
            encrypted = self.fernet.encrypt(json.dumps(session).encode())
            self.redis.setex(
                f"{self.session_prefix}{session_id}",
                self.absolute_timeout,
                encrypted
            )

            return session
        except Exception:
            return None

    def invalidate_session(self, session_id: str) -> None:
        """Delete session (logout)."""
        self.redis.delete(f"{self.session_prefix}{session_id}")

# apps/web_console_ng/auth/middleware.py
from nicegui import app, ui
from starlette.requests import Request
from functools import wraps
from typing import Callable, Any

def _get_session_from_cookie(request: Request) -> tuple[str, str] | None:
    """Extract session_id and signature from signed HTTP cookie.

    SECURITY: Session ID comes from HttpOnly cookie, NOT app.storage.user.
    app.storage.user is client-side localStorage - never use for session ID!
    """
    cookie_value = request.cookies.get("__Host-nicegui_session")
    if not cookie_value or "." not in cookie_value:
        return None
    session_id, signature = cookie_value.rsplit(".", 1)
    return session_id, signature

def requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """NiceGUI auth decorator with server-side session validation."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Get session_id from signed HTTP cookie (NOT app.storage.user!)
        request = app.storage.request  # Access underlying Starlette request
        session_tuple = _get_session_from_cookie(request)

        if not session_tuple:
            ui.navigate.to("/login")
            return

        session_id, signature = session_tuple
        client_ip = get_client_ip(request)

        # Validate against server-side store with signature verification
        session_store = get_session_store()
        session = await session_store.validate_session(session_id, signature, client_ip)

        if not session:
            # Clear cookie on invalid session
            ui.navigate.to("/login?reason=session_expired")
            return

        # Device binding validation (with X-Forwarded-For handling)
        if not validate_device_binding(session, request):
            await session_store.invalidate_session(session_id)
            ui.navigate.to("/login?reason=device_mismatch")
            return

        # CSRF validation for state-changing requests
        # (NiceGUI handles this via WebSocket, but validate for API calls)

        return await func(*args, **kwargs)

    return wrapper

def has_permission(permission: str) -> bool:
    """Check if current user has permission."""
    request = app.storage.request
    session_tuple = _get_session_from_cookie(request)
    if not session_tuple:
        return False

    session_id, signature = session_tuple
    # Note: This is sync for template usage - use async version for full validation
    session = get_session_store().get_session_sync(session_id, signature)
    if not session:
        return False

    role = session.get("user", {}).get("role", "viewer")
    return permission in ROLE_PERMISSIONS.get(role, [])
```

**Security Considerations:**
- **Tokens in Redis ONLY** - never in client storage or cookies
- **Session ID signing** - HMAC-SHA256 to prevent tampering
- **Encryption at rest** - Fernet encryption for session data in Redis
- **CSRF protection** - Token validation for all state-changing operations
- **Device binding** - IP + User-Agent validation with X-Forwarded-For support
- **Replay protection** - Session ID rotation on privilege escalation
- **Audit logging** - All auth events logged to database

#### Session Security Hardening Checklist

| Security Control | Implementation |
|-----------------|----------------|
| **Cookie Flags** | `HttpOnly=True, Secure=True, SameSite=Lax` (Lax required for OAuth2 redirects) |
| **Session Fixation Prevention** | Rotate session ID on login and privilege escalation |
| **Origin Checks (WebSocket)** | Validate `Origin` header matches allowed domains |
| **CSRF for HTTP** | Double-submit cookie pattern for non-WS requests |
| **Session Timeout** | 15min idle (sliding), 4hr absolute (hard limit) |
| **Account Lockout** | 5 failed attempts → 15min lockout, exponential backoff |
| **JWKS Rotation** | Cache JWKS for 1hr, refresh on signature failure |
| **Refresh Token Storage** | Encrypted in Redis, rotated on each use |
| **Key Rotation** | Session encryption keys rotated monthly, old keys valid 24hr |
| **Trusted Proxy IPs** | Explicit allowlist for X-Forwarded-For trust |

#### Cookie Configuration

```python
# apps/web_console_ng/auth/cookie_config.py
SESSION_COOKIE_CONFIG = {
    "name": "nicegui_session",
    "httponly": True,      # Prevent XSS access
    "secure": True,        # HTTPS only
    "samesite": "lax",     # Lax required for OAuth2 redirects; use CSRF tokens for protection
    "max_age": 14400,      # 4 hours absolute
    "path": "/",
    "domain": None,        # Current domain only
}

def set_session_cookie(response, session_id: str, signature: str):
    """Set secure session cookie with HMAC signature."""
    signed_value = f"{session_id}.{signature}"
    response.set_cookie(
        **SESSION_COOKIE_CONFIG,
        value=signed_value,
    )
```

#### WebSocket Origin Validation

```python
# apps/web_console_ng/auth/ws_security.py
ALLOWED_ORIGINS = [
    "https://console.trading-platform.com",
    "https://staging-console.trading-platform.com",
]

async def validate_websocket_origin(websocket) -> bool:
    """Reject WebSocket connections from unauthorized origins."""
    origin = websocket.headers.get("origin", "")

    if not origin:
        return False  # Reject missing origin

    if origin not in ALLOWED_ORIGINS:
        logger.warning(f"Rejected WS from unauthorized origin: {origin}")
        return False

    return True
```

**Files to Create:**
- `apps/web_console_ng/auth/session_store.py` (NEW - server-side store)
- `apps/web_console_ng/auth/middleware.py`
- `apps/web_console_ng/auth/csrf.py` (NEW - CSRF token handling)
- `apps/web_console_ng/auth/permissions.py`
- `tests/apps/web_console_ng/test_session_store.py`
- `tests/apps/web_console_ng/test_auth_middleware.py`

---

#### T1.4: WebSocket Reconnection & State Recovery
**Effort:** 2 days | **PR:** `feat(P5): WebSocket stability and recovery`

**Problem (from Gemini review):** NiceGUI relies heavily on WebSockets. Network interruptions can disconnect the session.

**Deliverables:**
- Automatic WebSocket reconnection with exponential backoff
- State recovery after reconnection (restore UI state from server)
- "Connection lost" banner with retry button
- Graceful degradation to read-only mode during disconnection

**Implementation:**
```python
# apps/web_console_ng/core/connection_monitor.py
from nicegui import ui, app
import asyncio

class ConnectionMonitor:
    """Monitor WebSocket connection and handle reconnection."""

    def __init__(self):
        self.connected = True
        self.reconnect_attempts = 0
        self.max_attempts = 5

    async def on_disconnect(self):
        """Handle disconnection - show banner, attempt reconnect."""
        self.connected = False

        # Show connection lost banner
        with ui.dialog(value=True) as dialog:
            with ui.card().classes("bg-red-100 p-4"):
                ui.label("Connection Lost").classes("text-red-600 font-bold")
                ui.label("Attempting to reconnect...")
                spinner = ui.spinner()

        # Exponential backoff reconnection
        while self.reconnect_attempts < self.max_attempts:
            delay = min(30, 2 ** self.reconnect_attempts)
            await asyncio.sleep(delay)

            if await self._try_reconnect():
                dialog.close()
                ui.notify("Reconnected", type="positive")
                return

            self.reconnect_attempts += 1

        # Failed - suggest page reload
        dialog.close()
        ui.notify("Connection failed. Please refresh the page.", type="negative")
```

**Files to Create:**
- `apps/web_console_ng/core/connection_monitor.py`
- `tests/apps/web_console_ng/test_connection_recovery.py`

---

### Phase 2: Core Layout & Navigation
**Effort:** 4-6 days | **Priority:** P0 (Visual Foundation)

**Goal:** Establish persistent layout (header, sidebar, content area) that all pages inherit.

#### T2.1: Main Layout Component
**Effort:** 2-3 days | **PR:** `feat(P5): Main layout and navigation`

**Deliverables:**
- Header with branding, user info, global status indicators
- Left drawer (sidebar) with navigation links
- Main content area with routing
- Kill switch status badge (global visibility)
- Connection status indicator

**Implementation:**
```python
# apps/web_console_ng/ui/layout.py
from nicegui import ui, app
from typing import Callable, Any

def main_layout(page_func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for consistent page layout."""

    async def wrapper(*args: Any, **kwargs: Any) -> None:
        # Header
        with ui.header().classes("bg-slate-900 items-center text-white px-4"):
            ui.label("Trading Console").classes("text-xl font-bold mr-5")

            # Global status indicators
            with ui.row().classes("ml-auto gap-4"):
                kill_switch_badge = ui.label("").classes("px-2 py-1 rounded")
                connection_badge = ui.label("Connecting...").classes("px-2 py-1 rounded bg-yellow-500")

        # Sidebar (Drawer)
        with ui.left_drawer(value=True).classes("bg-slate-100 w-64") as drawer:
            with ui.column().classes("w-full gap-1 p-2"):
                ui.label("Navigation").classes("text-gray-500 text-sm mb-2")

                # Navigation links with active state
                nav_items = [
                    ("Dashboard", "/", "home"),
                    ("Manual Controls", "/manual", "edit"),
                    ("Kill Switch", "/kill-switch", "warning"),
                    ("Risk Analytics", "/risk", "trending_up"),
                    ("Backtest", "/backtest", "science"),
                    ("Admin", "/admin", "settings"),
                ]

                for label, path, icon in nav_items:
                    with ui.link(target=path).classes("nav-link w-full"):
                        with ui.row().classes("items-center gap-2 p-2 hover:bg-slate-200 rounded"):
                            ui.icon(icon).classes("text-gray-600")
                            ui.label(label)

        # Main content area
        with ui.column().classes("w-full p-4"):
            await page_func(*args, **kwargs)

        # Global status update timer
        async def update_global_status() -> None:
            try:
                status = await client.fetch_kill_switch_status()
                if status.get("state") == "ENGAGED":
                    kill_switch_badge.set_text("KILL SWITCH ENGAGED")
                    kill_switch_badge.classes("bg-red-500", remove="bg-green-500")
                else:
                    kill_switch_badge.set_text("TRADING ACTIVE")
                    kill_switch_badge.classes("bg-green-500", remove="bg-red-500")

                connection_badge.set_text("Connected")
                connection_badge.classes("bg-green-500", remove="bg-yellow-500 bg-red-500")
            except Exception:
                connection_badge.set_text("Disconnected")
                connection_badge.classes("bg-red-500", remove="bg-green-500 bg-yellow-500")

        ui.timer(5.0, update_global_status)

    return wrapper
```

**Files to Create:**
- `apps/web_console_ng/ui/layout.py`
- `apps/web_console_ng/ui/theme.py`
- `apps/web_console_ng/ui/navigation.py`
- `tests/apps/web_console_ng/test_layout.py`

---

#### T2.2: Login Page & All 4 Auth Flows
**Effort:** 5-6 days | **PR:** `feat(P5): Login page and all auth flows`

**Note:** Must explicitly implement ALL 4 auth types.

**Deliverables - All 4 Auth Types:**

1. **Dev Mode Authentication** (local development)
   - Username/password form
   - No external dependencies
   - Test user fixtures

2. **Basic Auth** (simple deployments)
   - Form-based username/password
   - LDAP/database backend integration
   - Rate limiting on failed attempts

3. **mTLS (Mutual TLS)** (production - certificate-based)
   - Client certificate validation
   - JWT-DN binding for session
   - Admin fallback during IdP outages

4. **OAuth2/OIDC** (production - Auth0)
   - PKCE flow with state validation
   - Token refresh/rotation
   - IDP health monitoring with fallback

**Additional Deliverables:**
- MFA step-up verification (port from existing `mfa_verification.py`)
- Login page with form validation
- Logout with complete session cleanup
- Rate limiting (exponential backoff on failed attempts)

**Implementation - Auth Type Detection:**
```python
# apps/web_console_ng/auth/auth_router.py
from apps.web_console_ng import config

def get_auth_handler():
    """Return appropriate auth handler based on AUTH_TYPE config."""
    auth_type = config.AUTH_TYPE

    handlers = {
        "dev": DevAuthHandler(),
        "basic": BasicAuthHandler(),
        "mtls": MTLSAuthHandler(),
        "oauth2": OAuth2AuthHandler(),
    }

    return handlers.get(auth_type, DevAuthHandler())
```

**Files to Create:**
- `apps/web_console_ng/pages/login.py`
- `apps/web_console_ng/auth/auth_router.py` (dispatcher)
- `apps/web_console_ng/auth/dev_auth.py`
- `apps/web_console_ng/auth/basic_auth.py` (NEW - was missing!)
- `apps/web_console_ng/auth/oauth2.py` (with PKCE)
- `apps/web_console_ng/auth/mtls.py`
- `apps/web_console_ng/auth/mfa.py` (MFA step-up)
- `apps/web_console_ng/auth/rate_limiter.py`
- `tests/apps/web_console_ng/test_login.py`
- `tests/apps/web_console_ng/test_all_auth_flows.py`

---

### Phase 2.5: Horizontal Scaling & High Availability Architecture
**Effort:** 2-3 days | **Priority:** P0 (Infrastructure Foundation)

**Goal:** Define and implement HA/scaling strategy for WebSocket connections and server-side sessions before real-time features.

**Dependency:** Must complete before Phase 3 (Real-Time Dashboard).

#### Scaling Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           LOAD BALANCER (nginx)                          │
│                                                                          │
│   • Sticky sessions via cookie (nicegui_server_id)                      │
│   • WebSocket upgrade headers preserved                                  │
│   • Health checks on /health endpoint                                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                    │                    │                    │
        ┌───────────▼──────┐  ┌──────────▼──────┐  ┌──────────▼──────┐
        │  NiceGUI Pod 1   │  │  NiceGUI Pod 2   │  │  NiceGUI Pod N   │
        │                  │  │                  │  │                  │
        │  • WS connections │  │  • WS connections │  │  • WS connections │
        │  • Local state    │  │  • Local state    │  │  • Local state    │
        └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
                 │                     │                     │
                 └─────────────────────┼─────────────────────┘
                                       │
                          ┌────────────▼────────────┐
                          │   Redis Cluster (HA)    │
                          │                         │
                          │  • Session store        │
                          │  • Pub/Sub fan-out      │
                          │  • Connection state     │
                          └─────────────────────────┘
```

#### HA Components

| Component | Strategy | Implementation |
|-----------|----------|----------------|
| **Sticky Sessions** | Cookie-based routing | nginx `upstream` with `sticky cookie` |
| **Session Store** | Redis Cluster (HA) | 3-node cluster, automatic failover |
| **WS Fan-out** | Redis Pub/Sub | Broadcast updates to all pods |
| **Connection Recovery** | Reconnect to any pod | Session rehydrated from Redis |
| **Instance Limits** | Per-pod caps | Max 1000 WS connections per instance |

#### nginx Configuration Example

```nginx
upstream nicegui_cluster {
    # Sticky sessions - same user routes to same pod
    sticky cookie nicegui_server_id expires=1h path=/;

    server nicegui-1:8080 max_fails=3 fail_timeout=30s;
    server nicegui-2:8080 max_fails=3 fail_timeout=30s;
    server nicegui-3:8080 max_fails=3 fail_timeout=30s;
}

location / {
    proxy_pass http://nicegui_cluster;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # Timeouts for long-lived WS
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
}
```

#### Session Store HA Configuration

```python
# apps/web_console_ng/core/redis_ha.py
from redis.sentinel import Sentinel

class HARedisStore:
    """High-availability Redis session store with Sentinel."""

    def __init__(self):
        self.sentinel = Sentinel([
            ('redis-sentinel-1', 26379),
            ('redis-sentinel-2', 26379),
            ('redis-sentinel-3', 26379),
        ], socket_timeout=0.5)

    def get_master(self):
        return self.sentinel.master_for('nicegui-sessions', socket_timeout=0.5)

    def get_slave(self):
        return self.sentinel.slave_for('nicegui-sessions', socket_timeout=0.5)
```

#### State Rehydration on WS Failover

**Problem:** NiceGUI per-client UI state is in-memory. On pod failure or WS disconnect, UI state is lost.

**Solution:** Hybrid state model - critical state in Redis, UI re-renders from server state.

```python
# apps/web_console_ng/core/state_manager.py

class UserStateManager:
    """
    Manages user state with Redis persistence for failover recovery.

    UI state: ephemeral, re-rendered on reconnect
    Critical state: persisted in Redis (positions, pending orders, preferences)
    """

    def __init__(self, redis: HARedisStore, user_id: str):
        self.redis = redis
        self.user_id = user_id
        self.state_key = f"user_state:{user_id}"

    async def save_critical_state(self, state: dict) -> None:
        """Persist critical state for failover recovery."""
        await self.redis.get_master().setex(
            self.state_key,
            ttl=86400,  # 24hr
            value=json.dumps(state)
        )

    async def restore_state(self) -> dict:
        """Restore state after reconnection or pod switch."""
        data = await self.redis.get_slave().get(self.state_key)
        return json.loads(data) if data else {}

    async def on_reconnect(self, ui_context) -> None:
        """Called when user reconnects after WS drop."""
        state = await self.restore_state()

        # Re-fetch fresh data from APIs
        positions = await client.fetch_positions()
        orders = await client.fetch_open_orders()

        # Re-render UI with fresh data + restored preferences
        ui_context.refresh_dashboard(positions, orders, state.get("preferences", {}))
```

**State Categories:**
| State Type | Storage | Recovery Strategy |
|------------|---------|-------------------|
| UI widgets | In-memory (lost on disconnect) | Re-render from server data |
| User preferences | Redis (persisted) | Restore on reconnect |
| Pending form data | Redis (persisted) | Restore, prompt user to confirm |
| Dashboard filters | Redis (persisted) | Restore with last values |
| Position/order data | Backend API | Fetch fresh on reconnect |

#### Per-Client Task Cleanup

```python
# apps/web_console_ng/core/client_lifecycle.py

class ClientLifecycleManager:
    """Manages per-client background tasks and cleanup."""

    def __init__(self):
        self.client_tasks: dict[str, list[asyncio.Task]] = {}

    def register_task(self, client_id: str, task: asyncio.Task) -> None:
        """Register a background task for a client."""
        if client_id not in self.client_tasks:
            self.client_tasks[client_id] = []
        self.client_tasks[client_id].append(task)

    async def cleanup_client(self, client_id: str) -> None:
        """Cancel all tasks when client disconnects."""
        tasks = self.client_tasks.pop(client_id, [])
        for task in tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info(f"Cleaned up {len(tasks)} tasks for client {client_id}")
```

**Files to Create:**
- `apps/web_console_ng/core/redis_ha.py`
- `apps/web_console_ng/core/state_manager.py`
- `apps/web_console_ng/core/client_lifecycle.py`
- `infra/nginx/nicegui-cluster.conf`
- `docs/RUNBOOKS/nicegui-scaling.md`

---

### Phase 2.6: Observability & Alerting
**Effort:** 2 days | **Priority:** P0 (Pre-requisite for production)

**Goal:** Define metrics, alerts, and runbooks for monitoring the NiceGUI deployment.

#### Key Metrics

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| `nicegui_ws_connections_total` | NiceGUI server | > 800 per pod (80% capacity) |
| `nicegui_ws_disconnect_rate` | NiceGUI server | > 5% per minute |
| `nicegui_auth_failures_total` | Auth middleware | > 10 per minute |
| `nicegui_session_creation_rate` | Session store | > 100/min (possible attack) |
| `nicegui_redis_latency_p99` | Redis client | > 50ms |
| `nicegui_api_latency_p95` | HTTP client | > 500ms |
| `nicegui_memory_per_user_mb` | Process metrics | > 30MB average |
| `nicegui_push_queue_depth` | Pub/Sub handler | > 100 (backpressure) |

#### Alert Rules (Prometheus)

```yaml
# infra/prometheus/alerts/nicegui.yml
groups:
  - name: nicegui
    rules:
      - alert: HighWSDisconnectRate
        expr: rate(nicegui_ws_disconnects_total[5m]) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High WebSocket disconnect rate ({{ $value | printf \"%.2f\" }})"

      - alert: AuthFailureSpike
        expr: rate(nicegui_auth_failures_total[5m]) > 0.1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Authentication failure spike detected"

      - alert: HighAPILatency
        expr: histogram_quantile(0.95, nicegui_api_latency_seconds_bucket) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 API latency > 500ms"

      - alert: MemoryPerUserHigh
        expr: nicegui_memory_bytes / nicegui_active_users > 30000000
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Memory per user exceeds 30MB"
```

#### Error Budget

| SLO | Target | Error Budget (30d) |
|-----|--------|-------------------|
| Availability | 99.9% | 43 minutes downtime |
| P95 latency < 500ms | 99% | 7.2 hours degraded |
| Auth success rate | 99.5% | ~1500 failures allowed |

**Files to Create:**
- `infra/prometheus/alerts/nicegui.yml`
- `infra/grafana/dashboards/nicegui-overview.json`
- `docs/RUNBOOKS/nicegui-incident-response.md`

---

### Parallel Run: Session Isolation Strategy

**Problem:** During parallel run, users might hit both Streamlit and NiceGUI. Session cookies must not conflict.

**Solution:** Separate cookie names and domains, coordinated logout.

| Aspect | Streamlit | NiceGUI |
|--------|-----------|---------|
| **Cookie name** | `streamlit_session` | `nicegui_session` |
| **Cookie path** | `/legacy/` | `/` |
| **Session store key** | `st_session:{id}` | `ng_session:{id}` |
| **OAuth callback** | `/legacy/callback` | `/auth/callback` |
| **Logout endpoint** | `/legacy/logout` | `/auth/logout` |

#### Coordinated Logout

```python
# apps/web_console_ng/auth/logout.py

async def logout(request) -> RedirectResponse:
    """Logout from NiceGUI and signal Streamlit to clear session."""

    # 1. Invalidate NiceGUI session
    session_id = get_session_id(request)
    await session_store.invalidate_session(session_id)

    # 2. Clear NiceGUI cookie
    response = RedirectResponse(url="/login")
    response.delete_cookie("nicegui_session")

    # 3. If user also has Streamlit session, invalidate via shared Redis
    if await redis.exists(f"st_session:{user_id}"):
        await redis.delete(f"st_session:{user_id}")

    # 4. For OAuth2: trigger RP-initiated logout
    if auth_method == "oauth2":
        return RedirectResponse(url=build_oidc_logout_url())

    return response
```

---

### Phase 3: Real-Time Dashboard
**Effort:** 8-12 days | **Priority:** P0 (Core Functionality)

**Goal:** Migrate the main dashboard with real-time P&L, positions, and system status.

**Dependency:** Phase 2.5 (HA/Scaling) must be complete.

#### Real-Time Update Strategy

**Problem:** 1s polling doesn't meet sub-second requirement for trading dashboards.

**Solution:** Hybrid approach - push for critical data, polling for non-critical.

```
┌─────────────────────────────────────────────────────────────────┐
│                    REAL-TIME UPDATE STRATEGY                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PUSH-BASED (WebSocket/SSE) - Sub-second updates:              │
│  ├── Position changes (fills, new positions)                   │
│  ├── Kill switch state changes                                  │
│  ├── Circuit breaker status                                     │
│  └── P&L updates (when positions change)                       │
│                                                                  │
│  POLLING (ui.timer) - 1-5 second updates:                       │
│  ├── Market prices (backed by market data service)             │
│  ├── System health metrics                                      │
│  └── Non-critical dashboard stats                               │
│                                                                  │
│  BACKEND STREAMING:                                             │
│  ├── Redis Pub/Sub → NiceGUI WebSocket                         │
│  ├── Debounce: max 10 updates/second per user                  │
│  └── Backpressure: queue overflow → drop oldest                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation - Server Push:**
```python
# apps/web_console_ng/core/realtime.py
from nicegui import ui, app
import redis.asyncio as redis

class RealtimeUpdater:
    """Push real-time updates to connected clients."""

    def __init__(self):
        self.redis = redis.from_url(config.REDIS_URL)
        self.subscribers: dict[str, set] = {}  # user_id -> set of callbacks

    async def subscribe_position_updates(self, user_id: str, callback):
        """Subscribe to position updates for a user."""
        channel = f"positions:{user_id}"
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)

        async def listener():
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = json.loads(message["data"])
                    # Debounce - max 10 updates/sec
                    await callback(data)

        asyncio.create_task(listener())

# Usage in dashboard.py
async def dashboard():
    realtime = RealtimeUpdater()

    # Subscribe to push updates
    async def on_position_update(data):
        grid.options["rowData"] = data["positions"]
        grid.update()
        pnl_label.set_text(f"${data['unrealized_pl']:,.2f}")

    await realtime.subscribe_position_updates(user_id, on_position_update)

    # Polling for less critical data (every 5s)
    async def update_market_data():
        prices = await client.fetch_market_prices()
        # Update price columns only
        ...

    ui.timer(5.0, update_market_data)
```

#### T3.1: Dashboard - Metric Cards
**Effort:** 2-3 days | **PR:** `feat(P5): Dashboard metric cards`

**Deliverables:**
- Unrealized P&L card with color coding
- Total positions count
- Day's realized P&L
- Buying power / margin used
- Real-time updates via `ui.timer`

**Implementation:**
```python
# apps/web_console_ng/pages/dashboard.py
from nicegui import ui
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng.ui.layout import main_layout
from apps.web_console_ng.auth.middleware import requires_auth

@ui.page("/")
@requires_auth
@main_layout
async def dashboard() -> None:
    """Main trading dashboard with real-time updates."""
    client = AsyncTradingClient.get()

    # Metric cards row
    with ui.row().classes("w-full gap-4 mb-6"):
        # Unrealized P&L
        with ui.card().classes("flex-1"):
            ui.label("Unrealized P&L").classes("text-gray-500 text-sm")
            pnl_label = ui.label("$ --").classes("text-2xl font-bold")

        # Total Positions
        with ui.card().classes("flex-1"):
            ui.label("Positions").classes("text-gray-500 text-sm")
            pos_count = ui.label("--").classes("text-2xl font-bold")

        # Day's Realized
        with ui.card().classes("flex-1"):
            ui.label("Realized (Today)").classes("text-gray-500 text-sm")
            realized_label = ui.label("$ --").classes("text-2xl font-bold")

        # Buying Power
        with ui.card().classes("flex-1"):
            ui.label("Buying Power").classes("text-gray-500 text-sm")
            bp_label = ui.label("$ --").classes("text-2xl font-bold")

    # Update function (surgical DOM updates, no page refresh)
    async def update_metrics() -> None:
        try:
            data = await client.fetch_realtime_pnl()

            # Unrealized P&L
            pnl = data.get("total_unrealized_pl", 0)
            pnl_label.set_text(f"${pnl:,.2f}")
            if pnl >= 0:
                pnl_label.classes("text-green-600", remove="text-red-600")
            else:
                pnl_label.classes("text-red-600", remove="text-green-600")

            # Position count
            pos_count.set_text(str(data.get("total_positions", 0)))

            # Realized P&L
            realized = data.get("realized_pl_today", 0)
            realized_label.set_text(f"${realized:,.2f}")

            # Buying Power
            bp = data.get("buying_power", 0)
            bp_label.set_text(f"${bp:,.2f}")

        except Exception as e:
            ui.notify(f"Update failed: {e}", type="negative")

    # Initial load + timer
    await update_metrics()
    ui.timer(1.0, update_metrics)
```

**Files to Create:**
- `apps/web_console_ng/pages/dashboard.py`
- `apps/web_console_ng/components/metric_card.py`
- `tests/apps/web_console_ng/test_dashboard.py`

---

#### T3.2: Dashboard - Positions Table (AG Grid)
**Effort:** 2-3 days | **PR:** `feat(P5): Positions AG Grid table`

**Deliverables:**
- AG Grid with client-side sorting/filtering
- P&L color coding (green/red)
- Position close button per row
- Real-time row updates (efficient diffing)

**Implementation:**
```python
# Positions grid (continuation of dashboard.py)
grid = ui.aggrid({
    "columnDefs": [
        {"field": "symbol", "headerName": "Symbol", "sortable": True, "filter": True},
        {"field": "qty", "headerName": "Qty", "sortable": True},
        {"field": "avg_entry_price", "headerName": "Avg Entry", "valueFormatter": "x => '$' + x.value.toFixed(2)"},
        {"field": "current_price", "headerName": "Current", "valueFormatter": "x => '$' + x.value.toFixed(2)"},
        {"field": "unrealized_pl", "headerName": "P&L",
         "cellStyle": {"function": "params.value >= 0 ? {color: 'green'} : {color: 'red'}"},
         "valueFormatter": "x => '$' + x.value.toFixed(2)"},
        {"field": "unrealized_plpc", "headerName": "P&L %",
         "valueFormatter": "x => (x.value * 100).toFixed(2) + '%'"},
    ],
    "rowData": [],
    "domLayout": "autoHeight",
}).classes("w-full")

async def update_positions() -> None:
    data = await client.fetch_positions()
    grid.options["rowData"] = data.get("positions", [])
    grid.update()
```

**Files to Create:**
- `apps/web_console_ng/components/positions_grid.py`
- `tests/apps/web_console_ng/test_positions_grid.py`

---

#### T3.3: Dashboard - Orders & Activity
**Effort:** 2 days | **PR:** `feat(P5): Orders and activity feed`

**Deliverables:**
- Open orders table with cancel buttons
- Recent fills activity feed
- Order status badges (pending, filled, cancelled)

**Files to Create:**
- `apps/web_console_ng/components/orders_table.py`
- `apps/web_console_ng/components/activity_feed.py`
- `tests/apps/web_console_ng/test_orders_table.py`

---

### Phase 3.5: GATING MILESTONE - Security & Performance Validation
**Effort:** 3-4 days | **Priority:** P0 (MUST PASS before Phase 4)

**Note:** Auth flows and performance MUST be validated before any trading actions.

**Purpose:** Validate security and performance before enabling trading functionality.

#### Security Validation Checklist
- [ ] All 4 auth flows tested (dev, basic, mTLS, OAuth2)
- [ ] Session store encryption verified
- [ ] CSRF protection functional
- [ ] Device binding working with proxies
- [ ] MFA step-up tested
- [ ] Audit logging capturing all events
- [ ] Rate limiting preventing brute force

#### Performance Validation Targets

| Metric | Target | Stretch Goal | Test Method |
|--------|--------|--------------|-------------|
| Page load (cold) | < 500ms | < 300ms | Playwright timing |
| Page load (warm) | < 150ms | < 100ms | Playwright timing |
| P&L update (push) | < 50ms | < 30ms | Backend → UI measurement |
| Position grid update | < 100ms | < 50ms | 100 row dataset |
| Concurrent users | 100 | 200 | Load test with k6/locust |
| WebSocket stability | 99.95% | 99.99% | 4hr soak test |
| Memory per user | < 25MB | < 15MB | Monitoring during load test |
| P99 latency (all ops) | < 300ms | < 200ms | APM monitoring |
| Order submission E2E | < 200ms | < 100ms | Click to API response |

#### Load Test Plan
```bash
# k6 load test script
k6 run --vus 50 --duration 30m tests/load/web_console_load.js

# Metrics to capture:
# - http_req_duration (p95 < 500ms)
# - ws_connecting_duration (p95 < 100ms)
# - ws_sessions (stable over time)
# - memory_usage (no unbounded growth)
```

#### Failure Mode UX Validation
- [ ] "Connection Lost" banner displays correctly
- [ ] Read-only mode during disconnection
- [ ] Stale data indicators show when data > 30s old
- [ ] Backend down → graceful error, no crash

**Files to Create:**
- `tests/load/web_console_load.js` (k6 script)
- `tests/e2e/test_auth_flows.py` (Playwright auth tests)
- `docs/RUNBOOKS/nicegui-performance.md`

**Exit Criteria:** ALL checkboxes complete, ALL metrics met. BLOCK Phase 4 until passed.

---

### Phase 4: Manual Trading Controls
**Effort:** 5-7 days | **Priority:** P0 (Critical Safety)

**Dependency:** Phase 3.5 MUST pass before starting Phase 4.

**Goal:** Port manual order entry, kill switch, and position management with safety confirmations.

#### T4.1: Manual Order Entry
**Effort:** 3-4 days | **PR:** `feat(P5): Manual order entry form`

**Problem:** Current two-step confirmation pattern relies on `st.session_state` and `st.rerun()`.

**NiceGUI Solution:** Use `ui.dialog` for confirmation modal.

**Deliverables:**
- Order form (symbol, qty, side, order type)
- Idempotent `client_order_id` generation
- Preview dialog with order summary
- Kill switch re-check before submission
- Audit logging

**Implementation:**
```python
# apps/web_console_ng/pages/manual_order.py
from nicegui import ui
import hashlib
from datetime import date

@ui.page("/manual")
@requires_auth
@main_layout
async def manual_order() -> None:
    client = AsyncTradingClient.get()

    # Form inputs
    with ui.card().classes("w-full max-w-md"):
        ui.label("Manual Order Entry").classes("text-xl font-bold mb-4")

        symbol = ui.input("Symbol", placeholder="AAPL").classes("w-full")
        qty = ui.number("Quantity", value=10, min=1).classes("w-full")
        side = ui.select(["buy", "sell"], value="buy", label="Side").classes("w-full")
        order_type = ui.select(["market", "limit"], value="market", label="Type").classes("w-full")
        limit_price = ui.number("Limit Price", value=0).classes("w-full")
        reason = ui.textarea("Reason (required)", placeholder="Why this trade?").classes("w-full")

        limit_price.bind_visibility_from(order_type, "value", value="limit")

    async def preview_order() -> None:
        # Validation
        if len(reason.value or "") < 10:
            ui.notify("Reason must be at least 10 characters", type="warning")
            return

        # Generate DETERMINISTIC idempotent client_order_id
        # Includes REASON to allow intentional repeat trades (same symbol/qty/side)
        # while preventing accidental duplicates from network retries
        # Pattern: symbol + side + qty + price + strategy + date + reason_hash
        price_component = str(limit_price.value) if order_type.value == "limit" else "market"
        reason_hash = hashlib.sha256(reason.value.encode()).hexdigest()[:8]
        order_hash = hashlib.sha256(
            f"{symbol.value}{side.value}{qty.value}{price_component}manual{date.today()}{reason_hash}".encode()
        ).hexdigest()[:24]
        # NOTE: Different reasons = different client_order_id = allowed repeat trades
        # Same inputs + same reason = same client_order_id = retry protection

        # Check kill switch BEFORE showing dialog
        ks_status = await client.fetch_kill_switch_status()
        if ks_status.get("state") == "ENGAGED":
            ui.notify("Cannot submit: Kill Switch is ENGAGED", type="negative")
            return

        # Confirmation dialog
        with ui.dialog() as dialog, ui.card().classes("p-4"):
            ui.label("Confirm Order").classes("text-xl font-bold mb-4")

            with ui.column().classes("gap-2"):
                ui.label(f"Symbol: {symbol.value}")
                ui.label(f"Side: {side.value.upper()}")
                ui.label(f"Quantity: {qty.value}")
                ui.label(f"Type: {order_type.value}")
                if order_type.value == "limit":
                    ui.label(f"Limit Price: ${limit_price.value}")
                ui.label(f"Reason: {reason.value}").classes("text-gray-600 text-sm")

            ui.separator()

            with ui.row().classes("gap-4 mt-4"):
                async def confirm() -> None:
                    # FRESH kill switch check (critical safety)
                    ks = await client.fetch_kill_switch_status()
                    if ks.get("state") == "ENGAGED":
                        ui.notify("Order blocked: Kill Switch engaged", type="negative")
                        dialog.close()
                        return

                    try:
                        order_req = {
                            "symbol": symbol.value,
                            "qty": int(qty.value),
                            "side": side.value,
                            "type": order_type.value,
                            "client_order_id": order_hash,
                            "reason": reason.value,
                        }
                        if order_type.value == "limit":
                            order_req["limit_price"] = float(limit_price.value)

                        result = await client.submit_order(order_req)
                        ui.notify(f"Order submitted: {result.get('client_order_id')}", type="positive")
                        dialog.close()

                        # Clear form
                        symbol.value = ""
                        qty.value = 10
                        reason.value = ""

                    except Exception as e:
                        ui.notify(f"Order failed: {e}", type="negative")

                ui.button("Confirm", on_click=confirm).classes("bg-green-600 text-white")
                ui.button("Cancel", on_click=dialog.close).classes("bg-gray-400")

        dialog.open()

    ui.button("Preview Order", on_click=preview_order).classes("bg-blue-600 text-white mt-4")
```

**Files to Create:**
- `apps/web_console_ng/pages/manual_order.py`
- `apps/web_console_ng/components/order_form.py`
- `apps/web_console_ng/components/order_confirmation_dialog.py`
- `tests/apps/web_console_ng/test_manual_order.py`

---

#### T4.2: Kill Switch Management
**Effort:** 2-3 days | **PR:** `feat(P5): Kill switch management page`

**Deliverables:**
- Kill switch status display (large, prominent)
- Engage button with confirmation
- Disengage button with two-factor confirmation
- Rate limiting (1 action per minute)
- Full audit trail display

**Files to Create:**
- `apps/web_console_ng/pages/kill_switch.py`
- `apps/web_console_ng/components/kill_switch_panel.py`
- `tests/apps/web_console_ng/test_kill_switch.py`

---

#### T4.3: Position Management
**Effort:** 2 days | **PR:** `feat(P5): Position close and flatten controls`

**Deliverables:**
- Close single position button
- Flatten all positions (two-factor + confirmation)
- Cancel all orders button
- Force adjustment with reason input

**Files to Create:**
- `apps/web_console_ng/pages/position_management.py`
- `apps/web_console_ng/components/position_actions.py`
- `tests/apps/web_console_ng/test_position_management.py`

---

### Phase 5: Charts & Analytics
**Effort:** 4-6 days | **Priority:** P1 (Visual Features)

**Goal:** Port Plotly charts for P&L, risk, and performance visualization.

#### T5.1: P&L Charts
**Effort:** 2-3 days | **PR:** `feat(P5): P&L visualization charts`

**Deliverables:**
- Equity curve chart
- Daily P&L bar chart
- Drawdown visualization
- Cumulative returns

**Implementation:**
```python
# apps/web_console_ng/components/pnl_chart.py
from nicegui import ui
import plotly.graph_objects as go

async def render_equity_curve(data: list[dict]) -> None:
    """Render equity curve with Plotly."""

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[d["date"] for d in data],
        y=[d["equity"] for d in data],
        mode="lines",
        name="Equity",
        line=dict(color="blue", width=2),
    ))

    fig.update_layout(
        title="Equity Curve",
        xaxis_title="Date",
        yaxis_title="Equity ($)",
        template="plotly_white",
        height=400,
    )

    ui.plotly(fig).classes("w-full")
```

**Files to Create:**
- `apps/web_console_ng/components/equity_curve_chart.py`
- `apps/web_console_ng/components/pnl_chart.py`
- `apps/web_console_ng/components/drawdown_chart.py`
- `tests/apps/web_console_ng/test_charts.py`

---

#### T5.2: Risk Dashboard Charts
**Effort:** 2-3 days | **PR:** `feat(P5): Risk analytics charts`

**Deliverables:**
- VaR history chart (port from `var_chart.py`)
- Factor exposure heatmap
- Stress test results visualization
- Risk budget utilization

**Files to Create:**
- `apps/web_console_ng/pages/risk.py`
- `apps/web_console_ng/components/var_chart.py`
- `apps/web_console_ng/components/factor_heatmap.py`
- `tests/apps/web_console_ng/test_risk_charts.py`

---

### Phase 6: Remaining Pages Migration
**Effort:** 15-20 days | **Priority:** P1 (Feature Parity)

**Goal:** Port all remaining pages to achieve feature parity.

#### T6.1: Circuit Breaker Dashboard
**Effort:** 2 days | **PR:** `feat(P5): Circuit breaker dashboard`

#### T6.2: System Health Monitor
**Effort:** 2 days | **PR:** `feat(P5): System health monitor`

#### T6.3: Backtest Manager
**Effort:** 3-4 days | **PR:** `feat(P5): Backtest manager page`

#### T6.4: Admin Dashboard
**Effort:** 3-4 days | **PR:** `feat(P5): Admin dashboard`

#### T6.5: Alerts Configuration
**Effort:** 2-3 days | **PR:** `feat(P5): Alerts configuration`

#### T6.6: Audit Log Viewer
**Effort:** 2 days | **PR:** `feat(P5): Audit log viewer`

#### T6.7: Data Management Pages
**Effort:** 3-4 days | **PR:** `feat(P5): Data sync and explorer pages`

---

### Phase 7: Infrastructure & Cutover
**Effort:** 4-6 days | **Priority:** P0 (Production Readiness)

**Goal:** Production deployment, testing, and Streamlit deprecation.

#### T7.1: Dockerfile & Deployment
**Effort:** 2 days | **PR:** `feat(P5): NiceGUI Docker and deployment`

**Deliverables:**
- Production Dockerfile
- Health check endpoint (`/health`)
- CSP configuration for NiceGUI WebSocket paths
- Resource limits and monitoring

**Dockerfile Changes:**
```dockerfile
# apps/web_console_ng/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# NiceGUI runs on port 8080 by default
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "apps/web_console_ng/main.py"]
```

---

#### T7.2: Test Migration
**Effort:** 2-3 days | **PR:** `feat(P5): Test suite migration`

**Deliverables:**
- Port 30+ test files to NiceGUI patterns
- Update CSP tests for WebSocket paths
- Integration tests for auth flows
- E2E smoke tests

---

#### T7.3: Cutover & Cleanup
**Effort:** 1-2 days | **PR:** `chore(P5): Streamlit deprecation`

**Deliverables:**
- Remove Streamlit dependencies from `requirements.txt`
- Archive `apps/web_console/` (or delete after stabilization)
- Update `docker-compose.yml`

---

### Phase 8: Documentation & Knowledge Base
**Effort:** 5-7 days | **Priority:** P1 (Project Completion)

**Goal:** Comprehensive documentation updates for all phases, ensuring maintainability and knowledge transfer.

#### T8.1: Architecture Decision Record (ADR)
**Effort:** 2 days | **PR:** `docs(P5): ADR-0031 NiceGUI migration`

**Deliverables:**
- `docs/ADRs/ADR-0031-nicegui-migration.md`

**ADR Content:**
- Decision rationale (Streamlit limitations, NiceGUI benefits)
- Alternatives considered (React, Vue, Dash, Panel)
- Migration strategy (parallel run, phased cutover)
- Security considerations (session architecture, auth flows)
- Performance requirements and validation
- Rollback plan and triggers
- Consequences and trade-offs

---

#### T8.2: Concept Documentation
**Effort:** 2-3 days | **PR:** `docs(P5): NiceGUI concepts and patterns`

**Deliverables:**
- `docs/CONCEPTS/nicegui-architecture.md` - Event-driven model, AsyncIO patterns
- `docs/CONCEPTS/nicegui-auth.md` - Session store, auth flows, CSRF protection
- `docs/CONCEPTS/nicegui-realtime.md` - Push vs polling, WebSocket handling
- `docs/CONCEPTS/nicegui-components.md` - Component patterns, AG Grid usage

**Content per document:**
1. Overview and purpose
2. Architecture diagrams
3. Code patterns and examples
4. Common pitfalls and solutions
5. Integration with existing services

---

#### T8.3: Operational Runbooks
**Effort:** 1-2 days | **PR:** `docs(P5): NiceGUI operational runbooks`

**Deliverables:**
- `docs/RUNBOOKS/nicegui-deployment.md` - Deployment procedures, health checks
- `docs/RUNBOOKS/nicegui-troubleshooting.md` - Common issues, debugging
- `docs/RUNBOOKS/nicegui-performance.md` - Performance tuning, monitoring
- `docs/RUNBOOKS/nicegui-rollback.md` - Rollback procedures, triggers

**Runbook Content:**
1. Prerequisites and dependencies
2. Step-by-step procedures
3. Verification commands
4. Rollback steps
5. Escalation paths

---

#### T8.4: Migration Guide Updates
**Effort:** 1 day | **PR:** `docs(P5): Update getting started and repo map`

**Deliverables:**
- Update `docs/GETTING_STARTED/REPO_MAP.md` - Add `apps/web_console_ng/`
- Update `docs/GETTING_STARTED/PROJECT_STATUS.md` - Mark P5 complete
- Update `docs/INDEX.md` - Add new documentation links
- Update `CLAUDE.md` - Add NiceGUI-specific guidance for AI agents
- Archive/deprecate Streamlit-specific docs

---

## Migration Checklist

### Phase 1: Foundation
- [ ] Create `apps/web_console_ng/` directory structure
- [ ] Add NiceGUI and httpx to requirements
- [ ] Implement `AsyncTradingClient`
- [ ] Port auth middleware from Streamlit helpers
- [ ] Configure session storage

### Phase 2: Layout & Navigation
- [ ] Create main layout wrapper
- [ ] Implement navigation sidebar
- [ ] Add global status indicators
- [ ] Port login page and all 4 auth flows
- [ ] Session security hardening (cookie flags, Origin checks)

### Phase 2.5: HA/Scaling Architecture
- [ ] nginx sticky session configuration
- [ ] Redis Sentinel/Cluster setup
- [ ] WebSocket fan-out via Redis Pub/Sub
- [ ] State rehydration on WS failover
- [ ] Per-client task cleanup on disconnect
- [ ] Connection recovery testing
- [ ] Per-instance resource limits

### Phase 2.6: Observability & Alerting
- [ ] Prometheus metrics for WS, auth, latency
- [ ] Grafana dashboard for NiceGUI
- [ ] Alert rules configured
- [ ] SLOs and error budgets defined
- [ ] Incident response runbook

### Parallel Run: Session Isolation
- [ ] Separate cookie names (streamlit_session vs nicegui_session)
- [ ] Separate OAuth callback URLs
- [ ] Coordinated logout across both apps
- [ ] Redis key namespacing (st_session vs ng_session)

### Phase 3: Dashboard
- [ ] Metric cards with real-time updates
- [ ] Positions AG Grid table
- [ ] Orders table with cancel buttons
- [ ] Activity feed

### Phase 4: Manual Controls
- [ ] Order entry form with dialog confirmation
- [ ] Kill switch management
- [ ] Position close/flatten controls
- [ ] Two-factor confirmation for destructive actions

### Phase 5: Charts
- [ ] Equity curve and P&L charts
- [ ] Risk analytics charts
- [ ] Factor exposure heatmap

### Phase 6: Remaining Pages
- [ ] Circuit breaker dashboard
- [ ] System health monitor
- [ ] Backtest manager
- [ ] Admin dashboard
- [ ] Alerts configuration
- [ ] Audit log viewer
- [ ] Data management pages

### Phase 7: Production
- [ ] Dockerfile and deployment config
- [ ] Test suite migration
- [ ] CSP configuration
- [ ] Cutover and cleanup

### Phase 8: Documentation
- [ ] ADR-0031 NiceGUI migration
- [ ] Concept documentation (architecture, auth, realtime, components)
- [ ] Operational runbooks (deployment, troubleshooting, performance, rollback)
- [ ] Update REPO_MAP, PROJECT_STATUS, INDEX.md
- [ ] Archive Streamlit-specific documentation

---

## Effort Summary

**Note:** Includes Phase 8 (Documentation) and realistic estimates.

| Phase | Original | Revised | Priority | Description |
|-------|----------|---------|----------|-------------|
| Phase 1 | 5-7 days | 9-12 days | P0 | Foundation + Auth + WebSocket Recovery |
| Phase 2 | 4-6 days | 8-10 days | P0 | Layout + All 4 Auth Flows |
| Phase 2.5 | - | 2-3 days | P0 | HA/Scaling Architecture (WS, Sessions) |
| Phase 2.6 | - | 2 days | P0 | Observability & Alerting |
| Phase 3 | 6-8 days | 8-12 days | P0 | Real-Time Dashboard + Push Architecture |
| Phase 3.5 | - | 3-4 days | P0 | Security & Performance Validation Gate |
| Phase 4 | 5-7 days | 5-7 days | P0 | Manual Trading Controls |
| Phase 5 | 4-6 days | 4-6 days | P1 | Charts & Analytics |
| Phase 6 | 15-20 days | 18-25 days | P1 | Remaining Pages + Data Grids |
| Phase 7 | 4-6 days | 6-8 days | P0 | Infrastructure + Load Testing + Cutover |
| Phase 8 | - | 5-7 days | P1 | Documentation (ADR, Concepts, Runbooks) |

**Revised Total Effort:** 70-96 days (sequential)
**With Parallel Execution:** ~50-68 days (2 developers)
**Recommended Buffer:** +20% for unknowns = 84-115 days sequential

**Why the increase from original?**
1. Auth complexity (23 modules) - was severely underestimated
2. Server-side session architecture - not originally scoped
3. Push-based real-time updates - polling insufficient
4. Performance/security validation gate - mandatory
5. Load testing and failure mode UX - not originally included
6. Comprehensive documentation phase - ensures maintainability

---

## Risk Assessment

**Note:** Comprehensive risk assessment for trading platform migration.

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **Session management edge cases** | Medium | High | Server-side session store, extensive testing, parallel run |
| **WebSocket stability** | Medium | High | Reconnection logic, state recovery, connection monitor |
| **Server memory growth** | Medium | Medium | Per-user memory limits, monitoring, garbage collection |
| **Auth flow compatibility** | Medium | High | Port ALL 4 auth types, dedicated test suite per flow |
| **Idempotency regression** | Low | Critical | Preserve existing hash scheme, NO nonce in client_order_id |
| **Push update backpressure** | Medium | Medium | Debounce (10/sec), drop oldest on queue overflow |
| **AG Grid licensing/perf** | Low | Medium | Validate community edition, test with 500+ rows |
| **OAuth redirect URI overlap** | Medium | Low | Separate callback URLs for legacy/new during parallel run |
| **Reverse proxy WebSocket** | Medium | Medium | Explicit nginx config for WS upgrade, test with TLS termination |
| **Browser WS connection limits** | Medium | Low | ~6 connections per domain; warn users about multiple tabs |
| **NiceGUI framework maturity** | Medium | Medium | Monitor upstream issues, prepare to "own" fixes if needed |
| **Browser compatibility** | Low | Low | NiceGUI uses standard web tech |
| **Learning curve** | Medium | Low | Team ramp-up, documentation |

### Critical Risk: Rollback Plan

If NiceGUI migration fails in production:
1. **Immediate:** Route all traffic to Streamlit via nginx
2. **Investigation:** Identify root cause, capture logs
3. **Fix forward** preferred over rollback if possible
4. **Rollback trigger:** >5% error rate OR >2s latency p95 for >5min

---

## Success Metrics

| Metric | Target | Stretch Goal | Validation Method |
|--------|--------|--------------|-------------------|
| **Page load (cold)** | < 500ms | < 300ms | Playwright timing |
| **Page load (warm)** | < 150ms | < 100ms | Playwright timing |
| **P&L push update** | < 50ms | < 30ms | Backend → UI timing |
| **Position grid update** | < 100ms | < 50ms | 100 rows measurement |
| **UI response (button)** | < 30ms | < 20ms | User interaction timing |
| **Concurrent users** | 100 stable | 200 | k6 load test |
| **WebSocket stability** | 99.95% | 99.99% | 4hr soak test |
| **Memory per user** | < 25MB | < 15MB | Monitoring under load |
| **P99 latency** | < 300ms | < 200ms | APM monitoring |
| **Order E2E latency** | < 200ms | < 100ms | Click to API response |
| **Test coverage** | > 90% | > 95% | pytest-cov report |
| **Auth flow parity** | 100% | - | All 4 auth types tested |
| **Feature parity** | 100% | - | Checklist vs Streamlit |
| **Security regressions** | 0 findings | - | Security review + pen test |

---

## Dependencies

### External Dependencies
- NiceGUI 2.x
- httpx (async HTTP)
- AG Grid (via NiceGUI)
- Plotly (via NiceGUI)

### Internal Dependencies
- `libs/` service layer (unchanged)
- Redis for session storage
- Postgres for audit logs
- Execution Gateway API

---

## ADR Requirement

This migration requires **ADR-0031: Web Console Migration to NiceGUI** covering:
- Decision rationale (Streamlit limitations)
- Tech stack selection (why NiceGUI over alternatives)
- Migration strategy (parallel run vs big bang)
- Security considerations (auth flow porting)
- Rollback plan

---

## Related Documents

- [P4_PLANNING_DONE.md](./P4_PLANNING_DONE.md) - Current web console features
- [ADR-0018-web-console-mtls-authentication.md](../../ADRs/0018-web-console-mtls-authentication.md) - Auth architecture
- [ADR-015-auth0-idp-selection.md](../../ADRs/ADR-015-auth0-idp-selection.md) - OAuth2 provider
- [docs/RUNBOOKS/ops.md](../../RUNBOOKS/ops.md) - Operational procedures

---

**Last Updated:** 2025-12-30
**Status:** Draft - v3 (Pending Review)
**Author:** AI Assistant (Claude Code)

### Next Steps

1. **Plan Review:** Request Gemini and Codex planning reviews
2. **ADR Creation:** Create ADR-0031 for architectural approval
3. **Implementation:** Begin Phase 1 after all approvals

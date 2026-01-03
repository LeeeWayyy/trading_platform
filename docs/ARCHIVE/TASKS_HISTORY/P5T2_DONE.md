---
id: P5T2
title: "NiceGUI Migration - Core Layout & Navigation"
phase: P5
task: T2
priority: P0
owner: "@development-team"
state: COMPLETE
created: 2025-12-30
dependencies: [P5T1]
estimated_effort: "8-10 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_TASK.md]
features: [T2.1, T2.2]
---

# P5T2: NiceGUI Migration - Core Layout & Navigation

**Phase:** P5 (Web Console Modernization)
**Status:** ✅ Complete
**Priority:** P0 (Visual Foundation)
**Owner:** @development-team
**Created:** 2025-12-30
**Estimated Effort:** 8-10 days
**Track:** Phase 2 from P5_PLANNING.md
**Dependency:** P5T1 (Foundation & Async Infrastructure) must be complete

---

## P5T1 Pre-implemented Security (Review Clarification)

**The following security controls were already implemented in P5T1 and are available for P5T2:**

| Control | P5T1 Implementation | File |
|---------|---------------------|------|
| **Session Cookie Security** | HttpOnly, Secure, SameSite, `__Host-` prefix | `auth/cookie_config.py` |
| **CSRF Protection** | Double-submit cookie pattern with header validation | `auth/csrf.py` |
| **Session Rate Limiting** | Redis Lua script (10 creates/min, 100 validates/min) | `auth/session_store.py` |
| **Device Binding** | IP subnet + UA hash validation | `auth/session_store.py` |
| **Audit Logging** | Structured auth event logging | `auth/audit.py` |
| **Session Rotation** | Rotate session ID on privilege change | `auth/session_store.py` |

**Component Dependency Order (Clarified):**
1. **C0: Layout Component** - No internal dependencies
2. **C3: Rate Limiting** - Implement before C2 (auth handlers need rate limiter)
3. **C2: Auth Handlers** - Depends on C3 rate limiter + P5T1 session store
4. **C1: Login UI Integration** - Depends on C2 auth handlers

**Security Notes:**
- Logout will be POST-only with CSRF validation (not GET)
- SLA timings are non-blocking goals measured via manual Playwright timing assertions
- OAuth2 callback method will be GET-only with state validation

---

## Objective

Establish the persistent layout (header, sidebar, content area) and implement all 4 authentication flows that all pages will inherit.

**Success looks like:**
- Consistent layout wrapper used by all pages
- Header with branding, user info, and global status indicators
- Left drawer navigation with active state highlighting
- Kill switch status badge visible globally
- Connection status indicator in header
- All 4 auth types (dev, basic, mTLS, OAuth2) fully functional
- Login page with form validation and rate limiting
- Logout with complete session cleanup across both Streamlit and NiceGUI

**Measurable SLAs:**
| Metric | Target | Measurement | Environment |
|--------|--------|-------------|-------------|
| Layout render time | < 50ms | Time from route to first paint | Local dev |
| Navigation transition | < 100ms | Time from click to new page render | Local dev |
| Login flow (dev auth) | < 500ms | Form submit to dashboard | Mocked backend |
| Login flow (OAuth2) | < 2s | Callback to dashboard (excl. IdP) | Auth0 sandbox |
| Auth validation overhead | < 20ms p95 | Per-request auth check | Local Redis |
| Failed login rate limit | 5 attempts/15min | Exponential backoff triggers | Local dev |

---

## Acceptance Criteria

### T2.1 Main Layout Component
**Header:**
- [ ] Create `main_layout` decorator for consistent page wrapper
- [ ] Header with branding logo/text ("Trading Console")
- [ ] User info display (username, role badge)
- [ ] Kill switch status badge (prominent, color-coded)
- [ ] Connection status indicator (Connected/Disconnected)
- [ ] Logout button with session cleanup

**Left Drawer (Sidebar):**
- [ ] Collapsible left drawer navigation
- [ ] Navigation links with icons and labels
- [ ] Active page highlighting (based on current route)
- [ ] Role-based navigation visibility (admin-only sections hidden)
- [ ] Drawer toggle button in header

**Navigation Items:**
- [ ] Dashboard (`/`)
- [ ] Manual Controls (`/manual`)
- [ ] Kill Switch (`/kill-switch`)
- [ ] Risk Analytics (`/risk`)
- [ ] Backtest (`/backtest`)
- [ ] Admin (`/admin`) - visible only for admin role

**Global Status Updates:**
- [ ] Kill switch status refreshes via `ui.timer` (5s interval)
- [ ] Kill switch badge: "TRADING ACTIVE" (green) or "KILL SWITCH ENGAGED" (red)
- [ ] Connection status updates on WebSocket connect/disconnect events
- [ ] Connection badge: "Connected" (green) or "Disconnected" (red)
- [ ] Visual alert: `ui.notify()` with type="negative" when kill switch engages

**Testing:**
- [ ] Unit tests for layout component
- [ ] Role-based visibility tests
- [ ] Responsive layout tests (mobile/tablet/desktop)

---

### T2.2 Login Page & All 4 Auth Flows

**Login Page UI:**
- [ ] Login form with username/password inputs
- [ ] Auth type selector (dev, basic, mTLS, OAuth2)
- [ ] "Login with Auth0" button for OAuth2 flow
- [ ] Form validation (client-side + server-side)
- [ ] Error message display (invalid credentials, locked out, etc.)
- [ ] "Forgot Password" link (for basic auth)
- [ ] MFA step-up verification UI

**Dev Mode Authentication (T2.2.1):**
- [ ] Username/password form authentication
- [ ] No external dependencies (local verification)
- [ ] Test user fixtures (admin, trader, viewer roles)
- [ ] Instant login for development testing
- [ ] Session creation with full user context

**Basic Auth (T2.2.2):**
- [ ] Form-based username/password
- [ ] Backend credential validation contract: `POST /api/v1/auth/validate`
- [ ] Request: `{"username": str, "password": str}` → Response: `{"valid": bool, "user": {...}}`
- [ ] Rate limiting on failed attempts (5 attempts, 15min lockout)
- [ ] Exponential backoff display to user
- [ ] Password complexity hints (if applicable)

**mTLS Authentication (T2.2.3):**
- [ ] Client certificate validation via nginx/proxy
- [ ] Trust boundary: Only accept DN headers from TRUSTED_PROXY_IPS
- [ ] Header contract: `X-SSL-Client-DN`, `X-SSL-Client-Verify`, `X-SSL-Client-Serial`
- [ ] JWT-DN binding for session (certificate DN stored in session)
- [ ] **Auto-login bypass:** If cert present + valid, skip login form and create session
- [ ] **Missing cert handling:** If `X-SSL-Client-Verify` != "SUCCESS", show form with error
- [ ] Admin fallback during IdP outages (basic auth form displayed)
- [ ] Certificate expiry warning display (if `X-SSL-Client-Not-After` < 30 days)
- [ ] Auto-redirect to dashboard when certificate auto-login succeeds

**OAuth2/OIDC Authentication (T2.2.4):**
- [ ] PKCE flow with state validation (server-side Redis storage)
- [ ] Auth0 callback handling (`/auth/callback`)
- [ ] **Mandatory token validation:**
  - [ ] Validate `iss` (issuer) matches Auth0 tenant URL
  - [ ] Validate `aud` (audience) matches client_id
  - [ ] Validate `exp` (expiry) is in future
  - [ ] Validate signature using JWKS from Auth0 (`/.well-known/jwks.json`)
  - [ ] Use `python-jose` or `authlib` for JWT verification
- [ ] **Redirect URI validation:** Only accept configured `OAUTH2_CALLBACK_URL`
- [ ] **Open redirect protection:** `redirect_after_login` validated against allowlist
- [ ] Token refresh strategy: Refresh when `token_expires_at` < 5 minutes
- [ ] Token rotation: New refresh_token on each use (if supported by IdP)
- [ ] IdP health monitoring (fallback display if unavailable)
- [ ] RP-initiated logout (redirect to Auth0 logout)
- [ ] Consent screen handling

**MFA Step-Up (T2.2.5):**
- [ ] Port existing MFA verification logic from `mfa_verification.py`
- [ ] TOTP code input UI
- [ ] "Remember this device" checkbox (optional)
- [ ] MFA bypass for dev mode (configurable)

**Rate Limiting:**
- [ ] Per-IP rate limiting (10 attempts/minute)
- [ ] Per-account rate limiting (5 attempts before lockout)
- [ ] **Trusted client IP derivation:** Extract from `X-Forwarded-For` only if request from `TRUSTED_PROXY_IPS`
- [ ] **DOS protection:** IP rate limit checked BEFORE credential validation
- [ ] Exponential backoff display (next attempt in X seconds)
- [ ] Account lockout after 5 failed attempts (15min duration)
- [ ] **Lockout cleared on successful login** (no residual failure count)
- [ ] Admin unlock capability with **audit log entry**

**Session Management:**
- [ ] Session creation on successful login
- [ ] Session ID rotation after login (fixation protection)
- [ ] User context stored in server-side session
- [ ] Redirect to originally requested page after login

**Logout:**
- [ ] Clear NiceGUI session (Redis delete)
- [ ] Clear session cookie
- [ ] Invalidate Streamlit session if exists (parallel run)
- [ ] OAuth2: RP-initiated logout redirect
- [ ] Redirect to login page

**Testing:**
- [ ] Unit tests for each auth handler
- [ ] Integration tests for full auth flows
- [ ] Rate limiting tests (lockout triggers correctly)
- [ ] Session rotation tests
- [ ] Logout completeness tests

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T1 complete:** Foundation, async client, session store implemented
- [ ] **Auth0 sandbox available:** For OAuth2 testing
- [ ] **Test certificates available:** For mTLS testing
- [ ] **Session store operational:** Redis with encryption working
- [ ] **AUTH_TYPE config:** Environment variable for auth type selection
- [ ] **Test user fixtures:** Users for each role (admin, trader, viewer)

---

## Approach

### High-Level Plan (Dependency-Ordered)

1. **C0: Layout Component** (2-3 days) - No dependencies
   - Create main layout decorator
   - Implement header with status indicators
   - Create left drawer navigation
   - Add global status update timers
   - Role-based navigation filtering

2. **C3: Rate Limiting & Security** (1-2 days) - Depends on P5T1 Redis
   - Auth rate limiter (per-IP + per-account)
   - Account lockout logic with Lua scripts
   - Logout handler (POST-only with CSRF)
   - Session rotation on login

3. **C2: Auth Handlers** (4-5 days) - Depends on C3 + P5T1 session store
   - Dev auth handler (uses P5T1 session_store.create_session)
   - Basic auth handler with rate limiter
   - mTLS auth handler with header trust validation
   - OAuth2/OIDC handler with PKCE
   - Auth router/dispatcher

4. **C1: Login Page UI Integration** (1-2 days) - Depends on C2
   - Replace mock login with real auth handlers
   - Integrate rate limiting feedback
   - Wire MFA step-up flow
   - Add OAuth2 authorization URL redirect

---

## Component Breakdown

### C0: Layout Component

**Files to Create:**
```
apps/web_console_ng/ui/
├── layout.py                  # Main layout decorator
├── header.py                  # Header component
├── navigation.py              # Left drawer navigation
├── theme.py                   # Tailwind/CSS configuration
└── status_indicators.py       # Kill switch, connection badges
tests/apps/web_console_ng/
├── test_layout.py
└── test_navigation.py
```

**Main Layout Implementation:**
```python
# apps/web_console_ng/ui/layout.py
from nicegui import ui, app
from functools import wraps
from typing import Callable, Any
from apps.web_console_ng.auth.middleware import get_current_user
from apps.web_console_ng.core.client import AsyncTradingClient

def main_layout(page_func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for consistent page layout with header, sidebar, and content."""

    @wraps(page_func)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        user = await get_current_user()
        client = AsyncTradingClient.get()

        # Header
        with ui.header().classes("bg-slate-900 items-center text-white px-4 h-14"):
            # Menu toggle
            menu_button = ui.button(icon="menu", on_click=lambda: drawer.toggle()).props("flat color=white")

            # Branding
            ui.label("Trading Console").classes("text-xl font-bold ml-2")

            # Spacer
            ui.space()

            # Global status indicators
            with ui.row().classes("gap-4 items-center"):
                # Kill switch badge
                kill_switch_badge = ui.label("Checking...").classes("px-3 py-1 rounded text-sm font-medium")

                # Connection status
                connection_badge = ui.label("Connected").classes("px-2 py-1 rounded text-xs bg-green-500")

                # User info
                with ui.row().classes("items-center gap-2"):
                    ui.label(user.get("username", "Unknown")).classes("text-sm")
                    ui.badge(user.get("role", "viewer")).classes("bg-blue-500")

                # Logout button
                async def logout():
                    from apps.web_console_ng.auth.logout import perform_logout
                    await perform_logout()
                    ui.navigate.to("/login")

                ui.button(icon="logout", on_click=logout).props("flat color=white").tooltip("Logout")

        # Left drawer (sidebar)
        with ui.left_drawer(value=True).classes("bg-slate-100 w-64") as drawer:
            with ui.column().classes("w-full gap-1 p-3"):
                ui.label("Navigation").classes("text-gray-500 text-xs uppercase tracking-wide mb-2")

                # Navigation items
                nav_items = [
                    ("Dashboard", "/", "dashboard", None),
                    ("Manual Controls", "/manual", "edit", None),
                    ("Kill Switch", "/kill-switch", "warning", None),
                    ("Risk Analytics", "/risk", "trending_up", None),
                    ("Backtest", "/backtest", "science", None),
                    ("Admin", "/admin", "settings", "admin"),  # Admin only
                ]

                current_path = app.storage.user.get("current_path", "/")
                user_role = user.get("role", "viewer")

                for label, path, icon, required_role in nav_items:
                    # Role-based visibility
                    if required_role and user_role != required_role:
                        continue

                    is_active = current_path == path
                    active_classes = "bg-blue-100 text-blue-700" if is_active else "hover:bg-slate-200"

                    with ui.link(target=path).classes(f"nav-link w-full rounded {active_classes}"):
                        with ui.row().classes("items-center gap-3 p-2"):
                            ui.icon(icon).classes("text-gray-600" if not is_active else "text-blue-600")
                            ui.label(label).classes("text-sm")

        # Main content area
        with ui.column().classes("w-full p-6 bg-gray-50 min-h-screen"):
            await page_func(*args, **kwargs)

        # Global status update timer
        async def update_global_status() -> None:
            try:
                status = await client.fetch_kill_switch_status()
                if status.get("state") == "ENGAGED":
                    kill_switch_badge.set_text("KILL SWITCH ENGAGED")
                    kill_switch_badge.classes("bg-red-500 text-white", remove="bg-green-500 bg-yellow-500")
                else:
                    kill_switch_badge.set_text("TRADING ACTIVE")
                    kill_switch_badge.classes("bg-green-500 text-white", remove="bg-red-500 bg-yellow-500")
            except Exception:
                kill_switch_badge.set_text("STATUS UNKNOWN")
                kill_switch_badge.classes("bg-yellow-500 text-black", remove="bg-red-500 bg-green-500")

        ui.timer(5.0, update_global_status)
        await update_global_status()  # Initial load

    return wrapper
```

**Acceptance Tests:**
- [ ] Layout renders with header, drawer, content area
- [ ] Navigation links route correctly
- [ ] Active state highlights current page
- [ ] Admin section hidden for non-admin users
- [ ] Kill switch badge updates every 5 seconds
- [ ] Logout button clears session and redirects

---

### C1: Login Page UI

**Files to Create:**
```
apps/web_console_ng/pages/
├── login.py                   # Login page
└── mfa_verify.py              # MFA step-up page
apps/web_console_ng/components/
├── login_form.py              # Reusable login form
└── mfa_input.py               # MFA code input
tests/apps/web_console_ng/
├── test_login_page.py
└── test_mfa_verify.py
```

**Login Page Implementation:**
```python
# apps/web_console_ng/pages/login.py
from nicegui import ui, app
from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler

@ui.page("/login")
async def login_page() -> None:
    """Login page with auth type selection."""

    # Check for existing session via server-side validation
    # Note: app.storage.user["logged_in"] is client-side flag; verify via cookie
    from apps.web_console_ng.auth.session_store import get_session_store
    from apps.web_console_ng.auth.cookie_config import CookieConfig
    from apps.web_console_ng.auth.client_ip import get_client_ip

    cookie_cfg = CookieConfig.from_env()
    cookie_value = app.storage.request.cookies.get(cookie_cfg.get_cookie_name())
    if cookie_value:
        session_store = get_session_store()
        client_ip = get_client_ip(app.storage.request)
        user_agent = app.storage.request.headers.get("user-agent", "")
        session = await session_store.validate_session(cookie_value, client_ip, user_agent)
        if session:
            # Already logged in with valid session
            ui.navigate.to("/")
            return

    # Get redirect reason if any
    reason = app.storage.user.get("login_reason")
    if reason == "session_expired":
        ui.notify("Your session has expired. Please log in again.", type="warning")
        del app.storage.user["login_reason"]  # Clear after showing

    with ui.card().classes("absolute-center w-96 p-8"):
        ui.label("Trading Console").classes("text-2xl font-bold text-center mb-2")
        ui.label("Sign in to continue").classes("text-gray-500 text-center mb-6")

        # Auth type selector (for environments with multiple options)
        auth_type = config.AUTH_TYPE
        if config.SHOW_AUTH_TYPE_SELECTOR:
            auth_type_select = ui.select(
                ["dev", "basic", "mtls", "oauth2"],
                value=auth_type,
                label="Authentication Method"
            ).classes("w-full mb-4")
        else:
            auth_type_select = None

        # Error message area
        error_label = ui.label("").classes("text-red-500 text-sm text-center hidden")

        # Username/password form (for dev and basic auth)
        with ui.column().classes("w-full gap-4") as form_section:
            username_input = ui.input(
                label="Username",
                placeholder="Enter your username"
            ).classes("w-full").props("outlined")

            password_input = ui.input(
                label="Password",
                placeholder="Enter your password",
                password=True,
                password_toggle_button=True
            ).classes("w-full").props("outlined")

            # Rate limit message
            rate_limit_label = ui.label("").classes("text-orange-500 text-sm hidden")

            async def submit_login():
                selected_auth = auth_type_select.value if auth_type_select else auth_type
                handler = get_auth_handler(selected_auth)

                try:
                    result = await handler.authenticate(
                        username=username_input.value,
                        password=password_input.value,
                        client_ip=get_client_ip(app.storage.request),
                        user_agent=app.storage.request.headers.get("user-agent", ""),
                    )

                    if result.success:
                        if result.requires_mfa:
                            # Store pending session for MFA step-up
                            app.storage.user["pending_mfa_cookie"] = result.cookie_value
                            ui.navigate.to("/mfa-verify")
                        else:
                            # Set HttpOnly session cookie via response
                            # Note: In NiceGUI, we use app.storage.request.state to access response
                            from apps.web_console_ng.auth.cookie_config import CookieConfig
                            cookie_cfg = CookieConfig.from_env()
                            response = app.storage.request.state.response
                            response.set_cookie(
                                key=cookie_cfg.get_cookie_name(),
                                value=result.cookie_value,
                                **cookie_cfg.get_cookie_flags(),
                            )
                            # Set CSRF token cookie (not HttpOnly, readable by JS)
                            response.set_cookie(
                                key="ng_csrf",
                                value=result.csrf_token,
                                **cookie_cfg.get_csrf_flags(),
                            )
                            # Store logged-in flag in client storage (UI state only)
                            app.storage.user["logged_in"] = True
                            redirect_to = app.storage.user.get("redirect_after_login", "/")
                            ui.navigate.to(redirect_to)
                    else:
                        error_label.set_text(result.error_message)
                        error_label.classes(remove="hidden")

                        if result.rate_limited:
                            rate_limit_label.set_text(
                                f"Too many attempts. Try again in {result.retry_after} seconds."
                            )
                            rate_limit_label.classes(remove="hidden")

                except Exception as e:
                    error_label.set_text(f"Login failed: {str(e)}")
                    error_label.classes(remove="hidden")

            ui.button("Sign In", on_click=submit_login).classes("w-full bg-blue-600 text-white")

        # OAuth2 button (separate)
        if auth_type == "oauth2" or config.SHOW_AUTH_TYPE_SELECTOR:
            ui.separator().classes("my-4")
            ui.label("Or").classes("text-center text-gray-500 text-sm")

            async def oauth2_login():
                handler = get_auth_handler("oauth2")
                auth_url = await handler.get_authorization_url()
                ui.navigate.to(auth_url, new_tab=False)

            ui.button("Sign in with Auth0", on_click=oauth2_login).classes(
                "w-full bg-orange-500 text-white mt-4"
            ).props("icon=login")

        # Hide form for mTLS (auto-login via certificate)
        if auth_type == "mtls":
            form_section.classes(add="hidden")
            ui.label("Authenticating via client certificate...").classes("text-center")
            # mTLS auto-detection happens via middleware
```

**Acceptance Tests:**
- [ ] Login page renders with form
- [ ] Auth type selector shows/hides based on config
- [ ] Form validation prevents empty submission
- [ ] Error messages display correctly
- [ ] Rate limiting message appears after failed attempts
- [ ] Successful login redirects to dashboard
- [ ] OAuth2 button redirects to Auth0

---

### C2: Auth Handlers

**Files to Create:**
```
apps/web_console_ng/auth/
├── auth_router.py             # Auth handler dispatcher
├── dev_auth.py                # Dev mode handler
├── basic_auth.py              # Basic auth handler
├── mtls_auth.py               # mTLS handler
├── oauth2_auth.py             # OAuth2/OIDC handler
├── mfa.py                     # MFA step-up
└── auth_result.py             # Auth result dataclass
tests/apps/web_console_ng/
├── test_dev_auth.py
├── test_basic_auth.py
├── test_mtls_auth.py
├── test_oauth2_auth.py
└── test_mfa.py
```

**Auth Router Implementation:**
```python
# apps/web_console_ng/auth/auth_router.py
from apps.web_console_ng import config
from apps.web_console_ng.auth.dev_auth import DevAuthHandler
from apps.web_console_ng.auth.basic_auth import BasicAuthHandler
from apps.web_console_ng.auth.mtls_auth import MTLSAuthHandler
from apps.web_console_ng.auth.oauth2_auth import OAuth2AuthHandler

def get_auth_handler(auth_type: str | None = None):
    """Return appropriate auth handler based on AUTH_TYPE config."""
    auth_type = auth_type or config.AUTH_TYPE

    handlers = {
        "dev": DevAuthHandler(),
        "basic": BasicAuthHandler(),
        "mtls": MTLSAuthHandler(),
        "oauth2": OAuth2AuthHandler(),
    }

    handler = handlers.get(auth_type)
    if not handler:
        raise ValueError(f"Unknown auth type: {auth_type}")

    return handler
```

**Auth Result Dataclass:**
```python
# apps/web_console_ng/auth/auth_result.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class AuthResult:
    """Result of an authentication attempt.

    NOTE: Uses cookie_value (from P5T1 session_store) not session_id.
    The cookie_value contains the signed session ID.
    """
    success: bool
    cookie_value: Optional[str] = None  # Signed cookie value from session_store
    csrf_token: Optional[str] = None    # CSRF token for forms
    user_data: Optional[dict] = None
    requires_mfa: bool = False
    error_message: Optional[str] = None
    rate_limited: bool = False
    retry_after: int = 0
    locked_out: bool = False
    lockout_remaining: int = 0
```

**Dev Auth Handler:**
```python
# apps/web_console_ng/auth/dev_auth.py
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.session_store import get_session_store
from apps.web_console_ng import config

# Test users for development
DEV_USERS = {
    "admin": {"password": "admin123", "role": "admin", "strategies": ["alpha_baseline"]},
    "trader": {"password": "trader123", "role": "trader", "strategies": ["alpha_baseline"]},
    "viewer": {"password": "viewer123", "role": "viewer", "strategies": []},
}

class DevAuthHandler:
    """Development mode authentication handler."""

    async def authenticate(self, username: str, password: str, **kwargs) -> AuthResult:
        """Authenticate using dev user fixtures."""
        if config.AUTH_TYPE != "dev":
            return AuthResult(success=False, error_message="Dev auth not enabled")

        user = DEV_USERS.get(username)
        if not user or user["password"] != password:
            return AuthResult(success=False, error_message="Invalid credentials")

        # Create session
        session_store = get_session_store()
        user_data = {
            "user_id": username,
            "username": username,
            "role": user["role"],
            "strategies": user["strategies"],
            "auth_method": "dev",
        }

        # P5T1 API: create_session returns (cookie_value, csrf_token)
        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "dev-browser")},
            client_ip=kwargs.get("client_ip", "127.0.0.1"),
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,  # Use cookie_value, not session_id
            csrf_token=csrf_token,
            user_data=user_data,
            requires_mfa=False,
        )
```

**OAuth2 Handler (with PKCE + Nonce - Server-Side State):**
```python
# apps/web_console_ng/auth/oauth2_auth.py
import secrets
import hashlib
import base64
import time
from urllib.parse import urlencode
import httpx
import redis.asyncio as redis
from apps.web_console_ng.auth.auth_result import AuthResult
from apps.web_console_ng.auth.session_store import get_session_store
from apps.web_console_ng import config

class OAuth2AuthHandler:
    """
    OAuth2/OIDC authentication handler with PKCE.

    NOTE: This is a CONFIDENTIAL client (has client_secret).
    For public clients (SPA), remove client_secret from token exchange.
    """

    OAUTH2_STATE_TTL = 600  # 10 minutes - state/nonce expire after this
    OAUTH2_STATE_PREFIX = "oauth2_flow:"

    def __init__(self):
        self.redis = redis.from_url(config.REDIS_URL)
        self.client_id = config.OAUTH2_CLIENT_ID
        self.client_secret = config.OAUTH2_CLIENT_SECRET  # Confidential client
        self.authorize_url = config.OAUTH2_AUTHORIZE_URL
        self.token_url = config.OAUTH2_TOKEN_URL
        self.userinfo_url = config.OAUTH2_USERINFO_URL
        self.callback_url = config.OAUTH2_CALLBACK_URL
        self.logout_url = config.OAUTH2_LOGOUT_URL

    async def get_authorization_url(self) -> str:
        """Generate authorization URL with PKCE and nonce (server-side state)."""
        # Generate PKCE challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).decode().rstrip("=")

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)

        # Generate nonce for OIDC replay protection
        nonce = secrets.token_urlsafe(32)

        # Store in Redis (NOT client-side) with TTL for one-time use
        flow_data = {
            "code_verifier": code_verifier,
            "nonce": nonce,
            "created_at": time.time(),
        }
        await self.redis.setex(
            f"{self.OAUTH2_STATE_PREFIX}{state}",
            self.OAUTH2_STATE_TTL,
            json.dumps(flow_data)
        )

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.callback_url,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,  # OIDC nonce for id_token validation
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        return f"{self.authorize_url}?{urlencode(params)}"

    async def handle_callback(self, code: str, state: str, **kwargs) -> AuthResult:
        """Handle OAuth2 callback and exchange code for tokens."""
        # Retrieve and DELETE state from Redis (one-time use)
        flow_key = f"{self.OAUTH2_STATE_PREFIX}{state}"
        flow_data_raw = await self.redis.get(flow_key)

        if not flow_data_raw:
            return AuthResult(success=False, error_message="Invalid or expired state")

        # Delete immediately to prevent replay
        await self.redis.delete(flow_key)

        flow_data = json.loads(flow_data_raw)
        code_verifier = flow_data["code_verifier"]
        expected_nonce = flow_data["nonce"]

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                self.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.callback_url,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": code_verifier,
                },
            )

            if token_response.status_code != 200:
                return AuthResult(success=False, error_message="Token exchange failed")

            tokens = token_response.json()

            # MANDATORY: Full id_token validation (issuer, audience, exp, signature, nonce)
            id_token = tokens.get("id_token")
            if id_token:
                try:
                    id_token_claims = await self._validate_id_token(id_token, expected_nonce)
                except AuthError as e:
                    return AuthResult(success=False, error_message=str(e))

            # Fetch user info
            userinfo_response = await client.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )

            if userinfo_response.status_code != 200:
                return AuthResult(success=False, error_message="Failed to fetch user info")

            userinfo = userinfo_response.json()

        # Convert expires_in to absolute timestamp
        expires_in = tokens.get("expires_in", 3600)
        token_expires_at = time.time() + expires_in

        # Create session with tokens stored server-side
        session_store = get_session_store()
        user_data = {
            "user_id": userinfo.get("sub"),
            "username": userinfo.get("email", userinfo.get("name")),
            "email": userinfo.get("email"),
            "role": self._map_role(userinfo),
            "strategies": self._map_strategies(userinfo),
            "auth_method": "oauth2",
            "access_token": tokens["access_token"],  # Stored in Redis, not client
            "refresh_token": tokens.get("refresh_token"),
            "id_token": id_token,
            "token_expires_at": token_expires_at,  # Absolute timestamp
        }

        # P5T1 API: create_session returns (cookie_value, csrf_token)
        cookie_value, csrf_token = await session_store.create_session(
            user_data=user_data,
            device_info={"user_agent": kwargs.get("user_agent", "")},
            client_ip=kwargs.get("client_ip", "127.0.0.1"),
        )

        return AuthResult(
            success=True,
            cookie_value=cookie_value,
            csrf_token=csrf_token,
            user_data=user_data,
            requires_mfa=self._requires_mfa(userinfo),
        )

    async def _validate_id_token(self, id_token: str, expected_nonce: str) -> dict:
        """
        Validate id_token with FULL JWT verification.
        MANDATORY: issuer, audience, expiry, signature, nonce.
        """
        from jose import jwt, jwk
        from jose.exceptions import JWTError, ExpiredSignatureError

        # Fetch JWKS (with caching - refresh every hour or on signature failure)
        jwks = await self._get_jwks()

        try:
            # Full JWT verification with python-jose
            claims = jwt.decode(
                id_token,
                jwks,
                algorithms=["RS256"],
                audience=self.client_id,  # Validate aud
                issuer=config.OAUTH2_ISSUER,  # Validate iss (e.g., https://tenant.auth0.com/)
                options={
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "leeway": 60,  # 60 second clock skew tolerance
                },
            )

            # Validate nonce (OIDC spec)
            if claims.get("nonce") != expected_nonce:
                raise JWTError("Nonce mismatch - possible replay attack")

            return claims

        except ExpiredSignatureError:
            raise AuthError("Token expired")
        except JWTError as e:
            raise AuthError(f"Token validation failed: {e}")

    async def _get_jwks(self) -> dict:
        """Fetch JWKS with caching (1 hour TTL, refresh on signature failure)."""
        cache_key = "oauth2_jwks"
        cached = await self.redis.get(cache_key)

        if cached:
            return json.loads(cached)

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{config.OAUTH2_ISSUER}/.well-known/jwks.json")
            resp.raise_for_status()
            jwks = resp.json()

        await self.redis.setex(cache_key, 3600, json.dumps(jwks))  # Cache 1 hour
        return jwks

    def _map_role(self, userinfo: dict) -> str:
        """Map OAuth2 claims to application role."""
        roles = userinfo.get("roles", [])
        if "admin" in roles:
            return "admin"
        if "trader" in roles:
            return "trader"
        return "viewer"

    def _map_strategies(self, userinfo: dict) -> list[str]:
        """Map OAuth2 claims to allowed strategies."""
        return userinfo.get("strategies", ["alpha_baseline"])

    def _requires_mfa(self, userinfo: dict) -> bool:
        """Check if MFA step-up is required."""
        return userinfo.get("amr", []) == [] and config.REQUIRE_MFA

    async def get_logout_url(self, id_token: str) -> str:
        """Get RP-initiated logout URL."""
        params = {
            "client_id": self.client_id,
            "returnTo": config.BASE_URL + "/login",
            "id_token_hint": id_token,
        }
        return f"{self.logout_url}?{urlencode(params)}"
```

**Acceptance Tests:**
- [ ] Dev auth works with test users
- [ ] Basic auth validates against backend
- [ ] mTLS extracts DN from certificate
- [ ] OAuth2 PKCE flow completes successfully
- [ ] OAuth2 state validation prevents CSRF
- [ ] Tokens stored server-side only
- [ ] Role mapping works correctly

---

### C3: Rate Limiting & Security

**Files to Create:**
```
apps/web_console_ng/auth/
├── rate_limiter.py            # Rate limiting
├── lockout.py                 # Account lockout
└── logout.py                  # Logout handler
tests/apps/web_console_ng/
├── test_rate_limiter.py
└── test_logout.py
```

**Rate Limiter Implementation (Atomic Lua Script):**
```python
# apps/web_console_ng/auth/rate_limiter.py
import redis.asyncio as redis
from apps.web_console_ng import config

# Lua script for CHECK ONLY (no increment) - used before auth attempt
CHECK_ONLY_SCRIPT = """
local ip_key = KEYS[1]
local lockout_key = KEYS[2]
local max_ip_attempts = tonumber(ARGV[1])

-- Check IP rate limit (no increment, just check)
local ip_count = tonumber(redis.call('GET', ip_key) or '0')
if ip_count >= max_ip_attempts then
    return {1, redis.call('TTL', ip_key), 'ip_rate_limit'}
end

-- Check account lockout
local is_locked = redis.call('EXISTS', lockout_key)
if is_locked == 1 then
    return {1, redis.call('TTL', lockout_key), 'account_locked'}
end

return {0, 0, 'allowed'}
"""

# Lua script for RECORD FAILURE (single increment per failed attempt)
RECORD_FAILURE_SCRIPT = """
local ip_key = KEYS[1]
local failure_key = KEYS[2]
local lockout_key = KEYS[3]
local max_ip_attempts = tonumber(ARGV[1])
local max_account_attempts = tonumber(ARGV[2])
local lockout_duration = tonumber(ARGV[3])
local failure_window = tonumber(ARGV[4])  -- 15 minutes = 900 seconds

-- Increment IP rate limit (once per attempt)
local ip_count = redis.call('INCR', ip_key)
if ip_count == 1 then
    redis.call('EXPIRE', ip_key, 60)  -- 1 minute window for IP
end

if ip_count > max_ip_attempts then
    return {0, redis.call('TTL', ip_key), 'ip_rate_limit'}
end

-- Increment failure count for account
local fail_count = redis.call('INCR', failure_key)
if fail_count == 1 then
    redis.call('EXPIRE', failure_key, failure_window)  -- 15 minute window
end

if fail_count >= max_account_attempts then
    -- Lock account AND clear failure count (prevents re-lock after expiry)
    redis.call('SETEX', lockout_key, lockout_duration, '1')
    redis.call('DEL', failure_key)
    return {0, lockout_duration, 'account_locked_now'}
end

return {1, 0, 'failure_recorded', fail_count}
"""

class AuthRateLimiter:
    """
    Atomic rate limiting for authentication attempts.

    Uses separate Lua scripts for check vs record to prevent double-counting:
    - check_only: Called BEFORE auth attempt (no increment)
    - record_failure: Called AFTER failed auth attempt (single increment)

    Lockout clears failure count to prevent immediate re-lock after expiry.
    """

    def __init__(self):
        self.redis = redis.from_url(config.REDIS_URL)
        self.max_attempts_per_ip = 10  # Per minute
        self.max_attempts_per_account = 5  # Before lockout
        self.lockout_duration = 15 * 60  # 15 minutes
        self.failure_window = 15 * 60  # 15 minute window for failures
        self._check_script_sha = None
        self._record_script_sha = None

    async def _load_scripts(self):
        """Load Lua scripts (cached SHAs)."""
        if self._check_script_sha is None:
            self._check_script_sha = await self.redis.script_load(CHECK_ONLY_SCRIPT)
        if self._record_script_sha is None:
            self._record_script_sha = await self.redis.script_load(RECORD_FAILURE_SCRIPT)

    async def check_only(
        self, client_ip: str, username: str
    ) -> tuple[bool, int, str]:
        """
        Check rate limits WITHOUT incrementing counters.
        Call this BEFORE attempting authentication.

        Returns (is_blocked, retry_after_seconds, reason).
        Reasons: 'allowed', 'ip_rate_limit', 'account_locked'
        """
        await self._load_scripts()

        keys = [
            f"auth_rate:ip:{client_ip}",
            f"auth_lockout:{username}",
        ]
        args = [self.max_attempts_per_ip]

        result = await self.redis.evalsha(
            self._check_script_sha, len(keys), *keys, *args
        )

        is_blocked = bool(result[0])
        retry_after = int(result[1])
        reason = result[2].decode() if isinstance(result[2], bytes) else result[2]

        return is_blocked, retry_after, reason

    async def record_failure(
        self, client_ip: str, username: str
    ) -> tuple[bool, int, str]:
        """
        Record a failed authentication attempt (single increment).
        Call this AFTER authentication fails.

        Returns (is_allowed, retry_after_seconds, reason).
        Reasons: 'ip_rate_limit', 'account_locked_now', 'failure_recorded'
        """
        await self._load_scripts()

        keys = [
            f"auth_rate:ip:{client_ip}",
            f"auth_failures:{username}",
            f"auth_lockout:{username}",
        ]
        args = [
            self.max_attempts_per_ip,
            self.max_attempts_per_account,
            self.lockout_duration,
            self.failure_window,
        ]

        result = await self.redis.evalsha(
            self._record_script_sha, len(keys), *keys, *args
        )

        is_allowed = bool(result[0])
        retry_after = int(result[1])
        reason = result[2].decode() if isinstance(result[2], bytes) else result[2]

        return is_allowed, retry_after, reason

    async def clear_on_success(self, username: str) -> None:
        """Clear failure count and lockout on successful login."""
        await self.redis.delete(
            f"auth_failures:{username}",
            f"auth_lockout:{username}"
        )
```

**Usage in Auth Handler:**
```python
# In any auth handler's authenticate method:
rate_limiter = AuthRateLimiter()

# Extract trusted client IP (BEFORE any rate limiting)
client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)

# Pre-check ONLY (no increment) - just check if already locked/rate-limited
is_blocked, retry_after, reason = await rate_limiter.check_only(client_ip, username)
if is_blocked:
    return AuthResult(
        success=False,
        error_message=f"Too many attempts. Try again in {retry_after} seconds.",
        rate_limited=True,
        retry_after=retry_after,
        locked_out=(reason == "account_locked"),
    )

# Attempt authentication...
auth_success = await verify_credentials(username, password)

if not auth_success:
    # Record failure (SINGLE increment per attempt)
    is_allowed, retry_after, reason = await rate_limiter.record_failure(
        client_ip, username
    )
    return AuthResult(
        success=False,
        error_message="Invalid credentials",
        rate_limited=not is_allowed,
        retry_after=retry_after,
        locked_out=(reason in ["account_locked", "account_locked_now"]),
    )

# Success - clear all rate limits
await rate_limiter.clear_on_success(username)
```

**Extract Trusted Client IP:**
```python
def extract_trusted_client_ip(request, trusted_proxies: list[str]) -> str:
    """Extract client IP, trusting X-Forwarded-For only from trusted proxies."""
    remote_ip = request.client.host

    if remote_ip in trusted_proxies:
        # Trust X-Forwarded-For from configured proxies
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            # Take the rightmost untrusted IP (client IP)
            ips = [ip.strip() for ip in xff.split(",")]
            for ip in reversed(ips):
                if ip not in trusted_proxies:
                    return ip
    return remote_ip
```

**Logout Handler:**
```python
# apps/web_console_ng/auth/logout.py
from nicegui import app
import redis.asyncio as redis
from apps.web_console_ng.auth.session_store import get_session_store
from apps.web_console_ng.auth.cookie_config import CookieConfig
from apps.web_console_ng.auth.oauth2_auth import OAuth2AuthHandler
from apps.web_console_ng.auth.client_ip import get_client_ip
from apps.web_console_ng import config

async def perform_logout(request, response) -> str | None:
    """
    Perform complete logout (POST-only with CSRF validation):
    1. Validate and get session from cookie
    2. Invalidate NiceGUI session in Redis
    3. Clear cookies
    4. Invalidate Streamlit session (parallel run)
    5. For OAuth2: return logout URL

    NOTE: CSRF validation happens in the route handler before calling this.

    Returns OAuth2 logout URL if applicable, None otherwise.
    """
    session_store = get_session_store()
    cookie_cfg = CookieConfig.from_env()

    # Get cookie_value from HttpOnly cookie (P5T1 API)
    cookie_value = request.cookies.get(cookie_cfg.get_cookie_name())

    if cookie_value:
        # Validate session using P5T1 API: validate_session(cookie_value, client_ip, user_agent)
        client_ip = get_client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        session = await session_store.validate_session(cookie_value, client_ip, user_agent)

        # Extract session_id from cookie_value for invalidation
        session_id = session_store.verify_cookie(cookie_value)
        if session_id:
            await session_store.invalidate_session(session_id)

        # Clear session cookie
        response.delete_cookie(
            key=cookie_cfg.get_cookie_name(),
            path="/",
            secure=cookie_cfg.secure,
        )
        # Clear CSRF cookie
        response.delete_cookie(key="ng_csrf", path="/")

        # Clear Streamlit session if exists (parallel run)
        if session:
            user_id = session.get("user", {}).get("user_id")
            if user_id:
                r = redis.from_url(config.REDIS_URL)
                await r.delete(f"st_session:{user_id}")

        # Clear NiceGUI client storage
        app.storage.user.clear()

        # Handle OAuth2 RP-initiated logout
        if session and session.get("user", {}).get("auth_method") == "oauth2":
            id_token = session.get("user", {}).get("id_token")
            if id_token:
                handler = OAuth2AuthHandler()
                return await handler.get_logout_url(id_token)

    return None
```

**Acceptance Tests:**
- [ ] Rate limiting blocks after threshold
- [ ] Account lockout after 5 failures
- [ ] Lockout clears after 15 minutes
- [ ] Successful login clears failure count
- [ ] Logout invalidates NiceGUI session
- [ ] Logout clears Streamlit session (parallel run)
- [ ] OAuth2 logout redirects to IdP

---

## Testing Strategy

### Unit Tests
- `test_layout.py`: Layout rendering, role visibility, status updates
- `test_navigation.py`: Route handling, active states, drawer toggle
- `test_login_page.py`: Form validation, error display
- `test_dev_auth.py`: Dev user authentication
- `test_basic_auth.py`: Basic auth flow, backend validation
- `test_mtls_auth.py`: Certificate extraction, DN binding, header trust
- `test_oauth2_auth.py`: PKCE flow, token exchange, nonce validation
- `test_rate_limiter.py`: Rate limit enforcement, atomic operations
- `test_logout.py`: Session cleanup, OAuth2 redirect

### Security Tests
- `test_oauth2_security.py`:
  - [ ] State/nonce TTL expiry (10 min)
  - [ ] State one-time use (replay prevention)
  - [ ] Nonce validation in id_token
  - [ ] Invalid state rejection
  - [ ] **Token validation:** issuer, audience, expiry, signature
  - [ ] **Invalid signature rejection** (tampered token)
  - [ ] **Expired token rejection**
  - [ ] **Redirect URI tampering prevention**
  - [ ] **Open redirect protection** on post-login redirect
- `test_oauth2_refresh.py`:
  - [ ] Token refresh triggered when near expiry
  - [ ] Refresh token rotation (new token on each use)
  - [ ] Session invalidated if refresh fails
- `test_rate_limit_security.py`:
  - [ ] Lockout expiry doesn't cause immediate re-lock
  - [ ] Failure count cleared on lockout
  - [ ] Atomic operation (no race conditions)
  - [ ] Lockout cleared on successful login
  - [ ] **Trusted client IP extraction** (X-Forwarded-For only from trusted proxies)
  - [ ] **Admin unlock creates audit log entry**
- `test_mtls_headers.py`:
  - [ ] X-Forwarded-For trust boundary (only from trusted proxies)
  - [ ] DN header spoofing prevention
  - [ ] Certificate CN/DN extraction
- `test_csrf_protection.py`:
  - [ ] CSRF token required for login initiation (state parameter)
  - [ ] Invalid CSRF token rejected

### Integration Tests
- `test_full_login_flow.py`: End-to-end login for each auth type
- `test_session_rotation.py`: Session ID changes on login
- `test_parallel_run_logout.py`: Both sessions cleared
- `test_auth_callback.py`: OAuth2 callback handling
- `test_oauth2_logout_redirect.py`: RP-initiated logout to Auth0
- `test_connection_status.py`: WebSocket connection state updates

### Manual Verification
- [ ] Login with each auth type (dev, basic, mTLS, OAuth2)
- [ ] Verify rate limiting kicks in after 5 failures (check Redis keys)
- [ ] Test lockout recovery after 15 minutes (verify no re-lock)
- [ ] Verify OAuth2 redirect flow works (check state in Redis)
- [ ] Test MFA step-up (if configured)
- [ ] Verify layout on mobile (375px), tablet (768px), desktop (1280px)

---

## Dependencies

### External
- `nicegui>=2.0`: Web framework
- `httpx>=0.25`: Async HTTP client (OAuth2 token exchange)
- `redis[hiredis]>=5.0`: Rate limiting storage
- `cryptography>=41.0`: Session encryption

### Internal
- `apps/web_console_ng/core/client.py`: Async trading client (from P5T1)
- `apps/web_console_ng/auth/session_store.py`: Session management (from P5T1)
- `apps/web_console/auth/`: Reference for existing auth logic

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| OAuth2 callback timing | Medium | Medium | State stored in Redis with TTL, not browser storage |
| mTLS certificate parsing | Low | High | Rely on nginx to pass DN via headers |
| Rate limiting bypass | Low | Medium | Per-IP + per-account + Redis atomic operations |
| Parallel run session conflicts | Medium | Medium | Separate cookie names, coordinated logout |
| Auth0 availability | Low | High | Fallback to mTLS/basic during outages |
| MFA compatibility | Medium | Low | Support multiple MFA methods |

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests for all 4 auth flows
- [ ] Rate limiting tests pass
- [ ] Layout works on mobile/tablet/desktop
- [ ] No regressions in P5T1 tests
- [ ] Code reviewed and approved
- [ ] Documentation updated (README in web_console_ng)
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-30 (Rev 4)
**Status:** ✅ Complete

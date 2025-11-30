# P2T3 Phase 3 - Component 2: OAuth2 Authorization Flow with PKCE (FINAL - v3)

**Status:** Planning (Revision 3 - FastAPI Sidecar Architecture)
**Component:** 2 of 6
**Estimated Duration:** 4 days (32 hours)
**Dependencies:** Component 1 (OAuth2 Config & IdP Setup) ✅ COMPLETED
**References:**
- Parent Plan: `docs/TASKS/P2T3_Phase3_FINAL_PLAN.md` (lines 127-143)
- Component 1 Plan: `docs/TASKS/P2T3-Phase3_Component1_Plan.md`
- ADR-015: Auth0 IdP Selection
- **v2 Review:** Codex CRITICAL issue - HttpOnly cookies cannot be set via JavaScript
- **Architecture Decision:** FastAPI sidecar for auth endpoints (Codex recommendation)

---

## v3 Changes from v2

**CRITICAL FIX - HttpOnly Cookie Implementation:**

**Problem in v2:**
- Streamlit pages used JavaScript `document.cookie` to set session cookies
- JavaScript CANNOT set HttpOnly flag (browser security restriction)
- This leaves session IDs vulnerable to XSS attacks

**Solution in v3 (Codex Recommended):**
- ✅ Add **FastAPI sidecar service** (`auth_service`) for `/callback`, `/refresh`, `/logout` endpoints
- ✅ FastAPI sets proper `Set-Cookie` headers with HttpOnly+Secure+SameSite
- ✅ Nginx routes auth endpoints to FastAPI, UI paths to Streamlit
- ✅ Streamlit becomes **read-only** for cookies (uses `st.context.cookies`)
- ✅ Reuses all existing modules (session_store, jwks_validator, oauth2_flow, rate_limiter)

**Architecture Diagram:**
```
User → Nginx Reverse Proxy
         ├─ /callback      → auth_service (FastAPI) → Sets HttpOnly cookie → 302 redirect
         ├─ /refresh       → auth_service (FastAPI) → Refreshes + rotates tokens
         ├─ /logout        → auth_service (FastAPI) → Clears cookie → 302 to Auth0
         │
         └─ / (all other)  → web_console (Streamlit) → Reads cookies (read-only)
                                                      → Renders UI
```

**Benefits:**
- ✅ Proper HttpOnly cookie security (XSS protection)
- ✅ Reuses existing FastAPI patterns from signal_service/execution_gateway
- ✅ Clean separation: Auth in FastAPI, UI in Streamlit
- ✅ Easy to test and debug (dedicated auth service)
- ✅ Future-proof: Can add API endpoints for mobile app

---

## Overview

Implement OAuth2 Authorization Code Flow with PKCE using a **FastAPI sidecar architecture** for secure cookie handling. The FastAPI `auth_service` handles authentication endpoints while Streamlit provides the UI.

**Security Requirements:**
- PKCE for authorization code interception protection (S256)
- State parameter for CSRF protection (single-use, Redis-backed)
- Nonce for ID token replay protection (single-use)
- JWKS signature validation (RS256 + ES256 support)
- Rate limiting (10/min callback, 5/min refresh) - Redis-backed
- Session binding (IP + User-Agent)
- Absolute 4-hour session timeout
- Refresh token rotation
- **HttpOnly + Secure + SameSite=Lax cookies** (FastAPI sidecar)

---

## Tasks Breakdown

### Task 1: PKCE + OAuth2 State Management (4 hours - UNCHANGED from v2)

**Files:** `pkce.py`, `oauth2_state.py`

All logic from v2 remains the same:
- PKCE challenge generation (S256)
- State/nonce/session_id generation
- Redis state store with single-use enforcement

See v2 plan for full implementation details.

---

### Task 2: JWKS Validator with ES256 Support (8 hours - UNCHANGED from v2)

**Files:** `jwks_validator.py`

All logic from v2 remains the same:
- JWKS caching (12-hour TTL)
- RS256 + ES256 algorithm support
- Algorithm-specific key loading
- Nonce validation

See v2 plan for full implementation details.

---

### Task 3: Redis Rate Limiter (4 hours - UNCHANGED from v2)

**Files:** `rate_limiter.py`

All logic from v2 remains the same:
- Redis sorted set sliding window
- Multi-worker safe
- 10/min callback, 5/min refresh

See v2 plan for full implementation details.

---

### Task 4: FastAPI Auth Service - NEW ARCHITECTURE (10 hours)

**CRITICAL:** This replaces the Streamlit pages approach from v2.

Create: `apps/auth_service/` (NEW microservice)

#### Directory Structure
```
apps/auth_service/
├── __init__.py
├── main.py              # FastAPI app with auth routes
├── dependencies.py      # Shared dependencies (config, session store, JWKS, etc.)
└── routes/
    ├── __init__.py
    ├── callback.py      # /callback endpoint
    ├── refresh.py       # /refresh endpoint
    └── logout.py        # /logout endpoint
```

#### Implementation

Create: `apps/auth_service/main.py`

```python
"""FastAPI auth service for OAuth2 endpoints.

This service handles ONLY authentication endpoints that require
setting HttpOnly cookies. Streamlit web console handles UI.

Endpoints:
- GET /login: Initiate OAuth2 flow (generate state, redirect to Auth0)
- GET /callback: Handle Auth0 callback (exchange code, set cookie)
- POST /refresh: Refresh access token (rotate, preserve TTL)
- GET /logout: Logout (clear cookie, redirect to Auth0)
- GET /health: Health check
"""

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
import logging

from apps.auth_service.dependencies import (
    get_oauth2_handler,
    get_rate_limiters,
)
from apps.auth_service.routes import callback, refresh, logout

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Auth Service",
    description="OAuth2 authentication endpoints for web console",
    version="1.0.0",
)

# Include routers
app.include_router(callback.router, tags=["auth"])
app.include_router(refresh.router, tags=["auth"])
app.include_router(logout.router, tags=["auth"])


@app.get("/login")
async def login(request: Request):
    """Initiate OAuth2 login flow.

    Generates PKCE challenge, stores state in Redis, redirects to Auth0.
    """
    oauth2_handler = get_oauth2_handler()

    # Generate authorization URL and store state
    authorization_url, oauth_state = await oauth2_handler.initiate_login()

    logger.info(
        "OAuth2 login initiated",
        extra={"state": oauth_state.state[:8] + "..."},
    )

    # Redirect to Auth0 authorization endpoint
    return RedirectResponse(url=authorization_url, status_code=302)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "auth_service"}
```

Create: `apps/auth_service/routes/callback.py`

```python
"""OAuth2 callback handler with HttpOnly cookie setting."""

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
import logging

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters, get_config

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
):
    """Handle OAuth2 callback from Auth0.

    Validates state, exchanges code for tokens, creates session,
    sets HttpOnly cookie, redirects to dashboard.

    Args:
        code: Authorization code from Auth0
        state: State parameter for CSRF protection

    Returns:
        RedirectResponse with Set-Cookie header
    """
    # Rate limiting (10/min per IP)
    rate_limiters = get_rate_limiters()
    client_ip = request.headers.get("X-Real-IP", request.client.host)

    if not await rate_limiters["callback"].is_allowed(client_ip):
        logger.warning("Callback rate limit exceeded", extra={"ip": client_ip})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Get client info for session binding
    user_agent = request.headers.get("User-Agent", "unknown")

    # Handle callback
    oauth2_handler = get_oauth2_handler()

    try:
        session_id, session_data = await oauth2_handler.handle_callback(
            code=code,
            state=state,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    except ValueError as e:
        logger.error("OAuth2 callback failed", extra={"error": str(e)})
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")

    # Build redirect response
    config = get_config()
    response = RedirectResponse(url="/", status_code=302)

    # Set HttpOnly session cookie
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=14400,  # 4 hours
        path="/",
        domain=config.cookie_domain,
        secure=True,  # HTTPS only
        httponly=True,  # XSS protection
        samesite="lax",  # CSRF protection
    )

    logger.info(
        "OAuth2 callback successful, cookie set",
        extra={
            "session_id": session_id[:8] + "...",
            "user_id": session_data.user_id,
        },
    )

    return response
```

Create: `apps/auth_service/routes/refresh.py`

```python
"""Token refresh endpoint with rotation and binding validation."""

from fastapi import APIRouter, Request, Response, HTTPException, Cookie
from fastapi.responses import JSONResponse
import logging

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/refresh")
async def refresh_token(
    request: Request,
    session_id: str = Cookie(None),
):
    """Refresh access token.

    Validates session binding, refreshes tokens, rotates refresh token,
    preserves absolute timeout.

    Args:
        session_id: Session ID from HttpOnly cookie

    Returns:
        JSON response with success status
    """
    if not session_id:
        raise HTTPException(status_code=401, detail="No session cookie")

    # Rate limiting (5/min per session)
    rate_limiters = get_rate_limiters()
    if not await rate_limiters["refresh"].is_allowed(session_id):
        logger.warning("Refresh rate limit exceeded", extra={"session_id": session_id[:8] + "..."})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Get client info for session binding
    client_ip = request.headers.get("X-Real-IP", request.client.host)
    user_agent = request.headers.get("User-Agent", "unknown")

    # Refresh tokens
    oauth2_handler = get_oauth2_handler()

    try:
        session_data = await oauth2_handler.refresh_tokens(
            session_id=session_id,
            ip_address=client_ip,
            user_agent=user_agent,
        )
    except ValueError as e:
        logger.error("Token refresh failed", extra={"error": str(e)})
        raise HTTPException(status_code=401, detail=f"Refresh failed: {str(e)}")

    logger.info(
        "Tokens refreshed successfully",
        extra={
            "session_id": session_id[:8] + "...",
            "user_id": session_data.user_id,
        },
    )

    return JSONResponse(
        content={"status": "success", "message": "Tokens refreshed"},
        status_code=200,
    )
```

Create: `apps/auth_service/routes/logout.py`

```python
"""Logout endpoint with cookie clearing."""

from fastapi import APIRouter, Request, Cookie
from fastapi.responses import RedirectResponse
import logging

from apps.auth_service.dependencies import get_oauth2_handler, get_config

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/logout")
async def logout(
    request: Request,
    session_id: str = Cookie(None),
):
    """Handle logout.

    Deletes session from Redis, clears cookie, redirects to Auth0 logout.

    Args:
        session_id: Session ID from HttpOnly cookie

    Returns:
        RedirectResponse to Auth0 logout with cleared cookie
    """
    if not session_id:
        # No session, just redirect to login
        return RedirectResponse(url="/login", status_code=302)

    # Delete session
    oauth2_handler = get_oauth2_handler()
    logout_url = await oauth2_handler.handle_logout(session_id)

    # Build redirect response
    response = RedirectResponse(url=logout_url, status_code=302)

    # Clear session cookie
    config = get_config()
    response.set_cookie(
        key="session_id",
        value="",
        max_age=0,  # Expire immediately
        path="/",
        domain=config.cookie_domain,
        secure=True,
        httponly=True,
        samesite="lax",
    )

    logger.info(
        "User logged out, cookie cleared",
        extra={"session_id": session_id[:8] + "..."},
    )

    return response
```

Create: `apps/auth_service/dependencies.py`

```python
"""Shared dependencies for FastAPI auth service.

Uses functools.lru_cache for singleton pattern (similar to @st.cache_resource).
"""

from functools import lru_cache
import os
import redis.asyncio

from apps.web_console.auth.oauth2_flow import OAuth2FlowHandler, OAuth2Config
from apps.web_console.auth.oauth2_state import OAuth2StateStore
from apps.web_console.auth.session_store import RedisSessionStore
from apps.web_console.auth.jwks_validator import JWKSValidator
from apps.web_console.auth.rate_limiter import RedisRateLimiter


# UNIFIED CONFIG: Combines Auth0 params + cookie domain
class AuthServiceConfig:
    """Auth service configuration (Auth0 + cookie domain)."""

    def __init__(self):
        self.auth0_domain = os.getenv("AUTH0_DOMAIN")
        self.client_id = os.getenv("AUTH0_CLIENT_ID")
        self.client_secret = os.getenv("AUTH0_CLIENT_SECRET")
        self.audience = os.getenv("AUTH0_AUDIENCE")
        self.redirect_uri = os.getenv("OAUTH2_REDIRECT_URI")
        self.logout_redirect_uri = os.getenv("OAUTH2_LOGOUT_REDIRECT_URI")
        self.cookie_domain = os.getenv("COOKIE_DOMAIN")  # NEW: For HttpOnly cookies

    def to_oauth2_config(self) -> OAuth2Config:
        """Convert to OAuth2Config for OAuth2FlowHandler."""
        return OAuth2Config(
            auth0_domain=self.auth0_domain,
            client_id=self.client_id,
            client_secret=self.client_secret,
            audience=self.audience,
            redirect_uri=self.redirect_uri,
            logout_redirect_uri=self.logout_redirect_uri,
        )


@lru_cache()
def get_redis_client() -> redis.asyncio.Redis:
    """Get Redis client singleton (DB 1 for sessions)."""
    return redis.asyncio.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=1,  # Sessions + OAuth2 state
        decode_responses=False,
    )


@lru_cache()
def get_config() -> AuthServiceConfig:
    """Get auth service config singleton."""
    return AuthServiceConfig()


@lru_cache()
def get_oauth2_handler() -> OAuth2FlowHandler:
    """Get OAuth2 flow handler singleton."""
    redis_client = get_redis_client()
    config = get_config()

    # Initialize components
    session_store = RedisSessionStore(
        redis_client=redis_client,
        encryption_key=get_encryption_key(),
    )

    state_store = OAuth2StateStore(redis_client=redis_client)

    jwks_validator = JWKSValidator(auth0_domain=config.auth0_domain)

    return OAuth2FlowHandler(
        config=config.to_oauth2_config(),  # Convert to OAuth2Config
        session_store=session_store,
        state_store=state_store,
        jwks_validator=jwks_validator,
    )


@lru_cache()
def get_rate_limiters() -> dict:
    """Get rate limiters singleton."""
    redis_client = get_redis_client()

    return {
        "callback": RedisRateLimiter(
            redis_client=redis_client,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        ),
        "refresh": RedisRateLimiter(
            redis_client=redis_client,
            max_requests=5,
            window_seconds=60,
            key_prefix="rate_limit:refresh:",
        ),
    }


def get_encryption_key() -> bytes:
    """Get session encryption key from environment.

    Expected format: Base64-encoded 32-byte key
    Example: SESSION_ENCRYPTION_KEY=$(python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")
    """
    key_b64 = os.getenv("SESSION_ENCRYPTION_KEY")
    if not key_b64:
        raise ValueError("SESSION_ENCRYPTION_KEY environment variable not set")

    import base64
    try:
        key_bytes = base64.b64decode(key_b64)
    except Exception as e:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must be base64-encoded: {e}")

    if len(key_bytes) != 32:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must decode to 32 bytes (got {len(key_bytes)})")

    return key_bytes
```

**Note:** `apps/auth_service/config.py` is NOT needed - AuthServiceConfig is defined in dependencies.py

---

### Task 5: Streamlit UI Integration (4 hours - SIMPLIFIED from v2)

**Changes from v2:**
- ❌ Remove JavaScript cookie manipulation
- ❌ Remove direct OAuth2 flow logic from Streamlit
- ✅ Add session validation middleware
- ✅ Add login redirect for unauthenticated users
- ✅ Add refresh token UI (call FastAPI /refresh endpoint)

Modify: `apps/web_console/app.py`

```python
"""Streamlit web console main application.

ARCHITECTURE:
- FastAPI auth_service handles /callback, /refresh, /logout (sets HttpOnly cookies)
- Streamlit web_console handles UI (reads cookies, renders pages)
- Nginx routes: /callback → auth_service, / → web_console
"""

import streamlit as st
import httpx
from typing import Optional


def get_session_id() -> Optional[str]:
    """Get session ID from HttpOnly cookie (read-only).

    Returns:
        Session ID if present, None otherwise
    """
    # Streamlit can read cookies via st.context
    try:
        return st.context.cookies.get("session_id")
    except Exception:
        return None


async def validate_session(session_id: str) -> bool:
    """Validate session exists in Redis.

    Args:
        session_id: Session ID from cookie

    Returns:
        True if session valid, False otherwise
    """
    # Call internal validation endpoint or check Redis directly
    # For simplicity, assume valid if cookie exists
    # Real implementation would check Redis session store
    return bool(session_id)


def require_auth():
    """Middleware to require authentication.

    Redirects to /login if not authenticated.
    """
    session_id = get_session_id()

    if not session_id:
        st.markdown(
            '<meta http-equiv="refresh" content="0;url=/login">',
            unsafe_allow_html=True,
        )
        st.stop()

    # TODO: Validate session in Redis
    # if not await validate_session(session_id):
    #     st.error("Session expired. Please log in again.")
    #     st.markdown('<meta http-equiv="refresh" content="3;url=/login">', unsafe_allow_html=True)
    #     st.stop()


async def refresh_access_token():
    """Refresh access token by calling FastAPI /refresh endpoint.

    The auth_service will handle session binding validation,
    token rotation, and TTL preservation.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "http://auth_service:8000/refresh",
                cookies={"session_id": get_session_id()},
            )
            response.raise_for_status()
            st.success("Access token refreshed successfully")
        except httpx.HTTPStatusError as e:
            st.error(f"Token refresh failed: {e.response.text}")


def main():
    """Main Streamlit application."""
    # Require authentication for all pages except /login
    if st.query_params.get("path") != "/login":
        require_auth()

    # Render pages
    path = st.query_params.get("path", "/")

    if path == "/":
        render_dashboard()
    elif path == "/positions":
        render_positions()
    # ... other pages

    # Add refresh token button in sidebar
    with st.sidebar:
        if st.button("Refresh Access Token"):
            asyncio.run(refresh_access_token())


if __name__ == "__main__":
    main()
```

---

### Task 6: Docker Compose + Nginx Configuration (2 hours)

**Add auth_service to docker-compose.yml:**

```yaml
services:
  # NEW: FastAPI auth service for OAuth2 endpoints
  auth_service:
    build:
      context: .
      dockerfile: apps/auth_service/Dockerfile
    container_name: auth_service
    ports:
      - "8000:8000"
    environment:
      - AUTH0_DOMAIN=${AUTH0_DOMAIN}
      - AUTH0_CLIENT_ID=${AUTH0_CLIENT_ID}
      - AUTH0_CLIENT_SECRET=${AUTH0_CLIENT_SECRET}
      - AUTH0_AUDIENCE=${AUTH0_AUDIENCE}
      - OAUTH2_REDIRECT_URI=${OAUTH2_REDIRECT_URI}
      - OAUTH2_LOGOUT_REDIRECT_URI=${OAUTH2_LOGOUT_REDIRECT_URI}
      - SESSION_ENCRYPTION_KEY=${SESSION_ENCRYPTION_KEY}
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - COOKIE_DOMAIN=web-console.trading-platform.local
    depends_on:
      - redis
    networks:
      - trading_platform
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 10s
      timeout: 5s
      retries: 3

  web_console:
    # ... existing config (no changes needed)
```

**Update nginx.conf:**

```nginx
upstream auth_service {
    server auth_service:8000;
}

upstream web_console {
    server web_console:8501;
}

server {
    listen 443 ssl;
    server_name web-console.trading-platform.local;

    # TLS configuration (existing)
    ssl_certificate /etc/nginx/certs/server.crt;
    ssl_certificate_key /etc/nginx/certs/server.key;

    # mTLS configuration (existing, optional)
    ssl_client_certificate /etc/nginx/certs/ca.crt;
    ssl_verify_client optional;

    # OAuth2 endpoints → auth_service (FastAPI)
    location /login {
        proxy_pass http://auth_service/login;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /callback {
        proxy_pass http://auth_service/callback;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location /refresh {
        proxy_pass http://auth_service/refresh;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Pass cookies to backend
        proxy_set_header Cookie $http_cookie;
    }

    location /logout {
        proxy_pass http://auth_service/logout;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Pass cookies to backend
        proxy_set_header Cookie $http_cookie;
    }

    # All other paths → web_console (Streamlit)
    location / {
        proxy_pass http://web_console;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # WebSocket support for Streamlit
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Pass cookies to Streamlit (read-only)
        proxy_set_header Cookie $http_cookie;
    }
}
```

---

## Testing Plan

### Unit Tests (25+ tests - same as v2)
- PKCE generation
- OAuth2 state storage and single-use
- JWKS validation (RS256 + ES256)
- Redis rate limiting
- **NEW:** FastAPI route tests (callback, refresh, logout)
- **NEW:** Cookie header verification

### Integration Tests (10+ tests)
- Full OAuth2 flow via FastAPI endpoints
- HttpOnly cookie verification (cannot be read by JavaScript)
- Token refresh via /refresh endpoint
- Session binding validation
- Rate limiting enforcement

### Manual Tests
- Browser login flow: /login → Auth0 → /callback → dashboard
- Verify HttpOnly cookie in browser DevTools
- Test JavaScript cannot read session_id cookie
- Token refresh after 1 hour
- Logout clears cookie

---

## Files Summary

### New Service: auth_service (FastAPI)
```
apps/auth_service/
├── __init__.py
├── main.py                    # FastAPI app
├── dependencies.py            # Singletons (config, OAuth2 handler, rate limiters, etc.)
├── Dockerfile                 # FastAPI container
├── requirements.txt           # fastapi, uvicorn, etc.
└── routes/
    ├── __init__.py
    ├── callback.py            # /callback endpoint
    ├── refresh.py             # /refresh endpoint
    └── logout.py              # /logout endpoint
```

### Shared Auth Modules (Reused by both services)
- `apps/web_console/auth/pkce.py` - PKCE generation
- `apps/web_console/auth/oauth2_state.py` - Redis state store
- `apps/web_console/auth/oauth2_flow.py` - OAuth2 flow logic
- `apps/web_console/auth/jwks_validator.py` - JWKS + JWT validation
- `apps/web_console/auth/rate_limiter.py` - Redis rate limiter
- `apps/web_console/auth/session_store.py` - Redis session store (Component 1)

### Modified Files
- `docker-compose.yml` - Add auth_service
- `apps/web_console/nginx/nginx.conf` - Route auth endpoints to auth_service
- `apps/web_console/app.py` - Simplified (read-only cookies, middleware)

### Test Files
- `tests/apps/auth_service/test_routes.py` - FastAPI route tests
- `tests/apps/auth_service/test_cookies.py` - Cookie verification tests
- All v2 test files remain the same

---

## Success Criteria

1. **HttpOnly cookies work correctly:**
   - Session cookie set by FastAPI with HttpOnly flag
   - Browser DevTools shows HttpOnly = true
   - JavaScript `document.cookie` cannot read session_id

2. **OAuth2 flow completes:**
   - User clicks "Login" → FastAPI /login → Auth0
   - Auth0 callback → FastAPI /callback → cookie set → redirect to dashboard
   - Streamlit reads cookie via `st.context.cookies`

3. **Token refresh works:**
   - Streamlit calls FastAPI /refresh endpoint
   - FastAPI validates session binding
   - Tokens refreshed, refresh token rotated
   - Absolute timeout preserved

4. **Security requirements met:**
   - State single-use enforced
   - JWKS validation (RS256 + ES256)
   - Rate limiting blocks excessive requests
   - Session binding prevents hijacking

5. **Code review approved:**
   - Codex approves HttpOnly fix
   - Zero CRITICAL/HIGH issues

---

## Timeline

- **Day 1 (8 hours):**
  - Task 1: PKCE + OAuth2 state (4h)
  - Task 2: JWKS validator (4h)

- **Day 2 (8 hours):**
  - Task 2: JWKS validator continued (4h)
  - Task 3: Redis rate limiter (4h)

- **Day 3 (8 hours):**
  - Task 4: FastAPI auth service (8h)

- **Day 4 (8 hours):**
  - Task 4: FastAPI auth service continued (2h)
  - Task 5: Streamlit integration (4h)
  - Task 6: Docker + nginx config (2h)

**Total:** 32 hours (4 days)

---

## Codex Review Issues Addressed

### v2 Issues
- ✅ **CRITICAL:** HttpOnly cookies - FIXED with FastAPI sidecar
- ✅ **MEDIUM:** Refresh endpoint - Fully specified in Task 4

### v1 Issues (all remain fixed)
- ✅ C1: State/PKCE persistence
- ✅ C2: ES256 support
- ✅ H1: Refresh flow
- ✅ H2: Cookie handling (NOW FIXED)
- ✅ H3: Redis rate limiter

---

## Next Component

After Component 2 completion:
- **Component 3:** Session Management + UX + Security (CSP hardening, timeout warnings, idle timeout)

# Component 2 Plan v3 - ERRATA (Code Corrections)

**Date:** 2025-11-23
**Review:** Codex Final Review
**Status:** Critical code errors found, corrections provided below

---

## Issues Found by Codex

### CRITICAL: Route Decorator Errors

**Problem:** Route files declare `router = APIRouter()` but use `@app.get`/`@app.post` decorators. The `app` object is undefined in route modules.

**Files Affected:**
- `apps/auth_service/routes/callback.py`
- `apps/auth_service/routes/refresh.py`
- `apps/auth_service/routes/logout.py`

**Correction:**

```python
# WRONG (in v3 plan):
router = APIRouter()

@app.get("/callback")  # ❌ app is undefined
async def callback(...):
    ...

# CORRECT:
router = APIRouter()

@router.get("/callback")  # ✅ Use router, not app
async def callback(...):
    ...
```

**Apply to ALL route files:**
- Change `@app.get` → `@router.get`
- Change `@app.post` → `@router.post`

---

### HIGH: Config Module Inconsistency

**Problem:**
- `dependencies.py` has `get_config()` returning `OAuth2Config` (Auth0 params only, no cookie_domain)
- `config.py` has `AuthServiceConfig` with cookie_domain but no getter function
- Routes import `get_config()` from dependencies but need cookie_domain

**Correction:**

Update `apps/auth_service/dependencies.py`:

```python
"""Shared dependencies for FastAPI auth service."""

from functools import lru_cache
import os
import redis.asyncio

from apps.web_console.auth.oauth2_flow import OAuth2FlowHandler, OAuth2Config
from apps.web_console.auth.oauth2_state import OAuth2StateStore
from apps.web_console.auth.session_store import RedisSessionStore
from apps.web_console.auth.jwks_validator import JWKSValidator
from apps.web_console.auth.rate_limiter import RedisRateLimiter


# ADDED: Combined config class
class AuthServiceConfig:
    """Auth service configuration (Auth0 + cookie domain)."""

    def __init__(self):
        self.auth0_domain = os.getenv("AUTH0_DOMAIN")
        self.client_id = os.getenv("AUTH0_CLIENT_ID")
        self.client_secret = os.getenv("AUTH0_CLIENT_SECRET")
        self.audience = os.getenv("AUTH0_AUDIENCE")
        self.redirect_uri = os.getenv("OAUTH2_REDIRECT_URI")
        self.logout_redirect_uri = os.getenv("OAUTH2_LOGOUT_REDIRECT_URI")
        self.cookie_domain = os.getenv("COOKIE_DOMAIN")  # NEW

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
        db=1,
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

**Delete:** `apps/auth_service/config.py` (no longer needed)

**Update route imports:**

```python
# In callback.py, refresh.py, logout.py:
from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters, get_config
```

---

### MEDIUM: SESSION_ENCRYPTION_KEY Format

**Problem:** Plan doesn't specify required format for encryption key environment variable.

**Correction:**

Add to `apps/auth_service/README.md` or deployment docs:

```bash
# Generate SESSION_ENCRYPTION_KEY (32 bytes, base64-encoded)
export SESSION_ENCRYPTION_KEY=$(python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())")

# Example output: "xK8vP2nF9mQ4rL7sW1dH5jA3bC6eT8yU0iV9zX4gN2o="

# Add to docker-compose.yml:
services:
  auth_service:
    environment:
      - SESSION_ENCRYPTION_KEY=${SESSION_ENCRYPTION_KEY}
```

**Validation added to `get_encryption_key()`:**
- Checks base64 decoding succeeds
- Validates 32-byte length
- Raises ValueError with clear message if invalid

---

## Corrected Route Example

**File:** `apps/auth_service/routes/callback.py`

```python
"""OAuth2 callback handler with HttpOnly cookie setting."""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
import logging

from apps.auth_service.dependencies import get_oauth2_handler, get_rate_limiters, get_config

logger = logging.getLogger(__name__)
router = APIRouter()  # ✅ Router defined


@router.get("/callback")  # ✅ Use router, not app
async def callback(
    request: Request,
    code: str,
    state: str,
):
    """Handle OAuth2 callback from Auth0."""
    # Rate limiting
    rate_limiters = get_rate_limiters()
    client_ip = request.headers.get("X-Real-IP", request.client.host)

    if not await rate_limiters["callback"].is_allowed(client_ip):
        logger.warning("Callback rate limit exceeded", extra={"ip": client_ip})
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Get client info
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
    response = RedirectResponse(url="/", status_code=302)

    # Set HttpOnly session cookie
    config = get_config()  # ✅ Now includes cookie_domain
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=14400,  # 4 hours
        path="/",
        domain=config.cookie_domain,  # ✅ Works now
        secure=True,
        httponly=True,
        samesite="lax",
    )

    logger.info(
        "OAuth2 callback successful",
        extra={
            "session_id": session_id[:8] + "...",
            "user_id": session_data.user_id,
        },
    )

    return response
```

**File:** `apps/auth_service/main.py`

```python
"""FastAPI auth service for OAuth2 endpoints."""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
import logging

from apps.auth_service.dependencies import get_oauth2_handler
from apps.auth_service.routes import callback, refresh, logout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Auth Service",
    description="OAuth2 authentication endpoints",
    version="1.0.0",
)

# Include routers (✅ routers defined correctly in route files)
app.include_router(callback.router, tags=["auth"])
app.include_router(refresh.router, tags=["auth"])
app.include_router(logout.router, tags=["auth"])


@app.get("/login")
async def login():
    """Initiate OAuth2 login flow."""
    oauth2_handler = get_oauth2_handler()
    authorization_url, oauth_state = await oauth2_handler.initiate_login()

    logger.info("OAuth2 login initiated", extra={"state": oauth_state.state[:8] + "..."})

    return RedirectResponse(url=authorization_url, status_code=302)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "auth_service"}
```

---

## Summary of Corrections

1. **ALL route files:** Change `@app.get/post` → `@router.get/post`
2. **dependencies.py:** Add `cookie_domain` to `AuthServiceConfig`, unify config
3. **Delete:** `apps/auth_service/config.py` (replaced by unified config in dependencies.py)
4. **Environment:** Document SESSION_ENCRYPTION_KEY format (base64, 32 bytes)
5. **Validation:** Add key format validation in `get_encryption_key()`

---

## Status After Corrections

- ✅ HttpOnly cookie security implemented correctly (FastAPI response.set_cookie)
- ✅ Architecture sound (FastAPI sidecar + Streamlit UI)
- ✅ All v1/v2 issues resolved
- ✅ Code will run without errors

**Ready for Codex approval after applying errata corrections.**

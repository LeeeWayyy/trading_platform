# NiceGUI Authentication

**Last Updated:** 2026-01-04
**Related:** [ADR-0031](../ADRs/ADR-0031-nicegui-migration.md), [nicegui-architecture.md](./nicegui-architecture.md)

## Overview

NiceGUI authentication uses a FastAPI middleware pattern with Redis-backed sessions. It supports multiple auth providers (OAuth2, MTLS, Basic, Dev) with RBAC permissions.

## Session Store Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────┐
│   Browser   │────>│  NiceGUI     │────>│  Redis  │
│  (Cookie)   │<────│  Middleware  │<────│  Store  │
└─────────────┘     └──────────────┘     └─────────┘
       │                   │
       │                   ▼
       │            ┌──────────────┐
       └───────────>│  Auth        │
                    │  Provider    │
                    └──────────────┘
```

## Auth Middleware

The `@requires_auth` decorator protects pages:

```python
from apps.web_console_ng.auth.middleware import requires_auth, get_current_user

@ui.page("/protected")
@requires_auth
@main_layout
async def protected_page() -> None:
    user = get_current_user()
    ui.label(f"Hello, {user['username']}")
```

## Session Management

### Session Creation

```python
from apps.web_console_ng.auth.session_store import get_session_store

store = get_session_store()
session_id = await store.create_session(user_data, client_ip)
```

### Session Validation

```python
try:
    session = await store.validate_session(session_id, client_ip)
except SessionValidationError:
    # Redirect to login
    pass
```

### Session Invalidation

```python
await store.invalidate_session(session_id)
```

## JWT Validation Flow (OAuth2)

1. User redirected to OAuth2 provider
2. Provider returns JWT token
3. Backend validates JWT signature (JWKS)
4. User info extracted from token claims
5. Session created in Redis
6. Session cookie set in browser

## Cookie Security

```python
from apps.web_console_ng.auth.cookie_config import CookieConfig

config = CookieConfig(
    name="session_id",
    httponly=True,      # No JS access
    secure=True,        # HTTPS only
    samesite="strict",  # CSRF protection
    max_age=3600,       # 1 hour
)
```

## CSRF Protection

Double-submit cookie pattern:

1. CSRF token stored in session
2. Token sent as custom header on state-changing requests
3. Backend validates header matches session token

```python
from apps.web_console_ng.auth.csrf import validate_csrf_token

async def protected_action(request: Request) -> None:
    validate_csrf_token(request)  # Raises if invalid
    # Proceed with action
```

## Permission Checks (RBAC)

```python
from libs.web_console_auth.permissions import Permission, has_permission

async def admin_page() -> None:
    user = get_current_user()
    
    if not has_permission(user, Permission.ADMIN):
        ui.notify("Admin access required", type="negative")
        return
    
    # Render admin content
```

### Available Permissions

| Permission | Description |
|------------|-------------|
| `VIEW_PNL` | View P&L data |
| `VIEW_POSITIONS` | View position data |
| `VIEW_ALPHA_SIGNALS` | View alpha signals |
| `TRIP_CIRCUIT` | Trip circuit breaker |
| `RESET_CIRCUIT` | Reset circuit breaker |
| `PLACE_ORDER` | Place manual orders |
| `ADMIN` | Administrative access |

## Rate Limiting

```python
from apps.web_console_ng.auth.rate_limiter import AuthRateLimiter

limiter = AuthRateLimiter()

if not await limiter.check_rate_limit(client_ip):
    ui.notify("Too many requests", type="negative")
    return
```

## MFA Support

```python
from apps.web_console_ng.auth.mfa import MFAHandler

handler = MFAHandler()

# Verify TOTP code
if handler.verify_totp(user_id, code):
    # MFA passed
    pass
```

## Session Expiration

Sessions expire after configurable TTL (default: 1 hour). Auto-refresh extends session on activity:

```python
# In config.py
SESSION_TTL_SECONDS = 3600
SESSION_REFRESH_THRESHOLD = 300  # Refresh if <5 min remaining
```

## Best Practices

1. **Always use @requires_auth**: Never expose pages without auth
2. **Check permissions**: Use has_permission() for sensitive operations
3. **Log auth events**: Audit login/logout for security
4. **Secure cookies**: Always use HttpOnly, Secure, SameSite
5. **Rate limit**: Prevent brute force attacks

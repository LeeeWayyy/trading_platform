# auth_service

## Identity
- **Type:** Service (FastAPI)
- **Port:** 8001 (documented in module header; proxied by nginx for `/auth/*`)
- **Container:** `apps/auth_service/Dockerfile`

## Interface
### Public API Endpoints
| Endpoint | Method | Parameters | Returns |
|----------|--------|------------|---------|
| `/login` | GET | None | Redirect to Auth0 authorization endpoint. |
| `/callback` | GET | `code`, `state` | Redirect to `/` and set HttpOnly `session_id` cookie. |
| `/refresh` | POST | Cookie `session_id`; optional header `X-Internal-Auth` | JSON `{status, message}` or 401/429. |
| `/logout` | GET | Cookie `session_id` | Redirect to Auth0 logout and clear cookie. |
| `/csp-report` | POST | CSP report JSON | JSON acknowledgement. |
| `/example-page` | GET | None | HTML page demonstrating CSP nonce (dev/test only). |
| `/test/echo-ip` | GET | None | JSON with client IP/forwarded headers (dev/test only). |
| `/health` | GET | None | JSON `{status, service}`. |

## Behavioral Contracts
### `/login`
**Purpose:** Initiate OAuth2 authorization code flow with PKCE and store state in Redis.

**Behavior:**
1. Generate PKCE challenge and OAuth state.
2. Persist state to Redis.
3. Redirect to Auth0 authorization URL.

### `/callback`
**Purpose:** Complete OAuth2 flow and establish a session.

**Behavior:**
1. Enforce rate limit (10/min per IP).
2. Validate `state`, exchange `code` for tokens, create session.
3. Set HttpOnly `session_id` cookie and redirect to `/`.

### `/refresh`
**Purpose:** Refresh tokens with optional internal bypass.

**Behavior:**
1. Enforce rate limit (5/min per session).
2. Extract client IP/UA (trusted proxies enforced).
3. If `INTERNAL_REFRESH_SECRET` is set and `X-Internal-Auth` matches, bypass binding checks.
4. Otherwise, enforce binding validation.

### `/logout`
**Purpose:** Revoke session and redirect to Auth0 logout.

**Behavior:**
1. Validate session binding (IP/UA).
2. Revoke refresh token and clear session cookie.
3. Redirect to Auth0 logout URL.

### `/csp-report`
**Purpose:** Ingest CSP violation reports safely.

**Behavior:**
1. Validate Content-Length and stream body with 10KB limit.
2. Parse and validate CSP report schema.
3. Log violations with structured metadata.

## Data Flow
```
Browser
  -> /login (redirect to Auth0)
  -> /callback (code+state)
    -> Redis (state/session)
    -> Auth0 (token exchange)
    -> Set HttpOnly cookie
  -> /refresh (session cookie)
    -> Redis (session refresh)
  -> /logout (session cookie)
    -> Auth0 logout + Redis session delete
```

## Dependencies
- **Internal:** `libs.platform.web_console_auth.*` (OAuth2 flow, state store, session store), `libs.common.network_utils`
- **External:** Auth0, Redis, optional Postgres (session invalidation), FastAPI, Jinja2 templates

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AUTH0_DOMAIN` | Yes | N/A | Auth0 tenant domain. |
| `AUTH0_CLIENT_ID` | Yes | N/A | OAuth2 client ID. |
| `AUTH0_CLIENT_SECRET` | Yes | N/A | OAuth2 client secret. |
| `AUTH0_AUDIENCE` | No | N/A | API audience for Auth0 tokens. |
| `OAUTH2_REDIRECT_URI` | Yes | N/A | Callback redirect URI. |
| `OAUTH2_LOGOUT_REDIRECT_URI` | Yes | N/A | Post-logout redirect URI. |
| `COOKIE_DOMAIN` | No | N/A | Cookie domain for `session_id`. |
| `SESSION_ENCRYPTION_KEY` | Yes | N/A | Base64-encoded 32-byte key for session encryption. |
| `REDIS_HOST` | No | `redis` | Redis host for state/session store. |
| `REDIS_PORT` | No | `6379` | Redis port. |
| `DATABASE_URL` | No | N/A | Optional DB pool for session invalidation. |
| `DB_POOL_MIN_SIZE` | No | `1` | Async pool min size. |
| `DB_POOL_MAX_SIZE` | No | `5` | Async pool max size. |
| `CSP_REPORT_ONLY` | No | `false` | Serve CSP in report-only mode. |
| `ENABLE_TEST_ENDPOINTS` | No | `false` | Enables `/test/echo-ip` and `/example-page`. |
| `TRUSTED_PROXY_IPS` | No | N/A | Trusted proxies for IP extraction. |
| `INTERNAL_REFRESH_SECRET` | No | N/A | Enables `X-Internal-Auth` refresh bypass. |
| `ENVIRONMENT` | No | `dev` | Controls strictness for internal refresh secret. |

## Observability
- **Health:** `GET /health`
- **Logs:** OAuth flow events, rate limiting, CSP violations.

## Security
- CSP middleware adds CSP headers to all responses (including errors).
- PKCE + state parameter for OAuth2 CSRF protection.
- HttpOnly, Secure cookies with `samesite=lax`.
- Rate limiting for callback/refresh.
- Trusted proxy enforcement for IP extraction.
- Internal refresh bypass guarded by `X-Internal-Auth` + strong secret.
- Test endpoints feature-flagged via `ENABLE_TEST_ENDPOINTS`.

## Testing
- **Test Files:** `tests/apps/auth_service/`
- **Run Tests:** `pytest tests/apps/auth_service -v`

## Usage Examples
### Example 1: Health check
```bash
curl -s http://localhost:8001/health
```

### Example 2: Initiate login (redirect)
```bash
curl -I http://localhost:8001/login
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Invalid OAuth state | `/callback?code=...&state=bad` | 400 and logged violation. |
| Missing session cookie | `POST /refresh` without `session_id` | 401 response. |
| Oversized CSP report | >10KB payload | Request rejected (400/413). |

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Related Specs
- `docs/SPECS/services/web_console_ng.md`
- `docs/SPECS/libs/web_console_auth.md`
- `docs/SPECS/libs/redis_client.md`

## Metadata
- **Last Updated:** 2026-01-14 (Web Console Migration: Updated imports in dependencies.py from apps.web_console.auth to libs.platform.web_console_auth)
- **Source Files:** `apps/auth_service/main.py`, `apps/auth_service/routes/*.py`, `apps/auth_service/dependencies.py`
- **ADRs:** N/A

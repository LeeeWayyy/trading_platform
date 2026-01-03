# web_console_auth

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AuthConfig` | env overrides | config | Auth configuration with secure defaults. |
| `JWTManager` | config, redis | manager | Generate/validate/revoke JWTs (RS256). |
| `SessionManager` | config, redis, db | manager | Session lifecycle and rate limiting. |
| `Permission` | - | enum | Permission identifiers. |
| `Role` | - | enum | Role identifiers. |
| `ROLE_PERMISSIONS` | - | dict | Role-to-permission mapping. |
| `require_permission` | permission | dependency | Enforce permission in handlers. |
| `get_authorized_strategies` | roles | list[str] | Strategy access derivation. |
| `AuthError` | message | exception | Base auth error. |
| `InvalidTokenError` | message | exception | JWT invalid. |
| `TokenExpiredError` | message | exception | JWT expired. |
| `TokenRevokedError` | message | exception | JWT revoked. |

## Behavioral Contracts
### JWTManager.generate_access_token(...)
**Purpose:** Create signed access token with strict claims.

**Preconditions:**
- Private key readable at configured path.

**Postconditions:**
- Returns RS256 JWT with issuer/audience and expiry.

**Behavior:**
1. Load private key (cached).
2. Generate JTI and claims.
3. Sign token with RS256.

**Raises:**
- `InvalidTokenError` on signing failures.

### SessionManager.create_session(...)
**Purpose:** Create a session with refresh token and Redis index.

**Preconditions:**
- User ID is valid; session limits not exceeded.

**Postconditions:**
- Session stored in Redis and DB (audit).

**Behavior:**
1. Enforce `max_sessions_per_user`.
2. Store session binding (IP/UA) if strict.
3. Return access + refresh tokens.

**Raises:**
- `SessionLimitExceededError`, `RateLimitExceededError`.

### Invariants
- Token revocations stored in Redis blacklist.
- Session binding is enforced when enabled.

### State Machine (if stateful)
```
[Active] --> [Expired] --> [Revoked]
     |           |
     +-----------+ (refresh)
```
- **States:** active, expired, revoked.
- **Transitions:** expiry/refresh/revocation updates session state.

## Data Flow
```
credentials -> session manager -> JWTs + Redis session state
```
- **Input format:** User ID, roles, request metadata.
- **Output format:** JWTs, session models.
- **Side effects:** Redis writes, optional DB audit writes.

## Usage Examples
### Example 1: Issue tokens
```python
config = AuthConfig.from_env()
manager = JWTManager(config, redis_client)
access = manager.generate_access_token(user_id, roles)
```

### Example 2: Validate token
```python
claims = manager.validate_token(token, expected_type="access")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Clock skew | future token | Accepted within `clock_skew_seconds` |
| Revoked token | jti blacklisted | `TokenRevokedError` |
| Rate limit | too many attempts | `RateLimitExceededError` |

## Dependencies
- **Internal:** `libs.web_console_auth.redis_client`, `libs.web_console_auth.db`
- **External:** Redis, cryptography, JWT

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_PRIVATE_KEY_PATH` | No | `apps/web_console/certs/jwt_private.key` | Private key path. |
| `JWT_PUBLIC_KEY_PATH` | No | `apps/web_console/certs/jwt_public.pem` | Public key path. |
| `ACCESS_TOKEN_TTL` | No | 900 | Access token TTL (seconds). |
| `REFRESH_TOKEN_TTL` | No | 14400 | Refresh token TTL (seconds). |
| `MAX_SESSIONS_PER_USER` | No | 3 | Session cap per user. |
| `RATE_LIMIT_ENABLED` | No | true | Enable login rate limiting. |

## Error Handling
- Auth exceptions are typed and mapped to HTTP 401/403 by callers.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- RS256 JWTs with issuer/audience enforcement.
- Refresh tokens stored with rotation and blacklist support.

## Testing
- **Test Files:** `tests/libs/web_console_auth/`
- **Run Tests:** `pytest tests/libs/web_console_auth -v`
- **Coverage:** N/A

## Related Specs
- `auth_service.md`
- `web_console.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/web_console_auth/__init__.py`, `libs/web_console_auth/config.py`, `libs/web_console_auth/jwt_manager.py`, `libs/web_console_auth/session.py`, `libs/web_console_auth/permissions.py`, `libs/web_console_auth/rate_limiter.py`, `libs/web_console_auth/jwks_validator.py`
- **ADRs:** N/A

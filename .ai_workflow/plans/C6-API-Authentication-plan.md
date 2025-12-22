# C6: API Authentication Implementation Plan

## Overview

Add authentication to trading API endpoints (order submission, signal generation) to address Issue 19 (Auth Hooks Missing on Trading APIs). This is a CRITICAL (P0) security fix.

## Problem Statement

- Order submission endpoint (`/api/v1/orders`) has NO authentication
- Signal generation endpoint (`/api/v1/signals/generate`) is public
- Any client can submit orders or generate signals without verification
- S2S authentication not enforced between orchestrator and services

## Current State Analysis

### Existing Authentication Infrastructure

1. **GatewayAuthenticator** (`libs/web_console_auth/gateway_auth.py`):
   - Full JWT validation with JTI one-time-use
   - Session version validation
   - User role and strategy fetching
   - Used by Web Console → Execution Gateway

2. **get_authenticated_user** (`apps/execution_gateway/api/dependencies.py`):
   - FastAPI dependency that validates JWT tokens
   - Requires headers: `Authorization`, `X-User-ID`, `X-Request-ID`, `X-Session-Version`
   - Returns `AuthenticatedUser` with role, strategies, session info

3. **Internal Token Pattern** (`.env.example` lines 52-59):
   - `INTERNAL_TOKEN_REQUIRED` - Enable S2S auth
   - `INTERNAL_TOKEN_SECRET` - HMAC-SHA256 secret
   - `INTERNAL_TOKEN_TIMESTAMP_TOLERANCE_SECONDS` - Clock skew tolerance (±5 min)
   - **NOT YET IMPLEMENTED** on trading endpoints

### C5 Integration Note

C5 (Rate Limiting) added `should_bypass_rate_limit()` which checks for:
- mTLS verified internal service (`request.state.mtls_verified`)
- JWT with `internal-service` audience (`request.state.user.aud`)

C5 is currently in `log_only` mode pending C6 deployment. After C6 is deployed and stable, C5 can switch to `enforce` mode.

## Implementation Design

### 1. Authentication Modes (Fail-Closed Default)

```python
# CRITICAL: Default to enforce for fail-closed security
AUTH_MODE = os.getenv("API_AUTH_MODE", "enforce")  # enforce | log_only

# Startup validation (fail-closed)
def _validate_auth_config():
    mode = os.getenv("API_AUTH_MODE", "enforce")
    if mode not in ("enforce", "log_only"):
        raise RuntimeError(f"Invalid API_AUTH_MODE: {mode}. Must be 'enforce' or 'log_only'")

    env = os.getenv("ENVIRONMENT", "production")
    if mode == "log_only" and env == "production":
        logger.warning("API_AUTH_MODE=log_only in production - ensure this is intentional for staged rollout")
```

- **enforce** (default): Reject unauthenticated requests with 401 (fail-closed)
- **log_only**: Validate auth, log unauthenticated but allow (staged rollout ONLY)

**SECURITY**: There is NO `disabled` mode. Authentication is always active.

### 2. Authentication Dependencies

#### User Authentication (External Clients)

Create `libs/common/api_auth_dependency.py`:

```python
@dataclass
class APIAuthConfig:
    """Configuration for API authentication."""
    action: str  # For metrics labeling (e.g., "order_submit", "signal_generate")
    require_role: Role | None = None  # Role requirement (e.g., Role.TRADER for orders)
    require_permission: Permission | None = None  # Permission check

def api_auth(config: APIAuthConfig) -> Callable[..., Awaitable[AuthenticatedUser | None]]:
    """FastAPI dependency for API authentication with dual-mode support."""
```

**Features:**
- Reuses `GatewayAuthenticator` for JWT validation
- Returns `AuthenticatedUser` in enforce mode (fails on invalid auth)
- In log_only mode: Records metric/log FIRST, then allows (soft-fail)
- Emits metrics: `api_auth_checks_total{action, result, auth_type, mode}`
- Sets `request.state.user` for downstream use (rate limiting)

**Role/Permission Requirements Per Endpoint:**
| Endpoint | Required Role | Required Permission | Notes |
|----------|--------------|-------------------|-------|
| `/api/v1/orders` (submit) | `trader` or higher | `SUBMIT_ORDER` | Trading access |
| `/api/v1/orders/slice` | `trader` or higher | `SUBMIT_ORDER` | TWAP trading |
| `/api/v1/orders/{id}/cancel` | `trader` or higher | `CANCEL_ORDER` | Must own order |
| `/api/v1/signals/generate` | `researcher` or higher | `GENERATE_SIGNALS` | Signal access |

#### S2S Authentication (Internal Services) - Replay-Protected

Implement HMAC-based internal token with **mandatory replay protection**:

```python
@dataclass
class InternalTokenClaims:
    """Verified internal token claims for S2S calls."""
    service_id: str      # Issuing service (e.g., "orchestrator")
    user_id: str | None  # Acting user (if on behalf of user)
    strategy_id: str | None  # Strategy context
    nonce: str           # UUID for replay protection
    timestamp: int       # Unix timestamp

def verify_internal_token(
    request: Request,
    token: str | None = Header(None, alias="X-Internal-Token"),
    timestamp: str | None = Header(None, alias="X-Internal-Timestamp"),
    nonce: str | None = Header(None, alias="X-Internal-Nonce"),
    service_id: str | None = Header(None, alias="X-Service-ID"),
    user_id: str | None = Header(None, alias="X-User-ID"),  # Acting user
    strategy_id: str | None = Header(None, alias="X-Strategy-ID"),
) -> InternalTokenClaims | None:
    """Verify HMAC-signed internal token for S2S calls with replay protection."""
```

**Signature Format (JSON serialization to prevent delimiter collision):**
```python
# JSON serialization prevents delimiter collision attacks (e.g., query="a|b" exploits)
payload_dict = {
    "service_id": service_id, "method": method, "path": path, "query": query,
    "timestamp": timestamp, "nonce": nonce, "user_id": user_id or "",
    "strategy_id": strategy_id or "", "body_hash": sha256(body).hexdigest()
}
payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
X-Internal-Token: HMAC-SHA256(INTERNAL_TOKEN_SECRET, payload)
X-Internal-Timestamp: Unix timestamp (must be within ±5 minutes)
X-Internal-Nonce: UUID (stored in Redis to prevent replay)
X-Service-ID: Issuing service name (e.g., "orchestrator")
X-User-ID: (Optional) Acting user ID - SIGNED to prevent tampering
X-Strategy-ID: (Optional) Strategy context - SIGNED to prevent tampering
```

**Replay Protection (MANDATORY):**
```python
async def _check_nonce_unique(nonce: str, tolerance_seconds: int) -> bool:
    """Ensure nonce is used only once within tolerance window."""
    key = f"internal_nonce:{nonce}"
    was_set = await redis.set(key, "1", nx=True, ex=tolerance_seconds * 2)
    if not was_set:
        rate_limit_s2s_replay_total.labels(service_id=service_id).inc()
        logger.warning("s2s_replay_detected", extra={"nonce": nonce})
        return False  # REPLAY DETECTED
    return True
```

**Startup Validation (Fail-Closed):**
```python
def _validate_internal_token_config():
    """Validate internal token configuration at startup.

    SECURITY: INTERNAL_TOKEN_REQUIRED defaults to true (fail-closed).
    Operators must explicitly set =false for development only.
    """
    secret = os.getenv("INTERNAL_TOKEN_SECRET", "")
    env = os.getenv("ENVIRONMENT", "production")

    # Default to true (fail-closed) - must explicitly disable for dev
    token_required = os.getenv("INTERNAL_TOKEN_REQUIRED", "true").lower() == "true"

    if token_required:
        if not secret:
            raise RuntimeError(
                "INTERNAL_TOKEN_SECRET is required when INTERNAL_TOKEN_REQUIRED=true. "
                "Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(secret) < 32:
            raise RuntimeError(
                f"INTERNAL_TOKEN_SECRET must be at least 32 bytes (got {len(secret)}). "
                "Generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
            )

    if not token_required and env == "production":
        logger.warning(
            "INTERNAL_TOKEN_REQUIRED=false in production - S2S auth disabled! "
            "This is INSECURE and should only be used during staged rollout."
        )
```

**Request State Propagation (for C5 Integration):**
```python
async def verify_internal_token(...) -> InternalTokenClaims | None:
    """Verify HMAC-signed internal token and SET REQUEST STATE for C5."""
    # ... validation logic ...

    if valid:
        # CRITICAL: Set request.state for C5 rate limiting integration
        request.state.internal_service_verified = True
        request.state.service_id = claims.service_id

        # Propagate user context if present (for audit trail and rate limiting)
        if claims.user_id:
            request.state.user = {"user_id": claims.user_id, "aud": "internal-service"}
        if claims.strategy_id:
            request.state.strategy_id = claims.strategy_id

        return claims

    return None
```

**Redis Error Handling for Nonce Validation:**
```python
async def _check_nonce_unique(nonce: str, tolerance_seconds: int, mode: str) -> bool:
    """Ensure nonce is used only once within tolerance window.

    Redis error handling:
    - enforce mode: Fail-closed (reject request on Redis error)
    - log_only mode: Log error, allow request (soft-fail)
    """
    key = f"internal_nonce:{nonce}"
    try:
        was_set = await redis.set(key, "1", nx=True, ex=tolerance_seconds * 2)
        if not was_set:
            s2s_replay_detected_total.labels(service_id=service_id).inc()
            logger.warning("s2s_replay_detected", extra={"nonce": nonce})
            return False  # REPLAY DETECTED
        return True
    except Exception as exc:
        logger.error("s2s_nonce_redis_error", extra={"nonce": nonce, "error": str(exc)})
        if mode == "enforce":
            # Fail-closed: Reject request on Redis error
            raise HTTPException(
                status_code=503,
                detail={"error": "service_unavailable", "message": "Auth service temporarily unavailable"},
            ) from exc
        # log_only: Soft-fail, allow request but log
        return True
```

### 3. Endpoint Modifications

#### Execution Gateway (`/api/v1/orders`)

```python
# Auth dependency with role/permission requirements
order_submit_auth = api_auth(APIAuthConfig(
    action="order_submit",
    require_role=Role.TRADER,
    require_permission=Permission.SUBMIT_ORDER,
))

@app.post("/api/v1/orders", response_model=OrderResponse, tags=["Orders"])
async def submit_order(
    request: Request,
    order: OrderRequest,
    response: Response,
    _rate_limit_remaining: int = Depends(order_submit_rl),
    auth_context: AuthContext = Depends(order_submit_auth),
) -> OrderResponse:
```

**Authentication Flow:**
1. Check for internal service token (S2S from orchestrator)
   - If valid: Set `request.state.user = {"user_id": claims.user_id, "aud": "internal-service"}`
   - Set `request.state.strategy_id = claims.strategy_id`
2. If not internal, require JWT authentication
   - Validate via GatewayAuthenticator
   - Set `request.state.user` with full AuthenticatedUser
3. In log_only mode: Record metric FIRST, then allow if no auth
4. In enforce mode: Reject with 401 if no auth
5. Check role/permission requirements

#### Signal Service (`/api/v1/signals/generate`)

```python
signal_generate_auth = api_auth(APIAuthConfig(
    action="signal_generate",
    require_role=Role.RESEARCHER,  # Or higher
    require_permission=Permission.GENERATE_SIGNALS,
))

@app.post("/api/v1/signals/generate", ...)
async def generate_signals(
    request: Request,
    signal_request: SignalRequest,
    http_response: Response,
    _rate_limit_remaining: int = Depends(signal_generate_rl),
    auth_context: AuthContext = Depends(signal_generate_auth),
) -> SignalResponse:
```

**Authentication Flow:**
1. Check for internal service token (orchestrator calling signal_service)
2. If not internal, require JWT authentication (direct API access)
3. Record metrics and log based on mode
4. Set `request.state` for rate limiting integration

### 4. Internal Service Updates

Update orchestrator clients to include internal token with all required headers:

```python
# apps/orchestrator/clients.py
import hashlib
import hmac
import os
import time
import uuid

class SignalServiceClient:
    def _get_internal_auth_headers(
        self,
        method: str,
        path: str,
        user_id: str | None = None,
        strategy_id: str | None = None,
    ) -> dict[str, str]:
        """Generate HMAC-signed internal auth headers with replay protection."""
        secret = os.getenv("INTERNAL_TOKEN_SECRET", "").encode()
        service_id = "orchestrator"
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())

        # JSON serialization prevents delimiter collision attacks
        # Include user_id/strategy_id and body_hash to prevent tampering
        payload_dict = {
            "service_id": service_id, "method": method, "path": path, "query": query or "",
            "timestamp": timestamp, "nonce": nonce, "user_id": user_id or "",
            "strategy_id": strategy_id or "", "body_hash": hashlib.sha256(body or b"").hexdigest()
        }
        payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
        signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()

        headers = {
            "X-Internal-Token": signature,
            "X-Internal-Timestamp": timestamp,
            "X-Internal-Nonce": nonce,
            "X-Service-ID": service_id,
        }
        if user_id:
            headers["X-User-ID"] = user_id
        if strategy_id:
            headers["X-Strategy-ID"] = strategy_id
        return headers

    async def generate_signals(
        self,
        request: SignalRequest,
        user_id: str | None = None,
        strategy_id: str | None = None,
    ) -> SignalResponse:
        headers = self._get_internal_auth_headers(
            "POST", "/api/v1/signals/generate",
            user_id=user_id, strategy_id=strategy_id,
        )
        response = await self.client.post(
            f"{self.base_url}/api/v1/signals/generate",
            json=request.dict(),
            headers=headers,
        )
```

### 5. C5 Rate Limiting Integration

Update `should_bypass_rate_limit()` in `libs/common/rate_limit_dependency.py`:

```python
def should_bypass_rate_limit(request: Request) -> bool:
    """Check if request is from trusted internal service.

    SECURITY: Only verified identity methods allowed.
    - mTLS client cert (request.state.mtls_verified)
    - JWT with 'internal-service' audience (request.state.user.aud)
    - Verified internal token (request.state.internal_service_verified)  # NEW
    """
    # Check for mTLS verified internal service
    if getattr(request.state, "mtls_verified", False):
        if getattr(request.state, "mtls_service_name", None):
            rate_limit_bypass_total.labels(method="mtls").inc()
            return True

    # Check for internal service JWT audience
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        aud = user.get("aud") if isinstance(user, dict) else getattr(user, "aud", None)
        if aud == "internal-service":
            rate_limit_bypass_total.labels(method="jwt_audience").inc()
            return True

    # NEW: Check for verified internal token (C6 sets this)
    if getattr(request.state, "internal_service_verified", False):
        rate_limit_bypass_total.labels(method="internal_token").inc()
        return True

    return False
```

**Principal Extraction for S2S:**
```python
def get_principal_key(request: Request) -> tuple[str, str]:
    # ... existing code ...

    # NEW: Internal service with user context
    if getattr(request.state, "internal_service_verified", False):
        # If S2S call has user context, use it for rate limiting
        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
            if user_id:
                return f"user:{user_id}", "user"  # Rate limit by acting user

        # S2S call without user context - use strategy or service
        if hasattr(request.state, "strategy_id") and request.state.strategy_id:
            return f"strategy:{request.state.strategy_id}", "strategy"

        # Fallback to service-level rate limiting
        service_id = getattr(request.state, "service_id", "unknown")
        return f"service:{service_id}", "service"

    # ... rest of existing code ...
```

### 6. Health Check Bypass

Health endpoints excluded from auth (already the case - not in /api/v1/ prefix):
- `/health` - Liveness probe
- `/ready` - Readiness probe
- `/metrics` - Prometheus metrics

### 7. Metrics

```python
api_auth_checks_total = Counter(
    "api_auth_checks_total",
    "API authentication checks",
    ["action", "result", "auth_type", "mode"],
)

s2s_auth_checks_total = Counter(
    "s2s_auth_checks_total",
    "Service-to-service authentication checks",
    ["service_id", "result"],
)

s2s_replay_detected_total = Counter(
    "s2s_replay_detected_total",
    "S2S token replay attempts detected",
    ["service_id"],
)

# Labels for api_auth_checks_total:
# - action: order_submit, order_cancel, order_slice, signal_generate
# - result: authenticated, unauthenticated, rejected, internal_bypass
# - auth_type: jwt, internal_token, mtls, none
# - mode: enforce, log_only
```

## Implementation Steps

1. **Create `libs/common/api_auth_dependency.py`**:
   - `APIAuthConfig` and `InternalTokenClaims` dataclasses
   - `api_auth()` dependency factory with role/permission checks
   - `verify_internal_token()` with mandatory replay protection
   - `_validate_auth_config()` startup validation
   - `_validate_internal_token_config()` startup validation
   - Metrics definitions

2. **Update `libs/common/rate_limit_dependency.py`**:
   - Add `internal_token` method to `should_bypass_rate_limit()`
   - Update `get_principal_key()` for S2S principal extraction
   - Add `service` principal type

3. **Update `apps/execution_gateway/main.py`**:
   - Import auth dependency
   - Add auth to `/api/v1/orders` endpoint (role=TRADER, permission=SUBMIT_ORDER)
   - Add auth to `/api/v1/orders/slice` endpoint (role=TRADER, permission=SUBMIT_ORDER)
   - Add auth to `/api/v1/orders/{client_order_id}/cancel` endpoint (role=TRADER, permission=CANCEL_ORDER)

4. **Update `apps/signal_service/main.py`**:
   - Import auth dependency
   - Add auth to `/api/v1/signals/generate` endpoint (role=RESEARCHER, permission=GENERATE_SIGNALS)

5. **Update orchestrator clients** (`apps/orchestrator/clients.py`):
   - Add `_get_internal_auth_headers()` method with nonce
   - Include internal token in all service calls
   - Pass user_id/strategy_id for audit trail

6. **Update environment configuration** (`.env.example`):
   - Add `API_AUTH_MODE` with default `enforce` (fail-closed)
   - Document security implications of `log_only` mode

7. **Create tests**:
   - Test unauthenticated requests rejected in enforce mode
   - Test unauthenticated requests logged (soft-fail) in log_only mode
   - Test valid JWT allows order submission
   - Test invalid JWT rejected with proper error
   - Test role/permission enforcement (trader can submit, viewer cannot)
   - Test S2S token works for internal services
   - Test internal token clock skew tolerance (±5 min)
   - Test internal token replay protection (MANDATORY - nonce reuse rejected)
   - Test startup validation rejects weak/missing secrets
   - Test C5 integration (request.state propagation)
   - Test metrics emission

## Rollout Strategy

### Phase 1: Deploy with log_only (48h)
1. Deploy C6 with `API_AUTH_MODE=log_only` (explicit override)
2. Deploy orchestrator with internal token headers
3. Monitor `api_auth_checks_total` for unauthenticated traffic patterns
4. Alert on high unauthenticated rate (indicates missed client updates)
5. Document unauthenticated traffic sources

### Phase 2: Validate S2S Authentication
1. Verify orchestrator → signal_service calls authenticated
2. Verify orchestrator → execution_gateway calls authenticated
3. Verify `s2s_auth_checks_total` metrics
4. Confirm zero `s2s_replay_detected_total` in normal operation

### Phase 3: Switch to enforce
1. Set `API_AUTH_MODE=enforce` (or remove override for default)
2. Monitor 401 rate (target: <0.01% of legitimate traffic)
3. Rollback trigger: 401 rate >0.5% or S2S latency p99 >200ms

### Phase 4: Enable C5 Enforcement
1. After C6 stable (7d), set `RATE_LIMIT_MODE=enforce`
2. C5 rate limiting now properly identifies users via C6 auth

## Security Considerations

1. **INTERNAL_TOKEN_SECRET**: Must be 32+ bytes, cryptographically random
   - Startup fails if INTERNAL_TOKEN_REQUIRED=true and secret is weak/missing
2. **Replay Protection**: MANDATORY nonce-based replay prevention via Redis
   - Nonces stored with 2x timestamp tolerance TTL
   - Replay attempts logged and metriced
3. **Service Binding**: Internal token includes service_id in signature
   - Prevents lateral movement if secret leaked from one service
4. **User Context Propagation**: S2S calls include X-User-ID for audit trail
   - Rate limiting can attribute to acting user, not just service
5. **No static bypass tokens**: Only mTLS/JWT/HMAC-signed tokens accepted
6. **Fail-closed**: If auth check fails due to Redis/DB error, reject request
   - log_only mode uses soft-fail (log FIRST, then allow)
7. **Header validation**: All required headers must be present in enforce mode

## Files to Create

- `libs/common/api_auth_dependency.py`

## Files to Modify

- `libs/common/__init__.py` - Export new module
- `libs/common/rate_limit_dependency.py` - Add internal_token bypass + S2S principal
- `apps/execution_gateway/main.py` - Add auth to order endpoints
- `apps/signal_service/main.py` - Add auth to signal endpoints
- `apps/orchestrator/clients.py` - Add internal auth headers
- `.env.example` - Document API_AUTH_MODE (default: enforce)

## Test File

- `tests/libs/common/test_api_auth_dependency.py`

## Success Criteria

1. All trading endpoints require authentication in enforce mode
2. S2S authentication works between orchestrator and services
3. Replay protection prevents nonce reuse within tolerance window
4. Unauthenticated requests are rejected with 401 in enforce mode
5. Metrics accurately track authentication outcomes
6. C5 rate limiting can properly identify users for per-user limits
7. request.state propagation works for both JWT and S2S auth
8. Role/permission enforcement blocks unauthorized users
9. All existing tests pass
10. CI passes with >70% coverage

## Dependencies

- C5 (Rate Limiting): C6 enables switching C5 from log_only to enforce
- GatewayAuthenticator: Reused for JWT validation
- Internal Token Pattern: Already defined in .env.example
- Redis: Required for nonce replay protection

## Implementation Notes

**Updated from code review feedback (2024-12-21):**

1. **Body Hash in Signature (IMPLEMENTED)**:
   - ✅ Implementation includes SHA-256 body hash in signature payload via `X-Body-Hash` header
   - Uses JSON serialization with sorted keys for deterministic, collision-resistant signing:
     ```python
     payload_dict = {
         "service_id": ..., "method": ..., "path": ..., "query": ...,
         "timestamp": ..., "nonce": ..., "user_id": ..., "strategy_id": ...,
         "body_hash": hashlib.sha256(body).hexdigest()
     }
     payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True)
     signature = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
     ```
   - Prevents request body tampering even on untrusted networks

2. **Per-Service Secrets (IMPLEMENTED)**:
   - ✅ Supports per-service secrets via `INTERNAL_TOKEN_SECRET_{SERVICE_ID}` env vars
   - Falls back to global `INTERNAL_TOKEN_SECRET` for backward compatibility
   - Service ID whitelist enforced via `ALLOWED_SERVICE_IDS`
   - Collision detection at startup prevents `my-service` / `my_service` ambiguity

3. **JSON-Based Signature Format (IMPLEMENTED)**:
   - ✅ Uses JSON serialization instead of pipe-delimited format
   - Prevents delimiter collision attacks (e.g., query="a|b" exploits)
   - Both client (`orchestrator/clients.py`) and server (`api_auth_dependency.py`) use same format

## Estimate

1.5 days (as per task document)

# C5: Rate Limiting on Order Submission - Implementation Plan

## Objective
Add rate limiting to order submission and signal generation endpoints using per-user/principal buckets to prevent abuse and protect trading infrastructure.

## Critical Dependencies

**⚠️ C5 MUST remain in `log_only` mode until C6 (API Authentication) is deployed.**

- C5 relies on `request.state.user` for per-user rate limiting
- Without C6, endpoints lack authentication → `request.state.user` is empty
- This would force all traffic into strict Anonymous bucket (6/min), causing outages
- **Enforcement gate:** C5 can only switch to `RATE_LIMIT_MODE=enforce` AFTER C6 is verified in production

## Current State Analysis

### Existing Rate Limiter
- `libs/web_console_auth/rate_limiter.py` provides a robust Redis-backed sliding window rate limiter
- Uses Lua script for atomic check-and-increment
- Has Prometheus metrics (`rate_limit_checks_total`, `rate_limit_redis_errors_total`) with labels `[action, result]`
- Uses Redis DB 2 with `decode_responses=True`
- Supports fallback mode (deny/allow) on Redis failure

### Endpoints Requiring Rate Limiting
| Endpoint | File | Line | Current Status |
|----------|------|------|----------------|
| `POST /api/v1/orders` | execution_gateway/main.py | 2183 | NO rate limiting |
| `POST /api/v1/orders/slice` | execution_gateway/main.py | 3319 | NO rate limiting |
| `POST /api/v1/signals/generate` | signal_service/main.py | 1275 | NO rate limiting |

## Implementation Approach

### 1. Proxy Headers Configuration (Required for IP Resolution)

**CRITICAL: Configure proxy headers BEFORE deployment to avoid IP bucket collisions.**

In containerized environments (Docker/K8s), `request.client.host` resolves to the Ingress Controller's internal IP unless properly configured.

```python
# apps/execution_gateway/main.py - Add ProxyHeadersMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import os

# SECURITY: Restrict trusted_hosts to known ingress/load balancer IPs
# Never use ["*"] in production - allows IP spoofing via X-Forwarded-For
TRUSTED_PROXY_HOSTS = os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1").split(",")

app = FastAPI(...)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXY_HOSTS)
```

**SECURITY: ProxyHeadersMiddleware trusted_hosts Configuration**
- **NEVER use `trusted_hosts=["*"]`** - allows untrusted clients to spoof X-Forwarded-For
- Configure `TRUSTED_PROXY_HOSTS` env var with actual ingress/LB IPs (comma-separated)
- Default to `127.0.0.1` (localhost only) for safety
- Example: `TRUSTED_PROXY_HOSTS=10.0.0.1,10.0.0.2` for K8s ingress controllers

**Verification task:** Before C5 deployment, verify that `request.client.host` returns real client IPs, not ingress/LB IPs.

### 2. Key Identity Strategy (Per-User Buckets)

**Per AC5 requirement: Rate limiting must use per-user buckets, NOT IP-based limiting.**

**IMPORTANT:** Principal extraction MUST use only verified identity from `request.state.user` (set by auth middleware). Never decode unverified JWT claims for rate limiting keys.

```python
# libs/common/rate_limit_dependency.py

def get_principal_key(request: Request) -> tuple[str, str]:
    """
    Extract principal key for rate limiting.

    SECURITY: Only use verified identity from request.state.user (set by auth middleware).
    Never decode unverified JWT claims - this could allow bucket evasion.

    Returns:
        Tuple of (key, principal_type) for metrics labeling.

    Priority order (highest to lowest):
    1. Authenticated user ID from request.state.user (verified by auth middleware)
    2. Strategy ID from request.state.strategy (verified by S2S auth)
    3. IP address as last resort (unauthenticated endpoints only)
    """
    # Authenticated user from verified session/JWT (auth middleware sets this)
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        user_id = user.get("user_id") or user.get("sub")
        if user_id:
            return f"user:{user_id}", "user"

    # Strategy ID from verified S2S auth (set by internal auth middleware)
    if hasattr(request.state, "strategy_id") and request.state.strategy_id:
        return f"strategy:{request.state.strategy_id}", "strategy"

    # Fallback to IP (only for truly unauthenticated endpoints)
    # NOTE: Requires ProxyHeadersMiddleware for accurate client IP
    if request.client:
        return f"ip:{request.client.host}", "ip"

    return "ip:unknown", "ip"
```

**Anonymous Traffic Handling:**
```python
# When auth middleware fails to populate request.state.user
# Anonymous traffic is rate-limited separately with stricter limits
ANONYMOUS_RATE_LIMIT_FACTOR = float(os.getenv("ANONYMOUS_RATE_LIMIT_FACTOR", "0.1"))
# e.g., if per-user is 60/min, anonymous gets 6/min per IP
```

**Internal Service Bypass (mTLS/JWT Only - NO Static Tokens):**
```python
# For internal S2S calls, ONLY use verified identity methods:
# 1. mTLS client certificate (verified by ingress/proxy)
# 2. JWT with internal service audience claim (verified by auth middleware)
#
# SECURITY: Static bypass tokens are NOT allowed due to abuse risk.

def should_bypass_rate_limit(request: Request) -> bool:
    """
    Check if request is from trusted internal service.

    SECURITY: Only verified identity methods allowed.
    - mTLS client cert (request.state.mtls_verified)
    - JWT with 'internal-service' audience (request.state.user.aud)
    """
    # Check for mTLS verified internal service
    if getattr(request.state, "mtls_verified", False):
        if getattr(request.state, "mtls_service_name", None):
            rate_limit_bypass_total.labels(method="mtls").inc()
            return True

    # Check for internal service JWT audience (verified by auth middleware)
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        if user.get("aud") == "internal-service":
            rate_limit_bypass_total.labels(method="jwt_audience").inc()
            return True

    return False
```

### 3. Rate Limit Configuration

#### Threshold Derivation

Based on:
- **Alpaca API limits: 200 requests/minute** (for paper trading account)
- Expected production traffic: 5-10 orders/minute per strategy during market hours
- **CRITICAL:** Global limit MUST stay well below broker ceiling to account for:
  - Slice fan-out (1 slice request → multiple broker orders)
  - Retry storms
  - Concurrent users
- Safety margin: 60% of broker limit for global orders, accounting for slice multiplier

| Endpoint | Action | Per-User Limit | Burst Buffer | Global Limit | Window | Notes |
|----------|--------|---------------|--------------|--------------|--------|-------|
| Order submission | `order_submit` | 40/min | +10 | 80/min | 60s | Direct orders only |
| Sliced orders | `order_slice` | 10/min | +3 | 30/min | 60s | Assumes ~3x slice fan-out |
| Signal generation | `signal_generate` | 30/min | +10 | 160/min | 60s | No broker calls |

**Slice Fan-Out Calculation:**
- Slice endpoint creates 1 parent + N child orders
- Typical TWAP: 5-10 slices per parent
- Global 30 slice requests/min × 3 avg broker orders = 90 broker orders/min
- Combined with direct orders (80/min) = 170/min theoretical max
- **Always < 200/min Alpaca ceiling with 30 orders/min safety margin**

**Configurable Parameters per Action:**
```python
@dataclass
class RateLimitConfig:
    action: str
    max_requests: int
    window_seconds: int = 60
    burst_buffer: int = 0  # Extra allowance (effectively raises limit)
    fallback_mode: str = "deny"  # "deny" or "allow"
    global_limit: int | None = None  # Optional global cap across all users
    anonymous_factor: float = 0.1  # Multiplier for anonymous traffic
```

**Burst Buffer Clarification:**
The `burst_buffer` is NOT true token-bucket burst shaping. It simply raises the effective limit to `max_requests + burst_buffer` over the same window.

### 4. Global Limit Implementation (Using Redis TIME)

**CRITICAL: Use Redis server TIME to avoid clock skew between app instances.**

```python
# libs/common/rate_limit_dependency.py

_RATE_LIMIT_WITH_GLOBAL_SCRIPT = """
-- Use Redis server time to avoid clock skew
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1])

local key = KEYS[1]
local global_key = KEYS[2]
local window = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local global_limit = tonumber(ARGV[3])
local member = ARGV[4]

-- Check global limit first
if global_key ~= "" then
    redis.call('ZADD', global_key, now, member)
    redis.call('ZREMRANGEBYSCORE', global_key, 0, now - window)
    local global_count = redis.call('ZCARD', global_key)
    redis.call('EXPIRE', global_key, window)
    if global_count > global_limit then
        return {-1, global_count, now}  -- Global limit exceeded
    end
end

-- Check per-user limit
redis.call('ZADD', key, now, member)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, window)

return {count, 0, now}
"""

async def check_rate_limit_with_global(
    redis_client: redis.Redis,
    user_id: str,
    action: str,
    config: RateLimitConfig,
) -> tuple[bool, int, str]:
    """
    Check rate limit with global cap using Redis server time.

    Uses the SAME Redis client settings as existing rate limiter (DB 2, decode_responses=True).

    Returns:
        Tuple of (allowed, remaining, rejection_reason)
    """
    key = f"rl:{action}:{user_id}"
    global_key = f"rl:{action}:global" if config.global_limit else ""
    member = f"{user_id}:{time.time_ns()}"  # Unique member per request

    effective_limit = config.max_requests + config.burst_buffer

    result = await redis_client.eval(
        _RATE_LIMIT_WITH_GLOBAL_SCRIPT,
        2,
        key,
        global_key,
        str(config.window_seconds),
        str(effective_limit),
        str(config.global_limit or 0),
        member,
    )

    count, global_flag, _redis_time = result
    if count == -1:
        return False, 0, "global_limit_exceeded"

    allowed = count <= effective_limit
    remaining = max(0, effective_limit - count)
    reason = "" if allowed else "per_user_limit_exceeded"
    return allowed, remaining, reason
```

### 5. Redis Client Consistency

**CRITICAL: Use the same Redis client configuration as existing rate limiter.**

```python
# libs/common/rate_limit_dependency.py

from libs.web_console_auth.rate_limiter import get_rate_limiter

def _get_redis_client() -> redis.Redis:
    """
    Get Redis client with SAME configuration as existing rate limiter.
    - DB 2
    - decode_responses=True
    - Connection timeout settings
    """
    limiter = get_rate_limiter()
    return limiter.redis
```

### 6. Redis Resilience

```python
# Circuit breaker for Redis latency with connection timeout
REDIS_LATENCY_THRESHOLD_MS = int(os.getenv("RATE_LIMIT_REDIS_LATENCY_THRESHOLD", "50"))

async def check_rate_limit_with_circuit_breaker(
    user_id: str,
    action: str,
    config: RateLimitConfig,
) -> tuple[bool, int, str]:
    """Check rate limit with circuit breaker for Redis latency."""
    redis_client = _get_redis_client()
    try:
        result = await asyncio.wait_for(
            check_rate_limit_with_global(redis_client, user_id, action, config),
            timeout=REDIS_LATENCY_THRESHOLD_MS / 1000,
        )
        return result
    except asyncio.TimeoutError:
        rate_limit_redis_timeout_total.labels(action=action).inc()
        logger.warning("rate_limit_redis_timeout", extra={"action": action})
        if config.fallback_mode == "deny":
            return False, 0, "redis_timeout"
        return True, config.max_requests, ""
    except Exception as exc:
        rate_limit_redis_errors_total.labels(action=action).inc()
        logger.error("rate_limit_redis_error", extra={"action": action, "error": str(exc)})
        if config.fallback_mode == "deny":
            return False, 0, "redis_error"
        return True, config.max_requests, ""
```

### 7. Mode Configuration (Per-Request Read)

```python
# Read mode per-request to allow hot-switching without restart
def _get_rate_limit_mode() -> str:
    """Get current rate limit mode. Read per-request for hot-switch support."""
    return os.getenv("RATE_LIMIT_MODE", "log_only")  # Default to log_only until C6 deployed
```

### 8. Metrics (Avoid Label Conflicts)

**CRITICAL: Use NEW metric names to avoid Prometheus label conflicts with existing metrics.**

```python
# libs/common/rate_limit_dependency.py

from prometheus_client import Counter

# NEW metrics with different names to avoid conflict with existing rate_limit_checks_total
rate_limit_api_checks_total = Counter(
    "rate_limit_api_checks_total",
    "API rate limit checks (order/signal endpoints)",
    ["action", "result", "principal_type"]
)

rate_limit_bypass_total = Counter(
    "rate_limit_bypass_total",
    "Rate limit bypasses for internal services",
    ["method"]
)

rate_limit_redis_timeout_total = Counter(
    "rate_limit_redis_timeout_total",
    "Redis timeouts during rate limit checks",
    ["action"]
)

# Import existing metrics for Redis errors (already has correct labels)
from libs.web_console_auth.rate_limiter import rate_limit_redis_errors_total
```

### 9. Response Headers

```python
# Response headers for rate limit info - emitted in BOTH log_only and enforce modes
response.headers["X-RateLimit-Limit"] = str(effective_limit)
response.headers["X-RateLimit-Remaining"] = str(remaining)
response.headers["X-RateLimit-Window"] = str(config.window_seconds)
```

### 10. FastAPI Rate Limit Dependency

```python
# libs/common/rate_limit_dependency.py

from fastapi import Depends, HTTPException, Request, Response

def rate_limit(config: RateLimitConfig):
    """FastAPI dependency for rate limiting."""
    async def dependency(request: Request, response: Response):
        # Check for internal service bypass (mTLS/JWT only, no static tokens)
        if should_bypass_rate_limit(request):
            effective_limit = config.max_requests + config.burst_buffer
            # Still emit headers even for bypass
            response.headers["X-RateLimit-Limit"] = str(effective_limit)
            response.headers["X-RateLimit-Remaining"] = str(effective_limit)
            response.headers["X-RateLimit-Window"] = str(config.window_seconds)
            return effective_limit

        key, principal_type = get_principal_key(request)

        # Apply anonymous factor if IP-based
        effective_config = config
        if principal_type == "ip":
            effective_config = RateLimitConfig(
                action=config.action,
                max_requests=int(config.max_requests * config.anonymous_factor),
                window_seconds=config.window_seconds,
                burst_buffer=int(config.burst_buffer * config.anonymous_factor),
                fallback_mode=config.fallback_mode,
                global_limit=config.global_limit,
                anonymous_factor=config.anonymous_factor,
            )

        allowed, remaining, rejection_reason = await check_rate_limit_with_circuit_breaker(
            user_id=key,
            action=config.action,
            config=effective_config,
        )

        effective_limit = effective_config.max_requests + effective_config.burst_buffer

        # ALWAYS add response headers (both log_only and enforce modes)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Window"] = str(effective_config.window_seconds)

        # ALWAYS emit metrics (both log_only and enforce modes)
        rate_limit_api_checks_total.labels(
            action=config.action,
            result="blocked" if not allowed else "allowed",
            principal_type=principal_type,
        ).inc()

        if not allowed:
            mode = _get_rate_limit_mode()  # Read per-request
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "action": config.action,
                    "key": key,
                    "principal_type": principal_type,
                    "mode": mode,
                    "rejection_reason": rejection_reason,
                    "request_id": getattr(request.state, "request_id", None),
                },
            )

            if mode == "enforce":
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limited",
                        "message": "Too many requests",
                        "retry_after": effective_config.window_seconds,
                        "reason": rejection_reason,
                    },
                    headers={"Retry-After": str(effective_config.window_seconds)},
                )

        return remaining
    return dependency
```

### 11. Apply to Endpoints

#### Execution Gateway (apps/execution_gateway/main.py)

```python
from libs.common.rate_limit_dependency import rate_limit, RateLimitConfig

# Conservative limits: 80 direct + 30 slices × 3 = 170 broker orders/min (< 200 Alpaca ceiling)
ORDER_SUBMIT_LIMIT = int(os.getenv("ORDER_SUBMIT_RATE_LIMIT", "40"))
ORDER_SLICE_LIMIT = int(os.getenv("ORDER_SLICE_RATE_LIMIT", "10"))

order_submit_rl = rate_limit(RateLimitConfig(
    action="order_submit",
    max_requests=ORDER_SUBMIT_LIMIT,
    window_seconds=60,
    burst_buffer=10,
    fallback_mode="deny",
    global_limit=80,  # Direct orders only
))

order_slice_rl = rate_limit(RateLimitConfig(
    action="order_slice",
    max_requests=ORDER_SLICE_LIMIT,
    window_seconds=60,
    burst_buffer=3,
    fallback_mode="deny",
    global_limit=30,  # 30 × 3 fan-out = 90 broker orders
))

@app.post("/api/v1/orders", response_model=OrderResponse, tags=["Orders"])
async def submit_order(
    order: OrderRequest,
    response: Response,
    _remaining: int = Depends(order_submit_rl),
) -> OrderResponse:
    ...

@app.post("/api/v1/orders/slice", response_model=SlicingPlan, tags=["Orders"])
async def submit_sliced_order(
    request: SlicingRequest,
    response: Response,
    _remaining: int = Depends(order_slice_rl),
) -> SlicingPlan:
    ...
```

#### Signal Service (apps/signal_service/main.py)

```python
from libs.common.rate_limit_dependency import rate_limit, RateLimitConfig

SIGNAL_GENERATE_LIMIT = int(os.getenv("SIGNAL_GENERATE_RATE_LIMIT", "30"))

signal_generate_rl = rate_limit(RateLimitConfig(
    action="signal_generate",
    max_requests=SIGNAL_GENERATE_LIMIT,
    window_seconds=60,
    burst_buffer=10,
    fallback_mode="deny",
    global_limit=160,  # No broker calls, can be higher
))

@app.post("/api/v1/signals/generate", ...)
async def generate_signals(
    request: SignalRequest,
    response: Response,
    _remaining: int = Depends(signal_generate_rl),
) -> SignalResponse:
    ...
```

## Files to Create

| File | Description |
|------|-------------|
| `libs/common/rate_limit_dependency.py` | FastAPI rate limit dependency with principal extraction, global limits |

## Files to Modify

| File | Changes |
|------|---------|
| `apps/execution_gateway/main.py` | Add rate limiting to order endpoints, add ProxyHeadersMiddleware |
| `apps/signal_service/main.py` | Add rate limiting to signal endpoint, add ProxyHeadersMiddleware |

## Test Cases

### Unit Tests (`tests/libs/common/test_rate_limit_dependency.py`)

1. **Metric Initialization:**
   - Test: No Prometheus ValueError when importing alongside existing rate limiter
   - Test new metrics use distinct names from existing metrics

2. **Principal Extraction (Verified Identity Only):**
   - Test authenticated user ID extracted from `request.state.user`
   - Test strategy ID extracted from `request.state.strategy_id`
   - Test IP fallback for unauthenticated requests
   - Test: Unverified Authorization header is IGNORED (no JWT decode without verification)
   - Test: Forged Authorization header on unauthenticated request uses IP bucket
   - **Test: Switch from IP-based to User-based limits when Auth is present**

3. **Rate Limiting Behavior:**
   - Test rate limit blocks after threshold
   - Test 429 response with Retry-After header
   - Test log-only mode allows requests but logs
   - Test mode hot-switch (log_only → enforce) per-request read

4. **Global Limits:**
   - **Test: Global limit fires BEFORE per-user limit when global exhausted first**
   - Test per-user limit blocks when exceeded even if global under limit
   - Test global limit aligned with broker ceiling (80 + 30*3 = 170 < Alpaca 200)

5. **Burst Buffer:**
   - Test burst buffer raises effective limit to (max_requests + burst_buffer)
   - Test effective limit resets after window expires

6. **User Isolation:**
   - Test: Two users sharing same IP are NOT blocked by each other
   - Test: One user across multiple IPs is still rate limited globally

7. **Redis Resilience:**
   - Test Redis timeout triggers fallback (deny mode)
   - Test Redis timeout triggers fallback (allow mode)
   - Test Redis client uses same config as existing limiter (DB 2)
   - **Test: Redis TIME is used (not app time) for clock skew handling**

8. **Response Headers:**
   - Test X-RateLimit-Limit header present for each action
   - Test X-RateLimit-Remaining header accurate
   - Test X-RateLimit-Window header present
   - **Test: Headers emitted in BOTH log_only and enforce modes**
   - **Test: Headers emitted even with fallback_mode=allow**

9. **Internal Service Bypass:**
   - Test mTLS verified request bypasses rate limit
   - Test JWT with internal audience bypasses rate limit
   - Test: No static token bypass allowed (security)
   - Test bypass usage metric is emitted
   - **Test: Headers still emitted for bypassed requests**

10. **Metrics:**
    - Test rate_limit_api_checks_total (new name) includes principal_type label
    - Test rate_limit_bypass_total includes method label
    - Test 429 logging includes request_id
    - **Test: Metrics emitted in BOTH log_only and enforce modes**

11. **Slice Fan-Out:**
    - **Test: Slice requests producing multiple downstream orders don't breach broker cap**
    - **Test: Global slice limit × avg fan-out < broker limit**

### Integration Tests

**`tests/apps/execution_gateway/test_rate_limiting.py`:**
- Test order submission rate limiting with authenticated user
- Test sliced order rate limiting
- Test Redis failure fallback (deny mode blocks orders)
- Test global limit enforcement across multiple users
- **Test: ProxyHeadersMiddleware correctly resolves client IP**

**`tests/apps/signal_service/test_rate_limiting.py`:**
- Test signal generation rate limiting with S2S auth

### Load Tests (Pre-Enforcement)

**`tests/load/test_rate_limit_market_open.py`:**
- Simulate market-open burst: 50 orders in 30 seconds
- Verify legitimate burst traffic not blocked
- Measure p99 latency overhead from rate limiting
- Validate thresholds before enforcement
- Verify global limit stays below Alpaca's 200/min

## Rollout Strategy

### Prerequisite: C6 (API Authentication) Must Be Deployed First

**⚠️ C5 enforcement BLOCKED until C6 is verified in production.**

### Phase A: Deploy in Log-Only Mode (Pre-C6)
1. Deploy C5 with `RATE_LIMIT_MODE=log_only` (default)
2. Verify ProxyHeadersMiddleware correctly resolves client IPs
3. Monitor logs for rate limit violations (all traffic hits IP bucket)
4. **Gate:** Do NOT proceed to enforcement until C6 is deployed

### Phase B: Log-Only with Auth (Post-C6)
1. After C6 deployed, verify `request.state.user` is populated
2. Monitor logs for 48-72h with proper user identification
3. Analyze traffic patterns and adjust thresholds if needed

**Go/No-Go Gate for Phase C:**
| Metric | Threshold | Required |
|--------|-----------|----------|
| 429 rate (log_only) | < 0.5% | ✓ |
| p99 RL latency | < 10ms | ✓ |
| Broker 429s | = 0 | ✓ |
| User ID resolution | > 95% authenticated | ✓ |

### Phase C: Enforcement
1. Enable `RATE_LIMIT_MODE=enforce`
2. Monitor 429 rate closely
3. Be ready to revert to log_only if issues arise

**Rollback Trigger:**
| Metric | Threshold | Action |
|--------|-----------|--------|
| 429 rate | > 1% of legitimate traffic | Revert to log_only mode |
| Redis latency p99 | > 100ms | Enable fallback allow mode |
| Redis errors | > 5% of rate limit checks | Enable fallback allow mode |
| Broker 429s | > 0 | Immediately lower global limits |

### Rollback Procedure
```bash
# Single-switch rollback
export RATE_LIMIT_MODE=log_only

# If limits changed materially, clear Redis keys
redis-cli -n 2 KEYS "rl:*" | xargs redis-cli -n 2 DEL
```

### Monitoring & Alerting (Pre-Create Before Rollout)

**Grafana Dashboard:**
- Rate limit checks by action (allowed vs blocked)
- Rate limit checks by principal_type (user, strategy, ip)
- 429 response rate by endpoint
- Redis errors and timeouts
- Per-user violation distribution
- Bypass usage by method (mtls vs jwt_audience)
- Global vs per-user limit exhaustion ratio

**Alerts:**
- `rate_limit_429_rate_high`: > 0.5% of traffic returns 429 for 5min
- `rate_limit_redis_errors_high`: > 10 Redis errors in 1min
- `rate_limit_redis_latency_high`: p99 > 50ms for 5min
- `rate_limit_missing_headers`: any 2xx response missing X-RateLimit-* headers
- `alpaca_429_rate`: any Alpaca 429 response (broker limit breach)

## Success Metrics

- 429 rate < 0.1% of legitimate traffic after enforcement
- No increase in p99 latency (< 10ms overhead)
- Rate limit metrics visible in Grafana
- Zero user complaints about false positive rate limiting
- **Zero Alpaca 429 errors (our limits stay below broker ceiling)**

## Dependencies

- Existing `libs/web_console_auth/rate_limiter.py` (reuse Redis client)
- Redis must be available (fails-closed on Redis down by default)
- **C6 (API Authentication) must be deployed before enforcement**
- ProxyHeadersMiddleware must be configured for accurate client IP

## Edge Cases Addressed

1. **Clock Skew:** Lua script uses Redis TIME, not app time
2. **Multi-Instance Keys:** Redis provides atomic operations across instances
3. **Async Safety:** All Redis calls are async-safe via `redis.asyncio`
4. **X-Forwarded-For Spoofing:** Explicitly NOT used; principal from verified auth context
5. **Anonymous Traffic:** Separately bucketed with stricter limits (10% of authenticated)
6. **Mode Hot-Switch:** Read env per-request, no restart needed
7. **Prometheus Label Conflict:** Use new metric name `rate_limit_api_checks_total`
8. **Redis Client Drift:** Reuse existing rate limiter's Redis client (DB 2, decode_responses)
9. **Broker Limit Alignment:** 80 direct + 30×3 slice = 170 < Alpaca 200/min with 30-order safety margin
10. **Unverified JWT:** Only use verified identity from `request.state.user`
11. **C5/C6 Sequencing:** C5 log_only until C6 deployed
12. **Proxy Headers:** ProxyHeadersMiddleware with restricted trusted_hosts (not "*") for accurate client IP
13. **Slice Fan-Out:** Global slice limit accounts for downstream orders
14. **Trusted Hosts Security:** ProxyHeadersMiddleware configured with explicit ingress IPs to prevent X-Forwarded-For spoofing

## Estimated Effort

- Implementation: 0.5 day
- Testing (including load test): 0.4 day
- Dashboard/Alerts: 0.1 day
- **Total: 1 day**

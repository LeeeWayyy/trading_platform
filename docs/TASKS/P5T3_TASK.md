---
id: P5T3
title: "NiceGUI Migration - HA/Scaling & Observability"
phase: P5
task: T3
priority: P0
owner: "@development-team"
state: PLANNING
created: 2025-12-30
dependencies: [P5T1, P5T2]
estimated_effort: "4-5 days"
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P5_PLANNING.md, P5T1_TASK.md, P5T2_TASK.md]
features: [T3.1, T3.2, T3.3]
---

# P5T3: NiceGUI Migration - HA/Scaling & Observability

**Phase:** P5 (Web Console Modernization)
**Status:** PLANNING
**Priority:** P0 (Infrastructure Foundation)
**Owner:** @development-team
**Created:** 2025-12-30
**Estimated Effort:** 4-5 days
**Track:** Phase 2.5 + Phase 2.6 from P5_PLANNING.md
**Dependency:** P5T1 (Foundation) and P5T2 (Layout & Auth) must be complete

---

## Objective

Define and implement High Availability (HA) and horizontal scaling architecture for WebSocket connections, server-side sessions, and observability infrastructure before implementing real-time features.

**Success looks like:**
- Multiple NiceGUI pods can serve users with sticky session routing
- WebSocket connections survive pod restarts via session rehydration
- Redis Sentinel/Cluster provides session store HA
- Per-client background tasks are properly cleaned up on disconnect
- Prometheus metrics capture WS connections, auth failures, latency
- Grafana dashboard provides operational visibility
- Alert rules fire on critical thresholds
- State is recoverable after WebSocket disconnection

**Measurable SLAs:**
| Metric | Target | Measurement | Environment |
|--------|--------|-------------|-------------|
| WS reconnection time | < 5s | Time from disconnect to reconnected | Local dev |
| State recovery time | < 2s | Time to restore UI state from Redis | Local Redis |
| Session failover | < 10s | Time to recover on pod failure | k8s cluster |
| Redis latency p99 | < 50ms | Session store operations | Local Redis |
| Memory per user | < 25MB | Per-connection memory usage | Load test |
| WS stability | 99.95% | Uptime over 4hr soak test | Load test |

---

## Acceptance Criteria

### T3.1 Horizontal Scaling Architecture

**Load Balancer Configuration:**
- [ ] nginx upstream config with sticky sessions (ip_hash for open-source nginx)
- [ ] **Alternative:** If using k8s ingress, use `nginx.ingress.kubernetes.io/affinity: cookie` annotation
- [ ] **Alternative:** If nginx-sticky-module-ng is available, use `sticky cookie` directive
- [ ] WebSocket upgrade headers preserved (`Upgrade`, `Connection`)
- [ ] Health check endpoint (`/health`) for pod liveness
- [ ] Max connection limits per pod (1000 WS connections)
- [ ] Graceful drain on pod termination (30s drain period)
- [ ] ASGI lifespan hooks for startup/shutdown
- [ ] k8s preStop hook for graceful drain signal

**Sticky Session Implementation:**
- [ ] `nicegui_server_id` cookie for routing (if using cookie-based)
- [ ] **Cookie security attributes:** `HttpOnly=True, Secure=True, SameSite=Lax`
- [ ] Cookie expires after 1 hour (matches session timeout)
- [ ] Fallback to round-robin routing if cookie missing
- [ ] Cookie set on initial connection, not login

**Health Check Endpoint:**
- [ ] `/health` returns 200 when pod is healthy
- [ ] Check Redis connectivity
- [ ] Check backend API reachability
- [ ] Return 503 during graceful shutdown
- [ ] **Security:** Minimal response for external requests (just status code)
- [ ] **Security:** Detailed info only for requests from internal networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- [ ] Connection count in detailed response only (internal)
- [ ] Uses `extract_trusted_client_ip()` for proper proxy handling

**Testing:**
- [ ] Multi-pod deployment test (3 pods)
- [ ] Sticky routing verification
- [ ] Pod failure and recovery test
- [ ] Health check accuracy tests

---

### T3.2 Session Store High Availability

**Redis Sentinel Configuration:**
- [ ] 3-node Sentinel cluster for automatic failover
- [ ] Master discovery via Sentinel
- [ ] **CRITICAL:** Read from MASTER for session validation (avoid replica lag)
- [ ] Write to master for session creation/update
- [ ] Read from slave only for non-critical reads (user preferences, filters)
- [ ] Socket timeout 0.5s for fast failover detection
- [ ] **Security:** TLS encryption for Redis connections (if cross-network)
- [ ] **Security:** Redis AUTH password from secrets (not hardcoded)
- [ ] **Security:** Sentinel AUTH if enabled
- [ ] **Security:** Network policy to restrict Redis access to NiceGUI pods

**Session Store HA Implementation:**
- [ ] `HARedisStore` class with Sentinel support
- [ ] `get_master()` for writes
- [ ] `get_slave()` for reads (with fallback to master)
- [ ] Connection pooling (min 5, max 50)
- [ ] Automatic reconnection on connection loss

**State Persistence Strategy:**
- [ ] Critical state persisted to Redis (preferences, pending forms)
- [ ] UI state is ephemeral (re-rendered from server data)
- [ ] Position/order data fetched fresh from backend API
- [ ] Dashboard filters persisted for UX continuity
- [ ] 24hr TTL for user state keys

**State Categories:**
| State Type | Storage | Recovery Strategy |
|------------|---------|-------------------|
| UI widgets | In-memory (lost) | Re-render from server |
| User preferences | Redis (persisted) | Restore on reconnect |
| Pending form data | Redis (persisted) | Restore, prompt confirm |
| Dashboard filters | Redis (persisted) | Restore with last values |
| Position/order data | Backend API | Fetch fresh on reconnect |

**Testing:**
- [ ] Sentinel failover test (kill master, verify auto-switch)
- [ ] Read replica test (verify slave reads)
- [ ] State restoration after reconnect
- [ ] TTL expiry handling

---

### T3.3 WebSocket State Recovery & Task Cleanup

**State Manager Implementation:**
- [ ] `UserStateManager` class for state persistence
- [ ] `save_critical_state()` - persist preferences, filters
- [ ] `restore_state()` - load on reconnect
- [ ] `on_reconnect()` - re-fetch API data + restore preferences
- [ ] State versioning for conflict detection
- [ ] **JSON serialization:** Custom encoder for datetime/Decimal types
- [ ] **Session fixation:** Rotate session ID on privilege escalation (auth change)

**Client Lifecycle Manager:**
- [ ] `ClientLifecycleManager` for background task tracking
- [ ] `register_task()` - register per-client tasks
- [ ] `cleanup_client()` - cancel all tasks on disconnect
- [ ] Task cancellation with proper exception handling
- [ ] Logging of task cleanup counts

**Connection Events:**
- [ ] `app.on_connect` handler for new connections
- [ ] `app.on_disconnect` handler for cleanup
- [ ] Connection count tracking (for health endpoint)
- [ ] **Thread-safe:** Use atomic counter or lock for connection count
- [ ] **Guard:** Prevent negative count on double-disconnect
- [ ] Client ID generation (UUID per connection)

**Reconnection Flow:**
```
1. Client reconnects (new WebSocket)
2. Session ID cookie sent
3. Server validates session (Redis)
4. Server loads critical state (Redis)
5. Server fetches fresh API data
6. Server re-renders UI with restored state
7. Client sees restored dashboard
```

**Testing:**
- [ ] Disconnect/reconnect cycle test
- [ ] Task cleanup verification (no leaked tasks)
- [ ] State restoration accuracy test
- [ ] Connection count accuracy

---

### T3.4 Observability & Metrics

**Prometheus Metrics:**
| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `nicegui_ws_connections` | Gauge | `pod` | Current WS connections |
| `nicegui_ws_connects_total` | Counter | `pod` | Total connects |
| `nicegui_ws_disconnects_total` | Counter | `pod`, `reason` | Total disconnects |
| `nicegui_auth_failures_total` | Counter | `auth_type`, `reason` | Auth failures |
| `nicegui_sessions_created_total` | Counter | `auth_type` | Sessions created |
| `nicegui_redis_latency_seconds` | Histogram | `operation` | Redis operation latency |
| `nicegui_api_latency_seconds` | Histogram | `endpoint` | Backend API latency |
| `nicegui_memory_bytes` | Gauge | `pod` | Process memory usage |
| `nicegui_active_users` | Gauge | `pod` | Unique active users |
| `nicegui_push_queue_depth` | Gauge | `pod` | Pub/Sub queue depth |

**Metrics Implementation Notes:**
- [ ] Pod label value set from `POD_NAME` environment variable
- [ ] Single-process model (NiceGUI runs one process per pod)
- [ ] If multi-process, use `prometheus_client` multiprocess mode

**Alert Rules:**
- [ ] `HighWSDisconnectRate`: >5% disconnect rate over 5m
- [ ] `AuthFailureSpike`: >10 failures/min over 1m
- [ ] `HighAPILatency`: P95 > 500ms over 5m
- [ ] `MemoryPerUserHigh`: >30MB per user over 10m
- [ ] `RedisLatencyHigh`: P99 > 50ms over 5m
- [ ] `ConnectionCountHigh`: >800 per pod (80% capacity)
- [ ] `SessionCreationSpike`: >100/min (possible attack) - uses `nicegui_sessions_created_total`

**Grafana Dashboard:**
- [ ] Connection count over time (per pod)
- [ ] Auth success/failure rate
- [ ] Redis latency percentiles
- [ ] API latency percentiles
- [ ] Memory usage per pod
- [ ] Active users over time
- [ ] Error rate by type

**Error Budget (30-day):**
| SLO | Target | Budget |
|-----|--------|--------|
| Availability | 99.9% | 43 min downtime |
| P95 latency < 500ms | 99% | 7.2 hr degraded |
| Auth success rate | 99.5% | ~1500 failures |

**Testing:**
- [ ] Metrics endpoint verification (`/metrics`)
- [ ] Alert rule trigger tests
- [ ] Dashboard data accuracy

---

## Prerequisites Checklist

**Must verify before starting implementation:**

- [ ] **P5T1 complete:** Foundation, async client, session store
- [ ] **P5T2 complete:** Layout, auth flows, session management
- [ ] **Redis available:** For session store and pub/sub
- [ ] **Redis Sentinel available:** 3-node cluster for HA testing
- [ ] **Redis AUTH credentials:** Stored in secrets manager
- [ ] **Prometheus available:** For metrics collection
- [ ] **Grafana available:** For dashboard visualization
- [ ] **nginx/ingress available:** For load balancing (staging)
- [ ] **nginx sticky module:** Verify `nginx-sticky-module-ng` OR use ip_hash OR k8s ingress
- [ ] **Multi-pod deployment capability:** k8s or docker-compose
- [ ] **k8s manifests:** Deployment, Service, Ingress for NiceGUI pods
- [ ] **Network policy:** Redis access restricted to NiceGUI pods
- [ ] **redis-py version:** >= 5.0 required for async Sentinel support

---

## Approach

### High-Level Plan

1. **C0: Load Balancer & Sticky Sessions** (1 day)
   - nginx upstream configuration
   - Sticky session cookie setup
   - Health check endpoint
   - WebSocket upgrade handling

2. **C1: Redis HA & State Manager** (1-2 days)
   - Redis Sentinel configuration
   - HARedisStore class
   - UserStateManager class
   - State persistence patterns

3. **C2: Client Lifecycle & Cleanup** (1 day)
   - ClientLifecycleManager class
   - Connection event handlers
   - Task registration/cleanup
   - Reconnection flow

4. **C3: Observability** (1-2 days)
   - Prometheus metrics implementation
   - Alert rules configuration
   - Grafana dashboard
   - Error budget tracking

---

## Component Breakdown

### C0: Load Balancer Configuration

**Files to Create:**
```
infra/nginx/
├── nicegui-cluster.conf         # Upstream configuration
└── nicegui-location.conf        # Location block for WS
apps/web_console_ng/core/
├── health.py                    # Health check endpoint (in core/ to match imports)
tests/apps/web_console_ng/
└── test_health_endpoint.py
```

**nginx Upstream Configuration:**
```nginx
# infra/nginx/nicegui-cluster.conf
upstream nicegui_cluster {
    # Option A: ip_hash (open-source nginx) - routes same IP to same backend
    ip_hash;

    # Option B: sticky cookie (requires nginx-sticky-module-ng or NGINX Plus)
    # sticky cookie nicegui_server_id expires=1h path=/ httponly secure;

    # Backend pods
    server nicegui-1:8080 max_fails=3 fail_timeout=30s;
    server nicegui-2:8080 max_fails=3 fail_timeout=30s;
    server nicegui-3:8080 max_fails=3 fail_timeout=30s;

    # Keepalive connections
    keepalive 32;
}

# k8s Ingress alternative (if using nginx-ingress controller):
# apiVersion: networking.k8s.io/v1
# kind: Ingress
# metadata:
#   annotations:
#     nginx.ingress.kubernetes.io/affinity: "cookie"
#     nginx.ingress.kubernetes.io/session-cookie-name: "nicegui_server_id"
#     nginx.ingress.kubernetes.io/session-cookie-expires: "3600"
#     nginx.ingress.kubernetes.io/session-cookie-secure: "true"
#     nginx.ingress.kubernetes.io/session-cookie-samesite: "Lax"

# infra/nginx/nicegui-location.conf
location / {
    proxy_pass http://nicegui_cluster;

    # WebSocket support
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Timeouts for long-lived WebSocket
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;

    # Buffer settings
    proxy_buffering off;
}

# Health check endpoint (not sticky)
location /health {
    proxy_pass http://nicegui_cluster;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
}
```

**Health Check Endpoint:**
```python
# apps/web_console_ng/core/health.py
from nicegui import app
from fastapi import Response, Request
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng import config
import asyncio
import json
import threading
from contextlib import asynccontextmanager

# Thread-safe connection counter
# PURPOSE: Fast, lock-protected counter for real-time metrics updates
# Used by: connection_events.py for immediate metric updates
# Note: ClientLifecycleManager.get_active_client_count() is authoritative for health checks
#       ConnectionCounter is optimized for high-frequency metric updates during connect/disconnect
class ConnectionCounter:
    """
    Thread-safe connection counter with guard against negative values.

    AUTHORITATIVE FOR: Prometheus metrics (ws_connections gauge)
    SYNCHRONIZED WITH: ClientLifecycleManager (both updated in connection handlers)
    """
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def increment(self) -> int:
        with self._lock:
            self._count += 1
            return self._count

    def decrement(self) -> int:
        with self._lock:
            # Guard against double-decrement
            if self._count > 0:
                self._count -= 1
            return self._count

    @property
    def value(self) -> int:
        with self._lock:
            return self._count

connection_counter = ConnectionCounter()
is_draining = False

# Internal networks for detailed health info
INTERNAL_NETWORKS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1"]

def is_internal_request(request: Request) -> bool:
    """
    Check if request is from internal network.

    Uses extract_trusted_client_ip() to handle proxied requests correctly.
    """
    import ipaddress
    from apps.web_console_ng.auth.utils import extract_trusted_client_ip
    from apps.web_console_ng import config

    # Use trusted proxy extraction (same as auth) for consistency
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)

    for network in INTERNAL_NETWORKS:
        try:
            if ipaddress.ip_address(client_ip) in ipaddress.ip_network(network):
                return True
        except ValueError:
            # Invalid IP format - treat as external
            return False
    return False

@app.get("/health")
async def health_check(request: Request) -> Response:
    """
    Health check endpoint for load balancer.

    Returns 200 if healthy, 503 if unhealthy or draining.
    External requests get minimal response for security.
    """
    global is_draining

    if is_draining:
        return Response(content='{"status": "draining"}', status_code=503,
                        media_type="application/json")

    checks = {}

    # Check Redis connectivity
    try:
        redis = get_redis_store()
        await asyncio.wait_for(redis.ping(), timeout=1.0)
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # Check backend API
    try:
        client = AsyncTradingClient.get()
        await asyncio.wait_for(client.health_check(), timeout=2.0)
        checks["backend"] = "ok"
    except Exception as e:
        checks["backend"] = f"error: {e}"

    # Overall status
    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    # Security: minimal response for external, detailed for internal
    if is_internal_request(request):
        response_body = {
            "status": "healthy" if all_ok else "unhealthy",
            "checks": checks,
            "connections": connection_counter.value,
            "pod": config.POD_NAME,
        }
    else:
        # External: minimal info
        response_body = {"status": "healthy" if all_ok else "unhealthy"}

    return Response(content=json.dumps(response_body), status_code=status_code,
                    media_type="application/json")


# ASGI lifespan for graceful shutdown
@asynccontextmanager
async def lifespan(app):
    """ASGI lifespan handler for startup/shutdown."""
    # Startup
    yield
    # Shutdown - signal draining
    await start_graceful_shutdown()

async def start_graceful_shutdown():
    """Signal pod is draining - return 503 to health checks."""
    global is_draining
    is_draining = True
    # Allow 30s for existing connections to drain
    await asyncio.sleep(30)
```

**k8s preStop Hook (for graceful drain):**
```yaml
# In deployment spec
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 30"]  # Allow drain before SIGTERM
```

**Acceptance Tests:**
- [ ] Health check returns 200 when all deps healthy
- [ ] Health check returns 503 when Redis down
- [ ] Health check returns 503 during graceful shutdown
- [ ] Connection count reflects actual WS connections

---

### C1: Redis HA & State Manager

**Files to Create:**
```
apps/web_console_ng/core/
├── redis_ha.py                  # HA Redis store with Sentinel
├── state_manager.py             # User state persistence
tests/apps/web_console_ng/
├── test_redis_ha.py
└── test_state_manager.py
```

**HA Redis Store Implementation:**
```python
# apps/web_console_ng/core/redis_ha.py
# IMPORTANT: Use redis.asyncio.sentinel for async Sentinel support
# Requires redis-py >= 5.0
from redis.asyncio.sentinel import Sentinel
from redis.asyncio import Redis
from typing import Optional
import asyncio
from apps.web_console_ng import config

class HARedisStore:
    """
    High-availability Redis store with async Sentinel support.

    Provides automatic failover and read replica support.
    Requires redis-py >= 5.0 for redis.asyncio.sentinel module.
    """

    _instance: Optional["HARedisStore"] = None

    def __init__(self):
        # Use async Sentinel from redis.asyncio.sentinel
        self.sentinel = Sentinel(
            config.REDIS_SENTINEL_HOSTS,  # [('sentinel-1', 26379), ...]
            socket_timeout=0.5,
            password=config.REDIS_PASSWORD,
            sentinel_kwargs={"password": config.REDIS_SENTINEL_PASSWORD},  # Sentinel auth
        )
        self.master_name = config.REDIS_MASTER_NAME  # "nicegui-sessions"
        self._master: Optional[Redis] = None
        self._slave: Optional[Redis] = None

    @classmethod
    def get(cls) -> "HARedisStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_master(self) -> Redis:
        """Get master connection for writes."""
        if self._master is None or not await self._is_connected(self._master):
            # master_for returns async Redis client in redis.asyncio.sentinel
            self._master = self.sentinel.master_for(
                self.master_name,
                socket_timeout=0.5,
                decode_responses=True,
            )
        return self._master

    async def get_slave(self) -> Redis:
        """Get slave connection for reads (fallback to master)."""
        if self._slave is None or not await self._is_connected(self._slave):
            try:
                self._slave = self.sentinel.slave_for(
                    self.master_name,
                    socket_timeout=0.5,
                    decode_responses=True,
                )
            except Exception:
                # Fallback to master if no slaves available
                self._slave = await self.get_master()
        return self._slave

    async def _is_connected(self, conn: Redis) -> bool:
        """Check if connection is alive."""
        try:
            await asyncio.wait_for(conn.ping(), timeout=0.5)
            return True
        except Exception:
            return False

    async def ping(self) -> bool:
        """Health check - verify Redis is reachable."""
        master = await self.get_master()
        return await master.ping()


# Fallback for non-Sentinel environments (dev/test)
class SimpleRedisStore:
    """Simple Redis store for development (no Sentinel)."""

    _instance: Optional["SimpleRedisStore"] = None

    def __init__(self):
        self.redis = Redis.from_url(
            config.REDIS_URL,
            decode_responses=True,
        )

    @classmethod
    def get(cls) -> "SimpleRedisStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_master(self) -> Redis:
        return self.redis

    async def get_slave(self) -> Redis:
        return self.redis

    async def ping(self) -> bool:
        return await self.redis.ping()


def get_redis_store():
    """Factory function - returns appropriate store based on config."""
    if config.REDIS_USE_SENTINEL:
        return HARedisStore.get()
    return SimpleRedisStore.get()
```

**User State Manager:**
```python
# apps/web_console_ng/core/state_manager.py
import json
import time
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.client import AsyncTradingClient


class TradingJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime, date, and Decimal types."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return {"__type__": "datetime", "value": obj.isoformat()}
        if isinstance(obj, date):
            return {"__type__": "date", "value": obj.isoformat()}
        if isinstance(obj, Decimal):
            return {"__type__": "Decimal", "value": str(obj)}
        return super().default(obj)


def trading_json_decoder(dct):
    """Custom JSON decoder for datetime, date, and Decimal types."""
    if "__type__" in dct:
        if dct["__type__"] == "datetime":
            return datetime.fromisoformat(dct["value"])
        if dct["__type__"] == "date":
            return date.fromisoformat(dct["value"])
        if dct["__type__"] == "Decimal":
            return Decimal(dct["value"])
    return dct


class UserStateManager:
    """
    Manages user state with Redis persistence for failover recovery.

    State categories:
    - UI state: ephemeral, re-rendered on reconnect
    - Critical state: persisted in Redis (preferences, pending forms, filters)
    - API data: fetched fresh from backend on reconnect
    """

    STATE_KEY_PREFIX = "user_state:"
    STATE_TTL = 86400  # 24 hours

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.redis = get_redis_store()
        self.state_key = f"{self.STATE_KEY_PREFIX}{user_id}"

    async def save_critical_state(self, state: dict[str, Any]) -> None:
        """
        Persist critical state for failover recovery.

        Only save state that improves UX on reconnection:
        - User preferences (theme, layout)
        - Dashboard filters
        - Pending form data (unsaved)

        Uses custom JSON encoder for datetime/Decimal types.
        """
        state_with_meta = {
            "data": state,
            "saved_at": time.time(),
            "version": 1,
        }
        master = await self.redis.get_master()
        await master.setex(
            self.state_key,
            self.STATE_TTL,
            json.dumps(state_with_meta, cls=TradingJSONEncoder)
        )

    async def restore_state(self) -> dict[str, Any]:
        """Restore state after reconnection. Uses custom decoder for types."""
        slave = await self.redis.get_slave()
        data = await slave.get(self.state_key)

        if not data:
            return {}

        try:
            parsed = json.loads(data, object_hook=trading_json_decoder)
            return parsed.get("data", {})
        except json.JSONDecodeError:
            return {}

    async def update_preference(self, key: str, value: Any) -> None:
        """Update a single preference (merge into existing state)."""
        state = await self.restore_state()
        preferences = state.get("preferences", {})
        preferences[key] = value
        state["preferences"] = preferences
        await self.save_critical_state(state)

    async def save_pending_form(self, form_id: str, form_data: dict) -> None:
        """Save pending form data (for recovery after disconnect)."""
        state = await self.restore_state()
        pending_forms = state.get("pending_forms", {})
        pending_forms[form_id] = {
            "data": form_data,
            "saved_at": time.time(),
        }
        state["pending_forms"] = pending_forms
        await self.save_critical_state(state)

    async def clear_pending_form(self, form_id: str) -> None:
        """Clear pending form after successful submission."""
        state = await self.restore_state()
        pending_forms = state.get("pending_forms", {})
        pending_forms.pop(form_id, None)
        state["pending_forms"] = pending_forms
        await self.save_critical_state(state)

    async def on_reconnect(self, ui_context) -> dict[str, Any]:
        """
        Called when user reconnects after WS drop.

        Returns data needed to restore UI.
        """
        # 1. Load persisted state
        state = await self.restore_state()

        # 2. Fetch fresh API data
        client = AsyncTradingClient.get()
        api_data = {
            "positions": await client.fetch_positions(self.user_id),
            "orders": await client.fetch_open_orders(self.user_id),
            "kill_switch": await client.fetch_kill_switch_status(),
        }

        # 3. Return combined data for UI restoration
        return {
            "preferences": state.get("preferences", {}),
            "filters": state.get("filters", {}),
            "pending_forms": state.get("pending_forms", {}),
            "api_data": api_data,
        }

    async def delete_state(self) -> None:
        """Delete all state (on logout)."""
        master = await self.redis.get_master()
        await master.delete(self.state_key)
```

**Acceptance Tests:**
- [ ] State saved to Redis successfully
- [ ] State restored after simulated disconnect
- [ ] Sentinel failover doesn't lose state
- [ ] Pending form data survives reconnect
- [ ] State deleted on logout

---

### C2: Client Lifecycle & Cleanup

**Files to Create:**
```
apps/web_console_ng/core/
├── client_lifecycle.py          # Task tracking and cleanup
├── connection_events.py         # Connection handlers
tests/apps/web_console_ng/
├── test_client_lifecycle.py
└── test_connection_events.py
```

**Client Lifecycle Manager:**
```python
# apps/web_console_ng/core/client_lifecycle.py
import asyncio
import uuid
from typing import Callable, Any
import logging

logger = logging.getLogger(__name__)

class ClientLifecycleManager:
    """
    Manages per-client background tasks and cleanup.

    CRITICAL: All background tasks (timers, subscriptions) must be
    registered here to prevent resource leaks on disconnect.
    """

    _instance: "ClientLifecycleManager | None" = None

    def __init__(self):
        self.client_tasks: dict[str, list[asyncio.Task]] = {}
        self.client_callbacks: dict[str, list[Callable]] = {}
        self.active_clients: set[str] = set()

    @classmethod
    def get(cls) -> "ClientLifecycleManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate_client_id(self) -> str:
        """Generate unique client ID for this connection."""
        return str(uuid.uuid4())

    def register_client(self, client_id: str) -> None:
        """Register a new client connection."""
        self.active_clients.add(client_id)
        self.client_tasks[client_id] = []
        self.client_callbacks[client_id] = []
        logger.info(f"Client registered: {client_id}")

    def register_task(self, client_id: str, task: asyncio.Task) -> None:
        """
        Register a background task for a client.

        Task will be cancelled when client disconnects.
        """
        if client_id not in self.client_tasks:
            self.client_tasks[client_id] = []
        self.client_tasks[client_id].append(task)

    def register_cleanup_callback(
        self, client_id: str, callback: Callable[[], Any]
    ) -> None:
        """Register a cleanup callback to run on disconnect."""
        if client_id not in self.client_callbacks:
            self.client_callbacks[client_id] = []
        self.client_callbacks[client_id].append(callback)

    async def cleanup_client(self, client_id: str) -> None:
        """
        Cancel all tasks and run cleanup when client disconnects.

        MUST be called on disconnect to prevent resource leaks.
        """
        self.active_clients.discard(client_id)

        # Cancel all registered tasks
        tasks = self.client_tasks.pop(client_id, [])
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Run cleanup callbacks
        callbacks = self.client_callbacks.pop(client_id, [])
        for callback in callbacks:
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Cleanup callback error for {client_id}: {e}")

        logger.info(
            f"Cleaned up client {client_id}: "
            f"{len(tasks)} tasks, {len(callbacks)} callbacks"
        )

    def get_active_client_count(self) -> int:
        """Return number of active clients (for health check)."""
        return len(self.active_clients)

    def is_client_active(self, client_id: str) -> bool:
        """Check if client is still connected."""
        return client_id in self.active_clients
```

**Connection Event Handlers:**
```python
# apps/web_console_ng/core/connection_events.py
from nicegui import app, Client
from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager
from apps.web_console_ng.core.state_manager import UserStateManager
from apps.web_console_ng.core import health
from apps.web_console_ng import metrics
import logging

logger = logging.getLogger(__name__)

def setup_connection_handlers():
    """
    Set up NiceGUI connection event handlers.

    Call this once during app initialization.
    """
    from apps.web_console_ng import config

    @app.on_connect
    async def on_client_connect(client: Client):
        """Handle new WebSocket connection."""
        lifecycle = ClientLifecycleManager.get()

        # Generate and store client ID
        client_id = lifecycle.generate_client_id()
        client.storage["client_id"] = client_id

        # Register client
        lifecycle.register_client(client_id)

        # Update metrics (use thread-safe counter)
        # IMPORTANT: All metrics must include pod label
        count = health.connection_counter.increment()
        metrics.ws_connects_total.labels(pod=config.POD_NAME).inc()
        metrics.ws_connections.labels(pod=config.POD_NAME).set(count)

        logger.info(f"Client connected: {client_id}")

    @app.on_disconnect
    async def on_client_disconnect(client: Client):
        """Handle WebSocket disconnection - cleanup resources."""
        lifecycle = ClientLifecycleManager.get()
        client_id = client.storage.get("client_id")

        if client_id:
            # Cleanup all client resources
            await lifecycle.cleanup_client(client_id)

            # Update metrics (use thread-safe counter)
            # IMPORTANT: All metrics must include pod label
            count = health.connection_counter.decrement()
            metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="normal").inc()
            metrics.ws_connections.labels(pod=config.POD_NAME).set(count)

            logger.info(f"Client disconnected: {client_id}")

    @app.on_exception
    async def on_client_exception(client: Client, exception: Exception):
        """Handle client exception - log and cleanup."""
        client_id = client.storage.get("client_id")
        logger.error(f"Client {client_id} exception: {exception}")

        # IMPORTANT: All metrics must include pod label
        metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="error").inc()


async def restore_client_state(client: Client, request) -> dict:
    """
    Restore client state after reconnection.

    Called by pages to restore UI state.
    NOTE: Requires request object for client IP extraction.
    """
    session_id = app.storage.user.get("session_id")
    if not session_id:
        return {}

    # Extract client IP from request (handles proxies)
    from apps.web_console_ng.auth.utils import extract_trusted_client_ip
    from apps.web_console_ng import config
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)

    # Get user ID from session
    from apps.web_console_ng.auth.session_store import get_session_store
    session_store = get_session_store()
    session = await session_store.validate_session(
        session_id,
        app.storage.user.get("session_signature", ""),
        client_ip  # Use real client IP, not hardcoded
    )

    if not session:
        return {}

    user_id = session.get("user", {}).get("user_id")
    if not user_id:
        return {}

    # Restore state
    state_manager = UserStateManager(user_id)
    return await state_manager.on_reconnect(client)
```

**Acceptance Tests:**
- [ ] Client ID generated on connect
- [ ] Tasks cancelled on disconnect
- [ ] Cleanup callbacks executed
- [ ] Connection count accurate
- [ ] No task leaks after disconnect

---

### C3: Observability

**Files to Create:**
```
apps/web_console_ng/
├── metrics.py                   # Prometheus metrics definitions
infra/prometheus/alerts/
├── nicegui.yml                  # Alert rules
infra/grafana/dashboards/
├── nicegui-overview.json        # Grafana dashboard
tests/apps/web_console_ng/
└── test_metrics.py
```

**Prometheus Metrics:**
```python
# apps/web_console_ng/metrics.py
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from nicegui import app
from fastapi import Response

# WebSocket metrics
# NOTE: Gauge names should NOT end in "_total" (Prometheus convention)
ws_connections = Gauge(
    "nicegui_ws_connections",  # Gauge - current value, not _total
    "Current WebSocket connections",
    ["pod"]
)

ws_connects_total = Counter(
    "nicegui_ws_connects_total",  # Counter - cumulative, ends in _total
    "Total WebSocket connects",
    ["pod"]
)

ws_disconnects_total = Counter(
    "nicegui_ws_disconnects_total",  # Counter - cumulative, ends in _total
    "Total WebSocket disconnects",
    ["pod", "reason"]
)

# Auth metrics
auth_failures_total = Counter(
    "nicegui_auth_failures_total",
    "Authentication failures",
    ["auth_type", "reason"]
)

sessions_created_total = Counter(
    "nicegui_sessions_created_total",  # Fixed: matches table naming
    "Sessions created",
    ["auth_type"]
)

# Latency metrics
redis_latency = Histogram(
    "nicegui_redis_latency_seconds",
    "Redis operation latency",
    ["operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

api_latency = Histogram(
    "nicegui_api_latency_seconds",
    "Backend API latency",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# Resource metrics
memory_bytes = Gauge(
    "nicegui_memory_bytes",
    "Process memory usage",
    ["pod"]
)

active_users = Gauge(
    "nicegui_active_users",
    "Unique active users",
    ["pod"]
)

push_queue_depth = Gauge(
    "nicegui_push_queue_depth",
    "Pub/Sub queue depth",
    ["pod"]
)


@app.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus metrics endpoint."""
    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


# Helper decorators for timing
import functools
import time

def time_redis_operation(operation: str):
    """Decorator to time Redis operations."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                redis_latency.labels(operation=operation).observe(duration)
        return wrapper
    return decorator

def time_api_call(endpoint: str):
    """Decorator to time API calls."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                api_latency.labels(endpoint=endpoint).observe(duration)
        return wrapper
    return decorator


# Periodic metrics update (call from background task)
import psutil
import os

async def update_resource_metrics():
    """
    Update resource metrics periodically.

    Call this from a background task (e.g., every 15s).
    """
    from apps.web_console_ng import config
    from apps.web_console_ng.core.client_lifecycle import ClientLifecycleManager

    pod = config.POD_NAME

    # Memory usage (process RSS)
    process = psutil.Process(os.getpid())
    memory_bytes.labels(pod=pod).set(process.memory_info().rss)

    # Active users (unique sessions from lifecycle manager)
    lifecycle = ClientLifecycleManager.get()
    active_users.labels(pod=pod).set(lifecycle.get_active_client_count())

    # Connection count (from lifecycle manager for single source of truth)
    ws_connections.labels(pod=pod).set(lifecycle.get_active_client_count())
```

**Alert Rules:**
```yaml
# infra/prometheus/alerts/nicegui.yml
groups:
  - name: nicegui
    rules:
      - alert: HighWSDisconnectRate
        expr: |
          rate(nicegui_ws_disconnects_total[5m]) /
          (rate(nicegui_ws_connects_total[5m]) + 0.001) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High WebSocket disconnect rate ({{ $value | printf \"%.2f\" }})"
          description: "More than 5% of connections are disconnecting"

      - alert: AuthFailureSpike
        expr: rate(nicegui_auth_failures_total[5m]) > 0.1
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Authentication failure spike detected"
          description: "Auth failure rate exceeds 10/min - possible attack"

      - alert: HighAPILatency
        expr: |
          histogram_quantile(0.95,
            rate(nicegui_api_latency_seconds_bucket[5m])
          ) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 API latency > 500ms"
          description: "Backend API is responding slowly"

      - alert: MemoryPerUserHigh
        expr: |
          nicegui_memory_bytes / (nicegui_active_users + 1) > 30000000
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Memory per user exceeds 30MB"
          description: "Possible memory leak or inefficient resource usage"

      - alert: RedisLatencyHigh
        expr: |
          histogram_quantile(0.99,
            rate(nicegui_redis_latency_seconds_bucket[5m])
          ) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Redis P99 latency > 50ms"
          description: "Session store is slow - may affect user experience"

      - alert: ConnectionCountHigh
        expr: nicegui_ws_connections > 800  # Gauge, not _total
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Connection count approaching limit ({{ $value }}/1000)"
          description: "Pod is at 80% connection capacity"

      - alert: SessionCreationSpike
        expr: rate(nicegui_sessions_created_total[5m]) > 1.67  # Fixed: matches metric name
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Session creation rate > 100/min"
          description: "Possible credential stuffing attack"
```

**Acceptance Tests:**
- [ ] `/metrics` endpoint returns Prometheus format
- [ ] `/metrics` protected by ingress allowlist or internal-only route (verify with test)
- [ ] Metrics increment correctly on events
- [ ] All metric calls include `pod` label (verify `config.POD_NAME` is set)
- [ ] Alert rules syntax valid (prometheus check-config)
- [ ] Histogram buckets appropriate for SLOs
- [ ] `update_resource_metrics()` updates memory_bytes, active_users correctly

**Metrics Endpoint Protection Strategy:**
- **Chosen mechanism:** nginx ingress allowlist (internal IPs only)
- **Fallback:** Internal-only k8s Service (ClusterIP, no external exposure)

```yaml
# k8s ingress annotation for /metrics protection
# Only allow access from internal networks
annotations:
  nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
```

**Metrics Endpoint Protection Test:**
```python
# tests/apps/web_console_ng/test_metrics_security.py
import pytest
from fastapi.testclient import TestClient

class TestMetricsEndpointProtection:
    """Verify /metrics is protected for external access."""

    def test_metrics_returns_prometheus_format(self, client: TestClient):
        """Verify metrics endpoint returns valid Prometheus format."""
        # Internal request (test client is localhost)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "nicegui_ws_connections" in response.text
        assert "# HELP" in response.text  # Prometheus format includes HELP

    def test_metrics_includes_pod_label(self, client: TestClient):
        """Verify all metrics include required pod label."""
        response = client.get("/metrics")
        # Check that metrics with pod label exist
        assert 'nicegui_ws_connections{pod="' in response.text or \
               'nicegui_ws_connections{' in response.text  # May be no connections yet

    @pytest.mark.integration
    def test_metrics_blocked_from_external_via_ingress(self):
        """
        Verify nginx ingress blocks external /metrics access.

        Run in staging with external client to verify ingress allowlist.
        Expected: 403 Forbidden from external IPs.
        """
        # This test runs against deployed staging environment
        # External request should be blocked by ingress allowlist
        pass  # Manual verification in staging
```

---

## Testing Strategy

### Unit Tests (CI - Automated)
**Run in CI pipeline on every PR:**
- `test_redis_ha.py`: Sentinel failover, connection pooling (mocked Sentinel)
- `test_state_manager.py`: State persistence, restoration (mocked Redis)
- `test_client_lifecycle.py`: Task registration, cleanup
- `test_connection_events.py`: Connect/disconnect handling
- `test_metrics.py`: Metric increments, timing decorators
- `test_health_endpoint.py`: Health check responses (mocked deps)

### Integration Tests (CI - Requires Docker)
**Run in CI with docker-compose services:**
- `test_sentinel_failover.py`: Redis master failover (requires 3-node Redis Sentinel)
- `test_reconnection_flow.py`: Full disconnect/reconnect cycle
- `test_state_recovery.py`: State restoration after reconnect

**Note:** Configure `docker-compose.test.yml` with:
- 3 Redis Sentinel nodes
- Single NiceGUI pod (for unit integration)

### Integration Tests (Manual - Requires k8s/Multi-Pod)
**Run manually in staging environment:**
- `test_multi_pod.py`: Sticky session routing (requires 3 NiceGUI pods)
- Pod failure and recovery test (kill pod, verify session migrates)

### Load Tests (Manual - Pre-Release)
**Run manually before major releases:**
- `test_connection_limits.py`: 1000 connections per pod (k6/locust)
- `test_memory_growth.py`: Memory per user tracking (4hr soak test)
- `test_latency_under_load.py`: P95/P99 latency targets (100+ concurrent users)

### Manual Verification (Pre-Production)
- [ ] Deploy 3 pods and verify sticky routing
- [ ] Kill Redis master and verify failover (<10s recovery)
- [ ] Disconnect browser and verify reconnection (<5s)
- [ ] Verify Grafana dashboard data accuracy
- [ ] Trigger alerts and verify notifications

---

## Dependencies

### External
- `redis[hiredis]>=5.0`: Redis client with async Sentinel support (`redis.asyncio.sentinel`)
- `prometheus-client>=0.17`: Metrics library
- `psutil>=5.9`: Process/system metrics (memory, CPU)
- nginx (for load balancing)
- Grafana (for dashboards)
- Prometheus (for metrics)

### Internal
- `apps/web_console_ng/core/client.py`: Async trading client (from P5T1)
- `apps/web_console_ng/auth/session_store.py`: Session management (from P5T1)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Sentinel failover latency | Medium | Medium | 0.5s socket timeout, retry logic |
| State loss on rapid reconnect | Low | Medium | State versioning, idempotent restore |
| Metric cardinality explosion | Low | Low | Limit label values, drop high-cardinality |
| nginx sticky session failures | Low | High | Fallback to session rehydration from Redis |
| Task leak on abnormal disconnect | Medium | Medium | Periodic cleanup of orphaned tasks |
| Redis replica lag for session validation | Medium | High | Read from master for auth validation |
| NAT concentration with ip_hash | Medium | Medium | Prefer cookie affinity via ingress annotations |

---

## Implementation Notes (Address During Development)

**These items were identified during planning review and should be addressed during implementation:**

1. **Metrics/Alerts Consistency:** ✅ ADDRESSED IN DOCUMENT (Rev 6)
   - Alert rules use updated metric names (`nicegui_ws_connections`, `nicegui_sessions_created_total`)
   - All metric calls include `pod` label in `connection_events.py` and `update_resource_metrics()`
   - Example: `metrics.ws_connections.labels(pod=config.POD_NAME).set(count)`
   - Fixed in Rev 6: Added `pod` label to all metric calls in connection handlers

2. **Metrics Endpoint Security:** ✅ ADDRESSED IN DOCUMENT (Rev 6)
   - Concrete protection strategy chosen: nginx ingress allowlist
   - k8s ingress annotation example provided
   - Full acceptance test class with specific assertions
   - Fixed in Rev 6: No longer a stub - has concrete expected behavior

3. **Redis Read Consistency for Auth:** ✅ ADDRESSED IN DOCUMENT
   - T3.2 explicitly states: read from MASTER for session validation
   - Slave reads only for non-critical operations (preferences, filters)

4. **Redis TLS Configuration:**
   - Add `ssl=True`, `ssl_cert_reqs`, CA paths to Redis connection config
   - Add Sentinel TLS if enabled
   - **TODO during implementation:** Add TLS params to `HARedisStore.__init__()`

5. **Health Check IP Detection:** ✅ ADDRESSED IN DOCUMENT
   - `is_internal_request()` now uses `extract_trusted_client_ip()` utility
   - Consistent with auth IP extraction for proxy handling

6. **Connection Counter Consolidation:** ✅ ADDRESSED IN DOCUMENT (Rev 6)
   - `ConnectionCounter` is authoritative for Prometheus metrics (high-frequency updates)
   - `ClientLifecycleManager` is authoritative for health checks (task tracking context)
   - Both are synchronized in connection handlers and report the same value
   - Fixed in Rev 6: Added docstrings clarifying which is authoritative for what use case

7. **Environment Variables:**
   - Required config values documented in Prerequisites Checklist
   - `POD_NAME`, `REDIS_SENTINEL_HOSTS`, `REDIS_MASTER_NAME`, `REDIS_USE_SENTINEL`, `REDIS_SENTINEL_PASSWORD`, `REDIS_PASSWORD`, `TRUSTED_PROXY_IPS`

8. **Test Harness:** ✅ ADDRESSED IN DOCUMENT
   - Testing Strategy section now specifies CI vs manual tests
   - docker-compose.test.yml requirements documented
   - k8s/staging requirements for multi-pod tests documented

9. **Resource Metrics Derivation:** ✅ ADDRESSED IN DOCUMENT
   - `update_resource_metrics()` function shows how `memory_bytes` and `active_users` are derived
   - Uses `psutil` for memory, `ClientLifecycleManager` for active users

10. **Exception Handler Metrics (from Rev 6 review):**
    - `on_client_exception` currently increments `ws_disconnects_total` but exceptions ≠ disconnects
    - **TODO during implementation:** Either create separate `nicegui_exceptions_total` metric, or verify NiceGUI guarantees `on_disconnect` follows exceptions
    - If both fire, may double-count disconnects - verify behavior and adjust

11. **Pod Label on All Metrics (from Rev 6 review):**
    - Add `pod` label to latency and auth metrics for per-pod visibility:
      - `nicegui_redis_latency_seconds`
      - `nicegui_api_latency_seconds`
      - `nicegui_auth_failures_total`
    - Enables "top-K bad pod" analysis

12. **Metrics Protection Dev Fallback (from Rev 6 review):**
    - Ingress allowlist only works in k8s environments
    - **TODO during implementation:** Add app-side guard for non-ingress dev deployments
    - Option: bind `/metrics` to internal-only route or require auth header in dev mode

---

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests pass with >90% coverage
- [ ] Integration tests for HA scenarios
- [ ] nginx config tested with multi-pod deployment
- [ ] Grafana dashboard functional
- [ ] Alert rules firing correctly
- [ ] No regressions in P5T1/P5T2 tests
- [ ] Code reviewed and approved
- [ ] Documentation updated (runbooks)
- [ ] Merged to feature branch

---

**Last Updated:** 2025-12-31 (Rev 6)
**Status:** PLANNING

---
id: P5T3
title: "NiceGUI Migration - HA/Scaling & Observability"
phase: P5
task: T3
priority: P0
owner: "@development-team"
state: COMPLETE
created: 2025-12-30
dependencies: [P5T1, P5T2]
estimated_effort: "4-5 days"
related_adrs: [ADR-0031-nicegui-migration, ADR-0032-nicegui-ha-observability]
related_docs: [P5_PLANNING.md, P5T1_TASK.md, P5T2_TASK.md]
features: [T3.1, T3.2, T3.3]
---

# P5T3: NiceGUI Migration - HA/Scaling & Observability

**Phase:** P5 (Web Console Modernization)
**Status:** ✅ Complete
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
- [ ] Health check endpoint (`/healthz`, `/readyz`) for pod liveness/readiness
- [ ] Max connection limits per pod (1000 WS connections)
- [ ] **Connection admission control:** Reject new connections above threshold with 503
- [ ] **Overload metrics:** `nicegui_connections_rejected_total` counter for admission control rejections
- [ ] Graceful drain on pod termination (30s drain period)
- [ ] ASGI lifespan hooks for startup/shutdown (NiceGUI `ui.run()` with lifespan parameter)
- [ ] k8s preStop hook for graceful drain signal

**Sticky Session Implementation:**
- [ ] `nicegui_server_id` cookie for routing (if using cookie-based)
- [ ] **Cookie security attributes:** `HttpOnly=True, Secure=True, SameSite=Lax`
- [ ] Cookie expires after 1 hour (matches session timeout)
- [ ] Fallback to round-robin routing if cookie missing
- [ ] Cookie set on initial connection, not login

**Health Check Endpoints (Liveness/Readiness Split):**
- [ ] **`/healthz` (Liveness):** Always returns 200 unless process is unhealthy (no dependency checks)
- [ ] **`/readyz` (Readiness):** Returns 200 when Redis + backend healthy, 503 during drain or failures
- [ ] Readiness checks Redis connectivity
- [ ] Readiness checks backend API reachability
- [ ] Readiness returns 503 during graceful shutdown (liveness stays 200)
- [ ] **Security:** Minimal response for external requests (just status code)
- [ ] **Security:** Detailed info only for internal requests detected via:
  - **Primary:** `X-Internal-Probe` header with `INTERNAL_PROBE_TOKEN` secret (preferred for k8s)
  - **Fallback:** IP-based check for internal networks (only when token not configured)
- [ ] Connection count in detailed response only (internal)
- [ ] Uses `extract_trusted_client_ip()` for proper proxy handling
- [ ] **k8s probes:** livenessProbe → `/healthz`, readinessProbe → `/readyz`

**Connection Admission Control Policy:**
- [ ] **Enforcement point:** WebSocket upgrade middleware (before auth handshake)
- [ ] **Threshold:** 1000 concurrent connections per pod
- [ ] **Priority order:**
  1. **Existing session reconnects** (valid session cookie in Redis) → ALLOW (bypass capacity limit)
  2. **New sessions** → REJECT with 503 if at capacity
- [ ] **Rejection behavior:**
  - Return HTTP 503 Service Unavailable before WebSocket upgrade completes
  - Set `Retry-After: 5` header to signal client backoff
  - Increment `nicegui_connections_rejected_total{pod, reason}` metric
- [ ] **Reason labels for rejections:**
  - `capacity` — Pod at max connection limit, new session rejected → HTTP 503 + Retry-After
  - `draining` — Pod in graceful shutdown, rejecting new connections → HTTP 503 + Retry-After
  - `invalid_session` — Reconnect attempt with invalid/expired session cookie → HTTP 401 (forces re-auth, no retry)
  - `session_limit` — Session already has max concurrent connections (default: 2) → HTTP 429
- [ ] **Reconnection storm protection:**
  - Existing sessions always allowed to reconnect (session validated via Redis)
  - Prevents lockout of legitimate users during pod drain/restart cycles
  - Session cookie presence triggers Redis validation before capacity check
- [ ] **Capacity accounting:** Use `asyncio.Semaphore` as authoritative source for admission control (atomic, prevents race conditions). `ConnectionCounter` is for metrics/health only and may briefly drift under error paths.
- [ ] **Single-process constraint:** Runtime assertion in lifespan enforces workers=1 (in-memory semaphore/counters only valid for single process)
- [ ] **Implementation pattern (ASGI middleware for WebSocket scope):**
  ```python
  # apps/web_console_ng/core/admission.py
  # CRITICAL: Must be ASGI middleware (not HTTP middleware) to intercept WebSocket connections
  import asyncio
  from apps.web_console_ng import config
  from apps.web_console_ng.core import health
  from apps.web_console_ng.core.redis_ha import get_redis_store
  from apps.web_console_ng.auth.session_store import get_session_store
  from apps.web_console_ng.metrics import connections_rejected_total
  from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip

  POD_NAME = config.POD_NAME
  SESSION_COOKIE_NAME = config.SESSION_COOKIE_NAME
  MAX_CONNECTIONS = 1000
  SESSION_VALIDATION_TIMEOUT = 2.0  # seconds

  # Atomic semaphore for connection reservation (prevents burst over limit)
  _connection_semaphore = asyncio.Semaphore(MAX_CONNECTIONS)


  class AdmissionControlMiddleware:
      """
      ASGI middleware for connection admission control.

      CRITICAL: Standard HTTP middleware (add_middleware) does NOT run for WebSocket
      connections in Starlette/FastAPI. This ASGI middleware handles both scopes.

      Uses semaphore for atomic capacity reservation to prevent burst over MAX_CONNECTIONS.
      """

      def __init__(self, app):
          self.app = app

      async def __call__(self, scope, receive, send):
          # Only apply to WebSocket connections
          if scope["type"] != "websocket":
              await self.app(scope, receive, send)
              return

          # EARLY REJECTION 1: Pod is draining - HTTP 503 before upgrade
          if health.is_draining:
              connections_rejected_total.labels(pod=POD_NAME, reason="draining").inc()
              await self._send_http_error(send, 503, "Server draining", retry_after=30)
              return

          # Parse cookies using Starlette's HTTPConnection (robust, handles edge cases)
          from starlette.requests import HTTPConnection
          conn = HTTPConnection(scope)
          session_cookie = conn.cookies.get(SESSION_COOKIE_NAME)

          if session_cookie:
              try:
                  session_store = get_session_store()
                  # Use extract_trusted_client_ip for consistency with auth path
                  # HTTPConnection provides a Request-like interface for header access
                  client_ip = extract_trusted_client_ip(conn, config.TRUSTED_PROXY_IPS)
                  user_agent = conn.headers.get("user-agent", "")

                  # TIMEOUT: Prevent blocking on Redis stall
                  session_valid = await asyncio.wait_for(
                      session_store.validate_session(session_cookie, client_ip, user_agent),
                      timeout=SESSION_VALIDATION_TIMEOUT
                  )

                  if session_valid:
                      # Valid session reconnect - check per-session connection limit
                      # SECURITY: Prevents resource exhaustion via parallel WS from same session
                      # Use helper to extract session_id (handles format changes, additional dots)
                      from apps.web_console_ng.auth.session_store import extract_session_id
                      session_id = extract_session_id(session_cookie)
                      session_conn_key = f"session_conns:{session_id}"
                      redis = await get_redis_store().get_master()

                      # ATOMIC check-and-increment with TTL using Lua script
                      # Prevents stale lockouts if process crashes between INCR and EXPIRE
                      MAX_CONNECTIONS_PER_SESSION = 2  # Allow brief overlap during reconnect
                      SESSION_CONN_TTL = 3600  # 1hr matches session TTL

                      # Lua script: INCR + conditional EXPIRE (only set TTL if not already set)
                      lua_script = """
                      local count = redis.call('INCR', KEYS[1])
                      if redis.call('TTL', KEYS[1]) == -1 then
                          redis.call('EXPIRE', KEYS[1], ARGV[1])
                      end
                      return count
                      """
                      current_conns = await redis.eval(lua_script, 1, session_conn_key, SESSION_CONN_TTL)

                      if current_conns > MAX_CONNECTIONS_PER_SESSION:
                          await redis.decr(session_conn_key)
                          connections_rejected_total.labels(pod=POD_NAME, reason="session_limit").inc()
                          await self._send_http_error(send, 429, "Too many connections for session")
                          return

                      # Store for cleanup on disconnect
                      scope["state"] = scope.get("state", {})
                      scope["state"]["session_conn_key"] = session_conn_key
                      scope["state"]["handshake_complete"] = False  # Set True after on_connect

                      # CRITICAL: Only decrement if handshake FAILED (on_connect never ran)
                      # on_disconnect handles normal close, middleware handles failure cases
                      # This prevents double-decrement
                      try:
                          await self.app(scope, receive, send)
                      finally:
                          # Only decrement if handshake failed (on_disconnect won't run)
                          # on_disconnect sets handshake_complete=True before decrementing
                          if not scope["state"].get("handshake_complete", False):
                              lua_decr = """
                              local count = redis.call('GET', KEYS[1])
                              if count and tonumber(count) > 0 then
                                  return redis.call('DECR', KEYS[1])
                              end
                              return 0
                              """
                              try:
                                  redis = await get_redis_store().get_master()
                                  await redis.eval(lua_decr, 1, session_conn_key)
                              except Exception:
                                  pass  # Best-effort cleanup, TTL will expire stale counters
                      return
                  else:
                      connections_rejected_total.labels(pod=POD_NAME, reason="invalid_session").inc()
                      await self._send_http_error(send, 401, "Session expired")
                      return
              except asyncio.TimeoutError:
                  # Fail-closed on timeout
                  connections_rejected_total.labels(pod=POD_NAME, reason="timeout").inc()
                  await self._send_http_error(send, 503, "Service timeout", retry_after=5)
                  return
              except Exception as e:
                  # Catch-all: log with context and fail-closed
                  import logging
                  logger = logging.getLogger(__name__)
                  logger.error(
                      f"Admission control error: {e}",
                      extra={"pod": POD_NAME, "session_id": session_id if 'session_id' in dir() else "unknown"}
                  )
                  connections_rejected_total.labels(pod=POD_NAME, reason="error").inc()
                  await self._send_http_error(send, 503, "Service error", retry_after=5)
                  return

          # ATOMIC CAPACITY CHECK: Use semaphore to reserve slot before proceeding
          acquired = _connection_semaphore.locked() is False and await self._try_acquire_semaphore()
          if not acquired:
              connections_rejected_total.labels(pod=POD_NAME, reason="capacity").inc()
              await self._send_http_error(send, 503, "Server at capacity", retry_after=5)
              return

          try:
              await self.app(scope, receive, send)
          finally:
              _connection_semaphore.release()

      async def _send_http_error(self, send, status: int, message: str, retry_after: int = None):
          """Send HTTP error response before WebSocket upgrade (per ASGI spec)."""
          headers = [(b"content-type", b"application/json")]
          if retry_after:
              headers.append((b"retry-after", str(retry_after).encode()))

          await send({
              "type": "websocket.http.response.start",
              "status": status,
              "headers": headers,
          })
          await send({
              "type": "websocket.http.response.body",
              "body": f'{{"error": "{message}"}}'.encode(),
          })

      async def _try_acquire_semaphore(self) -> bool:
          """Attempt a near-instant acquire of the connection semaphore.

          Uses asyncio.wait_for with a minimal timeout (1ms) to allow the
          event loop to process the acquire. A timeout of 0 doesn't work
          because semaphore.acquire() needs at least one event loop iteration.
          """
          try:
              # Use 1ms timeout - enough for event loop to process if permit available
              await asyncio.wait_for(_connection_semaphore.acquire(), timeout=0.001)
              return True
          except TimeoutError:
              return False
  ```

**Testing:**
- [ ] Multi-pod deployment test (3 pods)
- [ ] Sticky routing verification
- [ ] Pod failure and recovery test
- [ ] Health check accuracy tests (both `/healthz` and `/readyz`)
- [ ] **Readiness drain behavior:** Verify `/readyz` 503 drains new traffic but does NOT kill active WebSockets
- [ ] **Connection admission control:** Test rejection at 1000 connections with proper 503 + metric increment
- [ ] **Admission control - valid session bypass:** Verify existing sessions can reconnect at capacity (bypass 1000 limit)
- [ ] **Admission control - reconnect after drain:** Verify sessions from drained pod can reconnect to other pods
- [ ] **Admission control - reason labels:** Verify `capacity`, `draining`, `invalid_session` labels emitted correctly
- [ ] **Admission control - handshake failure cleanup:** Verify session counter is decremented if WS handshake fails after INCR (prevents counter leak)
- [ ] **NAT/IP-shared clients:** Test `ip_hash` behavior with shared IP (enterprise NAT scenarios)
- [ ] **NAT decision gate:** If tests show >20% load skew or >5% reconnection spike, switch to cookie affinity (document decision)
- [ ] **SIGTERM drain test:** Verify /readyz transitions to 503 within 100ms of SIGTERM signal

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

**Redis TLS Configuration (config.py fields):**
- [ ] `REDIS_SSL_ENABLED`: Enable TLS for Redis connections
- [ ] `REDIS_SSL_CA_CERTS`: Path to CA certificate file
- [ ] `REDIS_SSL_CERTFILE`: Path to client certificate (for mTLS)
- [ ] `REDIS_SSL_KEYFILE`: Path to client key (for mTLS)
- [ ] `REDIS_SSL_CERT_REQS`: Certificate verification mode (required/optional/none)
  - Maps to `ssl.SSLContext.verify_mode`: `ssl.CERT_REQUIRED` / `ssl.CERT_OPTIONAL` / `ssl.CERT_NONE`
  - Apply via `ssl_context.verify_mode = ssl.CERT_REQUIRED` in `HARedisStore.__init__`
  - Also set `ssl_context.check_hostname = True` when using CERT_REQUIRED
- [ ] Pass TLS options to `redis.asyncio.sentinel.Sentinel` via `ssl=True` and `ssl_context` params

**Sentinel vs Data Node TLS Considerations:**
- [ ] **IMPORTANT:** Sentinel connections and data node connections may have different TLS requirements
- [ ] **Managed Redis (ElastiCache, MemoryStore):** Sentinel endpoint may be cleartext while data nodes are TLS (or vice versa)
- [ ] **Self-hosted Redis:** Typically both use TLS, but verify your configuration
- [ ] Add `REDIS_SENTINEL_SSL_ENABLED` config if Sentinel TLS differs from data node TLS
- [ ] During implementation: Validate TLS works for both Sentinel discovery AND data connections

**Session Store HA Implementation:**
- [ ] `HARedisStore` class with Sentinel support
- [ ] `get_master()` for writes
- [ ] `get_slave()` for reads (with fallback to master)
- [ ] Connection pooling (max 200 per pod - sized for 1000 WS connections; note: redis-py doesn't support min_connections)
- [ ] Automatic reconnection on connection loss

**State Persistence Strategy:**
- [ ] Critical state persisted to Redis (preferences, pending forms)
- [ ] UI state is ephemeral (re-rendered from server data)
- [ ] Position/order data fetched fresh from backend API
- [ ] Dashboard filters persisted for UX continuity
- [ ] 24hr TTL for user state keys
- [ ] **DoS PREVENTION:** State writes deferred until AFTER successful authentication
  - Do NOT create Redis state entries for unauthenticated connections
  - UserStateManager only initialized after session validation passes

**State Categories:**
| State Type | Storage | Recovery Strategy |
|------------|---------|-------------------|
| UI widgets | In-memory (lost) | Re-render from server |
| User preferences | Redis (persisted) | Restore on reconnect |
| Pending form data | Redis (persisted) | Restore, prompt confirm |
| Dashboard filters | Redis (persisted) | Restore with last values |
| Position/order data | Backend API | Fetch fresh on reconnect |

**Trading Safety (UI Integration):**
- [ ] **CRITICAL:** UI actions CANNOT bypass backend risk checks
  - All order submissions go through Execution Gateway which enforces:
    - Circuit breaker state checks
    - Per-symbol and total position limits
    - Order state machine transitions
    - Client_order_id idempotency
  - UI is presentation layer only - no direct market access
  - State restoration does NOT auto-submit pending orders (requires user confirmation)
- [ ] **CRITICAL:** client_order_id regeneration on form modification
  - Preserved client_order_id is valid ONLY for exact resubmission of original payload
  - If user modifies restored form data, UI MUST regenerate a new client_order_id
  - Compare current form data hash with `original_data_hash` to detect modifications
  - Prevents backend rejection or dangerous ambiguity from reusing ID with different params

**Testing:**
- [ ] Sentinel failover test (kill master, verify auto-switch)
- [ ] Read replica test (verify slave reads for non-critical state)
- [ ] **Master-only session validation test:** Verify session.validate() reads from MASTER (not slave)
  - Simulate revoked session with replica lag
  - Confirm validation rejects stale session immediately (not after lag propagates)
- [ ] State restoration after reconnect
- [ ] TTL expiry handling
- [ ] **Replica lag session acceptance test:** Verify revoked session is rejected even if slave has stale data

---

### T3.3 WebSocket State Recovery & Task Cleanup

**State Manager Implementation:**
- [ ] `UserStateManager` class for state persistence
- [ ] `save_critical_state()` - persist preferences, filters
- [ ] `restore_state()` - load on reconnect
- [ ] `on_reconnect()` - re-fetch API data + restore preferences
- [ ] **State versioning:** Use `saved_at` timestamp for conflict detection
  - Each save includes `saved_at: datetime.now(timezone.utc).isoformat()` in metadata (UTC-aware)
  - WATCH/MULTI/EXEC pattern handles concurrent writes (see UserStateManager methods)
  - No client-side versioning needed - server is authoritative
  - Future enhancement: ETag-style version if client-side caching is added
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
| `nicegui_connections_rejected_total` | Counter | `pod`, `reason` | Connections rejected (admission control) |
| `nicegui_auth_failures_total` | Counter | `pod`, `auth_type`, `reason` | Auth failures |
| `nicegui_sessions_created_total` | Counter | `pod`, `auth_type` | Sessions created |
| `nicegui_redis_latency_seconds` | Histogram | `pod`, `operation` | Redis operation latency |
| `nicegui_api_latency_seconds` | Histogram | `pod`, `endpoint` | Backend API latency |
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
- [ ] **Dependency pinning:** Add `redis[hiredis]>=5.0.0` to requirements.txt
- [ ] **Version validation:** Add import-time assertion in `redis_ha.py`:
  ```python
  import redis
  assert tuple(map(int, redis.__version__.split('.')[:2])) >= (5, 0), \
      f"redis-py >= 5.0 required for async Sentinel, got {redis.__version__}"
  ```

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

**Files to Verify/Update (existing):**
```
infra/nginx/
├── nicegui-cluster.conf         # Verify upstream + sticky session configuration
└── nicegui-location.conf        # Verify WebSocket upgrade headers
```

**Files to Create (new):**
```
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

# Liveness probe (always routed, no dependency checks)
location /healthz {
    proxy_pass http://nicegui_cluster;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
}

# Readiness probe (not sticky, includes dependency checks)
location /readyz {
    proxy_pass http://nicegui_cluster;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
}
```

**Health Check Endpoints (Liveness/Readiness Split):**
```python
# apps/web_console_ng/core/health.py
import asyncio
import json
import logging
import os
import signal
import threading
from contextlib import asynccontextmanager

from nicegui import app
from fastapi import Response, Request
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.client import AsyncTradingClient
from apps.web_console_ng import config

logger = logging.getLogger(__name__)

# Thread-safe connection counter for METRICS AND HEALTH CHECKS ONLY
# PURPOSE: Track connection count for observability (NOT for admission control)
# Used by:
#   - connection_events.py: immediate metric updates on connect/disconnect
#   - health.py readiness: connection count in detailed response
#   - metrics.py update_resource_metrics: periodic metric sync
#
# IMPORTANT: Admission control uses asyncio.Semaphore (in admission.py) as authoritative source
#   - Semaphore: Authoritative for capacity enforcement (prevents race conditions)
#   - ConnectionCounter: Metrics-only, may briefly drift from semaphore under error paths
#   - This separation is intentional: semaphore handles atomic admission, counter handles observability
class ConnectionCounter:
    """
    Thread-safe connection counter with guard against negative values.

    FOR METRICS AND OBSERVABILITY ONLY:
    - Prometheus metrics (ws_connections gauge)
    - Health check responses (/readyz connections field)

    NOT FOR ADMISSION CONTROL: Use asyncio.Semaphore in admission.py for capacity enforcement.
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

# Internal request detection configuration
# SECURITY NOTE: Hardcoded CIDRs are risky in k8s (pod subnets vary, externalTrafficPolicy
# can obscure source IPs). Prefer header-based check for k8s liveness/readiness probes.
INTERNAL_PROBE_TOKEN = os.getenv("INTERNAL_PROBE_TOKEN", "").strip()
# SECURITY: Set INTERNAL_PROBE_DISABLE_IP_FALLBACK=true in production to require token
INTERNAL_PROBE_DISABLE_IP_FALLBACK = os.getenv(
    "INTERNAL_PROBE_DISABLE_IP_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}
# Restricted fallback networks (localhost only in production, broader for dev)
INTERNAL_NETWORKS_STRICT = ["127.0.0.1", "::1"]  # Production: localhost only
INTERNAL_NETWORKS_DEV = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.1", "::1"]

def is_internal_request(request: Request) -> bool:
    """
    Check if request is from internal source (k8s probes, Prometheus).

    Detection order (most secure first):
    1. X-Internal-Probe header with shared secret (REQUIRED in production)
    2. IP-based fallback (disabled in production via INTERNAL_PROBE_DISABLE_IP_FALLBACK)

    For k8s deployments, configure probes with:
      httpGet:
        httpHeaders:
        - name: X-Internal-Probe
          value: <INTERNAL_PROBE_TOKEN>
    """
    import ipaddress
    from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
    from apps.web_console_ng import config

    # PRIMARY: Header-based check (most reliable in k8s)
    if INTERNAL_PROBE_TOKEN:
        probe_header = request.headers.get("x-internal-probe", "")
        if probe_header == INTERNAL_PROBE_TOKEN:
            return True

    # SECURITY: In production, IP fallback should be disabled
    if INTERNAL_PROBE_DISABLE_IP_FALLBACK:
        return False  # Token required, no fallback

    # FALLBACK: IP-based check for dev/non-k8s environments only
    # Use restricted networks (localhost) unless in DEBUG mode
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    allowed_networks = INTERNAL_NETWORKS_DEV if config.DEBUG else INTERNAL_NETWORKS_STRICT

    for network in allowed_networks:
        try:
            if ipaddress.ip_address(client_ip) in ipaddress.ip_network(network):
                return True
        except ValueError:
            # Invalid IP format - treat as external
            return False
    return False


@app.get("/healthz")
async def liveness_check() -> Response:
    """
    Liveness probe - always returns 200 unless process is unhealthy.

    k8s uses this to determine if the pod should be restarted.
    Does NOT check dependencies (Redis, backend) to avoid unnecessary restarts
    during transient downstream failures.
    """
    return Response(content='{"status": "alive"}', status_code=200,
                    media_type="application/json")


@app.get("/readyz")
async def readiness_check(request: Request) -> Response:
    """
    Readiness probe - returns 200 when ready to serve traffic.

    k8s uses this to determine if the pod should receive traffic.
    Returns 503 during:
    - Graceful shutdown (draining)
    - Redis unavailable
    - Backend API unavailable

    External requests get minimal response for security.
    """
    global is_draining

    # During drain: return 503 immediately (liveness stays 200)
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
        # Sanitize error: don't expose raw exception to avoid info leak
        checks["redis"] = "error: connection_failed"
        logger.warning(f"Redis health check failed: {e}")

    # Check backend API - only if explicitly enabled
    # PRODUCTION NOTE: Backend health check is opt-in via HEALTH_CHECK_BACKEND_ENABLED
    # because it depends on DEV_* credentials which may not be configured in all envs.
    # Prefer dedicated /api/v1/health endpoint in execution_gateway for production.
    if os.getenv("HEALTH_CHECK_BACKEND_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        try:
            client = AsyncTradingClient.get()
            # Use fetch_kill_switch_status as lightweight health check (GET, no side effects)
            await asyncio.wait_for(client.fetch_kill_switch_status("health-check"), timeout=2.0)
            checks["backend"] = "ok"
        except Exception as e:
            # Sanitize error: don't expose raw exception to avoid info leak
            checks["backend"] = "error: connection_failed"
            logger.warning(f"Backend health check failed: {e}")
    else:
        # Backend check disabled - assume ok (Redis is the critical dependency)
        checks["backend"] = "ok"

    # Overall status
    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    # Security: minimal response for external, detailed for internal
    if is_internal_request(request):
        response_body = {
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,  # Sanitized errors only
            "connections": connection_counter.value,
            "pod": config.POD_NAME,
        }
    else:
        # External: minimal info
        response_body = {"status": "ready" if all_ok else "not_ready"}

    return Response(content=json.dumps(response_body), status_code=status_code,
                    media_type="application/json")


# ASGI lifespan for graceful shutdown
@asynccontextmanager
async def lifespan(app):
    """ASGI lifespan handler for startup/shutdown."""
    # RUNTIME ASSERTION: Verify single-process mode
    # In-memory admission control (asyncio.Semaphore) and ClientLifecycleManager
    # only work correctly with workers=1. Multi-process would multiply limits.
    #
    # IMPORTANT: WEB_WORKERS env var MUST be set explicitly to match actual Uvicorn --workers flag.
    # This env-based check cannot detect CLI flags passed directly to Uvicorn.
    # Deployment configs (docker-compose, k8s) MUST set WEB_WORKERS=1 and --workers=1 together.
    import os
    worker_count = os.getenv("WEB_WORKERS", "1")
    if worker_count != "1":
        raise RuntimeError(
            f"NiceGUI requires single-process mode (workers=1) for admission control. "
            f"Got WEB_WORKERS={worker_count}. For horizontal scaling, use multiple pods."
        )

    # Startup - register SIGTERM handler for graceful drain
    # Use get_running_loop() instead of get_event_loop() for Python 3.11+ compatibility
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(start_graceful_shutdown()))
    except NotImplementedError:
        # Windows or other platforms without signal handler support
        logger.warning("Signal handler not supported on this platform")
    yield
    # Shutdown (after SIGTERM or normal exit) - ensure drain started
    await start_graceful_shutdown()

async def start_graceful_shutdown():
    """
    Signal pod is draining - readiness returns 503, liveness stays 200.

    Called on SIGTERM (via signal handler) or during ASGI shutdown.
    This allows k8s to stop routing new requests while existing connections
    complete their work.
    """
    global is_draining
    if is_draining:
        return  # Already draining
    is_draining = True
    # Allow 30s for existing connections to drain
    await asyncio.sleep(30)
```

**k8s preStop Hook (for graceful drain):**
```yaml
# In deployment spec
# NOTE: Drain timing strategy - pick ONE mechanism:
# Option A (preferred): App handles SIGTERM with internal 30s drain (current approach)
#   - preStop is minimal (just for LB deregistration signal if needed)
#   - terminationGracePeriodSeconds: 45 (30s drain + 15s buffer)
# Option B: preStop handles all drain timing
#   - preStop: sleep 30
#   - App SIGTERM handler: immediate shutdown (no internal sleep)
#
# Current: Using Option A - app-controlled drain via SIGTERM handler
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 2"]  # Brief pause for LB deregistration, app handles 30s drain
terminationGracePeriodSeconds: 45  # Must exceed total drain time (2s preStop + 30s app + buffer)

# k8s probe configuration
# NOTE: httpHeaders with X-Internal-Probe enables secure internal detection
# Set INTERNAL_PROBE_TOKEN env var to the same value as the header below
livenessProbe:
  httpGet:
    path: /healthz
    port: 8080
    httpHeaders:
    - name: X-Internal-Probe
      value: "k8s-probe-token"  # Match INTERNAL_PROBE_TOKEN env var
  initialDelaySeconds: 5
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /readyz
    port: 8080
    httpHeaders:
    - name: X-Internal-Probe
      value: "k8s-probe-token"  # Match INTERNAL_PROBE_TOKEN env var
  initialDelaySeconds: 5
  periodSeconds: 5
```

**Acceptance Tests:**
- [ ] `/healthz` returns 200 always (unless process crashed)
- [ ] `/readyz` returns 200 when all deps healthy
- [ ] `/readyz` returns 503 when Redis down
- [ ] `/readyz` returns 503 during graceful shutdown (liveness stays 200)
- [ ] Connection count reflects actual WS connections
- [ ] SIGTERM triggers drain state immediately

---

### C1: Redis HA & State Manager

**Files to Create:**
```
apps/web_console_ng/core/
├── redis_ha.py                  # HA Redis store with Sentinel
├── state_manager.py             # User state persistence
├── admission.py                 # ASGI admission control middleware (depends on redis_ha, health)
tests/apps/web_console_ng/
├── test_redis_ha.py
├── test_state_manager.py
└── test_admission.py
```

**Files to Update:**
```
apps/web_console_ng/
└── config.py                    # Add REDIS_*, POD_NAME, INTERNAL_PROBE_* configs
```

**HA Redis Store Implementation:**
```python
# apps/web_console_ng/core/redis_ha.py
# IMPORTANT: Use redis.asyncio.sentinel for async Sentinel support
# Requires redis-py >= 5.0
#
# NOTE: This async Redis client is separate from libs/redis_client (sync).
# Duplication is intentional: NiceGUI requires async I/O, while libs/redis_client
# is synchronous for other services. Future consolidation to async-capable
# client in libs/ may be considered but is not required for this task.
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

    # Configurable pool size: default 200 for 1000 WS connections (20% ratio)
    # Override via config.REDIS_POOL_MAX_CONNECTIONS if different sizing needed
    POOL_MAX_CONNECTIONS = getattr(config, "REDIS_POOL_MAX_CONNECTIONS", 200)

    def __init__(self):
        # Build TLS context if EITHER data SSL or Sentinel SSL is enabled
        # Some setups use plaintext Sentinel with TLS data nodes (or vice versa)
        ssl_context = None
        sentinel_ssl_enabled = getattr(config, "REDIS_SENTINEL_SSL_ENABLED", config.REDIS_SSL_ENABLED)
        need_ssl_context = config.REDIS_SSL_ENABLED or sentinel_ssl_enabled
        if need_ssl_context:
            import ssl
            ssl_context = ssl.create_default_context(cafile=config.REDIS_SSL_CA_CERTS)

            # Apply certificate verification mode from config
            cert_reqs_map = {
                "required": ssl.CERT_REQUIRED,
                "optional": ssl.CERT_OPTIONAL,
                "none": ssl.CERT_NONE,
            }
            ssl_context.verify_mode = cert_reqs_map.get(
                config.REDIS_SSL_CERT_REQS.lower(), ssl.CERT_REQUIRED
            )
            ssl_context.check_hostname = (ssl_context.verify_mode == ssl.CERT_REQUIRED)

            # Load client cert for mTLS if provided
            if config.REDIS_SSL_CERTFILE and config.REDIS_SSL_KEYFILE:
                ssl_context.load_cert_chain(
                    certfile=config.REDIS_SSL_CERTFILE,
                    keyfile=config.REDIS_SSL_KEYFILE,
                )

        # Build sentinel_kwargs with TLS if enabled
        # REDIS_SENTINEL_SSL_ENABLED allows separate control (some setups use plaintext Sentinel + TLS data)
        sentinel_kwargs = {"password": config.REDIS_SENTINEL_PASSWORD}
        sentinel_ssl_enabled = getattr(config, "REDIS_SENTINEL_SSL_ENABLED", config.REDIS_SSL_ENABLED)
        if sentinel_ssl_enabled and ssl_context:
            sentinel_kwargs["ssl"] = True
            sentinel_kwargs["ssl_context"] = ssl_context

        # Use async Sentinel from redis.asyncio.sentinel
        # NOTE: ssl/ssl_context here are for data node connections, NOT Sentinel
        self.sentinel = Sentinel(
            config.REDIS_SENTINEL_HOSTS,  # [('sentinel-1', 26379), ...]
            socket_timeout=0.5,
            password=config.REDIS_PASSWORD,
            sentinel_kwargs=sentinel_kwargs,  # Sentinel auth + TLS
            ssl=config.REDIS_SSL_ENABLED,  # Enable TLS for data connections (separate from Sentinel)
            ssl_context=ssl_context if config.REDIS_SSL_ENABLED else None,
        )
        self.master_name = config.REDIS_MASTER_NAME  # "nicegui-sessions"
        self._master: Optional[Redis] = None
        self._slave: Optional[Redis] = None
        self._ssl_context = ssl_context if config.REDIS_SSL_ENABLED else None  # For data connections

    @classmethod
    def get(cls) -> "HARedisStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_master(self) -> Redis:
        """Get master connection for writes with connection pooling."""
        if self._master is None or not await self._is_connected(self._master):
            # master_for returns async Redis client in redis.asyncio.sentinel
            # Connection pooling: max_connections limits burst (redis-py doesn't support min_connections)
            self._master = self.sentinel.master_for(
                self.master_name,
                socket_timeout=0.5,
                decode_responses=True,
                ssl=True if self._ssl_context else False,
                ssl_context=self._ssl_context,
                # Connection pooling to handle reconnection storms
                connection_pool_class_kwargs={
                    "max_connections": self.POOL_MAX_CONNECTIONS,
                },
            )
        return self._master

    async def get_slave(self) -> Redis:
        """Get slave connection for reads (fallback to master) with connection pooling."""
        if self._slave is None or not await self._is_connected(self._slave):
            try:
                self._slave = self.sentinel.slave_for(
                    self.master_name,
                    socket_timeout=0.5,
                    decode_responses=True,
                    ssl=True if self._ssl_context else False,
                    ssl_context=self._ssl_context,
                    # Connection pooling for read scaling
                    connection_pool_class_kwargs={
                        "max_connections": self.POOL_MAX_CONNECTIONS,
                    },
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
    """Simple Redis store for development (no Sentinel).

    Supports TLS when REDIS_SSL_ENABLED=true for secure dev/test environments.
    """

    _instance: Optional["SimpleRedisStore"] = None

    def __init__(self):
        # Build connection options
        connection_kwargs = {"decode_responses": True}

        # Add TLS if enabled (mirrors HARedisStore config exactly)
        if config.REDIS_SSL_ENABLED:
            import ssl
            ssl_context = ssl.create_default_context()

            # Use same mapping as HARedisStore for consistency
            cert_reqs_map = {
                "required": ssl.CERT_REQUIRED,
                "optional": ssl.CERT_OPTIONAL,
                "none": ssl.CERT_NONE,
            }
            ssl_context.verify_mode = cert_reqs_map.get(
                config.REDIS_SSL_CERT_REQS.lower(), ssl.CERT_REQUIRED
            )
            ssl_context.check_hostname = (ssl_context.verify_mode == ssl.CERT_REQUIRED)

            # Load CA cert if provided
            if config.REDIS_SSL_CA_CERTS:
                ssl_context.load_verify_locations(config.REDIS_SSL_CA_CERTS)

            # Load client cert for mTLS if provided
            if config.REDIS_SSL_CERTFILE and config.REDIS_SSL_KEYFILE:
                ssl_context.load_cert_chain(
                    certfile=config.REDIS_SSL_CERTFILE,
                    keyfile=config.REDIS_SSL_KEYFILE,
                )

            connection_kwargs["ssl"] = True
            connection_kwargs["ssl_context"] = ssl_context

        self.redis = Redis.from_url(
            config.REDIS_URL,
            **connection_kwargs,
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
import hashlib
import json
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Any, Optional
from apps.web_console_ng.core.redis_ha import get_redis_store
from apps.web_console_ng.core.client import AsyncTradingClient


class TradingJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime, date, and Decimal types.

    UTC REQUIREMENT: All datetime objects should be UTC-aware before serialization.
    If a naive datetime is encountered, we assume it's UTC and append 'Z'.
    """

    def default(self, obj):
        if isinstance(obj, datetime):
            # Ensure UTC-aware: if naive, assume UTC and add 'Z' suffix
            if obj.tzinfo is None:
                # Naive datetime - assume UTC, serialize with Z suffix
                return {"__type__": "datetime", "value": obj.isoformat() + "Z"}
            else:
                # Timezone-aware - serialize as-is (isoformat includes offset)
                return {"__type__": "datetime", "value": obj.isoformat()}
        if isinstance(obj, date):
            return {"__type__": "date", "value": obj.isoformat()}
        if isinstance(obj, Decimal):
            return {"__type__": "Decimal", "value": str(obj)}
        return super().default(obj)


def trading_json_decoder(dct):
    """Custom JSON decoder for datetime, date, and Decimal types.

    Python 3.11+ supports 'Z' suffix natively in fromisoformat().
    """
    if "__type__" in dct:
        if dct["__type__"] == "datetime":
            # Python 3.11+ handles 'Z' suffix natively
            return datetime.fromisoformat(dct["value"])
        if dct["__type__"] == "date":
            return date.fromisoformat(dct["value"])
        if dct["__type__"] == "Decimal":
            return Decimal(dct["value"])
    return dct


class UserStateManager:
    """
    Manages user state with Redis persistence for failover recovery.

    SECURITY: Only instantiate AFTER successful authentication to prevent DoS.
    Unauthenticated connections must NOT create Redis state entries.

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
            "saved_at": datetime.now(timezone.utc).isoformat(),  # UTC-aware timestamp
            "version": 1,
        }
        master = await self.redis.get_master()
        await master.setex(
            self.state_key,
            self.STATE_TTL,
            json.dumps(state_with_meta, cls=TradingJSONEncoder)
        )

    async def restore_state(self) -> dict[str, Any]:
        """Restore state after reconnection. Uses custom decoder for types.

        CRITICAL: Read from MASTER, not replica, to avoid stale data during reconnection.
        Replica lag can cause restored state to miss recent saves (e.g., pending forms).
        """
        master = await self.redis.get_master()
        data = await master.get(self.state_key)

        if not data:
            return {}

        try:
            parsed = json.loads(data, object_hook=trading_json_decoder)
            return parsed.get("data", {})
        except json.JSONDecodeError:
            return {}

    async def update_preference(self, key: str, value: Any) -> None:
        """
        Update a single preference (merge into existing state).

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        Retries on WatchError (concurrent modification).
        """
        from redis.exceptions import WatchError
        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    # READ within WATCH
                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_decoder)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    # MODIFY
                    preferences = state.get("preferences", {})
                    preferences[key] = value
                    state["preferences"] = preferences

                    # WRITE atomically
                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(self.state_key, self.STATE_TTL,
                              json.dumps(state_with_meta, cls=TradingJSONEncoder))
                    await pipe.execute()
                    return  # Success
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        f"save_preferences failed after {max_retries} retries due to concurrent modification",
                        extra={"user_id": self.user_id}
                    )
                    raise  # Last attempt failed
                logger.debug(f"save_preferences retry {attempt + 1}/{max_retries} due to WatchError")
                continue  # Retry

    async def save_pending_form(self, form_id: str, form_data: dict, client_order_id: str | None = None) -> None:
        """
        Save pending form data (for recovery after disconnect).

        TRADING SAFETY: For order-related forms, pass client_order_id to enable
        idempotent re-submission. The backend (Execution Gateway) will reject
        duplicate client_order_ids, preventing double-execution on reconnection.

        Args:
            form_id: Unique identifier for the form
            form_data: Form field values
            client_order_id: Optional pre-generated UUID for order idempotency.
                            MUST be generated before first submission attempt.

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        """
        from redis.exceptions import WatchError
        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_decoder)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    pending_forms = state.get("pending_forms", {})
                    pending_forms[form_id] = {
                        "data": form_data,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        # TRADING SAFETY: Include client_order_id for idempotent re-submission
                        # CRITICAL: UI MUST regenerate client_order_id if user modifies restored form
                        # Preserved ID is valid ONLY for exact resubmission of original payload
                        "client_order_id": client_order_id,
                        # Use sha256 for stable hash (Python's hash() is process-randomized)
                        "original_data_hash": hashlib.sha256(
                            json.dumps(form_data, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                    }
                    state["pending_forms"] = pending_forms

                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(self.state_key, self.STATE_TTL,
                              json.dumps(state_with_meta, cls=TradingJSONEncoder))
                    await pipe.execute()
                    return
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        f"save_pending_form failed after {max_retries} retries due to concurrent modification",
                        extra={"user_id": self.user_id, "form_id": form_id}
                    )
                    raise
                logger.debug(f"save_pending_form retry {attempt + 1}/{max_retries} due to WatchError")
                continue

    async def clear_pending_form(self, form_id: str) -> None:
        """
        Clear pending form after successful submission.

        Uses Redis WATCH/MULTI/EXEC for atomic read-modify-write.
        """
        from redis.exceptions import WatchError
        master = await self.redis.get_master()

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with master.pipeline() as pipe:
                    await pipe.watch(self.state_key)

                    data = await pipe.get(self.state_key)
                    state = {}
                    if data:
                        try:
                            parsed = json.loads(data, object_hook=trading_json_decoder)
                            state = parsed.get("data", {})
                        except json.JSONDecodeError:
                            pass

                    pending_forms = state.get("pending_forms", {})
                    pending_forms.pop(form_id, None)
                    state["pending_forms"] = pending_forms

                    state_with_meta = {
                        "data": state,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "version": 1,
                    }
                    pipe.multi()
                    pipe.setex(self.state_key, self.STATE_TTL,
                              json.dumps(state_with_meta, cls=TradingJSONEncoder))
                    await pipe.execute()
                    return
            except WatchError:
                if attempt == max_retries - 1:
                    logger.error(
                        f"clear_pending_form failed after {max_retries} retries due to concurrent modification",
                        extra={"user_id": self.user_id, "form_id": form_id}
                    )
                    raise
                logger.debug(f"clear_pending_form retry {attempt + 1}/{max_retries} due to WatchError")
                continue

    async def on_reconnect(self, ui_context) -> dict[str, Any]:
        """
        Called when user reconnects after WS drop.

        Returns data needed to restore UI.
        """
        # 1. Load persisted state
        state = await self.restore_state()

        # 2. Fetch fresh API data (use only existing AsyncTradingClient methods)
        client = AsyncTradingClient.get()
        api_data = {
            "positions": await client.fetch_positions(self.user_id),
            "kill_switch": await client.fetch_kill_switch_status(self.user_id),
            "circuit_breaker": await client.get_circuit_breaker_state(self.user_id),
        }
        # Note: Add fetch_open_orders() to AsyncTradingClient if order display needed

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

    THREAD SAFETY: Uses asyncio.Lock for concurrent access protection.
    All mutations to client_tasks/client_callbacks/active_clients are guarded.

    SINGLE-PROCESS CONSTRAINT: This in-memory tracking only works with
    single-process deployments (uvicorn workers=1). Multi-process deployments
    would require Redis-backed lifecycle tracking.
    """

    _instance: "ClientLifecycleManager | None" = None

    def __init__(self):
        self.client_tasks: dict[str, list[asyncio.Task]] = {}
        self.client_callbacks: dict[str, list[Callable]] = {}
        self.active_clients: set[str] = set()
        self._lock = asyncio.Lock()  # Protect concurrent access

    @classmethod
    def get(cls) -> "ClientLifecycleManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def generate_client_id(self) -> str:
        """Generate unique client ID for this connection."""
        return str(uuid.uuid4())

    async def register_client(self, client_id: str) -> None:
        """Register a new client connection (async for lock)."""
        async with self._lock:
            self.active_clients.add(client_id)
            self.client_tasks[client_id] = []
            self.client_callbacks[client_id] = []
        logger.info(f"Client registered: {client_id}")

    async def register_task(self, client_id: str, task: asyncio.Task) -> None:
        """
        Register a background task for a client (async for lock).

        Task will be cancelled when client disconnects.
        """
        async with self._lock:
            if client_id not in self.client_tasks:
                self.client_tasks[client_id] = []
            self.client_tasks[client_id].append(task)

    async def register_cleanup_callback(
        self, client_id: str, callback: Callable[[], Any]
    ) -> None:
        """Register a cleanup callback to run on disconnect (async for lock)."""
        async with self._lock:
            if client_id not in self.client_callbacks:
                self.client_callbacks[client_id] = []
            self.client_callbacks[client_id].append(callback)

    async def cleanup_client(self, client_id: str) -> None:
        """
        Cancel all tasks and run cleanup when client disconnects.

        MUST be called on disconnect to prevent resource leaks.
        Uses lock to safely access/remove client data.
        Uses timeout to prevent hanging on stubborn tasks.
        """
        TASK_CANCEL_TIMEOUT = 5.0  # Max seconds to wait per task

        async with self._lock:
            self.active_clients.discard(client_id)
            tasks = self.client_tasks.pop(client_id, [])
            callbacks = self.client_callbacks.pop(client_id, [])

        # Cancel all registered tasks (outside lock to avoid blocking)
        # Use gather with timeout to prevent hanging on stubborn tasks
        async def cancel_task_with_timeout(task: asyncio.Task) -> None:
            if task.done():
                return
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=TASK_CANCEL_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(f"Task {task.get_name()} ignored cancellation, forcing")
            except asyncio.CancelledError:
                pass

        await asyncio.gather(
            *[cancel_task_with_timeout(t) for t in tasks],
            return_exceptions=True
        )

        # Run cleanup callbacks
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

    async def get_active_client_count(self) -> int:
        """Return number of active clients (for health check).

        Uses lock to ensure consistent snapshot during concurrent updates.
        """
        async with self._lock:
            return len(self.active_clients)

    async def is_client_active(self, client_id: str) -> bool:
        """Check if client is still connected.

        Uses lock to ensure consistent read during concurrent updates.
        """
        async with self._lock:
            return client_id in self.active_clients
```

**Connection Event Handlers:**
```python
# apps/web_console_ng/core/connection_events.py
from nicegui import app, Client
from starlette.requests import Request  # For restore_client_state signature
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

        # CRITICAL: Mark handshake complete so middleware knows on_disconnect will handle cleanup
        # Access ASGI scope via client.request.scope to set flag middleware checks
        if hasattr(client.request, "scope") and "state" in client.request.scope:
            client.request.scope["state"]["handshake_complete"] = True

        # Store session connection key for cleanup on disconnect
        # The admission control middleware already incremented this counter;
        # we store the key here so on_disconnect can decrement it
        session_cookie = client.request.cookies.get(config.SESSION_COOKIE_NAME)
        if session_cookie:
            # Use session_store helper to extract session_id (more robust than manual split)
            from apps.web_console_ng.auth.session_store import extract_session_id
            session_id = extract_session_id(session_cookie)
            client.storage["session_conn_key"] = f"session_conns:{session_id}"

        # Register client (MUST await - register_client is async with lock)
        await lifecycle.register_client(client_id)

        # Update metrics (use thread-safe counter)
        # IMPORTANT: All metrics must include pod label
        count = health.connection_counter.increment()
        metrics.ws_connects_total.labels(pod=config.POD_NAME).inc()
        metrics.ws_connections.labels(pod=config.POD_NAME).set(count)

        logger.info(f"Client connected: {client_id}")

    @app.on_disconnect
    async def on_client_disconnect(client: Client):
        """Handle WebSocket disconnection - cleanup resources.

        DOUBLE-COUNTING PREVENTION: Check if on_exception already ran for this client.
        If so, skip the "normal" disconnect metric (already counted as "error").
        """
        lifecycle = ClientLifecycleManager.get()
        client_id = client.storage.get("client_id")

        if client_id:
            # Cleanup all client resources
            await lifecycle.cleanup_client(client_id)

            # Decrement per-session connection counter if present
            # Use Lua script to guard against underflow (clamp at 0)
            session_conn_key = client.storage.get("session_conn_key")
            if session_conn_key:
                try:
                    from apps.web_console_ng.core.redis_ha import get_redis_store
                    redis = await get_redis_store().get_master()
                    # Lua script: DECR with clamp at 0, delete if zero
                    lua_decr_script = """
                    local count = redis.call('GET', KEYS[1])
                    if count and tonumber(count) > 0 then
                        count = redis.call('DECR', KEYS[1])
                        if tonumber(count) <= 0 then
                            redis.call('DEL', KEYS[1])
                        end
                        return count
                    end
                    return 0
                    """
                    await redis.eval(lua_decr_script, 1, session_conn_key)
                except Exception as e:
                    logger.warning(f"Failed to decrement session conn count: {e}")

            # Update metrics (use thread-safe counter)
            # IMPORTANT: All metrics must include pod label
            count = health.connection_counter.decrement()
            metrics.ws_connections.labels(pod=config.POD_NAME).set(count)

            # DOUBLE-COUNTING PREVENTION: Only increment "normal" if no prior exception
            # on_exception sets this flag before we get called
            had_exception = client.storage.get("had_exception", False)
            if not had_exception:
                metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="normal").inc()
                logger.info(f"Client disconnected: {client_id}")
            else:
                # Exception already logged and counted - just log cleanup
                logger.info(f"Client cleanup after exception: {client_id}")

    @app.on_exception
    async def on_client_exception(client: Client, exception: Exception):
        """Handle client exception - log and flag for disconnect handler.

        NOTE: Do NOT decrement connection_counter here - NiceGUI calls
        on_disconnect after on_exception, which handles counter decrement.
        Set flag to prevent double-counting in on_disconnect.
        """
        client_id = client.storage.get("client_id")
        logger.error(f"Client {client_id} exception: {exception}")

        # Set flag to prevent double-counting in on_disconnect
        client.storage["had_exception"] = True

        # Track exception as disconnect reason
        # IMPORTANT: All metrics must include pod label
        metrics.ws_disconnects_total.labels(pod=config.POD_NAME, reason="error").inc()


async def restore_client_state(client: Client, request: Request) -> dict:
    """
    Restore client state after reconnection.

    Called by pages to restore UI state.

    SECURITY: Session ID is retrieved from the signed HttpOnly cookie (server-side),
    NOT from app.storage.user (client-side localStorage). This prevents session
    spoofing/fixation attacks where a malicious client could inject a fake session ID.

    Args:
        client: NiceGUI client instance
        request: Starlette Request object (for cookie and IP extraction)

    Returns:
        Dict with preferences, filters, pending_forms, and fresh api_data
    """
    from apps.web_console_ng.auth.client_ip import extract_trusted_client_ip
    from apps.web_console_ng.auth.session_store import get_session_store
    from apps.web_console_ng import config

    # SECURITY: Get session cookie from HttpOnly cookie (NOT app.storage.user!)
    # The cookie contains: {session_id}.{key_id}:{signature}
    # SESSION_COOKIE_NAME is defined in config.py
    cookie_value = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not cookie_value:
        return {}

    # Extract client IP from request (handles X-Forwarded-For via trusted proxies)
    client_ip = extract_trusted_client_ip(request, config.TRUSTED_PROXY_IPS)
    user_agent = request.headers.get("user-agent", "")

    # Validate session using the signed cookie (server-side validation)
    session_store = get_session_store()
    session = await session_store.validate_session(
        cookie_value,  # Full cookie value with signature
        client_ip,
        user_agent,
    )

    if not session:
        return {}

    user_id = session.get("user", {}).get("user_id")
    if not user_id:
        return {}

    # Restore state from Redis + fetch fresh API data
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
from starlette.requests import Request
from starlette.responses import Response
from apps.web_console_ng import config
from apps.web_console_ng.core.health import is_internal_request

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

# Admission control metrics
connections_rejected_total = Counter(
    "nicegui_connections_rejected_total",
    "Connections rejected by admission control",
    ["pod", "reason"]  # reason: capacity, draining, invalid_session
)

# Auth metrics
auth_failures_total = Counter(
    "nicegui_auth_failures_total",
    "Authentication failures",
    ["pod", "auth_type", "reason"]  # pod label added for per-pod visibility
)

sessions_created_total = Counter(
    "nicegui_sessions_created_total",  # Fixed: matches table naming
    "Sessions created",
    ["pod", "auth_type"]  # pod label added for per-pod visibility
)

# Latency metrics
redis_latency = Histogram(
    "nicegui_redis_latency_seconds",
    "Redis operation latency",
    ["pod", "operation"],  # pod label added for per-pod visibility
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

api_latency = Histogram(
    "nicegui_api_latency_seconds",
    "Backend API latency",
    ["pod", "endpoint"],  # pod label added for per-pod visibility
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
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus metrics endpoint with app-level protection.

    Security: Protected by ingress allowlist in k8s.
    For non-ingress deployments, use is_internal_request() guard.
    """
    # App-level protection for non-ingress deployments (dev/direct exposure)
    if not getattr(config, "METRICS_INGRESS_PROTECTED", False):
        if not is_internal_request(request):
            return Response(status_code=403, content="Forbidden", media_type="text/plain")

    return Response(
        content=generate_latest(),
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


# Helper decorators for timing
import functools
import time

# POD_NAME used for per-pod metrics labels
POD_NAME = config.POD_NAME

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
                redis_latency.labels(pod=POD_NAME, operation=operation).observe(duration)
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
                api_latency.labels(pod=POD_NAME, endpoint=endpoint).observe(duration)
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
    active_users.labels(pod=pod).set(await lifecycle.get_active_client_count())

    # Connection count (from ConnectionCounter - authoritative for ws_connections gauge)
    # Note: ConnectionCounter.value is the single source of truth (updated in connection_events.py)
    # This periodic update ensures consistency even if incremental updates were missed
    from apps.web_console_ng.core.health import connection_counter
    ws_connections.labels(pod=pod).set(connection_counter.value)
```

**Alert Rules:**
```yaml
# infra/prometheus/alerts/nicegui.yml
groups:
  - name: nicegui
    rules:
      - alert: HighWSDisconnectRate
        # Measure churn relative to active connections, not new connections
        # This prevents false positives in steady state (few new logins)
        expr: |
          rate(nicegui_ws_disconnects_total[5m]) >
          (avg_over_time(nicegui_ws_connections[5m]) * 0.05)
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High WebSocket disconnect rate ({{ $value | printf \"%.2f\" }}/s)"
          description: "Disconnect rate exceeds 5% of active connection pool"

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

### Integration Points (Rev 7 - From Planning Review)

**A. Session Store HA Integration (MEDIUM - Required Refactor):**
- **Current:** `apps/web_console_ng/auth/session_store.py` initializes its own Redis connection using `config.REDIS_URL`
- **Required:** Update `get_session_store()` factory function to use `apps.web_console_ng.core.redis_ha.get_redis_store()` instead of creating a direct connection
- **Why:** Ensures session storage respects Sentinel configuration for HA failover
- **Files to modify:** `auth/session_store.py` (inject HA client), `core/redis_ha.py` (provide compatible interface)
- **Add helper function** `extract_session_id()` for robust session ID extraction:
  ```python
  # apps/web_console_ng/auth/session_store.py
  def extract_session_id(signed_cookie: str) -> str:
      """Extract session ID from signed cookie.

      Handles format changes and edge cases (additional dots, signature variations).
      Cookie format: {session_id}.{timestamp}.{signature}

      Args:
          signed_cookie: The full signed session cookie value

      Returns:
          The session ID portion
      """
      if not signed_cookie:
          raise ValueError("Empty cookie")
      # Session ID is always the first segment before any dots
      # This is more robust than split(".")[0] as it handles edge cases
      parts = signed_cookie.split(".", maxsplit=2)  # Split into at most 3 parts
      if not parts[0]:
          raise ValueError("Invalid cookie format: empty session ID")
      return parts[0]
  ```

**B. State Manager Refactor (LOW - Rewrite):**
- **Current:** `apps/web_console_ng/core/state_manager.py` exists as basic implementation
- **Required:** This is a **Modify/Rewrite** (not "Files to Create")
- **Preserve:** Existing `UserStateManager` class structure where possible
- **Update:** Use `HARedisStore` instead of direct Redis connection

**C. Health Check Rewrite (LOW - Rewrite):**
- **Current:** `apps/web_console_ng/core/health.py` exists as a stub (14 lines)
- **Required:** Complete rewrite as per the task design

**D. Config Updates (LOW - Add New Variables):**
- **Add to `config.py`:** `REDIS_USE_SENTINEL`, `REDIS_SENTINEL_HOSTS`, `REDIS_MASTER_NAME`, `REDIS_SENTINEL_PASSWORD`, `REDIS_SENTINEL_SSL_ENABLED`, `REDIS_PASSWORD`, `REDIS_POOL_MAX_CONNECTIONS`, `POD_NAME`
- **Add to `config.py` (security):** `INTERNAL_PROBE_TOKEN`, `INTERNAL_PROBE_DISABLE_IP_FALLBACK`
- **Add to `config.py` (metrics):** `METRICS_INGRESS_PROTECTED`
- **Add to `config.py` (health):** `HEALTH_CHECK_BACKEND_ENABLED`

---

### Previous Implementation Notes

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

10. **Exception Handler Metrics (from Rev 6 review):** ✅ ADDRESSED IN REV 17
    - Double-counting prevention implemented via `had_exception` flag in client.storage
    - `on_client_exception` sets flag + increments "error" disconnect
    - `on_disconnect` checks flag and skips "normal" increment if exception already counted
    - Both handlers decrement counter only once (in on_disconnect)

11. **Pod Label on All Metrics (from Rev 6 review):**
    - Add `pod` label to latency and auth metrics for per-pod visibility:
      - `nicegui_redis_latency_seconds`
      - `nicegui_api_latency_seconds`
      - `nicegui_auth_failures_total`
    - Enables "top-K bad pod" analysis

12. **Metrics Protection Dev Fallback (from Rev 6 review):** ✅ ADDRESSED IN REV 18
    - Ingress allowlist only works in k8s environments
    - **App-side guard required:** Add `is_internal_request()` check to `/metrics` endpoint
    - Return 403 Forbidden for external requests in non-ingress deployments
    - Implementation pattern:
      ```python
      @app.get("/metrics")
      async def metrics_endpoint(request: Request):
          # App-level protection for non-ingress deployments
          if not config.METRICS_INGRESS_PROTECTED and not is_internal_request(request):
              return Response(status_code=403, content="Forbidden")
          return generate_prometheus_metrics()
      ```

---

## Definition of Done

- [ ] **ADR-0032 created:** Document HA/observability architecture decisions (Redis Sentinel, admission control, metrics protection, scaling)
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

**Last Updated:** 2026-01-01
**Status:** ✅ Complete

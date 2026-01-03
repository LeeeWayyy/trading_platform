# Nginx (Web Console Proxy)

## Identity
- **Type:** Infrastructure
- **Port:** 80/443
- **Container:** trading_platform_nginx_mtls / trading_platform_nginx_oauth2 (profile-specific)

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| Upstream | `nicegui_cluster` | Load-balanced NiceGUI backends. |
| Proxy | WebSocket enabled | Supports long-lived NiceGUI connections. |
| Health paths | `/healthz`, `/readyz` | Liveness/readiness routing. |
- **Version:** N/A (custom image in `apps/web_console/nginx/Dockerfile`)
- **Persistence:** N/A

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Reverse proxy routing
**Purpose:** Route web console traffic to NiceGUI backends with sticky affinity.

**Preconditions:**
- Upstream servers are reachable at `nicegui-1:8080`, etc.

**Postconditions:**
- HTTP/WebSocket traffic forwarded to NiceGUI cluster.

**Behavior:**
1. Uses `ip_hash` to keep client affinity.
2. Proxies `/` with WebSocket headers.
3. Exposes `/healthz` and `/readyz` routes.

**Raises:**
- N/A (Nginx logs proxy errors).

### Invariants
- WebSocket headers are always set for `/`.

### State Machine (if stateful)
```
[Listening] --> [Proxying]
```
- **States:** Listening, Proxying
- **Transitions:** Request handling.

## Data Flow
```
Client --> Nginx --> NiceGUI cluster
```
- **Input format:** HTTP/WebSocket.
- **Output format:** HTTP/WebSocket.
- **Side effects:** None.

## Usage Examples
### Example 1: Inspect upstream config
```bash
cat infra/nginx/nicegui-cluster.conf
```

### Example 2: Inspect proxy location
```bash
cat infra/nginx/nicegui-location.conf
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Backend down | One node unavailable | Nginx routes to remaining upstreams. |
| WebSocket idle | Long-lived connection | Allowed via 86400s timeouts. |
| Proxy headers missing | Misconfig | NiceGUI session issues. |

## Dependencies
- **Internal:** `infra/nginx/*.conf`, `apps/web_console/nginx/Dockerfile`
- **External:** NiceGUI backends

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AUTH_MODE` | Yes | `mtls`/`oauth2` | Selects nginx config template.
| `CSP_REPORT_ONLY` | No | `false` | CSP enforcement mode (oauth2 profile).
| `TRUSTED_PROXY_SUBNET` | No | `172.28.0.0/24` | Real IP trust boundary.

## Error Handling
- Proxy errors logged in Nginx logs.

## Observability (Services only)
### Health Check
- **Endpoint:** `/healthz`
- **Checks:** Upstream liveness routing.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Nginx metrics not configured. |

## Security
- **Auth Required:** Yes (mTLS or OAuth2)
- **Auth Method:** mTLS or OAuth2 via auth_service
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `docker-compose.md`
- `auth_service` (service spec)

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/nginx/*.conf`, `apps/web_console/nginx/Dockerfile`, `docker-compose.yml`
- **ADRs:** N/A

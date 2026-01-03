# Redis

## Identity
- **Type:** Infrastructure
- **Port:** 6379
- **Container:** trading_platform_redis

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| Image | `redis:7-alpine` | Redis container image. |
| Port mapping | `127.0.0.1:6379:6379` | Local dev port exposure. |
| Persistence | AOF + volume `redisdata` | Append-only file enabled. |
| Command | `redis-server --appendonly yes` | Enables AOF. |
- **Version:** Redis 7 (alpine)
- **Persistence:** Yes

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Data store
**Purpose:** Provide caching, pub/sub, and state storage for services.

**Preconditions:**
- Redis container healthy (PING ok).

**Postconditions:**
- Services can read/write keys and streams.

**Behavior:**
1. Accepts commands on port 6379.
2. Persists writes via AOF.
3. Healthcheck uses `redis-cli ping`.

**Raises:**
- N/A (errors returned per command).

### Invariants
- AOF persistence is enabled in docker-compose.

### State Machine (if stateful)
```
[Running] --> [Serving]
```
- **States:** Running, Serving
- **Transitions:** Continuous service.

## Data Flow
```
Services --> Redis --> Services
```
- **Input format:** Redis commands/protocol.
- **Output format:** Redis responses.
- **Side effects:** Data stored in volume.

## Usage Examples
### Example 1: Ping
```bash
docker exec -it trading_platform_redis redis-cli ping
```

### Example 2: Inspect persistence
```bash
ls -la /var/lib/docker/volumes/redisdata/_data
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Redis down | Connection refused | Services error/retry. |
| Disk full | Writes fail | Redis returns error. |
| AOF corruption | Bad file | Redis may refuse start or repair. |

## Dependencies
- **Internal:** `libs/redis_client/`
- **External:** None

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | No | `redis://redis:6379/0` | Default connection URL in services. |
| `REDIS_HOST` | No | `redis` | Hostname in Docker network. |
| `REDIS_PORT` | No | `6379` | Port.

## Error Handling
- Redis returns command-level errors; services handle retries.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** `redis-cli ping` in docker-compose healthcheck.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Redis metrics not exported here. |

## Security
- **Auth Required:** No (dev)
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/libs/redis_client/`
- **Run Tests:** `pytest tests/libs/redis_client -v`
- **Coverage:** N/A

## Related Specs
- `../libs/redis_client.md`
- `docker-compose.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `docker-compose.yml`
- **ADRs:** N/A

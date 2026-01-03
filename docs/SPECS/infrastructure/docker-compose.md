# Docker Compose Overview

## Identity
- **Type:** Infrastructure
- **Port:** N/A
- **Container:** N/A (orchestrates multiple containers)

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| Compose file | `docker-compose.yml` | Base local/dev stack.
| Profiles | `dev`, `workers`, `mtls`, `oauth2`, `manual` | Service selection.
| Volumes | `pgdata`, `redisdata`, `prometheusdata`, `grafanadata`, `lokidata`, `promtaildata`, `backtest_data` | Persistent data.
| Networks | `default`, `trading_platform` | Service connectivity.
- **Version:** Docker Compose V2 (no `version:` field)
- **Persistence:** Yes (named volumes)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Profile-based startup
**Purpose:** Allow partial stacks for dev, workers, or secure web console modes.

**Preconditions:**
- Docker and docker compose available.

**Postconditions:**
- Selected services start with configured dependencies.

**Behavior:**
1. Base services (postgres, redis, observability) start without profiles.
2. `dev` profile starts API services and web console.
3. `workers` profile starts backtest/alert workers.
4. `mtls`/`oauth2` profiles start nginx + auth variants.

**Raises:**
- N/A (compose logs errors).

### Invariants
- `postgres` and `redis` are prerequisites for most services.

### State Machine (if stateful)
```
[Stopped] --> [Starting] --> [Healthy]
```
- **States:** Stopped, Starting, Healthy
- **Transitions:** Docker healthchecks control readiness.

## Data Flow
```
Docker Compose --> Containers --> Service network
```
- **Input format:** YAML service definitions.
- **Output format:** Running containers + networks.
- **Side effects:** Creates named volumes and networks.

## Usage Examples
### Example 1: Start dev stack
```bash
docker compose --profile dev up -d
```

### Example 2: Start workers only
```bash
docker compose --profile workers up -d
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing env vars | Required secrets unset | Compose fails for those services. |
| Port conflicts | 5433/6379 in use | Container fails to bind. |
| Profile mismatch | No profile specified | Only base services start. |

## Dependencies
- **Internal:** `apps/*/Dockerfile`, `infra/*`
- **External:** Docker Engine

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_USER` | No | `trader` | Database user.
| `POSTGRES_PASSWORD` | No | `trader` | Database password.
| `POSTGRES_DB` | No | `trader` | Database name.
| `REDIS_URL_DOCKER` | No | `redis://redis:6379/0` | Redis URL in docker network.
| `ALPACA_API_KEY_ID` | No | empty | Alpaca credentials (execution gateway).

## Error Handling
- Docker Compose surfaces build/run errors; services fail fast on missing envs.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** Healthchecks per service.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Compose does not emit metrics. |

## Security
- **Auth Required:** N/A (varies by service)
- **Auth Method:** N/A
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `redis.md`
- `postgres.md`
- `prometheus.md`
- `grafana.md`
- `loki.md`
- `promtail.md`
- `nginx.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `docker-compose.yml`
- **ADRs:** N/A

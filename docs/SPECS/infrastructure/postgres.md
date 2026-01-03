# Postgres

## Identity
- **Type:** Infrastructure
- **Port:** 5432 (container), 5433 (host)
- **Container:** trading_platform_postgres

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| Image | `postgres:16` | Postgres container image. |
| Port mapping | `127.0.0.1:5433:5432` | Host port to avoid conflicts. |
| Database | `${POSTGRES_DB:-trader}` | Default DB name. |
| User | `${POSTGRES_USER:-trader}` | Default user.
| Password | `${POSTGRES_PASSWORD:-trader}` | Default password.
| Persistence | Volume `pgdata` | Data directory persistence.
| Healthcheck | `pg_isready -U trader` | Container health. |
- **Version:** Postgres 16
- **Persistence:** Yes

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Database service
**Purpose:** Provide relational storage for orders, positions, audit logs, model registry.

**Preconditions:**
- Container healthy and accepting connections.

**Postconditions:**
- Services can read/write via `DATABASE_URL`.

**Behavior:**
1. Initializes database on first run.
2. Stores data under `/var/lib/postgresql/data`.
3. Healthcheck reports readiness.

**Raises:**
- N/A (errors returned per query).

### Invariants
- Data is persisted in `pgdata` volume.

### State Machine (if stateful)
```
[Initializing] --> [Ready]
```
- **States:** Initializing, Ready
- **Transitions:** Startup sequence.

## Data Flow
```
Services --> Postgres --> Services
```
- **Input format:** SQL queries over TCP.
- **Output format:** Query results.
- **Side effects:** Data persisted.

## Usage Examples
### Example 1: Connect locally
```bash
psql -h 127.0.0.1 -p 5433 -U trader -d trader
```

### Example 2: Check health
```bash
docker exec -it trading_platform_postgres pg_isready -U trader
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Incorrect credentials | Bad user/password | Connection refused.
| Port conflict | 5433 in use | Container fails to bind.
| Disk full | No space | Writes fail.

## Dependencies
- **Internal:** `db/`, `migrations/`, apps using `DATABASE_URL`
- **External:** None

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_USER` | No | `trader` | Database user.
| `POSTGRES_PASSWORD` | No | `trader` | Database password.
| `POSTGRES_DB` | No | `trader` | Database name.
| `DATABASE_URL` | No | computed | Connection URL used by services.

## Error Handling
- Postgres returns SQL errors; services handle failures.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** `pg_isready` in docker-compose healthcheck.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Postgres metrics not exported here. |

## Security
- **Auth Required:** Yes (user/password)
- **Auth Method:** Password auth (dev defaults)
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `docker-compose.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `docker-compose.yml`
- **ADRs:** N/A

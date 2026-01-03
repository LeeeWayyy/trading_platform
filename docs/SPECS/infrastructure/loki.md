# Loki

## Identity
- **Type:** Infrastructure
- **Port:** 3100
- **Container:** trading_platform_loki_server

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| `http_listen_port` | 3100 | Loki HTTP API port. |
| `grpc_listen_port` | 9096 | Loki gRPC port. |
| Storage | filesystem | Local chunk storage under `/tmp/loki`. |
| Retention | 30 days | `limits_config.retention_period`. |
- **Version:** `grafana/loki:3.0.0`
- **Persistence:** Yes (volume `lokidata` mapped to `/tmp/loki`)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Log ingestion
**Purpose:** Receive log streams pushed by promtail.

**Preconditions:**
- Promtail configured with Loki push URL.

**Postconditions:**
- Logs stored in local filesystem and queryable via API.

**Behavior:**
1. Accepts log batches via `/loki/api/v1/push`.
2. Indexes and stores chunks on disk.
3. Enforces retention and ingestion limits.

**Raises:**
- N/A (Loki returns HTTP errors for invalid payloads).

### Invariants
- Structured metadata disabled (`allow_structured_metadata: false`).

### State Machine (if stateful)
```
[Running] --> [Ingesting] --> [Compacting]
```
- **States:** Running, Ingesting, Compacting
- **Transitions:** Background compaction/retention cycles.

## Data Flow
```
Promtail --> Loki API --> Filesystem chunks --> Queries
```
- **Input format:** Loki push API JSON streams.
- **Output format:** Log query results (LogQL).
- **Side effects:** Writes to `/tmp/loki`.

## Usage Examples
### Example 1: Query readiness
```bash
curl http://localhost:3100/ready
```

### Example 2: Inspect config
```bash
cat infra/loki/loki-config.yml
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| High ingest rate | Burst traffic | Rate limited by `ingestion_*` settings. |
| Disk full | No space | Ingestion fails; errors logged. |
| Bad schema | Invalid config | Loki fails to start. |

## Dependencies
- **Internal:** `infra/promtail/promtail-config.yml`
- **External:** None

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `loki-config.yml` | Yes | N/A | Loki configuration file. |

## Error Handling
- HTTP errors on invalid push payloads.

## Observability (Services only)
### Health Check
- **Endpoint:** `/ready`
- **Checks:** Loki readiness.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `loki_request_duration_seconds` | Histogram | route | Loki request latency. |

## Security
- **Auth Required:** No (dev config)
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `promtail.md`
- `grafana.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/loki/loki-config.yml`, `docker-compose.yml`
- **ADRs:** N/A

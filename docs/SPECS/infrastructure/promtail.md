# Promtail

## Identity
- **Type:** Infrastructure
- **Port:** 9080
- **Container:** trading_platform_promtail

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| `http_listen_port` | 9080 | Promtail HTTP port. |
| `clients[0].url` | `http://loki_server:3100/loki/api/v1/push` | Loki push target. |
| `docker_sd_configs` | enabled | Scrapes Docker container logs. |
| `pipeline_stages` | json parsing | Extracts `level`, `service_name`, `trace_id`. |
- **Version:** `grafana/promtail:3.0.0`
- **Persistence:** Yes (positions at `/tmp/positions.yaml` via `promtaildata`)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Log shipping
**Purpose:** Collect container logs and push to Loki.

**Preconditions:**
- Docker socket mounted and Loki reachable.

**Postconditions:**
- Logs available in Loki with labels `service`, `container`, `compose_service`.

**Behavior:**
1. Discovers containers via Docker SD.
2. Applies relabeling and JSON parsing pipeline.
3. Pushes logs to Loki.

**Raises:**
- N/A (promtail logs errors).

### Invariants
- Container name is mapped to `service` label via regex.

### State Machine (if stateful)
```
[Discover] --> [Parse] --> [Push]
```
- **States:** Discover, Parse, Push
- **Transitions:** Continuous scrape pipeline.

## Data Flow
```
Docker logs --> Promtail --> Loki
```
- **Input format:** Docker log lines (JSON for structured logs).
- **Output format:** Loki streams with labels.
- **Side effects:** Writes positions file.

## Usage Examples
### Example 1: Inspect config
```bash
cat infra/promtail/promtail-config.yml
```

### Example 2: Check positions
```bash
cat /tmp/positions.yaml
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Loki down | Push fails | Promtail retries and logs errors. |
| Non-JSON logs | Plain text | JSON stage skips; logs still forwarded. |
| Docker socket missing | No discovery | No logs collected. |

## Dependencies
- **Internal:** `infra/promtail/promtail-config.yml`
- **External:** Docker, Loki

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `promtail-config.yml` | Yes | N/A | Promtail configuration file. |

## Error Handling
- Logs errors for failed pushes or parse errors.

## Observability (Services only)
### Health Check
- **Endpoint:** `/-/ready`
- **Checks:** Promtail readiness.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `promtail_read_bytes_total` | Counter | job | Bytes read from logs. |

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
- `loki.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/promtail/promtail-config.yml`, `docker-compose.yml`
- **ADRs:** N/A

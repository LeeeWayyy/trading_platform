# Grafana

## Identity
- **Type:** Infrastructure
- **Port:** 3000
- **Container:** trading_platform_grafana

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| Datasources | Prometheus, Loki | Provisioned via `infra/grafana/datasources/datasources.yml`. |
| Dashboards | trading-overview, service-health, performance, logs-dashboard, track7_slo, nicegui | Provisioned via `infra/grafana/dashboards/*.json`. |
| Provisioning | file provider | `infra/grafana/dashboards/dashboards.yml`. |
- **Version:** `grafana/grafana:10.4.2`
- **Persistence:** Yes (volume `grafanadata`)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Provisioning
**Purpose:** Load datasources and dashboards on startup.

**Preconditions:**
- Provisioning files mounted at `/etc/grafana/provisioning`.

**Postconditions:**
- Dashboards and datasources appear in UI.

**Behavior:**
1. Reads datasources from `datasources.yml`.
2. Loads dashboards from configured folder.
3. Serves UI on port 3000.

**Raises:**
- N/A (Grafana logs provisioning errors).

### Invariants
- Datasource names must match expected references in dashboard JSON.

### State Machine (if stateful)
```
[Boot] --> [Provisioned] --> [Serving]
```
- **States:** Boot, Provisioned, Serving
- **Transitions:** Startup provisioning then UI serving.

## Data Flow
```
Prometheus/Loki --> Grafana queries --> Dashboards
```
- **Input format:** PromQL/LogQL query results.
- **Output format:** Dashboard panels.
- **Side effects:** None.

## Usage Examples
### Example 1: Access UI
```bash
open http://localhost:3000
```

### Example 2: Check provisioning
```bash
cat infra/grafana/datasources/datasources.yml
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing datasource | Prometheus down | Panels show errors. |
| Mismatched datasource name | Dashboard refers to missing name | Panels show errors. |
| Provisioning error | Invalid JSON | Dashboard not loaded. |

## Dependencies
- **Internal:** `infra/grafana/dashboards/*`, `infra/grafana/datasources/*`
- **External:** Prometheus, Loki

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GF_SECURITY_ADMIN_USER` | No | `admin` | Default admin username (dev). |
| `GF_SECURITY_ADMIN_PASSWORD` | No | `admin` | Default admin password (dev). |

## Error Handling
- Grafana logs provisioning and datasource errors on startup.

## Observability (Services only)
### Health Check
- **Endpoint:** `/api/health`
- **Checks:** Grafana internal health.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `grafana_stat_*` | Gauge | N/A | Internal metrics (if enabled). |

## Security
- **Auth Required:** Yes (Grafana login)
- **Auth Method:** Built-in username/password (dev defaults)
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `prometheus.md`
- `loki.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/grafana/dashboards/*.json`, `infra/grafana/datasources/datasources.yml`, `infra/grafana/dashboards/dashboards.yml`, `docker-compose.yml`
- **ADRs:** N/A

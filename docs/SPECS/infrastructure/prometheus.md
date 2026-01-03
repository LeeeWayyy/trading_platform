# Prometheus

## Identity
- **Type:** Infrastructure
- **Port:** 9090
- **Container:** trading_platform_prometheus

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| `scrape_interval` | 15s | Global scrape interval. |
| `evaluation_interval` | 15s | Rule evaluation interval. |
| `scrape_timeout` | 10s | Scrape timeout. |
| `rule_files` | `alerts.yml`, `alerts/*.yml` | Alert rule sets. |
| `scrape_configs` | execution-gateway, signal-service, orchestrator, market-data-service, web-console-metrics | Target jobs. |
- **Version:** `prom/prometheus:latest` (docker-compose)
- **Persistence:** Yes (volume `prometheusdata`)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Scrape cycle
**Purpose:** Collect metrics from configured targets.

**Preconditions:**
- Targets are reachable at `http://localhost:<port>/metrics`.

**Postconditions:**
- Time series stored in local TSDB.

**Behavior:**
1. Scrapes targets at configured intervals.
2. Evaluates alert rules and emits alerts.
3. Exposes UI and query API on port 9090.

**Raises:**
- N/A (Prometheus logs errors per target).

### Invariants
- Scrape configs in `infra/prometheus/prometheus.yml` are the source of truth.

### State Machine (if stateful)
```
[Running] --> [Scraping] --> [EvaluatingRules]
```
- **States:** Running, Scraping, EvaluatingRules
- **Transitions:** Continuous loop.

## Data Flow
```
/metrics endpoints --> Scrape --> TSDB --> Alerts/UI
```
- **Input format:** Prometheus text exposition format.
- **Output format:** Time series + alerts.
- **Side effects:** Alerts (if alertmanager configured).

## Usage Examples
### Example 1: Local query
```bash
curl http://localhost:9090/api/v1/labels
```

### Example 2: Inspect config
```bash
cat infra/prometheus/prometheus.yml
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Target down | Unreachable service | Target marked down; alerts may fire. |
| Slow target | Slow response | Scrape timeout after 10s. |
| Missing rules file | Bad path | Prometheus fails to load config. |

## Dependencies
- **Internal:** `apps/*` metrics endpoints, `infra/prometheus/alerts/*.yml`
- **External:** Optional Alertmanager

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `prometheus.yml` | Yes | N/A | Primary config file. |
| `alerts.yml` | No | N/A | Alert rules. |

## Error Handling
- Logs scrape errors per target.

## Observability (Services only)
### Health Check
- **Endpoint:** `/-/healthy`
- **Checks:** Prometheus internal health.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `prometheus_build_info` | Gauge | build info | Prometheus build metadata. |

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
- `grafana.md`
- `loki.md`
- `alertmanager.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/prometheus/prometheus.yml`, `infra/prometheus/alerts/*.yml`, `docker-compose.yml`
- **ADRs:** N/A

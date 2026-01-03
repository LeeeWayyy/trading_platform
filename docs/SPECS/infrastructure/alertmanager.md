# Alertmanager

## Identity
- **Type:** Infrastructure
- **Port:** 9093 (default Alertmanager)
- **Container:** N/A (config only; not wired in docker-compose)

## Interface
### For Infrastructure: Service Configuration
| Setting | Value | Description |
|---------|-------|-------------|
| `resolve_timeout` | 5m | Delay before considering alert resolved. |
| `route` | severity-based | Routes critical to PagerDuty, warnings to Slack. |
| `receivers` | `slack-ops`, `pagerduty-platform` | Notification destinations. |
| `inhibit_rules` | critical suppresses warning | Suppression rules. |
- **Version:** N/A (config only)
- **Persistence:** N/A

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### Alert routing
**Purpose:** Route Prometheus alerts to Slack/PagerDuty based on severity.

**Preconditions:**
- Environment variables set: `SLACK_WEBHOOK_URL`, `PAGERDUTY_SERVICE_KEY`.

**Postconditions:**
- Alerts delivered to configured receivers.

**Behavior:**
1. Groups alerts by `alertname`, `severity`, `team`.
2. Routes critical alerts to PagerDuty and Slack.
3. Routes warnings to Slack.
4. Applies inhibition rules (critical suppresses warning).

**Raises:**
- N/A (Alertmanager logs delivery errors).

### Invariants
- `severity=critical` always routes to PagerDuty.

### State Machine (if stateful)
```
[Receiving] --> [Routing] --> [Notifying]
```
- **States:** Receiving, Routing, Notifying
- **Transitions:** Alert evaluation cycle.

## Data Flow
```
Prometheus alerts --> Alertmanager --> Slack/PagerDuty
```
- **Input format:** Prometheus alert payloads.
- **Output format:** Notifications.
- **Side effects:** External notifications.

## Usage Examples
### Example 1: Validate config
```bash
amtool check-config infra/alertmanager/config.yml
```

### Example 2: Inspect routes
```bash
rg -n "route:" infra/alertmanager/config.yml
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing webhook | `SLACK_WEBHOOK_URL` unset | Slack notifications fail. |
| PagerDuty key missing | `PAGERDUTY_SERVICE_KEY` unset | Critical alerts fail to send. |
| Inhibition match | Critical alert firing | Warning alert suppressed. |

## Dependencies
- **Internal:** `infra/alertmanager/config.yml`
- **External:** Slack, PagerDuty

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SLACK_WEBHOOK_URL` | Yes | N/A | Slack webhook for `#alerts-ops`.
| `PAGERDUTY_SERVICE_KEY` | Yes | N/A | PagerDuty integration key.

## Error Handling
- Delivery errors are logged by Alertmanager.

## Observability (Services only)
### Health Check
- **Endpoint:** `/-/healthy`
- **Checks:** Alertmanager health.

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `alertmanager_notifications_failed_total` | Counter | receiver | Notification failures. |

## Security
- **Auth Required:** No (config file)
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** N/A
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `prometheus.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `infra/alertmanager/config.yml`
- **ADRs:** N/A

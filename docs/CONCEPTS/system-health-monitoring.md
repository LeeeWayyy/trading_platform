# System Health Monitoring

## Overview

The System Health Monitor provides a unified dashboard for monitoring the health and performance of all trading platform services. It displays real-time status, latency metrics, queue depths, and connectivity information for microservices, Redis, and PostgreSQL.

## Architecture

```
+--------------------+     +------------------+
| Health Monitor UI  | --> | Service Probes   |
| (pages/health.py)  |     | (HTTP/Redis/PG)  |
+--------------------+     +------------------+
        |
        v
+--------------------+     +------------------+
| Streamlit Session  |     | Prometheus       |
| (cached status)    |     | (metrics)        |
+--------------------+     +------------------+
```

## RBAC Requirements

| Action | Required Permission | Required Feature Flag |
|--------|---------------------|----------------------|
| View health dashboard | `VIEW_CIRCUIT_BREAKER` | `FEATURE_HEALTH_MONITOR` |

The page uses the `@operations_requires_auth` decorator which validates:
1. User is authenticated
2. Feature flag `FEATURE_HEALTH_MONITOR` is enabled
3. User has `VIEW_CIRCUIT_BREAKER` permission

## Service Status Grid

The dashboard displays a grid of all monitored services:

| Service | Endpoint | Health Check |
|---------|----------|--------------|
| Execution Gateway | :8002/health | HTTP 200 |
| Signal Service | :8001/health | HTTP 200 |
| Risk Manager | :8003/health | HTTP 200 |
| Reconciler | :8004/health | HTTP 200 |

### Status Indicators

| Icon | Status | Description |
|------|--------|-------------|
| Green | Healthy | Service responding normally |
| Yellow | Degraded | High latency or partial failure |
| Red | Unhealthy | Service unreachable or error |
| Gray | Stale | Using cached status (fetch failed) |

## Connectivity Checks

### Redis Connectivity
- **Check:** PING command
- **Key metrics:** Connection status, latency
- **Critical for:** Circuit breaker state, feature store

### PostgreSQL Connectivity
- **Check:** Simple SELECT query
- **Key metrics:** Connection status, latency, pool usage
- **Critical for:** Audit logs, order state, positions

## Queue Depth Metrics

The dashboard monitors Redis queue depths for key data flows:

| Queue | Key Pattern | Alert Threshold |
|-------|-------------|-----------------|
| Signal Queue | `signal_queue:*` | > 100 messages |
| Order Queue | `order_queue:*` | > 50 messages |
| Alert Queue | `alert_queue:*` | > 200 messages |

Growing queues indicate processing backlogs.

## Latency Metrics

The dashboard displays percentile latencies (P50, P95, P99) for:

| Metric | Source | Target |
|--------|--------|--------|
| Gateway latency | Prometheus | P95 < 100ms |
| Signal latency | Prometheus | P95 < 500ms |
| DB query latency | Prometheus | P95 < 50ms |

## Staleness Indicators

When a health fetch fails, the dashboard displays:
- Last known status (gray icon)
- Staleness duration (e.g., "Status from 45s ago")
- Warning banner explaining data is not current

This implements graceful degradation - operators see cached data rather than blank screens.

## Auto-Refresh Mechanism

The dashboard auto-refreshes every 10 seconds (configurable via `AUTO_REFRESH_INTERVAL`):

```python
# Streamlit auto-rerun with sleep
time.sleep(config.AUTO_REFRESH_INTERVAL)
st.rerun()
```

**Note:** The `@st.cache_data(ttl=X)` decorator only prevents redundant API calls within the TTL window. Without `st.rerun()`, cached data would become stale but the UI would not update until user interaction.

## Graceful Degradation

The health monitor implements multiple levels of degradation:

1. **Primary:** Fetch all health data in parallel
2. **Fallback:** Show cached data with staleness warning
3. **Minimal:** Show "Service unreachable" status

This ensures operators always have some visibility, even during partial outages.

## Prometheus Metrics

Health check operations emit metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `health_check_total` | Counter | Total health check attempts |
| `health_check_duration_seconds` | Histogram | Health check latency |
| `health_check_failures_total` | Counter | Failed health checks by service |

## SLA Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Dashboard refresh | ≤ 10s | > 15s |
| Service health check | ≤ 1s | > 2s |
| Data staleness | ≤ 30s | > 60s |

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `FEATURE_HEALTH_MONITOR` | `true` | Enable health dashboard |
| `AUTO_REFRESH_INTERVAL` | `10` | Refresh interval (seconds) |
| `HEALTH_CHECK_TIMEOUT` | `5` | Service probe timeout (seconds) |

## Related Documentation

- [Monitoring and Observability](./monitoring-and-observability.md)
- [Operations Runbook](../RUNBOOKS/ops.md)
- [Distributed Tracing](./distributed-tracing.md)

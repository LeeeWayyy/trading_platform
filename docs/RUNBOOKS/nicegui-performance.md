# NiceGUI Performance Runbook

**Last Updated:** 2026-01-04
**Related:** [nicegui-architecture](../CONCEPTS/nicegui-architecture.md)

## Key Metrics to Monitor

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| Page Load Time | <500ms | >1s |
| API Response Time | <100ms | >500ms |
| WebSocket Latency | <50ms | >200ms |
| Active Connections | <1000 | >800 |
| Memory Usage | <1GB | >1.5GB |
| CPU Usage | <50% | >80% |

## Prometheus Metrics

```python
# Custom metrics in apps/web_console_ng/core/metrics.py

from prometheus_client import Counter, Histogram, Gauge

# Request metrics
REQUEST_LATENCY = Histogram(
    'nicegui_request_latency_seconds',
    'Request latency',
    ['path']
)

# Connection metrics
ACTIVE_CONNECTIONS = Gauge(
    'nicegui_active_connections',
    'Active WebSocket connections'
)

# Error metrics
ERROR_COUNT = Counter(
    'nicegui_errors_total',
    'Total errors',
    ['type']
)
```

## Grafana Dashboard Setup

### Essential Panels

1. **Request Rate**: `rate(nicegui_requests_total[5m])`
2. **Error Rate**: `rate(nicegui_errors_total[5m])`
3. **Latency P95**: `histogram_quantile(0.95, nicegui_request_latency_seconds)`
4. **Active Connections**: `nicegui_active_connections`
5. **Memory Usage**: `container_memory_usage_bytes{name="web-console-ng"}`

## Performance Targets (SLOs)

| Metric | SLO | Measurement Window |
|--------|-----|-------------------|
| Availability | 99.9% | Monthly |
| Page Load P95 | <800ms | Daily |
| API P99 | <500ms | Daily |
| Error Rate | <0.1% | Daily |

## Bottleneck Identification

### Database Bottlenecks

```sql
-- Slow queries
SELECT query, mean_time, calls
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 10;

-- Missing indexes
SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE seq_scan > idx_scan
ORDER BY seq_scan DESC;
```

### Redis Bottlenecks

```bash
# Slow commands
redis-cli -h $REDIS_HOST slowlog get 10

# Memory analysis
redis-cli -h $REDIS_HOST memory doctor
```

### Application Bottlenecks

```python
# Profile endpoint
import time

async def profiled_handler() -> None:
    start = time.perf_counter()
    
    db_start = time.perf_counter()
    data = await fetch_data()
    db_time = time.perf_counter() - db_start
    
    render_start = time.perf_counter()
    render_page(data)
    render_time = time.perf_counter() - render_start
    
    total = time.perf_counter() - start
    logger.info(f"total={total:.3f}s db={db_time:.3f}s render={render_time:.3f}s")
```

## Optimization Techniques

### 1. Database Query Optimization

```python
# BAD: N+1 queries
for order in orders:
    trades = await fetch_trades(order.id)

# GOOD: Batch query
order_ids = [o.id for o in orders]
trades = await fetch_trades_batch(order_ids)
```

### 2. Caching

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_static_config() -> dict:
    return load_config()
```

### 3. Lazy Loading

```python
# Only load data when tab is selected
with ui.tab_panel(tab_details):
    async def load_details() -> None:
        data = await fetch_details()
        render_details(data)
    
    ui.button("Load Details", on_click=load_details)
```

### 4. Pagination

```python
# Paginate large datasets
PAGE_SIZE = 50

async def fetch_page(offset: int) -> list:
    return await db.fetch(
        "SELECT * FROM orders LIMIT $1 OFFSET $2",
        PAGE_SIZE, offset
    )
```

## Load Testing Procedures

```bash
# Using locust
locust -f locustfile.py --host=http://localhost:8080

# Using wrk
wrk -t12 -c400 -d30s http://localhost:8080/api/positions
```

### Sample Locustfile

```python
from locust import HttpUser, task, between

class WebConsoleUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def view_dashboard(self):
        self.client.get("/")
    
    @task(1)
    def view_positions(self):
        self.client.get("/positions")
```

## Resource Sizing Guidelines

| Users | CPU | Memory | Instances |
|-------|-----|--------|-----------|
| <100 | 1 | 1GB | 1 |
| 100-500 | 2 | 2GB | 2 |
| 500-1000 | 2 | 4GB | 3 |
| >1000 | 4 | 4GB | 4+ |

## Alert Configuration

```yaml
# Prometheus alerting rules
groups:
  - name: nicegui
    rules:
      - alert: HighLatency
        expr: histogram_quantile(0.95, nicegui_request_latency_seconds) > 0.8
        for: 5m
        labels:
          severity: warning
      
      - alert: HighErrorRate
        expr: rate(nicegui_errors_total[5m]) > 0.01
        for: 5m
        labels:
          severity: critical
      
      - alert: HighConnectionCount
        expr: nicegui_active_connections > 800
        for: 5m
        labels:
          severity: warning
```

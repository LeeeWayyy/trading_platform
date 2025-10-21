# Next Task - Single Source of Truth

**Last Updated:** October 20, 2024
**Current Phase:** P1 (Advanced Features)
**Overall Progress:** 62% (8/13 tasks complete)
**Track 1 Status:** ✅ **100% COMPLETE** (5/5 tasks)
**Track 2 Status:** ✅ **67% COMPLETE** (2/3 tasks) - P1.2T2 pending
**Track 3 Status:** 🔄 **20% COMPLETE** (1/5 tasks) - In Progress

---

## 🎯 CURRENT TASK

### P1.3T1 - Monitoring & Alerting (Prometheus + Grafana)

**Status:** Ready to Start
**Branch:** `feature/p1.3t1-monitoring-alerting` (to be created)
**Priority:** ⭐ High
**Estimated Effort:** 5-7 days

**Alternative Option:** P1.2T2 - Advanced Strategies (7-10 days, medium priority)

---

## What to Build

Add production-grade monitoring and alerting infrastructure using Prometheus and Grafana.

**Current State:**
- No metrics collection
- No real-time dashboards
- No alerting on critical events
- Manual status checking via `make status`

**P1.3T1 Goal:**
Implement comprehensive monitoring stack for operational visibility:

```yaml
# Prometheus metrics exported by all services
# apps/execution_gateway/main.py
from prometheus_client import Counter, Histogram, Gauge

# Business metrics
orders_placed_total = Counter('orders_placed_total', 'Total orders placed', ['symbol', 'side'])
order_placement_duration = Histogram('order_placement_duration_seconds', 'Order placement latency')
positions_total = Gauge('positions_total', 'Current open positions')
unrealized_pnl = Gauge('unrealized_pnl_dollars', 'Unrealized P&L', ['symbol'])

# Service health metrics
circuit_breaker_state = Gauge('circuit_breaker_state', 'Circuit breaker state (0=OPEN, 1=TRIPPED)')
redis_connection_status = Gauge('redis_connection_status', 'Redis connection (1=up, 0=down)')

@app.post("/api/v1/orders")
async def place_order(order: Order):
    with order_placement_duration.time():
        # Place order
        orders_placed_total.labels(symbol=order.symbol, side=order.side).inc()
```

---

## Acceptance Criteria

- [ ] All services (Execution Gateway, Signal Service, Orchestrator, Market Data) export Prometheus metrics
- [ ] Prometheus server scrapes metrics from all services
- [ ] Grafana dashboards show:
  - Real-time P&L (realized, unrealized, total)
  - Order flow (submissions, fills, cancellations)
  - Position sizes by symbol
  - Circuit breaker state
  - Service health (uptime, errors, latency)
  - Redis/Database connection status
- [ ] AlertManager configured with critical alerts:
  - Circuit breaker tripped
  - Daily loss > 5%
  - Service down for > 1 minute
  - Database/Redis connection lost
- [ ] Docker Compose includes Prometheus + Grafana + AlertManager
- [ ] Tests verify metrics are being exported
- [ ] Documentation includes dashboard screenshots and alert runbook

---

## Implementation Steps

### 1. **Add Prometheus Client to Services**

```bash
# Add dependency
poetry add prometheus-client

# Update all services
# apps/execution_gateway/main.py
from prometheus_client import make_asgi_app, Counter, Histogram, Gauge

app = FastAPI()

# Mount prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Define metrics
orders_total = Counter('orders_total', 'Total orders', ['symbol', 'side', 'status'])
positions_gauge = Gauge('positions_current', 'Current positions', ['symbol'])
pnl_gauge = Gauge('pnl_dollars', 'P&L in dollars', ['type'])  # type=realized/unrealized
```

### 2. **Instrument Business Logic**

```python
# Execution Gateway
@app.post("/api/v1/orders")
async def place_order(order: OrderRequest):
    try:
        result = await alpaca_client.place_order(order)
        orders_total.labels(symbol=order.symbol, side=order.side, status='success').inc()
        return result
    except Exception as e:
        orders_total.labels(symbol=order.symbol, side=order.side, status='failed').inc()
        raise

# Update positions gauge
positions = await db.get_open_positions()
for pos in positions:
    positions_gauge.labels(symbol=pos.symbol).set(pos.qty)

# Update P&L gauge
pnl = await calculate_pnl()
pnl_gauge.labels(type='realized').set(float(pnl.realized))
pnl_gauge.labels(type='unrealized').set(float(pnl.unrealized))
```

### 3. **Add Prometheus Configuration**

```yaml
# infra/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'execution-gateway'
    static_configs:
      - targets: ['execution-gateway:8002']

  - job_name: 'signal-service'
    static_configs:
      - targets: ['signal-service:8003']

  - job_name: 'orchestrator'
    static_configs:
      - targets: ['orchestrator:8001']

  - job_name: 'market-data'
    static_configs:
      - targets: ['market-data-service:8004']
```

### 4. **Create Grafana Dashboards**

```json
// infra/grafana/dashboards/trading-overview.json
{
  "dashboard": {
    "title": "Trading Platform Overview",
    "panels": [
      {
        "title": "Total P&L",
        "targets": [
          {
            "expr": "pnl_dollars{type='realized'} + pnl_dollars{type='unrealized'}"
          }
        ]
      },
      {
        "title": "Orders by Symbol",
        "targets": [
          {
            "expr": "sum(rate(orders_total[5m])) by (symbol)"
          }
        ]
      },
      {
        "title": "Circuit Breaker State",
        "targets": [
          {
            "expr": "circuit_breaker_state"
          }
        ]
      }
    ]
  }
}
```

### 5. **Configure AlertManager**

```yaml
# infra/prometheus/alerts.yml
groups:
  - name: trading_alerts
    rules:
      - alert: CircuitBreakerTripped
        expr: circuit_breaker_state == 1
        for: 1m
        annotations:
          summary: "Circuit breaker is TRIPPED"
          description: "Trading has been halted"

      - alert: DailyLossExceeded
        expr: pnl_dollars{type="realized"} < -5000
        for: 5m
        annotations:
          summary: "Daily loss limit exceeded"
          description: "Realized P&L: {{ $value }}"

      - alert: ServiceDown
        expr: up == 0
        for: 1m
        annotations:
          summary: "Service {{ $labels.job }} is down"
```

### 6. **Update Docker Compose**

```yaml
# docker-compose.yml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./infra/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./infra/prometheus/alerts.yml:/etc/prometheus/alerts.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    volumes:
      - ./infra/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./infra/grafana/datasources:/etc/grafana/provisioning/datasources
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin

  alertmanager:
    image: prom/alertmanager:latest
    volumes:
      - ./infra/alertmanager/config.yml:/etc/alertmanager/config.yml
    ports:
      - "9093:9093"
```

### 7. **Add Tests**

```python
# tests/test_metrics.py
def test_metrics_endpoint_exists():
    """Test that /metrics endpoint returns Prometheus metrics."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "orders_total" in response.text

def test_order_counter_increments():
    """Test that orders_total counter increments on order placement."""
    # Place order
    client.post("/api/v1/orders", json=order_data)

    # Check metrics
    response = client.get("/metrics")
    assert "orders_total" in response.text
    assert 'symbol="AAPL"' in response.text
```

### 8. **Create Documentation**

- `docs/IMPLEMENTATION_GUIDES/p1.3t1-monitoring-alerting.md`
- `docs/CONCEPTS/prometheus-metrics.md`
- `docs/ADRs/0012-monitoring-stack.md`
- `docs/RUNBOOKS/prometheus-alerts.md`

---

## Files to Create/Modify

```
infra/
├── prometheus/
│   ├── prometheus.yml           # Scrape configuration
│   └── alerts.yml               # Alert rules
├── grafana/
│   ├── dashboards/
│   │   ├── trading-overview.json
│   │   ├── service-health.json
│   │   └── risk-monitoring.json
│   └── datasources/
│       └── prometheus.yml
└── alertmanager/
    └── config.yml

apps/execution_gateway/
└── main.py                       # Add prometheus_client

apps/signal_service/
└── main.py                       # Add prometheus_client

apps/orchestrator/
└── main.py                       # Add prometheus_client

apps/market_data_service/
└── main.py                       # Add prometheus_client

docker-compose.yml                # Add prometheus, grafana, alertmanager

docs/
├── ADRs/
│   └── 0012-monitoring-stack.md
├── CONCEPTS/
│   └── prometheus-metrics.md
├── IMPLEMENTATION_GUIDES/
│   └── p1.3t1-monitoring-alerting.md
└── RUNBOOKS/
    └── prometheus-alerts.md
```

---

## Success Metrics

**Performance:**
- Metrics scrape interval: 15 seconds
- Dashboard load time: < 2 seconds
- Alert firing latency: < 1 minute from condition

**Coverage:**
- All 4 services export metrics
- 20+ business metrics defined
- 10+ service health metrics
- 5+ critical alerts configured

**Usability:**
- Grafana dashboards accessible at http://localhost:3000
- Real-time P&L visible without refresh
- Alerts integrate with Slack/PagerDuty (optional)

---

## Related Documents

- [P1 Progress](./GETTING_STARTED/P1_PROGRESS.md) - Detailed progress tracker
- [P1 Planning](./TASKS/P1_PLANNING.md) - Complete P1 roadmap
- [Project Status](./GETTING_STARTED/PROJECT_STATUS.md) - Overall project state

---

## Quick Commands

```bash
# Check current progress
cat docs/NEXT_TASK.md

# View detailed P1 status
cat docs/GETTING_STARTED/P1_PROGRESS.md

# Start next task
git checkout -b feature/p1.3t1-monitoring-alerting

# Add prometheus dependency
poetry add prometheus-client

# Start infrastructure
docker-compose up prometheus grafana alertmanager
```

---

**🎯 ACTION REQUIRED:** Create branch and begin P1.3T1 - Monitoring & Alerting

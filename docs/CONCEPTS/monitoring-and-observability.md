# Monitoring and Observability Concepts

This document explains monitoring and observability concepts in the context of our trading platform, designed for developers new to production system monitoring.

## Table of Contents

1. [Why Monitor?](#why-monitor)
2. [Observability vs Monitoring](#observability-vs-monitoring)
3. [Prometheus Fundamentals](#prometheus-fundamentals)
4. [Metric Types](#metric-types)
5. [Grafana Dashboards](#grafana-dashboards)
6. [Alert Rules](#alert-rules)
7. [Best Practices](#best-practices)

---

## Why Monitor?

In a trading platform, monitoring is **critical** for:

### 1. **Detecting Failures Quickly**
Without monitoring, you only learn about problems when:
- Users report issues ("My order didn't execute!")
- Money is lost (worst case)
- Circuit breakers trip and block trading

**With monitoring**: Alerts fire within seconds/minutes of issues, enabling rapid response.

### 2. **Understanding System Behavior**
Questions monitoring answers:
- How many orders are we processing per minute?
- What's our P95 order placement latency?
- Is the signal service model loaded?
- How often is the WebSocket reconnecting?

### 3. **Capacity Planning**
Metrics help predict:
- When will we hit database connection limits?
- Do we need more CPU/memory for signal generation?
- Is our Redis cache sized appropriately?

### 4. **Incident Analysis**
During outages, metrics reveal:
- **When** did the problem start?
- **Which** service was affected first?
- **What** changed? (latency spike, error rate increase)

---

## Observability vs Monitoring

**Monitoring**: Collecting predefined metrics and alerting on known failure modes.
- Example: Alert if service is down for >1 minute

**Observability**: Ability to ask arbitrary questions about system state, especially for **unknown unknowns**.
- Example: Why is this specific order taking 10 seconds? (requires tracing, logs, metrics correlation)

**Our Stack**:
- **P1**: Monitoring with Prometheus + Grafana (metrics + alerts)
- **P2**: Full observability (add distributed tracing + log aggregation)

**Analogy**: Monitoring is like car dashboard lights (oil pressure, engine temperature). Observability is like having full engine diagnostics to debug strange noises.

---

## Prometheus Fundamentals

[Prometheus](https://prometheus.io/) is an open-source **time-series database** optimized for metrics collection.

### How It Works

```
┌─────────────────┐      ┌──────────────────┐      ┌──────────────┐
│ Your Service    │      │   Prometheus     │      │   Grafana    │
│                 │      │                  │      │              │
│ /metrics        │◄─────│  Scrapes every   │◄─────│  Queries for │
│ endpoint        │ HTTP │  15 seconds      │ PromQL│  dashboards  │
│                 │      │                  │      │              │
│ Exposes:        │      │ Stores:          │      │              │
│ orders_total=42 │      │ time-series DB   │      │              │
└─────────────────┘      └──────────────────┘      └──────────────┘
```

### Pull Model (Not Push)

Prometheus **pulls** metrics from your service, not the other way around.

**Why pull?**
- Service doesn't need to know about Prometheus (loose coupling)
- Prometheus controls scrape frequency (no overwhelming with data)
- Easy to add/remove services (just update Prometheus config)

### Time Series Data

Each metric is a **time series** identified by:
- **Metric name**: `execution_gateway_orders_total`
- **Labels**: `{symbol="AAPL", side="buy", status="accepted"}`
- **Timestamp**: `2025-10-20T14:30:00Z`
- **Value**: `42`

Example:
```
execution_gateway_orders_total{symbol="AAPL", side="buy", status="accepted"} 42 @1697814600
execution_gateway_orders_total{symbol="AAPL", side="buy", status="rejected"} 3 @1697814600
execution_gateway_orders_total{symbol="MSFT", side="sell", status="accepted"} 18 @1697814600
```

---

## Metric Types

Prometheus has 4 metric types. Choosing the right type is crucial.

### 1. **Counter** (Always Increasing)

**Use for**: Events that only go up (orders submitted, errors, requests)

**Example**:
```python
from prometheus_client import Counter

orders_total = Counter(
    "execution_gateway_orders_total",
    "Total number of orders submitted",
    ["symbol", "side", "status"],
)

# In your code
orders_total.labels(symbol="AAPL", side="buy", status="accepted").inc()
```

**Querying**: Use `rate()` to get per-second rate:
```promql
rate(execution_gateway_orders_total[5m])  # Orders per second over last 5 minutes
```

**Why not just use the raw value?** If `orders_total=1000`, you don't know if that's 1000 orders today or since server started. `rate()` normalizes it.

**Important**: Counters **never decrease** (except on restart). If you need a value that goes up and down, use a **Gauge**.

### 2. **Gauge** (Can Go Up or Down)

**Use for**: Current state (connections, positions, memory usage, queue depth)

**Example**:
```python
from prometheus_client import Gauge

positions_current = Gauge(
    "execution_gateway_positions_current",
    "Current number of shares held",
    ["symbol"],
)

# Update based on current state
positions_current.labels(symbol="AAPL").set(150)  # Holding 150 shares
positions_current.labels(symbol="MSFT").set(0)    # Closed position
```

**Querying**: Use directly:
```promql
execution_gateway_positions_current  # Current positions
```

**When to use Gauge vs Counter?**
- Counter: "How many orders have we submitted?" (cumulative)
- Gauge: "How many positions do we currently hold?" (snapshot)

### 3. **Histogram** (Distribution of Values)

**Use for**: Latency, request sizes, durations (when you care about percentiles)

**Example**:
```python
from prometheus_client import Histogram

order_placement_duration = Histogram(
    "execution_gateway_order_placement_duration_seconds",
    "Time taken to place order with Alpaca",
    ["symbol", "side"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],  # Custom buckets
)

# Measure duration
import time
start = time.time()
await alpaca_client.submit_order(...)
duration = time.time() - start

order_placement_duration.labels(symbol="AAPL", side="buy").observe(duration)
```

**What it tracks**:
- Count of observations in each bucket (how many requests took <0.1s, <0.25s, etc.)
- Sum of all observed values
- Count of observations

**Querying P95 latency**:
```promql
histogram_quantile(0.95,
  rate(execution_gateway_order_placement_duration_seconds_bucket[5m])
)
```

This means: "95% of orders are placed within X seconds"

**Why percentiles matter?** Average can be misleading:
- Average latency: 0.5s (looks good!)
- P95 latency: 10s (whoops, 5% of orders are slow!)

### 4. **Summary** (Similar to Histogram, Pre-Computed)

**Use for**: When you need percentiles but can't afford histogram overhead

**We don't use Summaries** in this platform (Histograms are more flexible). Just know they exist.

---

## Grafana Dashboards

[Grafana](https://grafana.com/) visualizes Prometheus metrics with interactive dashboards.

### Dashboard Structure

A dashboard contains **panels**, each showing a specific metric:

**Example Panel: "Order Rate by Status"**
```json
{
  "title": "Order Rate by Status",
  "type": "timeseries",
  "targets": [
    {
      "expr": "sum(rate(execution_gateway_orders_total[1m])) by (status)",
      "legendFormat": "{{status}}"
    }
  ]
}
```

This creates a line graph showing orders/second, with separate lines for "accepted", "rejected", "failed".

### Panel Types

1. **Stat Panel**: Big number (e.g., "Total Orders: 1,234")
2. **Time Series**: Line graph over time (e.g., order rate)
3. **Gauge**: Progress bar (e.g., "Position Utilization: 75%")
4. **Table**: Tabular data (e.g., top 10 symbols by volume)

### Dashboard Organization

**Our 3 Dashboards**:

1. **Trading Overview** (`trading-overview.json`):
   - Focus: **Business metrics** (orders, positions, signals)
   - Audience: Traders, product managers
   - Refresh: 10 seconds

2. **Service Health** (`service-health.json`):
   - Focus: **Operational health** (uptime, connections)
   - Audience: DevOps, on-call engineers
   - Refresh: 10 seconds

3. **Performance** (`performance.json`):
   - Focus: **Latency and throughput** (P50/P95/P99)
   - Audience: Developers, performance engineers
   - Refresh: 10 seconds

### Reading a Time Series Graph

Example: "Order Placement Latency (P95)"

```
Latency (seconds)
    3s │                              ╭─╮
       │                          ╭───╯ ╰─╮
    2s │                      ╭───╯       ╰──╮
       │                  ╭───╯              ╰─
    1s │              ╭───╯
       │          ╭───╯
    0s │──────────╯
       └────────────────────────────────────────► Time
         10:00   10:15   10:30   10:45   11:00
```

**Interpretation**:
- At 10:00: P95 latency = 0.5s (good!)
- At 10:30: Spike to 3s (investigate!)
- At 11:00: Back to 1.5s (still elevated)

**Actions**:
1. Check Prometheus alerts (did anything fire?)
2. Correlate with other metrics (CPU, error rate, order volume)
3. Check logs for errors around 10:30

---

## Alert Rules

Alerts notify you when something is wrong, **before** users complain.

### Alert Anatomy

```yaml
- alert: HighOrderRejectionRate
  expr: |
    (
      sum(rate(execution_gateway_orders_total{status="rejected"}[5m]))
      /
      sum(rate(execution_gateway_orders_total[5m]))
    ) > 0.1
  for: 2m
  labels:
    severity: high
    component: trading
  annotations:
    summary: "High order rejection rate"
    description: "More than 10% of orders are being rejected in the last 5 minutes."
```

**Breakdown**:
- **expr**: PromQL expression (fires when >10% of orders rejected)
- **for**: Wait 2 minutes before firing (avoids transient spikes)
- **severity**: `critical` > `high` > `medium` > `low`
- **annotations**: Human-readable message

### Alert States

1. **Inactive**: Condition is false (green)
2. **Pending**: Condition is true, waiting for `for` duration (yellow)
3. **Firing**: Condition true for >2 minutes (red)

### Alert Severity Guidelines

**Critical** (Page on-call engineer immediately):
- Trading halted (circuit breaker tripped)
- Service down (can't process orders)
- Database disconnected (data loss risk)

**High** (Notify team channel, respond within 30 min):
- High error rates (>5% API failures)
- Dependency down (Signal Service, Market Data)
- Performance degradation (P95 latency >2x normal)

**Medium** (Create ticket, review next business day):
- Elevated error rates (1-5%)
- Slow but functional (P95 latency >1.5x normal)
- Non-critical connection issues (Redis cache down)

**Low** (Log only, review weekly):
- Minor performance anomalies
- Informational alerts (model reloaded)

### Avoiding Alert Fatigue

**Problem**: Too many alerts → people ignore them

**Solutions**:
1. **Tune thresholds**: Use production data to set realistic limits
2. **Adjust `for` duration**: Avoid firing on transient spikes
3. **Prioritize by severity**: Only page for critical issues
4. **Create runbooks**: Link alerts to actionable steps

**Example**:
- Bad: Alert on any error (fires 100x/day)
- Good: Alert when error rate >5% for >5 minutes (fires 1x/month)

---

## Best Practices

### 1. **Metric Naming**

**Pattern**: `<service>_<metric>_<unit>`

✅ Good:
```
execution_gateway_order_placement_duration_seconds
signal_service_model_predictions_total
market_data_websocket_connection_status
```

❌ Bad:
```
orders          # Which service? What unit?
duration        # Of what?
status          # 0/1? Connected/disconnected?
```

**Conventions**:
- Counters: End with `_total`
- Histograms: End with `_seconds`, `_milliseconds`, `_bytes`
- Gauges: End with `_current`, `_status`, `_count`

### 2. **Label Cardinality**

**Problem**: Each unique label combination creates a new time series.

**Example**:
```python
# ❌ BAD: client_order_id has millions of unique values
orders_total = Counter("orders_total", "Orders", ["client_order_id"])

# ✅ GOOD: symbol has ~10 unique values
orders_total = Counter("orders_total", "Orders", ["symbol", "side", "status"])
```

**Why?** Prometheus stores each time series in memory. 1 million time series = out of memory crash.

**Rule of Thumb**: Labels should have <100 unique values. Never use:
- User IDs
- Order IDs
- Timestamps
- High-cardinality strings

### 3. **Instrumentation Pattern**

**Consistent pattern across all services**:

```python
async def endpoint_handler(request):
    request_started = time.time()
    request_status = "success"

    try:
        # Business logic
        result = await process_request(request)

        # Track success metrics
        success_counter.labels(type=request.type).inc()

        return result

    except HTTPException:
        request_status = "error"
        raise
    except Exception:
        request_status = "error"
        raise
    finally:
        # ALWAYS record metrics (even on errors)
        elapsed = time.time() - request_started
        requests_total.labels(status=request_status).inc()
        request_duration.observe(elapsed)
```

**Why `finally`?** Ensures metrics are recorded even if exception is raised.

### 4. **Histogram Buckets**

**Default buckets**: `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]`

**Customize for your use case**:

```python
# Fast operations (order placement)
buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

# Slow operations (signal generation)
buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]

# Very slow operations (orchestration)
buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
```

**Why?** Percentiles are computed from buckets. If your P95 is 2.5s but your highest bucket is 1.0s, you get inaccurate results.

### 5. **Testing Metrics**

**Every metric should have a test**:

```python
def test_orders_total_metric_exists(client):
    """Test that orders_total metric exists."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "execution_gateway_orders_total" in response.text
    assert "# HELP execution_gateway_orders_total" in response.text
    assert "# TYPE execution_gateway_orders_total counter" in response.text
```

**Why?** Metrics are part of your API contract. Breaking them breaks dashboards and alerts.

---

## Common Debugging Scenarios

### Scenario 1: "Orders are slow!"

**Steps**:
1. Check **Performance Dashboard** → Order Placement Latency panel
   - Is P95 elevated? When did it spike?

2. Query Prometheus:
   ```promql
   histogram_quantile(0.95,
     rate(execution_gateway_order_placement_duration_seconds_bucket[5m])
   )
   ```

3. Correlate with other metrics:
   - Alpaca API error rate (is Alpaca down?)
   - Database connection status (is DB slow?)
   - Order volume (are we overloaded?)

4. Check alerts:
   - Did "SlowOrderPlacement" alert fire?
   - Review alert annotations for guidance

### Scenario 2: "No signals generated!"

**Steps**:
1. Check **Service Health Dashboard** → Signal Service panel
   - Is service up? (green = UP, red = DOWN)

2. Check **Service Health Dashboard** → ML Model status
   - Is model loaded? (should be 1.0, not 0.0)

3. Query Prometheus:
   ```promql
   rate(signal_service_signals_generated_total[5m])
   ```
   - If 0, signals aren't being generated

4. Check related metrics:
   - Model predictions: `rate(signal_service_model_predictions_total[5m])`
   - Request failures: `rate(signal_service_requests_total{status="error"}[5m])`

### Scenario 3: "High WebSocket reconnections!"

**Steps**:
1. Check **Service Health Dashboard** → WebSocket Connection Status
   - Is it bouncing between 0 and 1?

2. Query Prometheus:
   ```promql
   rate(market_data_reconnect_attempts_total[5m])
   ```

3. Check alert:
   - Did "HighWebSocketReconnectionRate" fire?

4. Investigate:
   - Network issues?
   - Alpaca API problems?
   - Check Market Data Service logs

---

## Learning Resources

1. **Prometheus Documentation**: https://prometheus.io/docs/introduction/overview/
2. **PromQL Tutorial**: https://prometheus.io/docs/prometheus/latest/querying/basics/
3. **Grafana Tutorials**: https://grafana.com/tutorials/
4. **Our Implementation**:
   - Metrics code: `apps/*/main.py` (search for `prometheus_client`)
   - Prometheus config: `infra/prometheus/prometheus.yml`
   - Alert rules: `infra/prometheus/alerts.yml`
   - Dashboards: `infra/grafana/*.json`

---

## Summary

**Key Takeaways**:

1. **Monitoring is essential** for trading platforms (detect issues before users do)
2. **Prometheus** is a time-series database that scrapes metrics from services
3. **4 metric types**: Counter (events), Gauge (state), Histogram (latency), Summary (rare)
4. **Grafana** visualizes metrics with dashboards
5. **Alerts** notify you when thresholds are breached
6. **Best practices**: Consistent naming, low label cardinality, always use `finally` for metrics

**Next Steps**:
- Explore Grafana dashboards at `http://localhost:3000` (after `make up`)
- Review alert rules in `infra/prometheus/alerts.yml`
- Read ADR-0012 for architectural decisions
- Practice writing PromQL queries in Grafana's Explore tab

# ADR 0012: Prometheus and Grafana Monitoring Infrastructure

- Status: Accepted
- Date: 2025-10-20

## Context

The trading platform requires comprehensive observability to ensure system reliability, detect issues early, and provide operational visibility into:

1. **Trading Operations**: Order flow, execution latency, rejection rates, position tracking
2. **Service Health**: Uptime, database/Redis/WebSocket connections, ML model status
3. **Performance**: Request latency (P50/P95/P99), throughput, resource utilization
4. **Data Quality**: Market data freshness, WebSocket stability, API error rates

Without monitoring:
- We cannot detect circuit breaker trips, service outages, or degraded performance until user reports
- No visibility into whether orders are being rejected at elevated rates
- Cannot track signal generation latency or orchestration bottlenecks
- No historical metrics for capacity planning or incident analysis

**Why now?** P1.3T1 (Monitoring & Alerting) is a hardening requirement before paper trading deployment. We need to instrument all 4 microservices and establish alerting infrastructure.

## Decision

We adopt **Prometheus + Grafana** as the monitoring stack with the following architecture:

### 1. Metrics Instrumentation (All Services)

Each microservice exposes `/metrics` endpoint using `prometheus_client`:

**Execution Gateway (9 metrics)**:
- Business: `orders_total`, `order_placement_duration_seconds`, `positions_current`, `alpaca_api_requests_total`
- Health: `database_connection_status`, `alpaca_connection_status`, `circuit_breaker_status`

**Signal Service (9 metrics)**:
- Business: `requests_total`, `signal_generation_duration_seconds`, `signals_generated_total`, `model_predictions_total`, `model_reload_total`
- Health: `database_connection_status`, `redis_connection_status`, `model_loaded_status`, `model_version`

**Orchestrator (8 metrics)**:
- Business: `runs_total`, `orchestration_duration_seconds`, `signals_received_total`, `orders_submitted_total`, `positions_adjusted_total`
- Health: `database_connection_status`, `signal_service_available`, `execution_gateway_available`

**Market Data Service (8 metrics)**:
- Business: `subscription_requests_total`, `subscription_duration_seconds`, `subscribed_symbols_current`, `websocket_messages_received_total`, `position_syncs_total`
- Health: `websocket_connection_status`, `redis_connection_status`, `reconnect_attempts_total`

### 2. Metrics Collection (Prometheus)

**Configuration** (`infra/prometheus/prometheus.yml`):
- Scrape interval: 10s for critical services (Execution Gateway, Market Data), 15s for others
- Retention: 30 days (configurable)
- Service discovery: Static configs (local development), extensible to Kubernetes service discovery
- Labels: `service`, `tier` (critical/important), `component` (trading/ml/data/coordination)

**Alert Rules** (`infra/prometheus/alerts.yml`):
- **Service Health** (12 alerts): Service down, database/Redis/WebSocket disconnections, model not loaded
- **Trading Operations** (5 alerts): High order rejection rate, no orders submitted, circuit breaker tripped, orchestration failures, no signals
- **Data Quality** (3 alerts): No market data updates, high WebSocket reconnection rate, Alpaca API errors
- **Performance** (4 alerts): Slow order placement (>2s), slow signal generation (>10s), slow orchestration (>60s), model reload failures

Severity levels: `critical`, `high`, `medium`, `low`

### 3. Visualization (Grafana)

**Dashboard Strategy**:
- **Trading Overview**: Real-time trading activity (orders, positions, signals, orchestration)
- **Service Health**: Service uptime, connection status (DB/Redis/WebSocket/model)
- **Performance**: Latency percentiles (P50/P95/P99) for all operations, request rates

**Dashboard Format**: JSON definitions in `infra/grafana/` for version control and automated provisioning

### 4. Instrumentation Pattern (Consistent Across Services)

```python
# Module-level imports
import time
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

# Define metrics after app initialization
orders_total = Counter(
    "execution_gateway_orders_total",
    "Total number of orders submitted",
    ["symbol", "side", "status"],
)

order_placement_duration = Histogram(
    "execution_gateway_order_placement_duration_seconds",
    "Time taken to place order with Alpaca",
    ["symbol", "side"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# Mount metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Instrument endpoints with try/except/finally
async def place_order(order: OrderRequest):
    request_started = time.time()
    request_status = "success"

    try:
        # Business logic
        result = await alpaca_client.submit_order(...)

        # Track metrics on success
        orders_total.labels(
            symbol=order.symbol,
            side=order.side,
            status="accepted"
        ).inc()

        return result
    except HTTPException:
        request_status = "rejected"
        raise
    except Exception:
        request_status = "failed"
        raise
    finally:
        # Always record metrics
        elapsed = time.time() - request_started
        order_placement_duration.labels(
            symbol=order.symbol,
            side=order.side
        ).observe(elapsed)
```

### 5. Naming Conventions

- **Metric names**: `<service>_<metric>_<unit>` (e.g., `execution_gateway_order_placement_duration_seconds`)
- **Counters**: Must end with `_total` suffix (Prometheus best practice)
- **Histograms**: Must end with time unit (e.g., `_seconds`, `_milliseconds`)
- **Labels**: Lowercase with underscores, avoid high cardinality (no `client_order_id` labels)

### 6. Scope Boundaries

**In Scope**:
- Application-level metrics (business logic, health checks)
- Prometheus + Grafana for local development and paper trading
- Alert rule definitions (no Alertmanager yet)
- Comprehensive test coverage for metrics endpoints

**Out of Scope** (deferred to P2):
- Alertmanager integration (alert routing, notifications)
- Long-term storage (Thanos, Cortex, VictoriaMetrics)
- Distributed tracing (OpenTelemetry, Jaeger)
- Log aggregation (ELK, Loki)
- Infrastructure metrics (node exporter, cadvisor)
- Kubernetes service discovery

## Consequences

### Benefits

1. **Operational Visibility**:
   - Real-time dashboards show trading activity, service health, and performance
   - Historical metrics enable trend analysis and capacity planning
   - Clear indication of circuit breaker status and dependency health

2. **Early Problem Detection**:
   - Alerts fire within 1-5 minutes of issues (service down, high error rates, slow latency)
   - Proactive monitoring prevents silent failures
   - Quantifiable SLAs (e.g., P95 order placement <2s)

3. **Debugging Aid**:
   - Metrics help narrow down issues during incidents (which service, when, correlation with load)
   - Performance baselines establish "normal" vs "degraded" behavior
   - Latency percentiles identify tail latency problems

4. **Production Readiness**:
   - Essential foundation for paper trading and live rollout
   - Industry-standard tooling (Prometheus widely adopted)
   - Grafana provides intuitive visualization for non-technical stakeholders

### Tradeoffs

1. **Complexity**:
   - Additional infrastructure to manage (Prometheus, Grafana containers)
   - Metric instrumentation adds code to every endpoint (mitigated by consistent pattern)
   - Learning curve for PromQL query language

2. **Performance Overhead**:
   - Metrics collection adds ~1-5ms per request (negligible for trading timescales)
   - Prometheus scrapes consume network bandwidth (every 10-15s)
   - Storage growth: ~1GB/month for 4 services with current metric cardinality

3. **Maintenance Burden**:
   - Alert rules require tuning to avoid false positives/negatives
   - Dashboards need updates when adding new metrics
   - Metric retention management (disk space)

### Risks

1. **Alert Fatigue**: Too many low-severity alerts desensitize operators
   - **Mitigation**: Start with critical/high alerts only, tune thresholds based on production data

2. **Label Cardinality Explosion**: Adding high-cardinality labels (e.g., `client_order_id`) can crash Prometheus
   - **Mitigation**: Code review enforces label guidelines, test coverage validates metric structure

3. **Single Point of Failure**: Prometheus outage blinds us during incidents
   - **Mitigation**: Prometheus itself is monitored via self-scraping, alerts can still fire from previous data

### Follow-Up Tasks

1. **P1.3 (Immediate)**:
   - ✅ Instrument all 4 services with Prometheus metrics
   - ✅ Create Prometheus configuration with alert rules
   - ✅ Create Grafana dashboards (Trading, Health, Performance)
   - ✅ Comprehensive test coverage for `/metrics` endpoints

2. **P1.4 (Before Paper Trading)**:
   - Integrate Alertmanager for alert routing (Slack, PagerDuty, email)
   - Set up alert runbooks in `/docs/RUNBOOKS/`
   - Load testing to validate metric overhead and alert thresholds
   - Prometheus data backup strategy

3. **P2 (Advanced Observability)**:
   - Distributed tracing with OpenTelemetry
   - Log aggregation with Loki (structured JSON logs)
   - Long-term metric storage (1+ year retention)
   - Infrastructure monitoring (CPU, memory, disk)

### Migration Plan

**Phase 1: Local Development (Current)**
- Prometheus + Grafana run via `make up` (docker-compose)
- Developers access Grafana at `http://localhost:3000`
- Manual dashboard import from `infra/grafana/*.json`

**Phase 2: Paper Trading Deployment**
- Prometheus persistent volume for 30-day retention
- Grafana provisioning for automatic dashboard loading
- Alertmanager integration for Slack notifications

**Phase 3: Live Trading**
- Prometheus high availability (2+ replicas)
- Remote write to long-term storage (Thanos/Cortex)
- SLO/SLA dashboards for regulatory compliance

## References

- **Prometheus Best Practices**: https://prometheus.io/docs/practices/naming/
- **Grafana Dashboard Design**: https://grafana.com/docs/grafana/latest/best-practices/
- **Task Ticket**: `/docs/TASKS/P1_TICKETS.md` (P1.3T1: Monitoring & Alerting)
- **Related ADRs**:
  - ADR-0004: Signal Service Architecture (model hot reload metrics)
  - ADR-0006: Execution Gateway Architecture (order metrics)
  - ADR-0011: Risk Management System (circuit breaker metrics)

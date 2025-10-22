# Centralized Logging

This document explains why centralized logging is critical for production trading systems and how the Loki/Promtail/Grafana stack enables unified observability.

---

## What is Centralized Logging?

**Centralized logging** aggregates logs from all microservices into a single queryable system, enabling:
- **Distributed tracing** - Follow a request across multiple services
- **Production debugging** - Diagnose failures when they occur
- **Compliance & audit** - Maintain comprehensive records for regulatory requirements
- **Performance analysis** - Identify bottlenecks and optimize execution paths

### Without Centralized Logging

In a microservices architecture, logs are scattered:

```
Signal Service logs → Docker container stdout
Execution Gateway logs → Docker container stdout
Orchestrator logs → Docker container stdout
Market Data Service logs → Docker container stdout
```

**Problems:**
- SSH into each container to view logs
- Manual correlation across services (matching timestamps)
- No way to trace a request end-to-end
- Logs disappear when containers restart
- No long-term retention or search

### With Centralized Logging

All logs flow to a single system:

```
All Services → Promtail (collector) → Loki (storage) → Grafana (UI)
```

**Benefits:**
- Query all logs from one interface
- Trace requests via trace ID across services
- Persistent storage with 30-day retention
- Fast queries with label-based indexing
- Automated alerting on errors

---

## Why Loki (Not ELK Stack)?

The trading platform uses **Grafana Loki** instead of the traditional ELK (Elasticsearch/Logstash/Kibana) stack.

### Technology Comparison

| Feature | Loki | ELK Stack |
|---------|------|-----------|
| **Index Strategy** | Labels only (low cardinality) | Full-text index (high cardinality) |
| **Storage Cost** | Low (compressed chunks) | High (inverted index) |
| **Query Speed** | Fast for label filters | Fast for full-text search |
| **Resource Usage** | Minimal (RAM/CPU) | Heavy (JVM, heap tuning) |
| **Setup Complexity** | Simple (single binary) | Complex (3+ services) |
| **Integration** | Native Grafana | Requires Kibana |

### Why Loki Wins for Trading

**1. Label-Based Indexing**
```logql
# Fast: Query by service and level (labels)
{service="execution_gateway"} | json | level="ERROR"

# Slow in Loki, but not needed for trading logs
{job="docker"} | search "account_number=12345"
```

Trading logs have **low-cardinality labels** (service name, log level, environment) but **high-cardinality fields** (trace IDs, order IDs, account numbers). Loki's approach:
- Index labels (low cardinality) → fast queries
- Store fields as JSON (high cardinality) → no index explosion
- Query fields via `| json | field="value"` when needed

**2. Cost Efficiency**
```
ELK Stack for 30-day retention:
- Elasticsearch: 50GB disk per index
- RAM: 8GB+ for JVM heap
- CPU: Constant reindexing overhead

Loki for 30-day retention:
- Storage: ~10GB compressed chunks
- RAM: 512MB typical usage
- CPU: Minimal (no indexing)
```

For development and paper trading, Loki's footprint is **10x smaller**.

**3. Integration with Existing Stack**

The platform already uses **Grafana** for metrics (Prometheus). Adding Loki means:
- Same UI for logs and metrics
- Correlate logs with metric spikes
- Single authentication/authorization system
- No need to learn Kibana

---

## Architecture Components

### 1. Promtail (Log Collector)

**Role:** Scrapes logs from Docker containers and ships to Loki

```yaml
# infra/promtail/promtail-config.yml
scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock  # Auto-discover containers
    pipeline_stages:
      - json:  # Parse JSON logs
          expressions:
            level: level
            service_name: service  # Extract "service" field as "service_name"
            trace_id: trace_id
      - labels:  # Promote to searchable labels
          level:
          service_name:  # Query as {service_name="execution_gateway"}
```

**Key features:**
- **Service Discovery:** Automatically finds new containers
- **JSON Parsing:** Extracts structured fields from logs
- **Label Promotion:** Converts select fields to queryable labels
- **Position Tracking:** Resumes from last read position on restart

### 2. Loki (Log Storage)

**Role:** Stores logs as compressed chunks with label-based index

```yaml
# infra/loki/loki-config.yml
schema_config:
  configs:
    - from: 2024-01-01
      store: boltdb-shipper  # Embedded DB for index
      object_store: filesystem  # Local storage for chunks

limits_config:
  retention_period: 720h  # 30 days
  ingestion_rate_mb: 16   # Rate limiting

compactor:
  retention_enabled: true
  retention_delete_delay: 2h  # Grace period before deletion
```

**Key features:**
- **30-Day Retention:** Auto-delete old logs (configurable to 7 years for compliance)
- **Compression:** Gzip chunks reduce storage by ~10x
- **No Index Bloat:** Only labels indexed, not full content
- **Horizontal Scalability:** Can scale to multiple nodes (future)

### 3. Grafana (Query Interface)

**Role:** Unified UI for logs and metrics

**Access Grafana:** [http://localhost:3000](http://localhost:3000) (login: admin/admin)

```logql
# LogQL query examples

# All errors from execution gateway
{service_name="execution_gateway"} | json | level="ERROR"

# Trace a specific request
{job="docker"} | json | trace_id="550e8400-e29b-41d4-a716-446655440000"

# Error rate over time
sum by (service_name) (rate({job="docker"} | json | level="ERROR" [1m]))
```

**Note on labels:**
- `{job="docker"}` - Selects all logs collected from Docker containers (broad filter)
- `{service_name="execution_gateway"}` - Selects logs from a specific service (narrow filter)
- **Best practice:** Always start with the most specific label (`service_name`) for better performance

**Key features:**
- **LogQL Language:** Similar to PromQL (familiar to users)
- **Derived Fields:** Click trace ID → see all related logs
- **Alerting:** Fire alerts on error rate spikes
- **Dashboards:** Pre-built panels for log volume, errors, traces

---

## Data Flow

### Log Lifecycle

```
1. Service Logs
   ↓
libs/common/logging/formatter.py → JSON output
{"timestamp": "2025-10-21T12:00:00Z", "level": "INFO", "service": "execution_gateway", ...}

2. Docker Captures
   ↓
Container stdout → Docker logging driver → /var/lib/docker/containers/...

3. Promtail Scrapes
   ↓
Promtail → Reads container logs → Parses JSON → Extracts labels

4. Loki Stores
   ↓
Loki → Compresses chunks → Writes to filesystem → Indexes labels (BoltDB)

5. Grafana Queries
   ↓
User → Grafana Explore → LogQL query → Loki API → Returns matching logs
```

### Trace ID Propagation

**How trace IDs flow through the system:**

```
1. Request arrives at Execution Gateway
   Headers: { "X-Trace-ID": "abc-123" }
   ↓
2. Middleware extracts trace ID
   libs/common/logging/middleware.py → set_trace_id("abc-123")
   ↓
3. All logs in this request include trace_id
   logger.info("Order submitted") → {"trace_id": "abc-123", "message": "Order submitted", ...}
   ↓
4. Outgoing HTTP requests include header
   libs/common/logging/http_client.py → adds X-Trace-ID: abc-123
   ↓
5. Downstream services inherit the same trace ID
   Signal Service logs → {"trace_id": "abc-123", ...}
   Risk Manager logs → {"trace_id": "abc-123", ...}
```

**Query in Grafana:**
```logql
{job="docker"} | json | trace_id="abc-123"
```

**Result:** All logs from execution gateway, signal service, and risk manager for this request.

---

## Label Cardinality

**Critical concept:** Loki's performance depends on **low-cardinality labels**.

### Good Labels (Low Cardinality)

```
service_name: execution_gateway, signal_service, orchestrator, ...  (5-10 values)
level: DEBUG, INFO, WARNING, ERROR, CRITICAL  (5 values)
environment: dev, staging, production  (3 values)
```

**Total label combinations:** ~150 streams

### Bad Labels (High Cardinality)

```
❌ trace_id: 550e8400-e29b-41d4-a716-446655440000, ...  (MILLIONS of unique values)
❌ order_id: ORDER-123, ORDER-456, ...  (THOUSANDS per day)
❌ account_id: ACC-001, ACC-002, ...  (HUNDREDS)
```

**Total label combinations:** MILLIONS of streams → Loki crashes

### Solution: JSON Fields

High-cardinality data goes in JSON fields (not labels):

```json
{
  "timestamp": "2025-10-21T12:00:00Z",
  "level": "INFO",  ← Label (low cardinality)
  "service": "execution_gateway",  ← Extracted as service_name label (low cardinality)
  "trace_id": "550e8400-...",  ← JSON field (high cardinality)
  "order_id": "ORDER-123",  ← JSON field (high cardinality)
  "context": {
    "symbol": "AAPL",
    "quantity": 10
  }
}
```

**Query high-cardinality fields:**
```logql
{service_name="execution_gateway"} | json | trace_id="550e8400-..."
```

Slower than label queries, but avoids cardinality explosion.

---

## Query Performance

### Fast Queries (Label Filters)

```logql
# Label filters are indexed
{service_name="execution_gateway"} | json | level="ERROR"  # < 100ms

# Multiple label filters
{service_name="execution_gateway", level="ERROR"}  # < 100ms
```

### Slow Queries (JSON Field Filters)

```logql
# JSON field filters require scanning chunks
{job="docker"} | json | trace_id="abc-123"  # ~500ms (depends on time range)

# Text search is slowest
{job="docker"} | search "order failed"  # ~1-2s (brute force scan)
```

### Optimization Tips

1. **Always start with label filters:**
   ```logql
   # Good
   {service_name="execution_gateway"} | json | trace_id="abc-123"

   # Bad
   {job="docker"} | json | trace_id="abc-123"  # Scans all services
   ```

2. **Limit time range:**
   ```logql
   # Good (use Grafana time picker to limit to last 1 hour)
   {service_name="execution_gateway"} | json

   # Or use count_over_time for metrics over time range
   count_over_time({service_name="execution_gateway"} | json [1h])
   ```

3. **Use aggregations for metrics:**
   ```logql
   # Error rate over time
   sum by (service_name) (rate({job="docker"} | json | level="ERROR" [1m]))
   ```

---

## Production Considerations

### Retention

**Development/Paper Trading:** 30 days (10GB storage)
```yaml
retention_period: 720h  # 30 days
```

**Production/Compliance:** 7 years (configurable)
```yaml
retention_period: 61320h  # 7 years
```

### Scaling

**Single Node (Current):**
- Loki: Single process
- Storage: Local filesystem
- Limits: ~100GB logs, ~10 services

**Multi-Node (Future):**
- Loki: Distributed mode (read/write separation)
- Storage: S3 or GCS
- Limits: Petabyte scale

### Alerting

**Loki Ruler (future enhancement):**

```yaml
# Alert on high error rate
groups:
  - name: trading_alerts
    rules:
      - alert: HighErrorRate
        expr: |
          sum by (service) (rate({job="docker"} | json | level="ERROR" [5m])) > 10
        annotations:
          summary: "High error rate in {{ $labels.service }}"
```

---

## Comparison with Alternatives

### CloudWatch Logs

**Pros:**
- Managed service (no infrastructure)
- Integrates with AWS services

**Cons:**
- **Vendor lock-in:** AWS only
- **Cost:** $0.50/GB ingestion + $0.03/GB storage (expensive at scale)
- **Query limits:** 10,000 log events per query
- **No correlation:** Metrics in CloudWatch, logs in CloudWatch Logs (separate UIs)

**Verdict:** Rejected for development; may reconsider for production AWS deployment

### Datadog

**Pros:**
- Best-in-class UI
- Integrated metrics, logs, traces, profiling

**Cons:**
- **Cost:** $1.70/GB logs (extremely expensive)
- **Vendor lock-in:** Proprietary platform
- **Overkill:** Too many features for paper trading

**Verdict:** Rejected for cost; may reconsider for live trading

### Self-Hosted ELK

**Pros:**
- Full-text search (fastest for text queries)
- Mature ecosystem (Kibana, Beats)

**Cons:**
- **Resource intensive:** 8GB+ RAM, complex tuning
- **Maintenance:** Index management, shard rebalancing
- **Cost:** High infrastructure costs

**Verdict:** Rejected for development; Loki is simpler and cheaper

---

## Summary

**Why Centralized Logging?**
- Unified observability across microservices
- Distributed tracing via trace IDs
- Production debugging and compliance

**Why Loki?**
- Low resource usage (512MB RAM vs 8GB for ELK)
- Simple setup (single binary)
- Native Grafana integration
- Label-based indexing (perfect for trading logs)

**Key Patterns:**
- Low-cardinality labels (service, level)
- High-cardinality JSON fields (trace_id, order_id)
- Trace ID propagation via X-Trace-ID header
- 30-day retention (configurable to 7 years)

**Next Steps:**
- [Distributed Tracing](./distributed-tracing.md) - How trace IDs work
- [Structured Logging](./structured-logging.md) - JSON log format
- [LOGGING_GUIDE.md](../GETTING_STARTED/LOGGING_GUIDE.md) - Developer usage guide
- [logging-queries.md](../RUNBOOKS/logging-queries.md) - LogQL examples

# Logging Queries Runbook

This runbook provides common LogQL queries for debugging production issues using the centralized logging system.

## Quick Start

**Access Grafana:**
- URL: http://localhost:3000 (development)
- Username: `admin`
- Password: `admin`
- Navigate to: Explore → Select "Loki" datasource

**Basic Query Syntax:**
```logql
{label_selector} | json | filter_expression
```

---

## Common Queries

### 1. View All Logs from a Service

**Signal Service:**
```logql
{service="signal_service"} | json
```

**Execution Gateway:**
```logql
{service="execution_gateway"} | json
```

**All Services:**
```logql
{job="docker"} | json
```

---

### 2. Filter by Log Level

**All Errors:**
```logql
{job="docker"} | json | level="ERROR"
```

**Errors and Warnings:**
```logql
{job="docker"} | json | level=~"ERROR|WARNING"
```

**Critical Issues Only:**
```logql
{job="docker"} | json | level="CRITICAL"
```

**Exclude Debug Logs:**
```logql
{job="docker"} | json | level!="DEBUG"
```

---

### 3. Trace a Specific Request

**Find all logs for a trace ID:**
```logql
{job="docker"} | json | trace_id="550e8400-e29b-41d4-a716-446655440000"
```

This shows the entire request flow across all services that handled this request.

**Find trace IDs related to errors:**
```logql
{job="docker"} | json | level="ERROR" | trace_id != ""
```

---

### 4. Search Log Messages

**Find logs containing specific text:**
```logql
{job="docker"} | json | message =~ "order.*failed"
```

**Case-insensitive search:**
```logql
{job="docker"} | json | message =~ "(?i)circuit.*breaker"
```

**Multiple keywords (AND):**
```logql
{job="docker"} | json | message =~ "AAPL" | message =~ "buy"
```

**Exclude certain messages:**
```logql
{job="docker"} | json | message !~ "healthcheck"
```

---

### 5. Filter by Context Fields

**Find logs for a specific symbol:**
```logql
{job="docker"} | json | context_symbol="AAPL"
```

**Find logs for a strategy:**
```logql
{job="docker"} | json | context_strategy_id="alpha_baseline"
```

**Note:** Context field names are prefixed with `context_` in Loki labels.

---

### 6. Time-Based Queries

**Last 5 minutes:**
```logql
{job="docker"} | json [5m]
```

**Last hour:**
```logql
{job="docker"} | json [1h]
```

**Specific time range:**
Use the Grafana time picker in the UI (top-right corner)

---

### 7. Aggregations and Metrics

**Count errors by service:**
```logql
sum by (service) (count_over_time({job="docker"} | json | level="ERROR" [5m]))
```

**Error rate (errors per minute):**
```logql
sum by (service) (rate({job="docker"} | json | level="ERROR" [1m]))
```

**Log volume by level:**
```logql
sum by (level) (count_over_time({job="docker"} | json [1m]))
```

**Top 10 services by log volume:**
```logql
topk(10, sum by (service) (count_over_time({job="docker"} | json [5m])))
```

---

## Troubleshooting Scenarios

### Scenario 1: Order Execution Failed

**Problem:** A trade order failed to execute and you need to understand why.

**Steps:**

1. **Find the error:**
```logql
{service="execution_gateway"} | json | level="ERROR" | message =~ "order"
```

2. **Extract the trace ID from the error log**
   - Click on the log entry in Grafana
   - Copy the `trace_id` value

3. **Trace the full request path:**
```logql
{job="docker"} | json | trace_id="<paste-trace-id-here>"
```

4. **Look for the sequence:**
   - Signal generation (signal_service)
   - Risk checks (risk_manager)
   - Order submission (execution_gateway)
   - Look for the point of failure

**Example output interpretation:**
```
10:30:00 signal_service   INFO  Generated signal for AAPL: BUY 10 shares
10:30:01 risk_manager     INFO  Risk check passed for order
10:30:02 execution_gateway ERROR Order submission failed: insufficient buying power
```

---

### Scenario 2: High Error Rate Spike

**Problem:** Grafana alert shows error rate spike at 10:15 AM.

**Steps:**

1. **Find all errors during the spike:**
```logql
{job="docker"} | json | level="ERROR"
```
   - Set time range to 10:10 - 10:20

2. **Group errors by service:**
```logql
sum by (service) (count_over_time({job="docker"} | json | level="ERROR" [1m]))
```

3. **Identify the most common error message:**
```logql
{job="docker"} | json | level="ERROR" | __error__=""
```
   - Look for patterns in the messages

4. **Check for cascading failures:**
   - If execution_gateway shows errors, check upstream services:
```logql
{service=~"signal_service|risk_manager"} | json | level="ERROR"
```

---

### Scenario 3: Slow Request Investigation

**Problem:** Users report slow signal generation.

**Steps:**

1. **Find recent signal generation logs:**
```logql
{service="signal_service"} | json | message =~ "Generated.*signals"
```

2. **Look for timing context:**
```logql
{service="signal_service"} | json | context_duration_ms > 1000
```

3. **Check for resource exhaustion errors:**
```logql
{service="signal_service"} | json | message =~ "timeout|memory|database"
```

4. **Correlate with database logs:**
```logql
{container="postgres"} | json
```

---

### Scenario 4: Circuit Breaker Tripped

**Problem:** Trading halted due to circuit breaker.

**Steps:**

1. **Find the circuit breaker trip event:**
```logql
{job="docker"} | json | message =~ "(?i)circuit.*breaker.*trip"
```

2. **Find the root cause (usually high drawdown or errors):**
```logql
{job="docker"} | json | level="ERROR" | message =~ "drawdown|loss"
```

3. **Check what happened just before the trip:**
   - Note the timestamp of the circuit breaker log
   - Query 5 minutes before:
```logql
{job="docker"} | json
```
   - Set time range to [trip_time - 5min, trip_time]

4. **Verify recovery:**
```logql
{job="docker"} | json | message =~ "(?i)circuit.*breaker.*reset"
```

---

### Scenario 5: Missing Logs from a Service

**Problem:** No logs appearing for a service.

**Steps:**

1. **Verify the service is running:**
```bash
docker ps | grep trading_platform
```

2. **Check if service has the logging label:**
```bash
docker inspect <container_name> | grep logging
```
   - Should show: `"logging": "promtail"`

3. **Check Promtail is scraping the container:**
```logql
{container=~".*"}
```
   - Should see your container name in the labels

4. **Check service is actually logging:**
```bash
docker logs <container_name>
```

5. **Verify JSON format:**
   - Logs should be valid JSON
   - Check for syntax errors in log output

---

### Scenario 6: Trace ID Not Propagating

**Problem:** Cannot correlate logs across services for a request.

**Steps:**

1. **Check if trace IDs exist at all:**
```logql
{job="docker"} | json | trace_id != ""
```

2. **Verify middleware is configured:**
   - Check service startup logs:
```logql
{service="signal_service"} | json | message =~ "(?i)middleware|startup"
```

3. **Test trace propagation manually:**
```bash
curl -H "X-Trace-ID: test-123" http://localhost:8000/health
```
   - Then query:
```logql
{job="docker"} | json | trace_id="test-123"
```

4. **Check for context leakage:**
```logql
{job="docker"} | json | trace_id != "" | trace_id =~ ".*-.*-.*-.*-.*"
```
   - All trace IDs should be valid UUIDs

---

## Advanced Queries

### Pattern Extraction

**Extract order IDs from log messages:**
```logql
{service="execution_gateway"} | json | regexp "order_id=(?P<order_id>\\w+)"
```

### Log Parsing with Line Format

**Custom formatted output:**
```logql
{job="docker"} | json | line_format "{{.timestamp}} [{{.level}}] {{.service}}: {{.message}}"
```

### Multi-Service Correlation

**Find all services that processed a specific symbol:**
```logql
{job="docker"} | json | context_symbol="AAPL" | distinct service
```

### Log Rate Alerting

**Alert if error rate exceeds threshold:**
```logql
sum by (service) (rate({job="docker"} | json | level="ERROR" [5m])) > 10
```

---

## Performance Tips

1. **Always use time ranges:** Don't query unbounded time
2. **Filter early:** Apply filters before JSON parsing when possible
3. **Use labels over text search:** `level="ERROR"` is faster than `message =~ "ERROR"`
4. **Limit results:** Add `| limit 100` for exploratory queries
5. **Use metrics queries for graphs:** `count_over_time` for visualization

---

## Label Reference

**Available labels:**
- `job`: Always "docker" for container logs
- `container`: Docker container name (e.g., "trading_platform_signal_service")
- `service`: Extracted from container name (e.g., "signal_service")
- `compose_project`: Docker Compose project name
- `compose_service`: Docker Compose service name
- `level`: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `trace_id`: Distributed trace ID (UUID)
- `service_name`: Service name from JSON log field

**Context fields** (prefixed with `context_` in labels):
- Variable per service
- Common: `symbol`, `strategy_id`, `order_id`, `position_id`

---

## Query Builder Tips

**In Grafana Explore:**

1. **Use the query builder:**
   - Click "Code" to switch to builder mode
   - Select labels from dropdowns
   - Less error-prone than typing

2. **Use Ctrl+Space for autocomplete:**
   - Works in code mode
   - Shows available labels and functions

3. **Use the "Explain" button:**
   - Shows what your query does
   - Helps debug complex queries

4. **Save useful queries:**
   - Star queries in Explore
   - Create dashboard panels from queries

---

## Common Errors

### Error: "parse error: syntax error"
**Cause:** Invalid LogQL syntax
**Fix:** Check for unmatched braces, quotes, or pipes

### Error: "too many outstanding requests"
**Cause:** Query is too broad (scanning too much data)
**Fix:** Narrow time range, add more label filters

### Error: "no data"
**Cause:** No logs match the query
**Fix:**
- Verify time range
- Check label values with: `{job="docker"}`
- Verify services are running

### Error: "entry out of order"
**Cause:** Log timestamp in the past (clock skew)
**Fix:** Ensure NTP is running on all Docker hosts

---

## Further Reading

- [Loki LogQL Documentation](https://grafana.com/docs/loki/latest/logql/)
- [Grafana Explore Guide](https://grafana.com/docs/grafana/latest/explore/)
- [LogQL Cheat Sheet](https://megamorf.gitlab.io/cheat-sheets/loki/)
- Project: `libs/common/logging/` implementation
- ADR: `docs/ADRs/0005-centralized-logging-architecture.md`

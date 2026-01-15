# Structured Logging

This document explains why structured logging in JSON format is essential for production systems and how it enables powerful querying and analysis.

---

## What is Structured Logging?

**Structured logging** outputs logs as machine-readable data structures (JSON) rather than human-readable text strings.

### The Problem: Plain Text Logs Are Hard to Query

**Plain text logging:**
```
2025-10-21 12:00:01 INFO Order submitted for AAPL quantity=10 side=buy price=150.25
2025-10-21 12:00:02 ERROR Order validation failed for TSLA: insufficient funds
2025-10-21 12:00:03 INFO Position updated: GOOGL qty=5 cost_basis=2800.00
```

**Problems:**
- **No standard format** - Each developer writes logs differently
- **Hard to parse** - Regex required to extract fields
- **Inconsistent fields** - Sometimes `symbol="AAPL"`, sometimes `AAPL:`, sometimes `(AAPL)`
- **Query limitations** - Can't filter by numeric ranges (e.g., `quantity > 100`)
- **Type loss** - Everything is a string (can't distinguish `"10"` vs `10`)

**Example: Find all orders with quantity > 50**
```bash
# Plain text - fragile regex, misses variations
grep -E "quantity[=:]? *[0-9]{3,}" orders.log

# What about "qty", "size", "shares"?
# What about quantity=9 (single digit)?
```

**With structured logging:**
```json
{"timestamp": "2025-10-21T12:00:01.000Z", "level": "INFO", "service": "execution_gateway", "message": "Order submitted", "context": {"symbol": "AAPL", "quantity": 10, "side": "buy", "price": 150.25}}
{"timestamp": "2025-10-21T12:00:02.000Z", "level": "ERROR", "service": "execution_gateway", "message": "Order validation failed", "context": {"symbol": "TSLA", "error": "insufficient funds"}}
{"timestamp": "2025-10-21T12:00:03.000Z", "level": "INFO", "service": "execution_gateway", "message": "Position updated", "context": {"symbol": "GOOGL", "quantity": 5, "cost_basis": 2800.0}}
```

**Query in Loki:**
```logql
# Find all orders with quantity > 50
{service="execution_gateway"} | json | json context | quantity > 50

# Filter by exact field
{service="execution_gateway"} | json | json context | symbol="AAPL"

# Aggregate
sum by (symbol) (count_over_time({service="execution_gateway"} | json [1h]))
```

---

## Benefits of Structured Logging

### 1. Machine-Readable

**Plain text:**
```python
logger.info(f"Order submitted: {order_id} for {symbol} qty={quantity} price={price}")
```

**Problem:** How do you extract `order_id` from this? Regex? What if format changes?

**Structured:**
```python
log_with_context(
    logger,
    "INFO",
    "Order submitted",
    order_id=order_id,
    symbol=symbol,
    quantity=quantity,
    price=price
)
```

**Result:**
```json
{
  "timestamp": "2025-10-21T12:00:00.000Z",
  "level": "INFO",
  "service": "execution_gateway",
  "message": "Order submitted",
  "context": {
    "order_id": "ORDER-123",
    "symbol": "AAPL",
    "quantity": 10,
    "price": 150.25
  }
}
```

**Query:** Simple JSON field access, no regex needed.

### 2. Type Preservation

**Plain text:** Everything is a string
```
price=150.25 → "price=150.25"
quantity=10  → "quantity=10"
```

**Structured:** Types preserved
```json
{
  "price": 150.25,     // float
  "quantity": 10,      // int
  "is_filled": true,   // boolean
  "symbols": ["AAPL", "GOOGL"]  // array
}
```

**Benefit:** Numeric comparisons, boolean filters, array operations
```logql
# Numeric comparison
{service="execution_gateway"} | json | json context | price > 100

# Boolean filter
{service="execution_gateway"} | json | json context | is_filled=true
```

### 3. Consistent Schema

**Plain text:** Every developer logs differently
```
Service A: "Order order_id=123 submitted for AAPL"
Service B: "Submitted order 123 (AAPL)"
Service C: "AAPL: order 123 submitted"
```

**Structured:** Standardized schema enforced by code
```json
// All services use same schema
{
  "timestamp": "...",
  "level": "INFO",
  "service": "execution_gateway",  // Always present
  "trace_id": "abc-123",           // Always present
  "message": "...",
  "context": {...}                 // Always a dict
}
```

**Benefit:** Query works across all services, no per-service customization.

### 4. Context Isolation

**Plain text:** Context mixed with message
```
logger.info(f"Processing order {order_id} for {symbol} qty={quantity}")
```

**Problem:** Can't change message without breaking parsing.

**Structured:** Message separate from context
```python
log_with_context(
    logger,
    "INFO",
    "Processing order",  # Human-readable message
    order_id=order_id,   # Machine-readable context
    symbol=symbol,
    quantity=quantity
)
```

**Benefit:**
- Change message text without affecting queries
- Add/remove context fields without regex updates
- Human reads `message`, machines read `context`

### 5. Aggregation and Analytics

**Plain text:** Count logs with grep/wc
```bash
grep "Order submitted" orders.log | wc -l
```

**Structured:** Aggregate by any field
```logql
# Count orders over time
count_over_time({service_name="execution_gateway"} | json | message="Order submitted" [1h])

# Average order size (requires unwrap)
avg_over_time(
  {service_name="execution_gateway"} | json | message="Order submitted"
  | json context | unwrap quantity [1h]
)

# Error rate by service
sum by (service_name) (rate({job="docker"} | json | level="ERROR" [5m]))
```

---

## The Trading Platform Log Schema

Every log entry follows this standardized JSON schema:

```json
{
  "timestamp": "2025-10-21T22:00:00.123456Z",  // ISO 8601 UTC
  "level": "INFO",                              // DEBUG | INFO | WARNING | ERROR | CRITICAL
  "service": "signal_service",                  // Service name from configure_logging()
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",  // Distributed trace ID
  "message": "Generated signals for 10 symbols",        // Human-readable message
  "context": {                                  // Custom structured data
    "symbols": ["AAPL", "GOOGL"],
    "count": 10,
    "strategy_id": "alpha_baseline"
  },
  "exception": null,                            // Exception info (if error)
  "source": {                                   // Source code location
    "file": "signal_generator.py",
    "line": 142,
    "function": "generate_signals"
  }
}
```

### Schema Fields

**Required fields (always present):**
- `timestamp`: ISO 8601 UTC timestamp with milliseconds
- `level`: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `service`: Service name (e.g., "signal_service", "execution_gateway")
- `trace_id`: Distributed tracing ID (UUID v4 or null)
- `message`: Human-readable log message
- `source`: Source code location (file, line, function)

**Optional fields:**
- `context`: Dictionary of custom structured data
- `exception`: Exception information (type, message, traceback)

### Why This Schema?

**Timestamp in ISO 8601 UTC:**
- Sortable chronologically (string comparison works)
- No timezone ambiguity (always UTC)
- Standardized format (parseable by all tools)

**Service name:**
- Low-cardinality label (10-20 services)
- Fast filtering in Loki

**Trace ID:**
- Correlates logs across services
- Null for background jobs (no request context)

**Context dict:**
- Arbitrary structured data
- Type-safe (ints, floats, booleans, arrays preserved)
- Queryable in Loki via JSON field access

**Exception dict:**
- Structured exception info (not just stacktrace string)
- Type, message, and full traceback separated
- Parseable for error aggregation

**Source location:**
- Jump to code from log entry
- Filter by file/function for debugging

---

## How It Works

### 1. JSON Formatter

The `JSONFormatter` class converts Python `LogRecord` objects into JSON:

```python
# libs/common/logging/formatter.py
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "service": self.service_name,
            "trace_id": self._extract_trace_id(record),
            "message": record.getMessage(),
            "context": self._extract_context(record),
            "source": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        }
        return json.dumps(log_entry, default=str)
```

**Key features:**
- ISO 8601 UTC timestamps
- Extracts trace ID from context (if present)
- Separates context dict from message
- Includes source location for every log

### 2. Service Configuration

Every service configures logging at startup:

```python
from libs.core.common.logging import configure_logging

# Configure JSON logging for this service
logger = configure_logging(
    service_name="signal_service",
    log_level="INFO"
)
```

**What this does:**
1. Creates a `JSONFormatter` with the service name
2. Attaches formatter to root logger
3. Sets minimum log level
4. Returns configured logger

**Result:** All logs from this service use JSON format with consistent schema.

### 3. Logging with Context

**Simple logging:**
```python
from libs.core.common.logging import get_logger

logger = get_logger(__name__)
logger.info("Service started")
```

**Output:**
```json
{
  "timestamp": "2025-10-21T12:00:00.000Z",
  "level": "INFO",
  "service": "signal_service",
  "trace_id": null,
  "message": "Service started",
  "source": {"file": "main.py", "line": 15, "function": "startup"}
}
```

**Logging with context:**
```python
from libs.core.common.logging import log_with_context

log_with_context(
    logger,
    "INFO",
    "Generated signals",
    symbol_count=10,
    strategy="alpha_baseline",
    model_version="v1.2.3"
)
```

**Output:**
```json
{
  "timestamp": "2025-10-21T12:00:01.000Z",
  "level": "INFO",
  "service": "signal_service",
  "trace_id": "abc-123",
  "message": "Generated signals",
  "context": {
    "symbol_count": 10,
    "strategy": "alpha_baseline",
    "model_version": "v1.2.3"
  },
  "source": {"file": "generator.py", "line": 142, "function": "generate"}
}
```

### 4. Exception Logging

**Automatic exception formatting:**
```python
try:
    risky_operation()
except Exception as e:
    # Use logger.exception() for errors - automatically includes traceback
    # Pass context via extra={} dictionary
    logger.exception("Operation failed", extra={"context": {"operation": "risky_operation"}})

    # Alternative: Use log_with_context for non-exception errors
    # log_with_context(logger, "ERROR", "Operation failed", operation="risky_operation")
```

**Note:** `logger.exception()` is Python's standard way to log exceptions with automatic traceback capture. Context is passed via the `extra` dictionary. For non-exception errors, use `log_with_context()` which provides a more consistent interface.

**Output:**
```json
{
  "timestamp": "2025-10-21T12:00:02.000Z",
  "level": "ERROR",
  "service": "signal_service",
  "trace_id": "abc-123",
  "message": "Operation failed",
  "context": {
    "operation": "risky_operation"
  },
  "exception": {
    "type": "ValueError",
    "message": "Invalid input",
    "traceback": "Traceback (most recent call last):\n  File \"...\", line 42, in risky_operation\n    ...\nValueError: Invalid input"
  },
  "source": {"file": "worker.py", "line": 55, "function": "process"}
}
```

**Benefit:** Exception type, message, and traceback are separate fields, queryable independently.

---

## Integration with Loki/Promtail

### Promtail Parses JSON

Promtail automatically detects JSON logs and extracts fields:

```yaml
# infra/promtail/promtail-config.yml
pipeline_stages:
  - match:
      selector: '{job="docker"}'
      stages:
        - json:
            expressions:
              timestamp: timestamp
              level: level
              service_name: service
              trace_id: trace_id
              message: message
              context: context
        - labels:
            level:
            service_name:
```

**What this does:**
1. Detects JSON logs from Docker containers
2. Parses JSON fields (`level`, `service`, `trace_id`, etc.)
3. Promotes low-cardinality fields to labels (`level`, `service_name`)
4. Stores high-cardinality fields as JSON (`trace_id`, `context`)

### Loki Stores Labels + JSON

**Labels (indexed):**
- `service_name` (low cardinality: 10-20 values)
- `level` (low cardinality: 5 values)

**JSON fields (not indexed):**
- `trace_id` (high cardinality: millions of UUIDs)
- `context.*` (high cardinality: arbitrary data)

### Grafana Queries JSON Fields

**Query by label (fast):**
```logql
{service="execution_gateway", level="ERROR"} | json
```

**Query by JSON field (slower, but works):**
```logql
{service="execution_gateway"} | json | trace_id="abc-123"
```

**Query nested context fields:**
```logql
{service="execution_gateway"} | json | json context | symbol="AAPL"
```

**Aggregate context fields:**
```logql
sum by (symbol) (count_over_time({service="execution_gateway"} | json | json context [1h]))
```

---

## Best Practices

### ✅ DO

**Use structured context instead of string formatting:**
```python
# Good
log_with_context(logger, "INFO", "Order placed", order_id=order_id, symbol=symbol, quantity=quantity)

# Bad
logger.info(f"Order placed: {order_id} for {symbol} qty={quantity}")
```

**Why?** String formatting burns context into the message, making it unparseable.

**Log at appropriate levels:**
- `DEBUG`: Detailed diagnostic information (development only)
- `INFO`: General informational messages (normal operation)
- `WARNING`: Warning messages (degraded state, but functional)
- `ERROR`: Error messages (operation failed, service continues)
- `CRITICAL`: Critical errors (service cannot continue)

**Include relevant context:**
```python
log_with_context(
    logger,
    "ERROR",
    "Order submission failed",
    order_id=order_id,
    symbol=symbol,
    error_code=error.code,
    error_message=str(error)
)
```

**Why?** Context makes logs self-descriptive, no need to correlate multiple log lines.

**Use consistent field names:**
```python
# Good - consistent across services
log_with_context(logger, "INFO", "...", order_id="ORDER-123")
log_with_context(logger, "INFO", "...", order_id="ORDER-456")

# Bad - inconsistent field names
log_with_context(logger, "INFO", "...", order_id="ORDER-123")
log_with_context(logger, "INFO", "...", id="ORDER-456")  # Different field name!
```

**Why?** Queries break if field names differ across logs.

### ❌ DON'T

**Don't log sensitive data:**

⚠️ **CRITICAL:** Automated sanitization is NOT yet implemented. You MUST manually avoid logging:
- API keys, tokens, passwords
- Account numbers, SSNs, credit cards
- PII (names, emails, addresses)

```python
# Bad - logs API key
logger.info(f"Using API key: {api_key}")

# Good - sanitized
log_with_context(logger, "INFO", "Using API key", key_prefix=api_key[:4])
```

**Don't use string concatenation:**
```python
# Bad
logger.info("User " + user_id + " placed order")

# Good
log_with_context(logger, "INFO", "User placed order", user_id=user_id)
```

**Why?** String concatenation is slower and makes fields unparseable.

**Don't log in tight loops:**
```python
# Bad - thousands of logs
for item in items:
    logger.debug(f"Processing {item}")

# Good - summary logs
logger.info(f"Processing {len(items)} items")
for item in items:
    process(item)
logger.info(f"Completed {len(items)} items")
```

**Why?** Log volume costs storage and makes queries slow.

**Don't swallow exceptions:**
```python
# Bad - silent failure
try:
    risky_operation()
except Exception:
    pass

# Good - log and re-raise
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed", extra={"context": {"operation": "risky_operation"}})
    raise
```

**Why?** Silent failures are impossible to debug.

---

## Querying Structured Logs

### Basic Queries

**All logs from a service:**
```logql
{service_name="execution_gateway"} | json
```

**Filter by log level:**
```logql
{service_name="execution_gateway", level="ERROR"} | json
```

**Filter by message:**
```logql
{service_name="execution_gateway"} | json | message="Order submitted"
```

### Context Field Queries

**Filter by context field:**
```logql
{service_name="execution_gateway"} | json | json context | symbol="AAPL"
```

**Numeric comparison:**
```logql
{service_name="execution_gateway"} | json | json context | quantity > 50
```

**Multiple conditions:**
```logql
{service_name="execution_gateway"} | json | json context | symbol="AAPL" | quantity > 10
```

### Aggregations

**Count logs by service:**
```logql
sum by (service_name) (count_over_time({job="docker"} | json [1h]))
```

**Error rate per service:**
```logql
sum by (service_name) (rate({job="docker"} | json | level="ERROR" [5m]))
```

**Average quantity (requires unwrap):**
```logql
avg_over_time(
  {service_name="execution_gateway"} | json | message="Order submitted"
  | json context | unwrap quantity [1h]
)
```

### Trace Correlation

**Follow a request across services:**
```logql
{job="docker"} | json | trace_id="550e8400-e29b-41d4-a716-446655440000"
```

**Result:** All logs from all services for that trace ID, chronologically ordered.

---

## Performance Considerations

### JSON Serialization Overhead

**Cost:** ~5% CPU overhead compared to plain text logging

**Mitigation:**
- Use `INFO` level in production (not `DEBUG`)
- Avoid logging in tight loops
- Use sampling for high-frequency logs

### Log Volume

**Structured logs are larger:**
- Plain text: `"Order submitted for AAPL"` → 25 bytes
- Structured JSON: `{"timestamp": "...", "level": "INFO", ...}` → 200 bytes

**Mitigation:**
- Loki compression reduces storage by ~10x
- 30-day retention keeps volume manageable
- Use log level filtering to reduce noise

### Query Performance

**Label queries are fast:**
```logql
{service="execution_gateway", level="ERROR"} | json  # <100ms
```

**JSON field queries are slower:**
```logql
{service="execution_gateway"} | json | json context | symbol="AAPL"  # ~500ms
```

**Best practice:** Always start with label filters, then apply JSON filters.

---

## Common Patterns

### Pattern 1: Request-Response Logging

```python
log_with_context(
    logger,
    "INFO",
    "HTTP request received",
    method="POST",
    path="/api/orders",
    client_ip=request.client.host
)

# ... process request ...

log_with_context(
    logger,
    "INFO",
    "HTTP response sent",
    status_code=200,
    duration_ms=elapsed_ms
)
```

**Query:** Filter by status code or duration
```logql
{service="execution_gateway"} | json | json context | status_code >= 400
{service="execution_gateway"} | json | json context | duration_ms > 1000
```

### Pattern 2: Business Event Logging

```python
log_with_context(
    logger,
    "INFO",
    "Order submitted to broker",
    order_id=order_id,
    symbol=symbol,
    quantity=quantity,
    side=side,
    order_type=order_type,
    client_order_id=client_order_id
)
```

**Query:** Analyze order patterns
```logql
# Count orders over time
count_over_time({service_name="execution_gateway"} | json | message="Order submitted to broker" [1h])

# Average order size (unwrap required for numeric aggregation)
avg_over_time(
  {service_name="execution_gateway"} | json | message="Order submitted to broker"
  | json context | unwrap quantity [1h]
)

# Note: To aggregate by symbol, you'd need to promote it to a label first,
# but that creates high cardinality. Better to query specific symbols:
# {service_name="execution_gateway"} | json | json context | symbol="AAPL"
```

### Pattern 3: Error Context

```python
try:
    execute_trade(order)
except BrokerError as e:
    # Use logger.exception() with extra parameter for single log entry
    logger.exception(
        "Broker rejected order",
        extra={
            "context": {
                "order_id": order.id,
                "symbol": order.symbol,
                "error_code": e.code,
                "error_message": str(e),
                "retry_count": retry_count
            }
        }
    )
```

**Query:** Analyze error patterns
```logql
# Count errors over time
count_over_time({service_name="execution_gateway"} | json | level="ERROR" [1h])

# Note: error_code is a JSON field, not a label, so you can't aggregate by it directly.
# To analyze errors by code, query and process results client-side.
```

---

## Summary

**What is Structured Logging?**
- Logs as JSON instead of plain text
- Machine-readable with consistent schema
- Type-safe (preserves ints, floats, booleans, arrays)

**Why Structured Logging?**
- **Queryable:** Filter by any field without regex
- **Aggregatable:** Sum, average, count by any dimension
- **Consistent:** Standardized schema across all services
- **Type-safe:** Numeric comparisons, boolean filters
- **Future-proof:** Add fields without breaking queries

**How It Works:**
- `JSONFormatter` converts Python logs to JSON
- Promtail parses JSON and extracts fields
- Loki stores labels (low cardinality) + JSON (high cardinality)
- Grafana queries via LogQL

**Key Components:**
- `libs/common/logging/formatter.py` - JSON formatter
- `libs/common/logging/__init__.py` - Configuration and helpers
- `infra/promtail/promtail-config.yml` - JSON parsing pipeline

**Next Steps:**
- [Centralized Logging](./centralized-logging.md) - Loki/Promtail/Grafana stack
- [Distributed Tracing](./distributed-tracing.md) - How trace IDs work
- [LOGGING_GUIDE.md](../GETTING_STARTED/LOGGING_GUIDE.md) - Developer usage guide
- [logging-queries.md](../RUNBOOKS/logging-queries.md) - LogQL examples

# Distributed Tracing

This document explains how trace IDs enable request correlation across microservices in the trading platform.

---

## What is Distributed Tracing?

**Distributed tracing** tracks a single request as it flows through multiple services using a unique **trace ID**.

### The Problem: Microservices Make Debugging Hard

In a monolithic application, following a request is easy:
```
Request → Single Process → Response
(All logs have same process ID)
```

In microservices, a single user request triggers multiple service calls:
```
User Request → Execution Gateway → Risk Manager → Signal Service → Database
               (logs scattered across 4 services + DB)
```

**Without tracing:**
```
# Execution Gateway logs
[12:00:01] INFO  Order submitted for AAPL
[12:00:02] ERROR Order validation failed

# Risk Manager logs
[12:00:01] DEBUG Position check for account 123
[12:00:02] ERROR Position limit exceeded

# Signal Service logs
[12:00:01] INFO  Generated signal for AAPL
```

**Questions we can't answer:**
- Which "Order submitted" corresponds to which "Position check"?
- Did the ERROR in Execution Gateway cause the ERROR in Risk Manager?
- What was the complete sequence of events for account 123's order?

**With tracing:**
```
# Execution Gateway logs
[12:00:01] trace_id=abc-123 INFO  Order submitted for AAPL
[12:00:02] trace_id=abc-123 ERROR Order validation failed

# Risk Manager logs
[12:00:01] trace_id=abc-123 DEBUG Position check for account 123
[12:00:02] trace_id=abc-123 ERROR Position limit exceeded

# Signal Service logs
[12:00:01] trace_id=abc-123 INFO  Generated signal for AAPL
```

**Query in Grafana:**
```logql
{job="docker"} | json | trace_id="abc-123"
```

**Result:** Complete timeline of the request across all services, chronologically ordered.

---

## How Trace IDs Work

### 1. Trace ID Generation

**UUID v4 format:**
```
550e8400-e29b-41d4-a716-446655440000
```

**Why UUID v4?**
- **Globally unique:** No central coordination needed
- **128-bit space:** 2^128 possible values (no collisions in practice)
- **Random:** No information leakage (unlike sequential IDs)
- **Standard:** Supported by all tracing systems (Jaeger, Zipkin, etc.)

**Generated at entry point:**
```python
# libs/common/logging/middleware.py
def dispatch(self, request: Request, call_next: Callable) -> Response:
    # Extract from header if present, generate if missing
    trace_id = request.headers.get("X-Trace-ID")
    if not trace_id:
        trace_id = generate_trace_id()  # UUID v4

    set_trace_id(trace_id)  # Store in context
    ...
```

### 2. Context Propagation (Same Service)

**Challenge:** How do logs within the same service get the same trace ID?

**Solution:** Python's `contextvars` module (async-safe thread-local storage)

```python
# libs/common/logging/context.py
import contextvars

_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)

def set_trace_id(trace_id: str) -> None:
    """Store trace ID in context (async-safe)."""
    _trace_id_var.set(trace_id)

def get_trace_id() -> Optional[str]:
    """Retrieve trace ID from context."""
    return _trace_id_var.get()
```

**Why `contextvars` instead of thread-local?**

```python
# ❌ Thread-local storage (BREAKS with async)
import threading
_trace_id = threading.local()

# Problem: FastAPI handlers run in asyncio event loop
# Multiple requests can share the same thread!
async def handler1():  # Request 1, trace_id="abc"
    await asyncio.sleep(1)  # Yields to event loop
    # trace_id might now be "xyz" from request 2!

async def handler2():  # Request 2, trace_id="xyz"
    _trace_id.value = "xyz"  # Overwrites request 1's value
```

```python
# ✅ contextvars (WORKS with async)
_trace_id_var = contextvars.ContextVar("trace_id")

async def handler1():
    _trace_id_var.set("abc")
    await asyncio.sleep(1)
    print(_trace_id_var.get())  # Always "abc" (isolated context)

async def handler2():
    _trace_id_var.set("xyz")  # Separate context, doesn't affect handler1
```

**Key property:** Each async task gets its own context copy.

### 3. Header Propagation (Across Services)

**HTTP Header:** `X-Trace-ID`

```
Service A → HTTP Request → Service B
            Headers: { "X-Trace-ID": "abc-123" }
```

**Automatic injection via HTTP client:**
```python
# libs/common/logging/http_client.py
class TracedHTTPXClient(httpx.AsyncClient):
    async def request(self, method: str, url: str, **kwargs: Any) -> Response:
        trace_id = get_trace_id()  # Get from context
        if trace_id:
            headers = dict(kwargs.get("headers") or {})
            headers["X-Trace-ID"] = trace_id  # Inject into request
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)
```

**Automatic extraction via middleware:**
```python
# libs/common/logging/middleware.py
def dispatch(self, request: Request, call_next: Callable) -> Response:
    trace_id = request.headers.get("X-Trace-ID")  # Extract from incoming request
    if not trace_id:
        trace_id = generate_trace_id()
    set_trace_id(trace_id)  # Store in context
    ...
```

---

## Trace Flow Example

### Scenario: User Submits Order

```
User → Execution Gateway → Risk Manager → Signal Service
```

**Step-by-step:**

#### 1. User Request (Entry Point)
```http
POST /api/v1/orders HTTP/1.1
Host: execution-gateway:8001
(No X-Trace-ID header - first entry point)

Body:
{
  "symbol": "AAPL",
  "quantity": 10,
  "side": "buy"
}
```

**Execution Gateway middleware:**
```python
# No trace ID in request, generate new one
trace_id = generate_trace_id()  # "550e8400-e29b-41d4-a716-446655440000"
set_trace_id(trace_id)

# All subsequent logs in this request include trace_id
logger.info("Order received")
# → {"trace_id": "550e8400-...", "message": "Order received", ...}
```

#### 2. Call Risk Manager

**Execution Gateway → Risk Manager:**
```python
# libs/common/logging/http_client.py automatically injects trace ID
async with get_traced_client(base_url="http://risk-manager:8002") as client:
    response = await client.post("/api/v1/risk/check", json={...})
```

**HTTP request sent:**
```http
POST /api/v1/risk/check HTTP/1.1
Host: risk-manager:8002
X-Trace-ID: 550e8400-e29b-41d4-a716-446655440000  ← Injected automatically

Body:
{
  "symbol": "AAPL",
  "quantity": 10,
  "account_id": "123"
}
```

**Risk Manager middleware:**
```python
# Extract trace ID from header
trace_id = request.headers.get("X-Trace-ID")  # "550e8400-..."
set_trace_id(trace_id)  # Reuse same trace ID

logger.info("Risk check started")
# → {"trace_id": "550e8400-...", "message": "Risk check started", ...}
```

#### 3. Call Signal Service

**Risk Manager → Signal Service:**
```python
async with get_traced_client(base_url="http://signal-service:8003") as client:
    response = await client.post("/api/v1/signals/generate", json={...})
```

**HTTP request sent:**
```http
POST /api/v1/signals/generate HTTP/1.1
Host: signal-service:8003
X-Trace-ID: 550e8400-e29b-41d4-a716-446655440000  ← Same trace ID

Body:
{
  "symbols": ["AAPL"],
  "as_of_date": "2025-10-21"
}
```

**Signal Service middleware:**
```python
trace_id = request.headers.get("X-Trace-ID")  # "550e8400-..."
set_trace_id(trace_id)

logger.info("Signal generation started")
# → {"trace_id": "550e8400-...", "message": "Signal generation started", ...}
```

#### 4. Complete Log Trail

**Query in Grafana:**
```logql
{job="docker"} | json | trace_id="550e8400-e29b-41d4-a716-446655440000"
```

**Result (chronologically ordered):**
```
[12:00:00.100] execution-gateway  INFO  Order received
[12:00:00.150] execution-gateway  DEBUG Validating order parameters
[12:00:00.200] execution-gateway  INFO  Calling risk manager
[12:00:00.250] risk-manager       INFO  Risk check started
[12:00:00.300] risk-manager       DEBUG Position check for account 123
[12:00:00.350] risk-manager       INFO  Calling signal service
[12:00:00.400] signal-service     INFO  Signal generation started
[12:00:00.450] signal-service     DEBUG Loading model version v1.0.0
[12:00:00.500] signal-service     INFO  Generated signal: AAPL=+0.015
[12:00:00.550] risk-manager       INFO  Risk check passed
[12:00:00.600] execution-gateway  INFO  Order submitted to broker
```

**Insight:** Complete request timeline across 3 services, showing:
- Execution order (which service called which)
- Timing (where latency occurred - 450ms in signal generation)
- Success/failure (all steps succeeded)

---

## Context Managers for Manual Control

### LogContext Class

**For web requests:** Trace IDs are managed automatically by the FastAPI middleware. No manual context management needed.

**For background tasks:** Use `LogContext` for batch jobs, cron tasks, or other entry points that don't originate from a web request:

```python
from libs.core.common.logging import LogContext, get_logger

logger = get_logger(__name__)

# Option 1: Provide custom trace ID
with LogContext("batch-job-2025-10-21"):
    logger.info("Processing batch job")
    # → {"trace_id": "batch-job-2025-10-21", "message": "Processing batch job", ...}

    process_records()
    # All logs inside this block have same trace ID

# Outside the context, trace ID is cleared
logger.info("Cleanup complete")
# → {"trace_id": null, "message": "Cleanup complete", ...}

# Option 2: Auto-generate trace ID
with LogContext():  # Generates UUID v4
    logger.info("Processing item")
    # → {"trace_id": "9c8e3f7a-...", "message": "Processing item", ...}
```

### Nested Contexts

```python
with LogContext("outer-context"):
    logger.info("Outer task")
    # → {"trace_id": "outer-context", ...}

    with LogContext("inner-context"):
        logger.info("Inner task")
        # → {"trace_id": "inner-context", ...}

    # Restored to outer context
    logger.info("Back to outer")
    # → {"trace_id": "outer-context", ...}
```

---

## Debugging Scenarios

### Scenario 1: Order Submission Failed

**User report:** "My order for AAPL failed at 12:00 PM."

**Without tracing:**
```logql
# Find errors around that time
{service="execution-gateway"} | json | level="ERROR"
```

**Problem:** Hundreds of orders might have failed around 12:00 PM. Which one is the user's?

**With tracing:**

1. Find the user's order in the database (has trace ID)
2. Query Loki with that trace ID:
   ```logql
   {job="docker"} | json | trace_id="550e8400-..."
   ```

3. See complete timeline:
   ```
   [12:00:00.100] execution-gateway INFO  Order received for AAPL
   [12:00:00.200] risk-manager      ERROR Position limit exceeded for account 123
   [12:00:00.250] execution-gateway ERROR Order rejected by risk manager
   ```

**Root cause:** Position limit exceeded (not a bug, expected behavior).

### Scenario 2: Slow Request Investigation

**Alert:** 95th percentile latency spiked to 5 seconds at 3:00 PM.

**Without tracing:**
```
# Which service is slow?
# Which requests are affected?
# What's the bottleneck?
(Guess and check each service's logs)
```

**With tracing:**

1. Find slow requests in Execution Gateway logs:
   ```logql
   # Note: Requires adding duration_ms to log context
   # Example: log_with_context(logger, "INFO", "Request completed", duration_ms=elapsed_ms)
   {service_name="execution_gateway"} | json | json context | duration_ms > 5000
   ```

2. Extract trace IDs from slow requests

3. Query each trace ID to see timeline:
   ```logql
   {job="docker"} | json | trace_id="abc-123"
   ```

4. Identify bottleneck:
   ```
   [15:00:00.000] execution-gateway INFO  Order received
   [15:00:00.050] risk-manager      INFO  Risk check started
   [15:00:00.100] signal-service    INFO  Signal generation started
   [15:00:04.800] signal-service    INFO  Signal generated  ← 4.7 seconds!
   [15:00:04.850] risk-manager      INFO  Risk check passed
   [15:00:04.900] execution-gateway INFO  Order submitted
   ```

**Root cause:** Signal service taking 4.7 seconds (model loading issue, fixed in hot reload).

---

## Advanced Patterns

### Span IDs (Future Enhancement)

Trace IDs track requests across services. **Span IDs** track sub-operations within a service.

```
trace_id: abc-123
  span_id: 1 → Execution Gateway: Order validation
  span_id: 2 → Risk Manager: Position check
    span_id: 2.1 → Database: Query current positions
    span_id: 2.2 → Database: Query limits
  span_id: 3 → Signal Service: Feature generation
  span_id: 4 → Execution Gateway: Broker submission
```

**Not implemented yet**, but compatible with:
- **OpenTelemetry** - Industry standard for distributed tracing
- **Jaeger** - Distributed tracing UI with span visualization
- **Zipkin** - Alternative tracing backend

### Correlation with Metrics

**Grafana allows linking logs to metrics:**

```
User sees high error rate in Prometheus dashboard
  ↓
Clicks on spike
  ↓
Jumps to Loki logs filtered to that time range
  ↓
Finds trace IDs of failed requests
  ↓
Investigates root cause
```

**Example:**
1. **Metrics:** Error rate for `execution_gateway` spiked to 50% at 3:00 PM
2. **Logs:** Query errors during that time
   ```logql
   {service="execution_gateway"} | json | level="ERROR" | timestamp >= 3:00 PM
   ```
3. **Traces:** Extract trace IDs from error logs
4. **Root cause:** All errors have same trace ID pattern → upstream service (signal service) was down

---

## Best Practices

### ✅ DO

**Always use traced HTTP clients:**
```python
# Good
from libs.core.common.logging import get_traced_client

async with get_traced_client(base_url="http://service") as client:
    response = await client.get("/api")  # Trace ID auto-injected
```

**Add trace IDs to database records:**
```python
# Good
INSERT INTO orders (client_order_id, trace_id, ...)
VALUES ('ORDER-123', '550e8400-...', ...)
```

Why? Allows correlating database state with logs later.

**Include trace ID in API responses:**
```python
# Good
@app.post("/api/v1/orders")
async def submit_order(order: OrderRequest) -> OrderResponse:
    trace_id = get_trace_id()
    return OrderResponse(
        order_id="ORDER-123",
        trace_id=trace_id,  # Return to client
        ...
    )
```

Why? Client can use trace ID for support requests.

### ❌ DON'T

**Don't use raw httpx clients:**
```python
# Bad
import httpx

async with httpx.AsyncClient() as client:
    response = await client.get("http://service/api")
    # Trace ID NOT propagated!
```

**Don't generate new trace IDs mid-request:**
```python
# Bad
async def handler():
    set_trace_id(generate_trace_id())  # Breaks correlation!
    # Should inherit trace ID from middleware
```

**Don't use trace IDs as Loki labels:**
```yaml
# Bad (causes cardinality explosion)
labels:
  trace_id:  # MILLIONS of unique values → Loki crashes
```

---

## Summary

**What is Distributed Tracing?**
- Unique ID (trace ID) follows a request across services
- Enables end-to-end visibility in microservices
- Critical for debugging production issues

**How It Works:**
- **Generation:** UUID v4 at entry point
- **Storage:** `contextvars` for async-safe context propagation
- **Propagation:** `X-Trace-ID` HTTP header between services
- **Query:** LogQL in Grafana (`| json | trace_id="..."`)

**Key Components:**
- `libs/common/logging/context.py` - Trace ID storage
- `libs/common/logging/middleware.py` - HTTP header extraction/injection
- `libs/common/logging/http_client.py` - Automatic header propagation

**Next Steps:**
- [Structured Logging](./structured-logging.md) - JSON log format
- [Centralized Logging](./centralized-logging.md) - Loki/Promtail/Grafana stack
- [LOGGING_GUIDE.md](../GETTING_STARTED/LOGGING_GUIDE.md) - Developer usage guide

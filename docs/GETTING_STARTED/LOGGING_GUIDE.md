# Centralized Logging Guide

This guide shows how to use the centralized structured logging system in your services.

## Quick Start

### 1. Add Logging to Your Service

```python
# In your service's main.py
from libs.core.common.logging import configure_logging, add_trace_id_middleware
from fastapi import FastAPI

# Configure logging at startup
logger = configure_logging(
    service_name="my_service",
    log_level="INFO"
)

# Add trace ID middleware
app = FastAPI()
add_trace_id_middleware(app)

# Use throughout your service
from libs.core.common.logging import get_logger, log_with_context

logger = get_logger(__name__)

# Simple logging
logger.info("Service started")

# Logging with context
log_with_context(
    logger,
    "INFO",
    "Processing order",
    order_id="12345",
    symbol="AAPL",
    quantity=10
)
```

### 2. Make Traced HTTP Requests

```python
from libs.core.common.logging import get_traced_client

# Async client (automatically propagates trace ID)
async with get_traced_client(base_url="http://other-service") as client:
    response = await client.get("/api/data")
    # X-Trace-ID header added automatically

# Sync client
from libs.core.common.logging import get_traced_sync_client

with get_traced_sync_client() as client:
    response = client.get("http://other-service/api/data")

# Convenience functions
from libs.core.common.logging import traced_get, traced_post

response = await traced_get("http://other-service/api/data")
response = await traced_post("http://other-service/api/create", json={"name": "test"})
```

### 3. Query Logs in Grafana

1. Open Grafana: http://localhost:3000 (admin/admin)
2. Navigate to: **Explore** → Select **Loki** datasource
3. Try these queries:

```logql
# All logs from your service
{service="my_service"} | json

# Only errors
{service="my_service"} | json | level="ERROR"

# Trace a request
{job="docker"} | json | trace_id="your-trace-id-here"
```

See the [Logging Queries Runbook](../RUNBOOKS/logging-queries.md) for more examples.

---

## Log Schema

Every log entry follows this JSON schema:

```json
{
  "timestamp": "2025-10-21T22:00:00.123456Z",  // ISO 8601 UTC
  "level": "INFO",                              // DEBUG, INFO, WARNING, ERROR, CRITICAL
  "service": "signal_service",                  // Service name from configure_logging()
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",  // Distributed trace ID
  "message": "Generated signals for 10 symbols",        // Human-readable message
  "context": {                                  // Custom structured data
    "symbols": ["AAPL", "GOOGL"],
    "count": 10,
    "strategy_id": "alpha_baseline"
  },
  "exception": null,                            // Exception traceback (if error)
  "source": {                                   // Source code location
    "file": "signal_generator.py",
    "line": 142,
    "function": "generate_signals"
  }
}
```

---

## API Reference

### Configuration

#### `configure_logging(service_name, log_level="INFO", include_context=True)`

Sets up structured JSON logging for the service.

**Parameters:**
- `service_name` (str): Name of your service (e.g., "signal_service")
- `log_level` (str): Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `include_context` (bool): Include context fields in logs (default: True)

**Returns:** Configured logger instance

**Example:**
```python
logger = configure_logging("my_service", log_level="INFO")
```

---

### Getting a Logger

#### `get_logger(name)`

Get a logger instance for a module.

**Parameters:**
- `name` (str): Logger name (typically `__name__`)

**Returns:** Logger instance with trace ID support

**Example:**
```python
from libs.core.common.logging import get_logger

logger = get_logger(__name__)
logger.info("Module initialized")
```

---

### Logging with Context

#### `log_with_context(logger, level, message, **context)`

Log a message with structured context fields.

**Parameters:**
- `logger`: Logger instance
- `level` (str): Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `message` (str): Log message
- `**context`: Arbitrary keyword arguments added to log context

**Example:**
```python
from libs.core.common.logging import log_with_context

log_with_context(
    logger,
    "INFO",
    "Order executed",
    order_id="12345",
    symbol="AAPL",
    side="BUY",
    quantity=10,
    price=150.25
)
```

**Produces:**
```json
{
  "timestamp": "2025-10-21T22:00:00Z",
  "level": "INFO",
  "service": "my_service",
  "trace_id": "abc-123",
  "message": "Order executed",
  "context": {
    "order_id": "12345",
    "symbol": "AAPL",
    "side": "BUY",
    "quantity": 10,
    "price": 150.25
  }
}
```

---

### Trace ID Management

#### `set_trace_id(trace_id)`

Manually set the trace ID for the current context.

**Parameters:**
- `trace_id` (str): Trace ID to set

**Example:**
```python
from libs.core.common.logging import set_trace_id

set_trace_id("custom-trace-id")
logger.info("This log will have trace_id='custom-trace-id'")
```

#### `get_trace_id()`

Get the current trace ID from context.

**Returns:** Current trace ID (str | None)

**Example:**
```python
from libs.core.common.logging import get_trace_id

trace_id = get_trace_id()
if trace_id:
    print(f"Current trace: {trace_id}")
```

#### `generate_trace_id()`

Generate a new UUID v4 trace ID.

**Returns:** New trace ID (str)

**Example:**
```python
from libs.core.common.logging import generate_trace_id

new_trace = generate_trace_id()  # "550e8400-e29b-41d4-a716-446655440000"
```

#### `clear_trace_id()`

Clear the trace ID from context.

**Example:**
```python
from libs.core.common.logging import clear_trace_id

clear_trace_id()
assert get_trace_id() is None
```

---

### Middleware

#### `add_trace_id_middleware(app)`

Add trace ID middleware to a FastAPI application.

**Parameters:**
- `app`: FastAPI application instance

**Behavior:**
- Extracts `X-Trace-ID` header from incoming requests
- Generates new trace ID if header missing
- Sets trace ID in logging context
- Injects `X-Trace-ID` header into responses
- Clears trace ID after request completes

**Example:**
```python
from fastapi import FastAPI
from libs.core.common.logging import add_trace_id_middleware

app = FastAPI()
add_trace_id_middleware(app)

@app.get("/health")
async def health():
    # Trace ID automatically available in all logs
    logger.info("Health check")
    return {"status": "ok"}
```

---

### HTTP Clients

#### `get_traced_client(base_url=None, timeout=10.0, **kwargs)`

Create an async HTTP client with automatic trace ID propagation.

**Parameters:**
- `base_url` (str, optional): Base URL for all requests
- `timeout` (float): Request timeout in seconds (default: 10.0)
- `**kwargs`: Additional httpx.AsyncClient parameters

**Returns:** TracedHTTPXClient instance

**Example:**
```python
from libs.core.common.logging import get_traced_client

async with get_traced_client(base_url="http://api.example.com") as client:
    response = await client.get("/users")
    # X-Trace-ID header automatically added
```

#### `get_traced_sync_client(base_url=None, timeout=10.0, **kwargs)`

Synchronous version of `get_traced_client()`.

**Example:**
```python
from libs.core.common.logging import get_traced_sync_client

with get_traced_sync_client(base_url="http://api.example.com") as client:
    response = client.get("/users")
```

#### `traced_get(url, **kwargs)` / `traced_post(url, **kwargs)`

Convenience functions for single HTTP requests.

**Example:**
```python
from libs.core.common.logging import traced_get, traced_post

# GET request
response = await traced_get("http://api.example.com/data")

# POST request
response = await traced_post(
    "http://api.example.com/create",
    json={"name": "test"}
)
```

---

## Best Practices

### ✅ DO

**Use structured context instead of string formatting:**
```python
# Good
log_with_context(logger, "INFO", "Order placed", order_id=order_id, symbol=symbol)

# Bad
logger.info(f"Order placed: {order_id} for {symbol}")
```

**Log at appropriate levels:**
- `DEBUG`: Detailed diagnostic information
- `INFO`: General informational messages
- `WARNING`: Warning messages (degraded state, but still functional)
- `ERROR`: Error messages (operation failed, but service continues)
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

**Use trace ID middleware:**
```python
# Every FastAPI service should have this
add_trace_id_middleware(app)
```

**Use traced HTTP clients for service-to-service calls:**
```python
# Propagates trace ID automatically
async with get_traced_client() as client:
    response = await client.get("http://other-service/api")
```

### ❌ DON'T

**Don't log sensitive data:**

⚠️ **CRITICAL:** Automated sanitization is NOT yet implemented. You MUST manually avoid logging:
- API keys, tokens, passwords
- Account numbers, SSNs, credit cards
- PII (names, emails, addresses)
- Any other sensitive data

```python
# Bad - logs API keys
logger.info(f"Using API key: {api_key}")

# Good - sanitized
log_with_context(logger, "INFO", "Using API key", key_prefix=api_key[:4])
```

**Don't use string concatenation in log messages:**
```python
# Bad
logger.info("User " + user_id + " placed order for " + str(quantity) + " shares")

# Good
log_with_context(logger, "INFO", "User placed order", user_id=user_id, quantity=quantity)
```

**Don't swallow exceptions without logging:**
```python
# Bad
try:
    risky_operation()
except Exception:
    pass  # Silent failure

# Good
try:
    risky_operation()
except Exception as e:
    logger.exception("Operation failed", operation="risky_operation")
    raise
```

**Don't create multiple loggers in the same module:**
```python
# Bad
logger1 = get_logger("custom1")
logger2 = get_logger("custom2")

# Good - one logger per module
logger = get_logger(__name__)
```

---

## Troubleshooting

### Logs Not Appearing in Grafana

**Check service is logging JSON:**
```bash
docker logs trading_platform_my_service
```
Should show JSON formatted logs.

**Verify Promtail is running:**
```bash
docker ps | grep promtail
```

**Check docker labels:**
```bash
docker inspect trading_platform_my_service | grep logging
```
Should show: `"logging": "promtail"`

**Test Loki directly:**
```bash
curl http://localhost:3100/loki/api/v1/labels
```

### Trace IDs Not Propagating

**Ensure middleware is added:**
```python
add_trace_id_middleware(app)  # Must be called at startup
```

**Use traced HTTP clients:**
```python
# Not this
import httpx
async with httpx.AsyncClient() as client:
    ...

# Use this
from libs.core.common.logging import get_traced_client
async with get_traced_client() as client:
    ...
```

**Verify header in requests:**
```bash
curl -H "X-Trace-ID: test-123" http://localhost:8000/api
```

Then query in Grafana:
```logql
{job="docker"} | json | trace_id="test-123"
```

### Performance Issues

**Reduce log level in production:**
```python
# Development
configure_logging("my_service", log_level="DEBUG")

# Production
configure_logging("my_service", log_level="INFO")
```

**Avoid logging in tight loops:**
```python
# Bad
for item in items:
    logger.info(f"Processing {item}")  # 1000s of logs

# Good
logger.info(f"Processing {len(items)} items")
# ... process ...
logger.info(f"Completed processing {len(items)} items")
```

---

## Examples

### Example 1: Trading Signal Generation

```python
from libs.core.common.logging import configure_logging, log_with_context, get_logger

# Configure at startup
logger = configure_logging("signal_service", log_level="INFO")

# Get module logger
logger = get_logger(__name__)

def generate_signals(symbols: list[str]):
    """Generate trading signals for symbols."""
    log_with_context(
        logger,
        "INFO",
        "Starting signal generation",
        symbol_count=len(symbols),
        strategy="alpha_baseline"
    )

    signals = []
    for symbol in symbols:
        try:
            signal = compute_signal(symbol)
            signals.append(signal)

            log_with_context(
                logger,
                "DEBUG",
                "Signal generated",
                symbol=symbol,
                signal_strength=signal.strength,
                direction=signal.direction
            )
        except Exception as e:
            log_with_context(
                logger,
                "ERROR",
                "Signal generation failed",
                symbol=symbol,
                error_type=type(e).__name__,
                error_message=str(e)
            )
            logger.exception("Full traceback")

    log_with_context(
        logger,
        "INFO",
        "Signal generation complete",
        signals_generated=len(signals),
        symbols_requested=len(symbols)
    )

    return signals
```

### Example 2: Service-to-Service Call

```python
from libs.core.common.logging import get_traced_client, log_with_context, get_logger

logger = get_logger(__name__)

async def submit_order(order_data: dict):
    """Submit order to execution gateway."""
    log_with_context(
        logger,
        "INFO",
        "Submitting order to execution gateway",
        symbol=order_data["symbol"],
        quantity=order_data["quantity"]
    )

    try:
        async with get_traced_client(base_url="http://execution_gateway:8001") as client:
            response = await client.post("/orders", json=order_data)

            if response.status_code == 200:
                result = response.json()
                log_with_context(
                    logger,
                    "INFO",
                    "Order submitted successfully",
                    order_id=result["order_id"],
                    symbol=order_data["symbol"]
                )
                return result
            else:
                log_with_context(
                    logger,
                    "ERROR",
                    "Order submission failed",
                    status_code=response.status_code,
                    response_body=response.text
                )
                raise Exception(f"Order submission failed: {response.text}")

    except Exception as e:
        log_with_context(
            logger,
            "ERROR",
            "Order submission exception",
            symbol=order_data["symbol"],
            error_type=type(e).__name__
        )
        logger.exception("Full traceback")
        raise
```

### Example 3: Manual Trace ID Management

```python
from libs.core.common.logging import set_trace_id, get_trace_id, LogContext, get_logger

logger = get_logger(__name__)

# Option 1: Manual set/clear
set_trace_id("custom-trace-123")
logger.info("This log has trace_id='custom-trace-123'")
clear_trace_id()

# Option 2: Context manager (auto cleanup)
with LogContext("batch-job-456"):
    logger.info("Processing batch job")
    # trace_id='batch-job-456'

    process_items()
    # Still trace_id='batch-job-456'

# Automatically cleared after context exit

# Option 3: Preserve existing trace ID
original_trace = get_trace_id()
set_trace_id("temporary-trace")
logger.info("Temporary trace")
set_trace_id(original_trace)  # Restore
```

---

## Further Reading

- [Logging Queries Runbook](../RUNBOOKS/logging-queries.md) - Common LogQL queries
- [ADR-0005](../ADRs/0005-centralized-logging-architecture.md) - Architecture decisions
- [Grafana Loki Documentation](https://grafana.com/docs/loki/)
- [LogQL Language](https://grafana.com/docs/loki/latest/logql/)
- Implementation: `libs/common/logging/`
- Tests: `tests/libs/common/logging/`

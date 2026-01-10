# health

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `HealthClient` | service_urls, timeouts | client | Fetch `/health` from services with caching. |
| `ServiceHealthResponse` | fields | model | Normalized health response model. |
| `PrometheusClient` | prometheus_url | client | Query Prometheus latency metrics. |
| `LatencyMetrics` | fields | model | P50/P95/P99 latency metrics with staleness. |

## Behavioral Contracts
### HealthClient.check_service(service_name) -> ServiceHealthResponse
**Purpose:** Retrieve a service health response with graceful degradation.

**Preconditions:**
- `service_urls` contains service name.

**Postconditions:**
- Returns live response or cached stale response if unavailable.

**Behavior:**
1. HTTP GET `{service_url}/health` with timeout.
2. Normalize response into `ServiceHealthResponse`.
3. Cache response for fallback on errors.

**Raises:**
- Does not raise on HTTP errors; returns `status="stale"` or `status="unknown"`.

### PrometheusClient.get_service_latencies() -> (dict, is_stale, stale_age)
**Purpose:** Fetch latency percentiles from Prometheus with caching.

**Preconditions:**
- Prometheus is reachable.

**Postconditions:**
- Returns metrics dict, with staleness flags if fallback used.

**Behavior:**
1. Query histogram_quantile for configured metrics.
2. If all failed, return stale cached metrics when available.

**Raises:**
- Errors are logged; returns `None` latencies when unavailable.

### Invariants
- Stale responses are flagged with `is_stale=True` and `stale_age_seconds`.

### State Machine (if stateful)
```
[Fresh Cache] --> [Stale Cache] --> [Refreshed]
      |               ^
      +---------------+ (errors)
```
- **States:** fresh, stale, refreshed.
- **Transitions:** cache expiry and successful refresh.

## Data Flow
```
service /health -> HealthClient -> cached response
prometheus -> PrometheusClient -> latency metrics
```
- **Input format:** HTTP JSON responses.
- **Output format:** Pydantic models.
- **Side effects:** In-memory cache updates.

## Usage Examples
### Example 1: Check a service
```python
client = HealthClient({"signal_service": "http://localhost:8001"})
result = await client.check_service("signal_service")
```

### Example 2: Query latency metrics
```python
prom = PrometheusClient("http://localhost:9090")
latencies, is_stale, _ = await prom.get_service_latencies()
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Timeout | slow service | Returns cached stale response if available |
| Unknown service | missing URL | status="unknown" with error field |
| Prometheus down | network error | returns stale cache or None latencies |

## Dependencies
- **Internal:** N/A
- **External:** HTTPX, Prometheus HTTP API

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `timeout_seconds` | No | 5.0 | HTTP timeout for health checks. |
| `cache_ttl_seconds` | No | 30/10 | Health/Prometheus cache TTL. |

## Error Handling
- HTTP and parsing errors are logged and converted to stale/unknown responses.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- No auth; depends on service endpoints being protected upstream.

## Testing
- **Test Files:** `tests/libs/health/`
- **Run Tests:** `pytest tests/libs/health -v`
- **Coverage:** N/A

## Related Specs
- `signal_service.md`
- `execution_gateway.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09 (P5T10: removed unused variable in health_client.py)
- **Source Files:** `libs/health/__init__.py`, `libs/health/health_client.py`, `libs/health/prometheus_client.py`
- **ADRs:** N/A

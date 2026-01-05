# redis_client

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `RedisClient` | host, port, db, password? | instance | Redis connection manager with retries. |
| `FeatureCache` | redis_client | instance | Feature cache with TTL. |
| `EventPublisher` | redis_client | instance | Publish events to Redis channels. |
| `SignalEvent` | fields | model | Signal event payload. |
| `OrderEvent` | fields | model | Order event payload. |
| `PositionEvent` | fields | model | Position event payload. |
| `FallbackBuffer` | path | instance | Disk-backed buffer for events. |
| `RedisKeys` | class | constants | Redis key names. |

## Behavioral Contracts
### FeatureCache.get/set
**Purpose:** Cache features by symbol/date with TTL.

### EventPublisher.publish
**Purpose:** Publish JSON events to Redis channels.

### Invariants
- Cache keys are stable per symbol/date.
- Fallback buffer preserves event order when Redis unavailable.

## Data Flow
```
producer -> EventPublisher -> Redis channel -> subscribers
```
- **Input format:** event models and feature dicts.
- **Output format:** JSON payloads.
- **Side effects:** Redis writes, optional disk buffer writes.

## Usage Examples
### Example 1: Feature cache
```python
from libs.redis_client import RedisClient, FeatureCache

client = RedisClient(host="localhost", port=6379)
cache = FeatureCache(client)
cache.set("AAPL", "2025-01-02", {"feature": 1.0})
```

### Example 2: Publish event
```python
from libs.redis_client import EventPublisher, SignalEvent

publisher = EventPublisher(client)
publisher.publish(SignalEvent(...))
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Redis down | connection failure | fallback buffer records event. |
| TTL expired | cached feature | cache miss returns None. |
| Invalid payload | non-serializable | raises serialization error. |

## Dependencies
- **Internal:** N/A
- **External:** Redis

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via constructor args. |

## Error Handling
- Raises `RedisConnectionError` for connection failures.

## Security
- Redis credentials handled via secrets manager where used.

## Testing
- **Test Files:** `tests/libs/redis_client/`
- **Run Tests:** `pytest tests/libs/redis_client -v`
- **Coverage:** N/A

## Related Specs
- `../services/signal_service.md`
- `../services/execution_gateway.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-04
- **Source Files:** `libs/redis_client/client.py`, `libs/redis_client/feature_cache.py`, `libs/redis_client/event_publisher.py`, `libs/redis_client/keys.py`
- **ADRs:** `docs/ADRs/0009-redis-integration.md`

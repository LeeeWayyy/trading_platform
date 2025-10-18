# ADR-0009: Redis Integration for Feature Store and Event Bus (T1.2)

**Status:** Proposed
**Date:** 2025-01-18
**Deciders:** System Architect, Product Owner
**Tags:** redis, caching, pub/sub, t1.2, p1, performance

## Context

P0 MVP implemented HTTP-based communication between services (T3↔T5↔T4). While functional, this architecture has several limitations that become apparent in production use:

### Current State (P0)

**Service Communication:**
- Orchestrator (T5) calls Signal Service (T3) via HTTP
- Orchestrator (T5) calls Execution Gateway (T4) via HTTP
- All communication is synchronous request/response
- No caching or event-driven patterns

**Feature Generation (T3 Signal Service):**
```python
# apps/signal_service/signal_generator.py
def generate_signals(symbols, as_of_date):
    # 1. Load data from Parquet files (10-50ms per symbol)
    # 2. Generate Alpha158 features (expensive computation)
    # 3. Run model prediction (1-5ms)
    # 4. Return signals
```

**Performance Characteristics:**
- Feature generation: 10-50ms per symbol (I/O bound)
- Model prediction: 1-5ms (CPU bound)
- **Total latency: 50-200ms for 5 symbols**
- Features recalculated on every request (no caching)

**Orchestration Pattern:**
```python
# apps/orchestrator/orchestrator.py
async def run(symbols, strategy_id, as_of_date):
    # Step 1: Fetch signals (HTTP request to T3)
    signals = await signal_client.generate_signals(symbols)

    # Step 2: Map signals to orders
    orders = self._map_signals_to_orders(signals)

    # Step 3: Submit orders (HTTP request to T4)
    results = await execution_client.submit_orders(orders)
```

### Identified Pain Points

1. **Feature Generation Latency**: Features recalculated for every request even when data hasn't changed
2. **No Event-Driven Architecture**: Orchestrator must poll or actively trigger workflows
3. **No Real-Time Notifications**: Services can't notify each other of state changes
4. **Scalability Concerns**: HTTP request/response doesn't scale to real-time scenarios
5. **Resource Inefficiency**: CPU wasted regenerating identical features

### P1 T1.2 Requirements

From `docs/TASKS/P1_PLANNING.md`:

**Use Case 1: Online Feature Store**
- Cache generated features in Redis with TTL
- Signal service retrieves from cache before regenerating
- 50%+ latency reduction expected

**Use Case 2: Event Bus for Orchestration**
- Signal service publishes events when signals ready
- Orchestrator subscribes and reacts to events
- Enables real-time and scheduled workflows

### Constraints

1. **No Breaking Changes**: Existing HTTP APIs must continue to work
2. **Gradual Migration**: Redis should be optional initially
3. **Production Readiness**: Must handle Redis failures gracefully
4. **Performance Target**: Feature cache hit should be < 10ms
5. **Testing**: Must support mocking Redis for tests

## Decision

We will integrate Redis into the trading platform with two primary use cases:

### 1. Online Feature Store (Caching Layer)

**Decision:** Cache generated features in Redis with time-based expiration

**Implementation Strategy:**

```python
# libs/redis_client/feature_cache.py

class FeatureCache:
    """
    Redis-backed cache for Alpha158 features.

    Key Format:
        features:{symbol}:{date} -> JSON serialized features

    Example:
        features:AAPL:2025-01-17 -> {"feature_1": 0.123, ...}

    TTL: 1 hour (features are immutable for a given date)
    """

    def __init__(self, redis_client: Redis, ttl: int = 3600):
        self.redis = redis_client
        self.ttl = ttl

    def get(self, symbol: str, date: str) -> Optional[Dict[str, float]]:
        """
        Retrieve cached features for symbol on date.

        Returns None if not cached or expired.
        """
        key = f"features:{symbol}:{date}"
        data = self.redis.get(key)

        if data:
            return json.loads(data)
        return None

    def set(self, symbol: str, date: str, features: Dict[str, float]) -> None:
        """
        Cache features with TTL.

        TTL ensures stale data doesn't persist indefinitely.
        """
        key = f"features:{symbol}:{date}"
        self.redis.setex(key, self.ttl, json.dumps(features))

    def invalidate(self, symbol: str, date: str) -> None:
        """
        Invalidate cached features (for data corrections).
        """
        key = f"features:{symbol}:{date}"
        self.redis.delete(key)
```

**Integration with Signal Service:**

```python
# apps/signal_service/signal_generator.py (updated)

class SignalGenerator:
    def __init__(self, model_registry, data_dir, feature_cache=None):
        self.model_registry = model_registry
        self.data_provider = DataProvider(data_dir)
        self.feature_cache = feature_cache  # Optional Redis cache

    def generate_signals(self, symbols, as_of_date):
        features_list = []

        for symbol in symbols:
            # Try cache first (if enabled)
            if self.feature_cache:
                cached_features = self.feature_cache.get(symbol, as_of_date)
                if cached_features:
                    logger.debug(f"Cache HIT: {symbol} on {as_of_date}")
                    features_list.append(cached_features)
                    continue

            # Cache MISS: Generate features
            logger.debug(f"Cache MISS: {symbol} on {as_of_date}")
            features = self._generate_features(symbol, as_of_date)

            # Cache for future requests
            if self.feature_cache:
                self.feature_cache.set(symbol, as_of_date, features)

            features_list.append(features)

        # Run model predictions on features
        predictions = self.model_registry.predict(features_list)
        return self._build_signals(predictions)
```

**Performance Impact:**
- Cache HIT: ~5ms (Redis get + JSON decode)
- Cache MISS: ~50ms (Parquet read + feature generation + Redis set)
- **Expected improvement: 10x faster for repeated requests**

**Rationale:**
- Features are **deterministic**: Same symbol + date = same features
- Features are **immutable**: Historical data doesn't change (except corrections)
- TTL handles edge cases (data corrections, quarantined data)
- Cache is optional: service works without Redis (graceful degradation)

---

### 2. Event Bus for Orchestration (Pub/Sub)

**Decision:** Use Redis Pub/Sub for event-driven orchestration

**Event Schema:**

```python
# libs/redis_client/events.py

class SignalEvent(BaseModel):
    """
    Event published when signals are generated.

    Published to channel: signals.generated
    """
    event_type: str = "signals.generated"
    timestamp: datetime
    strategy_id: str
    symbols: List[str]
    num_signals: int
    as_of_date: str

    # Example:
    # {
    #   "event_type": "signals.generated",
    #   "timestamp": "2025-01-18T09:00:00Z",
    #   "strategy_id": "alpha_baseline",
    #   "symbols": ["AAPL", "MSFT", "GOOGL"],
    #   "num_signals": 3,
    #   "as_of_date": "2025-01-17"
    # }


class OrderEvent(BaseModel):
    """
    Event published when orders are executed.

    Published to channel: orders.executed
    """
    event_type: str = "orders.executed"
    timestamp: datetime
    run_id: str
    num_orders: int
    num_accepted: int
    num_rejected: int

    # Example:
    # {
    #   "event_type": "orders.executed",
    #   "timestamp": "2025-01-18T09:01:00Z",
    #   "run_id": "550e8400-e29b-41d4-a716-446655440000",
    #   "num_orders": 3,
    #   "num_accepted": 3,
    #   "num_rejected": 0
    # }
```

**Publisher: Signal Service**

```python
# apps/signal_service/main.py (updated)

@app.post("/api/v1/signals/generate")
async def generate_signals(request: SignalRequest):
    # Generate signals (existing logic)
    signals = signal_generator.generate_signals(
        symbols=request.symbols,
        as_of_date=as_of_date
    )

    # Publish event (new)
    if redis_client:
        event = SignalEvent(
            timestamp=datetime.now(timezone.utc),
            strategy_id=settings.default_strategy,
            symbols=request.symbols,
            num_signals=len(signals),
            as_of_date=as_of_date.isoformat()
        )
        redis_client.publish('signals.generated', event.model_dump_json())
        logger.info(f"Published SignalEvent: {len(signals)} signals")

    return SignalResponse(signals=signals, metadata=...)
```

**Subscriber: Orchestrator (Optional Real-Time Mode)**

```python
# apps/orchestrator/event_subscriber.py (new)

class OrchestrationEventSubscriber:
    """
    Subscribes to Redis events and triggers orchestration.

    This enables real-time orchestration when signals are generated,
    rather than relying on scheduled cron jobs.
    """

    def __init__(self, redis_client, orchestrator):
        self.redis = redis_client
        self.orchestrator = orchestrator
        self.pubsub = redis_client.pubsub()

    async def start(self):
        """
        Start listening for events.

        Runs in background task (similar to model reload task).
        """
        self.pubsub.subscribe('signals.generated')
        logger.info("Event subscriber started")

        for message in self.pubsub.listen():
            if message['type'] == 'message':
                await self._handle_signal_event(message['data'])

    async def _handle_signal_event(self, data: bytes):
        """
        Handle signal.generated event.

        Triggers orchestration run automatically.
        """
        event = SignalEvent.model_validate_json(data)
        logger.info(f"Received SignalEvent: {event.num_signals} signals")

        # Trigger orchestration (async)
        try:
            result = await self.orchestrator.run(
                symbols=event.symbols,
                strategy_id=event.strategy_id,
                as_of_date=event.as_of_date
            )
            logger.info(f"Auto-orchestration completed: {result.run_id}")
        except Exception as e:
            logger.error(f"Auto-orchestration failed: {e}")
```

**Channel Design:**

| Channel | Publisher | Subscriber | Purpose |
|---------|-----------|------------|---------|
| `signals.generated` | T3 Signal Service | T5 Orchestrator | Notify when signals ready |
| `orders.executed` | T4 Execution Gateway | T5 Orchestrator | Notify when orders filled |
| `positions.updated` | T4 Execution Gateway | Monitoring/Alerts | Notify position changes |

**Rationale:**
- **Decoupling**: Services don't need to know about each other's schedules
- **Real-Time**: Enables sub-second reaction times
- **Scalability**: Multiple subscribers can listen to same events
- **Observability**: All events are logged and traceable
- **Optional**: Pub/Sub is additive, doesn't replace HTTP APIs

---

### 3. Redis Client Library

**Decision:** Create unified Redis client in `libs/redis_client/`

**Module Structure:**

```
libs/redis_client/
├── __init__.py              # Public API
├── client.py                # Redis connection manager
├── feature_cache.py         # Feature caching (Use Case 1)
├── event_publisher.py       # Event publishing
├── event_subscriber.py      # Event subscription
└── events.py                # Event schemas (Pydantic models)
```

**Connection Manager:**

```python
# libs/redis_client/client.py

import redis
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential

class RedisClient:
    """
    Redis connection manager with retry logic.

    Handles connection pooling, health checks, and graceful failures.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        decode_responses: bool = True
    ):
        self.host = host
        self.port = port
        self.db = db

        # Connection pool (thread-safe)
        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=decode_responses,
            max_connections=10
        )

        self._client = redis.Redis(connection_pool=self.pool)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    def get(self, key: str) -> Optional[str]:
        """Get value with retry logic."""
        return self._client.get(key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        reraise=True
    )
    def setex(self, key: str, ttl: int, value: str) -> None:
        """Set value with TTL and retry logic."""
        self._client.setex(key, ttl, value)

    def health_check(self) -> bool:
        """
        Check Redis connectivity.

        Returns True if Redis is reachable, False otherwise.
        """
        try:
            self._client.ping()
            return True
        except redis.ConnectionError:
            return False

    def close(self):
        """Close connection pool."""
        self.pool.disconnect()
```

**Configuration:**

```python
# Environment variables (.env)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=          # Optional
REDIS_ENABLED=true       # Feature flag
```

**Graceful Degradation:**

```python
# apps/signal_service/main.py

# Initialize Redis (optional)
redis_client = None
feature_cache = None

if os.getenv("REDIS_ENABLED", "false").lower() == "true":
    try:
        redis_client = RedisClient(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            db=int(os.getenv("REDIS_DB", "0"))
        )

        if redis_client.health_check():
            feature_cache = FeatureCache(redis_client)
            logger.info("Redis feature cache enabled")
        else:
            logger.warning("Redis unreachable, feature cache disabled")
    except Exception as e:
        logger.warning(f"Redis initialization failed: {e}, continuing without cache")
```

**Rationale:**
- **Centralized**: All Redis logic in one library
- **Retry Logic**: Handles transient network errors
- **Health Checks**: Services can detect Redis failures
- **Optional**: Services work without Redis (fallback to HTTP-only mode)
- **Testable**: Easy to mock for unit tests

---

## Implementation Plan

### Phase 1: Redis Client Library (Day 1)

**Tasks:**
1. Create `libs/redis_client/` module
2. Implement `RedisClient` with connection pooling
3. Implement `FeatureCache` class
4. Add health check endpoint
5. Add unit tests (mocking Redis)

**Deliverables:**
- `libs/redis_client/client.py`
- `libs/redis_client/feature_cache.py`
- `tests/test_redis_client.py` (100% coverage)

**Testing Strategy:**
```python
# tests/test_redis_client.py
import pytest
from unittest.mock import Mock, patch
from libs.redis_client import RedisClient, FeatureCache

@pytest.fixture
def mock_redis():
    return Mock()

def test_feature_cache_get_hit(mock_redis):
    """Test cache hit returns cached features."""
    mock_redis.get.return_value = '{"feature_1": 0.5}'
    cache = FeatureCache(mock_redis)

    result = cache.get("AAPL", "2025-01-17")

    assert result == {"feature_1": 0.5}
    mock_redis.get.assert_called_once_with("features:AAPL:2025-01-17")
```

---

### Phase 2: Feature Caching in Signal Service (Day 2)

**Tasks:**
1. Update `apps/signal_service/config.py` with Redis settings
2. Initialize `FeatureCache` in `main.py` lifespan
3. Update `SignalGenerator` to use cache
4. Add cache metrics (hits/misses) to logs
5. Add integration tests

**Modified Files:**
- `apps/signal_service/main.py` (+50 lines)
- `apps/signal_service/signal_generator.py` (+30 lines)
- `apps/signal_service/config.py` (+10 lines)

**Testing Strategy:**
```python
# apps/signal_service/tests/test_signal_cache.py
@pytest.mark.asyncio
async def test_signal_generation_with_cache():
    """Test signals use cached features when available."""
    mock_cache = Mock()
    mock_cache.get.return_value = {"feature_1": 0.5, "feature_2": 0.3}

    generator = SignalGenerator(
        model_registry=mock_registry,
        data_dir=test_data_dir,
        feature_cache=mock_cache
    )

    signals = generator.generate_signals(["AAPL"], "2025-01-17")

    # Should hit cache, not regenerate
    mock_cache.get.assert_called_once()
    assert len(signals) == 1
```

---

### Phase 3: Event Publishing (Day 3)

**Tasks:**
1. Implement `EventPublisher` class
2. Define event schemas (`SignalEvent`, `OrderEvent`)
3. Update Signal Service to publish events
4. Update Execution Gateway to publish events
5. Add event logging and tracing

**Deliverables:**
- `libs/redis_client/event_publisher.py`
- `libs/redis_client/events.py`
- Event publishing in T3 and T4

**Testing Strategy:**
```python
# tests/test_event_publishing.py
def test_signal_event_published():
    """Test signal generation publishes event."""
    mock_redis = Mock()
    publisher = EventPublisher(mock_redis)

    event = SignalEvent(
        timestamp=datetime.now(timezone.utc),
        strategy_id="alpha_baseline",
        symbols=["AAPL"],
        num_signals=1,
        as_of_date="2025-01-17"
    )

    publisher.publish("signals.generated", event)

    mock_redis.publish.assert_called_once()
    args = mock_redis.publish.call_args[0]
    assert args[0] == "signals.generated"
    assert "AAPL" in args[1]
```

---

### Phase 4: Event Subscription (Optional, Day 4)

**Tasks:**
1. Implement `EventSubscriber` class
2. Add background task to Orchestrator
3. Add subscription management (start/stop)
4. Add event replay for missed events
5. Add integration tests

**Deliverables:**
- `libs/redis_client/event_subscriber.py`
- `apps/orchestrator/event_handler.py`

**Note:** This phase is optional for P1. Can be deferred to P2 if needed.

---

### Phase 5: Documentation (Day 5)

**Tasks:**
1. Create `docs/CONCEPTS/redis-patterns.md`
2. Update `docs/IMPLEMENTATION_GUIDES/` with Redis setup
3. Add Redis to system architecture diagrams
4. Update API documentation
5. Create troubleshooting guide

**Documentation Outline:**

```markdown
# Redis Integration Patterns

## Architecture

[Diagram: Services with Redis]

## Use Case 1: Feature Caching

### How It Works
1. Signal Service checks cache before regenerating features
2. Cache key format: `features:{symbol}:{date}`
3. TTL: 1 hour (configurable)

### Performance Impact
- Cache HIT: 5ms (10x faster)
- Cache MISS: 50ms (same as before)
- Expected hit rate: 80% (repeated symbols)

## Use Case 2: Event Bus

### Event Flow
1. Signal Service publishes to `signals.generated`
2. Orchestrator subscribes and triggers workflow
3. Execution Gateway publishes to `orders.executed`

### Channel List
- `signals.generated`: Signal generation events
- `orders.executed`: Order execution events
- `positions.updated`: Position change events

## Configuration

### Environment Variables
```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_ENABLED=true
```

### Docker Compose
```yaml
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"
  volumes:
    - redis_data:/data
```

## Monitoring

### Health Checks
```bash
# Check Redis connectivity
curl http://localhost:8001/health | jq .redis_connected

# Redis CLI
redis-cli ping
```

### Cache Metrics
```bash
# Cache hit rate
redis-cli info stats | grep keyspace_hits
redis-cli info stats | grep keyspace_misses
```

## Troubleshooting

### Redis Unreachable
**Symptom:** Services start but feature cache disabled
**Solution:** Check REDIS_HOST and ensure Redis is running

### Cache Not Working
**Symptom:** All requests are cache misses
**Solution:** Check TTL configuration, ensure keys are being set

### Events Not Received
**Symptom:** Subscriber doesn't receive messages
**Solution:** Check channel names match exactly, verify subscription
```

---

## Consequences

### Benefits

1. ✅ **10x Faster Signal Generation** (cache hits)
   - Before: 50ms per symbol (feature generation)
   - After: 5ms per symbol (cache hit)
   - Impact: 50-200ms → 5-25ms for typical request

2. ✅ **Event-Driven Architecture**
   - Services can react to events in real-time
   - Decouples service dependencies
   - Enables complex workflows (P2)

3. ✅ **Reduced CPU Usage**
   - Features cached, not regenerated
   - 80% fewer feature calculations (estimated)

4. ✅ **Scalability Foundation**
   - Redis pub/sub supports multiple subscribers
   - Enables horizontal scaling (multiple orchestrators)
   - Prepares for real-time trading (P1 Phase 1B)

5. ✅ **Backward Compatible**
   - HTTP APIs remain unchanged
   - Redis is optional (feature flag)
   - Graceful degradation if Redis unavailable

6. ✅ **Production Ready**
   - Connection pooling for performance
   - Retry logic for resilience
   - Health checks for monitoring

### Trade-offs

1. ⚠️ **Infrastructure Dependency**
   - Adds Redis to deployment requirements
   - **Mitigation:** Redis is optional, services work without it
   - **Mitigation:** Redis is lightweight (< 100MB memory for this use case)

2. ⚠️ **Cache Invalidation Complexity**
   - Stale features if data corrections happen
   - **Mitigation:** TTL expires cache automatically (1 hour)
   - **Mitigation:** Manual invalidation API for corrections

3. ⚠️ **Event Ordering Not Guaranteed**
   - Redis pub/sub doesn't guarantee order
   - **Mitigation:** Include timestamp in events
   - **Mitigation:** Use event ID for deduplication (P2)

4. ⚠️ **No Event Persistence**
   - Redis pub/sub is fire-and-forget
   - Subscribers miss events if offline
   - **Mitigation:** P1 uses HTTP APIs as fallback
   - **Future (P2):** Use Redis Streams for event replay

5. ⚠️ **Testing Complexity**
   - Need to mock Redis in unit tests
   - Integration tests require Redis instance
   - **Mitigation:** Docker Compose for local testing
   - **Mitigation:** Mock library for unit tests

### Risks

1. **Redis Memory Usage**
   - **Risk:** Cache grows unbounded
   - **Probability:** Low (TTL limits growth)
   - **Impact:** Medium (performance degradation)
   - **Mitigation:** Monitor memory usage, set maxmemory policy

2. **Cache Stampede**
   - **Risk:** Many requests regenerate features simultaneously
   - **Probability:** Low (features are immutable for a date)
   - **Impact:** Low (same as current behavior)
   - **Mitigation:** Lock-based cache warming (P2 enhancement)

3. **Redis Single Point of Failure**
   - **Risk:** Redis outage breaks caching
   - **Probability:** Low (Redis is very stable)
   - **Impact:** Low (graceful fallback to HTTP-only)
   - **Mitigation:** Redis failover (P2), health checks

## Alternatives Considered

### Alternative 1: In-Memory Cache (Python dict)

**Description:** Use Python dictionary for caching instead of Redis

**Pros:**
- No external dependency
- Simpler implementation
- Faster (no network roundtrip)

**Cons:**
- ❌ Cache not shared across service instances
- ❌ Cache lost on service restart
- ❌ Doesn't enable pub/sub patterns
- ❌ No persistence or TTL management

**Decision:** **Rejected** - Doesn't support multi-instance deployments or pub/sub

---

### Alternative 2: RabbitMQ for Event Bus

**Description:** Use RabbitMQ instead of Redis pub/sub

**Pros:**
- Guaranteed delivery (persistent queues)
- Dead letter queues for failed messages
- Better ordering guarantees
- Built-in retry logic

**Cons:**
- ❌ Heavier infrastructure (separate service)
- ❌ More complex to operate
- ❌ Overkill for P1 use case (simple notifications)
- ❌ Still need Redis for caching

**Decision:** **Deferred to P2** - Redis pub/sub sufficient for P1 notifications

---

### Alternative 3: PostgreSQL for Feature Cache

**Description:** Store features in PostgreSQL instead of Redis

**Pros:**
- Already have PostgreSQL running
- ACID guarantees
- Persistent storage

**Cons:**
- ❌ 10-50ms latency (vs 5ms for Redis)
- ❌ Doesn't support pub/sub
- ❌ More load on database
- ❌ Slower than in-memory cache

**Decision:** **Rejected** - PostgreSQL is for persistent data, not caching

---

### Alternative 4: Memcached for Caching

**Description:** Use Memcached instead of Redis for feature cache

**Pros:**
- Simpler than Redis (cache-only)
- Slightly faster for simple get/set

**Cons:**
- ❌ No pub/sub support
- ❌ No persistence
- ❌ Less feature-rich
- ❌ Need separate service for pub/sub anyway

**Decision:** **Rejected** - Redis provides both caching and pub/sub

---

## Success Metrics

### Performance Metrics
- [ ] Cache hit rate > 70% (target: 80%)
- [ ] Cache hit latency < 10ms (target: 5ms)
- [ ] Feature generation latency reduced by 50%+ on cache hit
- [ ] Signal generation endpoint P95 latency < 50ms (with cache)

### Reliability Metrics
- [ ] Services start successfully without Redis
- [ ] Services degrade gracefully if Redis fails
- [ ] Health check reports Redis connectivity
- [ ] Zero cache-related errors in production

### Event Metrics
- [ ] Events published successfully within 1ms
- [ ] Event payload < 1KB (efficient serialization)
- [ ] Event schema validation (100% valid events)

### Testing Metrics
- [ ] 100% test coverage for Redis client library
- [ ] Integration tests with real Redis instance
- [ ] Load tests with 100 concurrent requests

---

## Related Documents

- [P1_PLANNING.md](../TASKS/P1_PLANNING.md) - T1.2 requirements
- [ADR-0004](./0004-signal-service-architecture.md) - Signal Service architecture
- [ADR-0006](./0006-orchestrator-architecture.md) - Orchestrator architecture
- [Redis Best Practices](https://redis.io/docs/management/optimization/)

---

**Last Updated:** 2025-01-18
**Status:** Proposed (awaiting approval)
**Next Review:** After implementation completion

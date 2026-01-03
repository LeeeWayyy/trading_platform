# Redis Integration Patterns

**Related**: [ADR-0009](../ADRs/0009-redis-integration.md), [P1.1T2 Implementation Guide](../ARCHIVE/TASKS_HISTORY/P1T1_DONE.md)

---

## Table of Contents

1. [Overview](#overview)
2. [Pattern 1: Feature Caching](#pattern-1-feature-caching)
3. [Pattern 2: Event-Driven Communication](#pattern-2-event-driven-communication)
4. [Pattern 3: Graceful Degradation](#pattern-3-graceful-degradation)
5. [Pattern 4: Health Monitoring](#pattern-4-health-monitoring)
6. [Anti-Patterns to Avoid](#anti-patterns-to-avoid)
7. [Performance Optimization](#performance-optimization)
8. [Production Considerations](#production-considerations)

---

## Overview

Redis is used in the trading platform for two primary patterns:

1. **Cache-Aside Pattern**: Feature caching to reduce computation time
2. **Publish-Subscribe Pattern**: Event-driven communication between services

Both patterns follow the principle of **graceful degradation** - the system works without Redis, but performs better with it.

---

## Pattern 1: Feature Caching

### Cache-Aside Pattern

The application checks the cache before generating features. On cache miss, it generates features and populates the cache for future requests.

```
┌─────────────────────────────────────────────┐
│           Cache-Aside Flow                   │
├─────────────────────────────────────────────┤
│                                              │
│  1. Application requests features            │
│     ↓                                        │
│  2. Check Redis cache                        │
│     ├─ HIT  → Return cached features (5ms)  │
│     └─ MISS → Generate features (50ms)      │
│                ↓                             │
│  3. Store in cache for next time             │
│     ↓                                        │
│  4. Return features to caller                │
│                                              │
└─────────────────────────────────────────────┘
```

### Implementation

```python
from libs.redis_client import FeatureCache

class SignalGenerator:
    def __init__(self, model_registry, data_dir, feature_cache=None):
        self.feature_cache = feature_cache

    def generate_signals(self, symbols, as_of_date):
        features_list = []

        for symbol in symbols:
            # STEP 1: Try cache first
            if self.feature_cache:
                cached = self.feature_cache.get(symbol, date_str)
                if cached:
                    logger.debug(f"Cache HIT: {symbol}")
                    features_list.append(cached)
                    continue  # Skip generation

            # STEP 2: Cache MISS - generate features
            logger.debug(f"Cache MISS: {symbol}")
            features = self._generate_alpha158_features(symbol, as_of_date)

            # STEP 3: Cache for future requests
            if self.feature_cache:
                self.feature_cache.set(symbol, date_str, features)

            features_list.append(features)

        # STEP 4: Run model predictions
        return self._predict(features_list)
```

### Key Characteristics

**Features are Deterministic:**
- Same symbol + date = same features (always)
- Perfect for caching (no consistency issues)

**TTL Strategy:**
- Default: 1 hour (3600 seconds)
- Reasoning: Features don't change intraday, but data corrections can happen
- TTL ensures stale data doesn't persist indefinitely

**Key Format:**
```
features:{symbol}:{date}

Examples:
  features:AAPL:2025-01-17
  features:MSFT:2025-01-18
  features:GOOGL:2025-01-17
```

### Performance Analysis

| Scenario | Latency | Calculation |
|----------|---------|-------------|
| **Cache MISS** | ~50ms | Parquet read (40ms) + computation (10ms) |
| **Cache HIT** | ~5ms | Redis GET (0.2ms) + JSON parse (0.5ms) + overhead |
| **Speedup** | **10x** | 50ms / 5ms = 10x faster |

**Real-World Example** (5 symbols, 80% cache hit rate):
```
Without cache: 5 symbols × 50ms = 250ms
With cache:    4 HITs × 5ms + 1 MISS × 50ms = 20ms + 50ms = 70ms
Improvement:   (250ms - 70ms) / 250ms = 72% faster
```

### Cache Invalidation

**When to Invalidate**:
1. Data correction detected (rare)
2. Quarantine status changes
3. Manual invalidation for debugging

**How to Invalidate**:
```python
# Invalidate specific symbol/date
cache.invalidate("AAPL", "2025-01-17")

# Invalidate all features for a symbol
for key in redis_client._client.keys("features:AAPL:*"):
    redis_client.delete(key)

# Clear all cached features (use with caution!)
redis_client._client.flushdb()
```

---

## Pattern 2: Event-Driven Communication

### Publish-Subscribe Pattern

Services publish events to Redis channels when significant actions occur. Subscribers receive events in real-time and can react accordingly.

```
┌─────────────────────────────────────────────────────────┐
│              Publish-Subscribe Flow                      │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  T3 Signal Service                                       │
│    ↓                                                     │
│  1. Generate signals                                     │
│    ↓                                                     │
│  2. PUBLISH SignalEvent to "signals.generated"          │
│    ↓                                                     │
│  ┌──────────────────────────┐                           │
│  │   Redis Pub/Sub Broker   │                           │
│  └──────────┬───────────────┘                           │
│             │ Broadcast to all subscribers               │
│             ↓                                            │
│  ┌──────────────────────────┐                           │
│  │  T5 Orchestrator         │ SUBSCRIBE "signals.*"    │
│  │  (Optional subscriber)   │ React to events          │
│  └──────────────────────────┘                           │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### Event Schema Design

All events follow a common structure:

```python
from datetime import datetime, timezone
from pydantic import BaseModel

class BaseEvent(BaseModel):
    event_type: str          # Event identifier
    timestamp: datetime      # When event occurred (UTC)
    # ... event-specific fields ...
```

**Example: SignalEvent**
```python
{
    "event_type": "signals.generated",
    "timestamp": "2025-01-18T09:00:00+00:00",
    "strategy_id": "alpha_baseline",
    "symbols": ["AAPL", "MSFT", "GOOGL"],
    "num_signals": 3,
    "as_of_date": "2025-01-17"
}
```

### Channel Naming Convention

```
{entity}.{action}

Examples:
  signals.generated    - Signals were generated
  orders.executed      - Orders were executed
  positions.updated    - Positions changed
  models.reloaded      - Model was hot-reloaded (future)
```

### Publishing Events

```python
from datetime import datetime, timezone
from libs.redis_client import EventPublisher
from libs.redis_client.events import SignalEvent

# Initialize publisher
publisher = EventPublisher(redis_client)

# Create event
event = SignalEvent(
    timestamp=datetime.now(timezone.utc),  # MUST be UTC
    strategy_id="alpha_baseline",
    symbols=["AAPL", "MSFT"],
    num_signals=2,
    as_of_date="2025-01-17"
)

# Publish to channel
num_subscribers = publisher.publish_signal_event(event)

# Log for observability
logger.info(f"Published SignalEvent to {num_subscribers} subscribers")
```

### Subscribing to Events

```python
# Create subscriber
pubsub = redis_client.pubsub()
pubsub.subscribe("signals.generated")

# Listen for events
for message in pubsub.listen():
    if message['type'] == 'message':
        # Parse event
        event_data = json.loads(message['data'])
        event = SignalEvent(**event_data)

        # React to event
        logger.info(f"Received signals for {len(event.symbols)} symbols")
        trigger_orchestration(event)
```

### Event Delivery Guarantees

**Redis Pub/Sub Characteristics:**

✅ **Fire-and-Forget**: Events are delivered immediately, no persistence
✅ **Fan-Out**: All active subscribers receive the event
✅ **Low Latency**: Sub-millisecond delivery within localhost

❌ **No Persistence**: Events not stored (missed if subscriber offline)
❌ **No Ordering**: Multiple events may arrive out of order
❌ **No Acknowledgment**: No confirmation of receipt

**When This Is Acceptable** (P1):
- Events are notifications, not critical commands
- HTTP APIs remain primary communication method
- Pub/sub is an optimization, not a requirement

**Future Enhancement** (P2):
- Use Redis Streams for persistence
- Implement event replay from timestamp
- Add consumer groups for scalability

---

## Pattern 3: Graceful Degradation

### Design Philosophy

Redis enhances performance but is **not required** for core functionality.

```python
# Initialize Redis (optional)
redis_client = None
feature_cache = None

if REDIS_ENABLED:
    try:
        redis_client = RedisClient(host=REDIS_HOST)
        if redis_client.health_check():
            feature_cache = FeatureCache(redis_client)
            logger.info("✓ Redis enabled")
        else:
            logger.warning("✗ Redis unreachable, continuing without cache")
    except Exception as e:
        logger.warning(f"✗ Redis failed: {e}, continuing without cache")

# Service works with or without cache
signal_generator = SignalGenerator(
    model_registry=model_registry,
    data_dir=data_dir,
    feature_cache=feature_cache  # None if Redis unavailable
)
```

### Graceful Handling in Code

**Pattern**: Check if feature exists before using

```python
def generate_signals(self, symbols, as_of_date):
    # Graceful: Check if cache exists
    if self.feature_cache:
        cached = self.feature_cache.get(symbol, date)
        if cached:
            return cached

    # Always works: Generate features
    return self._generate_features(symbol, date)
```

**Pattern**: Catch errors and continue

```python
def set_cache(self, symbol, date, features):
    if not self.feature_cache:
        return  # No cache, skip silently

    try:
        self.feature_cache.set(symbol, date, features)
    except RedisError as e:
        # Log error but don't fail request
        logger.warning(f"Cache SET failed: {e}")
```

### Degradation Scenarios

| Scenario | Behavior | Impact |
|----------|----------|--------|
| **Redis not installed** | Services start without cache | No cache benefit |
| **Redis connection fails** | Services continue, log warning | No cache benefit |
| **Redis crashes mid-request** | Request completes, next requests work | Temporary slowdown |
| **Cache full (eviction)** | LRU eviction, new keys cached | Slight hit rate decrease |

---

## Pattern 4: Health Monitoring

### Health Check Implementation

```python
@app.get("/health")
async def health_check():
    """Health check including Redis status."""

    # Check Redis health
    redis_healthy = False
    if redis_client:
        redis_healthy = redis_client.health_check()

    return {
        "status": "healthy" if model_loaded else "unhealthy",
        "model_loaded": model_loaded,
        "redis_enabled": REDIS_ENABLED,
        "redis_connected": redis_healthy,
        "timestamp": datetime.utcnow().isoformat()
    }
```

### Monitoring Metrics

**Cache Performance**:
```bash
# Get cache statistics
redis-cli INFO stats | grep keyspace

keyspace_hits:1000      # Cache HITs
keyspace_misses:200     # Cache MISSes
# Hit rate = 1000 / (1000 + 200) = 83.3%
```

**Memory Usage**:
```bash
# Check memory
redis-cli INFO memory | grep used_memory_human
# used_memory_human:256.00M

# Check key count
redis-cli DBSIZE
# (integer) 500
```

**Connection Health**:
```bash
# Check connected clients
redis-cli INFO clients | grep connected_clients
# connected_clients:3

# Monitor commands in real-time
redis-cli MONITOR
# Live feed of all commands
```

### Alerting Thresholds

**Warning Conditions**:
- Cache hit rate < 50% (expected: 70-80%)
- Used memory > 80% of max
- Connected clients > 50 (expected: 5-10)
- Keyspace errors > 0

**Critical Conditions**:
- Redis unreachable (health check fails)
- Used memory > 95% of max
- Eviction policy not set (risk of OOM)

---

## Anti-Patterns to Avoid

### ❌ Anti-Pattern 1: Caching Mutable Data

**Bad**:
```python
# DON'T cache data that changes frequently
cache.set("current_price:AAPL", current_price, ttl=3600)
```

**Why**: Price changes every millisecond, cache becomes stale immediately

**Good**:
```python
# DO cache immutable/deterministic data
cache.set(f"features:AAPL:{date}", features, ttl=3600)
```

---

### ❌ Anti-Pattern 2: No TTL on Cached Data

**Bad**:
```python
# DON'T cache without TTL
cache.set("features:AAPL:2025-01-17", features)  # No TTL!
```

**Why**: Data persists forever, memory grows unbounded

**Good**:
```python
# DO always set TTL
cache.set("features:AAPL:2025-01-17", features, ttl=3600)
```

---

### ❌ Anti-Pattern 3: Catching All Exceptions Silently

**Bad**:
```python
try:
    cache.set(symbol, date, features)
except:
    pass  # Silent failure, no logging
```

**Why**: Errors are invisible, debugging is impossible

**Good**:
```python
try:
    cache.set(symbol, date, features)
except RedisError as e:
    logger.warning(f"Cache SET failed for {symbol}: {e}")
    # Continue gracefully
```

---

### ❌ Anti-Pattern 4: Using Pub/Sub for Critical Commands

**Bad**:
```python
# DON'T use pub/sub for critical operations
publisher.publish("execute_order", order_data)
# What if no subscribers? Order not executed!
```

**Why**: No delivery guarantee, no acknowledgment

**Good**:
```python
# DO use HTTP for critical operations
response = await http_client.post("/api/v1/orders", order_data)
# Guaranteed delivery, response confirms success

# Optional: Also publish event for monitoring
publisher.publish("orders.submitted", order_event)
```

---

### ❌ Anti-Pattern 5: Storing Large Objects

**Bad**:
```python
# DON'T cache entire DataFrames
cache.set("all_data:AAPL", dataframe.to_json(), ttl=3600)
# 10MB JSON string!
```

**Why**: Memory explosion, slow serialization/deserialization

**Good**:
```python
# DO cache only computed features (small)
features = compute_features(dataframe)  # dict with 158 floats
cache.set("features:AAPL:2025-01-17", features, ttl=3600)
# ~5KB JSON string
```

---

## Performance Optimization

### Optimization 1: Batch Operations

**Problem**: Multiple GET calls are slow

```python
# Slow: 5 round trips to Redis
for symbol in ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"]:
    cache.get(symbol, date)  # 5 × 1ms = 5ms
```

**Solution**: Use pipeline (future enhancement)

```python
# Fast: 1 round trip to Redis
pipe = redis_client._client.pipeline()
for symbol in symbols:
    pipe.get(f"features:{symbol}:{date}")
results = pipe.execute()  # 1ms total
```

---

### Optimization 2: Compression (for large features)

**Problem**: Large JSON strings slow to transfer

```python
# Uncompressed: 50KB JSON
cache.set("features:AAPL:2025-01-17", json.dumps(features))
```

**Solution**: Compress with gzip (future enhancement)

```python
import gzip

# Compressed: 5KB (10x smaller)
compressed = gzip.compress(json.dumps(features).encode())
cache.set("features:AAPL:2025-01-17", compressed)

# Decompress on retrieval
data = cache.get("features:AAPL:2025-01-17")
features = json.loads(gzip.decompress(data))
```

---

### Optimization 3: Connection Pooling

**Already Implemented** ✅

```python
# Connection pool (thread-safe, reuses connections)
pool = ConnectionPool(
    host=host,
    port=port,
    max_connections=10  # Reuse up to 10 connections
)
redis_client = redis.Redis(connection_pool=pool)
```

**Benefit**: Avoid connection overhead (~10ms per connection)

---

## Production Considerations

### Redis Configuration

**redis.conf recommendations**:

```conf
# Memory
maxmemory 512mb
maxmemory-policy volatile-lru  # Evict keys with TTL using LRU

# Persistence (optional for cache)
save ""  # Disable RDB snapshots (cache is ephemeral)
appendonly no  # Disable AOF (cache is ephemeral)

# Performance
tcp-backlog 511
timeout 0  # Don't close idle connections
tcp-keepalive 300

# Security
bind 127.0.0.1  # Only accept local connections
protected-mode yes
requirepass <strong-password>  # Set password
```

### Deployment Architecture

**Development**:
```
┌─────────────────┐
│  Developer Mac  │
│  ├─ Services    │
│  └─ Redis       │
└─────────────────┘
```

**Production** (future):
```
┌──────────────────┐     ┌──────────────────┐
│  Service Cluster │────▶│  Redis Cluster   │
│  (3 nodes)       │     │  (3 nodes + 3    │
│                  │     │   sentinels)     │
└──────────────────┘     └──────────────────┘
         ↓                       ↓
    Auto-scaling            Auto-failover
```

### Monitoring in Production

**Metrics to Track**:
1. Cache hit rate (target: > 70%)
2. Memory usage (alert at 80%)
3. Connection count (alert at 50)
4. Command latency (P99 < 5ms)
5. Evictions per second (should be near 0)

**Tools**:
- Redis MONITOR for live debugging
- Redis Slowlog for slow commands
- Prometheus + Grafana for dashboards
- AlertManager for alerts

---

## Summary

### Redis Usage Patterns in Trading Platform

| Pattern | Use Case | Benefit | Risk |
|---------|----------|---------|------|
| **Cache-Aside** | Feature caching | 10x faster (5ms vs 50ms) | Stale data (mitigated by TTL) |
| **Pub/Sub** | Event notifications | Real-time workflows | No guaranteed delivery |
| **Graceful Degradation** | Optional Redis | Service always works | Slower without cache |
| **Health Monitoring** | Observability | Early problem detection | Requires monitoring setup |

### Key Takeaways

1. **Redis is an optimization, not a requirement** - Services work without it
2. **Cache immutable data only** - Features are deterministic, perfect for caching
3. **Always set TTL** - Prevents unbounded memory growth
4. **Log errors, don't fail** - Graceful degradation on Redis errors
5. **Monitor performance** - Track cache hit rate and memory usage

---

## References

- [ADR-0009: Redis Integration](../ADRs/0009-redis-integration.md) - Architecture decisions
- [P1.1T2 Implementation Guide](../ARCHIVE/TASKS_HISTORY/P1T1_DONE.md) - Step-by-step integration
- [Redis Documentation](https://redis.io/docs/) - Official Redis docs
- [Redis Best Practices](https://redis.io/docs/management/optimization/) - Performance tuning

---

**Last Updated**: 2025-01-18
**Relates To**: T1.2 Redis Integration (P1 Phase 1A)

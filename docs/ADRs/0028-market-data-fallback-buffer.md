# ADR-0028: Market Data Fallback Buffer

**Status:** Accepted
**Date:** 2025-12-17
**Deciders:** AI Assistant (Claude Code), reviewed by Gemini and Codex
**Tags:** market-data, redis, reliability, fallback

## Context

The market data service streams quotes to Redis for consumption by other services. Currently:

1. **No Fallback:** If Redis is unavailable, quotes are lost
2. **Service Blindness:** Signal service goes "blind" without quotes
3. **No Staleness Check:** Consumers don't know if data is fresh

The system needs a fallback mechanism to maintain quote availability during Redis outages.

## Decision

Implement an in-memory ring buffer with HTTP fallback endpoint:

### Components

1. **QuoteBuffer** (`apps/market_data_service/quote_buffer.py`):
   - In-memory ring buffer (last 100 quotes per symbol)
   - Thread-safe concurrent access
   - Automatic old quote eviction

2. **Fallback Endpoint** (`apps/market_data_service/main.py`):
   - `GET /api/v1/quotes/{symbol}/latest` returns buffered quote
   - Includes `timestamp` for staleness detection
   - Returns 404 if no quote available

3. **QuoteClient** (`libs/redis_client/quote_client.py`):
   - Primary: Redis pub/sub subscription
   - Fallback: HTTP call to market data service
   - Staleness policy: Reject quotes older than 60 seconds

4. **Health Status**:
   - Market data service reports "degraded" when operating from buffer only
   - Consumers can check health before trusting quotes

### Key Design Choices

**In-Memory Buffer (Not Persistent):**
- Quotes are ephemeral; persistence adds complexity without benefit
- Buffer survives Redis outages, not service restarts
- Service restart triggers fresh quote stream anyway

**HTTP Fallback (Not Direct Memory):**
- Services already have HTTP client infrastructure
- No shared memory complexity
- Works across process boundaries

**Per-Instance Buffer:**
- Each market data service instance has its own buffer
- In multi-instance deployment, any instance can serve fallback
- Documented limitation: Not synchronized across instances

**Staleness Rejection:**
- 60-second threshold prevents acting on stale data
- Consumers must handle "no fresh quote" gracefully
- Configurable per use case

## Consequences

### Positive

- **Resilience:** Quote availability survives Redis outages
- **Graceful Degradation:** System continues with fallback data
- **Staleness Protection:** Old quotes are rejected, not trusted
- **Simple Implementation:** Ring buffer is straightforward

### Negative

- **Memory Usage:** Buffer consumes memory per symbol (~100 quotes * N symbols)
- **HTTP Latency:** Fallback is slower than Redis pub/sub
- **Instance Isolation:** Buffers not synchronized in multi-instance deployment

### Risks

- **Buffer Overflow:** High-frequency updates may evict quotes too fast
- **Stale Data:** 60-second threshold may be too long for some use cases

### Configuration

```python
QUOTE_BUFFER_SIZE = 100           # quotes per symbol
QUOTE_STALENESS_THRESHOLD_SEC = 60
FALLBACK_ENABLED = True           # default
```

### Health Response

```json
{
  "status": "degraded",
  "redis_connected": false,
  "buffer_active": true,
  "symbols_buffered": 50
}
```

## Related

- [ADR-0010: Realtime Market Data](./0010-realtime-market-data.md)
- [ADR-0009: Redis Integration](./0009-redis-integration.md)
- [BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md](../TASKS/BUGFIX_RELIABILITY_SAFETY_IMPROVEMENTS.md)

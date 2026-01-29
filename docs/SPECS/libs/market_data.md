# market_data

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AlpacaMarketDataStream` | api_key, secret_key, redis_client, event_publisher | client | WebSocket stream for real-time quotes. |
| `QuoteData` | fields | model | Raw quote model with bid/ask and validation. |
| `PriceData` | fields | model | Cache-friendly price model (mid, bid, ask). |
| `MarketDataError` | message | exception | Base error type. |
| `ConnectionError` | message | exception | WebSocket connection errors. |
| `SubscriptionError` | message | exception | Subscription management errors. |

## Behavioral Contracts
### AlpacaMarketDataStream.subscribe_symbols(symbols, source)
**Purpose:** Subscribe to symbols with source ref-counting.

**Preconditions:**
- Alpaca credentials valid.

**Postconditions:**
- Symbols tracked with source set; Alpaca subscribed only once.

**Behavior:**
1. Compute new symbols vs existing.
2. Subscribe new symbols via Alpaca SDK.
3. Track subscription sources to avoid premature unsubscribe.

**Raises:**
- `SubscriptionError` on subscribe failure.

### AlpacaMarketDataStream._handle_quote(...)
**Purpose:** Transform quote, update Redis cache, publish events.

**Preconditions:**
- Quote payload conforms to Alpaca SDK model.

**Postconditions:**
- `PriceData` cached in Redis with TTL.
- `PriceUpdateEvent` published via Redis pub/sub.

**Behavior:**
1. Convert Quote -> `QuoteData` -> `PriceData`.
2. Cache in Redis, publish event.
3. Log and continue on validation errors.

**Raises:**
- Validation errors handled and logged (no crash).

### Invariants
- Ask price must be >= bid price (validated in `QuoteData`).
- Subscription sources must be tracked to avoid accidental unsubscribe.

### State Machine (if stateful)
```
[Disconnected] --> [Connecting] --> [Connected]
      ^                 |
      +-----------------+ (reconnect)
```
- **States:** disconnected, connecting, connected.
- **Transitions:** reconnect attempts on disconnect.

## Data Flow
```
Alpaca WS -> QuoteData -> PriceData -> Redis cache + pubsub
```
- **Input format:** Alpaca `Quote` messages.
- **Output format:** Redis cached `PriceData`, `PriceUpdateEvent`.
- **Side effects:** Redis writes and pub/sub publications.

## Usage Examples
### Example 1: Start stream
```python
stream = AlpacaMarketDataStream(api_key, secret_key, redis_client, publisher)
await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")
await stream.start()
```

### Example 2: Unsubscribe
```python
await stream.unsubscribe_symbols(["AAPL"], source="manual")
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Empty subscription | [] | No-op with warning |
| Crossed market | ask < bid | ValidationError logged; quote skipped |
| Redis down | cache failure | Logs error; streaming continues |

## Dependencies
- **Internal:** `libs.redis_client`
- **External:** Alpaca SDK, Redis

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `price_ttl` | No | 300 | Redis TTL for cached prices (seconds). |
| `max_reconnect_attempts` | No | 10 | Max reconnect attempts. |

## Error Handling
- Connection and subscription errors wrapped as `MarketDataError` subclasses.
- Validation errors logged per message; stream continues.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- Alpaca API keys are required; passed in at runtime.

## Testing
- **Test Files:** N/A (no dedicated library tests found)
- **Run Tests:** N/A
- **Coverage:** N/A

## Related Specs
- `redis_client.md`
- `market_data_service.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-28 (Added provider.py for ADV data endpoint)
- **Source Files:** `libs/data/market_data/__init__.py`, `libs/data/market_data/alpaca_stream.py`, `libs/data/market_data/types.py`, `libs/data/market_data/exceptions.py`, `libs/data/market_data/provider.py`
- **ADRs:** N/A

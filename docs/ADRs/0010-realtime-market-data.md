# ADR-0010: Real-Time Market Data Streaming (P1.2T1)

**Status:** Proposed
**Date:** 2025-10-19
**Deciders:** System Architect, Product Owner
**Tags:** market-data, websocket, real-time, alpaca, redis, p1.2t1, streaming

## Context

P0 MVP and P1 Phase 1A rely exclusively on historical batch data loaded from Parquet files. While functional for backtesting and daily signal generation, this architecture cannot support real-time trading or intraday position monitoring.

### Current State (P0 + P1.1)

**Data Sources:**
- Historical OHLCV data from Parquet files (T1 Data ETL)
- Static data loaded once per `paper_run.py` execution
- No intraday price updates

**Position Monitoring:**
```python
# scripts/paper_run.py
def calculate_pnl(positions):
    # P&L calculated at script execution time only
    # Uses closing prices from previous day
    # No real-time updates during market hours
```

**Signal Generation:**
```python
# apps/signal_service/signal_generator.py
def generate_signals(symbols, as_of_date):
    # Features based on historical data only
    # Cannot react to intraday price movements
    # Signals generated once per day
```

**Limitations:**
1. **Stale P&L Data**: Unrealized P&L uses yesterday's closing prices during market hours
2. **No Intraday Monitoring**: Cannot detect significant price movements in real-time
3. **Delayed Risk Detection**: Risk thresholds checked only once daily
4. **No Live Trading Readiness**: Cannot support real-time order execution
5. **Poor User Experience**: Users must wait until end-of-day for P&L updates

### P1.2T1 Requirements

From `docs/TASKS/P1_PLANNING.md` and `docs/NEXT_TASK.md`:

**Use Case 1: Real-Time Price Streaming**
- Subscribe to live market data from Alpaca via WebSocket
- Stream bid/ask quotes for all active positions
- Update unrealized P&L in real-time (< 100ms latency)

**Use Case 2: Price-Based Alerts** (Future P2)
- Trigger alerts when P&L crosses thresholds
- Notify on significant price movements
- Enable circuit breakers for risk management

**Use Case 3: Intraday Signal Generation** (Future P2)
- Generate signals based on intraday data
- React to market events in real-time
- Support higher-frequency trading strategies

### Constraints

1. **No Breaking Changes**: Existing batch data pipeline must continue to work
2. **Cost Conscious**: Alpaca real-time data has subscription costs
3. **Graceful Degradation**: System must work if WebSocket disconnects
4. **Testing Complexity**: Must support mocking WebSocket for tests
5. **Performance Target**: Price updates processed in < 10ms

### Architectural Questions

1. **Where should WebSocket client live?**
   - New dedicated service vs extending existing service?
   - Centralized vs distributed price streaming?

2. **How to distribute prices to consumers?**
   - Direct WebSocket connections vs centralized distribution?
   - Redis pub/sub vs HTTP polling vs gRPC streaming?

3. **How to handle reconnections?**
   - Automatic retry logic
   - Price data reconciliation after disconnect
   - Missed quote detection

4. **How to cache latest prices?**
   - Redis (shared) vs in-memory (per-service)?
   - TTL strategy for stale price detection?

5. **What data to persist?**
   - Store tick data for analytics?
   - Database choice (TimescaleDB vs InfluxDB vs append-only files)?

---

## Decision

We will implement a dedicated **Market Data Service** that streams real-time prices from Alpaca via WebSocket and distributes them to other services via Redis.

### 1. New Dedicated Service: Market Data Service

**Decision:** Create `apps/market_data_service/` as a new FastAPI service

**Rationale:**
- **Single Responsibility**: Isolates WebSocket complexity and connection management
- **Independent Scaling**: Can scale separately from Signal/Execution services
- **Fault Isolation**: WebSocket failures don't affect other services
- **Clear Ownership**: One service responsible for all external market data
- **Reusability**: Other services can consume data without Alpaca dependencies

**Architecture:**

```
┌──────────────────────────────────────────────────────────────┐
│                      Alpaca Market Data API                   │
│                      (WebSocket: wss://...)                   │
└────────────────────────┬─────────────────────────────────────┘
                         │ WebSocket (live quotes)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│             Market Data Service (NEW)                         │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  AlpacaMarketDataStream                                │  │
│  │  - WebSocket client with auto-reconnect                │  │
│  │  - Quote buffering and deduplication                   │  │
│  │  - Error handling and logging                          │  │
│  └─────────────┬──────────────────────────────────────────┘  │
│                │                                               │
│                ▼                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Price Cache (Redis)                                   │  │
│  │  - Key: price:{symbol}                                 │  │
│  │  - TTL: 5 minutes                                      │  │
│  │  - Fields: bid, ask, timestamp                         │  │
│  └─────────────┬──────────────────────────────────────────┘  │
│                │                                               │
│                ▼                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Event Publisher (Redis pub/sub)                       │  │
│  │  - Channel: price.updated.{symbol}                     │  │
│  │  - Payload: {symbol, price, timestamp}                 │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                         │
                         │ Redis pub/sub
                         ▼
┌──────────────────────────────────────────────────────────────┐
│              Execution Gateway (T4) - UPDATED                 │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Price Subscriber                                      │  │
│  │  - Subscribes to price.updated.*                       │  │
│  │  - Updates in-memory P&L cache                         │  │
│  │  - Triggers alerts (future)                            │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  New Endpoint: GET /api/v1/positions/pnl/realtime            │
│  - Returns P&L with latest prices from Redis                 │
└──────────────────────────────────────────────────────────────┘
```

**Service Structure:**

```python
# apps/market_data_service/main.py

from fastapi import FastAPI
from contextlib import asynccontextmanager
from libs.market_data import AlpacaMarketDataStream
from libs.redis_client import RedisClient

stream: Optional[AlpacaMarketDataStream] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage WebSocket lifecycle."""
    global stream

    # Initialize Redis
    redis_client = RedisClient(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT
    )

    # Initialize Alpaca stream
    stream = AlpacaMarketDataStream(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_SECRET_KEY,
        redis_client=redis_client
    )

    # Start streaming in background
    asyncio.create_task(stream.start())
    logger.info("Market data streaming started")

    yield

    # Cleanup
    await stream.stop()
    logger.info("Market data streaming stopped")

app = FastAPI(lifespan=lifespan)

@app.post("/api/v1/market-data/subscribe")
async def subscribe_symbol(symbol: str):
    """Subscribe to real-time data for a symbol."""
    await stream.subscribe_symbols([symbol])
    return {"status": "subscribed", "symbol": symbol}

@app.delete("/api/v1/market-data/subscribe/{symbol}")
async def unsubscribe_symbol(symbol: str):
    """Unsubscribe from a symbol."""
    await stream.unsubscribe_symbols([symbol])
    return {"status": "unsubscribed", "symbol": symbol}

@app.get("/api/v1/market-data/subscriptions")
async def list_subscriptions():
    """List currently subscribed symbols."""
    return {"symbols": list(stream.subscribed_symbols)}

@app.get("/health")
async def health():
    """Health check with WebSocket status."""
    is_connected = stream.is_connected() if stream else False
    return {
        "status": "healthy" if is_connected else "degraded",
        "websocket_connected": is_connected,
        "subscribed_symbols": len(stream.subscribed_symbols) if stream else 0
    }
```

**Port Assignment:**
- Signal Service (T3): `8001`
- Execution Gateway (T4): `8002`
- Orchestrator (T5): `8003`
- **Market Data Service: `8004`** (NEW)

---

### 2. WebSocket Client: Alpaca SDK

**Decision:** Use `alpaca-py` library's `StockDataStream` for WebSocket connection

**Rationale:**
- **Official SDK**: Maintained by Alpaca, follows API changes
- **Built-in Reconnection**: Automatic retry logic included
- **Type Safety**: Pydantic models for quote/trade data
- **Proven**: Used by thousands of developers
- **Simpler Than Raw WebSocket**: No need to implement protocol details

**Library Structure:**

```python
# libs/market_data/alpaca_stream.py

from alpaca.data.live import StockDataStream
from alpaca.data.models import Quote
from typing import Set, Callable, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

class AlpacaMarketDataStream:
    """
    WebSocket client for Alpaca real-time market data.

    Manages connection, subscriptions, and quote distribution via Redis.
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        redis_client: RedisClient,
        event_publisher: EventPublisher
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.redis = redis_client
        self.publisher = event_publisher

        # Alpaca WebSocket client
        self.stream = StockDataStream(api_key, secret_key)

        # Track subscriptions
        self.subscribed_symbols: Set[str] = set()

        # Register quote handler
        self.stream.subscribe_quotes(self._handle_quote, *self.subscribed_symbols)

    async def subscribe_symbols(self, symbols: List[str]):
        """
        Subscribe to real-time quotes for symbols.

        Args:
            symbols: List of symbols (e.g., ["AAPL", "MSFT"])
        """
        new_symbols = [s for s in symbols if s not in self.subscribed_symbols]

        if new_symbols:
            await self.stream.subscribe_quotes(*new_symbols)
            self.subscribed_symbols.update(new_symbols)
            logger.info(f"Subscribed to {len(new_symbols)} new symbols: {new_symbols}")

    async def unsubscribe_symbols(self, symbols: List[str]):
        """Unsubscribe from symbols."""
        for symbol in symbols:
            if symbol in self.subscribed_symbols:
                await self.stream.unsubscribe_quotes(symbol)
                self.subscribed_symbols.remove(symbol)
                logger.info(f"Unsubscribed from {symbol}")

    async def _handle_quote(self, quote: Quote):
        """
        Handle incoming quote from Alpaca.

        Stores in Redis and publishes event.
        """
        try:
            symbol = quote.symbol
            mid_price = (quote.bid_price + quote.ask_price) / 2

            # Store in Redis with 5-minute TTL
            price_data = {
                'symbol': symbol,
                'bid': float(quote.bid_price),
                'ask': float(quote.ask_price),
                'mid': float(mid_price),
                'bid_size': quote.bid_size,
                'ask_size': quote.ask_size,
                'timestamp': quote.timestamp.isoformat(),
                'exchange': quote.exchange
            }

            await self.redis.setex(
                f"price:{symbol}",
                300,  # 5 minutes
                json.dumps(price_data)
            )

            # Publish price update event
            await self.publisher.publish(
                f"price.updated.{symbol}",
                {
                    'symbol': symbol,
                    'price': mid_price,
                    'timestamp': quote.timestamp.isoformat()
                }
            )

            logger.debug(f"Price update: {symbol} = ${mid_price:.2f}")

        except Exception as e:
            logger.error(f"Error handling quote for {quote.symbol}: {e}")

    async def start(self):
        """Start WebSocket connection."""
        logger.info("Starting Alpaca WebSocket stream...")
        try:
            await self.stream.run()
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            # Auto-reconnection handled by Alpaca SDK
            await asyncio.sleep(5)
            await self.start()  # Retry

    async def stop(self):
        """Stop WebSocket connection gracefully."""
        logger.info("Stopping Alpaca WebSocket stream...")
        await self.stream.stop()

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.stream._running if hasattr(self.stream, '_running') else False
```

**Dependencies:**

```txt
# requirements.txt (additions)
alpaca-py>=0.9.0          # Alpaca SDK with WebSocket support
websockets>=11.0          # WebSocket protocol implementation
```

---

### 3. Price Distribution: Redis Cache + Pub/Sub

**Decision:** Use Redis for both price caching and event distribution

**Cache Strategy:**

```python
# Key Format
price:{symbol} -> JSON object

# Example
price:AAPL -> {
    "symbol": "AAPL",
    "bid": 150.25,
    "ask": 150.27,
    "mid": 150.26,
    "bid_size": 100,
    "ask_size": 200,
    "timestamp": "2025-10-19T14:30:15.123456+00:00",
    "exchange": "NASDAQ"
}

# TTL: 5 minutes (300 seconds)
```

**Pub/Sub Strategy:**

```python
# Channel Format
price.updated.{symbol}

# Example
price.updated.AAPL -> {
    "symbol": "AAPL",
    "price": 150.26,
    "timestamp": "2025-10-19T14:30:15.123456+00:00"
}
```

**Rationale:**
- **Cache Enables Polling**: Services can query latest price without WebSocket
- **Pub/Sub Enables Push**: Services can react immediately to price changes
- **Redis Already Deployed**: Reuse existing infrastructure (from P1.1T2)
- **Low Latency**: Redis operations < 1ms
- **Flexible Consumption**: Services choose polling vs push

**Consumer Example (Execution Gateway):**

```python
# apps/execution_gateway/main.py

@app.get("/api/v1/positions/pnl/realtime")
async def get_realtime_pnl():
    """
    Get real-time P&L with latest market prices.

    Uses Redis cache for latest prices.
    Fallback to yesterday's close if no real-time data.
    """
    positions = db.get_open_positions()
    pnl_data = []

    for position in positions:
        # Try to get latest price from Redis
        price_json = await redis.get(f"price:{position.symbol}")

        if price_json:
            # Real-time price available
            price_data = json.loads(price_json)
            current_price = price_data['mid']
            price_source = 'real-time'
        else:
            # Fallback to last known price (yesterday's close)
            current_price = position.last_price
            price_source = 'closing'

        # Calculate unrealized P&L
        unrealized = (current_price - position.avg_entry_price) * position.qty

        pnl_data.append({
            'symbol': position.symbol,
            'unrealized_pnl': round(unrealized, 2),
            'current_price': current_price,
            'price_source': price_source,
            'last_updated': price_data.get('timestamp') if price_json else None
        })

    return {
        'positions': pnl_data,
        'total_unrealized': sum(p['unrealized_pnl'] for p in pnl_data)
    }
```

---

### 4. Automatic Position Subscription

**Decision:** Market Data Service auto-subscribes to symbols with open positions

**Implementation:**

```python
# apps/market_data_service/position_sync.py

class PositionBasedSubscription:
    """
    Automatically subscribe to symbols with open positions.

    Queries Execution Gateway every 5 minutes to sync subscriptions.
    """

    def __init__(
        self,
        stream: AlpacaMarketDataStream,
        execution_gateway_url: str
    ):
        self.stream = stream
        self.gateway_url = execution_gateway_url

    async def start_sync_loop(self):
        """Background task to sync subscriptions."""
        while True:
            try:
                await self._sync_subscriptions()
            except Exception as e:
                logger.error(f"Subscription sync error: {e}")

            await asyncio.sleep(300)  # 5 minutes

    async def _sync_subscriptions(self):
        """Fetch open positions and subscribe to their symbols."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.gateway_url}/api/v1/positions"
            )
            data = response.json()

        position_symbols = {p['symbol'] for p in data['positions']}

        # Subscribe to new symbols
        new_symbols = position_symbols - self.stream.subscribed_symbols
        if new_symbols:
            await self.stream.subscribe_symbols(list(new_symbols))
            logger.info(f"Auto-subscribed to {len(new_symbols)} symbols")

        # Unsubscribe from closed positions (optional)
        closed_symbols = self.stream.subscribed_symbols - position_symbols
        if closed_symbols:
            await self.stream.unsubscribe_symbols(list(closed_symbols))
            logger.info(f"Auto-unsubscribed from {len(closed_symbols)} symbols")
```

**Rationale:**
- **Zero Configuration**: No manual subscription management
- **Cost Efficient**: Only subscribe to symbols we have positions in
- **Auto-Cleanup**: Unsubscribe when positions close
- **Resilient**: Recovers from missed position updates

---

### 5. Reconnection Strategy

**Decision:** Rely on Alpaca SDK's built-in reconnection + custom retry wrapper

**Implementation:**

```python
# libs/market_data/alpaca_stream.py

async def start(self):
    """
    Start WebSocket with automatic reconnection.

    Alpaca SDK handles transient disconnects.
    We add exponential backoff for persistent failures.
    """
    retry_count = 0
    max_retries = 10
    base_delay = 5  # seconds

    while retry_count < max_retries:
        try:
            logger.info(f"Starting WebSocket (attempt {retry_count + 1}/{max_retries})...")
            await self.stream.run()

        except alpaca.common.exceptions.APIError as e:
            logger.error(f"Alpaca API error: {e}")
            # Credential or permission error - don't retry
            raise

        except Exception as e:
            retry_count += 1
            delay = min(base_delay * (2 ** retry_count), 300)  # Max 5 minutes

            logger.warning(
                f"WebSocket disconnected: {e}. "
                f"Retrying in {delay}s (attempt {retry_count}/{max_retries})"
            )

            await asyncio.sleep(delay)

    logger.error("Max reconnection attempts reached. Giving up.")
    raise RuntimeError("Failed to establish WebSocket connection")
```

**Reconnection Flow:**

```
┌─────────────────┐
│ WebSocket Start │
└────────┬────────┘
         │
         ▼
┌──────────────────────┐       Success
│ Alpaca SDK Connect   ├──────────────────► Streaming Quotes
└─────────┬────────────┘
          │
          │ Disconnect
          ▼
┌──────────────────────┐
│ Alpaca SDK Auto-     │ (Handles transient network issues)
│ Reconnect (built-in) │
└──────────┬───────────┘
           │
           │ Persistent Failure
           ▼
┌──────────────────────┐
│ Custom Exponential   │
│ Backoff Retry        │ (5s, 10s, 20s, ..., max 5min)
└──────────────────────┘
```

**Rationale:**
- **Leverage SDK**: Alpaca handles most reconnection scenarios
- **Exponential Backoff**: Prevents thundering herd on Alpaca
- **Fail Fast**: Credential errors don't retry
- **Observable**: Logs all reconnection attempts

---

### 6. Data Persistence: None (P1), Future (P2)

**Decision (P1):** Do NOT persist tick data initially

**Rationale:**
- **Scope Control**: P1 focuses on real-time P&L, not tick analytics
- **Cost/Complexity**: TimescaleDB/InfluxDB adds infrastructure burden
- **Alternative**: Can replay Parquet data for backtesting
- **Future-Proof**: Redis keys are self-documenting for P2 migration

**Future P2 Enhancement:**

```python
# Future: Persist to TimescaleDB
@app.on_event("startup")
async def enable_tick_persistence():
    if settings.PERSIST_TICKS:
        tick_writer = TickDataWriter(timescale_db)
        stream.add_handler(tick_writer.write_quote)
```

---

### 7. Testing Strategy

**Unit Tests (Mock WebSocket):**

```python
# tests/market_data/test_alpaca_stream.py

@pytest.mark.asyncio
async def test_quote_handling():
    """Test quote is cached in Redis and event published."""
    mock_redis = AsyncMock()
    mock_publisher = AsyncMock()

    stream = AlpacaMarketDataStream(
        api_key="test_key",
        secret_key="test_secret",
        redis_client=mock_redis,
        event_publisher=mock_publisher
    )

    # Simulate quote from Alpaca
    quote = Quote(
        symbol="AAPL",
        bid_price=150.25,
        ask_price=150.27,
        bid_size=100,
        ask_size=200,
        timestamp=datetime.now(timezone.utc),
        exchange="NASDAQ"
    )

    await stream._handle_quote(quote)

    # Verify Redis cache
    mock_redis.setex.assert_called_once()
    cache_key = mock_redis.setex.call_args[0][0]
    assert cache_key == "price:AAPL"

    # Verify event published
    mock_publisher.publish.assert_called_once()
    event_channel = mock_publisher.publish.call_args[0][0]
    assert event_channel == "price.updated.AAPL"
```

**Integration Tests (Real Redis, Mock Alpaca):**

```python
# tests/integration/test_realtime_pnl.py

@pytest.mark.asyncio
async def test_realtime_pnl_with_live_prices():
    """Test P&L calculation uses real-time prices from Redis."""

    # Setup: Create position in database
    db.create_position("AAPL", qty=100, avg_entry_price=150.00)

    # Simulate price update in Redis
    await redis.setex(
        "price:AAPL",
        300,
        json.dumps({
            'mid': 152.00,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    )

    # Query real-time P&L
    response = await client.get("/api/v1/positions/pnl/realtime")
    data = response.json()

    # Verify P&L uses real-time price
    assert data['positions'][0]['current_price'] == 152.00
    assert data['positions'][0]['unrealized_pnl'] == 200.00  # (152-150) * 100
    assert data['positions'][0]['price_source'] == 'real-time'
```

**Manual Testing (Alpaca Paper Trading):**

```bash
# Start services
make start-market-data
make start-execution-gateway

# Subscribe to AAPL
curl -X POST http://localhost:8004/api/v1/market-data/subscribe \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL"}'

# Wait 5 seconds for quotes...

# Check Redis cache
redis-cli GET price:AAPL

# Query real-time P&L
curl http://localhost:8002/api/v1/positions/pnl/realtime | jq
```

---

## Implementation Plan

### Phase 1: Library & Service Foundation (Days 1-2)

**Tasks:**
1. Create `libs/market_data/` module
2. Implement `AlpacaMarketDataStream` class
3. Create `apps/market_data_service/` FastAPI service
4. Add subscription management endpoints
5. Add health check with WebSocket status

**Deliverables:**
- `libs/market_data/alpaca_stream.py` (~300 lines)
- `apps/market_data_service/main.py` (~200 lines)
- Unit tests with mocked WebSocket (~200 lines)

---

### Phase 2: Redis Integration (Day 3)

**Tasks:**
1. Integrate Redis price caching
2. Implement pub/sub event publishing
3. Add TTL and cache invalidation
4. Add cache health monitoring

**Deliverables:**
- Price caching in `_handle_quote()` method
- Event publishing to `price.updated.*` channels
- Integration tests with real Redis

---

### Phase 3: Execution Gateway Integration (Day 4)

**Tasks:**
1. Add `/api/v1/positions/pnl/realtime` endpoint
2. Implement price retrieval from Redis
3. Add fallback to closing prices
4. Update `make status` to show real-time P&L

**Deliverables:**
- Real-time P&L endpoint in Execution Gateway
- Updated operational status script
- End-to-end integration tests

---

### Phase 4: Auto-Subscription (Day 5)

**Tasks:**
1. Implement `PositionBasedSubscription` class
2. Add background sync task
3. Test subscription/unsubscription logic
4. Add metrics (subscribed symbols count)

**Deliverables:**
- Auto-subscription based on open positions
- Background sync every 5 minutes
- Tests verifying auto-subscription

---

### Phase 5: Testing & Documentation (Days 6-7)

**Tasks:**
1. Add comprehensive unit tests
2. Add integration tests
3. Manual testing with Alpaca paper trading
4. Create implementation guide
5. Create WebSocket streaming concepts doc
6. Update system architecture diagrams

**Deliverables:**
- 90%+ test coverage
- `docs/IMPLEMENTATION_GUIDES/p1.2t1-realtime-market-data.md`
- `docs/CONCEPTS/websocket-streaming.md`
- Updated architecture diagrams

---

## Consequences

### Benefits

1. ✅ **Real-Time P&L Monitoring**
   - Unrealized P&L updated within 100ms of price change
   - Users see current position value during market hours
   - Enables intraday risk monitoring

2. ✅ **Foundation for Live Trading**
   - WebSocket infrastructure ready for order execution
   - Real-time price feeds for signal generation (P2)
   - Supports higher-frequency trading strategies

3. ✅ **Improved User Experience**
   - `make status` shows current P&L, not yesterday's close
   - Real-time position monitoring
   - Immediate feedback on market movements

4. ✅ **Scalable Architecture**
   - Dedicated service isolates WebSocket complexity
   - Redis enables multiple P&L consumers
   - Can add more data sources (IEX, Polygon) later

5. ✅ **Production Ready**
   - Automatic reconnection on disconnect
   - Graceful degradation (fallback to closing prices)
   - Health checks for monitoring
   - Comprehensive test coverage

6. ✅ **Cost Efficient**
   - Only subscribe to symbols with positions
   - Auto-unsubscribe when positions close
   - No tick data storage (P1 scope)

### Trade-offs

1. ⚠️ **Alpaca Subscription Required**
   - Real-time data costs $9-99/month (depending on tier)
   - **Mitigation:** Paper trading account has free real-time data
   - **Mitigation:** Can disable for development (use closing prices)

2. ⚠️ **WebSocket Complexity**
   - More complex than HTTP polling
   - Reconnection logic required
   - **Mitigation:** Alpaca SDK handles most complexity
   - **Mitigation:** Comprehensive tests and error logging

3. ⚠️ **Additional Service to Deploy**
   - New service increases operational complexity
   - **Mitigation:** Similar to existing services (FastAPI + Docker)
   - **Mitigation:** Health checks and monitoring

4. ⚠️ **Redis Dependency**
   - Requires Redis for price caching
   - **Mitigation:** Redis already deployed (P1.1T2)
   - **Mitigation:** Fallback to closing prices if Redis unavailable

5. ⚠️ **Data Freshness Window**
   - 5-minute TTL means stale prices possible
   - **Mitigation:** WebSocket pushes updates immediately
   - **Mitigation:** TTL only matters if WebSocket disconnects

6. ⚠️ **Testing Complexity**
   - Hard to test WebSocket in CI/CD
   - **Mitigation:** Mock Alpaca SDK in unit tests
   - **Mitigation:** Integration tests with real Redis, mock WebSocket

### Risks

1. **Alpaca WebSocket Downtime**
   - **Risk:** Alpaca service outage breaks real-time data
   - **Probability:** Low (Alpaca SLA: 99.9%)
   - **Impact:** Medium (fallback to closing prices)
   - **Mitigation:** Graceful degradation, health monitoring

2. **Quote Volume Overload**
   - **Risk:** Too many quotes overwhelm Redis/network
   - **Probability:** Low (< 100 symbols typically)
   - **Impact:** Low (Redis handles 100K ops/sec)
   - **Mitigation:** Subscribe only to positions, not full market

3. **Missed Quotes During Reconnect**
   - **Risk:** Price gap during brief disconnect
   - **Probability:** Medium (network issues happen)
   - **Impact:** Low (P&L slightly stale for <1 minute)
   - **Mitigation:** Auto-reconnect, 5-minute TTL catches staleness

4. **Redis Memory Growth**
   - **Risk:** Price cache grows too large
   - **Probability:** Very Low (< 100 symbols * 500 bytes each)
   - **Impact:** Very Low (< 50KB total)
   - **Mitigation:** TTL automatically expires, maxmemory policy

---

## Alternatives Considered

### Alternative 1: Polling Alpaca REST API

**Description:** Query Alpaca's REST API for quotes every 10 seconds

**Pros:**
- Simpler than WebSocket
- No reconnection logic needed
- Easier to test

**Cons:**
- ❌ 10-second latency (vs 100ms WebSocket)
- ❌ Rate limits (200 req/min for free tier)
- ❌ Doesn't scale to 100+ symbols
- ❌ High API usage costs

**Decision:** **Rejected** - WebSocket provides 100x better latency

---

### Alternative 2: Extend Signal Service Instead of New Service

**Description:** Add WebSocket client to Signal Service (T3)

**Pros:**
- One fewer service to deploy
- Tighter integration with signal generation

**Cons:**
- ❌ Violates single responsibility principle
- ❌ Couples market data to signal logic
- ❌ Can't scale services independently
- ❌ WebSocket failures could break signal generation

**Decision:** **Rejected** - Dedicated service provides better isolation

---

### Alternative 3: Store Tick Data in TimescaleDB

**Description:** Persist every quote to TimescaleDB for analytics

**Pros:**
- Historical tick data for backtesting
- Can replay market events
- Enables tick-level analytics

**Cons:**
- ❌ Adds infrastructure complexity (new database)
- ❌ High write volume (100s of quotes/sec)
- ❌ Storage costs (GB per day)
- ❌ Out of scope for P1 (real-time P&L only)

**Decision:** **Deferred to P2** - Focus on real-time P&L first

---

### Alternative 4: In-Memory Price Cache (No Redis)

**Description:** Cache prices in Python dict inside Market Data Service

**Pros:**
- No Redis dependency
- Faster (no network roundtrip)

**Cons:**
- ❌ Not shared across service instances
- ❌ Lost on service restart
- ❌ Other services can't access (Execution Gateway needs prices)

**Decision:** **Rejected** - Redis enables multi-service access

---

### Alternative 5: IEX Cloud Instead of Alpaca

**Description:** Use IEX Cloud for market data instead of Alpaca

**Pros:**
- More data sources (non-US markets)
- Better historical data API

**Cons:**
- ❌ More expensive ($9/month vs free paper trading)
- ❌ Different SDK (more integration work)
- ❌ Already using Alpaca for orders (consolidate providers)

**Decision:** **Rejected** - Stick with Alpaca for consistency

---

## Success Metrics

### Performance Metrics
- [ ] Quote latency < 100ms (Alpaca → Redis)
- [ ] Price cache write < 5ms
- [ ] Real-time P&L endpoint response < 50ms
- [ ] WebSocket uptime > 99% (excluding scheduled maintenance)

### Reliability Metrics
- [ ] Automatic reconnection < 30 seconds
- [ ] Health check reports WebSocket status accurately
- [ ] Graceful fallback to closing prices when WebSocket down
- [ ] Zero crashes from malformed quotes

### Functional Metrics
- [ ] Auto-subscribe to all open positions
- [ ] Auto-unsubscribe from closed positions
- [ ] Price updates every second during market hours
- [ ] `make status` shows real-time P&L

### Testing Metrics
- [ ] 90%+ code coverage for market data library
- [ ] Integration tests with real Redis
- [ ] Manual testing with Alpaca paper trading
- [ ] Load test: 100 symbols streaming concurrently

---

## Related Documents

- [P1_PLANNING.md](../TASKS/P1_PLANNING.md) - P1.2T1 requirements
- [NEXT_TASK.md](../NEXT_TASK.md) - Current task details
- [ADR-0009](./0009-redis-integration.md) - Redis integration patterns
- [Alpaca WebSocket Docs](https://alpaca.markets/docs/market-data/streaming/)

---

**Last Updated:** 2025-10-19
**Status:** Proposed (awaiting approval)
**Next Review:** After Phase 1 completion

# Next Task - Single Source of Truth

**Last Updated:** October 19, 2024
**Current Phase:** P1 (Advanced Features)
**Overall Progress:** 38% (5/13 tasks complete)
**Track 1 Status:** ✅ **100% COMPLETE** (5/5 tasks)
**Track 2 Status:** 🔄 **STARTING** (0/3 tasks)

---

## 🎯 CURRENT TASK

### P1.2T1 - Real-Time Market Data Streaming

**Status:** Ready to Start
**Branch:** `feature/p1.2t1-realtime-market-data` (to be created)
**Priority:** ⭐ High
**Estimated Effort:** 5-7 days

---

## What to Build

Add WebSocket connection for real-time price updates from Alpaca.

**Current State:**
- Data loaded from Parquet files (batch/historical only)
- No real-time price feeds
- Unrealized P&L calculated only at paper_run.py execution time

**P1.2T1 Goal:**
Enable real-time market data streaming for live price updates:

```python
# WebSocket connection to Alpaca
from alpaca.data.live import StockDataStream

# Subscribe to real-time quotes
stream = StockDataStream(api_key, secret_key)

@stream.on_quote
async def on_quote(quote):
    symbol = quote.symbol
    bid = quote.bid_price
    ask = quote.ask_price

    # Update Redis with latest price
    redis_client.set(f"price:{symbol}", json.dumps({
        'bid': bid,
        'ask': ask,
        'timestamp': quote.timestamp
    }))

    # Publish price update event
    await event_publisher.publish('price.updated', {
        'symbol': symbol,
        'price': (bid + ask) / 2
    })

# Start streaming
await stream.subscribe_quotes(['AAPL', 'MSFT', 'GOOGL'])
```

---

## Acceptance Criteria

- [ ] WebSocket connection to Alpaca real-time data
- [ ] Subscribe to quotes for all active positions
- [ ] Store latest prices in Redis with TTL
- [ ] Publish price update events via Redis pub/sub
- [ ] Execution Gateway subscribes to price updates
- [ ] Real-time unrealized P&L updates
- [ ] Graceful handling of WebSocket disconnections
- [ ] Tests verify streaming and reconnection logic
- [ ] Documentation includes WebSocket patterns

---

## Implementation Steps

###  1. **Add Alpaca WebSocket Client** (`libs/market_data/`)

Create market data library:
```python
# libs/market_data/alpaca_stream.py
class AlpacaMarketDataStream:
    """WebSocket client for Alpaca real-time market data."""

    def __init__(self, api_key: str, secret_key: str, redis_client: RedisClient):
        self.stream = StockDataStream(api_key, secret_key)
        self.redis = redis_client
        self.subscribed_symbols: Set[str] = set()

    async def subscribe_symbols(self, symbols: List[str]):
        """Subscribe to real-time quotes for symbols."""
        await self.stream.subscribe_quotes(symbols)
        self.subscribed_symbols.update(symbols)

    async def on_quote(self, quote: Quote):
        """Handle incoming quote."""
        mid_price = (quote.bid_price + quote.ask_price) / 2

        # Store in Redis
        await self.redis.set(
            f"price:{quote.symbol}",
            json.dumps({
                'price': mid_price,
                'bid': quote.bid_price,
                'ask': quote.ask_price,
                'timestamp': quote.timestamp.isoformat()
            }),
            ex=300  # 5-minute TTL
        )

        # Publish event
        await self.event_publisher.publish('price.updated', {
            'symbol': quote.symbol,
            'price': mid_price
        })
```

### 2. **Create Market Data Service** (new FastAPI service)

```python
# apps/market_data_service/main.py
@app.on_event("startup")
async def startup():
    # Initialize Alpaca stream
    stream = AlpacaMarketDataStream(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_SECRET_KEY,
        redis_client=redis_client
    )

    # Get active positions from Execution Gateway
    positions = await fetch_active_positions()
    symbols = [p['symbol'] for p in positions]

    # Subscribe to real-time data
    await stream.subscribe_symbols(symbols)
    await stream.start()

@app.post("/api/v1/market-data/subscribe")
async def subscribe_symbol(symbol: str):
    """Subscribe to real-time data for a symbol."""
    await stream.subscribe_symbols([symbol])
    return {"status": "subscribed", "symbol": symbol}
```

### 3. **Update Execution Gateway**

Add real-time P&L endpoint:
```python
# apps/execution_gateway/main.py
@app.get("/api/v1/positions/pnl/realtime")
async def get_realtime_pnl():
    """Get real-time P&L with latest market prices."""
    positions = db.get_open_positions()

    pnl_data = []
    for position in positions:
        # Get latest price from Redis
        price_data = await redis.get(f"price:{position.symbol}")
        current_price = json.loads(price_data)['price'] if price_data else None

        if current_price:
            unrealized = (current_price - position.avg_entry_price) * position.qty
            pnl_data.append({
                'symbol': position.symbol,
                'unrealized_pnl': unrealized,
                'current_price': current_price,
                'last_updated': json.loads(price_data)['timestamp']
            })

    return {'positions': pnl_data}
```

### 4. **Add Redis Pub/Sub Subscriber**

```python
# apps/execution_gateway/price_subscriber.py
class PriceUpdateSubscriber:
    """Subscribe to price update events and update P&L."""

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
        self.pubsub = redis_client.pubsub()

    async def start(self):
        await self.pubsub.subscribe('price.updated')

        async for message in self.pubsub.listen():
            if message['type'] == 'message':
                data = json.loads(message['data'])
                await self.handle_price_update(data)

    async def handle_price_update(self, data: dict):
        """Update unrealized P&L when price changes."""
        symbol = data['symbol']
        price = data['price']

        # Log price update
        logger.info(f"Price update: {symbol} = ${price:.2f}")

        # Could trigger alerts if P&L crosses thresholds
```

### 5. **Add Tests**

- WebSocket connection and subscription tests
- Quote handling and Redis storage tests
- Price update event publishing tests
- Reconnection logic tests
- Real-time P&L calculation tests

### 6. **Create Documentation**

- `docs/IMPLEMENTATION_GUIDES/p1.2t1-realtime-market-data.md`
- `docs/CONCEPTS/websocket-streaming.md`
- `docs/ADRs/0010-realtime-market-data.md`

---

## Files to Create

```
libs/
└── market_data/
    ├── __init__.py
    ├── alpaca_stream.py        # WebSocket client (~300 lines)
    └── types.py                # Pydantic models

apps/
└── market_data_service/        # New service
    ├── __init__.py
    ├── main.py                 # FastAPI app (~200 lines)
    └── config.py

tests/
├── market_data/
│   ├── test_alpaca_stream.py  # WebSocket tests
│   └── test_price_updates.py
└── integration/
    └── test_realtime_pnl.py    # End-to-end tests

docs/
├── IMPLEMENTATION_GUIDES/
│   └── p1.2t1-realtime-market-data.md
├── CONCEPTS/
│   └── websocket-streaming.md
└── ADRs/
    └── 0010-realtime-market-data.md
```

---

## Getting Started

```bash
# 1. Create feature branch
git checkout -b feature/p1.2t1-realtime-market-data

# 2. Add Alpaca WebSocket dependency
echo "alpaca-py>=0.9.0" >> requirements.txt
pip install -r requirements.txt

# 3. Create library structure
mkdir -p libs/market_data
touch libs/market_data/__init__.py
touch libs/market_data/alpaca_stream.py

# 4. Create new service
mkdir -p apps/market_data_service
touch apps/market_data_service/main.py

# 5. Create test structure
mkdir -p tests/market_data
touch tests/market_data/test_alpaca_stream.py

# 6. Start implementation
# See docs/TASKS/P1_PLANNING.md for detailed requirements
```

---

## Dependencies

**Required:**
- Alpaca account with real-time data subscription
- Alpaca API credentials (API key + secret)
- Redis running (for price caching and pub/sub)
- Existing Execution Gateway (for position queries)

**Optional:**
- WebSocket debugging tools (wscat, websocat)

---

## Success Metrics

**Performance:**
- WebSocket latency < 100ms from Alpaca to Redis
- Price updates processed in < 10ms
- Real-time P&L calculation < 50ms for 100 positions

**Reliability:**
- Automatic reconnection on WebSocket disconnect
- No data loss during brief disconnections
- Graceful degradation if Alpaca unavailable

**Coverage:**
- 90%+ test coverage for market data library
- Integration tests for full streaming pipeline

**Documentation:**
- Implementation guide (500+ lines)
- WebSocket patterns documented
- ADR explaining architecture decisions

---

## After Completion

### Next Tasks in Order:

1. **P1.2T3 - Risk Management System** (5-7 days)
   - Position size limits
   - Daily loss limits
   - Circuit breakers

2. **P1.3T1 - Monitoring & Alerting** (5-7 days)
   - Prometheus metrics
   - Grafana dashboards

3. **Phase 1C** - Production Hardening
   - Centralized logging
   - CI/CD pipeline

---

## Related Documents

- [P1 Progress](./GETTING_STARTED/P1_PROGRESS.md) - Detailed progress tracker
- [P1 Planning](./TASKS/P1_PLANNING.md) - Complete P1 roadmap
- [Project Status](./GETTING_STARTED/PROJECT_STATUS.md) - Overall project state

---

## Quick Commands

```bash
# Check current progress
cat docs/NEXT_TASK.md

# View detailed P1 status
cat docs/GETTING_STARTED/P1_PROGRESS.md

# Start next task
git checkout -b feature/p1.2t1-realtime-market-data
```

---

**🎯 ACTION REQUIRED:** Create branch and begin P1.2T1 - Real-Time Market Data Streaming

# WebSocket Streaming for Real-Time Market Data

**Last Updated:** October 19, 2024
**Related:** ADR-0010, P1.2T1 Implementation Guide

## Overview

WebSocket streaming enables real-time, bidirectional communication between the trading platform and market data providers. Unlike traditional HTTP request-response patterns, WebSockets maintain a persistent connection that allows servers to push data to clients instantly as market conditions change.

## Why WebSockets for Market Data?

### Traditional HTTP Polling (Inefficient)

```
Client → Server: "Do you have new quotes for AAPL?"
Server → Client: "No"
[Wait 1 second]
Client → Server: "Do you have new quotes for AAPL?"
Server → Client: "No"
[Wait 1 second]
Client → Server: "Do you have new quotes for AAPL?"
Server → Client: "Yes! AAPL: $150.25"
```

**Problems:**
- 🔴 **High Latency**: 1-second delay before receiving quotes
- 🔴 **Resource Waste**: 99% of requests return "no new data"
- 🔴 **Server Load**: Thousands of unnecessary requests
- 🔴 **Bandwidth**: Overhead of HTTP headers on every request

### WebSocket Streaming (Efficient)

```
Client → Server: [Establish WebSocket connection]
Client → Server: {"action": "subscribe", "symbols": ["AAPL"]}
Server → Client: [Instantly when quote updates]
                 {"type": "quote", "symbol": "AAPL", "bid": 150.25, "ask": 150.27}
Server → Client: [Instantly when quote updates]
                 {"type": "quote", "symbol": "AAPL", "bid": 150.26, "ask": 150.28}
```

**Benefits:**
- ✅ **Low Latency**: < 100ms from market event to client
- ✅ **Efficient**: Only transmits data when it changes
- ✅ **Scalable**: One connection handles all symbols
- ✅ **Real-Time**: Instant push notifications of market changes

## WebSocket Lifecycle

### 1. Connection Establishment

```python
# libs/market_data/alpaca_stream.py
async def start(self):
    """Establish WebSocket connection with authentication."""

    # 1. Create WebSocket connection
    self._ws = await websockets.connect(
        "wss://stream.data.alpaca.markets/v2/iex",
        max_size=10_000_000  # 10MB message buffer
    )

    # 2. Authenticate with API keys
    auth_message = {
        "action": "auth",
        "key": self.api_key,
        "secret": self.secret_key
    }
    await self._ws.send(json.dumps(auth_message))

    # 3. Wait for authentication confirmation
    response = await self._ws.recv()
    # {"T": "success", "msg": "authenticated"}

    # 4. Start receiving messages
    await self._consume_messages()
```

**Connection States:**
- **CONNECTING**: Establishing TCP connection
- **AUTHENTICATING**: Sending credentials
- **CONNECTED**: Ready to subscribe/receive data
- **DISCONNECTED**: Connection lost (triggers reconnection)

### 2. Subscription Management

```python
async def subscribe_symbols(self, symbols: List[str]):
    """Subscribe to real-time quotes for symbols."""

    # Send subscription message
    subscribe_msg = {
        "action": "subscribe",
        "quotes": symbols  # ["AAPL", "MSFT", "GOOGL"]
    }
    await self._ws.send(json.dumps(subscribe_msg))

    # Server confirms subscription
    # {"T": "subscription", "quotes": ["AAPL", "MSFT", "GOOGL"]}
```

**Subscription Lifecycle:**
```
User Opens Position → Auto-Subscribe to Symbol
     ↓
WebSocket receives quotes → Cache in Redis → Publish events
     ↓
User Closes Position → Auto-Unsubscribe from Symbol
```

### 3. Message Processing

```python
async def _consume_messages(self):
    """Continuously process incoming WebSocket messages."""

    async for message in self._ws:
        try:
            # Parse JSON message
            data = json.loads(message)

            # Handle different message types
            if data[0]["T"] == "q":  # Quote message
                await self._handle_quote(data[0])
            elif data[0]["T"] == "subscription":
                await self._handle_subscription(data[0])

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Continue processing (don't crash on bad message)
```

**Message Types:**

| Type | Code | Description | Example |
|------|------|-------------|---------|
| Quote | `q` | Real-time bid/ask prices | `{"T":"q","S":"AAPL","bp":150.25,"ap":150.27}` |
| Trade | `t` | Executed trade | `{"T":"t","S":"AAPL","p":150.26,"s":100}` |
| Bar | `b` | OHLCV candle | `{"T":"b","S":"AAPL","o":150,"h":151,"l":149,"c":150.5,"v":10000}` |
| Subscription | `subscription` | Confirm subscription | `{"T":"subscription","quotes":["AAPL"]}` |
| Error | `error` | Error message | `{"T":"error","code":406,"msg":"invalid symbol"}` |

### 4. Disconnection & Reconnection

**Why Disconnections Happen:**
- Network interruptions (Wi-Fi drops, ISP issues)
- Server maintenance (Alpaca deploys, restarts)
- Authentication expiry (tokens timeout)
- Firewall/proxy interference
- Client crashes

**Reconnection Strategy (Exponential Backoff):**

```python
async def _handle_reconnection(self):
    """Reconnect with exponential backoff."""

    for attempt in range(self.max_reconnect_attempts):
        # Calculate delay: 5s, 10s, 20s, 40s, 80s, ...
        delay = self.reconnect_base_delay * (2 ** attempt)
        delay = min(delay, 300)  # Cap at 5 minutes

        logger.info(f"Reconnecting in {delay}s (attempt {attempt + 1})")
        await asyncio.sleep(delay)

        try:
            # Re-establish connection
            await self.start()

            # Re-subscribe to previous symbols
            if self._subscribed_symbols:
                await self.subscribe_symbols(list(self._subscribed_symbols))

            logger.info("Reconnection successful!")
            return

        except Exception as e:
            logger.error(f"Reconnection failed: {e}")

    logger.critical("Max reconnection attempts reached!")
```

**Reconnection Timeline:**
```
[0s]    Connection lost
[5s]    Attempt 1 (fails)
[15s]   Attempt 2 (fails) [5 + 10]
[35s]   Attempt 3 (fails) [15 + 20]
[75s]   Attempt 4 (SUCCESS!) [35 + 40]
        Re-subscribe to all symbols
        Resume normal operation
```

## Data Flow Architecture

### End-to-End Message Flow

```
┌─────────────────┐
│  Alpaca Market  │
│   Data Server   │
└────────┬────────┘
         │ WebSocket (wss://)
         │ {"T":"q","S":"AAPL","bp":150.25,"ap":150.27,"t":"2024-10-19T14:30:00Z"}
         ↓
┌─────────────────────────────────────────────────────────────┐
│  AlpacaMarketDataStream (libs/market_data/alpaca_stream.py) │
│  - Authenticate & maintain connection                       │
│  - Parse incoming messages                                  │
│  - Calculate mid price: (bid + ask) / 2                     │
└────────┬────────────────────────────────────────────────────┘
         │
         ├─→ Redis Cache (price:{symbol})
         │   Key: "price:AAPL"
         │   Value: {"symbol":"AAPL","bid":150.25,"ask":150.27,"mid":150.26,"timestamp":"2024-10-19T14:30:00Z"}
         │   TTL: 300 seconds (5 minutes)
         │
         └─→ Redis Pub/Sub (price.updated.AAPL)
             Message: {"symbol":"AAPL","bid":150.25,"ask":150.27,"mid":150.26,"timestamp":"2024-10-19T14:30:00Z"}
             ↓
         ┌────────────────────────────────┐
         │  Execution Gateway (Subscriber) │
         │  - Listen for price updates     │
         │  - Calculate real-time P&L      │
         │  - Trigger alerts (future)      │
         └────────────────────────────────┘
```

### Redis Integration

**Why Redis?**
1. **Decoupling**: Market Data Service owns WebSocket, other services read from Redis
2. **Caching**: Fast O(1) lookups for latest prices
3. **Pub/Sub**: Real-time event distribution to multiple consumers
4. **Persistence**: Prices survive service restarts (with TTL)

**Redis Data Structures:**

```redis
# Price Cache (Hash stored as JSON string)
SET price:AAPL '{"symbol":"AAPL","bid":150.25,"ask":150.27,"mid":150.26,"timestamp":"2024-10-19T14:30:00.123456+00:00","exchange":"IEX"}'
EXPIRE price:AAPL 300  # 5-minute TTL

# Price Update Events (Pub/Sub)
PUBLISH price.updated.AAPL '{"symbol":"AAPL","bid":150.25,"ask":150.27,"mid":150.26,"timestamp":"2024-10-19T14:30:00.123456+00:00"}'

# Retrieve Latest Price
GET price:AAPL
```

**Price Cache Benefits:**
- **Fast Lookups**: Execution Gateway retrieves prices in < 1ms
- **Graceful Degradation**: Works even if WebSocket is down
- **Staleness Detection**: TTL indicates if price is stale (> 5 minutes)

## Auto-Subscription Pattern

### Problem: Manual Subscription is Error-Prone

❌ **Manual approach:**
```python
# User opens AAPL position
# Developer must remember to subscribe
stream.subscribe_symbols(["AAPL"])

# User closes AAPL position
# Developer must remember to unsubscribe
stream.unsubscribe_symbols(["AAPL"])
```

**Issues:**
- Forgetting to subscribe = no real-time prices
- Forgetting to unsubscribe = wasted bandwidth
- Tight coupling between position management and market data

### Solution: Position-Based Auto-Subscription

✅ **Automatic approach:**
```python
# PositionBasedSubscription runs in background
# Every 5 minutes:
#   1. Query Execution Gateway for open positions
#   2. Subscribe to new symbols
#   3. Unsubscribe from closed symbols
```

**Implementation:**

```python
# apps/market_data_service/position_sync.py
class PositionBasedSubscription:
    """Automatically subscribe to symbols with open positions."""

    async def start_sync_loop(self):
        """Background task that syncs subscriptions every 5 minutes."""
        while self._running:
            # Fetch positions from Execution Gateway
            position_symbols = await self._fetch_position_symbols()
            # {"AAPL", "MSFT"}

            # Compare with current subscriptions
            current_subscribed = set(stream.get_subscribed_symbols())
            # {"AAPL", "GOOGL"}  (GOOGL position was closed)

            # Subscribe to new symbols
            new_symbols = position_symbols - current_subscribed
            # {"MSFT"}
            if new_symbols:
                await stream.subscribe_symbols(list(new_symbols))

            # Unsubscribe from closed positions
            closed_symbols = current_subscribed - position_symbols
            # {"GOOGL"}
            if closed_symbols:
                await stream.unsubscribe_symbols(list(closed_symbols))

            # Wait 5 minutes
            await asyncio.sleep(300)
```

**Auto-Subscription Timeline:**
```
[T+0s]    User opens AAPL position (via Execution Gateway)
[T+5s]    PositionBasedSubscription detects new position
          → Subscribe to AAPL quotes
[T+10s]   Start receiving AAPL quotes via WebSocket
          → Cache in Redis
          → Calculate real-time P&L

[T+300s]  User closes AAPL position
[T+305s]  PositionBasedSubscription detects closed position
          → Unsubscribe from AAPL quotes
```

## Performance Characteristics

### Latency Breakdown

**Quote Received to P&L Updated:**
```
Alpaca Exchange → WebSocket Send: ~10-50ms
    ↓
WebSocket Recv → Parse JSON: ~1ms
    ↓
Calculate Mid Price: <1ms
    ↓
Write to Redis Cache: ~1ms
    ↓
Publish to Redis Pub/Sub: ~1ms
    ↓
Execution Gateway Receives Event: ~1ms
    ↓
Calculate P&L: <1ms
    ↓
TOTAL: ~15-60ms (real-time!)
```

### Throughput

**Message Rate:**
- **Quiet Market**: ~10 quotes/second/symbol
- **Active Market**: ~100 quotes/second/symbol
- **Volatile Market**: ~1,000 quotes/second/symbol

**Platform Capacity:**
- **10 symbols**: ~10K messages/second (easily handled)
- **100 symbols**: ~100K messages/second (requires optimization)
- **1,000 symbols**: ~1M messages/second (requires sharding)

### Resource Usage

**Memory:**
- WebSocket buffer: ~10MB per connection
- Redis cache: ~500 bytes per symbol
- 100 symbols = ~50KB total (negligible)

**Network:**
- **Idle**: ~1KB/s (heartbeat)
- **10 symbols, quiet market**: ~10KB/s
- **100 symbols, active market**: ~500KB/s

**CPU:**
- JSON parsing: ~0.1ms per message
- Redis write: ~0.1ms per message
- **10K messages/second = ~2% CPU** (single core)

## Error Handling

### Connection Errors

```python
try:
    await self._ws.connect(url)
except ConnectionError as e:
    logger.error(f"WebSocket connection failed: {e}")
    # Trigger reconnection with exponential backoff
    await self._handle_reconnection()
```

**Common Errors:**
- `ConnectionRefusedError`: Server not reachable
- `TimeoutError`: Connection timeout (firewall, slow network)
- `SSLError`: Certificate validation failed

### Message Errors

```python
try:
    data = json.loads(message)
    await self._handle_quote(data)
except json.JSONDecodeError:
    logger.warning(f"Invalid JSON: {message}")
    # Skip this message, continue processing
except KeyError as e:
    logger.warning(f"Missing field: {e}")
    # Skip this message, continue processing
```

**Graceful Degradation:**
- Single bad message doesn't crash service
- Logs warning for debugging
- Continues processing subsequent messages

### Subscription Errors

```python
# Server returns error for invalid symbol
{"T": "error", "code": 406, "msg": "symbol not found: INVALID"}

# Our handling:
if data["T"] == "error":
    logger.error(f"Subscription error: {data['msg']}")
    # Remove invalid symbol from subscribed set
    self._subscribed_symbols.discard("INVALID")
```

## Best Practices

### 1. Always Implement Reconnection

❌ **Bad:**
```python
# Connection lost = service dead
await ws.connect(url)
await ws.recv()  # Hangs forever if disconnected
```

✅ **Good:**
```python
async def start(self):
    while self._should_run:
        try:
            await self._connect_and_consume()
        except websockets.ConnectionClosed:
            logger.warning("Connection lost, reconnecting...")
            await self._handle_reconnection()
```

### 2. Use Exponential Backoff

❌ **Bad:**
```python
# Hammers server with reconnection attempts
while True:
    try:
        await ws.connect(url)
        break
    except:
        await asyncio.sleep(1)  # Always 1 second
```

✅ **Good:**
```python
for attempt in range(max_attempts):
    delay = base_delay * (2 ** attempt)  # 5s, 10s, 20s, 40s, ...
    await asyncio.sleep(min(delay, max_delay))
    try:
        await ws.connect(url)
        break
    except:
        continue
```

### 3. Validate Message Structure

❌ **Bad:**
```python
# Crash on unexpected message
quote_price = data["bp"]  # KeyError if field missing
```

✅ **Good:**
```python
# Graceful handling
quote_price = data.get("bp")
if quote_price is None:
    logger.warning(f"Missing bid price in message: {data}")
    return  # Skip this message
```

### 4. Set Reasonable TTLs

❌ **Bad:**
```python
# Price never expires (stale data!)
redis.set(f"price:{symbol}", price_json)
```

✅ **Good:**
```python
# Price expires after 5 minutes
redis.setex(f"price:{symbol}", 300, price_json)

# Consumers can check staleness
price_age = now - price["timestamp"]
if price_age > 300:
    logger.warning(f"Stale price for {symbol}: {price_age}s old")
```

### 5. Limit Subscription Count

❌ **Bad:**
```python
# Subscribe to entire market (thousands of symbols)
stream.subscribe_symbols(all_symbols)  # Overwhelms connection
```

✅ **Good:**
```python
# Only subscribe to symbols with open positions
position_symbols = get_open_position_symbols()  # ~10-50 symbols
stream.subscribe_symbols(position_symbols)
```

## Security Considerations

### 1. Secure WebSocket URLs

```python
# ✅ Use wss:// (WebSocket Secure = TLS)
url = "wss://stream.data.alpaca.markets/v2/iex"

# ❌ Never use ws:// in production
url = "ws://stream.data.alpaca.markets/v2/iex"  # Unencrypted!
```

### 2. API Key Protection

```python
# ✅ Load from environment variables
api_key = os.getenv("ALPACA_API_KEY")
secret_key = os.getenv("ALPACA_SECRET_KEY")

# ❌ Never hardcode
api_key = "PKXXXXXXXX"  # Exposed in version control!
```

### 3. Message Validation

```python
# Validate message structure before processing
EXPECTED_FIELDS = {"T", "S", "bp", "ap", "t"}
if not EXPECTED_FIELDS.issubset(data.keys()):
    logger.warning(f"Unexpected message structure: {data}")
    return  # Don't process malformed messages
```

## Monitoring & Observability

### Key Metrics

```python
# Connection health
connection_uptime = time.time() - last_connect_time
reconnection_count = total_reconnections

# Message throughput
messages_per_second = message_count / elapsed_time

# Subscription status
subscribed_symbol_count = len(subscribed_symbols)

# Latency
quote_age = time.time() - quote_timestamp
```

### Health Check Endpoint

```python
@app.get("/health")
def health():
    return {
        "status": "healthy" if ws.is_connected else "degraded",
        "websocket_connected": ws.is_connected,
        "subscribed_symbols": len(ws.subscribed_symbols),
        "reconnect_attempts": ws.reconnect_attempts,
        "last_message_time": ws.last_message_time,
    }
```

## Further Reading

- **WebSocket RFC**: [RFC 6455](https://tools.ietf.org/html/rfc6455)
- **Alpaca Market Data API**: [https://alpaca.markets/docs/market-data/](https://alpaca.markets/docs/market-data/)
- **Redis Pub/Sub**: [https://redis.io/topics/pubsub](https://redis.io/topics/pubsub)
- **ADR-0010**: Real-Time Market Data Architecture
- **Implementation Guide**: P1.2T1 Real-Time Market Data Streaming

# ADR 0014: Execution Gateway Architecture with Alpaca Integration

- Status: Proposed
- Date: 2025-10-17

## Context

After completing T1 (Data ETL), T2 (Baseline Strategy), and T3 (Signal Service), we need to implement T4: Execution Gateway to translate trading signals into actual orders. The execution gateway is a critical component that bridges our ML-generated signals with the Alpaca broker API for paper trading.

### Requirements

1. **Idempotent Order Submission**: Orders must be safely retryable without creating duplicates
2. **DRY_RUN Mode**: Ability to log orders without submitting to broker (for testing/validation)
3. **Order State Persistence**: Track order lifecycle in PostgreSQL
4. **Webhook Integration**: Receive real-time order status updates from Alpaca
5. **Retry Logic**: Handle transient failures with exponential backoff
6. **Position Tracking**: Maintain current positions from order fills

### Constraints

- Must integrate with Alpaca Paper Trading API (https://paper-api.alpaca.markets)
- Must support both DRY_RUN (logging only) and live paper trading modes
- Must guarantee exactly-once order submission semantics
- Must be compatible with existing T3 Signal Service output format
- Must support circuit breakers and safety mechanisms for production (Phase 1)

### Current System State

From T3, we have:
- Signal Service generating `target_weight` for each symbol (-1.0, 0.0, 1.0)
- FastAPI application pattern established
- PostgreSQL database with model registry
- Environment configuration pattern via .env file

## Decision

We will implement the Execution Gateway as a FastAPI microservice with the following architecture:

### 1. Idempotency Pattern

**Deterministic Client Order ID Generation:**

```python
def generate_client_order_id(order: OrderRequest, strategy_id: str) -> str:
    """
    Generate deterministic client_order_id to ensure idempotency.

    Same order parameters + same date = same ID
    This prevents duplicate submissions on retry.
    """
    today = date.today().isoformat()
    raw = f"{order.symbol}|{order.side}|{order.qty}|{order.limit_price}|{strategy_id}|{today}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
```

**Key Properties:**
- Same order parameters on the same day → same ID
- Alpaca API rejects duplicate client_order_id → safe retry
- Date component ensures different IDs across days
- 24-character hex string (sufficient entropy, Alpaca-compatible)

### 2. DRY_RUN Mode Implementation

The system will support two execution modes controlled by the `DRY_RUN` environment variable:

**Mode 1: DRY_RUN=true (Default for Development)**
- Log order details to stdout and database
- Do NOT call Alpaca API
- Mark orders with status='dry_run'
- Allows testing order logic without broker interaction

**Mode 2: DRY_RUN=false (Paper Trading)**
- Submit orders to Alpaca Paper Trading API
- Receive real order_id from broker
- Track order lifecycle via webhooks
- Mark orders with status='pending_new' initially

**Implementation:**
```python
if DRY_RUN:
    logger.info(f"[DRY_RUN] Would submit order: {payload}")
    db_order = create_order_record(
        client_order_id=client_order_id,
        status="dry_run",
        broker_order_id=None,
        ...
    )
else:
    response = alpaca_client.submit_order(**payload)
    db_order = create_order_record(
        client_order_id=client_order_id,
        status="pending_new",
        broker_order_id=response.id,
        ...
    )
```

### 3. Database Schema

**Orders Table:**

```sql
CREATE TABLE IF NOT EXISTS orders (
    -- Primary key: deterministic client_order_id
    client_order_id TEXT PRIMARY KEY,

    -- Order parameters
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT CHECK (side IN ('buy', 'sell')) NOT NULL,
    qty NUMERIC NOT NULL,
    order_type TEXT DEFAULT 'market',
    limit_price NUMERIC,
    time_in_force TEXT DEFAULT 'day',

    -- Status tracking
    status TEXT NOT NULL,  -- dry_run, pending_new, filled, cancelled, rejected
    broker_order_id TEXT,  -- Alpaca's order_id (null for dry_run)

    -- Error tracking
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    filled_at TIMESTAMPTZ,

    -- Fill details (populated by webhooks)
    filled_qty NUMERIC DEFAULT 0,
    filled_avg_price NUMERIC
);

CREATE INDEX idx_orders_strategy ON orders(strategy_id, created_at DESC);
CREATE INDEX idx_orders_symbol ON orders(symbol, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_broker_id ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;
```

**Positions Table:**

```sql
CREATE TABLE IF NOT EXISTS positions (
    -- Primary key: symbol
    symbol TEXT PRIMARY KEY,

    -- Position details
    qty NUMERIC NOT NULL,
    avg_entry_price NUMERIC NOT NULL,
    current_price NUMERIC,

    -- P&L tracking
    unrealized_pl NUMERIC,
    realized_pl NUMERIC DEFAULT 0,

    -- Timestamps
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_trade_at TIMESTAMPTZ
);

CREATE INDEX idx_positions_updated ON positions(updated_at DESC);
```

### 4. API Endpoints

**FastAPI Application Structure:**

```python
# apps/execution_gateway/main.py

@app.post("/api/v1/orders", response_model=OrderResponse)
async def submit_order(order: OrderRequest):
    """
    Submit order with idempotent retry semantics.

    Request:
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "time_in_force": "day"
        }

    Response:
        {
            "client_order_id": "abc123...",
            "status": "dry_run" | "pending_new",
            "broker_order_id": "..." | null
        }
    """
    client_order_id = generate_client_order_id(order, STRATEGY_ID)

    # Check if order already exists (idempotency)
    existing_order = get_order_by_client_id(client_order_id)
    if existing_order:
        return existing_order

    # Submit to Alpaca or log (based on DRY_RUN)
    result = submit_to_broker(order, client_order_id)
    return result


@app.post("/api/v1/webhooks/orders")
async def order_webhook(event: OrderEvent):
    """
    Receive order status updates from Alpaca.

    Updates:
    - order status (filled, cancelled, rejected)
    - filled_qty, filled_avg_price
    - positions table on fills
    """
    update_order_status(event)

    if event.event == "fill":
        update_positions_on_fill(event)

    return {"status": "ok"}


@app.get("/api/v1/positions")
async def get_positions():
    """
    Retrieve current positions.

    Response:
        {
            "positions": [
                {"symbol": "AAPL", "qty": 10, "avg_entry_price": 150.0},
                {"symbol": "MSFT", "qty": -5, "avg_entry_price": 300.0}
            ]
        }
    """
    return fetch_positions_from_db()


@app.get("/api/v1/orders/{client_order_id}")
async def get_order(client_order_id: str):
    """Retrieve order by client_order_id."""
    return fetch_order_from_db(client_order_id)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "execution_gateway",
        "dry_run": DRY_RUN,
        "alpaca_connected": check_alpaca_connection()
    }
```

### 5. Alpaca Client Configuration

**Environment Variables (.env):**

```bash
# Alpaca API Configuration
ALPACA_API_KEY_ID=your_key_here
ALPACA_API_SECRET_KEY=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Execution Mode
DRY_RUN=true  # Set to false for actual paper trading

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform

# Strategy Configuration
STRATEGY_ID=alpha_baseline
```

**Alpaca Client Initialization:**

```python
# apps/execution_gateway/alpaca_client.py

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

class AlpacaExecutor:
    def __init__(self, api_key: str, secret_key: str, base_url: str):
        self.client = TradingClient(api_key, secret_key, paper=True)
        self.base_url = base_url

    def submit_market_order(self, symbol: str, qty: int, side: str,
                           client_order_id: str) -> OrderResponse:
        """Submit market order to Alpaca."""
        request = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id
        )
        return self.client.submit_order(request)

    def get_order(self, order_id: str) -> Order:
        """Retrieve order by broker order_id."""
        return self.client.get_order_by_id(order_id)

    def cancel_order(self, order_id: str):
        """Cancel order by broker order_id."""
        return self.client.cancel_order_by_id(order_id)
```

### 6. Retry Logic with Exponential Backoff

```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
)
async def submit_to_alpaca_with_retry(order: OrderRequest, client_order_id: str):
    """
    Submit order to Alpaca with retry logic.

    Retry policy:
    - Max 3 attempts
    - Exponential backoff: 2s, 4s, 8s
    - Only retry on transient errors (connection, timeout)
    - Do NOT retry on validation errors (400)
    """
    try:
        response = alpaca_client.submit_market_order(
            symbol=order.symbol,
            qty=order.qty,
            side=order.side,
            client_order_id=client_order_id
        )
        return response
    except AlpacaAPIError as e:
        if e.status_code == 400:
            # Bad request - do not retry
            raise ValueError(f"Invalid order: {e.message}")
        elif e.status_code in (422, 403):
            # Unprocessable entity or forbidden - do not retry
            raise ValueError(f"Order rejected: {e.message}")
        else:
            # Transient error - will retry
            raise ConnectionError(f"Alpaca API error: {e.message}")
```

### 7. Webhook Security

Alpaca webhooks should be authenticated to prevent spoofed events:

```python
import hmac
import hashlib

def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify webhook signature from Alpaca.

    Alpaca sends HMAC-SHA256 signature in header.
    """
    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)


@app.post("/api/v1/webhooks/orders")
async def order_webhook(request: Request):
    """Authenticated webhook endpoint."""
    body = await request.body()
    signature = request.headers.get("X-Alpaca-Signature")

    if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = OrderEvent.parse_raw(body)
    update_order_status(event)
    return {"status": "ok"}
```

### 8. Migration Script

```sql
-- migrations/002_create_execution_tables.sql

-- Orders table for tracking order lifecycle
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT CHECK (side IN ('buy', 'sell')) NOT NULL,
    qty NUMERIC NOT NULL,
    order_type TEXT DEFAULT 'market',
    limit_price NUMERIC,
    time_in_force TEXT DEFAULT 'day',
    status TEXT NOT NULL,
    broker_order_id TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    filled_qty NUMERIC DEFAULT 0,
    filled_avg_price NUMERIC
);

CREATE INDEX idx_orders_strategy ON orders(strategy_id, created_at DESC);
CREATE INDEX idx_orders_symbol ON orders(symbol, created_at DESC);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_broker_id ON orders(broker_order_id) WHERE broker_order_id IS NOT NULL;

-- Positions table for tracking current holdings
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    qty NUMERIC NOT NULL,
    avg_entry_price NUMERIC NOT NULL,
    current_price NUMERIC,
    unrealized_pl NUMERIC,
    realized_pl NUMERIC DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_trade_at TIMESTAMPTZ
);

CREATE INDEX idx_positions_updated ON positions(updated_at DESC);

-- Trigger to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_positions_updated_at BEFORE UPDATE ON positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

## Consequences

### Benefits

1. **Idempotency Guarantees Exactly-Once Semantics**
   - Deterministic client_order_id prevents duplicates on retry
   - Safe to retry failed requests without checking database first
   - Alpaca API enforces uniqueness of client_order_id

2. **DRY_RUN Mode Enables Safe Testing**
   - Can test order generation logic without broker submission
   - No risk of accidental real trades during development
   - Easy to switch between modes via environment variable

3. **Database Persistence Provides Audit Trail**
   - Full order history with timestamps
   - Error tracking with retry counts
   - Supports reconciliation with broker state

4. **Webhook Integration for Real-Time Updates**
   - No polling required for order status
   - Immediate position updates on fills
   - Reduces API calls to Alpaca

5. **Retry Logic Handles Transient Failures**
   - Exponential backoff prevents thundering herd
   - Intelligent retry only on recoverable errors
   - Max retry limit prevents infinite loops

### Tradeoffs

1. **Deterministic ID Limitation**
   - **Tradeoff**: Cannot submit identical order twice on same day
   - **Rationale**: This is acceptable because:
     - Our strategy generates signals once per day
     - Duplicate orders on same day likely indicate a bug
     - Can override by changing limit_price or qty slightly
   - **Mitigation**: Add optional `idempotency_key` parameter for manual overrides

2. **Database as Source of Truth**
   - **Tradeoff**: Database can diverge from broker state
   - **Rationale**: Webhooks keep them synchronized, but network issues can cause lag
   - **Mitigation**: Implement periodic reconciliation job (T5)

3. **DRY_RUN Doesn't Test Broker Rejection**
   - **Tradeoff**: DRY_RUN mode skips Alpaca API validation
   - **Rationale**: Some errors only appear when submitting to broker (e.g., insufficient buying power)
   - **Mitigation**:
     - Run integration tests in paper trading mode
     - Add pre-flight validation (check positions, buying power)

4. **Webhook Dependency**
   - **Tradeoff**: System relies on Alpaca webhooks for order updates
   - **Rationale**: Polling is less efficient and adds latency
   - **Mitigation**:
     - Implement fallback polling for webhook failures
     - Add webhook health monitoring

### Risks

1. **Clock Skew in Deterministic ID**
   - **Risk**: Server time mismatch could cause ID collision or miss
   - **Mitigation**: Use UTC consistently, add timestamp to order record

2. **Webhook Spoofing**
   - **Risk**: Attacker could send fake fill events
   - **Mitigation**: HMAC signature verification required

3. **Database Connection Failure**
   - **Risk**: Cannot persist order if database is down
   - **Mitigation**:
     - Return 503 Service Unavailable if DB unreachable
     - Add connection pooling and retry logic
     - Consider in-memory queue for temporary buffering (future)

4. **Alpaca API Rate Limits**
   - **Risk**: 200 requests/minute limit on paper trading
   - **Mitigation**:
     - Batch order submission where possible
     - Add rate limiting middleware
     - Monitor API usage

### Follow-Up Tasks

1. **Phase 1 (Days 16-20): Core Implementation**
   - [ ] Create FastAPI application structure
   - [ ] Implement deterministic client_order_id generation
   - [ ] Add DRY_RUN mode support
   - [ ] Create database migration (002_create_execution_tables.sql)
   - [ ] Implement Alpaca client wrapper
   - [ ] Add /orders POST endpoint with idempotency
   - [ ] Write unit tests (target: 90%+ coverage)

2. **Phase 2 (Days 21-23): Webhook Integration**
   - [ ] Implement webhook endpoint with signature verification
   - [ ] Add order status update handler
   - [ ] Add position update on fill logic
   - [ ] Test webhook with Alpaca webhook simulator
   - [ ] Add webhook health monitoring

3. **Phase 3 (Days 24-25): Testing & Documentation**
   - [ ] Integration tests with Alpaca Paper API
   - [ ] Manual testing scripts (dry_run and paper modes)
   - [ ] Add implementation guide (t4-execution-gateway.md)
   - [ ] Update CLAUDE.md with T4 completion
   - [ ] Document Alpaca setup and webhook configuration

4. **Future Enhancements (Post-P0)**
   - [ ] Add reconciliation job to sync with broker state
   - [ ] Implement circuit breaker for excessive rejections
   - [ ] Add order modification support
   - [ ] Support limit orders and other order types
   - [ ] Add real-time position P&L calculation
   - [ ] Implement rate limiting middleware

### Integration with Existing System

**T3 Signal Service → T4 Execution Gateway:**

```python
# Signal Service generates target weights
signals = [
    {"symbol": "AAPL", "target_weight": 1.0},   # Long position
    {"symbol": "MSFT", "target_weight": 0.0},   # Neutral
    {"symbol": "GOOGL", "target_weight": -1.0}  # Short position
]

# Execution Gateway converts weights to orders
def convert_signals_to_orders(signals: List[Signal], portfolio_value: float):
    """Convert target weights to executable orders."""
    orders = []

    for signal in signals:
        if signal.target_weight == 0:
            continue  # No position change

        # Calculate target dollar amount
        target_dollars = portfolio_value * abs(signal.target_weight)

        # Get current price (from last quote)
        current_price = get_current_price(signal.symbol)

        # Calculate shares
        qty = int(target_dollars / current_price)

        if qty > 0:
            orders.append(OrderRequest(
                symbol=signal.symbol,
                side="buy" if signal.target_weight > 0 else "sell",
                qty=qty,
                order_type="market",
                time_in_force="day"
            ))

    return orders
```

**End-to-End Flow:**

```
1. T3 Signal Service generates signals
   → POST /api/v1/signals/generate
   → Returns: [{"symbol": "AAPL", "target_weight": 1.0}, ...]

2. Orchestrator converts signals to orders
   → calculate_order_quantities(signals, portfolio_value)
   → Returns: [{"symbol": "AAPL", "side": "buy", "qty": 10}, ...]

3. T4 Execution Gateway submits orders
   → POST /api/v1/orders (for each order)
   → Generates deterministic client_order_id
   → Submits to Alpaca (or logs if DRY_RUN=true)
   → Returns: {"client_order_id": "...", "status": "pending_new"}

4. Alpaca processes orders and sends webhooks
   → POST /api/v1/webhooks/orders
   → Updates order status to "filled"
   → Updates positions table

5. T5 Position Tracker syncs state
   → GET /api/v1/positions
   → Reconciles with broker positions
```

### Migration Plan

1. **Development Phase (DRY_RUN=true)**
   - Develop and test all endpoints locally
   - Run unit tests with mocked Alpaca client
   - Verify order logic without broker submission

2. **Integration Testing (Paper Trading)**
   - Set DRY_RUN=false in test environment
   - Submit real orders to Alpaca Paper Trading API
   - Verify webhooks are received and processed
   - Check positions table updates correctly

3. **Production Deployment (Paper Trading)**
   - Deploy to production environment with DRY_RUN=false
   - Start with small position sizes (1 share per order)
   - Monitor for 1 week before scaling up
   - Add circuit breakers and safety checks

4. **Future: Live Trading (Post-P0)**
   - Switch ALPACA_BASE_URL to live API
   - Implement additional safety checks
   - Start with minimal capital allocation
   - Gradually increase position sizes

## References

- [Alpaca Trading API Documentation](https://docs.alpaca.markets/docs/trading-api)
- [Alpaca Webhooks Guide](https://docs.alpaca.markets/docs/webhooks)
- [ADR 0003: Baseline Strategy with Qlib and MLflow](./0003-baseline-strategy-with-qlib-and-mlflow.md)
- [ADR 0004: Signal Service Architecture](./0004-signal-service-architecture.md)
- [P0 Tickets - T4 Requirements](../ARCHIVE/TASKS_HISTORY/P0_TASKS_DONE.md)
- [Trading Platform Realization Plan - Phase 6](../trading_platform_realization_plan.md)

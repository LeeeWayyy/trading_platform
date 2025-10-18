# ADR-0006: Orchestrator Service (T5)

**Status:** Proposed
**Date:** 2024-10-17
**Deciders:** Engineering Team
**Technical Story:** T5 - Orchestrator Service

## Context and Problem Statement

We have successfully built two independent microservices:
- **T3 Signal Service** (port 8001): Generates trading signals from ML models
- **T4 Execution Gateway** (port 8002): Submits orders to Alpaca with idempotency

The problem is these services don't communicate with each other. We need an orchestrator to:
1. Fetch signals from Signal Service (T3)
2. Convert signals to executable orders
3. Submit orders to Execution Gateway (T4)
4. Track order status and update positions
5. Handle errors and retries across the pipeline

**Key Questions:**
- How should services communicate? (REST, async messaging, direct function calls)
- How do we handle failures in each step?
- What happens if signal generation succeeds but order submission fails?
- How do we track the complete flow from signal → order → fill?
- Should the orchestrator be a separate service or embedded in one of the existing services?

## Decision Drivers

1. **Simplicity** - Minimize operational complexity for MVP
2. **Observability** - Clear visibility into the complete trading flow
3. **Error Handling** - Graceful degradation and retry logic
4. **Testability** - Easy to test in DRY_RUN mode
5. **Performance** - Complete flow should execute in < 5 seconds
6. **Idempotency** - Safe to retry the entire orchestration

## Considered Options

### Option 1: Standalone Orchestrator Service (Separate Microservice)

**Architecture:**
```
┌─────────────────────┐
│ Orchestrator Service│ (port 8003)
│  - Cron scheduler   │
│  - Signal fetcher   │
│  - Order mapper     │
│  - Status tracker   │
└─────────────────────┘
         ↓ HTTP
    ┌────────┴────────┐
    ↓                 ↓
┌─────────┐      ┌─────────┐
│ Signal  │      │Execution│
│ Service │      │ Gateway │
│ (8001)  │      │ (8002)  │
└─────────┘      └─────────┘
```

**Pros:**
- Clean separation of concerns
- Easy to scale independently
- Can replace scheduler (cron → Airflow) without changing other services
- Clear ownership boundaries

**Cons:**
- More services to deploy and monitor
- Additional network hops (latency)
- More complex infrastructure (3 services instead of 2)
- Requires inter-service authentication

### Option 2: Embedded in Execution Gateway (Extend T4)

**Architecture:**
```
┌──────────────────────────────┐
│ Execution Gateway (8002)     │
│  ┌────────────────────────┐  │
│  │ Orchestrator Module    │  │
│  │  - Signal fetcher      │  │
│  │  - Order mapper        │  │
│  │  - Scheduler           │  │
│  └────────────────────────┘  │
│  ┌────────────────────────┐  │
│  │ Order Submission       │  │
│  └────────────────────────┘  │
└──────────────────────────────┘
         ↓ HTTP
    ┌────────┐
    │ Signal │
    │ Service│
    │ (8001) │
    └────────┘
```

**Pros:**
- One less service to deploy
- Direct access to order submission logic (no network hop)
- Simpler deployment

**Cons:**
- Execution Gateway becomes heavyweight
- Mixing concerns (orchestration + execution)
- Harder to replace scheduler

### Option 3: Python Script (`paper_run.py`) with Direct Imports

**Architecture:**
```
┌─────────────────────────────┐
│ paper_run.py (CLI script)   │
│  - Imports SignalGenerator  │
│  - Imports AlpacaExecutor   │
│  - Runs end-to-end flow     │
└─────────────────────────────┘
         ↓ Direct function calls
    ┌────────┴────────┐
    ↓                 ↓
┌─────────┐      ┌─────────┐
│ Signal  │      │Execution│
│Generator│      │ Gateway │
│ (lib)   │      │ (lib)   │
└─────────┘      └─────────┘
```

**Pros:**
- Simplest possible implementation
- No network overhead (direct Python imports)
- Easy to test locally
- Perfect for MVP/POC

**Cons:**
- Tight coupling (harder to evolve services independently)
- Can't scale services independently
- No service-to-service boundaries
- Harder to monitor (no REST API)

## Decision Outcome

**Chosen option:** **Option 1 - Standalone Orchestrator Service**

**Rationale:**
1. **Separation of Concerns** - Each service has a clear responsibility:
   - Signal Service: ML predictions
   - Execution Gateway: Order execution
   - Orchestrator: Workflow coordination

2. **Production-Ready** - Service boundaries enable:
   - Independent scaling (e.g., scale signal generation separately)
   - Service-level monitoring and alerting
   - Gradual migration to Airflow/Prefect later

3. **Testing** - Clear API contracts make integration testing easier

4. **Evolution Path** - Can later replace with Airflow DAG without changing T3/T4

**Trade-off Accepted:**
- More complex deployment (3 services instead of 2)
- Additional network latency (~10-20ms per HTTP call)

This is acceptable for MVP because:
- Performance target is < 5 seconds (network latency is negligible)
- Operational complexity is manageable with Docker Compose
- Clean architecture pays off as system grows

## Architecture Details

### 1. Orchestrator Service Design

**Core Components:**

```python
# apps/orchestrator/main.py

class TradingOrchestrator:
    """
    Coordinates the complete trading flow:
    1. Fetch signals from Signal Service
    2. Map signals to orders (position sizing, risk limits)
    3. Submit orders to Execution Gateway
    4. Track order status
    5. Report results
    """

    def __init__(
        self,
        signal_service_url: str,
        execution_gateway_url: str,
        capital: Decimal,
        max_position_size: Decimal,
    ):
        self.signal_client = SignalServiceClient(signal_service_url)
        self.execution_client = ExecutionGatewayClient(execution_gateway_url)
        self.capital = capital
        self.max_position_size = max_position_size

    async def run_daily_strategy(
        self,
        symbols: List[str],
        as_of_date: Optional[date] = None
    ) -> OrchestrationResult:
        """
        Execute complete daily trading strategy.

        Returns:
            OrchestrationResult with signals, orders, and execution status
        """
        # Phase 1: Fetch signals
        signals = await self._fetch_signals(symbols, as_of_date)

        # Phase 2: Map signals to orders
        orders = self._map_signals_to_orders(signals)

        # Phase 3: Submit orders
        submissions = await self._submit_orders(orders)

        # Phase 4: Track execution
        executions = await self._track_executions(submissions)

        return OrchestrationResult(
            signals=signals,
            orders=orders,
            submissions=submissions,
            executions=executions
        )
```

### 2. Signal-to-Order Mapping

**Position Sizing Algorithm:**

```python
def _map_signals_to_orders(
    self,
    signals: List[Signal]
) -> List[OrderRequest]:
    """
    Convert trading signals to executable orders.

    Position sizing logic:
    1. Calculate dollar amount per signal: capital * |target_weight|
    2. Apply max position size limit
    3. Convert to shares (round down)
    4. Filter out positions < 1 share

    Example:
        Capital = $100,000
        Signal: AAPL target_weight = 0.333 (33.3% long)
        Current price = $150

        Dollar amount = $100,000 * 0.333 = $33,300
        Shares = $33,300 / $150 = 222 shares

        Order: BUY 222 AAPL @ market
    """
    orders = []

    for signal in signals:
        # Skip zero-weight signals
        if signal.target_weight == 0:
            continue

        # Calculate dollar amount
        dollar_amount = abs(self.capital * signal.target_weight)

        # Apply max position size
        dollar_amount = min(dollar_amount, self.max_position_size)

        # Get current price (from signal metadata or market data)
        current_price = self._get_current_price(signal.symbol)

        # Convert to shares (round down)
        qty = int(dollar_amount / current_price)

        # Filter out fractional shares
        if qty < 1:
            logger.warning(
                f"Skipping {signal.symbol}: qty < 1 share "
                f"(dollar_amount={dollar_amount}, price={current_price})"
            )
            continue

        # Determine side (buy if weight > 0, sell if weight < 0)
        side = "buy" if signal.target_weight > 0 else "sell"

        # Create order request
        orders.append(OrderRequest(
            symbol=signal.symbol,
            side=side,
            qty=qty,
            order_type="market",  # Market orders for simplicity in MVP
            time_in_force="day"
        ))

    return orders
```

### 3. Error Handling Strategy

**Failure Modes and Recovery:**

| Failure Point | Behavior | Recovery |
|---------------|----------|----------|
| Signal Service down | Skip this run, log alert | Retry next scheduled run |
| Signal Service returns 0 signals | Log warning, no orders | Continue normally |
| Execution Gateway down | Queue orders, retry | Exponential backoff (5min, 10min, 20min) |
| Single order rejected | Continue with other orders | Log rejection, alert if > 50% rejected |
| Network timeout | Retry with idempotency | client_order_id prevents duplicates |

**Retry Logic:**

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    retry=retry_if_exception_type(HTTPError),
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
async def _submit_order(
    self,
    order: OrderRequest
) -> OrderSubmission:
    """
    Submit order with automatic retry for transient failures.

    Retry policy:
    - Max 3 attempts
    - Exponential backoff: 4s, 8s, 16s
    - Only retry on HTTP errors (not validation errors)
    """
    response = await self.execution_client.submit_order(order)
    return OrderSubmission.from_response(response)
```

### 4. Scheduling Strategy

**For MVP (P0):**

Use APScheduler for simplicity:

```python
# apps/orchestrator/scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

# Run daily at market open (9:30 AM ET)
scheduler.add_job(
    orchestrator.run_daily_strategy,
    trigger=CronTrigger(
        day_of_week='mon-fri',  # Weekdays only
        hour=9,
        minute=30,
        timezone='America/New_York'
    ),
    args=[['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']],
    id='daily_strategy',
    replace_existing=True
)

scheduler.start()
```

**For Production (P1+):**

Migrate to Airflow for:
- DAG visualization
- Task dependencies
- Backfilling
- Alerting integrations

```python
# airflow/dags/daily_trading.py (future)

from airflow import DAG
from airflow.operators.http_operator import SimpleHttpOperator

dag = DAG(
    'daily_trading_strategy',
    schedule_interval='30 9 * * 1-5',  # 9:30 AM weekdays
    catchup=False
)

fetch_signals = SimpleHttpOperator(
    task_id='fetch_signals',
    http_conn_id='signal_service',
    endpoint='/api/v1/signals/generate',
    method='POST',
    data=json.dumps({'symbols': ['AAPL', 'MSFT', 'GOOGL']}),
    dag=dag
)

submit_orders = SimpleHttpOperator(
    task_id='submit_orders',
    http_conn_id='execution_gateway',
    endpoint='/api/v1/orders',
    method='POST',
    dag=dag
)

fetch_signals >> submit_orders
```

### 5. Observability and Logging

**Structured Logging:**

```python
logger.info(
    "Orchestration run started",
    extra={
        "run_id": str(uuid.uuid4()),
        "as_of_date": as_of_date.isoformat(),
        "num_symbols": len(symbols),
        "capital": float(self.capital)
    }
)

logger.info(
    "Signals fetched",
    extra={
        "run_id": run_id,
        "num_signals": len(signals),
        "num_longs": sum(1 for s in signals if s.target_weight > 0),
        "num_shorts": sum(1 for s in signals if s.target_weight < 0),
        "model_version": signals_metadata["model_version"]
    }
)

logger.info(
    "Orders submitted",
    extra={
        "run_id": run_id,
        "num_orders": len(submissions),
        "num_accepted": sum(1 for s in submissions if s.status == "accepted"),
        "num_rejected": sum(1 for s in submissions if s.status == "rejected")
    }
)
```

**Metrics to Track:**

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `orchestration.run.duration_seconds` | Total orchestration time | > 60s |
| `orchestration.signals.count` | Number of signals fetched | == 0 |
| `orchestration.orders.submitted` | Orders submitted | < 50% of signals |
| `orchestration.orders.rejected` | Orders rejected by broker | > 10% |
| `orchestration.errors.count` | Errors during run | > 0 |

### 6. API Endpoints

**Orchestrator Service API:**

```
GET /health
  - Health check

POST /api/v1/orchestration/run
  - Manually trigger orchestration run
  - Request body: { "symbols": [...], "as_of_date": "2024-12-31" }
  - Returns: OrchestrationResult

GET /api/v1/orchestration/runs
  - List recent orchestration runs
  - Query params: limit, offset

GET /api/v1/orchestration/runs/{run_id}
  - Get details of specific run
  - Returns: OrchestrationResult with full details
```

### 7. Database Schema

**Orchestration Runs Table:**

```sql
CREATE TABLE orchestration_runs (
    id SERIAL PRIMARY KEY,
    run_id UUID UNIQUE NOT NULL,
    strategy_id VARCHAR(100) NOT NULL,
    as_of_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,  -- running, completed, failed, partial

    -- Input
    symbols TEXT[] NOT NULL,
    capital NUMERIC(15, 2) NOT NULL,

    -- Metrics
    num_signals INTEGER DEFAULT 0,
    num_orders_submitted INTEGER DEFAULT 0,
    num_orders_accepted INTEGER DEFAULT 0,
    num_orders_rejected INTEGER DEFAULT 0,
    num_orders_filled INTEGER DEFAULT 0,

    -- Timing
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10, 3),

    -- Error tracking
    error_message TEXT,

    -- Metadata
    model_version VARCHAR(50),
    signal_service_response JSONB,
    execution_gateway_responses JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_orchestration_runs_status ON orchestration_runs(status);
CREATE INDEX idx_orchestration_runs_as_of_date ON orchestration_runs(as_of_date DESC);
CREATE INDEX idx_orchestration_runs_run_id ON orchestration_runs(run_id);
```

**Signal-Order Mapping Table:**

```sql
CREATE TABLE signal_order_mappings (
    id SERIAL PRIMARY KEY,
    run_id UUID REFERENCES orchestration_runs(run_id),

    -- Signal
    symbol VARCHAR(10) NOT NULL,
    predicted_return NUMERIC(10, 6),
    rank INTEGER,
    target_weight NUMERIC(5, 4),

    -- Order
    client_order_id TEXT,
    order_qty INTEGER,
    order_side VARCHAR(10),

    -- Execution
    broker_order_id TEXT,
    order_status VARCHAR(20),
    filled_qty NUMERIC(15, 4),
    filled_avg_price NUMERIC(15, 4),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_signal_order_mappings_run_id ON signal_order_mappings(run_id);
CREATE INDEX idx_signal_order_mappings_symbol ON signal_order_mappings(symbol);
```

## Consequences

### Positive

1. **Clean Architecture** - Each service has single responsibility
2. **Independent Scaling** - Can scale services based on load
3. **Testability** - Clear contracts enable integration tests
4. **Monitoring** - Service-level metrics and alerts
5. **Evolution Path** - Can migrate to Airflow without changing T3/T4

### Negative

1. **Operational Complexity** - 3 services instead of 2
2. **Network Latency** - Additional HTTP calls (~20ms overhead)
3. **Distributed Tracing** - Need correlation IDs across services
4. **Error Handling** - More failure modes to handle

### Mitigation Strategies

1. **Docker Compose** - Simplify local development with single `docker-compose up`
2. **Health Checks** - Implement `/health` endpoint on all services
3. **Correlation IDs** - Pass `X-Correlation-ID` header through all requests
4. **Circuit Breakers** - Prevent cascading failures with timeout/retry limits

## Implementation Plan

### Phase 1: Core Orchestration (Days 1-3)

**Deliverables:**
- Orchestrator service skeleton (FastAPI)
- Signal Service HTTP client
- Execution Gateway HTTP client
- Basic signal-to-order mapping
- Manual run endpoint (POST /api/v1/orchestration/run)

**Tests:**
- Unit tests for signal-to-order mapping
- Integration test (mock Signal Service + Execution Gateway)
- End-to-end test with DRY_RUN mode

### Phase 2: Scheduling (Days 4-5)

**Deliverables:**
- APScheduler integration
- Cron trigger for daily runs
- Market hours validation (don't run on weekends)
- Run history tracking in database

**Tests:**
- Scheduler initialization test
- Cron expression validation
- Database persistence test

### Phase 3: Error Handling & Observability (Days 6-7)

**Deliverables:**
- Retry logic with exponential backoff
- Structured logging with correlation IDs
- Metrics collection (Prometheus format)
- Alerting rules (high rejection rate, zero signals, etc.)

**Tests:**
- Retry behavior tests
- Error handling tests (service down, timeout, etc.)
- Metrics validation

### Phase 4: Documentation & Validation (Days 8-10)

**Deliverables:**
- Implementation guide (docs/IMPLEMENTATION_GUIDES/t5-orchestrator.md)
- API documentation (OpenAPI spec)
- Docker Compose setup
- End-to-end validation script

**Tests:**
- Full integration test (Signal Service + Execution Gateway + Orchestrator)
- Performance test (< 5s end-to-end)
- Idempotency test (safe to retry)

## References

- [T3 Signal Service](./0004-signal-service-architecture.md)
- [T4 Execution Gateway](./0005-execution-gateway-architecture.md)
- [Microservices Patterns - Chris Richardson](https://microservices.io/patterns/microservices.html)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [Airflow Documentation](https://airflow.apache.org/)

## Related Decisions

- ADR-0004: Signal Service Architecture
- ADR-0005: Execution Gateway Architecture

## Notes

**Why not use Airflow from the start?**

Airflow is powerful but adds complexity:
- Requires separate Airflow server + scheduler + worker
- More infrastructure to deploy (PostgreSQL for Airflow metadata)
- Steeper learning curve for team

For MVP (P0), APScheduler provides 80% of the value with 20% of the complexity. We can migrate to Airflow in P1 when we need:
- Complex DAGs with branching logic
- Backfilling historical runs
- Advanced monitoring and alerting
- Multiple concurrent strategies

**Position Sizing - Future Enhancements:**

MVP uses simple equal-weight allocation within long/short groups. Future enhancements:
- Kelly criterion for optimal position sizing
- Volatility-based sizing (scale by inverse volatility)
- Risk parity (equal risk contribution)
- Dynamic capital allocation based on model confidence

**Market Data for Position Sizing:**

MVP uses simple approach: fetch latest price from Alpaca API. Future enhancements:
- Cache prices in Redis for performance
- Use VWAP or TWAP for large orders
- Consider bid-ask spread for limit orders

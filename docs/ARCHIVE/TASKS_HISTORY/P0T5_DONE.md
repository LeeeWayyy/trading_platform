---
id: P0T5
title: "Trade Orchestrator"
phase: P0
task: T5
priority: P0
owner: "@development-team"
state: DONE
created: 2025-10-20
started: 2025-10-20
completed: 2025-10-20
duration: "Completed prior to task lifecycle system"
dependencies: []
related_adrs: []
related_docs: []
---


# P0T5: Trade Orchestrator ✅

**Phase:** P0 (MVP Core, 0-45 days)
**Status:** DONE (Completed prior to task lifecycle system)
**Priority:** P0
**Owner:** @development-team

---

## Original Implementation Guide

**Note:** This content was migrated from `docs/IMPLEMENTATION_GUIDES/p0t5-orchestrator.md`
and represents work completed before the task lifecycle management system was implemented.

---

**Status:** Complete  
**Author:** T5 Implementation Team  
**Date:** 2024-10-17  
**Related ADR:** [ADR-0006: Orchestrator Service](../../ADRs/0006-orchestrator-service.md)

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Setup](#setup)
4. [Components](#components)
5. [API Reference](#api-reference)
6. [Testing](#testing)
7. [Deployment](#deployment)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The Orchestrator Service (T5) coordinates the complete trading workflow by integrating Signal Service (T3) and Execution Gateway (T4). It fetches trading signals, converts them to executable orders with position sizing, submits them for execution, and tracks the complete lifecycle.

### What It Does

1. **Fetches Signals** from Signal Service (ML predictions + target weights)
2. **Position Sizing** converts target weights to order quantities
3. **Order Submission** sends orders to Execution Gateway
4. **Tracks Results** persists complete workflow to database

### Key Features

- **Complete Workflow**: Signals → Orders → Execution in one API call
- **Position Sizing**: Automatic conversion of weights to shares with max limits
- **Error Handling**: Partial failure support (some orders succeed, some fail)
- **Persistence**: Full audit trail in PostgreSQL
- **Observability**: Structured logging and health checks

---

## Architecture

### Service Diagram

```
┌─────────────────────────────────────────────────┐
│        Orchestrator Service (Port 8003)         │
│  ┌───────────────────────────────────────────┐  │
│  │  POST /api/v1/orchestration/run           │  │
│  │  GET  /api/v1/orchestration/runs          │  │
│  │  GET  /api/v1/orchestration/runs/{id}     │  │
│  │  GET  /health                              │  │
│  └───────────────────────────────────────────┘  │
└────────────┬──────────────────────┬─────────────┘
             │                      │
      ┌──────▼──────┐        ┌─────▼──────┐
      │   Signal    │        │ Execution  │
      │   Service   │        │  Gateway   │
      │  (Port 8001)│        │ (Port 8002)│
      └─────────────┘        └────────────┘
             │
      ┌──────▼──────────────────┐
      │  PostgreSQL Database    │
      │  - orchestration_runs   │
      │  - signal_order_mappings│
      └─────────────────────────┘
```

### Data Flow

```
1. Client → POST /api/v1/orchestration/run
   ↓
2. Orchestrator → Signal Service: Fetch signals
   ↓
3. Orchestrator: Map signals to orders (position sizing)
   ↓
4. Orchestrator → Execution Gateway: Submit orders
   ↓
5. Orchestrator → Database: Persist results
   ↓
6. Orchestrator → Client: Return OrchestrationResult
```

### Position Sizing Algorithm

```python
# Example: 33.3% weight, $100k capital, $150/share, $20k max
dollar_amount = abs(capital × target_weight)  # $33,300
dollar_amount = min(dollar_amount, max_pos)    # $20,000 (capped)
qty = floor(dollar_amount / price)             # 133 shares
side = "buy" if weight > 0 else "sell"         # "buy"
```

---

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (with migrations 001, 002, 003 applied)
- Signal Service running on port 8001
- Execution Gateway running on port 8002

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Or specific dependencies
pip install httpx psycopg[binary] tenacity fastapi uvicorn
```

### Database Setup

```bash
# Apply migration 003
psql $DATABASE_URL -f migrations/003_create_orchestration_tables.sql

# Verify tables created
psql $DATABASE_URL -c "\dt orchestration*"
#  orchestration_runs
#  signal_order_mappings
```

### Environment Configuration

```bash
# .env file
SIGNAL_SERVICE_URL=http://localhost:8001
EXECUTION_GATEWAY_URL=http://localhost:8002
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_platform
CAPITAL=100000
MAX_POSITION_SIZE=20000
STRATEGY_ID=alpha_baseline
LOG_LEVEL=INFO
```

### Start the Service

```bash
# Development (with auto-reload)
uvicorn apps.orchestrator.main:app --reload --port 8003

# Production
uvicorn apps.orchestrator.main:app --host 0.0.0.0 --port 8003 --workers 4
```

---

## Components

### 1. TradingOrchestrator (`orchestrator.py`)

Core orchestration logic that coordinates the complete workflow.

**Key Methods:**

```python
async def run(
    symbols: List[str],
    strategy_id: str,
    as_of_date: Optional[date] = None
) -> OrchestrationResult
```

Executes complete workflow:
1. Fetch signals from Signal Service
2. Map signals to orders with position sizing
3. Submit orders to Execution Gateway  
4. Return complete results

**Position Sizing Example:**

```python
orchestrator = TradingOrchestrator(
    signal_service_url="http://localhost:8001",
    execution_gateway_url="http://localhost:8002",
    capital=Decimal("100000"),
    max_position_size=Decimal("20000")
)

result = await orchestrator.run(
    symbols=["AAPL", "MSFT", "GOOGL"],
    strategy_id="alpha_baseline"
)

print(f"Status: {result.status}")
print(f"Orders submitted: {result.num_orders_submitted}")
print(f"Orders accepted: {result.num_orders_accepted}")
```

### 2. HTTP Clients (`clients.py`)

Async HTTP clients for Signal Service and Execution Gateway.

**SignalServiceClient:**

```python
client = SignalServiceClient("http://localhost:8001")

# Fetch signals
signals = await client.fetch_signals(
    symbols=["AAPL", "MSFT"],
    as_of_date=date(2024, 12, 31),
    top_n=1,
    bottom_n=1
)

# signals.signals: List[Signal]
# signals.metadata: SignalMetadata (model_version, etc.)

await client.close()
```

**ExecutionGatewayClient:**

```python
client = ExecutionGatewayClient("http://localhost:8002")

# Submit order
order = OrderRequest(
    symbol="AAPL",
    side="buy",
    qty=100,
    order_type="market"
)

submission = await client.submit_order(order)
print(submission.client_order_id)
print(submission.status)

await client.close()
```

**Retry Logic:**

Both clients have automatic retry with exponential backoff:
- 3 attempts max
- Backoff: 2s, 4s, 8s
- Only retries on network errors (not validation errors)

### 3. Database Client (`database.py`)

PostgreSQL persistence for orchestration runs.

**Create Run:**

```python
db = OrchestrationDatabaseClient(DATABASE_URL)

# Persist result
db_id = db.create_run(result)  # Returns database ID
```

**List Runs:**

```python
# Recent runs
runs = db.list_runs(limit=10, status="completed")

for run in runs:
    print(f"{run.run_id}: {run.status}, {run.num_orders_submitted} orders")
```

**Get Run Details:**

```python
run = db.get_run(uuid.UUID("..."))
mappings = db.get_mappings(uuid.UUID("..."))

for mapping in mappings:
    print(f"{mapping.symbol}: {mapping.order_side} {mapping.order_qty} shares")
```

### 4. FastAPI Application (`main.py`)

REST API with 4 endpoints.

**Health Check:**

```bash
curl http://localhost:8003/health
```

Response:
```json
{
  "status": "healthy",
  "signal_service_healthy": true,
  "execution_gateway_healthy": true,
  "database_connected": true
}
```

**Run Orchestration:**

```bash
curl -X POST http://localhost:8003/api/v1/orchestration/run \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["AAPL", "MSFT", "GOOGL"],
    "as_of_date": "2024-12-31",
    "capital": 100000
  }'
```

Response:
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "num_signals": 3,
  "num_orders_submitted": 2,
  "num_orders_accepted": 2,
  "num_orders_rejected": 0,
  "duration_seconds": 2.5,
  "mappings": [
    {
      "symbol": "AAPL",
      "target_weight": 0.333,
      "order_qty": 133,
      "order_side": "buy",
      "client_order_id": "abc123...",
      "order_status": "accepted"
    }
  ]
}
```

---

## API Reference

### POST /api/v1/orchestration/run

Trigger orchestration workflow.

**Request Body:**

```typescript
{
  symbols: string[],              // Required: ["AAPL", "MSFT", ...]
  as_of_date?: string,            // Optional: "2024-12-31"
  capital?: number,               // Optional: Override capital
  max_position_size?: number,     // Optional: Override max position
  dry_run?: boolean               // Optional: Override DRY_RUN
}
```

**Response:** OrchestrationResult

**Status Codes:**
- 200: Success
- 400: Invalid request
- 503: Dependent service unavailable
- 500: Internal error

### GET /api/v1/orchestration/runs

List orchestration runs with pagination.

**Query Parameters:**
- `limit`: Max results (1-100, default 50)
- `offset`: Skip N results (default 0)
- `strategy_id`: Filter by strategy
- `status`: Filter by status (running, completed, failed, partial)

**Response:**

```json
{
  "runs": [...],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

### GET /api/v1/orchestration/runs/{run_id}

Get run details by UUID.

**Response:** OrchestrationResult with full signal-order mappings

**Status Codes:**
- 200: Success
- 404: Run not found

### GET /health

Health check.

**Response:**

```json
{
  "status": "healthy | degraded | unhealthy",
  "service": "orchestrator",
  "signal_service_healthy": true,
  "execution_gateway_healthy": true,
  "database_connected": true
}
```

---

## Testing

### Unit Tests

```bash
# Run position sizing tests
pytest apps/orchestrator/tests/test_position_sizing.py -v

# Expected: 10/10 passing
```

**Test Coverage:**
- Basic long/short positions
- Max position size capping
- Fractional share rounding
- Edge cases (zero weight, penny stocks, high-price stocks)

### Integration Tests

```bash
# Run full integration test suite
python3 scripts/test_t5_orchestrator.py

# Expected output:
# ✅ Test 1: Position sizing (3/3 passed)
# ✅ Test 2: Database connection (1/1 passed)
# ✅ Test 3: Orchestrator with mock data (1/1 passed)
# Pass Rate: 100.0%
```

### Manual Testing

**Prerequisites:**
- Signal Service running (port 8001)
- Execution Gateway running (port 8002) in DRY_RUN mode
- PostgreSQL with migration 003 applied

**Step 1: Health Check**

```bash
curl http://localhost:8003/health | jq
```

Expected: All services healthy

**Step 2: Trigger Orchestration**

```bash
curl -X POST http://localhost:8003/api/v1/orchestration/run \
  -H "Content-Type: application/json" \
  -d '{
    "symbols": ["AAPL", "MSFT", "GOOGL"]
  }' | jq
```

Expected:
- Status: "completed"
- num_signals: 3
- num_orders_submitted: ≥ 0 (depends on weights)
- Mappings with order details

**Step 3: List Recent Runs**

```bash
curl 'http://localhost:8003/api/v1/orchestration/runs?limit=5' | jq
```

Expected: List of recent runs with pagination info

**Step 4: Get Run Details**

```bash
# Use run_id from Step 2
curl http://localhost:8003/api/v1/orchestration/runs/{run_id} | jq
```

Expected: Full run details with all signal-order mappings

---

## Deployment

### Production Checklist

- [ ] PostgreSQL running with migrations applied
- [ ] Signal Service healthy and accessible
- [ ] Execution Gateway healthy and accessible  
- [ ] Environment variables configured
- [ ] Capital and max_position_size set appropriately
- [ ] Monitoring configured (Prometheus/Grafana)
- [ ] Alerts configured for failures

### Docker Deployment

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "apps.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8003"]
```

```bash
# Build
docker build -t orchestrator:latest .

# Run
docker run -p 8003:8003 \
  -e SIGNAL_SERVICE_URL=http://signal-service:8001 \
  -e EXECUTION_GATEWAY_URL=http://execution-gateway:8002 \
  -e DATABASE_URL=postgresql://... \
  orchestrator:latest
```

### Kubernetes Deployment

```yaml
# k8s/orchestrator-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orchestrator
spec:
  replicas: 2
  selector:
    matchLabels:
      app: orchestrator
  template:
    metadata:
      labels:
        app: orchestrator
    spec:
      containers:
      - name: orchestrator
        image: orchestrator:latest
        ports:
        - containerPort: 8003
        env:
        - name: SIGNAL_SERVICE_URL
          value: "http://signal-service:8001"
        - name: EXECUTION_GATEWAY_URL
          value: "http://execution-gateway:8002"
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-credentials
              key: url
```

---

## Troubleshooting

### Issue: Signal Service Unavailable

**Symptoms:**
- Health check shows `signal_service_healthy: false`
- Orchestration runs fail with connection errors

**Resolution:**

1. Check Signal Service is running:
   ```bash
   curl http://localhost:8001/health
   ```

2. Verify SIGNAL_SERVICE_URL:
   ```bash
   echo $SIGNAL_SERVICE_URL
   ```

3. Check network connectivity:
   ```bash
   ping signal-service-host
   ```

### Issue: Orders Not Being Submitted

**Symptoms:**
- num_orders_submitted = 0
- All mappings have skip_reason set

**Resolution:**

1. Check signal weights:
   - Zero weights → no orders
   - All weights < threshold → may result in qty=0

2. Check position sizing:
   - High stock price + small weight = qty < 1 share
   - Check max_position_size not too restrictive

3. Enable debug logging:
   ```bash
   LOG_LEVEL=DEBUG uvicorn apps.orchestrator.main:app --port 8003
   ```

### Issue: Database Connection Failed

**Symptoms:**
- Health check shows `database_connected: false`
- Runs fail with database errors

**Resolution:**

1. Verify PostgreSQL is running:
   ```bash
   psql $DATABASE_URL -c "SELECT 1"
   ```

2. Check migration 003 applied:
   ```bash
   psql $DATABASE_URL -c "\dt orchestration*"
   ```

3. Re-apply migration if needed:
   ```bash
   psql $DATABASE_URL -f migrations/003_create_orchestration_tables.sql
   ```

### Issue: Partial Failures

**Symptoms:**
- status = "partial"
- Some orders accepted, some rejected

**Resolution:**

1. Check execution gateway logs for rejection reasons

2. Review rejected orders:
   ```python
   rejected = [m for m in result.mappings if m.order_status == "rejected"]
   for m in rejected:
       print(f"{m.symbol}: {m.skip_reason}")
   ```

3. Common causes:
   - Insufficient buying power
   - Symbol not tradeable
   - Market closed
   - Validation errors

---

## Performance Tuning

### Target Metrics

- **End-to-End Latency:** < 5 seconds
- **Signal Fetch:** < 1 second
- **Position Sizing:** < 100ms
- **Order Submission:** < 2 seconds (batch of 10)
- **Database Persist:** < 500ms

### Optimization Tips

1. **Parallel Order Submission:**
   - Use asyncio.gather() for concurrent submissions
   - Limit concurrency to avoid overwhelming Execution Gateway

2. **Connection Pooling:**
   - Use persistent HTTP connections (httpx)
   - Use psycopg connection pool for database

3. **Caching:**
   - Cache prices for position sizing (Redis)
   - Cache signal results if re-running same date

4. **Monitoring:**
   - Track orchestration_duration_seconds metric
   - Alert on runs > 10 seconds

---

## Database Schema

### orchestration_runs

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| run_id | UUID | Unique run identifier |
| strategy_id | VARCHAR(100) | Strategy name |
| as_of_date | DATE | Signal date |
| status | VARCHAR(20) | running, completed, failed, partial |
| symbols | TEXT[] | Input symbols |
| capital | NUMERIC(15,2) | Capital allocated |
| num_signals | INTEGER | Signals received |
| num_orders_submitted | INTEGER | Orders submitted |
| num_orders_accepted | INTEGER | Orders accepted |
| num_orders_rejected | INTEGER | Orders rejected |
| started_at | TIMESTAMPTZ | Start timestamp |
| completed_at | TIMESTAMPTZ | End timestamp |
| duration_seconds | NUMERIC(10,3) | Total duration |
| error_message | TEXT | Error if failed |

**Indexes:**
- idx_orchestration_runs_run_id (run_id)
- idx_orchestration_runs_status (status)
- idx_orchestration_runs_as_of_date (as_of_date DESC)

### signal_order_mappings

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| run_id | UUID | Foreign key to orchestration_runs |
| symbol | VARCHAR(10) | Stock symbol |
| predicted_return | NUMERIC(10,6) | Model prediction |
| rank | INTEGER | Signal rank |
| target_weight | NUMERIC(5,4) | Target portfolio weight |
| client_order_id | TEXT | Order ID (if submitted) |
| order_qty | INTEGER | Order quantity in shares |
| order_side | VARCHAR(10) | buy or sell |
| broker_order_id | TEXT | Broker's order ID |
| order_status | VARCHAR(20) | Order status |
| filled_qty | NUMERIC(15,4) | Filled quantity |
| filled_avg_price | NUMERIC(15,4) | Average fill price |
| skip_reason | TEXT | Reason if not submitted |

**Indexes:**
- idx_signal_order_mappings_run_id (run_id)
- idx_signal_order_mappings_symbol (symbol)
- idx_signal_order_mappings_client_order_id (client_order_id)

---

## Related Documentation

- [ADR-0006: Orchestrator Service Architecture](../../ADRs/0006-orchestrator-service.md)
- [P0T3: Signal Service](./P0T3_DONE.md)
- [P0T4: Execution Gateway](./P0T4_DONE.md)
- [P0T1: Data ETL](./P0T1_DONE.md)

---

**End of Implementation Guide**

---

## Migration Notes

**Migrated:** 2025-10-20
**Original File:** `docs/IMPLEMENTATION_GUIDES/p0t5-orchestrator.md`
**Migration:** Automated migration to task lifecycle system

**Historical Context:**
This task was completed before the PxTy_TASK → _PROGRESS → _DONE lifecycle
system was introduced. The content above represents the implementation guide
that was created during development.

For new tasks, use the structured DONE template with:
- Summary of what was built
- Code references
- Test coverage details
- Zen-MCP review history
- Lessons learned
- Metrics

See `docs/TASKS/00-TEMPLATE_DONE.md` for the current standard format.

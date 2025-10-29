# Execution Algorithms Concepts

**Audience:** Beginner traders and developers
**Purpose:** Educational guide to order execution algorithms and strategies
**Related ADR:** [ADR-0015: TWAP Order Slicer](../ADRs/0015-twap-order-slicer.md)

---

## Table of Contents

1. [What are Execution Algorithms?](#what-are-execution-algorithms)
2. [Why Use Execution Algorithms?](#why-use-execution-algorithms)
3. [TWAP (Time-Weighted Average Price)](#twap-time-weighted-average-price)
4. [VWAP (Volume-Weighted Average Price)](#vwap-volume-weighted-average-price)
5. [Implementation Slicing](#implementation-slicing)
6. [Market Impact](#market-impact)
7. [Adaptive Algorithms](#adaptive-algorithms)
8. [Common Execution Scenarios](#common-execution-scenarios)
9. [Best Practices](#best-practices)

---

## What are Execution Algorithms?

**Execution algorithms** (also called "algos") are automated strategies for breaking large orders into smaller pieces and executing them over time to minimize market impact and achieve better average prices.

**Key Concept:** Instead of sending one large order that moves the market, send many small orders strategically timed.

**Analogy:** Imagine you need to buy 10,000 apples from a farmer's market without driving up the price. Instead of buying all 10,000 at once (which would signal high demand and raise prices), you buy 500 apples every hour for 20 hours. By spreading your purchases, you get a better average price.

---

## Why Use Execution Algorithms?

### The Problem: Market Impact

**Market Impact** is the price movement caused by your own order.

**Example:**
```
Current AAPL price: $180.00
You want to buy: 10,000 shares

Scenario 1: Single market order
- Your order buys all available shares at $180.00
- Then buys at $180.05, $180.10, $180.15, ...
- Average fill price: $180.25
- Market impact: $0.25/share = $2,500 total slippage

Scenario 2: TWAP over 1 hour (20 slices of 500 shares)
- Slice 1: 500 shares @ $180.00
- Slice 2: 500 shares @ $180.02
- ...
- Slice 20: 500 shares @ $180.05
- Average fill price: $180.08
- Market impact: $0.08/share = $800 total slippage
```

**Savings:** $1,700 (68% reduction in market impact)

### Benefits of Execution Algorithms

1. **Reduced Market Impact** - Avoid moving the market against yourself
2. **Better Average Price** - Spread orders across time and price levels
3. **Stealth** - Don't reveal full size to other traders
4. **Risk Management** - Limit exposure during volatile periods
5. **Regulatory Compliance** - Demonstrate best execution practices

---

## TWAP (Time-Weighted Average Price)

### What is TWAP?

**TWAP** splits an order into equal-sized pieces and executes them at regular time intervals.

**Goal:** Achieve an average price close to the time-weighted average of the market during the execution period.

**Formula:**
```
TWAP = (P₁ + P₂ + P₃ + ... + Pₙ) / n

Where:
- Pᵢ = price at time interval i
- n = number of time intervals
```

### How TWAP Works

**Example: Buy 1,000 shares of MSFT over 1 hour**

```
Total shares: 1,000
Duration: 1 hour (60 minutes)
Number of slices: 20
Slice interval: 3 minutes
Slice size: 1,000 / 20 = 50 shares each

Schedule:
09:30:00 → Buy 50 shares (Slice 1)
09:33:00 → Buy 50 shares (Slice 2)
09:36:00 → Buy 50 shares (Slice 3)
...
10:27:00 → Buy 50 shares (Slice 20)
```

### TWAP Parameters

**1. Duration**
- How long to spread the order
- Typical: 30 minutes to 4 hours
- Longer = less impact, more risk of missing opportunity

**2. Slice Count**
- How many pieces to split into
- Typical: 10-50 slices
- More slices = less impact, more overhead

**3. Slice Size**
- `slice_size = total_quantity / num_slices`
- Must be integer (round down, accumulate remainder)

**4. Interval**
- Time between slices
- `interval = duration / num_slices`
- Example: 1 hour / 20 slices = 3 minutes

### TWAP Variations

**1. Passive TWAP (Limit Orders)**
```python
# Use limit orders at or better than current price
# For buy: bid side (below market to avoid crossing spread)
# For sell: ask side (above market to avoid crossing spread)
side = "buy"
limit_price = current_price * 0.9995  # 5 bps below for buy (passive)
# Reduces market impact but risks non-fill
```

**2. Aggressive TWAP (Market Orders)**
```python
# Use market orders for guaranteed fill
order_type = "market"
# Higher market impact but ensures completion
```

**3. Hybrid TWAP**
```python
# Start with limit orders, switch to market if falling behind
if slices_remaining < 5 and percent_filled < 0.80:
    order_type = "market"  # Catch up to schedule
```

### When to Use TWAP

**Use TWAP when:**
- ✅ Order size is >5% of average daily volume (ADV)
- ✅ Not time-sensitive (you have hours, not minutes)
- ✅ Market is relatively stable (low volatility)
- ✅ You want predictable execution pattern

**Avoid TWAP when:**
- ❌ Market is very volatile (price may run away)
- ❌ Order is urgent (use aggressive algo or market order)
- ❌ Order size is small relative to ADV (<1%)
- ❌ You need to react to real-time volume patterns (use VWAP instead)

---

## VWAP (Volume-Weighted Average Price)

### What is VWAP?

**VWAP** splits an order based on expected volume patterns throughout the day, executing more during high-volume periods and less during low-volume periods.

**Goal:** Match the volume distribution of the market to minimize detection and impact.

**Formula:**
```
VWAP = Σ(Pᵢ × Vᵢ) / Σ(Vᵢ)

Where:
- Pᵢ = price at time interval i
- Vᵢ = volume at time interval i
```

### How VWAP Works

**Example: Expected Volume Pattern**

```
Time         Expected Volume %    Slice Size
09:30-10:00       15%               150 shares
10:00-11:00       10%               100 shares
11:00-12:00        8%                80 shares
12:00-13:00        7%                70 shares
13:00-14:00        8%                80 shares
14:00-15:00       12%               120 shares
15:00-16:00       40%               400 shares  (market on close)
Total:           100%             1,000 shares
```

### VWAP vs TWAP

| Feature | TWAP | VWAP |
|---------|------|------|
| **Slice Size** | Equal | Varies by expected volume |
| **Timing** | Regular intervals | Follows volume curve |
| **Complexity** | Simple | Requires volume forecast |
| **Stealth** | Moderate | High (matches market) |
| **Use Case** | Stable markets | Following market rhythm |

### When to Use VWAP

**Use VWAP when:**
- ✅ You want to blend with market volume
- ✅ Have historical volume patterns
- ✅ Order is large and needs stealth
- ✅ Full trading day available

**Avoid VWAP when:**
- ❌ Intraday execution (partial day)
- ❌ No reliable volume forecast
- ❌ Market conditions changed (volume pattern broken)

---

## Implementation Slicing

### Slice Calculation

**Goal:** Divide total quantity into N slices, handling rounding correctly.

**Challenge:** Integer shares must sum exactly to total.

**Algorithm:**
```python
def calculate_slices(total_qty: int, num_slices: int) -> list[int]:
    """
    Split total_qty into num_slices, distributing remainder.

    Examples:
        1000 shares / 20 slices → 50 each
        1000 shares / 23 slices → 43 each + 11 slices get 44
    """
    base_size = total_qty // num_slices
    remainder = total_qty % num_slices

    slices = []
    for i in range(num_slices):
        # First 'remainder' slices get +1 share
        slice_size = base_size + (1 if i < remainder else 0)
        slices.append(slice_size)

    assert sum(slices) == total_qty, "Slices must sum to total"
    return slices


# Example:
slices = calculate_slices(total_qty=1000, num_slices=23)
# Returns: [44, 44, 44, ..., 44, 43, 43, ..., 43]
#          └─ 11 slices ─┘  └── 12 slices ──┘
# Sum: 11*44 + 12*43 = 484 + 516 = 1000 ✓
```

### Idempotent Slice IDs

**Problem:** Scheduler restart shouldn't create duplicate slices.

**Solution:** Deterministic client_order_id generation.

```python
def generate_slice_id(parent_order_id: str, slice_num: int) -> str:
    """
    Generate deterministic slice ID.

    Examples:
        parent="PAR123", slice=0 → "PAR123_S000"
        parent="PAR123", slice=5 → "PAR123_S005"
    """
    return f"{parent_order_id}_S{slice_num:03d}"


# Idempotency guarantee:
# - Same parent + same slice_num → same ID
# - Broker rejects duplicate client_order_id
# - Safe to retry slice submission
```

### Scheduling Slices

**Options:**

**1. APScheduler (our implementation)**
```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

# Schedule all slices at once
for i, (timestamp, qty) in enumerate(slice_schedule):
    scheduler.add_job(
        func=execute_slice,
        trigger="date",
        run_date=timestamp,
        args=[parent_order_id, i, qty],
        id=f"{parent_order_id}_S{i:03d}",
    )
```

**2. Database-Driven (future enhancement)**
```sql
-- Store slices in database
CREATE TABLE order_slices (
    client_order_id TEXT PRIMARY KEY,
    parent_order_id TEXT NOT NULL,
    slice_num INTEGER NOT NULL,
    scheduled_time TIMESTAMP NOT NULL,
    status TEXT NOT NULL,  -- pending, submitted, filled, cancelled
    qty INTEGER NOT NULL
);

-- Poll for due slices every second
SELECT * FROM order_slices
WHERE status = 'pending'
  AND scheduled_time <= NOW()
ORDER BY scheduled_time ASC;
```

---

## Market Impact

### Types of Market Impact

**1. Temporary Impact**
- Price movement during execution
- Recovers after order completes
- Caused by short-term supply/demand imbalance

**2. Permanent Impact**
- Information leakage (others see your intent)
- Price moves and doesn't recover
- Caused by signaling future demand

### Estimating Market Impact

**Square-Root Model (simplified):**
```
Impact = σ × √(Q / ADV)

Where:
- σ = daily volatility (standard deviation of returns)
- Q = order quantity
- ADV = average daily volume
```

**Example:**
```python
from math import sqrt

symbol = "AAPL"
sigma = 0.02          # 2% daily volatility
Q = 10_000           # shares to buy
ADV = 50_000_000     # average daily volume

impact = sigma * sqrt(Q / ADV)
       = 0.02 * sqrt(10_000 / 50_000_000)
       = 0.02 * sqrt(0.0002)
       = 0.02 * 0.0141
       = 0.000283 (2.83 bps)

# If AAPL = $180
expected_slippage = 180 * 0.000283 = $0.051/share
total_slippage = 10_000 * 0.051 = $510
```

### Reducing Market Impact

**Strategies:**

1. **Increase Duration** - Spread over more time
2. **Use Limit Orders** - Join the bid/ask, don't cross
3. **Follow Volume** - Execute during high-volume periods (VWAP)
4. **Dark Pools** - Hide order from lit exchanges
5. **Randomize Timing** - Add ±10% jitter to slice times

---

## Adaptive Algorithms

### What are Adaptive Algorithms?

**Adaptive algorithms** adjust execution strategy in real-time based on market conditions.

**Examples:**

**1. Volume Participation**
```python
target_participation_rate = 0.10  # 10% of market volume

# Adjust slice size based on recent volume
recent_volume = get_last_5min_volume()
next_slice_size = recent_volume * target_participation_rate
```

**2. Arrival Price**
```python
# Goal: minimize distance from initial price
initial_price = 180.00
current_price = 180.50

# If price moving against us, speed up
if current_price > initial_price * 1.005:
    slice_interval = interval * 0.8  # Execute faster
```

**3. Implementation Shortfall**
```python
# Minimize cost vs. decision price
decision_price = 180.00  # Price when decision was made
decision_time = "09:30:00"

# Track total cost
total_cost = sum(fill_price * qty for fill_price, qty in fills)
avg_fill = total_cost / total_filled

shortfall = (avg_fill - decision_price) / decision_price
# Negative = good (filled below decision)
# Positive = bad (filled above decision)
```

---

## Common Execution Scenarios

### Scenario 1: Opening a Large Position

**Context:** Buy 5,000 shares of NVDA (ADV = 2M shares)

**Recommendation:** TWAP over 2 hours
```
Order size / ADV = 5,000 / 2,000,000 = 0.25%
→ Small relative to ADV, TWAP is fine

Duration: 2 hours (minimize time risk)
Slices: 40 (3-minute intervals)
Slice size: 125 shares each
```

### Scenario 2: Closing Position at End of Day

**Context:** Sell 10,000 shares of TSLA before market close

**Recommendation:** Aggressive VWAP focused on last hour
```
Target: Close by 16:00
Start: 15:00
Strategy: Front-load slices (sell more early)

15:00-15:20: 4,000 shares (40%)
15:20-15:40: 3,000 shares (30%)
15:40-16:00: 3,000 shares (30%)

Use market orders in final 10 minutes if behind schedule
```

### Scenario 3: Filling Model Rebalance

**Context:** Portfolio optimizer says buy 15 stocks, sell 10 stocks

**Recommendation:** Parallel TWAP for all 25 symbols
```
Duration: 1 hour (synchronized)
Slices: 20 per symbol
Interval: 3 minutes

Benefits:
- Maintain portfolio balance throughout
- Reduce correlation risk
- Simplify reconciliation
```

---

## Best Practices

### 1. Pre-Execution Checks

**Before starting execution algorithm:**

```python
# Validate order parameters
assert total_qty > 0, "Quantity must be positive"
assert num_slices > 0, "Need at least 1 slice"
assert duration_seconds > 0, "Duration must be positive"

# Check against average daily volume
adv = get_average_daily_volume(symbol, days=30)
order_pct = total_qty / adv
if order_pct > 0.10:
    logger.warning(f"Large order: {order_pct:.1%} of ADV")

# Check circuit breaker
if circuit_breaker.is_tripped():
    raise CircuitBreakerTripped("Cannot start new execution")

# Check kill switch
if kill_switch.is_engaged():
    raise KillSwitchEngaged("Trading halted")
```

### 2. Monitor Execution Progress

**Track key metrics:**

```python
class ExecutionMetrics:
    total_qty: int  # Total order quantity
    duration_seconds: int  # Expected execution duration
    filled_qty: int
    avg_fill_price: Decimal
    total_cost: Decimal
    slices_submitted: int
    slices_filled: int
    slices_failed: int
    start_time: datetime
    end_time: datetime | None

    @property
    def fill_rate(self) -> float:
        """Percentage of total quantity filled."""
        return self.filled_qty / self.total_qty if self.total_qty > 0 else 0.0

    @property
    def pace(self) -> float:
        """How ahead/behind schedule we are."""
        elapsed = (datetime.now(UTC) - self.start_time).total_seconds()
        if self.duration_seconds == 0:
            return 1.0  # Instant execution
        expected_filled = self.total_qty * (elapsed / self.duration_seconds)
        return self.filled_qty / expected_filled if expected_filled > 0 else 0.0  # 1.0 = on pace
```

### 3. Handle Failures Gracefully

**Slice execution can fail:**

```python
try:
    broker_response = executor.submit_order(order_request)
except BrokerConnectionError as e:
    # Transient error → retry with backoff
    logger.warning(f"Broker connection failed: {e}")
    db.update_order_status(client_order_id, "retry_pending")
    raise  # Retry decorator handles this

except OrderRejected as e:
    # Permanent error → don't retry
    logger.error(f"Order rejected by broker: {e}")
    db.update_order_status(client_order_id, "rejected")
    # Continue with remaining slices (don't fail entire parent)
```

### 4. Respect Market Hours

**Don't submit orders outside trading hours:**

```python
from datetime import time
from zoneinfo import ZoneInfo

MARKET_OPEN = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET

def is_market_open(now: datetime) -> bool:
    """Check if market is open (simplified, ignores holidays)."""
    et_time = now.astimezone(ZoneInfo("America/New_York"))
    weekday = et_time.weekday()  # 0=Monday, 6=Sunday

    # Weekend
    if weekday >= 5:
        return False

    # Market hours
    return MARKET_OPEN <= et_time.time() < MARKET_CLOSE
```

### 5. Reconcile After Completion

**Verify all slices executed:**

```python
def reconcile_parent_order(parent_order_id: str) -> None:
    """Verify parent order execution after completion."""
    parent = db.get_order(parent_order_id)
    slices = db.get_child_orders(parent_order_id)

    # Check all slices accounted for
    total_filled = sum(s.filled_qty for s in slices)
    assert total_filled == parent.qty, "Slice quantities don't match parent"

    # Check average fill price
    total_cost = sum(s.filled_qty * s.avg_fill_price for s in slices)
    avg_fill = total_cost / total_filled

    # Update parent order
    db.update_order(
        order_id=parent_order_id,
        status="filled",
        filled_qty=total_filled,
        avg_fill_price=avg_fill,
    )

    logger.info(
        f"Parent order reconciled: {parent_order_id}",
        extra={
            "filled_qty": total_filled,
            "avg_fill_price": str(avg_fill),
            "num_slices": len(slices),
        },
    )
```

### 6. Test Before Production

**Backtest execution algorithm:**

```python
# Use historical tick data
ticks = load_historical_ticks("AAPL", date="2024-01-15")

# Simulate TWAP execution
execution_log = simulate_twap(
    ticks=ticks,
    side="buy",
    total_qty=10_000,
    duration_minutes=60,
    num_slices=20,
)

# Analyze results
benchmark_vwap = calculate_vwap(ticks)
avg_fill_price = execution_log.avg_fill_price
slippage = avg_fill_price - benchmark_vwap

print(f"VWAP benchmark: ${benchmark_vwap:.2f}")
print(f"TWAP avg fill: ${avg_fill_price:.2f}")
print(f"Slippage: ${slippage:.4f}/share ({slippage/benchmark_vwap*10000:.1f} bps)")
```

---

## Further Reading

**Academic Papers:**
- Almgren & Chriss (2000): "Optimal Execution of Portfolio Transactions"
- Kissell & Glantz (2003): "Optimal Trading Strategies"

**Industry Resources:**
- [CFA Institute: Execution Algorithms](https://www.cfainstitute.org)
- [Investopedia: Algorithmic Trading](https://www.investopedia.com/terms/a/algorithmictrading.asp)

**Related Documentation:**
- [ADR-0015: TWAP Order Slicer](../ADRs/0015-twap-order-slicer.md)
- [Risk Management Concepts](./risk-management.md)
- [API: Submit Parent Order](../API/execution_gateway.openapi.yaml)

---

**Questions or feedback?**
Open an issue or see `/docs/GETTING_STARTED/CONTRIBUTING.md`

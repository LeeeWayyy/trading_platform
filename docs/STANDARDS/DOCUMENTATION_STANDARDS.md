# Documentation Standards

This document defines comprehensive documentation requirements for the trading platform. All code must be educational and well-documented for learning.

## Educational Documentation Requirements

Every implementation must include educational documentation to help you learn trading concepts and system design.

## Concept Documentation (`/docs/CONCEPTS/`)

### When to Add
Before implementing any trading-specific feature.

### Required Sections
1. **Plain English Explanation** — What is this concept? (no jargon)
2. **Why It Matters** — Real-world impact and consequences
3. **Common Pitfalls** — What goes wrong? How to avoid?
4. **Examples** — Concrete scenarios with numbers
5. **Further Reading** — Links to authoritative sources

### Example Structure
```markdown
# Corporate Actions

## Plain English Explanation
A corporate action is when a company does something that changes
the value or quantity of its stock. The two most common are:
- Stock splits: One share becomes multiple shares
- Dividends: Company pays cash to shareholders

## Why It Matters
If you don't adjust for splits, your backtest will show fake
price drops. Example: AAPL traded at $500 on Aug 30, 2020.
On Aug 31, it "dropped" to $125. This wasn't a 75% crash —
it was a 4-for-1 split.

## Common Pitfalls
- Failing to adjust historical prices leads to fake losses
- Using adjusted prices for intraday trading (use unadjusted)
- Missing ex-dividend dates causes incorrect cash flows

## Examples
### Stock Split Example
- Before split: 100 shares @ $400 = $40,000
- After 4:1 split: 400 shares @ $100 = $40,000
- Historical prices must all be divided by 4

### Dividend Example
- Stock trades at $100
- $2 dividend declared, ex-date tomorrow
- Tomorrow stock opens at ~$98 (market adjusts)
- Your backtest must account for $2 cash received

## Further Reading
- [Investopedia: Corporate Actions](https://www.investopedia.com/terms/c/corporateaction.asp)
- [CRSP Data Adjustment Guide](https://www.crsp.org/)
```

## Implementation Guide Documentation (`/docs/IMPLEMENTATION_GUIDES/`)

### When to Add
For every ticket in `/docs/TASKS/`.

### Required Sections
1. **Overview** — What are we building? High-level flow
2. **Prerequisites** — What must exist first?
3. **Step-by-Step Implementation** — Detailed, ordered steps
4. **Code Walkthrough** — Explain key functions line-by-line
5. **Testing Strategy** — How to verify it works
6. **Troubleshooting** — Common errors and solutions
7. **Next Steps** — What builds on this?

### Example Structure
```markdown
# Implementing Idempotent Order Submission

## Overview
We're building a system where retrying an order submission
doesn't create duplicate orders. This uses deterministic IDs.

**Flow:**
```
Order Request → Generate Deterministic ID → Check DB for Duplicate →
Submit to Broker → Handle 409 Conflict → Return Result
```

## Prerequisites
- Execution gateway service running
- Postgres orders table created
- Alpaca API credentials configured
- Understanding of hash functions (see `/docs/CONCEPTS/hashing.md`)

## Step-by-Step Implementation

### Step 1: Design the Deterministic ID Generator

**Goal:** Create an ID that is the same for the same order parameters within a day.

**Inputs to hash:**
- Symbol (AAPL, MSFT, etc.)
- Side (buy/sell)
- Quantity
- Limit price (if any)
- Strategy ID
- Current date (YYYY-MM-DD)

**Why these inputs?** They uniquely identify an order's intent. Same intent = same ID.

**Why include date?** Allows same order on different days to have different IDs.

### Step 2: Implement the Hash Function

```python
import hashlib
from datetime import date

def deterministic_id(order: OrderIn) -> str:
    """
    Generate a deterministic client_order_id for idempotent orders.

    The ID is based on order characteristics and current date, ensuring:
    1. Same order parameters → same ID (within same day)
    2. Different parameters → different ID
    3. Collision-resistant (SHA256)

    Args:
        order: Order details (symbol, side, qty, etc.)

    Returns:
        24-character hex string, safe for Alpaca client_order_id

    Example:
        >>> order = OrderIn(symbol="AAPL", side="buy", qty=10)
        >>> id1 = deterministic_id(order)
        >>> id2 = deterministic_id(order)
        >>> id1 == id2  # Same order → same ID
        True
        >>> order2 = OrderIn(symbol="AAPL", side="buy", qty=11)
        >>> id3 = deterministic_id(order2)
        >>> id1 == id3  # Different qty → different ID
        False
    """
    # Get today's date in ISO format (YYYY-MM-DD)
    today = date.today().isoformat()

    # Combine all order parameters into a single string
    # Use pipe delimiter to prevent collision like "AB|C" vs "A|BC"
    raw = "|".join([
        order.symbol,
        order.side,
        str(order.qty),
        str(order.limit_price) if order.limit_price else "None",
        order.strategy_id,
        today
    ])

    # Hash the combined string using SHA256
    # This gives us a 64-character hex string
    hash_full = hashlib.sha256(raw.encode()).hexdigest()

    # Take first 24 characters (Alpaca's limit for client_order_id)
    # 24 hex chars = 96 bits, collision probability ~1 in 10^28
    return hash_full[:24]
```

**Code walkthrough:**
- Line 1-2: Use SHA256 for cryptographic strength
- Line 3: Get today's date to make IDs unique per day
- Line 4-10: Combine inputs with pipe delimiter
- Line 11: Hash the string to get deterministic output
- Line 12: Truncate to 24 chars for Alpaca compatibility

### Step 3: Handle Duplicate Submissions

```python
@app.post("/orders")
async def place_order(o: OrderIn):
    # Generate deterministic ID
    client_order_id = deterministic_id(o)

    # Check if we already submitted this order today
    existing = await db.fetch_one(
        "SELECT * FROM orders WHERE client_order_id = %s",
        (client_order_id,)
    )

    if existing:
        logger.info(
            "Duplicate order detected, returning existing",
            extra={"client_order_id": client_order_id}
        )
        return {"status": "duplicate", "order": existing}

    # Submit to Alpaca
    try:
        result = await submit_to_alpaca(o, client_order_id)

        # Save to DB
        await db.execute(
            "INSERT INTO orders (client_order_id, symbol, side, qty, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (client_order_id, o.symbol, o.side, o.qty, result["status"])
        )

        return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            # Alpaca says duplicate, but we didn't have it in DB
            # This can happen if DB write failed previously
            logger.warning(
                "Alpaca returned 409 but order not in DB",
                extra={"client_order_id": client_order_id}
            )
            return {"status": "duplicate_recovered", "client_order_id": client_order_id}
        raise
```

## Testing Strategy

### Unit Tests
```python
def test_deterministic_id_same_inputs():
    """Same order should generate same ID."""
    order = OrderIn(symbol="AAPL", side="buy", qty=10)
    id1 = deterministic_id(order)
    id2 = deterministic_id(order)
    assert id1 == id2

def test_deterministic_id_different_inputs():
    """Different orders should generate different IDs."""
    order1 = OrderIn(symbol="AAPL", side="buy", qty=10)
    order2 = OrderIn(symbol="AAPL", side="buy", qty=11)
    assert deterministic_id(order1) != deterministic_id(order2)
```

### Integration Tests
```python
async def test_duplicate_order_rejected():
    """Submitting same order twice should return duplicate status."""
    order = OrderIn(symbol="AAPL", side="buy", qty=10)

    # First submission
    response1 = await client.post("/orders", json=order.dict())
    assert response1.status_code == 200

    # Second submission (duplicate)
    response2 = await client.post("/orders", json=order.dict())
    assert response2.status_code == 200
    assert response2.json()["status"] == "duplicate"
```

## Troubleshooting

### Issue: Different IDs for Same Order
**Symptom:** Retry creates duplicate order instead of returning existing.

**Causes:**
1. System clock changed between submissions
2. Order parameters have floating point precision issues
3. Strategy ID not consistent

**Solution:**
```python
# Ensure qty is consistently formatted
str(round(order.qty, 2))  # Not str(order.qty)

# Use fixed-precision for limit_price
str(round(order.limit_price, 2)) if order.limit_price else "None"
```

### Issue: 409 from Alpaca but Not in DB
**Symptom:** Alpaca says duplicate but our DB has no record.

**Cause:** DB write failed after Alpaca submission succeeded.

**Solution:** Already handled in code (see 409 handler above).

## Next Steps
- Add retry logic with exponential backoff (Phase 7)
- Implement stale order cleanup (Phase 7)
- Add order state machine (Phase 7)
```

## Code Comment Standards

### Function Docstring Requirements (STRICT)

Every function MUST include a comprehensive docstring using Google style:

```python
def compute_momentum(df: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """
    Calculate momentum signal based on percentage change.

    Momentum is a trend-following indicator that measures the rate of
    change in price over a period. Positive momentum suggests upward
    trend, negative suggests downward trend.

    Args:
        df: DataFrame with columns [symbol, date, close]. Must be sorted
            by symbol and date in ascending order.
        lookback: Number of periods for momentum calculation. Default 20
                  (roughly one trading month).

    Returns:
        DataFrame with additional 'momentum' column containing the
        percentage change from lookback periods ago. Values range from
        -1.0 (100% drop) to infinity (for gains).

    Raises:
        ValueError: If required columns missing or lookback < 1

    Example:
        >>> df = pl.DataFrame({
        ...     "symbol": ["AAPL", "AAPL", "AAPL"],
        ...     "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
        ...     "close": [150.0, 153.0, 156.0]
        ... })
        >>> result = compute_momentum(df, lookback=1)
        >>> result["momentum"].to_list()
        [None, 0.02, 0.0196]  # 2% gain, then 1.96% gain

    Notes:
        - First lookback rows will have null momentum values
        - Handles symbol groups independently (preserves boundaries)
        - Replaces inf/-inf with null (division by zero protection)

    See Also:
        - /docs/CONCEPTS/momentum-signals.md for theory
        - ADR-0015 for choice of lookback period
    """
    # Implementation...
```

### Required Docstring Sections

1. **Summary Line** (required)
   - One-line description of what function does
   - Starts with verb (Calculate, Generate, Validate, etc.)

2. **Detailed Description** (required for complex functions)
   - Explain the algorithm or approach
   - Include relevant trading concepts
   - Link to concept docs if applicable

3. **Args** (required if function has parameters)
   - Type annotation in signature (not in docstring)
   - Description of each parameter
   - Include valid ranges, units, or formats
   - Note any implicit requirements (e.g., sorted data)

4. **Returns** (required if function returns value)
   - Type annotation in signature (not in docstring)
   - Description of return value
   - Include shape, range, or format details

5. **Raises** (required if function raises exceptions)
   - List all exceptions that may be raised
   - Explain conditions that trigger each exception

6. **Example** (required for non-trivial functions)
   - Executable code snippet
   - Include expected output
   - Show typical use case

7. **Notes** (optional but encouraged)
   - Edge cases and their handling
   - Performance considerations
   - Relationship to other functions

8. **See Also** (optional but encouraged)
   - Links to related concept docs
   - References to ADRs explaining design decisions
   - Related functions or modules

### Inline Comment Requirements

Add inline comments for:

1. **Non-obvious Logic**
   ```python
   # Use maintain_order=True to preserve time series ordering
   # Without this, group_by may reorder rows within groups
   df.group_by("symbol", maintain_order=True)
   ```

2. **Trading-Specific Calculations**
   ```python
   # Adjust for stock split by dividing historical prices
   # Example: 4-for-1 split means all pre-split prices ÷ 4
   adj_close = close / split_ratio
   ```

3. **Error Handling Rationale**
   ```python
   # Retry on timeout (network issue), not on 4xx (client error)
   # 4xx means our request is invalid, retry won't help
   if 400 <= status_code < 500:
       raise OrderValidationError(...)
   ```

4. **Performance Considerations**
   ```python
   # Process in chunks to avoid loading entire dataset into memory
   # 100K rows ≈ 50MB, safe for most systems
   CHUNK_SIZE = 100_000
   ```

5. **Edge Cases**
   ```python
   # Handle division by zero when price was zero (data error)
   # Replace inf with null rather than failing
   momentum = momentum.replace([float('inf'), float('-inf')], None)
   ```

### Complex Algorithm Documentation Example

```python
def allocate_risk_parity(
    signals: dict[str, float],
    volatilities: dict[str, float],
    target_risk: float = 0.10
) -> dict[str, float]:
    """
    Allocate capital using risk parity principles.

    Risk parity aims to equalize the risk contribution of each position
    rather than equalizing dollar amounts. This prevents volatile assets
    from dominating portfolio risk.

    The algorithm:
    1. Scale each position inversely to its volatility
    2. Normalize weights to sum to 1.0 (or 0 if no signals)
    3. Apply target risk scaling

    Mathematical formula:
        weight_i = signal_i / volatility_i / sum(signal_j / volatility_j)

    Args:
        signals: Dict of {symbol: signal_strength} where signal is
                 typically in range [-1, 1]. Positive = long, negative = short.
        volatilities: Dict of {symbol: realized_volatility} where volatility
                      is annualized (e.g., 0.25 = 25% annual vol).
        target_risk: Target portfolio volatility (default 10% = 0.10). Used
                     to scale final weights.

    Returns:
        Dict of {symbol: weight} where weights are portfolio allocations.
        Weights sum to approximately target_risk if signals exist, else 0.

    Raises:
        ValueError: If any volatility <= 0 or keys don't match

    Example:
        >>> signals = {"AAPL": 0.8, "MSFT": 0.6}
        >>> vols = {"AAPL": 0.25, "MSFT": 0.20}  # 25% and 20% annual vol
        >>> allocate_risk_parity(signals, vols, target_risk=0.10)
        {"AAPL": 0.045, "MSFT": 0.055}  # MSFT gets more (lower vol)

    Notes:
        - Higher signal + lower volatility = larger allocation
        - Returns empty dict or zeros if signals are all zero
        - Does NOT account for correlations (use mean-variance for that)
        - Assumes volatilities are recent/relevant (stale vol = bad weights)

    See Also:
        - ADR-0023 for rationale of risk parity vs equal weight
        - /docs/CONCEPTS/risk-parity.md for detailed explanation
    """
    # Validate inputs
    if set(signals.keys()) != set(volatilities.keys()):
        raise ValueError("signals and volatilities must have same symbols")

    if any(v <= 0 for v in volatilities.values()):
        raise ValueError("All volatilities must be positive")

    # Edge case: no signals or all zero signals
    if not signals or all(s == 0 for s in signals.values()):
        return {sym: 0.0 for sym in signals}

    # Step 1: Inverse volatility weighting
    # Divide each signal by its volatility to get risk-adjusted signal
    # Lower vol → higher weight for same signal strength
    risk_adj = {
        sym: signals[sym] / volatilities[sym]
        for sym in signals
    }

    # Step 2: Normalize to sum to 1.0
    # This ensures we're fully invested (before target risk scaling)
    # Use abs() to handle long/short positions correctly
    total = sum(abs(w) for w in risk_adj.values())
    if total == 0:  # Shouldn't happen but defensive
        return {sym: 0.0 for sym in signals}

    normalized = {sym: w / total for sym, w in risk_adj.items()}

    # Step 3: Scale to target risk level
    # Multiply all weights by target risk to control overall exposure
    # Example: target_risk=0.10 means 10% portfolio volatility target
    weights = {
        sym: w * target_risk
        for sym, w in normalized.items()
    }

    return weights
```

## Documentation File Organization

```
docs/
├── CONCEPTS/                    # Trading concepts explained
│   ├── corporate-actions.md
│   ├── slippage.md
│   ├── circuit-breakers.md
│   ├── idempotency.md
│   ├── risk-parity.md
│   ├── momentum-signals.md
│   ├── backtest-validation.md
│   └── position-sizing.md
├── IMPLEMENTATION_GUIDES/       # Step-by-step how-tos
│   ├── phase-0-setup.md
│   ├── phase-2-data-pipeline.md
│   ├── phase-3-qlib-strategy.md
│   ├── phase-6-execution-gateway.md
│   ├── phase-7-risk-manager.md
│   └── phase-8-reconciler.md
├── ADRs/                        # Architecture decisions
│   ├── 0000-template.md
│   ├── 0001-use-postgres-for-state.md
│   ├── 0002-idempotent-order-ids.md
│   ├── 0003-circuit-breaker-in-redis.md
│   └── 0004-feature-parity-strategy.md
└── LESSONS_LEARNED/             # Retrospectives and learnings
    ├── week-1-setup-challenges.md
    ├── week-2-data-quality-issues.md
    └── retrospectives.md
```

## When to Update Documentation

### Before Implementation
- Create/update concept documentation for new trading concepts
- Create implementation guide from ticket
- Create ADR for architectural changes

### During Implementation
- Add comprehensive docstrings to all functions
- Add inline comments for complex logic
- Update implementation guide with actual steps taken

### After Implementation
- Update concept docs with lessons learned
- Add troubleshooting section to implementation guide
- Create retrospective in LESSONS_LEARNED if significant issues encountered

## Documentation Quality Checklist

Before marking a ticket complete:

- [ ] Concept documentation exists for all trading-specific features
- [ ] Implementation guide covers all steps taken
- [ ] All functions have complete docstrings (summary, args, returns, examples)
- [ ] Complex logic has inline comments explaining "why"
- [ ] ADR exists for any architectural changes
- [ ] Examples are executable and produce stated output
- [ ] Links to related docs are included
- [ ] Common errors and solutions documented

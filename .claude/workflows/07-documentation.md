# Documentation Writing Workflow

**Purpose:** Write comprehensive documentation following project standards
**Prerequisites:** Code implemented and tested
**Expected Outcome:** Complete documentation (docstrings, concept docs, guides)
**Owner:** @development-team + @tech-writers
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

- During implementation (docstrings)
- After implementing trading concepts (concept docs)
- When creating new features (implementation guides)
- Before creating PR

---

## Step-by-Step Process

### 1. Write Function Docstrings

**Follow Google style format:**

```python
def compute_momentum(df: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """
    Calculate momentum signal based on percentage change.

    Momentum measures rate of change in price. Positive = uptrend,
    negative = downtrend.

    Args:
        df: DataFrame with [symbol, date, close]. Must be sorted.
        lookback: Periods for calculation. Default 20 (1 month).

    Returns:
        DataFrame with 'momentum' column. Range -1.0 to infinity.

    Raises:
        ValueError: If required columns missing or lookback < 1

    Example:
        >>> df = pl.DataFrame({"symbol": ["AAPL"], "close": [150, 153]})
        >>> compute_momentum(df, lookback=1)["momentum"].to_list()
        [None, 0.02]  # 2% gain

    Notes:
        - First lookback rows = null
        - Handles symbols independently
        - Replaces inf with null

    See Also:
        /docs/CONCEPTS/momentum-signals.md
    """
```

**Required sections:**
- Summary (one line)
- Detailed description
- Args
- Returns
- Raises (if applicable)
- Example (executable)
- Notes (edge cases)
- See Also (links)

### 2. Create Concept Documentation

**For trading-specific features:**

Create `/docs/CONCEPTS/{concept-name}.md`:

```markdown
# Concept Name

## Plain English Explanation
What is this concept? (no jargon)

## Why It Matters
Real-world impact and consequences

## Common Pitfalls
What goes wrong? How to avoid?

## Examples
Concrete scenarios with numbers

## Further Reading
Links to authoritative sources
```

### 3. Update Implementation Guides

**If creating new feature:**

Create `/docs/IMPLEMENTATION_GUIDES/{phase}-{task}.md`

**Include:**
- Overview and flow diagram
- Prerequisites
- Step-by-step implementation
- Code walkthrough
- Testing strategy
- Troubleshooting

### 4. Inline Comments for Complex Logic

```python
# Use comments to explain WHY, not WHAT
# Example:

# Adjust for stock split by dividing historical prices
# 4-for-1 split means all pre-split prices ÷ 4
adj_close = close / split_ratio

# Retry on timeout (network issue), not on 4xx (client error)
# 4xx means our request is invalid, retry won't help
if 400 <= status_code < 500:
    raise OrderValidationError(...)
```

### 5. Verify Documentation Quality

**Checklist:**
- [ ] All functions have docstrings
- [ ] Examples are executable
- [ ] Trading concepts documented
- [ ] Links work
- [ ] No spelling errors
- [ ] Follows standards

---

## Decision Points

### Should I document this in code or separate file?

**In-code (docstring):**
- Function/class/method documentation
- Parameter explanations
- Return values and exceptions
- Usage examples

**Separate file (concept doc):**
- Trading concepts (momentum, P&L, etc.)
- Complex algorithms
- Design rationale
- Background knowledge

### How detailed should documentation be?

**Detailed (comprehensive):**
- Public APIs
- Trading logic
- Complex algorithms
- Anything safety-critical

**Concise (brief):**
- Internal utilities
- Self-explanatory code
- Simple getters/setters
- Obvious operations

---

## Common Issues & Solutions

### Issue: Don't Know What to Document

**Solution:**
- Start with function signature (args, returns)
- Add one-line summary
- Add example showing usage
- Explain any non-obvious behavior
- Link to related concepts

### Issue: Examples Don't Run

**Solution:**
```python
# Test your examples!
# Add to doctest or test file

def test_docstring_example():
    """Verify docstring example works."""
    # Copy example from docstring
    df = pl.DataFrame({"symbol": ["AAPL"], "close": [150, 153]})
    result = compute_momentum(df, lookback=1)["momentum"].to_list()
    assert result == [None, 0.02]
```

---

## Examples

### Example 1: Complete Function Documentation

See "Trading Calculations" pattern below

### Example 2: Concept Documentation

```markdown
# Position Sizing

## Plain English
Position sizing determines how many shares to buy based on your risk tolerance and the stock's volatility.

## Why It Matters
Wrong position size = too much risk or missed opportunity

## Example
- Stock A: $100, volatility 40%
- Stock B: $100, volatility 10%
- Same $ amount → Stock A has 4x the risk
- Size positions by vol → equal risk contribution
```

---

## Common Documentation Patterns

### Trading Calculations

```python
def calculate_position_size(
    signal: float,
    volatility: float,
    portfolio_value: float,
    target_risk: float = 0.02
) -> int:
    """
    Calculate position size using volatility-based sizing.

    Position size = (portfolio_value * target_risk) / (price * volatility)

    This ensures each position has similar risk contribution regardless
    of individual stock volatility.

    Args:
        signal: Trading signal strength [-1, 1]. Positive = long.
        volatility: Annualized volatility (e.g., 0.25 = 25%).
        portfolio_value: Total portfolio value in USD.
        target_risk: Target risk per position (default 2%).

    Returns:
        Number of shares to buy/sell (rounded to integer).

    Example:
        >>> calculate_position_size(
        ...     signal=0.8,
        ...     volatility=0.25,
        ...     portfolio_value=100000,
        ...     target_risk=0.02
        ... )
        64  # shares

    See Also:
        /docs/CONCEPTS/position-sizing.md
    """
```

### API Endpoints

```python
@app.post("/api/v1/orders")
async def create_order(request: OrderRequest) -> OrderResponse:
    """
    Submit order to broker with idempotent client_order_id.

    This endpoint generates deterministic order IDs to prevent duplicates.
    Same order parameters on same day = same ID.

    Args:
        request: Order details (symbol, side, qty, price).

    Returns:
        Order confirmation with client_order_id and status.

    Raises:
        CircuitBreakerTripped: If circuit breaker is TRIPPED.
        RiskViolation: If order exceeds position limits.
        ValidationError: If order parameters invalid.

    Example:
        POST /api/v1/orders
        {
            "symbol": "AAPL",
            "side": "buy",
            "qty": 100,
            "order_type": "market"
        }

        Response:
        {
            "client_order_id": "abc123...",
            "status": "accepted",
            "submitted_at": "2024-01-15T10:30:00Z"
        }

    See Also:
        /docs/CONCEPTS/idempotency.md
        ADR-0005: Execution Gateway Architecture
    """
```

---

## Validation

**How to verify documentation is complete:**
- [ ] All functions have Google-style docstrings
- [ ] Examples are executable and correct
- [ ] Trading concepts documented in `/docs/CONCEPTS/`
- [ ] Links work (no 404s)
- [ ] No spelling errors
- [ ] Follows DOCUMENTATION_STANDARDS.md

**What to check if documentation seems insufficient:**
- Read it as if you're new to the codebase
- Can someone understand the code from docs alone?
- Are examples realistic and helpful?
- Is the "why" explained, not just "what"?

---

## Related Workflows

- [08-adr-creation.md](./08-adr-creation.md) - Creating ADRs for architecture
- [01-git-commit.md](./01-git-commit.md) - Include docs in commits

---

## References

- [/docs/STANDARDS/DOCUMENTATION_STANDARDS.md](../../docs/STANDARDS/DOCUMENTATION_STANDARDS.md) - Full standards with examples
- Google Style Guide: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings

---

**Maintenance Notes:**
- Update when documentation standards change
- Review when new patterns emerge
- Add examples as new concepts documented

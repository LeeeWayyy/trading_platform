# Gemini Code Assist - Project Context

This file provides project-specific context for @gemini-code-assist automated code reviews.

## Project Overview

This is a **Qlib + Alpaca trading platform** for algorithmic trading with emphasis on:
- Safety (circuit breakers, risk checks)
- Idempotency (deterministic order IDs, safe retries)
- Feature parity (research and production share code)
- Educational documentation (learning-focused)

## Critical Review Focus Areas

### 1. Trading Safety (CRITICAL Priority)

**ALWAYS check for:**
- Missing circuit breaker checks before order placement
- Missing risk validation (position limits, exposure)
- Order deduplication (must use deterministic client_order_id)
- Idempotency violations (can this be safely retried?)
- Race conditions in order placement or position updates
- Missing DRY_RUN mode checks

**Example violations to catch:**
```python
# ❌ BAD - No circuit breaker check
def place_order(symbol, qty):
    alpaca_client.submit_order(...)

# ✅ GOOD - Check breaker first
def place_order(symbol, qty):
    if redis.get("cb:state") == b"TRIPPED":
        raise CircuitBreakerTripped()
    alpaca_client.submit_order(...)
```

### 2. Data Quality (HIGH Priority)

**ALWAYS check for:**
- Missing freshness checks (data must be <30min old)
- Corporate action adjustments not applied
- Missing quality gates (outlier detection)
- Survivorship bias (using current universe for historical data)
- Timezone-naive timestamps (ALL must be UTC-aware)

**Example violations to catch:**
```python
# ❌ BAD - Timezone-naive
now = datetime.now()

# ✅ GOOD - UTC-aware
now = datetime.now(timezone.utc)
```

### 3. Feature Parity (HIGH Priority)

**ALWAYS check for:**
- Duplicate feature logic (offline vs online)
- Inconsistent feature definitions between research and production
- Missing shared library usage

**Example violations to catch:**
```python
# ❌ BAD - Duplicate logic in signal service
def compute_momentum(df):
    return df["close"].pct_change(20)

# ✅ GOOD - Use shared library
from strategies.alpha_baseline.features import compute_momentum
```

### 4. Code Quality Standards (MEDIUM Priority)

**Python requirements:**
- Type hints on all functions (mypy --strict must pass)
- Pydantic for all config/models
- Structured logging (JSON format)
- Comprehensive docstrings with examples (see DOCUMENTATION_STANDARDS.md)
- No bare except: clauses
- Parameterized SQL queries only (no f-strings in SQL)

**Testing requirements:**
- Tests for happy path AND edge cases
- Mock external dependencies (Alpaca API, Redis, Postgres)
- Test idempotency (call twice, same result)
- Test circuit breaker integration

### 5. Documentation Standards (MEDIUM Priority)

**ALWAYS check for:**
- Missing docstrings on public functions
- Missing ADR for architectural changes
- Missing concept docs for trading-specific features
- Missing examples in docstrings
- Vague variable names (use descriptive names)

**Example violations to catch:**
```python
# ❌ BAD - No docstring, vague names
def calc(df, n):
    return df["c"].pct_change(n)

# ✅ GOOD - Clear docstring and names
def compute_momentum(df: pl.DataFrame, lookback: int = 20) -> pl.DataFrame:
    """Calculate momentum signal based on percentage change.

    Args:
        df: DataFrame with 'close' column
        lookback: Number of periods for calculation

    Returns:
        DataFrame with 'momentum' column

    Example:
        >>> df = pl.DataFrame({"close": [100, 105, 110]})
        >>> compute_momentum(df, lookback=1)
    """
    return df.with_columns(
        pl.col("close").pct_change(lookback).alias("momentum")
    )
```

## Anti-Patterns to Flag

### Order Placement Anti-Patterns
- ❌ Non-deterministic client_order_id (use hash-based ID)
- ❌ No deduplication check before submission
- ❌ Missing DRY_RUN handling
- ❌ No circuit breaker integration
- ❌ No risk checks

### Data Handling Anti-Patterns
- ❌ Timezone-naive timestamps
- ❌ No corporate action adjustment
- ❌ No quality gates
- ❌ Using current universe for historical data
- ❌ Stale data (>30min old) without trip

### Testing Anti-Patterns
- ❌ No tests for error paths
- ❌ No idempotency tests
- ❌ Mocking internal code (only mock external dependencies)
- ❌ Tests that depend on execution order
- ❌ Tests with hardcoded dates/times

## Project-Specific Patterns

### Idempotent Order IDs
```python
def deterministic_order_id(symbol: str, side: str, qty: int,
                          price: Decimal, date: datetime.date) -> str:
    """Generate deterministic client_order_id for idempotency."""
    data = f"{symbol}|{side}|{qty}|{price}|{date.isoformat()}"
    return hashlib.sha256(data.encode()).hexdigest()[:24]
```

### Circuit Breaker Pattern
```python
def check_breaker_before_action():
    """ALL order/signal actions must check breaker first."""
    if redis.get("cb:state") == b"TRIPPED":
        raise CircuitBreakerTripped()
```

### Freshness Check Pattern
```python
def validate_freshness(timestamp: datetime) -> None:
    """Data must be <30min old."""
    age = datetime.now(timezone.utc) - timestamp
    if age > timedelta(minutes=30):
        raise StalenessError(f"Data too old: {age}")
```

## Review Severity Guidelines

### CRITICAL
- Security vulnerabilities (SQL injection, XSS)
- Data loss or corruption risks
- Missing circuit breaker checks
- Race conditions in order placement
- Non-idempotent operations without safeguards

### HIGH
- Logic bugs affecting correctness
- Missing risk validation
- Performance issues (N+1 queries, inefficient algorithms)
- Missing tests for critical paths
- Feature parity violations

### MEDIUM
- Code quality issues (type hints, naming)
- Missing docstrings
- Test coverage gaps
- Style violations (but not blocking)

### LOW
- Nitpicks (variable naming preferences)
- Minor style inconsistencies
- Suggestions for refactoring

## Files to Always Review Carefully

- `apps/execution_gateway/` - Order placement (idempotency critical)
- `apps/risk_manager/` - Risk checks (safety critical)
- `libs/risk_management/` - Circuit breaker logic
- `strategies/*/features.py` - Feature parity critical
- Any SQL queries - Injection risk
- Any timestamp handling - Timezone issues

## Files to Ignore (see .gemini/config.yaml)

- Documentation files (*.md)
- Config files (*.yaml, *.json)
- GitHub workflows (.github/)
- Data artifacts (data/, artifacts/)
- Generated coverage reports

## When in Doubt

If you're unsure about a pattern or standard:
1. Check `CLAUDE.md` for project overview
2. Check `docs/STANDARDS/` for normative guidance
3. Check `docs/ADRs/` for architectural decisions
4. Check `docs/CONCEPTS/` for trading concepts
5. Flag as MEDIUM and let human reviewer decide

## Response Format

When reviewing code:
1. Start with overall assessment (PASS/NEEDS_WORK)
2. Group findings by severity (CRITICAL → HIGH → MEDIUM → LOW)
3. Reference specific line numbers
4. Explain WHY the issue matters (not just WHAT is wrong)
5. Provide concrete fix suggestions with code examples
6. Link to relevant documentation

**Example good review comment:**
```
HIGH: Missing circuit breaker check (line 42)

The order placement in `place_order()` doesn't check circuit breaker state
before submitting to Alpaca. This violates the safety-first principle.

Risk: Orders may be placed during circuit breaker TRIPPED state, potentially
causing additional losses.

Fix:
```python
def place_order(self, order: Order):
    # Check circuit breaker first
    if self.breaker.get_state() == CircuitBreakerState.TRIPPED:
        raise CircuitBreakerTripped("Cannot place orders while breaker TRIPPED")

    # Then place order
    return self.alpaca_client.submit_order(order)
```

See: docs/CONCEPTS/risk-management.md#circuit-breaker
```

## Summary

Focus reviews on:
1. **Safety** (circuit breakers, risk checks, idempotency)
2. **Correctness** (logic bugs, edge cases)
3. **Data quality** (freshness, corporate actions, timezones)
4. **Feature parity** (no duplicate logic)
5. **Testing** (coverage, edge cases, idempotency)

Be thorough but respectful. The goal is to catch critical issues before they
reach production while helping developers learn trading system best practices.

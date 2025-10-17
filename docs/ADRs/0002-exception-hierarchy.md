# ADR-0002: Exception Hierarchy

## Status
Accepted (2024-10-16)

## Context

As we build the trading platform, we need a consistent strategy for error handling. The codebase will have many error conditions:
- Data quality issues (outliers, staleness, missing values)
- API failures (Alpaca timeouts, rate limits)
- Business logic violations (risk limits exceeded, invalid orders)
- Infrastructure problems (database down, Redis unreachable)

**Problems with generic exceptions:**
```python
# BAD: Hard to handle precisely
try:
    run_pipeline()
except Exception as e:  # Catches everything!
    # What went wrong? Data issue? Network? Bug?
    logger.error(f"Something failed: {e}")
```

**Requirements:**
- Precise error handling (catch data errors differently from API errors)
- Clear error messages for debugging
- Educational: errors should explain what went wrong and why it matters
- Testable: easy to assert specific errors in tests
- Organized: logical grouping of related errors

## Decision

We will implement a **hierarchical exception structure** with domain-specific exceptions:

```python
TradingPlatformError (base)
├── DataQualityError
│   ├── StalenessError
│   ├── OutlierError
│   └── SchemaError (future)
├── RiskViolationError (T4+)
│   ├── PositionLimitError
│   ├── NotionalLimitError
│   └── DrawdownLimitError
├── OrderExecutionError (T4+)
│   ├── OrderRejectedError
│   └── OrderTimeoutError
└── CircuitBreakerTripped (P1)
```

### Implementation

**Base Exception:**
```python
class TradingPlatformError(Exception):
    """
    Base exception for all trading platform errors.

    All custom exceptions inherit from this, enabling:
    - Catch-all error handling when needed
    - Clear separation from stdlib/library exceptions
    - Guaranteed to be our error, not a bug
    """
    pass
```

**Domain-Specific Exceptions:**
```python
class DataQualityError(TradingPlatformError):
    """Data validation failures."""
    pass

class StalenessError(DataQualityError):
    """Data too old for trading."""
    pass

class OutlierError(DataQualityError):
    """Abnormal price movement."""
    pass
```

### Usage Patterns

**Raising with context:**
```python
# GOOD: Rich context
if age_minutes > threshold:
    raise StalenessError(
        f"Data is {age_minutes:.1f}m old (threshold: {threshold}m). "
        f"Latest bar: {latest_date}, current time: {now}"
    )

# BAD: No context
if age_minutes > threshold:
    raise Exception("Data too old")
```

**Precise catching:**
```python
# GOOD: Handle different errors differently
try:
    adjusted = run_etl_pipeline(raw_data)
except StalenessError as e:
    # Data is stale → wait and retry
    logger.warning(f"Stale data, retrying in 5m: {e}")
    time.sleep(300)
    retry()
except OutlierError as e:
    # Bad data → quarantine and continue with other symbols
    logger.error(f"Outlier detected, quarantining: {e}")
    quarantine(e)
except DataQualityError as e:
    # Any other data issue → skip this run
    logger.error(f"Data quality issue: {e}")
    skip()
```

## Consequences

### Positive
- **Precise error handling**: Can catch data errors separately from API errors
- **Better logging**: Know exactly what failed and why
- **Testable**: `pytest.raises(StalenessError)` is specific and clear
- **Self-documenting**: Exception name explains the problem
- **Educational**: Error messages teach trading concepts

### Negative
- **More code**: Need to define and maintain exception classes
- **Learning curve**: Need to know which exception to use (mitigated: clear docstrings)

### Risks
- **Exception proliferation**: Too many exceptions becomes hard to remember
  - **Mitigation**: Only create exceptions when precision matters; use base classes when not

## Alternatives Considered

### Alternative 1: Use Generic Exceptions
```python
# Use ValueError, RuntimeError, etc.
if age > threshold:
    raise ValueError(f"Data too old: {age}m")
```

**Pros:** No extra code, familiar
**Cons:** Can't distinguish our errors from library errors; harder to catch precisely
**Why not:** Precision matters for error handling strategy

### Alternative 2: Error Codes
```python
class TradingPlatformError(Exception):
    def __init__(self, code, message):
        self.code = code  # e.g., "DATA_STALE"
        super().__init__(message)
```

**Pros:** Can group errors by code patterns
**Cons:** More verbose, string matching is fragile, not Pythonic
**Why not:** Python's exception hierarchy is more elegant

### Alternative 3: Flat Structure (No Inheritance)
```python
class StalenessError(Exception): pass
class OutlierError(Exception): pass
# No shared base class
```

**Pros:** Simpler
**Cons:** Can't catch "any data error"; harder to organize
**Why not:** Hierarchy enables flexible error handling

## Implementation Notes

### For T1 (Now)
```python
# libs/common/exceptions.py
class TradingPlatformError(Exception): pass
class DataQualityError(TradingPlatformError): pass
class StalenessError(DataQualityError): pass
class OutlierError(DataQualityError): pass
```

### For T4+ (Later)
Add execution-related exceptions:
```python
class OrderExecutionError(TradingPlatformError): pass
class OrderRejectedError(OrderExecutionError): pass
class OrderTimeoutError(OrderExecutionError): pass
```

### For P1 (Later)
Add risk-related exceptions:
```python
class RiskViolationError(TradingPlatformError): pass
class CircuitBreakerTripped(TradingPlatformError): pass
```

### Guidelines
1. **Raise with context**: Include relevant values in error message
2. **Catch specifically**: Use most specific exception that makes sense
3. **Document**: Every exception needs docstring explaining when it's raised
4. **Test**: Every raised exception should have a test case

### Example Test
```python
def test_freshness_check_raises_on_stale_data():
    """Stale data should raise StalenessError."""
    old_data = create_data(timestamp="2024-01-01 00:00:00")

    with pytest.raises(StalenessError) as exc_info:
        check_freshness(old_data, max_age_minutes=30)

    assert "exceeds 30m" in str(exc_info.value)
```

## Related ADRs
- ADR-0001: Data Pipeline Architecture (uses DataQualityError exceptions)
- (Future) ADR-00XX: Retry Strategy (will use exception types to determine retry behavior)
- (Future) ADR-00XX: Alerting Rules (will route alerts based on exception type)

## References
- [Python Exception Hierarchy](https://docs.python.org/3/library/exceptions.html#exception-hierarchy)
- [PEP 8: Exception Names](https://peps.python.org/pep-0008/#exception-names)
- "Effective Python" by Brett Slatkin - Item 87: Define a Root Exception

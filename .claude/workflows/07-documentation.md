# Documentation Writing Workflow

**Purpose:** Write comprehensive documentation following project standards
**When:** During implementation, after trading concepts, before PR
**Prerequisites:** Code implemented and tested
**Expected Outcome:** Complete documentation (docstrings, concept docs, guides)

---

## Quick Reference

**Standards:** See [/docs/STANDARDS/DOCUMENTATION_STANDARDS.md](../../docs/STANDARDS/DOCUMENTATION_STANDARDS.md)

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

    See Also:
        /docs/CONCEPTS/momentum-signals.md
    """
```

**Required sections:** Summary, description, Args, Returns, Raises, Example, Notes, See Also

### 2. Create Concept Documentation

**For trading-specific features, create `/docs/CONCEPTS/{concept-name}.md`:**

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

### 3. Create Implementation Guides

**For complex features or workflows, create `/docs/IMPLEMENTATION_GUIDES/{feature-name}.md`:**

```markdown
# Feature/Component Implementation Guide

## Overview
What this component does and why it exists

## Architecture
How it fits into the system

## Step-by-Step Implementation
Detailed implementation steps with code examples

## Testing
How to test this feature

## Common Issues
Known gotchas and solutions
```

### 4. Inline Comments for Complex Logic

```python
# Use comments to explain WHY, not WHAT

# Adjust for stock split by dividing historical prices
# 4-for-1 split means all pre-split prices ÷ 4
adj_close = close / split_ratio

# Retry on timeout (network issue), not on 4xx (client error)
# 4xx means our request is invalid, retry won't help
if 400 <= status_code < 500:
    raise OrderValidationError(...)
```

---

## Decision Points

### In-code vs Separate File?

**In-code (docstring):**
- Function/class/method documentation
- Parameter explanations
- Usage examples

**Separate file (concept doc):**
- Trading concepts (momentum, P&L, circuit breakers)
- Complex algorithms
- Design rationale
- Background knowledge

### How Detailed?

**Comprehensive:**
- Public APIs
- Trading logic
- Complex algorithms
- Safety-critical code

**Concise:**
- Internal utilities
- Self-explanatory code
- Simple operations

---

## Common Issues

### Don't Know What to Document

**Solution:**
- Start with function signature (args, returns)
- Add one-line summary
- Add example showing usage
- Explain non-obvious behavior
- Link to related concepts

### Examples Don't Run

**Solution:** Test your examples!

```python
def test_docstring_example():
    """Verify docstring example works."""
    df = pl.DataFrame({"symbol": ["AAPL"], "close": [150, 153]})
    result = compute_momentum(df, lookback=1)["momentum"].to_list()
    assert result == [None, 0.02]
```

---

## Validation

**How to verify documentation is complete:**
- [ ] All functions have Google-style docstrings
- [ ] Examples are executable and correct
- [ ] Trading concepts documented in `/docs/CONCEPTS/`
- [ ] Links work (no 404s)
- [ ] Follows DOCUMENTATION_STANDARDS.md

---

## Related Workflows

- [08-adr-creation.md](./08-adr-creation.md) - Creating ADRs for architecture
- [01-git.md](./01-git.md) - Include docs in commits

---

## References

- [/docs/STANDARDS/DOCUMENTATION_STANDARDS.md](../../docs/STANDARDS/DOCUMENTATION_STANDARDS.md) - Complete standards
- Google Style Guide: https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings

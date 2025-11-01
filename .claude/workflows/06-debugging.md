# Debugging Workflow

**Purpose:** Systematically debug failing tests and production issues
**When:** Tests failing, bugs in code review, unexpected behavior
**Prerequisites:** Failing test or bug report
**Expected Outcome:** Root cause identified and fixed

---

## Quick Reference

**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## Step-by-Step Process

### 1. Reproduce the Issue

```bash
# Run failing test
pytest tests/test_file.py::test_name -v -s

# Or reproduce production scenario
python scripts/reproduce_issue.py
```

### 2. Use Debugger

```bash
# Drop into pdb on failure
pytest tests/test_file.py::test_name --pdb

# Or run with python debugger
python -m pdb script.py
```

**Basic pdb commands:**
- `n` - Next line
- `s` - Step into function
- `c` - Continue execution
- `p variable` - Print variable value
- `l` - List code around current line
- `q` - Quit debugger

**Alternative: Add breakpoint in code:**
```python
import pdb; pdb.set_trace()
```

### 3. Add Debug Output

```python
# Add logging
import logging
logger = logging.getLogger(__name__)
logger.debug(f"Variable value: {variable}")

# Or print statements
print(f"DEBUG: value={value}, expected={expected}")
```

### 4. Identify Root Cause

**Check:**
- Variable values at failure point
- Function inputs vs expected
- State changes during execution
- External dependencies (DB, API, files)

### 5. Fix and Verify

```bash
# Make the fix
# Add test to prevent regression

# Run test again
pytest tests/test_file.py::test_name -v

# Run full suite
make test
```

---

## Decision Points

### Debugger vs Print Statements?

**Use debugger (pdb) when:**
- Complex flow with many variables
- Need to step through code
- Issue is reproducible

**Use print/logging when:**
- Simple quick check
- Production issues (can't use debugger)
- Async code (debugger can be tricky)

### Fix Now or File for Later?

**Fix immediately:**
- Blocks development
- Causes test failures
- Trading safety issue
- Easy fix (<15 min)

**File for later:**
- Edge case, low priority
- Requires investigation
- Not blocking current work

---

## Common Issues & Solutions

### Assertion Error

```python
# Test shows:
> assert result == expected
E AssertionError: assert 100 == 50

# Debug:
print(f"result={result}, expected={expected}")
print(f"Steps: base={base}, multiplier={mult}, result={base*mult}")
```

### Type Error (NoneType)

```python
# Error: 'NoneType' object has no attribute 'something'

# Fix: Add None check
if value is not None:
    result = value.something
```

### Race Condition

```python
# Test passes sometimes, fails sometimes

# Debug:
# 1. Add delays to expose race
import time; time.sleep(0.1)

# 2. Add locks around shared state
# 3. Use atomic operations
```

---

## Example: Debug and Fix

```bash
$ pytest tests/test_order_placer.py::test_limit_check -v
FAILED - AssertionError: assert False == True

$ pytest tests/test_order_placer.py::test_limit_check --pdb
> assert placer.check_limit("AAPL", 100) == True
(Pdb) placer.max_position
50  # Found it! Max is 50, test uses 100

# Fix test to use valid value
$ pytest tests/test_order_placer.py::test_limit_check -v
PASSED ✅

# Run full suite
$ make test
==================== 296 passed in 2.18s ====================
```

---

## Validation

**How to verify debugging succeeded:**
- [ ] Root cause identified
- [ ] Fix implemented
- [ ] Test added to prevent regression
- [ ] All tests pass
- [ ] Issue no longer reproduces

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Review fixes before commit

---

## References

- [Test Commands Reference](./_common/test-commands.md) - Testing commands and patterns
- Python debugger: https://docs.python.org/3/library/pdb.html
- pytest debugging: https://docs.pytest.org/en/stable/how-to/failures.html

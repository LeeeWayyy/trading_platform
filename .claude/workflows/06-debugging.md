# Debugging Workflow

**Purpose:** Systematically debug failing tests and production issues
**Prerequisites:** Failing test or bug report
**Expected Outcome:** Root cause identified and fixed
**Owner:** @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

- Tests failing
- Bugs reported in code review
- Production issues in DRY_RUN or paper trading
- Unexpected behavior

---

## Step-by-Step Process

### 1. Reproduce the Issue

```bash
# Run failing test
pytest tests/test_file.py::test_name -v -s

# Or reproduce production scenario
python scripts/reproduce_issue.py
```

### 2. Add Debug Output

```python
# Add logging
import logging
logger = logging.getLogger(__name__)
logger.debug(f"Variable value: {variable}")

# Or print statements
print(f"DEBUG: value={value}, expected={expected}")

# Or use debugger
import pdb; pdb.set_trace()
```

### 3. Run With Debugger

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

### 4. Identify Root Cause

**Check:**
- Variable values at failure point
- Function inputs vs expected
- State changes during execution
- External dependencies (DB, API, files)

### 5. Fix and Verify

```python
# Make the fix
# Add test to prevent regression

# Run test again
pytest tests/test_file.py::test_name -v

# Run full suite
make test
```

---

## Decision Points

### Should I use debugger or print statements?

**Use debugger (pdb) when:**
- Complex flow with many variables
- Need to step through code line by line
- Want to inspect state at specific point
- Issue is reproducible

**Use print/logging when:**
- Simple quick check
- Issues in production (can't use debugger)
- Async code (debugger can be tricky)
- Want persistent output

### Should I fix the bug or file it for later?

**Fix immediately:**
- Blocks development
- Causes test failures
- Trading safety issue
- Easy fix (<15 min)

**File for later:**
- Edge case, low priority
- Requires investigation
- Not blocking current work
- Create ticket and document

---

## Common Issues & Solutions

### Assertion Error

```python
# Test shows:
> assert result == expected
E AssertionError: assert 100 == 50

# Debug:
# 1. Print both values
print(f"result={result}, expected={expected}")

# 2. Check calculation
print(f"Steps: base={base}, multiplier={mult}, result={base*mult}")

# 3. Verify test expectations are correct
```

### Type Error

```python
# Error: 'NoneType' object has no attribute 'something'

# Debug:
# 1. Check where None comes from
# 2. Add None check
if value is not None:
    result = value.something

# 3. Fix upstream to not return None
```

### Race Condition

```python
# Test passes sometimes, fails sometimes

# Debug:
# 1. Add delays to expose race
import time; time.sleep(0.1)

# 2. Add locks around shared state
# 3. Use atomic operations
# 4. Add test to catch races
```

---

## Examples

### Example 1: Debug Failing Test

```bash
$ pytest tests/test_order_placer.py::test_limit_check -v
FAILED - AssertionError: assert False == True

$ pytest tests/test_order_placer.py::test_limit_check -v -s --pdb
> assert placer.check_limit("AAPL", 100) == True
(Pdb) placer.max_position
50  # Found it! Max is 50, test uses 100

# Fix test to use valid value
$ pytest tests/test_order_placer.py::test_limit_check -v
PASSED ✅
```

### Example 2: Debug Production Issue

```python
# Issue: Position calculation returns wrong value

# Add debug logging
logger.debug(f"calc_position: current={current}, fills={fills}, total={current+fills}")

# Run script
$ python scripts/reproduce_issue.py
DEBUG: calc_position: current=100, fills=50, total=150  # Expected!
DEBUG: calc_position: current=None, fills=50, total=error  # Found it!

# Fix: Handle None case
if current is None:
    current = 0
```

---

## Validation

**How to verify debugging succeeded:**
- [ ] Root cause identified
- [ ] Fix implemented
- [ ] Test added to prevent regression
- [ ] All tests pass
- [ ] Issue no longer reproduces

**What to check if issue persists:**
- Verify you're testing the fix (not cached code)
- Check if issue is environmental
- Confirm test actually covers the scenario
- Review if fix addresses root cause or just symptom

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Review fixes before commit

---

## References

- Python debugger: https://docs.python.org/3/library/pdb.html
- pytest debugging: https://docs.pytest.org/en/stable/how-to/failures.html

---

**Maintenance Notes:**
- Update when new debugging tools added
- Review when common issues change
- Add new debugging patterns as discovered

# Testing Workflow

**Purpose:** Run tests and validate code before committing and during development
**Prerequisites:** Code implemented, test environment set up
**Expected Outcome:** All tests pass, code validated, ready to commit
**Owner:** @qa-team + @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Run tests:**
- ✅ Before each commit (MANDATORY)
- ✅ After implementing new features
- ✅ After fixing bugs
- ✅ After modifying existing code
- ✅ Before requesting zen-mcp review
- ✅ Before creating PR

**Frequency:** Multiple times per development session

---

## Step-by-Step Process

### 1. Quick Smoke Test (5-10 seconds)

**Run fast, focused tests to catch obvious breaks:**

```bash
# Run only tests for what you changed
pytest tests/apps/execution_gateway/test_order_placer.py -v

# Or run tests matching a pattern
pytest -k "test_position_limit" -v
```

**What this does:** Fast feedback on your changes without waiting for full suite

### 2. Full Test Suite (1-3 minutes)

**Run all tests before committing:**

```bash
make test
```

**This runs:**
- All unit tests
- All integration tests
- Coverage report

**Expected output:**
```
===================== X passed in Y.YYs ======================
Coverage: ZZ%
```

(Where X = number of tests, Y = time in seconds, ZZ = coverage percentage)

**If failures:** See step 5 for debugging

### 3. Linting (10-20 seconds)

**Check code style and type safety:**

```bash
make lint
```

**This runs:**
- `mypy --strict` - Type checking
- `ruff check` - Linting
- `black --check` - Format checking

**Expected:** No errors

**If errors:** See common issues section

### 4. Coverage Check (Optional)

**Check test coverage for your changes:**

```bash
# Generate coverage report
make coverage

# View HTML report
open htmlcov/index.html
```

**Target:** ≥80% coverage for new code

### 5. Debugging Failed Tests

**If tests fail:**

**A. See detailed output:**
```bash
# Run with verbose output
pytest tests/test_failing.py -v

# Run with print statements visible
pytest tests/test_failing.py -v -s

# Run specific test
pytest tests/test_file.py::test_function_name -v
```

**B. Use debugger:**
```bash
# Drop into debugger on failure
pytest tests/test_file.py --pdb

# Or add breakpoint in code
import pdb; pdb.set_trace()
```

**C. Check test output:**
- Read error message carefully
- Check expected vs actual values
- Review stack trace
- Check test setup/teardown

See [06-debugging.md](./06-debugging.md) for detailed debugging workflow.

### 6. Fix Issues and Re-run

```bash
# Fix the code
# Re-run tests
make test

# If still failing, debug more
pytest tests/test_file.py::test_name -v -s --pdb
```

---

## Decision Points

### Should I run full suite or focused tests?

**Run focused tests when:**
- 🔄 During active development (fast feedback)
- 🐛 Debugging specific failure
- ⚡ Want quick validation

**Run full suite when:**
- ✅ Before committing (MANDATORY)
- 📝 Before zen-mcp review
- 🔀 Before creating PR
- 🎯 After modifying core code

### Should I fix failing tests or skip them?

**NEVER skip failing tests!**

**Fix immediately:**
- Tests related to your changes
- Tests you broke

**Can defer IF:**
- Pre-existing failures (not your fault)
- User approves deferral
- Created ticket for follow-up
- Document in commit message

**To skip temporarily (ONLY if pre-existing):**
```python
@pytest.mark.skip(reason="Pre-existing failure, ticket P1.5T7")
def test_something():
    ...
```

### Tests taking too long?

**Normal:** 1-3 minutes for full suite
**Slow:** > 5 minutes

**If too slow:**
- Run focused tests during development
- Run full suite before commits
- Consider parallelization: `pytest -n auto`
- Identify slow tests: `pytest --durations=10`

---

## Common Issues & Solutions

### Issue: ImportError or ModuleNotFoundError

**Symptom:**
```
ImportError: cannot import name 'something' from 'module'
```

**Solutions:**
```bash
# 1. Install dependencies
poetry install

# 2. Check PYTHONPATH
export PYTHONPATH=.
pytest

# 3. Activate virtual environment
source .venv/bin/activate

# 4. Check if module exists
ls -la apps/module_name/
```

### Issue: Tests Pass Locally But Fail in CI

**Common causes:**

**1. Environment differences:**
```bash
# Check Python version
python --version  # Should match CI

# Check dependencies
poetry install --sync
```

**2. Database state:**
```bash
# Reset database
make db-reset
pytest
```

**3. Missing environment variables:**
```bash
# Check .env file
cat .env.example
# Ensure CI has same vars
```

### Issue: Mypy Type Errors

**Symptom:**
```
error: Argument 1 to "foo" has incompatible type "str"; expected "int"
```

**Solutions:**
```python
# Fix type annotation
def foo(value: int) -> str:  # Correct type
    return str(value)

# Or add type cast
result = foo(int(string_value))

# Or add type ignore (last resort)
result = foo(value)  # type: ignore[arg-type]
```

### Issue: Ruff Linting Errors

**Common fixes:**
```bash
# Auto-fix what's possible
ruff check --fix .

# Format code
black .

# Then check again
make lint
```

### Issue: Tests Hang or Timeout

**Symptoms:**
- Test runs forever
- No output
- Have to Ctrl+C

**Debug:**
```bash
# Run with timeout
pytest tests/test_file.py --timeout=10

# Run one test to isolate
pytest tests/test_file.py::test_name -v -s

# Check for infinite loops, missing mocks, or blocking I/O
```

---

## Examples

### Example 1: Normal Test Run

```bash
$ make test

==================== test session starts ====================
platform darwin -- Python 3.11.5
collected 296 items

tests/libs/data_pipeline/test_etl.py ........... [ 10%]
tests/apps/signal_service/test_generator.py .... [ 25%]
tests/apps/execution_gateway/test_orders.py .... [ 50%]
...

==================== 296 passed in 2.14s ====================

Coverage report:
Name                              Stmts   Miss  Cover
-----------------------------------------------------
apps/execution_gateway/main.py      145      8    94%
apps/signal_service/generator.py     98      3    97%
...
-----------------------------------------------------
TOTAL                              2847    142    95%

✅ All tests passed!
```

### Example 2: Test Failure and Fix

```bash
$ pytest tests/apps/execution_gateway/test_order_placer.py -v

==================== FAILURES ====================
______ test_position_limit_check _______

    def test_position_limit_check():
        placer = OrderPlacer()
>       assert placer.check_position_limit("AAPL", 100) == True
E       AssertionError: assert False == True
E       + where False = <bound method OrderPlacer.check_position_limit...

tests/test_order_placer.py:45: AssertionError

==================== 1 failed in 0.24s ====================

# Debug the issue
$ pytest tests/test_order_placer.py::test_position_limit_check -v -s --pdb

> /Users/.../test_order_placer.py(45)test_position_limit_check()
-> assert placer.check_position_limit("AAPL", 100) == True
(Pdb) placer.max_position
50  # Ah! Max is 50, requesting 100 fails!

(Pdb) quit

# Fix the test (was using wrong value)
# Edit test to use 50 instead of 100

$ pytest tests/test_order_placer.py::test_position_limit_check -v
==================== 1 passed in 0.11s ====================

# Run full suite to be sure
$ make test
==================== 296 passed in 2.18s ====================
✅
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] All tests pass (`make test` shows "passed")
- [ ] No lint errors (`make lint` clean)
- [ ] Coverage ≥80% for new code
- [ ] No skipped tests (unless pre-existing and documented)
- [ ] Ready to commit

**What to check if something seems wrong:**
- Run `pytest --collect-only` to see if tests are discovered
- Check virtual environment is activated
- Verify dependencies installed: `poetry install`
- Check database is running and migrated
- Review test output for specific error messages

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Run tests before committing
- [06-debugging.md](./06-debugging.md) - Detailed debugging when tests fail
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Run tests before zen review

---

## References

**Standards:**
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Test requirements and structure
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code quality standards

**Setup:**
- [/docs/GETTING_STARTED/TESTING_SETUP.md](../../docs/GETTING_STARTED/TESTING_SETUP.md) - Test environment setup

**Tools:**
- pytest: https://docs.pytest.org/
- coverage: https://coverage.readthedocs.io/
- mypy: https://mypy.readthedocs.io/
- ruff: https://docs.astral.sh/ruff/

---

**Maintenance Notes:**
- Update when test framework changes
- Review when new test patterns added
- Adjust if test execution time increases significantly

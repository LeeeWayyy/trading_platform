# Testing Workflow

**Purpose:** Run tests and validate code before committing
**When:** Before EVERY commit, after implementing features, before zen-mcp review
**Prerequisites:** Code implemented, test environment set up
**Expected Outcome:** All tests pass, code validated, ready to commit

---

## Quick Reference

**Test Commands:** See [Test Commands Reference](./_common/test-commands.md)
**Zen Review:** See [Zen-MCP Review Process](./_common/zen-review-process.md)

---

## When to Use This Workflow

**MANDATORY before:**
- ✅ Each commit
- ✅ Zen-mcp review request
- ✅ Creating PR

**Frequency:** Multiple times per development session (~2-3 minutes each)

---

## Step-by-Step Process

### 1. Quick Smoke Test (5-10 seconds)

**Run focused tests for fast feedback:**

```bash
# Test specific file
pytest tests/apps/execution_gateway/test_order_placer.py -v

# Test by pattern
pytest -k "test_position_limit" -v
```

### 2. Full Test Suite (MANDATORY before commit)

```bash
make test
```

**Expected:** All tests pass, coverage ≥80%

See [Test Commands Reference](./_common/test-commands.md) for all testing options.

**If failures:** See [06-debugging.md](./06-debugging.md)

### 3. Linting (10-20 seconds)

```bash
make lint
```

**Expected:** No mypy, ruff, or black errors

### 4. Full CI Suite (MANDATORY before commit)

```bash
make ci-local
```

**This runs:**
1. `make fmt` - Format code
2. `make lint` - Type checking and linting
3. `make test` - Full test suite

**Expected:** ✅ All green

See [Test Commands Reference](./_common/test-commands.md#full-ci-suite) for details.

---

## Decision Points

### Should I run full suite or focused tests?

**Focused tests when:**
- 🔄 During active development (fast feedback)
- 🐛 Debugging specific failure

**Full suite when:**
- ✅ Before committing (MANDATORY)
- 📝 Before zen-mcp review (MANDATORY)
- 🔀 Before creating PR (MANDATORY)

### Should I fix or skip failing tests?

**NEVER skip failing tests!**

**Fix immediately:**
- Tests related to your changes
- Tests you broke

**Can defer ONLY if:**
- Pre-existing failures (not your fault)
- User approves deferral
- Ticket created for follow-up
- Marked with `@pytest.mark.skip(reason="Pre-existing, ticket P1.5T7")`

---

## Common Issues & Solutions

### ImportError or ModuleNotFoundError

```bash
# Install dependencies
poetry install

# Activate virtual environment
source .venv/bin/activate

# Set PYTHONPATH
export PYTHONPATH=.
pytest
```

### Tests Pass Locally But Fail in CI

**Common causes:**

1. **Environment differences:**
   ```bash
   python --version  # Match CI version
   poetry install --sync
   ```

2. **Database state:**
   ```bash
   make db-reset
   pytest
   ```

3. **Missing environment variables:**
   ```bash
   cat .env.example  # Ensure CI has same vars
   ```

### Mypy Type Errors

```python
# Fix type annotation
def foo(value: int) -> str:
    return str(value)

# Or add type cast
result = foo(int(string_value))

# Or type ignore (last resort)
result = foo(value)  # type: ignore[arg-type]
```

### Ruff Linting Errors

```bash
# Auto-fix what's possible
ruff check --fix .

# Format code
black .

# Then check again
make lint
```

### Tests Hang or Timeout

```bash
# Run with timeout
pytest tests/test_file.py --timeout=10

# Run one test to isolate
pytest tests/test_file.py::test_name -v -s
```

---

## Example: Normal Test Run

```bash
$ make ci-local

# Format code
black .
All done! ✨

# Lint
mypy . --strict
Success: no issues found
ruff check .
All checks passed!

# Run tests
==================== 296 passed in 2.14s ====================
Coverage: 95%

✅ All tests passed!
```

---

## Validation

**How to verify this workflow succeeded:**
- [ ] All tests pass (`make test`)
- [ ] No lint errors (`make lint`)
- [ ] Coverage ≥80% for new code
- [ ] `make ci-local` green
- [ ] Ready to commit

---

## Related Workflows

- [01-git-commit.md](./01-git-commit.md) - Run tests before committing
- [06-debugging.md](./06-debugging.md) - Debug failing tests
- [03-zen-review-quick.md](./03-zen-review-quick.md) - Run tests before review

---

## References

- [Test Commands Reference](./_common/test-commands.md) - Complete testing guide
- [Zen-MCP Review Process](./_common/zen-review-process.md) - Three-tier review system
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Test requirements
- [/docs/STANDARDS/CODING_STANDARDS.md](../../docs/STANDARDS/CODING_STANDARDS.md) - Code quality standards

# CI/CD Triage Workflow

**Purpose:** Diagnose and fix CI/CD pipeline failures quickly
**When:** PR shows ❌ failing checks
**Prerequisites:** PR created, CI pipeline running
**Expected Outcome:** CI passing, PR ready for merge

---

## Quick Reference

**Testing:** See [Test Commands Reference](./_common/test-commands.md)
**Debugging:** See [06-debugging.md](./06-debugging.md)

---

## Step-by-Step Process

### 1. Identify Which Check Failed

```bash
# List all checks
gh pr checks <PR-number>

# Expected output:
# ✅ test-suite       passed
# ❌ lint            failed
# ✅ build           passed
```

**Common CI checks:**
- `test-suite` - Pytest unit/integration tests
- `lint` - Mypy + Ruff
- `format` - Black code formatting
- `build` - Docker image build
- `security` - Dependency scan
- `coverage` - Code coverage threshold

### 2. View Failure Details

```bash
# View specific check logs
gh pr checks <PR-number> --watch

# Or view in browser
gh pr view <PR-number> --web
```

**What to look for:**
- Error message at end of logs
- Stack trace with file:line number
- Failed assertion details
- Missing dependencies

### 3. Reproduce Locally

```bash
# Run exact command CI uses
make ci-local

# Run specific failing test
pytest tests/test_file.py::test_name -v -s

# Run linters
make lint
```

See [Test Commands Reference](./_common/test-commands.md) for all testing options.

### 4. Fix the Issue

**Test failures:** See [06-debugging.md](./06-debugging.md)

**Lint errors:**
```python
# Fix type annotation
def foo(value: int) -> str:
    return str(value)

# Or type ignore (last resort)
result = foo(value)  # type: ignore[arg-type]
```

**Format errors:**
```bash
# Auto-format
black .
ruff check --fix .
```

### 5. Verify Fix Locally

```bash
# Run full CI suite locally
make ci-local

# Verify all checks pass
```

### 6. Push Fix and Monitor CI

```bash
# Commit fix
git add .
git commit -m "Fix CI: [describe fix]"
git push

# Watch CI status
gh pr checks <PR-number> --watch
```

---

## Decision Points

### Rerun CI or Fix Code?

**Rerun CI (without code changes) when:**
- Flaky test (passes sometimes)
- Network timeout (external API)
- CI infrastructure issue
- Dependency download failed

**Fix code when:**
- Test consistently fails
- Lint/format errors
- Actual logic error
- Missing dependency

**How to rerun:**
```bash
gh pr checks <PR-number> --rerun-failed
```

### Tests Pass Locally But Fail in CI?

**Common causes:**

1. **Environment differences:**
   - Python version mismatch
   - OS differences (macOS local, Linux CI)
   - Missing .env variables in CI

2. **Test isolation issues:**
   - Tests modify shared database
   - Tests rely on execution order
   - Tests create temp files

3. **Timing/race conditions:**
   - Tests with sleep() rely on timing
   - Async tests with races

4. **Resource constraints:**
   - CI has less memory/CPU
   - Timeouts too aggressive

---

## Common Issues

### Lint Error: Type Incompatible

```python
# Fix type annotation
def foo(value: int) -> str:
    return str(value)

# Or add type cast
result = foo(int(string_value))
```

### Lint Error: Unused Import

```python
# Remove unused import
# from module import something  # ← Delete

# Or use it
something()
```

### Docker Build Failure

```bash
# Build with detailed output
docker build --progress=plain -t test .

# Check .dockerignore
cat .dockerignore

# Fix COPY path
COPY ./path/to/file /dest/
```

### Environment Differences

```python
# Use environment-agnostic code
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/test_db")
```

### Flaky Tests

```python
# Add retries for external calls
import tenacity

@tenacity.retry(wait=tenacity.wait_fixed(1), stop=tenacity.stop_after_attempt(3))
def test_api_call():
    response = requests.get("https://api.example.com")
    assert response.status_code == 200

# Use deterministic data
import random
random.seed(42)

# Increase timeouts
@pytest.mark.timeout(30)
def test_slow_operation():
    ...
```

---

## Validation

**How to verify CI is fixed:**
- [ ] All PR checks showing ✅ green
- [ ] No flaky tests (rerun 2-3 times to confirm)
- [ ] Local tests match CI results
- [ ] No new failures introduced
- [ ] Coverage threshold met
- [ ] Build completes successfully

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests locally
- [06-debugging.md](./06-debugging.md) - Debugging test failures
- [01-git.md](./01-git.md) - Commit fixes
- [01-git.md](./01-git.md) - PR creation and checks

---

## References

- [Test Commands Reference](./_common/test-commands.md) - Complete testing guide
- GitHub Actions: https://docs.github.com/en/actions
- pytest: https://docs.pytest.org/
- act (run GitHub Actions locally): https://github.com/nektos/act
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Testing requirements

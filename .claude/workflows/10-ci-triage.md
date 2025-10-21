# CI/CD Triage Workflow

**Purpose:** Diagnose and fix CI/CD pipeline failures quickly
**Prerequisites:** PR created, CI pipeline running
**Expected Outcome:** CI passing, PR ready for merge
**Owner:** @devops-team + @development-team
**Last Reviewed:** 2025-10-21

---

## When to Use This Workflow

**Use this workflow when:**
- PR shows ❌ failing checks
- CI pipeline fails unexpectedly
- Tests pass locally but fail in CI
- Linting/formatting errors in CI
- Docker build fails in CI
- Database migration fails in CI

**Check CI status:**
```bash
# View PR checks
gh pr view <PR-number> --json statusCheckRollup

# Or check GitHub Actions
open https://github.com/user/repo/actions
```

---

## Step-by-Step Process

### 1. Identify Which Check Failed

**View CI status:**
```bash
# List all checks
gh pr checks <PR-number>

# Expected output:
# ✅ test-suite       passed
# ❌ lint            failed
# ✅ build           passed
# ❌ integration     failed
```

**Common CI checks:**
- `test-suite` - Pytest unit/integration tests
- `lint` - Mypy type checking + Ruff linting
- `format` - Black code formatting
- `build` - Docker image build
- `security` - Dependency vulnerability scan
- `coverage` - Code coverage threshold

### 2. View Failure Details

**Get detailed logs:**
```bash
# View specific check logs
gh pr checks <PR-number> --watch

# Or view in browser
gh pr view <PR-number> --web
# Click on "Details" next to failed check
```

**What to look for:**
- Error message at end of logs
- Stack trace with file:line number
- Failed assertion details
- Missing dependencies
- Timeout messages

### 3. Reproduce Locally

**Most common: Reproduce test failure:**
```bash
# Run exact command CI uses
pytest tests/ -v --cov

# Run specific failing test
pytest tests/test_file.py::test_name -v -s

# Check if it passes locally
# If yes → environment issue
# If no → code issue
```

**Reproduce lint failure:**
```bash
# Run linters
make lint

# Or run individually
mypy --strict .
ruff check .
black --check .
```

**Reproduce build failure:**
```bash
# Build docker image
docker build -t trading_platform:test .

# Check for errors
docker build --progress=plain -t trading_platform:test .
```

### 4. Fix the Issue

**Based on failure type, see specific sections below:**
- Test failures → See [#test-failures](#common-issues--solutions)
- Lint errors → See [#lint-errors](#common-issues--solutions)
- Build failures → See [#docker-build-failures](#common-issues--solutions)
- Environment issues → See [#environment-differences](#common-issues--solutions)

### 5. Verify Fix Locally

```bash
# Run full CI suite locally
make test && make lint

# Or use act (GitHub Actions locally)
brew install act  # macOS
act -j test  # Run specific job

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

# Wait for all checks to pass ✅
```

---

## Decision Points

### Should I rerun CI or fix the code?

**Rerun CI (without code changes) when:**
- Flaky test (passes sometimes)
- Network timeout (external API)
- CI infrastructure issue (GitHub outage)
- Dependency download failed
- Random timeout in parallel tests

**Fix code when:**
- Test consistently fails
- Lint/format errors
- Actual logic error
- Missing dependency in requirements
- Test relies on local state

**How to rerun:**
```bash
# Rerun all failed checks
gh pr checks <PR-number> --rerun-failed

# Or via GitHub UI: Click "Re-run jobs"
```

### Should I skip failing tests or fix them?

**NEVER skip failing tests in CI!**

**Fix immediately when:**
- Test fails due to your changes
- Test fails on main branch too (regression)
- Test is critical for trading safety

**Can investigate later IF:**
- Pre-existing failure (not your changes)
- User approves skip with ticket created
- Documented with `pytest.mark.skip` + reason

**To skip (ONLY pre-existing failures):**
```python
@pytest.mark.skip(reason="Pre-existing failure, ticket #123")
def test_something():
    ...
```

### Tests pass locally but fail in CI - why?

**Common causes:**

**1. Environment differences:**
- Python version (3.11 local, 3.10 CI?)
- OS differences (macOS local, Linux CI)
- Missing .env variables in CI
- Timezone differences (UTC in CI)

**2. Test isolation issues:**
- Tests modify shared database
- Tests rely on execution order
- Tests create temp files not cleaned up
- Tests mock global state

**3. Timing/race conditions:**
- Tests with sleep() rely on timing
- Async tests with race conditions
- Tests rely on external service response time

**4. Resource constraints:**
- CI has less memory/CPU
- Timeouts too aggressive
- Parallel execution issues

---

## Common Issues & Solutions

### Test Failures

**Issue: `AssertionError: assert False == True`**

**Debug:**
```bash
# Run test with verbose output
pytest tests/test_file.py::test_name -vv

# Check test setup/teardown
pytest tests/test_file.py::test_name -s --setup-show

# Add debug prints
# (then check CI logs for output)
```

**Common causes:**
- Assertion expectation wrong
- Test data incorrect
- Mocking not working
- Database state issue

**Solution:**
```python
# Fix assertion
assert actual == expected, f"Expected {expected}, got {actual}"

# Or fix test data
expected = compute_expected_value()
```

### Lint Errors

**Issue: `mypy: error: Argument has incompatible type`**

**Solution:**
```python
# Fix type annotation
def foo(value: int) -> str:  # Correct type
    return str(value)

# Or add type cast
result = foo(int(string_value))

# Or type ignore (last resort)
result = foo(value)  # type: ignore[arg-type]
```

**Issue: `ruff: F401 'module' imported but unused`**

**Solution:**
```python
# Remove unused import
# from module import something  # ← Delete

# Or use it
something()

# Or mark as re-export (if intentional)
from module import something as something  # Re-export
```

**Issue: `black: would reformat file.py`**

**Solution:**
```bash
# Auto-format
black .

# Then commit
git add -u
git commit -m "Apply black formatting"
```

### Docker Build Failures

**Issue: `ERROR: failed to solve: failed to copy files`**

**Debug:**
```bash
# Build with detailed output
docker build --progress=plain -t test .

# Check .dockerignore
cat .dockerignore

# Verify files exist
ls -la <missing-file>
```

**Common causes:**
- File in .dockerignore
- Wrong COPY path in Dockerfile
- File not committed to git
- Build context issue

**Solution:**
```dockerfile
# Fix COPY path
COPY ./path/to/file /dest/

# Or add to .dockerignore exception
!path/to/needed/file
```

**Issue: `ERROR: failed to solve: process "/bin/sh -c pip install" did not complete`**

**Debug:**
```bash
# Check requirements.txt
cat requirements.txt | grep -v "^#" | sort

# Try installing locally
pip install -r requirements.txt
```

**Common causes:**
- Dependency version conflict
- Package no longer exists
- Network timeout
- Python version incompatibility

**Solution:**
```txt
# Fix requirements.txt
# Update version constraint
package>=1.0,<2.0

# Or pin specific version
package==1.2.3

# Or remove conflicting package
```

### Environment Differences

**Issue: Tests pass locally, fail in CI (environment)**

**Debug:**
```yaml
# Check CI environment in .github/workflows/test.yml
- name: Run tests
  env:
    DATABASE_URL: postgresql://...
    REDIS_HOST: localhost
```

**Solution:**
```python
# Use environment-agnostic code
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/test_db")

# Or add to CI config
# .github/workflows/test.yml:
env:
  PYTHONPATH: .
  DATABASE_URL: postgresql://postgres:password@localhost/test_db
```

**Issue: `ModuleNotFoundError` in CI but not locally**

**Debug:**
```bash
# Check PYTHONPATH
echo $PYTHONPATH

# Check requirements.txt
cat requirements.txt | grep module-name

# Check if installed
pip list | grep module-name
```

**Solution:**
```yaml
# Add to CI config
# .github/workflows/test.yml:
- name: Install dependencies
  run: |
    pip install -r requirements.txt
    pip install -e .  # Install project in editable mode

# Or fix import path
from apps.module import something  # Use absolute import
```

### Flaky Tests

**Issue: Test passes sometimes, fails sometimes**

**Symptoms:**
- Timing-dependent tests
- Tests with random data
- Tests with external API calls
- Parallel execution issues

**Solutions:**
```python
# 1. Add retries for external calls
import tenacity

@tenacity.retry(wait=tenacity.wait_fixed(1), stop=tenacity.stop_after_attempt(3))
def test_api_call():
    response = requests.get("https://api.example.com")
    assert response.status_code == 200

# 2. Use deterministic data
import random
random.seed(42)  # Fixed seed for reproducibility

# 3. Increase timeouts
@pytest.mark.timeout(30)  # 30 seconds
def test_slow_operation():
    ...

# 4. Mock external dependencies
@patch('requests.get')
def test_with_mock(mock_get):
    mock_get.return_value.status_code = 200
    ...
```

---

## Examples

### Example 1: Fix Lint Error in CI

```bash
# Scenario: CI shows lint failures

# Step 1: Check what failed
$ gh pr checks 123
❌ lint            failed

# Step 2: View details
$ gh pr checks 123 --watch
# See error: "mypy: error: Argument 1 has incompatible type 'str'; expected 'int'"

# Step 3: Reproduce locally
$ make lint
apps/signal_service/generator.py:45: error: Argument 1 to "compute" has incompatible type "str"; expected "int"

# Step 4: Fix the issue
# Edit apps/signal_service/generator.py:45
# Change: result = compute("123")
# To:     result = compute(int("123"))

# Step 5: Verify fix
$ make lint
✅ All checks passed

# Step 6: Commit and push
$ git add apps/signal_service/generator.py
$ git commit -m "Fix CI: Cast string to int in compute()"
$ git push

# Step 7: Watch CI
$ gh pr checks 123 --watch
✅ lint            passed

# Success!
```

### Example 2: Debug Flaky Test

```bash
# Scenario: Test fails in CI but passes locally

# Step 1: Identify flaky test
$ gh pr checks 123
❌ test-suite      failed
# Logs show: tests/test_reconciler.py::test_sync FAILED

# Step 2: Run test locally 10 times
$ for i in {1..10}; do pytest tests/test_reconciler.py::test_sync -v || break; done
# Passes all 10 times locally

# Step 3: Check CI environment differences
# CI runs on Linux, I'm on macOS
# CI might have timing differences

# Step 4: Look for timing issues in test
$ cat tests/test_reconciler.py
# See: time.sleep(0.1) # Wait for async operation

# Step 5: Fix by using proper async wait
# Before:
time.sleep(0.1)
assert reconciler.synced == True

# After:
import asyncio
await reconciler.wait_for_sync(timeout=5.0)
assert reconciler.synced == True

# Step 6: Commit fix
$ git add tests/test_reconciler.py
$ git commit -m "Fix CI: Use async wait instead of sleep in reconciler test"
$ git push

# Step 7: Monitor CI (run multiple times to confirm)
$ gh pr checks 123 --watch
✅ test-suite      passed

# Rerun to confirm not flaky
$ gh pr checks 123 --rerun-failed
✅ test-suite      passed

# Success!
```

---

## Validation

**How to verify CI is fixed:**
- [ ] All PR checks showing ✅ green
- [ ] No flaky tests (rerun 2-3 times to confirm)
- [ ] Local tests match CI results
- [ ] No new failures introduced
- [ ] Coverage threshold met (if applicable)
- [ ] Build completes successfully
- [ ] Lint/format checks pass

**What to check if CI still failing:**
- Review full CI logs (not just summary)
- Check for recent CI configuration changes
- Verify dependencies installed correctly
- Test with exact CI environment (use act or docker)
- Check GitHub Actions status page (outage?)
- Ask team if others seeing same failures

---

## Related Workflows

- [05-testing.md](./05-testing.md) - Running tests locally
- [06-debugging.md](./06-debugging.md) - Debugging test failures
- [01-git-commit.md](./01-git-commit.md) - Commit fixes
- [02-git-pr.md](./02-git-pr.md) - PR creation and checks

---

## References

**CI/CD Documentation:**
- GitHub Actions: https://docs.github.com/en/actions
- pytest: https://docs.pytest.org/
- Docker build: https://docs.docker.com/engine/reference/commandline/build/

**Testing:**
- [/docs/STANDARDS/TESTING.md](../../docs/STANDARDS/TESTING.md) - Testing requirements
- pytest plugins: https://docs.pytest.org/en/stable/plugins.html

**Debugging:**
- act (run GitHub Actions locally): https://github.com/nektos/act
- pytest debugging: https://docs.pytest.org/en/stable/how-to/failures.html

---

**Maintenance Notes:**
- Update when CI pipeline changes
- Review when new checks added
- Add common failures as they're discovered
- Update timeout values based on CI performance

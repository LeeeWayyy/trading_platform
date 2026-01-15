# Test Improvement Plan: Coverage & Parallel CI

**Branch:** `feature/test-improvement-coverage-parallel`
**Created:** 2026-01-15
**Status:** FINAL - Ready for Implementation

---

## Executive Summary

This plan addresses three critical test infrastructure goals:
1. **Test Consolidation**: Merge collocated tests into centralized `tests/` folder
2. **Coverage Improvement**: Increase repository-wide coverage from 19% to 85%+, with 95% branch coverage for critical services
3. **CI Performance**: Split monolithic CI tests into parallel jobs using runtime-based sharding

---

## Current State Analysis

### Coverage Metrics
- **Current overall coverage:** 19% (target: 85%)
- **Current branch coverage:** Unknown (target: 95% for P0)
- **Coverage threshold in CI:** 50% (was meant to be 80%)
- **Total test files:** 255 (in `tests/`) + 37 (collocated) = 292 total
- **Total tests:** 4,736
- **Integration tests:** 8 files
- **E2E tests:** 4 files

### Collocated Test Problem

**Current situation:** Tests exist in TWO locations:
- `tests/` - Main test directory (255 files)
- `libs/**/tests/` and `apps/**/tests/` - Collocated tests (37 files)

**Collocated test locations found:**
```
libs/redis_client/tests/               (needs audit)
libs/core/redis_client/tests/          (1 file)
libs/trading/risk_management/tests/    (7 files)
apps/execution_gateway/tests/          (15 files)
apps/signal_service/tests/             (6 files)
apps/market_data_service/tests/        (5 files)
apps/orchestrator/tests/               (2 files)
```

**Problems with collocated tests:**
1. **Duplication:** Same tests exist in both locations (e.g., `test_breaker.py`, `test_checker.py`)
2. **CI complexity:** Must configure shards to search two locations
3. **Discoverability:** Developers may not know tests exist in source directories
4. **Maintenance:** Changes require updating tests in multiple places

### Current CI Structure (`ci-tests-coverage.yml`)
- **Job 1:** `test-and-coverage` - Runs all unit tests sequentially
- **Job 2:** `integration-tests` - Runs after Job 1 completes
- **Total estimated CI time:** ~15-25 minutes (sequential)

### Critical Services Requiring 95% Branch Coverage (P0)

| Service/Library | Priority | Reason | Notes |
|----------------|----------|--------|-------|
| `libs/trading/risk_management/` | P0 | Circuit breakers, risk limits | `breaker.py`, `checker.py`, `kill_switch.py` |
| `libs/core/redis_client/` | P0 | Redis client, caching | `client.py`, `feature_cache.py` |
| `apps/execution_gateway/` | P0 | Critical path for all trades | `main.py`, `alpaca_client.py`, `reconciliation.py` |
| `apps/signal_service/` | P0 | Trading signals, model inference | Audit during Phase 0 |
| `apps/market_data_service/` | P0 | Market data, staleness detection | Audit during Phase 0 |
| `apps/orchestrator/` | P1 | Trading workflow coordination | Audit during Phase 0 |
| `libs/platform/` | P1 | Alerts, web console auth | Audit during Phase 0 |

**NOTE:** Exact file paths will be finalized during Phase 0 audit. The paths above are based on current directory structure.

---

## Proposed Solution

### Part 1: Test Consolidation (PREREQUISITE)

**Goal:** Single source of truth for all tests in `tests/` directory.

#### Why Consolidate?
- Simplifies CI configuration (one location to search)
- Eliminates duplicate test maintenance
- Improves discoverability for developers
- Reduces confusion about test ownership

#### Migration Plan

1. **Audit collocated tests** - Compare with existing tests in `tests/`
2. **Inventory conftest.py files** - List all local conftest.py in collocated directories
3. **Migrate conftest.py fixtures** - Place in appropriate `tests/` subtree hierarchy
4. **Merge unique tests** - Move tests that don't exist in `tests/`
5. **Resolve duplicates** - INVESTIGATE discrepancies (do NOT merge blindly)
6. **Remove collocated folders** - Delete `*/tests/` directories from source
7. **Update pytest config** - Simplify `testpaths` to just `tests`
8. **Update ownership docs** - Add test location mapping to CONTRIBUTING.md

#### Conftest.py Migration Plan

**CRITICAL:** Collocated tests often rely on local `conftest.py` files. These must be migrated correctly.

| Source conftest.py | Destination | Notes |
|-------------------|-------------|-------|
| `apps/execution_gateway/tests/conftest.py` | `tests/apps/execution_gateway/conftest.py` | Check for local imports |
| `apps/signal_service/tests/conftest.py` | `tests/apps/signal_service/conftest.py` | Verify fixture dependencies |
| `apps/market_data_service/tests/conftest.py` | `tests/apps/market_data_service/conftest.py` | If exists |
| (others) | Corresponding `tests/` subtree | Same pattern |

**NOTE:** Verify actual conftest.py existence before migration - some directories may not have them.

**Migration Steps for Each conftest.py:**
1. Copy to destination
2. Update any relative imports to absolute imports
3. Run affected tests to verify fixtures work
4. Remove original only after tests pass

#### Files to Migrate

| Source | Destination | Action |
|--------|-------------|--------|
| `libs/redis_client/tests/` | `tests/libs/redis_client/` | Audit & Merge (check vs libs/core/redis_client) |
| `libs/core/redis_client/tests/` | `tests/libs/core/redis_client/` | Merge |
| `libs/trading/risk_management/tests/` | `tests/libs/trading/risk_management/` | Merge (resolve duplicates) |
| `apps/execution_gateway/tests/` | `tests/apps/execution_gateway/` | Merge |
| `apps/signal_service/tests/` | `tests/apps/signal_service/` | Merge |
| `apps/market_data_service/tests/` | `tests/apps/market_data_service/` | Merge |
| `apps/orchestrator/tests/` | `tests/apps/orchestrator/` | Merge |

#### Duplicate Resolution Strategy

**DO NOT MERGE BLINDLY.** If two tests assert different outcomes for the same input, one is WRONG.

For each duplicate:
1. Compare test inputs and expected outputs
2. If identical assertions → keep one, delete other
3. If different assertions → INVESTIGATE which is correct
4. If both valid but different scenarios → keep both, rename to clarify
5. Document resolution in PR description

#### Known Duplicates to Investigate
- `test_breaker.py` - exists in both locations
- `test_checker.py` - exists in both locations
- `test_config.py` - exists in both locations
- `test_metrics.py` - exists in multiple locations
- `test_integration.py` - exists in both locations
- `test_types.py` - exists in both locations

### Part 2: CI Test Parallelization

#### Sharding Strategy: Runtime-Based (NOT Directory-Based)

**Initial Approach:** Start with directory-based shards, then optimize based on runtime data.

**Phase 1 (Initial):** Directory-based shards with explicit includes
**Phase 2 (After 3 CI runs):** Rebalance based on `pytest --durations` data

#### New CI Workflow Structure

**NOTE:** `ci-tests-parallel.yml` REPLACES `ci-tests-coverage.yml`. The old workflow should be deleted/deprecated after migration to prevent duplicate CI runs.

```yaml
name: CI - Parallel Tests & Coverage

jobs:
  # Stage 1: Quick checks (parallel with tests)
  lint-and-typecheck:
    runs-on: ubuntu-latest
    steps:
      - scripts/dev/validate_doc_index.sh          # Documentation checks
      - poetry run python scripts/dev/check_doc_freshness.py
      - poetry run python scripts/dev/generate_architecture.py --check
      - poetry run mypy libs/ apps/ strategies/ --strict   # Type checking
      - poetry run ruff check .                    # Linting
      - poetry run python scripts/dev/check_layering.py    # Layer violations
      - poetry run python scripts/testing/verify_gate_compliance.py  # Governance
      - poetry run python scripts/testing/verify_branch_protection.py  # Branch protection

  # Stage 2: Unit tests (parallel matrix)
  # Uses EXPLICIT includes (not exclusion-based "other")
  unit-tests:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        test-group:
          - name: libs-core
            paths: "tests/libs/core/"
          - name: libs-platform
            paths: "tests/libs/platform/"
          - name: libs-trading
            paths: "tests/libs/trading/ tests/libs/data/"
          - name: apps-services
            paths: "tests/apps/"
          - name: strategies
            paths: "tests/strategies/ tests/research/"
          - name: root-and-misc
            # EXPLICIT list, not exclusion-based
            paths: "tests/test_*.py tests/regression/ tests/workflows/ tests/fixtures/ tests/infra/ tests/load/"
    env:
      COVERAGE_FILE: .coverage.${{ matrix.test-group.name }}
      COVERAGE_RCFILE: pyproject.toml  # REQUIRED: consistent config
    steps:
      - name: Run unit tests with coverage
        run: |
          # Exclude integration/e2e tests and quarantined tests
          DESELECT_ARGS=""
          if [ -f tests/quarantine.txt ]; then
            DESELECT_ARGS=$(grep -v '^#' tests/quarantine.txt | cut -d'|' -f1 | xargs -I{} echo "--deselect {}" | tr '\n' ' ')
          fi
          PYTHONPATH=. poetry run pytest ${{ matrix.test-group.paths }} \
            -m "not integration and not e2e" \
            $DESELECT_ARGS \
            --cov=libs --cov=apps --cov-branch \
            --durations=50
      - Upload coverage artifact

  # Stage 3: Integration tests (after unit tests pass)
  # NOTE: Integration tests run WITHOUT coverage (see Part 3: Coverage Scope)
  # FUTURE OPTIMIZATION: If integration tests exceed 5 minutes, consider splitting into
  # parallel matrix jobs (e.g., integration-trading, integration-data)
  integration-tests:
    needs: [lint-and-typecheck, unit-tests]
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: trading_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      DATABASE_URL: postgresql://test:test@localhost:5432/trading_test
      REDIS_URL: redis://localhost:6379
      # IMPORTANT: Use mock/stub for broker - NO real API calls in CI
      ALPACA_USE_MOCK: "true"  # Enable broker mock mode
      ALPACA_API_KEY: "mock_key_for_ci"
      ALPACA_SECRET_KEY: "mock_secret_for_ci"
    steps:
      - name: Initialize database schema
        run: |
          # Use Alembic for migrations (db/alembic.ini exists)
          poetry run alembic upgrade head
      - name: Run integration tests with broker mock
        run: |
          # Verify no real network calls (respx or responses mock layer)
          PYTHONPATH=. poetry run pytest tests/integration/ --no-cov \
            -p no:randomly  # Deterministic order for integration tests
      - name: Assert no --cov flag accidentally passed
        run: |
          if grep -r "\-\-cov" .coverage* 2>/dev/null; then
            echo "ERROR: Coverage flag detected in integration tests"
            exit 1
          fi

#### Broker Mock Strategy for CI

**CRITICAL:** Integration tests MUST NOT make real network calls to broker APIs.

**Approved Stubbing Methods:**
1. **Mock Mode Flag:** Set `ALPACA_USE_MOCK=true` to enable in-process mock client
2. **VCR Recordings:** Use `pytest-recording` or `responses` library with pre-recorded fixtures
3. **Fake Server:** Run local fake Alpaca server (e.g., `tests/fixtures/fake_alpaca_server.py`)

**Implementation:**
```python
# libs/trading/alpaca_client.py
import os

def get_alpaca_client():
    if os.environ.get("ALPACA_USE_MOCK") == "true":
        return MockAlpacaClient()  # In-memory mock
    return RealAlpacaClient(api_key=..., secret_key=...)
```

**Network Blocking (MANDATORY):**
External network calls are FORBIDDEN in all CI jobs.

```yaml
# In CI workflow - enforce network blocking via pytest-socket
- name: Run tests with network blocking
  run: |
    # pytest-socket denies all network by default, allow only localhost
    PYTHONPATH=. poetry run pytest tests/integration/ \
      --no-cov \
      --disable-socket \
      --allow-hosts=localhost,127.0.0.1,::1 \
      -p no:randomly
```

**pytest-socket Configuration:**
```toml
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--disable-socket --allow-hosts=localhost,127.0.0.1,::1"
```

**CI Enforcement:**
All CI jobs that run tests MUST use `--disable-socket`. A test that attempts external network access will fail with:
```
SocketBlockedError: A]test tried to use socket.socket.connect
```

  # Stage 4: Coverage aggregation (unit tests only)
  coverage-report:
    needs: [unit-tests]  # Only depends on unit-tests, not integration-tests
    runs-on: ubuntu-latest
    env:
      COVERAGE_RCFILE: pyproject.toml  # REQUIRED: consistent config
    steps:
      # Step 1: Download coverage artifacts from all shards
      - name: Download coverage artifacts
        uses: actions/download-artifact@v4
        with:
          pattern: coverage-*
          merge-multiple: true

      # Step 2: Combine coverage files (MUST complete before ratchet)
      - name: Combine coverage files
        run: |
          coverage combine .coverage.*
          coverage xml -o coverage.xml
          coverage report --format=markdown >> $GITHUB_STEP_SUMMARY

      # Step 3: Run ratchet check AFTER combine
      - name: Check coverage ratchet
        run: |
          # IMPORTANT: Must run after coverage combine
          python scripts/testing/check_coverage_ratchet.py

      - name: Upload combined coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          files: coverage.xml
          fail_ci_if_error: true
```

#### Marker Governance Job

**CRITICAL:** Ensure tests are correctly marked to prevent unit/integration leakage.

```yaml
  marker-governance:
    runs-on: ubuntu-latest
    steps:
      - name: Verify integration tests have markers
        run: |
          # All tests in tests/integration/ MUST have @pytest.mark.integration
          UNMARKED=$(PYTHONPATH=. pytest --collect-only -q tests/integration/ -m "not integration" 2>/dev/null | grep -c "::" || true)
          if [ "$UNMARKED" -gt 0 ]; then
            echo "ERROR: $UNMARKED integration tests missing @pytest.mark.integration marker"
            PYTHONPATH=. pytest --collect-only -q tests/integration/ -m "not integration"
            exit 1
          fi

      - name: Verify e2e tests have markers
        run: |
          # All tests in tests/e2e/ MUST have @pytest.mark.e2e
          UNMARKED=$(PYTHONPATH=. pytest --collect-only -q tests/e2e/ -m "not e2e" 2>/dev/null | grep -c "::" || true)
          if [ "$UNMARKED" -gt 0 ]; then
            echo "ERROR: $UNMARKED e2e tests missing @pytest.mark.e2e marker"
            exit 1
          fi

      - name: Verify P0 tests have markers
        run: |
          # All P0 tests MUST have @pytest.mark.p0 for explicit tracking
          # Derive P0 test paths from p0_modules.json (single source of truth)
          P0_DIRS=$(python -c "
          import json
          modules = json.load(open('scripts/testing/p0_modules.json'))['p0_modules']
          dirs = set()
          for m in modules:
              # Convert file path to test directory: libs/x/y.py -> tests/libs/x/
              parts = m.replace('.py', '').split('/')
              if len(parts) >= 2:
                  dirs.add('tests/' + '/'.join(parts[:-1]) + '/')
          print(' '.join(sorted(dirs)))
          ")
          P0_UNMARKED=$(PYTHONPATH=. pytest --collect-only -q $P0_DIRS -m "not p0" 2>/dev/null | grep -c "::" || true)
          if [ "$P0_UNMARKED" -gt 0 ]; then
            echo "ERROR: $P0_UNMARKED tests in P0 paths missing @pytest.mark.p0 marker"
            PYTHONPATH=. pytest --collect-only -q $P0_DIRS -m "not p0" | head -20
            # NOTE: Advisory warning during Phase 0-1 (Week 1)
            # Change to `exit 1` in Phase 2 after P0 tests are fully marked
            echo "::warning::P0 marker enforcement is advisory until end of Phase 1"
          fi
```

**pytest.ini marker registration:**
```ini
[pytest]
markers =
    integration: marks test as integration test (deselected from unit shards)
    e2e: marks test as end-to-end test (deselected from unit shards)
    p0: marks test as P0 critical (cannot be quarantined without Tech Lead approval)
    slow: marks test as slow (optional: use for shard balancing)
```

#### P0 Deselect Guard Job

**CRITICAL:** Ensure P0 tests are NEVER deselected or skipped by any CI configuration.

```yaml
  p0-guard:
    runs-on: ubuntu-latest
    steps:
      - name: Verify P0 tests not deselected
        run: |
          # Load P0 module paths
          P0_PATHS=$(python -c "import json; print(' '.join(json.load(open('scripts/testing/p0_modules.json'))['p0_modules']))")

          # Check quarantine.txt doesn't contain P0 tests (except temporary with approval)
          # Derive P0 TEST paths (tests/...) from module paths (libs/...)
          if [ -f tests/quarantine.txt ]; then
            P0_TEST_DIRS=$(python -c "
            import json
            modules = json.load(open('scripts/testing/p0_modules.json'))['p0_modules']
            dirs = set()
            for m in modules:
                parts = m.replace('.py', '').split('/')
                if len(parts) >= 2:
                    dirs.add('tests/' + '/'.join(parts[:-1]) + '/')
            print(' '.join(sorted(dirs)))
            ")
            for p0_test_dir in $P0_TEST_DIRS; do
              if grep -q "$p0_test_dir" tests/quarantine.txt; then
                echo "ERROR: P0 test quarantined without Tech Lead approval in: $p0_test_dir"
                echo "Quarantined P0 tests:"
                grep "$p0_test_dir" tests/quarantine.txt
                exit 1
              fi
            done
          fi

          # Verify P0 tests exist in at least one shard
          # Derive P0 test paths from p0_modules.json (single source of truth)
          P0_DIRS=$(python -c "
          import json
          modules = json.load(open('scripts/testing/p0_modules.json'))['p0_modules']
          dirs = set()
          for m in modules:
              parts = m.replace('.py', '').split('/')
              if len(parts) >= 2:
                  dirs.add('tests/' + '/'.join(parts[:-1]) + '/')
          print(' '.join(sorted(dirs)))
          ")
          PYTHONPATH=. pytest --collect-only -q $P0_DIRS > /tmp/p0_tests.txt
          if [ ! -s /tmp/p0_tests.txt ]; then
            echo "ERROR: No P0 tests collected. Check test paths."
            exit 1
          fi

          echo "P0 guard passed: $(wc -l < /tmp/p0_tests.txt) P0 tests verified"
```

#### Shard Validation Job

Add a validation job to ensure all tests are covered with no duplicates.

**CRITICAL:** Validation MUST use the same selection criteria as actual test runs.

```yaml
  validate-shards:
    runs-on: ubuntu-latest
    steps:
      - name: Collect all UNIT tests (matching actual shard selection)
        run: |
          # IMPORTANT: Use same markers and deselects as unit test shards
          DESELECT_ARGS=""
          if [ -f tests/quarantine.txt ]; then
            DESELECT_ARGS=$(grep -v '^#' tests/quarantine.txt | cut -d'|' -f1 | xargs -I{} echo "--deselect {}" | tr '\n' ' ')
          fi
          PYTHONPATH=. pytest --collect-only -q \
            -m "not integration and not e2e" \
            $DESELECT_ARGS \
            tests/ > all_tests.txt
      - name: Verify shard coverage (with duplicate detection)
        run: python scripts/testing/verify_shard_coverage.py
```

**verify_shard_coverage.py requirements:**
```python
# scripts/testing/verify_shard_coverage.py
"""
Validates that:
1. Every test is assigned to exactly one shard
2. No test is missing from all shards
3. No test appears in multiple shards (DUPLICATE DETECTION)

IMPORTANT: pytest nodeids format is 'tests/path/test_file.py::TestClass::test_method'
We extract the file path and match against shard definitions using fnmatch for glob support.
"""
import fnmatch
import sys
from pathlib import Path

SHARD_DEFINITIONS = {
    "libs-core": ["tests/libs/core/**"],
    "libs-platform": ["tests/libs/platform/**"],
    "libs-trading": ["tests/libs/trading/**", "tests/libs/data/**"],
    "apps-services": ["tests/apps/**"],
    "strategies": ["tests/strategies/**", "tests/research/**"],
    "root-and-misc": ["tests/test_*.py", "tests/regression/**", "tests/workflows/**",
                      "tests/fixtures/**", "tests/infra/**", "tests/load/**"],
}

def nodeid_to_filepath(nodeid: str) -> str:
    """Extract file path from pytest nodeid.

    'tests/libs/core/test_redis.py::TestRedis::test_connect' -> 'tests/libs/core/test_redis.py'
    """
    return nodeid.split("::")[0]

def matches_shard(filepath: str, shard_patterns: list[str]) -> bool:
    """Check if filepath matches any of the shard's glob patterns."""
    for pattern in shard_patterns:
        # Convert glob pattern to fnmatch pattern
        # 'tests/libs/core/**' matches any file under tests/libs/core/
        if pattern.endswith("**"):
            prefix = pattern[:-2]  # Remove '**'
            if filepath.startswith(prefix):
                return True
        elif fnmatch.fnmatch(filepath, pattern):
            return True
    return False

def main():
    # Load all discovered tests (pytest nodeids)
    all_tests = Path("all_tests.txt").read_text().strip().split("\n")
    all_tests = [t for t in all_tests if t.strip()]  # Filter empty lines

    # Map each test to its shard(s)
    test_to_shards: dict[str, list[str]] = {}
    for nodeid in all_tests:
        filepath = nodeid_to_filepath(nodeid)
        test_to_shards[nodeid] = []
        for shard, patterns in SHARD_DEFINITIONS.items():
            if matches_shard(filepath, patterns):
                test_to_shards[nodeid].append(shard)

    # Check for issues
    missing = [t for t, shards in test_to_shards.items() if len(shards) == 0]
    duplicates = [t for t, shards in test_to_shards.items() if len(shards) > 1]

    if missing:
        print(f"ERROR: {len(missing)} tests not assigned to any shard:")
        for t in missing[:10]:
            print(f"  - {t}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    if duplicates:
        print(f"ERROR: {len(duplicates)} tests assigned to multiple shards:")
        for t in duplicates[:10]:
            print(f"  - {t} -> {test_to_shards[t]}")

    if missing or duplicates:
        return 1

    print(f"OK: {len(all_tests)} tests validated, all in exactly one shard")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

#### Test Group Definitions (After Consolidation)

| Group Name | Test Paths (EXPLICIT) | Estimated Tests | Estimated Time |
|------------|----------------------|-----------------|----------------|
| `libs-core` | `tests/libs/core/` | ~800 | 2-3 min |
| `libs-platform` | `tests/libs/platform/` | ~500 | 2-3 min |
| `libs-trading` | `tests/libs/trading/`, `tests/libs/data/` | ~650 | 2-3 min |
| `apps-services` | `tests/apps/` | ~1050 | 3-4 min |
| `strategies` | `tests/strategies/`, `tests/research/` | ~400 | 2-3 min |
| `root-and-misc` | `tests/test_*.py`, `tests/regression/`, etc. | ~1350 | 3-4 min |

#### Coverage Aggregation

```bash
# Each shard runs:
COVERAGE_FILE=.coverage.$GROUP pytest --cov=libs --cov=apps --cov-branch ...

# Aggregation job:
coverage combine .coverage.*
coverage xml -o coverage.xml
coverage report --fail-under=$RATCHET_THRESHOLD  # See coverage ratchet
```

#### Expected Benefits
- **Current sequential time:** ~15-25 minutes
- **Expected parallel time:** ~8-12 minutes (40-50% reduction)

#### Test Isolation Strategy for pytest-xdist

**CRITICAL:** Parallel tests must not share mutable state. Each worker needs isolated resources.

**Database Isolation:**
```python
# tests/conftest.py - Per-worker database schema
@pytest.fixture(scope="session")
def db_schema(worker_id):
    """Create isolated DB schema per xdist worker."""
    schema_name = f"test_{worker_id}" if worker_id != "master" else "test_main"
    # Create schema, yield connection, drop schema on teardown
    ...

@pytest.fixture(autouse=True)
def db_transaction(db_connection):
    """Wrap each test in a transaction and rollback."""
    with db_connection.begin() as txn:
        yield
        txn.rollback()
```

**Redis Isolation:**
```python
# tests/conftest.py - Per-worker Redis key namespace
@pytest.fixture(scope="session")
def redis_prefix(worker_id):
    """Unique Redis key prefix per xdist worker."""
    return f"test:{worker_id}:" if worker_id != "master" else "test:main:"

@pytest.fixture(autouse=True)
def clean_redis_keys(redis_client, redis_prefix):
    """Clean up Redis keys after each test."""
    yield
    for key in redis_client.scan_iter(f"{redis_prefix}*"):
        redis_client.delete(key)
```

**Time Isolation:**
```python
# Use freezegun or time-machine for deterministic time
@pytest.fixture
def frozen_time():
    """Freeze time for deterministic tests."""
    with freeze_time("2026-01-15 09:30:00", tz_offset=-5):  # Market open
        yield
```

**Resource Cleanup Hooks:**
```python
# tests/conftest.py - Global cleanup
def pytest_runtest_teardown(item, nextitem):
    """Cleanup hook after each test."""
    # Clear any global caches
    # Reset singleton state
    # Close any leaked connections
```

### Part 3: Coverage Improvement Strategy

#### Coverage Scope: Unit Tests Only

**EXPLICIT:** Coverage metrics are collected from **unit tests only**. Integration and E2E tests do NOT contribute to coverage.

**Rationale:**
1. **Predictability**: Integration tests have variable execution paths due to external dependencies (databases, APIs), leading to flaky coverage numbers
2. **Focus**: Unit tests are designed to cover all code paths; integration tests verify system behavior
3. **Speed**: Coverage collection adds overhead; keeping it unit-test-only keeps CI fast
4. **Accuracy**: Unit test coverage directly measures code quality; integration test coverage may give false confidence

**CI Implementation:**
- Unit test shards: Run with `--cov=libs --cov=apps --cov-branch`, upload coverage artifacts
- Integration tests: Run **without** coverage flags (`--no-cov`), do not upload coverage artifacts
- Coverage aggregation: Only combines unit test coverage files (`.coverage.libs-*`, `.coverage.apps-*`, etc.)

#### Mandatory Integration Tests for Trading Safety

**CRITICAL:** While coverage is unit-test-only, the following integration tests are MANDATORY for trading safety. These tests verify cross-service behavior that unit tests cannot catch.

**Required Integration Tests:**

| Test Category | Required Tests | Location |
|--------------|----------------|----------|
| **Idempotency** | Duplicate order submission returns same result | `tests/integration/test_order_idempotency.py` |
| **Order De-duplication** | Same `client_order_id` doesn't create duplicate orders | `tests/integration/test_order_deduplication.py` |
| **Circuit Breaker Cross-Service** | Breaker trip propagates to all services | `tests/integration/test_circuit_breaker_propagation.py` |
| **Position Reconciliation** | Broker vs DB position sync | `tests/integration/test_position_reconciliation.py` |
| **Ledger/P&L Integrity** | P&L deltas match position changes, double-entry consistent | `tests/integration/test_ledger_integrity.py` |
| **Kill Switch Enforcement** | No orders sent when TRIPPED (HTTP + background) | `tests/integration/test_kill_switch_enforcement.py` |
| **Market Data Staleness** | Stale data propagates to breaker and risk manager | `tests/integration/test_stale_data_propagation.py` |

**Integration Test Requirements:**
1. Must run after unit tests pass (dependency gate)
2. Must use real **internal** service boundaries (HTTP/gRPC between repo services)
3. **External** broker APIs must be mocked (see Broker Mock Strategy below)
4. Must cover retry/failure scenarios

**Clarification: Real vs. Mocked:**
- **REAL:** Postgres, Redis, internal microservices (execution_gateway, risk_manager, etc.)
- **MOCKED:** External APIs (Alpaca, market data providers), third-party webhooks

**Contract Tests (Alternative):**
If full integration tests are impractical, implement contract tests:
- Consumer-driven contracts for API boundaries
- Pact or similar framework for cross-service contracts

#### Coverage Quality Over Quantity

**CRITICAL:** Focus on **Branch Coverage**, not just Line Coverage.

Line coverage can be "gamed" with superficial tests. Branch coverage ensures all logic paths are tested.

#### Coverage Ratchet Strategy

Instead of hard 85% gate that blocks all PRs, use incremental ratcheting:

**Ratchet Rules:**
1. PR cannot decrease coverage below current baseline
2. When PR increases coverage, baseline is updated via PR
3. P0 modules have separate, stricter ratchets
4. Allows incremental progress without blocking unrelated PRs

#### Coverage Ratchet Persistence & Governance

**Storage:** Baselines are stored in the repository at `scripts/testing/coverage_baselines.json`

```json
// scripts/testing/coverage_baselines.json
// NOTE: Module paths finalized during Phase 0 audit
{
  "version": 1,
  "last_updated": "2026-01-15",
  "updated_by": "PR #123",
  "overall": 19,
  "modules": {
    "libs/trading/risk_management/breaker.py": 30,
    "libs/trading/risk_management/checker.py": 28,
    "libs/trading/risk_management/kill_switch.py": 22,
    "libs/core/redis_client/client.py": 35,
    "apps/execution_gateway/main.py": 18,
    "apps/execution_gateway/alpaca_client.py": 15
  }
}
```

**Update Process:**
1. CI job runs `scripts/testing/check_coverage_ratchet.py`
2. If coverage improved, script outputs proposed baseline update
3. Developer creates separate PR to update `coverage_baselines.json`
4. Baseline update PR requires review from Tech Lead or designated owner
5. Baseline PR description must include: coverage diff, modules affected, reason

**Governance:**
- Baseline updates are NEVER automatic (requires explicit PR)
- Baseline decreases require Tech Lead approval with documented justification
- Quarterly audit of baseline file to ensure accuracy

#### P0/P1 Module Registry

**Single Source of Truth:** Module priority lists are stored in `scripts/testing/p0_modules.json`

```json
// scripts/testing/p0_modules.json
// NOTE: This file will be populated during Phase 0 audit with verified file paths
{
  "version": 1,
  "last_updated": "2026-01-15",
  "note": "Exact paths finalized during Phase 0 audit",
  "p0_modules": [
    // Risk Management - verified files
    "libs/trading/risk_management/breaker.py",
    "libs/trading/risk_management/checker.py",
    "libs/trading/risk_management/kill_switch.py",
    // Core Infrastructure - verified files
    "libs/core/redis_client/client.py",
    "libs/core/redis_client/feature_cache.py",
    // Execution Gateway - verified files
    "apps/execution_gateway/main.py",
    "apps/execution_gateway/alpaca_client.py",
    "apps/execution_gateway/reconciliation.py"
    // Additional P0 files to be identified during Phase 0 audit
  ],
  "p1_modules": [
    "libs/platform/",
    "apps/orchestrator/"
  ],
  "p0_target_coverage": 95,
  "p1_target_coverage": 90
}
```

**P0 Selection Rationale:**
P0 includes services in the critical trading pipeline:
1. **Execution Gateway** - Order entry/exit point
2. **libs/trading/risk_management** - Circuit breakers, risk limits (pre-trade checks)
3. **Market Data Service** - Data freshness (breaker trigger)
4. **Signal Service** - Trading signals
5. **libs/core** - Redis client, health checks (infrastructure)

**Usage in CI:**
- Coverage ratchet script loads `p0_modules.json` to determine stricter thresholds
- Updates to this file require Tech Lead approval
- Adding a module to P0 requires corresponding test plan

**P0 Module Change Governance:**
When adding NEW modules to `p0_modules.json`:

1. **PR Requirements:**
   - [ ] Tech Lead approval required
   - [ ] Test plan for new P0 module documented
   - [ ] Baseline coverage measured and added to `coverage_baselines.json`
   - [ ] Integration tests identified and implemented

2. **CI Validation:**
```yaml
- name: Validate P0 module changes
  run: |
    # Check if p0_modules.json changed
    if git diff HEAD~1 --name-only | grep -q "p0_modules.json"; then
      # Verify all new P0 modules have baseline coverage
      NEW_MODULES=$(git diff HEAD~1 scripts/testing/p0_modules.json | grep "^+" | grep -v "^+++" | grep -oE '"[^"]+"' || true)
      for mod in $NEW_MODULES; do
        if ! grep -q "$mod" scripts/testing/coverage_baselines.json; then
          echo "ERROR: New P0 module $mod missing from coverage_baselines.json"
          exit 1
        fi
      done
    fi
```

3. **Quarantine Rule:**
   - New P0 modules automatically inherit quarantine restrictions
   - Cannot be quarantined without explicit Tech Lead approval note in PR

#### Branch Coverage Measurement Configuration

**coverage.py configuration** (in `pyproject.toml`):

```toml
[tool.coverage.run]
branch = true
relative_files = true
source = ["libs", "apps"]
omit = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/migrations/*",
    # Generated files (governance: add new entries via PR with Tech Lead approval)
    "*/generated/*",
    "*_pb2.py",        # Protobuf generated
    "*_pb2_grpc.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "@abstractmethod",
    "if __name__ == .__main__.:",
]
fail_under = 0  # Ratchet script handles thresholds
# Exclude trivial files from report (low impact)
omit = [
    "*/__init__.py",
    "*/conftest.py",
]

[tool.coverage.html]
directory = "htmlcov"
```

**Coverage Config Governance:**
- All CI jobs MUST set `COVERAGE_RCFILE=pyproject.toml` to ensure consistent config
- Coverage files are isolated per job: `COVERAGE_FILE=.coverage.$SHARD_NAME`
- Only expected artifacts are combined in aggregation job

**CI Environment Variables:**
```yaml
env:
  COVERAGE_RCFILE: pyproject.toml
  COVERAGE_FILE: .coverage.${{ matrix.test-group.name }}
```

**Exclusion List Governance:**
Adding to `omit` list requires:
1. PR with justification
2. Tech Lead approval
3. Entry in `coverage_exclusion_log.md` with reason and date

**Per-Module Branch Coverage Enforcement:**

The `scripts/testing/check_coverage_ratchet.py` script:
1. Runs `coverage json --include=<module_path>` for each P0 module
2. Extracts `covered_branches / num_branches` percentage
3. Compares against baseline in `coverage_baselines.json`
4. Fails CI if any module drops below its baseline
5. Outputs per-module report for visibility

```bash
# Example CI output:
Module Coverage Report (Branch)
================================
libs/trading/risk_management/breaker.py:      32% (baseline: 30%) ✓
libs/trading/risk_management/checker.py:      26% (baseline: 28%) ✗ REGRESSION
libs/trading/risk_management/kill_switch.py:  24% (baseline: 22%) ✓
libs/core/redis_client/client.py:             37% (baseline: 35%) ✓
apps/execution_gateway/main.py:               20% (baseline: 18%) ✓

FAILED: libs/trading/risk_management/checker.py dropped below baseline (26% < 28%)
```

#### Phase 1: P0 Critical Service Coverage (PRIORITY)

**Target:** 90% branch coverage initially, ratchet to 95%

**Quality Rubric for P0 Tests:**
- [ ] Branch coverage ≥ 90% (ratchet to 95%)
- [ ] All error handling paths tested (negative-path tests)
- [ ] Concurrency tests for multi-threaded code
- [ ] Edge case tests (boundary values, empty inputs)
- [ ] Integration points tested with realistic mocks
- [ ] Time-dependent behaviors tested (see below)
- [ ] Decimal precision tests (see below)

**Decimal Precision Requirements:**
P0 modules handling financial calculations MUST use `Decimal`, not `float`:

```python
# WRONG - float precision loss
price = 100.10
qty = 0.001
total = price * qty  # May have floating point errors

# CORRECT - Decimal precision
from decimal import Decimal
price = Decimal("100.10")
qty = Decimal("0.001")
total = price * qty  # Exact: 0.10010

# Required tests for financial calculations:
def test_order_sizing_decimal_precision():
    """Verify no float precision loss in order sizing."""
    # Test with values known to cause float issues
    result = calculate_order_size(Decimal("0.1") + Decimal("0.2"))
    assert result == Decimal("0.3")  # float would fail this

def test_pnl_calculation_rounding():
    """Verify P&L rounding follows trading standards."""
    # 2 decimal places for USD, 8 for crypto
    pnl = calculate_pnl(...)
    assert pnl == pnl.quantize(Decimal("0.01"))
```

**Time-Dependent Coverage Targets:**
P0 modules MUST include tests for time-sensitive trading behaviors:

| Time Behavior | Required Tests | Example |
|--------------|----------------|---------|
| **Market hours** | Pre-market, market open, market close, after-hours | `test_order_rejected_after_hours` |
| **Timezone handling** | UTC conversions, DST transitions | `test_order_timestamp_dst_transition` |
| **Data staleness** | Stale quote detection (>30min) | `test_circuit_breaker_stale_data` |
| **Trading calendar** | Holidays, early close days | `test_no_orders_on_holiday` |
| **Order expiration** | Day orders expire at close | `test_day_order_expires_at_close` |

**Implementation:**
```python
# Use time-machine or freezegun for deterministic time tests
@pytest.mark.parametrize("market_time", [
    "2026-01-15 09:30:00-05:00",  # Market open
    "2026-01-15 16:00:00-05:00",  # Market close
    "2026-01-15 20:00:00-05:00",  # After hours
])
def test_order_behavior_by_time(market_time, frozen_time):
    ...
```

**P0 Services and Specific Critical Modules:**

1. **`libs/trading/risk_management/`** - Circuit breakers, risk limits
   - Verified files: `breaker.py`, `checker.py`, `kill_switch.py` - 95% branch coverage required
   - Mandatory: race condition tests, state machine tests, idempotency tests

2. **`libs/core/redis_client/`** - Redis client, caching
   - Verified files: `client.py`, `feature_cache.py` - 90% branch coverage required
   - Note: Some boilerplate (repr, config) can use `# pragma: no cover`

3. **`apps/execution_gateway/`** - Order API endpoints
   - Verified files: `main.py`, `alpaca_client.py`, `reconciliation.py` - 95% branch coverage required
   - Mandatory: authentication tests, error response tests, idempotency tests

4. **`apps/signal_service/`** - Signal generation
   - Audit during Phase 0 to identify critical files
   - Mandatory: model fallback tests, feature validation tests

5. **`apps/market_data_service/`** - Market data, staleness detection
   - Audit during Phase 0 to identify critical files
   - Mandatory: staleness detection tests

#### Phase 2: Coverage Foundation
Target: 70% overall branch coverage

1. Add tests for 0% coverage modules
2. Focus on error handling paths

#### Phase 3: P1 Services & Comprehensive Coverage
Target: 85% overall branch coverage, 95% for P1 services

1. `libs/platform/` - Alerts, web console auth
2. `apps/orchestrator/` - Workflow coordination

### Part 4: Bug Fix Policy During Coverage Expansion (TRADING-SPECIFIC)

**CRITICAL FOR TRADING SYSTEMS:** Bug fix policy is STRICTER than typical software.

#### Severity Definitions (Trading Context)

| Severity | Definition | Examples | Action |
|----------|------------|----------|--------|
| **P0-Critical** | Trading safety, financial correctness, security | Negative balance handling, order duplication, circuit breaker bypass | **FIX IMMEDIATELY** |
| **P1-High** | Incorrect behavior affecting core functionality | Wrong position calculation, signal generation errors | **FIX IMMEDIATELY** |
| **P2-Medium** | Non-financial edge cases, minor incorrect behavior | Logging errors, UI display issues | Fix in same PR if <1hr, else create issue with SLA |
| **P3-Low** | Code quality, style issues | Missing docstrings, suboptimal patterns | Create issue, can defer |

#### Financial Edge Cases are ALWAYS P0

The following are **P0-Critical**, regardless of how "edge case" they seem:
- Negative balance/position handling
- Division by zero in financial calculations
- Overflow/underflow in quantity/price calculations
- Currency/decimal precision errors
- Timezone-related order timing issues
- Race conditions in order submission

#### Bug Handling Process

1. **Discovery:** Test fails unexpectedly, revealing a bug in production code

2. **Immediate Triage:**
   - Is it in a P0/P1 service? → Likely P0/P1 bug
   - Does it involve money/positions/orders? → P0
   - Is it a "financial edge case"? → P0

3. **Action by Severity:**

   **P0/P1 (ANY correctness bug in P0/P1 services):**
   - STOP coverage work immediately
   - Fix the bug in a SEPARATE PR (keeps scope clean)
   - Full test suite + code review required
   - Document in incident log

   **P2:**
   - Create GitHub issue with `bug` label
   - If fix is <1 hour and low risk: fix in same PR
   - If fix is complex: separate PR with 48-hour SLA
   - Requires explicit sign-off from reviewer to defer

   **P3:**
   - Create GitHub issue with `bug` and `low-priority` labels
   - Can be deferred to future sprint
   - No SLA required

4. **Documentation Required:**

```markdown
## Bugs Found During Coverage Expansion

### [BUG-001] Circuit breaker race condition
- **Severity:** P0-Critical
- **File:** libs/trading/risk_management/breaker.py:123
- **Description:** Race condition when multiple threads trip breaker simultaneously
- **Fix PR:** #456 (separate PR, not mixed with coverage)
- **Tests Added:** test_breaker_concurrent_trip.py
- **Root Cause:** Missing atomic compare-and-set operation
```

#### Deferral Sign-Off Requirements

To defer a P2 bug, the following is required:
1. GitHub issue created with full description
2. Explicit comment from code reviewer approving deferral
3. SLA assigned (48 hours for P2)
4. Risk assessment documented

**Template:**
```markdown
**Deferral Request for BUG-XXX**
- Severity: P2-Medium
- Reason for deferral: [explanation]
- Risk if deferred: [assessment]
- SLA: 48 hours
- Reviewer approval: @reviewer-name
```

### Part 5: Local CI (`make ci-local`) Updates

```makefile
ci-local:
    # Existing sequential checks
    @./scripts/dev/validate_doc_index.sh
    @poetry run python scripts/dev/check_doc_freshness.py
    @poetry run python scripts/dev/generate_architecture.py --check
    @poetry run mypy libs/ apps/ strategies/ --strict
    @poetry run ruff check .
    @poetry run python scripts/dev/check_layering.py

    # Parallel test execution with pytest-xdist
    # Exclude integration/e2e tests (shared DB issue)
    # Use branch coverage for quality
    @PYTHONPATH=. poetry run pytest \
        -m "not integration and not e2e" \
        -n auto \
        --cov=libs --cov=apps \
        --cov-branch \
        --cov-report=term-missing \
        --cov-fail-under=$(shell python scripts/testing/get_ratchet_threshold.py)
```

### Part 6: Flake Management

#### Flake Quarantine System

Parallel tests (`pytest-xdist`) can introduce flakiness due to shared state.

**Quarantine Process:**
1. When a test flakes, add to `tests/quarantine.txt` with expiration date
2. Quarantined tests are EXCLUDED from main shards (see exclusion mechanism below)
3. Quarantined tests run in separate, sequential job
4. Owner has 1 week to fix or test is permanently skipped with `@pytest.mark.skip`
5. CI enforces expiration - expired quarantine entries fail the build

**P0 Test Quarantine Rules (STRICTER):**
- **P0 tests CANNOT be permanently skipped** - must be fixed or replaced
- P0 test quarantine requires Tech Lead approval
- P0 tests continue running in dedicated nightly job even while quarantined
- Maximum 2 renewals (3 weeks total), then escalation to incident review
- If P0 test is fundamentally flaky, must be rewritten, not skipped

**Quarantine File Format:**
```
# tests/quarantine.txt
# Format: test_path | added_date | expiration_date | owner | severity | issue_url | reason
tests/libs/core/test_redis_client.py::test_concurrent_publish | 2026-01-15 | 2026-01-22 | @developer | P2 | https://github.com/org/repo/issues/123 | State pollution with xdist
```

**Required Fields:**
- `test_path` - Full pytest nodeid
- `added_date` - ISO date (YYYY-MM-DD)
- `expiration_date` - ISO date, max 1 week from added_date
- `owner` - GitHub handle with @ prefix
- `severity` - P0/P1/P2/P3 (P0 requires Tech Lead approval)
- `issue_url` - REQUIRED: Link to GitHub issue tracking the flake
- `reason` - Brief description

**CI Validation:**
```yaml
- name: Validate quarantine format
  run: |
    if [ -f tests/quarantine.txt ]; then
      # Check all entries have 7 fields and valid issue URL
      while IFS='|' read -r path date exp owner sev issue reason; do
        if [[ ! "$issue" =~ ^https://github.com/.*/issues/[0-9]+$ ]]; then
          echo "ERROR: Missing or invalid issue URL for: $path"
          exit 1
        fi
      done < <(grep -v '^#' tests/quarantine.txt)
    fi
```

#### Quarantine Exclusion Mechanism

**Main shard exclusion:** Quarantined tests are excluded from parallel runs using pytest's `--deselect` option, dynamically generated from `tests/quarantine.txt`.

```yaml
# In CI workflow - each shard job
- name: Run tests (excluding quarantined)
  run: |
    DESELECT_ARGS=""
    if [ -f tests/quarantine.txt ]; then
      DESELECT_ARGS=$(grep -v '^#' tests/quarantine.txt | cut -d'|' -f1 | xargs -I{} echo "--deselect {}" | tr '\n' ' ')
    fi
    pytest ${{ matrix.test-group.paths }} $DESELECT_ARGS --cov=libs --cov=apps --cov-branch
```

**Local CI (Makefile):**
```makefile
ci-local:
    # ... other checks ...
    # Exclude quarantined tests from parallel run
    @DESELECT=$$(grep -v '^#' tests/quarantine.txt 2>/dev/null | cut -d'|' -f1 | xargs -I{} echo "--deselect {}" | tr '\n' ' '); \
    PYTHONPATH=. poetry run pytest -m "not integration and not e2e" -n auto \
        $$DESELECT --cov=libs --cov=apps --cov-branch ...
```

#### Quarantine Expiration Enforcement

**CI job to enforce expiration:**

```yaml
  check-quarantine-expiration:
    runs-on: ubuntu-latest
    steps:
      - name: Check for expired quarantine entries
        run: |
          python scripts/testing/check_quarantine_expiration.py
```

**Script logic (`scripts/testing/check_quarantine_expiration.py`):**

```python
#!/usr/bin/env python3
"""
Check quarantine.txt for expired entries.
Fails CI if any entry is past its expiration date without renewal.
"""
import sys
from datetime import datetime
from pathlib import Path

QUARANTINE_FILE = Path("tests/quarantine.txt")
TODAY = datetime.now().date()

def main():
    if not QUARANTINE_FILE.exists():
        print("No quarantine file found. OK.")
        return 0

    expired = []
    with open(QUARANTINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 3:
                continue
            test_path, added_date, expiration_date = parts[0], parts[1], parts[2]
            exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
            if exp_date < TODAY:
                expired.append((test_path, expiration_date))

    if expired:
        print("ERROR: Expired quarantine entries found!")
        print("Either fix the flaky test, renew with justification, or permanently skip.")
        print()
        for test, exp in expired:
            print(f"  EXPIRED ({exp}): {test}")
        return 1

    print(f"Quarantine check passed. {len(list(QUARANTINE_FILE.open()))} entries, none expired.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

**Renewal Process:**
To extend a quarantine entry beyond expiration:
1. Update expiration date in `tests/quarantine.txt`
2. Add justification comment above the entry
3. Maximum 2 renewals (3 weeks total), then must be fixed or permanently skipped

**Flaky Test Triage Template:**
When adding a test to quarantine, create a GitHub issue with this template:

```markdown
## Flaky Test Report

**Test:** `tests/path/test_file.py::TestClass::test_method`
**First Observed:** 2026-01-15
**Worker ID:** gw3 (if applicable)
**Quarantine Expiration:** 2026-01-22

### Failure Details
- **Error Type:** (AssertionError, TimeoutError, ConnectionError, etc.)
- **Error Message:** [paste exact error]
- **Stack Trace:** [paste relevant portion]

### Reproducibility
- [ ] Fails consistently in CI
- [ ] Fails only with xdist (-n auto)
- [ ] Fails only on specific worker
- [ ] Fails intermittently (X/Y runs)

### Suspected Root Cause
- [ ] Shared state / fixture pollution
- [ ] Race condition / timing issue
- [ ] External dependency (network, DB, Redis)
- [ ] Resource exhaustion (memory, file handles)
- [ ] Time-dependent (frozen time, timezone)
- [ ] Unknown

### Logs/Artifacts
[Attach relevant CI logs, screenshots, or artifacts]

### Retries Attempted
- Number of retries before quarantine: X
- Using pytest-rerunfailures: Yes/No

### Owner
@username - assigned to investigate and fix
```

**Retry Policy:**
- `pytest-rerunfailures` is FORBIDDEN for P0 tests (masks real failures)
- Non-P0 tests may use max 2 retries with `--reruns 2 --reruns-delay 1`
- All retried failures must still be logged for trend analysis

**CI Job for Quarantined Tests:**
```yaml
  quarantine-tests:
    runs-on: ubuntu-latest
    steps:
      - name: Run quarantined tests sequentially
        run: |
          if [ -f tests/quarantine.txt ]; then
            pytest $(cat tests/quarantine.txt | cut -d'|' -f1) --no-cov
          fi
```

---

## Implementation Phases

### Resourcing Assumptions

**Team Allocation:**
- 1-2 engineers dedicated to test infrastructure (Phases 0-1)
- 2-3 engineers for coverage expansion (Phases 2-3)
- Estimated velocity: 10-15 test files per engineer per week

**Fallback Timeline:**
If coverage plateaus before 85%:
1. At 4 weeks: Reassess scope, prioritize P0 modules only to 95%
2. At 6 weeks: Accept 70% overall if P0 modules meet 95% target
3. Document remaining coverage debt with SLA for future sprints

**Day 3-5 Early Warning Check:**
Quick sanity check before week 2 formal checkpoint:

| Day | Check | Pass Criteria | Action if Failed |
|-----|-------|---------------|------------------|
| Day 3 | Consolidation progress | ≥50% of collocated tests migrated | Escalate blockers, add resources |
| Day 5 | CI parallel prototype | At least one shard running in CI | Debug CI config, extend Phase 1 |
| Day 5 | Coverage baseline | Baseline measurements complete | Prioritize measurement |

**Week 2 Feasibility Checkpoint:**
At end of week 2, conduct formal re-estimation:

| Checkpoint Metric | Pass Criteria | Action if Failed |
|------------------|---------------|------------------|
| Phase 0 complete | All collocated tests migrated, CI green | Extend Phase 0, defer Phase 2 |
| Phase 1 complete | Parallel CI operational, shards validated | Debug CI issues, defer coverage work |
| P0 coverage progress | ≥50% branch coverage on P0 modules | Adjust 6-week target, add resources |
| Test velocity | ≥10 test files added per engineer per week | Re-evaluate complexity, adjust timeline |
| Bug discovery rate | <5 P0/P1 bugs discovered | Continue as planned |
| Bug discovery rate | ≥5 P0/P1 bugs discovered | Pause coverage, prioritize bug fixes |

**Checkpoint Meeting Agenda:**
1. Review metrics vs. targets
2. Identify blockers and risks
3. Decide: continue, adjust targets, or escalate for resources
4. Update plan document with revised timeline if needed
5. Sign-off: Tech Lead + Engineering Manager

**Staged Coverage Milestones:**

| Week | P0 Branch Coverage | Overall Branch Coverage | Key Deliverable |
|------|-------------------|------------------------|-----------------|
| Week 1 | Baseline measured | Baseline (~19%) | Consolidation + parallel CI |
| Week 2 | 50% | 35% | P0 core coverage + checkpoint |
| Week 3 | 70% | 50% | P0 edge cases + concurrency |
| Week 4 | 85% | 65% | P0 time-dependent tests |
| Week 5 | 90% | 75% | P1 services + remaining modules |
| Week 6 | 95% | 85% | Final polish + documentation |

**Minimum Viable Coverage (if schedule slips):**
If 85% overall is not achievable by week 6:
- **Must have:** All P0 modules at 95% branch coverage
- **Should have:** 70% overall coverage
- **Nice to have:** P1 modules at 90% coverage
- Document remaining debt with SLA for follow-up sprint

### Phase 0: Test Consolidation (Week 1 - First Half)

**Execute in small batches with CI after each:**

1. **Batch 1: Audit and conftest.py inventory**
   - List all 37 collocated test files (includes libs/redis_client/tests/)
   - List all conftest.py files in collocated directories
   - Document fixture dependencies
   - Run CI

2. **Batch 2: Migrate conftest.py files**
   - Copy each conftest.py to corresponding `tests/` subtree (MERGE if destination exists)
   - Update imports from relative to absolute
   - Run CI

3. **Batch 3: Migrate unique tests (non-duplicates)**
   - Move 24 unique test files to `tests/`
   - Run CI

4. **Batch 4: Resolve duplicate tests**
   - Investigate 12 duplicate pairs
   - Document resolution decisions
   - Merge or delete as appropriate
   - Run CI

5. **Batch 5: Cleanup**
   - Remove empty collocated test directories
   - Update pytest.ini testpaths
   - Update CONTRIBUTING.md with test location guide
   - Run CI

**Exit Criteria (Phase 0):**
- [ ] All 37 collocated test files moved to `tests/`
- [ ] All collocated `*/tests/` directories deleted
- [ ] pytest.ini testpaths simplified to just `tests`
- [ ] CI passes with all migrated tests
- [ ] Migration summary document created with duplicate resolution decisions
- **Sign-off:** Tech Lead

### Phase 1: CI Parallelization (Week 1 - Second Half)
1. Clean up pytest config (resolve pytest.ini vs pyproject.toml duplication)
2. Create `.github/workflows/ci-tests-parallel.yml`
3. Add shard validation job (with duplicate detection)
4. Implement coverage aggregation with ratchet
5. Add pytest-xdist to dependencies
6. Update Makefile for local parallel execution
7. Run 3 CI cycles to collect runtime data

**Exit Criteria (Phase 1):**
- [ ] Parallel CI workflow deployed and passing
- [ ] Shard validation confirms 100% test coverage with no duplicates
- [ ] Coverage ratchet script operational
- [ ] `make ci-local` uses pytest-xdist (target: <5 min, measure baseline first)
- [ ] Runtime data collected from 3 CI runs
- **Sign-off:** Tech Lead

### Phase 2: P0 Critical Coverage (Week 2-4)
1. Generate coverage report per P0 module (using `p0_modules.json`)
2. Identify branch coverage gaps
3. Add comprehensive tests following quality rubric:
   - Branch coverage tests
   - Negative-path tests
   - Concurrency tests
   - Time-dependent tests (market hours, timezone, staleness)
4. **Fix ALL bugs discovered (P0/P1 in separate PRs)**
5. Target: P0 services at 90% branch coverage, ratchet to 95%

**Exit Criteria (Phase 2):**
- [ ] All P0 modules listed in `p0_modules.json` at ≥90% branch coverage
- [ ] All P0 tests include idempotency and concurrency tests
- [ ] All discovered P0/P1 bugs fixed with separate PRs
- [ ] Coverage ratchet baseline updated to reflect new coverage
- **Sign-off:** Tech Lead + QA Lead

### Phase 3: Coverage Foundation (Week 5-6)
1. Expand coverage to remaining modules
2. Add tests for 0% coverage modules
3. **Fix ALL bugs discovered**
4. Target: 85% overall branch coverage

**Exit Criteria (Phase 3):**
- [ ] Overall branch coverage ≥85% (or fallback target if plateau)
- [ ] All modules have >0% coverage
- [ ] All discovered bugs triaged and addressed per severity
- [ ] Coverage ratchet baseline finalized
- **Sign-off:** Tech Lead

### Phase 4: Optimization & Maintenance (Ongoing)
1. Rebalance shards based on runtime data
2. P1 service coverage (95%)
3. Coverage trend monitoring
4. Flake quarantine review (weekly)
5. Documentation updates

**Exit Criteria (Phase 4 - Quarterly):**
- [ ] Shards rebalanced within 20% runtime variance
- [ ] P1 modules at ≥90% branch coverage
- [ ] Flake quarantine list <10 tests
- [ ] Nightly full coverage run operational
- **Sign-off:** Tech Lead

---

## Files to Create/Modify

### New Files
1. `.github/workflows/ci-tests-parallel.yml` - Parallel CI workflow
2. `scripts/testing/aggregate_coverage.py` - Coverage aggregation
3. `scripts/testing/check_coverage_ratchet.py` - Coverage ratchet enforcement
4. `scripts/testing/coverage_baselines.json` - Coverage baseline storage
5. `scripts/testing/get_ratchet_threshold.py` - Get current threshold for local CI
6. `scripts/testing/verify_shard_coverage.py` - Validate all tests in shards
7. `scripts/testing/check_quarantine_expiration.py` - Quarantine expiration enforcement
8. `tests/quarantine.txt` - Flaky test quarantine list
9. `docs/STANDARDS/TEST_COVERAGE_GUIDE.md` - Guidelines

### Modified Files
1. `Makefile` - Add pytest-xdist, branch coverage
2. `pytest.ini` - Clean up, simplify testpaths to just `tests`
3. `pyproject.toml` - Add pytest-xdist, remove duplicate pytest config
4. `CONTRIBUTING.md` - Add test location and ownership guide

### Required Dependency Updates (pyproject.toml)

**Dev Dependencies to Add:**
```toml
[tool.poetry.dev-dependencies]
pytest-xdist = "^3.5"           # Parallel test execution
pytest-socket = "^0.7"          # Network blocking enforcement
pytest-cov = "^4.1"             # Coverage (likely exists, verify version)
freezegun = "^1.2"              # Time freezing for deterministic tests
respx = "^0.21"                 # HTTP mocking for httpx clients
pytest-timeout = "^2.2"         # Timeout enforcement (optional but recommended)
```

**Approval:** Tech Lead must review and approve dependency additions before implementation.

### Deleted Directories (after migration)
1. `libs/redis_client/tests/`
2. `libs/core/redis_client/tests/`
3. `libs/trading/risk_management/tests/`
4. `apps/execution_gateway/tests/`
5. `apps/signal_service/tests/`
6. `apps/market_data_service/tests/`
7. `apps/orchestrator/tests/`

---

## Success Criteria

| Metric | Current | Target | Timeline |
|--------|---------|--------|----------|
| Test locations | 2 | 1 | 1 week |
| P0 branch coverage | TBD | 90% → 95% | 4 weeks |
| Overall branch coverage | ~15% | 85% | 6 weeks |
| CI time (remote) | ~20 min | ~10 min | 1 week |
| CI time (local) | ~5 min | ~3 min | 1 week |
| P0/P1 bugs found & fixed | N/A | 100% | Ongoing |
| Flaky tests quarantined | N/A | <10 | Ongoing |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Test consolidation breaks tests | High | Small batches with CI after each |
| Duplicate tests have different assertions | High | INVESTIGATE discrepancies, don't merge blindly |
| conftest.py migration breaks fixtures | High | Test after each conftest.py move |
| Bug fixes introduce regressions | High | Separate PRs, full test suite, code review |
| Test flakiness in parallel | High | Flake quarantine system with expiration |
| Coverage threshold blocks all PRs | High | Coverage ratchet (incremental, not hard gate) |
| Shard imbalance | Medium | Runtime-based rebalancing after initial runs |
| State pollution with xdist | Medium | Fixture isolation, unique resource names |

---

## Appendix A: Collocated Test Inventory (37 files)

### libs/redis_client/tests/ (needs audit)
- **NOTE:** `libs/redis_client/` exists separately from `libs/core/redis_client/`. Audit both and consolidate.

### libs/core/redis_client/tests/ (1 file)
- test_fallback_buffer.py

### libs/trading/risk_management/tests/ (7 files)
- test_kill_switch_fail_closed.py
- test_kill_switch.py
- test_kill_switch_race_conditions.py
- test_breaker.py ⚠️ DUPLICATE - INVESTIGATE
- test_config.py ⚠️ DUPLICATE - INVESTIGATE
- test_checker.py ⚠️ DUPLICATE - INVESTIGATE
- test_position_reservation.py

### apps/execution_gateway/tests/ (15 files)
- test_slice_scheduler.py
- test_twap_idempotency.py
- test_order_id_generator.py
- test_alpaca_client.py
- test_fat_finger_validator.py
- test_alpaca_client_comprehensive.py
- test_metrics.py ⚠️ DUPLICATE - INVESTIGATE
- test_order_id_generator_comprehensive.py
- test_order_slicer.py
- test_realtime_pnl.py
- test_liquidity_service.py
- test_slice_endpoint.py
- test_metrics_integration.py
- test_webhook_security.py
- test_webhook_security_comprehensive.py

### apps/signal_service/tests/ (6 files)
- test_model_registry.py
- test_metrics.py ⚠️ DUPLICATE - INVESTIGATE
- test_feature_parity.py
- test_signal_generator.py
- test_integration.py ⚠️ DUPLICATE - INVESTIGATE
- test_shadow_validator.py

### apps/market_data_service/tests/ (5 files)
- test_alpaca_stream.py
- test_metrics.py ⚠️ DUPLICATE - INVESTIGATE
- test_position_sync_comprehensive.py
- test_types.py ⚠️ DUPLICATE - INVESTIGATE
- test_position_sync.py

### apps/orchestrator/tests/ (2 files)
- test_metrics.py ⚠️ DUPLICATE - INVESTIGATE
- test_position_sizing.py

---

## Appendix B: Coverage Ratchet Script Implementation

```python
# scripts/testing/check_coverage_ratchet.py
"""
Coverage ratchet prevents coverage regression while allowing incremental progress.
Baselines are stored in scripts/testing/coverage_baselines.json (see Part 3).
"""
import json
import subprocess
import sys
from pathlib import Path

BASELINES_FILE = Path(__file__).parent / "coverage_baselines.json"
P0_MODULES_FILE = Path(__file__).parent / "p0_modules.json"
MIN_BRANCH_COUNT = 5  # Modules with fewer branches are not counted (trivial files)
REPO_ROOT = Path(__file__).parent.parent.parent  # scripts/testing/ -> repo root

# Target coverage goals (informational, baselines ratchet towards these)
TARGET_COVERAGE = {
    "overall": 85,
    "P0_modules": 95,  # libs/trading/*, apps/execution_gateway/*, apps/signal_service/*
    "P1_modules": 90,  # libs/platform/*, apps/orchestrator/*
}

def normalize_path(filepath: str) -> str:
    """Normalize coverage paths to repo-relative format.

    coverage.json may report absolute paths; baselines use repo-relative.
    This ensures consistent matching.
    """
    p = Path(filepath)
    if p.is_absolute():
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:
            return filepath  # Not under repo root, return as-is
    # Normalize separators and remove leading ./
    return str(Path(filepath)).lstrip("./")

def load_baselines() -> dict:
    """Load baselines from JSON file."""
    if not BASELINES_FILE.exists():
        print(f"ERROR: Baselines file not found: {BASELINES_FILE}")
        sys.exit(1)
    with open(BASELINES_FILE) as f:
        return json.load(f)

def get_current_coverage() -> dict:
    """Parse coverage.json to get current branch coverage per module."""
    result = subprocess.run(
        ["coverage", "json", "-o", "-"],
        capture_output=True, text=True
    )

    # Error handling for missing/empty coverage data
    if result.returncode != 0:
        print(f"ERROR: coverage json failed: {result.stderr}")
        sys.exit(1)

    if not result.stdout.strip():
        print("ERROR: coverage.json output is empty. Did tests run with --cov?")
        sys.exit(1)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON from coverage: {e}")
        sys.exit(1)

    # Validate required keys exist
    if "totals" not in data or "files" not in data:
        print("ERROR: coverage.json missing 'totals' or 'files' keys")
        sys.exit(1)

    totals = data["totals"]
    if totals.get("num_branches", 0) == 0:
        print("ERROR: No branches found in coverage data. Check --cov-branch flag.")
        sys.exit(1)

    coverage = {
        "overall": totals["covered_branches"] / totals["num_branches"] * 100,
        "_branch_counts": {}  # Track branch counts for minimum enforcement
    }

    for filepath, filedata in data["files"].items():
        normalized = normalize_path(filepath)  # Normalize to repo-relative path
        num_branches = filedata["summary"].get("num_branches", 0)
        if num_branches >= MIN_BRANCH_COUNT:  # Enforce minimum branch count
            coverage[normalized] = (
                filedata["summary"]["covered_branches"] / num_branches * 100
            )
            coverage["_branch_counts"][normalized] = num_branches

    return coverage

def check_ratchet() -> int:
    """Check coverage against baselines. Returns exit code."""
    baselines = load_baselines()
    current = get_current_coverage()

    failures = []
    warnings = []

    for module, baseline in baselines.get("modules", {}).items():
        if module not in current:
            # Module not in coverage - could be missing or too few branches
            if module in current.get("_branch_counts", {}):
                warnings.append(f"{module}: skipped (only {current['_branch_counts'][module]} branches)")
            else:
                warnings.append(f"{module}: not found in coverage data")
            continue

        actual = current.get(module, 0)
        if actual < baseline:
            failures.append(f"{module}: {actual:.1f}% < {baseline}% baseline")

    # Check overall
    if current["overall"] < baselines["overall"]:
        failures.append(f"overall: {current['overall']:.1f}% < {baselines['overall']}% baseline")

    # Print warnings
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  ⚠ {w}")
        print()

    if failures:
        print("Coverage ratchet FAILED - regression detected:")
        for f in failures:
            print(f"  ✗ {f}")
        return 1

    print("Coverage ratchet PASSED - no regressions detected")
    return 0

if __name__ == "__main__":
    sys.exit(check_ratchet())
```

**Note:** Actual baseline values are stored in `scripts/testing/coverage_baselines.json`
(see Part 3: Coverage Ratchet Persistence & Governance). The JSON file is the single
source of truth for baselines and is updated via explicit PRs with Tech Lead approval.

---

## Appendix C: Quality Rubric for P0 Tests

### Required Test Types for P0 Modules

| Test Type | Description | Example |
|-----------|-------------|---------|
| **Happy Path** | Normal operation | `test_submit_order_success` |
| **Negative Path** | Error conditions | `test_submit_order_insufficient_funds` |
| **Boundary** | Edge values | `test_submit_order_max_quantity` |
| **Concurrency** | Race conditions | `test_concurrent_order_submission` |
| **Idempotency** | Duplicate handling | `test_duplicate_order_id_rejected` |
| **State Machine** | State transitions | `test_breaker_open_to_closed_transition` |

### P0 Test Checklist

```markdown
## P0 Module Test Checklist: [module_name]

### Coverage
- [ ] Branch coverage ≥ 90%
- [ ] All public functions have tests
- [ ] All error handling paths tested

### Test Types
- [ ] Happy path tests
- [ ] Negative path tests (invalid inputs, error conditions)
- [ ] Boundary tests (min/max values, empty inputs)
- [ ] Concurrency tests (if applicable)
- [ ] Idempotency tests (if applicable)

### Financial Safety
- [ ] Negative balance scenarios tested
- [ ] Overflow/underflow scenarios tested
- [ ] Precision/rounding scenarios tested
- [ ] Timezone edge cases tested (if applicable)
```

---

## Appendix D: Test Fixture Standards

### Fixture Naming Conventions

| Fixture Type | Naming Pattern | Example |
|-------------|----------------|---------|
| **Database** | `db_*` or `*_db` | `db_session`, `test_db` |
| **Redis** | `redis_*` | `redis_client`, `redis_prefix` |
| **API clients** | `*_client` | `alpaca_client`, `http_client` |
| **Mock objects** | `mock_*` | `mock_broker`, `mock_market_data` |
| **Test data** | `sample_*` or `*_fixture` | `sample_order`, `position_fixture` |
| **Time** | `frozen_*` or `*_time` | `frozen_time`, `market_open_time` |

### Fixture File Organization

```
tests/
├── conftest.py              # Root-level fixtures (db, redis, common)
├── fixtures/
│   ├── __init__.py
│   ├── orders.py            # Order-related fixtures
│   ├── positions.py         # Position-related fixtures
│   ├── market_data.py       # Market data fixtures
│   └── golden/              # Golden file storage
│       ├── orders/
│       │   └── sample_order_v1.json
│       └── signals/
│           └── sample_signal_v1.json
├── libs/
│   └── trading/
│       └── conftest.py      # Trading-specific fixtures
└── apps/
    └── execution_gateway/
        └── conftest.py      # Gateway-specific fixtures
```

### Golden File Versioning

Golden files (expected output files) must be versioned:

```python
# tests/fixtures/golden/orders/sample_order_v2.json
{
  "_golden_version": 2,
  "_created": "2026-01-15",
  "_description": "Sample order for TWAP tests",
  "symbol": "AAPL",
  "side": "buy",
  "qty": 100,
  ...
}
```

**Rules:**
- Golden files include version number in filename (`_v1.json`, `_v2.json`)
- Old versions kept for regression testing
- Updates require PR with justification

### Large Dataset Handling

For fixtures with large datasets:
1. Store in `tests/fixtures/data/` (gitignored)
2. Provide download script: `scripts/testing/download_fixtures.py`
3. Use `pytest.mark.skipif` if fixtures unavailable
4. Document in `tests/fixtures/README.md`

### Deterministic Mocks

All mocks must be deterministic:
- Use `faker` with fixed seed: `faker.Faker().seed_instance(42)`
- Use `freezegun` for time-dependent tests
- Avoid `random.random()` without seed

---

## Appendix E: Nightly Full Coverage Run

### Purpose

While daily CI uses unit-test-only coverage, a nightly job runs full coverage including integration tests for **informational reporting** (non-blocking).

### Workflow

```yaml
# .github/workflows/nightly-full-coverage.yml
name: Nightly Full Coverage

on:
  schedule:
    - cron: '0 3 * * *'  # 3 AM UTC daily
  workflow_dispatch:  # Manual trigger

jobs:
  full-coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: poetry install

      - name: Run all tests with coverage (including integration)
        run: |
          PYTHONPATH=. poetry run pytest \
            tests/ \
            --cov=libs --cov=apps \
            --cov-branch \
            --cov-report=xml:coverage-full.xml \
            --cov-report=html:htmlcov-full

      - name: Upload full coverage report
        uses: actions/upload-artifact@v4
        with:
          name: full-coverage-report
          path: htmlcov-full/

      - name: Post coverage summary
        run: |
          coverage report --format=markdown >> $GITHUB_STEP_SUMMARY

  p0-quarantine-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run quarantined P0 tests
        run: |
          # P0 tests in quarantine still run nightly
          if [ -f tests/quarantine.txt ]; then
            P0_TESTS=$(grep -E "libs/(trading|risk)|apps/execution_gateway" tests/quarantine.txt | cut -d'|' -f1)
            if [ -n "$P0_TESTS" ]; then
              echo "Running quarantined P0 tests:"
              echo "$P0_TESTS"
              poetry run pytest $P0_TESTS --no-cov || true
            fi
          fi
```

### Reporting

Nightly coverage is **informational only**:
- Does not block any PRs
- Posted to Slack/email for visibility
- Used to identify integration coverage gaps
- Helps track overall project health

### P0 Quarantine Monitoring

The nightly job also runs quarantined P0 tests:
- If they pass consistently, consider removing from quarantine
- If they fail, escalate for immediate fix
- Prevents P0 tests from being "forgotten" in quarantine

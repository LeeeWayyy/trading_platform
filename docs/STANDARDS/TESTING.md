# Testing Strategy

## Pyramid
- Unit: functions (features, allocators, idempotent ID generation)
- Contract: API endpoints vs OpenAPI (schemathesis)
- Integration: end-to-end paper run in DRY_RUN then real paper API

## Must-Haves
- Reproducible `paper_run` for yesterday (backtest replay parity)
- Circuit breaker tests: trip on DD, untrip via recovery policy
- Stale-order cleanup test (>15m → cancel)

## PR Checklist

**Zen-MCP Review (MANDATORY):**
- [ ] **Progressive commit reviews completed**
  - [ ] All commits reviewed by zen-mcp before committing
  - [ ] All HIGH/CRITICAL issues fixed
  - [ ] MEDIUM issues fixed or deferred with justification
- [ ] **Deep review before PR completed**
  - [ ] Comprehensive zen-mcp review of all branch changes
  - [ ] Architecture reviewed and approved
  - [ ] Test coverage verified
  - [ ] Edge cases identified and handled
- [ ] **Zen review documented in PR description**
  - [ ] Continuation ID included
  - [ ] Issues caught and fixed summary
  - [ ] Review approval confirmation

**Code Quality:**
- [ ] Tests added/updated (unit, integration, edge cases)
- [ ] All tests passing (`make test`)
- [ ] Linting passing (`make lint`)
- [ ] Code coverage ≥80% for new code

**Documentation:**
- [ ] OpenAPI updated if API changed
- [ ] Migrations included if DB changed
- [ ] Docs updated (REPO_MAP / ADR / TASKS / CONCEPTS)
- [ ] ADR created if architectural change
- [ ] Implementation guide updated/created

**GitHub Reviews:**
- [ ] GitHub App reviews requested (@codex @gemini-code-assist)

---

## Mocking Patterns

**MANDATORY:** Follow these mocking patterns to avoid test harness failures.

### Why This Matters

Incorrect mocking is a major cause of CI test failures. Common issues:
- **Module-level patching errors:** `@patch` targeting wrong import path
- **Missing pytest markers:** Tests lack proper `@pytest.mark.*` markers
- **Fixture pollution:** Module-level state leaking between tests
- **Health endpoint gaps:** Health checks not testing all failure modes

### Core Principles

1. **Mock at the point of import, not the point of definition**
2. **Use shared fixtures for common mocks**
3. **Always mark tests appropriately** (`@pytest.mark.unit`, `@pytest.mark.integration`)
4. **Test both success AND failure scenarios**
5. **Verify fixture cleanup in teardown**

---

### Pattern 1: Correct Module-Level Patching

**✅ CORRECT: Patch where the function is imported**

```python
# File: apps/execution_gateway/main.py
from libs.trading.risk_management.kill_switch import KillSwitch

def submit_order(...):
    kill_switch = KillSwitch()
    status = kill_switch.get_status()  # Using imported KillSwitch
    ...

# File: tests/test_execution_gateway.py
from unittest.mock import patch

# ✅ Patch at point of IMPORT (apps.execution_gateway.main)
@patch('apps.execution_gateway.main.KillSwitch')
def test_submit_order_success(mock_kill_switch):
    mock_instance = mock_kill_switch.return_value
    mock_instance.get_status.return_value = {"state": "ACTIVE"}

    result = submit_order(...)
    assert result.success
```

**❌ WRONG: Patching at point of definition**

```python
# ❌ NO! This doesn't work because submit_order imports from libs
@patch('libs.risk_management.kill_switch.KillSwitch')
def test_submit_order_success(mock_kill_switch):
    # This mock won't be used by submit_order!
    ...
```

**Rule:** Always patch `module_where_used.ImportedClass`, not `module_where_defined.Class`

---

### Pattern 2: Shared Fixtures for Common Mocks

**✅ CORRECT: Use shared fixtures in conftest.py**

```python
# File: tests/conftest.py
import pytest
from unittest.mock import Mock, patch

@pytest.fixture
def mock_redis_client():
    """Shared Redis client mock for all tests."""
    with patch('libs.redis_client.RedisClient') as mock:
        mock_instance = Mock()
        mock_instance.get.return_value = '{"state": "ACTIVE"}'
        mock_instance.set.return_value = True
        mock.return_value = mock_instance
        yield mock_instance

@pytest.fixture
def mock_kill_switch_active():
    """Shared kill-switch mock in ACTIVE state."""
    with patch('apps.execution_gateway.main.KillSwitch') as mock:
        mock_instance = Mock()
        mock_instance.get_status.return_value = {"state": "ACTIVE"}
        mock_instance.is_engaged.return_value = False
        mock.return_value = mock_instance
        yield mock_instance

# File: tests/test_execution_gateway.py
def test_submit_order_with_active_kill_switch(mock_kill_switch_active):
    """Test order submission when kill-switch is ACTIVE."""
    result = submit_order(...)
    assert result.success

def test_submit_order_with_engaged_kill_switch():
    """Test order submission when kill-switch is ENGAGED."""
    with patch('apps.execution_gateway.main.KillSwitch') as mock:
        mock_instance = Mock()
        mock_instance.get_status.return_value = {"state": "ENGAGED"}
        mock_instance.is_engaged.return_value = True
        mock.return_value = mock_instance

        result = submit_order(...)
        assert not result.success
```

**Benefits:**
- Reduces code duplication
- Ensures consistent mock behavior
- Easier to maintain
- Less likely to have wrong import paths

---

### Pattern 3: Pytest Marker Hygiene

**✅ CORRECT: All tests have proper markers (with parentheses)**

```python
import pytest

# Mark entire module
pytestmark = pytest.mark.unit()

# Individual test markers
@pytest.mark.unit()
def test_pure_function():
    """Unit test for pure function."""
    assert calculate_fee(100, 0.001) == 0.1

@pytest.mark.integration()
def test_api_endpoint(client):
    """Integration test requiring test server."""
    response = client.post("/orders", json={...})
    assert response.status_code == 200

@pytest.mark.integration()
@pytest.mark.slow()
def test_full_order_lifecycle(client, db):
    """Slow integration test with database."""
    ...
```

**❌ WRONG: Missing markers**

```python
# ❌ NO! Test lacks markers
def test_api_endpoint(client):
    response = client.post("/orders", json={...})
    assert response.status_code == 200
```

**Marker Guidelines (MUST use parentheses per Ruff PT rules):**
- `@pytest.mark.unit()` - Pure functions, no external dependencies
- `@pytest.mark.integration()` - Requires Redis, Postgres, or test server
- `@pytest.mark.slow()` - Tests taking >1 second
- `@pytest.mark.skip(reason="...")` - Temporarily disabled tests

**Enforcement:**
```bash
# Run only unit tests (fast)
pytest -m unit

# Run only integration tests
pytest -m integration

# Run everything except slow tests
pytest -m "not slow"
```

---

### Pattern 4: Testing Both Success AND Failure Scenarios

**✅ CORRECT: Test both paths**

```python
@pytest.mark.unit()
def test_get_status_success(mock_redis_client):
    """Test get_status() returns status when Redis available."""
    mock_redis_client.get.return_value = '{"state": "ACTIVE"}'

    status = kill_switch.get_status()
    assert status["state"] == "ACTIVE"

@pytest.mark.unit()
def test_get_status_fail_closed(mock_redis_client):
    """Test get_status() raises RuntimeError when Redis state missing."""
    mock_redis_client.get.return_value = None  # Simulate missing state

    with pytest.raises(RuntimeError, match="fail closed"):
        kill_switch.get_status()

@pytest.mark.integration()
def test_get_status_redis_unavailable():
    """Test get_status() raises RuntimeError when Redis is down."""
    with patch('libs.redis_client.RedisClient') as mock:
        mock.return_value.get.side_effect = redis.exceptions.ConnectionError()

        with pytest.raises(RuntimeError, match="Redis unavailable"):
            kill_switch.get_status()
```

**❌ WRONG: Only testing success path**

```python
# ❌ NO! Missing failure scenarios
def test_get_status():
    """Test get_status() returns status."""
    status = kill_switch.get_status()
    assert status["state"] == "ACTIVE"
```

**Rule:** For EVERY external dependency, test what happens when it fails.

---

### Pattern 5: Health Endpoint Testing

**✅ CORRECT: Test all failure modes**

```python
@pytest.mark.integration()
def test_health_endpoint_all_healthy(client, mock_redis_client, mock_db):
    """Test health endpoint when all dependencies healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["redis"] == "connected"
    assert response.json()["database"] == "connected"

@pytest.mark.integration()
def test_health_endpoint_redis_down(client, mock_db):
    """Test health endpoint when Redis is unavailable."""
    with patch('libs.redis_client.RedisClient') as mock:
        mock.return_value.ping.side_effect = redis.exceptions.ConnectionError()

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"
        assert response.json()["redis"] == "disconnected"

@pytest.mark.integration()
def test_health_endpoint_db_down(client, mock_redis_client):
    """Test health endpoint when database is unavailable."""
    with patch('sqlalchemy.orm.Session.execute') as mock:
        mock.side_effect = OperationalError("connection refused", None, None)

        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "unhealthy"
        assert response.json()["database"] == "disconnected"

@pytest.mark.integration()
def test_health_endpoint_kill_switch_fail_closed(client, mock_redis_client, mock_db):
    """Test health endpoint when kill-switch state is missing (fail-closed)."""
    mock_redis_client.get.return_value = None  # Missing kill-switch state

    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"
    assert "kill_switch" in response.json()
    assert response.json()["kill_switch"] == "state_missing"
```

**Rule:** Health endpoints must test EVERY external dependency failure.

---

### Pattern 6: Fixture Cleanup

**✅ CORRECT: Verify cleanup in teardown**

```python
@pytest.fixture
def redis_client():
    """Create Redis client for testing."""
    client = RedisClient(host="localhost", port=6379, db=1)  # Test DB
    yield client

    # Cleanup: delete all test keys
    client.delete("kill_switch:state")
    client.delete("kill_switch:history")
    client.flushdb()  # Clear entire test DB

@pytest.fixture
def db_session():
    """Create database session for testing."""
    session = Session()
    yield session

    # Cleanup: rollback any uncommitted changes
    session.rollback()
    session.close()
```

**Rule:** Always clean up test data to prevent cross-test pollution.

---

### Enforcement Checklist

**Before committing tests:**
- [ ] All `@patch` decorators target correct import paths (where used, not where defined)
- [ ] All tests have proper `@pytest.mark.*` markers
- [ ] Both success AND failure scenarios tested
- [ ] Health endpoint tests cover ALL external dependency failures
- [ ] Shared fixtures used for common mocks (in `conftest.py`)
- [ ] Fixture cleanup verified in teardown
- [ ] No module-level state pollution between tests

**CI Enforcement:**
```bash
# Run tests to verify markers work
pytest -m unit  # Should run only unit tests
pytest -m integration  # Should run only integration tests

# Verify health endpoint coverage
pytest tests/test_health.py -v
```

---

### Common Mistakes to Avoid

1. **❌ Patching at definition instead of import**
   ```python
   # Wrong: @patch('libs.module.Class')
   # Right: @patch('apps.service.main.Class')
   ```

2. **❌ Missing pytest markers (or missing parentheses)**
   ```python
   # Wrong: def test_something():
   # Wrong: @pytest.mark.unit def test_something():  # Missing parentheses!
   # Right: @pytest.mark.unit() def test_something():
   ```

3. **❌ Only testing happy path**
   ```python
   # Wrong: Only test when Redis is available
   # Right: Test both available AND unavailable
   ```

4. **❌ Ignoring fixture cleanup**
   ```python
   # Wrong: No teardown in fixture
   # Right: yield + cleanup in teardown
   ```

5. **❌ Module-level state pollution**
   ```python
   # Wrong: global variable modified in test
   # Right: Use fixtures with proper cleanup
   ```

**See root cause analysis for test harness failure examples.**

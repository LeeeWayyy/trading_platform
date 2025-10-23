# Coding Standards

## General Guidelines

- Python 3.11, type hints required, mypy passing.
- Logging: structured, include `strategy_id`, `client_order_id` when relevant.
- Errors: never swallow; raise domain errors with context. Use `tenacity` for retries.
- Config: pydantic settings, no `os.getenv` scattered.
- Time: always timezone-aware UTC; use market calendar utilities.
- DB: use parameterized queries/ORM, migrations for schema changes.
- Concurrency: prefer async FastAPI + httpx; guard shared state with DB not memory.

---

## Redis Client Reliability Contract

**MANDATORY:** ALL Redis client methods MUST include retry decorators.

### Why This Matters

Transient network issues should NOT cause system-wide failures. Redis is used for:
- Kill-switch state (safety-critical)
- Circuit breaker state (safety-critical)
- Online features (trading-critical)

Without retries, a brief network hiccup causes the system to fail closed, blocking ALL trading.

### The Pattern

**✅ CORRECT: All Redis methods have @retry decorator (using Tenacity)**

```python
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from redis.exceptions import ConnectionError, TimeoutError

class RedisClient:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def get(self, key: str) -> Optional[str]:
        """Get value from Redis.

        This method includes automatic retries for transient Redis failures.

        Args:
            key: Redis key

        Returns:
            Value if exists, None otherwise

        Raises:
            RedisConnectionError: If Redis is unavailable after retries
        """
        return self.client.get(key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set value in Redis with optional expiration.

        This method includes automatic retries for transient Redis failures.

        Args:
            key: Redis key
            value: Value to store
            ex: Expiration in seconds (optional)

        Returns:
            True if successful

        Raises:
            RedisConnectionError: If Redis is unavailable after retries
        """
        return self.client.set(key, value, ex=ex)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def lpush_list(self, key: str, *values: str) -> int:
        """Push values to the left of a Redis list.

        This method includes automatic retries for transient Redis failures.

        Args:
            key: Redis list key
            *values: Values to push

        Returns:
            Length of list after push

        Raises:
            RedisConnectionError: If Redis is unavailable after retries
        """
        return self.client.lpush(key, *values)
```

**❌ WRONG: Missing @retry decorator**

```python
# ❌ NO! This exposes the system to transient failures
def lpush_list(self, key: str, *values: str) -> int:
    return self.client.lpush(key, *values)
```

### Enforcement

- **Code Review:** All Redis methods must have `@retry` decorator before approval
- **Pattern Parity Check:** When adding new Redis methods, verify ALL existing methods have retries
- **Future:** Static analysis will enforce this (see Priority 3.5 in root cause analysis)

### Template for New Redis Methods

```python
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from redis.exceptions import ConnectionError, TimeoutError

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
)
def method_name(self, ...) -> ReturnType:
    """Method description.

    This method includes automatic retries for transient Redis failures.

    Args:
        ...

    Returns:
        ...

    Raises:
        RedisConnectionError: If Redis is unavailable after retries
    """
    return self.client.operation(...)
```

### Exceptions

**NONE.** ALL Redis client methods must have retries. No exceptions.

### Testing

When testing Redis methods:
- Mock the retry decorator in unit tests to avoid delays
- Test failure scenarios (Redis unavailable) in integration tests
- Verify retry behavior works end-to-end

```python
# Unit test: mock the decorator
@patch('libs.redis_client.client.retry', lambda **kwargs: lambda f: f)
def test_get_success():
    client = RedisClient()
    result = client.get("test_key")
    assert result is not None

# Integration test: test retry behavior
def test_get_retries_on_failure():
    client = RedisClient()
    with patch.object(client.client, 'get', side_effect=[
        redis.exceptions.ConnectionError(),  # First attempt fails
        redis.exceptions.ConnectionError(),  # Second attempt fails
        "success"  # Third attempt succeeds
    ]):
        result = client.get("test_key")
        assert result == "success"
```

---

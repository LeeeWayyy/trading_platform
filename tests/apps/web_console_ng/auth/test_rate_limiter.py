from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.web_console_ng.auth.rate_limiter import AuthRateLimiter


@pytest.mark.asyncio()
async def test_check_only_allowed() -> None:
    limiter = AuthRateLimiter()
    # Mock redis via private attribute (property uses lazy initialization)
    mock_redis = AsyncMock()
    limiter._redis = mock_redis

    # Mock script load to return a SHA
    mock_redis.script_load.return_value = "sha123"

    # Mock evalsha return: [is_blocked, retry_after, reason]
    # Allowed: [0, 0, 'allowed']
    mock_redis.evalsha.return_value = [0, 0, b"allowed"]

    is_blocked, retry, reason = await limiter.check_only("1.2.3.4", "user1")

    assert is_blocked is False
    assert retry == 0
    assert reason == "allowed"

    # Verify call arguments (keys)
    call_args = mock_redis.evalsha.call_args
    # args: (sha, numkeys, key1, key2, arg1)
    assert call_args[0][0] == "sha123"
    assert "auth_rate:ip:1.2.3.4" in call_args[0]
    assert "auth_lockout:user1" in call_args[0]


@pytest.mark.asyncio()
async def test_check_only_blocked() -> None:
    limiter = AuthRateLimiter()
    mock_redis = AsyncMock()
    limiter._redis = mock_redis
    mock_redis.script_load.return_value = "sha123"

    # Blocked: [1, 60, 'ip_rate_limit']
    mock_redis.evalsha.return_value = [1, 60, b"ip_rate_limit"]

    is_blocked, retry, reason = await limiter.check_only("1.2.3.4", "user1")

    assert is_blocked is True
    assert retry == 60
    assert reason == "ip_rate_limit"


@pytest.mark.asyncio()
async def test_record_failure_increment() -> None:
    limiter = AuthRateLimiter()
    mock_redis = AsyncMock()
    limiter._redis = mock_redis
    mock_redis.script_load.return_value = "sha456"

    # Recorded: [1, 0, 'failure_recorded'] (is_allowed=True means not yet blocked)
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded"]

    is_allowed, retry, reason = await limiter.record_failure("1.2.3.4", "user1")

    assert is_allowed is True
    assert reason == "failure_recorded"


@pytest.mark.asyncio()
async def test_record_failure_lockout() -> None:
    limiter = AuthRateLimiter()
    mock_redis = AsyncMock()
    limiter._redis = mock_redis
    mock_redis.script_load.return_value = "sha456"

    # Locked out now: [0, 900, 'account_locked_now']
    mock_redis.evalsha.return_value = [0, 900, b"account_locked_now"]

    is_allowed, retry, reason = await limiter.record_failure("1.2.3.4", "user1")

    assert is_allowed is False
    assert retry == 900
    assert reason == "account_locked_now"


@pytest.mark.asyncio()
async def test_clear_on_success() -> None:
    limiter = AuthRateLimiter()
    mock_redis = AsyncMock()
    limiter._redis = mock_redis

    await limiter.clear_on_success("user1")

    mock_redis.delete.assert_called_once_with("auth_failures:user1", "auth_lockout:user1")

"""Comprehensive tests for authentication rate limiter.

This module tests all code paths in AuthRateLimiter including:
- Rate limiting algorithms (sliding window, token bucket)
- Request counting and window expiration
- Redis operations (Lua scripts, NoScriptError recovery)
- Error handling and edge cases
- Account lockout mechanisms

Target: 85%+ branch coverage
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis import exceptions as redis_exceptions

from apps.web_console_ng.auth.rate_limiter import (
    CHECK_AND_INCR_IP_SCRIPT,
    CHECK_ONLY_SCRIPT,
    RECORD_FAILURE_SCRIPT,
    AuthRateLimiter,
)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Create a mock Redis client for testing."""
    redis_mock = AsyncMock()
    redis_mock.script_load = AsyncMock(return_value="test_sha_123")
    redis_mock.evalsha = AsyncMock()
    redis_mock.eval = AsyncMock()
    redis_mock.delete = AsyncMock()
    return redis_mock


@pytest.fixture()
def limiter(mock_redis: AsyncMock) -> AuthRateLimiter:
    """Create an AuthRateLimiter instance with mocked Redis."""
    limiter_instance = AuthRateLimiter()
    limiter_instance._redis = mock_redis
    return limiter_instance


@pytest.fixture()
def mock_redis_store() -> MagicMock:
    """Mock the Redis HA store."""
    store_mock = MagicMock()
    client_mock = AsyncMock()
    store_mock.get_master_client.return_value = client_mock
    return store_mock


# ============================================================================
# Initialization Tests
# ============================================================================


def test_init_default_values() -> None:
    """Test that AuthRateLimiter initializes with correct default values."""
    limiter = AuthRateLimiter()

    assert limiter._redis is None
    assert limiter.max_attempts_per_ip == 10
    assert limiter.max_attempts_per_account == 5
    assert limiter.lockout_duration == 15 * 60  # 15 minutes
    assert limiter.failure_window == 15 * 60  # 15 minutes
    assert limiter._check_script_sha is None
    assert limiter._record_script_sha is None


@pytest.mark.asyncio()
@patch("apps.web_console_ng.auth.rate_limiter.get_redis_store")
async def test_get_redis_lazy_initialization(mock_get_store: MagicMock) -> None:
    """Test lazy initialization of Redis client via HA store."""
    mock_store = MagicMock()
    mock_client = AsyncMock()
    mock_store.get_master_client.return_value = mock_client
    mock_get_store.return_value = mock_store

    limiter = AuthRateLimiter()
    assert limiter._redis is None

    redis_client = await limiter._get_redis()

    assert redis_client is mock_client
    assert limiter._redis is mock_client
    mock_store.get_master_client.assert_called_once_with(decode_responses=False)


@pytest.mark.asyncio()
async def test_get_redis_cached(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test that _get_redis returns cached client on subsequent calls."""
    first_client = await limiter._get_redis()
    second_client = await limiter._get_redis()

    assert first_client is second_client
    assert first_client is mock_redis


@patch("apps.web_console_ng.auth.rate_limiter.get_redis_store")
def test_redis_property_lazy_initialization(mock_get_store: MagicMock) -> None:
    """Test redis property initializes client synchronously if not initialized."""
    mock_store = MagicMock()
    mock_client = MagicMock()
    mock_store.get_master_client.return_value = mock_client
    mock_get_store.return_value = mock_store

    limiter = AuthRateLimiter()
    redis_client = limiter.redis

    assert redis_client is mock_client
    assert limiter._redis is mock_client
    mock_store.get_master_client.assert_called_once_with(decode_responses=False)


def test_redis_property_returns_cached(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test redis property returns cached client."""
    redis_client = limiter.redis

    assert redis_client is mock_redis


# ============================================================================
# Script Loading Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_load_scripts_initial(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test that _load_scripts loads both scripts on first call."""
    mock_redis.script_load = AsyncMock(side_effect=["check_sha", "record_sha"])

    await limiter._load_scripts()

    assert limiter._check_script_sha == "check_sha"
    assert limiter._record_script_sha == "record_sha"
    assert mock_redis.script_load.call_count == 2
    mock_redis.script_load.assert_any_call(CHECK_ONLY_SCRIPT)
    mock_redis.script_load.assert_any_call(RECORD_FAILURE_SCRIPT)


@pytest.mark.asyncio()
async def test_load_scripts_cached(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test that _load_scripts uses cached SHAs on subsequent calls."""
    limiter._check_script_sha = "cached_check_sha"
    limiter._record_script_sha = "cached_record_sha"

    await limiter._load_scripts()

    # Should not call script_load when SHAs are cached
    mock_redis.script_load.assert_not_called()


@pytest.mark.asyncio()
async def test_load_scripts_force_reload(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test that _load_scripts reloads scripts when force=True."""
    limiter._check_script_sha = "old_check_sha"
    limiter._record_script_sha = "old_record_sha"
    mock_redis.script_load = AsyncMock(side_effect=["new_check_sha", "new_record_sha"])

    await limiter._load_scripts(force=True)

    assert limiter._check_script_sha == "new_check_sha"
    assert limiter._record_script_sha == "new_record_sha"
    assert mock_redis.script_load.call_count == 2


# ============================================================================
# check_and_increment_ip Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_check_and_increment_ip_allowed(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_and_increment_ip when IP is allowed."""
    mock_redis.eval.return_value = [0, 0, b"allowed"]

    is_blocked, retry_after, reason = await limiter.check_and_increment_ip("192.168.1.1")

    assert is_blocked is False
    assert retry_after == 0
    assert reason == "allowed"

    # Verify eval was called with correct arguments
    mock_redis.eval.assert_called_once()
    call_args = mock_redis.eval.call_args
    assert call_args[0][0] == CHECK_AND_INCR_IP_SCRIPT
    assert call_args[0][1] == 1  # Number of keys
    assert call_args[0][2] == "auth_rate:ip:192.168.1.1"
    assert call_args[0][3] == "10"  # max_attempts_per_ip


@pytest.mark.asyncio()
async def test_check_and_increment_ip_blocked(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_and_increment_ip when IP rate limit is exceeded."""
    mock_redis.eval.return_value = [1, 45, b"ip_rate_limit"]

    is_blocked, retry_after, reason = await limiter.check_and_increment_ip("10.0.0.1")

    assert is_blocked is True
    assert retry_after == 45
    assert reason == "ip_rate_limit"


@pytest.mark.asyncio()
async def test_check_and_increment_ip_string_reason(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_and_increment_ip handles string reasons (not bytes)."""
    mock_redis.eval.return_value = [0, 0, "allowed"]  # String instead of bytes

    is_blocked, retry_after, reason = await limiter.check_and_increment_ip("172.16.0.1")

    assert is_blocked is False
    assert retry_after == 0
    assert reason == "allowed"


@pytest.mark.asyncio()
@patch("apps.web_console_ng.auth.rate_limiter.get_redis_store")
async def test_check_and_increment_ip_lazy_redis_init(mock_get_store: MagicMock) -> None:
    """Test check_and_increment_ip initializes Redis lazily."""
    mock_store = MagicMock()
    mock_client = AsyncMock()
    mock_client.eval.return_value = [0, 0, b"allowed"]
    mock_store.get_master_client.return_value = mock_client
    mock_get_store.return_value = mock_store

    limiter = AuthRateLimiter()
    await limiter.check_and_increment_ip("192.168.1.1")

    mock_store.get_master_client.assert_called_once_with(decode_responses=False)


# ============================================================================
# check_only Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_check_only_allowed(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only when both IP and account are allowed."""
    mock_redis.script_load.return_value = "check_sha"
    mock_redis.evalsha.return_value = [0, 0, b"allowed"]

    is_blocked, retry_after, reason = await limiter.check_only("192.168.1.1", "testuser")

    assert is_blocked is False
    assert retry_after == 0
    assert reason == "allowed"

    # Verify evalsha was called with correct keys
    call_args = mock_redis.evalsha.call_args
    assert call_args[0][0] == "check_sha"
    assert call_args[0][1] == 2  # Number of keys
    assert "auth_rate:ip:192.168.1.1" in call_args[0]
    assert "auth_lockout:testuser" in call_args[0]
    assert "10" in call_args[0]  # max_attempts_per_ip


@pytest.mark.asyncio()
async def test_check_only_ip_rate_limit(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only when IP rate limit is exceeded."""
    mock_redis.script_load.return_value = "check_sha"
    mock_redis.evalsha.return_value = [1, 60, b"ip_rate_limit"]

    is_blocked, retry_after, reason = await limiter.check_only("10.0.0.1", "user1")

    assert is_blocked is True
    assert retry_after == 60
    assert reason == "ip_rate_limit"


@pytest.mark.asyncio()
async def test_check_only_account_locked(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only when account is locked out."""
    mock_redis.script_load.return_value = "check_sha"
    mock_redis.evalsha.return_value = [1, 900, b"account_locked"]

    is_blocked, retry_after, reason = await limiter.check_only("192.168.1.1", "lockeduser")

    assert is_blocked is True
    assert retry_after == 900
    assert reason == "account_locked"


@pytest.mark.asyncio()
async def test_check_only_string_reason(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only handles string reasons (not bytes)."""
    mock_redis.script_load.return_value = "check_sha"
    mock_redis.evalsha.return_value = [0, 0, "allowed"]

    is_blocked, retry_after, reason = await limiter.check_only("192.168.1.1", "user1")

    assert is_blocked is False
    assert reason == "allowed"


@pytest.mark.asyncio()
async def test_check_only_noscript_recovery(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only recovers from NoScriptError by reloading scripts."""
    mock_redis.script_load.return_value = "new_sha"
    mock_redis.evalsha.side_effect = [
        redis_exceptions.NoScriptError("NOSCRIPT No matching script"),
        [0, 0, b"allowed"],
    ]

    is_blocked, retry_after, reason = await limiter.check_only("192.168.1.1", "user1")

    assert is_blocked is False
    assert reason == "allowed"
    # Should have called evalsha twice (first failed, second succeeded)
    assert mock_redis.evalsha.call_count == 2
    # Should have reloaded scripts after NoScriptError
    assert mock_redis.script_load.call_count == 2  # Initial load + force reload


@pytest.mark.asyncio()
async def test_check_only_loads_scripts_if_not_loaded(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test check_only loads scripts on first call."""
    assert limiter._check_script_sha is None
    mock_redis.script_load.return_value = "loaded_sha"
    mock_redis.evalsha.return_value = [0, 0, b"allowed"]

    await limiter.check_only("192.168.1.1", "user1")

    assert limiter._check_script_sha == "loaded_sha"
    mock_redis.script_load.assert_called()


# ============================================================================
# record_failure Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_record_failure_first_attempt(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure on first failed attempt."""
    mock_redis.script_load.return_value = "record_sha"
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded", 1]

    is_allowed, retry_after, reason = await limiter.record_failure("192.168.1.1", "user1")

    assert is_allowed is True
    assert retry_after == 0
    assert reason == "failure_recorded"

    # Verify evalsha was called with correct arguments
    call_args = mock_redis.evalsha.call_args
    assert call_args[0][0] == "record_sha"
    assert call_args[0][1] == 3  # Number of keys
    assert "auth_rate:ip:192.168.1.1" in call_args[0]
    assert "auth_failures:user1" in call_args[0]
    assert "auth_lockout:user1" in call_args[0]
    assert "10" in call_args[0]  # max_attempts_per_ip
    assert "5" in call_args[0]  # max_attempts_per_account
    assert "900" in call_args[0]  # lockout_duration
    assert "900" in call_args[0]  # failure_window


@pytest.mark.asyncio()
async def test_record_failure_ip_rate_limit(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure when IP rate limit is exceeded."""
    mock_redis.script_load.return_value = "record_sha"
    mock_redis.evalsha.return_value = [0, 60, b"ip_rate_limit"]

    is_allowed, retry_after, reason = await limiter.record_failure("10.0.0.1", "user1")

    assert is_allowed is False
    assert retry_after == 60
    assert reason == "ip_rate_limit"


@pytest.mark.asyncio()
async def test_record_failure_account_locked_now(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure when account gets locked after max failures."""
    mock_redis.script_load.return_value = "record_sha"
    mock_redis.evalsha.return_value = [0, 900, b"account_locked_now"]

    is_allowed, retry_after, reason = await limiter.record_failure("192.168.1.1", "user1")

    assert is_allowed is False
    assert retry_after == 900
    assert reason == "account_locked_now"


@pytest.mark.asyncio()
async def test_record_failure_string_reason(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure handles string reasons (not bytes)."""
    mock_redis.script_load.return_value = "record_sha"
    mock_redis.evalsha.return_value = [1, 0, "failure_recorded"]

    is_allowed, retry_after, reason = await limiter.record_failure("192.168.1.1", "user1")

    assert is_allowed is True
    assert reason == "failure_recorded"


@pytest.mark.asyncio()
async def test_record_failure_noscript_recovery(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure recovers from NoScriptError."""
    mock_redis.script_load.return_value = "new_sha"
    mock_redis.evalsha.side_effect = [
        redis_exceptions.NoScriptError("NOSCRIPT No matching script"),
        [1, 0, b"failure_recorded"],
    ]

    is_allowed, retry_after, reason = await limiter.record_failure("192.168.1.1", "user1")

    assert is_allowed is True
    assert reason == "failure_recorded"
    assert mock_redis.evalsha.call_count == 2
    # Should have reloaded scripts after NoScriptError
    assert mock_redis.script_load.call_count == 2


@pytest.mark.asyncio()
async def test_record_failure_loads_scripts_if_not_loaded(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test record_failure loads scripts on first call."""
    assert limiter._record_script_sha is None
    mock_redis.script_load.return_value = "loaded_sha"
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded"]

    await limiter.record_failure("192.168.1.1", "user1")

    assert limiter._record_script_sha == "loaded_sha"
    mock_redis.script_load.assert_called()


# ============================================================================
# clear_on_success Tests
# ============================================================================


@pytest.mark.asyncio()
async def test_clear_on_success(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test clear_on_success removes failure and lockout keys."""
    await limiter.clear_on_success("user1")

    mock_redis.delete.assert_called_once_with("auth_failures:user1", "auth_lockout:user1")


@pytest.mark.asyncio()
async def test_clear_on_success_multiple_users(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test clear_on_success handles multiple users correctly."""
    await limiter.clear_on_success("user1")
    await limiter.clear_on_success("user2")

    assert mock_redis.delete.call_count == 2
    mock_redis.delete.assert_any_call("auth_failures:user1", "auth_lockout:user1")
    mock_redis.delete.assert_any_call("auth_failures:user2", "auth_lockout:user2")


# ============================================================================
# unlock_account Tests
# ============================================================================


@pytest.mark.asyncio()
@patch("apps.web_console_ng.auth.rate_limiter.AuthAuditLogger")
@patch("apps.web_console_ng.auth.rate_limiter.config")
async def test_unlock_account_success(
    mock_config: MagicMock,
    mock_audit_logger_class: MagicMock,
    limiter: AuthRateLimiter,
    mock_redis: AsyncMock,
) -> None:
    """Test unlock_account successfully unlocks and logs audit event."""
    mock_config.AUTH_TYPE = "basic"
    mock_audit = MagicMock()
    mock_audit_logger_class.get.return_value = mock_audit

    result = await limiter.unlock_account("user1", "admin_user")

    assert result is True
    mock_redis.delete.assert_called_once_with("auth_failures:user1", "auth_lockout:user1")

    # Verify audit log was called
    mock_audit.log_event.assert_called_once()
    call_kwargs = mock_audit.log_event.call_args[1]
    assert call_kwargs["event_type"] == "account_unlock"
    assert call_kwargs["user_id"] == "user1"
    assert call_kwargs["session_id"] is None
    assert call_kwargs["client_ip"] == "0.0.0.0"
    assert call_kwargs["auth_type"] == "basic"
    assert call_kwargs["outcome"] == "success"
    assert call_kwargs["extra_data"]["admin_user"] == "admin_user"
    assert call_kwargs["extra_data"]["source"] == "admin_action"


@pytest.mark.asyncio()
@patch("apps.web_console_ng.auth.rate_limiter.AuthAuditLogger")
@patch("apps.web_console_ng.auth.rate_limiter.logger")
async def test_unlock_account_redis_error(
    mock_logger: MagicMock,
    mock_audit_logger_class: MagicMock,
    limiter: AuthRateLimiter,
    mock_redis: AsyncMock,
) -> None:
    """Test unlock_account handles Redis errors gracefully."""
    mock_redis.delete.side_effect = redis_exceptions.ConnectionError("Connection failed")

    result = await limiter.unlock_account("user1", "admin_user")

    assert result is False
    mock_logger.exception.assert_called_once()
    log_call = mock_logger.exception.call_args
    assert "Failed to unlock account" in log_call[0][0]
    assert log_call[1]["extra"]["username"] == "user1"
    assert log_call[1]["extra"]["admin_user"] == "admin_user"


@pytest.mark.asyncio()
@patch("apps.web_console_ng.auth.rate_limiter.AuthAuditLogger")
@patch("apps.web_console_ng.auth.rate_limiter.logger")
async def test_unlock_account_audit_error(
    mock_logger: MagicMock,
    mock_audit_logger_class: MagicMock,
    limiter: AuthRateLimiter,
    mock_redis: AsyncMock,
) -> None:
    """Test unlock_account handles audit logging errors gracefully."""
    mock_audit = MagicMock()
    mock_audit.log_event.side_effect = Exception("Audit logging failed")
    mock_audit_logger_class.get.return_value = mock_audit

    result = await limiter.unlock_account("user1", "admin_user")

    assert result is False
    mock_logger.exception.assert_called_once()


# ============================================================================
# Parametrized Tests for Edge Cases
# ============================================================================


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "ip_address",
    [
        "192.168.1.1",
        "10.0.0.1",
        "172.16.0.1",
        "8.8.8.8",
        "2001:db8::1",  # IPv6
        "127.0.0.1",  # localhost
    ],
)
async def test_check_only_various_ips(
    ip_address: str, limiter: AuthRateLimiter, mock_redis: AsyncMock
) -> None:
    """Test check_only handles various IP address formats."""
    mock_redis.script_load.return_value = "sha"
    mock_redis.evalsha.return_value = [0, 0, b"allowed"]

    is_blocked, retry_after, reason = await limiter.check_only(ip_address, "user1")

    assert is_blocked is False
    assert reason == "allowed"
    call_args = mock_redis.evalsha.call_args
    assert f"auth_rate:ip:{ip_address}" in call_args[0]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "username",
    [
        "user1",
        "admin",
        "test@example.com",
        "user-with-dashes",
        "user_with_underscores",
        "User123",
    ],
)
async def test_record_failure_various_usernames(
    username: str, limiter: AuthRateLimiter, mock_redis: AsyncMock
) -> None:
    """Test record_failure handles various username formats."""
    mock_redis.script_load.return_value = "sha"
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded"]

    is_allowed, retry_after, reason = await limiter.record_failure("192.168.1.1", username)

    assert is_allowed is True
    call_args = mock_redis.evalsha.call_args
    assert f"auth_failures:{username}" in call_args[0]
    assert f"auth_lockout:{username}" in call_args[0]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("return_value", "expected_blocked", "expected_retry", "expected_reason"),
    [
        ([0, 0, b"allowed"], False, 0, "allowed"),
        ([1, 30, b"ip_rate_limit"], True, 30, "ip_rate_limit"),
        ([1, 60, b"ip_rate_limit"], True, 60, "ip_rate_limit"),
        ([1, 900, b"account_locked"], True, 900, "account_locked"),
        ([0, 0, "allowed"], False, 0, "allowed"),  # String reason
    ],
)
async def test_check_only_return_values(
    return_value: list,
    expected_blocked: bool,
    expected_retry: int,
    expected_reason: str,
    limiter: AuthRateLimiter,
    mock_redis: AsyncMock,
) -> None:
    """Test check_only handles various return values correctly."""
    mock_redis.script_load.return_value = "sha"
    mock_redis.evalsha.return_value = return_value

    is_blocked, retry_after, reason = await limiter.check_only("192.168.1.1", "user1")

    assert is_blocked == expected_blocked
    assert retry_after == expected_retry
    assert reason == expected_reason


# ============================================================================
# Lua Script Constants Tests
# ============================================================================


def test_lua_scripts_defined() -> None:
    """Test that all Lua script constants are defined and non-empty."""
    assert CHECK_ONLY_SCRIPT
    assert CHECK_AND_INCR_IP_SCRIPT
    assert RECORD_FAILURE_SCRIPT

    # Verify scripts contain expected Redis commands
    assert "redis.call" in CHECK_ONLY_SCRIPT
    assert "redis.call" in CHECK_AND_INCR_IP_SCRIPT
    assert "redis.call" in RECORD_FAILURE_SCRIPT

    # Verify key operations
    assert "INCR" in CHECK_AND_INCR_IP_SCRIPT
    assert "INCR" in RECORD_FAILURE_SCRIPT
    assert "EXPIRE" in CHECK_AND_INCR_IP_SCRIPT
    assert "EXPIRE" in RECORD_FAILURE_SCRIPT
    assert "EXISTS" in CHECK_ONLY_SCRIPT
    assert "SETEX" in RECORD_FAILURE_SCRIPT
    assert "DEL" in RECORD_FAILURE_SCRIPT


# ============================================================================
# Integration-style Tests (Multiple Operations)
# ============================================================================


@pytest.mark.asyncio()
async def test_full_failure_cycle(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test full cycle: check -> record failure -> check again -> clear."""
    mock_redis.script_load.return_value = "sha"

    # First check - allowed
    mock_redis.evalsha.return_value = [0, 0, b"allowed"]
    is_blocked, _, _ = await limiter.check_only("192.168.1.1", "user1")
    assert is_blocked is False

    # Record failure
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded"]
    is_allowed, _, _ = await limiter.record_failure("192.168.1.1", "user1")
    assert is_allowed is True

    # Check again - now locked
    mock_redis.evalsha.return_value = [1, 900, b"account_locked"]
    is_blocked, retry, reason = await limiter.check_only("192.168.1.1", "user1")
    assert is_blocked is True
    assert reason == "account_locked"

    # Clear on success
    await limiter.clear_on_success("user1")
    mock_redis.delete.assert_called_with("auth_failures:user1", "auth_lockout:user1")


@pytest.mark.asyncio()
async def test_concurrent_operations_different_users(limiter: AuthRateLimiter, mock_redis: AsyncMock) -> None:
    """Test that operations on different users are independent."""
    mock_redis.script_load.return_value = "sha"
    mock_redis.evalsha.return_value = [1, 0, b"failure_recorded"]

    # Record failures for multiple users
    await limiter.record_failure("192.168.1.1", "user1")
    await limiter.record_failure("192.168.1.2", "user2")
    await limiter.record_failure("192.168.1.3", "user3")

    # Verify each call used correct keys
    assert mock_redis.evalsha.call_count == 3
    calls = mock_redis.evalsha.call_args_list

    assert "auth_failures:user1" in calls[0][0]
    assert "auth_failures:user2" in calls[1][0]
    assert "auth_failures:user3" in calls[2][0]
